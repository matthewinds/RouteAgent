# RouteAgent：DeepSeek 真实在线出行规划

网页正常流程：DeepSeek 理解需求 → 自主调用真实地图、POI、路况、天气或停车工具 → 验证和有限重规划 → 比较并选择有证据的方案。支持新加坡驾车、全程步行、驾车后一次停车步行接驳，以及最多一个业务经停点。

**当前交付是在线接入代码。Key 的填写状态由页面检查；OSM、Open-Meteo 与 OSRM 驾车／步行已完成小范围真实响应检查，一次真实网页自动判断方式、推荐和下载流程已通过；其他场景及效果仍待验收。自动化模拟测试不是接通证明。缺少必需 Key 时网页禁止开始规划，不提供演示或规则回退。**

## 启动

1. 在根目录 `.env` 填写 `DEEPSEEK_API_KEY`、`ORS_API_KEY`（ORS 仅用于地址定位）；OSRM 算路端点已配置，无需 Key。驾车交通与停车功能另需 `LTA_API_KEY`。不要覆盖已有 Google/OpenRouter 凭据。
2. 在 PyCharm 选择 **FYP Web**，运行后打开 http://127.0.0.1:8502 。
3. 直接输入自己的出行需求，没有示例或交通方式选择框。未指定方式时，DeepSeek 比较真实步行／驾车路线再选择；未指定出发时间按现在。点击页面“刷新配置状态”。“已配置”只表示 Key 非空，调用是否成功需要真实运行验证。

也可在项目根目录运行：

```powershell
.\scripts\start_web.ps1
```

新环境：`.venv-fyp\Scripts\python.exe`（Python 3.11）。现有环境依赖已安装。重新安装可使用：

```powershell
python -m venv .venv-fyp
.\.venv-fyp\Scripts\python.exe -m pip install -r requirements.lock.txt
```

## 保存的方案和说明

- [OSRM 配置与限制](docs/OSRM_SETUP.md)：驾车／步行端点、避高速限制和真实证据。
- [API 申请与填写](docs/API_SETUP.md)：需要哪些 Key、官方入口、检查方法。
- [PyCharm 启动](docs/PYCHARM_SETUP.md)：解释器、运行菜单、网页和测试。
- [模块实施方案](docs/FYP_IMPLEMENTATION_PLAN.md)：U00–U04、证据规则、限额与剩余验收。
- [新版验收记录](docs/VALIDATION.md)：自动化与真实在线验收分开记录。
- [旧项目归档索引](docs/ARCHIVE_INDEX.md)：原项目整理与恢复。
- [后端接口](src/route_agent/service.py)：`plan_route(text, clarifications, settings=..., progress=...)`。

## 目录

```text
app/                        真实在线 Streamlit 页面
src/route_agent/             DeepSeek 工具循环与真实服务适配器
src/route_agent/fixtures/    历史合成机制测试，正常入口不导入
tests/                      协议、发布检查和网页自动化测试
docs/                       当前方案、配置与验收；history 保存旧版记录
data/cache/online-v2/        带时间和来源的真实响应缓存（不包含认证头）
outputs/                    v2 需求、分段路线、证据、事件和诊断
baselines/mapagent/          独立原 MapAgent 基线
```

OSRM 提供基础服务预计时间，不是实时交通 ETA。LTA 只更新方向和几何匹配且有效的路段；覆盖不完整时完整驾车 ETA 未知。Open-Meteo 是天气模型网格预报，NEA 为可选区域补充；均不能保证全程无雨。当前停车位不能保证未来可用。未知硬约束只能列为待核实。

历史 20/60 请求和七种合成策略保留供机制研究，其结果不代表新版 DeepSeek 在线系统效果。旧版导出仍保留，不通过当前在线入口重放。

基础预计总时长与交通修正总时长分开显示；交通证据缺失不会遮住已经存在的 OSRM 基础估计。LTA 读取不再固定于 40 页，预算内自适应分页并保留有效部分、标记完整性；硬时限继续只由有效证据验证。真实证据见 [修复验收](docs/FREE_TEXT_AND_TRAFFIC_FIX.md)。
