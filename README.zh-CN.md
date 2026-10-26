# 在 128 GiB Mac 上部署 YoungAi

**[English](README.md) | 简体中文**

这是一套可复现的个人 [YoungAi](https://github.com/fodelf/YoungAi) 部署配置，
在 Apple Silicon 上运行 DeepSeek-V4.1-Flash-1017。包含固定版本的引擎补丁、
带校验的模型下载、本地 OpenAI 兼容 API，以及独立内存监控。
参考机器为 **M5 Max、128 GiB 统一内存、macOS 26.6.2**；尚未在其他 Mac 上验证此配置。

仓库分享配置和源码改动，**不包含模型权重、缓存、请求历史、日志、二进制或虚拟环境**。
不会安装或启动 Splash、MLX-Audio。

## 当前配置

| 项目 | 配置 |
| --- | --- |
| 引擎 | C/Objective-C + Metal，固定上游提交并应用本地补丁 |
| 模型 | YoungAi `vq8sh14-q4k-mtpnative`，配套 code GRRB 和 posttrain 侧车 |
| 上下文容量 | 从模型元数据读取的原生 **1,048,576 tokens** |
| 单次输出上限 | **32,768 tokens**；客户端设置更低时仍以客户端限制为准 |
| DSpark | 开启，包含本地运算一致性和上传时序修复 |
| Prefill | 按内存余量自适应分块；确认 Metal OOM 后有限次降档重试 |
| 视觉 | 开启，包含配套视觉权重和本地 Metal 图片算子 |
| API | 绑定 `127.0.0.1`，选择 **9000–9099** 的第一个可用端口 |
| 引擎内存预算 | **114,000 MiB**，仍需保留外部内存保护 |
| 前缀缓存 | V4.1 内存检查点，重启后丢失 |
| 磁盘 KV 缓存 | **尚未接入此 V4.1 路径** |

1M 表示窗口容量，不代表任意内存状态下都能成功或具有固定延迟。
输入、图片 tokens 和生成内容共用窗口。此固定版本的 V4.1 引擎没有 `--ctx` 参数。
Engram 按需从 SSD 读取的是模型参数，**不是对话或 KV 的磁盘持久缓存**。

其中正确性和资源回收修复已提交为
[上游 Draft PR #6](https://github.com/fodelf/YoungAi/pull/6)。
本仓库补丁还包含自适应 prefill、OOM 重试和 Metal 视觉，因此范围大于该 PR，
不等同于上游正式发布版。

## 环境与磁盘要求

- Apple Silicon Mac，**至少 128 GiB 统一内存**。启动器拒绝更小内存的机器，
  系统内存压力必须为 normal 才能启动。
- Xcode Command Line Tools（`xcode-select --install`）、Git、`make`、`cc`、
  `curl` 和 Python **3.9+**。部署脚本仅使用 Python 标准库。
- 快速本地 SSD，建议首次下载前准备 **500 GB 可用空间**。
  资源下载总量约 **317.75 GB**（十进制）：主 GGUF 113.56 GB、Engram 两片共
  203.07 GB，其余视觉/侧车/元数据约 1.12 GB。合并 GGUF 时还需临时占用
  113.56 GB；整文件通过固定 SHA-256 校验后才删除已验证的下载分片。

主 GGUF 使用 mmap，Engram 按需读取；磁盘文件大小不等于进程 RSS 或 GPU 总内存。
不要同时加载第二个完整模型。引擎保留全机单实例锁。

## 安装

```sh
git clone https://github.com/aria-vibe/youngai-mac-deployment.git
cd youngai-mac-deployment

python3 bootstrap.py
python3 download.py --plan
python3 download.py
```

`bootstrap.py` 下载 [engine.lock.json](engine.lock.json) 固定的上游提交，
校验补丁 SHA-256、应用补丁并编译服务、CLI、测试程序和内存探针，不会加载模型。
重复运行允许补丁已应用的状态；遇到不同提交的源码目录会拒绝重置。
生成的 `source/` 目录由本部署仓库忽略。

下载器使用 [manifests/](manifests/) 中固定的两个 Hugging Face revision，逐文件校验。
支持从 `.partial` 文件续传，默认两个并发下载，可用 `--workers 1` 降低 I/O 并发。
中断后重新执行即可；下载期间不要手动修改或删除分片。下载与合并均使用小块流式缓冲，
不会把整个模型读进 Python 内存。

复用完全相同的已有模型时，可将原来的 `models/model/` 和 `models/deepseek-engram/`
复制或链接到本仓库对应位置，然后执行 `python3 download.py --verify`。
它会完整重新计算已安装资源的哈希并生成本地校验标记，可能需要数分钟。
平时启动核对标记、文件大小、revision 和修改时间，不会每次重新读取约 318 GB。
缺失的文件可通过 `download.py` 补齐。

## 启停和 API

```sh
./start.sh
./status.sh
python3 smoke.py
# 可选：python3 smoke.py --image /path/to/example.png
./stop.sh
```

`start.sh` 返回表示进程已启动，不代表 API 已就绪；请等模型初始化结束后执行冒烟测试。
实际地址记录在 `run/service.json` 的 `base_url`。启动被拒绝时查看
`logs/supervisor.log`；引擎日志和内存采样保存在 `logs/`。
`stop.sh` 请求退出，监控进程负责终止模型子进程并释放模型；用 `status.sh` 确认停止完成。

9000 可用时，客户端 endpoint 为 `http://127.0.0.1:9000/v1`。
通过 `GET /v1/models` 查询模型 ID，使用 `POST /v1/chat/completions` 发送图文请求。
`GET /monitor` 是监控页，`GET /metrics` 返回指标。
`smoke.py` 会自动读取实际端口并查询模型 ID。

图片采用 OpenAI `image_url` 的 **base64 data URL**，或 Anthropic base64 image source。
此引擎不会主动下载远程 HTTP 图片。`smoke.py --image` 提供 PNG/JPEG 混合文字请求示例，
大图的视觉编码时间可能明显更长。

供可信局域网使用时，将 [service.json](service.json) 的 `host` 改为 `0.0.0.0`，
停止后重新启动。其他电脑使用 Mac 的局域网 IP 和实际端口，不要填写 `0.0.0.0`。
裸服务未配置鉴权或 TLS；开放给非可信客户端时应使用带鉴权的代理。
客户端强制要求 API key 时可填占位符，但占位符不会为服务增加鉴权。

## 内存保护

以下保留参考部署中已放宽的 128 GiB 配置。修改 [service.json](service.json) 后重启生效。

| 监测项目 | 停止条件 |
| --- | --- |
| 模型进程 RSS 或 physical footprint | 超过 120 GiB |
| 全机 wired 内存 | 超过 120 GiB |
| 系统内存压力 | critical 立即停止；连续 warning 1,800 秒停止 |
| swap 占用或累计 swap-out 增长 | 相对启动基线超过 8,192 MiB |
| 压缩内存增长 | 相对启动基线超过 24 GiB |
| 采样 | 每 0.5 秒；探针失败或数据无效时停止子进程 |

这些是采样触发的停止阈值，不是硬分配上限，也不保证始终有 8 GiB 空闲内存。
RSS 无法完整反映 GPU 内存，因此另外监控 wired 和系统压力；引擎预算也无法覆盖所有
懒映射。启动器不会修改全局 GPU wired limit，也不会关闭其他应用。
保护触发属于预期行为，重启前请检查 `run/service.json` 中的 `stop_reason`。

## 验证范围与限制

[验证说明](docs/VALIDATION.md) 包含可复现命令、历史本机结果及适用范围。
部署包从全新固定提交成功构建；内存保护、下载校验和资源合并有针对性的测试。

历史测试包括有限 8K 请求中 DSpark 开关输出逐字节一致，以及长前缀多轮请求结束后
分配量稳定。这些结果不能证明所有任务质量或满窗口稳定性。
上游 PR 仍是草稿：已有的 5 项 Metal offload 夹具失败、CPU 构建问题，以及尚未完成的
CUDA 和独立质量验证，均在 PR 报告中列明。

## 目录与致谢

| 路径 | 用途 |
| --- | --- |
| `engine.lock.json`、`patches/` | 固定上游版本及实际部署的源码补丁 |
| `manifests/`、`assets.py`、`download.py` | 固定资源清单、校验和下载 |
| `bootstrap.py` | 获取源码、应用补丁并构建 |
| `service.json`、`control.py`、`memory_probe.c` | 参数、监控进程和 macOS 内存探针 |
| 启停脚本、`smoke.py` | 日常操作与 API 检查 |
| `tests/`、`docs/` | 无需模型的部署测试及验证记录 |

上游引擎：[fodelf/YoungAi](https://github.com/fodelf/YoungAi)。
量化和侧车：[wenzhouwu/YoungAi-DeepSeek-V4.1-Flash-1017](https://huggingface.co/wenzhouwu/YoungAi-DeepSeek-V4.1-Flash-1017)。
Engram 和 tokenizer：[deepseek-ai/DeepSeek-V4.1-Flash-1017](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017)。
代码采用 [MIT](LICENSE)，保留上游版权声明；模型许可随资源下载，本仓库不再分发权重。
