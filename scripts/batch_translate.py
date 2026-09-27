#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
batch_translate.py — 批量翻译论文 PDF 为中文 Markdown（pdf2zh + MiniMax）
用法：
  python3 batch_translate.py                 # 翻译六大子目录下所有英文 PDF（跳过已翻译的）
  python3 batch_translate.py --dir 06-AI医疗 # 只翻译指定子目录
  python3 batch_translate.py --skip-existing # 跳过已有输出的（默认即跳过）
流程：每篇 PDF → mineru 解析 → MiniMax-M3 分块翻译 → <name>_zh.md
输出：<PAPER_ROOT>/<子目录>/translated/<论文名>_zh.md
"""
import os, sys, subprocess, argparse, time, re, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import CFG

PDF2ZH = CFG["paths"]["pdf2zh"]
PYTHON = os.path.join(PDF2ZH, ".venv", "bin", "python")
SCRIPT = os.path.join(PDF2ZH, "pdf_to_zh_md.py")
PAPER_ROOT = CFG["paths"]["paper_root_legacy"]
SUBDIRS = list(CFG["directions"])
MODEL = CFG["translate"]["model"]
BASE_URL = CFG["translate"]["base_url"]
WORKERS = int(CFG["translate"]["workers"])  # 每篇内部翻译并发（降低以省内存，16GB 机器 3 路并行会 OOM）

def load_api_key():
    """读取翻译 API key：优先 MiniMax，其次 OpenRouter（从 .env 或环境变量）"""
    minimax = os.environ.get("MINIMAX_API_KEY", "")
    openrouter = os.environ.get("OPENROUTER_API_KEY", "")
    env_path = os.path.join(PDF2ZH, ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if line.startswith("MINIMAX_API_KEY="):
                v = line.split("=", 1)[1]
                if v:
                    minimax = v
            elif line.startswith("OPENROUTER_API_KEY="):
                v = line.split("=", 1)[1]
                if v:
                    openrouter = v
    return minimax or openrouter or ""

def already_done(pdf_path):
    """检查是否已有翻译输出：匹配 <name>_zh.md 或 <name> 相关的翻译记录"""
    name = Path(pdf_path).stem
    for root, _, files in os.walk(os.path.dirname(pdf_path)):
        for f in files:
            if f == f"{name}_zh.md":
                return True
            # 中文标题产物：同目录下存在 _done.txt 标记（记录原始 pdf 名）
    # 检查翻译标记文件（记录已完成翻译的原始 PDF 名，兼容中文重命名）
    marker = os.path.join(os.path.dirname(pdf_path), "translated", ".done.txt")
    if os.path.exists(marker):
        for line in open(marker, encoding="utf-8"):
            if line.strip() == name:
                return True
    return False

def translate_one(pdf_path, out_root):
    """翻译单篇 PDF；返回 (状态, 信息)"""
    name = Path(pdf_path).stem
    out_dir = os.path.join(out_root, "translated")
    os.makedirs(out_dir, exist_ok=True)
    cmd = [PYTHON, SCRIPT, pdf_path, "-o", out_dir,
           "--model", MODEL, "--base-url", BASE_URL, "--workers", str(WORKERS)]
    env = os.environ.copy()
    env["MINIMAX_API_KEY"] = load_api_key()
    print(f"\n▶ [{name}] 开始翻译...", flush=True)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=3600)
        if r.returncode == 0:
            # 找产物
            zh = None
            for root, _, files in os.walk(out_dir):
                for f in files:
                    if f.endswith("_zh.md"):
                        zh = os.path.join(root, f)
            if zh:
                # 将产物从 <name>/auto/ 移到 translated/ 根目录，再重命名为中文标题
                try:
                    target = os.path.join(out_dir, os.path.basename(zh))
                    if os.path.abspath(target) != os.path.abspath(zh) and os.path.exists(zh):
                        shutil.move(zh, target)
                        zh = target
                except Exception:
                    pass
                zh = rename_to_chinese(zh)
                # 写入完成标记（记录原始 pdf 名，兼容中文标题重命名后的跳过逻辑）
                try:
                    marker = os.path.join(os.path.dirname(zh), ".done.txt")
                    with open(marker, "a", encoding="utf-8") as mf:
                        mf.write(f"{name}\n")
                except Exception:
                    pass
            print(f"✓ [{name}] 完成 ({time.time()-t0:.0f}s) → {zh}", flush=True)
            return "ok", zh
        else:
            err = (r.stderr or r.stdout or "")[-300:]
            print(f"✗ [{name}] 失败: {err}", flush=True)
            return "fail", err
    except subprocess.TimeoutExpired:
        print(f"✗ [{name}] 超时（>1h）", flush=True)
        return "timeout", ""
    except Exception as e:
        print(f"✗ [{name}] 异常: {e}", flush=True)
        return "error", str(e)

def rename_to_chinese(zh_path):
    """将翻译产物 <name>_zh.md 重命名为 <中文标题>.md（取 md 中的 # 标题行）"""
    try:
        # 读取前 20 行，找第一个真正的 # 标题（跳过图片引用/空行/作者行等）
        title = ""
        with open(zh_path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= 20:
                    break
                s = line.strip()
                if s.startswith("# ") and not s.startswith("#!"):
                    title = s.lstrip("# ").strip()
                    break
        if not title:
            return zh_path  # 找不到标题则保留原名
        # 清理文件名非法字符
        for ch in '/\\:*?"<>|':
            title = title.replace(ch, " ")
        title = re.sub(r"\s+", " ", title).strip().rstrip(".")
        if not title:
            return zh_path
        new_path = os.path.join(os.path.dirname(zh_path), f"{title}.md")
        if os.path.abspath(new_path) != os.path.abspath(zh_path):
            os.rename(zh_path, new_path)
            return new_path
        return zh_path
    except Exception:
        return zh_path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", help="只翻译指定子目录")
    ap.add_argument("--force", action="store_true", help="强制重新翻译（默认跳过已有）")
    ap.add_argument("--parallel", type=int, default=1, help="并行翻译的 PDF 数（默认 1=串行）")
    args = ap.parse_args()

    key = load_api_key()
    if not key:
        sys.exit(f"未找到 MINIMAX_API_KEY（检查 {PDF2ZH}/.env 或环境变量）")

    parallel = max(1, min(args.parallel, 4))  # 上限 4，避免资源过载
    from concurrent.futures import ThreadPoolExecutor, as_completed

    dirs = [args.dir] if args.dir else SUBDIRS
    for sub in dirs:
        d = os.path.join(PAPER_ROOT, sub)
        if not os.path.isdir(d):
            print(f"跳过不存在的目录: {sub}")
            continue
        pdfs = sorted([f for f in os.listdir(d) if f.lower().endswith(".pdf")])
        if not pdfs:
            print(f"[{sub}] 无 PDF")
            continue
        print(f"\n===== [{sub}] 共 {len(pdfs)} 篇 PDF（并行度 {parallel}）=====")
        todo = []
        for f in pdfs:
            pdf_path = os.path.join(d, f)
            if not args.force and already_done(pdf_path):
                print(f"⏭ [{f}] 已翻译，跳过")
                continue
            todo.append(pdf_path)
        if parallel <= 1:
            for pdf_path in todo:
                translate_one(pdf_path, d)
                time.sleep(2)  # 礼貌间隔
        else:
            with ThreadPoolExecutor(max_workers=parallel) as ex:
                futs = {ex.submit(translate_one, p, d): os.path.basename(p) for p in todo}
                for fut in as_completed(futs):
                    fut.result()  # 异常已在 translate_one 内部处理

    print("\n✅ 批量翻译结束")

if __name__ == "__main__":
    main()
