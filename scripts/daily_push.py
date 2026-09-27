#!/usr/bin/env python3
"""每日推送打包脚本：当日日报 + 当日翻译完成的论文 → 邮件包（body.html + HTML 附件 + manifest.json）

用法:
    python3 daily_push.py [--date YYYY-MM-DD]

产物目录: <workspace>/out/push/<date>/
  - body.html      邮件正文（日报 HTML + 今日论文清单注入）
  - attachments/   每篇论文一个移动端友好的 HTML 附件
  - manifest.json  {subject, body_file, attachments, stats} 供发送方读取

发送流程（由自动化执行）:
  1. 运行本脚本生成推送包
  2. 读取 manifest.json，逐个 upload_attachment 后 SendMessage(body_format=HTML)
"""

import argparse
import base64
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import CFG

WORKSPACE = Path(CFG["paths"]["workspace"])
PAPER_ROOT = Path(CFG["paths"]["paper_root"])

DIRECTIONS = ["01-智能体", "02-上下文工程", "03-提示词工程",
              "04-Harness执行框架", "05-循环工程", "06-AI医疗"]

MAX_IMG_BYTES = 800 * 1024        # 单图嵌入上限
MAX_TOTAL_EMBED = 8 * 1024 * 1024 # 单篇附件图片总预算

PAPER_CSS = """
body{font-family:-apple-system,'PingFang SC','Hiragino Sans GB',sans-serif;
  font-size:17px;line-height:1.85;color:#1F1D17;background:#FAFAF7;margin:0;padding:0}
.wrap{max-width:680px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:1.5em;line-height:1.4;margin:1.2em 0 .6em}
h2{font-size:1.3em;margin:1.4em 0 .5em;border-bottom:1px solid #E0DCD0;padding-bottom:.3em}
h3,h4,h5,h6{margin:1.2em 0 .4em}
p{margin:.6em 0;text-align:justify}
img{max-width:100%;height:auto;border-radius:6px;margin:8px 0}
blockquote{border-left:3px solid #A85032;margin:.8em 0;padding:.2em .9em;color:#5A5447;background:#F4F2EC;border-radius:0 8px 8px 0}
pre{background:#221D16;color:#EDE6D8;padding:12px;border-radius:8px;overflow-x:auto;font-size:13px;line-height:1.5}
code{font-family:ui-monospace,'SF Mono',Consolas,monospace;font-size:.88em;background:#F4F2EC;padding:1px 5px;border-radius:4px}
pre code{background:none;padding:0;color:inherit}
table{border-collapse:collapse;width:100%;margin:.8em 0;font-size:.9em}
th,td{border:1px solid #E0DCD0;padding:6px 8px;text-align:left}
th{background:#F4F2EC}
hr{border:none;border-top:1px solid #E0DCD0;margin:1.5em 0}
a{color:#2F5061}
.math{font-family:Georgia,serif;white-space:pre-wrap;background:#F4F2EC;padding:8px 12px;border-radius:8px;overflow-x:auto;font-size:.92em;margin:.6em 0}
.meta{color:#9A9385;font-size:.85em;text-align:center;padding:14px 0 4px}
"""


# ---------------------------------------------------------------- md -> html

