# SETUP.md — 新用户接入指南（AI 助手必读）

> **给 AI 助手的强制流程**：用户要求使用/部署本仓库时，**先不要改任何脚本**。
> 所有个人环境差异已收敛到 `config.json` 一处。正确流程：
>
> 1. 向用户**逐项询问**下方问题清单（必问项一个不能少；可选项视用户需要）；
> 2. 用 `python3 setup.py --set 键=值` 把答案写入 `config.json`；
> 3. 运行 `python3 setup.py --check` 校验；
> 4. 若用户要用定时任务：`python3 scripts/render_automations.py` 渲染个性化 prompt；
> 5. 按需把凭据放进 secrets_dir（脚本从不入库凭据）。
>
> `config.json` 缺失或某键缺失时，脚本回退到**作者的默认环境**（即仓库默认值），
> 那不是你的环境——所以问询不能跳过。

---

## 一、必问清单（缺一不可）

### Q1. 用户称呼
- 问：「我该怎么称呼你？」
- 写入：`user.name`

### Q2. 研究方向
- 问：「你的研究方向是什么？（用于每日论文筛选、日报文案与导读的『与你的研究的关联』一节）」
- 写入：`research_interests`（示例：`Agent 与四大工程（context / prompt / harness / loop）、AI 医疗`）

### Q3. 研究关联锚点（导读个性化）
- 问：「生成翻译导读时，『与你研究关联』一节要提到哪些具体项目/方法名？」
- 写入：`research_anchors`（示例：`medbrain、RAG/Graph/Harness/Loop 四件套、devguard`；没有可置空字符串）

### Q4. 论文库根目录
- 问：「论文统一归档在哪个目录？（其下是各方向中文子目录，每篇论文一个文件夹）」
- 写入：`paths.paper_root`
- 校验：目录必须存在，否则 `--check` 报错。

### Q5. 方向子目录清单
- 问：「论文库分哪些方向子目录？给我目录名清单（如 01-智能体、02-上下文工程…）」
- 写入：`directions`（JSON 数组）
- 追问：若用户有仅用于全库扫描、不参与每日检索的扩展方向，写入 `extra_directions`。

### Q6. 日报工作目录
- 问：「日报 HTML 和检索结果 JSON 放在哪个工作目录？」
- 写入：`paths.workspace`

### Q7. 凭据目录
- 问：「API 凭据放在哪个目录？（需要 mineru.json / minimax.json / serverchan_key 三个文件，见下文凭据节）」
- 写入：`paths.secrets_dir`
- 若用户暂无凭据：照写路径并创建目录，功能缺失项由 `--check` 以 ⚠ 提示。

## 二、可选项（用户用到才问）

| 问题 | 写入键 | 不配置的后果 |
|------|--------|--------------|
| 有没有 v1 本地批量翻译管线的独立论文根目录？ | `paths.paper_root_legacy` | 回退用 Q4 同路径 |
| 本机装没装 pdf2zh 工具链？目录在哪？ | `paths.pdf2zh` | v1 批量翻译与本地翻译不可用（v2 云管线不受影响） |
| 装有 pypdf 的 python 解释器路径？（审计脚本提示用） | `paths.python_hint` | 仅提示文案不精确 |
| 用不用夸克网盘同步？CLI 路径？ | `paths.quark_cli`、`paths.quark_skill_dir` | 同步环节自动跳过 |
| 用夸克的话：日报 HTML 和各方向的目标目录 fid？ | `quark.fid_report`、`quark.fid_directions`（每方向一个） | 夸克同步环节跳过 |
| 翻译模型/API 地址/分块大小/并发数要不要改？ | `translate.model` / `base_url` / `max_chunk` / `workers` | 默认 MiniMax-M3 / api.minimaxi.com / 5500 / 4 |
| 微信通知用不用 Server酱？环境变量名/本地 key 文件名？ | `notify.serverchan_env`、`notify.serverchan_key_file` | 微信通知环节静默跳过 |
| 特殊目录命名要不要改？（待 OCR 池 / 99 待分类 / 扫描跳过名单） | `special_dirs.pending_ocr` / `classify_99` / `skip` | 默认即可 |

## 三、凭据文件（用户自行放置，AI 不得索取内容）

放置到 `paths.secrets_dir` 下，脚本只读文件、仓库永不入库：

| 文件 | 格式 | 用途 |
|------|------|------|
| `mineru.json` | `{"token": "..."}` （ak/sk 可选） | MinerU 云 OCR（https://mineru.net/apiManage/token ） |
| `minimax.json` | `{"key": "sk-..."}` | MiniMax-M3 翻译；也可用 pdf2zh/.env 的 `MINIMAX_API_KEY=` 或环境变量 |
| `serverchan_key` | 纯文本 SEND key | Server酱微信通知；也可用环境变量 `SERVERCHAN_SEND_KEY` |

## 四、写入与校验命令（AI 助手执行）

```bash
# 逐项写入（点路径，值按 JSON 解析，字符串可不带引号）
python3 setup.py --set user.name="张三"
python3 setup.py --set research_interests="多智能体、医疗 LLM"
python3 setup.py --set paths.paper_root="/Users/zhangsan/Documents/论文"
python3 setup.py --set directions='["01-智能体","02-医疗AI"]'
python3 setup.py --set quark.fid_directions='{"01-智能体":"fid123"}'

# 校验（✗ 阻断项必须解决；⚠ 可选项按需处理）
python3 setup.py --check

# 渲染个性化定时任务定义 → automations/*.md
python3 scripts/render_automations.py
```

## 五、自检要点（AI 助手交付前核对）

1. `python3 setup.py --check` 无 ✗；
2. `python3 -c "import sys;sys.path.insert(0,'scripts');import pipeline_config;print(pipeline_config.CFG['paths'])"` 输出与用户答案一致；
3. 渲染后的 `automations/*.md` 中**不应残留 `{{占位符}}`**；
4. 凭据文件确认存在（或明确告知用户哪些功能不可用）；
5. 严禁把用户凭据内容写进 config.json、日志或任何入库文件。

## 六、脚本与配置的关系（供 AI 理解，无需向用户转述）

- `scripts/pipeline_config.py`：唯一配置入口，DEFAULTS=作者环境，`config.json` 深合并覆盖，env `PIPELINE_CONFIG` 可指定其他配置文件；
- 各脚本头部 `from pipeline_config import CFG` 取路径/方向/模型/FID——**没有任何脚本再硬编码个人路径**；
- `automations/templates/*.md.tpl` 是定时任务 prompt 的模板源，`automations/*.md` 是渲染产物（勿手改）。
