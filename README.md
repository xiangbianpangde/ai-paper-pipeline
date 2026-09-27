# AI Paper Pipeline · 每日 AI 论文管线

一套运行于 macOS + WorkBuddy 的个人自动化管线：**arXiv 六方向检索 → PDF 归档 → 云 OCR → LLM 全文翻译 → 三件套论文库 → 期刊式日报 HTML → 夸克网盘同步 → 微信推送**。

## 整体架构

```
┌─ 06:00 日报一条龙（WorkBuddy automation ─────────────────────────┐
│  arxiv_daily.py fetch/download     → 论文 PDF 按方向归档          │
│  + 翻译导读生成 + 新闻采集          → <日期>.html 期刊式日报        │
│  + 夸克网盘同步（日报 + 新译文 HTML）→ 手机碎片阅读                  │
│  + Server酱 → 个人微信通知                                         │
└──────────────────────────────────────────────────────────────────┘
┌─ 06:30 微信送达（WorkBuddy automation）──────────────────────────┐
│  提取日报 KPI + 核心论文 → 微信 Claw 通道推送摘要版                 │
└──────────────────────────────────────────────────────────────────┘
┌─ 论文翻译管线（batch_translate / translate_v2 / daemon）─────────┐
│  MinerU 云 OCR（PDF→MD）→ MiniMax-M3 分块中文翻译 → 组论文夹       │
│  三件套: <标题>.pdf + <标题>_全文翻译.md + <标题>_翻译导读.md       │
└──────────────────────────────────────────────────────────────────┘
└─ 论文库运维工具（index / dedup / normalize / audit / fix ...）────┘
```

## 目录结构

```
ai-paper-pipeline/
├── setup.py            # 初始化向导：交互问答 / --set 非交互写入 config.json
├── config.json         # 唯一个人环境配置（路径/方向/模型/夸克 fid），脚本一律从此读取
├── SETUP.md            # 新用户接入指南（AI 助手使用本仓库前的必问清单）
├── scripts/            # 全部管线脚本（纯 Python 标准库，无第三方依赖）
│   └── pipeline_config.py  # 统一配置加载器（DEFAULTS 兜底 + config.json 覆盖）
├── automations/
│   ├── templates/      # 定时任务 prompt 模板（{{占位符}} 源文件）
│   └── *.md            # 渲染产物（scripts/render_automations.py 生成，勿手改）
└── README.md
```

## 新用户接入（重要）

仓库默认配置指向作者的环境；**新用户使用前必须先完成配置问答**，由 AI 助手
按 [`SETUP.md`](SETUP.md) 的问询清单收集信息后写入 `config.json`：

```bash
python3 setup.py                # 交互问答（回车沿用默认值）
python3 setup.py --check        # 校验
python3 scripts/render_automations.py   # 渲染个性化定时任务定义
```

脚本零硬编码个人路径：改环境只动 `config.json`，或用环境变量
`PIPELINE_CONFIG=<path>` 指向其他配置文件。

## 论文库组织

论文按六大研究方向中文子目录归档，每篇论文一个文件夹（三件套 + 解析产物）：

```
<论文库根>/
├── 01-智能体/            # Agent 框架、多智能体、工具调用
├── 02-上下文工程/        # Context engineering、ICL、RAG、记忆
├── 03-提示词工程/        # Prompt engineering、CoT、指令微调
├── 04-Harness执行框架/   # Agent harness、运行时、沙箱
├── 05-循环工程/          # 反思循环、自改进、长程 RL
└── 06-AI医疗/            # 医疗大模型、临床 QA、医学 Agent
```

> 脚本内的路径、方向名、模型、夸克 fid 等个人环境项**全部收敛在 `config.json`**，
> 复用时跑一遍 `setup.py` 即可，无需改脚本。

## 核心脚本

### 每日日报链路

| 脚本 | 作用 |
|------|------|
| `arxiv_daily.py` | arXiv 六方向检索（去重输出 JSON）+ PDF 下载（校验 `%PDF` 文件头、镜像重试、按方向归档） |
| `generate_guides.py` | 为论文生成 `<标题>_翻译导读.md` |
| `daily_push.py` | 全文翻译 MD → 自包含 HTML（图片 base64 内嵌），供夸克网盘手机阅读 |
| `quark_incremental_sync.py` | 夸克网盘增量同步 |

