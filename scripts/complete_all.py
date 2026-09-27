#!/usr/bin/env python3
"""complete_all.py — 批量补全库内所有半成品论文夹

逐个调用 complete_paper_folder.complete()：MinerU 云 OCR + MiniMax-M3 全文翻译 + 导读。
幂等：已存在的产物自动跳过，中断后重跑只补缺口。

用法:
  python3 scripts/complete_all.py --scan          # 只列出待补全清单
  python3 scripts/complete_all.py --daemon        # 后台守护（double-fork + setsid + caffeinate）
  python3 scripts/complete_all.py --workers 2     # 前台跑（可 Ctrl-C）
状态: /tmp/complete_all.status + /tmp/complete_all.log + /tmp/complete_all.fail.jsonl

⚠ 代理陷阱（2026-09-17 实测踩坑，导致 83/94 批量失败）:
  本机沙箱按「回合」注入 HTTP(S)_PROXY=http://127.0.0.1:<随机端口>。守护进程若继承该
  环境变量，回合结束后代理端口被回收，后续所有请求瞬间 `Connection refused`。
  实测 mineru.net 与 api.minimaxi.com 均可**直连**，故启动时一律剥离代理变量。
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from complete_paper_folder import ROOT, DIRS, paper_folders, survey, complete, log  # noqa: E402

STATUS = Path("/tmp/complete_all.status")
LOGF = Path("/tmp/complete_all.log")
FAILF = Path("/tmp/complete_all.fail.jsonl")
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
              "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy")


def strip_proxy() -> None:
    """剥离沙箱按回合注入的临时代理（端口随回合回收，长任务必须绕过）。"""
    for k in PROXY_VARS:
        if os.environ.pop(k, None) is not None:
            log(f"剥离代理变量 {k}")


def set_status(s: str) -> None:
    STATUS.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {s}\n", encoding="utf-8")


def pending(ocr_only: bool = False, exclude: set | None = None,
            include_parked: bool = False, only: str | None = None) -> list:
    """待处理论文夹。ocr_only=True 时只挑「还缺解析目录」的（译文后续再补）。
    exclude 为待弃夹路径集合（如去重冗余夹）——不做任何 OCR，省配额。
    include_parked=False（默认）跳过 PARKED 停放区（如「其他方向/」的 PDF-only 论文）。
    only 限定只处理某目录子树下的论文夹（如刚导入的新方向目录）——
      避免为了补几篇新论文而顺带重扫全库既有的半成品夹、白烧配额。"""
    exclude = exclude or set()
    only_root = Path(only).resolve() if only else None
    if only_root and not only_root.is_dir():
        log(f"⚠ --only 不是目录，忽略: {only_root}")
        only_root = None
    out = []
    for f in sorted(paper_folders(include_parked=include_parked)):
        if str(f) in exclude:
            continue
        if only_root and only_root not in f.resolve().parents:
            continue
        s = survey(f)
        if not s["pdf"]:
            continue
        if ocr_only:
            if not s["parse_dir"]:
                out.append(f)
        elif not s["full_trans"].exists() or not s["parse_dir"]:
            out.append(f)
    return out


def load_exclude(path: str | None) -> set:
    if not path:
        return set()
    p = Path(path)
    if not p.exists():
        log(f"⚠ 排除清单不存在，忽略: {p}")
        return set()
    s = {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()}
    log(f"排除清单载入 {len(s)} 个夹（不做 OCR）")
    return s


def run(workers: int = 2, retries: int = 2, ocr_only: bool = False,
        exclude: set | None = None, include_parked: bool = False,
        only: str | None = None) -> None:
    todo = pending(ocr_only, exclude, include_parked, only)
    total = len(todo)
    log(f"待处理 {total} 个论文夹 | workers={workers} | 单夹最多尝试 {retries + 1} 次"
        f"{' | 仅 OCR' if ocr_only else ''}"
        f"{' | 含停放区' if include_parked else ''}"
        f"{' | 限定目录 ' + only if only else ''}")
    set_status(f"RUNNING 0/{total}")
    ok = fail = 0
    lock = threading.Lock()

    def attempt(f):
        """单夹补全，失败按 20s/60s 退避重试；返回 (是否成功, 失败原因)。"""
        last = ""
        for i in range(retries + 1):
            try:
                if complete(f, ocr_only=ocr_only):
                    return True, ""
                last = "complete() 返回失败"
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
            if i < retries:
                log(f"↻ 重试 {i + 2}/{retries + 1} ({f.name[:40]}): {last}")
                time.sleep(20 if i == 0 else 60)
        return False, last

    def work(sub):
        nonlocal ok, fail
        for f in sub:
            good, why = attempt(f)
            with lock:
                if good:
                    ok += 1
                else:
                    fail += 1
                    with FAILF.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"folder": str(f), "reason": why},
                                            ensure_ascii=False) + "\n")
                set_status(f"RUNNING {ok+fail}/{total} (ok={ok} fail={fail})")
                log(f"—— 进度 {ok+fail}/{total} | 成功 {ok} 失败 {fail} ——")
                if not good:
                    log(f"✗ 失败 [{f.parent.name}] {f.name}: {why}")

    n = max(1, (total + workers - 1) // workers)
    ts = [threading.Thread(target=work, args=(todo[i * n:(i + 1) * n],))
          for i in range(workers) if todo[i * n:(i + 1) * n]]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    set_status(f"DONE ok={ok} fail={fail} total={total}")
    log(f"ALL_DONE 成功 {ok} 失败 {fail} 总计 {total}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--ocr-only", action="store_true",
                    help="只跑 MinerU OCR（不需 MiniMax 凭据，可先把最耗时的一步批量前置）")
    ap.add_argument("--exclude", type=str, default="",
                    help="排除清单文件（每行一个夹绝对路径），如 /tmp/dedup_exclude.txt")
    ap.add_argument("--include-other", action="store_true",
                    help="连「其他方向/」停放区的 PDF-only 论文一起补全（默认跳过，避免烧配额）")
    ap.add_argument("--only", type=str, default="",
                    help="只补全指定目录子树下的论文夹（如刚导入的新方向目录）")
    a = ap.parse_args()

    strip_proxy()          # 必须在 fork 之前：保证守护进程不继承会失效的回合代理
    exclude = load_exclude(a.exclude)

    if a.scan:
        todo = pending(a.ocr_only, exclude, a.include_other, a.only)
        log(f"待处理 {len(todo)} 个：")
        for f in todo:
            print(f"  [{f.parent.name}] {f.name}")
        return 0

    if a.daemon:
        if os.fork() > 0:
            sys.exit(0)
        os.setsid()
        if os.fork() > 0:
            os._exit(0)
        # 防休眠：长任务期间阻止系统睡眠
        try:
            subprocess.Popen(["caffeinate", "-is"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except Exception:
            pass
        sys.stdout = open(LOGF, "a", encoding="utf-8", buffering=1)
        sys.stderr = sys.stdout
        log("=== complete_all daemon 启动 ===")
        try:
            run(a.workers, a.retries, a.ocr_only, exclude, a.include_other, a.only)
        except Exception as e:
            log(f"守护异常: {e}")
            set_status(f"ERROR {e}")
        sys.exit(0)

    run(a.workers, a.retries, a.ocr_only, exclude, a.include_other, a.only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
