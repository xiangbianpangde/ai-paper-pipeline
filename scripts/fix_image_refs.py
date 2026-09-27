#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_image_refs.py — 修复论文库译文/导读里的「图片路径写错」型断链。

背景（2026-09-17 巡检结论）：
  全库 3062 条图片引用中 497 条断链，其中
    445 条 = 路径写错（图片实体就在本夹内，只是前缀不对）
    52 条  = 真丢失（本夹内根本没有该图，需重译或人工核图源）
  路径写错的成因是历史遗留的三种前缀：
    `images/x.jpg`                —— 早年无前缀写法
    `<纯中文旧夹名>/images/x.jpg`  —— 夹名后来规范化为「English：中文」，译文没跟上
    `<slug>/auto/images/x.jpg`     —— 旧云管线布局

修复方式：按夹内图片索引定位实体，把引用整体重写为「相对夹根的实体路径」，
        即 `<当前夹名>/images/<basename>`（或 auto/hybrid_auto 布局下的实际位置）。

用法：
  python3 fix_image_refs.py                # dry-run，只预览
  python3 fix_image_refs.py --apply        # 执行（写 manifest，可回滚）
  python3 fix_image_refs.py --restore      # 按 manifest 回滚
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from check_image_refs import (  # noqa: E402
    DEFAULT_ROOT, build_image_index, resolve_target, scan_refs, walk_library,
)

MANIFEST = Path("/tmp/fix_image_refs_manifest.json")
MANIFEST_ENC = Path("/tmp/fix_image_refs_encode_manifest.json")


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def enc_ref(ref: str) -> str:
    """把路径里的 **ASCII 括号** 百分号编码。

    括号不配平的路径（如 `…_(InstructGPT/auto/images/x.jpg`）会让朴素 markdown
    解析器（Obsidian、日报渲染器）直接认不出这个链接 —— 编码成 %28/%29 后人人可解析，
    巡检侧的 resolve_target() 会自动解码回来。
    """
    return ref.replace("(", "%28").replace(")", "%29")


def plan(root: str, types: list, limit_folders: set | None = None) -> list:
    """产出待改写清单：[{md, old, new, folder}]"""
    items = []
    for md_path in walk_library(root, types):
        folder = os.path.dirname(md_path)
        rel_folder = os.path.relpath(folder, root)
        if limit_folders and rel_folder not in limit_folders:
            continue
        img_idx = build_image_index(folder)
        try:
            text = Path(md_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        seen = set()
        for ref, exists in scan_refs(text, folder):
            ref = ref.strip().strip("<>")
            if not ref or ref in seen:
                continue
            seen.add(ref)
            if ref.startswith(("http://", "https://", "data:", "/", "~")):
                continue
            if exists:
                continue
            cands = img_idx.get(os.path.basename(ref.rstrip(")")), [])
            if not cands:
                continue  # LOST：本方案不处理
            new = sorted(cands, key=len)[0].replace(os.sep, "/")
            new = enc_ref(new)
            if new == ref:
                continue
            items.append({"md": md_path, "old": ref, "new": new.replace(os.sep, "/"),
                          "folder": rel_folder})
    return items


def apply_items(items: list, manifest: Path = MANIFEST) -> Path:
    man = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "file": str(manifest), "rewritten": []}
    by_md = {}
    for it in items:
        by_md.setdefault(it["md"], []).append(it)
    for md_path, group in by_md.items():
        p = Path(md_path)
        text = p.read_text(encoding="utf-8")
        before = text
        applied = []
        for it in group:
            old = it["old"]
            if old not in text:
                continue
            # 只替换「整段 markdown 引用目标」，不做裸子串替换：
            # 否则 `images/x.jpg` 会命中 `<夹名>/images/x.jpg` 里的同一子串，
            # 把本来正确的引用改成双前缀（曾一次性造成 110 条新断链）。
            pattern = r"(!\[[^\]]*\]\()\s*" + re.escape(old) + r"(\s*\))"
            text, n = re.subn(pattern, lambda m: m.group(1) + it["new"] + m.group(2), text)
            if not n:
                # 非 markdown 写法（HTML src、裸链接）才退回整串替换
                text = text.replace(old, it["new"])
            applied.append({"old": old, "new": it["new"]})
        if text != before:
            p.write_text(text, encoding="utf-8")
            man["rewritten"].append({"md": md_path, "refs": applied})
            log(f"✎ {len(applied):3d} 条  {os.path.relpath(md_path, DEFAULT_ROOT)[:88]}")
    manifest.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"共改写 {len(man['rewritten'])} 个文件 / "
        f"{sum(len(r['refs']) for r in man['rewritten'])} 条引用 → {manifest}")
    return manifest


def plan_encode(root: str, types: list) -> list:
    """列出「路径含 ASCII 括号、但还没编码」的引用（第二类修复）。

    为什么必须编码：路径里的括号会让朴素 markdown 解析器（Obsidian 的部分实现、
    日报渲染器的 `[^)]+`）认不出链接；不配平的括号更是彻底无法解析。
    编码成 %28/%29 后所有解析器都能识别，巡检侧 resolve_target() 自动解码。
    """
    items = []
    for md_path in walk_library(root, types):
        folder = os.path.dirname(md_path)
        try:
            text = Path(md_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        seen = set()
        for ref, _ in scan_refs(text, folder):
            r = ref.strip().strip("<>")
            if not r or r in seen or r.startswith(("http://", "https://", "data:")):
                continue
            seen.add(r)
            enc = enc_ref(r)
            if enc != r:
                items.append({"md": md_path, "old": r, "new": enc,
                              "folder": os.path.relpath(folder, root)})
    return items


def restore(mf: Path = MANIFEST) -> None:
    man = json.loads(mf.read_text(encoding="utf-8"))
    n = 0
    for r in man["rewritten"]:
        p = Path(r["md"])
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        for it in r["refs"]:
            text = text.replace(it["new"], it["old"])
            n += 1
        p.write_text(text, encoding="utf-8")
    log(f"↩ 已回滚 {n} 条引用")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--types", default="guide,trans")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--encode-parens", action="store_true",
                    help="第二类修复：把路径里的 ASCII 括号百分号编码（不配平括号必须编码才能被解析）")
    a = ap.parse_args()

    types = [t.strip() for t in a.types.split(",") if t.strip()]
    if a.restore:
        restore(MANIFEST_ENC if a.encode_parens else MANIFEST)
        return 0

    if a.encode_parens:
        items = plan_encode(a.root, types)
        log(f"含 ASCII 括号待编码 {len(items)} 条")
        if not a.apply:
            for it in items[:10]:
                print(f"   {it['folder'][:60]}")
                print(f"     {it['old'][:96]}")
            log("（dry-run，未写盘；加 --apply 执行）")
            return 0
        apply_items(items, MANIFEST_ENC)
        return 0

    items = plan(a.root, types)
    folders = {}
    for it in items:
        folders.setdefault(it["folder"], 0)
        folders[it["folder"]] += 1
    log(f"可改写 {len(items)} 条，涉及 {len(folders)} 个夹")
    for f, c in sorted(folders.items(), key=lambda kv: -kv[1])[:15]:
        print(f"   {c:4d}  {f[:96]}")
    if not a.apply:
        log("（dry-run，未写盘；加 --apply 执行）")
        return 0
    apply_items(items)
    return 0


if __name__ == "__main__":
    sys.exit(main())
