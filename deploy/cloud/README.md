# NASDX 云服务器部署指南（React/Cosmos 模式）

> 目标：把 NASDX 部署到公网服务器，朋友通过浏览器访问，**开箱即用、无需配置任何 Key**。
> LLM 默认模型已固定为 **Agnes AI（agnes-2.5-flash）**，Key 已内置在 `deploy/cloud/config.toml`。

---

## 0. 部署包内容

| 文件 | 作用 |
|---|---|
| `deploy/cloud/config.toml` | 服务器 LLM 配置（含真实 Agnes Key，**已被 .gitignore 忽略，不会进 Git**） |
| `deploy/cloud/setup_server.sh` | **服务器端一键部署脚本**（Ubuntu/CentOS 自适应）：装依赖→拉代码→放前端→写配置→systemd→健康检查 |
| `deploy/cloud/start_nasdx.sh` | Linux 启动脚本：注入 `NASDX_*` 环境变量 + 启动 uvicorn（同源托管前端） |
| `deploy/cloud/nasdx.service` | systemd 服务单元（开机自启 + 崩溃重启） |
| `deploy/cloud/README.md` | 本文档 |

> **注意**：`frontend/dist` 被 `.gitignore` 忽略不入库，服务器 `git clone` 后没有前端。
> 部署前必须**在本机先 `npm run build` 构建前端**，并把 `dist/` 一并上传服务器（见 §5）。

---

## 1. 前置条件

- 一台云服务器（推荐 Ubuntu 22.04/24.04，2C2G 起步）
- 域名（可选但强烈推荐，用于 HTTPS）
- 服务器安全组/防火墙：按需放行 **22（SSH）、443（HTTPS）**；直接裸跑可放行 8901（不推荐）

## 2. 上传代码到服务器

```bash
# 在本机（Windows PowerShell 或 Git Bash）执行：
# 方式 A：git clone（推荐，保持可更新）
git clone https://github.com/Caesar-ZZh/NASDX.git /opt/nasdx

# 方式 B：打包上传（若用便携包/离线方式）
# tar czf nasdx.tar.gz <NASDX 项目目录> 后 scp 上传，再解压到 /opt/nasdx

# 注意：deploy/cloud/config.toml 含 Key 且不进 Git，需要单独上传！
#   本机: scp deploy/cloud/config.toml root@服务器IP:/opt/nasdx/deploy/cloud/
#   或者直接在服务器上 vim 创建（内容见下文 §4）
```

## 3. 安装 Python 与依赖

```bash
# Ubuntu 服务器上执行：
sudo apt update
sudo apt install -y python3 python3-venv python3-pip

cd /opt/nasdx
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip

# 核心依赖（server 层最小集）
pip install -r server/requirements.txt

# 完整功能（行情/扫描/量化）需要：
pip install -r requirements_nasdx.txt
# 若安装 akshare/mootdx 失败（可选），核心接口会自动降级为 501，不阻塞启动
```

> Windows 云服务器备选：见 §7。

## 4. LLM 配置（Key 已内置）

`deploy/cloud/config.toml` 内容即为服务器生效配置（已写入你的 Agnes Key）：

```toml
[llm]
base_url = "https://apihub.agnes-ai.com/v1"
api_key = "<你的 Agnes API Key>"  # 真实 key 只写进 deploy/cloud/config.toml（已 gitignore），绝不要写进任何会提交或分享的文档
model = "agnes-2.5-flash"
```

如果该文件没有随代码上传，在服务器上手动创建：

```bash
mkdir -p /opt/nasdx/deploy/cloud
cat > /opt/nasdx/deploy/cloud/config.toml <<'EOF'
[llm]
base_url = "https://apihub.agnes-ai.com/v1"
api_key = "<你的 Agnes API Key>"  # 真实 key 只写进 deploy/cloud/config.toml（已 gitignore），绝不要写进任何会提交或分享的文档
model = "agnes-2.5-flash"
EOF
chmod 600 /opt/nasdx/deploy/cloud/config.toml
```

配置生效链路：`config.toml` → 启动脚本注入 `NASDX_API_KEY / NASDX_BASE_URL / NASDX_MODEL` 环境变量 → server 层（`server/stock/llm_cfg.py`）与 quant 层（`nasdx/llm.py`）自动读取。