def _inline(s: str) -> str:
    s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"<em>[\1]</em>", s)  # 残留图占位
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*\*([^*]+)\*\*\*", r"<strong><em>\1</em></strong>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def md_to_html(md_text: str, img_resolver=None, budget=None) -> str:
    """极简 Markdown -> HTML。img_resolver(rel_path) 返回 data URI 或 None。
    budget: [剩余字节预算] 列表，用于图片嵌入限额。"""
    out, i = [], 0
    lines = md_text.split("\n")
    n = len(lines)
    in_code, code_buf = False, []
    list_stack = []  # 'ul' | 'ol'
    para_buf = []

    def flush_para():
        if para_buf:
            out.append(f"<p>{_inline(' '.join(para_buf))}</p>")
            para_buf.clear()

    def close_list():
        while list_stack:
            out.append(f"</{list_stack.pop()}>")

    while i < n:
        line = lines[i]
        if line.strip().startswith("```"):
            flush_para(); close_list()
            if in_code:
                out.append("<pre><code>" + "\n".join(code_buf) + "</code></pre>")
                code_buf, in_code = [], False
            else:
                in_code = True
            i += 1; continue
        if in_code:
            code_buf.append(line); i += 1; continue

        stripped = line.strip()
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            flush_para(); close_list()
            lv = len(m.group(1))
            out.append(f"<h{lv}>{_inline(m.group(2))}</h{lv}>")
            i += 1; continue
        m = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", stripped)
        if m:
            flush_para()
            alt, rel = m.group(1), m.group(2).split()[0]
            uri = img_resolver(rel, budget) if img_resolver else None
            if uri:
                out.append(f'<img src="{uri}" alt="{alt}">')
            i += 1; continue
        if stripped in ("---", "***", "___"):
            flush_para(); close_list(); out.append("<hr>"); i += 1; continue
        m = re.match(r"^>\s?(.*)$", stripped)
        if m:
            flush_para(); close_list()
            quote = [m.group(1)]
            while i + 1 < n and re.match(r"^>\s?", lines[i + 1].strip()):
                i += 1; quote.append(re.sub(r"^>\s?", "", lines[i].strip()))
            out.append(f"<blockquote><p>{_inline(' '.join(quote))}</p></blockquote>")
            i += 1; continue
        m = re.match(r"^([-*+])\s+(.*)$", stripped)
        if m:
            flush_para()
            if not list_stack or list_stack[-1] != "ul":
                close_list(); out.append("<ul>"); list_stack.append("ul")
            out.append(f"<li>{_inline(m.group(2))}</li>")
            i += 1; continue
        m = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
        if m:
            flush_para()
            if not list_stack or list_stack[-1] != "ol":
                close_list(); out.append("<ol>"); list_stack.append("ol")
            out.append(f"<li>{_inline(m.group(2))}</li>")
            i += 1; continue
        if stripped.startswith("|") and stripped.endswith("|"):
            flush_para(); close_list()
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            rows = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c or "---") for c in r)]
            if rows:
                out.append("<table><thead><tr>" +
                           "".join(f"<th>{_inline(c)}</th>" for c in rows[0]) +
                           "</tr></thead><tbody>")
                for r in rows[1:]:
                    out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
                out.append("</tbody></table>")
            continue
        if stripped.startswith("$$"):
            flush_para(); close_list()
            buf = [stripped]
            if not (stripped.endswith("$$") and len(stripped) > 2):
                while i + 1 < n and not lines[i + 1].strip().endswith("$$"):
                    i += 1; buf.append(lines[i].strip())
                if i + 1 < n:
                    i += 1; buf.append(lines[i].strip())
            out.append('<div class="math">' + "\n".join(buf) + "</div>")
            i += 1; continue
        if not stripped:
            flush_para(); close_list()
        else:
            para_buf.append(stripped)
        i += 1

    if in_code and code_buf:
        out.append("<pre><code>" + "\n".join(code_buf) + "</code></pre>")
    flush_para(); close_list()
    return "\n".join(out)


def build_img_index(translated_dir: Path) -> dict:
    """扫描 translated 目录下所有 images/ 文件夹，建立 文件名 -> 绝对路径 索引。"""
    index = {}
    for root, dirs, files in os.walk(translated_dir):
        if os.path.basename(root) == "images":
            for f in files:
                index.setdefault(f, Path(root) / f)
    return index


def make_resolver(index: dict):
    def resolve(rel: str, budget) -> str | None:
        fname = os.path.basename(rel)
        path = index.get(fname)
        if not path or not path.exists():
            return None
        try:
            if path.stat().st_size > MAX_IMG_BYTES:
                return None
            if budget and budget[0] <= 0:
                return None
            data = path.read_bytes()
            if budget:
                budget[0] -= len(data)
            ext = path.suffix.lstrip(".").lower()
            mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
                    "gif": "gif", "webp": "webp"}.get(ext, "jpeg")
            return f"data:image/{mime};base64," + base64.b64encode(data).decode()
        except OSError:
            return None
    return resolve