### 翻译管线（v1 → v2）

| 脚本 | 作用 |
|------|------|
| `batch_translate.py` | v1 批量翻译调度：调用本地 pdf2zh（MinerU 解析 + MiniMax-M3 翻译），`.done.txt` 断点续跑 |
| `translate_v2.py` | v2 云管线：消费 `00-待OCR转换` 池 → MinerU 云 OCR → MiniMax-M3 分块翻译（5500 字符/块，代码块/表格不切断）→ 组三件套论文夹；`/tmp/translate_v2.state` 断点状态 |
| `translate_daemon.py` / `translate_daemon.sh` | 后台守护批量翻译 |
| `mineru_client.py` | MinerU Cloud API 客户端 |
| `complete_paper_folder.py` / `complete_all.py` | 论文夹三件套完整性补全（复用导读、重命名、归位） |

### 论文库运维

| 脚本 | 作用 |
|------|------|
| `paper_index.py` | 论文库索引 + 重复检测 |
| `dedup_papers.py` | 孪生译文/PDF 去重 |
| `normalize_names.py` | 文件夹/文件名中文规范化（英文标题自动翻译） |
| `audit_integrity.py` | 三件套齐全性审计 |
| `check_image_refs.py` / `fix_image_refs.py` | 译文图片引用完整性校验与修复 |
| `fix_trans_gaps.py` | 翻译缺失段落（整块回退英文）检测补译 |
| `migrate_papers.py` / `preview_migration.py` | 论文库迁移（预览 + 执行） |
| `analyze_papers.py` / `inventory_papers.py` / `classify_99.py` | 盘点与待分类论文归类 |

## 翻译管线用法

```bash
# v2 云管线（推荐）：把待翻译 PDF 放入 <论文库根>/00-待OCR转换/ 后
python3 scripts/translate_v2.py            # 全量消费（跳过已处理）
python3 scripts/translate_v2.py --test <pdf路径>   # 单篇测试

# v1 本地 pdf2zh 批量翻译
python3 scripts/batch_translate.py
bash scripts/translate_daemon.sh           # 后台守护模式
```

- 翻译模型：MiniMax-M3（推理模型，`<think>` 标签已自动剥离），分块并发翻译，3 次指数退避重试
- 翻译质量护栏：整块回退英文检测（`SYS_TRANS_STRICT` 强硬 prompt 补译）、出版社样板文字与正文区分处理
- OCR：MinerU Cloud API（免本地 3.4GB 模型）；本地模式走 pdf2zh 内置 MinerU

## 定时任务（WorkBuddy automations）

两个 ACTIVE 任务的 **prompt 模板**在 `automations/templates/`（`{{占位符}}`），
运行 `python3 scripts/render_automations.py` 用 `config.json` 渲染出个性化的
`automations/*.md`（含完整 prompt 原文与调度元数据，可在 WorkBuddy 中一键复现）：

1. **每日日报一条龙（06:00）** — 检索 → 下载归档 → 导读 → 日报 HTML → 夸克同步 → Server酱微信通知
2. **每日日报微信送达（06:30）** — 提取当日日报 KPI 与核心论文，经微信 Claw 通道推送摘要版

## 凭据配置（不入库）

凭据统一放本地 `.secrets/` 目录或环境变量，仓库不含任何密钥：

| 用途 | 环境变量 | 本地文件 |
|------|----------|----------|
| MiniMax 翻译 | `MINIMAX_API_KEY` | `.secrets/minimax.json` |
| MinerU 云 OCR | — | `.secrets/mineru.json` |
| Server酱微信通知 | `SERVERCHAN_SEND_KEY` | `.secrets/serverchan_key` |

## 环境说明

- macOS + Python 3（**纯标准库，无第三方依赖**；翻译本体依赖外部 pdf2zh 工具链）
- macOS 无 `timeout` 命令，不要用 `timeout`/`gtimeout` 包裹长任务，直接后台运行
- arXiv API 偶发 HTTP 500（限流），脚本已内置退避重试

## 质量红线

- 论文摘要翻译基于 arXiv API 原文，**不编造**
- 新闻标注来源与日期，不确定的标「未验证」
- 报告内 KPI 数字与正文必须一致
- 破坏性操作（去重/迁移/删除）先预览、逐项确认、支持回收站级回滚
