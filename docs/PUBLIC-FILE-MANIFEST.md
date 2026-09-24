# 公开文件清单

这份清单用于创建 GitHub 仓库前的最后筛选。

## 首次提交建议

```text
README.md
WORKFLOW.md
PRODUCT-NEXT.md
web/
research_acquisition.py
research_evidence.py
research_exports.py
research_flow.py
research_web.py
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
runs/*/RESULT.md
runs/*/FINAL.md
runs/*/FOLLOW-UP*.md
runs/*/MODEL-RESULT.md
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
- 真实验收记录使用`revise`、`stopped`等真实状态，没有编造通过率。
- 所有外部网页正文只在本机证据目录保存，公开仓库只保留来源摘要、哈希和脱敏结果。
- `.local/model_credentials.json`不在提交列表中，权限保持`600`。
- 前端代码不包含模型密钥，也不直接接收 Cookie。
