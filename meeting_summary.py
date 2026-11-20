#!/usr/bin/env python3
"""飞书会议纪要自动总结：会议逐字稿 → AI 总结 → 飞书卡片。

只依赖 Python 标准库和官方 lark-cli（https://github.com/larksuite/cli）。

用法:
  python3 meeting_summary.py                 # 正常跑一次（适合放 cron / launchd）
  python3 meeting_summary.py --lookback 7    # 回看 7 天
  python3 meeting_summary.py --dry-run       # 只打印，不发卡片、不写记录
  python3 meeting_summary.py --out-dir notes # 另外存一份 Markdown 到本地
  python3 meeting_summary.py --print-prompt  # 打印内置提示词，方便改成自己的
  python3 meeting_summary.py --self-test     # 自检

环境变量见 .env.example。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

MAX_CHARS = 45000  # 逐字稿截断长度；再长对总结质量帮助不大，只会烧 token

DEFAULT_PROMPT = """你是会议纪要助手。下面是会议逐字稿（含说话人前缀）。

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


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def lark_cli_path() -> str:
    return env("LARK_CLI") or shutil.which("lark-cli") or "lark-cli"


def state_path() -> Path:
    return Path(env("STATE_FILE") or Path(__file__).resolve().parent / "state.json")


def load_prompt() -> str:
    custom = env("PROMPT_FILE")
    if custom:
        return Path(custom).expanduser().read_text(encoding="utf-8")
    return DEFAULT_PROMPT


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def run(cmd: list[str], timeout: int = 300, stdin: str | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=stdin)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


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


def lark_json(args: list[str]) -> dict | None:
    rc, out = run([lark_cli_path(), *args, "--json"], timeout=300)
    if rc != 0:
        print(f"  [lark-cli 退出码 {rc}] {out[:200]}", file=sys.stderr)
    return parse_first_json(out)


