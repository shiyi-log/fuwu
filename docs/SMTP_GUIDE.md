# SMTP 配置与测试指南

本文档说明如何配置和验证 Fuwu 的邮件发送功能，用于邮箱验证码找回密码。

## 功能概述

Fuwu 支持通过邮箱验证码找回密码：
- 用户可使用邮箱地址作为用户名登录
- 忘记密码时，向已登记邮箱发送 6 位数字验证码
- 验证码有效期 10 分钟，每个邮箱 60 秒冷却、每小时最多 3 次
- 验证码使用 HMAC 保护，最多 5 次错误尝试后作废
- 成功重置后撤销该账号所有会话

## 必需配置

### 1. DJANGO_SECRET_KEY

**重要**：生产环境必须配置强随机密钥，至少 32 字符。开发默认密钥下验证码功能拒绝运行。

生成随机密钥：
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 2. SMTP 服务器配置

在 `.env` 文件中配置以下环境变量：

```dotenv
# Django 密钥（必需，至少 32 字符）
DJANGO_SECRET_KEY=your-strong-random-secret-key-at-least-32-chars

# SMTP 服务器配置（必需）
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=service@example.com
EMAIL_HOST_PASSWORD=your-smtp-password
EMAIL_TIMEOUT=10

# TLS/SSL 配置（二选一）
EMAIL_USE_TLS=true    # STARTTLS，端口通常为 587
EMAIL_USE_SSL=false   # 或使用隐式 SSL，端口通常为 465

# 发件人地址（必需）
DEFAULT_FROM_EMAIL=Fuwu <service@example.com>
```

### 3. HTTPS 要求

邮件验证码接口仅接受：
- HTTPS 请求
- 或 loopback (127.0.0.1) 初始化请求

生产部署需配置 TLS 反向代理，并正确设置：
```dotenv
FUWU_TRUST_X_FORWARDED_PROTO=1
FUWU_TRUSTED_PROXY_CIDRS=172.18.0.0/16
```

## 常见 SMTP 服务商配置示例

### Gmail

```dotenv
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
EMAIL_USE_TLS=true
EMAIL_USE_SSL=false
DEFAULT_FROM_EMAIL=Fuwu <your-email@gmail.com>
```

**注意**：Gmail 需要使用应用专用密码，不是账号密码。在 Google 账号设置中启用两步验证后生成。

### 阿里云邮件推送

```dotenv
EMAIL_HOST=smtpdm.aliyun.com
EMAIL_PORT=465
EMAIL_HOST_USER=your-sender@your-domain.com
EMAIL_HOST_PASSWORD=your-smtp-password
EMAIL_USE_TLS=false
EMAIL_USE_SSL=true
DEFAULT_FROM_EMAIL=Fuwu <your-sender@your-domain.com>
```

### 腾讯企业邮箱

```dotenv
EMAIL_HOST=smtp.exmail.qq.com
EMAIL_PORT=465
EMAIL_HOST_USER=service@your-domain.com
EMAIL_HOST_PASSWORD=your-email-password
EMAIL_USE_TLS=false
EMAIL_USE_SSL=true
DEFAULT_FROM_EMAIL=Fuwu <service@your-domain.com>
```

### SendGrid

```dotenv
EMAIL_HOST=smtp.sendgrid.net
EMAIL_PORT=587
EMAIL_HOST_USER=apikey
EMAIL_HOST_PASSWORD=your-sendgrid-api-key
EMAIL_USE_TLS=true
EMAIL_USE_SSL=false
DEFAULT_FROM_EMAIL=Fuwu <your-verified-sender@your-domain.com>
```

## 配置验证步骤

### 1. 本地开发环境测试

```bash
# 1. 配置 .env
cp .env.example .env
# 编辑 .env，填写上述 SMTP 配置

# 2. 启动服务
uv run --env-file .env --frozen fuwu-server

# 3. 在另一终端运行测试
uv run --frozen pytest tests/test_auth.py::TestPasswordResetFlow::test_email_password_reset_on_django_and_lightweight_adapters -v
```

