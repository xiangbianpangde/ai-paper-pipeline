#!/usr/bin/env python3
"""complete_paper_folder.py — 把「半成品论文夹」补全为标准格式

标准格式（01-06 论文夹）:
  <方向>/<夹名>/
      <夹名>.pdf               论文原件
      <夹名>_全文翻译.md        MinerU云OCR + MiniMax-M3 全文中文翻译
      <夹名>_翻译导读.md        要点导读（本脚本只在缺失时补生成）
      <夹名>/                   MinerU 解析目录(full.md + images + content_list)

典型场景: 每日早报流水线只落了 PDF + 摘要导读，全文翻译被中断 —— 本脚本补齐。

用法:
  python3 scripts/complete_paper_folder.py "<论文夹绝对路径>" [--force] [--skip-guide]
  python3 scripts/complete_paper_folder.py --scan          # 扫描全库列出所有半成品

特性: 幂等（已存在的产物不重做）、失败不写半截文件、图片引用按夹名重写。
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path("/Users/xbpd/Documents/xbpd_obsidian/02. 🟡 归类 Arrange/论文")
# 兼容旧调用点的方向名清单（供展示用）；实际遍历一律走 paper_folders()
DIRS = ["01-智能体", "02-上下文工程", "03-提示词工程",
        "04-Harness执行框架", "05-循环工程", "06-AI医疗"]
WORK = Path("/tmp/v2_work")
# 「停放区」容器：内含的是 PDF-only 的论文（无译文/解析/导读），默认不参与自动补全
PARKED = {"其他方向"}
sys.path.insert(0, str(Path(__file__).parent))


# MinerU 解析目录里的中间产物 PDF，绝非论文原件，必须排除（否则解析目录会被误判为论文夹）
MINERU_PDF_SUFFIX = ("_origin.pdf", "_layout.pdf", "_span.pdf")


def has_paper_pdf(d: Path) -> bool:
    """目录内是否存在论文原件 PDF（排除 MinerU 中间产物）。"""
    try:
        for f in d.iterdir():
            n = f.name.lower()
            if n.endswith(".pdf") and not n.endswith(MINERU_PDF_SUFFIX):
                return True
    except OSError:
        pass
    return False


def paper_folders(depth: int = 3, include_parked: bool = True):
    """遍历全库论文夹：任意深度内含论文原件 PDF 的目录。

    ⚠ 不要再用硬编码 DIRS 遍历 —— 库结构会变（方向增删/搬迁到「其他方向/」等容器下），
      硬编码会静默漏扫。含 PDF 才视为论文夹，可自动排除榜单合集、资料区等非论文目录。

    include_parked=False 时跳过 PARKED 容器（如「其他方向/」的 PDF-only 停放区），
    供批处理使用 —— 那些夹没有译文/解析目录，默认不该被自动补全烧配额。
    """
    if not ROOT.is_dir():
        return
    root_depth = len(ROOT.parts)
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d != "__pycache__"]
        rel = Path(dirpath).relative_to(ROOT)
        if not include_parked and rel.parts and rel.parts[0] in PARKED:
            dirnames[:] = []
            continue
        # 只限制继续下探，不能跳过本层的 PDF 判定（否则第 depth 层的论文夹会被漏掉）
        if len(Path(dirpath).parts) - root_depth >= depth:
            dirnames[:] = []
        if has_paper_pdf(Path(dirpath)):
            yield Path(dirpath)


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


# ----------------------------------------------------------------- 结构探测
def find_pdf(folder: Path) -> Path | None:
    """优先取与夹名同名的 PDF，否则取夹内唯一 PDF。"""
    named = folder / f"{folder.name}.pdf"
    if named.exists():
        return named
    pdfs = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".pdf")
    return pdfs[0] if len(pdfs) == 1 else None


def parse_dir(folder: Path) -> Path | None:
    """定位解析目录。历史存在多种 MinerU 布局，均需识别：
      A) 扁平:        <夹>/<任意名>/full.md + images/
      B) auto:        <夹>/<任意名>/auto/<任意名>.md + images/      （旧云管线）
      C) hybrid_auto: <夹>/<任意名>/hybrid_auto/<任意名>.md + images/
    子目录命名亦不统一：夹名 / hash / 原slug 都有。

    ⚠ 必须覆盖 hybrid_auto：漏识别会被当成「无解析目录」→ 触发重跑 OCR →
       line 141+ 会 rmtree 掉既有解析目录，使旧译文里的图片引用集体断链。
    """
    subs = [folder / folder.name] + sorted(p for p in folder.iterdir() if p.is_dir())
    for sub in subs:
        if not sub.is_dir():
            continue
        if (sub / "full.md").exists():
            return sub
        for variant in ("auto", "hybrid_auto", "vlm", "ocr"):
            v = sub / variant
            if v.is_dir() and any(v.glob("*.md")):
                return sub
        # 兜底：二级目录内任意 md（防止再出现未列举的布局）
        if any(sub.glob("*/*.md")):
            return sub
    return None


def parse_md(pd: Path) -> Path | None:
    """取解析目录里的正文 markdown（兼容 full.md 与各代 auto 子目录）。"""
    if (pd / "full.md").exists():
        return pd / "full.md"
    for variant in ("auto", "hybrid_auto", "vlm", "ocr"):
        v = pd / variant
        if v.is_dir():
            mds = sorted(v.glob("*.md"))
            if mds:
                return mds[0]
    mds = sorted(pd.glob("*/*.md"))
    return mds[0] if mds else None


def survey(folder: Path) -> dict:
    pdf = find_pdf(folder)
    pd = parse_dir(folder)
    md = parse_md(pd) if pd else None
    return {
        "pdf": pdf,
        "parse_dir": pd,
        "full_md": md,
        "full_trans": folder / f"{folder.name}_全文翻译.md",
        "guide": folder / f"{folder.name}_翻译导读.md",
    }


# ----------------------------------------------------------------- 工具
IMG_REF_RE = re.compile(r"(!\[[^\]]*\]\(\s*)([^)]+?)(\s*\))")


def rewrite_img_refs(text: str, prefix: str) -> str:
    """把译文里的图片引用统一改写为 `<prefix>/<文件名>`（相对译文所在目录）。

    prefix 例：`<夹名>/images` 或 `<夹名>/auto/images`（旧布局）。
    只处理「未带该前缀」的引用，避免二次前缀；按 basename 归一，
    对 `images/x.jpg` / `auto/images/x.jpg` / `hybrid_auto/images/x.jpg` 均适用。
    """
    def sub(m: "re.Match[str]") -> str:
        ref = m.group(2)
        if ref.startswith(("http://", "https://", "data:")) or ref.startswith(f"{prefix}/"):
            return m.group(0)
        return f"{m.group(1)}{prefix}/{Path(ref).name}{m.group(3)}"
    return IMG_REF_RE.sub(sub, text)


def img_prefix(pd: Path, name: str) -> str:
    """解析目录内图片所在子目录 → 返回译文应使用的前缀。"""
    for variant in ("images", "auto/images", "hybrid_auto/images"):
        d = pd / variant
        if d.is_dir() and has_images(d):
            return f"{name}/{variant}"
    return f"{name}/images"


def trash(path: Path) -> None:
    """移入废纸篓（避免 rmtree 造成不可逆丢失）。"""
    import subprocess
    try:
        r = subprocess.run(["osascript", "-e",
                            f'tell application "Finder" to delete POSIX file "{path}"'],
                           capture_output=True, timeout=30)
        if r.returncode == 0:
            return
    except Exception:
        pass
    dest = Path.home() / ".Trash" / path.name
    i = 1
    while dest.exists():
        dest = Path.home() / ".Trash" / f"{path.name} ({i})"
        i += 1
    shutil.move(str(path), str(dest))


def has_images(d: Path) -> bool:
    return any(d.glob("**/*.png")) or any(d.glob("**/*.jpg")) or any(d.glob("**/*.jpeg"))


def scan_library(quiet: bool = False) -> list[Path]:
    """扫描全库，返回结构不完整的论文夹。"""
    incomplete = []
    for folder in sorted(paper_folders()):
        s = survey(folder)
        if not s["pdf"]:
            continue
        miss = []
        if not s["full_trans"].exists():
            miss.append("全文翻译")
        if not s["parse_dir"]:
            miss.append("解析目录")
        if not s["guide"].exists():
            miss.append("导读")
        if miss:
            incomplete.append(folder)
            if not quiet:
                log(f"⚠ {folder.relative_to(ROOT)}  缺: {'/'.join(miss)}")
    return incomplete


# ----------------------------------------------------------------- 主流程
def complete(folder: Path, force=False, skip_guide=False, ocr_only=False) -> bool:
    from generate_guides import api_key, gen_guide
    from mineru_client import precision_extract  # noqa: E402

    name = folder.name
    s = survey(folder)
    if not s["pdf"]:
        log(f"✗ 找不到唯一 PDF: {folder}")
        return False
    log(f"论文夹: {folder.parent.name}/{name}")

    # ---- 1) MinerU 云 OCR（解析目录）----
    pd = parse_dir(folder)
    need_ocr = force or pd is None
    if need_ocr:
        tmp = WORK / f"complete_{re.sub(r'[^0-9A-Za-z\u4e00-\u9fff]+', '_', name)[:40]}"
        if tmp.exists():
            shutil.rmtree(tmp)
        log("① MinerU 云 OCR 中…")
        rc = precision_extract(s["pdf"], tmp, model="vlm")
        if rc != 0 or not (tmp / "full.md").exists():
            # vlm 模型对部分长文档会稳定返回「parsing failed」（实测 45 页 ESWA
            # accepted manuscript 连续两次 vlm 失败、pipeline 一次成功），故自动降级重试一次。
            log(f"⚠ vlm 解析失败 (rc={rc})，降级为 pipeline 模型重试…")
            if tmp.exists():
                shutil.rmtree(tmp)
            rc = precision_extract(s["pdf"], tmp, model="pipeline")
        if rc != 0 or not (tmp / "full.md").exists():
            log(f"✗ OCR 失败 (rc={rc})，未写入任何产物")
            return False
        target = folder / name
        if target.exists():
            # ⚠ 绝不用 rmtree：既有解析目录里可能存有旧译文正在引用的图片。
            #   含图一律移入废纸篓（可回滚），并登记告警交由巡检复核。
            if has_images(target):
                trash(target)
                log(f"⚠ 旧解析目录含图片，已移入废纸篓（非删除）: {target.name}")
                with open("/tmp/parse_overwrite_warnings.jsonl", "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                         "folder": str(folder), "replaced": target.name,
                                         "translation": s["full_trans"].name,
                                         "translation_exists": s["full_trans"].exists()},
                                        ensure_ascii=False) + "\n")
            else:
                shutil.rmtree(target)
        shutil.move(str(tmp), str(target))
        pd = target
        log(f"① 解析目录就绪: {pd.name}/")
    else:
        log(f"① 解析目录已存在({pd.name})，跳过 OCR")

    src_md = parse_md(pd)
    if not src_md:
        log(f"✗ 解析目录内找不到正文 markdown: {pd}")
        return False

    if ocr_only:
        log("⏹ --ocr-only：OCR 阶段结束（译文/导读留待有凭据时补）")
        return True

    # ---- 2) 全文翻译 ----
    ft = s["full_trans"]
    prefix = img_prefix(pd, name)
    if force or not ft.exists():
        key = api_key()
        if not key:
            log("✗ 缺少 MiniMax API key（见 .secrets/minimax.json 或 pdf2zh/.env）")
            return False
        from translate_v2 import translate_md
        log("② MiniMax-M3 全文翻译中（分块串行，约 3-6 分钟）…")
        t = translate_md(src_md, key)
        if not t or len(t) < 200:
            log(f"✗ 译文过短({len(t or '')}B)，疑似失败，未写入")
            return False
        # 图片引用重写: images/x.jpg -> <夹名>/images/x.jpg（相对本 md 所在目录）
        ft.write_text(rewrite_img_refs(t, prefix), encoding="utf-8")
        log(f"② 全文翻译就绪: {ft.name} ({ft.stat().st_size}B)")
    elif need_ocr:
        # OCR 刚被重跑、译文沿用旧的 → 图片可能已换批次，引用会集体断链
        log("⚠ 解析目录刚被重建，但译文沿用旧版本 —— 图片引用可能失效。"
            "可用 scripts/check_image_refs.py 复核，或 --force 重译本夹。")
        with open("/tmp/parse_overwrite_warnings.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                 "folder": str(folder), "replaced": name,
                                 "translation": ft.name, "translation_exists": True},
                                ensure_ascii=False) + "\n")
    else:
        log("② 全文翻译已存在，跳过")

    # ---- 3) 导读 ----
    if not skip_guide and not s["guide"].exists():
        log("③ 生成翻译导读…")
        try:
            gen_guide(folder, folder.parent.name)
            log(f"③ 导读就绪: {s['guide'].name}")
        except Exception as e:
            log(f"⚠ 导读生成失败（不阻塞交付）: {e}")
    else:
        log("③ 导读已存在，跳过")

    log(f"✅ 完成: {folder.parent.name}/{name}")
    return True


def main():
    ap = argparse.ArgumentParser(description="补全半成品论文夹为标准格式")
    ap.add_argument("folder", nargs="?", help="论文夹绝对路径")
    ap.add_argument("--scan", action="store_true", help="扫描全库列出所有半成品论文夹")
    ap.add_argument("--force", action="store_true", help="强制重做（覆盖已有产物）")
    ap.add_argument("--skip-guide", action="store_true", help="不生成导读")
    ap.add_argument("--ocr-only", action="store_true", help="只做 OCR（不需 MiniMax 凭据，可先批量前置）")
    a = ap.parse_args()

    if a.scan:
        bad = scan_library()
        log(f"共 {len(bad)} 个半成品论文夹")
        return 0
    if not a.folder:
        ap.print_help()
        return 2
    folder = Path(a.folder).expanduser().resolve()
    if not folder.is_dir():
        log(f"✗ 不是目录: {folder}")
        return 2
    return 0 if complete(folder, a.force, a.skip_guide, a.ocr_only) else 1


if __name__ == "__main__":
    sys.exit(main())
