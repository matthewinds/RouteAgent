# DeepSeek 在線改造驗收記錄

日期：2026-10-02，新加坡時間。

## 狀態邊界

代碼接入、配置入口和自動化測試已實現。**OSM 道路／醫院／餐廳與 Open-Meteo 已完成小範圍真實響應檢查，完整 DeepSeek 工具規劃與在線網頁流程仍未驗收。** 頁面“已配置”只判斷 Key 非空；配置檢查不等於接通。下方早期缺憑據記錄保留為歷史時間點，不代表當前配置。

舊版的真實中心區域 OSM 下載與合成網頁演示保留在 [history/VALIDATION_V1.md](history/VALIDATION_V1.md)，不能作為新 ORS / DeepSeek 服務驗收依據。

## 自動化檢查

- 新版 test_online.py：21 項，模擬模型與 HTTP 返回；禁止真實外部網絡。覆蓋原生連續 tool_calls、思考歷史回傳、錯誤模型不回退、第一次 POI 驗證失敗後擴大查詢並恢復、參數／未知 ID／重複 ID、硬約束與時間證據、有限重規劃、預算、ORS 雙 profile、HTTP 重試／緩存／分頁、認證信息不外洩、交通方向／覆蓋／未來出發／過期、停車零位、停車預留／入口、步行彙總、預報時段與幾何連接。
- 新版 test_web.py：4 項，用模擬規劃結果；驗證缺 Key 禁止開始、無快照／規則選擇、後臺完成後結果／地圖組件／下載、修改提交、澄清繼續、服務錯誤提示。
- 歷史 fixtures 後端迴歸：47 項，明確屬於舊機制研究。
- 環境依賴檢查：無衝突。

最終執行：python -m pytest -q，72 passed；代碼語法編譯通過。沒有任何模擬結果被寫成真實服務響應。

## 配置存在性檢查

scripts/check_live.py 默認只讀 Key 是否非空，不髮網絡請求。2026-10-02 檢查：DeepSeek=false、ORS=false、LTA=false、data.gov.sg=false。輸出 outputs/online-check/summary.json，connectivity 為空，acceptance=未驗收。

--public 僅調用公開 OSM／Open-Meteo；--smoke 另檢查配置好的 ORS／LTA；--plan 調用正常 DeepSeek 規劃，可能產生費用。此輪已執行小範圍 --smoke 和 --public，未執行真實 DeepSeek 規劃。

## 待真實驗收

| 功能 | 狀態與缺口 |
| --- | --- |
| DeepSeek 官方認證與工具協議 | Key 當前已配置，真實模型選擇質量、延遲與輸出長度仍未驗收 |
| ORS Pelias / driving-car / foot-walking | Pelias 真實響應通過；算路、消歧確認和道路入口未完成驗收 |
| OSM Overpass | 道路／醫院／餐廳真實查詢通過；完整經停和營業流程仍待驗收 |
| LTA v4 速度區間、事故、車位 | Key 當前已配置；速度區間已收到 40 頁真實響應，但分頁上限觸發，未取得完整交通上下文；未作有效 ETA 證據，方向匹配與車位未驗收 |
| Open-Meteo / NEA | Open-Meteo 真實公開預報響應通過；路線關聯和網頁卡片僅模擬檢查；NEA 可選補充未在線驗收 |
| 駕車／步行／停車接駁網頁 | 自動化模擬驗證；完整真實流程待在線驗收 |
| 新版在線對照／消融實驗 | 未實施統一真實快照運行器；舊 420 次合成實驗不替代新系統效果評測 |

每次真實驗收保存 run_id、響應來源、獲取時間／有效期、緩存狀態、模型和代碼哈希；單接口成功和整個用戶流程成功分別記錄。需完成的流程清單見 [實施方案](FYP_IMPLEMENTATION_PLAN.md)。

## 瀏覽器缺憑據檢查

2026-10-02 在 http://127.0.0.1:8502 打開實際新版頁面：DeepSeek／ORS／LTA 顯示缺少 Key，開始在線規劃按鈕禁用，頁面沒有規則解析或快照選擇。未提交真實服務請求。截圖見 [缺密鑰頁面](screenshots/online-missing-keys.png)。這僅證明缺配置狀態的網頁提示，不代表真實規劃驗收。

## 2026-10-02 OSM 與 Open-Meteo 更新

