# RouteAgent

新加坡自然语言出行规划，使用 DeepSeek 调用地图、公共交通、路况和天气服务。

## 安装和运行

在自己的 Python 3.11 虚拟环境中，进入项目根目录安装：

```bash
python -m pip install -r requirements.lock.txt
```

复制 `.env.example` 为 `.env`，填写自己的凭据：

- 必需：`DEEPSEEK_API_KEY`、`ORS_API_KEY`。
- 公交／地铁：`ONEMAP_TOKEN`。
- 实时路况／停车：`LTA_API_KEY`。
- 网页地点搜索：`TAVILY_API_KEY`。

启动：

```bash
python -m streamlit run app/streamlit_app.py --server.port 8502
```

打开 http://127.0.0.1:8502 。实际规划需要联网及有效凭据。
