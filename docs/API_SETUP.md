# API 申请和本地配置

更新：2026-10-02。算路已改为 OSRM；ORS 仅用于地址定位。真实 OSRM 驾车／步行响应已验证，一次真实 DeepSeek 网页自动方式判断／推荐／下载已通过，其余场景仍需单独验收。

## 本地配置

在项目根目录 `.env` 填写密钥。现有凭据已保留，OSRM 端点已补充；无需新增 OSRM Key。模板见 [.env.example](../.env.example)。

```dotenv
FYP_MODE=live
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_THINKING=enabled

# ORS 仅用于文字地址定位，不再调用 Directions
ORS_API_KEY=
ORS_GEOCODE_BASE_URL=https://api.heigit.org/pelias/v1

# OSRM 真实驾车／步行路网实例，无需 Key
OSRM_DRIVING_BASE_URL=https://routing.openstreetmap.de/routed-car
OSRM_WALKING_BASE_URL=https://routing.openstreetmap.de/routed-foot
OSRM_DRIVING_PROFILE=driving
OSRM_WALKING_PROFILE=foot

OVERPASS_URL=https://overpass-api.de/api/interpreter
OPEN_METEO_BASE_URL=https://api.open-meteo.com/v1
LTA_API_KEY=
LTA_API_BASE=https://datamall2.mytransport.sg/ltaodataservice
DATAGOVSG_API_KEY=
WEATHER_API_BASE=https://api-open.data.gov.sg/v2/real-time/api
```

Key 只在本地填写，不发到聊天或截图中。原 `ORS_BASE_URL` 可保留，但正常流程不读取它；已有 Google／OpenRouter 凭据也保持不动。

## 服务与申请入口

| 服务 | 用途与配置 | 入口 |
| --- | --- | --- |
| DeepSeek | 必填 Key；理解需求和原生工具选择 | [官方平台](https://platform.deepseek.com/) |
| ORS / Pelias | 必填 Key；文字地址定位，不负责算路 | [HeiGIT 账户](https://account.heigit.org/) |
| OSRM | 驾车／步行；当前公开端点无需 Key | [API 文档](https://project-osrm.org/docs/v5.24.0/api/)、[当前服务说明与限制](https://routing.openstreetmap.de/about.html) |
| LTA DataMall | 交通和停车功能需要 AccountKey | [DataMall](https://datamall.lta.gov.sg/content/datamall/en.html) |
| OSM Overpass | 道路、医院、餐厅、咖啡店、营业标签；无需 Key | [Overpass](https://overpass-api.de/) |
| Open-Meteo | 非商业公开小时网格预报；无需 Key | [接口](https://open-meteo.com/en/docs)、[使用条件](https://open-meteo.com/en/terms) |
| data.gov.sg / NEA | 可选区域预报补充；配额 Key 可选 | [开发者说明](https://guide.data.gov.sg/developer-guide) |

DeepSeek 的工具历史保留 reasoning_content 供后续模型请求，页面和导出不展示原始思考内容。[官方协议](https://api-docs.deepseek.com/guides/thinking_mode/)。

## OSRM 路网与限制

配置不同的 car／foot 服务地址。OSRM 的路网是在预处理时确定的，仅把同一驾车服务的请求 profile 改成 foot 并不会重建步行路网。应用拒绝将同一端点当作两种实例；自建服务应使用不同端口或不同代理路径。

接口返回完整 GeoJSON、距离（米）、服务基础时间（秒）、替代路径及分段指令。请求最多两条替代加主路线，总计最多三条；服务不保证一定有替代方案。数据使用真实返回值，不补本地速度。完整实时驾车 ETA 仍需 LTA 有效覆盖。

当前公共端点真实检查：
- 驾车、步行成功，分别得到两条路线。
- `exclude=motorway` 被拒绝，属于当前实例不支持。强制避高速会明确失败，不能换回普通路线后声称满足。
- 默认路由由服务的 profile 权重决定；替代路线中距离较短不等于全局最短路线。

详见 [OSRM_SETUP.md](OSRM_SETUP.md)。应用在进程内共享锁、缓存和请求间隔，OSRM 每秒最多一次。公共服务适合轻量、非商业交互测试；20＋60 批量评测和多人运行应配置自建或获授权服务，不能靠多个进程绕过限额。

## 检查与启动

重启 PyCharm **FYP Web**，浏览器地址为 http://127.0.0.1:8502 。刷新配置只判断填写情况，不证明服务接通。

```powershell
# 只看配置状态
.\.venv-fyp\Scripts\python.exe scripts/check_live.py

# 只测真实 OSRM，不调用模型、ORS 或 LTA
.\.venv-fyp\Scripts\python.exe scripts/check_live.py --routing

# 只测公开 OSM 与 Open-Meteo
.\.venv-fyp\Scripts\python.exe scripts/check_live.py --public

# 所有配置服务的小范围检查；会消费配额
.\.venv-fyp\Scripts\python.exe scripts/check_live.py --smoke

# 真实 DeepSeek 步行规划；可能产生模型费用
.\.venv-fyp\Scripts\python.exe scripts/check_live.py --plan
```

`--routing` 报告写到 outputs/osrm-check/summary.json，其中含真实路线和证据；不支持避高速时返回非零退出码，不能把报告当作全部功能通过。保存的首次结果见 [OSRM_API_EVIDENCE.json](OSRM_API_EVIDENCE.json)。

只有 DeepSeek＋ORS 地址定位 Key，也可使用公开 OSRM 测试步行。没有 LTA 时，驾车时限和实时交通条件不能核实，停车位查询不可用。普通无时限驾车可显示明确标注的 OSRM 基础估计。错误不会切回 ORS 算路、规则或演示数据。

## OSM 和天气

道路／POI／营业标签由 Overpass 查询，缺失标签保持未知。道路标签不是实时封路证明。Open-Meteo 按最多八个真实坐标查询小时降水概率和降水量；有限网格采样不代表逐路段观测，不能保证全程不淋雨。NEA 是显式可选区域补充。

既有公开查询证据见 [OSM_WEATHER_API_EVIDENCE.json](OSM_WEATHER_API_EVIDENCE.json)；完整流程验收见 [VALIDATION.md](VALIDATION.md)。

## 分页与纯文本输入更新

网页没有示例／方式／初始时间选择，仅提交原始文本。方式未指定时模型调用 compare_modes／choose_mode；用户明确的方式不能被覆盖。缺省出发时间按当前新加坡时间，用户写出的未来时间由模型解析。

LTA 每页 500 条，使用 $skip 顺序读取直到末页；不再固定为 40 页。仍保持 96 HTTP／240 秒规划预算，为后续决策保留额度。提前停止时只使用实际成功页，记录 complete=false 和原因，不把未覆盖路段补成实时数据。

只检查 LTA 分页和 OSRM 交通匹配（不调用模型或地址定位）：

```powershell
.\.venv-fyp\Scripts\python.exe scripts/check_live.py --traffic
```

结果保存到 outputs/traffic-check/summary.json。成功返回不等于全岛数据已读完；检查 collection.complete、pages、record_count 及路线覆盖率。真实本次结果见 [LTA_PAGINATION_EVIDENCE.json](LTA_PAGINATION_EVIDENCE.json)。
