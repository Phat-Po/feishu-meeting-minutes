#!/usr/bin/env python3
"""飞书会议纪要自动总结：会议逐字稿 → AI 总结 → 飞书文档 + 卡片。

两个来源，自动都扫：
  1. 线上会议（vc）：飞书视频会议开完、生成了会议笔记的
  2. 上传录音（minutes）：用录音笔之类的设备录完上传，飞书转成妙记的

产出是一篇排版好的飞书文档：开头一张思维导图，然后是一句话、
关键决策、待办、金句，最后才是详细章节。

只依赖 Python 标准库和官方 lark-cli（https://github.com/larksuite/cli）。

用法:
  python3 meeting_summary.py                      # 正常跑一次（适合 cron / launchd）
  python3 meeting_summary.py --lookback 7         # 回看 7 天
  python3 meeting_summary.py --dry-run            # 只打印，不建文档、不发卡片、不写记录
  python3 meeting_summary.py --source minutes     # 只扫上传录音
  python3 meeting_summary.py --only <ID>,<ID>     # 只做指定的几条（跳过时长过滤）
  python3 meeting_summary.py --print-prompt       # 打印内置提示词
  python3 meeting_summary.py --self-test          # 自检

环境变量见 .env.example。
"""
from __future__ import annotations

import argparse
import atexit
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

MAX_CHARS = 45000          # 单次送入的逐字稿上限
CHUNK_ABOVE = 30000        # 超过这个字数就分块提炼再合并
CHUNK_SIZE = 14000         # 每块目标字数
MIN_CHARS = 600            # 逐字稿短于这个就跳过（多半是误触）
MIN_DURATION = 120         # 录音时长短于这个秒数就跳过
MAX_CHAPTERS = 16          # 详细章节最多留多少条，防止末尾拖太长

# ─────────────────────────── 提示词 ───────────────────────────

SCHEMA = """{
  "title": "这场会到底是什么，12-20 字，不要「会议」「讨论」这种空词",
  "one_line": "一句话说清这场会干了什么，40 字以内",
  "core_points": ["最关键的结论，三条，每条 30 字以内"],
  "modules": [{"name": "模块名，8 字以内", "icon": "图标名", "group": "属于哪一段", "items": [{"head": "结论，12 字以内", "sub": "补充说明，24 字以内"}]}],
  "decisions": [{"what": "定了什么", "why": "为什么这么定"}],
  "todos": [{"what": "要做什么", "who": "谁（没提就写未指定）", "when": "什么时候"}],
  "quotes": [{"text": "值得记的原话", "why": "为什么值得记"}],
  "summary": [{"topic": "议题名", "points": ["要点", "要点"]}],
  "chapters": [{"time": "MM:SS 或 HH:MM:SS", "title": "章节名", "note": "一句话概括"}]
}"""

DIAGRAM_RULE = """字段写法：

core_points：整场会最关键的 2-3 条结论，直接给判断和数字，不要复述讨论过程。

modules：2-8 个模块，每个模块
- name：模块名，8 字以内
- icon：从下面这个清单里挑一个最贴切的，只能写清单里的名字
  topic-discussion target success warning dangerous information document table
  bar-chart line-chart timer clock stopwatch calendar person business-user crowd
  store factory bank link share search settings code terminal lightning bookmark
  folder email video-call toolbox pipeline ud-tasks ud-flag
- group：这个模块属于哪一段，同一段的模块写一样的名字。整场会最多分 2-3 段，
  比如「讨论要点」「结论与后续」。只有一段时全部留空字符串
- items：2-5 条要点，head 写结论（12 字以内），sub 写说明（24 字以内，没有就留空）

要求：按会议实际顺序排列；不要写「其他」「杂项」这种凑数模块。

示例：
"core_points": ["主推三款各备 500 件", "11/8 前必须到货", "落选款转清库存"],
"modules": [
  {"name": "选品", "icon": "target", "group": "讨论要点",
   "items": [{"head": "主推三款", "sub": "集中预算"}, {"head": "落选转清库存", "sub": ""}]},
  {"name": "备货", "icon": "timer", "group": "结论与后续",
   "items": [{"head": "首批各 500 件", "sub": "11/8 前到货"}]}
]"""

PROMPT = """你是会议记录员。下面是一场会议的逐字稿。

请输出 JSON，结构严格如下：

""" + SCHEMA + """

""" + DIAGRAM_RULE + """

内容要求：
- 只写逐字稿里真实出现的，不要推测、不要补细节。
- decisions 只收真正拍板的事；没有就给空数组，不要硬凑。
- todos 里没明确说谁负责的，who 写「未指定」。
- quotes 挑 2-4 句最有信息量的原话。
- summary 收 5-8 个议题，每个议题 1-3 条要点，每条一句话。
- chapters 按时间顺序，最多 """ + str(MAX_CHAPTERS) + """ 条，每条一句话。
- 全文（不含 diagram）控制在 2000-3000 字，别写成流水账。
- 人名、机构名、专有名词照原样写，不要翻译。

只输出 JSON 本身，不要加解释、不要加代码块标记。

逐字稿：
"""

MAP_PROMPT = """下面是长会议逐字稿的第 {i}/{n} 段。

只提取事实，不要写总结性的散文。用 JSON 输出，结构严格如下：

{{
  "topics": ["这段讨论了哪些议题"],
  "decisions": [{{"what": "决定了什么", "why": "为什么", "quote": "依据的原话片段", "time": "时间点"}}],
  "todos": [{{"what": "要做什么", "owner": "谁（没说就写未指定）", "when": "什么时候"}}],
  "quotes": [{{"text": "值得记的原话", "time": "时间点", "why": "为什么值得记"}}],
  "open_questions": ["没解决的问题"]
}}

要求：只写这一段里真实出现的，没有的字段给空数组。不要推测。

逐字稿片段：
"""