def paper_body_html(title: str, md_path: Path, direction: str) -> str:
    text = md_path.read_text(encoding="utf-8", errors="replace")
    tdir = md_path.parent
    index = build_img_index(tdir)
    resolver = make_resolver(index)
    content = md_to_html(text, resolver, [MAX_TOTAL_EMBED])
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title><style>{PAPER_CSS}</style></head>
<body><div class="wrap">
<p class="meta">{direction} · 论文中文翻译 · 由 pdf2zh + MiniMax-M3 生成</p>
{content}
</div></body></html>"""


# ---------------------------------------------------------------- 打包主流程

def sanitize(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\n\r]', " ", name).strip()[:60]


def collect_papers(target: date) -> list[dict]:
    """收集 target 当日有修改的根级翻译 md。"""
    papers = []
    lo = datetime(target.year, target.month, target.day).timestamp()
    hi = lo + 86400
    for d in DIRECTIONS:
        tdir = PAPER_ROOT / d / "translated"
        if not tdir.is_dir():
            continue
        for md in sorted(tdir.glob("*.md")):
            mt = md.stat().st_mtime
            if lo <= mt < hi:
                papers.append({"direction": d, "title": md.stem,
                               "md_path": str(md), "mtime": mt})
    papers.sort(key=lambda p: p["mtime"])
    return papers


def find_report(target: date) -> Path | None:
    """优先当天日报，否则往前找最近 3 天。"""
    for k in range(4):
        p = WORKSPACE / f"{target - timedelta(days=k)}.html"
        if p.exists():
            return p
    return None


def inject_section(report_html: str, papers: list[dict]) -> str:
    """在日报 <body> 开头注入「今日新翻译论文」清单。"""
    items = "".join(
        f'<li><strong>{p["title"]}</strong> <span style="opacity:.6">({p["direction"]})</span>'
        f'<br><small style="opacity:.6">见邮件附件</small></li>'
        for p in papers)
    section = f"""
<div style="max-width:680px;margin:12px auto;padding:14px 18px;border:2px solid #A85032;
  border-radius:14px;background:#FDF6EF;font-family:-apple-system,'PingFang SC',sans-serif">
  <div style="font-weight:700;font-size:17px;color:#8B3A1F;margin-bottom:8px">
    📎 今日新翻译论文 · {len(papers)} 篇（全文见附件，手机点开即读）
  </div>
  <ol style="margin:0;padding-left:20px;font-size:14px;line-height:1.7">{items}</ol>
</div>"""
    m = re.search(r"<body[^>]*>", report_html)
    if m:
        return report_html[:m.end()] + section + report_html[m.end():]
    return section + report_html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD，默认今天")
    args = ap.parse_args()

    target = (datetime.strptime(args.date, "%Y-%m-%d").date()
              if args.date else date.today())

    papers = collect_papers(target)
    report = find_report(target)

    out_dir = WORKSPACE / "out" / "push" / str(target)
    att_dir = out_dir / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)

    attachments = []
    for idx, p in enumerate(papers, 1):
        html = paper_body_html(p["title"], Path(p["md_path"]), p["direction"])
        fname = f"{idx:02d}-{sanitize(p['title'])}.html"
        fpath = att_dir / fname
        fpath.write_text(html, encoding="utf-8")
        attachments.append({"filename": fname, "path": str(fpath),
                            "title": p["title"], "direction": p["direction"]})

    if report:
        body = inject_section(report.read_text(encoding="utf-8", errors="replace"), papers)
        report_used = report.name
    else:
        items = "".join(f"<li>{p['title']} ({p['direction']})</li>" for p in papers)
        body = (f'<html><body style="font-family:-apple-system,\'PingFang SC\',sans-serif">'
                f'<h2>AI 论文翻译日报 · {target}</h2>'
                f'<p>今日新翻译 {len(papers)} 篇（全文见附件）：</p><ol>{items}</ol></body></html>')
        report_used = None

    body_path = out_dir / "body.html"
    body_path.write_text(body, encoding="utf-8")

    subject = f"AI 早报 · {target} · 日报+{len(papers)}篇论文翻译"
    manifest = {
        "date": str(target), "subject": subject,
        "body_file": str(body_path), "report_used": report_used,
        "attachments": attachments,
        "stats": {"papers": len(papers)},
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[OK] 推送包已生成: {out_dir}")
    print(f"  日报: {report_used or '无（未找到近期日报）'}")
    print(f"  论文附件: {len(attachments)} 篇")
    print(f"  正文: {body_path} ({body_path.stat().st_size // 1024}KB)")
    print(f"  manifest: {out_dir / 'manifest.json'}")


if __name__ == "__main__":
    sys.exit(main())
