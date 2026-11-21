# AGENTS.md — 流水线说明书

> 这份文档写给**要让 AI 复现、改造或维护这条流水线**的人。
> 想知道「怎么用」，看 [README](README.md)。想知道「它是怎么搭出来的」，看这里。

一句话：**飞书会议 / 录音 → 逐字稿 → 模型出结构化 JSON → 渲染成飞书文档 + 白板 + 卡片。**

---

## 一、流水线六个阶段

| # | 阶段 | 代码入口 | 输入 | 输出 | 这一步算成了的标志 |
|---|---|---|---|---|---|
| 1 | 取逐字稿 | `scan_vc` / `scan_minutes` → `fetch_transcript` | 账号 + 时间窗口 | 纯文本 | 长度 ≥ `MIN_CHARS`（默认 600） |
| 2 | 清洗 | `clean_transcript` | 原始逐字稿 | 去噪文本 | 时间戳行消失、语义完整 |
| 3 | 调模型 | `summarize` | 清洗后文本 | Python dict | `ask_json` 能解析出对象 |
| 4 | 校验渲染 | `render_doc` + `build_board_dsl` | dict | 文档 XML + 白板 DSL | XML 含 `<title>` 和 `<whiteboard>` |
| 5 | 建文档 | `create_doc` | XML | URL + 画板 token | 返回非空 url |
| 6 | 写白板 | `to_openapi` → `write_board` | DSL + 画板 token | 画板内容 | 接口返回 `ok: true` |

任何一步返回空，就跳过这条记录、不写状态，下次重试。

---

## 二、第 1 步：两个数据源，接口链不一样

| | 线上会议 | 上传录音 |
|---|---|---|
| 领域 | `vc` | `minutes`（妙记） |
| 列表 | `vc +search --start --end` | `minutes +search --owner-ids me --start --end` |
| 拿到内容 | `vc +detail` → `note_id` → `note +detail` → `verbatim_doc_token` → `docs +fetch` | `minutes +detail --transcript --output-dir` → 读文件 |
| 有说话人名字吗 | **没有**，只有时间戳 | **有**，`Speaker 1` / `Speaker 2` |
| 有播放地址吗 | 没有 | 有，`meta_data.app_link` |

**两个坑**：

1. `minutes +search` 一页最多 30 条，**必须翻页**（`page_token` + `has_more`），否则录音多的账号会静默漏数据。
2. `minutes +detail` 不返回播放地址，只有 `+search` 的 `meta_data.app_link` 有。

---

## 三、第 2 步：清洗

线上会议的逐字稿里，纯时间戳行占了近三成 token，对理解毫无帮助。

规则：

- 纯时间戳行删掉，每 5 分钟留一个 `[HH:MM:SS]` 锚点（后面章节要用）
- `Speaker 1 00:00:00.590` 改写成 `Speaker 1 [00:00:00]`
- 长度 ≤ 6 字的碎句合并到上一行

**别删说话人标签** —— 那是判断「谁在反对谁」的唯一线索。

---

## 四、第 3 步：调用模型

两种后端，二选一：

| 方式 | 触发条件 | 约定 |
|---|---|---|
| 外部命令 | 设了 `SUMMARY_CMD` | 提示词从 **stdin** 进，结果从 **stdout** 出，失败必须返回**非 0 退出码** |
| HTTP 接口 | 设了 `AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL` | Anthropic 兼容的 `/v1/messages` |

**两个必须注意的点**：

1. **`max_tokens` 默认 32000**。部分推理模型会先输出一大段 `thinking` 块，额度小了正文一个字都出不来（`stop_reason=max_tokens`、`blocks=['thinking']`）。16000 在带议题概括的长纪要上实测不够。
2. **`SUMMARY_CMD` 失败必须用非 0 退出码表示**。退出码为 0 的话，报错信息会被当成纪要发出去。

超长稿（> `CHUNK_ABOVE`，默认 30000 字）走两段式：

1. `MAP_PROMPT` 分块只提**事实**（议题 / 决定 / 待办 / 原话），不写散文
2. `REDUCE_PROMPT` 合并成最终 JSON

