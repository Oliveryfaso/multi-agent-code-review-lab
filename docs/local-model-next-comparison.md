# 下一本地模型对照：来源与一次批量预算提案

查阅日期：2026-10-11。此页是待批准方案；本次准备批次新增权重下载、真实模型服务、生成请求和 VM 均为 **0**。旧三题的 3B 0/3、4B 1/3 与 GBNF 结构 3/3 保持原记录，新开发样例不补算旧成绩。

建议下一轮比较**已缓存的 Qwen3-4B-Instruct-2507 Q4_K_M 与 Qwen 官方 Qwen2.5-Coder-7B-Instruct Q4_K_M**。目标是在冻结的 12 个新保留案例上检查诊断机制与拒答边界，分别报告缺陷、限定健康、证据不足各 4 例的结构、合同、语义、基础设施结果。3 个开发样例只用于离线回归，不进入 24 个真实请求分母。Coder7B 是代码专用的更大候选，官方 GGUF 又能减少第三方量化来源；这构成对照理由，不能提前推断它会胜过 4B。[Qwen 官方模型卡](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF)

## 固定候选身份

只读取了官方发布页面、小型模型元数据、源码、许可证和 LFS 指针；没有读取权重正文。表内字节数和 SHA256 是发布者元数据，实际下载后仍须独立验长、验摘要。

| 项目 | 首选：Qwen2.5-Coder-7B-Instruct | 备选：Qwen3-8B |
| --- | --- | --- |
| GGUF 发布组织 | `Qwen`，模型作者自有组织 | `Qwen`，模型作者自有组织 |
| 仓库 | `Qwen/Qwen2.5-Coder-7B-Instruct-GGUF` | `Qwen/Qwen3-8B-GGUF` |
| 固定 revision | `13fb94bfda8c8cf22497dc57b78f391a9acb426a` | `7c41481f57cb95916b40956ab2f0b139b296d974` |
| 单个文件 | `qwen2.5-coder-7b-instruct-q4_k_m.gguf` | `Qwen3-8B-Q4_K_M.gguf` |
| 精确文件长度 | **4,683,073,536 bytes**（4.361 GiB） | **5,027,783,488 bytes**（4.682 GiB） |
| LFS SHA256 | `509287f78cb4d4cf6b3843734733b914b2c158e43e22a7f4bf5e963800894d3c` | `d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785` |
| 发布许可证 | Apache-2.0 | Apache-2.0 |
| 当前选择 | 建议唯一下载候选 | 本预算不下载、不启动 |

Coder7B 的身份依据是[官方固定提交](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/commit/13fb94bfda8c8cf22497dc57b78f391a9acb426a)、[固定文件页面](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/blob/13fb94bfda8c8cf22497dc57b78f391a9acb426a/qwen2.5-coder-7b-instruct-q4_k_m.gguf)与[固定 LFS 指针](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/raw/13fb94bfda8c8cf22497dc57b78f391a9acb426a/qwen2.5-coder-7b-instruct-q4_k_m.gguf)。量化仓库[LICENSE 文本](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/blob/main/LICENSE)与模型卡均声明 Apache-2.0；未来下载时保存并复核[该 revision 的 LICENSE](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/blob/13fb94bfda8c8cf22497dc57b78f391a9acb426a/LICENSE)。已另行有界读取该固定 revision 的 LICENSE 正文：11,343 bytes，SHA256 `832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e`，确认为 Apache License Version 2.0。未来下载时仍须保存并复核这份固定文本。

