# DeepSeek 在线改造验收记录

日期：2026-10-02，新加坡时间。

## 状态边界

代码接入、配置入口和自动化测试已实现。**OSM 道路／医院／餐厅与 Open-Meteo 已完成小范围真实响应检查，完整 DeepSeek 工具规划与在线网页流程仍未验收。** 页面“已配置”只判断 Key 非空；配置检查不等于接通。下方早期缺凭据记录保留为历史时间点，不代表当前配置。

旧版的真实中心区域 OSM 下载与合成网页演示保留在 [history/VALIDATION_V1.md](history/VALIDATION_V1.md)，不能作为新 ORS / DeepSeek 服务验收依据。

## 自动化检查

- 新版 test_online.py：21 项，模拟模型与 HTTP 返回；禁止真实外部网络。覆盖原生连续 tool_calls、思考历史回传、错误模型不回退、第一次 POI 验证失败后扩大查询并恢复、参数／未知 ID／重复 ID、硬约束与时间证据、有限重规划、预算、ORS 双 profile、HTTP 重试／缓存／分页、认证信息不外泄、交通方向／覆盖／未来出发／过期、停车零位、停车预留／入口、步行汇总、预报时段与几何连接。
- 新版 test_web.py：4 项，用模拟规划结果；验证缺 Key 禁止开始、无快照／规则选择、后台完成后结果／地图组件／下载、修改提交、澄清继续、服务错误提示。
- 历史 fixtures 后端回归：47 项，明确属于旧机制研究。
- 环境依赖检查：无冲突。

最终执行：python -m pytest -q，72 passed；代码语法编译通过。没有任何模拟结果被写成真实服务响应。

## 配置存在性检查

scripts/check_live.py 默认只读 Key 是否非空，不发网络请求。2026-10-02 检查：DeepSeek=false、ORS=false、LTA=false、data.gov.sg=false。输出 outputs/online-check/summary.json，connectivity 为空，acceptance=未验收。

--public 仅调用公开 OSM／Open-Meteo；--smoke 另检查配置好的 ORS／LTA；--plan 调用正常 DeepSeek 规划，可能产生费用。此轮已执行小范围 --smoke 和 --public，未执行真实 DeepSeek 规划。

## 待真实验收

| 功能 | 状态与缺口 |
| --- | --- |
| DeepSeek 官方认证与工具协议 | Key 当前已配置，真实模型选择质量、延迟与输出长度仍未验收 |
| ORS Pelias / driving-car / foot-walking | Pelias 真实响应通过；算路、消歧确认和道路入口未完成验收 |
| OSM Overpass | 道路／医院／餐厅真实查询通过；完整经停和营业流程仍待验收 |
| LTA v4 速度区间、事故、车位 | Key 当前已配置；速度区间已收到 40 页真实响应，但分页上限触发，未取得完整交通上下文；未作有效 ETA 证据，方向匹配与车位未验收 |
| Open-Meteo / NEA | Open-Meteo 真实公开预报响应通过；路线关联和网页卡片仅模拟检查；NEA 可选补充未在线验收 |
| 驾车／步行／停车接驳网页 | 自动化模拟验证；完整真实流程待在线验收 |
| 新版在线对照／消融实验 | 未实施统一真实快照运行器；旧 420 次合成实验不替代新系统效果评测 |

每次真实验收保存 run_id、响应来源、获取时间／有效期、缓存状态、模型和代码哈希；单接口成功和整个用户流程成功分别记录。需完成的流程清单见 [实施方案](FYP_IMPLEMENTATION_PLAN.md)。

## 浏览器缺凭据检查

2026-10-02 在 http://127.0.0.1:8502 打开实际新版页面：DeepSeek／ORS／LTA 显示缺少 Key，开始在线规划按钮禁用，页面没有规则解析或快照选择。未提交真实服务请求。截图见 [缺密钥页面](screenshots/online-missing-keys.png)。这仅证明缺配置状态的网页提示，不代表真实规划验收。

## 2026-10-02 OSM 与 Open-Meteo 更新

