# MapAgent 本地运行与复现记录

## 原始数据已补充下载（2026-09-17）

已从数据集作者官方仓库独立下载到 `datasets_original/`，未修改 `datasets_dir/`：
MapEval-API 300 条、MapEval-Textual 300 条、MapEval-Visual 最终发布版 400 条及完整图片、
MapQA 3154 条（California 2206 + Illinois 948）。来源、提交版本、文件哈希和验证结果见
`datasets_original/README.md` 与 `datasets_original/manifest.json`。
视觉数据的 238 个题目引用图片全部可读取，Hugging Face 文件均通过上游哈希验证。
这补齐了原始数据来源，但尚未确认 MapAgent 作者如何选择样本与转换输入，不能直接把原始数据换入并声称实验协议一致。

## 正式论文版本核查（2026-09-17）

从 [ACL 正式页面](https://aclanthology.org/2026.findings-eacl.67/) 下载论文 PDF，
与用户最初提供的微信目录 PDF 做 SHA-256 比对，两者完全一致：
`BC40E887CF56DE6B2D0C1929216756E784F19BE943359B8810DF77A8D56C772A`。
因此前述论文/代码差异并非由阅读了另一版 PDF 导致。
页面提供 PDF 和 Checklist 链接；本次请求 Checklist 返回 HTTP 404，未能取得其内容。
该页面未列出可下载的独立数据或实验配置附件。

## 原仓库核查（2026-09-17）

已在线核查用户指定的 [Hasebul/MapAgent](https://github.com/Hasebul/MapAgent)，
本地 `origin` 正是该仓库。`git ls-remote --heads --tags` 返回唯一分支 `main`，
远端提交为 `a82eedc74cb2cb153e07739060857da3fc48214d`，与本地 HEAD 相同。
GitHub API 返回 0 个 Release、0 个 Issue（包含已关闭）、0 个 PR（包含已关闭）。
本次没有执行 pull/reset，也没有覆盖本地修复或密钥。

检查全部 8 个提交（2025-06-21 首次发布至 2026-01-08 最新提交）后发现：

- `datasets_dir/`、`src/model.py`、`src/run.py`、`src/demos/` 和 `utilities.py`
  从首次发布到最新提交没有变化。前述数据缺口、视觉函数缺失与 sequencer 问题存在于作者发布版本。
- 历史版本没有提供另一套完整实验数据或修复后的主入口。
- 已删除的 `src/error_analysis.ipynb` 仅使用手写的四个汇总数字绘制柱状图，
  没有逐题结果、推理配置或原始 API 响应，无法据此恢复实验。
- `parallel_function_implementation.py` 的历史差异主要是删除注释，另删除了
  `place_info` 中的 `serves_beer` 字段；不能把历史版本一概视为等价，需要作者确认实验提交。

证据：[提交历史](https://github.com/Hasebul/MapAgent/commits/main/)、
[当前发布提交](https://github.com/Hasebul/MapAgent/commit/a82eedc74cb2cb153e07739060857da3fc48214d)、
[首次发布提交](https://github.com/Hasebul/MapAgent/commit/65905ca)。

需要向作者核实的最小材料清单：四个基准的完整题目及正式划分、论文对应代码提交及各基准运行命令、
Sequencer 输出如何传入后续阶段、Textual 的 context 输入方式、视觉分支完整实现、
模型快照与推理参数，以及可公开的逐题预测/API 响应缓存。
本记录只是列出问题，没有向作者发送消息或发布 Issue。

## 当前验证结果

2026-09-17，在 Windows / 项目原有 Python 3.9.6 虚拟环境中，通过 OpenRouter 的
`openai/gpt-3.5-turbo` 和用户自己的 Google Maps key，真实运行了 Trip 第 1 题。
规划 → Google Maps → sequencer → solution_generator → answer_generator 全流程完成，
预测与原数据答案一致。第二次运行同时验证了完整 JSONL 过程记录。
这是运行验证，**不是论文整体准确率的复现结果**。

最终验证产物：

- `outputs/20260917T144023_846526Z/manifest.json`：模型、参数、版本、代码/数据哈希。
- `outputs/20260917T144023_846526Z/trip/mapagent_minitest.json`：1 条题目，1 条正确。
- `outputs/20260917T144023_846526Z/trip/mapagent_minitest_cache.jsonl`：可解析的完整过程。
- `outputs/20260917T144023_846526Z/console.log`：完整终端日志。

首次运行 `outputs/20260917T143900_269930Z` 虽然预测正确，但暴露了原代码的
SDK 对象序列化错误，过程缓存不完整；不能作为完整实验记录使用。请使用第二次结果。

## 使用方式

在项目根目录执行 PowerShell 命令；不必激活环境。

```powershell
# 检查本地依赖、数据和密钥是否填写，不联网
.\.venv\Scripts\python.exe -X utf8 scripts/run_mapagent.py --check

# 少量真实请求检查服务权限
.\.venv\Scripts\python.exe -X utf8 scripts/check_services.py

# 运行一条真实 Trip 样本
.\.venv\Scripts\python.exe -X utf8 scripts/run_mapagent.py --task trip --test-number 1

# 运行仓库已有 Trip 划分的全部 67 条（产生相应 API 费用）
.\.venv\Scripts\python.exe -X utf8 scripts/run_mapagent.py --task trip --test-number -1

# 离线回归测试（模拟 API，禁止网络连接）
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v

# 查看仓库原始数据划分
.\.venv\Scripts\python.exe -X utf8 scripts/audit_data.py
```

`--task` 支持 `trip`、`poi`、`nearby`、`routing`、`unanswerable`。
仅 Trip 单题经过真实端到端验证，其他分类尚未进行真实端到端运行。
每次启动创建独立时间戳目录，避免原入口按已完成数量续跑时混用不同实验的结果。
本次没有启动全量实验。

根目录 `.env` 中保存密钥，已加入 Git 忽略规则；环境变量优先于 `.env`：

```dotenv
OPENROUTER_API_KEY=你的密钥
GOOGLE_MAP_API_KEY=你的密钥
MAPAGENT_MODEL=openai/gpt-3.5-turbo
MAPAGENT_MAP_MODEL=openai/gpt-3.5-turbo
```

切换论文的其他骨干时，需要同时显式配置主模型和地图工具模型，不能仅改变一个。
脚本不自动替换失效模型。原 `get_40_response` 仍使用 GPT-4o，仅转换其服务端模型名称。

## 改动边界

基线提交：`a82eedc74cb2cb153e07739060857da3fc48214d`。

1. `runtime_config.py`：读取本地配置、创建 OpenRouter/Google Maps 客户端，转换模型服务名称。
2. `utilities.py` / `parallel_function_implementation.py`：空密钥、作者 Azure 地址改为配置客户端；
   文本阶段使用明确指定的模型，地图阶段模型单独记录。不改变请求消息、工具定义、并行线程数、采样参数和重试逻辑。
3. `src/model.py`：去掉两行密钥打印；为 `sequencer` 补 `(input, output)` 返回值。
   没有把 sequencer 的输出注入后续提示词，没有增加新的推理阶段。
4. `src/run.py`：启动时检查凭据；仅在写结果文件时将 SDK 对象转成 JSON。
   保留内存对象和提示词构造方式，避免因字符串表示改变模型输入。
5. 新增启动器、服务检查、只读数据审计、离线测试及本说明。

所有 `src/demos/` 提示词、数据文件、答案抽取、评分逻辑和地图工具函数计算保持原样。
入口默认 temperature=0；文本请求 top_p=0.95；地图工具代理原本没有显式 temperature，
本次也没有添加。seed=0 仅设置原程序 Python 随机种子，不保证服务端模型确定性。
原 `*_cache.json` 是追加对象加逗号的历史格式，不能当成单个标准 JSON 读取；
使用验证过的 `*_cache.jsonl` 作为逐题记录。

开始任务之前，`requirements.txt` 已有两项修改：
`langchain-core 0.3.6 → 0.2.43`、`langchain-openai 0.2.0 → 0.1.25`，本次保留，未覆盖。
没有升级推理依赖。曾同版本重装 `ninja==1.11.1.1`；`pip check` 仍报告
`ninja 1.11.1.1 is not supported on this platform`。此项尚未解决，不能宣称全环境检查通过；
当前已验证的 API 入口未使用 ninja，真实流程运行成功。未为消除提示擅自升级版本。

## 严格复现尚存的差异

### MapEval-Textual 的独立适配实验（非论文 Table 3 严格复现）

`scripts/prepare_mapeval_textual_mapagent.py` 将原始 300 条 `dataset.json` 转成已发布
`src/run.py` 可读取的 `problems.json` 和 `pid_splits.json`。生成文件放在
`datasets_adapted/mapeval_textual/`，原始数据和 MapAgent 推理代码不变。所有题目的
`context` 复制到 `hint`；原始 `Option0: Unanswerable` 映射成选项 A，原始
Option1–4 映射成 B–E。每个选项带有原始编号，以免原数据中重复的选项文本被
`src/run.py` 的字符串评分误判。空白占位选项按照原始 Evaluator 的行为跳过。

旧的 `src/run.py --data_root ../datasets_adapted/mapeval_textual` 命令仍会在每题调用
实时 Google Maps。实测第 347 题使用了与固定 context 不同的地图结果；
`textual_mapagent_run/` 中的旧结果只能作为这个混合流程的记录，不适合直接对照 Table 3。

新的 `scripts/run_mapeval_textual_mapagent_context.py` 只在本次进程内替换
`google_maps` 模块的数据来源：固定 context 已在 `hint` 中，模块不再查询实时地图，
并避免把长 context 重复放入后续提示词。其余已发布的规划、排序、生成与提取流程
仍由 `src/run.py` 执行。如果规划器选到网页检索、知识检索或视觉模块，新入口会
保留原始模块选择记录，再把该分支改道到固定 context，防止混入其他实时数据。
新入口已经完成 300 条离线输入检查，尚未由本工具发起付费评测。
若自行运行这个新实验，请从项目目录执行：

```powershell
python scripts\run_mapeval_textual_mapagent_context.py
```

这仍是 **Textual 适配实验**。作者没有公开 Textual 对应的 MapAgent 数据接入、
模块选择与评分配置；我们对地图模块的数据来源进行了显式适配，模型服务也不是
论文中可确认的同一快照。因此其准确率不能直接声称复现论文 Table 3 的 72.94%。

论文：[ACL Anthology, 2026.findings-eacl.67](https://aclanthology.org/2026.findings-eacl.67/)。
论文 §4.2 的文本骨干为 GPT-3.5-Turbo/Qwen-2.5-72B，视觉骨干为 GPT-4o/Qwen-2.5-VL-72B。
README 中 GPT-4 示例不适合直接当成论文文本实验设置。

| 数据 | 论文 Table 4 数量 | 本地情况 |
|---|---:|---|
| MapEval-API | 300 | problems 共 300，minitest 共 299；POI 的 ID 21 未列入划分；没有 test 划分 |
| MapEval-Textual | 300 | 本地有 context 字段，但现入口使用 hint；没有明确独立的 Textual 实验配置 |
| MapEval-Visual | 400 | problems 共 400；minitest 共 394，test 共 140 |
| MapQA | 3154 | task_1…task_9 的 problems 共 2206；minitest 共 2083 |

原始工作数据未补齐或重划分；上面的独立适配副本把 context 填入 hint，
因此改变了模型输入与实验协议，只能作为适配实验。
完整复现需要确认作者使用的确切划分、缺失数据以及模型快照/服务设置。

视觉入口另有原始缺陷：`get_metadata()` 不返回视觉函数需要的 `img_path`；
`visual_place_recognizer()` 引用不存在的 `format_content()`、旧图片目录且缺少模块返回值。
其他 `coordinatoragent` / `solve.py` 文件还保留 `octotools` 导入、作者端点或旧密钥配置；
它们不是 README 的已验证主入口。本次没有执行这些分支，也没有猜测作者实验逻辑重写它们。
因此当前交付不能宣称四个基准全部可复现。

Google Maps 返回实时营业时间、路线、评分，原函数还使用当前出发时间，结果可能与论文时期不同。
OpenRouter 与作者 Azure 服务的模型快照和默认参数等价性未得到验证。
若规划器选择 Bing 分支，两把现有密钥不足以完成该分支；不擅自替换检索服务或修改规划提示词。

Google 权限实测通过：Geocoding、Places Text Search (Legacy)、Place Details (Legacy)、Directions (Legacy)。
当前仍使用原代码的 Legacy API，没有替换成会改变返回结构的新 API。
参考：[OpenRouter 接入文档](https://openrouter.ai/docs/quickstart)、
[Google Maps Legacy 说明](https://developers.google.com/maps/legacy)。