最終自動化 85 passed：47 項舊機制、21 項在線協議、12 項 OSM／天氣邊界和 5 項網頁模擬測試。新增道路標籤／穩定 ID、醫院／餐廳／咖啡店篩選、Overpass 部分失敗拒絕、Open-Meteo 座標／網格與小時單位、整點邊界、未知降水、過期數據、NEA 獨立補充檢查。

16:57:31（新加坡時間）在固定公共 City Hall 附近查詢：Open-Meteo 1 個座標預報、OSM 10 條道路、1 個醫院、10 個餐廳，均為真實網絡返回，非緩存。不同查詢半徑為道路 200 米、醫院 1500 米、餐廳 300 米；這是有上限的接口檢查，不是全區域數量統計。來源、獲取時間和響應哈希保存在 [OSM_WEATHER_API_EVIDENCE.json](OSM_WEATHER_API_EVIDENCE.json)。全部查詢不需要 API Key。

早期 Overpass POST 返回 HTTP 406，GET 曾返回服務忙的 504。現採用 GET、明確 JSON 接受頭和 64 MiB 內存上限，小範圍查詢成功；不使用離線結果替代。其他公共鏡像連接超時，未切換當前配置。完整 Agent／路線／交通／網頁過程仍未驗收。

網頁新增醫院示例、Open-Meteo 天氣採樣表和 OSM 道路屬性的模擬顯示檢查通過；當前真實頁面配置截圖見 [osm-open-meteo-configured.png](screenshots/osm-open-meteo-configured.png)。截圖只驗證頁面配置顯示，沒有提交模型規劃。

## 2026-10-02 五階段架構：按需工具調用

根據用戶五階段目標，移除僅因步行而強制天氣、僅因駕車而強制交通的固定查詢。相關時間、擁堵、營業及天氣需求仍要求嘗試對應服務；硬約束檢查與證據發佈規則繼續生效。

驗證命令：`.venv-fyp/Scripts/python.exe -m pytest -q`。結果：96 passed in 6.36s。新增 11 項檢查覆蓋無關工具可跳過、駕車基礎時間與實時 ETA 區分，以及相關需求不能繞過交通／天氣查詢。模擬原生工具會話也驗證普通步行可以不調用天氣直接完成。

這些是自動化協議與行為證據，沒有新增真實模型、交通或網頁驗收。最快／最短／避高速多策略接口及 OSRM／OSMnx 適配待實施。架構核對見 [FYP_AGENT_ARCHITECTURE.md](FYP_AGENT_ARCHITECTURE.md)。

## 2026-10-02 後續 OSRM 遷移與真實查詢

用戶指定改為 OSRM，已替換正常路由提供者、路線數據來源、證據發佈檢查、比較與網頁指標；ORS 僅用於地址定位。`.env` 新增 car／foot 獨立端點，所有已有 Key 保留，沒有新增算路 Key。PyCharm 原 FYP Web 配置沿用。

全量 109 passed in 9.22s。新增 13 項模擬 OSRM 協議檢查覆蓋 GET／單位／分段索引、無 ORS 算路認證、不同模式路網、NoRoute、InvalidValue、避高速失敗不回退、矛盾 motorway 標記、不合法指標、共享限速和失敗後的間隔。原生工具閉環測試現在引用 OSRM Route 證據。

17:26:03（新加坡時間）真實查詢固定公共座標：car／foot 各返回兩條路線，均非緩存；硬性 `exclude=motorway` 返回 unsupported_feature。真實結果與幾何保存到 [OSRM_API_EVIDENCE.json](OSRM_API_EVIDENCE.json)，配置及數值見 [OSRM_SETUP.md](OSRM_SETUP.md)。OSRM 服務估計不包含已核實實時交通。

強制避高速需要配置支持排除的 OSRM 實例；本輪沒有啟動 Docker 圖構建。最短距離多策略接口、完整真實模型／LTA／網頁流程和批量評測仍未驗收。

最終補充：算路服務故障／不支持功能現在保留明確的 tool_error，不能寫成道路無解；NoRoute 仍屬於有限範圍未找到。新增兩項檢查，全量 111 passed in 9.11s。已重啟網頁，頁面實際顯示 OSRM 獨立 car／foot 配置與公共服務限制；[配置截圖](screenshots/osrm-configured.png) 只證明配置頁面顯示，未提交真實模型任務。

