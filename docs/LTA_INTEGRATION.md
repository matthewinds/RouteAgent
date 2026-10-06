# LTA 动态数据接入

依据 [LTA 官方 API 文档 6.10（2026-10-01）](https://datamall.lta.gov.sg/content/dam/datamall/datasets/LTA_DataMall_API_User_Guide.pdf)，统一接入 33 类动态数据。接口实现见 `src/route_agent/lta_data.py`；Agent 使用 `get_lta_catalog`、`get_lta_data`、`check_lta_routes` 和 `get_lta_image`，原驾车 `get_traffic` 与停车接口继续使用。

## 覆盖范围

| 范围 | 数据 |
| --- | --- |
| 公共交通 | 公交到站、运营班次、线路站序、站点、计划线路变更、地铁运营通告、电梯维护、车站实时拥挤和拥挤预测 |
| 道路交通 | 实时车速、事故、高速分段预计时间、交通灯故障、道路开放、施工、摄像头、道路信息牌、积水预警 |
| 设施与接驳 | 停车余量、自行车停车、出租车位置与站点、按邮编充电设施、全岛充电设施文件、全岛地理图层 |
| 统计与时刻文件 | 四类月度客流、季度道路流量、GTFS 地铁时刻表、实时通告和中断期间班次更新 |

沿用本地 `.env` 的 `LTA_API_KEY`，没有新增密钥。新增 protobuf 解析依赖已加入 `pyproject.toml` 和 `requirements.lock.txt`；另一台设备更新代码后重新安装依赖。

## 查询与证据

- Agent 自主选择数据集和查询范围，不在每条行程中自动查询所有接口。
- 官方参数按数据集校验；附近坐标必须来自已取得的真实地点。分页有页数、时间和 HTTP 预算，返回是否完整及继续位置。
- 公交到站使用 v3；交通灯使用 v2；速度区间使用 v4。充电的 `value.evLocationsData` 和 GTFS 的小写 `link` 按真实响应处理。
- JSON、CSV ZIP、SHP ZIP 和 GTFS protobuf 均可读取。CSV/TXT 文件可通过 `file_table` 及精确 `filters` 查询实际表格记录；表名来自文件清单。坐标转换使用文件声明的坐标系，不由模型生成。
- 文件下载最多 25 MB；ZIP 解压总尺寸最多 150 MB，每表最多扫描 500,000 行，向 Agent 展示有限记录。未扫描完时总记录数未知，另有已读下界。公交 OD 月度文件可能超过下载上限，接口可获取清单但不声称文件已读。
- 官方临时下载链接只在内存使用；结果、导出和缓存不包含签名查询参数，也不会将 AccountKey 发给文件存储服务。摄像头按已取得的 CameraID 下载实际图像并在本地显示，不估算图片中的通关等待时间。
- GTFS 的文件观测时间与下载时间分开，过期文件不会验证当前状态。统计文件不是实时 ETA，出租车位置不是已订车或报价，充电状态也不是未来空位保证。

## 验证和重新规划

OneMap 返回的站号、原始站点、线路和班次标识保留在分段记录中。LTA 检查按实际站号和服务号匹配公交，按实际线路和站点匹配地铁中断；缺站号时只能通过真实站点名称和位置唯一匹配，不能猜附近站号。

明确受影响的地铁候选不能正式推荐。同线路的部分中断若缺少经过站序或方向，保持未知，不把端点未命中当成整段不受影响。到站数据未覆盖预计上车时刻、缺测、失效或查询失败时保留 OneMap 服务估计并显示缺口；新到站参考不会直接改变整程时间。需要改变候车、换乘或经停时序时，Agent 必须重新查询完整路线再验证。

车站拥挤预测按站点、日期和半小时时段核对，电梯维护作为具体设施提示。其他道路事件供 Agent 按路线与生效时段审阅，不能由一个事件点推断整条路封闭；积水广播圈不是实际淹水范围。GTFS 与 OneMap 的班次 ID 不默认等价，未建立真实标识关联时不能宣布本次列车取消或直接调整其 ETA。地图中有遮蔽设施不等于已验证全程无雨。

网页显示本次实际查询的数据、采集时间、数据范围和读取完整性，保留旧规划记录兼容性。

## 小范围接通检查

只列目录，不调用服务：

```bash
.venv/bin/python scripts/check_lta.py
```

真实小范围检查（会使用 API 配额）：

```bash
.venv/bin/python scripts/check_lta.py --all
.venv/bin/python scripts/check_lta.py --dataset train_alerts
.venv/bin/python scripts/check_lta.py --dataset gtfs_alerts --read-files
```

Windows 将解释器换成 `.\.venv-fyp\Scripts\python.exe`。记录保存到 `outputs/lta-check/status.json`。检查用公开参数，不代表每个地点/线路覆盖完整；API 成功返回也不代表全部分页、全部文件、全部路线或未来运行状态已核实。

本次真实证据摘要见 [LTA_DYNAMIC_ACCEPTANCE.json](LTA_DYNAMIC_ACCEPTANCE.json)。协议和安全回归见 `tests/test_lta_integration.py`；模拟测试与真实接通记录分开。

本次 33 类小范围接口查询均取得实际响应或文件清单，保留了真实采集时间与分页完整性。已读取 CSV、JSON、GTFS protobuf、地理图层和摄像头图像；公交 OD 大文件明确受下载上限限制，地铁 OD 超出扫描范围时保留部分读取，未推断完整总记录数。325 项回归通过，依赖检查通过。

原生网页行程 `20261006T030346-395a1e79`：Chinatown MRT Station → Bayfront 站口，Agent 自主查询地铁运营通告并进行路线核对，返回约 13.4 分钟的 OneMap 服务估计。LTA 当前状态核对通过；没有将通告编成一个新的时间修正数值。这次网页测试不证明所有线路、换乘组合或未来时段都已验收。