## 5. 启动服务

### 5.1 前台测试启动

```bash
cd /opt/nasdx
chmod +x deploy/cloud/start_nasdx.sh
./deploy/cloud/start_nasdx.sh          # 默认 React 模式，端口 8901
# 另开终端验证：
curl http://127.0.0.1:8901/api/health
# 期望: {"ok":true,"service":"cosmos-api","version":"0.3.0"}
```

### 5.2 systemd 开机自启（推荐）

```bash
# 新建独立运行账号（安全）
sudo useradd -r -s /usr/sbin/nologin nasdx
sudo chown -R nasdx:nasdx /opt/nasdx

# 按实际路径调整 nasdx.service 里的 /opt/nasdx 后安装
sudo cp deploy/cloud/nasdx.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nasdx
sudo systemctl status nasdx        # active (running) 即成功
journalctl -u nasdx -f             # 看日志
```

### 5.3 Streamlit 模式（备选，原版网页入口 8502）

```bash
./deploy/cloud/start_nasdx.sh MODE=streamlit
# 或 systemd 里加: Environment=MODE=streamlit
```

## 6. 公网暴露（推荐：Cloudflare Tunnel + Access，自动 HTTPS + 登录鉴权）

**为什么选 Cloudflare Tunnel**：无需公网 IP、无需在安全组开 8901 端口、自动签发 HTTPS、免费额度够用；叠加 Cloudflare Access 可让朋友用邮箱/一次性验证码登录后才能打开页面（替代 Basic Auth，体验更好）。

> ⚠️ 大陆访问提示：Cloudflare 免费版在中国大陆没有官方节点，速度和连通性视运营商/地区而定（有时直连流畅、有时被干扰）。若你的朋友大多在大陆且实测不通，回退 §6 的 Caddy/Nginx 方案，或换用国内 CDN 前置。

### 方式 A：Cloudflare Tunnel（推荐）

前置：一个域名，且其 DNS 已托管到 Cloudflare（NS 指向 Cloudflare，免费）。

```bash
# 1. 在服务器上安装 cloudflared
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared
cloudflared --version

# 2. 登录并创建隧道（会打开浏览器授权，选你的域名）
cloudflared tunnel login
cloudflared tunnel create nasdx
# 记住输出的 TUNNEL_ID（如 6ff42ae2-xxxx-xxxx）

# 3. 写隧道配置（把 TUNNEL_ID 换成你的）
cat > ~/.cloudflared/config.yml <<EOF
tunnel: TUNNEL_ID
credentials-file: /root/.cloudflared/TUNNEL_ID.json
ingress:
  - hostname: nasdx.example.com
    service: http://127.0.0.1:8901
  - service: http_status:404
EOF

# 4. 把域名指向隧道（自动加 DNS 记录）
cloudflared tunnel route dns nasdx nasdx.example.com

# 5. 前台测试
cloudflared tunnel run nasdx
# 浏览器访问 https://nasdx.example.com 应能打开页面
```

设置开机自启（systemd）：

```bash
sudo cp /etc/cloudflared/config.yml ~/.cloudflared/config.yml 2>/dev/null || true
sudo cloudflared service install
sudo systemctl status cloudflared
```

**加登录鉴权（Cloudflare Access，免费 50 用户内）**：
1. Cloudflare 控制台 → 你的域名 → **Zero Trust**（访问控制）
2. 新建 **Access → Applications**：域名选 `nasdx.example.com`，Policy 选「Email 邮箱验证」或「One-time PIN 一次性验证码」
3. 添加朋友邮箱到 Policy；未登录者打开链接会被要求验证邮箱，通过后才进页面
4. 可选再加 **WAF / 防火墙规则**：仅允许中国大陆+朋友所在地区访问

### 方式 B：Caddy（自动 HTTPS，无需 CF）

```bash
sudo apt install -y caddy
sudo cat > /etc/caddy/Caddyfile <<'EOF'
nasdx.example.com {
    reverse_proxy 127.0.0.1:8901
    basicauth {
        friend $2a$14$xxxxxxxxxxxxxxxxxxxxxxxxxxxx  # caddy hash-password 生成
    }
}
EOF
sudo systemctl reload caddy
```

