<div align="center">

<pre>
██████ ██████ ██     ██████ ██  ██ ██  ██
██     ██     ██     ██     ██  ██ ██  ██
█████  █████  ██     ██████ ██████ ██  ██
██     ██     ██         ██ ██  ██ ██  ██
██     ██████ ██     ██████ ██  ██  ████
</pre>

### M E E T I N G &nbsp; M I N U T E S

**开完会，纪要自己写好，存进飞书。**

[![Python](https://img.shields.io/badge/python-3.9%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Feishu](https://img.shields.io/badge/Feishu%20%2F%20Lark-open%20platform-00d6b9)](https://open.feishu.cn/)
[![No deps](https://img.shields.io/badge/pip%20install-nothing-brightgreen)](#)

</div>

---

> **English** — Turns Feishu/Lark transcripts into proper written minutes and saves them as a
> Feishu doc. Handles both online meetings and uploaded recordings (dictaphone, phone, any audio
> you drop into Drive). Works with any model — Claude, DeepSeek, Codex, Ollama, or your own
> command. One Python file, no dependencies.

---

## 这个项目干什么

飞书里其实有两种「会」：

1. **线上会议** —— 用飞书视频会议开的，会后有会议笔记和逐字稿
2. **上传录音** —— 用录音笔、手机录完传上去，飞书会把它转成妙记和逐字稿

两种飞书都会处理，但生成的「智能纪要」往往很短 —— 两个半小时的会可能只给你三百字。

这个脚本读那份完整的逐字稿，重新写一份**能直接看、能直接发给人**的纪要，存成飞书文档，再推一张卡片带链接给你。

```
找到会议 / 录音 → 拉逐字稿 → 清洗 → AI 总结 → 建飞书文档 → 推卡片
```

**适合谁**

- 经常开会或听讲，事后懒得翻几万字逐字稿的人
- 用录音笔录东西（课堂、共修、访谈、灵感），想要一份能回看的文字稿的人
- 想把会议结论自动落成文档、不想手动复制粘贴的人

**不适合谁**

- 没生成逐字稿的会 —— 没有稿子就没有东西可总结
- 想要「会中实时字幕」的 —— 这个只做事后

## 长什么样

生成出来的飞书文档有这几节（示例，内容是编的）：

> **会议总结 · 双十一备货复盘**
>
> **总结**
> 定下了双十一主推的三个款和首批备货量。
> - 主推款从五个收窄到三个
>   - 集中预算，单款投入翻倍
>   - 落选的款转清库存
>
> **待办**
> | 事项 | 负责人 | 时间 |
> |---|---|---|
> | 确认工厂交期 | 小王 | 11/6 |
> | 更新客服话术 | 小李 | 11/7 |
>
> **智能章节**
> **[00:12:30] 备货量怎么定**
> > 按去年销量和今年预售数据倒推，留两周缓冲。
>
> **关键决策**
> 主推 A/B/C 三款，首批各 500 件。
>
> **金句时刻**
> 「宁可少卖，也别压死现金流。」
> —— 定下了这次备货的底线。

卡片会同时推给你，带一个「看完整纪要」按钮直接跳文档。

## 三步跑起来

### 1. 装飞书命令行工具

```bash
npx @larksuite/cli@latest install
lark-cli config init          # 会让你填飞书应用的 App ID 和 App Secret
```

还没有飞书应用？看 **[飞书配置教程](docs/feishu-setup.md)**，从建应用到开权限一步步写好了。

### 2. 登录授权

```bash
lark-cli auth login --scope "vc:meeting.search:read vc:note:read docx:document:readonly docx:document:create minutes:minutes.basic:read minutes:minutes.search:read minutes:minutes.artifacts:read im:message:send_as_bot"
```

### 3. 跑一次看看

```bash
export AI_BASE_URL="https://api.anthropic.com"
export AI_API_KEY="sk-ant-..."
export AI_MODEL="claude-sonnet-5-5"

python3 meeting_summary.py --dry-run --lookback 7   # 先空跑，只打印，不建文档
python3 meeting_summary.py --lookback 2             # 确认没问题再真跑
```

`--dry-run` 不会建文档、不会发卡片、不写状态文件，可以放心反复试。

## 生成的文档长什么样

不是一大段文字，是排好版的：

```
┌──────────────────────────────┐
│  思维导图（3-8 个分支）         │  ← 一眼看全貌
├──────────────────────────────┤
│  📌 一句话                     │  ← 蓝色高亮块
├───────────────┬──────────────┤
│  ✅ 关键决策   │  📋 待办      │  ← 左右双栏
├───────────────┴──────────────┤
│  💬 金句时刻                   │  ← 引用块
├──────────────────────────────┤
│  总结（5-8 个议题）             │
├──────────────────────────────┤
│  详细章节（表格，放最后）        │
└──────────────────────────────┘
```

- **顺序是有讲究的**：先给全貌和结论，细节压在最后。回看时不用从头读到尾。
- **标题自动生成**：不再是「会议纪要：新录音」这种文件名，而是从内容里提炼，比如
  「会议纪要：双十一备货方案与主推款敲定 2026-11-05」。
- **篇幅约 2,000-3,000 字**，不是逐字稿的复述。

想改结构或篇幅，`--print-prompt` 导出提示词自己调。

## 长会议怎么处理

两个半小时的会，逐字稿能到四万字。整段丢给模型，中间的内容容易被忽略。

脚本会在逐字稿超过 **3 万字**时自动切换成两段式：

1. **分块提炼** —— 切成几块，每块只提事实（议题、决定、待办、原话），不写散文
2. **合并成文** —— 把所有事实合成一份完整纪要

关键是提事实而不是提摘要。先写散文再合并，细节会一层层磨没。

字数阈值可以调：`CHUNK_ABOVE`、`CHUNK_SIZE`。

## 模型怎么接

脚本对「谁来总结」没有意见。两种接法，选一种：

### 方式一：任何 Anthropic 兼容接口

| 变量 | 例子 |
|---|---|
| `AI_BASE_URL` | `https://api.anthropic.com` |
| `AI_API_KEY` | `sk-ant-...` |
| `AI_MODEL` | `claude-sonnet-5-5` |

Claude 官方、DeepSeek、各种兼容 Anthropic 协议的网关和本地代理都走这一条。
换服务商只改 `AI_BASE_URL`，代码一行不动。

### 方式二：交给你自己的命令

```bash
export SUMMARY_CMD="claude -p"          # 本机已登录的 Claude Code
export SUMMARY_CMD="codex exec -"       # Codex
export SUMMARY_CMD="ollama run qwen3"   # 本地模型
```

「提示词 + 逐字稿」从标准输入进来，纪要从标准输出出去。
**成功退出码 0，失败必须非 0** —— 否则报错信息会被当成会议纪要存进文档。

## 配置项

| 环境变量 | 必填 | 说明 |
|---|---|---|
| `AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL` | 二选一 | 接口方式 |
| `SUMMARY_CMD` | 二选一 | 命令方式，设了就忽略上面三个 |
| `LARK_PROFILES` | 否 | 要扫哪些账号，逗号分隔。不填就扫当前登录的 |
| `OPERATOR_OPEN_ID` | 否 | 卡片固定发给谁。不填就发给每个账号自己 |
| `PROMPT_FILE` | 否 | 换成你自己的提示词文件 |
| `MIN_DURATION` | 否 | 录音短于多少秒跳过，默认 120 |
| `MIN_CHARS` | 否 | 逐字稿短于多少字跳过，默认 600 |
| `CHUNK_ABOVE` / `CHUNK_SIZE` | 否 | 分块阈值 / 每块大小 |
| `AI_MAX_TOKENS` | 否 | 输出上限，默认 8000 |
| `STATE_FILE` | 否 | 已处理记录存哪，默认脚本同目录 `state.json` |
| `LARK_CLI` | 否 | `lark-cli` 不在 PATH 里时手动指定路径 |

> `.env` 不会自动读。自己 `export`，或者交给 launchd / cron / Docker 注入。

## 多个账号

**默认自动扫所有已登录的账号** —— 这台机器上 `lark-cli` 登了几个号，就查几个号，
不用配任何东西。以后多登一个号，脚本自己会发现。

想限定范围再设：

```bash
export LARK_PROFILES="cli_xxxxxxxx,cli_yyyyyyyy"
```

账号名用 `lark-cli profile list` 查。

- 每个号各查各的会议和录音，互不影响
- **文档建在录音所属的那个账号的云盘里**，不会串号
- 卡片发给每个账号自己（除非你设了 `OPERATOR_OPEN_ID`）
- 某个号登录过期，只跳过它，其他号照常跑

## 让它定时跑

脚本只跑一次就退出，找个东西定时拉起它：

**自己的电脑（macOS）** —— `launchd`，每 10 分钟：

```bash
cat > ~/Library/LaunchAgents/com.me.meeting-minutes.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.me.meeting-minutes</string>
  <key>ProgramArguments</key>
  <array><string>/usr/bin/python3</string><string>/绝对路径/meeting_summary.py</string></array>
  <key>EnvironmentVariables</key><dict>
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

**不想装定时任务** —— 直接在 Claude Code 里让它跑：

```text
帮我在这个目录跑一下 meeting_summary.py --lookback 1
```

> 服务器上别直接在 crontab 里写 `python3`：cron 的 PATH 很短，
> npm 全局装的 `lark-cli` 会找不到，而且**静默失败**、日志里看不出原因。
> 用 `deploy/run.example.sh` 那个模板包一层。

更多跑法（Windows、Docker、常驻）见 **[运行方式](docs/run-it.md)**。

## 任务挂了会告诉你

定时任务最怕的不是报错，是**安静地停掉** —— 你以为它在跑，其实已经一周没动了。

`heartbeat.py` 就是查这个的。它看三件事：

- 最后一次运行是多久以前（默认超过 30 小时就报警）
- 上次跑有没有处理失败的
- 飞书登录有没有过期

一切正常就什么都不做，不打扰你。

```bash
python3 heartbeat.py --dry-run      # 先看看现在的状态
python3 heartbeat.py                # 有问题就发飞书消息
```

挂一条独立定时任务，每天早上查一次：

```cron
0 9 * * * cd /path/to/repo && /usr/bin/python3 heartbeat.py >> heartbeat.log 2>&1
```

> **这不是机器监控。** CPU、内存、磁盘那些用专门的监控工具（比如 Beszel、Uptime Kuma）。
> 机器活着不等于任务跑成功了，两件事要分开看。

## 自定义

- **改总结格式** → `--print-prompt` 导出提示词，改完用 `PROMPT_FILE` 指过去
- **加后处理** → 用 `--out-dir notes` 另外存一份本地 Markdown
- **只处理一种来源** → `--source minutes` 或 `--source vc`
- **只补做某几条** → `--only <ID>,<ID>`，跳过时长过滤和历史记录

> 同一个目录只能同时跑一个实例。批量处理长录音时，进程会写一个锁文件，
> 定时任务撞上来会直接跳过 —— 否则两边会为同一条录音各建一份文档。

详细做法见 **[自定义指南](docs/customize.md)**。

## 需要的飞书权限

**线上会议**（前三条）+ **上传录音**（中间三条）+ **写文档**：

| 权限 | 干什么 | 不开会怎样 |
|---|---|---|
| `vc:meeting.search:read` | 搜出你参加过的会议 | 找不到线上会议 |
| `vc:note:read` | 读会议笔记，拿逐字稿地址 | 找到会也读不到内容 |
| `docx:document:readonly` | 读逐字稿正文 | 逐字稿拉不下来 |
| `minutes:minutes.basic:read` | 读妙记基本信息 | 上传的录音全看不到 |
| `minutes:minutes.search:read` | 搜妙记列表 | 找不到上传的录音 |
| `minutes:minutes.artifacts:read` | 下载妙记的逐字稿 | 有录音但拉不到字 |
| `docx:document:create` | 新建飞书文档 | 总结写完了没地方存 |
| `im:message:send_as_bot` | 机器人推卡片给你 | 文档建好了但没通知 |

开通方式见 **[飞书配置教程](docs/feishu-setup.md)**。

## 常见问题

**跑出来 0 条？**
只有生成了逐字稿的会才有内容。短于 2 分钟的录音会被当成误触跳过（可调 `MIN_DURATION`）。

**为什么要等 10 分钟？**
默认轮询。飞书的事件订阅能秒级，但会议类事件在部分应用类型下控制台里订不上，
所以走最稳的路。想试事件推送见 [FAQ](docs/faq.md)。

**卡片没收到？**
看脚本输出里有没有「卡片发送失败」。机器人身份发不出去时脚本会自动改用你的用户身份。

**同一个会会不会重复建文档？**
不会。处理过的记录记在 `state.json`，下次跳过。想重做就删掉对应那条。

更多见 **[FAQ](docs/faq.md)**。

## 项目结构

```
meeting_summary.py        主脚本，单文件，只用 Python 标准库
heartbeat.py              检查定时任务有没有在跑
deploy/run.example.sh     服务器上定时任务的启动器模板
docs/feishu-setup.md      飞书应用配置：建应用、开权限、发布、授权
docs/run-it.md            怎么跑：本机 / 服务器 / 定时任务 / Docker / Windows
docs/customize.md         改提示词、换模型、加后处理
docs/faq.md               踩过的坑和限制
CHANGELOG.md              更新记录
.env.example              配置模板
```

## 它不做什么

- 不存你的逐字稿，只在内存里过一遍
- 不上传任何东西到第三方，除了你自己配的那个模型接口
- 不改飞书里原有的妙记和智能纪要

## License

MIT —— 随便用、随便改、随便商用，出事别找我。

---

<div align="center">
<sub>如果这个项目帮你省下了翻逐字稿的时间，点个 ⭐ 就够了。</sub>
</div>
