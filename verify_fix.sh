#!/bin/bash
# 验证修复是否成功

set -e

echo "🔍 验证修复..."
echo

# 1. 检查 server.py 的关键改动
echo "✓ 检查 server.py 日志系统"
grep -q "setup_logging" fuwu_service/server.py && echo "  ✓ setup_logging() 存在"
grep -q "8088" fuwu_service/server.py && echo "  ✓ 端口改为 8088"

# 2. 检查 api.py 没有被破坏
echo
echo "✓ 检查 api.py 完整性"
lines=$(wc -l < fuwu_service/api.py)
if [ "$lines" -eq 310 ]; then
    echo "  ✓ api.py 行数正确 (310 行)"
else
    echo "  ✗ 警告：api.py 行数异常 ($lines 行，应为 310)"
    exit 1
fi

# 3. 检查认证相关函数存在
echo
echo "✓ 检查认证功能"
grep -q "def _user" fuwu_service/api.py && echo "  ✓ _user() 函数存在"
grep -q "from . import auth" fuwu_service/api.py && echo "  ✓ auth 模块导入正常"

# 4. 检查临时文件已清理
echo
echo "✓ 检查临时文件"
if [ ! -d "fuwu/" ] && [ ! -f "main.py" ]; then
    echo "  ✓ 临时文件已清理"
else
    echo "  ✗ 警告：仍有临时文件"
fi

echo
echo "✅ 所有检查通过！"
echo
echo "📋 下一步："
echo "  1. 在服务器上执行: git pull"
echo "  2. 重启服务: sudo systemctl restart fuwu"
echo "  3. 验证端口: curl http://127.0.0.1:8088/health"
echo "  4. 检查日志带时间戳: journalctl -u fuwu -n 20"
