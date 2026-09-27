#!/usr/bin/env python3
"""只读预览：论文库 → WikiSkill 格式 重组计划引擎
产出 /tmp/migration_preview.md：逐篇论文的目标操作（建夹/移动/改名/入待转换池）与歧义标记
绝不执行任何写操作。
"""
import os, re, unicodedata, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import CFG

ROOT = Path(CFG["paths"]["paper_root_legacy"])
DIRS = list(CFG["directions"])
SKIP = {".DS_Store"}
PENDING = CFG["special_dirs"]["pending_ocr"]   # 统一目录

def has_cjk(s): return any('\u4e00' <= c <= '\u9fff' for c in s)

def norm(s):
    s = re.sub(r"[《》【】\[\]:：,，.。·\-_#()（）\s]+", "", s or "")
    return s.lower()

def scan_dir(d):
    base = ROOT / d
    out = {"base": base, "pdfs": [], "guides": [], "t_md": [], "parse_dirs": [], "manual": []}
    for f in sorted(base.iterdir()):
        if f.name in SKIP: continue
        if f.is_dir():
            if f.name == "translated": continue
            out["manual"].append(f)
        elif f.suffix.lower() == ".pdf":
            out["pdfs"].append(f)
        elif f.suffix.lower() == ".md":
            (out["guides"] if "导读" in f.name else out["t_md"]).append(f)
    tdir = base / "translated"
    if tdir.is_dir():
        for f in sorted(tdir.iterdir()):
            if f.name in SKIP: continue
            if f.is_dir(): out["parse_dirs"].append(f)
            elif f.suffix.lower() == ".md": out["t_md"].append(f)
    return out

def first_h1(md):
    try:
        for ln in md.read_text(encoding="utf-8", errors="replace").splitlines()[:25]:
            s = ln.strip()
            if s.startswith("# ") and not s.startswith("#!"):
                return s[2:].strip()
    except OSError: pass
    return None

def english_h1(parse_md):
    """找 auto 内英文 md 的英文标题（首个 # 且 ASCII 为主）"""
    t = first_h1(parse_md)
    if t and has_cjk(t):  # 已是中文则返回 None 由调用方用文件名
        return None
    return t

def author_tokens(md, maxl=8):
    """抽取作者英文姓/名 token 集合，用于内容配对"""
    try:
        txt = "\n".join(md.read_text(encoding="utf-8", errors="replace").splitlines()[:maxl])
    except OSError:
        return set()
    toks = re.findall(r"[A-Z][a-z]{2,}", txt)
    stop = {"Google","DeepMind","Research","Institute","University","Stanford","Microsoft","Meta","OpenAI","Anthropic","Agent","Intelligence","Laboratory","The","Of","And","For","In","On","With","From"}
    return {t.lower() for t in toks if t not in stop and len(t) > 2}

