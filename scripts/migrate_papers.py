#!/usr/bin/env python3
"""论文库 → WikiSkill 格式 重组执行引擎
- 每篇已翻译论文：<方向>/<中文标题>/ 内含 <中文标题>.pdf + <中文标题>_全文翻译.md
  + <中文标题>_翻译导读.md + 原始解析目录(保留,md图片引用重写指向它)
- 未翻译 PDF(含已解析未翻译): 移入 00-待OCR转换/
- 重复 PDF(同论文多份): 移入 _重复PDF待删/
- 全流程先备份珍贵原件到 .migration_backup_<ts>/
幂等: 目标已存在则跳过; 输出逐文件日志 /tmp/migrate.log
"""
import os, re, shutil, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import CFG

ROOT = Path(CFG["paths"]["paper_root"])
DIRS = list(CFG["directions"])
SKIP = {".DS_Store"}
PENDING_DIR = ROOT / CFG["special_dirs"]["pending_ocr"]
DUP_DIR = ROOT / "_重复PDF待删"
LOG = "/tmp/migrate.log"

def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

def has_cjk(s): return any('\u4e00' <= c <= '\u9fff' for c in s)

def sanitize(name: str, maxlen=80) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:maxlen].rstrip()

def safe_move(src: Path, dst: Path):
    if src == dst: return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        log(f"  ⚠目标已存在, 跳过: {dst}")
        return
    src.rename(dst)

def author_tokens(md, maxl=8):
    try:
        txt = "\n".join(md.read_text(encoding="utf-8", errors="replace").splitlines()[:maxl])
    except OSError:
        return set()
    toks = re.findall(r"[A-Z][a-z]{2,}", txt)
    stop = {"Google","DeepMind","Research","Institute","University","Stanford","Microsoft","Meta","OpenAI","Anthropic","Laboratory","The","Of","And","For","In","On","With","From","Agent","Intelligence"}
    return {t.lower() for t in toks if t not in stop and len(t) > 2}

def scan_dir(d):
    base = ROOT / d
    out = {"base": base, "pdfs": [], "guides": [], "t_md": [], "parse_dirs": []}
    for f in sorted(base.iterdir()):
        if f.name in SKIP or f.name.startswith("."): continue
        if f.is_dir():
            if f.name == "translated": continue
            continue  # 已有 manual 论文夹不动
        if f.suffix.lower() == ".pdf":
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

def pair_translation_md(md, parse_info):
    if md.stem in parse_info:
        return parse_info[md.stem], "stem"
    tk = author_tokens(md)
    best, bestscore = None, 0
    for name, info in parse_info.items():
        score = 0
        mds = info["mds"]
        if mds:
            for m in mds[:2]:
                try:
                    h1 = next((ln[2:].strip() for ln in m.read_text(encoding="utf-8",
                         errors="replace").splitlines()[:25] if ln.startswith("# ")), None)
                except OSError:
                    h1 = None
                if h1 and not has_cjk(h1) and re.sub(r"[^a-z0-9]", "", h1.lower()) and \
                   re.sub(r"[^a-z0-9]", "", h1.lower()) == re.sub(r"[^a-z0-9]", "", md.stem.lower()):
                    score += 3
                cand = author_tokens(m)
                if tk and cand: score += len(tk & cand)
        if score > bestscore:
            best, bestscore = name, score
    if best and bestscore >= 2:
        return parse_info[best], f"content({bestscore})"
    return None, ""