先写散文再合并，细节会一层层磨没 —— 提事实不会。

---

## 五、第 4 步：模型输出契约

模型必须返回**纯 JSON**，结构如下。这是整条流水线唯一的接口约定。

```json
{
  "title": "这场会是什么，12-20 字",
  "one_line": "一句话，40 字以内",
  "core_points": ["最关键的结论，2-3 条，每条 30 字以内"],
  "modules": [{
    "name": "模块名，8 字以内",
    "icon": "图标名",
    "group": "属于哪一段",
    "items": [{"head": "结论，12 字以内", "sub": "说明，24 字以内"}]
  }],
  "decisions": [{"what": "定了什么", "why": "为什么"}],
  "todos": [{"what": "要做什么", "who": "谁", "when": "什么时候"}],
  "quotes": [{"text": "原话", "why": "为什么值得记"}],
  "summary": [{"topic": "议题名", "digest": "2-3 句概括、60-120 字", "points": ["细节"]}],
  "chapters": [{"time": "MM:SS", "title": "章节名", "note": "一句话"}]
}
```

各字段的硬约束和兜底：

| 字段 | 数量 | 约束 | 缺了怎么办 |
|---|---|---|---|
| `title` | 1 | 12-20 字，不要「会议」「讨论」这种空词 | 退回录音原名 |
| `core_points` | 2-3 | 每条 30 字以内，给数字和判断 | 整块不渲染 |
| `modules` | 2-8 | 每段 2-5 条 items | 整块不渲染，白板退回 Mermaid |
| `modules[].icon` | 1 | **必须在白名单里**（见 `ICON_SET`） | 兜底 `topic-discussion` |
| `modules[].group` | 2-3 段 | 同段写相同值 | 全部当成一段 |
| `summary[].digest` | 每条必有 | 2-3 句、60-120 字 | 不渲染段落，只剩要点 |
| `chapters` | ≤16 | 时间能解析成 `MM:SS` / `HH:MM:SS` | 时间戳不转链接 |

**为什么所有字段都有字数上限**：早期版本没有 `digest`，每个字段都限成一句话，结果整篇像 PPT 提纲。写死的字数上限是刻意的约束 —— 加字段时要想清楚「这个字段是不是又一个只能写一句话的地方」。

---

## 六、第 5 步：渲染成飞书文档

文档是飞书 DocxXML，块按这个顺序拼：

```
<title>会议纪要：{title} {date}</title>
<whiteboard …>                     ← 画板
<callout emoji="📌">               ← 一句话 + 听录音链接
<grid><column><callout>…           ← 左：关键决策；右：待办（checkbox）
<h1>💬 金句时刻</h1> + <blockquote>
<h1>总结</h1> + <h2>议题</h2> + <p>digest</p> + <ul>points</ul>
<h1>详细章节</h1> + <table>        ← 时间做成 ?t= 跳转链接
<hr/> + 脚注
```

**飞书特有的块**（换成别的平台要重做）：

| 块 | 用途 | 换平台时 |
|---|---|---|
| `<whiteboard type="blank">` | 原生画板容器 | 换成图片或别的图组件 |
| `<callout emoji background-color>` | 高亮块 | 换成 blockquote / 卡片 |
| `<grid><column width-ratio>` | 左右分栏 | 换成两列表格 |
| `<checkbox done="false">` | 可勾选待办 | 换成任务清单 |
| `?t=<毫秒>` | 跳到录音某时刻 | 换成时间轴锚点 |

**通用的部分**：JSON 契约、prompt、清洗规则、兜底策略 —— 这些跟平台无关。

**转义**：所有文本进 XML 前必须转义 `& < >`，否则整篇解析失败。画板内容也一样。

---

## 七、第 6 步：白板三步走

这是整条流水线最容易翻车的地方。

```
① build_board_dsl(data)          Python dict → 布局 DSL JSON
② npx @larksuite/whiteboard-cli  DSL → 飞书画板 OpenAPI 格式
③ whiteboard +update             写入画板块
```

**第 ① 步：排布局**

根节点是 vertical frame，孩子依次是：

