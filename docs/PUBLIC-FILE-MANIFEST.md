# 公开文件清单

这份清单用于既有 GitHub 仓库的更新筛选；实际版本以远端提交记录为准。

## 本轮更新候选

```text
README.md
requirements.txt
constraints-tested.txt
flow_backend.py
WORKFLOW.md
PRODUCT-NEXT.md
web/
research_acquisition.py
research_evidence.py
research_exports.py
research_flow.py
research_web.py
research_live.py
research_budget.py
review_benchmark.py
tests/fixtures/review-semantics.json
run_research.py
run_followup.py
run_resume.py
model_gateway.py
model_validation.py
configure_model.py
configure_model_web.py
demo_model_gateway.py
model_smoke.py
model_usage.py
test_*.py
docs/
prototypes/layout-comparison/
runs/*/RESULT.md
runs/*/FINAL.md
runs/*/FOLLOW-UP*.md
runs/*/MODEL-RESULT.md
runs/web-evaluation-2026-09-26/SEMANTIC-BENCHMARK-*.md
```

## 提交前排除

```text
.local/
.venv/
.tools/node_modules/
vendor/
runs/**/analysis-input.json
runs/**/sources/
*.cookies
*.token
任何 API Key、Cookie、请求/响应原文和本机配置
```

## 必查项目

- `README.md`没有把模拟回放说成实时调用。
- PRD v0.3、v0.2 基线和布局草稿的链接与边界一致；原型能力目录不作为已接入平台清单，模拟进度不作为真实执行记录。
- 真实验收记录保留`passed`、`revise`、`stopped`等各自实际状态，单题通过不写成整体通过率。
- 所有外部网页正文只在本机证据目录保存，公开仓库只保留来源摘要、哈希和脱敏结果。
- `.local/model_credentials.json`不在提交列表中，权限保持`600`。
- 前端代码不包含模型密钥，也不直接接收 Cookie。

启动与演示材料：`docs/QUICKSTART.md`、`docs/DEMO-SCRIPT.md`。离线测试还需要已有的 `runs/offline-flow-2026-09-23/scenarios/` 虚构场景和测试夹具；不要把它们当作真实响应删除。依赖 `.venv/`、`.tools/`、`vendor/` 不发布，按启动说明另行安装。

- `docs/STAGE-ACCEPTANCE-2026-09-26.md` 与 `runs/web-evaluation-2026-09-26/STAGE-ACCEPTANCE-PLAN.md`：本轮阶段结论和执行边界，可公开；对应真实原文、模型响应和完整导出仅在 `.local/`。

- `repair_contract.py`、`test_repair_contract.py`、`runs/web-evaluation-2026-09-26/REPAIR-CONTRACT-*.md`：修正契约实现、虚构测试及脱敏记录。实际原文和模型补丁仍仅在 `.local/`。

- `docs/FINAL-DELIVERY-2026-09-26.md`：本阶段最终结果、验证范围和已知限制，不包含私人账本或原始证据。
