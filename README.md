# 通用多平台资料助手（基于 Agent-Reach）

> **公开网页阶段交付记录（2026-09-27）：已接入新默认流程，稳定语义质量尚未通过。** 新任务使用 Qwen3.8-Flash 分析/修正＋思考模式复核，旧任务保留原配置。206项程序测试通过。本轮正式网页实测取得3份来源、完成3次模型请求，因修正引用格式不合要求停止，保留草稿与证据；之后补充了一次有界格式修复并完成模拟验证，未把历史失败改判为通过。详见[预览版交付记录](docs/WEB-PREVIEW-RELEASE-2026-09-27.md)。

小红书网页入口已接入：可单独搜索笔记或复用已保存样本。本轮网页复用4篇Keep笔记，首次连接失败后在保留记录的前提下恢复一次，完成Flash分析＋思考复核2次及报告/导出。模型判通过，但人工仍发现表述过强，不代表语义质量通过；评论与混合研究未接入。详见[网页接入结果](docs/XHS-WEB-2026-09-27.md)及[首次采集记录](docs/XHS-ACQUISITION-2026-09-27.md)。

刘子谦的 AI 产品课项目。目标产品根据用户意图选择已接入工具，获取多平台外部资料，整合分析，经独立复核和有限修正后交付回答或资料包。面向产品经理／个人研究者，首版公开网页＋小红书，后续逐步扩展 Agent-Reach 支持的板块。电梯行业仅作测试案例。

用户已确认“本机网页＋模型 API”，默认执行已授权、预算内的查询，必要时询问，导出 Markdown＋CSV。两款模型的基础 API、本机配置网页、公开网页采集和真实模型调度均已有可运行实现；新版网页的通力中国案例已完成修正和独立再复核，形成模型复核通过的报告；这不代表多题质量评测通过。

当前需求以 [PRD v0.3 变更补充](docs/PRD-v0.3.md)＋[v0.2 详细基线](docs/PRD-v0.2.md) 为准：v0.3 更新布局、来源管理、实施顺序和过时状态，未修改的质量、预算与复核规则沿用 v0.2。目标需求不代表已实现。

2026-09-26 历史实施记录（方案 A 保留）：对话＋报告、来源中心和单任务资料范围，正常阶段自动推进。真实网页任务已完成获取3份原文→Qwen分析／修正→DeepSeek复核→页面及Markdown／CSV交付；首次复核要求补充1项限制说明；追加修正曾遇接口失败。保留该未知用量预留后，一次有界恢复完成Qwen修正＋DeepSeek再复核，得到 `passed / review_passed` 报告，详见[本轮记录](runs/workspace-a-2026-09-26/RESULT.md)。来源中心仅公开网页可执行，小红书后续接入。独立[布局草稿](prototypes/layout-comparison/README.md)仍保留模拟标识，全平台接入不加入当前验收。

