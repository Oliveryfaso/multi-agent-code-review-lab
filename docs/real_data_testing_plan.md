# Real Data Testing Plan

## 目标

把当前本地样例 eval 升级到真实项目数据，但不把项目变成 benchmark 复刻。

当前真实数据进度：

- 已完成外部数据格式 adapter smoke test。
- 已完成 `prepare-real-eval` 转换入口。
- 已在 Trace Viewer 和 README 展示真实数据测试状态。
- 已对本地 `pallets/markupsafe` checkout 执行 4 条 GitHub issue 风格真实外部 eval。
- MarkupSafe eval: `task_success_rate=1.0`、`final_review_pass_rate=1.0`、`tool_call_failure_rate=0.0`、`avg_code_smell_ratio=0.031`。
- 尚未执行完整 SWE-bench Lite benchmark；这需要下载数据集、checkout 对应 `base_commit`，并配置 repo-specific test sandbox。

真实数据测试分三层推进：

1. Real issue localization：从真实 issue/problem statement 定位相关文件、函数、测试。
2. Real patch planning：在真实 repo 上输出最小修复方向和测试建议。
3. Real patch verification：后续接入隔离环境后，验证 patch apply 和目标测试。

当前阶段先完成第 1 层和第 2 层的数据准备入口。

`external_data/` 和 `external_repos/` 已加入 `.gitignore`。真实数据和真实仓库不直接提交到项目中，保留转换脚本、eval 配置和报告即可。

## 已接入的数据格式

### SWE-bench / SWE-bench Lite

参考：[SWE-bench Lite](https://www.swebench.com/lite.html)

适合：

- 真实 issue-to-patch 任务。
- 评估是否能从 problem statement 找到 gold patch 涉及的文件和测试。
- 后续验证 patch planning 和 test selection。

不直接全量接入的原因：

- 需要 checkout 真实 repo 到指定 `base_commit`。
- 需要安装每个项目自己的依赖。
- 需要隔离测试环境、timeout、磁盘缓存和失败恢复。
- 当前项目第一目标是工程工具闭环，不是复刻 SOTA benchmark。

当前导入方式：

```bash
python3 cli/agent_review.py prepare-real-eval \
  --source swe-bench \
  --input external_data/swe_bench_lite.jsonl \
  --repo-map external_data/repo_map.json \
  --output eval_sets/real_swe_bench_lite.jsonl \
  --limit 20
```

`repo_map.json` 示例：

```json
{
  "django/django": "external_repos/django__django",
  "psf/requests": "external_repos/psf__requests"
}
```

Agent 运行时只看到 `problem_statement`。`patch/test_patch` 只用于提取 `expected_files`、`expected_symbols` 和测试选择，作为离线评分 oracle。

### CodeSearchNet

参考：[github/CodeSearchNet](https://github.com/github/CodeSearchNet)

适合：

- 评估自然语言 query 到函数/文件的检索能力。
- 单独观察 `rg + AST + Symbol Graph + Code Graph + 少量语义搜索` 的召回表现。

不适合：

- 完整 code review。
- patch verification。
- 多 Agent 闭环质量评估。

当前导入方式：

```bash
python3 cli/agent_review.py prepare-real-eval \
  --source codesearchnet \
  --input external_data/codesearchnet_python_sample.jsonl \
  --repo-map external_data/repo_map.json \
  --output eval_sets/real_codesearchnet_python.jsonl \
  --limit 50
```

### GitHub Issue JSONL

适合：

- 用你自己挑选的真实 GitHub issue 做小规模、高质量、可解释测试集。
- 在项目中展示“真实工程问题定位”。

输入 JSONL 示例：

```json
{"id":"requests-issue-1","repo":"psf/requests","title":"Session redirect behavior regression","body":"...","expected_files":["requests/sessions.py"],"expected_symbols":["Session"],"issue_url":"https://github.com/psf/requests/issues/..."}
```

导入方式：

```bash
python3 cli/agent_review.py prepare-real-eval \
  --source github-issue \
  --input external_data/github_issues.jsonl \
  --repo-map external_data/repo_map.json \
  --output eval_sets/real_github_issues.jsonl
```

## 推荐的真实测试流程

### Step 1：选择 1-2 个小型 Python repo

优先选择：

- 依赖安装简单。
- 测试命令清晰。
- 代码量 5k-50k 行。
- issue/PR 关联明确。

不优先选择：

- 大型 monorepo。
- 需要数据库、浏览器、GPU 或复杂云服务。
- 测试强依赖外部网络。

### Step 2：建立 repo map

把真实 repo clone 到 `external_repos/`，然后写 `external_data/repo_map.json`。

项目不自动 clone，是为了避免 eval 隐式依赖网络状态，也避免在 CI 或展示环境中不可复现。

### Step 3：生成真实 eval JSONL

用 `prepare-real-eval` 转换公开数据或手工 issue 数据。

### Step 4：先跑 localization，不急着 patch

```bash
python3 cli/agent_review.py eval \
  --eval-file eval_sets/real_github_issues.jsonl \
  --repo-root . \
  --report reports/real_github_issues_eval.md
```

重点看：

- `file_hit_rate`
- `symbol_hit_rate`
- `avg_expected_file_recall`
- `final_review_pass_rate`
- `human_review_required_rate`

### Step 5：挑 3-5 条做 patch smoke

只对定位稳定、证据充分的 case 开 patch。

```bash
python3 cli/agent_review.py patch \
  --repo external_repos/psf__requests \
  "issue text here" \
  --provider deepseek \
  --llm-patch
```

## 评估口径

真实数据阶段不要只看 `task_success_rate`。

更重要的是：

- Agent 是否把问题定位到 gold patch 附近。
- Final Review 是否能拦住证据不足的答案。
- Recovery 是否能降低 empty recall。
- Human Review 问题是否具体、可回答。
- Patch 是否保持小范围、可应用、可测试。

## 后续升级点

- 增加 `repo checkout manager`：按 `base_commit` 准备工作树。
- 增加 `test command registry`：每个真实 repo 显式配置测试命令。
- 增加 `sandbox verifier`：限制 patch 测试时间、环境变量和写入范围。
- 增加 `semantic search adapter`：仅在 `rg/AST/graph` 召回不足时启用，控制 API 成本。
- 增加真实 repo dashboard：按 repo 展示 hit rate、recovery rate、human review rate 和成本。
