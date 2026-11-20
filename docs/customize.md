# 改成你要的样子

默认的纪要格式是我拍脑袋定的。你的会跟我的会不一样，改就完了 —— 代码不用动。

---

## 改文档结构

现在只有一种结构（导图 → 一句话 → 决策 → 待办 → 金句 → 总结 → 详细章节），
不再分档。想改，就改提示词里那段 JSON 说明：

```bash
python3 meeting_summary.py --print-prompt > my-prompt.txt
```

改完：

```bash
export PROMPT_FILE="$PWD/my-prompt.txt"
```

**常见的几种改法**：

- **只想更短**：把「全文控制在 2000-3000 字」改成 1000-1500，summary 收到 3-5 个议题
- **不要思维导图**：把 `mindmap` 字段从 JSON 说明里删掉，代码会自动用议题名兜底
- **待办要多**：把 `todos` 上限从 8 条改大（`render_doc` 里的 `todos[:8]`）
- **不要详细章节**：把 `chapters` 从 JSON 说明里删掉，最后一节就不生成了

> 分块模式（超长逐字稿）用的是内置的合并模板，**不会**用你的 `PROMPT_FILE`。
> 改结构要两边都改，或者把 `CHUNK_ABOVE` 调大让分块不触发。

## 长会议会被自动分块

逐字稿超过 **3 万字**（约两小时以上）时，脚本不再一次丢给模型，而是：

1. 切成几块，每块只提取**事实**（议题、决定、待办、原话），不写散文
2. 把所有事实合并成最终纪要

这个阈值可以调：

```bash
export CHUNK_ABOVE=50000   # 5 万字以下都不分块
export CHUNK_SIZE=20000    # 每块目标 2 万字
```

**什么时候该调小**：你用的模型上下文窗口小（比如 32k），希望每次调用更轻。
**什么时候该调大**：模型窗口够大，你想让它一次看完全文。

> 分块模式下用的是内置的合并模板，**不会**用你的 `PROMPT_FILE`。
> 因为分块和自定义提示词很容易打架 —— 你写了「按我的格式输出」，
> 但合并那一步还得知道怎么合并。真要自定义超长会议的格式，建议调 `--detail`
> 或者直接改代码里的 `REDUCE_PROMPTS`。

---

## 几个常见改法

**给老板看的版本** —— 把 summary 压到 3-5 个议题，todos 只留真正有人负责的：

在提示词里把「summary 收 5-8 个议题」改成「3-5 个，只要结论和风险」。

**给执行团队看的版本** —— 让 todos 更细：

在 JSON 说明里给 todos 加一个 `accept` 字段（验收标准），正文里提示「没有明确负责人的任务不要编」。

**英文会议要求英文纪要** —— 在提示词开头加一句：

```text
Always respond in English, regardless of the transcript language.
```

### 一个提醒

提示词里最好保留「只写逐字稿里有的内容，不要推测」这句。
删掉它，模型会开始帮你「补」细节，补出来的东西看着很合理但全是假的 ——
会议纪要这种东西，编错一句比少写十句麻烦得多。

---

## 换模型

### 换个 Anthropic 兼容的服务

只改三个环境变量，代码一行不动：

```bash
# Claude 官方
export AI_BASE_URL="https://api.anthropic.com"
export AI_API_KEY="sk-ant-..."
export AI_MODEL="claude-sonnet-5-5"

# DeepSeek（走它的 Anthropic 兼容端点）
export AI_BASE_URL="https://api.deepseek.com/anthropic"
export AI_API_KEY="sk-..."
export AI_MODEL="deepseek-chat"

# 本地 vLLM / LM Studio / 自建网关
export AI_BASE_URL="http://127.0.0.1:8000"
export AI_API_KEY="dummy"          # 本地服务不看这个，但不能为空
export AI_MODEL="qwen3-30b"
```

判断标准只有一个：`$AI_BASE_URL/v1/messages` 这个地址能不能用 Anthropic 的
请求格式正常返回。能就通。

### 换成完全不同的东西

不想走 HTTP 的话，用 `SUMMARY_CMD`，它接管一切：

```bash
export SUMMARY_CMD="claude -p"                    # 本机 Claude Code
export SUMMARY_CMD="codex exec -"                 # Codex
export SUMMARY_CMD="ollama run qwen3"             # 本地 Ollama
export SUMMARY_CMD="llm -m gpt-4o-mini"           # Simon Willison 的 llm 工具
export SUMMARY_CMD="/opt/scripts/my_summarizer.sh" # 你自己的
```

契约只有三条：

1. 「提示词 + 逐字稿」从**标准输入**进来
2. 纪要正文从**标准输出**出去
3. **成功退出码 0，失败必须非 0**

第 3 条别偷懒。脚本靠它判断这次到底成没成 —— 退出码一直是 0 的话，
报错信息会被当成纪要发给你。

想先单独试一条命令对不对：

```bash
echo "这是一段测试逐字稿，张三说下周三交方案，李四说没问题。" | claude -p
```

---

## 加后处理

### 存一份到本地

```bash
python3 meeting_summary.py --out-dir ~/Documents/meeting-notes
```

会存成 `20261120-1400-会议主题.md`，文件名带时间和主题。

### 发到群里

把 `send_card()` 里的 `--user-id` 换成 `--chat-id`：

```python
# 原
"--user-id", operator,
# 改
"--chat-id", "oc_你的群ID",
```

群 ID 用 `lark-cli im +chat-search --query "群名" --json` 能查到。

### 同步到别的地方

`--out-dir` 存完之后接一句你自己的命令就行：

```bash
python3 meeting_summary.py --out-dir ./notes && \
  rsync -a ./notes/ ~/Obsidian/Meetings/
```

或者在 `main()` 里 `save_markdown()` 后面加几行，调你自己的接口。
这地方没什么好封装的 —— 每个人的下游都不一样，硬做插件系统反而更麻烦。

### 写进多维表格

飞书多维表格的写入用 `lark-cli base +record-create` 就行。
思路是：把 `summary` 拆成字段，一条记录一场会。

```python
run([
    lark_cli_path(), "base", "+record-create",
    "--app-token", "bascn...", "--table-id", "tbl...",
    "--fields", json.dumps({
        "主题": topic, "时间": when, "纪要": summary,
    }, ensure_ascii=False),
])
```

具体参数用 `lark-cli base +record-create --help` 查，字段名要和表里的列名对上。

---

## 改卡片样子

卡片在 `send_card()` 里，就一个字典：

```python
card = {
    "config": {"wide_screen_mode": True},
    "header": {"template": "blue", ...},        # 颜色：blue/green/red/orange/purple/grey
    "elements": [...],
}
```

想加按钮、分栏、图片，看飞书卡片搭建工具：<https://open.feishu.cn/cardkit>

想加个「跳回原会议笔记」的按钮，拿到 `note_doc_token` 拼个链接塞进 `elements` 就行。

---

## 改判断逻辑

几个你可能会想动的地方：

| 想改什么 | 改哪 |
|---|---|
| 多短的逐字稿算「没内容」 | `if len(transcript) < 200:` |
| 逐字稿截断长度 | `MAX_CHARS = 45000` |
| 卡片正文长度 | `body[:3500]` |
| 一次拉多少场会 | `--page-size 20` |
| 回看几天 | `--lookback`（默认 2） |

改完记得跑一下：

```bash
python3 meeting_summary.py --self-test
python3 meeting_summary.py --dry-run --lookback 7
```

`--dry-run` 永远不发消息、不写状态文件，放心试。
