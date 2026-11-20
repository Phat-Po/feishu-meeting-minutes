#!/usr/bin/env python3
"""飞书会议纪要自动总结：会议逐字稿 → AI 总结 → 飞书文档 + 卡片。

两个来源，自动都扫：
  1. 线上会议（vc）：飞书视频会议开完、生成了会议笔记的
  2. 上传录音（minutes）：用录音笔之类的设备录完上传，飞书转成妙记的

只依赖 Python 标准库和官方 lark-cli（https://github.com/larksuite/cli）。

用法:
  python3 meeting_summary.py                      # 正常跑一次（适合 cron / launchd）
  python3 meeting_summary.py --lookback 7         # 回看 7 天
  python3 meeting_summary.py --detail full        # 完整版纪要
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

MAX_CHARS = 45000          # brief/standard 单次送入的逐字稿上限
CHUNK_ABOVE = 30000        # 超过这个字数就分块提炼再合并
CHUNK_SIZE = 14000         # 每块目标字数
MIN_CHARS = 600            # 逐字稿短于这个就跳过（多半是误触）
MIN_DURATION = 120         # 录音时长短于这个秒数就跳过

# ─────────────────────────── 提示词 ───────────────────────────

PROMPT_BRIEF = """你是会议纪要助手。下面是会议逐字稿。

请用中文输出，严格按这个结构，不要加别的：

一句话：这场会干了什么（一句话）
讨论内容：（3-6 条要点，每条一行，用「- 」开头）
决定：（达成的一致或拍板的事，没有就写「没有明确决定」）
待办：（谁要做什么，没有就写「没有」）
时间安排：（约好的时间地点，没有就写「没有」）

要求：
- 只写逐字稿里有的内容，不要推测、不要补细节。
- 用业务词，不用技术黑话。
- 总计不超过 700 字。

逐字稿：
"""

PROMPT_STANDARD = """你是会议记录员。下面是会议逐字稿。

请输出一份完整的会议纪要，用 Markdown，严格按下面的结构。只写逐字稿里有的内容，不要推测、不要补细节。

# 总结

先用两三句话说明这场会是什么会、讨论了什么、结果如何。
然后按议题分层展开，用多级无序列表，一层议题、二层细节：

- **议题名**
    - 具体内容
    - 具体内容

# 待办

用表格，没有待办就写「无」：

| 事项 | 负责人 | 时间 |
|---|---|---|
| 谁要做什么 | 谁 | 什么时候 |

逐字稿里没明确说谁负责的，负责人写「未指定」，不要编。

# 智能章节

按时间顺序把会分成若干章节。每章写成：

**[MM:SS] 章节名**

> 一两句话概括这章讲了什么。

时间点用逐字稿里出现的时间戳，找不到就省略时间。

# 关键决策

每条写清楚：定了什么、为什么定。没有决策就写「本次为分享/讨论类，无关键决策」。

# 金句时刻

挑 2-4 句值得记的原话，格式：

「原话」

—— 一句话说明为什么值得记

# 写作要求

- 内容多就写长，不要压成摘要；不要写「等等」「其余略」。
- 用业务词，不用技术黑话。
- 逐字稿里出现的人名、机构名、专有名词照原样写，不要翻译、不要改写。

逐字稿：
"""

PROMPT_FULL = PROMPT_STANDARD.replace(
    "# 写作要求",
    """# 逐字稿脉络

按时间把整场会梳理一遍，每 10-20 分钟一段，写清楚这段在讲什么、谁在主导、话题怎么转的。这是给没参会的人看的，要能顺着读下来。

# 写作要求""",
).replace(
    "挑 2-4 句值得记的原话",
    "挑 4-8 句值得记的原话",
)

PROMPTS = {"brief": PROMPT_BRIEF, "standard": PROMPT_STANDARD, "full": PROMPT_FULL}

# 分块提炼用的提示词：只要事实，不要修辞
MAP_PROMPT = """下面是长会议逐字稿的第 {i}/{n} 段。

只提取事实，不要写总结性的散文。用 JSON 输出，结构严格如下：

{{
  "topics": ["这段讨论了哪些议题"],
  "decisions": [{{"what": "决定了什么", "why": "为什么", "quote": "依据的原话片段", "time": "时间点"}}],
  "todos": [{{"what": "要做什么", "owner": "谁（没说就写未指定）", "when": "什么时候", "quote": "依据的原话"}}],
  "quotes": [{{"text": "值得记的原话", "time": "时间点", "why": "为什么值得记"}}],
  "open_questions": ["没解决的问题"]
}}

要求：只写这一段里真实出现的，没有的字段给空数组。不要推测。

逐字稿片段：
"""

