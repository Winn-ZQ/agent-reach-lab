/* 上游能力目录的设计快照：2026-09-26。不是本机健康检查或授权记录。 */
const capabilityCatalog = [
 {id:'web',name:'公开网页',group:'网页与订阅',ops:['读取正文'],purpose:'核对官网、公告和公开文档',ready:true},
 {id:'search',name:'全网搜索',group:'网页与订阅',ops:['搜索候选网页'],purpose:'发现与问题相关的资料'},
 {id:'rss',name:'RSS / Atom',group:'网页与订阅',ops:['读取订阅源'],purpose:'整理指定信息源；定期监测另行配置'},
 {id:'xhs',name:'小红书',group:'社区与社交',ops:['搜索笔记','读取笔记','读取评论'],purpose:'整理体验反馈，需分别验证笔记和评论能力',auth:'需要用户控制的登录环境'},
 {id:'reddit',name:'Reddit',group:'社区与社交',ops:['搜索帖子','读取帖子与评论'],purpose:'研究社区讨论与问题反馈',auth:'需验证登录及访问条件'},
 {id:'x',name:'Twitter / X',group:'社区与社交',ops:['读取推文','搜索','时间线与长文'],purpose:'整理公开讨论与动态',auth:'不同操作配置要求不同'},
 {id:'facebook',name:'Facebook',group:'社区与社交',ops:['搜索','主页与动态','群组列表'],purpose:'读取用户有权访问的内容',auth:'需登录环境'},
 {id:'instagram',name:'Instagram',group:'社区与社交',ops:['用户搜索','主页与近期帖子','Explore'],purpose:'发现产品内容及创作者信息',auth:'需登录环境'},
 {id:'v2ex',name:'V2EX',group:'社区与社交',ops:['热门与节点帖子','正文与回复','用户信息'],purpose:'技术社区讨论'},
 {id:'youtube',name:'YouTube',group:'视频与音频',ops:['视频搜索','字幕提取'],purpose:'整理视频讲解；字幕不等于读取画面'},
 {id:'bilibili',name:'B站',group:'视频与音频',ops:['搜索与视频详情','字幕'],purpose:'视频资料整理；字幕能力单独验证',auth:'字幕与搜索可能使用不同路径'},
 {id:'podcast',name:'小宇宙',group:'视频与音频',ops:['音频转文字'],purpose:'长音频整理；需显示转录范围与额外消耗',auth:'需转录服务配置'},
 {id:'github',name:'GitHub',group:'代码与项目',ops:['公开仓库读取','搜索'],purpose:'项目资料研究；写入操作不属于资料获取',auth:'私有内容另需明确授权'},
 {id:'linkedin',name:'LinkedIn',group:'职业与企业',ops:['公开页面','个人与公司资料','职位搜索'],purpose:'整理公司及招聘资料',auth:'详情操作需额外配置'},
 {id:'boss',name:'Boss直聘',group:'职业与企业',ops:['岗位搜索','职位正文'],purpose:'岗位要求与招聘趋势资料',auth:'专用浏览器环境与用户登录'},
 {id:'xueqiu',name:'雪球',group:'财经社区',ops:['行情与股票搜索','热门帖子与榜单'],purpose:'收集行情与社区观点，分别标注性质'}
];
