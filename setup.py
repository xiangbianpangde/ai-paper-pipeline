#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""setup.py — 新用户初始化向导：问答生成 config.json

两种用法：
  1. 交互问答：  python3 setup.py
     逐项提问（括号内为默认值，直接回车沿用），最后写出 config.json。
  2. AI 协助：   AI 助手按 SETUP.md 的问询清单向用户收集信息后，
     用 --set 键=值 写入（可多次），无需交互：
       python3 setup.py --set paths.paper_root="/path/to/论文" --set user.name="张三"
  3. 校验配置：  python3 setup.py --check

写完后运行 `python3 scripts/render_automations.py` 生成个性化定时任务定义。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from pipeline_config import CFG, load, config_file

CONFIG_FILE = config_file()


def ask(prompt, default=""):
    suffix = f"（回车 = {default}）" if default else ""
    while True:
        v = input(f"{prompt}{suffix}: ").strip()
        if v or default:
            return v or default
        print("  不能为空，请输入。")


def ask_int(prompt, default):
    while True:
        v = ask(prompt, str(default))
        try:
            return int(v)
        except ValueError:
            print("  请输入整数。")


def interactive():
    print("=" * 62)
    print("AI 论文管线 · 初始化向导")
    print("逐项回答回车沿用默认值；全部完成后写出 config.json")
    print("=" * 62)

    cfg = json.loads(json.dumps(CFG))  # 从当前配置出发（默认 = 作者环境）

    print("\n── ① 基础信息 ──")
    cfg["user"]["name"] = ask("你的称呼", cfg["user"]["name"])
    cfg["research_interests"] = ask("研究方向（用于论文筛选与导读关联）", cfg["research_interests"])
    cfg["research_anchors"] = ask("研究关联锚点（导读「与你研究关联」一节引用）", cfg.get("research_anchors", ""))

    print("\n── ② 路径 ──")
    p = cfg["paths"]
    p["paper_root"] = ask("论文库根目录（六方向子目录的父目录）", p["paper_root"])
    p["paper_root_legacy"] = ask("v1 本地翻译管线的论文根（可同上）", p["paper_root_legacy"])
    p["workspace"] = ask("日报工作目录（out/ 与日报 HTML 所在）", p["workspace"])
    p["pdf2zh"] = ask("pdf2zh 工具链目录（v1 翻译依赖，可留空停用 v1）", p["pdf2zh"])
    p["secrets_dir"] = ask("凭据目录（mineru.json / minimax.json / serverchan_key）", p["secrets_dir"])
    p["python_hint"] = ask("装有 pypdf 的 python 解释器（仅提示用）", p["python_hint"])
    p["quark_cli"] = ask("夸克网盘 CLI 路径（不用夸克可留空停用同步）", p["quark_cli"])
    p["quark_skill_dir"] = ask("夸克 CLI skill 目录（不用夸克可留空）", p["quark_skill_dir"])

    print("\n── ③ 研究方向子目录 ──")
    print("每行一个中文子目录名，直接回车结束。当前：")
    for d in cfg["directions"]:
        print(f"  · {d}")
    dirs = list(cfg["directions"])
    while True:
        d = input("新增/修改方向（回车结束）: ").strip()
        if not d:
            break
        if d not in dirs:
            dirs.append(d)
    if dirs:
        cfg["directions"] = dirs
    print(f"最终方向：{cfg['directions']}")

    print("\n── ④ 翻译引擎 ──")
    t = cfg["translate"]
    t["model"] = ask("翻译模型", t["model"])
    t["base_url"] = ask("翻译 API base-url", t["base_url"])
    t["max_chunk"] = ask_int("每块字符数", t["max_chunk"])
    t["workers"] = ask_int("单篇翻译并发 workers", t["workers"])

    print("\n── ⑤ 夸克网盘目录 fid（不用夸克可一路回车）──")
    q = cfg["quark"]
    q["fid_report"] = ask("日报 HTML 目标目录 fid", q["fid_report"])
    print("六个方向各自的目标 fid：")
    for d in cfg["directions"]:
        q["fid_directions"][d] = ask(f"  {d} fid", q["fid_directions"].get(d, ""))

    write(cfg)
    print("\n✅ config.json 已写出。下一步：")
    print("  1) 把凭据放进 secrets_dir（mineru.json / minimax.json / serverchan_key）")
    print("  2) python3 scripts/render_automations.py  # 生成个性化定时任务定义")
    print("  3) python3 setup.py --check               # 校验")


def set_values(pairs):
    """--set k=v：点路径写入，v 按 JSON 解析（失败则当字符串）"""
    cfg = load()
    for item in pairs:
        if "=" not in item:
            sys.exit(f"--set 参数格式错误：{item}（应为 键=值）")
        k, _, v = item.partition("=")
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            pass
        node = cfg
        keys = k.split(".")
        for key in keys[:-1]:
            node = node.setdefault(key, {})
            if not isinstance(node, dict):
                sys.exit(f"--set 路径错误：{k}")
        node[keys[-1]] = v
        print(f"  ✓ {k} = {v!r}")
    write(cfg)


def write(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 已写出 {CONFIG_FILE}")


def check():
    cfg = load()
    problems = []
    p = cfg["paths"]
    if not Path(p["paper_root"]).is_dir():
        problems.append(f"论文库根不存在: {p['paper_root']}")
    if not Path(p["secrets_dir"]).is_dir():
        problems.append(f"凭据目录不存在: {p['secrets_dir']}（翻译/通知将不可用）")
    if not Path(p["pdf2zh"]).is_dir():
        print(f"  ⚠ pdf2zh 目录不存在（v1 翻译不可用，v2 云管线不受影响）: {p['pdf2zh']}")
    if len(cfg["directions"]) < 1:
        problems.append("directions 为空")
    if not Path(p["quark_cli"]).exists():
        print(f"  ⚠ 夸克 CLI 不存在（同步停用）: {p['quark_cli']}")
    mineru = Path(p["secrets_dir"]) / "mineru.json"
    minimax = Path(p["secrets_dir"]) / "minimax.json"
    if not mineru.exists():
        print(f"  ⚠ 缺 {mineru}（MinerU 云 OCR 不可用）")
    if not minimax.exists():
        print(f"  ⚠ 缺 {minimax}（MiniMax 翻译不可用，可改用 pdf2zh/.env）")
    if problems:
        print("\n✗ 配置有问题：")
        for x in problems:
            print(f"  ✗ {x}")
        sys.exit(1)
    print("✅ 配置校验通过（⚠ 为可选项缺失提示）")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", action="append", default=[], metavar="键=值",
                    help="非交互写入，点路径如 paths.paper_root=...（可多次）")
    ap.add_argument("--check", action="store_true", help="校验当前配置")
    args = ap.parse_args()
    if args.check:
        check()
    elif args.set:
        set_values(args.set)
    else:
        interactive()


if __name__ == "__main__":
    main()
