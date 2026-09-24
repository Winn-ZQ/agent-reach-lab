# 通用多平台资料助手（基于 Agent-Reach）

刘子谦的 AI 产品课项目。目标产品根据用户意图选择已接入工具，获取多平台外部资料，整合分析，经独立复核和有限修正后交付回答或资料包。面向产品经理／个人研究者，首版公开网页＋小红书，后续逐步扩展 Agent-Reach 支持的板块。电梯行业仅作测试案例。

用户已确认“本机网页＋模型 API”，默认执行已授权、预算内的查询，必要时询问，导出 Markdown＋CSV。两款模型的基础 API、本机配置网页、公开网页采集和真实模型调度均已有可运行实现；首版真实案例已跑到独立复核，但因复核仍要求修正而停止，尚未形成通过终稿。

详细需求以 [PRD v0.2](docs/PRD-v0.2.md) 为主，包含已确认决定、建议参数、待确认项和验收标准。该文档是评审稿，不代表目标功能已实现。

当前首版执行边界以 [MVP 冻结单](docs/MVP-v0.1-FREEZE.md) 为准：只验收一个电梯行业公开网页问题的完整链路，暂不扩展小红书、更多平台或 UI 美化。

准备发布到 GitHub 时，按[发布清单](docs/GITHUB-RELEASE.md)筛选脱敏文件；本目录尚未创建远程仓库，也不会把本机模型配置和网页正文提交出去。

课程演示可直接使用[5 分钟演示脚本](docs/DEMO-SCRIPT.md)和[公开文件清单](docs/PUBLIC-FILE-MANIFEST.md)。

可先查看 [三种状态的结果交付样例](docs/examples/app-research/index.html) 和 [样例说明](docs/examples/app-research/README.md)。App A 数据全部虚构，仅用于评审交付，不是实时应用或小红书实测。

## 已完成

- 模型角色适配和持久预算已接入新流程，支持Qwen分析／修正＋DeepSeek复核；相关测试通过。首版电梯案例已真实执行3次调用，后续修正需新的、仅限该案例的免费预算。[适配与预算说明](docs/MODEL-GATEWAY.md) · [集成验收](runs/model-adapter-2026-09-23/RESULT.md)。
- 本机研究工作台已完成并实际预览：案例选择、任务状态、证据详情、停止原因、Markdown／CSV导出、任务输入计划预览、公开网页获取、证据准备和会话保护已接通；离线回放与实时公开网页采集分开，网页采集和证据准备不调用模型。新增9项HTTP/UI测试，前端脚本语法检查通过。[工作台说明](docs/RESEARCH-WEB.md)。

- 公开网页获取适配层已跑通：Exa via mcporter 的候选结果经安全解析、去重后，交给 Agent-Reach WebChannel / Jina Reader 保存逐页证据；命令入口和网页入口各完成一次电梯公开资料实跑，均取得3页，模型调用0次。证据准备还会校验正文哈希、来源编号和范围限制，角色交接预览会展示分析→复核→条件修正的输入输出契约；全套95项测试通过。[命令验收](runs/public-web-elevator-2026-09-23/RESULT.md) · [网页验收](runs/public-web-ui-2026-09-23/RESULT.md)。

- 新增固定证据的离线调度：程序检查→独立复核→最多一次修正→再验证。旧真实错误已回放拦截，名额不足与修正上限能停止，相关43项测试通过（20项为新流程）。此次API调用0次，模拟通过不代表模型质量提高。[流程与运行说明](docs/RESEARCH-FLOW.md) · [四个分支验收](runs/offline-flow-2026-09-23/RESULT.md)。

- 首轮模型试测已收尾：累计12次尝试（含连接、失败和格式修复），已知72,285 Token，另有1次失败精确用量未知。两款都完成App分析／复核尝试和人工错误题；均暴露质量问题，尚无可用终稿。Qwen的JSON模式修复只解决语法，来源校验仍失败。[完整实测与下一步](runs/model-trial-2026-09-23/FINAL.md)。

- 首版电梯公开网页真实链路已执行：Qwen分析→一次修正→DeepSeek复核，共3次调用、已知59,679 Token。复核发现两处引用/表述边界问题，按修正上限停止，未宣称通过。[首版真实验收](runs/mvp-elevator-2026-09-24/RESULT.md)。
- 同一案例随后又在独立的2次免费预算下完成 Qwen 修正→DeepSeek 再复核。复核发现范围、来源性质和补充资料问题，结果仍为 `revise`；复核目标已按新规则重新本地验证，未形成通过终稿。[跟进结果](runs/mvp-elevator-2026-09-24/FOLLOW-UP-RESULT.md) · [跟进计划](runs/mvp-elevator-2026-09-24/FOLLOW-UP.md)。
- 针对复核提出的官方政策资料缺口，已新增5个政府来源并完成真实模型链路：Qwen分析/修正＋DeepSeek两轮复核，共5次调用。最终仍为 `revise / revision_limit`，主要问题是全国性外推和补贴适用范围不能由少数地方样本证明。[官方补充任务结果](runs/public-web-elevator-followup-2026-09-24/MODEL-RESULT.md)。

- 已完成 Qwen3.8-Flash 与 DeepSeek-V4.1-Flash 的真实API连接测试：各1次，均返回预期内容，总计57 Token。这只是连通性验证，不是分析或复核质量评测。[真实调用记录](runs/model-smoke-2026-09-23/RESULT.md)。

