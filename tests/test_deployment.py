"""Small fixtures exercise guard boundaries and fail-closed asset verification."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import assets
import control
import download


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.mock = patch.object(assets, "ROOT", self.root)
        self.mock.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.mock.stop)

    def job(self, name="fixture", data=b"fixture"):
        return dict(name=name, path=self.root / name, repo="example/repo", revision="pinned",
                    bytes=len(data), algorithm="sha256", hash=hashlib.sha256(data).hexdigest())

    def test_verified_marker_detects_changed_file_and_revision(self):
        j = self.job()
        j["path"].write_bytes(b"fixture")
        self.assertFalse(assets.verified(j))
        self.assertTrue(assets.verify(j))
        self.assertTrue(assets.verified(j))
        self.assertFalse(assets.verified({**j, "revision": "changed"}))
        j["path"].write_bytes(b"corrupt")
        self.assertFalse(assets.verify(j, force=True))

    def test_git_blob_hash(self):
        j = self.job()
        j.update(algorithm="git-sha1", hash=hashlib.sha1(b"blob 7\0fixture").hexdigest())
        j["path"].write_bytes(b"fixture")
        self.assertTrue(assets.verify(j))

    def test_missing_and_malformed_marker(self):
        j = self.job()
        self.assertFalse(assets.verify(j))
        j["path"].write_bytes(b"fixture")
        marker = assets.marker_path(j)
        marker.parent.mkdir(parents=True)
        marker.write_text("broken json")
        self.assertFalse(assets.verified(j))
        self.assertTrue(assets.verify(j))

    def test_resume_complete_partial_requires_matching_hash(self):
        j = self.job()
        j["path"].with_name("fixture.partial").write_bytes(b"fixture")
        with patch.object(download.subprocess, "run") as curl:
            download.fetch(j)
        curl.assert_not_called()
        self.assertTrue(assets.verified(j))

    def test_corrupt_download_never_publishes_marker(self):
        j = self.job()
        def fake_curl(*args, **kwargs):
            j["path"].with_name("fixture.partial").write_bytes(b"corrupt")
        with patch.object(download.subprocess, "run", side_effect=fake_curl), \
             patch.object(download.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                download.fetch(j)
        self.assertFalse(j["path"].exists())
        self.assertFalse(assets.marker_path(j).exists())

    def test_assembly_verifies_whole_before_deleting_parts(self):
        parts = [self.job("part01", b"abc"), self.job("part02", b"def")]
        for j, data in zip(parts, [b"abc", b"def"]):
            j["path"].write_bytes(data)
            self.assertTrue(assets.verify(j))
        whole = self.job("model.gguf", b"abcdef")
        with patch.object(download, "whole_model", return_value={**whole, "hash": "wrong"}):
            with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                download.assemble(parts)
        self.assertTrue(all(j["path"].exists() for j in parts))
        self.assertFalse(whole["path"].exists())
        with patch.object(download, "whole_model", return_value=whole):
            download.assemble(parts)
        self.assertTrue(assets.verified(whole))
        self.assertTrue(all(not j["path"].exists() for j in parts))


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((control.ROOT / "service.json").read_text())
        self.base = {k: 0 for k in ["rss", "footprint", "wired", "compressed", "swap_used", "swapouts"]}
        self.base["pressure"] = 1

    def test_limits_and_boundaries(self):
        self.assertEqual(control.guard_reasons(self.base, self.base, self.config, 0), [])
        for field, limit in [("rss", 120*2**30), ("footprint", 120*2**30),
                             ("wired", 120*2**30), ("compressed", 24*2**30),
                             ("swap_used", 8192*2**20), ("swapouts", 8192*2**20)]:
            with self.subTest(field=field):
                self.assertEqual(control.guard_reasons({**self.base, field: limit}, self.base, self.config, 0), [])
                self.assertTrue(control.guard_reasons({**self.base, field: limit+1}, self.base, self.config, 0))

    def test_pressure_and_relative_growth(self):
        self.assertTrue(control.guard_reasons({**self.base, "pressure": 4}, self.base, self.config, 0))
        self.assertEqual(control.guard_reasons({**self.base, "pressure": 2}, self.base, self.config, 1799), [])
        self.assertTrue(control.guard_reasons({**self.base, "pressure": 2}, self.base, self.config, 1800))
        prior = {**self.base, "swap_used": 20*2**30, "swapouts": 40*2**30, "compressed": 30*2**30}
        self.assertEqual(control.guard_reasons(prior, prior, self.config, 0), [])

    def test_probe_failure_is_not_zero_memory(self):
        with patch.object(control.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "")):
            with self.assertRaisesRegex(RuntimeError, "probe failed"):
                control.sample(1)
        for data in [dict(self.base, total=2**37, process_ok=False),
                     dict(self.base, total=2**37, process_ok=True, pressure=0),
                     dict(self.base, total=2**37, process_ok=True, wired=-1)]:
            with patch.object(control.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(data))):
                with self.assertRaises(RuntimeError):
                    control.sample(1)


if __name__ == "__main__":
    unittest.main()
