"""Fetch the pinned engine, apply the recorded patch, and build for Apple Silicon."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parent


def run(*args, cwd=ROOT):
    subprocess.run(args, cwd=cwd, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, choices=range(1, 17), default=6)
    parser.add_argument("--fetch-only", action="store_true")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("This deployment targets Apple Silicon macOS only.")
    spec = json.loads((ROOT / "engine.lock.json").read_text())
    patch = ROOT / spec["patch"]
    if hashlib.sha256(patch.read_bytes()).hexdigest() != spec["patch_sha256"]:
        raise SystemExit("Patch hash does not match engine.lock.json")
    source = ROOT / "source"
    if not source.exists():
        run("git", "init", str(source))
    if not (source / ".git").exists():
        raise SystemExit("source/ exists without Git metadata; refusing to overwrite")
    current = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source,
                             text=True, capture_output=True)
    if current.returncode:
        run("git", "fetch", "--depth=1", spec["repository"], spec["commit"], cwd=source)
        run("git", "checkout", "--detach", "FETCH_HEAD", cwd=source)
    elif current.stdout.strip() != spec["commit"]:
        raise SystemExit("source/ has a different revision; refusing to reset it")
    reverse = subprocess.run(["git", "apply", "--reverse", "--check", str(patch)],
                             cwd=source, capture_output=True)
    if reverse.returncode:
        run("git", "apply", "--check", str(patch), cwd=source)
        run("git", "apply", str(patch), cwd=source)
    if args.fetch_only:
        return
    run("make", f"-j{args.jobs}", "ds4", "ds4-server", "ds4_test", cwd=source)
    run("cc", "-O2", "-Wall", "-Wextra", "memory_probe.c", "-o", "memory_probe")
    for folder in ["run", "logs", "verification"]:
        (ROOT / folder).mkdir(exist_ok=True)
    print("Build complete. Next: python3 download.py --plan")


if __name__ == "__main__":
    main()
