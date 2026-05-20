# LLM API 选择建议

更新时间：2026-05-19

## 结论

这个项目第一阶段推荐买一个支持工具调用、结构化输出、长上下文、稳定流式响应的 API。优先级如下：

1. 首选：OpenAI `gpt-5.4-mini` 或同系列 mini 模型。
2. 国内购买/访问优先：阿里云百炼 `qwen3.6-plus` 或 `qwen-plus`。
3. 成本极低的实验备选：DeepSeek `deepseek-chat`，但要把工具调用和 JSON 可靠性纳入评测。
4. 高质量代码推理备选：Anthropic Claude Sonnet/Haiku 系列，成本更高，适合后续对比实验。

## 推荐购买方案

### 方案 A：OpenAI mini 模型作为主力

适合：想把项目包装成“标准 agentic workflow”，强调 tool calling、structured outputs、trace、eval。

用法：

- Planner Agent：mini 模型。
- Tool Router：mini 模型或更便宜模型。
- Solver/Patch Agent：mini 模型，复杂任务再切到更强模型。
- Judge Agent：低温 mini 模型。
- Embedding：单独用 embedding 模型或本地 embedding。

理由：

- OpenAI 官方 pricing 页显示 `gpt-5.4-mini` 标准短上下文价格为 input $0.75 / 1M tokens、cached input $0.075 / 1M tokens、output $4.50 / 1M tokens。
- OpenAI GPT-5 系列官方介绍中明确支持 custom tools、parallel tool calling、streaming、Structured Outputs、prompt caching 和 Batch API。
- 对这个项目来说，工具调用和结构化输出稳定性比“最强推理能力”更重要。

注意：

- 不建议一开始买或默认使用 pro 级模型，成本和项目体量不匹配。
- 要在系统里做 provider abstraction，避免后续换模型重构。

参考：

- OpenAI pricing: https://developers.openai.com/api/docs/pricing
- GPT-5 developer announcement: https://openai.com/index/introducing-gpt-5-for-developers/

### 方案 B：阿里云百炼 Qwen 作为国内主力

适合：购买和访问希望更顺滑，或者你希望中文文档、国内云账号、人民币/云资源管理更方便。

用法：

- Planner / Router / Solver：`qwen3.6-plus` 或 `qwen-plus`。
- 简单分类、摘要、trace 压缩：更便宜的小模型。
- Embedding：百炼 embedding 或本地 bge 系列。

理由：

- 阿里云文档说明 Qwen Function Calling 支持通过 `tools` 参数传入工具信息，并支持 `tool_choice` 和 `parallel_tool_calls`。
- 阿里云文档说明 `qwen3.6-plus` 支持完整工具调用和 1M 上下文，适合大型代码库。
- 官方价格页列出了不同部署地域的 `qwen-plus` 价格：部分地域 0-128K input 为 $0.115 / 1M tokens，非思考输出 $0.287 / 1M tokens；US / China Hong Kong 部署下 0-256K input 为 $0.4 / 1M tokens，非思考输出 $1.2 / 1M tokens。

注意：

- 不同部署地域价格差异很大，买之前确认 region。
- 需要把工具选择准确率、参数抽取准确率单独做评测。

参考：

- Qwen Function Calling: https://help.aliyun.com/zh/model-studio/qwen-function-calling
- Qwen model pricing: https://www.alibabacloud.com/help/en/model-studio/model-pricing
- Qwen text generation concepts: https://help.aliyun.com/zh/model-studio/core-concepts/

### 方案 C：DeepSeek 作为低成本实验备选

适合：早期大量跑离线评测、trace 生成、低成本试错。

用法：

- 不建议第一版直接把 DeepSeek 作为唯一 provider。
- 可以作为 batch eval 或低风险 agent 的备选。
- 对 JSON schema、工具参数、失败恢复做强约束。

理由：

- DeepSeek 官方价格页显示 `deepseek-chat` 64K 上下文，cache miss input $0.27 / 1M tokens、cache hit input $0.07 / 1M tokens、output $1.10 / 1M tokens；`deepseek-reasoner` output $2.19 / 1M tokens。
- 价格很适合跑大量评测，但这个项目最关键的是可靠工具调用和结构化 trace，所以需要先测。

参考：

- DeepSeek pricing: https://api-docs.deepseek.com/quick_start/pricing-details-usd

### 方案 D：Anthropic Claude 作为质量对照

适合：后期做 provider 对比，或把复杂代码推理交给更强模型。

理由：

- Anthropic 官方价格页显示 Claude Sonnet 4.6 / 4.5 为 input $3 / MTok、output $15 / MTok；Claude Haiku 4.5 为 input $1 / MTok、output $5 / MTok。
- 官方文档还列出工具使用会引入额外 system prompt token，这对多工具 Agent 成本评估有参考价值。

注意：

- 成本高于 OpenAI mini、Qwen 和 DeepSeek 的轻量方案。
- 更适合作为“复杂任务升级模型”或离线质量对照，而不是第一阶段全量主力。

参考：

- Anthropic pricing: https://platform.claude.com/docs/en/about-claude/pricing

## 项目内 Provider 抽象

建议从第一天就做统一接口：

```python
class LLMProvider:
    async def complete(self, messages, tools=None, response_schema=None, metadata=None):
        ...
```

统一记录：

- provider
- model
- prompt_version
- input_tokens
- output_tokens
- cached_tokens
- latency_ms
- tool_calls
- finish_reason
- estimated_cost

这个 provider 抽象层在项目中的作用：

> Provider-agnostic LLM 调用层支持 OpenAI/Qwen/DeepSeek 等模型可插拔切换，统一记录 token cost、latency、tool-call trace 和 prompt version，用于离线评测、成本观察和运行监控。
