#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arxiv_daily.py — 每日 arXiv 六方向检索 + PDF 下载工具链
用法：
  python3 arxiv_daily.py fetch            # 按配置检索六方向，输出 JSON 到 out/
  python3 arxiv_daily.py download <id>... # 批量下载 PDF（带文件头校验 + 重试）
  python3 arxiv_daily.py all              # fetch + 自动下载 top N 篇
论文归档：/Users/xbpd/Documents/论文/<方向中文子目录>/<id>.pdf
依赖：python3 标准库（urllib/re/json），无需第三方包
"""
import sys, os, re, json, time, html as htmlmod, subprocess
import urllib.request, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
OUT_DIR = os.path.join(ROOT, "out")
# 论文统一归档（2026-09-04 起 Obsidian 论文库）
PAPER_ROOT = "/Users/xbpd/Documents/xbpd_obsidian/02. 🟡 归类 Arrange/论文"

# 方向 -> 中文子目录（directory-discipline：按职责内聚分组）
DIRECTION_DIRS = {
    "agent":   "01-智能体",
    "context": "02-上下文工程",
    "prompt":  "03-提示词工程",
    "harness": "04-Harness执行框架",
    "loop":    "05-循环工程",
    "medical": "06-AI医疗",
}

# 六大方向检索配置（简化查询：不带日期范围，靠 sortBy=submittedDate 倒序取最新，更稳）
QUERIES = {
    "agent":   'cat:cs.AI AND cat:cs.CL AND (abs:"agent" OR abs:"agentic")',
    "context": 'cat:cs.CL AND (abs:"context engineering" OR abs:"context management" OR abs:"context window")',
    "prompt":  'cat:cs.CL AND (abs:"prompt engineering" OR abs:"prompt optimization" OR abs:"prompt design")',
    "harness": 'cat:cs.AI AND (abs:"harness" OR abs:"agent harness" OR abs:"agent runtime")',
    "loop":    'cat:cs.AI AND (abs:"agent loop" OR abs:"reflection loop" OR abs:"long-horizon" OR abs:"self-improvement")',
    "medical": 'cat:cs.CL AND (abs:"medical" OR abs:"clinical" OR abs:"healthcare") AND (abs:"agent" OR abs:"LLM")',
}
DEFAULT_TOP_N = 10  # all 模式下自动下载的论文数

def _curl_get(url, timeout=60):
    """用 curl 取数（urllib 易触发 arXiv 前端 406 内容协商），返回 (data, ok)"""
    proc = subprocess.run(
        ["curl", "-s", "--compressed", "--max-time", str(timeout),
         "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
         url],
        capture_output=True, text=True,
    )
    data = proc.stdout
    return data, bool(data and "<entry>" in data)

def arxiv_search(query, max_results=12, wait=3.5, retries=2):
    """调用 arXiv API，返回论文 dict 列表（带 406/500/限流退避重试 + 服务端代理兜底）"""
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({
        "search_query": query, "start": 0, "max_results": max_results,
        "sortBy": "submittedDate", "sortOrder": "descending"
    })
    last = None
    for attempt in range(retries + 1):
        if attempt:  # 重试前多等一会
            backoff = 10 * attempt
            print(f"  ↻ 重试（第 {attempt} 次，等待 {backoff}s）")
            time.sleep(backoff)
        else:
            time.sleep(wait)  # arXiv 礼貌间隔
        # 1) 直连（沙箱出口未被 arXiv 封禁时快且稳）
        data, ok = _curl_get(url)
        # 2) 兜底：服务端代理（api.allorigins.win）绕开沙箱出口限流/406
        if not ok:
            proxy_url = "https://api.allorigins.win/raw?url=" + urllib.parse.quote(url, safe="")
            data, ok = _curl_get(proxy_url, timeout=90)
            if ok:
                print("  · 经服务端代理取数成功")
        if ok:
            break
        last = RuntimeError("直连与服务端代理均取数失败")
        print(f"  ⚠ 取数失败：{last}")
    else:
        raise last
    entries = re.findall(r"<entry>(.*?)</entry>", data, re.S)
    out = []
    for e in entries:
        def g(tag):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", e, re.S)
            return re.sub(r"\s+", " ", htmlmod.unescape(m.group(1))).strip() if m else ""
        aid = re.search(r"/abs/([\w.]+)", g("id"))
        out.append({
            "id": aid.group(1) if aid else "",
            "title": g("title"),
            "published": g("published")[:10],
            "authors": [re.sub(r"\s+", " ", htmlmod.unescape(a)).strip()
                        for a in re.findall(r"<name>(.*?)</name>", e, re.S)],
            "summary": g("summary"),
            "withdrawn": bool(re.search(r"withdrawn", e, re.I)),
        })
    return out

def fetch_all(days=30):
    """检索六方向，按 id 去重合并，输出 JSON"""
    from datetime import date
    os.makedirs(OUT_DIR, exist_ok=True)
    all_papers = {}
    for name, q in QUERIES.items():
        try:
            papers = arxiv_search(q)
            for p in papers:
                key = p["id"].split("v")[0]
                p.setdefault("direction", name)
                all_papers[key] = p
            print(f"[{name}] {len(papers)} 篇")
        except Exception as ex:
            print(f"[{name}] FAILED: {ex}")
        time.sleep(1)
    # 按发布日期排序
    result = sorted(all_papers.values(), key=lambda x: x["published"], reverse=True)
    stamp = date.today().isoformat()
    path = os.path.join(OUT_DIR, f"papers_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"共 {len(result)} 篇（去重后）→ {path}")
    return result

def is_pdf(path):
    """校验文件头是否为合法 PDF（%PDF-）"""
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"%PDF"
    except Exception:
        return False

def dir_for(aid, direction=None):
    """确定论文归档子目录：优先 direction 映射，否则回退根目录"""
    if direction and direction in DIRECTION_DIRS:
        return os.path.join(PAPER_ROOT, DIRECTION_DIRS[direction])
    return PAPER_ROOT

def download_pdf(arxiv_id, out_dir=None, retries=3):
    """下载 PDF，带文件头校验与重试；返回 (路径, 状态)"""
    out_dir = out_dir or PAPER_ROOT
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, f"{arxiv_id}.pdf")
    urls = [
        f"https://arxiv.org/pdf/{arxiv_id}",
        f"https://export.arxiv.org/pdf/{arxiv_id}",
    ]
    for attempt in range(retries):
        for u in urls:
            try:
                req = urllib.request.Request(u, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                })
                with urllib.request.urlopen(req, timeout=90) as r:
                    data = r.read()
                if data[:4] == b"%PDF":
                    with open(dest, "wb") as f:
                        f.write(data)
                    return dest, "ok"
                # 非 PDF（HTML 拦截/页面）→ 尝试下一个 URL
            except Exception as ex:
                last = ex
        time.sleep(2 * (attempt + 1))
    return None, f"failed({getattr(last, 'reason', 'unknown')})"

def download_batch(ids, meta=None, out_dir=None):
    """批量下载；ids 可为 JSON 文件路径、id 列表或 (id, direction) 列表。
    meta: id -> direction 映射（用于按中文子目录归档）"""
    os.makedirs(PAPER_ROOT, exist_ok=True)
    ok, fail = [], []
    for item in ids:
        if isinstance(item, tuple):
            aid, direction = item
        else:
            aid, direction = item, (meta or {}).get(item.split("v")[0])
        target = dir_for(aid, direction) if out_dir is None else out_dir
        p, s = download_pdf(aid, out_dir=target)
        if s == "ok":
            ok.append(aid); print(f"✓ {aid} → {os.path.relpath(p, PAPER_ROOT)}")
        else:
            fail.append((aid, s)); print(f"✗ {aid} {s}")
        time.sleep(1)
    print(f"完成：成功 {len(ok)} / 失败 {len(fail)}")
    if fail:
        print("失败列表（多为被撤回或暂未上线）：")
        for a, s in fail: print(f"  {a}  {s}")
    return ok, fail

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "fetch":
        fetch_all()
    elif cmd == "download":
        ids = [a for a in sys.argv[2:]]
        if ids and ids[0].endswith(".json"):
            data = json.load(open(ids[0]))
            # 从 JSON 带 direction 归档到中文子目录
            items = [(p["id"].split("v")[0], p.get("direction")) for p in data[:DEFAULT_TOP_N]]
            download_batch(items)
        else:
            download_batch(ids)
    elif cmd == "all":
        papers = fetch_all()
        items = [(p["id"].split("v")[0], p.get("direction")) for p in papers[:DEFAULT_TOP_N]]
        download_batch(items)
    else:
        print(__doc__)

if __name__ == "__main__":
    main()
