"""Weekly schedule objectives, uncertainty and read-only CLI/evidence integration."""

import contextlib
import copy
import hashlib
import io
import itertools
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import Mock, patch

from fabric_merge.cli import main
from fabric_merge.schedules import (
    DAYS,
    events,
    native_candidates,
    normalize,
    observe_schedules,
    recommend_schedules,
    verify_suggestion,
    write_suggestion,
)
from fabric_merge.util import MergeError, digest, load, save
from fabric_merge.workflow import Migration, create_plan, open_plan
from .support import LEFT, RIGHT, SECURITY, WS1, WS2, service


def schedule(days=("Monday",), times=("08:00",), zone="UTC", enabled=True):
    return {
        "enabled": enabled,
        "days": list(days),
        "times": list(times),
        "localTimeZoneId": zone,
    }


def snapshot(left=None, right=None):
    return {
        "schema_version": 1,
        "sources": {
            name: {
                "workspace_id": WS1,
                "id": model,
                "status": "observed",
                "schedule": value if value is not None else schedule(),
            }
            for name, model, value in (("left", LEFT, left), ("right", RIGHT, right))
        },
    }


class ScheduleTests(unittest.TestCase):
    def test_identical_schedules_deduplicate_without_measure_intersection(self):
        result = recommend_schedules(snapshot(), "premium")
        self.assertTrue(result["ready"])
        selected = result["recommended"]
        self.assertEqual(selected["original_model_runs_per_week"], 2)
        self.assertEqual(selected["runs_per_week"], 1)
        self.assertEqual(selected["exactly_matched_model_runs"], 2)
        self.assertEqual(selected["extra_runs"], [])
        self.assertNotIn("savings", result)

    def test_different_times_same_days_preserve_both(self):
        days = DAYS[:5]
        result = recommend_schedules(
            snapshot(schedule(days, ["08:00"]), schedule(days, ["16:00"])), "shared"
        )
        selected = result["recommended"]
        self.assertEqual(selected["schedule"]["times"], ["08:00", "16:00"])
        self.assertEqual(selected["runs_per_week"], 10)
        self.assertEqual(selected["exactly_matched_model_runs"], 10)
        self.assertEqual(selected["extra_runs"], [])

    def test_different_days_expose_cartesian_extras_and_no_extra_alternative(self):
        result = recommend_schedules(
            snapshot(schedule(), schedule(["Tuesday"], ["16:00"])), "shared"
        )
        selected = result["recommended"]
        self.assertEqual(selected["runs_per_week"], 4)
        self.assertEqual(
            selected["extra_runs"],
            [{"day": "Monday", "time": "16:00"}, {"day": "Tuesday", "time": "08:00"}],
        )
        alternative = result["alternatives"]["8"]["no_extra_runs"]
        self.assertEqual(alternative["runs_per_week"], 1)
        self.assertEqual(alternative["extra_runs"], [])
        self.assertEqual(alternative["exactly_matched_model_runs"], 1)

    def test_capacity_cap_ranks_old_start_event_counts_and_reports_missing_delays(self):
        left = schedule(DAYS, [f"{h:02}:00" for h in range(8)])
        right = schedule(["Sunday"], ["23:00"])
        result = recommend_schedules(snapshot(left, right), "shared")
        selected = result["recommended"]
        self.assertEqual(len(selected["schedule"]["times"]), 8)
        self.assertEqual(selected["coverage"]["left"]["exactly_matched_runs"], 56)
        self.assertEqual(selected["coverage"]["right"]["exactly_matched_runs"], 0)
        self.assertEqual(
            selected["coverage"]["right"]["missing_runs"],
            [{"day": "Sunday", "time": "23:00"}],
        )
        self.assertEqual(
            selected["coverage"]["right"]["max_next_start_delay_wall_minutes"], 60
        )
        premium = recommend_schedules(snapshot(left, right), "premium")
        self.assertEqual(premium["recommended"]["exactly_matched_model_runs"], 57)

    def test_unknown_target_capacity_is_not_inferred_from_sources(self):
        result = recommend_schedules(snapshot())
        self.assertEqual(result["status"], "needs_capacity_review")
        self.assertFalse(result["ready"])
        self.assertIsNone(result["proposed_patch_body"])
        self.assertEqual(set(result["alternatives"]), {"8", "48"})

    def test_one_disabled_has_no_active_slots_or_zone_conflict(self):
        result = recommend_schedules(
            snapshot(
                schedule(enabled=False, zone="Eastern Standard Time"),
                schedule(zone="W. Europe Standard Time"),
            ),
            "premium",
        )
        self.assertTrue(result["ready"])
        self.assertEqual(
            result["recommended"]["coverage"]["left"]["original_runs_per_week"], 0
        )
        self.assertEqual(
            result["recommended"]["schedule"]["localTimeZoneId"],
            "W. Europe Standard Time",
        )

    def test_both_disabled_propose_only_disabled_no_other_patch_changes(self):
        result = recommend_schedules(snapshot({"enabled": False}, {"enabled": False}))
        self.assertTrue(result["ready"])
        self.assertEqual(result["proposed_patch_body"], {"value": {"enabled": False}})

    def test_different_active_zones_block_fixed_offset_guess(self):
        result = recommend_schedules(
            snapshot(schedule(), schedule(zone="W. Europe Standard Time")), "premium"
        )
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["recommended"])
        self.assertIn("daylight", result["problems"][0])

    def test_same_windows_zone_is_preserved_without_iana_or_fixed_offset_conversion(
        self,
    ):
        result = recommend_schedules(
            snapshot(
                schedule(zone="W. Europe Standard Time"),
                schedule(zone="W. Europe Standard Time"),
            ),
            "premium",
        )
        self.assertEqual(
            result["proposed_patch_body"]["value"]["localTimeZoneId"],
            "W. Europe Standard Time",
        )

    def test_omitted_defaults_and_malformed_schedule_are_unknown(self):
        mutations = [
            {"enabled": None},
            {"enabled": "true"},
            {"days": []},
            {"days": ["monday"]},
            {"times": []},
            {"times": None},
            {"times": ["24:00"]},
            {"times": ["8:00"]},
            {"times": ["08:0١"]},
            {"times": [None]},
            {"localTimeZoneId": None},
            {"localTimeZoneId": "UTC "},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                result = recommend_schedules(
                    snapshot({**schedule(), **mutation}), "shared"
                )
                self.assertEqual(result["status"], "incomplete")
                self.assertIsNone(result["proposed_patch_body"])

    def test_duplicate_days_and_times_are_set_values_and_notifications_not_copied(self):
        raw = {
            **schedule(["Monday", "Monday"], ["08:00", "08:00"]),
            "notifyOption": "MailOnFailure",
        }
        result = recommend_schedules(snapshot(raw, raw), "shared")
        self.assertEqual(result["recommended"]["runs_per_week"], 1)
        self.assertNotIn("notifyOption", result["proposed_patch_body"]["value"])

    def test_maximum_native_schedule_sizes_stay_bounded(self):
        a = [f"{m // 60:02}:{m % 60:02}" for m in range(0, 1440, 30)]
        b = [f"{m // 60:02}:{m % 60:02}" for m in range(15, 1440, 30)]
        result = recommend_schedules(
            snapshot(schedule(DAYS, a), schedule(DAYS, b)), "premium"
        )
        selected = result["recommended"]
        self.assertEqual(len(selected["schedule"]["times"]), 48)
        self.assertEqual(selected["original_model_runs_per_week"], 672)
        self.assertEqual(selected["exactly_matched_model_runs"], 336)

    def test_small_domain_exhaustive_oracle_for_both_optimization_objectives(self):
        rng = random.Random(25)
        days, times = DAYS[:3], ["06:00", "08:00", "16:00", "20:00"]
        for _ in range(35):
            normalized = {
                name: normalize(
                    schedule(
                        rng.sample(days, rng.randint(1, 3)),
                        rng.sample(times, rng.randint(1, 4)),
                    )
                )
                for name in ("left", "right")
            }
            originals = {name: events(s) for name, s in normalized.items()}
            union = set().union(*originals.values())
            used_days = sorted({d for d, _ in union})
            used_times = sorted({t for _, t in union})
            limit = 2

            def score(slots):
                return sum(len(slots & e) for e in originals.values())

            coverage_scores, no_extra_scores = [], [(0, 0)]
            for n in range(1, min(limit, len(used_times)) + 1):
                for ts in itertools.combinations(used_times, n):
                    slots = set(itertools.product(used_days, ts))
                    coverage_scores.append((score(slots), -len(slots - union)))
                    for d in range(1, len(used_days) + 1):
                        for ds in itertools.combinations(used_days, d):
                            subset = set(itertools.product(ds, ts))
                            if subset <= union:
                                no_extra_scores.append((score(subset), -len(subset)))
            variants = native_candidates(normalized, limit)
            cover, no_extra = variants["coverage_first"], variants["no_extra_runs"]
            self.assertEqual(
                (cover["exactly_matched_model_runs"], -len(cover["extra_runs"])),
                max(coverage_scores),
            )
            self.assertEqual(
                (no_extra["exactly_matched_model_runs"], -no_extra["runs_per_week"]),
                max(no_extra_scores),
            )

    def test_unavailable_get_is_not_disabled_and_only_gets_are_sent(self):
        with service() as (api, state):
            state.schedule_errors[RIGHT] = 403
            observed = observe_schedules(api, (WS1, LEFT), (WS2, RIGHT))
            self.assertEqual(observed["sources"]["right"]["http_status"], 403)
            self.assertEqual(len(state.calls), 2)
            self.assertTrue(
                all(
                    method == "GET" and path.endswith("/refreshSchedule")
                    for method, path, _ in state.calls
                )
            )
            self.assertIn("/groups/" + WS2 + "/", state.calls[1][1])
            self.assertEqual(
                recommend_schedules(observed, "premium")["status"], "incomplete"
            )

    def test_incomplete_schedule_observation_does_not_block_merge_plan_and_is_hashed(
        self,
    ):
        with service() as (api, state), tempfile.TemporaryDirectory() as tmp:
            state.schedule_errors[RIGHT] = 403
            directory = Path(tmp) / "plan"
            plan = create_plan(
                api,
                directory,
                (WS1, LEFT),
                (WS1, RIGHT),
                WS1,
                "Merged",
                security=SECURITY,
            )
            self.assertTrue(plan["ready"])
            self.assertEqual(plan["refresh_schedule"]["status"], "incomplete")
            self.assertIn("refresh-schedules.json", plan["artifact_hashes"])
            open_plan(directory)
            save(directory / "refresh-schedules.json", {})
            with self.assertRaisesRegex(MergeError, "artifact changed"):
                open_plan(directory)

    def test_standalone_cli_plan_observation_leaves_plan_and_journal_unchanged(self):
        with service() as (api, state), tempfile.TemporaryDirectory() as tmp:
            directory, out = Path(tmp) / "plan", Path(tmp) / "proposal"
            create_plan(
                api,
                directory,
                (WS1, LEFT),
                (WS1, RIGHT),
                WS1,
                "Merged",
                security=SECURITY,
            )
            before = {str(p): p.read_bytes() for p in directory.iterdir()}
            state.schedules[RIGHT]["times"] = ["16:00"]
            state.calls.clear()
            credential = Mock()
            credential.account.return_value = {"tenant_id": "test"}
            with (
                patch("fabric_merge.cli.AzureCliCredential", return_value=credential),
                patch("fabric_merge.cli.Client", return_value=api.client),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    main(
                        [
                            "suggest-schedule",
                            str(directory),
                            "--capacity",
                            "premium",
                            "--out",
                            str(out),
                        ]
                    ),
                    0,
                )
            self.assertEqual(len(state.calls), 2)
            self.assertTrue(all(c[0] == "GET" for c in state.calls))
            self.assertEqual(
                before, {str(p): p.read_bytes() for p in directory.iterdir()}
            )
            self.assertEqual(
                load(out / "suggestion.json")["recommended"]["schedule"]["times"],
                ["08:00", "16:00"],
            )
            self.assertTrue(verify_suggestion(out)["verified"])
            self.assertEqual((out / "snapshot.json").stat().st_mode & 0o777, 0o600)

    def test_offline_cli_never_authenticates_and_fresh_output_is_required(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch(
                "fabric_merge.cli.AzureCliCredential",
                side_effect=AssertionError("Must be offline"),
            ),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            source, out = Path(tmp) / "input.json", Path(tmp) / "out"
            save(source, snapshot())
            self.assertEqual(
                main(
                    [
                        "suggest-schedule",
                        "--snapshot",
                        str(source),
                        "--capacity",
                        "shared",
                        "--out",
                        str(out),
                    ]
                ),
                0,
            )
            self.assertEqual(main(["verify-schedule", str(out)]), 0)
            before = (out / "snapshot.json").read_bytes()
            self.assertEqual(
                main(
                    ["suggest-schedule", "--snapshot", str(source), "--out", str(out)]
                ),
                1,
            )
            self.assertEqual((out / "snapshot.json").read_bytes(), before)

    def test_hashes_and_rehashed_fabricated_recommendations_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle"
            raw = snapshot()
            write_suggestion(out, raw, recommend_schedules(raw, "shared"))
            result = load(out / "suggestion.json")
            result["recommended"]["runs_per_week"] = 9
            save(out / "suggestion.json", result)
            with self.assertRaisesRegex(MergeError, "evidence changed"):
                verify_suggestion(out)
            manifest = load(out / "manifest.json")
            manifest["files"]["suggestion.json"] = hashlib.sha256(
                (out / "suggestion.json").read_bytes()
            ).hexdigest()
            save(out / "manifest.json", manifest)
            with self.assertRaisesRegex(MergeError, "does not replay"):
                verify_suggestion(out)

    def test_unknown_and_incomplete_evidence_replay_and_report_order_survives_json_sorting(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            unknown = snapshot()
            incomplete = snapshot(schedule(), schedule(zone="Eastern Standard Time"))
            for name, raw in (("unknown", unknown), ("incomplete", incomplete)):
                out = Path(tmp) / name
                write_suggestion(out, raw, recommend_schedules(raw))
                self.assertTrue(verify_suggestion(out)["verified"])
                self.assertFalse(verify_suggestion(out)["ready"])

    def test_malformed_manifest_has_actionable_error_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            for malformed in ([], None, {}, {"schema_version": 1, "files": []}):
                save(Path(tmp) / "manifest.json", malformed)
                with self.assertRaisesRegex(MergeError, "Invalid schedule manifest"):
                    verify_suggestion(tmp)

    def test_new_schedule_capability_does_not_invalidate_old_plan_format(self):
        with service() as (api, state), tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "plan"
            plan = create_plan(
                api,
                directory,
                (WS1, LEFT),
                (WS1, RIGHT),
                WS1,
                "Merged",
                security=SECURITY,
            )
            legacy = copy.deepcopy(plan)
            del legacy["refresh_schedule"]
            for name in ("refresh-schedules.json", "refresh-schedule-suggestion.json"):
                del legacy["artifact_hashes"][name]
                (directory / name).unlink()
            save(directory / "plan.json", legacy)
            self.assertEqual(digest(Migration(api, directory).plan), digest(legacy))


if __name__ == "__main__":
    unittest.main()
