# OSRM 算路配置与验收

更新：2026-10-02。正常路线工具已由 ORS Directions 改为 OSRM Route；原 MapAgent 基线与历史 fixtures 不变。ORS Pelias 仅负责文字地址定位。

## 已配置的真实服务

```dotenv
OSRM_DRIVING_BASE_URL=https://routing.openstreetmap.de/routed-car
OSRM_WALKING_BASE_URL=https://routing.openstreetmap.de/routed-foot
OSRM_DRIVING_PROFILE=driving
OSRM_WALKING_PROFILE=foot
```

这些公共服务无需 Key。两个地址分别指向驾车、步行图；profile 名称不代替服务端预处理。模型仍选择工具顺序，后端接收真实指标、检查约束并控制发布。

协议：GET `/route/v1/{profile}/{lon,lat;lon,lat}`，`geometries=geojson`、`overview=full`、`steps=true`、`radiuses=150;150`。每段最多三条路径；OSRM alternatives 数量为 count−1，应用再限制返回总数。每个指令的几何范围映射到完整路径索引，供 LTA 道路名／几何匹配使用。

路线来源为 OSRM，发布时每段必须引用 OSRM Route 证据；旧 ORS Directions 证据不能替代。没有算路 Key、没有 ORS Directions 请求，也没有网络失败后的本地速度或其他引擎回退。缓存按实际端点／参数隔离；不同引擎的路线 ID 不混用。

## 真实检查结果

2026-10-02 17:26:03（新加坡时间）提交固定公共坐标 `[103.85,1.29]` → `[103.86,1.30]`。两个坐标仅用于接口检查，并非经过定位的具体建筑入口。

| 查询 | 实际结果 | 是否缓存 |
| --- | --- | --- |
| 驾车 | 两条：2736.9 米／269.1 秒；3174.3 米／299.4 秒 | 否 |
| 步行 | 两条：1799.6 米／1441.6 秒；1894.5 米／1519.9 秒 | 否 |
| 驾车避高速 | 当前实例不支持 `exclude=motorway`，明确返回 unsupported_feature | 否 |

记录包含几何、分段、来源、获取时间和响应哈希：[OSRM_API_EVIDENCE.json](OSRM_API_EVIDENCE.json)。这些时间是基础服务估计，不是实时交通时间；替代路径不是全局最短保证。真实 DeepSeek 决策、完整交通融合和网页提交仍待单独验收。

## 避高速与自建实例

OSRM 支持哪些 exclude class 取决于服务器使用的 profile 与预处理数据。应用对硬性避高速传 `exclude=motorway`；服务拒绝时不会删除该参数重试普通路线。若成功响应的指令仍带 motorway 类别，也会拒绝发布。

要恢复此功能，需要部署并验收支持 motorway 排除的 car 实例。可用官方 car.lua（带 motorway excludable class）和 foot.lua 分别处理新加坡 OSM 数据，再运行两个 OSRM 服务。以实际部署端口为准，配置示例：

```dotenv
OSRM_DRIVING_BASE_URL=http://127.0.0.1:5000
OSRM_WALKING_BASE_URL=http://127.0.0.1:5001
OSRM_DRIVING_PROFILE=driving
OSRM_WALKING_PROFILE=foot
```

本轮没有启动或构建 Docker 路网：检测到 Docker CLI，但 Docker Linux 引擎未运行。当前使用真实公共服务，不把“可自建”写成“已部署”。完整最短距离策略还需要相应预处理 profile，不能靠给默认请求添加虚构 preference 参数实现。

## 使用限制与验证

当前公共服务要求每秒最多一次请求，只允许适度、非商业使用。代码在同一进程的所有 worker 之间共享请求锁，响应或失败后至少间隔 1.05 秒；重试也受限，缓存命中不发请求。多个 PyCharm／网页进程不会共享锁，应只运行一个公共服务实例；批量评测、多人服务改用自建或获授权端点。

```powershell
.\.venv-fyp\Scripts\python.exe scripts/check_live.py --routing
.\.venv-fyp\Scripts\python.exe -m pytest -q
```

当前 121 项自动化通过，包括 13 项新的 OSRM 协议／错误／几何／模式／共享限速检查和 2 项工具错误分类检查。自动化使用模拟返回，与上表真实证据分开。

官方依据：[OSRM API](https://project-osrm.org/docs/v5.24.0/api/)、[服务使用说明](https://routing.openstreetmap.de/about.html)、[car profile](https://github.com/Project-OSRM/osrm-backend/blob/master/profiles/car.lua)、[部署说明](https://github.com/Project-OSRM/osrm-backend)。

## 后续真实网页个案

2026-10-02 已通过一个未指定交通方式的真实 DeepSeek 网页流程：比较 OSRM car／foot，选择步行，显示约 6.1 分钟基础总时长和地图，并下载一致的规划记录。首屏改为空白自然语言输入。交通数据部分匹配不能补造完整实时总时间；验收与证据见 [FREE_TEXT_AND_TRAFFIC_FIX.md](FREE_TEXT_AND_TRAFFIC_FIX.md)。
