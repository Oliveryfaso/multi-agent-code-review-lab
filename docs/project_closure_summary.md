# 项目收尾总结

更新时间：2026-05-21

## 定位结论

`Multi-Agent Code Review Lab` 当前定位为：

> 面向能力展示和个人本地使用的 multi-agent code review 工具。

建议公开到 GitHub，作为开源作品集项目维护。它不应该被包装成生产级 SaaS 或完整企业安全产品。

## 为什么建议公开开源

- 项目的核心价值在代码结构、Agent 分工、评测、trace、PR workflow 和工程文档，公开仓库更容易被面试官验证。
- 开源能展示工程透明度：不仅有结果，还有测试、失败记录、技术演进和设计取舍。
- 项目依赖少，默认本地运行，不需要复杂部署环境，适合公开展示。

## 公开边界

可以公开：

- `src/`、`cli/`、`tests/`、`docs/`、`sample_repos/`、`eval_sets/`
- `.github/workflows/macr-review.yml`
- `README.md`、`SECURITY.md`、`LICENSE`
- 可复现的示例 reports

不要公开：

- `.env`
- 私人 API key
- `.macr_uploads/`
- `.macr_cache/`
- `external_repos/`
- `external_data/`
- 大量本地 `traces/*.json`
- 私有客户或未授权代码

## 当前完成度

已完成：

- CLI 和 Web Review Workbench 双入口。
- 后台 Web job 和状态轮询。
- 上传 zip 的大小、路径穿越、symlink、文件数量和解压体积限制。
- `rg + AST + Symbol Graph + Code Graph` 的 evidence-first 代码检索路线。
- Agent Board、Routing Policy、Retrieval Critic、Final Review、Code Smell、Monitor。
- Diff Review、SARIF、GitHub Review Comments JSON。
- GitHub Actions PR workflow 和 PR review 发布脚本。
- Phase1 / Phase2 / MarkupSafe 外部 repo 评测记录。
- 技术演进和测试问题记录。

仍然不是生产级的部分：

- Web job table 是进程内内存，服务重启后状态不会恢复。
- 没有公网鉴权、多用户权限或持久化任务队列。
- 没有容器级沙箱。
- GitHub PR 评论还没有重复评论更新/折叠策略。

## 推荐 README 表述

推荐使用这个定位：

> A local-first, evidence-driven multi-agent code review lab for codebase understanding, PR risk detection, patch verification, and agent workflow evaluation.

避免使用这些表述：

- “production-ready SaaS”
- “enterprise security scanner”
- “fully automated code reviewer”
- “replaces human review”

## Push 前检查清单

1. 确认 `.env` 没有进入 git。
2. 确认 `external_repos/`、`external_data/`、`.macr_cache/`、`.macr_uploads/` 和大量 `traces/*.json` 没有进入 git。
3. 跑测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

4. 跑编译检查：

```bash
PYTHONPYCACHEPREFIX=/private/tmp/macr_pycache PYTHONPATH=src python3 -m compileall -q src tests scripts
```

5. 检查将要提交的文件：

```bash
git status --short
git diff --stat
```

6. 首次推送建议：

```bash
git init
git add .
git status --short
git commit -m "Initial release of multi-agent code review lab"
git branch -M main
git remote add origin <your-github-repo-url>
git push -u origin main
```

## 简历表述

中文：

> 设计并实现本地优先的多 Agent 代码审查工具，构建 Planner、Routing Policy、Tool Router、Retrieval Critic、Code Graph、Final Review、Code Smell、Diff Review 和 Monitor 等 Agent，通过共享 Agent Board 交换结构化 artifact；基于 `rg + AST + Symbol Graph + Code Graph` 的 evidence-first 路线完成代码定位、调用链解释、PR 风险审查、patch 验证和离线评测，并提供 CLI、Web Workbench、SARIF 和 GitHub PR review workflow。

英文：

> Built a local-first multi-agent code review lab with Planner, Routing Policy, Tool Router, Retrieval Critic, Code Graph, Final Review, Code Smell, Diff Review, and Monitor agents coordinated through a shared Agent Board. Implemented an evidence-first pipeline with `rg`, AST, Symbol Graph, and Code Graph for code localization, call-chain reasoning, PR risk review, patch verification, offline evaluation, CLI/Web workflows, SARIF output, and GitHub PR review integration.
