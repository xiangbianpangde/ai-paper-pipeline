#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
translate_daemon.py — 自守护批量翻译循环
- double-fork + setsid 完全脱离会话（WorkBuddy 会话回收杀不掉）
- 内部循环：跑 batch_translate.py --parallel N，崩溃则自动重启，全部完成后退出
- 用法：python3 scripts/translate_daemon.py [parallel]  （默认 2）
- 状态：/tmp/translate_daemon.status  日志：/tmp/translate_daemon.log
"""
import os, sys, time, subprocess, signal

DAEMON_LOG = "/tmp/translate_daemon.log"
STATUS_FILE = "/tmp/translate_daemon.status"
ROUND_LOG = "/tmp/batch_daemon_round.log"  # batch 输出落地，消除盲区
WORKDIR = "/Users/xbpd/WorkBuddy/每日早报"
SCRIPT = os.path.join(WORKDIR, "scripts", "batch_translate.py")
PAPER_ROOT = "/Users/xbpd/Documents/论文"
SUBDIRS = ["01-智能体", "02-上下文工程", "03-提示词工程", "04-Harness执行框架", "05-循环工程", "06-AI医疗"]

def log(msg):
    with open(DAEMON_LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")

def set_status(msg):
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

def remaining_count():
    """统计剩余未翻译 PDF 数（基于 .done.txt）"""
    remain = 0
    for sub in SUBDIRS:
        d = os.path.join(PAPER_ROOT, sub)
        if not os.path.isdir(d):
            continue
        pdfs = [f for f in os.listdir(d) if f.lower().endswith(".pdf")]
        mark = os.path.join(d, "translated", ".done.txt")
        done = set()
        if os.path.exists(mark):
            done = {l.strip() for l in open(mark, encoding="utf-8") if l.strip()}
        remain += max(0, len(pdfs) - len(done))
    return remain

def main():
    parallel = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    log(f"守护进程启动 (parallel={parallel}, PID={os.getpid()})")
    set_status("daemon started")
    # 防休眠：系统空闲休眠会冻结/杀掉翻译进程（08-26 凌晨事故根因之一）
    caffeinate = None
    try:
        caffeinate = subprocess.Popen(
            ["caffeinate", "-is", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"caffeinate 已挂载 (PID={caffeinate.pid})")
    except Exception:
        log("caffeinate 不可用，跳过防休眠")
    consecutive_fail = 0
    try:
        _run_loop(parallel, consecutive_fail)
    finally:
        if caffeinate:
            caffeinate.terminate()

def _run_loop(parallel, consecutive_fail):
    while True:
        remain = remaining_count()
        if remain == 0:
            log("✅ 全部翻译完成，守护进程退出")
            set_status("ALL_DONE")
            break
        log(f"剩余 {remain} 篇，启动 batch_translate (parallel={parallel})")
        set_status(f"running, remain={remain}")
        round_log = open(ROUND_LOG, "a", encoding="utf-8")
        round_log.write(f"\n===== 轮次开始 {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        try:
            r = subprocess.run(
                [sys.executable, SCRIPT, "--parallel", str(parallel)],
                cwd=WORKDIR,
                stdout=round_log, stderr=subprocess.STDOUT,
                timeout=4 * 3600,  # 单轮最长 4 小时
            )
            if r.returncode == 0:
                consecutive_fail = 0
                log(f"本轮完成 (exit 0)，剩余 {remaining_count()}")
            else:
                consecutive_fail += 1
                log(f"本轮异常退出 (exit {r.returncode})，连续失败 {consecutive_fail} 次")
        except subprocess.TimeoutExpired:
            consecutive_fail += 1
            log(f"单轮超时(4h)，重启；连续 {consecutive_fail}")
        except Exception as e:
            consecutive_fail += 1
            log(f"异常: {e}；连续 {consecutive_fail}")
        finally:
            round_log.close()
        if consecutive_fail >= 5:
            log("连续失败 5 次，暂停守护，等待人工干预")
            set_status("STOPPED_5_FAILS")
            break
        time.sleep(10)  # 轮次间小间隔

def daemonize():
    """double-fork + setsid 脱离会话"""
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    # 重定向标准流到 /dev/null 或日志
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    main()

if __name__ == "__main__":
    if os.environ.get("DAEMON_CHILD"):
        main()
    else:
        # 启动守护子进程
        pid = os.fork()
        if pid > 0:
            print(f"守护进程已启动 (PID {pid})")
            sys.exit(0)
        os.setsid()
        if os.fork() > 0:
            os._exit(0)
        os.environ["DAEMON_CHILD"] = "1"
        os.chdir(WORKDIR)
        devnull = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(devnull, fd)
        main()
