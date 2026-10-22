#!/bin/bash
# 修复生产环境 fuwu 服务

# 默认端口改为 8088
PORT=${FUWU_PORT:-8088}

echo "=== 修复 fuwu 生产环境 ==="
echo "使用端口: $PORT"

# 1. 查找占用端口的进程
echo ""
echo "1. 检查端口 $PORT 占用情况..."
PORT_PID=$(lsof -ti:$PORT 2>/dev/null)

if [ -z "$PORT_PID" ]; then
    echo "   ✓ 端口 $PORT 未被占用"
else
    echo "   发现进程占用端口 $PORT: PID $PORT_PID"
    ps -fp $PORT_PID

    echo ""
    read -p "   是否杀掉这些进程? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "   正在停止进程..."
        kill -9 $PORT_PID
        sleep 2
        echo "   ✓ 进程已停止"
    fi
fi

# 2. 检查是否有残留的 fuwu-server 进程
echo ""
echo "2. 检查 fuwu-server 进程..."
FUWU_PIDS=$(pgrep -f "fuwu-server")

if [ -z "$FUWU_PIDS" ]; then
    echo "   ✓ 没有残留的 fuwu-server 进程"
else
    echo "   发现 fuwu-server 进程:"
    ps -fp $FUWU_PIDS

    echo ""
    read -p "   是否杀掉这些进程? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "   正在停止进程..."
        kill -9 $FUWU_PIDS
        sleep 2
        echo "   ✓ 进程已停止"
    fi
fi

# 3. 检查 systemd 服务状态
echo ""
echo "3. 检查 systemd 服务..."
if systemctl is-active --quiet fuwu 2>/dev/null; then
    echo "   fuwu 服务正在运行"
    systemctl status fuwu --no-pager -l

    echo ""
    read -p "   是否重启 fuwu 服务? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "   正在重启服务..."
        sudo systemctl restart fuwu
        sleep 2
        systemctl status fuwu --no-pager -l
    fi
else
    echo "   fuwu 服务未运行"

    echo ""
    read -p "   是否启动 fuwu 服务? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "   正在启动服务..."
        sudo systemctl start fuwu
        sleep 2
        systemctl status fuwu --no-pager -l
    fi
fi

# 4. 验证服务
echo ""
echo "4. 验证服务..."
sleep 2

if curl -s http://127.0.0.1:$PORT/health > /dev/null 2>&1; then
    echo "   ✓ 服务正常运行"
    curl -s http://127.0.0.1:$PORT/health | python3 -m json.tool 2>/dev/null || curl -s http://127.0.0.1:$PORT/health
else
    echo "   ✗ 服务无法访问"
    echo ""
    echo "   请检查日志:"
    echo "   journalctl -u fuwu -n 50 --no-pager"
    echo ""
    echo "   或手动启动测试:"
    echo "   FUWU_PORT=$PORT fuwu-server"
fi

echo ""
echo "=== 修复完成 ==="
echo ""
echo "提示:"
echo "  - 服务端口已改为 $PORT (默认)"
echo "  - 可通过环境变量修改: export FUWU_PORT=8089"
echo "  - 日志现在包含时间戳"
