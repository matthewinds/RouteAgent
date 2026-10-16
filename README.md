# RouteAgent

新加坡自然语言出行规划。使用 DeepSeek 调用地图、路况、公共交通和天气服务，比较候选行程并检查用户约束。

## 安装

使用 Python 3.11，在项目根目录执行。

macOS / Linux：

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

在 `.env` 中填写 `DEEPSEEK_API_KEY` 和 `ORS_API_KEY`。公交／地铁需要 `ONEMAP_TOKEN`，实时路况和停车需要 `LTA_API_KEY`，网页地点搜索可配置 `TAVILY_API_KEY`。其余服务地址可保留模板默认值。实际规划需要联网及有效凭据。

## 启动

macOS / Linux：

```bash
.venv/bin/python -m streamlit run app/streamlit_app.py --server.address 127.0.0.1 --server.port 8502 --browser.gatherUsageStats false
```

Windows：

```powershell
.\scripts\start_web.ps1
```

打开 http://127.0.0.1:8502 。PyCharm 也可选择项目解释器后运行 **FYP Web**。

## 测试

```bash
.venv/bin/python -m pytest -q
```

Windows 使用 `.\.venv-fyp\Scripts\python.exe -m pytest -q`。测试使用模拟响应，不代表真实服务可用性验证。
