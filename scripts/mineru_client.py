#!/usr/bin/env python3
"""MinerU 官方云 API 客户端（v4 Precision + Agent Flash）
- Precision：需 Token（https://mineru.net/apiManage/token 创建）
  本地文件流程: POST /api/v4/file-urls/batch 申请上传链接 → PUT 文件 → 系统自动建任务
              → 轮询 GET /api/v4/extract-results/batch/{batch_id} → 下载 full_zip_url
- Flash：免 Token（IP 限频, ≤10MB/20页）: POST /api/v1/agent/parse/file → markdown_url
凭据: /Users/xbpd/WorkBuddy/每日早报/.secrets/mineru.json  {"ak","sk","token"}
用法: python3 mineru_client.py precision <pdf路径> -o <输出目录> [--ocr]
      python3 mineru_client.py flash <pdf路径> [-o 输出目录]
"""
import sys, os, json, time, hmac, hashlib, zipfile, io, http.client as hc
import urllib.request, urllib.error
from pathlib import Path
from urllib.parse import urlsplit

BASE = "https://mineru.net"
SECRETS = Path("/Users/xbpd/WorkBuddy/每日早报/.secrets/mineru.json")

def creds():
    d = {}
    if SECRETS.exists():
        d = json.loads(SECRETS.read_text(encoding="utf-8"))
    return d

def save_token(tok):
    d = creds(); d["token"] = tok
    SECRETS.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return d

def http(url, method="GET", data=None, headers=None, timeout=120):
    req = urllib.request.Request(url, data=data, method=method,
        headers=headers or {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (r.headers.get("Content-Type", ""), raw)
    except urllib.error.HTTPError as e:
        return e.code, (e.headers.get("Content-Type", ""), e.read())

def precision_extract(pdf: Path, outdir: Path, ocr=False, model="vlm"):
    tok = creds().get("token", "")
    if not tok:
        print("缺少 token：请先在 https://mineru.net/apiManage/token 创建后运行 mineru_client.py settoken <TOKEN>")
        return 1
    h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    # 1. 申请上传链接
    name = pdf.name
    st, resp = http(f"{BASE}/api/v4/file-urls/batch", "POST",
        json.dumps({"files": [{"name": name, "data_id": pdf.stem[:60]}], "model_version": model}).encode(), h)
    if st != 200:
        print("申请上传失败", st, resp); return 1
    body = json.loads(resp[1])
    if body.get("code") != 0:
        print("API错误:", body); return 1
    batch_id = body["data"]["batch_id"]; file_url = body["data"]["file_urls"][0]
    # 2. PUT 上传（上传后自动建任务）——必须零额外头，否则 OSS 签名不匹配(403)
    u = urlsplit(file_url)
    conn = hc.HTTPSConnection(u.hostname, timeout=300)
    conn.request("PUT", u.path + "?" + u.query, body=pdf.read_bytes())
    resp = conn.getresponse(); resp.read(); conn.close()
    print("上传:", resp.status)
    # 3. 轮询 data.extract_result[0]
    for _ in range(240):
        time.sleep(5)
        st, resp = http(f"{BASE}/api/v4/extract-results/batch/{batch_id}", headers=h)
        try:
            body = json.loads(resp[1])["data"]
            it = body["extract_result"][0]
        except Exception:
            time.sleep(5); continue
        state = it.get("state")
        if state == "done":
            zurl = it["full_zip_url"]
            break
        if state == "failed":
            print("解析失败:", it.get("err_msg")); return 1
    else:
        print("超时"); return 1
    # 4. 下载并解包
    outdir.mkdir(parents=True, exist_ok=True)
    st, resp = http(zurl, timeout=300)
    if st != 200 or resp[0].find("zip") < 0:
        print("下载失败", st); return 1
    zf = zipfile.ZipFile(io.BytesIO(resp[1]))
    zf.extractall(outdir)
    md = outdir / "full.md"
    print("OK →", outdir, "| md:", md.exists(), md.stat().st_size if md.exists() else 0)
    return 0

def flash_extract(pdf: Path, outdir: Path):
    # multipart 上传（无鉴权, IP 限频）—— http.client 精确控制避免签名/长度问题
    import uuid
    boundary = uuid.uuid4().hex
    data = pdf.read_bytes()
    fname = pdf.name.encode("ascii", "ignore").decode() or "file.pdf"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file_name\"\r\n\r\n"
            f"{pdf.name}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{fname}\"\r\nContent-Type: application/pdf\r\n\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    mp = head + data + tail
    u = urlsplit(f"{BASE}/api/v1/agent/parse/file")
    conn = hc.HTTPSConnection(u.hostname, timeout=300)
    conn.request("POST", u.path, body=mp,
                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                          "Content-Length": str(len(mp))})
    resp = conn.getresponse()
    raw = resp.read(); conn.close()
    if resp.status != 200:
        print("flash 提交失败", resp.status, raw[:200]); return 1
    try:
        task = json.loads(raw)["data"]["task_id"]
    except Exception:
        print("resp:", raw[:200]); return 1
    for _ in range(240):
        time.sleep(4)
        st, resp = http(f"{BASE}/api/v1/agent/parse/{task}", timeout=120)
        try:
            d = json.loads(resp[1])["data"]
        except Exception:
            continue
        if d.get("state") == "done":
            murl = d["markdown_url"]
            st, resp = http(murl, timeout=180)
            outdir.mkdir(parents=True, exist_ok=True)
            (outdir / f"{pdf.stem}.md").write_bytes(resp[1])
            print("OK →", outdir / f"{pdf.stem}.md", len(resp[1]))
            return 0
        if d.get("state") == "failed":
            print("解析失败:", d.get("err_msg")); return 1
    print("超时"); return 1

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)
    if args[0] == "settoken":
        if len(args) < 2:
            print("用法: mineru_client.py settoken <TOKEN>"); sys.exit(1)
        save_token(args[1]); print("token 已保存 →", SECRETS); sys.exit(0)
    mode, pdf = args[0], Path(args[1])
    outdir = Path(args[3]) if len(args) > 3 and args[2] == "-o" else pdf.parent / "mineru_out"
    sys.exit(precision_extract(pdf, outdir) if mode == "precision" else flash_extract(pdf, outdir))
