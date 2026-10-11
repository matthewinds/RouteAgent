# RouteAgent：DeepSeek 真實在線出行規劃

網頁正常流程：DeepSeek 理解需求 → 自主調用真實地圖、POI、路況、天氣或停車工具 → 驗證和有限重規劃 → 比較並選擇有證據的方案。支持新加坡駕車、步行、公交＋地鐵＋步行組合、駕車後一次停車步行接駁，以及最多一個業務經停點。

**當前交付是在線接入代碼。Key 的填寫狀態由頁面檢查；OSM、Open-Meteo 與 OSRM 駕車／步行已完成小範圍真實響應檢查，一次真實網頁自動判斷方式、推薦和下載流程已通過；其他場景及效果仍待驗收。自動化模擬測試不是接通證明。缺少必需 Key 時網頁禁止開始規劃，不提供演示或規則回退。**

## 啟動

1. 在根目錄 `.env` 填寫 `DEEPSEEK_API_KEY`、`ORS_API_KEY`（ORS 僅用於地址定位）；OSRM 算路端點已配置，無需 Key。駕車交通與停車功能另需 `LTA_API_KEY`。不要覆蓋已有 Google/OpenRouter 憑據。
2. 在 PyCharm 選擇 **FYP Web**，運行後打開 http://127.0.0.1:8502 。
3. 直接輸入自己的出行需求，沒有示例或交通方式選擇框。未指定方式時，DeepSeek 比較完整的步行、公交／地鐵組合和已確認可用車輛的自駕行程；臨時選擇不會鎖定整程方式。公共交通需配置 `ONEMAP_TOKEN`；未指定出發時間按現在。點擊頁面“刷新配置狀態”。“已配置”只表示 Key 非空，調用是否成功需要真實運行驗證。

macOS 新環境安裝和啟動（在項目根目錄執行，PyCharm 解釋器選擇 `.venv/bin/python`，Python 3.11）：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
cp -n .env.example .env
.venv/bin/python -m streamlit run app/streamlit_app.py --server.address 127.0.0.1 --server.port 8502 --browser.gatherUsageStats false
```

安裝後可運行 `.venv/bin/python -m pip check` 和 `.venv/bin/python -m pytest -q` 檢查依賴與主項目測試。真實在線規劃還需在 `.env` 填寫上面列出的 Key。

Windows 也可在項目根目錄運行：

```powershell
.\scripts\start_web.ps1
```

Windows 新環境：`.venv-fyp\Scripts\python.exe`（Python 3.11）。安裝可使用：

```powershell
python -m venv .venv-fyp
.\.venv-fyp\Scripts\python.exe -m pip install -r requirements.lock.txt
```

## 保存的方案和說明

- [另一臺設備使用](docs/SECOND_DEVICE_SETUP.md)：拉取最新代碼、安裝依賴和配置本地密鑰。
- [OSRM 配置與限制](docs/OSRM_SETUP.md)：駕車／步行端點、避高速限制和真實證據。
- [API 申請與填寫](docs/API_SETUP.md)：需要哪些 Key、官方入口、檢查方法。
- [LTA 完整動態數據接入](docs/LTA_INTEGRATION.md)：33 類數據、公共交通實時核對、文件讀取與實際覆蓋範圍。
- [PyCharm 啟動](docs/PYCHARM_SETUP.md)：解釋器、運行菜單、網頁和測試。
- [模塊實施方案](docs/FYP_IMPLEMENTATION_PLAN.md)：U00–U04、證據規則、限額與剩餘驗收。
- [新版驗收記錄](docs/VALIDATION.md)：自動化與真實在線驗收分開記錄。
- [舊項目歸檔索引](docs/ARCHIVE_INDEX.md)：原項目整理與恢復。
- [後端接口](src/route_agent/service.py)：`plan_route(text, clarifications, settings=..., progress=...)`。

## 目錄

```text
app/                        真實在線 Streamlit 頁面
src/route_agent/             DeepSeek 工具循環與真實服務適配器
src/route_agent/fixtures/    歷史合成機制測試，正常入口不導入
tests/                      協議、發佈檢查和網頁自動化測試
docs/                       當前方案、配置與驗收；history 保存舊版記錄
data/cache/online-v2/        帶時間和來源的真實響應緩存（不包含認證頭）
outputs/                    v2 需求、分段路線、證據、事件和診斷
baselines/mapagent/          獨立原 MapAgent 基線
```

OSRM 提供基礎服務預計時間，不是實時交通 ETA。LTA 只更新方向和幾何匹配且有效的路段；覆蓋不完整時完整駕車 ETA 未知。Open-Meteo 是天氣模型網格預報，NEA 為可選區域補充；均不能保證全程無雨。當前停車位不能保證未來可用。未知硬約束只能列為待核實。

組合行程與業務經停、候車換乘時序及真實步行接駁的實現與驗證見 [組合出行修復](docs/MULTIMODAL_PLANNING_FIX.md)。公交與地鐵時刻來自 OneMap，所有接駁幾何與時間來自真實路線服務；遮蔽未知時，避雨比較使用步行及等待的潛在暴露參考。

地點描述在提取修正時不得丟失，單店早餐不會當成多個經停；網頁顯示每次規劃中 LTA 的查詢與適用狀態。實現和真實服務核對見 [地點提取與 LTA 展示修復](docs/ENDPOINT_EXTRACTION_LTA_FIX.md)。

歷史 20/60 請求和七種合成策略保留供機制研究，其結果不代表新版 DeepSeek 在線系統效果。舊版導出仍保留，不通過當前在線入口重放。

基礎預計總時長與交通修正總時長分開顯示；交通證據缺失不會遮住已經存在的 OSRM 基礎估計。LTA 讀取不再固定於 40 頁，預算內自適應分頁並保留有效部分、標記完整性；硬時限繼續只由有效證據驗證。真實證據見 [修復驗收](docs/FREE_TEXT_AND_TRAFFIC_FIX.md)。