最终自动化 85 passed：47 项旧机制、21 项在线协议、12 项 OSM／天气边界和 5 项网页模拟测试。新增道路标签／稳定 ID、医院／餐厅／咖啡店筛选、Overpass 部分失败拒绝、Open-Meteo 坐标／网格与小时单位、整点边界、未知降水、过期数据、NEA 独立补充检查。

16:57:31（新加坡时间）在固定公共 City Hall 附近查询：Open-Meteo 1 个坐标预报、OSM 10 条道路、1 个医院、10 个餐厅，均为真实网络返回，非缓存。不同查询半径为道路 200 米、医院 1500 米、餐厅 300 米；这是有上限的接口检查，不是全区域数量统计。来源、获取时间和响应哈希保存在 [OSM_WEATHER_API_EVIDENCE.json](OSM_WEATHER_API_EVIDENCE.json)。全部查询不需要 API Key。

早期 Overpass POST 返回 HTTP 406，GET 曾返回服务忙的 504。现采用 GET、明确 JSON 接受头和 64 MiB 内存上限，小范围查询成功；不使用离线结果替代。其他公共镜像连接超时，未切换当前配置。完整 Agent／路线／交通／网页过程仍未验收。

网页新增医院示例、Open-Meteo 天气采样表和 OSM 道路属性的模拟显示检查通过；当前真实页面配置截图见 [osm-open-meteo-configured.png](screenshots/osm-open-meteo-configured.png)。截图只验证页面配置显示，没有提交模型规划。

## 2026-10-02 五阶段架构：按需工具调用

根据用户五阶段目标，移除仅因步行而强制天气、仅因驾车而强制交通的固定查询。相关时间、拥堵、营业及天气需求仍要求尝试对应服务；硬约束检查与证据发布规则继续生效。

验证命令：`.venv-fyp/Scripts/python.exe -m pytest -q`。结果：96 passed in 6.36s。新增 11 项检查覆盖无关工具可跳过、驾车基础时间与实时 ETA 区分，以及相关需求不能绕过交通／天气查询。模拟原生工具会话也验证普通步行可以不调用天气直接完成。

这些是自动化协议与行为证据，没有新增真实模型、交通或网页验收。最快／最短／避高速多策略接口及 OSRM／OSMnx 适配待实施。架构核对见 [FYP_AGENT_ARCHITECTURE.md](FYP_AGENT_ARCHITECTURE.md)。

## 2026-10-02 后续 OSRM 迁移与真实查询

用户指定改为 OSRM，已替换正常路由提供者、路线数据来源、证据发布检查、比较与网页指标；ORS 仅用于地址定位。`.env` 新增 car／foot 独立端点，所有已有 Key 保留，没有新增算路 Key。PyCharm 原 FYP Web 配置沿用。

全量 109 passed in 9.22s。新增 13 项模拟 OSRM 协议检查覆盖 GET／单位／分段索引、无 ORS 算路认证、不同模式路网、NoRoute、InvalidValue、避高速失败不回退、矛盾 motorway 标记、不合法指标、共享限速和失败后的间隔。原生工具闭环测试现在引用 OSRM Route 证据。

17:26:03（新加坡时间）真实查询固定公共坐标：car／foot 各返回两条路线，均非缓存；硬性 `exclude=motorway` 返回 unsupported_feature。真实结果与几何保存到 [OSRM_API_EVIDENCE.json](OSRM_API_EVIDENCE.json)，配置及数值见 [OSRM_SETUP.md](OSRM_SETUP.md)。OSRM 服务估计不包含已核实实时交通。

强制避高速需要配置支持排除的 OSRM 实例；本轮没有启动 Docker 图构建。最短距离多策略接口、完整真实模型／LTA／网页流程和批量评测仍未验收。

最终补充：算路服务故障／不支持功能现在保留明确的 tool_error，不能写成道路无解；NoRoute 仍属于有限范围未找到。新增两项检查，全量 111 passed in 9.11s。已重启网页，页面实际显示 OSRM 独立 car／foot 配置与公共服务限制；[配置截图](screenshots/osrm-configured.png) 只证明配置页面显示，未提交真实模型任务。

