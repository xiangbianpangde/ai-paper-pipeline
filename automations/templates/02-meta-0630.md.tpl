# 自动化任务 2：每日日报微信送达（06:30）

> 本文件由 `scripts/render_automations.py` 从 `automations/templates/02-每日日报微信送达-0630.md.tpl` 渲染生成——**请勿手改本文件**，改模板后重新渲染。
> 在 WorkBuddy 中新建自动化时，将「执行指令」整段粘贴到 prompt，调度设置见下方元数据。

## 元数据

| 字段 | 值 |
|------|-----|
| 任务名 | 每日日报微信送达（06:30） |
| 调度 | 每天 06:30（RRULE: `FREQ=DAILY;BYHOUR=6;BYMINUTE=30`） |
| 工作目录 | {{workspace}} |
| 状态 | ACTIVE |

## 执行指令（prompt 原文）

{{prompt}}
