# PyCharm 启动与环境配置

配置日期：2026-10-01。项目：`F:\develop\projects\MapAgents`。

## 日常启动

1. 在 PyCharm 打开项目根目录。
2. 右上角运行菜单选择 **FYP Web**，点击绿色运行按钮。
3. 浏览器打开 <http://127.0.0.1:8502>。
4. 页面中保留“演示快照 / 离线规则”即可离线体验输入、重规划、地图、约束检查和记录导出。
5. 停止服务时，使用 PyCharm 的红色停止按钮。已经运行时不要重复启动相同端口。

页面必须通过 Streamlit 模块启动。直接运行 `app/streamlit_app.py` 不会启动正常的交互网页。

## 已配置的环境

- 项目解释器：`F:\develop\projects\MapAgents\.venv-fyp\Scripts\python.exe`，Python **3.11.5**。
- FYP 依赖已安装，包括 Streamlit、Folium、streamlit-folium、OSMnx、NetworkX、Pydantic 和 pytest，以及可编辑安装的 `fyp-route-agent`。
- 运行配置使用项目默认解释器，工作目录统一为项目根目录。
- `PYTHONUNBUFFERED=1`、`PYTHONUTF8=1`，避免日志缓冲及中文编码问题。
- 默认 `FYP_PARSER=rules`；网页中可以显式选择模型解析。密钥继续由后端读取根目录 `.env`，运行配置不存放密钥。
- `src` 已识别为源码目录；两个虚拟环境已排除索引。
- 原 `.venv` 保留供旧基线使用。

## 运行菜单

| 名称 | 用途 |
| --- | --- |
| FYP Web | Streamlit 网页，端口 8502 |
| FYP Tests | 执行 `pytest -q`，50 项离线测试 |
| FYP Evaluate Dev | 20 条开发请求，规则解析，七个对照/消融策略 |
| FYP Evaluate Test | 60 条固定测试请求，规则解析，七个对照/消融策略 |
| FYP Replay Demo | 合成快照 `demo-replan` 中的咖啡店经停演示 |
| FYP Check OSM | 检查真实 OSM 路网及捕获/重放一致性 |
| FYP Prepare OSM | 下载并准备默认新加坡中心区域路网，需联网 |

配置保存在项目 `.run/*.run.xml` 中，可以随项目保存。评测输出写入 `outputs/evaluation-*`，规划记录写入后端配置的输出目录。

## 验收记录

- `python -m pip check`：无依赖冲突。
- `python -m pytest -q`：**50 passed**。
- 已在 PyCharm 核对 FYP Web 的模块 `streamlit`、项目解释器和工作目录，并点击运行。
- PyCharm 控制台显示 `.venv-fyp\Scripts\python.exe -m streamlit run ... --server.port 8502`，服务监听 `127.0.0.1:8502`。
- 浏览器已打开该服务并提交默认咖啡店示例；页面返回满足硬约束的推荐、15 分钟合成演示行程、交互地图和逐项检查。
- 启动证据：`docs/validation/pycharm-web-started.jpg`。
- 页面证据：`docs/validation/pycharm-web-page.png`。

真实 LTA 交通仍需要配置有效凭据；合成演示及真实 OSM 地图检查不等于实时交通验收。

## 配置备份

改动前的 PyCharm 配置保存在：

`F:\develop\archives\MapAgents\pycharm-config-20261001\.idea-before`

恢复前先关闭项目，将所需配置复制回项目 `.idea` 后重新打开。

## 常见问题

- **缺少 folium / streamlit**：检查右下角解释器是否为 `.venv-fyp [3.11.5]`，不要切回旧 `.venv`。
- **页面打不开**：确认 FYP Web 正在运行，使用 8502 地址。原脚本的默认端口为 8501，两个入口端口不同。
- **端口占用**：先检查是否已经有本项目的 FYP Web 在运行；也可在 Edit Configurations 中修改 `--server.port` 并使用对应地址。
- **新机器或环境丢失**：安装 Python 3.11，在根目录建立 `.venv-fyp`，用该环境运行 `python -m pip install -e ".[test]"`，然后在 PyCharm 重新选择此解释器。
