#!/usr/bin/env python3
"""夸克增量上传：out/quark_sync/<方向>/*.html → 对应方向 fid（直传文件）"""
import json, subprocess, time, sys
from pathlib import Path

CLI = "/Users/xbpd/.workbuddy/skills/quarkclouddrive/scripts/quark-drive.cjs"
FID = {"01-智能体": "05ab1fceb0ca498299b8aa3a9130d39a",
       "02-上下文工程": "ab6089fa815a436aab6973e7ccbc29c5",
       "03-提示词工程": "98df445d848f41b1871277c28840f2da",
       "04-Harness执行框架": "57b77d4b09854d1d942edc16bc26ba1c",
       "05-循环工程": "63f2f6b86c3c4ba38560ed8bd22d16db",
       "06-AI医疗": "4bcae7854bb243ddbf945e417d0965d2"}
BASE = Path("/Users/xbpd/WorkBuddy/每日早报")
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