REDUCE_PROMPT = """你是会议记录员。下面是一场长会议分段提取出来的事实记录（共 {n} 段）。

请把这些事实合并成一份完整的会议纪要，用 Markdown，严格按下面的结构。

# 总结

先用两三句话说明这场会是什么会、讨论了什么、结果如何。
然后按议题分层展开，用多级无序列表。

# 待办

用表格，没有就写「无」：

| 事项 | 负责人 | 时间 |
|---|---|---|

# 智能章节

按时间顺序分章，每章：

**[MM:SS] 章节名**

> 一两句话概括。

# 关键决策

每条写清楚：定了什么、为什么。

# 金句时刻

挑 4-8 句原话：

「原话」

—— 一句话说明为什么值得记

# 写作要求

- 合并同一件事的重复记录，不要重复列。
- 分段记录里没有的内容一律不写，不要补充、不要推测。
- 人名、机构名、专有名词照原样写。
- 内容多就写长，不要压成摘要。
{extra}
分段事实记录：
"""

REDUCE_BRIEF = """你是会议纪要助手。下面是一场长会议分段提取出来的事实记录（共 {n} 段）。

请合并成一份简短纪要，用中文，严格按这个结构：

一句话：这场会干了什么（一句话）
讨论内容：（3-6 条要点，每条一行，用「- 」开头）
决定：（达成的一致或拍板的事，没有就写「没有明确决定」）
待办：（谁要做什么，没有就写「没有」）
时间安排：（约好的时间地点，没有就写「没有」）

要求：只写记录里有的内容，不要推测；总计不超过 700 字。

分段事实记录：
"""

FULL_EXTRA = """
# 逐字稿脉络

在「智能章节」之后、「关键决策」之前，再加一节「逐字稿脉络」：按时间把整场会梳理一遍，每 10-20 分钟一段，写清楚这段在讲什么、谁在主导、话题怎么转的。要能让没参会的人顺着读下来。

「金句时刻」挑 4-8 句，不要只挑 2 句。
"""

REDUCE_PROMPTS = {
    "brief": lambda n: REDUCE_BRIEF.format(n=n),
    "standard": lambda n: REDUCE_PROMPT.format(n=n, extra=""),
    "full": lambda n: REDUCE_PROMPT.format(n=n, extra=FULL_EXTRA),
}


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


def parse_any_json(text: str):
    """lark-cli 有时返回对象、有时返回数组，两种都吃。"""
    m = re.search(r"[\[{]", text)
    if not m:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[m.start():])[0]
    except Exception:  # noqa: BLE001
        return None


def profiles() -> list[str]:
    """要扫哪些账号。

    LARK_PROFILES 设了就按它来；没设就自动扫所有已登录的账号 ——
    以后在机器上多登一个号，不用回来改配置。
    """
    raw = env("LARK_PROFILES")
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    # 注意：profile list 不接受 --json，它本来就输出 JSON
    rc, out = run([lark_cli_path(), "profile", "list"])
    listed = parse_any_json(out) if rc == 0 else None
    if isinstance(listed, list):
        names = [p.get("name", "") for p in listed if isinstance(p, dict) and p.get("name")]
        if names:
            return names
    return [""]


def run(cmd: list[str], timeout: int = 300, stdin: str | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=stdin)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def lark(profile: str, args: list[str], timeout: int = 300) -> tuple[int, str]:
    cmd = [lark_cli_path()]
    if profile:
        cmd += ["--profile", profile]
    return run(cmd + args, timeout=timeout)


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


def lark_json(profile: str, args: list[str], timeout: int = 300) -> dict | None:
    rc, out = lark(profile, [*args, "--json"], timeout=timeout)
    if rc != 0:
        print(f"  [lark-cli 退出码 {rc}] {out[:200]}", file=sys.stderr)
    return parse_first_json(out)


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# ─────────────────────────── 去重记录 ───────────────────────────

