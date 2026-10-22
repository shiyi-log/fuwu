#!/bin/bash
# 宝塔端口迁移诊断脚本
# 在服务器上运行：bash baota_port_check.sh

set -e

echo "🔍 宝塔 fuwu 服务诊断"
echo "================================"
echo

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 1. 检查代码版本
echo "📦 检查代码版本..."
cd /www/wwwroot/fuwu
CURRENT_COMMIT=$(git rev-parse --short HEAD)
LATEST_COMMIT=$(git rev-parse --short origin/main)

if [ "$CURRENT_COMMIT" = "$LATEST_COMMIT" ]; then
    echo -e "${GREEN}✓${NC} 代码已是最新版本: $CURRENT_COMMIT"
else
    echo -e "${YELLOW}⚠${NC}  代码不是最新，需要 git pull"
    echo "   当前: $CURRENT_COMMIT"
    echo "   最新: $LATEST_COMMIT"
fi

# 2. 检查 server.py 端口配置
echo
echo "⚙️  检查代码配置..."
DEFAULT_PORT=$(grep -o 'FUWU_PORT.*[0-9]\+' fuwu_service/server.py | grep -o '[0-9]\+')
if [ "$DEFAULT_PORT" = "8088" ]; then
    echo -e "${GREEN}✓${NC} server.py 默认端口: 8088"
else
    echo -e "${RED}✗${NC} server.py 默认端口: $DEFAULT_PORT (应为 8088)"
fi

# 3. 检查运行中的进程
echo
echo "🔄 检查运行中的进程..."
FUWU_PIDS=$(pgrep -f fuwu-server || true)
if [ -z "$FUWU_PIDS" ]; then
    echo -e "${YELLOW}⚠${NC}  没有运行中的 fuwu-server 进程"
else
    echo -e "${GREEN}✓${NC} 找到运行中的进程:"
    ps aux | grep fuwu-server | grep -v grep | while read line; do
        echo "   $line"
    done
fi

# 4. 检查端口监听
echo
echo "🔌 检查端口监听..."

check_port() {
    local port=$1
    local result=$(lsof -i:$port 2>/dev/null | grep LISTEN || true)
    if [ -n "$result" ]; then
        echo -e "${GREEN}✓${NC} 端口 $port 有服务监听:"
        echo "$result" | while read line; do
            echo "   $line"
        done
        return 0
    else
        echo -e "${YELLOW}⚠${NC}  端口 $port 无服务监听"
        return 1
    fi
}

check_port 8000
check_port 8088

# 5. 测试 API 端点
echo
echo "🌐 测试 API 端点..."

test_endpoint() {
    local port=$1
    local url="http://127.0.0.1:$port/health"

    if curl -s --max-time 2 "$url" > /dev/null 2>&1; then
        local response=$(curl -s "$url")
        echo -e "${GREEN}✓${NC} 端口 $port 响应正常: $response"
        return 0
    else
        echo -e "${YELLOW}⚠${NC}  端口 $port 无响应或超时"
        return 1
    fi
}

test_endpoint 8000
test_endpoint 8088

# 6. 检查环境变量
echo
echo "🔧 检查环境变量..."
if [ -n "$FUWU_PORT" ]; then
    echo -e "${GREEN}✓${NC} FUWU_PORT 已设置: $FUWU_PORT"
else
    echo -e "${YELLOW}⚠${NC}  FUWU_PORT 未设置（将使用代码默认值）"
fi

# 7. 检查最近的日志
echo
echo "📋 最近的日志（最后 10 行）..."
if [ -f "/www/wwwroot/fuwu/logs/error.log" ]; then
    tail -10 /www/wwwroot/fuwu/logs/error.log
elif command -v journalctl &> /dev/null; then
    journalctl -u fuwu -n 10 --no-pager || echo "无法读取 systemd 日志"
else
    echo "找不到日志文件"
fi

# 8. 生成建议
echo
echo "================================"
echo "📊 诊断总结"
echo "================================"
echo

# 判断状态
if lsof -i:8088 2>/dev/null | grep -q LISTEN; then
    echo -e "${GREEN}✅ 服务已在端口 8088 运行！${NC}"
    echo
    echo "✓ 配置正确，无需操作"
    echo "✓ 访问 URL: http://你的域名或IP:8088"

elif lsof -i:8000 2>/dev/null | grep -q LISTEN; then
    echo -e "${YELLOW}⚠️  服务仍在旧端口 8000 运行${NC}"
    echo
    echo "需要更新宝塔配置："
    echo "1. 在宝塔面板停止 fuwu 项目"
    echo "2. 修改启动命令，添加: FUWU_PORT=8088"
    echo "3. 保存并重新启动"
    echo
    echo "详细步骤见: BAOTA_UPDATE_GUIDE.md"

else
    echo -e "${RED}❌ 服务未运行${NC}"
    echo
    echo "需要操作："
    echo "1. 确保代码是最新的: cd /www/wwwroot/fuwu && git pull"
    echo "2. 在宝塔面板启动 fuwu 项目"
    echo "3. 检查启动日志是否有错误"
fi

echo
echo "🔗 相关文档："
echo "   - BAOTA_UPDATE_GUIDE.md (宝塔配置指南)"
echo "   - PRODUCTION_FIX.md (生产环境修复)"
echo
