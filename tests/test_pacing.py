"""Deterministic timing checks for staging and other DAX entry points."""

import unittest
from unittest.mock import Mock

from fabric_merge.api import Fabric
from fabric_merge.pacing import QueryPacer
from fabric_merge.transport import ApiError, Response
from fabric_merge.util import MergeError
from .support import LEFT, NEW, WS1


class PacingTests(unittest.TestCase):
    def clock(self, *, early=False):
        value = [0.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            value[0] += seconds / 2 if early and len(sleeps) == 1 else seconds

        return value, sleeps, QueryPacer(clock=lambda: value[0], sleep=sleep)

    def test_all_query_paths_share_one_pacer_across_models(self):
        value, sleeps, pacer = self.clock()
        starts = []
        client = Mock()

        def request(*args):
            starts.append(value[0])
            return Response(200, {}, {"results": [{"tables": [{"rows": []}]}]})

        client.request.side_effect = request
        api = Fabric(client, query_pacer=pacer)
        for _ in range(130):
            api.rows(WS1, LEFT, 'EVALUATE ROW("V", 1)')
            api.query_response(WS1, NEW, 'EVALUATE ROW("V", 1)')
        self.assertEqual(len(starts), 260)
        self.assertTrue(all(b - a >= 0.55 - 1e-12 for a, b in zip(starts, starts[1:])))
        self.assertTrue(
            all(starts[i + 120] - starts[i] >= 66 - 1e-10 for i in range(140))
        )

    def test_early_sleep_wakeup_does_not_allow_an_early_query(self):
        value, sleeps, pacer = self.clock(early=True)
        pacer.wait()
        pacer.wait()
        self.assertEqual(len(sleeps), 2)
        self.assertAlmostEqual(value[0], 0.55)

    def test_slow_query_needs_no_extra_wait_and_invalid_interval_rejected(self):
        value, sleeps, pacer = self.clock()
        pacer.wait()
        value[0] = 3
        pacer.wait()
        self.assertEqual(sleeps, [])
        for interval in (0, -1, 0.5, float("inf"), float("nan")):
            with self.assertRaises(MergeError):
                QueryPacer(interval)

    def test_query_errors_are_not_automatically_replayed(self):
        _, _, pacer = self.clock()
        client = Mock()
        client.request.side_effect = ApiError("POST", "url", 429, {})
        api = Fabric(client, query_pacer=pacer)
        with self.assertRaises(ApiError):
            api.rows(WS1, LEFT, 'EVALUATE ROW("V", 1)')
        self.assertEqual(client.request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
