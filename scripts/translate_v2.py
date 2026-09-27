#!/usr/bin/env python3
"""translate_v2.py — 新翻译管线：消费 00-待OCR转换 池 → 产出标准论文夹
流程(每篇): MinerU云OCR → MiniMax-M3 分块中文翻译 → 组论文夹
  <方向>/<中文标题>/{<中文标题>.pdf, <中文标题>_全文翻译.md,
                   <中文标题>_翻译导读.md, <中文标题>/原解析(full.md+images)}
状态: /tmp/translate_v2.log + /tmp/translate_v2.state(processed)
用法: python3 translate_v2.py [--test <pdf路径>] 或 无参全量(跳过 processed)
"""
import sys, os, re, json, time, shutil, subprocess
import urllib.request, urllib.error
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import CFG

ROOT = Path(CFG["paths"]["paper_root"])
PENDING = ROOT / CFG["special_dirs"]["pending_ocr"]
DIRS = list(CFG["directions"])
LOG = "/tmp/translate_v2.log"
STATE = Path("/tmp/translate_v2.state")
MAX_CHUNK = int(CFG["translate"]["max_chunk"])   # 每块字符数

def log(*a):
    msg = f"[{time.strftime('%H:%M:%S')}] " + " ".join(str(x) for x in a)
    print(msg, flush=True)
    with open(LOG, "a", encoding="utf-8") as f: f.write(msg + "\n")

def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}

def save_state(st):
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")

def sanitize(name, maxlen=80):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .#*")
    return name[:maxlen].rstrip()

def chunk_md(text):
    """按行分块，不在代码块/表格中切分"""
    lines = text.split("\n")
    chunks, cur, n = [], [], 0
    in_code = False
    for ln in lines:
        if ln.strip().startswith("```"):
            in_code = not in_code
        cur.append(ln); n += len(ln) + 1
        if n >= MAX_CHUNK and not in_code:
            chunks.append("\n".join(cur)); cur, n = [], 0
    if cur: chunks.append("\n".join(cur))
    return chunks or [""]

SYS_TRANS = ("你是资深 AI 论文中英翻译。将用户给出的英文 Markdown 论文段落译为简体中文：完整翻译不省略；保留全部 Markdown 结构（标题层级/列表/引用/表格/代码块）、图片引用(![](...))、LaTeX 公式与 HTML 表格原样；术语首次出现保留英文原文(如 in-context learning 上下文学习)；图表标题译中文；只输出译文本身。"
             "重要：机构库封面页/版权声明/出版社样板文字（如 brought to you by、Research Collection、See discussions, stats、Follow this and additional works、作者机构页、DOI 行）可原样保留英文；"
             "但它们之后的一切论文正文——摘要(Abstract)、关键词、引言、各章节、结论、致谢、参考文献条目——必须完整译为中文，"
             "绝不可因为同段落里出现了英文样板文字就把紧随其后的摘要或正文一并保留为英文。")

# 块级回退补救用：某块输出被判为「整块回退成英文原文」时，用这条更强硬的 prompt 重试
SYS_TRANS_STRICT = ("你是资深 AI 论文中英翻译。下面这段**是论文正文**，必须完整译为简体中文："
                    "不允许保留任何英文原文段落，不允许以「出版社样板/版权声明/机构页」为由跳过正文。"
                    "只允许原样保留 Markdown 结构（标题层级/列表/引用/表格/代码块）、图片引用(![](...))、"
                    "LaTeX 公式、HTML 标签与术语的英文原文。章节标题也要译成中文。只输出译文本身。")

CJK_CHAR = re.compile(r"[\u4e00-\u9fff]")


def cn_ratio(t: str) -> float:
    return len(CJK_CHAR.findall(t)) / max(1, len(t))


