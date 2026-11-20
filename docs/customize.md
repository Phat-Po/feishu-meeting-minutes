# 改成你要的样子

默认的纪要格式是我拍脑袋定的。你的会跟我的会不一样，改就完了 —— 代码不用动。

---

## 改总结格式

把默认提示词复制出来：

```bash
cp meeting_summary.py /tmp/x.py && python3 - <<'EOF'
import re, pathlib
src = pathlib.Path("meeting_summary.py").read_text()
m = re.search(r'DEFAULT_PROMPT = """(.*?)"""', src, re.S)
pathlib.Path("my-prompt.txt").write_text(m.group(1))
EOF
```

（其实就是把 `meeting_summary.py` 里 `DEFAULT_PROMPT` 那段抄到 `my-prompt.txt`。）

改完指定它：

```bash
export PROMPT_FILE="$PWD/my-prompt.txt"
```

### 几个常见改法

**给老板看的版本** —— 删掉讨论过程，只留结论：

```text
请只输出三部分：
结论：（这场会定下来的事，一行一条）
风险：（会上提到的隐患或没解决的问题，没有就写「无」）
下一步：（谁、什么时候、做什么）

不要复述讨论过程，不要写「大家一致认为」这种空话。
```

**给执行团队看的版本** —— 要具体到可执行：

```text
请输出任务清单，每条包含：
- 负责人（从逐字稿里的说话人判断，判断不出就写「待定」）
- 具体动作（动词开头，可执行）
- 截止时间（会上提到的就写，没提就写「未定」）
- 验收标准（做到什么程度算完成）

用 Markdown 表格输出。没有明确负责人的任务不要编。
```

**英文会议要求英文纪要** —— 在提示词开头加：

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

群 ID 用 `lark-cli im +search-chat` 之类的命令能查到。

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
