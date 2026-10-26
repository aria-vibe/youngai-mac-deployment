"""Resumable pinned downloads and bounded-memory GGUF assembly; no inference."""
import argparse
import concurrent.futures
import fcntl
import hashlib
import os
import shutil
import subprocess
import time
from urllib.parse import quote

from assets import ROOT, digest, is_part, jobs, record, verify, whole_model


def fetch(job):
    if verify(job):
        return
    target = job["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    url = f"https://huggingface.co/{job['repo']}/resolve/{job['revision']}/{quote(job['name'])}"
    print(f"FETCH {job['name']} ({job['bytes'] / 1e9:.3f} GB)", flush=True)
    for attempt in range(4):
        try:
            if not partial.exists() or partial.stat().st_size != job["bytes"]:
                subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location",
                                "--continue-at", "-", "--connect-timeout", "30",
                                "--speed-time", "120", "--speed-limit", "1024",
                                "--output", str(partial), url], check=True)
            if partial.stat().st_size != job["bytes"]:
                raise RuntimeError("download size mismatch")
            if digest(partial, job["algorithm"]) != job["hash"]:
                partial.unlink()  # This downloader's own corrupt staging file only.
                raise RuntimeError("download hash mismatch")
            partial.replace(target)
            record(job)
            print("VERIFIED " + job["name"], flush=True)
            return
        except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
            if attempt == 3:
                raise
            print(f"RETRY {job['name']}: {error}", flush=True)
            time.sleep(5 * (attempt + 1))


def assemble(parts):
    job = whole_model()
    temporary = job["path"].with_suffix(".gguf.assembling")
    if shutil.disk_usage(job["path"].parent).free < job["bytes"] + 5 * 1024**3:
        raise RuntimeError("need another 114 GB plus 5 GiB free to assemble GGUF; parts preserved")
    h = hashlib.sha256()
    print("ASSEMBLING verified parts", flush=True)
    with temporary.open("wb") as output:
        for part in sorted(parts, key=lambda p: p["name"]):
            if not verify(part):
                raise RuntimeError("unverified model part: " + part["name"])
            with part["path"].open("rb") as handle:
                while block := handle.read(8 * 1024 * 1024):
                    h.update(block)
                    output.write(block)
        output.flush()
        os.fsync(output.fileno())
    if temporary.stat().st_size != job["bytes"] or h.hexdigest() != job["hash"]:
        raise RuntimeError("assembled model size/hash mismatch; parts preserved")
    temporary.replace(job["path"])
    record(job)
    for part in parts:
        part["path"].unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="show sizes without downloading")
    parser.add_argument("--verify", action="store_true", help="rehash installed assets; no download")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=2)
    args = parser.parse_args()
    selected = list(jobs())
    parts = [j for j in selected if is_part(j)]
    print(f"Pinned assets: {len(selected)} files, {sum(j['bytes'] for j in selected)/1e9:.2f} GB")
    print("Allow 500 GB free SSD space initially (including temporary assembly).")
    if args.plan:
        return
    (ROOT / "run").mkdir(exist_ok=True)
    with (ROOT / "run/download.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.verify:
            installed = [whole_model()] + [j for j in selected if not is_part(j)]
            bad = [j["name"] for j in installed if not verify(j, force=True)]
            if bad:
                raise RuntimeError("missing or invalid assets: " + ", ".join(bad))
        else:
            if verify(whole_model()):
                selected = [j for j in selected if not is_part(j)]
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
                list(pool.map(fetch, selected))
            if not verify(whole_model()):
                assemble(parts)
    print("ALL_ASSETS_VERIFIED", flush=True)


if __name__ == "__main__":
    main()