REDUCE_PROMPT = """你是会议记录员。下面是一场长会议分段提取出来的事实记录（共 {n} 段）。

请把这些事实合并成一份完整的会议纪要，输出 JSON，结构严格如下：

{SCHEMA}

{DIAGRAM}

内容要求：
- 合并同一件事的重复记录，不要重复列。
- 分段记录里没有的内容一律不写，不要补充、不要推测。
- summary 收 5-8 个议题，每个 1-3 条要点；chapters 最多 {MAXCHAPTERS} 条。
- 全文（不含 diagram）控制在 2000-3000 字。
- 人名、机构名、专有名词照原样写。

只输出 JSON 本身，不要加解释、不要加代码块标记。

分段事实记录：
"""


# ─────────────────────────── 基础工具 ───────────────────────────

def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name) or default)
    except ValueError:
        return default


def lark_cli_path() -> str:
    return env("LARK_CLI") or shutil.which("lark-cli") or "lark-cli"


def state_path() -> Path:
    return Path(env("STATE_FILE") or Path(__file__).resolve().parent / "state.json")


def run(cmd: list[str], timeout: int = 300, stdin: str | None = None,
        cwd: str | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           input=stdin, cwd=cwd)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def lark(profile: str, args: list[str], timeout: int = 300,
         cwd: str | None = None) -> tuple[int, str]:
    cmd = [lark_cli_path()]
    if profile:
        cmd += ["--profile", profile]
    return run(cmd + args, timeout=timeout, cwd=cwd)


def parse_first_json(text: str):
    """从混合输出里取出第一个完整 JSON 对象。

    lark-cli 的进度提示走 stderr，run() 把 stdout+stderr 拼接后 JSON 可能被尾巴污染，
    所以用 raw_decode 只吃第一个对象，忽略后面多余内容。
    """
    i = text.find("{")
    if i < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[i:])[0]
    except Exception:  # noqa: BLE001
        return None


def parse_any_json(text: str):
    m = re.search(r"[\[{]", text)
    if not m:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[m.start():])[0]
    except Exception:  # noqa: BLE001
        return None


def lark_json(profile: str, args: list[str], timeout: int = 300) -> dict | None:
    rc, out = lark(profile, [*args, "--json"], timeout=timeout)
    if rc != 0:
        print(f"  [lark-cli 退出码 {rc}] {out[:200]}", file=sys.stderr)
    return parse_first_json(out)


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def profiles() -> list[str]:
    """要扫哪些账号。没配就自动扫所有已登录的账号。"""
    raw = env("LARK_PROFILES")
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    rc, out = run([lark_cli_path(), "profile", "list"])
    listed = parse_any_json(out) if rc == 0 else None
    if isinstance(listed, list):
        names = [p.get("name", "") for p in listed if isinstance(p, dict) and p.get("name")]
        if names:
            return names
    return [""]


class Lock:
    """同一时间只允许一个实例跑。

    批量处理一条长录音要好几分钟，很容易和每 10 分钟的定时任务撞上。
    撞上了两边都会处理同一条 —— 因为状态只在结束时才写盘 —— 于是生成重复文档。
    """

    def __init__(self) -> None:
        self.path = state_path().with_suffix(".lock")
        self.held = False

    def __enter__(self) -> "Lock":
        try:
            age = datetime.datetime.now().timestamp() - self.path.stat().st_mtime
            if age < 7200:                      # 两小时内算有效，超过就是残留
                print("  另一个实例正在跑，本次跳过（避免重复生成文档）")
                raise SystemExit(0)
            self.path.unlink()
        except FileNotFoundError:
            pass
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            self.held = True
        except FileExistsError:
            print("  另一个实例正在跑，本次跳过")
            raise SystemExit(0)
        return self

    def __exit__(self, *exc) -> None:
        if self.held:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def temp_workdir() -> str:
    """lark-cli 只允许往 cwd、/tmp、~/files 里写，TMPDIR 在 macOS 上会被拒。"""
    for base in ("/tmp", os.getcwd()):
        try:
            return tempfile.mkdtemp(prefix="feishu-minutes-", dir=base)
        except Exception:  # noqa: BLE001
            continue
    return tempfile.mkdtemp(prefix="feishu-minutes-")


# ─────────────────────────── 去重记录 ───────────────────────────

def load_state() -> dict:
    try:
        with open(state_path(), encoding="utf-8") as f:
            s = json.load(f)
    except Exception:  # noqa: BLE001
        s = {}
    s.setdefault("processed", [])
    s.setdefault("legacy_ids", [])
    for old in s.get("processed_notes", []) or []:
        if old not in s["legacy_ids"]:
            s["legacy_ids"].append(old)
    return s


