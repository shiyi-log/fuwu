# 生产环境快速修复指南

## 问题：端口被占用

### 症状
```
OSError: [Errno 98] Address already in use
```

### 解决方案

#### 方案 1：使用修复脚本（推荐）

```bash
# 在生产服务器上
cd /www/wwwroot/fuwu
./fix_production.sh
```

脚本会自动：
- 检查端口占用
- 停止旧进程
- 重启服务
- 验证服务状态

#### 方案 2：手动修复

**1. 查找占用端口的进程**
```bash
# 查找占用 8000 端口的进程
lsof -ti:8000

# 查看进程详情
ps -fp $(lsof -ti:8000)
```

**2. 停止进程**
```bash
# 杀掉所有占用 8000 端口的进程
kill -9 $(lsof -ti:8000)
```

**3. 使用新端口重启服务**

服务默认端口已改为 **8088**，避免与其他项目冲突。

```bash
# 方法 1：使用环境变量
export FUWU_PORT=8088
fuwu-server

# 方法 2：直接指定
FUWU_PORT=8088 fuwu-server

# 方法 3：使用 systemd 重启
sudo systemctl restart fuwu
```

**4. 验证服务**
```bash
curl http://127.0.0.1:8088/health
```

## 改进：日志时间戳

### 更新内容

修改了 `fuwu_service/server.py`，添加了：

1. **带时间戳的日志**
   - 格式：`2026-10-22 07:49:18 [INFO] message`
   - 包含日期和时间

2. **更详细的启动信息**
   ```
   2026-10-22 07:49:18 [INFO] fuwu release service listening on 0.0.0.0:8088
   2026-10-22 07:49:18 [INFO] API endpoints available at http://0.0.0.0:8088/api/
   ```

3. **请求日志**
   - 记录每个 API 请求
   - 自动过滤健康检查请求（避免日志过多）

4. **端口冲突提示**
   ```
   2026-10-22 07:49:18 [ERROR] Port 8088 is already in use. Please:
   2026-10-22 07:49:18 [ERROR]   1. Check for other processes: lsof -ti:8088
   2026-10-22 07:49:18 [ERROR]   2. Kill them: kill -9 $(lsof -ti:8088)
   2026-10-22 07:49:18 [ERROR]   3. Or set a different port: FUWU_PORT=8089 fuwu-server
   ```

### 重新部署

**本地开发：**
```bash
cd /Users/shiyi/Documents/ChatGPT/fuwu
uv pip install -e .
fuwu-server
```

**生产环境：**
```bash
cd /www/wwwroot/fuwu
source .venv/bin/activate
uv pip install -e .
sudo systemctl restart fuwu
```

## 生产环境配置

### Systemd 服务文件

如果使用 systemd，更新服务配置：

```ini
# /etc/systemd/system/fuwu.service
[Unit]
Description=Fuwu Release Service
After=network.target

[Service]
Type=simple
User=admin
WorkingDirectory=/www/wwwroot/fuwu
Environment="FUWU_PORT=8088"
Environment="FUWU_HOST=127.0.0.1"
Environment="FUWU_DATABASE=/www/wwwroot/fuwu/fuwu_service/releases.db"
ExecStart=/www/wwwroot/fuwu/.venv/bin/fuwu-server
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

重载配置：
```bash
sudo systemctl daemon-reload
sudo systemctl restart fuwu
sudo systemctl status fuwu
```

### Nginx 反向代理

如果使用 Nginx，更新端口配置：

```nginx
location /fuwu/ {
    proxy_pass http://127.0.0.1:8088/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

重载 Nginx：
```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 环境变量

可用的环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `FUWU_PORT` | `8088` | 服务监听端口 |
| `FUWU_HOST` | `0.0.0.0` | 服务监听地址 |
| `FUWU_DATABASE` | `fuwu.sqlite3` | 数据库文件路径 |
| `FUWU_STAGING_DIR` | `staging` | 临时文件目录 |
| `FUWU_STORAGE` | `memory` | 存储类型 (memory/s3) |
| `FUWU_S3_PREFIX` | `releases` | S3 存储前缀 |

## 故障排查

### 1. 服务无法启动

**检查端口**
```bash
lsof -ti:8088
netstat -tuln | grep 8088
```

**检查进程**
```bash
ps aux | grep fuwu-server
pgrep -f fuwu-server
```

**检查日志**
```bash
# systemd 日志
journalctl -u fuwu -f

# 直接运行查看输出
FUWU_PORT=8088 fuwu-server
```

### 2. 数据库权限

```bash
# 检查数据库文件权限
ls -la /www/wwwroot/fuwu/fuwu_service/releases.db

# 修复权限
chown admin:admin /www/wwwroot/fuwu/fuwu_service/releases.db
chmod 644 /www/wwwroot/fuwu/fuwu_service/releases.db
```

### 3. Python 环境

```bash
# 验证虚拟环境
which python3
python3 --version

# 重新安装
cd /www/wwwroot/fuwu
python3 -m venv .venv
source .venv/bin/activate
uv pip install -e .
```

## 监控

### 健康检查

```bash
# 手动检查
curl http://127.0.0.1:8088/health

# 自动监控
watch -n 5 'curl -s http://127.0.0.1:8088/health | python3 -m json.tool'
```

### 日志监控

```bash
# 实时查看日志
journalctl -u fuwu -f

# 查看最近 50 行
journalctl -u fuwu -n 50

# 查看错误日志
journalctl -u fuwu -p err

# 查看今天的日志
journalctl -u fuwu --since today
```

## 联系

如果问题仍未解决，请检查：
1. 服务器防火墙规则
2. SELinux 配置
3. Python 版本兼容性
4. 磁盘空间
