# 宝塔配置检查清单

## 问题现象
- 代码已改为端口 8088
- 宝塔显示服务运行在端口 8000
- 页面出现 "authentication required" 错误

## 可能原因

### 1. 宝塔启动命令硬编码了端口
宝塔的 Python 项目管理中，启动命令可能是：
```bash
# 旧的（错误）
/www/wwwroot/fuwu/.venv/bin/fuwu-server --host 0.0.0.0 --port 8000

# 新的（正确）
/www/wwwroot/fuwu/.venv/bin/fuwu-server --host 0.0.0.0 --port 8088
```

### 2. 环境变量未设置
如果启动命令没有指定端口，需要在宝塔中设置环境变量：
```bash
FUWU_PORT=8088
```

### 3. 旧服务仍在运行
可能有两个进程：
- 旧进程在 8000 端口（PID 2205675）
- 新进程在 8088 端口

## 解决步骤

### 方案 A：修改宝塔启动命令（推荐）

1. **在宝塔中点击「配置文件」或「启动命令」**
2. **找到类似这样的命令：**
   ```bash
   cd /www/wwwroot/fuwu && /www/wwwroot/fuwu/.venv/bin/fuwu-server
   ```
3. **改为：**
   ```bash
   cd /www/wwwroot/fuwu && FUWU_PORT=8088 /www/wwwroot/fuwu/.venv/bin/fuwu-server
   ```

### 方案 B：在宝塔环境变量中设置

1. 点击「环境管理」或「环境变量」
2. 添加：
   ```
   变量名: FUWU_PORT
   变量值: 8088
   ```

### 方案 C：直接改回 8000（不推荐）

如果 8000 端口对你们来说没问题，可以把代码改回去：
```python
port = port or int(os.environ.get("FUWU_PORT", "8000"))  # 改回 8000
```

## 验证步骤

1. **停止服务**（在宝塔界面点击「停止」）
2. **确认进程已清理：**
   ```bash
   lsof -i:8000
   lsof -i:8088
   ```
3. **重新启动服务**（点击「重启」）
4. **检查日志：**
   - 应该看到：`fuwu release service listening on 0.0.0.0:8088`
5. **访问新端口：**
   ```bash
   curl http://127.0.0.1:8088/health
   ```

## 需要更新的地方

### 1. Nginx 配置（如果有反向代理）
```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8088;  # 改为 8088
}
```

### 2. 防火墙规则
如果外部需要访问，开放 8088 端口：
```bash
firewall-cmd --zone=public --add-port=8088/tcp --permanent
firewall-cmd --reload
```
