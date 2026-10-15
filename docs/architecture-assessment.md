# 架构评估：按模块选择语言

2026-10-15；代码基线 [44b1890](https://github.com/Oliveryfaso/multi-agent-code-review-lab/commit/44b1890c12af4dbbe2b58adbc1006e3901264e8d)。本文是调研和建议，实际模块见 [架构说明](architecture.md)。没有引入所比较项目的框架、运行时或执行能力。

## 判断

保留 Python 作为静态分析、进程监督、合同和本地服务的主语言，浏览器继续使用现有 HTML / CSS / JavaScript。目前没有测量证明新增 TypeScript、Rust 或第二套解释器能解决本轮问题，因此不增加依赖或迁移运行时。

本仓既有可使用的有界静态证据工作台，也有 Agent 研究路径。前者需要准确、受限、可追溯的源码读取。研究模块数量不能证明自主修复能力，Python 文件比例也不能判断架构成熟度。

## 官方项目参考

下表为当日读取的官方源码与文档；`main` 链接会继续变化。比较限于模块边界，不比较未经本项目复现的榜单分数。

| 参考 | 实际观察 | 可借鉴做法 |
| --- | --- | --- |
| [Aider 控制层](https://github.com/Aider-AI/aider/blob/main/aider/coders/base_coder.py)、[仓库地图](https://aider.chat/docs/repomap.html) | Python 控制代码使用符号与关系地图选择相关上下文。 | 检索和证据选择独立可测；增加目标语言需要对应解析器，不要求控制层改成目标语言。 |
| [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/default.py) | Python Agent 对接 Model、Environment，显式保留步骤、费用、时间和轨迹。[SWE-agent README](https://github.com/SWE-agent/SWE-agent/blob/main/README.md)已推荐这个后继项目。 | 先明确输入、预算、状态、失败与轨迹合同。Agent 数量不能替代任务验收；本轮没有采用目标执行能力。 |
| [OpenHands SDK 边界](https://github.com/OpenHands/software-agent-sdk/blob/main/README.md#repository-boundaries) | Python SDK / Agent Server 经 OpenAPI 合同接 TypeScript 客户端，UI 和自动化各有归属。 | 多客户端出现时先版本化 API / 事件合同，再考虑类型化客户端。当前单个本地页面不足以支持整体后端迁移。 |
| [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | Checkpointer 保存线程状态，供状态读取与恢复使用。 | 已有可选 mock-recovery 合成实验要按实际覆盖解释；观测 checkpoint 不等于任意真实任务的持久执行保证，不需整体改写工作流。 |
| [Codex Rust workspace](https://github.com/openai/codex/blob/main/codex-rs/Cargo.toml) | Rust workspace 把控制、协议和运行职责拆分为 crate。 | 借鉴清楚的模块合同；是否需要原生实现由本项目剖析、平台 API 和维护成本决定。 |

## 模块决策与触发条件

| 模块 | 当前决策 | 后续变化所需证据 |
| --- | --- | --- |
| AST、符号、静态事实 | 保留 Python 和限定语法观察。 | 新增 JS/TS 时设计解析适配器，以真实仓库验证源码字节、物理区间和语义边界；无需执行目标 Node。 |
| Supervisor、资源与退出监督 | 保留受测单 worker、压力/RSS、取消与 wait 合同。 | Linux 需要平台适配器及真实原生资源/关闭验收。只有剖析证明开销或系统 API 需求后才考虑原生模块。 |
| CLI / Python API / 报告 | 继续共享核心请求与发布验证。 | 跨客户端扩展前明确版本兼容和事件合同，避免各适配器重算报告。 |
| 本地工作台 | 保留三个固定浏览器资源。 | 页面复杂度、复用或多个客户端产生可测需求后再评估 TypeScript 和构建链。 |
| Agent / provider / recovery | 保持独立研究路径与显式实验依赖。 | 模型、目标执行和修复效果需要各自授权、冻结输入和验收，不能由合成回归代替。 |

## 已确认差距与本轮处理

1. 旧 README 未覆盖新工作台，并把 Linux 监测写成已实现。本轮缩短入口、加架构图，区分 Darwin 原生监测与 Ubuntu 离线 CI。
2. 旧发布清单默认全量 discovery、旧 demo 和模型检查，与当前有界执行范围不符。本轮改为按影响选已有 runner，同步文档、敏感排除及精确远端 CI。
3. 第二个独立真实项目缺少测量。本轮冻结已有公开 Python 仓库的顺序 CLI / API 验收；结果与缺口见 [外部项目验证](external-project-validation.md)。不从小仓库结果推断大规模性能。
4. 包声明 Python 3.10+，现有实测是 macOS Python 3.12.13 与 Ubuntu Python 3.11 CI。完整版本/平台矩阵未验证；这是支持证据缺口，不是已确认的运行时缺陷。

优先积累冻结版本、独立源码 oracle、真实资源和关闭证明组成的外部用例资产，再按失败类型选择最小修复。持续/较大负载、多语言解析、类型化客户端、原生监督和完整持久执行均需另列可验证收益与计划。本轮没有新增解释器、全局技能、框架依赖或产品服务。

维护方法见 [文档维护](documentation-maintenance.md)，简短入口保留在 [README](../README.md)。