### 2. 使用自动检测工具

如果你还不知道 SMTP 配置，可以使用自动检测工具：

```bash
# 检测单个邮箱
./scripts/detect_smtp_config.py user@gmail.com

# 批量检测多个邮箱
./scripts/detect_smtp_config.py user@qq.com admin@163.com

# 交互模式
./scripts/detect_smtp_config.py --interactive

# 列出所有支持的服务商
./scripts/detect_smtp_config.py --list-providers
```

工具支持 30+ 邮箱服务商：
- 国内：QQ / Foxmail / 163 / 126 / 阿里云 / 新浪 / 搜狐
- 国际：Gmail / Outlook / Yahoo / iCloud / AOL
- 企业邮箱：腾讯企业邮 / 阿里企业邮 / Coremail
- 邮件服务：SendGrid / Mailgun / SparkPost

工具会自动生成可直接使用的 `.env` 配置格式，并提示是否需要应用专用密码或授权码。

### 3. 使用测试工具验证

创建测试脚本 `scripts/test_smtp.py`：

```python
#!/usr/bin/env python3
"""SMTP 配置验证工具"""
import os
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from django.core.mail import send_mail
from django.conf import settings

def test_smtp_connection():
    """测试 SMTP 配置"""
    print("=" * 60)
    print("SMTP 配置测试")
    print("=" * 60)
    
    # 检查必需配置
    if not settings.EMAIL_HOST:
        print("❌ EMAIL_HOST 未配置")
        return False
    
    if not settings.DEFAULT_FROM_EMAIL:
        print("❌ DEFAULT_FROM_EMAIL 未配置")
        return False
    
    if settings.SECRET_KEY == "fuwu-local-development-key":
        print("⚠️  警告：使用开发默认密钥，生产环境必须更换")
    
    print(f"📧 SMTP 服务器: {settings.EMAIL_HOST}:{settings.EMAIL_PORT}")
    print(f"👤 用户名: {settings.EMAIL_HOST_USER}")
    print(f"🔒 TLS: {settings.EMAIL_USE_TLS}, SSL: {settings.EMAIL_USE_SSL}")
    print(f"📮 发件人: {settings.DEFAULT_FROM_EMAIL}")
    print(f"⏱️  超时: {settings.EMAIL_TIMEOUT}s")
    print()
    
    # 获取测试邮箱
    test_email = input("请输入接收测试邮件的邮箱地址: ").strip()
    if not test_email or "@" not in test_email:
        print("❌ 无效的邮箱地址")
        return False
    
    print(f"\n📤 正在发送测试邮件到 {test_email}...")
    
    try:
        send_mail(
            subject="Fuwu SMTP 配置测试",
            message="这是一封测试邮件。如果你收到此邮件，说明 SMTP 配置正确。\n\n这是由 Fuwu 系统自动发送的测试邮件。",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[test_email],
            fail_silently=False,
        )
        print("✅ 邮件发送成功！")
        print(f"\n请检查 {test_email} 的收件箱（可能在垃圾邮件中）")
        return True
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")
        print("\n常见问题排查：")
        print("1. 检查 EMAIL_HOST 和 EMAIL_PORT 是否正确")
        print("2. 检查 EMAIL_HOST_USER 和 EMAIL_HOST_PASSWORD")
        print("3. 检查 EMAIL_USE_TLS 和 EMAIL_USE_SSL 配置")
        print("4. 确认 SMTP 服务器允许该账号发送邮件")
        print("5. 检查防火墙是否阻止了 SMTP 端口")
        return False

if __name__ == "__main__":
    success = test_smtp_connection()
    sys.exit(0 if success else 1)
```

运行测试：
```bash
chmod +x scripts/test_smtp.py
uv run --env-file .env scripts/test_smtp.py
```

### 3. 完整流程端到端测试

