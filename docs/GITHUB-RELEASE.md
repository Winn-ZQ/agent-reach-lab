# GitHub 发布清单

这份项目适合先作为课程作业和可复现原型发布。发布目标是展示产品思路、Agent-Reach 接入、证据契约、独立复核和停止规则，不公开本机凭据或网页全文。

## 可以公开

- `README.md`、`WORKFLOW.md`、`PRODUCT-NEXT.md`。
- `docs/PRD-v0.3.md`及历史 PRD、`docs/MVP-v0.1-FREEZE.md`、`docs/RESEARCH-FLOW.md`、`docs/RESEARCH-WEB.md`、`docs/MODEL-GATEWAY.md`。
- `prototypes/layout-comparison/`：独立交互草稿，须保留模拟标识与 README，不宣传为已接通的平台或真实任务结果。
- `web/`（含方案 A 实施版）、`research_*.py`（含真实任务调度与预算）、`run_research.py`、`run_followup.py`、`run_resume.py`及测试文件。
- `runs/**/RESULT.md`、`FOLLOW-UP-RESULT.md`和`MODEL-RESULT.md`等脱敏验收摘要。
- `docs/examples/app-research/`中的虚构资料示例。
- `review_benchmark.py`、`test_review_benchmark.py`、`tests/fixtures/review-semantics.json`及固定对照的脱敏总结；题集明确标为虚构，标准答案不发送给模型。

## 必须排除

- `.local/`，包括 API 配置、预算台账、请求、响应和本机运行状态。
- `runs/**/analysis-input.json`和`runs/**/sources/`，其中可能保存整页网页正文。
- 任何 Cookie、Token、API Key、浏览器导出数据和本机路径中的私人信息。
- 未经确认可公开的第三方网页原文、模型原始响应和思考过程。

`.gitignore`已经加入证据包正文的排除规则；提交前仍要人工检查一次 `git diff --cached`。

## 本轮更新的发布顺序

1. 先核对代码、测试、PRD、冻结单和脱敏结果摘要；用户已授权本轮收尾后提交并推送到既有仓库。
2. 按 [QUICKSTART](QUICKSTART.md) 安装依赖，在不带原 `.local/` 的项目副本执行测试；项目副本测试与全新系统安装分开记录。
3. 运行本机网页的离线回放，确认页面明确显示“回放／模拟”，不冒充实时研究。
4. 在 README 中说明 Agent-Reach 固定提交、模型角色和免费调用限制。
5. 已有作品仓库；检查此次变更后再发布更新，不复制本机配置。

## README 演示路径

当前演示步骤以 [方案 A 的5分钟脚本](DEMO-SCRIPT.md) 为准。新界面点击一次开始后自动推进；旧版的计划预览、逐步获取和离线回放只在 `/legacy`，不要混用两套操作说明。

发布说明应准确写明“公开网页已有一个真实闭环通过案例；跨关联任务完成，后续多题测试仍暴露质量问题，小红书未接入”。不得把单题通过描述为整体准确率。项目副本及macOS独立虚拟环境安装测试已通过，不能代替外部检索可用性、模型质量或其他系统兼容性验收。

本轮已完成的副本测试、首次启动修复与边界见 [2026-09-26检查记录](RELEASE-CHECK-2026-09-26.md)。本次按用户授权完成公开文件筛选、本地提交与远端推送。

本轮更新说明见 [RELEASE-NOTES-2026-09-26](RELEASE-NOTES-2026-09-26.md)，界面与候选文件检查见 [交付检查](DELIVERY-CHECK-2026-09-26.md)。

本轮最终质量结论已归档：[阶段报告](STAGE-ACCEPTANCE-2026-09-26.md)。发布需保留“阶段执行完毕但质量未通过”，不能只展示通力通过例或程序测试数。

最新交付状态、真实失败与程序检查见[最终交付报告](FINAL-DELIVERY-2026-09-26.md)。
