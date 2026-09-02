#!/usr/bin/env bash
# NASDX 云服务器（腾讯云轻量/任意 Ubuntu 系）一键部署脚本
#
# 用法（在服务器上，需 root 或 sudo）：
#   sudo bash setup_server.sh
#
# 前置：本脚本同目录必须有：
#   config.toml   —— 含真实 Agnes key 的服务器配置（.gitignore 已忽略，不入库）
#   dist/         —— 前端构建产物（本机 npm run build 后随包上传）
#
# 环境变量可覆盖：
#   APP_ROOT     部署目录       默认 /opt/nasdx
#   REPO_URL     git 仓库地址   默认 https://github.com/Caesar-ZZh/NASDX.git
#   BRANCH       部署分支       默认 master
#   PORT         服务端口       默认 8901
#   CONFIG_SRC   config 来源    默认本脚本同目录 config.toml
#   DIST_SRC     dist 来源      默认本脚本同目录 dist/
set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/nasdx}"
REPO_URL="${REPO_URL:-https://github.com/Caesar-ZZh/NASDX.git}"
BRANCH="${BRANCH:-master}"
PORT="${PORT:-8901}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_SRC="${CONFIG_SRC:-$SCRIPT_DIR/config.toml}"
DIST_SRC="${DIST_SRC:-$SCRIPT_DIR/dist}"

# ---------- 0. 权限与前置检查 ----------
if [ "$(id -u)" -ne 0 ]; then
  echo "请用 root 或 sudo 运行：sudo bash setup_server.sh" >&2
  exit 1
fi
[ -f "$CONFIG_SRC" ] || { echo "缺少 config.toml（含 Agnes key，随部署包分发，不入库）" >&2; exit 1; }
[ -d "$DIST_SRC/assets" ] || { echo "缺少 dist/ 前端构建产物（本机 npm run build 后随包上传）" >&2; exit 1; }

echo "==> [1/7] 探测系统包管理器"
if command -v apt-get >/dev/null 2>&1; then
  PKG=apt
elif command -v dnf >/dev/null 2>&1; then
  PKG=dnf
elif command -v yum >/dev/null 2>&1; then
  PKG=yum
else
  echo "不支持的包管理器（仅支持 apt/dnf/yum）" >&2
  exit 1
fi
echo "    使用 $PKG"

echo "==> [2/7] 安装基础依赖 (python3/venv/pip/git/curl)"
if [ "$PKG" = "apt" ]; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-pip git curl
else
  "$PKG" install -y python3 python3-pip git curl || true
  python3 -m ensurepip --upgrade 2>/dev/null || true
fi

echo "==> [3/7] 拉取代码 (branch=$BRANCH)"
if [ -d "$APP_ROOT/.git" ]; then
  git -C "$APP_ROOT" fetch origin
  git -C "$APP_ROOT" checkout -f "$BRANCH"
  git -C "$APP_ROOT" pull --ff-only origin "$BRANCH" || echo "    pull 失败，继续使用现有代码"
else
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_ROOT" \
    || git clone --depth 1 --branch "$BRANCH" "https://ghfast.top/https://github.com/Caesar-ZZh/NASDX.git" "$APP_ROOT" \
    || { echo "    GitHub 克隆失败（国内网络），请改用 scp 上传源码后重试" >&2; exit 1; }
fi

echo "==> [4/7] 放置前端构建产物 frontend/dist"
rm -rf "$APP_ROOT/frontend/dist"
mkdir -p "$APP_ROOT/frontend"
cp -r "$DIST_SRC" "$APP_ROOT/frontend/dist"
echo "    dist 就位: $(find "$APP_ROOT/frontend/dist" -type f | wc -l) 个文件"

echo "==> [5/7] 写入服务器 LLM 配置（Agnes key，chmod 600）"
mkdir -p "$APP_ROOT/deploy/cloud"
install -m 600 "$CONFIG_SRC" "$APP_ROOT/deploy/cloud/config.toml"
echo "    配置来源: $CONFIG_SRC"

echo "==> [6/7] 补齐未入库文件 + 创建 venv 并安装依赖（首次约 2-5 分钟，走国内 pip 镜像）"
# server/requirements.txt 未入库（本地工作区新增未提交），若随包携带则补齐
if [ -f "$SCRIPT_DIR/server_requirements.txt" ]; then
  install -m 644 "$SCRIPT_DIR/server_requirements.txt" "$APP_ROOT/server/requirements.txt"
  echo "    已补齐 server/requirements.txt（来自部署包）"
fi
# deploy/cloud/ 目录整体未入库（start_nasdx.sh 等），若随包携带则补齐
if [ -f "$SCRIPT_DIR/start_nasdx.sh" ]; then
  mkdir -p "$APP_ROOT/deploy/cloud"
  install -m 755 "$SCRIPT_DIR/start_nasdx.sh" "$APP_ROOT/deploy/cloud/start_nasdx.sh"
  echo "    已补齐 deploy/cloud/start_nasdx.sh（来自部署包）"
fi
cd "$APP_ROOT"
python3 -m venv .venv
PIP_MIRROR="https://mirrors.cloud.tencent.com/pypi/simple"
PIP_MIRROR_FALLBACK="https://pypi.tuna.tsinghua.edu.cn/simple"
.venv/bin/pip install -q -U pip
echo "    核心依赖（server/requirements.txt）..."
.venv/bin/pip install -q -i "$PIP_MIRROR" -r server/requirements.txt \
  || .venv/bin/pip install -q -i "$PIP_MIRROR_FALLBACK" -r server/requirements.txt
echo "    完整功能依赖（requirements_nasdx.txt，akshare/mootdx/streamlit...）"
.venv/bin/pip install -q -i "$PIP_MIRROR" -r requirements_nasdx.txt \
  || .venv/bin/pip install -q -i "$PIP_MIRROR_FALLBACK" -r requirements_nasdx.txt \
  || echo "    部分依赖安装失败（如 tdxrs 无 Linux wheel），核心服务不受影响"

echo "==> [7/7] 安装 systemd 服务并启动 (port=$PORT)"
cat > /etc/systemd/system/nasdx.service <<EOF
[Unit]
Description=NASDX Cosmos Web Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_ROOT
Environment=NASDX_CONFIG_FILE=$APP_ROOT/deploy/cloud/config.toml
Environment=PYTHON_BIN=$APP_ROOT/.venv/bin/python
Environment=PORT=$PORT
ExecStart=$APP_ROOT/deploy/cloud/start_nasdx.sh
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now nasdx
sleep 4

echo "==> 健康检查"
HEALTH="$(curl -s -m 5 "http://127.0.0.1:$PORT/api/health" || true)"
echo "    /api/health => $HEALTH"
echo
echo "========== 部署完成 =========="
echo "内网验证: curl http://127.0.0.1:$PORT/api/health"
echo "公网访问: http://<服务器公网IP>:$PORT   （需在腾讯云控制台防火墙放行 $PORT 端口）"
echo "日志查看: journalctl -u nasdx -f"
echo "服务状态: systemctl status nasdx"
echo "本机 IP 查询: curl -s ifconfig.me"
echo "=============================="
