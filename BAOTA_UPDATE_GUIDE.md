# 宝塔端口配置更新指南

## 📋 操作步骤

### 第一步：停止当前服务

1. 在宝塔面板中找到 **Python项目管理** → **fuwu** 项目
2. 点击 **「停止」** 按钮
3. 确认服务已停止（当前系统：绿色变成灰色）

### 第二步：更新启动配置

在宝塔的 fuwu 项目设置中找到以下配置项：

#### 方式 A：修改启动命令（推荐）

找到「启动命令」或「运行命令」输入框，可能当前是：
```bash
cd /www/wwwroot/fuwu && /www/wwwroot/fuwu/.venv/bin/fuwu-server
```

**改为：**
```bash
cd /www/wwwroot/fuwu && FUWU_PORT=8088 /www/wwwroot/fuwu/.venv/bin/fuwu-server
```

#### 方式 B：添加环境变量

如果有「环境变量」设置区域：
```
变量名: FUWU_PORT
变量值: 8088
```

### 第三步：更新端口号显示

在宝塔项目配置中找到：
- **监听端口** 或 **端口** 设置
- 从 `8000` 改为 `8088`

### 第四步：保存并重启

1. 点击 **「保存」** 按钮
2. 点击 **「启动」** 或 **「重启」** 按钮
3. 等待几秒钟

### 第五步：验证服务

#### 在宝塔界面检查
- **监听端口** 应显示：`8088`
- **当前状态** 应显示：绿色（运行中）

#### 在服务器命令行验证

```bash
# 1. 检查进程
ps aux | grep fuwu-server

# 2. 检查端口监听
lsof -i:8088
# 应该看到类似：
# Python  2205675 www  3u  IPv4  ...  TCP *:8088 (LISTEN)

# 3. 测试 API
curl http://127.0.0.1:8088/health
# 应该返回：{"status": "ok"}

# 4. 查看日志（应该有时间戳）
journalctl -u fuwu -n 20
# 或者在宝塔的「项目日志」中查看
```

#### 预期日志输出
```
2026-10-22 17:50:15 [INFO] fuwu release service listening on 0.0.0.0:8088
2026-10-22 17:50:15 [INFO] API endpoints available at http://0.0.0.0:8088/api/
```

---

## ⚠️ 注意事项

### 1. Nginx 反向代理更新

如果你使用了 Nginx 反向代理，需要更新配置：

```bash
# 找到 Nginx 配置文件
cd /www/server/panel/vhost/nginx/

# 找到 fuwu 相关的配置文件
grep -r "proxy_pass.*8000" .
```

**修改前：**
```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8000;
}
```

**修改后：**
```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8088;
}
```

**重载 Nginx：**
```bash
nginx -t  # 测试配置
nginx -s reload  # 重载配置
```

### 2. 防火墙规则

如果 8088 需要外部访问，在宝塔面板中：
1. 进入 **安全** → **防火墙**
2. 添加规则：
   - 端口：`8088`
   - 协议：`TCP`
   - 说明：`fuwu API 服务`

---

## 🔧 故障排查

### 问题 1：启动后立即停止

**检查日志：**
```bash
# 在宝塔「项目日志」或运行：
tail -100 /www/wwwroot/fuwu/logs/error.log
```

**可能原因：**
- 端口仍被占用
- 虚拟环境损坏
- 代码拉取不完整

**解决：**
```bash
# 1. 检查端口占用
lsof -i:8088
# 如果有进程，记下 PID，然后：
kill -9 <PID>

# 2. 重新拉取代码
cd /www/wwwroot/fuwu
git fetch origin
git reset --hard origin/main
```

### 问题 2：authentication required

这通常是旧服务缓存问题：

```bash
# 1. 清理所有 fuwu 进程
pkill -9 -f fuwu-server

# 2. 确认清理完成
ps aux | grep fuwu

# 3. 在宝塔重新启动
```

### 问题 3：Cannot bind to port

**错误信息：**
```
OSError: [Errno 98] Address already in use
```

**解决：**
```bash
# 找到占用端口的进程
lsof -ti:8088

# 强制停止
kill -9 $(lsof -ti:8088)

# 重新启动服务
```

---

## ✅ 完成确认

确认以下所有项都正确：

- [ ] 宝塔显示监听端口：**8088**
- [ ] 服务状态：**绿色运行中**
- [ ] `curl http://127.0.0.1:8088/health` 返回 `{"status": "ok"}`
- [ ] 日志显示：`listening on 0.0.0.0:8088`
- [ ] 日志带时间戳（如：`2026-10-22 17:50:15`）
- [ ] 前端页面能正常访问（不再显示 authentication required）

---

## 📞 需要帮助？

如果遇到问题，提供以下信息：

1. 宝塔日志截图或最后 50 行：
   ```bash
   tail -50 /www/wwwroot/fuwu/logs/error.log
   ```

2. 进程状态：
   ```bash
   ps aux | grep fuwu
   lsof -i:8088
   ```

3. 启动命令：
   ```bash
   # 宝塔界面中显示的启动命令
   ```
