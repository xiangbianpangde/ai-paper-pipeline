#!/usr/bin/env python3
"""夸克增量上传：out/quark_sync/<方向>/*.html → 对应方向 fid（直传文件）"""
import json, subprocess, time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import CFG

CLI = CFG["paths"]["quark_cli"]
FID = dict(CFG["quark"]["fid_directions"])
BASE = Path(CFG["paths"]["workspace"])
SID = f"{int(time.time())}-9f4k2x"
LOG = open("/tmp/quark_upload3.log", "w", encoding="utf-8")
ok = fail = 0
for d in FID:
    files = sorted((BASE / "out/quark_sync" / d).glob("*.html"))
    if not files:
        continue
    for f in files:
        try:
            r = subprocess.run(["node", CLI, "upload", str(f), "--parent-fid", FID[d],
                                "--session-input", "夸克增量同步", "--session-id", SID],
                               capture_output=True, text=True, timeout=180)
            good = '"type":"result"' in r.stdout and '"code":0' in r.stdout
        except Exception as e:
            good = False
        ok += 1 if good else 0
        fail += 0 if good else 1
        LOG.write(("✓" if good else "✗") + f" {d} {f.name}\n")
        LOG.flush()
LOG.write(f"DONE ok={ok} fail={fail}\n")
LOG.close()
print(f"DONE ok={ok} fail={fail}")