def save_state(s: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


# ─────────────────────────── 模型调用 ───────────────────────────

def normalize_base(base: str) -> str:
    """补上常见的路径后缀。

    DeepSeek 的 Anthropic 兼容端点挂在 /anthropic 下面，但很多人（包括不少
    现成配置）只填了 https://api.deepseek.com，于是请求打到 /v1/messages 直接 404。
    """
    base = base.rstrip("/")
    if "api.deepseek.com" in base and not base.endswith("/anthropic"):
        return base + "/anthropic"
    return base


def ask_model(prompt: str) -> str | None:
    cmd = env("SUMMARY_CMD")
    if cmd:
        try:
            p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                               timeout=900, input=prompt)
        except Exception as e:  # noqa: BLE001
            print(f"  [SUMMARY_CMD 失败] {str(e)[:200]}", file=sys.stderr)
            return None
        # 命令失败必须用非 0 退出码表示，否则报错信息会被当成会议纪要发出去
        if p.returncode != 0:
            print(f"  [SUMMARY_CMD 失败] rc={p.returncode} {p.stderr.strip()[:300]}", file=sys.stderr)
            return None
        out = (p.stdout or "").strip()
        if not out:
            print(f"  [SUMMARY_CMD 无输出] {p.stderr.strip()[:300]}", file=sys.stderr)
            return None
        return out

    base = normalize_base(env("AI_BASE_URL"))
    key = env("AI_API_KEY")
    model = env("AI_MODEL")
    if not (base and key and model):
        print("  [缺少模型配置] 要么设 SUMMARY_CMD，要么同时设 AI_BASE_URL / AI_API_KEY / AI_MODEL",
              file=sys.stderr)
        return None

    body = json.dumps({
        "model": model,
        # 部分推理模型会先输出 thinking 块；额度太小会被吃光，拿不到正文
        "max_tokens": env_int("AI_MAX_TOKENS", 16000),
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        base + "/v1/messages",
        data=body,
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"},
    )
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            d = json.loads(r.read())
        for blk in d.get("content", []):
            if blk.get("type") == "text":
                return blk.get("text", "").strip()
        types = [b.get("type") for b in d.get("content", [])]
        stop = d.get("stop_reason")
        print(f"  [AI 无正文] blocks={types} stop={stop}", file=sys.stderr)
        if "thinking" in types and stop == "max_tokens":
            print("  → 思考块把输出额度用光了。调大 AI_MAX_TOKENS（比如 32000）再试。",
                  file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"  [AI 失败] {str(e)[:200]}", file=sys.stderr)
    return None


def ask_json(prompt: str) -> dict | None:
    raw = ask_model(prompt)
    if not raw:
        return None
    d = parse_first_json(raw)
    if not isinstance(d, dict):
        print("  [模型没返回合法 JSON]", file=sys.stderr)
        return None
    return d


# ─────────────────────────── 逐字稿处理 ───────────────────────────

def strip_html(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def hhmmss(secs: int) -> str:
    return f"[{secs // 3600:02d}:{(secs % 3600) // 60:02d}:{secs % 60:02d}]"


def clean_transcript(text: str, anchor_every: int = 300) -> str:
    """去掉纯时间戳行，每 5 分钟留一个锚点；合并超短碎句。"""
    out: list[str] = []
    last_anchor = -10**9
    for line in (l.strip() for l in text.split("\n")):
        if not line:
            continue
        m = re.fullmatch(r"(\d{1,2}):(\d{2}):(\d{2})(?:\.\d+)?", line)
        if m:
            secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if secs - last_anchor >= anchor_every:
                out.append(hhmmss(secs))
                last_anchor = secs
            continue
        m = re.match(r"^(Speaker\s*\d+)\s+(\d{1,2}):(\d{2}):(\d{2})(?:\.\d+)?\s*$", line)
        if m:
            secs = int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4))
            out.append(f"{m.group(1)} {hhmmss(secs)}")
            continue
        if out and len(line) <= 6 and len(out[-1]) <= 20:
            out[-1] = out[-1] + line
            continue
        out.append(line)
    return "\n".join(out)


def chunk_text(text: str, size: int) -> list[str]:
    lines = text.split("\n")
    chunks: list[str] = []
    buf: list[str] = []
    n = 0
    for line in lines:
        buf.append(line)
        n += len(line) + 1
        if n >= size:
            chunks.append("\n".join(buf))
            buf, n = [], 0
    if buf:
        chunks.append("\n".join(buf))
    return [c for c in chunks if c.strip()]


def map_reduce(transcript: str) -> dict | None:
    chunks = chunk_text(transcript, CHUNK_SIZE)
    print(f"  分块提炼：{len(transcript)} 字 → {len(chunks)} 块")
    facts: list[str] = []
    for i, part in enumerate(chunks, 1):
        d = ask_json(MAP_PROMPT.format(i=i, n=len(chunks)) + part)
        if d is None:
            raw = ask_model(MAP_PROMPT.format(i=i, n=len(chunks)) + part)
            if not raw:
                print(f"  第 {i} 块提炼失败，跳过", file=sys.stderr)
                continue
            facts.append(raw)
        else:
            facts.append(json.dumps(d, ensure_ascii=False))
        print(f"  第 {i}/{len(chunks)} 块完成")
    if not facts:
        return None
    return ask_json(REDUCE_PROMPT.format(n=len(facts), SCHEMA=SCHEMA, DIAGRAM=DIAGRAM_RULE,
                                         MAXCHAPTERS=MAX_CHAPTERS)
                    + "\n\n".join(facts))


def summarize(transcript: str) -> dict | None:
    if len(transcript) > CHUNK_ABOVE:
        return map_reduce(transcript)
    return ask_json(PROMPT + transcript[-MAX_CHARS:])


# ─────────────────────────── 两个数据源 ───────────────────────────

def _dig(obj, path):
    cur = obj
    for k in path:
        cur = (cur or {}).get(k) if isinstance(cur, dict) else None
    return cur


def page_all(profile: str, args: list[str], path=("data", "items")) -> list[dict]:
    """把分页拉完。接口一页最多 30 条，不翻页会静默漏数据。"""
    out: list[dict] = []
    token = ""
    while True:
        page_args = [*args, "--page-size", "30"]
        if token:
            page_args += ["--page-token", token]
        d = lark_json(profile, page_args)
        if not d or not d.get("ok"):
            if not out:
                print(f"  [查询失败] {str(d)[:160]}", file=sys.stderr)
            break
        data = d.get("data") or {}
        out += _dig(d, path) or []
        token = data.get("page_token") or ""
        if not data.get("has_more") or not token:
            break
    return out


def scan_vc(profile: str, days: int) -> list[dict]:
    """线上会议：只有生成了会议笔记的才有逐字稿。"""
    end = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    start = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    out = []
    for m in page_all(profile, ["vc", "+search", "--as", "user", "--start", start, "--end", end]):
        mid = m.get("id")
        if not mid:
            continue
        det = lark_json(profile, ["vc", "+detail", "--as", "user", "--meeting-ids", str(mid)])
        if not det or not det.get("ok"):
            continue
        for mt in det.get("data", {}).get("meetings", []):
            note_id = mt.get("note_id")
            if not note_id:
                continue
            out.append({
                "profile": profile, "source": "vc", "id": str(note_id),
                "title": mt.get("topic") or "会议",
                "when": mt.get("start_time") or "",
                "ref": note_id,
            })
    return out


