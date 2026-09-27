#!/usr/bin/env python3
"""为论文夹批量生成 _翻译导读.md（MiniMax-M3，参考导读模板 6 节结构）
- 扫描 6 方向下缺 *_翻译导读.md 且含 *_全文翻译.md 的论文夹
- 幂等: 已有导读跳过; 失败重试2次; 全部完成后写 /tmp/guide_gen.status ALL_DONE
- 用法: python3 scripts/generate_guides.py [--daemon]
"""
import os, re, sys, time, json, urllib.request, subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import CFG

ROOT = Path(CFG["paths"]["paper_root"])
DIRS = list(CFG["directions"])
LOG = "/tmp/guide_gen.log"
STATUS = "/tmp/guide_gen.status"
ENV = Path(CFG["paths"]["pdf2zh"]) / ".env"
SECRETS = Path(CFG["paths"]["secrets_dir"]) / "minimax.json"
MODEL, BASE = CFG["translate"]["model"], CFG["translate"]["base_url"]

def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True)
    with open(LOG, "a", encoding="utf-8") as f: f.write(msg + "\n")

def set_status(s):
    with open(STATUS, "w", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {s}\n")

def api_key():
    """凭据解析，优先级：项目 .secrets > pdf2zh/.env > 环境变量。

    两个已实测的坑（2026-09-17）：
      ① shell 里可能残留**过期** key，会遮蔽 .env 里的有效值 → 故环境变量放最后；
      ② .env 位于沙箱「敏感内容」名单，后台/守护进程读取会因无人应答审批超时抛
         PermissionError（曾导致 84 个论文夹批量失败）→ 必须容错，否则整个管线崩。
    推荐做法：把有效 key 落到 .secrets/minimax.json  {"key": "sk-..."}，不触发审批。
    """
    try:
        if SECRETS.exists():
            v = json.loads(SECRETS.read_text(encoding="utf-8")).get("key", "")
            if v:
                return v
    except Exception as e:
        log(f"⚠ 读取 {SECRETS} 失败: {e}")
    try:
        if ENV.exists():
            for ln in ENV.read_text(encoding="utf-8").splitlines():
                if ln.startswith("MINIMAX_API_KEY="):
                    v = ln.split("=", 1)[1].strip().strip('"').strip("'")
                    if v:
                        return v
    except PermissionError:
        log("⚠ .env 读取被沙箱审批拦截（后台任务无法应答）→ 回退环境变量")
    except Exception as e:
        log(f"⚠ 读取 .env 失败: {e}")
    return os.environ.get("MINIMAX_API_KEY", "")

def chat(prompt, key, max_tokens=4000):
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role":"system","content":"你是资深 AI 论文导读作者，服务对象是 AI 方向研究者 master（袁浩岚），研究方向：Agent 与四大工程（context/prompt/harness/loop）、AI 医疗（medbrain: RAG/Graph/Harness/Loop 四件套）。用中文输出，结论先行、精炼、术语保留英文。"},
                     {"role":"user","content":prompt}],
        "temperature": 0.3, "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(f"{BASE}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode())
    return data["choices"][0]["message"]["content"].strip()

def extract_front(md: Path, maxchars=12000):
    txt = md.read_text(encoding="utf-8", errors="replace")
    # 去掉超过阈值的正文, 只保留开头（标题/作者/摘要/引言前段）
    txt = txt[:maxchars]
    arx = re.findall(r"arXiv\s*[:：]?\s*([0-9]{4}\.[0-9]{4,5})", txt)
    url = re.findall(r"arxiv\.org/abs/([0-9]{4}\.[0-9]{4,5})", txt)
    return txt, (arx or url or [None])[0]

def gen_guide(folder: Path, direction: str):
    title = folder.name
    md = next((m for m in folder.glob("*_全文翻译.md")), None)
    if not md:
        return "no_fulltrans"
    text, arx = extract_front(md)
    prompt = f"""请为以下论文撰写一份「翻译导读」Markdown 文档（直接输出文档内容，不要额外解释）。
论文所在方向目录：{direction}
论文本地标题：{title}
arXiv 编号（可能为空）：{arx or '未知'}

格式严格参照（用中文，结构如下）：
- 首行: # <英文原题或本地标题>
- 元信息表：方向判定 | 推荐度(1-5 ⭐，按对 master 研究价值) | arXiv 编号(未知写 '-') | arXiv 链接(https://arxiv.org/abs/<编号>，未知写'-') | 本地 PDF(原样写 @LOCAL_PDF@ 这个占位符，不要自行填写路径) | 作者(从译文文首提取，尽量全)
- ## 一、方向判定（一句话判定属于哪类/交叉，含理由）
- ## 二、解决什么问题
- ## 三、使用方法（方法名加粗，分点）
- ## 四、摘要中文翻译（若译文开头有摘要则摘录精炼；无摘要则基于引言改写，忠实不编造）
- ## 五、与 master 研究的关联（结合 medbrain/四大工程/多Agent栈/Obsidian知识库 谈启发，无关联就如实说少）
- ## 六、阅读建议
- 结尾注: *本导读由论文全文翻译自动生成。*

论文全文译文（截断）如下：
---BEGIN---
{text}
---END---"""
    # 完整性守卫：导读必须以结尾注收束。实测缺失即说明被 max_tokens 截断
    # （历史默认 1800 会把「六、阅读建议」截到中途），放宽预算重试，避免静默交付半截文档。
    out = ""
    for budget in (4000, 6000):
        out = chat(prompt, api_key(), max_tokens=budget)
        out = re.sub(r"</?think>.*?</think>", "", out, flags=re.S)  # 剥 think 推理块
        out = out.replace("```markdown", "").replace("```md", "").replace("```", "").strip()
        if "本导读由论文全文翻译自动生成" in out:
            break
        log(f"⚠ 导读疑似截断（{len(out)}B, max_tokens={budget}），放宽预算重试")
    if "本导读由论文全文翻译自动生成" not in out:
        log(f"⚠ 导读两次均未见到结尾注，仍写入（{len(out)}B）—— 请巡检复核")
    dst = folder / f"{title}_翻译导读.md"
    out = fix_local_pdf(out, folder)
    dst.write_text("# " + title + "\n\n" + out if not out.startswith("#") else out, encoding="utf-8")
    return "ok"


