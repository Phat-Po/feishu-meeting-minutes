# 飞书配置：从零到能跑

这份文档带你从「什么都没有」走到「脚本能读到我的会议」。

全程在浏览器里点，大约 10 分钟。需要你有飞书企业管理员权限，或者让管理员帮你审批。

---

## 第 1 步：建一个自建应用

1. 打开 [飞书开放平台](https://open.feishu.cn/app)（海外版是 [open.larksuite.com](https://open.larksuite.com/app)）
2. 点 **创建企业自建应用**
3. 名字随便起，比如「会议纪要助手」
4. 建好后进入应用，找到 **凭证与基础信息**

这里有两个值，等下要用：

| 名称 | 长什么样 | 用途 |
|---|---|---|
| App ID | `cli_` 开头 | 身份标识，可以公开 |
| App Secret | 一长串随机字符 | **相当于密码，绝对不要提交到 git** |

> 这个 Secret 只存在你自己电脑上，`lark-cli config init` 会把它放进系统钥匙串。

---

## 第 2 步：开权限

进入 **开发配置 → 权限管理**，用搜索框逐个搜下面四条，点「开通权限」。

| 权限代码 | 中文名 | 干什么用 | 不开会怎样 |
|---|---|---|---|
| `vc:meeting.search:read` | 获取会议记录 | 搜出你参加过的线上会议 | 找不到线上会议 |
| `vc:note:read` | 获取会议笔记 | 读笔记详情，拿逐字稿地址 | 找到会议也读不到内容 |
| `docx:document:readonly` | 查看新版文档 | 读逐字稿正文 | 逐字稿拉不下来 |
| `minutes:minutes.basic:read` | 查看妙记基本信息 | 知道上传的录音有哪些 | 上传的录音全看不到 |
| `minutes:minutes.search:read` | 搜索妙记 | 按时间列出妙记 | 找不到上传的录音 |
| `minutes:minutes.artifacts:read` | 获取妙记产物 | 下载妙记的逐字稿 | 有录音但拉不到文字 |
| `docx:document:create` | 创建新版文档 | 把纪要存成飞书文档 | 总结写完了没地方存 |
| `im:message:send_as_bot` | 以应用身份发消息 | 机器人把卡片推给你 | 文档建好了但没通知 |

**只开一部分行不行？**

- 只处理线上会议 → 前三条 + 建文档 + 发消息
- 只处理上传录音 → 中间三条 + 建文档 + 发消息
- 不想收卡片 → 可以不开最后一条，脚本会自动跳过推送

**关于发消息那两条**：`im:message:send_as_bot` 是让机器人发，
`im:message.send_as_user` 是用你自己的账号发。
脚本默认先试机器人，失败会自动退回用你自己的身份 —— 所以两个开一个就能用。

> 权限名在控制台里可能显示成「获取会议信息」「查看、评论和导出文档」之类的中文，
> 直接搜代码里的英文名最准。

---

## 第 3 步：加机器人能力

只有加了机器人，第 2 步的第 4 条权限才有意义。

1. 进入 **应用能力 → 添加应用能力**
2. 找到 **机器人**，点添加

然后设置 **可用范围**：至少要把你自己加进去，否则机器人没法给你发消息。
位置在 **应用发布 → 版本管理与发布 → 可用范围**。

---

## 第 4 步：发布一个版本

**这一步最容易漏。** 权限开好了但没发布版本，所有权限都不生效。

1. 进入 **应用发布 → 版本管理与发布**
2. 点 **创建版本**
3. 版本号填 `1.0.0`，更新说明随便写
4. 申请发布
5. 如果企业开启了审核，去 **管理后台 → 应用审核** 点通过（需要管理员）

发布成功后，权限才真正挂到这个应用上。

---

## 第 5 步：在电脑上登录

```bash
npx @larksuite/cli@latest install
lark-cli config init
```

`config init` 会依次问你：

- **App ID** —— 第 1 步拿到的 `cli_...`
- **App Secret** —— 第 1 步拿到的那串（输入时不会显示，正常）
- **品牌** —— 飞书选 `feishu`，Lark 选 `lark`

然后授权：

```bash
lark-cli auth login --scope "vc:meeting.search:read vc:note:read docx:document:readonly docx:document:create minutes:minutes.basic:read minutes:minutes.search:read minutes:minutes.artifacts:read im:message:send_as_bot"
```

它会给你一个链接或二维码，在浏览器里点同意。授权是**以你个人的身份**读会议数据 ——
这意味着你只能读到你有权限看的会议，别人的会读不到。这是刻意的设计。

---

## 第 6 步：确认权限真的到位了

```bash
lark-cli auth check --scope vc:meeting.search:read
```

退出码 `0` 是有权限，`1` 是缺权限。

再看看能不能搜到会议：

```bash
lark-cli vc +search --as user --start 2026-01-01 --end 2026-12-31 --json
```

能返回 `{"ok": true, ...}` 就说明通了。

---

## 第 7 步：拿到自己的用户 ID

```bash
lark-cli contact +get-user --json
```

返回里找 `open_id`，形如 `ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`。
把这一串填到 `OPERATOR_OPEN_ID`，纪要就会发给你。

---

## 全部配好之后

回到项目根目录：

```bash
export OPERATOR_OPEN_ID="ou_你的"
export AI_BASE_URL="https://api.anthropic.com"
export AI_API_KEY="sk-ant-..."
export AI_MODEL="claude-sonnet-5-5"

python3 meeting_summary.py --dry-run --lookback 7
```

看到「新纪要: xxx（1xxxx 字）」就是通了。去掉 `--dry-run` 就会真的发卡片。

---

## 卡住了看这里

**`config init` 报错说找不到应用**
App ID 抄错了，或者应用被停用了。回控制台确认应用状态是「已启用」。

**`auth login` 之后还是说权限不足**
九成是第 4 步没做 —— 权限开了但版本没发布，或者发布了但管理员没批。
另外注意：**改了权限后必须重新发布一个版本**，老版本不会自动带上新权限。

**搜得到会议，但一条纪要都没有**
只有**生成了会议笔记**的会议才有逐字稿。你可以用这场会议验证一下：

```bash
lark-cli vc +detail --as user --meeting-ids <会议ID> --json
```

返回里如果没有 `note_id`，或者带 `"hint": "no minute file for this meeting"`，
就说明这场会本来就没有笔记，脚本跳过它是对的。

**机器人发不出消息**
检查三件事：机器人能力加了吗（第 3 步）；可用范围里有你吗；你和机器人是不是在同一个企业。

**想确认到底哪一步断了**

```bash
lark-cli auth status --json     # 看登录状态和已授权的权限
lark-cli auth scopes            # 看这个应用总共有哪些权限
```

---

## 关于事件订阅

飞书支持「纪要生成时推送事件」，这样就不用轮询。但这个项目默认**没有**用它，原因：

1. 会议类事件（`minutes.minute.generated_v1`、`vc.note.generated_v1`）在部分应用类型下
   控制台里找不到、订不上，我们实测两个企业应用都是这个情况；
2. 事件订阅要么开公网回调地址，要么保持一个长连接常驻进程，比 cron 麻烦得多；
3. 轮询的代价只是「最多晚 10 分钟」，对会议纪要说完全够用。

如果你还是想试，先验证自己的应用能不能订上：

```bash
lark-cli event consume minutes.minute.generated_v1 --as user --dry-run
```

`console_event_published` 显示 `ok` 就说明能订。能订的话，把 `meeting_summary.py`
的定时任务去掉，改成常驻消费事件即可 —— 但那是另一套写法了，不在本项目范围内。
