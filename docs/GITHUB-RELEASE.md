# GitHub 发布清单

这份项目适合先作为课程作业和可复现原型发布。发布目标是展示产品思路、Agent-Reach 接入、证据契约、独立复核和停止规则，不公开本机凭据或网页全文。

## 可以公开

- `README.md`、`WORKFLOW.md`、`PRODUCT-NEXT.md`。
- `docs/PRD-v0.2.md`、`docs/MVP-v0.1-FREEZE.md`、`docs/RESEARCH-FLOW.md`、`docs/RESEARCH-WEB.md`、`docs/MODEL-GATEWAY.md`。
- `web/`、`research_*.py`、`run_research.py`、`run_followup.py`、`run_resume.py`及测试文件。
- `runs/**/RESULT.md`、`FOLLOW-UP-RESULT.md`和`MODEL-RESULT.md`等脱敏验收摘要。
- `docs/examples/app-research/`中的虚构资料示例。

## 必须排除

- `.local/`，包括 API 配置、预算台账、请求、响应和本机运行状态。
- `runs/**/analysis-input.json`和`runs/**/sources/`，其中可能保存整页网页正文。
- 任何 Cookie、Token、API Key、浏览器导出数据和本机路径中的私人信息。
- 未经确认可公开的第三方网页原文、模型原始响应和思考过程。

`.gitignore`已经加入证据包正文的排除规则；提交前仍要人工检查一次 `git diff --cached`。

## 建议的首次提交顺序

1. 提交代码、测试、PRD、冻结单和脱敏结果摘要。
2. 在干净环境执行 `python -m unittest -q`，确认不依赖 `.local/`。
3. 运行本机网页的离线回放，确认页面明确显示“回放／模拟”，不冒充实时研究。
4. 在 README 中说明 Agent-Reach 固定提交、模型角色和免费调用限制。
5. 创建 GitHub 仓库后再添加远程地址；不要把本机配置文件复制进仓库。

## README 演示路径

课程演示建议按以下顺序：

1. 输入电梯行业公开问题，展示计划预览。
2. 展示 Agent-Reach 读取公开网页和来源编号。
3. 展示分析角色、独立复核角色、修正和停止原因。
4. 打开[首版真实验收记录](../runs/mvp-elevator-2026-09-24/RESULT.md)与[官方补充任务结果](../runs/public-web-elevator-followup-2026-09-24/MODEL-RESULT.md)，说明真实结果仍为 `revise`。
5. 最后展示虚构 App A 回放，说明 UI 和导出格式如何工作。

不要把一次电梯案例包装成准确率，也不要把免费额度页面截图当成 API 账单证明。