def llm_translate(text, key, base="https://api.minimaxi.com/v1", model="MiniMax-M3",
                  system=None):
    body = json.dumps({
        "model": model,
        "messages": [
            {"role":"system","content": system or SYS_TRANS},
            {"role":"user","content":text}],
        "temperature": 0.2, "max_tokens": 6000,
    }).encode()
    req = urllib.request.Request(f"{base}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read().decode())
    out = data["choices"][0]["message"]["content"]
    return re.sub(r"</?think>.*?</think>", "", out, flags=re.S).strip()

def translate_md(md_path, key):
    text = md_path.read_text(encoding="utf-8", errors="replace")
    parts = []
    for c in chunk_md(text):
        out = None
        for attempt in range(3):
            try:
                out = llm_translate(c, key); break
            except Exception as e:
                if attempt == 2: raise
                time.sleep(3)
        # 块级回退校验：输入是英文正文块、输出却几乎无中文 → 判为「整块回退成原文」。
        # MiniMax-M3 会过度应用 system prompt 中「出版社样板可保留英文」的豁免，把正文块
        # 整块回退（实测全库 269 篇译文有 13 篇中招，表现为译文里连续若干段英文原文）。
        # 此处用 SYS_TRANS_STRICT 补救重试；仍失败则显式告警，不再静默产出半英半中的译文。
        if out is not None and cn_ratio(c) < 0.05 and cn_ratio(out) < 0.15:
            fixed = None
            for _ in range(2):
                try:
                    cand = llm_translate(c, key, system=SYS_TRANS_STRICT)
                    if cn_ratio(cand) >= 0.15:
                        fixed = cand
                        break
                except Exception:
                    pass
                time.sleep(3)
            if fixed is not None:
                log(f"↻ 块回退已补救（中文占比 {cn_ratio(out):.0%} → {cn_ratio(fixed):.0%}）")
                out = fixed
            else:
                log(f"⚠ 块翻译疑似整块回退为原文（中文占比仅 {cn_ratio(out):.0%}），补救未生效")
        parts.append(out)
    return "\n".join(parts)

def first_h1(text):
    for ln in text.splitlines():
        if re.match(r"^#\s+\S", ln) and not ln.startswith("#!"):
            return re.sub(r"[#*\s]", "", ln).strip()
    return None

def process_one(pdf: Path, key):
    """处理单篇：返回 (folder, err)"""
    stem = pdf.name
    mdir = re.match(r"^\[(.+?)\]\s*(.+)\.pdf$", stem)
    if not mdir:
        return None, f"命名不识别: {stem}"
    direction = mdir.group(1)
    if direction not in DIRS:
        return None, f"方向未知({direction}): {stem}"
    raw = mdir.group(2)

    work = Path("/tmp") / "v2_work"
    work.mkdir(exist_ok=True)
    # 1) MinerU 云 OCR
    outd = work / sanitize(raw)
    if outd.exists(): shutil.rmtree(outd)
    from mineru_client import precision_extract
    rc = precision_extract(pdf, outd, model="vlm")
    if rc != 0:
        return None, f"OCR失败: {stem}"
    full = outd / "full.md"
    if not full.exists():
        return None, f"OCR无full.md: {stem}"
    # 2) 翻译
    t = translate_md(full, key)
    title = sanitize(first_h1(t) or raw)
    # 标题仍为英文 → 单独中译标题，保证论文夹全中文规范（并同步替换 md 首行）
    import re as _re
    if title and not _re.search(r"[\u4e00-\u9fff]", title):
        try:
            zh = llm_translate(f"只翻译以下论文标题为简体中文（不加引号与句号，专有名词保留英文）：{title}", key)
            zh = sanitize(zh.split("\n")[0])
            if _re.search(r"[\u4e00-\u9fff]", zh):
                title = zh
                t = _re.sub(r"^#\s+.+$", f"# {zh}", t, count=1, flags=_re.M)
        except Exception:
            pass
    if not title:
        return None, f"译文无标题: {stem}"
    base = ROOT / direction
    folder = base / title
    # 判重：与译名无关的强信号（PDF 内容哈希 / arXiv 编号）全库比对。
    # 旧逻辑只比 LLM 译名，译名一抖动就重复建夹（ESPO 曾出现 5 个变体夹）。
    try:
        from paper_index import find_duplicate
        dup, why = find_duplicate(pdf)
        if dup:
            return None, f"重复论文({why})，已在 {dup.parent.parent.name}/{dup.parent.name}"
    except Exception as e:
        log(f"⚠ 判重跳过({e}): {pdf.name}")
    if folder.exists():
        return None, f"目标夹已存在(跳过): {direction}/{title}"
    folder.mkdir(parents=True)
    # 3) 组夹
    shutil.copy2(pdf, folder / f"{title}.pdf")
    ft = folder / f"{title}_全文翻译.md"
    # 图片引用重写 images/ -> {title}/images/  (解析目录以 title 命名放夹内)
    parse_dir = folder / title
    if parse_dir.exists(): shutil.rmtree(parse_dir)
    shutil.move(str(outd), str(parse_dir))
    md_text = t.replace("images/", f"{title}/images/")  # 指向夹内解析目录
    ft.write_text(md_text, encoding="utf-8")
    # 4) 导读：复用池内已有导读 else 生成
    guide_src = PENDING / f"[{direction}] {raw}_翻译导读.md"
    gdst = folder / f"{title}_翻译导读.md"
    if guide_src.exists():
        shutil.move(str(guide_src), str(gdst))
        guide_from = "复用池导读"
    else:
        from generate_guides import gen_guide
        try:
            gen_guide(folder, direction)
            guide_from = "新生成"
        except Exception as e:
            guide_from = f"导读生成失败({e})"
    # 5) 从池移除 pdf
    pdf.unlink(missing_ok=True)
    log(f"✅ {direction}/{title} | {guide_from} | 原文件 {raw}.pdf")
    return folder, None

def run(key, workers=3, limit=0):
    state = load_state()
    done = state.get("done", {})
    pending = sorted(PENDING.glob("*.pdf"))
    todo = [p for p in pending if p.name not in done]
    if limit and len(todo) > limit:
        todo = todo[:limit]
    log(f"池内PDF: {len(pending)} | 已完成: {len(done)} | 本轮待处理: {len(todo)}")
    import threading
    lock = threading.Lock()
    results = []
    def work(sub):
        for p in sub:
            try:
                folder, err = process_one(p, key)
            except Exception as e:
                folder, err = None, f"异常: {e}"
            with lock:
                if folder:
                    done[p.name] = str(folder)
                    save_state({"done": done})
                    results.append(("ok", p.name))
                else:
                    log(f"✗ {p.name}: {err}")
                    results.append(("fail", p.name))
    import math
    n = math.ceil(len(todo) / workers) or 1
    ts = [threading.Thread(target=work, args=(todo[i*n:(i+1)*n],))
          for i in range(workers) if todo[i*n:(i+1)*n]]
    for t in ts: t.start()
    for t in ts: t.join()
    left = len(list(PENDING.glob("*.pdf")))
    okc = sum(1 for r in results if r[0] == "ok")
    log(f"== 本轮完成: 成功 {okc} | 池剩余 {left} ==")

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    from generate_guides import api_key
    key = api_key()
    if not key:
        print("无 MiniMax key"); sys.exit(1)
    lim = 0
    if "--limit" in sys.argv:
        lim = int(sys.argv[sys.argv.index("--limit")+1])
    if "--daemon" in sys.argv:
        if os.fork() > 0: sys.exit(0)
        os.setsid()
        if os.fork() > 0: os._exit(0)
        try:
            caff = subprocess.Popen(["caffeinate", "-is"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            caff = None
        # 循环直到池空（自愈：异常时重跑）
        while True:
            left = len(list(PENDING.glob("*.pdf")))
            done = load_state().get("done", {})
            todo = [p for p in PENDING.glob("*.pdf") if p.name not in done]
            if not todo:
                log("ALL_POOL_DONE"); break
            try:
                run(key, workers=3, limit=10)
            except Exception as e:
                log(f"循环异常: {e}; 10s后重试")
                time.sleep(10)
        if caff: caff.terminate()
    elif len(sys.argv) > 2 and sys.argv[1] == "--test":
        p = Path(sys.argv[2])
        folder, err = process_one(p, key)
        print("test →", folder, err)
    else:
        run(key, workers=3, limit=lim)
