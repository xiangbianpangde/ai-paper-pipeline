#!/usr/bin/env python3
"""OCR 识别 99-待分类 47 篇 hash PDF 的标题与首段（MinerU flash，免 token）
产物: /tmp/ocr47/<hash>.md (成功) 或记录失败; /tmp/ocr47_state.json
"""
import sys, json, time, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from mineru_client import precision_extract

O = Path("/Users/xbpd/Documents/xbpd_obsidian/02. 🟡 归类 Arrange/论文/99-待分类(非方向)")
OUT = Path("/tmp/ocr47"); OUT.mkdir(exist_ok=True)
STATE = Path("/tmp/ocr47_state.json")

def load():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"ok": {}, "fail": {}}

def save(st):
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")

def main():
    st = load()
    pdfs = sorted(O.glob("*.pdf"))
    print(f"待识别: {len(pdfs)} | 已完成 ok={len(st['ok'])} fail={len(st['fail'])}", flush=True)
    for i, p in enumerate(pdfs, 1):
        key = p.stem.replace("[根目录] ", "")
        if key in st["ok"] or key in st["fail"]:
            continue
        try:
            out = OUT / key
            if out.exists() and list(out.glob("*.md")):
                st["ok"][key] = "cached"
            else:
                rc = precision_extract(p, out, model="pipeline")
                if rc != 0:
                    st["fail"][key] = "precision_error"
                else:
                    st["ok"][key] = "ok"
        except Exception as e:
            st["fail"][key] = str(e)[:100]
        save(st)
        print(f"[{i}/{len(pdfs)}] {key} -> ok" if key in st["ok"] else f"[{i}/{len(pdfs)}] {key} FAIL", flush=True)
    print(f"ALL_DONE ok={len(st['ok'])} fail={len(st['fail'])}", flush=True)

if __name__ == "__main__":
    main()