- 标题（`text`，24px）
- 核心结论块（`frame`，`#F5F5F4` 底，圆角 12，`information` 图标 + bullet）
- 每个分段：小标题（`text`，16px 灰）+ 卡片行（`horizontal` frame，**每行 3 张**）

卡片是 `frame`：图标 + 彩色标题 + `• head：sub` 单行要点，1px 边框，淡底色（`CARD_PALETTE` 六色轮换）。

**行内不足 3 张时补透明空 frame**，否则最后一张会被 `fill-container` 拉满整行。

**第 ② 步：转格式**

```bash
npx -y @larksuite/whiteboard-cli@0.2.13 -i d.json --to openapi --format json
```

输出从 stdout 读，`data` 字段非空才算成功。这台机器需要 Node.js。

**第 ③ 步：写画板**

```bash
lark-cli whiteboard +update --whiteboard-token <token> --source @b.json --input_format raw
```

**三个坑**：

1. **`@file` 是相对 CLI 的工作目录解析的**，不是相对调用方。文件写在临时目录，就必须让命令 `cwd` 指向那个临时目录。
2. **画板 token 只有在建文档时才有** —— 文档内容里放 `<whiteboard type="blank"></whiteboard>`，从返回的 `new_blocks[].block_token` 拿。
3. **整篇覆盖（`docs +update --command overwrite`）会让画板块重建**，旧 token 失效，要重新 `docs +fetch --doc-format xml` 读一次。

---

## 八、失败与兜底

| 失败点 | 表现 | 兜底 |
|---|---|---|
| 逐字稿太短 | 长度 < `MIN_CHARS` | 跳过，标记已处理 |
| 录音太短 | 时长 < `MIN_DURATION` | 跳过，标记已处理 |
| 模型不返回合法 JSON | `ask_json` 返回 None | 不标记，下次重试 |
| `SUMMARY_CMD` 非 0 退出 | 打印 stderr | 不标记，下次重试 |
| 白板转换失败 | `to_openapi` 返回 None | **退回 Mermaid 分组图**，不会出现空画板 |
| 白板写入失败 | 接口报错 | 文档已建但画板空，日志告警 |
| 建文档失败 | 无 url | 不标记，下次重试 |
| 账号登录过期 | `auth status` 不可用 | 跳过该账号，记 `auth_failed` |

---

## 九、验收清单

改完代码跑这三条：

```bash
python3 meeting_summary.py --self-test          # 不碰网络，纯逻辑
python3 meeting_summary.py --dry-run --lookback 7   # 不建文档、不发卡片、不写状态
python3 meeting_summary.py --only <ID> --force --dry-run --out-dir /tmp/x   # 单条验证，XML 落盘可看
```

`--out-dir` 会把渲染好的 XML 存下来，可以直接检查块结构：

```bash
grep -c "<whiteboard" /tmp/x/*.xml     # 应该有
grep -c "?t=" /tmp/x/*.xml             # 章节时间戳链接
grep -c "<checkbox" /tmp/x/*.xml       # 待办
```

真跑之后回读文档：

```bash
lark-cli docs +fetch --doc <token> --doc-format xml --json   # 看块结构
lark-cli whiteboard +export --whiteboard-token <tok> --output-type preview --output /tmp/b   # 看画板渲染
```

---

## 十、想换平台时要改哪些

| 部分 | 跟平台绑不绑 | 改造难度 |
|---|---|---|
| 取逐字稿 | 绑（飞书 vc / minutes 接口） | 换 API 即可，数据结构自己定 |
| 清洗 | 不绑 | 直接用 |
| 模型 + JSON 契约 | 不绑 | 直接用，字段可以按需增删 |
| 文档渲染 | 绑（DocxXML 块） | 换成 Notion blocks / Markdown 要重写 `render_doc` |
| 白板 | 绑得最死（whiteboard-cli + 私有 OpenAPI 格式） | 只能换成图片渲染（如 Mermaid → PNG） |
| 去重 / 锁 / 心跳 | 不绑 | 直接用 |

**结论**：换平台时，第 1、5、6 步要重写；第 2、3、4 步和整套状态管理可以原样搬。
