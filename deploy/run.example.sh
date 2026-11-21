#!/bin/bash
# 定时任务启动器模板 —— 复制成项目根目录的 run.sh 再改路径
#
# 为什么不直接在 crontab 里写 python3：
#   1. cron 的 PATH 很短，npm 全局装的 lark-cli 找不到，会「命令不存在」地静默失败
#   2. 模型凭据要么写进 crontab（会被 ps 看到），要么像这里一样从文件读
#
# 复制到项目根目录后，crontab 写：
#   */10 * * * * cd /path/to/repo && /bin/bash run.sh >> run.log 2>&1
set -euo pipefail

cd "$(dirname "$0")"

# ① PATH 里补上 lark-cli 所在目录（用 `which lark-cli` 查）
export PATH="/home/YOUR_USER/.npm-global/bin:/usr/local/bin:/usr/bin:/bin"

# ② 模型凭据，从文件读，不进命令行、不进日志
#    如果直接用 AI_BASE_URL / AI_API_KEY / AI_MODEL 三个变量，把这段删掉改成 export
set -a
. /path/to/ai.env
set +a

export AI_BASE_URL="${ANTHROPIC_BASE_URL:-}"
export AI_API_KEY="${ANTHROPIC_AUTH_TOKEN:-}"
export AI_MODEL="${ANTHROPIC_MODEL:-}"

# ③ 部分模型会先输出一大段思考，额度小了正文一个字都出不来
export AI_MAX_TOKENS="${AI_MAX_TOKENS:-32000}"

# -u 关掉输出缓冲：重定向到文件时，不加 -u 要等进程结束才看得到日志
exec /usr/bin/python3 -u meeting_summary.py "$@"
