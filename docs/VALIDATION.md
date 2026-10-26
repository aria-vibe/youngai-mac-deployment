# Validation / 验证记录

Recorded 2026-10-26 on M5 Max (18 CPU / 40 GPU cores), 128 GiB unified memory,
macOS 26.6.2. All memory figures use binary units unless explicitly marked GB.

记录环境：M5 Max（18 核 CPU / 40 核 GPU）、128 GiB 统一内存、macOS 26.6.2。
除明确标注 GB 外，内存单位均为二进制单位。

## Deployment package / 部署包

| Check / 检查 | Result / 结果 |
| --- | --- |
| Fresh upstream fetch + patch + Metal build / 全新获取、补丁、构建 | PASS: `ds4`, `ds4-server`, `ds4_test`, `memory_probe` |
| Rerun bootstrap / 重复应用检查 | PASS: `python3 bootstrap.py --fetch-only` |
| Source identity / 源码一致性 | All 30 patched files byte-identical to the existing deployment / 30 个补丁文件与现有部署逐字节一致 |
| Python deployment tests / Python 部署测试 | 10 tests passed; includes two real dummy-child stop cases / 10 项通过，含两个真实模拟子进程停止场景 |
| Real download / 真实下载 | Pinned 1,115-byte model LICENSE downloaded and Git-blob SHA-1 verified / 固定版本许可证下载和校验通过 |
| Model-free Metal/API suites / 无模型 Metal/API 套件 | PASS: `--metal-v41 --server --engine-units --rax --tp-allreduce` |
| Engine line count and script syntax / 行数和脚本语法 | PASS |
| Original deployment / 原部署 | Same supervisor/server PIDs; read-only `/v1/models` and `/metrics` checks passed / 进程未替换，只读接口检查通过 |

The new package did not redownload 318 GB or launch another full model. The
downloader's resume, corruption rejection, and assembly ordering were tested
with small fixtures; only a small real upstream file was fetched. This is not a
claim of a new end-to-end 1M generation test.

本次未重新下载约 318 GB，也未启动第二个完整模型。续传、损坏文件拒绝和合并顺序使用
小型夹具测试，真实网络下载验证使用小文件。本次验证不是一次新的 1M 完整生成测试。

Reproduce from the repository root / 在仓库根目录复现：

```sh
python3 bootstrap.py
python3 -m unittest discover -s tests -v
python3 download.py --plan
make -C source linecount
cd source
python3 ../tests/metal_guard.py \
  --probe ../memory_probe --out ../verification/metal-check \
  --process-gib 8 --wired-gib 120 --swap-mib 8192 --compressed-gib 24 -- \
  ./ds4_test --metal-v41 --server --engine-units --rax --tp-allreduce
```

Use a fresh `--out` directory on each run. The Python process integration test
allocates about 64 MiB in a dummy executable; it does not load weights. It checks
both the configured memory threshold and an injected probe failure, then confirms
the child was killed and reaped. It is skipped without macOS and a built probe;
the stock 128 GiB hardware/normal-pressure startup checks remain active.

每次使用新的 `--out` 目录。Python 进程集成测试只让模拟程序分配约 64 MiB，
不加载权重；分别触发内存阈值和探针故障，确认子进程已被终止并回收。
非 macOS 或未编译探针时跳过；128 GiB 机器和 normal 压力的启动检查仍生效。

The Metal suite includes synthetic prefix-capacity cases at 70,754, 524,288 and
1,048,560 tokens, failed checkpoint copying, shrink/reuse, and adaptive OOM
rollback/retry. These use small model shapes and do not measure real-model memory
or quality at those lengths. The vision external-golden test requires separately
generated fixtures; a skip of that optional fixture is not a new vision-quality pass.

Metal 套件包含 70,754、524,288、1,048,560 tokens 的合成容量用例，覆盖检查点复制失败、
缩容/复用和自适应 OOM 回滚重试。这些使用小模型形状，不能代表相应长度的实际模型内存或质量。
视觉外部金标需要另行生成夹具，可选夹具的跳过不等于重新通过视觉质量验证。

## Historical model evidence / 历史模型实测

These measurements come from earlier local runs, with different explicitly scoped
builds and output lengths. Do not compare them as one benchmark.

以下来自此前本机测试，构建和输出长度不同，不能拼接成同一组性能对照。

| Scenario / 场景 | Observed / 结果与范围 |
| --- | --- |
| Correctness/resource PR, 8K input, 128 output tokens / PR 对照 | DSpark off **8.4** → on **13.7 token/s** median (1.63×); paired text/reasoning bytes matched / 对应正文与思考逐字节一致 |
| Same PR, 73,696-token prefix + 12 follow-ups / 同一 PR 长前缀续写 | Post-request Metal allocation range 0.12 MiB; cancellation recovered in 3.67 s / 请求结束后分配量波动 0.12 MiB，取消后 3.67 秒恢复 |
| Adaptive prefill deployment, 260,151 input / 自适应分块部署 | Reused 254,664 tokens; 5,487-token suffix completed with 256-row chunks; first token 36.43 s; three retrieval values correct / 命中后补算完成，三处检索正确 |
| Earlier 80%-of-1M test / 较早的 1M 窗口 80% 测试 | 838,860 input + 31 output tokens, **DSpark off**; first token about 6,950 s, 6.33 token/s over only 31 decode tokens; all three retrieval values correct / DSpark 关闭，仅 31 个输出 tokens，检索正确 |

The earlier 838K run used a 110,000 MiB engine budget and an 8K output cap, before
the final adaptive/vision patch. It does **not** validate the complete published
profile at 80% of 1M. The current 32K cap was separately checked with a short
response; a full 32K output was not generated in that cap check.

较早的 838K 测试使用 110,000 MiB 预算和 8K 输出上限，早于最终自适应/视觉补丁，
**不能**证明当前发布的完整配置在 1M 窗口 80% 时的表现。32K 上限另用短回复核对，
该检查没有生成完整 32K 输出。

The upstream PR's full reproducible report, including baseline/fixed results,
model hashes, measurement methodology and quality limitations, is pinned here:
[METAL_CORRECTNESS_REPRO.md](https://github.com/aria-vibe/YoungAi/blob/d5bdae26c725e13c338f2264ee7d8c0cd4a2e8cf/docs/METAL_CORRECTNESS_REPRO.md).
The PR retains the original chunk policy and excludes this package's adaptive
prefill/vision additions. Its five known baseline offload fixture failures and
baseline CPU CLI build errors are not represented as passing. CUDA hardware and
independent official V4.1 quality validation remain outstanding.

上述固定链接包含 PR 的完整复现报告、模型哈希、基线/修复后对照和质量限制。
PR 保留原分块策略，不包含本包的自适应/视觉扩展。5 项已有 offload 夹具失败和已有
CPU CLI 构建错误没有被当作通过；CUDA 硬件及独立官方 V4.1 质量验证仍未完成。
