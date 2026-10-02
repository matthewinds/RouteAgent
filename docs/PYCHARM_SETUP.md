# PyCharm 启动与环境配置

更新：2026-10-02；项目 `F:\develop\projects\MapAgents`。

## 日常启动

1. 在 PyCharm 打开项目根目录，解释器使用 `F:\develop\projects\MapAgents\.venv-fyp\Scripts\python.exe`（Python 3.11.5）。
2. 按 [API_SETUP.md](API_SETUP.md) 填写根目录 `.env`。OSRM 驾车／步行端点已补充，无需算路 Key；ORS Key 仅用于地址定位。
3. 右上角选择 **FYP Web**，点击绿色运行按钮。
4. 浏览器打开 http://127.0.0.1:8502 。配置为空时页面明确显示缺少 Key，开始按钮禁用。
5. 填入 Key 后重启服务或点击“刷新配置状态”，再输入真实需求。服务认证失败会显示错误；不会生成离线结果。
6. 停止服务使用红色停止按钮。同一端口已运行时先停止旧实例。

运行方式必须是模块 `streamlit`，参数：

```text
run app/streamlit_app.py --server.address 127.0.0.1 --server.port 8502 --browser.gatherUsageStats false
```

工作目录为项目根目录。运行配置使用项目解释器；环境变量保留 `PYTHONUNBUFFERED=1`、`PYTHONUTF8=1`；正常网页设 `FYP_MODE=live`，已移除强制 `FYP_PARSER=rules`。认证密钥只从后端 `.env` 读取，不写入运行菜单。

## 运行菜单

| 名称 | 用途 |
| --- | --- |
| FYP Web | 正常 DeepSeek 在线网页，8502 |
| FYP Tests | 新版协议／页面模拟测试＋独立旧版回归 |
| FYP Check APIs | 只检查配置存在性，不自动消费真实 API；可手动加 --smoke 或 --plan |
| Historical Synthetic Evaluate Dev / Test | 旧 20／60 请求和合成机制对照，不能算新系统在线验收 |
| Historical Synthetic Fixture | 明确标注的旧合成重放，只用于研究测试 |
| Historical Prepare OSM | 旧本地 OSM 图准备；当前在线路线由 OSRM 提供，不依赖它 |

共享配置保存在 `.run/*.run.xml`。旧 `.venv` 留给 MapAgent 基线；新代码使用独立 `.venv-fyp`。依赖无冲突；目前无需增加软件包。

## 终端启动

```powershell
.\scripts\start_web.ps1
.\.venv-fyp\Scripts\python.exe -m pytest -q
```

自动化通过不能证明 Key 有效。一次真实自动方式判断、推荐和下载流程已通过，复杂接驳、失败恢复及完整评测仍待验收；旧版 PyCharm 启动和合成演示证据保存在 [history/PYCHARM_SETUP_V1.md](history/PYCHARM_SETUP_V1.md)。

## 自然语言输入新版

启动配置和依赖不变。页面只输入自己的需求，不需要选择示例、方式或初始出发时间。方式未指定时 DeepSeek 比较 OSRM 步行／驾车结果后选择；接驳从需求识别。未来出发时间直接写入需求，未指定按现在。结果中的基础总时长与交通修正时间分开，后者无法核实时有具体原因。

已通过一次真实网页自动选择步行、地图推荐和下载测试；其余复杂场景仍需验收，见 [FREE_TEXT_AND_TRAFFIC_FIX.md](FREE_TEXT_AND_TRAFFIC_FIX.md)。
