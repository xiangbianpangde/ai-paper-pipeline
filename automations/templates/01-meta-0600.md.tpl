# 自动化任务 1：每日日报一条龙（06:00）

> 本文件由 `scripts/render_automations.py` 从 `automations/templates/01-每日日报一条龙-0600.md.tpl` 渲染生成——**请勿手改本文件**，改模板后重新渲染。
> 在 WorkBuddy 中新建自动化时，将「执行指令」整段粘贴到 prompt，调度设置见下方元数据。

## 元数据

| 字段 | 值 |
|------|-----|
| 任务名 | 每日 AI 日报一条龙（论文归档·生成→夸克同步→微信通知）· 每天 06:00 |
| 调度 | 每天 06:00（RRULE: `FREQ=DAILY;BYHOUR=6;BYMINUTE=0`） |
| 工作目录 | {{workspace}} |
| 状态 | ACTIVE |

## 执行指令（prompt 原文）

{{prompt}}