def parse_duration_seconds(desc: str) -> int:
    total = 0
    for num, unit in re.findall(r"(\d+)\s*(小时|分钟|分|秒)", desc or ""):
        n = int(num)
        total += n * 3600 if unit == "小时" else n * 60 if unit in ("分钟", "分") else n
    return total


def scan_minutes(profile: str, days: int) -> list[dict]:
    """上传录音：飞书转成的妙记。"""
    end = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    start = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    out = []
    for it in page_all(profile, ["minutes", "+search", "--as", "user", "--owner-ids", "me",
                                 "--start", start, "--end", end]):
        token = it.get("token")
        if not token:
            continue
        desc = (it.get("meta_data") or {}).get("description", "")
        title = re.sub(r"<[^>]+>", "", it.get("display_info", "")).split("\n")[0].strip()
        m = re.search(r"开始时间:\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", desc)
        when = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""
        out.append({
            "profile": profile, "source": "minutes", "id": str(token),
            "title": title or "录音", "when": when, "ref": token,
            "duration": parse_duration_seconds(desc),
            "app_link": (it.get("meta_data") or {}).get("app_link", ""),
        })
    return out


def fetch_transcript(src: dict) -> str:
    """把两种来源都变成一段纯文本。"""
    profile = src["profile"]
    if src["source"] == "vc":
        nd = lark_json(profile, ["note", "+detail", "--as", "user", "--note-id", src["id"]])
        if not nd or not nd.get("ok"):
            return ""
        note = nd.get("data", {}).get("note", {})
        doc = note.get("verbatim_doc_token") or note.get("note_doc_token")
        if not doc:
            return ""
        rc, out = lark(profile, ["docs", "+fetch", "--as", "user", "--doc", str(doc),
                                 "--doc-format", "markdown", "--json"])
        parsed = parse_first_json(out)
        if not parsed:
            return ""
        try:
            return strip_html(parsed["data"]["document"]["content"])
        except Exception:  # noqa: BLE001
            return ""

    tmp = temp_workdir()
    d = lark_json(profile, ["minutes", "+detail", "--as", "user", "--minute-tokens", src["id"],
                            "--transcript", "--output-dir", tmp, "--json"])
    if not d or not d.get("ok"):
        return ""
    try:
        path = Path(d["data"]["minutes"][0]["artifacts"]["transcript_file"])
        if not path.is_absolute():
            path = Path.cwd() / path
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────── 渲染成飞书文档 ───────────────────────────