def analyze_all():
    rows = []   # 每篇论文/文件一条计划
    amb = []    # 歧义
    stats = {"translated_papers":0, "pending_pdfs":0, "parse_only":0, "amb":0}
    for d in DIRS:
        s = scan_dir(d)
        base = s["base"]

        # ---- A. 每篇已翻译论文：md(中文全文) 为主键 ----
        # 先收集 parse dir 信息
        parse_info = {}   # stem -> {dir, inner_md(english md path), has_img}
        for pd in s["parse_dirs"]:
            auto = pd / "auto"
            inner = []
            root_mds = []
            for m in pd.rglob("*.md"):
                if m.name.endswith("_middle.json"): continue
                inner.append(m)
            parse_info[pd.name] = {
                "dir": pd,
                "mds": inner,
                "has_img": any(i.suffix.lower() in (".jpg",".png",".jpeg") for i in pd.rglob("*")),
            }

        def find_parse_by_title(md):
            """md(中文) → 匹配 parse dir：优先同 stem；再按英文标题/作者 token 相似"""
            stem = md.stem
            if stem in parse_info: return parse_info[stem], "stem"
            # 英文作者 token 匹配
            tk = author_tokens(md)
            best, bestscore = None, 0
            for name, info in parse_info.items():
                score = 0
                for m in info["mds"][:1]:
                    h1 = english_h1(m)
                    if h1 and norm(h1):
                        if norm(h1) == norm(md.stem): score += 3
                # 内容作者 token
                cand = set()
                for m in info["mds"][:1]:
                    cand |= author_tokens(m)
                if tk and cand:
                    inter = len(tk & cand)
                    score += inter
                if score > bestscore:
                    best, bestscore = name, score
            if best and bestscore >= 2:
                return parse_info[best], f"content({bestscore})"
            return None, ""

        # 翻译中文 md 列表
        for md in s["t_md"]:
            title = md.stem
            # 去重：若根级同名目录已存在则跳过其 auto 内重复中文（保留）
            pinfo, how = find_parse_by_title(md)
            # pdf: 中文pdf 同名，或 parse dir stem → base pdf
            pdf = None
            if pinfo:
                cand = base / (pinfo["dir"].name + ".pdf")
                if cand.exists(): pdf = cand
            else:
                cand = base / (md.stem + ".pdf")
                if cand.exists(): pdf = cand
            # 导读：同标题/同pdf stem 或含 id
            guide = None
            for g in s["guides"]:
                if norm(g.stem.replace("_翻译导读","")) in (norm(md.stem), norm(pdf.stem if pdf else "")):
                    guide = g; break
            rows.append({"type":"paper","dir":d,"title":title,"pdf":pdf and pdf.name,
                         "parse":(pinfo["dir"].name if pinfo else None),"pair":how,
                         "guide":guide and guide.name})
            stats["translated_papers"] += 1
            if not pinfo or not pdf:
                amb.append({"dir":d,"title":title,"missing":("parse" if not pinfo else "")+("pdf" if not pdf else "")})

        # ---- B. 根级 PDF 未配对(无译文)：入待转换池 ----
        used_pdfs = {r["pdf"] for r in rows if r["pdf"]}
        for p in s["pdfs"]:
            if p.name in used_pdfs: continue
            # 该 pdf 是否有 parse dir（同名）且 parse 内有英文md=已解析未翻译？
            pinfo = parse_info.get(p.stem)
            rows.append({"type":"pending","dir":d,"pdf":p.name,
                         "has_parse":bool(pinfo)})
            stats["pending_pdfs"] += 1
        # ---- C. parse_dirs 未与任何 md/pdf 关联 = 孤儿，列歧义 ----
        used_parse = {r.get("parse") for r in rows if r.get("parse")}
        used_parse |= {p.stem for p in s["pdfs"]}
        for name, info in parse_info.items():
            if name not in used_parse:
                # parse-only (无中文翻译md) 也许只是该 pdf 不在根（pdf 可能已被移走）
                rows.append({"type":"orphan_parse","dir":d,"parse":name,"mds":[m.name for m in info["mds"]][:2]})
                stats["parse_only"] += 1
    return rows, amb, stats

if __name__ == "__main__":
    rows, amb, stats = analyze_all()
    L = []
    L.append(f"# 论文库重组预览（只读）\n\n统计：已翻译论文 **{stats['translated_papers']}** | 未翻译PDF **{stats['pending_pdfs']}** | 孤儿解析目录 **{stats['parse_only']}** | 歧义 **{len(amb)}**\n")
    cur = None
    for r in rows:
        if r["dir"] != cur:
            cur = r["dir"]; L.append(f"\n## {cur}\n")
        if r["type"] == "paper":
            L.append(f"- 📄→📁 **{r['title']}**\n  pdf={r['pdf'] or '⚠无'} | 解析={r['parse'] or '⚠无'} ({r['pair']}) | 导读={r['guide'] or '—'}")
        elif r["type"] == "pending":
            L.append(f"- ⏳ 待转换: {r['pdf']}" + ("（已解析待翻译）" if r["has_parse"] else ""))
        else:
            L.append(f"- 🗑 孤儿解析目录: {r['parse']}/ md={r['mds']}")
    if amb:
        L.append("\n## ⚠ 歧义待人工确认\n")
        for a in amb: L.append(f"- [{a['dir']}] {a['title']} 缺: {a['missing']}")
    text = "\n".join(L)
    Path("/tmp/migration_preview.md").write_text(text, encoding="utf-8")
    print(f"rows={len(rows)} translated={stats['translated_papers']} pending={stats['pending_pdfs']} orphan={stats['parse_only']} amb={len(amb)}")
    print("preview → /tmp/migration_preview.md")