8B 的身份依据是[官方元数据 API](https://huggingface.co/api/models/Qwen/Qwen3-8B-GGUF?blobs=true)与[固定 LFS 指针](https://huggingface.co/Qwen/Qwen3-8B-GGUF/raw/7c41481f57cb95916b40956ab2f0b139b296d974/Qwen3-8B-Q4_K_M.gguf)；[官方模型卡](https://huggingface.co/Qwen/Qwen3-8B-GGUF)和[许可证入口](https://huggingface.co/Qwen/Qwen3-8B-GGUF/blob/main/LICENSE)标明 Apache-2.0。它包含思考／非思考切换，官方示例即使 `/no_think` 也显示空思考块。采用本项目的纯 JSON、384 token、固定 ChatML 预算需要另做模板资格核验，不能把官方推荐的长输出、采样配置当成本项目已验证的配置。其内存估计约 6.4–8.0 GiB，同样未经本机测量；因此本轮优先选择 Coder7B。[Qwen3-8B 模型卡](https://huggingface.co/Qwen/Qwen3-8B-GGUF)

另核对的较新代码专用发布 [Qwen3-Coder-30B-A3B-Instruct](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct)有 30.5B 总参数、3.3B 激活参数。激活参数少不代表仅加载 3.3B 权重；理想全 4-bit 权重下限已约 15.25 GB／14.20 GiB，尚未含量化元数据、缓存、计算和系统余量，因此不适合此 16 GB、CPU、8 GiB RSS 上限的下一轮。这里只排除了所核对的这个发布，不声称已穷举所有较新模型。

## 现有 runtime 的兼容依据与边界

沿用已缓存的官方 `llama-b11521-bin-macos-arm64.tar.gz`，不下载、升级、重新编译或安装 runtime。其来源是[官方 b11521 预发布](https://github.com/ggml-org/llama.cpp/releases/tag/b11521)，对应固定 commit [`b42b7e6d3059503dd4bed6ca5dcf342bb6379f67`](https://github.com/ggml-org/llama.cpp/commit/b42b7e6d3059503dd4bed6ca5dcf342bb6379f67)。既有归档记录为 12,083,036 bytes、SHA256 `7d691b864a59460d6df081dd75e15d80dbb92c1c2d018da4009aeaf45199e4c8`；本次只读既有 version/help 记录，版本为 `0.6.0-dev / build 11521 / b42b7e6d3 / Darwin arm64`，没有执行 binary 或重加载模型。

Qwen 官方卡给出 Coder7B 的 `qwen2` 架构、28 层、28 个 Q heads／4 个 KV heads，且[官方 llama.cpp 指南](https://qwen.readthedocs.io/en/latest/run_locally/llama.cpp.html)提供 GGUF 使用说明。固定 b11521 的 [Qwen2 loader/graph](https://github.com/ggml-org/llama.cpp/blob/b11521/src/models/qwen2.cpp)含 28 层 7B 分支；[ChatML 实现](https://github.com/ggml-org/llama.cpp/blob/b11521/common/chat.cpp)含显式模板覆盖；[GBNF 文档](https://github.com/ggml-org/llama.cpp/blob/b11521/grammars/README.md)支持 server 请求 grammar；[server 文档](https://github.com/ggml-org/llama.cpp/blob/b11521/tools/server/README.md)提供 apply-template、tokenize、usage 与固定参数。这些构成静态兼容依据，**Coder7B 的本机加载、原生计量、GBNF 生成兼容和语义仍未实测**。GBNF 约束形式，不能保证原因或拒答正确。

未来运行前仍须校验实际 executable、真实依赖、每个模型／tokenizer 身份、模板渲染与精确 token 计数。4B 的 Unsloth revision 与 7B 的 Qwen revision 分别绑定，不能复制对方的 tokenizer 身份。两模型采用同一套显式 `chatml`，同样的系统／用户消息和 assistant 前缀、同一 GBNF、同一 transport，且无思考／工具；载入后 `/apply-template` 必须逐字匹配冻结渲染。量化 metadata 若不支持这套模板，应在第一请求前停止，不临时替换模板或去掉 GBNF。

## 内存估计

校准参考是同一 M3 16 GB 上既有 4B 三题运行的采样峰值 **4.110 GiB**；其文件为 2.326 GiB。将这一次约 1.784 GiB 的剩余开销机械加到 Coder7B 文件，得到约 **6.146 GiB**；这只是粗估，开销不会必然等比例复用。[官方 Coder7B config](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct/raw/main/config.json)的 hidden size 3584／28 Q heads 给出 head dimension 128；按 28 层、4 KV heads、2048 context、K/V 均 f16 计算，基础 KV 数组约 **117,440,512 bytes／112 MiB**，不含 allocator 和临时计算缓冲。

据此把 Coder7B 峰值 **6.0–7.5 GiB**作为规划估计，并提议采样 RSS 停止上限 **8 GiB／8,589,934,592 bytes**。4B 仍使用 **5.5 GiB**上限，但新 12 题的峰值未知。RSS 与系统可用内存都不是操作系统硬配额；二者独立守卫，启动可用内存须 ≥30%，运行须 ≥20%，任何一个不足就停止。区间、加载时间和生成速度均未验证，不能承诺 24 请求全部完成。

## 一次批准的精确批量范围

以下 **180 秒请求、180 秒 health、2700 秒服务、7B 8 GiB RSS 与扩展日志限额都是新的待批准建议**，不继承第 17 轮的三请求授权。只有新的明确批量批准后才创建执行 claim 和冻结实际执行记录。

| 范围 | 建议一次授权上限 |
| --- | --- |
| 下载 | 仅上述 Coder7B 固定单文件，唯一一次尝试；权重 4,683,073,536 bytes，另 ≤1 MiB 元数据／许可证；接收正文总上限 **4,684,122,112 bytes** |
| 下载入口 | `https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/13fb94bfda8c8cf22497dc57b78f391a9acb426a/qwen2.5-coder-7b-instruct-q4_k_m.gguf`，仅其正常匿名 Hugging Face 资产 CDN 重定向 |
| 下载／准备阶段 | ≤1800 秒，含下载、验长／摘要／许可证、现有 runtime 和依赖身份核对、冻结与离线适配检查 |
| 新磁盘占用 | ≤5 GiB（5,368,709,120 bytes），含 `.part`／最终模型与本轮记录；记录及 tmp/cache 合计 ≤64 MiB。开始可用磁盘 ≥7 GiB，全程保留 ≥2 GiB，计入 exFAT allocation／AppleDouble |
| 服务顺序与数量 | 固定 4B→Coder7B；逐个关闭后才启动下一个；各最多一次启动，共 **≤2**。从已观察累计 17 推算最多到 19，实际执行前重新核计 claim 与消费；VM 保持 15，新增 0 |
| 生成数量 | 同一冻结 holdout12 各模型每题仅一次，共 **≤24**；无预热生成、重试、补全、补发或替代案例 |
| 单请求／health／auxiliary | 两模型共同请求 ≤180 秒；health ≤180 秒；辅助 API 每次 ≤5 秒／1 MiB，非 health 辅助 API 累计耗时 ≤60 秒／服务 |
| 原生 release／idle／remove-waiting | 每题保留对应任务的完整证明，≤10 秒；日志 verbosity 5，HTTP200 来自捕获的完整 response |
| 单服务总／活跃／关闭 | **2700／2640／60 秒**。所有子上限受此总上限约束，资源或时间先到即停止；不保证所有子上限同时耗尽仍能完成 |
| 日志／记录 | 每服务总日志 ≤12 MiB、活跃日志 ≤9 MiB；本轮其他记录 ≤32 MiB，同时受上述 64 MiB 阶段总占用约束 |
| 两服务完成后评分收尾 | monotonic ≤300 秒；未完成独立审阅时存 pending 并停止，不等待无限审阅 |
| 整批墙钟 | **≤7500 秒／125 分钟**（准备1800＋服务5400＋评分300），无后台延长 |

下载写入独立 `.part`，验证成功才同目录 rename；不保留第二份完整模型，不下载分片、BF16、备选量化、tokenizer 大文件或新 runtime。任何下载失败保留有界证据并停止，不续传或自动重试。不使用付费 API、凭证、远程推理、外传项目数据或 VM。

两模型共同冻结：CPU `threads=threads-batch=threads-http=2`、parallel 1、ctx 2048、输入 ≤1536、输出 ≤384、batch/ubatch 128、temperature 0、top_p 1、top_k 0、min_p 0、seed 0。继续使用 `--device none --n-gpu-layers 0 --no-kv-offload --offline --cache-ram 0 --no-cache-prompt --no-cont-batching --no-context-shift --no-warmup --no-webui --no-agent`、显式 ChatML 与数字 loopback／kernel port0，禁止路由器模式或自动工具。180 秒只是本机 CPU 速度未知时的更宽停止界限，不是已测性能。

先冻结 12 例顺序、请求字节、schema/GBNF、评分 rubric、模型身份和所有预算。模型输入只含中性 metadata、问题与可见证据，不含类别、开发答案、保留答案、复核提示或评分目标。两模型 tokenization 不同，分别核验同一文字输入在各自 tokenizer 下 ≤1536，而非假定 token 数相同。

沿用现有 Provider／Evaluator／RunStore 和比较 CLI。保留原始响应与计量证据；仅在 HTTP200、exact usage、请求／profile／response 绑定、原生 release/idle/remove-waiting 和空闲成立时，结构／合同／语义失败可记录并继续下一冻结题。任何身份、依赖／内容绑定、模板、计量、兼容、健康、超时、日志、容量、写入或关闭异常停止整批并取消剩余启动，不临时改 grammar、模板、参数或评分。exFAT 身份变化时按既有完整内容／来源／资格流程另存派生证据，保留旧冻结计划，不降低校验。

关闭仅针对前后核验一致的自有 PID／executable／listener，沿用 TERM ≤30 秒、必要时该 PID 的 KILL ≤5 秒和独立消失检查；不能停止其他进程。真实请求的质量结果与 infra/pending 分开报告，按三类别给出分母和确定性校验／独立人工复核，不由被测小模型裁判。新分数不与旧三题拼接；若未完成全部 24 题，报告实际消费和停止位置，不宣称完整对照。

尚未检查：权重正文、本机 7B 加载／生成、12 题峰值与速度。固定 revision 许可证正文已核验。风险：内存、计量或模板门禁可能令获批批次提前停止；模型更大也可能没有语义改善。