## 2026-10-02 自然語言入口、自動方式與 LTA 分頁修復

全量自動化 **121 passed in 17.21s**。新增測試覆蓋自由文本入口／空白守衛、未來時間、可信澄清、真實工具形狀的方式比較與選擇、不可改寫明確方式、證據引用、超過 40 頁分頁、重複頁終止與預算內部分數據保留。測試模擬與真實驗收嚴格分開。

18:13:47（新加坡時間）真實 LTA 檢查獲得速度區間 76 頁／38,000 條、事故 1 頁／22 條，總計 78 次 HTTP。速度採集因預留後續調用預算停止，complete=false；有效數據被保留。兩條真實 OSRM 路線方向／幾何匹配覆蓋率分別約 63.0%／74.3%，完整交通時間保持未知。見 [LTA_PAGINATION_EVIDENCE.json](LTA_PAGINATION_EVIDENCE.json)。

實際網頁提交未指定方式的固定公共座標需求，真實 DeepSeek 調用 compare_modes／choose_mode 後選擇步行，狀態 verified，推薦 453.8 米／365.2 秒。run_id=20261002T102102-4f52dc9a；約 27 秒，HTTP 13 次、工具 14 次、模型交互 10 次，候選 2 條，無重規劃。已看到地圖和約 6.1 分鐘基礎總時長；實際點擊導出並驗證下載 JSON 與保存結果內容一致。證據文件無真實 Key 值。見 [AUTO_MODE_WEB_EVIDENCE.json](AUTO_MODE_WEB_EVIDENCE.json)。

[空白輸入截圖](screenshots/free-text-input.png)、[自動方式結果截圖](screenshots/auto-mode-live-result.png)、[基礎時長截圖](screenshots/base-time-live-result.png)。這證明一個真實網頁自動方式選擇／推薦／地圖／下載個案，不代替複雜經停、停車接駁、失敗重規劃、天氣時限和批量評測。詳細改造與剩餘問題見 [FREE_TEXT_AND_TRAFFIC_FIX.md](FREE_TEXT_AND_TRAFFIC_FIX.md)。

## 2026-10-02 金沙地點候選修復

用戶輸入“濱海灣金沙（Marina Bay Sands）”後，舊版將中英文整串發送 Pelias，結果包含 Gardens by the Bay 等無關地標；頁面默認顯示第一項。模型提取的終點本身是金沙，錯誤發生在定位與候選展示。

現在優先查詢用戶明確提供的括號英文別名，不由模型翻譯或編造名稱；返回結果必須包含完整查詢名稱，不能僅因共享 Bay 等詞就被接受。沒有匹配時要求重新輸入，保留真實來源與證據。地點確認框默認空白，未選擇不能提交，並支持替換地點查詢。

全量自動化 **126 passed in 35.00s**；新增檢查覆蓋別名、相鄰地標拒絕、高置信度錯誤名稱拒絕、地址與詞邊界、無默認確認及重新輸入。真實非緩存定位查詢 2 次 HTTP，金沙只得到 Marina Bay Sands, Bayfront Subzone, Singapore；市政廳得到 City Hall, Singapore。證據：[PLACE_NAME_MATCH_EVIDENCE.json](PLACE_NAME_MATCH_EVIDENCE.json)。

補充：真實繼續規劃複測暴露了原生 ask_user 缺少執行函數和重新解析丟失經停約束的問題；現已註冊實際執行方法，在頁面繼續提交中保留已驗證請求，並向模型提供不含原始輸入的參數字段錯誤，便於糾正工具調用。新增三項迴歸，全量 **129 passed in 17.23s**。原 20261002T104054-ea714139 運行達到輪數上限，明確記為失敗，不能作為路線成功證據。

最後真實網頁複測成功：run_id=20261002T104506-e0948cf3，狀態 verified；終點 Marina Bay Sands, Bayfront Subzone, Singapore，經停 Toast Box，停留 600 秒；模型選擇 walking，距離 2015.8 米，基礎總時長 2212.5 秒（服務預計，含停留）。本次直接完成，不代表所有澄清／重規劃已在線驗收。完整記錄：[MARINA_BAY_SANDS_WEB_EVIDENCE.json](MARINA_BAY_SANDS_WEB_EVIDENCE.json)；[網頁截圖](screenshots/marina-bay-sands-fixed.png)。