def load_state() -> dict:
    try:
        with open(state_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {"processed_notes": [], "last_run": ""}


def save_state(s: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def summarize(transcript: str) -> str | None:
    """把提示词 + 逐字稿交给模型，返回总结正文。

    两种后端：
      1. SUMMARY_CMD 环境变量设了 → 整段内容从 stdin 灌进这个命令，stdout 就是总结。
         这样可以接 claude -p、codex exec -、ollama run、本地脚本……任何东西。
      2. 没设 → 调用 Anthropic 兼容的 /v1/messages 接口（Claude、DeepSeek 等都能用）。
    """
    prompt = load_prompt() + transcript[-MAX_CHARS:]

    cmd = env("SUMMARY_CMD")
    if cmd:
        try:
            p = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=900, input=prompt
            )
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

    base = env("AI_BASE_URL").rstrip("/")
    key = env("AI_API_KEY")
    model = env("AI_MODEL")
    if not (base and key and model):
        print(
            "  [缺少模型配置] 要么设 SUMMARY_CMD，要么同时设 AI_BASE_URL / AI_API_KEY / AI_MODEL",
            file=sys.stderr,
        )
        return None

    body = json.dumps(
        {
            "model": model,
            # 8000 是因为部分推理模型会先输出 thinking 块；2000 会被 thinking 吃光，拿不到正文
            "max_tokens": 8000,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()
    req = urllib.request.Request(
        base + "/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read())
        for blk in d.get("content", []):
            if blk.get("type") == "text":
                return blk.get("text", "").strip()
        types = [b.get("type") for b in d.get("content", [])]
        print(f"  [AI 无正文] blocks={types} stop={d.get('stop_reason')}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"  [AI 失败] {str(e)[:200]}", file=sys.stderr)
    return None


def strip_html(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def send_card(title: str, body: str) -> bool:
    operator = env("OPERATOR_OPEN_ID")
    if not operator:
        print("  [跳过推送] 没设 OPERATOR_OPEN_ID", file=sys.stderr)
        return False
    card = {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": title[:60]}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": body[:3500]}},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": "会议自动总结"}]},
        ],
    }
    rc, out = run(
        [
            lark_cli_path(), "im", "+messages-send", "--as", "bot",
            "--user-id", operator, "--msg-type", "interactive",
            "--content", json.dumps(card, ensure_ascii=False), "--json",
        ],
        timeout=180,
    )
    ok = rc == 0 and '"ok": true' in out
    print(f"  推送 {'成功' if ok else '失败: ' + out[:200]}")
    return ok


def save_markdown(out_dir: str, title: str, body: str) -> None:
    d = Path(out_dir).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    safe = re.sub(r"[^\w一-鿿-]+", "-", title).strip("-")[:60] or "meeting"
    path = d / f"{stamp}-{safe}.md"
    path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    print(f"  已存 {path}")


def recent_meetings(days: int) -> list[dict]:
    end = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    start = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    d = lark_json(
        ["vc", "+search", "--as", "user", "--start", start, "--end", end, "--page-size", "20"]
    )
    if not d or not d.get("ok"):
        print(f"  取会议失败: {str(d)[:200]}", file=sys.stderr)
        return []
    return d.get("data", {}).get("items", [])


def self_test() -> int:
    assert strip_html("<p>a<br/>b</p>") == "a\nb"
    assert strip_html("<p>x</p><p>y</p>") == "x\ny"
    assert parse_first_json('{"ok": true}\n[vc +detail] querying…') == {"ok": True}
    assert parse_first_json("no json here") is None
    assert DEFAULT_PROMPT.startswith("你是会议纪要助手")
    assert env("__NOT_SET_EVER__") == ""
    print("self-test OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="飞书会议纪要自动总结")
    ap.add_argument("--lookback", type=int, default=2, help="回看几天（默认 2）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不推送、不写记录")
    ap.add_argument("--out-dir", default="", help="另外把总结存成 Markdown 到这个目录")
    ap.add_argument("--print-prompt", action="store_true", help="打印内置提示词（方便改）")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.print_prompt:
        print(load_prompt(), end="")
        return 0

    if args.self_test:
        return self_test()

    if not args.dry_run and not env("OPERATOR_OPEN_ID"):
        print("错误：没设 OPERATOR_OPEN_ID，不知道该把纪要发给谁。", file=sys.stderr)
        print("     先跑 `lark-cli contact +get-user --json` 拿到自己的 open_id。", file=sys.stderr)
        print("     只想先看看效果，可以加 --dry-run。", file=sys.stderr)
        return 2

    state = load_state()
    seen = set(state.get("processed_notes", []))
    items = recent_meetings(args.lookback)
    print(f"[{now()}] 近 {args.lookback} 天会议 {len(items)} 场，已处理 {len(seen)} 条纪要")

    new = 0
    for m in items:
        mid = m.get("id")
        if not mid:
            continue
        det = lark_json(["vc", "+detail", "--as", "user", "--meeting-ids", str(mid)])
        if not det or not det.get("ok"):
            continue
        for mt in det.get("data", {}).get("meetings", []):
            note_id = mt.get("note_id")
            if not note_id or note_id in seen:
                continue
            nd = lark_json(["note", "+detail", "--as", "user", "--note-id", str(note_id)])
            if not nd or not nd.get("ok"):
                continue
            note = nd.get("data", {}).get("note", {})
            doc = note.get("verbatim_doc_token") or note.get("note_doc_token")
            if not doc:
                continue
            rc, out = run([lark_cli_path(), "docs", "+fetch", "--as", "user", "--doc", str(doc), "--json"])
            parsed = parse_first_json(out)
            if not parsed:
                continue
            try:
                content = parsed["data"]["document"]["content"]
            except Exception:  # noqa: BLE001
                continue
            transcript = strip_html(content)
            if len(transcript) < 200:
                continue

            topic = mt.get("topic") or "会议"
            when = mt.get("start_time") or ""
            print(f"  新纪要: {topic} @ {when}（{len(transcript)} 字）")
            summary = summarize(transcript)
            if not summary:
                continue
            body = f"**{topic}**\n{when}\n\n{summary}"
            if args.dry_run:
                print(body[:800])
            else:
                send_card(f"会议总结 · {topic}", body)
            if args.out_dir:
                save_markdown(args.out_dir, topic, body)
            seen.add(note_id)
            new += 1

    state["processed_notes"] = sorted(seen)
    state["last_run"] = now()
    if not args.dry_run:
        save_state(state)
    print(f"  本次新增总结 {new} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