def load_state() -> dict:
    try:
        with open(state_path(), encoding="utf-8") as f:
            s = json.load(f)
    except Exception:  # noqa: BLE001
        s = {}
    s.setdefault("processed", [])
    # 老版本只记了 processed_notes（纯 note_id，不带账号和来源）。
    # 这里单独存一份，匹配时只看 ID 本身，免得升级后被重复处理一遍。
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

def load_prompt(detail: str) -> str:
    custom = env("PROMPT_FILE")
    if custom:
        return Path(custom).expanduser().read_text(encoding="utf-8")
    return PROMPTS.get(detail, PROMPT_STANDARD)


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
    """把整段提示词交给模型，返回正文。

    两种后端：
      1. SUMMARY_CMD 设了 → 整段从 stdin 灌进去，stdout 就是答案。
      2. 没设 → 调用 Anthropic 兼容的 /v1/messages 接口。
    """
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
        # 8000 是因为部分推理模型会先输出 thinking 块；2000 会被 thinking 吃光，拿不到正文
        "max_tokens": env_int("AI_MAX_TOKENS", 8000),
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        base + "/v1/messages",
        data=body,
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            d = json.loads(r.read())
        for blk in d.get("content", []):
            if blk.get("type") == "text":
                return blk.get("text", "").strip()
        types = [b.get("type") for b in d.get("content", [])]
        stop = d.get("stop_reason")
        print(f"  [AI 无正文] blocks={types} stop={stop}", file=sys.stderr)
        if "thinking" in types and stop == "max_tokens":
            print("  → 思考块把输出额度用光了。调大 AI_MAX_TOKENS（比如 16000）再试。",
                  file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"  [AI 失败] {str(e)[:200]}", file=sys.stderr)
    return None


# ─────────────────────────── 逐字稿处理 ───────────────────────────

