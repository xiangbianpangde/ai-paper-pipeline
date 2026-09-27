#!/usr/bin/env python3
"""paper_index.py — 论文库判重索引（按 PDF 内容哈希 + arXiv ID）

背景: translate_v2.py 原先用「LLM 译出的中文标题」判断论文是否已存在，
但同一篇论文每次译名都不同（ESPO 曾出现 5 个变体夹），导致重复建夹。
本模块改用与译名无关的强信号判重。

用法:
    from paper_index import find_duplicate
    dup, why = find_duplicate(pdf_path)      # (已有夹内 PDF 路径 | None, 原因)

行内缓存 .cache/paper_hash_index.json，按 (size, mtime) 增量更新，全库扫描仅首次较慢。
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from pipeline_config import CFG

ROOT = Path(CFG["paths"]["paper_root"])
DIRS = list(CFG["directions"]) + list(CFG.get("extra_directions", []))
CACHE = HERE.parent / ".cache" / "paper_hash_index.json"
SKIP_DIRS = set(CFG["special_dirs"]["skip"])

ARXIV_RE = re.compile(rb"arXiv:\s*(\d{4}\.\d{4,5})")
ARXIV_FN_RE = re.compile(r"(\d{4}\.\d{4,5})")


def md5_of(path: Path, cache: dict) -> str | None:
    """带缓存的文件 md5（缓存键=路径，失配条件=size/mtime 变化）。"""
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    hit = cache.get(key)
    if hit and hit.get("size") == st.st_size and abs(hit.get("mtime", 0) - st.st_mtime) < 1:
        return hit.get("h")
    try:
        h = hashlib.md5(path.read_bytes()).hexdigest()
    except OSError:
        return None
    cache[key] = {"h": h, "size": st.st_size, "mtime": st.st_mtime}
    return h


def arxiv_of(path: Path) -> str | None:
    """从 PDF 原始字节里抓 arXiv 编号（arXiv 会在首页左侧盖戳）。"""
    try:
        raw = path.read_bytes()[:400_000]
    except OSError:
        return None
    m = ARXIV_RE.search(raw)
    if m:
        return m.group(1).decode()
    m = ARXIV_FN_RE.search(path.stem)  # 退路：文件名里的编号
    return m.group(1) if m else None


def load_cache() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_cache(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache), encoding="utf-8")
    tmp.replace(CACHE)


def iter_library_pdfs(root: Path = ROOT, dirs=None, include_flat: bool = True):
    """产出库内所有论文 PDF（任意深度；排除解析目录里的 `*_origin.pdf`）。

    ⚠ 不再按硬编码 DIRS 两层遍历：库结构会变（「其他方向/」下嵌套方向目录、
      新增 07-Deepsearch 等），两层遍历会静默漏掉整批论文，影响去重判定的准确性。
    """
    root_depth = len(root.parts)
    skip = SKIP_DIRS | {"__pycache__"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in skip)
        rel = Path(dirpath).relative_to(root)
        if dirs and rel.parts and rel.parts[0] not in dirs:
            dirnames[:] = []
            continue
        if len(Path(dirpath).parts) - root_depth >= 3:
            dirnames[:] = []
        d = rel.parts[0] if len(rel.parts) > 1 else "."
        for fn in sorted(filenames):
            if fn.lower().endswith(".pdf") and not fn.endswith("_origin.pdf"):
                yield d, Path(dirpath), Path(dirpath) / fn


def build_index(root: Path = ROOT, dirs=None, cache: dict | None = None,
                persist: bool = True) -> tuple[dict, dict]:
    """返回 (hash -> [(dir, folder, pdf)], arxiv -> [(dir, folder, pdf)])。"""
    cache = cache if cache is not None else load_cache()
    by_hash, by_arxiv = {}, {}
    for d, folder, pdf in iter_library_pdfs(root, dirs):
        h = md5_of(pdf, cache)
        if h:
            by_hash.setdefault(h, []).append((d, folder, pdf))
        ax = arxiv_of(pdf)
        if ax:
            by_arxiv.setdefault(ax, []).append((d, folder, pdf))
    if persist:
        save_cache(cache)
    return by_hash, by_arxiv


def find_duplicate(pdf: Path, root: Path = ROOT, dirs=None) -> tuple[Path | None, str]:
    """判断该 PDF 是否已在库中。返回 (已有 PDF 路径, 原因) 或 (None, '')。"""
    pdf = Path(pdf)
    by_hash, by_arxiv = build_index(root, dirs)
    h = md5_of(pdf, load_cache())
    if h and h in by_hash:
        return by_hash[h][0][2], "PDF哈希相同"
    ax = arxiv_of(pdf)
    if ax and ax in by_arxiv:
        return by_arxiv[ax][0][2], f"arXiv:{ax} 已存在"
    return None, ""


def find_all_duplicates(root: Path = ROOT, dirs=None):
    """全库重复分组（含组内夹路径），用于清理。"""
    by_hash, _ = build_index(root, dirs)
    return {h: v for h, v in by_hash.items() if len(v) > 1}


if __name__ == "__main__":
    if "--build" in sys.argv:
        by_hash, by_arxiv = build_index()
        dup = {k: v for k, v in by_hash.items() if len(v) > 1}
        print(f"索引完成: {sum(len(v) for v in by_hash.values())} 个 PDF, "
              f"{len(by_arxiv)} 个 arXiv 号, 重复组 {len(dup)}")
    elif len(sys.argv) > 1:
        p = Path(sys.argv[1])
        d, why = find_duplicate(p)
        print(f"重复: {d}  ({why})" if d else "未发现重复")
    else:
        print(__doc__)
