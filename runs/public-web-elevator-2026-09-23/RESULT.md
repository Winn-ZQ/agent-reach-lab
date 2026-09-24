# 公开网页获取验收记录

日期：2026-09-23

本次复用了已完成的 Exa 公开网页搜索结果，没有再次调用模型。候选结果经过 `research_acquisition.py` 解析、URL 去重后，交给现有 `collect_page.py` 的 Agent-Reach WebChannel / Jina Reader 逐页读取。

## 输入

- 问题：`elevator predictive maintenance official product information`
- 目标：优先寻找电梯预测性维保的厂商官方资料，排除泛 SEO 摘要。
- 搜索后保留：3 条公开网页候选。
- 重试上限：每个页面 1 次；本次三页均首轮成功。

## 结果

| 排名 | 来源 | 读取状态 | 内容哈希 |
| --- | --- | --- | --- |
| 1 | TKE MAX Smart Maintenance | `fetched_unverified` | `aa6b56d281e491be29fe311858dcdc4cebe796409fe4068e5dfac9667c8e3c90` |
| 2 | KONE 24/7 Connected Services | `fetched_unverified` | `6df629cbf1484337de8bb94853119ee3c14f4e22757280dbd985a0327350bcdc` |
| 3 | KONE Predictive Maintenance Services | `fetched_unverified` | `71c0d29aba5540df6b99684f0c9909026bc02312b1c895711444d07b25d66c47` |

详细记录位于同目录的 `search.json` 与 `sources/`。`fetched_unverified` 表示已取得正文并记录哈希，但尚未完成事实分析和独立复核；不能把厂商自述直接写成客观效果。

## 已验证的程序行为

1. 搜索调用使用参数数组和 `shell=False`，用户问题不会作为 shell 片段执行。
2. 搜索响应只接受公开 HTTP(S) URL，候选按 URL 去重并保留标题、发布时间、作者和摘要。
3. 每个来源单独保存 URL、后端、采集时间、状态、失败分类、原文和 SHA-256。
4. 读取失败不会生成有效证据；访问受限、404、限流和网络错误保留状态供后续询问或停止。
5. 本次调用模型 API：0 次；小红书：未接入；分析和子助手复核：未执行。

## 下一步

把这份来源清单接入任务对象和结果页，先让用户确认候选范围，再进入结构化分析与独立复核。真实模型调用仍需新的、明确批准的预算；本轮不能把这次采集结果宣称为最终研究答案。
