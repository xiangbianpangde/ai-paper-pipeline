#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_automations.py — 用 config.json 渲染定时任务定义模板

读取 automations/templates/*.md.tpl，把 {{占位符}} 替换为 config.json 中的
实际值，输出到 automations/<原名去 .tpl>。两个模板：

  01-每日日报一条龙-0600.md.tpl   （prompt 模板）
  01-meta-0600.md.tpl             （元数据壳，内嵌 {{prompt}}）
  02-每日日报微信送达-0630.md.tpl （prompt 模板）
  02-meta-0630.md.tpl             （元数据壳）

用法: python3 scripts/render_automations.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import CFG

TPL_DIR = Path(__file__).resolve().parent.parent / "automations" / "templates"
OUT_DIR = TPL_DIR.parent

PAIRS = [  # (prompt 模板, 元数据壳模板, 输出文件名)
    ("01-每日日报一条龙-0600.md.tpl", "01-meta-0600.md.tpl", "01-每日日报一条龙-0600.md"),
    ("02-每日日报微信送达-0630.md.tpl", "02-meta-0630.md.tpl", "02-每日日报微信送达-0630.md"),
]


def build_context() -> dict:
    p, q, n = CFG["paths"], CFG["quark"], CFG["notify"]
    directions = list(CFG["directions"])
    fid_pairs = " / ".join(
        f"{d}={q['fid_directions'].get(d, '<待填写>')}" for d in directions)
    return {
        "user_name": CFG["user"]["name"],
        "research_interests": CFG["research_interests"],
        "research_anchors": CFG.get("research_anchors", ""),
        "workspace": p["workspace"],
        "paper_root": p["paper_root"],
        "secrets_dir": p["secrets_dir"],
        "quark_skill_dir": p.get("quark_skill_dir", ""),
        "directions_list": " / ".join(directions),
        "fid_report": q["fid_report"],
        "fid_directions": fid_pairs,
        "serverchan_env": n["serverchan_env"],
        "serverchan_key_file": n["serverchan_key_file"],
    }


def render(text: str, ctx: dict, tpl_name: str) -> str:
    missing = set(re.findall(r"\{\{(\w+)\}\}", text)) - set(ctx)

    def sub(m):
        return str(ctx.get(m.group(1), m.group(0)))

    out = re.sub(r"\{\{(\w+)\}\}", sub, text)
    if missing:
        print(f"  ⚠ {tpl_name} 未识别的占位符（原样保留）: {', '.join(sorted(missing))}")
    return out


def main():
    ctx = build_context()
    for prompt_tpl, meta_tpl, out_name in PAIRS:
        prompt = render((TPL_DIR / prompt_tpl).read_text(encoding="utf-8"), ctx, prompt_tpl)
        meta = (TPL_DIR / meta_tpl).read_text(encoding="utf-8")
        out = render(meta, {**ctx, "prompt": prompt.strip()}, meta_tpl)
        dest = OUT_DIR / out_name
        dest.write_text(out, encoding="utf-8")
        print(f"✓ {dest}")
    print("完成。在 WorkBuddy 自动化中新建任务时粘贴「执行指令」段即可。")


if __name__ == "__main__":
    main()
