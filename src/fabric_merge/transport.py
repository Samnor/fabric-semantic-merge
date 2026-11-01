"""Azure CLI credentials with bounded HTTP and no mutation retry/redirect."""

from dataclasses import dataclass
import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from .util import MergeError, require

FABRIC = "https://api.fabric.microsoft.com/v1"
POWER_BI = "https://api.powerbi.com/v1.0/myorg"
AUDIENCES = {
    "api.fabric.microsoft.com": "https://api.fabric.microsoft.com",
    "api.powerbi.com": "https://analysis.windows.net/powerbi/api",
}


@dataclass
class Response:
    status: int
    headers: dict
    body: object


class ApiError(MergeError):
    def __init__(self, method, url, status, body):
        self.status, self.body = status, body
        super().__init__(f"{method} {url}: HTTP {status}: {str(body)[:1200]}")


class UncertainRequest(MergeError):
    """No authoritative HTTP outcome; callers must reconcile, not resubmit."""


class AzureCliCredential:
    def __init__(self, tenant=None, subscription=None, executable="az"):
        self.tenant = tenant
        self.subscription = subscription
        self.executable = executable
        self.cache = {}
        self.identity_type = "unknown"

    def _run(self, args):
        try:
            result = subprocess.run(
                [self.executable, *args, "--output", "json", "--only-show-errors"],
                check=False,
                capture_output=True,
                text=True,
                timeout=45,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MergeError(
                "Azure CLI unavailable; install az and run az login"
            ) from exc
        require(
            result.returncode == 0,
            "Azure CLI could not complete the command: " + result.stderr[-500:],
        )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise MergeError("Azure CLI returned invalid JSON") from exc

    def account(self):
        args = ["account", "show"]
        if self.subscription:
            args += ["--subscription", self.subscription]
        value = self._run(args)
        if self.tenant:
            require(
                value["tenantId"].casefold() == self.tenant.casefold(),
                "Selected Azure account belongs to another tenant",
            )
        # Pin this invocation without changing global az account settings. A second
        # terminal switching its default subscription must not change our tokens.
        self.tenant = value["tenantId"]
        self.subscription = value["id"]
        self.identity_type = {
            "user": "user",
            "servicePrincipal": "service_principal",
        }.get((value.get("user") or {}).get("type"), "unknown")
        return {
            "tenant_id": value["tenantId"],
            "subscription_id": value["id"],
            "identity_type": self.identity_type,
        }

    def token(self, audience):
        cached = self.cache.get(audience)
        if cached and cached[0] > time.time() + 120:
            return cached[1]
        args = ["account", "get-access-token", "--resource", audience]
        if self.subscription:
            args += ["--subscription", self.subscription]
        elif self.tenant:
            args += ["--tenant", self.tenant]
        value = self._run(args)
        if self.tenant and value.get("tenant"):
            require(
                value["tenant"].casefold() == self.tenant.casefold(),
                "Azure CLI returned a token for another tenant",
            )
        token = value["accessToken"]
        require(bool(token), "Azure CLI returned no access token")
        self.cache[audience] = (
            float(value.get("expires_on", time.time() + 300)),
            token,
        )
        return token


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, credential, *, timeout=45, read_retries=2):
        self.credential = credential
        self.timeout = timeout
        self.read_retries = read_retries
        self.opener = urllib.request.build_opener(NoRedirect())

    def validate_url(self, url):
        parsed = urllib.parse.urlsplit(url)
        require(
            parsed.scheme == "https"
            and parsed.hostname in AUDIENCES
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443),
            "Untrusted API URL: " + url,
        )
        require(not parsed.fragment, "Fragments are not API URLs")
        return AUDIENCES[parsed.hostname]

    def request(self, method, url, body=None):
        audience = self.validate_url(url)
        method = method.upper()
        payload = None if body is None else json.dumps(body, allow_nan=False).encode()
        for attempt in range(self.read_retries + 1):
            headers = {
                "Authorization": "Bearer " + self.credential.token(audience),
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
            request = urllib.request.Request(
                url, data=payload, headers=headers, method=method
            )
            try:
                with self.opener.open(request, timeout=self.timeout) as handle:
                    status, raw, response_headers = (
                        handle.status,
                        handle.read(32 * 1024 * 1024 + 1),
                        dict(handle.headers),
                    )
            except urllib.error.HTTPError as exc:
                status, raw, response_headers = (
                    exc.code,
                    exc.read(1024 * 1024),
                    dict(exc.headers),
                )
                exc.close()
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if method == "GET" and attempt < self.read_retries:
                    time.sleep(min(2**attempt, 5))
                    continue
                raise UncertainRequest(
                    f"{method} {url}: no HTTP outcome; inspect the run journal before continuing"
                ) from exc
            lowered = {k.lower(): v for k, v in response_headers.items()}
            require(len(raw) <= 32 * 1024 * 1024, "API response exceeds 32 MiB")
            try:
                value = json.loads(raw) if raw else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                value = raw.decode("utf-8", errors="replace")[:1200]
            if (
                status in (429, 502, 503, 504)
                and method == "GET"
                and attempt < self.read_retries
            ):
                try:
                    delay = float(lowered.get("retry-after", 2**attempt))
                except ValueError:
                    delay = 2**attempt
                if delay > 60:
                    raise ApiError(method, url, status, value)
                time.sleep(max(0, delay))
                continue
            if not 200 <= status < 300:
                raise ApiError(method, url, status, value)
            return Response(status, lowered, value)
        raise MergeError("Read retry limit reached")

    def values(self, url, *, max_pages=1000):
        values, seen = [], set()
        for _ in range(max_pages):
            require(url not in seen, "Pagination loop")
            seen.add(url)
            response = self.request("GET", url)
            require(
                isinstance(response.body, dict)
                and isinstance(response.body.get("value"), list),
                "Invalid collection response",
            )
            values.extend(response.body["value"])
            next_url = (
                response.body.get("continuationUri")
                or response.body.get("@odata.nextLink")
                or response.body.get("odata.nextLink")
            )
            if not next_url and response.body.get("continuationToken"):
                parsed = urllib.parse.urlsplit(url)
                query = dict(urllib.parse.parse_qsl(parsed.query))
                query["continuationToken"] = response.body["continuationToken"]
                next_url = urllib.parse.urlunsplit(
                    parsed._replace(query=urllib.parse.urlencode(query))
                )
            if not next_url:
                return values
            self.validate_url(next_url)
            require(
                urllib.parse.urlsplit(next_url).hostname
                == urllib.parse.urlsplit(url).hostname,
                "Pagination changed API audience",
            )
            url = next_url
        raise MergeError("Pagination limit exceeded; discovery is incomplete")

    def operation(self, response, *, timeout=900):
        if response.status != 202:
            return response.body
        operation = response.headers.get("x-ms-operation-id")
        location = response.headers.get("location")
        if operation:
            from .util import uid

            location = f"{FABRIC}/operations/{uid(operation)}"
        require(
            location is not None,
            "Accepted operation lacks Location/operation ID; retain journal, do not repeat POST",
        )
        self.validate_url(location)
        require(
            location.startswith(FABRIC + "/operations/"),
            "Unexpected Fabric operation location",
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                delay = max(0, min(float(response.headers.get("retry-after", 1)), 60))
            except ValueError:
                delay = 1
            require(
                time.monotonic() + delay < deadline,
                "Operation still running; resume polling the same handle",
            )
            time.sleep(delay)
            response = self.request("GET", location)
            state = (response.body or {}).get("status")
            if state in ("Failed", "Cancelled", "Canceled"):
                raise MergeError("Fabric operation failed: " + str(response.body))
            if state == "Succeeded":
                return self.request("GET", location.rstrip("/") + "/result").body
            require(
                state in ("NotStarted", "Running", "Undefined"),
                "Unknown Fabric operation status: " + str(state),
            )
        raise MergeError(
            "Operation still running; use the saved operation handle, never repeat POST"
        )
