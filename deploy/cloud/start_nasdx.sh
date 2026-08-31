#!/usr/bin/env bash
# NASDX 云服务器启动脚本（Linux）
#
# 职责：
#   1. 从 deploy/cloud/config.toml 读取 [llm] 配置，注入 NASDX_API_KEY / NASDX_BASE_URL / NASDX_MODEL
#      （server 层 server/stock/llm_cfg.py 与 quant 层 nasdx/llm.py 都读这组环境变量）
#   2. 以 React/Cosmos 模式启动：uvicorn server.main:app 同源托管 frontend/dist
#
# 用法：
#   ./deploy/cloud/start_nasdx.sh                     # 默认 8901 端口
#   PORT=8900 ./deploy/cloud/start_nasdx.sh           # 指定端口
#   MODE=streamlit ./deploy/cloud/start_nasdx.sh      # 改用 Streamlit 模式（8502）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONFIG_FILE="${NASDX_CONFIG_FILE:-$SCRIPT_DIR/config.toml}"
PORT="${PORT:-8901}"
MODE="${MODE:-react}"   # react | streamlit

if [ ! -f "$CONFIG_FILE" ]; then
  echo "[nasdx] 找不到配置文件: $CONFIG_FILE" >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[nasdx] 未找到 python3，请先安装 Python 3.11+" >&2
  exit 1
fi

# 从 config.toml 的 [llm] 表导出 NASDX_* 环境变量
ENV_FILE="$(mktemp)"
"$PYTHON_BIN" - "$CONFIG_FILE" "$ENV_FILE" <<'PY'
import sys
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # Python 3.10 及以下，tomli 在依赖清单中
with open(sys.argv[1], "rb") as f:
    cfg = tomllib.load(f)
llm = cfg.get("llm", {}) or {}
with open(sys.argv[2], "w", encoding="utf-8") as out:
    for key in ("api_key", "base_url", "model", "fallback_models"):
        value = str(llm.get(key, "")).strip()
        if value:
            out.write(f"export NASDX_{key.upper()}={value!r}\n")
    # 可选 API 访问鉴权：[api] api_key 非空时导出 VR_API_KEY，
    # server 层会要求所有 /api/* 带 Authorization: Bearer <key>。
    # config.toml 被 .gitignore 忽略，key 不会进仓库。
    api = cfg.get("api", {}) or {}
    api_key = str(api.get("api_key", "")).strip()
    if api_key:
        out.write(f"export VR_API_KEY={api_key!r}\n")
PY
# shellcheck disable=SC1090
. "$ENV_FILE"
rm -f "$ENV_FILE"

echo "[nasdx] 配置注入完成: base_url=$(printenv NASDX_BASE_URL) model=$(printenv NASDX_MODEL)"
echo "[nasdx] 工作目录: $APP_ROOT"
cd "$APP_ROOT"

if [ "$MODE" = "streamlit" ]; then
  # Streamlit 模式（原版网页入口，8502）
  exec "$PYTHON_BIN" -m streamlit run app.py \
    --server.address 0.0.0.0 \
    --server.port "${PORT:-8502}" \
    --server.headless true
fi

# React/Cosmos 模式（默认）：同源托管 frontend/dist，单进程跑整套产品
if [ ! -d "$APP_ROOT/frontend/dist" ]; then
  echo "[nasdx] 缺少 frontend/dist，请先在本地执行 npm run build 后上传" >&2
  exit 1
fi
exec "$PYTHON_BIN" -m uvicorn server.main:app --host 0.0.0.0 --port "$PORT"
