#!/usr/bin/env python3
"""dedup_papers.py — 论文库重复论文夹合并（按 PDF 内容哈希）

背景: 日报流水线用 LLM 译名判断"是否已存在"，译名抖动导致同一篇论文重复建夹
（ESPO 出现 5 个变体、ContextPilot 3 个、Ecdysis 3 个…）。

策略:
  1. 同哈希分组 → 选「名称最规范」的夹为 keeper
     （结构化命名 > 英文前缀 > 完整度 > 名称短；完整度不做主导，理由见 score()）
  2. 把 loser 夹里 keeper 缺失的产物**迁移进 keeper**（并按 keeper 夹名重命名）
  3. loser 夹整体移入废纸篓（可回滚），全程写 manifest

用法:
  python3 scripts/dedup_papers.py                 # dry-run，只打印方案
  python3 scripts/dedup_papers.py --apply         # 执行（每批 ≤10 夹，逐步校验）
  python3 scripts/dedup_papers.py --restore <manifest.json>   # 按 manifest 回滚
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path("/Users/xbpd/Documents/xbpd_obsidian/02. 🟡 归类 Arrange/论文")
TRASH = Path.home() / ".Trash"
BATCH = 10  # 每批最多移动的夹数（安全护栏）

from complete_paper_folder import survey, parse_dir  # noqa: E402
from paper_index import find_all_duplicates  # noqa: E402


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def score(folder: Path) -> tuple:
    """越大越该保留。**名称质量优先于完整度**。

    为什么不能按完整度优先：完整度反映的是「哪条流水线恰好先碰过这个夹」，
    而非内容质量，且 plan_group 本就会把 loser 里 keeper 缺失的产物迁移过去 ——
    所以完整度根本不该决定归属。
    实例（2026-09-17）：一次「只跑 OCR」的批处理让某个 loser 先拿到解析目录，
    就把 ESPO 组的 keeper 反转成了名字最不规范的 `ESPO错误结构提示词优化`。

    排序：结构化命名(含「：」= 库内 house style) > 英文名前缀 > 有全文翻译 >
          有解析目录 > 有导读 > 名称短
    """
    s = survey(folder)
    has_pref = bool(re.match(r"^[A-Za-z]", folder.name))
    structured = 1 if "：" in folder.name else 0
    return (
        structured,
        has_pref,
        1 if s["full_trans"].exists() else 0,
        1 if s["parse_dir"] else 0,
        1 if s["guide"].exists() else 0,
        -len(folder.name),
    )


def plan_group(pdfs: list) -> dict:
    folders = sorted({p.parent for _, _, p in pdfs})
    ranked = sorted(folders, key=lambda f: (score(f), str(f)), reverse=True)
    keeper, losers = ranked[0], ranked[1:]
    mig = []
    for lo in losers:
        sk, sl = survey(keeper), survey(lo)
        items = []
        if sl["full_trans"].exists() and not sk["full_trans"].exists():
            items.append(("全文翻译", sl["full_trans"], keeper / f"{keeper.name}_全文翻译.md"))
        if sl["parse_dir"] and not sk["parse_dir"]:
            items.append(("解析目录", sl["parse_dir"], keeper / keeper.name))
        if sl["guide"].exists() and not sk["guide"].exists():
            items.append(("导读", sl["guide"], keeper / f"{keeper.name}_翻译导读.md"))
        mig.append({"loser": str(lo), "migrate": [(k, str(a), str(b)) for k, a, b in items]})
    return {"keeper": str(keeper), "losers": [m["loser"] for m in mig], "migrate": mig}


def build_plan() -> list:
    dups = find_all_duplicates()
    groups = [plan_group(v) for v in dups.values()]
    groups.sort(key=lambda g: -len(g["losers"]))
    return groups


def show_plan(groups: list) -> None:
    n_loser = sum(len(g["losers"]) for g in groups)
    log(f"重复组 {len(groups)} 个，冗余夹 {n_loser} 个")
    for i, g in enumerate(groups, 1):
        k = Path(g["keeper"])
        print(f"\n[{i}] 保留: {k.parent.name}/{k.name}")
        for m in g["migrate"]:
            lo = Path(m["loser"])
            print(f"    ✗ 移入废纸篓: {lo.parent.name}/{lo.name}")
            for kind, a, b in m["migrate"]:
                print(f"        ↳ 先迁移 {kind}: {Path(a).name} → {Path(b).name}")


def trash_path(p: Path) -> Path:
    """把 p 移入废纸篓（绝不 rm），返回落点。两条路径：

    ① 首选 osascript 交给 Finder —— 保留「放回原处」能力；
    ② 沙箱下 Finder 自动化常被拦（实测 2026-09-17 全部 exit 1），
       回退为直接搬进 ~/.Trash（同样可逆，只是没有 Put Back）。
    """
    try:
        subprocess.run(["osascript", "-e",
                        f'tell application "Finder" to move POSIX file "{p}" to trash'],
                       check=True, capture_output=True, timeout=60)
        return TRASH / p.name
    except Exception:
        pass
    dest = TRASH / p.name
    n = 1
    while dest.exists():
        dest = TRASH / f"{p.name}({n})"
        n += 1
    shutil.move(str(p), str(dest))
    return dest


def apply_plan(groups: list) -> Path:
    """执行合并；返回 manifest 路径。"""
    manifest = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "moved": [], "migrated": []}
    todo = []
    for g in groups:
        for m in g["migrate"]:
            todo.append((g["keeper"], m))
    log(f"待处理 loser 夹 {len(todo)} 个，分 {-(-len(todo)//BATCH)} 批（每批 ≤{BATCH}）")

    for bi in range(0, len(todo), BATCH):
        batch = todo[bi:bi + BATCH]
        log(f"—— 批次 {bi//BATCH + 1}: {len(batch)} 个 ——")
        for keeper, m in batch:
            keeper, loser = Path(keeper), Path(m["loser"])
            if not loser.exists():
                log(f"  跳过(不存在): {loser.name}")
                continue
            # 1) 先迁移 keeper 缺的产物
            for kind, src, dst in m["migrate"]:
                src, dst = Path(src), Path(dst)
                if not src.exists() or dst.exists():
                    continue
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dst))
                    manifest["migrated"].append({"kind": kind, "from": str(src), "to": str(dst)})
                    log(f"  迁移 {kind}: {src.name} → {dst.parent.name}/{dst.name}")
                except Exception as e:
                    log(f"  ⚠ 迁移失败({kind}) {src}: {e}")
            # 2) 移入废纸篓
            try:
                if not loser.exists():
                    continue
                dest = trash_path(loser)
                manifest["moved"].append({"from": str(loser), "to": str(dest),
                                          "name": loser.name, "keeper": str(keeper)})
                log(f"  🗑 {loser.parent.name}/{loser.name}")
            except Exception as e:
                log(f"  ⚠ 移废纸篓失败 {loser}: {e}")
        # 批次后校验
        left = [Path(x["from"]) for x in manifest["moved"] if Path(x["from"]).exists()]
        log(f"  批次校验: 累计已移 {len(manifest['moved'])}，未成功移走残留 {len(left)}")
        if left:
            log(f"  ⚠ 以下未成功移走，请人工确认: {[p.name for p in left]}")

    mf = Path("/tmp/dedup_manifest.json")
    mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"manifest → {mf}  (回滚: python3 scripts/dedup_papers.py --restore {mf})")
    return mf


def restore(mf: Path) -> None:
    man = json.loads(mf.read_text(encoding="utf-8"))
    for m in reversed(man["moved"]):
        # 优先用实际落点（重名时会是 name(1) 形式），旧 manifest 无 "to" 字段则回退按名找
        src = Path(m["to"]) if m.get("to") else TRASH / m["name"]
        dst = Path(m["from"])
        if not src.exists():
            alt = TRASH / m["name"]
            if alt.exists():
                src = alt
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            log(f"↩ 恢复 {m['name']}")
        else:
            log(f"⚠ 废纸篓中找不到 {m['name']}，跳过")
    for m in reversed(man["migrated"]):
        a, b = Path(m["to"]), Path(m["from"])
        if a.exists():
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(a), str(b))
            log(f"↩ 回迁 {a.name}")
    log("回滚完成")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restore", type=str)
    a = ap.parse_args()
    if a.restore:
        restore(Path(a.restore))
        return 0
    groups = build_plan()
    show_plan(groups)
    if a.apply:
        print()
        apply_plan(groups)
    else:
        log("dry-run 结束（加 --apply 执行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
