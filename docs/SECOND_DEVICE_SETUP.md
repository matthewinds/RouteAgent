# 在另一台设备使用

代码、依赖清单和项目目标保存在 GitHub。虚拟环境、`.env`、查询缓存及 `outputs/` 中的历史规划记录只保存在原设备，不随 Git 同步。

## 获取代码

首次使用：

```bash
git clone https://github.com/matthewinds/RouteAgent.git
cd RouteAgent
```

已有项目时，在项目根目录运行 `git pull --ff-only origin main`。若提示本地改动冲突，先保存自己的修改再更新。

## 安装和启动

安装 Python 3.11。在 macOS / Linux 的项目根目录运行：

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

在本地编辑 `.env`，自行填写凭据；也可以通过自己的安全渠道将原设备的 `.env` 复制到新设备。不要将密钥提交到 GitHub。当前配置模板为根目录 [.env.example](../.env.example)。

| 字段 | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 理解自然语言需求、决定工具调用及重新规划；在线规划必需 |
| `ORS_API_KEY` | 地图地点定位；在线规划必需 |
| `TAVILY_API_KEY` | 通用网页地点检索，补充地图未收录的名称或描述 |
| `ONEMAP_TOKEN` | 公交、地铁及步行接驳路线；需要使用有效令牌 |
| `LTA_API_KEY` | 实时道路车速、事故及停车信息 |

保留模板里的服务地址；OSRM 驾车／步行、Overpass 和 Open-Meteo 的默认公开服务不需要另填 Key。配置非空不代表认证或服务调用一定成功，页面会显示实际查询结果及证据缺口。

macOS / Linux 启动：

```bash
.venv/bin/python -m streamlit run app/streamlit_app.py --server.address 127.0.0.1 --server.port 8502 --browser.gatherUsageStats false
```

Windows 启动：

```powershell
.\scripts\start_web.ps1
```

打开 http://127.0.0.1:8502 。PyCharm 的运行配置及解释器设置见 [PYCHARM_SETUP.md](PYCHARM_SETUP.md)。

## 检查安装

macOS / Linux：

```bash
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
```

Windows 将解释器路径换成 `.\.venv-fyp\Scripts\python.exe`。测试检查代码回归，不证明第三方服务的真实覆盖或实时可用性。
