<div align="center">

<pre>
██████ ██████ ██     ██████ ██  ██ ██  ██
██     ██     ██     ██     ██  ██ ██  ██
█████  █████  ██     ██████ ██████ ██  ██
██     ██     ██         ██ ██  ██ ██  ██
██     ██████ ██     ██████ ██  ██  ████
</pre>

### M E E T I N G &nbsp; M I N U T E S

**开完会，纪要自己发到飞书上。**

[![Python](https://img.shields.io/badge/python-3.9%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Feishu](https://img.shields.io/badge/Feishu%20%2F%20Lark-open%20platform-00d6b9)](https://open.feishu.cn/)
[![No deps](https://img.shields.io/badge/pip%20install-nothing-brightgreen)](#)

</div>

---

> **English** — A small tutorial project that turns your Feishu/Lark meeting transcripts into AI-written
> minutes and pushes them to you as a card. Runs on your own laptop or any server, works with any
> model (Claude, DeepSeek, Codex, Ollama, or your own script). No SDK, no framework, one Python file.

---

## 这个项目干什么

飞书会议结束后，如果这场会生成了**会议笔记 / 纪要**，飞书里就已经有一份带说话人的逐字稿了。

这个脚本做的就是：

```
找到最近的会议 → 找到它的笔记 → 拉下逐字稿 → 丢给大模型 → 生成纪要 → 发到你飞书
```

它不替代飞书自带的纪要，而是给你一份**能直接看、直接转发的短版本**：一句话结论、讨论要点、决定了什么、谁要做什么、下次约在什么时候。

**适合谁**

- 一天开好几场会，会后懒得翻长逐字稿的人
- 想把会议结论自动落到飞书、不想手动复制粘贴的人
- 想基于逐字稿再自动做点别的事（存文件、发群、写多维表格、同步 Obsidian……）的人

**不适合谁**

- 没开飞书会议笔记的会 —— 没有逐字稿就没有东西可总结
- 想要「会中实时字幕」的 —— 这个只做事后总结

## 长什么样

钉在飞书里的卡片大概长这样（示例，内容是编的）：

> **会议总结 · 双十一备货复盘**
>
> **双十一备货复盘**
> 2026-11-05 14:00
>
> **一句话**：定下了双十一主推的三个款和首批备货量。
>
> **讨论内容**
> - 主推款从五个收窄到三个，集中预算
> - 仓库 11 月 8 日前必须到货，否则走空运
> - 客服话术要补上缺货时的替换方案
>
> **决定**：主推 A/B/C 三款，首批各 500 件。
>
> **待办**
> - 小王 11/6 前确认工厂交期
> - 小李 11/7 前更新客服话术
>
> **时间安排**：11/8 到货，11/10 开卖。

卡片底部还有一行小字「会议自动总结」，右上角是蓝色标题栏。

## 三步跑起来

### 1. 装飞书命令行工具

```bash
npx @larksuite/cli@latest install
lark-cli config init          # 会让你填飞书应用的 App ID 和 App Secret
```

还没有飞书应用？看 **[飞书配置教程](docs/feishu-setup.md)**，从建应用到开权限一步步写好了。

### 2. 拿到自己的用户 ID

```bash
lark-cli contact +get-user --json
```

返回里的 `open_id`（`ou_` 开头那串）就是要发给你的地址。

### 3. 跑一次看看

```bash
export OPERATOR_OPEN_ID="ou_你的用户ID"
export AI_BASE_URL="https://api.anthropic.com"
export AI_API_KEY="sk-ant-..."
export AI_MODEL="claude-sonnet-5-5"

python3 meeting_summary.py --dry-run --lookback 7   # 先空跑，只打印不发消息
python3 meeting_summary.py --lookback 2             # 确认没问题再真发
```

只想跑一次、不想配环境变量？也可以直接写在命令行前面：

```bash
OPERATOR_OPEN_ID=ou_xxx AI_BASE_URL=... AI_API_KEY=... AI_MODEL=... \
  python3 meeting_summary.py --lookback 2
```

## 模型怎么接

脚本对「谁来总结」没有意见。两种接法，选一种：

### 方式一：任何 Anthropic 兼容接口（最简单）

填三个环境变量就行：

| 变量 | 例子 |
|---|---|
| `AI_BASE_URL` | `https://api.anthropic.com` |
| `AI_API_KEY` | `sk-ant-...` |
| `AI_MODEL` | `claude-sonnet-5-5` |

Claude 官方、DeepSeek、以及各种兼容 Anthropic 协议的网关和本地代理都走这一条。
换个服务商只改 `AI_BASE_URL`，代码一行不动。

> 注意：部分推理模型会先输出一大段思考内容。脚本的 `max_tokens` 留了 8000，
> 就是为了不让思考块把正文吃掉。如果你换成思考特别长的模型还不够，把它调大。

### 方式二：交给你自己的命令（最自由）

设一个 `SUMMARY_CMD`，脚本会把「提示词 + 逐字稿」从标准输入灌进去，
命令的输出就是纪要：

```bash
export SUMMARY_CMD="claude -p"          # 用本机已登录的 Claude Code
export SUMMARY_CMD="codex exec -"       # 用 Codex
export SUMMARY_CMD="ollama run qwen3"   # 用本地模型
export SUMMARY_CMD="python3 my_chain.py"  # 用你自己的脚本
```

**约定**：命令成功时退出码为 0，失败时**必须返回非 0**。
否则脚本会把报错信息当成会议纪要发出去 —— 这个坑我们踩过，见 [FAQ](docs/faq.md)。

这条路的好处是不用管密钥：命令自己负责认证。缺点是每个工具的超时和输出格式不一样，
第一次接的时候建议先用一条固定文本手工试：

```bash
echo "测试" | claude -p
```

## 配置项

| 环境变量 | 必填 | 说明 |
|---|---|---|
| `OPERATOR_OPEN_ID` | 是 | 纪要发给谁。`--dry-run` 时可省 |
| `SUMMARY_CMD` | 否 | 自定义总结命令，优先级高于下面三个 |
| `AI_BASE_URL` | 否 | Anthropic 兼容接口地址 |
| `AI_API_KEY` | 否 | 接口密钥 |
| `AI_MODEL` | 否 | 模型名 |
| `PROMPT_FILE` | 否 | 换成你自己的提示词文件 |
| `STATE_FILE` | 否 | 已处理记录存哪，默认脚本同目录 `state.json` |
| `LARK_CLI` | 否 | `lark-cli` 不在 PATH 里时手动指定路径 |

> `.env` 文件不会自动读。想用 `.env`，自己加一行 `export $(grep -v '^#' .env | xargs)`，
> 或者交给 systemd / launchd / Docker 去注入。

## 让它定时跑

脚本本身只跑一次就退出。要自动总结，找个东西定时拉起它就行。

**自己的电脑（macOS）** —— `launchd`，每 10 分钟一次：

```bash
cat > ~/Library/LaunchAgents/com.me.meeting-minutes.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.me.meeting-minutes</string>
  <key>ProgramArguments</key>
  <array><string>/usr/bin/python3</string><string>/绝对路径/meeting_summary.py</string></array>
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
EOF
launchctl load ~/Library/LaunchAgents/com.me.meeting-minutes.plist
```

**服务器（Linux）** —— crontab：

```cron
*/10 * * * * cd /path/to/repo && /usr/bin/python3 meeting_summary.py >> run.log 2>&1
```

**不想装定时任务** —— 开会前手动跑，或者直接在 Claude Code 里让它跑：

```text
帮我在这个目录跑一下 meeting_summary.py --lookback 1
```

更多跑法（Windows、Docker、交给 AI 助手）见 **[运行方式](docs/run-it.md)**。

## 自定义

- **改总结格式** → `--print-prompt` 导出提示词，改完用 `PROMPT_FILE` 指过去，代码不动
- **加后处理** → 用 `--out-dir notes` 存本地 Markdown，再接你自己的脚本
- **换输出渠道** → 卡片是 `send_card()` 一个函数，想发群里就换 `--chat-id`

详细做法和示例见 **[自定义指南](docs/customize.md)**。

## 需要的飞书权限

四条，缺一不可：

| 权限 | 干什么 | 不开会怎样 |
|---|---|---|
| `vc:meeting.search:read` | 搜出你参加过的会议 | 一场会都找不到 |
| `vc:note:read` | 读会议笔记，拿逐字稿的地址 | 找到会也读不到内容 |
| `docx:document:readonly` | 读逐字稿正文 | 逐字稿拉不下来 |
| `im:message:send_as_bot` | 让机器人把卡片发给你 | 总结生成了但推不出去 |

开通方式和对应的控制台位置见 **[飞书配置教程](docs/feishu-setup.md)**。

## 常见问题

**为什么跑出来 0 场会议？**
只有**生成了会议笔记**的会议才有逐字稿。没开笔记的会，搜得到也总结不了。
另外 `--lookback` 只回看最近 N 天，别设太小。

**为什么要等 10 分钟？**
因为默认用轮询。飞书的事件订阅可以做到秒级，但会议类事件在部分应用类型下
控制台里订不上，所以这个项目默认走最稳的路。想试事件推送见 [FAQ](docs/faq.md)。

**卡片没收到？**
先看脚本输出里有没有「推送 失败」；再确认机器人是不是你的好友 / 能不能给你发单聊。

**会不会重复发？**
不会。处理过的笔记 ID 记在 `state.json` 里，下次跳过。
想重发就把对应 ID 从里面删掉。

更多见 **[FAQ](docs/faq.md)**。

## 项目结构

```
meeting_summary.py      主脚本，单文件，只用 Python 标准库
docs/feishu-setup.md    飞书应用配置：建应用、开权限、发布、授权
docs/run-it.md          怎么跑：本机 / 服务器 / 定时任务 / Docker / Windows
docs/customize.md       改提示词、换模型、加后处理
docs/faq.md             踩过的坑和限制
.env.example            配置模板
```

## 它不做什么

- 不存你的逐字稿，只存在内存里过一遍
- 不上传任何东西到第三方，除了你自己配的那个模型接口
- 不改你飞书里的原始纪要

## License

MIT —— 随便用、随便改、随便商用，出事别找我。

---

<div align="center">
<sub>如果这个项目帮你省下了翻逐字稿的时间，点个 ⭐ 就够了。</sub>
</div>
