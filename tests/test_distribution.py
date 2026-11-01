"""Regression guards for publishing private content and stale package versions."""

import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from fabric_merge import __version__
from fabric_merge.cli import main


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_check", ROOT / "scripts/check_release.py"
)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class DistributionTests(unittest.TestCase):
    def test_version_needs_no_azure_cli_or_network(self):
        output = io.StringIO()
        with (
            patch("fabric_merge.cli.AzureCliCredential") as credential,
            contextlib.redirect_stdout(output),
        ):
            with self.assertRaises(SystemExit) as result:
                main(["--version"])
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), f"fabric-merge {__version__}")
        credential.assert_not_called()
        self.assertEqual(CHECK.version(ROOT), __version__)

    def test_private_paths_and_archive_traversal_are_rejected(self):
        for name in [
            "runs/model.json",
            "../outside.py",
            "/etc/passwd",
            "formal/.tools/tool.jar",
            "src/fabric_merge/pilot.py",
            ".env.production",
            "key.pem",
        ]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                CHECK.check_path(name)

    def test_obvious_credentials_and_machine_paths_are_rejected(self):
        values = [
            b"/" + b"Volumes/private/data",
            b"-----BEGIN " + b"PRIVATE KEY-----",
            b"ghp_" + b"A" * 36,
            b"eyJ" + b"a" * 20 + b".eyJ" + b"b" * 20 + b"." + b"c" * 20,
        ]
        for data in values:
            with self.subTest(data=data[:8]), self.assertRaises(ValueError):
                CHECK.check_content("fixture", data)

    def test_candidate_public_tree_contains_no_private_paths_or_credentials(self):
        paths = list(CHECK.public_files(ROOT))
        self.assertTrue(paths)
        for p in paths:
            CHECK.check_path(str(p.relative_to(ROOT)))
            CHECK.check_content(str(p.relative_to(ROOT)), p.read_bytes())

    def test_release_tag_mismatch_blocks_before_build_or_publication(self):
        with self.assertRaisesRegex(ValueError, "Tag does not match"):
            CHECK.check(ROOT / "not-a-dist", tag="v999.0.0")

    def test_only_one_sdist_and_wheel_can_be_released(self):
        with self.assertRaisesRegex(ValueError, "exactly one wheel"):
            CHECK.check(ROOT / "not-a-dist")
