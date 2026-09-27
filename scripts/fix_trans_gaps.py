#!/usr/bin/env python3
"""fix_trans_gaps.py — 修复译文里被整块回退为英文原文的正文段落。

缺陷背景
--------
`translate_v2.llm_translate` 的 system prompt 为了让机构库封面/版权样板文字保持英文，
显式豁免了「出版社样板文字」。MiniMax-M3 会**过度应用**这条豁免，把部分**正文块**
整块回退成英文原文（实测全库 269 篇译文检出 13 篇，约 4.8%；表现是译文里出现
3~5 段连续英文原文，且连章节标题一起未译）。

本脚本做块级重译：定位连续英文正文段 → 单独重译 → 就地替换 → 备份可回滚。

判据对以下内容权威排除（它们保留英文是**正确**状态，不是缺陷）：
封面机构页、DOI/Citation/Copyright/Funding 声明、参考文献条目、表格/代码/链接行。

用法
----
  python3 scripts/fix_trans_gaps.py <论文夹目录> [<夹目录> ...]   # dry-run
  python3 scripts/fix_trans_gaps.py <夹目录> --apply
  python3 scripts/fix_trans_gaps.py --restore /tmp/trans_fix_backup_<ts>/manifest.json
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
from generate_guides import api_key  # noqa: E402

CJK = re.compile(r"[\u4e00-\u9fff]")
# 保留英文属正确状态的行（封面/声明/参考文献）—— 绝不纳入重译
FRONT = re.compile(
    r"^(Citation:|Copyright|Funding:|doi:|DOI|https?://|arXiv:|<sup>|Received|Accepted|"
    r"Published|Keywords?:|Cite as|Author|Table\s+\d+:|Figure\s+\d+:|\[?\d{1,3}[\].\)]\s)")
HEADING = re.compile(r"^#{1,6}\s+\S")
TOK = re.compile(r"[A-Za-z][A-Za-z-]{2,}")


def cn_ratio(t: str) -> float:
    return len(CJK.findall(t)) / max(1, len(t))


def is_en_para(t: str) -> bool:
    """叙述性英文段落 → 应当被译成中文。"""
    t = t.strip()
    if len(t) < 120 or FRONT.match(t):
        return False
    if re.match(r'^"[\w_]+"\s*:', t):              # JSON 数据片段，翻译会破坏结构
        return False
    if cn_ratio(t) >= 0.05:
        return False
    if re.search(r"[<>{}]|https?://|`", t):        # 表格 / 代码 / 链接
        return False
    if len(re.findall(r"[A-Za-z][A-Za-z'-]+", t)) < 25:
        return False
    return len(re.findall(r"\b(the|of|and|that|with|for|are|is|in|to)\b", t, re.I)) >= 8


def next_nonblank_is_zh(lines: list[str], i: int) -> bool:
    """EN 段 i 之后最近的第一个非空行，是否为「中文正文段」。"""
    for j in range(i + 1, min(i + 4, len(lines))):
        if not lines[j].strip():
            continue
        return cn_ratio(lines[j]) >= 0.15 and len(lines[j].strip()) >= 100
    return False


def find_blocks(lines: list[str]) -> tuple[list[int], list[tuple[int, int]]]:
    """→ (drops, blocks)：drops = 「原文残留」应删行号；blocks = 「真漏译」待重译区间。

    两种形态的处理方式**相反**，判错要么删掉正文、要么重译出重复段：
    - **真漏译**：连续 ≥2 段英文（互为邻居，间隔 ≤3 行）→ 后方没有对应译文，应**重译**。
    - **原文残留**：**孤立**英文段（前后都不是英文段）且下一个非空行就是中文正文
      → 译文已产出、原文被冗余插入，应**删除**。
    ⚠ 不要用「EN 段与中文段的拉丁 token 交集」当判据：译文把术语全译成中文时交集为 0
      （如 `To summarize…` ↔ `综上所述…`），正常对照又可能恰有 2 个交集 token
      （`same-category`），两种情况实测都会判错。
    """
    hits = [i for i, ln in enumerate(lines) if is_en_para(ln)]
    if not hits:
        return [], []
    groups, cur = [], [hits[0]]
    for i in hits[1:]:
        if i - cur[-1] <= 3:                        # 允许中间夹空行
            cur.append(i)
        else:
            groups.append(cur)
            cur = [i]
    groups.append(cur)
    drops, todo = [], []
    for g in groups:
        if len(g) >= 2:                             # 连续多段 → 整组真漏译
            todo.extend(g)
        elif next_nonblank_is_zh(lines, g[0]):      # 孤立段 + 紧邻中文 → 原文残留
            drops.append(g[0])
        else:
            todo.append(g[0])
    if not todo:
        return drops, []
    groups, cur = [], [todo[0]]
    for i in todo[1:]:
        if i - cur[-1] <= 3:                        # 允许中间夹空行
            cur.append(i)
        else:
            groups.append(cur)
            cur = [i]
    groups.append(cur)
    out = []
    for g in groups:
        lo, hi = g[0], g[-1]
        # 吸收紧邻的未译标题行（章节标题短，不会被 is_en_para 命中，但同样必须译）
        for idx in (hi + 1, hi + 2):
            if idx < len(lines) and HEADING.match(lines[idx].strip()) \
                    and cn_ratio(lines[idx]) < 0.05:
                hi = idx
        for idx in (lo - 1, lo - 2):
            if idx >= 0 and HEADING.match(lines[idx].strip()) and cn_ratio(lines[idx]) < 0.05:
                lo = idx
        out.append((lo, hi))
    return drops, out


def merge_blocks(blocks: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """吸收标题行后区间可能交叠 —— 合并之，避免重复重译同一段。"""
    if not blocks:
        return []
    blocks = sorted(blocks)
    merged = [blocks[0]]
    for lo, hi in blocks[1:]:
        p_lo, p_hi = merged[-1]
        if lo <= p_hi + 1:
            merged[-1] = (p_lo, max(p_hi, hi))
        else:
            merged.append((lo, hi))
    return merged


def llm_translate_block(text: str, key: str) -> str:
    """块级重译：与 translate_v2 同源，但明确「这是正文，必须译中文」。"""
    import urllib.request
    body = json.dumps({
        "model": "MiniMax-M3",
        "messages": [
            {"role": "system", "content":
             "你是资深 AI 论文中英翻译。下面给你的是论文**正文**的 Markdown 片段，"
             "请完整译为简体中文：不省略、不漏段、不保留英文原文段落。"
             "保留全部 Markdown 结构（标题层级/列表/引用/表格/代码块）、图片引用(![](...))、"
             "LaTeX 公式与 HTML 表格原样；术语首次出现保留英文原文；"
             "章节标题也要译成中文。只输出译文本身，不要任何解释或前后缀。"},
            {"role": "user", "content": text}],
        "temperature": 0.2, "max_tokens": 6000,
    }).encode()
    req = urllib.request.Request(
        "https://api.minimaxi.com/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read().decode())
    out = data["choices"][0]["message"]["content"]
    return re.sub(r"</?think>.*?</think>", "", out, flags=re.S).strip()


def short_name(p: Path) -> str:
    return hashlib.md5(str(p).encode()).hexdigest()[:16]


def plan(folders: list[Path]) -> list[dict]:
    items = []
    for folder in folders:
        if not folder.is_dir():
            print(f"  ⚠ 跳过非目录: {folder}")
            continue
        for ft in sorted(folder.rglob("*_全文翻译.md")):
            lines = ft.read_text(encoding="utf-8", errors="ignore").split("\n")
            drops, blocks = find_blocks(lines)
            blocks = merge_blocks(blocks)
            if not drops and not blocks:
                continue
            items.append({"md": str(ft), "blocks": blocks, "drops": drops, "lines": lines,
                          "ren": sum(1 for l in lines if is_en_para(l))})
    return items


def apply(items: list[dict], key: str) -> None:
    ts = time.strftime("%Y%m%d_%H%M%S")
    bdir = Path(f"/tmp/trans_fix_backup_{ts}")
    bdir.mkdir(parents=True, exist_ok=True)
    index = {}
    n_trans = n_drop = 0
    for it in items:
        md = Path(it["md"])
        shutil.copy2(md, bdir / f"{short_name(md)}.md")
        index[short_name(md)] = str(md)
        lines = it["lines"]
        ops = [(lo, hi, "drop") for lo, hi in it.get("drops", [])]
        ops += [(lo, hi, "trans") for lo, hi in it["blocks"]]
        ops.sort(key=lambda o: (o[0], o[1]), reverse=True)   # 自后向前 → 行号不漂移
        for lo, hi, kind in ops:
            if kind == "drop":
                end = hi + 1 if hi + 1 < len(lines) and not lines[hi + 1].strip() else hi
                del lines[lo:end + 1]
                n_drop += 1
                print(f"    － [{md.parent.name[:32]}] 行{lo}-{hi} 删除冗余英文原文段")
                continue
            src = "\n\n".join(l.strip() for l in lines[lo:hi + 1] if l.strip())
            if not src:
                continue
            try:
                out = llm_translate_block(src, key)
            except Exception as e:
                print(f"    ✗ 重译失败 [{md.parent.name[:32]}] 行{lo}-{hi}: "
                      f"{type(e).__name__}: {e}")
                continue
            if not out or cn_ratio(out) < 0.15:
                print(f"    ✗ 重译结果中文占比过低({cn_ratio(out):.0%})，保留原文")
                continue
            new = []
            for seg in out.split("\n\n"):
                seg = seg.strip()
                if seg:
                    new.extend([seg, ""])
            lines[lo:hi + 1] = new
            n_trans += 1
            print(f"    ✓ [{md.parent.name[:32]}] 行{lo}-{hi} 重译 {len(src)}B → {len(out)}B")
        md.write_text("\n".join(lines), encoding="utf-8")
    (bdir / "manifest.json").write_text(
        json.dumps({"time": ts, "index": index,
                    "blocks_retranslated": n_trans, "dropped_residue": n_drop},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n重译 {n_trans} 块 | 删冗余 {n_drop} 段 → 备份 {bdir}（manifest.json 可回滚）")


def restore(manifest: str) -> None:
    mf = Path(manifest)
    data = json.loads(mf.read_text(encoding="utf-8"))
    bdir = mf.parent
    n = 0
    for h, orig in data["index"].items():
        src = bdir / f"{h}.md"
        if src.exists():
            shutil.copy2(src, orig)
            n += 1
    print(f"已回滚 {n} 个译文 → 原始位置")


def main() -> int:
    ap = argparse.ArgumentParser(description="修复译文里整块回退为英文的正文段")
    ap.add_argument("folders", nargs="*", help="论文夹目录")
    ap.add_argument("--apply", action="store_true", help="执行重译并写回")
    ap.add_argument("--restore", type=str, default="", help="从 manifest 回滚")
    a = ap.parse_args()

    if a.restore:
        restore(a.restore)
        return 0
    if not a.folders:
        ap.print_help()
        return 2

    folders = [Path(p).expanduser().resolve() for p in a.folders]
    items = plan(folders)
    total_blocks = sum(len(i["blocks"]) for i in items)
    total_drops = sum(len(i.get("drops", [])) for i in items)
    print(f"扫描 {len(folders)} 个夹 | 命中 {len(items)} 篇 | "
          f"待重译块 {total_blocks} | 待删冗余原文段 {total_drops}\n")
    for it in items:
        md = Path(it["md"])
        spans = ", ".join(f"行{lo}-{hi}" for lo, hi in it["blocks"])
        print(f"  [{md.parent.parent.name}] {md.parent.name[:46]}")
        print(f"      英文段 {it['ren']} | 真漏译块 {len(it['blocks'])}"
              f"{' (' + spans + ')' if spans else ''}")
        if it.get("drops"):
            print(f"      原文残留（→删除，勿重译）行号: {it['drops']}")
    if not a.apply:
        print("\n（dry-run，未改动；加 --apply 执行）")
        return 0

    key = api_key()
    if not key:
        print("✗ 缺少 MiniMax API key（.secrets/minimax.json）")
        return 1
    apply(items, key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
