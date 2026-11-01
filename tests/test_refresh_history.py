"""Accepted standard refreshes without response handles require saved before-images."""

import unittest
from unittest.mock import Mock

from fabric_merge.api import Fabric
from fabric_merge.transport import Response
from fabric_merge.util import MergeError
from .support import LEFT, RIGHT, WS1


def history(identifier=RIGHT, status="Completed", kind="ViaApi"):
    return {"requestId": identifier, "status": status, "refreshType": kind}


class RefreshHistoryTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.api = Fabric(self.client, operation_timeout=0.01)
        self.accepted = Response(202, {}, None)
        self.before = {"workspace_id": WS1, "model_id": LEFT, "entries": []}

    def test_headerless_standard_refresh_uses_unique_history_delta_without_post(self):
        self.client.request.return_value = Response(200, {}, {"value": [history()]})
        result = self.api.wait_refresh(
            WS1, LEFT, self.accepted, history_before=self.before
        )
        self.assertEqual(result["request_id"], RIGHT)
        self.assertIn("history delta", result["tracking"])
        self.assertEqual(self.client.request.call_count, 1)
        self.assertEqual(self.client.request.call_args.args[0], "GET")

    def test_no_before_image_still_requires_reconciliation_and_never_guesses_latest(
        self,
    ):
        with self.assertRaisesRegex(MergeError, "no exact request ID"):
            self.api.wait_refresh(WS1, LEFT, self.accepted)
        self.client.request.assert_not_called()

    def test_existing_and_non_api_history_cannot_be_misattributed(self):
        self.before["entries"] = [history(LEFT)]
        self.client.request.return_value = Response(
            200, {}, {"value": [history(LEFT), history(RIGHT, kind="Scheduled")]}
        )
        with self.assertRaisesRegex(MergeError, "still running"):
            self.api.wait_refresh(WS1, LEFT, self.accepted, history_before=self.before)
        self.assertTrue(
            all(c.args[0] == "GET" for c in self.client.request.call_args_list)
        )

    def test_multiple_new_api_requests_are_ambiguous(self):
        self.client.request.return_value = Response(
            200, {}, {"value": [history(), history(LEFT)]}
        )
        with self.assertRaisesRegex(MergeError, "Ambiguous"):
            self.api.wait_refresh(WS1, LEFT, self.accepted, history_before=self.before)

    def test_before_image_from_another_model_is_rejected(self):
        self.before["model_id"] = RIGHT
        with self.assertRaisesRegex(MergeError, "another model"):
            self.api.wait_refresh(WS1, LEFT, self.accepted, history_before=self.before)

    def test_duplicate_and_failed_history_records_are_not_accepted(self):
        self.client.request.return_value = Response(
            200, {}, {"value": [history(), history()]}
        )
        with self.assertRaisesRegex(MergeError, "duplicate"):
            self.api.wait_refresh(WS1, LEFT, self.accepted, history_before=self.before)
        self.client.request.return_value = Response(
            200, {}, {"value": [history(status="Failed")]}
        )
        with self.assertRaisesRegex(MergeError, "refresh failed"):
            self.api.wait_refresh(WS1, LEFT, self.accepted, history_before=self.before)


if __name__ == "__main__":
    unittest.main()
