# MapAgent 本地運行與復現記錄

## 原始數據已補充下載（2026-09-17）

已從數據集作者官方倉庫獨立下載到 `datasets_original/`，未修改 `datasets_dir/`：
MapEval-API 300 條、MapEval-Textual 300 條、MapEval-Visual 最終發佈版 400 條及完整圖片、
MapQA 3154 條（California 2206 + Illinois 948）。來源、提交版本、文件哈希和驗證結果見
`datasets_original/README.md` 與 `datasets_original/manifest.json`。
視覺數據的 238 個題目引用圖片全部可讀取，Hugging Face 文件均通過上游哈希驗證。
這補齊了原始數據來源，但尚未確認 MapAgent 作者如何選擇樣本與轉換輸入，不能直接把原始數據換入並聲稱實驗協議一致。

## 正式論文版本核查（2026-09-17）

從 [ACL 正式頁面](https://aclanthology.org/2026.findings-eacl.67/) 下載論文 PDF，
與用戶最初提供的微信目錄 PDF 做 SHA-256 比對，兩者完全一致：
`BC40E887CF56DE6B2D0C1929216756E784F19BE943359B8810DF77A8D56C772A`。
因此前述論文/代碼差異並非由閱讀了另一版 PDF 導致。
頁面提供 PDF 和 Checklist 鏈接；本次請求 Checklist 返回 HTTP 404，未能取得其內容。
該頁面未列出可下載的獨立數據或實驗配置附件。

## 原倉庫核查（2026-09-17）

已在線核查用戶指定的 [Hasebul/MapAgent](https://github.com/Hasebul/MapAgent)，
本地 `origin` 正是該倉庫。`git ls-remote --heads --tags` 返回唯一分支 `main`，
遠端提交為 `a82eedc74cb2cb153e07739060857da3fc48214d`，與本地 HEAD 相同。
GitHub API 返回 0 個 Release、0 個 Issue（包含已關閉）、0 個 PR（包含已關閉）。
本次沒有執行 pull/reset，也沒有覆蓋本地修復或密鑰。

檢查全部 8 個提交（2025-06-21 首次發佈至 2026-01-08 最新提交）後發現：

- `datasets_dir/`、`src/model.py`、`src/run.py`、`src/demos/` 和 `utilities.py`
  從首次發佈到最新提交沒有變化。前述數據缺口、視覺函數缺失與 sequencer 問題存在於作者發佈版本。
- 歷史版本沒有提供另一套完整實驗數據或修復後的主入口。
- 已刪除的 `src/error_analysis.ipynb` 僅使用手寫的四個彙總數字繪製柱狀圖，
  沒有逐題結果、推理配置或原始 API 響應，無法據此恢復實驗。
- `parallel_function_implementation.py` 的歷史差異主要是刪除註釋，另刪除了
  `place_info` 中的 `serves_beer` 字段；不能把歷史版本一概視為等價，需要作者確認實驗提交。

證據：[提交歷史](https://github.com/Hasebul/MapAgent/commits/main/)、
[當前發佈提交](https://github.com/Hasebul/MapAgent/commit/a82eedc74cb2cb153e07739060857da3fc48214d)、
[首次發佈提交](https://github.com/Hasebul/MapAgent/commit/65905ca)。

需要向作者核實的最小材料清單：四個基準的完整題目及正式劃分、論文對應代碼提交及各基準運行命令、
Sequencer 輸出如何傳入後續階段、Textual 的 context 輸入方式、視覺分支完整實現、
模型快照與推理參數，以及可公開的逐題預測/API 響應緩存。
本記錄只是列出問題，沒有向作者發送消息或發佈 Issue。

## 當前驗證結果

2026-09-17，在 Windows / 項目原有 Python 3.9.6 虛擬環境中，通過 OpenRouter 的
`openai/gpt-3.5-turbo` 和用戶自己的 Google Maps key，真實運行了 Trip 第 1 題。
規劃 → Google Maps → sequencer → solution_generator → answer_generator 全流程完成，
預測與原數據答案一致。第二次運行同時驗證了完整 JSONL 過程記錄。
這是運行驗證，**不是論文整體準確率的復現結果**。

最終驗證產物：

- `outputs/20260917T144023_846526Z/manifest.json`：模型、參數、版本、代碼/數據哈希。
- `outputs/20260917T144023_846526Z/trip/mapagent_minitest.json`：1 條題目，1 條正確。
- `outputs/20260917T144023_846526Z/trip/mapagent_minitest_cache.jsonl`：可解析的完整過程。
- `outputs/20260917T144023_846526Z/console.log`：完整終端日誌。

首次運行 `outputs/20260917T143900_269930Z` 雖然預測正確，但暴露了原代碼的
SDK 對象序列化錯誤，過程緩存不完整；不能作為完整實驗記錄使用。請使用第二次結果。

## 使用方式

在項目根目錄執行 PowerShell 命令；不必激活環境。

```powershell
# 檢查本地依賴、數據和密鑰是否填寫，不聯網
.\.venv\Scripts\python.exe -X utf8 scripts/run_mapagent.py --check

# 少量真實請求檢查服務權限
.\.venv\Scripts\python.exe -X utf8 scripts/check_services.py

# 運行一條真實 Trip 樣本
.\.venv\Scripts\python.exe -X utf8 scripts/run_mapagent.py --task trip --test-number 1

# 運行倉庫已有 Trip 劃分的全部 67 條（產生相應 API 費用）
.\.venv\Scripts\python.exe -X utf8 scripts/run_mapagent.py --task trip --test-number -1

# 離線迴歸測試（模擬 API，禁止網絡連接）
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v

# 查看倉庫原始數據劃分
.\.venv\Scripts\python.exe -X utf8 scripts/audit_data.py
```

`--task` 支持 `trip`、`poi`、`nearby`、`routing`、`unanswerable`。
僅 Trip 單題經過真實端到端驗證，其他分類尚未進行真實端到端運行。
每次啟動創建獨立時間戳目錄，避免原入口按已完成數量續跑時混用不同實驗的結果。
本次沒有啟動全量實驗。

根目錄 `.env` 中保存密鑰，已加入 Git 忽略規則；環境變量優先於 `.env`：

```dotenv
OPENROUTER_API_KEY=你的密鑰
GOOGLE_MAP_API_KEY=你的密鑰
MAPAGENT_MODEL=openai/gpt-3.5-turbo
MAPAGENT_MAP_MODEL=openai/gpt-3.5-turbo
```

切換論文的其他骨幹時，需要同時顯式配置主模型和地圖工具模型，不能僅改變一個。
腳本不自動替換失效模型。原 `get_40_response` 仍使用 GPT-4o，僅轉換其服務端模型名稱。

## 改動邊界

基線提交：`a82eedc74cb2cb153e07739060857da3fc48214d`。

1. `runtime_config.py`：讀取本地配置、創建 OpenRouter/Google Maps 客戶端，轉換模型服務名稱。
2. `utilities.py` / `parallel_function_implementation.py`：空密鑰、作者 Azure 地址改為配置客戶端；
   文本階段使用明確指定的模型，地圖階段模型單獨記錄。不改變請求消息、工具定義、並行線程數、採樣參數和重試邏輯。
3. `src/model.py`：去掉兩行密鑰打印；為 `sequencer` 補 `(input, output)` 返回值。
   沒有把 sequencer 的輸出注入後續提示詞，沒有增加新的推理階段。
4. `src/run.py`：啟動時檢查憑據；僅在寫結果文件時將 SDK 對象轉成 JSON。
   保留內存對象和提示詞構造方式，避免因字符串表示改變模型輸入。
5. 新增啟動器、服務檢查、只讀數據審計、離線測試及本說明。

所有 `src/demos/` 提示詞、數據文件、答案抽取、評分邏輯和地圖工具函數計算保持原樣。
入口默認 temperature=0；文本請求 top_p=0.95；地圖工具代理原本沒有顯式 temperature，
本次也沒有添加。seed=0 僅設置原程序 Python 隨機種子，不保證服務端模型確定性。
原 `*_cache.json` 是追加對象加逗號的歷史格式，不能當成單個標準 JSON 讀取；
使用驗證過的 `*_cache.jsonl` 作為逐題記錄。

開始任務之前，`requirements.txt` 已有兩項修改：
`langchain-core 0.3.6 → 0.2.43`、`langchain-openai 0.2.0 → 0.1.25`，本次保留，未覆蓋。
沒有升級推理依賴。曾同版本重裝 `ninja==1.11.1.1`；`pip check` 仍報告
`ninja 1.11.1.1 is not supported on this platform`。此項尚未解決，不能宣稱全環境檢查通過；
當前已驗證的 API 入口未使用 ninja，真實流程運行成功。未為消除提示擅自升級版本。

## 嚴格復現尚存的差異

### MapEval-Textual 的獨立適配實驗（非論文 Table 3 嚴格復現）

`scripts/prepare_mapeval_textual_mapagent.py` 將原始 300 條 `dataset.json` 轉成已發佈
`src/run.py` 可讀取的 `problems.json` 和 `pid_splits.json`。生成文件放在
`datasets_adapted/mapeval_textual/`，原始數據和 MapAgent 推理代碼不變。所有題目的
`context` 複製到 `hint`；原始 `Option0: Unanswerable` 映射成選項 A，原始
Option1–4 映射成 B–E。每個選項帶有原始編號，以免原數據中重複的選項文本被
`src/run.py` 的字符串評分誤判。空白佔位選項按照原始 Evaluator 的行為跳過。

舊的 `src/run.py --data_root ../datasets_adapted/mapeval_textual` 命令仍會在每題調用
實時 Google Maps。實測第 347 題使用了與固定 context 不同的地圖結果；
`textual_mapagent_run/` 中的舊結果只能作為這個混合流程的記錄，不適合直接對照 Table 3。

新的 `scripts/run_mapeval_textual_mapagent_context.py` 只在本次進程內替換
`google_maps` 模塊的數據來源：固定 context 已在 `hint` 中，模塊不再查詢實時地圖，
並避免把長 context 重複放入後續提示詞。其餘已發佈的規劃、排序、生成與提取流程
仍由 `src/run.py` 執行。如果規劃器選到網頁檢索、知識檢索或視覺模塊，新入口會
保留原始模塊選擇記錄，再把該分支改道到固定 context，防止混入其他實時數據。
新入口已經完成 300 條離線輸入檢查，尚未由本工具發起付費評測。
若自行運行這個新實驗，請從項目目錄執行：

```powershell
python scripts\run_mapeval_textual_mapagent_context.py
```

這仍是 **Textual 適配實驗**。作者沒有公開 Textual 對應的 MapAgent 數據接入、
模塊選擇與評分配置；我們對地圖模塊的數據來源進行了顯式適配，模型服務也不是
論文中可確認的同一快照。因此其準確率不能直接聲稱復現論文 Table 3 的 72.94%。

論文：[ACL Anthology, 2026.findings-eacl.67](https://aclanthology.org/2026.findings-eacl.67/)。
論文 §4.2 的文本骨幹為 GPT-3.5-Turbo/Qwen-2.5-72B，視覺骨幹為 GPT-4o/Qwen-2.5-VL-72B。
README 中 GPT-4 示例不適合直接當成論文文本實驗設置。

| 數據 | 論文 Table 4 數量 | 本地情況 |
|---|---:|---|
| MapEval-API | 300 | problems 共 300，minitest 共 299；POI 的 ID 21 未列入劃分；沒有 test 劃分 |
| MapEval-Textual | 300 | 本地有 context 字段，但現入口使用 hint；沒有明確獨立的 Textual 實驗配置 |
| MapEval-Visual | 400 | problems 共 400；minitest 共 394，test 共 140 |
| MapQA | 3154 | task_1…task_9 的 problems 共 2206；minitest 共 2083 |

原始工作數據未補齊或重劃分；上面的獨立適配副本把 context 填入 hint，
因此改變了模型輸入與實驗協議，只能作為適配實驗。
完整復現需要確認作者使用的確切劃分、缺失數據以及模型快照/服務設置。

視覺入口另有原始缺陷：`get_metadata()` 不返回視覺函數需要的 `img_path`；
`visual_place_recognizer()` 引用不存在的 `format_content()`、舊圖片目錄且缺少模塊返回值。
其他 `coordinatoragent` / `solve.py` 文件還保留 `octotools` 導入、作者端點或舊密鑰配置；
它們不是 README 的已驗證主入口。本次沒有執行這些分支，也沒有猜測作者實驗邏輯重寫它們。
因此當前交付不能宣稱四個基準全部可復現。

Google Maps 返回實時營業時間、路線、評分，原函數還使用當前出發時間，結果可能與論文時期不同。
OpenRouter 與作者 Azure 服務的模型快照和默認參數等價性未得到驗證。
若規劃器選擇 Bing 分支，兩把現有密鑰不足以完成該分支；不擅自替換檢索服務或修改規劃提示詞。

Google 權限實測通過：Geocoding、Places Text Search (Legacy)、Place Details (Legacy)、Directions (Legacy)。
當前仍使用原代碼的 Legacy API，沒有替換成會改變返回結構的新 API。
參考：[OpenRouter 接入文檔](https://openrouter.ai/docs/quickstart)、
[Google Maps Legacy 說明](https://developers.google.com/maps/legacy)。
