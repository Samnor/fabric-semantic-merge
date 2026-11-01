"""Durable operation intents and resumable confirmed responses."""

from contextlib import contextmanager
import json
import os
from pathlib import Path

from .transport import ApiError, Response, UncertainRequest
from .util import digest, now, require


class Journal:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "journal.jsonl"

    def events(self):
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text().splitlines():
            require(bool(line.strip()), "Corrupt empty journal record")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise UncertainRequest(
                    "Journal has a partial record; preserve it and reconcile before continuing"
                ) from exc
            require(row["sequence"] == len(rows), "Journal sequence mismatch")
            require(
                row["previous_sha256"] == (digest(rows[-1]) if rows else None),
                "Journal hash chain mismatch",
            )
            rows.append(row)
        return rows

    def append(self, kind, **fields):
        rows = self.events()
        row = {
            "sequence": len(rows),
            "utc": now(),
            "previous_sha256": digest(rows[-1]) if rows else None,
            "kind": kind,
            **fields,
        }
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(
                json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        return row

    def latest(self, kind, key=None):
        return next(
            (
                r
                for r in reversed(self.events())
                if r["kind"] == kind and (key is None or r.get("key") == key)
            ),
            None,
        )

    def outcome(self, key):
        """Classify uncertainty independently of whether an HTTP receipt exists."""
        if not self.latest("intent", key):
            return "absent"
        if self.latest("reconciled", key):
            return "succeeded"
        receipt = self.latest("response", key)
        # A timeout/server error can follow a committed write. Retain the
        # receipt, but require exact observation instead of calling it rejected.
        if not receipt or receipt["status"] == 408 or receipt["status"] >= 500:
            return "unknown"
        return "succeeded" if 200 <= receipt["status"] < 300 else "failed"

    @contextmanager
    def lock(self):
        # Advisory OS locks disappear on process exit; a stale file is not a lock.
        with (self.directory / ".lock").open("a+") as handle:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                if not handle.read(1):
                    handle.write("0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise UncertainRequest(
                        "Another migration process holds this run lock"
                    ) from exc
                try:
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def mutate(self, client, key, method, url, body):
        signature = digest({"method": method, "url": url, "body": body})
        intent = self.latest("intent", key)
        if intent:
            require(
                intent["signature"] == signature,
                "Attempt to change an existing operation: " + key,
            )
            receipt = self.latest("response", key)
            reconciliation = self.latest("reconciled", key)
            if reconciliation:
                return Response(200, {}, reconciliation["body"])
            if self.outcome(key) == "unknown":
                raise UncertainRequest(
                    f"Unresolved operation {key}; run reconcile, never repeat the mutation"
                )
            if receipt:
                if not 200 <= receipt["status"] < 300:
                    raise ApiError(method, url, receipt["status"], receipt["body"])
                return Response(receipt["status"], receipt["headers"], receipt["body"])
            raise UncertainRequest(
                f"Unresolved operation {key}; run reconcile, never repeat the mutation"
            )
        self.append(
            "intent", key=key, method=method, url=url, body=body, signature=signature
        )
        try:
            response = client.request(method, url, body)
        except ApiError as exc:
            self.append(
                "response", key=key, status=exc.status, headers={}, body=exc.body
            )
            raise
        except BaseException as exc:
            self.append("uncertain", key=key, error_type=type(exc).__name__)
            raise
        self.append(
            "response",
            key=key,
            status=response.status,
            headers=response.headers,
            body=response.body,
        )
        return response