def esc(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")).strip()


PALETTE = [("#EAF4FF", "#3B82F6", "#1E3A5F"), ("#EAFBF2", "#10B981", "#065F46"),
           ("#FFF7EA", "#F59E0B", "#92400E"), ("#F3F4F6", "#9CA3AF", "#374151")]


def label_text(s, limit: int = 20) -> str:
    """方框里的文字。不能带英文双引号，否则 Mermaid 语法断掉。"""
    t = re.sub(r'["`\n\r]', " ", str(s or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit]


CARD_PALETTE = [
    ("#F1F0FF", "#C7C5F5", "#4F46E5"),
    ("#EAF4FF", "#BFD9F7", "#1D4ED8"),
    ("#F0FBF3", "#C3E8CF", "#047857"),
    ("#FFF7EA", "#F3DDB3", "#B45309"),
    ("#FDF2F6", "#F5C9D9", "#BE185D"),
    ("#F3F4F6", "#D7D9DE", "#374151"),
]

ICON_SET = {
    "topic-discussion", "target", "success", "warning", "dangerous", "information",
    "document", "table", "bar-chart", "line-chart", "timer", "clock", "stopwatch",
    "calendar", "person", "business-user", "crowd", "store", "factory", "bank",
    "link", "share", "search", "settings", "code", "terminal", "lightning",
    "bookmark", "folder", "email", "video-call", "toolbox", "pipeline",
    "ud-tasks", "ud-flag",
}


def _txt(text: str, size: int, color: str, width: str = "fill-container") -> dict:
    return {"type": "text", "width": width, "height": "fit-content",
            "text": label_text(text, 60), "fontSize": size, "textColor": color}


def _card(mod: dict, idx: int) -> dict:
    fill, border, title_color = CARD_PALETTE[idx % len(CARD_PALETTE)]
    icon = mod.get("icon") if mod.get("icon") in ICON_SET else "topic-discussion"
    head = {"type": "frame", "width": "fill-container", "height": "fit-content",
            "layout": "horizontal", "gap": 8, "padding": 0, "alignItems": "center",
            "children": [
                {"type": "icon", "name": icon, "width": 18, "height": 18},
                _txt(mod.get("name", ""), 15, title_color),
            ]}
    kids = [head]
    for it in (mod.get("items") or [])[:5]:
        h = label_text(it.get("head"), 20)
        sub = label_text(it.get("sub"), 30)
        line = f"• {h}" + (f"：{sub}" if sub else "")
        kids.append(_txt(line, 13, "#374151"))
    return {"type": "frame", "width": "fill-container", "height": "fit-content",
            "layout": "vertical", "gap": 10, "padding": [16, 16, 16, 16],
            "fillColor": fill, "borderColor": border, "borderWidth": 1,
            "borderRadius": 12, "children": kids}


def build_board_dsl(data: dict, width: int = 1120) -> dict | None:
    """把模块按「核心结论 + 分段卡片」排成飞书白板 DSL。没有模块就返回 None。"""
    mods = [m for m in (data.get("modules") or []) if isinstance(m, dict)][:8]
    if not mods:
        return None

    nodes: list[dict] = []
    title = label_text(data.get("title"), 40)
    if title:
        nodes.append(_txt(title, 24, "#111827"))

    core = [label_text(p, 60) for p in (data.get("core_points") or []) if label_text(p, 60)]
    if core:
        kids = [{"type": "frame", "width": "fill-container", "height": "fit-content",
                 "layout": "horizontal", "gap": 8, "padding": 0, "alignItems": "center",
                 "children": [{"type": "icon", "name": "information", "width": 16, "height": 16},
                              _txt("核心结论", 14, "#6B7280")]}]
        kids += [_txt(f"• {c}", 13, "#374151") for c in core[:3]]
        nodes.append({"type": "frame", "width": "fill-container", "height": "fit-content",
                      "layout": "vertical", "gap": 8, "padding": [16, 18, 16, 18],
                      "fillColor": "#F5F5F4", "borderRadius": 12, "children": kids})

    order: list[str] = []
    groups: dict[str, list[dict]] = {}
    for m in mods:
        g = label_text(m.get("group"), 12)
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append(m)

    idx = 0
    for g in order:
        if g:
            nodes.append(_txt(g, 16, "#6B7280"))
        cards = []
        for m in groups[g]:
            cards.append(_card(m, idx))
            idx += 1
        for i in range(0, len(cards), 3):
            nodes.append({"type": "frame", "width": "fill-container", "height": "fit-content",
                          "layout": "horizontal", "gap": 16, "padding": 0,
                          "alignItems": "stretch", "children": cards[i:i + 3]})

    return {"version": 2, "nodes": [{
        "type": "frame", "width": width, "height": "fit-content",
        "layout": "vertical", "gap": 20, "padding": 0, "children": nodes}]}


def to_openapi(dsl: dict) -> dict | None:
    """用官方 whiteboard-cli 把 DSL 转成飞书白板接口认的格式。转换失败返回 None。"""
    tmp = temp_workdir()
    try:
        Path(tmp, "d.json").write_text(json.dumps(dsl, ensure_ascii=False), encoding="utf-8")
        rc, out = run(["npx", "-y", "@larksuite/whiteboard-cli@0.2.13",
                       "-i", "d.json", "--to", "openapi", "--format", "json"],
                      timeout=180, cwd=tmp)
        if rc != 0:
            print(f"  [白板转换失败] {out[:200]}", file=sys.stderr)
            return None
        d = parse_first_json(out)
        return d if isinstance(d, dict) and d.get("data") else None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build_mermaid_fallback(mods: list[dict]) -> str:
    """白板写不进去时的退路：用模块名拼一张简单的分组图。"""
    names = [label_text(m.get("name"), 14) for m in (mods or []) if label_text(m.get("name"), 14)]
    names = names[:4] or ["会议内容"]
    out = ["flowchart TB"]
    for i, t in enumerate(names, 1):
        fill, stroke, _ = PALETTE[(i - 1) % len(PALETTE)]
        out.append(f'  subgraph G{i}["{t}"]')
        out.append(f'    G{i}A["{t}"]')
        out.append("  end")
        out.append(f"  style G{i} fill:{fill},stroke:{stroke}")
    for i in range(1, len(names)):
        out.append(f"  G{i} --> G{i + 1}")
    return "\n".join(out)


def time_to_ms(text: str) -> int | None:
    """把 MM:SS 或 HH:MM:SS 转成毫秒；转不了返回 None。"""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*", str(text or ""))
    if not m:
        return None
    a, b, c = m.group(1), m.group(2), m.group(3)
    secs = int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)
    return secs * 1000


def render_doc(data: dict, meta: dict, board_xml: str = "") -> tuple[str, str]:
    """把模型返回的结构渲染成飞书文档 XML。返回 (标题, XML)。"""
    title_core = str(data.get("title") or "").strip() or meta.get("title", "会议")
    title_core = re.sub(r"\s+", " ", title_core)[:40]
    date = meta.get("when", "")
    doc_title = f"会议纪要：{title_core}" + (f" {date}" if date else "")

    parts: list[str] = [f"<title>{esc(doc_title)}</title>"]

    if board_xml:
        parts.append(board_xml)

    one = str(data.get("one_line") or "").strip()
    audio = str(meta.get("audio_url") or "").strip()
    if one or audio:
        inner = ""
        if one:
            inner += f"<p><b>一句话</b>：{esc(one)}</p>"
        if audio:
            inner += (f'<p>🎧 <a type="url-preview" href="{esc(audio)}">'
                      "听原始录音（点章节时间可直接跳到那一刻）</a></p>")
        parts.append(f'<callout emoji="📌" background-color="light-blue">{inner}</callout>')

    decisions = data.get("decisions") or []
    todos = data.get("todos") or []
    if decisions or todos:
        left: list[str] = ['<callout emoji="✅" background-color="light-green">',
                           "<p><b>关键决策</b></p>"]
        if decisions:
            left.append("<ul>")
            for d in decisions[:6]:
                w = esc(d.get("what", ""))
                why = esc(d.get("why", ""))
                left.append(f"<li><b>{w}</b>" + (f" —— {why}" if why else "") + "</li>")
            left.append("</ul>")
        else:
            left.append("<p>本次为分享/讨论类，无关键决策。</p>")
        left.append("</callout>")

        right: list[str] = ['<callout emoji="📋" background-color="light-blue">',
                            "<p><b>待办</b></p>"]
        if todos:
            for t in todos[:8]:
                w = esc(t.get("what", ""))
                who = esc(t.get("who", "")) or "未指定"
                when = esc(t.get("when", ""))
                tail = " · ".join(x for x in (who, when) if x)
                right.append(f'<checkbox done="false">{w}（{tail}）</checkbox>')
        else:
            right.append("<p>无明确待办。</p>")
        right.append("</callout>")

        parts.append("<grid>"
                     f'<column width-ratio="0.5">{"".join(left)}</column>'
                     f'<column width-ratio="0.5">{"".join(right)}</column>'
                     "</grid>")

    quotes = data.get("quotes") or []
    if quotes:
        parts.append("<h1>💬 金句时刻</h1>")
        for q in quotes[:4]:
            parts.append(f"<blockquote><p>「{esc(q.get('text', ''))}」</p>"
                         f"<p>—— {esc(q.get('why', ''))}</p></blockquote>")

    summary = data.get("summary") or []
    if summary:
        parts.append("<h1>总结</h1>")
        for s in summary[:8]:
            topic = esc(s.get("topic", ""))
            if topic:
                parts.append(f"<h2>{topic}</h2>")
            points = [esc(p) for p in (s.get("points") or []) if esc(p)]
            if points:
                parts.append("<ul>" + "".join(f"<li>{p}</li>" for p in points[:4]) + "</ul>")

    chapters = data.get("chapters") or []
    if chapters:
        parts.append("<h1>详细章节</h1>")
        parts.append("<table><thead><tr>"
                     '<th background-color="light-gray"><p>时间</p></th>'
                     '<th background-color="light-gray"><p>章节</p></th>'
                     '<th background-color="light-gray"><p>讲了什么</p></th>'
                     "</tr></thead><tbody>")
        for c in chapters[:MAX_CHAPTERS]:
            t_raw = esc(c.get("time", ""))
            # 时间戳做成跳转链接：点一下直接跳到妙记里那一刻开始播
            ms = time_to_ms(c.get("time", ""))
            if audio and ms is not None:
                t_cell = f'<a href="{esc(audio)}?t={ms}">{t_raw}</a>'
            else:
                t_cell = t_raw
            parts.append(f"<tr><td><p>{t_cell}</p></td>"
                         f"<td><p><b>{esc(c.get('title', ''))}</b></p></td>"
                         f"<td><p>{esc(c.get('note', ''))}</p></td></tr>")
        parts.append("</tbody></table>")

    parts.append("<hr/>")
    foot = " · ".join(x for x in [
        esc(meta.get("title", "")),
        f"时长 {meta['minutes']} 分钟" if meta.get("minutes") else "",
        f"逐字稿 {meta['chars']} 字" if meta.get("chars") else "",
        esc(meta.get("when", "")),
    ] if x)
    parts.append(f'<p><span text-color="gray">{foot}</span></p>')

    return doc_title, "\n".join(parts)


def create_doc(profile: str, title: str, xml: str) -> tuple[str, str]:
    """在录音所属账号的云盘根目录新建文档，返回 (链接, 画板块 token)。"""
    cmd = [lark_cli_path()] + (["--profile", profile] if profile else []) + [
        "docs", "+create", "--as", "user", "--title", title,
        "--content", "-", "--doc-format", "xml",
        "--parent-position", "my_library", "--json",
    ]
    rc, out = run(cmd, timeout=300, stdin=xml)
    d = parse_first_json(out)
    if not d or not d.get("ok"):
        print(f"  [建文档失败] {out[:300]}", file=sys.stderr)
        return ""
    for w in d.get("data", {}).get("warnings", []) or []:
        print(f"  [文档警告] {w}", file=sys.stderr)
    doc = d.get("data", {}).get("document", {}) or {}
    board = ""
    for b in doc.get("new_blocks", []) or []:
        if b.get("block_type") == "whiteboard" and b.get("block_token"):
            board = b["block_token"]
            break
    return doc.get("url", ""), board


def write_board(profile: str, board_token: str, openapi: dict) -> bool:
    """把排好版的白板内容写进文档里的画板块。"""
    tmp = temp_workdir()
    try:
        Path(tmp, "b.json").write_text(json.dumps(openapi, ensure_ascii=False), encoding="utf-8")
        # @file 是相对 CLI 工作目录解析的，必须让它在临时目录里执行
        rc, out = lark(profile, ["whiteboard", "+update", "--as", "user",
                                 "--whiteboard-token", board_token,
                                 "--source", "@b.json", "--input_format", "raw", "--json"],
                       timeout=180, cwd=tmp)
        ok = rc == 0 and '"ok": true' in out
        if not ok:
            print(f"  [白板写入失败] {out[:200]}", file=sys.stderr)
        return ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def own_open_id(profile: str) -> str:
    override = env("OPERATOR_OPEN_ID")
    if override:
        return override
    d = lark_json(profile, ["contact", "+get-user", "--as", "user"])
    if d and d.get("ok"):
        return (d.get("data", {}).get("user", {}) or {}).get("open_id", "")
    return ""


def card_payload(title: str, digest: str, url: str) -> dict:
    elements: list[dict] = [{"tag": "div", "text": {"tag": "lark_md", "content": digest[:2800]}}]
    if url:
        elements.append({
            "tag": "action",
            "actions": [{
                "tag": "button", "type": "primary",
                "text": {"tag": "plain_text", "content": "看完整纪要"},
                "url": url,
            }],
        })
    elements.append({"tag": "note", "elements": [
        {"tag": "plain_text", "content": "会议自动总结"}]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": title[:60]}},
        "elements": elements,
    }


def send_card(profile: str, title: str, digest: str, url: str) -> bool:
    open_id = own_open_id(profile)
    if not open_id:
        print("  [跳过卡片] 拿不到当前账号的用户 ID", file=sys.stderr)
        return False
    content = json.dumps(card_payload(title, digest, url), ensure_ascii=False)
    base = ["im", "+messages-send", "--user-id", open_id, "--msg-type", "interactive",
            "--content", content, "--json"]
    for identity in ("bot", "user"):
        rc, out = lark(profile, [*base, "--as", identity], timeout=180)
        if rc == 0 and '"ok": true' in out:
            return True
        if identity == "bot":
            print(f"  [机器人发送失败，改用用户身份] {out[:160]}", file=sys.stderr)
    print(f"  [卡片发送失败] {out[:200]}", file=sys.stderr)
    return False


def card_digest(data: dict) -> str:
    lines = [f"**{data.get('one_line', '')}**"]
    for d in (data.get("decisions") or [])[:3]:
        if d.get("what"):
            lines.append(f"- {d['what']}")
    return "\n".join(x for x in lines if x)


# ─────────────────────────── 自检 ───────────────────────────

def self_test() -> int:
    assert strip_html("<p>a<br/>b</p>") == "a\nb"
    assert parse_first_json('{"ok": true}\n[vc +detail] querying…') == {"ok": True}
    assert parse_first_json("no json here") is None
    assert parse_any_json('[{"name":"a"},{"name":"b"}]') == [{"name": "a"}, {"name": "b"}]
    assert _dig({"data": {"items": [1, 2]}}, ("data", "items")) == [1, 2]
    assert _dig({"data": {}}, ("data", "items")) is None

    assert parse_duration_seconds("时长: 2 小时 24 分 11 秒") == 2 * 3600 + 24 * 60 + 11
    assert parse_duration_seconds("时长: 4 秒") == 4
    assert parse_duration_seconds("没有时长") == 0

    raw = "00:00:07\n哦呦。\n00:00:10\n啊，那好啊。\n00:06:00\n这段要保留。"
    cleaned = clean_transcript(raw)
    assert "[00:00:07]" in cleaned and "[00:06:00]" in cleaned
    assert "这段要保留" in cleaned
    assert hhmmss(3700) == "[01:01:40]"
    assert time_to_ms("12:30") == 750000
    assert time_to_ms("1:02:03") == 3723000
    assert time_to_ms("不是时间") is None

    spk = "Speaker 1 00:00:00.590 \n关了吗？\nSpeaker 2 00:01:05.100 \n第二节。"
    c2 = clean_transcript(spk)
    assert "Speaker 1 [00:00:00]" in c2 and "Speaker 2 [00:01:05]" in c2

    chunks = chunk_text("x" * 100 + "\n" + "y" * 100, 100)
    assert len(chunks) == 2
    assert chunk_text("单个短块", 1000) == ["单个短块"]

    # 思维导图节点不能带括号引号，否则整张图渲染失败
    mods = [{"name": "甲", "icon": "target", "group": "讨论", "items": [{"head": "要点一", "sub": "说明"}]},
            {"name": "乙", "icon": "timer", "group": "讨论", "items": [{"head": "要点二", "sub": ""}]},
            {"name": "丙", "icon": "瞎写的图标", "group": "后续", "items": [{"head": "要点三", "sub": "说明"}]}]
    dsl = build_board_dsl({"title": "标题", "core_points": ["结论一", "结论二"], "modules": mods})
    kids = dsl["nodes"][0]["children"]
    assert kids[0]["text"] == "标题"
    assert any(k.get("fillColor") == "#F5F5F4" for k in kids), "要有核心结论灰块"
    rows = [k for k in kids if k.get("layout") == "horizontal"]
    assert len(rows) == 2, "讨论段 2 张一行，后续段 1 张一行"
    assert len(rows[0]["children"]) == 2 and len(rows[1]["children"]) == 1
    first_card = rows[0]["children"][0]
    assert first_card["children"][0]["children"][0]["name"] == "target"
    bad_card = rows[1]["children"][0]
    assert bad_card["children"][0]["children"][0]["name"] == "topic-discussion", "图标名不合法要兜底"
    assert build_board_dsl({"modules": []}) is None
    fb = build_mermaid_fallback(mods)
    assert fb.startswith("flowchart") and fb.count("subgraph") == 3
    assert label_text('带"引号"的文字') == "带 引号 的文字"

    # 渲染出的 XML 必须转义干净
    _t, xml = render_doc({
        "title": "测试 <script>", "one_line": "A & B", "decisions": [{"what": "定 <x>", "why": ""}],
        "todos": [{"what": "做事", "who": "", "when": ""}],
        "quotes": [{"text": "原话 & 引用", "why": "理由"}],
        "summary": [{"topic": "议题", "points": ["要点 <1>"]}],
        "chapters": [{"time": "00:01", "title": "开场", "note": "说明"}],
    }, {"title": "录音", "when": "2026-01-01", "minutes": 30, "chars": 1000,
        "audio_url": "https://x.feishu.cn/minutes/abc"},
       '<whiteboard type="blank"></whiteboard>')
    assert "<script>" not in xml and "&lt;script&gt;" in xml
    assert "<whiteboard" in xml and "<grid>" in xml and "checkbox" in xml
    assert "详细章节" in xml
    # 顶部要有录音入口，章节时间要能跳到那一刻
    assert "听原始录音" in xml and "https://x.feishu.cn/minutes/abc" in xml
    assert "minutes/abc?t=1000" in xml  # 章节时间 00:01 → 1000ms

    assert env("__NOT_SET_EVER__") == ""

    import tempfile as _tf
    d = _tf.mkdtemp()
    os.environ["STATE_FILE"] = os.path.join(d, "state.json")
    a = Lock(); a.__enter__()
    assert a.path.exists()
    try:
        Lock().__enter__()
        raise AssertionError("第二个实例应该被锁挡住")
    except SystemExit:
        pass
    a.__exit__(None, None, None)
    assert not a.path.exists()
    os.environ.pop("STATE_FILE", None)
    import shutil as _sh
    _sh.rmtree(d, ignore_errors=True)

    print("self-test OK")
    return 0


# ─────────────────────────── 主流程 ───────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="飞书会议纪要自动总结")
    ap.add_argument("--lookback", type=int, default=2, help="回看几天（默认 2）")
    ap.add_argument("--source", choices=["all", "vc", "minutes"], default="all")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不建文档、不发卡片、不写记录")
    ap.add_argument("--only", default="", help="只处理指定的 ID，逗号分隔（跳过时长过滤）")
    ap.add_argument("--force", action="store_true", help="重做已处理过的记录")
    ap.add_argument("--out-dir", default="", help="另外把纪要存成 Markdown 到本地")
    ap.add_argument("--print-prompt", action="store_true", help="打印内置提示词")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--detail", choices=["brief", "standard", "full"], default=None,
                    help=argparse.SUPPRESS)  # 旧参数，保留兼容，不再生效
    args = ap.parse_args()

    if args.print_prompt:
        print(PROMPT, end="")
        return 0
    if args.self_test:
        return self_test()

    only = {x.strip() for x in args.only.split(",") if x.strip()}

    _lock = Lock()
    _lock.__enter__()
    atexit.register(_lock.__exit__, None, None, None)

    state = load_state()
    seen = set(state["processed"])
    total_new = 0
    failed = 0
    auth_failed = 0

    for profile in profiles():
        label = profile or "默认账号"
        if not args.dry_run:
            st = lark_json(profile, ["auth", "status", "--json"])
            if st and not (st.get("identities", {}).get("user", {}) or {}).get("available"):
                print(f"[{label}] 登录已失效，跳过。重新登录：lark-cli auth login --profile {profile}",
                      file=sys.stderr)
                auth_failed += 1
                continue

        sources: list[dict] = []
        if args.source in ("all", "vc"):
            sources += scan_vc(profile, args.lookback)
        if args.source in ("all", "minutes"):
            sources += scan_minutes(profile, args.lookback)

        # 妙记的播放地址是 https://<租户域名>/minutes/<token>，域名各家不同，
        # 从搜索结果里学一个，后面按 ID 补进来的记录就能拼出同样的地址。
        domain = ""
        for s in sources:
            m = re.match(r"https?://([^/]+)/", s.get("app_link") or "")
            if m:
                domain = m.group(1)
                break

        # --only 指定的 ID 不一定出现在搜索结果里（搜索索引本身不稳定）。
        # 直接按 ID 查一次详情，查到就补进候选，查不到说明不属于这个账号。
        if only:
            have = {s["id"] for s in sources}
            for tok in only - have:
                # 这里探错是正常的：ID 只属于某一个账号，别的账号当然读不到
                rc, out = lark(profile, ["minutes", "+detail", "--as", "user",
                                         "--minute-tokens", tok, "--json"])
                det = parse_first_json(out)
                if not det or not det.get("ok"):
                    continue
                try:
                    mt = det["data"]["minutes"][0]
                except Exception:  # noqa: BLE001
                    continue
                sources.append({
                    "profile": profile, "source": "minutes", "id": tok,
                    "title": mt.get("title") or "录音", "when": "",
                    "ref": tok, "duration": 0,
                    "app_link": f"https://{domain}/minutes/{tok}" if domain else "",
                })

        print(f"[{now()}] [{label}] 候选 {len(sources)} 条")

        for src in sources:
            key = f"{profile or 'default'}:{src['source']}:{src['id']}"
            if only and src["id"] not in only:
                continue
            if not args.force and (key in seen or src["id"] in set(state.get("legacy_ids", []))):
                continue
            if not only and src.get("duration") and src["duration"] < env_int("MIN_DURATION", MIN_DURATION):
                print(f"  跳过（只有 {src['duration']} 秒）: {src['title']}")
                seen.add(key)
                continue

            transcript = fetch_transcript(src)
            raw_len = len(transcript)
            if not src.get("when"):
                m = re.match(r"(\d{4}-\d{2}-\d{2})", transcript)
                if m:
                    src["when"] = m.group(1)
            transcript = clean_transcript(transcript)
            if len(transcript) < env_int("MIN_CHARS", MIN_CHARS):
                print(f"  跳过（逐字稿只有 {len(transcript)} 字）: {src['title']}")
                seen.add(key)
                continue

            print(f"  新纪录: [{src['source']}] {src['title']}（{raw_len} → {len(transcript)} 字）")
            data = summarize(transcript)
            if not data:
                print("  总结失败，下次重试", file=sys.stderr)
                failed += 1
                continue

            # 白板：优先用飞书原生画板（能控布局），转换失败就退回 Mermaid
            mods = data.get("modules") or []
            dsl = build_board_dsl(data)
            openapi = to_openapi(dsl) if dsl else None
            if openapi:
                board_xml = '<whiteboard type="blank"></whiteboard>'
                board_mode = "原生白板"
            else:
                board_xml = (f'<whiteboard type="mermaid">\n'
                             f'{esc(build_mermaid_fallback(mods))}\n</whiteboard>')
                board_mode = "Mermaid 兜底"

            doc_title, xml = render_doc(data, {
                "title": src["title"], "when": src["when"],
                "minutes": (src.get("duration") or 0) // 60, "chars": raw_len,
                "audio_url": src.get("app_link") or "",
            }, board_xml)

            if args.dry_run:
                print("-" * 60)
                print(f"标题：{doc_title}")
                print(card_digest(data)[:600])
                print(f"（{board_mode}，文档 XML {len(xml)} 字节，含 {xml.count('<li>')} 条列表、"
                      f"{xml.count('<checkbox')} 条待办、{xml.count('<row')} 行章节）")
            else:
                doc_url, board_tok = create_doc(profile, doc_title, xml)
                if not doc_url:
                    failed += 1
                    continue
                if openapi and board_tok:
                    if not write_board(profile, board_tok, openapi):
                        print("  白板没写进去，文档已建但画板是空的", file=sys.stderr)
                print(f"  文档: {doc_url}（{board_mode}）")
                send_card(profile, doc_title, card_digest(data), doc_url)

            if args.out_dir:
                d = Path(args.out_dir).expanduser()
                d.mkdir(parents=True, exist_ok=True)
                stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
                safe = re.sub(r"[^\w一-鿿-]+", "-", doc_title).strip("-")[:60] or "meeting"
                (d / f"{stamp}-{safe}.xml").write_text(xml, encoding="utf-8")

            seen.add(key)
            total_new += 1

    state["processed"] = sorted(seen)
    state["last_run"] = now()
    state["last_result"] = {"new": total_new, "failed": failed, "auth_failed": auth_failed}
    if not args.dry_run:
        save_state(state)
    print(f"  本次新增纪要 {total_new} 条，失败 {failed} 条，账号异常 {auth_failed} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
