# 现有项目升级分析

更新时间：2026-05-19

## 总体判断

如果目标是 AI Agent 工程方向，现有项目里最值得升级的是 `Jap_Eng`，其次是 `Voice Type Lab`。但如果你要突出“多 Agent 协作 + 工具调用 + 评测 + 监控”，更建议新开一个工程型 Agent 项目：`Multi-Agent Code Review Lab`。

原因很直接：日语学习教练可以展示多 Agent 流程，但它的工具需求不强，容易被面试官理解成“多个 prompt 串起来”。代码库协作 Agent 则天然需要检索、AST、LSP、git、测试、编译、语义召回和失败恢复，技术含量更明确。

## 项目逐项分析

### 1. Jap_Eng / AI 日语学习教练

适合升级，但不建议作为唯一主项目。

已有优势：

- 已有 Planner、Tutor、Evaluator、Review、Orchestrator，多 Agent 名义成立。
- 有学习计划、每日任务、批改、复习队列，业务闭环完整。
- 后续接入 LLM、RAG、Prompt 版本和 trace 日志比较自然。

短板：

- Agent 更像规则模块，缺少真实 LLM 工具调用。
- 工具空间偏窄，难展示复杂 routing。
- 评测指标容易停留在学习任务正确率，工程味不够强。

建议升级方式：

- 保留为“业务型多 Agent 应用”。
- 增加教材语法 RAG、错题队列工具、学习计划更新工具。
- 建 50 条评测集，指标包括答案正确率、引用命中率、工具调用成功率、低置信度澄清率。

简历表达：

- “将规则式日语学习教练升级为 LLM 多 Agent 编排系统，支持教材 RAG、错题记忆、工具调用、Prompt 版本管理和 trace 日志。”

### 2. Voice Type Lab

适合 AI 应用工程师方向，不适合作为多 Agent 主项目。

已有优势：

- 音频上传、特征提取、标签输出、隐私边界清晰。
- 可升级到 wav2vec2 / HuBERT / WavLM embedding + 评估集。

短板：

- 主线是模型服务和音频分类，不是多 Agent。
- 强行加入多 Agent 会显得不自然。

建议升级方式：

- 作为第二项目，补模型服务、Docker、评估集、延迟监控。
- 不把它包装成多 Agent 项目。

### 3. 慢病用药小管家

适合产品经理或 AI 应用工程方向，不建议做多 Agent 主项目。

已有优势：

- 场景真实，用户旅程完整。
- 医疗安全边界、规则兜底、PRD 和指标体系都能讲。

短板：

- 医疗建议风险高，面试中容易被追问合规和安全边界。
- 多 Agent 可以做，但会偏客服/健康顾问，不如代码库 Agent 更容易验收。

建议升级方式：

- 做 PRD + 指标包 + AI 安全规则层。
- 如果要做 Agent，可限定为“药单解析 Agent + 安全审核 Agent + 复诊摘要 Agent”，避免开放式医疗问答。

### 4. 水分小伙伴

不建议投入到多 Agent 方向。

已有优势：

- C 端体验完整，适合产品展示。
- 小程序形态有完整交互链路。

短板：

- 和 LLM Agent 技术目标距离较远。
- 加 Agent 容易变成“陪伴聊天”，技术含量不强。

建议升级方式：

- 作为产品/增长项目保留。
- 不作为 AI Agent 主线。

## 最终建议

推荐主项目：`Multi-Agent Code Review Lab`

推荐保留辅助项目：

- `Jap_Eng`: 证明你做过业务型多 Agent 应用。
- `Voice Type Lab`: 证明你能做模型服务、评估和部署。
- `慢病用药小管家`: 证明你有产品场景和安全边界意识。

简历组合建议：

- AI Agent 工程师版本：`Multi-Agent Code Review Lab` + `Jap_Eng` + 字节 LLM/Agent 实习 + NTU 研究。
- AI 应用工程师版本：`Voice Type Lab v2` + `Multi-Agent Code Review Lab` + 慢病用药小管家。
- AI 产品经理版本：慢病用药 PRD + 水分小伙伴 + Voice Type Lab 产品方案 + 字节实习。

