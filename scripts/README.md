# 每日 AI 日报 · 工具链说明

为 master 定制的每日 AI 日报生成工作流。

> **⚠ 配置说明（2026-09-27 起）**：所有个人路径/方向名/模型/夸克 fid 已收敛到仓库根
> `config.json`（由 `setup.py` 生成），脚本经 `pipeline_config.py` 读取，本目录脚本
> 不再硬编码个人路径。新用户接入见仓库根 `SETUP.md`。

目录结构：

```
每日早报/
├── scripts/
│   └── arxiv_daily.py      # arXiv 六方向检索 + PDF 下载工具链
├── out/                    # 检索结果 JSON（papers_<date>.json）
└── <YYYY-MM-DD>.html       # 每日日报（期刊式，可独立打开/打印）

论文库（统一归档，按研究方向中文子目录分类）：
/Users/xbpd/Documents/论文/
├── 01-智能体/               # Agent 框架、多智能体、工具调用
├── 02-上下文工程/           # Context engineering、ICL、RAG、记忆
├── 03-提示词工程/           # Prompt engineering、CoT、指令微调
├── 04-Harness执行框架/      # Agent harness、运行时、沙箱
├── 05-循环工程/             # 反思循环、自改进、长程 RL
└── 06-AI医疗/               # 医疗大模型、临床 QA、医学 Agent
```

## 工具链用法

```bash
# 1) 检索六方向（agent/context/prompt/harness/loop/medical），去重输出 JSON
python3 scripts/arxiv_daily.py fetch

# 2) 批量下载 PDF（自动校验 %PDF 文件头 + 重试；自动按方向归档到中文子目录）
python3 scripts/arxiv_daily.py download 2608.19875 2608.20169 ...

# 2b) 从检索 JSON 自动下载 top N 篇（带 direction 映射归档）
python3 scripts/arxiv_daily.py download out/papers_2026-08-24.json

# 3) 全流程：fetch + 自动下载 top 10
python3 scripts/arxiv_daily.py all
```

## 论文全文翻译（pdf2zh + MiniMax）

英文 PDF → 中文 Markdown，使用本地 pdf2zh 工具（MinerU 解析 + MiniMax-M3 翻译）：

```bash
cd /Users/xbpd/Projects/pdf2zh
export MINIMAX_API_KEY=$(grep '^MINIMAX_API_KEY=' .env | cut -d= -f2-)
.venv/bin/python pdf_to_zh_md.py "<论文.pdf>" \
    -o <输出目录> --model MiniMax-M3 \
    --base-url https://api.minimaxi.com/v1 --workers 4
```

- 输出：`<输出目录>/<论文名>/auto/<论文名>.md`（英文）+ `<论文名>_zh.md`（中文，即目标产物）
- 首次运行 mineru 会下载模型（较慢）；翻译按 chunk 并发（默认 8 workers）
- macOS 无 `timeout` 命令，**不要用 timeout/gtimeout 包裹**，直接后台运行
- 翻译失败重试逻辑已内置（translate_md.py 带 3 次指数退避）
- MiniMax-M3 为推理模型，translate_md.py 已自动剥离 `<think>` 标签

## 完整日报工作流（自动化已配置，每日 08:00 触发）

1. `arxiv_daily.py fetch` → 六方向论文 JSON
2. 筛选 8–10 篇命中论文
3. `arxiv_daily.py download <ids>` → PDF 自动归档到 `/Users/xbpd/Documents/论文/<方向>/`
4. 每篇生成 `<id>_翻译导读.md`（方向/问题/方法/摘要翻译/关联），与 PDF 同目录
5. 可选：pdf2zh 全文翻译（耗时，适合后台）
6. WebSearch + GitHub 趋势脚本采集新闻
7. 以 `2026-08-24.html` 为模板生成 `<date>.html`（含暗色模式/打印/历史入口）

## 关键容错点（踩坑记录）

| 问题 | 对策 |
|------|------|
| arXiv API 偶发 HTTP 500（限流） | 脚本内置退避重试（10s×N）；查询使用简化语法（不带 submittedDate 范围，靠 sortBy 倒序取最新），已验证更稳定 |
| PDF 下载返回 HTML（被拦截/无 PDF） | 脚本校验文件头 `%PDF`；非 PDF 自动换 export.arxiv.org 镜像重试 |
| 论文被作者撤回（无 PDF） | arXiv 页标注 withdrawn → 换同方向论文替代并在报告中注明 |
| 复杂查询含 `submittedDate` 范围偶发失败 | 简化查询或调整日期窗口后重试 |
| macOS 无 timeout 命令 | 不要用 timeout/gtimeout 包裹命令，直接后台运行 |
| MiniMax-M3 输出 think 标签 | translate_md.py 已自动剥离 `<think>...</think>` |

## 质量红线（devguard）

- 论文摘要翻译基于 arXiv API 原文，**不编造**
- 新闻标注来源与日期，不确定的标「未验证」
- 严禁写入 `~/Documents/xbpd_obsidian`
- 报告内 KPI 数字与正文必须一致
- 用户个人目录（Documents）只做授权范围内的论文归档，不移动/删除无关文件