def main():
    if os.path.exists(LOG): os.remove(LOG)
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = ROOT / f".migration_backup_{ts}"
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    DUP_DIR.mkdir(parents=True, exist_ok=True)
    log(f"=== 迁移开始 {ts} | backup={backup} ===")

    plan = []
    for d in DIRS:
        s = scan_dir(d)
        base = s["base"]
        parse_info = {}
        for pd in s["parse_dirs"]:
            parse_info[pd.name] = {"dir": pd, "mds": list(pd.rglob("*.md")),
                                   "has_img": any(i.suffix.lower() in (".jpg",".png",".jpeg") for i in pd.rglob("*"))}
        # 对每篇译文：建论文夹
        used_pdfs, used_parse = set(), set()
        for md in s["t_md"]:
            title = sanitize(md.stem)
            pinfo, how = pair_translation_md(md, parse_info)
            # 选 pdf: 中文同名 > parse同名
            pdf = None
            if pinfo:
                c = base / (pinfo["dir"].name + ".pdf")
                if c.exists(): pdf = c
                else:
                    # parse 目录可能是中文(已处理过)则 base/<title>.pdf
                    c2 = base / (title + ".pdf")
                    if c2.exists(): pdf = c2
            if not pdf:
                c = base / (md.stem + ".pdf")
                if c.exists(): pdf = c
            guide = None
            for g in s["guides"]:
                gstem = re.sub(r"_翻译导读$", "", g.stem)
                if gstem == md.stem or (pdf and gstem == pdf.stem):
                    guide = g; break
            folder = base / title
            plan.append({"d": d, "kind": "paper", "title": title, "md": md,
                         "pdf": pdf, "parse": pinfo and pinfo["dir"],
                         "guide": guide, "folder": folder, "how": how})
            if pdf: used_pdfs.add(pdf.name)
            if pinfo: used_parse.add(pinfo["dir"].name)

        # 未配对 pdf → 分类
        for p in s["pdfs"]:
            if p.name in used_pdfs: continue
            pinfo = parse_info.get(p.stem)
            dup = False
            # 重复判定: 其它译文已使用同名 parse 或同内容但此 pdf 无译文
            for item in plan:
                if item["d"] == d and item["kind"] == "paper" and item["parse"]:
                    if p.stem == item["parse"].name:
                        dup = True; break
            plan.append({"d": d, "kind": "dup" if dup else "pending",
                         "pdf": p, "parse": pinfo and pinfo["dir"]})
            if pinfo: used_parse.add(pinfo["dir"].name)

        # 孤儿 parse: 未使用的解析目录(可能 md 在内已译但译文不在 t_md, 或无)
        for name, info in parse_info.items():
            if name not in used_parse:
                plan.append({"d": d, "kind": "orphan", "parse": info["dir"]})
    # ---- 备份珍贵原件 ----
    log("--- 备份 ---")
    b = backup / "论文根PDF等"
    count = 0
    for item in plan:
        if item["kind"] == "paper":
            srcs = [x for x in (item["md"], item["pdf"], item["guide"]) if x]
        elif item["kind"] in ("dup","pending"):
            srcs = [item["pdf"]]
        else:
            srcs = []
        for src in srcs:
            rel = src.relative_to(ROOT)
            dst = backup / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src, dst); count += 1
            except OSError as e:
                log(f"  !备份失败 {rel}: {e}")
    log(f"  备份 {count} 个文件 -> {backup}")

    # ---- 执行 ----
    summary = {"paper":0,"pending":0,"dup":0,"orphan":0,"warn":[]}
    for item in plan:
        try:
            if item["kind"] == "paper":
                folder = item["folder"]; folder.mkdir(parents=True, exist_ok=True)
                # md → 全文翻译
                dst_md = folder / f"{item['title']}_全文翻译.md"
                safe_move(item["md"], dst_md)
                # 导读
                if item["guide"]:
                    safe_move(item["guide"], folder / f"{item['title']}_翻译导读.md")
                # pdf → 中文名
                if item["pdf"]:
                    safe_move(item["pdf"], folder / f"{item['title']}.pdf")
                # parse 目录移入并重写图片引用
                if item["parse"] and item["parse"].exists():
                    nested = folder / sanitize(item["parse"].name) if has_cjk(item["parse"].name) else folder / item["parse"].name
                    if not nested.exists():
                        item["parse"].rename(nested)
                    # 重写 md 中 images/ 引用 → {nested}/auto/images/
                    img_prefix = f"{nested.name}/auto/images/"
                    try:
                        t = dst_md.read_text(encoding="utf-8", errors="replace")
                        new = re.sub(r"(?<=\()images/", img_prefix, t)
                        if new != t:
                            dst_md.write_text(new, encoding="utf-8")
                            log(f"    ↻ 图片引用重写: {item['title']} -> {img_prefix}")
                    except OSError: pass
                log(f"📁 [{item['d']}] {item['title']} (配对:{item['how']})")
                summary["paper"] += 1
            elif item["kind"] == "pending":
                pdf = item["pdf"]
                dest = PENDING_DIR / f"[{item['d']}] {pdf.name}"
                safe_move(pdf, dest)
                # 带解析目录(英文md, 已解析未翻译)一起走, 便于后续翻译直接引用
                if item["parse"] and item["parse"].exists():
                    d2 = PENDING_DIR / f"[{item['d']}] {item['parse'].name}"
                    if not d2.exists():
                        item["parse"].rename(d2)
                # 同 id 导读一并移
                g = scan_dir(item["d"])["guides"]
                log(f"⏳ [{item['d']}] {pdf.name} -> 00-待OCR转换")
                summary["pending"] += 1
            elif item["kind"] == "dup":
                pdf = item["pdf"]
                dest = DUP_DIR / f"[{item['d']}] {pdf.name}"
                safe_move(pdf, dest)
                log(f"🔁 [{item['d']}] 重复PDF隔离: {pdf.name}")
                summary["dup"] += 1
            else:
                # orphan parse: 根级保留但记录; 有译文的 orphan 其 md 已在别处, 不动目录以免误删
                summary["orphan"] += 1
                summary["warn"].append(f"孤儿解析目录未处理: {item['parse']}")
        except Exception as e:
            log(f"  ✗ 处理失败 {item.get('d')}/{item.get('title') or item.get('pdf')}: {e}")
            summary["warn"].append(str(e))
    log(f"=== 完成: 论文夹 {summary['paper']} | 待转换 {summary['pending']} | 重复 {summary['dup']} | 孤儿 {summary['orphan']} ===")
    for w in summary["warn"][:20]:
        log(f"警告: {w}")
    # 清理空 translated 目录与根级空目录
    for d in DIRS:
        tdir = ROOT / d / "translated"
        if tdir.is_dir() and not any(tdir.iterdir()):
            tdir.rmdir(); log(f"清理空目录: {tdir}")
    for cand in (ROOT / d for d in DIRS):
        if cand.is_dir() and not any(cand.iterdir()):
            cand.rmdir(); log(f"清理空目录: {cand}")

if __name__ == "__main__":
    main()
