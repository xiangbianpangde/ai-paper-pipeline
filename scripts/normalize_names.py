#!/usr/bin/env python3
"""normalize_names.py — 论文夹名规范化为「英文原名：中文标题」

库内历史命名不统一：部分夹名带英文原名前缀（house style），部分纯中文。
英文原名来源：解析目录里 MinerU 解析出的**原始英文 markdown** 的 H1。

改名会同步处理夹内三件套与解析目录，保证「夹名 = 内部文件名」的格式约束。
全程写 manifest，可一键回滚。

用法:
  python3 scripts/normalize_names.py                 # dry-run
  python3 scripts/normalize_names.py --apply         # 执行
  python3 scripts/normalize_names.py --restore <mf>  # 回滚
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from complete_paper_folder import ROOT, DIRS, paper_folders, survey   # noqa: E402

ILLEGAL = re.compile(r'[\\/*?"<>|\x00-\x1f]')   # 注意: ':' 先转全角再过滤
MAXBYTES = 180                                   # macOS 单段上限 255 字节，留余量（中文 3B/字）


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def english_title(folder: Path) -> str | None:
    """从解析目录的原始英文 markdown 取 H1（MinerU 解析件是原文，非译文）。"""
    s = survey(folder)
    md = s["full_md"]
    if not md:
        return None
    try:
        lines = md.read_text(encoding="utf-8", errors="replace").splitlines()[:60]
    except OSError:
        return None
    for ln in lines:
        ln = ln.strip()
        if not ln.startswith("# "):
            continue
        # 必须先剥 HTML 标签：MinerU 会把 <sub>/<sup> 嵌进词内，如
        # `# Th<sub>e</sub> R<sub>ou</sub>t<sub>er</sub> Withi<sub>n:</sub> …`
        # 不剥的话，后续 ILLEGAL 把 < > 转空格，会得到
        # `Th sub e sub R sub ou sub t sub er sub Withi sub n` 这种垃圾名。
        t = re.sub(r"<[^>]+>", "", ln[2:])
        t = re.sub(r"[#*`]", "", t).strip()
        if len(t) < 8 or len(t) > 200:
            continue
        if re.search(r"[\u4e00-\u9fff]", t):        # 含中文 → 不是英文原名
            continue
        if not re.search(r"[A-Za-z]{4}", t):         # 英文词太少 → 可疑
            continue
        if re.search(r"arXiv|doi:|preprint", t, re.I):
            continue
        # 全大写标题多为 MinerU 解析伪影（还常带 LEARN-ING 这类断词），不可靠 → 放弃
        letters = [c for c in t if c.isalpha()]
        if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8:
            continue
        return re.sub(r"\s+", " ", t).strip(" .")
    return None


def strip_english_prefix(cn: str) -> str:
    """剥掉中文名里已有的前置标识（可能是英文/希腊字母/数字，如 τ-bench：、LLM²：）。"""
    m = re.match(r"^([^\u4e00-\u9fff]{1,40}?)[：:]\s*(\S.*)$", cn)
    if m and re.search(r"[A-Za-z0-9\u0370-\u03ff\u0400-\u04ff]", m.group(1)):
        return m.group(2).strip()
    return cn


def short_en(en: str) -> str:
    """取英文主标识（首个冒号前）—— 库内 house style 是「短标识：中文标题」，
    如 MemoryART：… / Dream-RSI：…，而非整条英文标题。"""
    head = en.replace(":", "：").split("：")[0].strip(" .")
    head = head.rstrip("？?!.。… ")          # 去掉尾部标点，避免出现「Fail？：中文」式拼接
    return head if len(head) >= 3 else en


def _norm_title(s: str) -> list:
    s = s.lower().lstrip("# ").strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).split()


def titles_consistent(folder: Path) -> bool:
    """防呆：确认解析目录的英文 H1 与导读里出现过的英文标题是同一篇论文。

    走过的三段弯路：
      ① `_origin.pdf` 哈希 == 夹内 PDF —— 不成立，MinerU 的原件是重编码副本
      ② 只看「导读 H1 语言 vs 夹名语言」 —— 误伤，导读 H1 用英文只是生成器格式差异
      ③ 把「📄 <arXiv号> — 中文标题」式导读 H1 当英文标题比对 —— 含足量拉丁/数字 token
         骗过长度检查，与解析标题零交集 → 假报装配错乱（2026-09-17 实测 2 例误伤）。
         现要求参与比对的标题不得含中日韩字符。

    正解：两边都取英文标题做词集比对。实测错配夹（夹名/译文是 RAG 疾病分类论文，
    解析目录与导读却是 Med-PaLM 2）的第一词组交集为 0，而正常夹交集充分。
    无法判断时返回 True（放行）。
    """
    s = survey(folder)
    if not s["full_md"]:
        return True
    try:
        p_lines = s["full_md"].read_text(encoding="utf-8", errors="replace").splitlines()[:60]
    except OSError:
        return True
    parse_en = next((ln for ln in p_lines
                     if ln.strip().startswith("# ") and re.search(r"[A-Za-z]{4}", ln)), None)
    if not parse_en:
        return True
    g = s["guide"]
    if not g.exists():
        return True
    try:
        g_lines = g.read_text(encoding="utf-8", errors="replace").splitlines()[:12]
    except OSError:
        return True
    # 只认 H1：导读前几行还含 arXiv 链接、方向判定等，若整段扫描会把 URL 误当标题
    h1 = next((ln.strip() for ln in g_lines if ln.strip().startswith("# ")), None)
    # 导读 H1 常写成「📄 <arXiv号> — 中文标题」，含足够多拉丁/数字 token，
    # 会被 len(_norm_title(h1)) >= 4 误当成英文标题 → 与解析标题零交集 → 假报「装配错乱」。
    # 故凡含中日韩字符者一律不参与比对（解析侧同样规则）。
    guide_en = (h1 if h1 and not re.search(r"[\u4e00-\u9fff]", h1)
                and len(_norm_title(h1)) >= 4 else None)
    if not guide_en:
        return True                       # 导读 H1 是中文标题 → 无可比英文，放行
    a, b = set(_norm_title(parse_en)[:5]), set(_norm_title(guide_en)[:5])
    if not a or not b:
        return True
    return len(a & b) >= 2


def build_new_name(en: str, cn: str) -> str:
    # 冒号/问号统一全角（ASCII ':' 在 Finder 显示为 '/'，易混淆）；其余非法字符转空格
    norm = lambda s: ILLEGAL.sub(" ", s.replace(":", "：").replace("?", "？"))
    en = re.sub(r"\s+", " ", norm(short_en(en))).strip(" .：")
    cn = re.sub(r"\s+", " ", norm(strip_english_prefix(cn))).strip(" .：")
    if not cn:
        return en
    # 中文名是主标识 → 完整保留；按字节裁剪英文标识
    budget = MAXBYTES - len(cn.encode("utf-8")) - len("：".encode("utf-8"))
    if budget < 12:
        return ""            # 中文名本身就太长 → 放弃规范化，保留原名
    if len(en.encode("utf-8")) > budget:
        raw = en.encode("utf-8")[:budget - 3].decode("utf-8", errors="ignore")
        en = (raw.rsplit(" ", 1)[0] if " " in raw else raw).rstrip(" ,;：") + "…"
    return f"{en}：{cn}"


def plan() -> list:
    cands = []
    for f in sorted(paper_folders()):
        if not f.parent.is_dir():
            continue
        d = f.parent.name
        if re.match(r"^[A-Za-z]", f.name):        # 已有英文前缀
            continue
        en = english_title(f)
        if not en:
            continue
        if not titles_consistent(f):
            log(f"⚠ 解析目录与导读英文标题不符（夹装配错乱），跳过改名: {d}/{f.name}")
            continue
        new = build_new_name(en, f.name)
        if not new or new == f.name or len(new) < 6:
            continue
        dst = f.parent / new
        if dst.exists():
            log(f"⚠ 目标名冲突，跳过: {d}/{f.name} → {new}")
            continue
        cands.append({"dir": d, "src": str(f), "dst": str(dst),
                      "old": f.name, "new": new, "en": en})
    return cands


def apply_plan(cands: list) -> Path:
    man = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "renamed": []}
    for c in cands:
        src, dst = Path(c["src"]), Path(c["dst"])
        if not src.exists():
            log(f"跳过(不存在): {c['old']}")
            continue
        old, new = src.name, dst.name
        try:
            # 1) 夹内三件套与解析目录同步改名（保持「夹名=内部文件名」约束）
            moves = []
            for pat in (f"{old}.pdf", f"{old}_全文翻译.md", f"{old}_翻译导读.md"):
                p = src / pat
                if p.exists():
                    moves.append((p, src / pat.replace(old, new, 1)))
            pd = src / old
            if pd.is_dir():
                moves.append((pd, src / new))
            for a, b in moves:
                if b.exists():
                    log(f"  ⚠ 内层目标已存在，跳过: {b.name}")
                    continue
                a.rename(b)
            # 2) 目录改名
            src.rename(dst)
            # 3) 修夹内产物里的自引用路径
            #
            # ⚠ 必须连**译文**一起修：新管线（complete_paper_folder）把译文图片引用写成
            #   `![](<夹名>/images/xxx.jpg)` —— 夹名一变，图全部断链。
            #   旧管线（batch_translate）的引用是 `<slug>/auto/images/…`（与夹名无关），
            #   所以上一轮 79 个夹没暴露这个问题；新补全的夹会中招。
            for pat, label in ((f"{new}_翻译导读.md", "导读"), (f"{new}_全文翻译.md", "译文")):
                p = dst / pat
                if not p.exists():
                    continue
                t = p.read_text(encoding="utf-8")
                t2 = t.replace(str(src), str(dst)).replace(f"{old}/", f"{new}/")
                if t2 != t:
                    p.write_text(t2, encoding="utf-8")
                    log(f"  ✎ 修正{label}内自引用 ({old[:20]} → {new[:20]})")
            man["renamed"].append({"from": str(src), "to": str(dst)})
            log(f"✓ {c['dir']} | {old[:30]} → {new[:60]}")
        except Exception as e:
            log(f"✗ 改名失败 {old}: {e}")
    mf = Path("/tmp/rename_manifest.json")
    mf.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"共改名 {len(man['renamed'])} 个夹 → manifest {mf}")
    return mf


def restore(mf: Path) -> None:
    man = json.loads(mf.read_text(encoding="utf-8"))
    for r in reversed(man["renamed"]):
        src, dst = Path(r["to"]), Path(r["from"])
        if not src.exists():
            continue
        new, old = src.name, dst.name
        # 先把产物里的自引用改回去（与 apply 的步骤 3 对称），再改文件名/夹名
        for pat, label in ((f"{new}_翻译导读.md", "导读"), (f"{new}_全文翻译.md", "译文")):
            p = src / pat
            if not p.exists():
                continue
            t = p.read_text(encoding="utf-8")
            t2 = t.replace(str(src), str(dst)).replace(f"{new}/", f"{old}/")
            if t2 != t:
                p.write_text(t2, encoding="utf-8")
                log(f"  ✎ 还原{label}内自引用")
        for pat in (f"{new}.pdf", f"{new}_全文翻译.md", f"{new}_翻译导读.md"):
            p = src / pat
            if p.exists():
                p.rename(src / pat.replace(new, old, 1))
        pd = src / new
        if pd.is_dir():
            pd.rename(src / old)
        src.rename(dst)
        log(f"↩ {new[:40]} → {old[:40]}")
    log("回滚完成")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restore", type=str)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    if a.restore:
        restore(Path(a.restore))
        return 0
    cands = plan()
    if a.limit:
        cands = cands[:a.limit]
    log(f"待规范化 {len(cands)} 个夹")
    for c in cands[:20]:
        print(f"  [{c['dir']}]")
        print(f"    旧: {c['old'][:70]}")
        print(f"    新: {c['new'][:80]}")
    if len(cands) > 20:
        print(f"  … 其余 {len(cands)-20} 个")
    if a.apply:
        print()
        apply_plan(cands)
    else:
        log("dry-run 结束（加 --apply 执行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