```bash
# 1. 创建测试账号（使用真实邮箱）
uv run --frozen python -m fuwu_service.accounts bootstrap --database data/fuwu.sqlite3
# 输入邮箱地址作为用户名，例如：test@example.com

# 2. 启动服务和前端
uv run --env-file .env --frozen fuwu-server &
cd frontend && pnpm dev:antd &

# 3. 浏览器测试
# - 打开 http://127.0.0.1:5666
# - 点击"忘记密码"
# - 输入注册时使用的邮箱
# - 检查邮箱收到验证码
# - 输入验证码和新密码
# - 验证可以用新密码登录
```

## Docker Compose 部署

### 1. 配置 .env

```bash
cp .env.example .env
# 编辑 .env，配置 SMTP 和密钥
```

### 2. 重启服务加载配置

```bash
docker compose down
docker compose --env-file .env up -d
```

### 3. 验证配置

```bash
# 在容器内运行测试
docker compose exec api bash -c "
export EMAIL_HOST='smtp.example.com'
export EMAIL_PORT=587
export EMAIL_HOST_USER='service@example.com'
export EMAIL_HOST_PASSWORD='your-password'
export EMAIL_USE_TLS=true
export EMAIL_USE_SSL=false
export DEFAULT_FROM_EMAIL='Fuwu <service@example.com>'
uv run --frozen --no-dev python -c '
from django.core.mail import send_mail
send_mail(
    \"Test\",
    \"Test message\",
    \"Fuwu <service@example.com>\",
    [\"test@example.com\"],
)
print(\"✅ Email sent successfully\")
'
"
```

## 测试用例覆盖

现有测试已覆盖：
- `tests/test_auth.py::TestPasswordResetFlow` - 完整邮件找回流程
- `tests/test_accounts.py` - 验证码存储、过期、限速

运行完整测试：
```bash
uv run --frozen pytest tests/test_auth.py tests/test_accounts.py -v -k "email"
```

## 常见问题排查

### 邮件未收到

1. **检查垃圾邮件箱**
2. **验证 SMTP 凭据**：使用 `test_smtp.py` 测试
3. **查看服务日志**：
   ```bash
   # 轻量服务
   grep "password reset email" logs/*.log
   
   # Docker
   docker compose logs api | grep "password reset"
   ```
4. **确认邮箱已登记**：API 对未知邮箱不发送，但响应相同

### 发送失败

1. **SMTP 认证失败**：检查用户名和密码
2. **端口被阻止**：确认防火墙允许 587 或 465
3. **TLS/SSL 配置错误**：STARTTLS 用 587 + TLS，隐式 SSL 用 465 + SSL
4. **发件人未验证**：某些服务商要求验证发件人域名

### 验证码无效

1. **超过 10 分钟**：验证码已过期
2. **错误 5 次**：验证码已作废，需重新请求
3. **密钥更换**：轮换 DJANGO_SECRET_KEY 会使未用验证码失效

## 生产部署检查清单

- [ ] 配置强随机 `DJANGO_SECRET_KEY`（至少 32 字符）
- [ ] 配置真实 SMTP 服务器和凭据
- [ ] 验证发送地址域名（如需要）
- [ ] 配置 TLS 反向代理
- [ ] 设置 `FUWU_TRUST_X_FORWARDED_PROTO=1`
- [ ] 配置 `FUWU_TRUSTED_PROXY_CIDRS`
- [ ] 测试邮件发送到真实邮箱
- [ ] 完整流程端到端测试
- [ ] 验证邮件内容和格式
- [ ] 检查服务日志无错误

## 安全注意事项

1. **不要提交 SMTP 密码**：使用环境变量，不写入代码或仓库
2. **保护 SECRET_KEY**：密钥泄露会使验证码安全性失效
3. **限制重试次数**：已实现 5 次错误限制和时间冷却
4. **HTTPS 要求**：生产环境必须使用 HTTPS
5. **日志安全**：验证码不进入日志，密码不回显

## 下一步

配置完成后：
1. 更新 PROJECT_LEDGER.md，记录 SMTP 验证完成
2. 在生产环境执行完整验收
3. 更新 VERSION_HISTORY.md，移除"SMTP 待验收"标记