- 项目专用 `.venv` 安装官方 Agent-Reach 1.5.0。
- 上游仓库：https://github.com/Panniantong/Agent-Reach
- 固定源码提交：a19a171fa980a0785849596492e0af4db800c82f（MIT）。源码位于忽略提交的 vendor/Agent-Reach。
- `.tools` 本地安装 mcporter 0.13.13；config/mcporter.json 配置 Exa，不导入编辑器账户配置。
- 真实搜索一次：返回 3 条通力资料；真实读取一篇官网；主助手完成带来源分析。
- 运行证据：runs/setup/search.txt、kone.json、analysis.md；依赖清单：python-packages.txt。
- 已完成真实原报告独立复核，以及人工错误样本的一次“复核→修正→再复核”循环；6 项控制器测试通过。详细边界见 [验收记录](runs/verification-summary.md)。
- 已完成中国官网真实问题的补查循环：子助手要求追加资料，实际获取两份新网页后修正并再次通过；现有程序测试共 8 项通过。[本轮记录与最终报告](runs/kone-china-discovery/RESULT.md)。

这是现有助手驱动的原型：Codex 负责选资料、分析、调用独立子助手与处理修正，本地命令负责获取、保存和循环状态控制。尚不是输入问题即可独立运行的应用。已增加 `loop.py` 和独立复核流程，详见 [WORKFLOW.md](WORKFLOW.md)。尚未完成多题质量评测，尚未接入小红书，尚未创建 GitHub 作品仓库。

## 职责与数据流

用户问题 → 计划预览 → Exa via mcporter 返回候选网页 → Agent-Reach WebChannel/Jina 读取 → `collect_page.py` 保存原文、网址、采集时间、状态与哈希 → 后续分析角色生成带来源分析 → 独立复核角色检查引用。

后续将把搜索、网页、小红书输出适配为统一证据记录，让分析和复核读取证据，不直接依赖特定平台命令。当前只完成网页记录，尚未实现完整通用适配层。

## 本机运行

在此目录执行，需要 Node 在 PATH 中：

```sh
export PATH="$PWD/.venv/bin:$PWD/.tools/node_modules/.bin:/Users/lzq/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH"
agent-reach doctor --json
mcporter --config config/mcporter.json call exa.web_search_exa 'query=site:kone.com 24/7 Connected Services predictive maintenance features' numResults=3 --timeout 45000
python collect_page.py https://www.kone.com/global/en/service/kone-predictive-maintenance.html --output runs/kone-new.json
```

配套 Node 绝对路径是本机环境专用；迁移到其他机器需改用当地 Node 路径。沙箱默认网络受限，本次联网命令通过权限批准执行。不要把沙箱 DNS 失败直接解释成平台不可用。

`collect_page.py` 不调用模型；获取成功仍标记 fetched_unverified，保留后续复核边界。已有输出路径会被拒绝，请每次使用新文件名。默认暂时性失败最多重试一次，可用 `--max-retries 0/1/2` 设置；访问受限、404、限流和网络配置问题直接停止。失败不保存为有效证据。详见 [失败处理验收](runs/acquisition-failures/RESULT.md)。

此前研究分析使用 Codex 会话，不提取其凭据作为 API Key。当前已通过用户自行配置的百炼密钥接通独立程序的基础API；研究分析与复核调度尚待接入。

## 下一阶段

最小模型连接程序已实现：`.venv/bin/python model_smoke.py` 仅检查配置；添加 `--run` 才尝试实际调用，两款真实连接测试已经完成，不会自动重放。同题执行器 `model_trial.py` 包含调用上限、复核预留、输入哈希、格式与引用校验、失败响应保留及人工核账占用。11项执行器专项测试和6项连接测试通过；首轮12次调用已用完，不再追加。它仍不是完整产品调度器。

本机密钥也可通过网页配置：运行 `.venv/bin/python configure_model_web.py`，打开输出的 `127.0.0.1` 地址。页面15分钟内有效，只保存本机配置，不调用模型；保存后服务在两分钟内关闭。已通过6项配置页专项测试（本机访问限制、跨站请求拒绝、凭据权限、禁止覆盖等），这不是模型API实测。

用户已选择 Qwen3.8-Flash 与 DeepSeek-V4.1-Flash（百炼北京、阿里直供），见[首轮同题试测方案](docs/MODEL-TRIAL.md)。真实密钥已保存在Git忽略的 `.local/`，两款用完即停已观察到开启，无需重新配置。付费调用未授权。`configure_model.py --check` 只检查配置状态。后续优先验证Qwen分析＋DeepSeek复核的可替换组合；尚未实际交叉调用，不代表正式选型。

1. 固定证据流程、模型适配、各模型Token容量预留与持久台账已通过模拟验证；新版真实组合仍待小规模验证，下轮真实请求另立预算，不能重置首轮台账。当前仅允许免费额度，不计算未核实的人民币费用。
2. 把任务状态、稿件与停止原因接到本机研究网页已完成；回放入口明确标注离线，真实验收结果另存为脱敏记录。
3. 将已跑通的公开网页证据包交给分析角色、独立复核角色并完成两轮真实有限修正；当前稿件因复核又发现范围与补充资料问题而未通过。后续若继续，需要按新任务补充资料，不能在旧案例台账上追加调用。
4. 接入小红书并测试跨平台研究；必要登录由用户在本机完成，不在聊天中传密码或 Cookie。
5. 完成真实任务评测、可迁移安装、演示和 GitHub 发布准备。模拟样本不计作真实准确率，原始网页全文不默认公开。
