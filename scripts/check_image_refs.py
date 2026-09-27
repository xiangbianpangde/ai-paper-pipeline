#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全库图片引用完整性巡检。

扫描论文库内每篇的 `_全文翻译.md` / `_翻译导读.md`，抽取 markdown 图片引用
`![alt](path)`，按「相对当前 md 文件所在目录」解析，判断目标文件是否存在。

输出分类：
  OK            引用可解析且实体存在
  FIXABLE       引用路径写错，但同名图片能在本夹内别处找到（可自动重写）
  LOST          本夹内根本找不到该图片（真丢失，需人工核查图源）
  REMOTE        http(s) 外链，跳过
  ABS_ESCAPE    绝对路径/越出夹目录，跳过（人工确认）

用法：
  python3 check_image_refs.py [--root DIR] [--json OUT] [--types guide,trans]
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

DEFAULT_ROOT = "/Users/xbpd/Documents/xbpd_obsidian/02. 🟡 归类 Arrange/论文"

# 图片引用抽取（本文件是唯一实现，其他脚本一律 import 这里）
#
# 这段逻辑踩过两次坑，改之前务必看清：
#   ① 路径含**空格**：用 `[^)\s]+` 会在空格处截断 → 曾把 3062 条引用误判出 1139 条断链。
#   ② 路径含 **ASCII 括号**：
#      - 配平的 `Language Models (Mostly)…/images/x.jpg`：用 `[^)]+?` 会在第一个 `)` 截断
#        → 曾把 108 条好引用误判成 LOST；
#      - 不配平的 `…_(InstructGPT/auto/images/x.jpg`：改用括号配平扫描后又会把整行吞掉
#        → 曾把 47 条误判成 LOST。
#   两种规则各自能修一边、坏另一边，所以最终方案是**多候选 + 磁盘校验**：
#   同一个 `![alt](` 起点给出 3 种候选切法，由调用方按「哪个在磁盘上存在」来选，
#   全都不存在时再退回最合理的那一种（配平切法）用于分类报告。
HTML_IMG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']")
_TITLE_RE = re.compile(r"^(.*?)\s+(?:\"[^\"]*\"|'[^']*')\s*$")
_MD_IMG_START = re.compile(r"!\[[^\]]*\]\(")


def _cut_first_paren(text, i):
    """切到第一个 `)`（朴素规则）——处理括号不配平的路径时用这个候选。"""
    j = text.find(")", i)
    return text[i:j].strip() if j != -1 else text[i:].strip()


def _cut_balanced(text, i):
    """括号配平切法——处理 `(Mostly)` 这类配平括号时用这个候选。"""
    depth, j = 0, i
    while j < len(text):
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        j += 1
    return text[i:j].strip()


def _cut_line_last_paren(text, i):
    """切到本行最后一个 `)`——处理「路径含空格且行尾即链接结束」的保守候选。"""
    nl = text.find("\n", i)
    seg = text[i:nl] if nl != -1 else text[i:]
    j = seg.rfind(")")
    return seg[:j].strip() if j != -1 else seg.strip()


def iter_img_links(text):
    """产出每个 markdown 图片引用：{"start": 起点, "cands": [候选路径…]}（已去重、按优先级）。"""
    for m in _MD_IMG_START.finditer(text):
        i = m.end()
        while i < len(text) and text[i] in " \t":
            i += 1
        if i >= len(text):
            continue
        if text[i] == "<":                        # CommonMark 尖括号写法 ![alt](<a b.jpg>)
            j = text.find(">", i)
            if j == -1:
                continue
            yield {"start": m.start(), "cands": [text[i + 1:j].strip()]}
            continue
        cands, seen = [], set()
        for raw in (_cut_balanced(text, i), _cut_first_paren(text, i),
                    _cut_line_last_paren(text, i)):
            ttl = _TITLE_RE.match(raw)
            if ttl:
                raw = ttl.group(1)
            if raw and raw not in seen:
                seen.add(raw)
                cands.append(raw)
        if cands:
            yield {"start": m.start(), "cands": cands}


def all_refs(text):
    """向后兼容：返回每个图片引用的「首选候选」（配平切法），按出现顺序。"""
    return [lk["cands"][0] for lk in iter_img_links(text)]


IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp")


def best_candidate(cands):
    """在「磁盘上都不存在」的情况下，挑最像真实路径的候选。

    判据：basename 带图片扩展名 > 含 `/` > 更短。
    用于区分「配平切法吞掉整行」与「朴素切法截断在括号处」这两种坏候选。
    """
    def score(c):
        base = os.path.basename(c.rstrip(")"))
        return (0 if base.lower().endswith(IMG_EXTS) else 1, 0 if "/" in c else 1, len(c))

    return sorted(cands, key=score)[0]


def scan_refs(text, folder):
    """产出一组 [(ref, exists)] —— ref 为经磁盘校验选定的写法。

    markdown 引用逐个做候选选择；HTML <img src> 直接按原样校验。
    """
    out = []
    for lk in iter_img_links(text):
        chosen = None
        for c in lk["cands"]:
            if os.path.exists(resolve_target(folder, c)):
                chosen = c
                break
        if chosen is None:
            chosen = best_candidate(lk["cands"])
        out.append((chosen, chosen is not None and
                    os.path.exists(resolve_target(folder, chosen))))
    for m in HTML_IMG_RE.finditer(text):
        r = m.group(1).strip()
        out.append((r, os.path.exists(resolve_target(folder, r))))
    return out


