#!/usr/bin/env python3
"""深度盘点(限层)：逐篇论文资产配对分析 → 输出 markdown 报告"""
import os, re, json
from pathlib import Path

ROOT = Path("/Users/xbpd/Documents/论文")
DIRS = ["01-智能体","02-上下文工程","03-提示词工程","04-Harness执行框架","05-循环工程","06-AI医疗"]
SKIP = {".DS_Store"}

def analyze(base: Path):
    root_files, root_dirs = [], []
    for f in sorted(base.iterdir()):
        if f.name in SKIP: continue
        (root_dirs if f.is_dir() else root_files).append(f)

    # 1) 根级 PDF（无 auto 干扰）
    root_pdfs = [f for f in root_files if f.suffix.lower() == ".pdf"]
    # 2) 根级 导读 md
    guides = [f for f in root_files if f.suffix.lower() == ".md" and "导读" in f.name]
    # 3) 根级其他 md
    other_md = [f for f in root_files if f.suffix.lower() == ".md" and "导读" not in f.name]

    # 4) translated 内：文件级 md + 子目录（auto/人工）
    tdir = base / "translated"
    trans_root_md, trans_dirs = [], []
    if tdir.is_dir():
        for f in sorted(tdir.iterdir()):
            if f.name in SKIP: continue
            if f.is_dir(): trans_dirs.append(f)
            elif f.suffix.lower() == ".md": trans_root_md.append(f)

    # 5) 手动论文文件夹(根级目录，排除 translated)
    manual_dirs = [d for d in root_dirs if d.name != "translated"]

    # 组装摘要
    lines = []
    lines.append(f"### {base.name}")
    lines.append(f"- 根级PDF: {len(root_pdfs)}")
    for f in root_pdfs[:60]:
        mark = ""
        lines.append(f"  · 📄 {f.name}")
    lines.append(f"- 根级导读: {len(guides)}")
    for f in guides: lines.append(f"  · 🧭 {f.name}")
    lines.append(f"- 根级其他md: {len(other_md)}")
    for f in other_md: lines.append(f"  · 📝 {f.name}")
    lines.append(f"- translated根级md: {len(trans_root_md)}")
    for f in trans_root_md: lines.append(f"  · ✅ {f.name}")
    lines.append(f"- translated子目录: {len(trans_dirs)}")
    for d in trans_dirs:
        inner = list(d.rglob("*.md"))
        inner = [i.relative_to(d) for i in inner][:3]
        has_img = any(i.suffix.lower() in (".jpg",".png",".jpeg") for i in d.rglob("*"))
        lines.append(f"  · 📁 {d.name}/ (md:{len(list(d.rglob('*.md')))}, 图:{'有' if has_img else '无'})")
    lines.append(f"- 手动论文文件夹: {len(manual_dirs)}")
    for d in manual_dirs: lines.append(f"  · 🗂 {d.name}/")
    return "\n".join(lines)

out = []
for d in DIRS:
    base = ROOT / d
    if not base.is_dir(): continue
    out.append(analyze(base))
    out.append("")

text = "\n".join(out)
Path("/Users/xbpd/WorkBuddy/每日早报/out/paper_analysis.md").write_text(text, encoding="utf-8")
print(text[:3000])
print(f"\n\n>>> 完整报告: /Users/xbpd/WorkBuddy/每日早报/out/paper_analysis.md")