项目已发布到 [Winn-ZQ/agent-reach-lab](https://github.com/Winn-ZQ/agent-reach-lab)。按[发布清单](docs/GITHUB-RELEASE.md)筛选脱敏文件；本机模型配置、网页正文和模型原始响应不会提交。

当前运行限制属于**开发验收配置**；快速／标准研究档位和用户日费用上限已记入后续规划，尚未实现。

新环境请先看 [安装与启动](docs/QUICKSTART.md)。当前测试仅承诺 macOS／Linux，Windows 可用 WSL；运行依赖版本已固定，其他系统仍需安装验证。

课程演示可直接使用[5 分钟演示脚本](docs/DEMO-SCRIPT.md)和[公开文件清单](docs/PUBLIC-FILE-MANIFEST.md)。 本轮[更新说明](docs/RELEASE-NOTES-2026-09-26.md)、[最终交付报告](docs/FINAL-DELIVERY-2026-09-26.md)与[交付检查记录](docs/DELIVERY-CHECK-2026-09-26.md)已提交并推送到既有 GitHub 仓库。

可先查看 [三种状态的结果交付样例](docs/examples/app-research/index.html)、[真实电梯验收结果展示页](web/result.html) 和 [样例说明](docs/examples/app-research/README.md)。App A 数据全部虚构，仅用于评审交付；电梯展示页使用真实脱敏验收摘要，不代表最终政策结论。

## 历史实施记录

以下按各次执行时的状态保留；最新结果与测试数以顶部预览版交付记录为准。

- 已补齐固定语义对照：流式接收1次真实成功，4,124 Token；整段与逐条复核按共同8组标签打平，理由仍有过度推断，未据此切换正式配置。147项测试通过，单次成功不代表断连已解决。[本轮结果](runs/web-evaluation-2026-09-26/STREAM-RECOVERY.md)。

- 建立8组/12条结论的虚构语义对照题，答案键不发送模型；142项程序测试通过。整段复核匹配8组标签，但仍有一处理由过度限定；逐条核查请求断连，比较尚未完成，未据此切换正式配置。[固定对照记录](runs/web-evaluation-2026-09-26/SEMANTIC-BENCHMARK-RESULT.md)。

- 复核传输失败的网页恢复入口已接通：沿用原稿与证据，最多3次调用，同一父任务仅恢复一次。137项测试通过。Notion真实恢复3次请求均成功返回，共42,426 Token，但仍要求修正；关键语义漏检仍在，没有新增通过稿。[恢复验证](runs/web-evaluation-2026-09-26/RECHECK-RESULT.md)。

- 收紧复核依据与修正闭环：新真实复核必须附可校验原文，阻断意见必须关联具体目标；再复核对照旧问题和新稿。134项测试通过；本轮没有新增模型调用，HTTPS诊断仍出现间歇TLS错误，尚未证明网络或模型语义质量改善。[收紧记录](runs/web-evaluation-2026-09-26/REVIEW-HARDENING.md)。

- 逐项问题清单和脱敏连接诊断已实现；129项测试通过。一次Notion跟进验证生成5项回答，但最终复核因远端断连失败；语义核查也发现复核误报和修正未落实，尚未取得新通过稿。[跟进验证](runs/web-evaluation-2026-09-26/FOLLOW-UP-RESULT.md)。

- 新增两题真实网页探索性评测：Notion取得3份官方资料但模型连接失败；Obsidian完成4次调用后仍要求修正。发现并修复缺页外壳被计为有效来源的问题；126项程序测试通过。不能据此宣称整体准确率。[评测记录](runs/web-evaluation-2026-09-26/RESULT.md)。

- 已完成无私有数据的项目副本验证：118项测试通过；修复首次无 `.local` 启动及3项私有夹具依赖，补充通用启动说明。后续已完成 macOS／Python 3.12 独立虚拟环境安装及同一套测试，运行依赖版本已记录；其他系统未验证。[检查记录](docs/RELEASE-CHECK-2026-09-26.md)。

- 方案 A 实施版已作为默认入口，连接公开网页获取、证据、预算与模型角色；旧界面保留 `/legacy`。20项集成测试及全套124项测试通过（测试传输均模拟）。真实网页案例完成修正和再复核，报告、引文及Markdown／CSV一致；旧接口失败的未知用量仍保守预留。[本轮记录](runs/workspace-a-2026-09-26/RESULT.md)。

- 方案 A 的独立静态草稿已完成来源中心、分类/能力搜索、必需来源缺失阻断、范围选择和自动进度演示；用户已认可布局方向。该静态草稿的报告与进度均为模拟，与已接后台的默认实施版区分。[设计规则](prototypes/layout-comparison/SOURCE-DESIGN.md)。

- 模型角色适配和持久预算已接入新流程，支持Qwen分析／修正＋DeepSeek复核；相关测试通过。首版电梯案例已真实执行3次调用，后续修正需新的、仅限该案例的免费预算。[适配与预算说明](docs/MODEL-GATEWAY.md) · [集成验收](runs/model-adapter-2026-09-23/RESULT.md)。
- 本机研究工作台已完成并实际预览：案例选择、任务状态、证据详情、停止原因、Markdown／CSV导出、任务输入计划预览、公开网页获取、证据准备和会话保护已接通；离线回放与实时公开网页采集分开，网页采集和证据准备不调用模型。新增9项HTTP/UI测试，前端脚本语法检查通过。[工作台说明](docs/RESEARCH-WEB.md)。

- 公开网页获取适配层已跑通：Exa via mcporter 的候选结果经安全解析、去重后，交给 Agent-Reach WebChannel / Jina Reader 保存逐页证据；命令入口和网页入口各完成一次电梯公开资料实跑，均取得3页，模型调用0次。证据准备还会校验正文哈希、来源编号和范围限制，角色交接预览会展示分析→复核→条件修正的输入输出契约；全套96项测试通过。[命令验收](runs/public-web-elevator-2026-09-23/RESULT.md) · [网页验收](runs/public-web-ui-2026-09-23/RESULT.md)。

- 新增固定证据的离线调度：程序检查→独立复核→最多一次修正→再验证。旧真实错误已回放拦截，名额不足与修正上限能停止，相关43项测试通过（20项为新流程）。此次API调用0次，模拟通过不代表模型质量提高。[流程与运行说明](docs/RESEARCH-FLOW.md) · [四个分支验收](runs/offline-flow-2026-09-23/RESULT.md)。

- 首轮模型试测已收尾：累计12次尝试（含连接、失败和格式修复），已知72,285 Token，另有1次失败精确用量未知。两款都完成App分析／复核尝试和人工错误题；均暴露质量问题，尚无可用终稿。Qwen的JSON模式修复只解决语法，来源校验仍失败。[完整实测与下一步](runs/model-trial-2026-09-23/FINAL.md)。

- 首版电梯公开网页真实链路已执行：Qwen分析→一次修正→DeepSeek复核，共3次调用、已知59,679 Token。复核发现两处引用/表述边界问题，按修正上限停止，未宣称通过。[首版真实验收](runs/mvp-elevator-2026-09-24/RESULT.md)。
- 同一案例随后又在独立的2次免费预算下完成 Qwen 修正→DeepSeek 再复核。复核发现范围、来源性质和补充资料问题，结果仍为 `revise`；复核目标已按新规则重新本地验证，未形成通过终稿。[跟进结果](runs/mvp-elevator-2026-09-24/FOLLOW-UP-RESULT.md) · [跟进计划](runs/mvp-elevator-2026-09-24/FOLLOW-UP.md)。
- 针对复核提出的官方政策资料缺口，已新增5个政府来源并完成真实模型链路：Qwen分析/修正＋DeepSeek两轮复核，共5次调用。最终仍为 `revise / revision_limit`，主要问题是全国性外推和补贴适用范围不能由少数地方样本证明。[官方补充任务结果](runs/public-web-elevator-followup-2026-09-24/MODEL-RESULT.md)。
- 已把上述真实验收结果整理为本机展示页：结论、证据片段、复核问题、Agent loop 和来源列表均绑定同一结果状态；页面本身不发起新的模型调用。[展示页代码](web/result.html)。

- 已完成 Qwen3.8-Flash 与 DeepSeek-V4.1-Flash 的真实API连接测试：各1次，均返回预期内容，总计57 Token。这只是连通性验证，不是分析或复核质量评测。[真实调用记录](runs/model-smoke-2026-09-23/RESULT.md)。

- 项目专用 `.venv` 安装官方 Agent-Reach 1.5.0。
- 上游仓库：https://github.com/Panniantong/Agent-Reach
- 固定源码提交：a19a171fa980a0785849596492e0af4db800c82f（MIT）。源码位于忽略提交的 vendor/Agent-Reach。
- `.tools` 本地安装 mcporter 0.13.13；config/mcporter.json 配置 Exa，不导入编辑器账户配置。
- 真实搜索一次：返回 3 条通力资料；真实读取一篇官网；主助手完成带来源分析。
- 运行时原始证据和诊断文件保存在本机 Git 忽略目录；公开仓库只保留脱敏验收摘要，不提交网页全文、模型原始响应或本机配置。
- 已完成真实原报告独立复核，以及人工错误样本的一次“复核→修正→再复核”循环；6 项控制器测试通过。详细边界见 [验收记录](runs/verification-summary.md)。
- 已完成中国官网真实问题的补查循环：子助手要求追加资料，实际获取两份新网页后修正并再次通过；现有程序测试共 8 项通过。[本轮记录与最终报告](runs/kone-china-discovery/RESULT.md)。

项目包含早期助手驱动原型、后来的模型 API 后台流程、原工作台及独立布局草稿。早期 `loop.py` 路径见 [WORKFLOW.md](WORKFLOW.md)，模型后台见 [RESEARCH-FLOW](docs/RESEARCH-FLOW.md)。新版网页已有真实闭环通过案例；尚未完成多题质量评测，小红书真实任务尚未验收。

## 职责与数据流

用户问题 → 计划预览 → Exa via mcporter 返回候选网页 → Agent-Reach WebChannel/Jina 读取 → `collect_page.py` 保存原文、网址、采集时间、状态与哈希 → 后续分析角色生成带来源分析 → 独立复核角色检查引用。

后续将把搜索、网页、小红书输出适配为统一证据记录，让分析和复核读取证据，不直接依赖特定平台命令。当前只完成网页记录，尚未实现完整通用适配层。

## 本机运行

在此目录执行，需要 Node 在 PATH 中：

```sh
export PATH="$PWD/.venv/bin:$PWD/.tools/node_modules/.bin:$PATH"
agent-reach doctor --json
mcporter --config config/mcporter.json call exa.web_search_exa 'query=site:kone.com 24/7 Connected Services predictive maintenance features' numResults=3 --timeout 45000
python collect_page.py https://www.kone.com/global/en/service/kone-predictive-maintenance.html --output runs/kone-new.json
```

上述手动诊断命令要求 Node 已在 PATH 中。工作台支持通过 AGENT_REACH_NODE 指定可执行路径；不再依赖个人目录。网络或执行环境受限时，应区分依赖缺失、DNS失败和平台拒绝访问。

`collect_page.py` 不调用模型；获取成功仍标记 fetched_unverified，保留后续复核边界。已有输出路径会被拒绝，请每次使用新文件名。默认暂时性失败最多重试一次，可用 `--max-retries 0/1/2` 设置；访问受限、404、限流和网络配置问题直接停止。失败不保存为有效证据。详见 [失败处理验收](runs/acquisition-failures/RESULT.md)。

此前研究分析使用 Codex 会话，不提取其凭据作为 API Key。当前已通过用户自行配置的百炼密钥接通独立程序的 API 和模型角色调度；新网页完整任务入口已接线；真实分析和修正已执行；程序定位规则已修复，真实独立复核、一次修正和再复核均已执行，已交付本案例通过稿。

## 早期模型接入记录

最小模型连接程序已实现：`.venv/bin/python model_smoke.py` 仅检查配置；添加 `--run` 才尝试实际调用，两款真实连接测试已经完成，不会自动重放。同题执行器 `model_trial.py` 包含调用上限、复核预留、输入哈希、格式与引用校验、失败响应保留及人工核账占用。11项执行器专项测试和6项连接测试通过；首轮12次调用已用完，不再追加。它仍不是完整产品调度器。

本机密钥也可通过网页配置：运行 `.venv/bin/python configure_model_web.py`，打开输出的 `127.0.0.1` 地址。页面15分钟内有效，只保存本机配置，不调用模型；保存后服务在两分钟内关闭。已通过6项配置页专项测试（本机访问限制、跨站请求拒绝、凭据权限、禁止覆盖等），这不是模型API实测。

用户已选择 Qwen3.8-Flash 与 DeepSeek-V4.1-Flash（百炼北京、阿里直供），见[首轮同题试测方案](docs/MODEL-TRIAL.md)。真实密钥已保存在Git忽略的 `.local/`，两款用完即停已观察到开启，无需重新配置。付费调用未授权。`configure_model.py --check` 只检查配置状态。Qwen分析＋DeepSeek复核的可替换组合已有真实调用记录；连通和实跑不代表质量已通过，也不代表选型最优。

1. 公开网页闭环已完成本案例验收：获取→分析→独立复核→有界修正→再复核→网页及Markdown／CSV交付。既有失败及未知用量保留，不将单题通过当整体准确率。
2. 本阶段已整理方案 A 的交互、故障处理、安装说明及 GitHub 展示材料；后续视觉打磨不改变底层任务和证据契约。
3. 接入小红书并测试 App 功能与反馈研究；必要登录由用户在本机完成，不在聊天中传密码或 Cookie。
4. 其他来源按能力注册表逐个验证、接入；共享任务预算，不以“全部平台可用”作为当前阶段完成条件。
5. 继续提升多题语义质量并验证其他系统安装；当前仅作为课程研究原型，模拟样本不计作真实准确率，原始网页全文不公开。
