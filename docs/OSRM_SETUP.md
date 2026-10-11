# OSRM 算路配置與驗收

更新：2026-10-02。正常路線工具已由 ORS Directions 改為 OSRM Route；原 MapAgent 基線與歷史 fixtures 不變。ORS Pelias 僅負責文字地址定位。

## 已配置的真實服務

```dotenv
OSRM_DRIVING_BASE_URL=https://routing.openstreetmap.de/routed-car
OSRM_WALKING_BASE_URL=https://routing.openstreetmap.de/routed-foot
OSRM_DRIVING_PROFILE=driving
OSRM_WALKING_PROFILE=foot
```

這些公共服務無需 Key。兩個地址分別指向駕車、步行圖；profile 名稱不代替服務端預處理。模型仍選擇工具順序，後端接收真實指標、檢查約束並控制發佈。

協議：GET `/route/v1/{profile}/{lon,lat;lon,lat}`，`geometries=geojson`、`overview=full`、`steps=true`、`radiuses=150;150`。每段最多三條路徑；OSRM alternatives 數量為 count−1，應用再限制返回總數。每個指令的幾何範圍映射到完整路徑索引，供 LTA 道路名／幾何匹配使用。

路線來源為 OSRM，發佈時每段必須引用 OSRM Route 證據；舊 ORS Directions 證據不能替代。沒有算路 Key、沒有 ORS Directions 請求，也沒有網絡失敗後的本地速度或其他引擎回退。緩存按實際端點／參數隔離；不同引擎的路線 ID 不混用。

## 真實檢查結果

2026-10-02 17:26:03（新加坡時間）提交固定公共座標 `[103.85,1.29]` → `[103.86,1.30]`。兩個座標僅用於接口檢查，並非經過定位的具體建築入口。

| 查詢 | 實際結果 | 是否緩存 |
| --- | --- | --- |
| 駕車 | 兩條：2736.9 米／269.1 秒；3174.3 米／299.4 秒 | 否 |
| 步行 | 兩條：1799.6 米／1441.6 秒；1894.5 米／1519.9 秒 | 否 |
| 駕車避高速 | 當前實例不支持 `exclude=motorway`，明確返回 unsupported_feature | 否 |

記錄包含幾何、分段、來源、獲取時間和響應哈希：[OSRM_API_EVIDENCE.json](OSRM_API_EVIDENCE.json)。這些時間是基礎服務估計，不是實時交通時間；替代路徑不是全局最短保證。真實 DeepSeek 決策、完整交通融合和網頁提交仍待單獨驗收。

## 避高速與自建實例

OSRM 支持哪些 exclude class 取決於服務器使用的 profile 與預處理數據。應用對硬性避高速傳 `exclude=motorway`；服務拒絕時不會刪除該參數重試普通路線。若成功響應的指令仍帶 motorway 類別，也會拒絕發佈。

要恢復此功能，需要部署並驗收支持 motorway 排除的 car 實例。可用官方 car.lua（帶 motorway excludable class）和 foot.lua 分別處理新加坡 OSM 數據，再運行兩個 OSRM 服務。以實際部署端口為準，配置示例：

```dotenv
OSRM_DRIVING_BASE_URL=http://127.0.0.1:5000
OSRM_WALKING_BASE_URL=http://127.0.0.1:5001
OSRM_DRIVING_PROFILE=driving
OSRM_WALKING_PROFILE=foot
```

本輪沒有啟動或構建 Docker 路網：檢測到 Docker CLI，但 Docker Linux 引擎未運行。當前使用真實公共服務，不把“可自建”寫成“已部署”。完整最短距離策略還需要相應預處理 profile，不能靠給默認請求添加虛構 preference 參數實現。

## 使用限制與驗證

當前公共服務要求每秒最多一次請求，只允許適度、非商業使用。代碼在同一進程的所有 worker 之間共享請求鎖，響應或失敗後至少間隔 1.05 秒；重試也受限，緩存命中不發請求。多個 PyCharm／網頁進程不會共享鎖，應只運行一個公共服務實例；批量評測、多人服務改用自建或獲授權端點。

```powershell
.\.venv-fyp\Scripts\python.exe scripts/check_live.py --routing
.\.venv-fyp\Scripts\python.exe -m pytest -q
```

當前 121 項自動化通過，包括 13 項新的 OSRM 協議／錯誤／幾何／模式／共享限速檢查和 2 項工具錯誤分類檢查。自動化使用模擬返回，與上表真實證據分開。

官方依據：[OSRM API](https://project-osrm.org/docs/v5.24.0/api/)、[服務使用說明](https://routing.openstreetmap.de/about.html)、[car profile](https://github.com/Project-OSRM/osrm-backend/blob/master/profiles/car.lua)、[部署說明](https://github.com/Project-OSRM/osrm-backend)。

## 後續真實網頁個案

2026-10-02 已通過一個未指定交通方式的真實 DeepSeek 網頁流程：比較 OSRM car／foot，選擇步行，顯示約 6.1 分鐘基礎總時長和地圖，並下載一致的規劃記錄。首屏改為空白自然語言輸入。交通數據部分匹配不能補造完整實時總時間；驗收與證據見 [FREE_TEXT_AND_TRAFFIC_FIX.md](FREE_TEXT_AND_TRAFFIC_FIX.md)。
