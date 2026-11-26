"""SMTP 集成测试

这些测试需要真实的 SMTP 配置才能运行。
默认情况下使用 Django locmem backend 进行单元测试。

要运行真实 SMTP 测试，配置环境变量后执行：
    FUWU_TEST_REAL_SMTP=1 pytest test_smtp_integration.py -v
"""
import os
import sys
import unittest

# 配置 Django 设置
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from django.conf import settings
from django.test import override_settings


class SMTPConfigurationTest(unittest.TestCase):
    """SMTP 配置验证测试"""

    def test_smtp_settings_are_defined(self) -> None:
        """验证 SMTP 设置存在"""
        self.assertIsNotNone(settings.EMAIL_BACKEND)
        self.assertIsInstance(settings.EMAIL_HOST, str)
        self.assertIsInstance(settings.EMAIL_PORT, int)
        self.assertIsInstance(settings.EMAIL_HOST_USER, str)
        self.assertIsInstance(settings.EMAIL_HOST_PASSWORD, str)
        self.assertIsInstance(settings.EMAIL_USE_TLS, bool)
        self.assertIsInstance(settings.EMAIL_USE_SSL, bool)
        self.assertIsInstance(settings.DEFAULT_FROM_EMAIL, str)
        self.assertIsInstance(settings.EMAIL_TIMEOUT, int)

    def test_tls_and_ssl_are_mutually_exclusive(self) -> None:
        """验证 TLS 和 SSL 不能同时启用"""
        # 这是逻辑警告，不是硬错误，但配置应该只选一个
        if settings.EMAIL_HOST:
            # 只在配置了 SMTP 时检查
            if settings.EMAIL_USE_TLS and settings.EMAIL_USE_SSL:
                self.fail("EMAIL_USE_TLS 和 EMAIL_USE_SSL 不应同时为 true")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST="smtp.test.example",
        DEFAULT_FROM_EMAIL="test@example.com",
    )
    def test_email_sending_with_locmem_backend(self) -> None:
        """使用内存后端测试邮件发送"""
        from django.core.mail import send_mail
        from django.core import mail

        # 清空邮箱
        if hasattr(mail, 'outbox'):
            mail.outbox.clear()

        # 发送测试邮件
        result = send_mail(
            subject="Test Subject",
            message="Test message body",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=["recipient@example.com"],
            fail_silently=False,
        )

        # 验证发送成功
        self.assertEqual(result, 1)

        # 如果有 outbox，验证邮件内容
        if hasattr(mail, 'outbox'):
            self.assertEqual(len(mail.outbox), 1)
            self.assertEqual(mail.outbox[0].subject, "Test Subject")
            self.assertEqual(mail.outbox[0].body, "Test message body")
            self.assertEqual(mail.outbox[0].from_email, "test@example.com")
            self.assertEqual(mail.outbox[0].to, ["recipient@example.com"])


@unittest.skipUnless(
    os.environ.get("FUWU_TEST_REAL_SMTP") == "1",
    "需要设置 FUWU_TEST_REAL_SMTP=1 和真实 SMTP 配置",
)
class RealSMTPIntegrationTest(unittest.TestCase):
    """真实 SMTP 服务器集成测试

    要运行这些测试：
    1. 在 .env 中配置真实 SMTP 服务器
    2. 设置测试邮箱环境变量：
       export FUWU_TEST_EMAIL=your-test@example.com
       export FUWU_TEST_REAL_SMTP=1
    3. 运行测试：
       uv run --env-file .env pytest test_smtp_integration.py::RealSMTPIntegrationTest -v
    """

    def setUp(self) -> None:
        self.test_email = os.environ.get("FUWU_TEST_EMAIL")
        if not self.test_email:
            self.skipTest("需要设置 FUWU_TEST_EMAIL 环境变量")

        if not settings.EMAIL_HOST:
            self.skipTest("EMAIL_HOST 未配置")

        if not settings.DEFAULT_FROM_EMAIL:
            self.skipTest("DEFAULT_FROM_EMAIL 未配置")

    def test_smtp_connection_and_send(self) -> None:
        """测试 SMTP 连接和发送"""
        from django.core.mail import send_mail

        print(f"\n发送测试邮件到 {self.test_email}")
        print(f"SMTP: {settings.EMAIL_HOST}:{settings.EMAIL_PORT}")
        print(f"TLS: {settings.EMAIL_USE_TLS}, SSL: {settings.EMAIL_USE_SSL}")

        # 发送真实邮件
        result = send_mail(
            subject="Fuwu SMTP 集成测试",
            message=(
                "这是一封自动化测试邮件。\n\n"
                f"SMTP 服务器: {settings.EMAIL_HOST}:{settings.EMAIL_PORT}\n"
                f"发件人: {settings.DEFAULT_FROM_EMAIL}\n\n"
                "如果收到此邮件，说明 SMTP 配置正确。"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[self.test_email],
            fail_silently=False,
        )

        # send_mail 返回成功发送的邮件数量
        self.assertEqual(result, 1, "邮件发送失败")
        print(f"✅ 邮件已发送到 {self.test_email}")
        print("请检查收件箱（可能在垃圾邮件中）")

    def test_password_reset_code_email_format(self) -> None:
        """测试密码重置验证码邮件格式"""
        from django.core.mail import send_mail

        code = "123456"
        result = send_mail(
            subject="fuwu 密码重置验证码",
            message=f"你的验证码是 {code}，10 分钟内有效。若不是你本人操作，请忽略此邮件。",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[self.test_email],
            fail_silently=False,
        )

        self.assertEqual(result, 1)
        print(f"✅ 密码重置邮件已发送到 {self.test_email}")


if __name__ == "__main__":
    unittest.main()
