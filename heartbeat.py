#!/usr/bin/env python3
"""检查定时任务有没有在正常跑。出问题发一条飞书消息，正常时什么都不做。

监控「任务本身」，不是监控机器 —— 机器活着不代表任务跑成功了。

用法:
  python3 heartbeat.py                # 检查一次
  python3 heartbeat.py --max-age 36   # 超过多少小时没跑算异常（默认 30）
  python3 heartbeat.py --dry-run      # 只打印，不发消息
  python3 heartbeat.py --self-test

建议放一条独立的定时任务，比如每天早上一跑：
  0 9 * * * cd /path/to/repo && /usr/bin/python3 heartbeat.py >> heartbeat.log 2>&1
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

# 让 -I 隔离模式也能引用同目录的主脚本
sys.path.insert(0, str(Path(__file__).resolve().parent))

from meeting_summary import (  # noqa: E402
    env_int, lark, load_state, own_open_id, profiles,
)


def check(state: dict, max_age_hours: float) -> str | None:
    """返回要发的告警文本；一切正常就返回 None。"""
    last = state.get("last_run") or ""
    if not last:
        return "会议纪要任务从来没跑过。检查定时任务和日志。"

    try:
        t = datetime.datetime.fromisoformat(last)
    except ValueError:
        return f"会议纪要任务的运行时间读不出来：{last}"

    if t.tzinfo is None:
        t = t.astimezone()
    age = (datetime.datetime.now(t.tzinfo) - t).total_seconds() / 3600

    if age > max_age_hours:
        return (f"会议纪要任务已经 {age:.0f} 小时没跑了。\n"
                f"最后一次运行：{last}\n"
                f"检查定时任务和日志。")

    res = state.get("last_result") or {}
    if res.get("auth_failed"):
        return (f"会议纪要任务的飞书登录失效，{res['auth_failed']} 个账号读不到数据。\n"
                f"重新登录：lark-cli auth login --profile <账号>")
    if res.get("failed"):
        return (f"会议纪要任务上次跑有 {res['failed']} 条处理失败。\n"
                f"看日志排查。")

    return None


def send_alert(profile: str, text: str) -> bool:
    open_id = own_open_id(profile)
    if not open_id:
        print("  [跳过] 拿不到当前账号的用户 ID", file=sys.stderr)
        return False
    content = json.dumps({"text": text}, ensure_ascii=False)
    for identity in ("bot", "user"):
        rc, out = lark(profile, ["im", "+messages-send", "--as", identity,
                                 "--user-id", open_id, "--msg-type", "text",
                                 "--content", content, "--json"], timeout=120)
        if rc == 0 and '"ok": true' in out:
            return True
    print(f"  [告警发送失败] {out[:200]}", file=sys.stderr)
    return False


def self_test() -> int:
    fresh = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    old = (datetime.datetime.now().astimezone()
           - datetime.timedelta(hours=50)).isoformat(timespec="seconds")

    assert check({}, 30) is not None                     # 从没跑过
    assert check({"last_run": fresh}, 30) is None        # 刚跑过，正常
    assert check({"last_run": old}, 30) is not None      # 太久没跑
    assert check({"last_run": fresh, "last_result": {"auth_failed": 1}}, 30) is not None
    assert check({"last_run": fresh, "last_result": {"failed": 2}}, 30) is not None
    assert check({"last_run": fresh, "last_result": {"new": 3, "failed": 0, "auth_failed": 0}}, 30) is None
    print("self-test OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="检查会议纪要任务是否正常")
    ap.add_argument("--max-age", type=float, default=float(env_int("MAX_AGE_HOURS", 30)),
                    help="超过多少小时没跑算异常（默认 30）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    state = load_state()
    alert = check(state, args.max_age)
    if not alert:
        print("心跳正常")
        return 0

    print(alert)
    if args.dry_run:
        print("（空跑，没发消息）")
        return 0
    profile = profiles()[0]
    return 0 if send_alert(profile, alert) else 1


if __name__ == "__main__":
    sys.exit(main())