def extract_refs(md_path, folder=None):
    """抽取引用列表。给了 folder 就做候选校验（推荐），否则用配平切法的首选候选。"""
    try:
        with open(md_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    if folder is None and os.path.isdir(os.path.dirname(md_path)):
        folder = os.path.dirname(md_path)
    if folder:
        return [r for r, _ in scan_refs(text, folder)]
    return [r for r in all_refs(text) if r]


def resolve_target(folder, ref):
    """把引用解析为磁盘路径。兼容 URL 编码（%20/%28/%29）与反斜杠转义。"""
    r = ref
    if "%" in r:
        try:
            from urllib.parse import unquote
            r = unquote(r)
        except Exception:
            pass
    r = r.replace("\\(", "(").replace("\\)", ")")
    return os.path.normpath(os.path.join(folder, r))


def walk_library(root, types):
    suffixes = []
    if "guide" in types:
        suffixes.append("_翻译导读.md")
    if "trans" in types:
        suffixes.append("_全文翻译.md")
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(".md") and any(fn.endswith(s) for s in suffixes):
                yield os.path.join(dirpath, fn)


_INDEX_CACHE: dict = {}


def build_image_index(folder):
    """夹内所有图片文件：basename -> [相对夹根的路径...]

    带按夹缓存：同一夹的导读与译文会各自调用一次，
    全库 300+ 夹时重复遍历会明显拖慢（曾让整轮扫描超出命令超时被 SIGTERM）。
    """
    key = os.path.realpath(folder)
    hit = _INDEX_CACHE.get(key)
    if hit is not None:
        return hit
    idx = defaultdict(list)
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"):
                rel = os.path.relpath(os.path.join(dirpath, fn), folder)
                idx[fn].append(rel)
    _INDEX_CACHE[key] = idx
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--json", default="/tmp/image_refs_report.json")
    ap.add_argument("--types", default="guide,trans")
    args = ap.parse_args()

    types = [t.strip() for t in args.types.split(",") if t.strip()]
    stats = Counter()
    by_folder = defaultdict(Counter)
    details = []

    for md_path in walk_library(args.root, types):
        folder = os.path.dirname(md_path)
        img_idx = build_image_index(folder)
        try:
            with open(md_path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for ref, exists in scan_refs(text, folder):
            low = ref.lower()
            if low.startswith(("http://", "https://", "data:")):
                stats["REMOTE"] += 1
                continue
            if os.path.isabs(ref) or ref.startswith("~"):
                stats["ABS_ESCAPE"] += 1
                details.append({"md": md_path, "ref": ref, "verdict": "ABS_ESCAPE"})
                continue
            if exists:
                stats["OK"] += 1
                continue
            base = os.path.basename(ref)
            cands = img_idx.get(base, [])
            if len(cands) == 1:
                verdict = "FIXABLE"
                fix = cands[0]
            elif len(cands) > 1:
                verdict = "FIXABLE"
                fix = sorted(cands, key=len)[0]
            else:
                verdict = "LOST"
                fix = None
            stats[verdict] += 1
            by_folder[os.path.relpath(folder, args.root)][verdict] += 1
            details.append({"md": os.path.relpath(md_path, args.root), "ref": ref,
                            "verdict": verdict, "fix": fix})

    total_ref = sum(stats[k] for k in ("OK", "FIXABLE", "LOST"))
    print("=" * 62)
    print(f"图片引用巡检 — 根目录 {args.root}")
    print("=" * 62)
    print(f"内含图片的 md 扫描完成，共解析引用 {total_ref + stats['REMOTE'] + stats['ABS_ESCAPE']} 条")
    print(f"  OK          {stats['OK']:5d}")
    print(f"  FIXABLE     {stats['FIXABLE']:5d}   (路径写错，图在夹内 → 可自动重写)")
    print(f"  LOST        {stats['LOST']:5d}   (夹内无此图 → 真丢失)")
    print(f"  REMOTE      {stats['REMOTE']:5d}   (外链，跳过)")
    print(f"  ABS_ESCAPE  {stats['ABS_ESCAPE']:5d}   (绝对路径，跳过)")
    broken = stats["FIXABLE"] + stats["LOST"]
    if total_ref:
        print(f"  断链合计     {broken:5d} / {total_ref}  ({broken / total_ref * 100:.1f}%)")

    if by_folder:
        print("\n按夹分布（断链 > 0 的夹）：")
        for folder, c in sorted(by_folder.items(), key=lambda kv: -(kv[1]["FIXABLE"] + kv[1]["LOST"])):
            print(f"  {c['FIXABLE'] + c['LOST']:4d}  ({c['FIXABLE']} 可修复 / {c['LOST']} 丢失)  {folder}")

    with open(args.json, "w", encoding="utf-8") as f:
        json.dump({"stats": dict(stats), "by_folder": {k: dict(v) for k, v in by_folder.items()},
                   "details": details}, f, ensure_ascii=False, indent=1)
    print(f"\n明细已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
