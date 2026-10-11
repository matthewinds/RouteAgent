# PyCharm 啟動與環境配置

配置日期：2026-10-01。項目：`F:\develop\projects\MapAgents`。

## 日常啟動

1. 在 PyCharm 打開項目根目錄。
2. 右上角運行菜單選擇 **FYP Web**，點擊綠色運行按鈕。
3. 瀏覽器打開 <http://127.0.0.1:8502>。
4. 頁面中保留“演示快照 / 離線規則”即可離線體驗輸入、重規劃、地圖、約束檢查和記錄導出。
5. 停止服務時，使用 PyCharm 的紅色停止按鈕。已經運行時不要重複啟動相同端口。

頁面必須通過 Streamlit 模塊啟動。直接運行 `app/streamlit_app.py` 不會啟動正常的交互網頁。

## 已配置的環境

- 項目解釋器：`F:\develop\projects\MapAgents\.venv-fyp\Scripts\python.exe`，Python **3.11.5**。
- FYP 依賴已安裝，包括 Streamlit、Folium、streamlit-folium、OSMnx、NetworkX、Pydantic 和 pytest，以及可編輯安裝的 `fyp-route-agent`。
- 運行配置使用項目默認解釋器，工作目錄統一為項目根目錄。
- `PYTHONUNBUFFERED=1`、`PYTHONUTF8=1`，避免日誌緩衝及中文編碼問題。
- 默認 `FYP_PARSER=rules`；網頁中可以顯式選擇模型解析。密鑰繼續由後端讀取根目錄 `.env`，運行配置不存放密鑰。
- `src` 已識別為源碼目錄；兩個虛擬環境已排除索引。
- 原 `.venv` 保留供舊基線使用。

## 運行菜單

| 名稱 | 用途 |
| --- | --- |
| FYP Web | Streamlit 網頁，端口 8502 |
| FYP Tests | 執行 `pytest -q`，50 項離線測試 |
| FYP Evaluate Dev | 20 條開發請求，規則解析，七個對照/消融策略 |
| FYP Evaluate Test | 60 條固定測試請求，規則解析，七個對照/消融策略 |
| FYP Replay Demo | 合成快照 `demo-replan` 中的咖啡店經停演示 |
| FYP Check OSM | 檢查真實 OSM 路網及捕獲/重放一致性 |
| FYP Prepare OSM | 下載並準備默認新加坡中心區域路網，需聯網 |

配置保存在項目 `.run/*.run.xml` 中，可以隨項目保存。評測輸出寫入 `outputs/evaluation-*`，規劃記錄寫入後端配置的輸出目錄。

## 驗收記錄

- `python -m pip check`：無依賴衝突。
- `python -m pytest -q`：**50 passed**。
- 已在 PyCharm 核對 FYP Web 的模塊 `streamlit`、項目解釋器和工作目錄，並點擊運行。
- PyCharm 控制檯顯示 `.venv-fyp\Scripts\python.exe -m streamlit run ... --server.port 8502`，服務監聽 `127.0.0.1:8502`。
- 瀏覽器已打開該服務並提交默認咖啡店示例；頁面返回滿足硬約束的推薦、15 分鐘合成演示行程、交互地圖和逐項檢查。
- 啟動證據：`docs/validation/pycharm-web-started.jpg`。
- 頁面證據：`docs/validation/pycharm-web-page.png`。

真實 LTA 交通仍需要配置有效憑據；合成演示及真實 OSM 地圖檢查不等於實時交通驗收。

## 配置備份

改動前的 PyCharm 配置保存在：

`F:\develop\archives\MapAgents\pycharm-config-20261001\.idea-before`

恢復前先關閉項目，將所需配置複製回項目 `.idea` 後重新打開。

## 常見問題

- **缺少 folium / streamlit**：檢查右下角解釋器是否為 `.venv-fyp [3.11.5]`，不要切回舊 `.venv`。
- **頁面打不開**：確認 FYP Web 正在運行，使用 8502 地址。原腳本的默認端口為 8501，兩個入口端口不同。
- **端口占用**：先檢查是否已經有本項目的 FYP Web 在運行；也可在 Edit Configurations 中修改 `--server.port` 並使用對應地址。
- **新機器或環境丟失**：安裝 Python 3.11，在根目錄建立 `.venv-fyp`，用該環境運行 `python -m pip install -e ".[test]"`，然後在 PyCharm 重新選擇此解釋器。