- 域名解析到服务器 IP，Caddy 自动签发 Let's Encrypt 证书。
- 朋友访问 `https://nasdx.example.com`，输入你给的账号密码即可。

### 方式 C：Nginx + Basic Auth

```bash
sudo apt install -y nginx apache2-utils
htpasswd -c /etc/nginx/.htpasswd friend    # 设置访问密码
sudo cat > /etc/nginx/sites-available/nasdx <<'EOF'
server {
    listen 443 ssl;
    server_name nasdx.example.com;
    # 需要先配置证书（certbot 或自有证书）
    location / {
        auth_basic "NASDX";
        auth_basic_user_file /etc/nginx/.htpasswd;
        proxy_pass http://127.0.0.1:8901;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
sudo ln -s /etc/nginx/sites-available/nasdx /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## 7. Windows 云服务器备选

若你的云服务器是 Windows：

```powershell
# 1. 安装 Python 3.11（官网安装包，勾选 Add to PATH）
# 2. 上传项目到 C:\NASDX
# 3. 安装依赖
cd C:\NASDX
python -m pip install -r server\requirements.txt
python -m pip install -r requirements_nasdx.txt
# 4. 复制 LLM 配置到用户配置目录（等价于内置 Key）
mkdir $env:APPDATA\NASDX -Force
Copy-Item deploy\cloud\config.toml $env:APPDATA\NASDX\config.toml
# 5. 启动（React 模式，同源托管前端）
python -m uvicorn server.main:app --host 0.0.0.0 --port 8901
#    或 Streamlit 模式：
# python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8502
```

- Windows 下 `%APPDATA%\NASDX\config.toml` 会被 `desktop/config.py` 自动读取（含桌面 launcher 路径）。
- 公网暴露同样建议加一层反向代理（如 IIS ARR / Caddy Windows 版）+ HTTPS。

## 8. 验证清单（部署后逐项检查）

| 检查项 | 命令/操作 | 期望 |
|---|---|---|
| 后端健康 | `curl http://127.0.0.1:8901/api/health` | `{"ok":true,...,"version":"0.3.0"}` |
| 前端可访问 | 浏览器开 `http://服务器IP:8901` | 页面正常渲染 |
| LLM 默认模型 | 前端「接入 AI」页或直接发起一次对话 | provider 显示 Agnes / agnes-2.5-flash，无需手填 |
| 公网访问 | 手机 4G 访问 `https://你的域名` | 正常打开（走鉴权） |
| 重启自愈 | `sudo systemctl restart nasdx` | 服务自动恢复 |

## 9. 安全须知（必读）

1. **Key 共享即额度共享**：`config.toml` 里的 Key 是你 Agnes 账号的计费凭证，朋友的使用量都记在你账上。按量计费，建议关注 Agnes 平台用量。
2. **Key 严禁进 Git**：本仓库已配置 secret-scan 门禁（#71），`config.toml` 已被 .gitignore 忽略。不要把 Key 复制到任何会被提交的文件。
3. **公网必须加鉴权**：至少 Basic Auth 或 Cloudflare Access（§6）；有条件的建议开 HTTPS + 限制 IP。**不要裸跑 8901 端口**——NASDX 含持仓/决策/报告数据，且 server/main.py 当前 CORS 为 `allow_origins=["*"]`（已知遗留 P1）。
4. **已知遗留 P1**：`server/main.py` 的 CORS `allow_origins=["*"]` + `allow_credentials=True` 属反模式（CONTEXT 2026-08-25 已记录），公网部署时建议收敛为具体来源。
5. **数据**：`nasdx_history.db` / `nasdx_portfolio*.db` / `reports/` 含你的账户与决策数据，默认就在项目根目录。公网部署建议通过 `[paths]` 指向服务器隔离目录，并定期备份。

## 10. 更新与维护

```bash
cd /opt/nasdx
git pull
sudo systemctl restart nasdx      # systemd 管理则自动拉起
```

- 前端改版后需重新 `npm run build` 并把 `frontend/dist` 更新到服务器（server 层同源托管 dist）。
- 本机开发仍是双轨：Streamlit（8502）+ React（8901），不受本次云部署影响。
