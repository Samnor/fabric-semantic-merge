import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from fabric_merge.journal import Journal
from fabric_merge.transport import (
    ApiError,
    AzureCliCredential,
    Client,
    Response,
    UncertainRequest,
)
from fabric_merge.util import MergeError


class TransportTests(unittest.TestCase):
    def test_account_pins_authentication_context_without_global_account_set(self):
        result = Mock(
            returncode=0,
            stdout=json.dumps(
                {"tenantId": "stable-tenant", "id": "stable-subscription"}
            ),
            stderr="",
        )
        with patch("subprocess.run", return_value=result) as run:
            credential = AzureCliCredential()
            credential.account()
            self.assertEqual(
                (credential.tenant, credential.subscription),
                ("stable-tenant", "stable-subscription"),
            )
            self.assertNotIn("set", run.call_args.args[0])

    def test_azure_cli_resource_and_tenant_are_explicit(self):
        process = Mock(
            returncode=0,
            stdout=json.dumps(
                {"accessToken": "private-test-token", "expires_on": 9999999999}
            ),
            stderr="",
        )
        with patch("subprocess.run", return_value=process) as run:
            credential = AzureCliCredential("tenant", "subscription")
            self.assertEqual(
                credential.token("https://api.fabric.microsoft.com"),
                "private-test-token",
            )
            self.assertEqual(
                credential.token("https://api.fabric.microsoft.com"),
                "private-test-token",
            )
            self.assertEqual(run.call_count, 1)
            args = run.call_args.args[0]
            self.assertIn("--resource", args)
            self.assertIn("https://api.fabric.microsoft.com", args)
            self.assertNotIn("--tenant", args)
            self.assertIn("--subscription", args)

    def test_tenant_only_token_does_not_add_a_subscription_argument(self):
        process = Mock(
            returncode=0,
            stdout=json.dumps(
                {"accessToken": "test", "tenant": "tenant", "expires_on": 9999999999}
            ),
            stderr="",
        )
        with patch("subprocess.run", return_value=process) as run:
            credential = AzureCliCredential("tenant")
            credential.token("https://api.fabric.microsoft.com")
            args = run.call_args.args[0]
            self.assertIn("--tenant", args)
            self.assertNotIn("--subscription", args)

    def test_token_never_requested_for_untrusted_endpoint(self):
        credential = Mock()
        client = Client(credential)
        for url in (
            "http://api.fabric.microsoft.com/v1",
            "https://evil.test/",
            "https://api.fabric.microsoft.com.evil.test/",
            "https://x@api.fabric.microsoft.com/v1",
            "https://api.fabric.microsoft.com:444/v1",
        ):
            with self.assertRaises(MergeError):
                client.request("GET", url)
        credential.token.assert_not_called()

    def test_failed_mutation_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(tmp)
            client = Mock()
            client.request.side_effect = ApiError("POST", "url", 429, {})
            for _ in range(2):
                with self.assertRaises(ApiError):
                    journal.mutate(client, "create", "POST", "url", {})
            self.assertEqual(client.request.call_count, 1)

    def test_lost_mutation_does_not_replay_and_payload_cannot_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(tmp)
            client = Mock()
            client.request.side_effect = UncertainRequest("lost")
            for _ in range(2):
                with self.assertRaises(UncertainRequest):
                    journal.mutate(client, "rebind", "POST", "url", {"id": "one"})
            self.assertEqual(client.request.call_count, 1)
            with self.assertRaisesRegex(MergeError, "change an existing"):
                journal.mutate(client, "rebind", "POST", "url", {"id": "different"})

    def test_timeout_and_server_error_receipts_remain_uncertain_without_replay(self):
        for status in (408, 500, 502, 503, 504):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                journal = Journal(tmp)
                client = Mock()
                client.request.side_effect = ApiError("POST", "url", status, {})
                with self.assertRaises(ApiError):
                    journal.mutate(client, "rebind", "POST", "url", {})
                self.assertEqual(journal.outcome("rebind"), "unknown")
                self.assertEqual(journal.latest("response", "rebind")["status"], status)
                with self.assertRaises(UncertainRequest):
                    journal.mutate(client, "rebind", "POST", "url", {})
                self.assertEqual(client.request.call_count, 1)
                journal.append("reconciled", key="rebind", body=None)
                self.assertEqual(journal.outcome("rebind"), "succeeded")
                journal.mutate(client, "rebind", "POST", "url", {})
                self.assertEqual(client.request.call_count, 1)

    def test_partial_or_modified_journal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(tmp)
            journal.append("test")
            with journal.path.open("a") as handle:
                handle.write('{"partial":')
            with self.assertRaises(UncertainRequest):
                journal.events()

    def test_pagination_refuses_wrong_audience_or_cycle(self):
        client = Client(Mock())
        url = "https://api.fabric.microsoft.com/v1/workspaces"
        with patch.object(
            client,
            "request",
            return_value=Response(200, {}, {"value": [], "continuationUri": url}),
        ):
            with self.assertRaisesRegex(MergeError, "Pagination loop"):
                client.values(url)
        with patch.object(
            client,
            "request",
            return_value=Response(
                200, {}, {"value": [], "continuationUri": "https://evil.test/"}
            ),
        ):
            with self.assertRaises(MergeError):
                client.values(url)

    def test_two_api_audiences_have_separate_token_caches(self):
        with patch(
            "subprocess.run",
            return_value=Mock(
                returncode=0,
                stdout=json.dumps({"accessToken": "test", "expires_on": 9999999999}),
                stderr="",
            ),
        ) as run:
            credential = AzureCliCredential()
            credential.token("https://api.fabric.microsoft.com")
            credential.token("https://analysis.windows.net/powerbi/api")
            self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