## 2026-10-02 自然语言入口、自动方式与 LTA 分页修复

全量自动化 **121 passed in 17.21s**。新增测试覆盖自由文本入口／空白守卫、未来时间、可信澄清、真实工具形状的方式比较与选择、不可改写明确方式、证据引用、超过 40 页分页、重复页终止与预算内部分数据保留。测试模拟与真实验收严格分开。

18:13:47（新加坡时间）真实 LTA 检查获得速度区间 76 页／38,000 条、事故 1 页／22 条，总计 78 次 HTTP。速度采集因预留后续调用预算停止，complete=false；有效数据被保留。两条真实 OSRM 路线方向／几何匹配覆盖率分别约 63.0%／74.3%，完整交通时间保持未知。见 [LTA_PAGINATION_EVIDENCE.json](LTA_PAGINATION_EVIDENCE.json)。

实际网页提交未指定方式的固定公共坐标需求，真实 DeepSeek 调用 compare_modes／choose_mode 后选择步行，状态 verified，推荐 453.8 米／365.2 秒。run_id=20261002T102102-4f52dc9a；约 27 秒，HTTP 13 次、工具 14 次、模型交互 10 次，候选 2 条，无重规划。已看到地图和约 6.1 分钟基础总时长；实际点击导出并验证下载 JSON 与保存结果内容一致。证据文件无真实 Key 值。见 [AUTO_MODE_WEB_EVIDENCE.json](AUTO_MODE_WEB_EVIDENCE.json)。

[空白输入截图](screenshots/free-text-input.png)、[自动方式结果截图](screenshots/auto-mode-live-result.png)、[基础时长截图](screenshots/base-time-live-result.png)。这证明一个真实网页自动方式选择／推荐／地图／下载个案，不代替复杂经停、停车接驳、失败重规划、天气时限和批量评测。详细改造与剩余问题见 [FREE_TEXT_AND_TRAFFIC_FIX.md](FREE_TEXT_AND_TRAFFIC_FIX.md)。

## 2026-10-02 金沙地点候选修复

用户输入“滨海湾金沙（Marina Bay Sands）”后，旧版将中英文整串发送 Pelias，结果包含 Gardens by the Bay 等无关地标；页面默认显示第一项。模型提取的终点本身是金沙，错误发生在定位与候选展示。

现在优先查询用户明确提供的括号英文别名，不由模型翻译或编造名称；返回结果必须包含完整查询名称，不能仅因共享 Bay 等词就被接受。没有匹配时要求重新输入，保留真实来源与证据。地点确认框默认空白，未选择不能提交，并支持替换地点查询。

全量自动化 **126 passed in 35.00s**；新增检查覆盖别名、相邻地标拒绝、高置信度错误名称拒绝、地址与词边界、无默认确认及重新输入。真实非缓存定位查询 2 次 HTTP，金沙只得到 Marina Bay Sands, Bayfront Subzone, Singapore；市政厅得到 City Hall, Singapore。证据：[PLACE_NAME_MATCH_EVIDENCE.json](PLACE_NAME_MATCH_EVIDENCE.json)。

补充：真实继续规划复测暴露了原生 ask_user 缺少执行函数和重新解析丢失经停约束的问题；现已注册实际执行方法，在页面继续提交中保留已验证请求，并向模型提供不含原始输入的参数字段错误，便于纠正工具调用。新增三项回归，全量 **129 passed in 17.23s**。原 20261002T104054-ea714139 运行达到轮数上限，明确记为失败，不能作为路线成功证据。

最后真实网页复测成功：run_id=20261002T104506-e0948cf3，状态 verified；终点 Marina Bay Sands, Bayfront Subzone, Singapore，经停 Toast Box，停留 600 秒；模型选择 walking，距离 2015.8 米，基础总时长 2212.5 秒（服务预计，含停留）。本次直接完成，不代表所有澄清／重规划已在线验收。完整记录：[MARINA_BAY_SANDS_WEB_EVIDENCE.json](MARINA_BAY_SANDS_WEB_EVIDENCE.json)；[网页截图](screenshots/marina-bay-sands-fixed.png)。
