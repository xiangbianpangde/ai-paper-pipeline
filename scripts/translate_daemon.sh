#!/usr/bin/env bash
# translate_daemon.sh — 批量翻译守护脚本（crontab 每 10 分钟调用）
# 若 batch_translate 未运行则拉起；已在运行则跳过（防并发）
# 解决：nohup 进程随 WorkBuddy 会话回收被杀的问题，改为系统级 cron 守护

set -u
LOCKFILE="/tmp/batch_translate_daemon.lock"
LOG="/tmp/batch_translate_daemon.log"
# WORKDIR 取脚本所在目录（batch_translate.py 同目录），可用环境变量覆盖
WORKDIR="${PIPELINE_WORKDIR:-$(cd "$(dirname "$0")" && pwd)}"
PARALLEL="${1:-2}"

# 锁文件防止并发（带 PID 校验的简单锁）
if [ -f "$LOCKFILE" ]; then
    LOCKPID=$(cat "$LOCKFILE" 2>/dev/null || echo "")
    if [ -n "$LOCKPID" ] && kill -0 "$LOCKPID" 2>/dev/null; then
        echo "$(date '+%H:%M:%S') 已有守护实例运行 (PID $LOCKPID)，退出" >> "$LOG"
        exit 0
    fi
    # 锁进程已死，删除过期锁
    rm -f "$LOCKFILE"
fi
echo $$ > "$LOCKFILE"
trap 'rm -f "$LOCKFILE"' EXIT

# 检查翻译是否已在运行
if pgrep -f "batch_translate.py" > /dev/null 2>&1; then
    echo "$(date '+%H:%M:%S') 翻译已在运行，跳过" >> "$LOG"
    exit 0
fi

echo "$(date '+%H:%M:%S') 翻译未运行，拉起 (parallel=$PARALLEL)" >> "$LOG"
cd "$WORKDIR" || exit 1
# 用 setsid 完全脱离会话（macOS 无 setsid 命令，用 python 实现）
python3 -c "
import os, sys
pid = os.fork()
if pid > 0:
    sys.exit(0)  # 父进程退出
os.setsid()
os.execvp('python3', ['python3', 'scripts/batch_translate.py', '--parallel', '$PARALLEL'])
" >> /tmp/batch_translate_cron.log 2>&1
echo "$(date '+%H:%M:%S') 已拉起翻译进程" >> "$LOG"
