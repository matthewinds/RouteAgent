# API 申請和本地配置

2026-10-05 擴展：LTA 官方 33 類動態數據通過統一接口接入，公交到站、地鐵運營通告和其他數據按需求查詢；沿用 `LTA_API_KEY`。新增依賴需按 `requirements.lock.txt` 更新環境。用途、參數、文件讀取限制與真實證據見 [LTA_INTEGRATION.md](LTA_INTEGRATION.md)。

更新：2026-10-02。算路已改為 OSRM；ORS 僅用於地址定位。真實 OSRM 駕車／步行響應已驗證，一次真實 DeepSeek 網頁自動方式判斷／推薦／下載已通過，其餘場景仍需單獨驗收。

## 本地配置

在項目根目錄 `.env` 填寫密鑰。現有憑據已保留，OSRM 端點已補充；無需新增 OSRM Key。模板見 [.env.example](../.env.example)。

```dotenv
FYP_MODE=live
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_THINKING=enabled

# ORS 僅用於文字地址定位，不再調用 Directions
ORS_API_KEY=
ORS_GEOCODE_BASE_URL=https://api.heigit.org/pelias/v1

# OSRM 真實駕車／步行路網實例，無需 Key
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

Key 只在本地填寫，不發到聊天或截圖中。原 `ORS_BASE_URL` 可保留，但正常流程不讀取它；已有 Google／OpenRouter 憑據也保持不動。

## 服務與申請入口

| 服務 | 用途與配置 | 入口 |
| --- | --- | --- |
| DeepSeek | 必填 Key；理解需求和原生工具選擇 | [官方平臺](https://platform.deepseek.com/) |
| ORS / Pelias | 必填 Key；文字地址定位，不負責算路 | [HeiGIT 賬戶](https://account.heigit.org/) |
| OSRM | 駕車／步行；當前公開端點無需 Key | [API 文檔](https://project-osrm.org/docs/v5.24.0/api/)、[當前服務說明與限制](https://routing.openstreetmap.de/about.html) |
| LTA DataMall | 交通和停車功能需要 AccountKey | [DataMall](https://datamall.lta.gov.sg/content/datamall/en.html) |
| OSM Overpass | 道路、醫院、餐廳、咖啡店、營業標籤；無需 Key | [Overpass](https://overpass-api.de/) |
| Open-Meteo | 非商業公開小時網格預報；無需 Key | [接口](https://open-meteo.com/en/docs)、[使用條件](https://open-meteo.com/en/terms) |
| data.gov.sg / NEA | 可選區域預報補充；配額 Key 可選 | [開發者說明](https://guide.data.gov.sg/developer-guide) |

DeepSeek 的工具歷史保留 reasoning_content 供後續模型請求，頁面和導出不展示原始思考內容。[官方協議](https://api-docs.deepseek.com/guides/thinking_mode/)。

## OSRM 路網與限制

配置不同的 car／foot 服務地址。OSRM 的路網是在預處理時確定的，僅把同一駕車服務的請求 profile 改成 foot 並不會重建步行路網。應用拒絕將同一端點當作兩種實例；自建服務應使用不同端口或不同代理路徑。

接口返回完整 GeoJSON、距離（米）、服務基礎時間（秒）、替代路徑及分段指令。請求最多兩條替代加主路線，總計最多三條；服務不保證一定有替代方案。數據使用真實返回值，不補本地速度。完整實時駕車 ETA 仍需 LTA 有效覆蓋。

當前公共端點真實檢查：
- 駕車、步行成功，分別得到兩條路線。
- `exclude=motorway` 被拒絕，屬於當前實例不支持。強制避高速會明確失敗，不能換回普通路線後聲稱滿足。
- 默認路由由服務的 profile 權重決定；替代路線中距離較短不等於全局最短路線。

詳見 [OSRM_SETUP.md](OSRM_SETUP.md)。應用在進程內共享鎖、緩存和請求間隔，OSRM 每秒最多一次。公共服務適合輕量、非商業交互測試；20＋60 批量評測和多人運行應配置自建或獲授權服務，不能靠多個進程繞過限額。

## 檢查與啟動

重啟 PyCharm **FYP Web**，瀏覽器地址為 http://127.0.0.1:8502 。刷新配置只判斷填寫情況，不證明服務接通。

```powershell
# 只看配置狀態
.\.venv-fyp\Scripts\python.exe scripts/check_live.py

# 只測真實 OSRM，不調用模型、ORS 或 LTA
.\.venv-fyp\Scripts\python.exe scripts/check_live.py --routing

# 只測公開 OSM 與 Open-Meteo
.\.venv-fyp\Scripts\python.exe scripts/check_live.py --public

# 所有配置服務的小範圍檢查；會消費配額
.\.venv-fyp\Scripts\python.exe scripts/check_live.py --smoke

# 真實 DeepSeek 步行規劃；可能產生模型費用
.\.venv-fyp\Scripts\python.exe scripts/check_live.py --plan
```

`--routing` 報告寫到 outputs/osrm-check/summary.json，其中含真實路線和證據；不支持避高速時返回非零退出碼，不能把報告當作全部功能通過。保存的首次結果見 [OSRM_API_EVIDENCE.json](OSRM_API_EVIDENCE.json)。

只有 DeepSeek＋ORS 地址定位 Key，也可使用公開 OSRM 測試步行。沒有 LTA 時，駕車時限和實時交通條件不能核實，停車位查詢不可用。普通無時限駕車可顯示明確標註的 OSRM 基礎估計。錯誤不會切回 ORS 算路、規則或演示數據。

## OSM 和天氣

道路／POI／營業標籤由 Overpass 查詢，缺失標籤保持未知。道路標籤不是實時封路證明。Open-Meteo 按最多八個真實座標查詢小時降水概率和降水量；有限網格採樣不代表逐路段觀測，不能保證全程不淋雨。NEA 是顯式可選區域補充。

既有公開查詢證據見 [OSM_WEATHER_API_EVIDENCE.json](OSM_WEATHER_API_EVIDENCE.json)；完整流程驗收見 [VALIDATION.md](VALIDATION.md)。

## 分頁與純文本輸入更新

網頁沒有示例／方式／初始時間選擇，僅提交原始文本。方式未指定時模型調用 compare_modes／choose_mode；用戶明確的方式不能被覆蓋。缺省出發時間按當前新加坡時間，用戶寫出的未來時間由模型解析。

LTA 每頁 500 條，使用 $skip 順序讀取直到末頁；不再固定為 40 頁。仍保持 96 HTTP／240 秒規劃預算，為後續決策保留額度。提前停止時只使用實際成功頁，記錄 complete=false 和原因，不把未覆蓋路段補成實時數據。

只檢查 LTA 分頁和 OSRM 交通匹配（不調用模型或地址定位）：

```powershell
.\.venv-fyp\Scripts\python.exe scripts/check_live.py --traffic
```

結果保存到 outputs/traffic-check/summary.json。成功返回不等於全島數據已讀完；檢查 collection.complete、pages、record_count 及路線覆蓋率。真實本次結果見 [LTA_PAGINATION_EVIDENCE.json](LTA_PAGINATION_EVIDENCE.json)。
