# 怎么跑

脚本本身只做一件事：跑一次、处理完、退出。**它不需要一直挂着。**

所以问题不是「装什么服务」，而是「谁来定时叫醒它」。下面四种方式，按从轻到重排列，选一种就行。

---

## 方式一：开完会手动跑一下

最轻，零配置。

```bash
python3 meeting_summary.py --lookback 1
```

`--lookback 1` 只回看昨天和今天，速度快。适合一天就一两场会的人。

**适合**：刚开始试、不想装任何后台任务。

---

## 方式二：交给 Claude Code 或 Codex 跑

你已经有 AI 命令行工具的话，连环境变量都不用配 ——
让工具自己跑脚本、自己看日志、失败了还能自己调。

在项目目录里开一个会话，直接说：

```text
跑一下 meeting_summary.py --lookback 1，把结果告诉我
```

或者写成一行交给它：

```bash
claude -p "在 $(pwd) 跑 python3 meeting_summary.py --lookback 1，如果有错误就分析原因"
```

**适合**：本机已经装了 Claude Code / Codex，想省掉定时任务。

**注意**：这类命令行工具登录态会过期。如果是**无人值守**的定时任务，
用 `claude setup-token` 之类的方式拿长期令牌，别指望主账号的登录态一直有效
（`claude -p` 登出时会返回非 0 退出码，脚本会报错跳过，不会发错内容）。

---

## 方式三：本机定时（推荐给个人电脑）

### macOS —— launchd

新建 `~/Library/LaunchAgents/com.me.meeting-minutes.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.me.meeting-minutes</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/绝对路径/meeting_summary.py</string>
    <string>--lookback</string><string>2</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>OPERATOR_OPEN_ID</key><string>ou_xxx</string>
    <key>AI_BASE_URL</key><string>https://api.anthropic.com</string>
    <key>AI_API_KEY</key><string>sk-ant-...</string>
    <key>AI_MODEL</key><string>claude-sonnet-5-5</string>
  </dict>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/meeting-minutes.log</string>
  <key>StandardErrorPath</key><string>/tmp/meeting-minutes.log</string>
</dict></plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.me.meeting-minutes.plist
tail -f /tmp/meeting-minutes.log
```

**两个常见的坑**：

- `ProgramArguments` 里必须是**绝对路径**，`~` 不会被展开
- 如果你的 Python 是 conda / pyenv / homebrew 装的，路径不是 `/usr/bin/python3`。
  用 `which python3` 查出来填进去，否则 launchd 会找不到

卸载：`launchctl unload ~/Library/LaunchAgents/com.me.meeting-minutes.plist`

### Linux —— crontab

```bash
crontab -e
```

```cron
*/10 * * * * cd /path/to/repo && /usr/bin/python3 meeting_summary.py >> run.log 2>&1
```

**注意**：cron 的环境变量和你终端里的不一样。要么写进 crontab 顶部，
要么用一个包装脚本 `source` 环境变量后再执行。

---

## 方式四：放在服务器上（没有服务器就不用看）

如果你想「电脑关了也能跑」，那就放服务器。任何一台能上网的 Linux 都行，
1 核 1G 足够 —— 这个脚本不训练模型，只是发几个 HTTP 请求。

```bash
# 服务器上
git clone <你的仓库地址> ~/meeting-minutes
cd ~/meeting-minutes
python3 meeting_summary.py --self-test
```

然后：

```bash
# 让服务器也有飞书登录态
npx @larksuite/cli@latest install
lark-cli config init
lark-cli auth login --no-wait --json     # 拿链接，在浏览器里点
```

配置好后照上面的 crontab 方式挂上去。

> **飞书的登录凭证会过期**（刷新令牌一般一周到一个月）。
> 过期后脚本会报错跳过，不会发错内容，但你需要重新 `auth login`。
> 长期无人值守的话，安排一个保活任务定期跑 `lark-cli auth status --verify`，
> 让它自己续期。

