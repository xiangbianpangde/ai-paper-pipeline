#!/usr/bin/env python3
"""只读盘点：论文库现状 → 输出每篇论文的资产配对报告(JSON + 摘要)"""
import json, os, re
from pathlib import Path

ROOT = Path("/Users/xbpd/Documents/论文")
DIRS = ["01-智能体","02-上下文工程","03-提示词工程","04-Harness执行框架","05-循环工程","06-AI医疗"]
SKIP_NAMES = {".DS_Store"}

def is_guide(p): return "翻译导读" in p.name
def is_fulltrans(p):
    n = p.name
    return n.endswith(".md") and ("全文翻译" in n or n.endswith("_zh.md") or (n.endswith(".md") and "auto" not in str(p)))
def is_pdf(p): return p.suffix.lower() == ".pdf"

report = {}
for d in DIRS:
    base = ROOT / d
    if not base.is_dir(): continue
    items = []
    # 递归收集(限制深度，避免 artifact 垃圾)
    for root, dirs, files in os.walk(base):
        depth = Path(root).relative_to(base).parts
        # 跳过 _layout/_middle/推理垃圾 由类型判断
        for f in sorted(files):
            if f in SKIP_NAMES: continue
            fp = Path(root)/f
            rel = fp.relative_to(base)
            items.append(str(rel))
    report[d] = items

# 输出整体摘要
print("=== 论文库资产总览 ===")
for d, items in report.items():
    pdfs=[i for i in items if i.lower().endswith('.pdf')]
    mds=[i for i in items if i.endswith('.md')]
    print(f"\n[{d}] 文件共 {len(items)} | PDF {len(pdfs)} | MD {len(mds)}")
json.dump(report, open("/tmp/paper_inventory.json","w"), ensure_ascii=False, indent=1)
print("\n保存 /tmp/paper_inventory.json")
