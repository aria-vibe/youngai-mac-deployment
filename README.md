# YoungAi on a 128 GiB Mac

**English | [简体中文](README.zh-CN.md)**

A reproducible personal deployment of [YoungAi](https://github.com/fodelf/YoungAi)
for DeepSeek-V4.1-Flash-1017 on Apple Silicon. Includes a pinned engine patch,
verified model downloads, a local OpenAI-compatible API, and an independent
memory watchdog. The reference machine is an **M5 Max, 128 GiB unified memory,
macOS 26.6.2**. Other Macs have not been validated with this profile.

This repository shares configuration and source changes. **Weights, caches,
request history, logs, binaries, and virtual environments are not included.**
It does not install or start Splash or MLX-Audio.

## What this profile enables

| Setting | Value |
| --- | --- |
| Engine | C/Objective-C + Metal; pinned upstream commit and local patch |
| Model | YoungAi `vq8sh14-q4k-mtpnative`, with code GRRB and posttrain sidecars |
| Context capacity | Native **1,048,576 tokens**, read from the model metadata |
| Output limit | **32,768 tokens** per request; a lower client limit still applies |
| DSpark | Enabled; local arithmetic/upload consistency fixes included |
| Prefill | Memory-adaptive chunks and bounded retry after confirmed Metal OOM |
| Vision | Enabled with paired vision weights and local Metal image operators |
| API | `127.0.0.1`, first available port in **9000–9099** |
| Engine budget | **114,000 MiB**; external watchdog remains necessary |
| Prefix cache | In-memory V4.1 checkpoints; lost on restart |
| Disk KV cache | **Not wired into this V4.1 path** |

The 1M figure is the window capacity, not a guarantee of latency or success under
every memory condition. Input, image tokens, and generated tokens share the window.
There is no `--ctx` switch for this pinned V4.1 engine. Engram reads from SSD are
model parameter lookups, **not persistent conversation/KV caching**.

The correctness/resource-lifetime subset is proposed in
[upstream draft PR #6](https://github.com/fodelf/YoungAi/pull/6).
This deployment patch additionally includes adaptive prefill, OOM retry, and
Metal vision changes; it is larger than that PR and is not an upstream release.

## Requirements and storage

- Apple Silicon Mac with **at least 128 GiB unified memory**. The launcher rejects
  smaller machines and refuses startup unless system memory pressure is normal.
- Xcode Command Line Tools (`xcode-select --install`), Git, `make`, `cc`, `curl`,
  and Python **3.9+**. The deployment scripts use only the Python standard library.
- A fast local SSD and **500 GB free before the initial download** is recommended.
  Downloaded assets total **317.75 GB** in decimal units: a 113.56 GB main GGUF,
  203.07 GB of Engram shards, and roughly 1.12 GB of vision/sidecars/metadata.
  Assembly temporarily needs another 113.56 GB. Verified model parts are removed
  only after the assembled file passes its fixed SHA-256 check.

The GGUF is mmap-backed and Engram is accessed on demand. Disk file size is not
the process RSS or total GPU memory. Do not launch a second full model alongside
this one; the engine retains its machine-wide instance lock.

## Install

```sh
git clone https://github.com/aria-vibe/youngai-mac-deployment.git
cd youngai-mac-deployment

python3 bootstrap.py
python3 download.py --plan
python3 download.py
```

`bootstrap.py` fetches the exact commit in [engine.lock.json](engine.lock.json),
checks the patch SHA-256, applies it, and builds the server, CLI, test binary, and
memory probe. It does not load a model. Rerunning it accepts an already applied
patch and refuses to reset a checkout at another revision. The generated `source/`
directory is ignored by this deployment repository.

The downloader pins both Hugging Face revisions and verifies every selected
asset against [manifests/](manifests/). Downloads resume from `.partial` files and
use two concurrent transfers by default (`--workers 1` for lower I/O concurrency).
Rerun after interruption. Avoid modifying or deleting a partial file while it is
downloading. Download and assembly use small streaming buffers, never a full
model-sized Python allocation.

To reuse an existing identical installation, copy or link its `models/model/` and
`models/deepseek-engram/` directories into this checkout, then run
`python3 download.py --verify`. This rehashes all installed assets and creates
local verification markers; it may take several minutes. Normal startup checks
those markers, sizes, revisions, and modification times instead of rehashing
318 GB on every start. `download.py` can retrieve any missing assets.

## Run and use the API

```sh
./start.sh
./status.sh
python3 smoke.py
# Optional: python3 smoke.py --image /path/to/example.png
./stop.sh
```

`start.sh` reports process startup, not API readiness. Allow model initialization
to finish before running the smoke check. The chosen address is in
`run/service.json` → `base_url`. If startup is refused, read
`logs/supervisor.log`; engine output and watchdog samples go to `logs/`.
`stop.sh` requests shutdown, and the supervisor terminates the child and releases
the model. Confirm completion with `status.sh`.

Clients can use `http://127.0.0.1:9000/v1` when port 9000 is available. Query
`GET /v1/models` for the model ID. `POST /v1/chat/completions` supports text and
image content, `GET /monitor` shows the engine monitor, and `GET /metrics` returns
metrics. `smoke.py` discovers the actual port and model ID automatically.

Image inputs use OpenAI `image_url` **base64 data URLs**, or Anthropic base64 image
sources. The engine does not fetch remote HTTP image URLs. PNG/JPEG text-plus-image
examples are available through `smoke.py --image`; larger images can take much
longer to encode.

For a trusted LAN, change `host` in [service.json](service.json) to `0.0.0.0`, then
stop/start. On another computer, use the Mac's LAN IP and selected port, not
`0.0.0.0`. This bare server has no configured authentication or TLS; use an
authenticated proxy if exposing it beyond trusted clients. A client requiring
an API-key field can use a placeholder, but that does not add authentication.

## Memory protection

The settings below preserve the reference deployment's relaxed 128 GiB profile.
Edit [service.json](service.json) and restart to change them.

| Watchdog condition | Stop threshold |
| --- | --- |
| Process RSS or physical footprint | Over 120 GiB |
| Whole-system wired memory | Over 120 GiB |
| System pressure | Critical immediately; warning continuously for 1,800 seconds |
| Swap use **or cumulative swap-out** growth | Over 8,192 MiB since startup |
| Compressed memory growth | Over 24 GiB since startup |
| Sampling | Every 0.5 seconds; probe failure/invalid sample stops the child |

These are sampled stop thresholds, not hard allocation limits. They do not
reserve 8 GiB of free RAM. RSS alone misses much GPU memory, so wired memory and
system pressure are monitored separately. The engine budget also cannot account
for every lazy mapping. The launcher does not change the system GPU wired limit
or close other applications. A guard stop is intentional: inspect `stop_reason`
in `run/service.json` before restarting.

## Validation and limitations

See [validation notes](docs/VALIDATION.md) for reproducible checks, recorded local
results, and their scope. The package was built from a fresh pinned checkout;
the watchdog, download verification, and asset assembly have focused tests.

Historical reference results include exact DSpark on/off output equality in
bounded 8K tests and stable post-request allocation across repeated long-prefix
requests. These do not establish universal model quality or full-window
reliability. The upstream PR remains a draft: five pre-existing Metal offload
fixture failures, CPU build issues, and outstanding CUDA/independent quality
validation are documented in its report.

## Repository contents and attribution

| Path | Purpose |
| --- | --- |
| `engine.lock.json`, `patches/` | Pinned upstream identity and deployed source changes |
| `manifests/`, `assets.py`, `download.py` | Fixed model identities, verification and download |
| `bootstrap.py` | Fetch, patch and build |
| `service.json`, `control.py`, `memory_probe.c` | Configuration, supervisor and macOS telemetry |
| `start.sh`, `status.sh`, `stop.sh`, `smoke.py` | Daily operation and API check |
| `tests/`, `docs/` | Model-free deployment tests and validation notes |

Upstream engine: [fodelf/YoungAi](https://github.com/fodelf/YoungAi).
Quantization and sidecars:
[wenzhouwu/YoungAi-DeepSeek-V4.1-Flash-1017](https://huggingface.co/wenzhouwu/YoungAi-DeepSeek-V4.1-Flash-1017).
Engram and tokenizer:
[deepseek-ai/DeepSeek-V4.1-Flash-1017](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017).
Code is distributed under [MIT](LICENSE), retaining upstream notices.
Model licenses are downloaded alongside the assets; weights are not redistributed here.
