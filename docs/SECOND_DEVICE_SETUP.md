# 在另一臺設備使用

代碼、依賴清單和項目目標保存在 GitHub。虛擬環境、`.env`、查詢緩存及 `outputs/` 中的歷史規劃記錄只保存在原設備，不隨 Git 同步。

## 獲取代碼

首次使用：

```bash
git clone https://github.com/matthewinds/RouteAgent.git
cd RouteAgent
```

已有項目時，在項目根目錄運行 `git pull --ff-only origin main`。若提示本地改動衝突，先保存自己的修改再更新。

這次 LTA 擴展加入了 GTFS 解析依賴。已有虛擬環境只需重新運行下面的依賴安裝命令，無需重新創建環境。

## 安裝和啟動

安裝 Python 3.11。在 macOS / Linux 的項目根目錄運行：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
cp -n .env.example .env
```

Windows PowerShell：

```powershell
py -3.11 -m venv .venv-fyp
.\.venv-fyp\Scripts\python.exe -m pip install -r requirements.lock.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

在本地編輯 `.env`，自行填寫憑據；也可以通過自己的安全渠道將原設備的 `.env` 複製到新設備。不要將密鑰提交到 GitHub。當前配置模板為根目錄 [.env.example](../.env.example)。

| 字段 | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 理解自然語言需求、決定工具調用及重新規劃；在線規劃必需 |
| `ORS_API_KEY` | 地圖地點定位；在線規劃必需 |
| `TAVILY_API_KEY` | 通用網頁地點檢索，補充地圖未收錄的名稱或描述 |
| `ONEMAP_TOKEN` | 公交、地鐵及步行接駁路線；需要使用有效令牌 |
| `LTA_API_KEY` | 實時道路車速、事故及停車信息 |

保留模板裡的服務地址；OSRM 駕車／步行、Overpass 和 Open-Meteo 的默認公開服務不需要另填 Key。配置非空不代表認證或服務調用一定成功，頁面會顯示實際查詢結果及證據缺口。

macOS / Linux 啟動：

```bash
.venv/bin/python -m streamlit run app/streamlit_app.py --server.address 127.0.0.1 --server.port 8502 --browser.gatherUsageStats false
```

Windows 啟動：

```powershell
.\scripts\start_web.ps1
```

打開 http://127.0.0.1:8502 。PyCharm 的運行配置及解釋器設置見 [PYCHARM_SETUP.md](PYCHARM_SETUP.md)。

## 檢查安裝

macOS / Linux：

```bash
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
```

Windows 將解釋器路徑換成 `.\.venv-fyp\Scripts\python.exe`。測試檢查代碼迴歸，不證明第三方服務的真實覆蓋或實時可用性。
