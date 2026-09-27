#!/usr/bin/env python3
"""audit_integrity.py — 论文库「组装一致性」巡检

回答一个问题：**每个夹的解析目录，到底是不是本夹这篇论文的？**

原理（确定性，不靠启发式）：
  解析目录里的 md 是 MinerU 对**某份 PDF** 的 OCR 结果。若它就是本夹的论文，
  那么它的英文标题必然出现在**本夹 PDF 的首页文本**里。
  → 取解析目录 H1 的词序列，在本夹 PDF 首页文本里找**最长连续命中长度**：
       ≥4 词  → OK（标题逐字出现在首页）
       2-3 词 → SUSPECT（首页文本质量差/标题被拆行，需人工看）
       ≤1 词  → MISMATCH（首页根本没有这个标题，解析目录是别人家的残留）
  ⚠ 通过时也要显式写 OK，否则会和「未生成译文」叠加被误判成 SKIP_CN（见下方注释）。

根因背景：库内已实证存在「夹名+译文是 A 篇、解析目录是 B 篇」的错配夹
（06-AI医疗 一个 RAG 疾病分类夹里躺着 Med-PaLM 2 的解析目录）。

附带检查：译文 H1（中文）与夹名中文部分是否一致 → 检出「夹名与内容错位」。

用法:
  python3 scripts/audit_integrity.py                # 全库巡检
  python3 scripts/audit_integrity.py --dir 06-AI医疗  # 只查某方向
报告: /tmp/integrity_report.json
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from complete_paper_folder import ROOT, DIRS, paper_folders, survey   # noqa: E402

CN_STOP = set("的一是在了与及和或有对为")

# 通用学术词：几乎所有 AI 论文标题/机构名都有，不构成同一篇的证据
GENERIC = {
    "large", "language", "model", "models", "learning", "medical", "health", "healthcare",
    "clinical", "artificial", "intelligence", "deep", "neural", "network", "networks",
    "based", "towards", "using", "with", "from", "data", "system", "systems", "analysis",
    "approach", "approaches", "method", "methods", "study", "survey", "review", "framework",
    "agent", "agents", "agentic", "knowledge", "question", "answering", "reasoning",
    "context", "prompt", "prompting", "generation", "generative", "training", "evaluation",
    "performance", "robust", "efficient", "multi", "new", "can", "what", "why", "how",
    "this", "that", "these", "their", "toward", "guide", "guidance", "report", "paper",
    "university", "institute", "department", "college", "school", "center", "centre",
}


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def strip_tags(s: str) -> str:
    """MinerU 会把下标/连字嵌成 HTML 标签（如 Dream-RSI: Recursive Self-Im<sub>p</sub>rovement），
    不清掉的话分词会得到 sub/p/roug 之类碎片，与 PDF 首页文本对不上 → 全是误报。"""
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"&[a-z]+;", " ", s)
    return s


def words(s: str) -> list:
    """切词。注意去连字符：MinerU 解析全大写双栏标题时会把词断开
    （`GRAPH FOUN-DATIONAL MODEL`），而 PDF 正文里是 `foundational`。
    两侧统一去掉词内连字符后即可对齐；对 `llm-free` 这类真实连字符词无副作用
    （两边同样处理，仍能相互匹配）。2026-09-17 实测：不去连字符会造成 7 例 SUSPECT 误报。
    """
    s = strip_tags(s).lower()
    s = re.sub(r"[^a-z0-9\s\-]", " ", s)
    return [w.replace("-", "") for w in re.sub(r"\s+", " ", s).split() if w]


def despaced(s: str) -> str:
    """去掉所有非字母数字字符。用途见 nospace_hit()。"""
    return re.sub(r"[^a-z0-9]", "", strip_tags(s).lower())


def nospace_hit(h1: str, page: str) -> bool:
    """兜底判定：PDF 文字层可能**丢失词间空格**，按词比对会假阴性。

    实例（2026-09-17）：ICLR 投稿模板的全大写标题被抽成
      `GILT: ANLLM-FREE, TUNING-FREEGRAPHFOUN-DATIONALMODEL FORIN-CONTEXTLEARNING`
    标题其实逐字在首页，只是词间空格没了 → longest_run 只剩 3。
    去空格后做子串比对即可救回；要求长度 ≥20 以免短标题误命中。
    """
    a, b = despaced(h1), despaced(page)
    return len(a) >= 20 and a in b


def longest_run(title: list, text: list) -> int:
    """title 的最长连续词串在 text 中出现的长度。"""
    if not title or not text:
        return 0
    pos = {}
    for j, w in enumerate(text):
        pos.setdefault(w, []).append(j)
    best = 0
    for i, w in enumerate(title):
        for j in pos.get(w, ()):
            k = 0
            while (i + k < len(title) and j + k < len(text)
                   and title[i + k] == text[j + k]):
                k += 1
            if k > best:
                best = k
                if best >= 6:
                    return best
    return best


def pdf_first_page(pdf: Path, cache: dict, pages: int = 3) -> str:
    """取 PDF 前 N 页文本。取 1 页不够：论文集类 PDF 首页常是版权页，作者在第 2 页
    （实测 EACL 版式），只取首页会漏掉作者 → 误判为错配。

    ⚠ 缺 pypdf 时必须吼出来：曾因用了未装 pypdf 的解释器，异常被吞掉，
      全库 237 个夹被静默判成 NO_TEXT（假结论）。请用装了 pypdf 的解释器
      （paths.python_hint，见 config.json）运行
    """
    key = f"{pdf}|{pages}"
    if key in cache:
        return cache[key]
    try:
        from pypdf import PdfReader
    except ImportError as e:  # 环境问题，必须立刻失败而不是产出假结论
        from pipeline_config import CFG
        raise SystemExit(
            f"✗ 缺少 pypdf（{e}）—— 请用 {CFG['paths']['python_hint']} 运行")
    try:
        with open(pdf, "rb") as f:
            rd = PdfReader(f)
            txt = "\n".join((p.extract_text() or "") for p in rd.pages[:pages])
    except Exception as e:
        log(f"⚠ PDF 文本提取失败({pdf.name}): {e}")
        txt = ""
    cache[key] = txt
    return txt


def cn_part(name: str) -> str:
    # 注意：英文标识可能很长（如 A Framework for Longitudinal Health AI Agents），不能设长度上限
    m = re.match(r"^([^\u4e00-\u9fff]+)[：:]\s*(\S.*)$", name)
    if m and re.search(r"[A-Za-z0-9]", m.group(1)):
        return m.group(2).strip()
    return name.strip()


def surnames(text: str) -> set:
    """抓「首字母大写、长度≥4」的拉丁词并剔除通用学术词——剩下的才是可辨识的作者/专名。"""
    return {w for w in re.findall(r"\b[A-Z][a-z]{3,}\b", text)
            if w.lower() not in GENERIC}


def translation_authors(folder: Path, full_trans: Path) -> set:
    """译文头部（H1 之后若干行）里的拉丁专名。译文保留作者名与专名，可作内容指纹。"""
    try:
        lines = full_trans.read_text(encoding="utf-8", errors="replace").splitlines()[:25]
    except OSError:
        return set()
    return surnames("\n".join(lines))


def fingerprint_verdict(trans_tokens: set, pdf_tokens: set) -> tuple:
    """用「交集 / 较小集合」判定译文与 PDF 是否同一篇。

    实测标定：真错配 0.00，正常夹 0.84~1.00（含单作者学位论文与 WHO 报告这类低信号样本）。
    信号太少（较小集合 < 2）时不判定，返回 (None, ratio)。
    """
    small = min(len(trans_tokens), len(pdf_tokens))
    if small < 2:
        return None, None
    ratio = len(trans_tokens & pdf_tokens) / small
    # 阈值标定（实测）：真错配 0.00；正常夹（含无个人作者、首页无标题版式）最低 0.4 以上。
    # 故只在「几乎零重合」时判错配，宁漏勿误——边界带交人工复核。
    return ("MISMATCH" if ratio < 0.2 else "OK"), round(ratio, 2)


HEADING = re.compile(r"^(#{1,6})\s+(.+)$")


def first_h1(path: Path, limit: int = 30) -> tuple:
    """取解析件里的首个标题 → (文本, 是否降级)。

    优先真 H1（`# `）；若通篇没有 H1 则降级取首个任意级标题。
    必要性：MinerU 对 WHO 指南这类文档会输出 `## Ethics and governance of …` 作标题，
    只认 `# ` 会让这些夹的标题提取为空 → 巡检出现盲区（2026-09-17 实测 1 例）。
    降级取得的标题可信度较低，调用方据此把 MISMATCH 降为 SUSPECT，避免误报。
    """
    try:
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()[:limit]:
            m = HEADING.match(ln.strip())
            if not m:
                continue
            t = re.sub(r"<[^>]+>", "", m.group(2)).strip()   # 去掉 MinerU 混入的 <sub> 等标签
            if t:
                return t, m.group(1) != "#"
    except OSError:
        return None, False
    return None, False


def audit(dirs=None) -> dict:
    pdf_cache: dict = {}
    rows = []
    folders = sorted(paper_folders())
    if dirs:
        folders = [f for f in folders
                   if any(f.relative_to(ROOT).parts[:1] == (d,) for d in dirs)]
    for f in folders:
        rel = f.relative_to(ROOT)
        d = rel.parts[0] if len(rel.parts) > 1 else "."
        s = survey(f)
        rec = {"dir": d, "folder": f.name}
        if not s["pdf"]:
            rec["verdict"] = "NO_PDF"
            rows.append(rec)
            continue
        if not s["parse_dir"] or not s["full_md"]:
            rec["verdict"] = "NO_PARSE"      # 解析目录缺失（A 正在补）
            rows.append(rec)
            continue
        h1, fellback = first_h1(s["full_md"])
        h1 = h1 or ""
        rec["parse_h1"] = h1
        if fellback:
            rec["h1_fallback"] = True      # 无 H1，取的是次级标题 → 结论从宽
        page = pdf_first_page(s["pdf"], pdf_cache)
        has_page = len(page.strip()) >= 80

        # 检查一：解析目录 vs PDF —— 解析件英文标题是否逐字出现在本夹 PDF 前 3 页。
        # 该检查误报率高（版式差异、标题跨行、中文标题都会压低命中），
        # 故只把「几乎零命中」当强证据；2-3 词记 SUSPECT 交人工，≥4 词判 OK。
        #
        # ⚠ 必须显式写 OK：若只在 MISMATCH 时赋值，通过的夹会留下 parse_vs_pdf=None，
        #   与「未生成译文→name_vs_pdf=None」叠加后被误判为 SKIP_CN（不可验证）。
        #   2026-09-17 实测：06-AI医疗 17/41 个已强匹配的夹被这样错标。
        if has_page and len(words(h1)) >= 4:   # 中文标题剥空后只剩碎片，必须要求足量英文词
            run = longest_run(words(h1)[:14], words(page))
            rec["run"] = run
            verdict = None
            if run <= 3 and nospace_hit(h1, page):
                # 标题其实逐字在首页，只是 PDF 文字层丢了空格 → 视为命中
                verdict = "OK"
                rec["run_nospace"] = True
            if verdict is None:
                if run <= 1:
                    # 降级标题只是次级标题，零命中不足以定罪 → 降为 SUSPECT 交人工
                    verdict = "SUSPECT" if fellback else "MISMATCH"
                elif run <= 3:
                    verdict = "SUSPECT"
                else:
                    verdict = "OK"
            rec["parse_vs_pdf"] = verdict

        # 检查二：译文/夹名 vs PDF —— 解析目录对得上 PDF，不代表夹名与译文也对得上。
        # 译文保留作者名/专名，故用「专名交集 / 较小集合」作为内容指纹。
        if has_page and s["full_trans"].exists():
            ta = translation_authors(f, s["full_trans"])
            v, ratio = fingerprint_verdict(ta, surnames(page))
            rec["fingerprint"] = ratio
            if v:
                rec["name_vs_pdf"] = v

        parts = [rec.get("parse_vs_pdf"), rec.get("name_vs_pdf")]
        if "MISMATCH" in parts:
            rec["verdict"] = "MISMATCH"
        elif "SUSPECT" in parts:
            rec["verdict"] = "SUSPECT"
        elif all(p is None for p in parts):
            rec["verdict"] = "NO_TEXT" if not has_page else "SKIP_CN"
        else:
            rec["verdict"] = "OK"

        # 附带检查：译文 H1 与夹名中文部分是否一致（仅在译文 H1 本身是中文时才有意义，
        # 不少译文保留了英文原标题，那属于正常，不能误判）
        th1, _ = (first_h1(s["full_trans"]) if s["full_trans"].exists()
                  else (None, False))
        if th1 and re.search(r"[\u4e00-\u9fff]", th1):
            a = {c for c in cn_part(f.name) if c not in CN_STOP and not c.isspace()}
            b = {c for c in strip_tags(th1) if c not in CN_STOP and not c.isspace()}
            if a:
                rec["name_vs_trans"] = round(len(a & b) / len(a), 2)
        rows.append(rec)
    return {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", action="append", help="只查指定方向（可多次）")
    a = ap.parse_args()
    rep = audit(a.dir)
    rows = rep["rows"]
    from collections import Counter
    c = Counter(r["verdict"] for r in rows)
    log(f"巡检 {len(rows)} 个夹 → " + " | ".join(f"{k}={v}" for k, v in c.most_common()))

    bad = [r for r in rows if r["verdict"] in ("MISMATCH", "SUSPECT")]
    if bad:
        print(f"\n=== 组装错位（{len(bad)}）===")
        for r in bad:
            which = []
            if r.get("parse_vs_pdf") in ("MISMATCH", "SUSPECT"):
                which.append(f"解析目录≠PDF(命中{r.get('run')}词)")
            if r.get("name_vs_pdf") == "MISMATCH":
                which.append(f"夹名/译文≠PDF(指纹{ r.get('fingerprint') })")
            print(f"  [{r['verdict']}] {' + '.join(which)}")
            print(f"      {r['dir']}/{r['folder']}")
            if r.get("parse_h1"):
                print(f"      解析目录标题: {strip_tags(r['parse_h1'])[:78]}")

    # name_vs_trans 仅写入 JSON 供参考：译文 H1 常是意译或保留英文，该项无实证真阳性，
    # 不做告警，避免刷屏。
    out = Path("/tmp/integrity_report.json")
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"报告 → {out}")
    return 0

    out = Path("/tmp/integrity_report.json")
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"报告 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
