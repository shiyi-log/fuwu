#!/usr/bin/env python3
"""SMTP 配置验证工具

用法:
    python scripts/test_smtp.py
    uv run scripts/test_smtp.py
    uv run --env-file .env scripts/test_smtp.py
"""
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
        print("   生成密钥: python3 -c \"import secrets; print(secrets.token_urlsafe(48))\"")
        print()

    print(f"📧 SMTP 服务器: {settings.EMAIL_HOST}:{settings.EMAIL_PORT}")
    print(f"👤 用户名: {settings.EMAIL_HOST_USER or '(未配置)'}")
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
            message=(
                "这是一封测试邮件。如果你收到此邮件，说明 SMTP 配置正确。\n\n"
                "SMTP 服务器: {host}:{port}\n"
                "发件人: {sender}\n\n"
                "这是由 Fuwu 系统自动发送的测试邮件。"
            ).format(
                host=settings.EMAIL_HOST,
                port=settings.EMAIL_PORT,
                sender=settings.DEFAULT_FROM_EMAIL,
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[test_email],
            fail_silently=False,
        )
        print("✅ 邮件发送成功！")
        print(f"\n请检查 {test_email} 的收件箱（可能在垃圾邮件中）")
        print("\n如果未收到邮件，请检查：")
        print("1. 垃圾邮件/垃圾箱")
        print("2. 邮件服务商的发送日志")
        print("3. 防火墙和 SMTP 端口")
        return True
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")
        print("\n常见问题排查：")
        print("1. 检查 EMAIL_HOST 和 EMAIL_PORT 是否正确")
        print("2. 检查 EMAIL_HOST_USER 和 EMAIL_HOST_PASSWORD")
        print("3. 检查 EMAIL_USE_TLS 和 EMAIL_USE_SSL 配置")
        print("   - STARTTLS: 端口 587, EMAIL_USE_TLS=true")
        print("   - 隐式 SSL: 端口 465, EMAIL_USE_SSL=true")
        print("4. 确认 SMTP 服务器允许该账号发送邮件")
        print("5. 检查防火墙是否阻止了 SMTP 端口")
        print("6. Gmail 需要使用应用专用密码，不是账号密码")
        return False


if __name__ == "__main__":
    success = test_smtp_connection()
    sys.exit(0 if success else 1)