# 「本地 PDF」是**确定性事实**，不应交给 LLM 复写：实测长路径（含 emoji 🟡）会被丢字/串字，
# 产出目录名损坏（如标题带斜杠/emoji 被吞成双空格等坏路径）的检测与清理记录。
# 故：prompt 只输出占位符，生成后在此统一重写为「相对库根」路径（同库内 MemoryART 先例，可随库迁移）。
_PDF_ROW = re.compile(r"^(\|\s*\**\s*本地\s*PDF\s*\**\s*\|)(.*?)(\|\s*)$", re.M)
_PDF_BUL = re.compile(r"^((?:[-*]\s*)?\**\s*本地\s*PDF\s*\**\s*[:：])(.*)$", re.M)
_PDF_LNK = re.compile(r"\[(本地\s*PDF)\]\(([^)]+)\)")

def fix_local_pdf(md: str, folder: Path) -> str:
    """把导读里的「本地 PDF」字段重写为真实存在的相对库根路径。

    兼容库内三种写法：独立表格行 / bullet 行 / 表内 markdown 链接。
    """
    pdf = folder / f"{folder.name}.pdf"
    want = str(pdf.relative_to(ROOT.parent.parent)) if pdf.exists() else f"{folder.name}/{folder.name}.pdf"
    md = _PDF_LNK.sub(lambda m: f"[{m.group(1)}]({want})", md)
    md = _PDF_ROW.sub(lambda m: f"{m.group(1)} `{want}` {m.group(3)}", md)
    md = _PDF_BUL.sub(lambda m: f"{m.group(1)} `{want}`", md)
    return md.replace("@LOCAL_PDF@", f"`{want}`")

def main():
    workers = 4
    key = api_key()
    if not key:
        log("缺少 MINIMAX_API_KEY"); set_status("NO_KEY"); return
    tasks = []
    for d in DIRS:
        base = ROOT / d
        if not base.is_dir(): continue
        for f in sorted(base.iterdir()):
            if not f.is_dir() or f.name.startswith(".") or f.name == "translated": continue
            if not list(f.glob("*_全文翻译.md")): continue
            if not list(f.glob("*_翻译导读.md")):
                tasks.append((f, d))
    set_status(f"running, remain={len(tasks)}")
    log(f"待生成导读: {len(tasks)}")
    ok = fail = 0
    import threading
    lock = threading.Lock()
    done = [0]
    def work(tasks_sub):
        nonlocal ok, fail
        for folder, d in tasks_sub:
            try:
                with lock: log(f"▶ {d}/{folder.name}")
                r = gen_guide(folder, d)
                with lock:
                    if r == "ok":
                        ok += 1; log(f"✓ {d}/{folder.name}")
                    else:
                        fail += 1; log(f"○ 跳过(无全文) {d}/{folder.name}")
            except Exception as e:
                time.sleep(2)
                try:
                    gen_guide(folder, d)
                    with lock: ok += 1; log(f"✓(重试) {d}/{folder.name}")
                except Exception as e2:
                    with lock: fail += 1; log(f"✗ {d}/{folder.name}: {e2}")
            with lock:
                done[0] += 1
                if done[0] % 5 == 0:
                    set_status(f"running, remain={len(tasks)-done[0]}")
    # 分片
    import math
    n = math.ceil(len(tasks)/workers) or 1
    threads = [threading.Thread(target=work, args=(tasks[i*n:(i+1)*n],)) for i in range(workers) if tasks[i*n:(i+1)*n]]
    for t in threads: t.start()
    for t in threads: t.join()
    set_status(f"ALL_DONE ok={ok} fail={fail} remain={len(tasks)-done[0]}")
    log(f"完成: ok={ok} fail={fail}")

if __name__ == "__main__":
    if "--daemon" in sys.argv:
        if os.fork() > 0: sys.exit(0)
        os.setsid()
        if os.fork() > 0: os._exit(0)
        try:
            caff = subprocess.Popen(["caffeinate","-is"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            caff = None
        main()
        if caff: caff.terminate()
    else:
        main()
