# AutoLeadGen 架构改进建议

> 缘起:2026-06-25 排查 lp 工作流(#18 Lin Joy-Home Textile)"搜不到新线索 / 邮件发不出 / 客户池为空"花了大半天。本文把那次排查暴露的根因模式沉淀成可落地的改进计划。
>
> **核心判断:产品骨架(多源获客 → 补全 → 起草 → 带成本/质量闸门发送)是对的,不用推倒重来。痛点集中在"几处工程实现脆弱 + 失败时不报警 + 状态不持久"。**

---

## 一、那次排查实际撞到的问题清单

| # | 现象 | 真实根因 | 类别 |
|---|---|---|---|
| 1 | LeadContact 搜索"全重复" | 多词关键词在 LC 搜不到;行业+职位过约束;松弛阶梯先丢行业;地区是 blob 字符串没拆 | 代码 |
| 2 | 搜回的都不是纺织公司 | 行业映射表缺 textile | 配置 |
| 3 | 重启后搜索从头来、又撞重复 | 游标/退避/轮换是**内存 dict**,重启即丢 | 架构 |
| 4 | 客户池显示空 | LeadContact 建线索漏写 `client_pool_id` | 代码 |
| 5 | auto_send 设了 true 却不发 | 进程吃旧 .env;`agent_core` 里 `load_dotenv(override=True)` 乱盖环境变量 | 配置/架构 |
| 6 | 邮件被"验证闸"拦下 | 验证硬依赖 Snov(401 挂了)→ 邮箱判 unknown → 闸门挡 | 依赖/策略 |
| 7 | 线索停在 76 不增长 | 网页搜索卡在连不上的 Google/DDG 各 60s 超时,霸占执行,LeadContact 一直被 skip | 架构/运维 |
| 8 | 有邮箱的线索却标 needs_email | suppression 改状态后未保持一致 | 代码 |
| 9 | 同一人重复入库 | 去重键不够 | 代码 |

**关键观察:#3、#6、#7 这几个"卡死"症状,底层其实是少数几个根因(内存状态、外部依赖挂掉、失败不报警)。一个根因冒出多个不相关的症状,被体验成"满地 bug"。**

---

## 二、4 个结构性病根

### 病根 1:关键状态存在内存里,一重启就丢 ⭐最致命
- 现状:`_leadcontact_cursor` / `_leadcontact_backoff_until` / `_leadcontact_kw_rotation` 都是 `services/outbound_engine.py` 里的模块级 dict。
- 后果:每次重启/部署清零 → 从第一页重搜 → 撞已有数据 → 全重复 → 退避 → 卡住。今天反复出现。
- 本质:**流水线每次重启就忘了自己搜到哪了。**

### 病根 2:重度依赖外部 API,挂了不"优雅降级"而是默默卡住
- 依赖链:LeadContact / Snov / Tavily / Bocha / Google·DDG·Bing / SMTP。
- 后果:任一挂掉(Snov 401、Tavily 超额、连不上 Google、Bocha 要充值),系统不报警,而是**安静停转或把线索默默归档**。验证更是**硬依赖 Snov**,Snov 一挂全员发不出。

### 病根 3:流水线一长串"闸门",每道都能悄悄拦线索
- 闸门:相关性闸 → fit 分闸 → 预算闸 → 验证闸 → suppression。
- 后果:线索可能在 5+ 个点被丢,每个只 INFO 一行日志。"为什么没效果"要逐闸追。闸门本身合理,**叠加后系统变黑盒**。

### 病根 4:配置散乱 + env 加载脆弱
- 几十个开关;`load_dotenv(override)` 不一致;改了要重启才生效;无校验;无单一真相源。
- 后果:**没人能一眼看出此刻哪些功能开着**。今天 auto_send「设了不生效」就是吃旧进程 env。

---

## 三、改进计划(按优先级)

### 🔴 P0-1 状态持久化
**做什么:** 把 LeadContact 的游标 / 退避到期时间 / 关键词轮换索引从内存 dict 迁到 DB(可放 `workflows` 表新增列或一张 `workflow_search_state` 表)。
**改哪:** `services/outbound_engine.py`(`_leadcontact_cursor` 等三处 dict 的读写点)。
**收益:** 重启不再丢进度;消灭"重启即卡死"类症状。
**落地:** 加一张 `workflow_search_state(workflow_id, cursor_json, backoff_until, kw_rotation, updated_at)`,读写包一层 helper,替换现有 dict 访问。
**风险:** 低,纯内部状态。

### 🔴 P0-2 工作流健康视图(可观测性)
**做什么:** 一个接口 + 页面,显示单个工作流的**漏斗 + 阻塞点 + 依赖状态**:
- 各阶段线索数(found/needs_email/drafted/sent/replied)
- 每道闸今天拦了多少(off-target / 低分 / 预算 / 未验证 / suppressed)
- 各 provider 当前状态(LeadContact 退避中?Snov 401?Tavily 超额?网络可达?)
- 最近一次搜索用的关键词 + 结果
**改哪:** 新增 `routers/workflow_health.py` + 前端一个面板;数据多数已在 DB / 日志里。
**收益:** **今天几小时的排查 → 一眼看清。** 这是 ROI 最高的一项。
**风险:** 低,纯读。

### 🟠 P1-1 Provider 健康 + 显式降级
**做什么:** 给每个外部 provider 一个统一的健康状态(可用 / 超额 / 认证失败 / 不可达),挂了就**绕开 + 在 UI 上告诉用户"X 挂了,去修"**,而不是默默卡住。把"连不上的搜索引擎"自动剔除(今天靠手动设 `SEARCH_DIRECT_PROVIDER=bing`)。
**改哪:** `services/search_engine.py`(已有 Tavily/Bocha 熔断雏形,统一化)、新增 provider 状态表/缓存。

### 🟠 P1-3 解耦验证对 Snov 的硬依赖
**做什么:** 验证器除 Snov 外提供 MX+SMTP 探测兜底,或允许"未验证但 MX 有效"分级发送(今天靠关 `EMAIL_REQUIRE_VERIFIED` 硬绕)。让"验证不可用"不等于"全员发不出"。
**改哪:** `services/email_verifier.py`、`services/email_preflight.py`。

### 🟡 P2-1 闸门漏斗可视化(并入 P0-2 面板)
每道闸拦截计数落库,前端展示。"为什么都 needs_email" 一目了然。

### 🟡 P2-2 配置收敛 + 启动校验
- 把几十个 env 开关分组、加启动期校验(缺关键 key 直接告警)。
- 修掉 `agent_core` 的 `load_dotenv(override=True)`(已改为 False)这类隐性覆盖。
- UI 暴露"当前生效配置 + 哪些需重启"。

### 🟡 P2-3 拆分超长模块
`services/outbound_engine.py` 2500+ 行,搜索/补全/闸门/发送交织。按"搜索 / 补全 / 起草 / 发送 / 闸门"拆子模块,降低改一处崩一片的风险。

---

## 四、明确"不是设计问题"的部分(避免过度纠偏)
- Snov 401、Tavily 超额、连不上 Google、进程吃旧 .env —— **运维/网络/密钥**问题,非代码缺陷。
- 相关性闸跳过 off-target、验证闸拦未验证邮箱 —— **故意的省钱/保信誉策略**,只是没针对具体工作流调好。
- 多源 + 多闸门 —— 成熟获客工具(Apollo/Clay)同款思路,方向对。

---

## 五、建议落地顺序
1. **P0-2 健康视图**(先能"看见",后续所有排查都受益)
2. **P0-1 状态持久化**(消灭重启即卡死)
3. **P1-1 / P1-3 依赖健康与降级**(让失败大声说出来、不再全军覆没)
4. P2 收尾(漏斗可视化、配置收敛、模块拆分)

> **一句话:把"状态持久化"和"失败要大声说出来(可观测性)"两件做了,约 80% 的 firefighting 会消失——今天的痛苦本质不是 bug 多,是系统坏了不告诉你、坏在哪也看不见。**
