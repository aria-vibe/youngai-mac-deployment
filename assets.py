"""Pinned asset identities shared by the downloader and launcher (stdlib only)."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STEM = "DeepSeek-V4.1-Flash-1017-vq8sh14-q4k-mtpnative"
MODEL_BYTES = 113556639424
MODEL_HASH = "0da4ae2d7528ae6a60153ee072ddb4680b9d36f1c8f0b5915cdd950e89de5070"


def jobs():
    for kind, folder in [("model", "model"), ("base", "deepseek-engram")]:
        manifest = json.loads((ROOT / f"manifests/{kind}.json").read_text())
        for entry in manifest["files"]:
            yield {**entry, "repo": manifest["repo"], "revision": manifest["revision"],
                   "path": ROOT / "models" / folder / entry["name"]}


def whole_model():
    return {"name": STEM + ".gguf", "path": ROOT / "models/model" / (STEM + ".gguf"),
            "bytes": MODEL_BYTES, "algorithm": "sha256", "hash": MODEL_HASH,
            "repo": "assembled", "revision": MODEL_HASH}


def marker_path(job):
    key = hashlib.sha256((job["repo"] + job["name"]).encode()).hexdigest()
    return ROOT / "verification/download-checks" / (key + ".json")


def digest(path, algorithm):
    h = hashlib.sha1() if algorithm == "git-sha1" else hashlib.sha256()
    if algorithm == "git-sha1":
        h.update(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            h.update(block)
    return h.hexdigest()


def verified(job):
    try:
        stat = job["path"].stat()
        check = json.loads(marker_path(job).read_text())
        return (stat.st_size == job["bytes"] and check["hash"] == job["hash"]
                and check["revision"] == job["revision"] and check["mtime_ns"] == stat.st_mtime_ns)
    except (OSError, ValueError, KeyError):
        return False


def record(job):
    marker = marker_path(job)
    marker.parent.mkdir(parents=True, exist_ok=True)
    data = {k: job[k] for k in ["repo", "revision", "name", "bytes", "hash"]}
    data["mtime_ns"] = job["path"].stat().st_mtime_ns
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(marker)


def verify(job, force=False):
    if not force and verified(job):
        return True
    path = job["path"]
    if not path.is_file() or path.stat().st_size != job["bytes"]:
        return False
    if digest(path, job["algorithm"]) != job["hash"]:
        return False
    record(job)
    return True


def is_part(job):
    return job["name"].startswith(STEM + ".gguf.part")


def required_assets(config):
    yield whole_model()
    for job in jobs():
        if is_part(job):
            continue
        if job["name"].endswith(".vision.gguf") and not config["vision"]:
            continue
        if job["name"].startswith("posttrain-") and not config["posttrain"]:
            continue
        yield job
