"""macOS integration: stop a tiny dummy child on a threshold or failed probe."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "darwin" and (ROOT / "memory_probe").exists(),
                     "requires macOS and compiled memory_probe")
class WatchdogProcessTests(unittest.TestCase):
    def test_real_child_is_reaped_on_guard_or_probe_failure(self):
        for mode, expected in [("memory", "MEMORY_GUARD:"), ("probe", "GUARD_ERROR:")]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for folder in ["source", "run", "logs", "verification"]:
                    (root / folder).mkdir()
                shutil.copy2(ROOT / "memory_probe", root / "memory_probe")
                fake = root / "source/ds4-server"
                fake.write_text(f"#!{sys.executable}\nimport time\nx = bytearray(64*1024*1024)\ntime.sleep(30)\n")
                fake.chmod(0o755)
                config = json.loads((ROOT / "service.json").read_text())
                config.update(rss_limit_gib=0.05, footprint_limit_gib=0.05, sample_seconds=0.05)
                (root / "service.json").write_text(json.dumps(config))
                code = """
import sys
from pathlib import Path
import control
control.ROOT = Path(sys.argv[1])
control.STATE = control.ROOT / 'run/service.json'
control.required_assets = lambda config: []
if sys.argv[2] == 'probe':
    original = control.sample
    def broken(pid=0):
        if pid: raise RuntimeError('injected probe failure')
        return original()
    control.sample = broken
control.supervise()
"""
                result = subprocess.run([sys.executable, "-c", code, str(root), mode], cwd=ROOT,
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                state = json.loads((root / "run/service.json").read_text())
                self.assertFalse(state["running"])
                self.assertTrue(state["stop_reason"].startswith(expected), state["stop_reason"])
                self.assertEqual(state["exit_code"], -9)
                with self.assertRaises(ProcessLookupError):
                    os.kill(state["server_pid"], 0)


if __name__ == "__main__":
    unittest.main()
