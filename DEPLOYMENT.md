# AutoLeadGen 海外客 — 服务器部署指南

## 🚀 一键部署（推荐）

### 前提条件
- 一台 Linux 服务器（阿里云 ECS / 腾讯云 CVM 等）
- 已安装 Docker 和 Docker Compose
- 服务器可以访问你的阿里云 RDS 数据库

### 步骤

```bash
# 1. 上传代码到服务器
scp -r AutoLeadGen/ root@你的服务器IP:/opt/autoleadgen

# 2. SSH 登录服务器
ssh root@你的服务器IP

# 3. 进入项目目录
cd /opt/autoleadgen

# 4. 配置环境变量（最关键的一步！）
cp .env.example .env
nano .env   # 填入你的真实配置
```

`.env` 文件必须填写以下内容：

```env
DATABASE_URL=mysql+pymysql://用户名:密码@数据库地址:3306/数据库名?charset=utf8mb4
MINIMAX_API_KEY=你的MiniMax API Key
SNOVIO_CLIENT_ID=你的Snov.io Client ID
SNOVIO_CLIENT_SECRET=你的Snov.io Client Secret
TAVILY_API_KEY=你的Tavily API Key
```

```bash
# 5. 一键启动！
docker compose up -d --build

# 6. 查看日志确认运行正常
docker compose logs -f
```

启动后访问 `http://你的服务器IP:8888` 即可使用前端；后端 API 暴露在 `http://你的服务器IP:8001`。

---

## ✅ 自动化保障

| 保障项 | 状态 |
|--------|------|
| 崩溃自动重启 | ✅ `restart: always` |
| 开机自启动 | ✅ Docker 服务默认开机启动 |
| 日志防爆盘 | ✅ 限制 10MB × 3 个文件 |
| 健康检查 | ✅ 每 30 秒检测一次 |
| 后台邮件发送 | ✅ outbound_engine 随应用启动 |
| 收件箱监控 | ✅ inbox_monitor 每 5 分钟检查 |

**部署后你什么都不用管。** 服务器重启、应用崩溃都会自动恢复。

---

## 🔧 常用运维命令

```bash
# 查看实时日志
docker compose logs -f

# 重启服务
docker compose restart

# 停止服务
docker compose down

# 更新代码后重新部署
docker compose up -d --build

# 查看容器状态
docker compose ps
```

---

## 🌐 配置域名 + HTTPS（可选）

如果你想用域名访问（如 `crm.yourcompany.com`），在服务器上安装 Nginx：

```bash
# 安装 Nginx + Certbot (SSL)
apt update && apt install -y nginx certbot python3-certbot-nginx

# 创建 Nginx 配置
cat > /etc/nginx/sites-available/autoleadgen << 'EOF'
server {
    listen 80;
    server_name crm.yourcompany.com;  # 改成你的域名

    location / {
        proxy_pass http://127.0.0.1:8888;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;  # AI 搜索可能需要较长时间
    }
}
EOF

# 启用配置
ln -sf /etc/nginx/sites-available/autoleadgen /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# 自动配置 HTTPS（免费 Let's Encrypt 证书）
certbot --nginx -d crm.yourcompany.com
```

---

## ⚠️ 安全提醒

1. **防火墙**：只开放 80/443 端口（如果用 Nginx），或 8001 端口（直连模式）
2. **修改默认密码**：首次登录后立即修改 admin 账号的密码
3. **`.env` 文件**：绝对不要提交到 Git！（已在 `.dockerignore` 和 `.gitignore` 中排除）
4. **退订链接**：生产环境设置 `PUBLIC_APP_URL=https://你的域名`，确保邮件中的一键退订链接可访问
5. **Webhook 签名**：使用 Unipile 时设置 `UNIPILE_WEBHOOK_SECRET`，生产环境未配置会拒绝 webhook
6. **人工审核默认**：`OUTBOUND_AUTO_SEND_DRAFTS` 默认关闭，试点期建议保持人工确认后发送
7. **积分制**：上线商业化前运行 `python migrate_v7.py`，并按套餐配置 `CREDITS_DEFAULT_BALANCE` 与 `CREDITS_COST_*`

---

## 🐛 常见问题

### Docker 未安装
```bash
# 一键安装 Docker（适用于 Ubuntu/Debian）
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker
```

### 数据库连接失败
- 检查阿里云 RDS 的**白名单**是否添加了服务器的公网 IP
- 检查 `.env` 中的数据库地址和密码是否正确

### 端口被占用
```bash
# 修改 docker-compose.yml 中的端口映射
# 例如改为 8080:8000
ports:
  - "8080:8000"
```
