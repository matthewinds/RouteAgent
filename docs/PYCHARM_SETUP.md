# PyCharm 啟動與環境配置

更新：2026-10-12。請在本機打開 RouteAgent 項目根目錄。

## 日常啟動

1. 在 PyCharm 打開項目根目錄。macOS 解釋器使用項目內的 `.venv/bin/python`；Windows 使用 `.venv-fyp\Scripts\python.exe`（Python 3.11）。
2. 按 [API_SETUP.md](API_SETUP.md) 填寫根目錄 `.env`。OSRM 駕車／步行端點已補充，無需算路 Key；ORS Key 僅用於地址定位。
3. 右上角選擇 **FYP Web**，點擊綠色運行按鈕。
4. 瀏覽器打開 http://127.0.0.1:8502 。配置為空時頁面明確顯示缺少 Key，開始按鈕禁用。
5. 填入 Key 後重啟服務或點擊“刷新配置狀態”，再輸入真實需求。服務認證失敗會顯示錯誤；不會生成離線結果。
6. 停止服務使用紅色停止按鈕。同一端口已運行時先停止舊實例。

運行方式必須是模塊 `streamlit`，參數：

```text
run app/streamlit_app.py --server.address 127.0.0.1 --server.port 8502 --browser.gatherUsageStats false
```

工作目錄為項目根目錄。運行配置使用項目解釋器；環境變量保留 `PYTHONUNBUFFERED=1`、`PYTHONUTF8=1`；正常網頁設 `FYP_MODE=live`，已移除強制 `FYP_PARSER=rules`。認證密鑰只從後端 `.env` 讀取，不寫入運行菜單。

## 運行菜單

| 名稱 | 用途 |
| --- | --- |
| FYP Web | 正常 DeepSeek 在線網頁，8502 |
| FYP Tests | 當前 RouteAgent 協議、後端和頁面迴歸測試 |
| FYP Check APIs | 只檢查配置存在性，不自動消費真實 API；可手動加 --smoke 或 --plan |

共享配置保存在 `.run/*.run.xml`，使用各平臺的項目解釋器。macOS 使用 `.venv`；Windows 使用 `.venv-fyp`。依賴使用根目錄 `requirements.lock.txt`。

## 終端啟動

macOS 在項目根目錄執行：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
cp -n .env.example .env
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
.venv/bin/python -m streamlit run app/streamlit_app.py --server.address 127.0.0.1 --server.port 8502 --browser.gatherUsageStats false
```

已有環境只需安裝依賴，無需重新創建。`.env` 中留空的 Key 需要本人填寫；網頁可以啟動，但在線規劃按鈕會在必需 Key 缺失時禁用。

Windows：

```powershell
.\scripts\start_web.ps1
.\.venv-fyp\Scripts\python.exe -m pytest -q
```

自動化通過不能證明 Key 有效。一次真實自動方式判斷、推薦和下載流程已通過，複雜接駁、失敗恢復及完整評測仍待驗收。

## 自然語言輸入新版

啟動配置和依賴不變。頁面只輸入自己的需求，不需要選擇示例、方式或初始出發時間。方式未指定時 DeepSeek 比較 OSRM 步行／駕車結果後選擇；接駁從需求識別。未來出發時間直接寫入需求，未指定按現在。結果中的基礎總時長與交通修正時間分開，後者無法核實時有具體原因。

已通過一次真實網頁自動選擇步行、地圖推薦和下載測試；其餘複雜場景仍需驗收，見 [FREE_TEXT_AND_TRAFFIC_FIX.md](FREE_TEXT_AND_TRAFFIC_FIX.md)。