def strip_html(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def hhmmss(secs: int) -> str:
    """统一成 [HH:MM:SS]，避免模型把 [61:46] 误解成别的东西。"""
    return f"[{secs // 3600:02d}:{(secs % 3600) // 60:02d}:{secs % 60:02d}]"


def clean_transcript(text: str, anchor_every: int = 300) -> str:
    """去掉纯时间戳行，每 5 分钟留一个锚点；合并超短碎句。

    线上会议的逐字稿里时间戳占了近三成，去掉既省 token 又降噪。
    """
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


def chunk_text(text: str, size: int) -> list[str]:
    """按行切成接近 size 字数的块，尽量在段落边界断开。"""
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


def map_reduce(transcript: str, detail: str) -> str | None:
    """长逐字稿分块提炼事实，再合并成纪要。"""
    chunks = chunk_text(transcript, CHUNK_SIZE)
    print(f"  分块提炼：{len(transcript)} 字 → {len(chunks)} 块")
    facts: list[str] = []
    for i, part in enumerate(chunks, 1):
        raw = ask_model(MAP_PROMPT.format(i=i, n=len(chunks)) + part)
        if not raw:
            print(f"  第 {i} 块提炼失败，跳过", file=sys.stderr)
            continue
        parsed = parse_first_json(raw)
        facts.append(json.dumps(parsed, ensure_ascii=False) if parsed else raw)
        print(f"  第 {i}/{len(chunks)} 块完成")
    if not facts:
        return None
    build = REDUCE_PROMPTS.get(detail) or REDUCE_PROMPTS["standard"]
    return ask_model(build(len(facts)) + "\n\n".join(facts))


def summarize(transcript: str, detail: str) -> str | None:
    prompt = load_prompt(detail)
    if len(transcript) <= CHUNK_ABOVE:
        return ask_model(prompt + transcript[-MAX_CHARS:])
    if env("PROMPT_FILE"):
        # 自定义提示词时不分块，避免用户写的提示词和分块模板打架
        print("  用了自定义提示词，超长稿按单次截断处理", file=sys.stderr)
        return ask_model(prompt + transcript[-MAX_CHARS:])
    return map_reduce(transcript, detail)


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
        cur = _dig(d, path)
        out += cur or []
        token = data.get("page_token") or ""
        if not data.get("has_more") or not token:
            break
    return out


def scan_vc(profile: str, days: int) -> list[dict]:
    """线上会议：只有生成了会议笔记的才有逐字稿。"""
    end = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    start = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    out = []
    for m in page_all(profile, ["vc", "+search", "--as", "user", "--start", start,
                                "--end", end]):
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
    """从妙记的说明文字里抠时长。抠不出一律返回 0（后面还有字数兜底）。"""
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
            "title": title or "录音",
            "when": when,
            "ref": token,
            "duration": parse_duration_seconds(desc),
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


# ─────────────────────────── 输出：文档 + 卡片 ───────────────────────────

def own_open_id(profile: str) -> str:
    override = env("OPERATOR_OPEN_ID")
    if override:
        return override
    d = lark_json(profile, ["contact", "+get-user", "--as", "user"])
    if d and d.get("ok"):
        return (d.get("data", {}).get("user", {}) or {}).get("open_id", "")
    return ""


def create_doc(profile: str, title: str, markdown: str) -> str:
    """在录音所属账号的云盘根目录新建文档，返回链接。"""
    rc, out = run(
        ([lark_cli_path()] + (["--profile", profile] if profile else [])) + [
            "docs", "+create", "--as", "user", "--title", title,
            "--content", "-", "--doc-format", "markdown",
            "--parent-position", "my_library", "--json",
        ],
        timeout=300, stdin=markdown,
    )
    d = parse_first_json(out)
    if not d or not d.get("ok"):
        print(f"  [建文档失败] {out[:300]}", file=sys.stderr)
        return ""
    for w in d.get("data", {}).get("warnings", []) or []:
        print(f"  [文档警告] {w}", file=sys.stderr)
    return (d.get("data", {}).get("document", {}) or {}).get("url", "")


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
    # 先试机器人身份；没有该权限时退回当前用户身份
    for identity in ("bot", "user"):
        rc, out = lark(profile, [*base, "--as", identity], timeout=180)
        if rc == 0 and '"ok": true' in out:
            return True
        if identity == "bot":
            print(f"  [机器人发送失败，改用用户身份] {out[:160]}", file=sys.stderr)
    print(f"  [卡片发送失败] {out[:200]}", file=sys.stderr)
    return False


def digest_of(markdown: str, limit: int = 600) -> str:
    """从完整纪要里抠一段适合放卡片的短文本。"""
    body = re.sub(r"^# .*$", "", markdown, flags=re.M).strip()
    body = re.sub(r"\n{2,}", "\n", body)
    return body[:limit]


# ─────────────────────────── 自检 ───────────────────────────

def self_test() -> int:
    assert strip_html("<p>a<br/>b</p>") == "a\nb"
    assert parse_first_json('{"ok": true}\n[vc +detail] querying…') == {"ok": True}
    assert parse_first_json("no json here") is None
    assert parse_any_json('[{"name":"a"},{"name":"b"}]') == [{"name": "a"}, {"name": "b"}]

    # 分页路径不能多下钻一层（data.items 写成 data.data.items 会永远返回空）
    assert _dig({"data": {"items": [1, 2]}}, ("data", "items")) == [1, 2]
    assert _dig({"data": {}}, ("data", "items")) is None
    assert parse_duration_seconds("时长: 2 小时 24 分 11 秒") == 2 * 3600 + 24 * 60 + 11
    assert parse_duration_seconds("时长: 4 秒") == 4
    assert parse_duration_seconds("没有时长") == 0

    raw = "00:00:07\n哦呦。\n00:00:10\n啊，那好啊。\n00:06:00\n这段要保留。"
    cleaned = clean_transcript(raw)
    assert "[00:00:07]" in cleaned and "[00:06:00]" in cleaned
    assert "00:00:07" not in cleaned.replace("[00:00:07]", "")
    assert "这段要保留" in cleaned
    assert hhmmss(3700) == "[01:01:40]"

    spk = "Speaker 1 00:00:00.590 \n关了吗？\nSpeaker 2 00:01:05.100 \n第二节。"
    c2 = clean_transcript(spk)
    assert "Speaker 1 [00:00:00]" in c2 and "Speaker 2 [00:01:05]" in c2

    chunks = chunk_text("x" * 100 + "\n" + "y" * 100, 100)
    assert len(chunks) == 2 and "".join(chunks).replace("\n", "") == "x" * 100 + "y" * 100
    assert chunk_text("单个短块", 1000) == ["单个短块"]

    assert PROMPT_BRIEF.startswith("你是会议纪要助手")
    assert "# 智能章节" in PROMPT_STANDARD and "# 逐字稿脉络" in PROMPT_FULL

    # 附加要求必须落在「分段事实记录：」之前，否则模型会当成噪声忽略
    full_reduce = REDUCE_PROMPTS["full"](3)
    assert "# 逐字稿脉络" in full_reduce
    assert full_reduce.index("# 逐字稿脉络") < full_reduce.index("分段事实记录：")
    assert "# 逐字稿脉络" not in REDUCE_PROMPTS["standard"](3)
    assert "700 字" in REDUCE_PROMPTS["brief"](3)
    assert env("__NOT_SET_EVER__") == ""

    # 锁：第二个实例必须立刻退出，不能两边同时处理同一条
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
    ap.add_argument("--detail", choices=["brief", "standard", "full"], default="standard")
    ap.add_argument("--source", choices=["all", "vc", "minutes"], default="all")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不建文档、不发卡片、不写记录")
    ap.add_argument("--only", default="", help="只处理指定的 ID，逗号分隔（跳过时长过滤）")
    ap.add_argument("--out-dir", default="", help="另外把纪要存成 Markdown 到本地")
    ap.add_argument("--print-prompt", action="store_true", help="打印当前档位的提示词")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.print_prompt:
        print(load_prompt(args.detail), end="")
        return 0
    if args.self_test:
        return self_test()

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    if only and args.dry_run:
        print(f"  只处理 {len(only)} 条指定记录")

    # 锁：批量跑和定时任务不能同时开工，否则同一条会生成两份文档
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

        # --only 指定的 ID 不一定出现在搜索结果里（搜索索引本身不稳定）。
        # 直接按 ID 查一次详情，查到就补进候选，查不到说明不属于这个账号。
        if only:
            have = {s["id"] for s in sources}
            for tok in only - have:
                det = lark_json(profile, ["minutes", "+detail", "--as", "user",
                                          "--minute-tokens", tok])
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
                })

        print(f"[{now()}] [{label}] 候选 {len(sources)} 条")

        for src in sources:
            key = f"{profile or 'default'}:{src['source']}:{src['id']}"
            if only and src["id"] not in only:
                continue
            if key in seen or src["id"] in set(state.get("legacy_ids", [])):
                continue
            if not only and src.get("duration") and src["duration"] < env_int("MIN_DURATION", MIN_DURATION):
                print(f"  跳过（只有 {src['duration']} 秒）: {src['title']}")
                seen.add(key)
                continue

            transcript = fetch_transcript(src)
            raw_len = len(transcript)
            transcript = clean_transcript(transcript)
            if len(transcript) < env_int("MIN_CHARS", MIN_CHARS):
                print(f"  跳过（逐字稿只有 {len(transcript)} 字）: {src['title']}")
                seen.add(key)
                continue

            print(f"  新纪录: [{src['source']}] {src['title']}（{raw_len} → {len(transcript)} 字）")
            summary = summarize(transcript, args.detail)
            if not summary:
                print("  总结失败，下次重试", file=sys.stderr)
                failed += 1
                continue

            title = f"会议纪要：{src['title']}"
            if src.get("when"):
                title += f" {src['when']}"
            doc_url = ""
            if args.dry_run:
                print("-" * 60)
                print(summary[:1500])
                print("-" * 60)
            else:
                doc_url = create_doc(profile, title, summary)
                if doc_url:
                    print(f"  文档: {doc_url}")
                    send_card(profile, title, digest_of(summary), doc_url)
                else:
                    failed += 1
                    continue
            if args.out_dir:
                d = Path(args.out_dir).expanduser()
                d.mkdir(parents=True, exist_ok=True)
                stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
                safe = re.sub(r"[^\w一-鿿-]+", "-", src["title"]).strip("-")[:60] or "meeting"
                (d / f"{stamp}-{safe}.md").write_text(f"# {title}\n\n{summary}\n", encoding="utf-8")

            seen.add(key)
            total_new += 1

    state["processed"] = sorted(seen)
    state["last_run"] = now()
    # 给心跳检查用：区分「跑了但全失败」和「跑了且正常」
    state["last_result"] = {"new": total_new, "failed": failed, "auth_failed": auth_failed}
    if not args.dry_run:
        save_state(state)
    print(f"  本次新增纪要 {total_new} 条，失败 {failed} 条，账号异常 {auth_failed} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
