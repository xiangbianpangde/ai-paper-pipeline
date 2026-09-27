#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline_config.py — 全管线统一配置加载器

所有脚本的个人环境项（路径 / 方向命名 / 模型 / 夸克 FID / 凭据位置）统一从
仓库根的 config.json 读取，本模块提供加载与默认值兜底：

  CFG        # 合并后的配置 dict（config.json > DEFAULTS）
  P          # CFG["paths"] 快捷方式

新用户接入：运行 `python3 setup.py`（交互问答生成 config.json），
或由 AI 助手按 SETUP.md 的问询清单收集信息后代填。
指定其他配置文件：环境变量 PIPELINE_CONFIG=<path>。
"""
import json
import os
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent


def _default_config_file():
    env = os.environ.get("PIPELINE_CONFIG")
    return Path(env) if env else REPO_ROOT / "config.json"


# 默认值 = 当前作者（master）的真实环境，未提供 config.json 时按此运行
DEFAULTS = {
    "user": {"name": "master"},
    "research_interests": "Agent 与四大工程（context / prompt / harness / loop）、AI 医疗",
    # 研究关联锚点：生成翻译导读「与本人研究关联」一节时引用（个性化内容）
    "research_anchors": "medbrain、RAG/Graph/Harness/Loop 四件套、devguard",
    "paths": {
        # 论文库根（v2 云管线与运维工具使用；每篇论文一个中文夹，三件套）
        "paper_root": "/Users/xbpd/Documents/xbpd_obsidian/02. 🟡 归类 Arrange/论文",
        # v1 本地批量翻译管线的论文根（历史目录）
        "paper_root_legacy": "/Users/xbpd/Documents/论文",
        # 日报工作目录（out/ 与日报 HTML 所在）
        "workspace": "/Users/xbpd/WorkBuddy/每日早报",
        # pdf2zh 工具链目录（v1 翻译 + 导读生成依赖其 .env）
        "pdf2zh": "/Users/xbpd/Projects/pdf2zh",
        # 凭据目录（mineru.json / minimax.json / serverchan_key）
        "secrets_dir": "/Users/xbpd/WorkBuddy/每日早报/.secrets",
        # 文档提示用的 python 解释器路径（pypdf 等依赖所在环境）
        "python_hint": "/Users/xbpd/.workbuddy/binaries/python/envs/default/bin/python",
        # 夸克网盘 CLI 路径
        "quark_cli": "/Users/xbpd/.workbuddy/skills/quarkclouddrive/scripts/quark-drive.cjs",
        # 夸克 CLI skill 安装目录（自动化 prompt 中 install.sh 幂等安装用）
        "quark_skill_dir": "/Users/xbpd/.workbuddy/skills/quarkclouddrive",
    },
    # 六大研究方向中文子目录（顺序即展示顺序）
    "directions": [
        "01-智能体", "02-上下文工程", "03-提示词工程",
        "04-Harness执行框架", "05-循环工程", "06-AI医疗",
    ],
    # 判重索引等扫描全库的场景额外覆盖的方向（无日报检索需求）
    "extra_directions": ["07-教育AI与知识图谱", "08-通用AI与深度学习"],
    # arXiv 检索方向 -> 中文子目录映射
    "direction_map": {
        "agent": "01-智能体",
        "context": "02-上下文工程",
        "prompt": "03-提示词工程",
        "harness": "04-Harness执行框架",
        "loop": "05-循环工程",
        "medical": "06-AI医疗",
    },
    # 论文库内的特殊目录命名
    "special_dirs": {
        "pending_ocr": "00-待OCR转换",           # v2 翻译池：待 OCR 的 PDF 投放处
        "classify_99": "99-待分类(非方向)",       # 无法归入六方向的论文
        "skip": ["00-待OCR转换", "99-资料区(非论文·待定)"],  # 索引/巡检时跳过
    },
    # 翻译引擎参数
    "translate": {
        "model": "MiniMax-M3",
        "base_url": "https://api.minimaxi.com/v1",
        "max_chunk": 5500,   # 每块字符数
        "workers": 4,
    },
    # 夸克网盘目标目录 fid（仅文件夹标识，非凭据；新用户需替换为自己的）
    "quark": {
        "fid_report": "3d60cf75257744ffa1551c85e47da36f",
        "fid_directions": {
            "01-智能体": "05ab1fceb0ca498299b8aa3a9130d39a",
            "02-上下文工程": "ab6089fa815a436aab6973e7ccbc29c5",
            "03-提示词工程": "98df445d848f41b1871277c28840f2da",
            "04-Harness执行框架": "57b77d4b09854d1d942edc16bc26ba1c",
            "05-循环工程": "63f2f6b86c3c4ba38560ed8bd22d16db",
            "06-AI医疗": "4bcae7854bb243ddbf945e417d0965d2",
        },
    },
    # 微信通知（Server酱）
    "notify": {
        "serverchan_env": "SERVERCHAN_SEND_KEY",
        "serverchan_key_file": "serverchan_key",
    },
}


def _deep_merge(base: dict, extra: dict) -> dict:
    """递归合并：extra 覆盖 base，dict 深合并，其余类型直接覆盖"""
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def config_file() -> Path:
    """当前生效的配置文件路径（env PIPELINE_CONFIG 优先，否则仓库根 config.json）"""
    return _default_config_file()


def load(config_file=None) -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))  # 深拷贝默认值
    cf = Path(config_file) if config_file else _default_config_file()
    if cf.exists():
        try:
            user_cfg = json.loads(cf.read_text(encoding="utf-8"))
            _deep_merge(cfg, user_cfg)
        except Exception as e:  # 配置损坏时回退默认值，不让整条管线瘫痪
            print(f"[pipeline_config] ⚠ 读取 {cf} 失败（{e}），使用默认配置")
    return cfg


CFG = load()
P = CFG["paths"]

if __name__ == "__main__":
    # 诊断：打印当前生效配置（脱敏：不打印 secrets_dir 内容本身）
    print(json.dumps(CFG, ensure_ascii=False, indent=2))