---

## Docker

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && npm install -g @larksuite/cli \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY meeting_summary.py .
ENTRYPOINT ["python3", "meeting_summary.py"]
```

```bash
docker build -t meeting-minutes .
docker run --rm \
  -e OPERATOR_OPEN_ID=ou_xxx \
  -e AI_BASE_URL=https://api.anthropic.com \
  -e AI_API_KEY=sk-ant-... \
  -e AI_MODEL=claude-sonnet-5-5 \
  -v ~/.lark-cli:/root/.lark-cli \
  meeting-minutes --lookback 2
```

> 挂载 `~/.lark-cli` 是为了复用本机已经做好的飞书登录。
> 如果容器在别的机器上跑，就得在容器里重新登录一次。

---

## Windows

用「任务计划程序」新建一个基本任务：

- 触发器：每天 / 重复每 10 分钟
- 操作：启动程序
- 程序：`python.exe` 的完整路径
- 参数：`C:\path\to\meeting_summary.py --lookback 2`
- 起始于：`C:\path\to`

环境变量在「操作 → 编辑 → 环境变量」里没有，得写一个 `.bat` 包一层：

```bat
@echo off
set OPERATOR_OPEN_ID=ou_xxx
set AI_BASE_URL=https://api.anthropic.com
set AI_API_KEY=sk-ant-...
set AI_MODEL=claude-sonnet-5-5
python C:\path\to\meeting_summary.py --lookback 2 >> C:\path\to\run.log 2>&1
```

然后把任务计划指向这个 `.bat`。

---

## 多个账号一起跑

设备换号、或者工作和个人的会分开记，都可能要扫多个账号：

```bash
export LARK_PROFILES="cli_aaaaaaaa,cli_bbbbbbbb"
```

账号名用 `lark-cli profile list` 查。每个号要先各自登录一次：

```bash
lark-cli --profile cli_aaaaaaaa config init
lark-cli --profile cli_aaaaaaaa auth login --scope "..."
```

跑起来之后：

- 每个号各查各的，互不影响
- **文档建在录音所属账号的云盘里**，不会串号
- 卡片发给每个账号自己
- 某个号登录过期，只打一行警告然后跳过，其他号照常跑

> 登录凭证会自动过期。无人值守的话，安排一个定期跑
> `lark-cli --profile <名字> auth status --verify` 的保活任务，让它自己续。

## 怎么知道它在正常工作

脚本每次都会打印进度：

```text
[2026-11-20T23:51:58+08:00] [cli_aaaa] 候选 2 条
  跳过（只有 4 秒）: 录音笔 10-13 13:51
  新纪录: [minutes] 课程录音（39849 → 38333 字）
  分块提炼：38333 字 → 3 块
  第 1/3 块完成
  第 2/3 块完成
  第 3/3 块完成
  文档: https://xxx.feishu.cn/docx/xxxxxxxx
  本次新增纪要 1 条
```

读法：

- **候选 0 条** —— 这段时间没有新会议/录音，正常
- **跳过（只有 N 秒）** —— 误触录音被过滤，正常
- **跳过（逐字稿只有 N 字）** —— 录音没转出文字，或者几乎没声音
- **分块提炼** —— 逐字稿超过 3 万字，进入两次式处理，比单次慢一些
- **文档: 链接** —— 这一步成了，纪要已经存进飞书
- **AI 失败** —— 接口地址、密钥、模型名三者之一不对
- **SUMMARY_CMD 失败 rc=1** —— 你的命令报错了，看它自己的 stderr
- **建文档失败** —— 多半是缺 `docx:document:create` 权限，或者权限改了没重新发布版本

**间隔建议**：10 分钟足够。调太密（比如每分钟）会反复请求飞书接口，
可能触发限流，而且也不会更快拿到纪要 —— 纪要本身也不是秒出的。
