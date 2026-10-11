# LTA 動態數據接入

依據 [LTA 官方 API 文檔 6.10（2026-10-01）](https://datamall.lta.gov.sg/content/dam/datamall/datasets/LTA_DataMall_API_User_Guide.pdf)，統一接入 33 類動態數據。接口實現見 `src/route_agent/lta_data.py`；Agent 使用 `get_lta_catalog`、`get_lta_data`、`check_lta_routes` 和 `get_lta_image`，原駕車 `get_traffic` 與停車接口繼續使用。

## 覆蓋範圍

| 範圍 | 數據 |
| --- | --- |
| 公共交通 | 公交到站、運營班次、線路站序、站點、計劃線路變更、地鐵運營通告、電梯維護、車站實時擁擠和擁擠預測 |
| 道路交通 | 實時車速、事故、高速分段預計時間、交通燈故障、道路開放、施工、攝像頭、道路信息牌、積水預警 |
| 設施與接駁 | 停車餘量、自行車停車、出租車位置與站點、按郵編充電設施、全島充電設施文件、全島地理圖層 |
| 統計與時刻文件 | 四類月度客流、季度道路流量、GTFS 地鐵時刻表、實時通告和中斷期間班次更新 |

沿用本地 `.env` 的 `LTA_API_KEY`，沒有新增密鑰。新增 protobuf 解析依賴已加入 `pyproject.toml` 和 `requirements.lock.txt`；另一臺設備更新代碼後重新安裝依賴。

## 查詢與證據

- Agent 自主選擇數據集和查詢範圍，不在每條行程中自動查詢所有接口。
- 官方參數按數據集校驗；附近座標必須來自已取得的真實地點。分頁有頁數、時間和 HTTP 預算，返回是否完整及繼續位置。
- 公交到站使用 v3；交通燈使用 v2；速度區間使用 v4。充電的 `value.evLocationsData` 和 GTFS 的小寫 `link` 按真實響應處理。
- JSON、CSV ZIP、SHP ZIP 和 GTFS protobuf 均可讀取。CSV/TXT 文件可通過 `file_table` 及精確 `filters` 查詢實際表格記錄；表名來自文件清單。座標轉換使用文件聲明的座標系，不由模型生成。
- 文件下載最多 25 MB；ZIP 解壓總尺寸最多 150 MB，每表最多掃描 500,000 行，向 Agent 展示有限記錄。未掃描完時總記錄數未知，另有已讀下界。公交 OD 月度文件可能超過下載上限，接口可獲取清單但不聲稱文件已讀。
- 官方臨時下載鏈接只在內存使用；結果、導出和緩存不包含簽名查詢參數，也不會將 AccountKey 發給文件存儲服務。攝像頭按已取得的 CameraID 下載實際圖像並在本地顯示，不估算圖片中的通關等待時間。
- GTFS 的文件觀測時間與下載時間分開，過期文件不會驗證當前狀態。統計文件不是實時 ETA，出租車位置不是已訂車或報價，充電狀態也不是未來空位保證。

## 驗證和重新規劃

OneMap 返回的站號、原始站點、線路和班次標識保留在分段記錄中。LTA 檢查按實際站號和服務號匹配公交，按實際線路和站點匹配地鐵中斷；缺站號時只能通過真實站點名稱和位置唯一匹配，不能猜附近站號。

明確受影響的地鐵候選不能正式推薦。同線路的部分中斷若缺少經過站序或方向，保持未知，不把端點未命中當成整段不受影響。到站數據未覆蓋預計上車時刻、缺測、失效或查詢失敗時保留 OneMap 服務估計並顯示缺口；新到站參考不會直接改變整程時間。需要改變候車、換乘或經停時序時，Agent 必須重新查詢完整路線再驗證。

車站擁擠預測按站點、日期和半小時時段核對，電梯維護作為具體設施提示。其他道路事件供 Agent 按路線與生效時段審閱，不能由一個事件點推斷整條路封閉；積水廣播圈不是實際淹水範圍。GTFS 與 OneMap 的班次 ID 不默認等價，未建立真實標識關聯時不能宣佈本次列車取消或直接調整其 ETA。地圖中有遮蔽設施不等於已驗證全程無雨。

網頁顯示本次實際查詢的數據、採集時間、數據範圍和讀取完整性，保留舊規劃記錄兼容性。

## 小範圍接通檢查

只列目錄，不調用服務：

```bash
.venv/bin/python scripts/check_lta.py
```

真實小範圍檢查（會使用 API 配額）：

```bash
.venv/bin/python scripts/check_lta.py --all
.venv/bin/python scripts/check_lta.py --dataset train_alerts
.venv/bin/python scripts/check_lta.py --dataset gtfs_alerts --read-files
```

Windows 將解釋器換成 `.\.venv-fyp\Scripts\python.exe`。記錄保存到 `outputs/lta-check/status.json`。檢查用公開參數，不代表每個地點/線路覆蓋完整；API 成功返回也不代表全部分頁、全部文件、全部路線或未來運行狀態已核實。

本次真實證據摘要見 [LTA_DYNAMIC_ACCEPTANCE.json](LTA_DYNAMIC_ACCEPTANCE.json)。協議和安全迴歸見 `tests/test_lta_integration.py`；模擬測試與真實接通記錄分開。

本次 33 類小範圍接口查詢均取得實際響應或文件清單，保留了真實採集時間與分頁完整性。已讀取 CSV、JSON、GTFS protobuf、地理圖層和攝像頭圖像；公交 OD 大文件明確受下載上限限制，地鐵 OD 超出掃描範圍時保留部分讀取，未推斷完整總記錄數。325 項迴歸通過，依賴檢查通過。

原生網頁行程 `20261006T030346-395a1e79`：Chinatown MRT Station → Bayfront 站口，Agent 自主查詢地鐵運營通告並進行路線核對，返回約 13.4 分鐘的 OneMap 服務估計。LTA 當前狀態核對通過；沒有將通告編成一個新的時間修正數值。這次網頁測試不證明所有線路、換乘組合或未來時段都已驗收。

## 沿線車速頁面（2026-10-10）

TrafficSpeedBands 全島約 14.4 萬條記錄（約 288 頁），單次規劃無法在時間與請求預算內順序讀完，以前只讀到前 5000 條，市中心路線覆蓋常為 0%。

- `data/cache/lta_speedband_index.json`：LinkID → 頁偏移的靜態索引，只含幾何與 ID，不含車速。Streamlit 啟動時若缺失或超過 7 天會在背景重建；也可手動執行 `.venv/bin/python scripts/build_lta_link_index.py`（約 25 秒）。
- `get_traffic` 依目前驅車候選的實際幾何，只即時讀取沿線頁面（並行、計入 HTTP 預算、每頁留證據）；新增驅車候選後再次調用只補讀缺頁。沒有索引時退回順序分頁。
- 每頁返回的 LinkID 與索引比對；不一致時仍使用真實車速，但標記 `index_drift_pages`，提示索引可能過期。
- 未全覆蓋時，時間限制只能以保守上界通過：已觀測路段按速度區間下限（0–9 km/h 區間按 3 km/h），未觀測路段按 10 km/h 與服務估計中較慢者；部分路況估計本身從不用於判定通過。
