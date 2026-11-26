#!/usr/bin/env python3
"""SMTP 配置自动检测工具

根据邮箱地址自动推荐 SMTP 配置参数。

用法:
    ./scripts/detect_smtp_config.py user@example.com
    python scripts/detect_smtp_config.py --interactive
"""

import argparse
import re
import sys
from typing import Dict, Optional, Tuple


class SMTPConfig:
    """SMTP 配置"""

    def __init__(
        self,
        host: str,
        port: int,
        use_tls: bool,
        use_ssl: bool,
        note: str = "",
        auth_note: str = "",
    ):
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self.use_ssl = use_ssl
        self.note = note
        self.auth_note = auth_note

    def to_env_format(self, email: str, username: Optional[str] = None) -> str:
        """转换为 .env 格式"""
        if username is None:
            username = email

        lines = [
            "# SMTP 配置",
            f"EMAIL_HOST={self.host}",
            f"EMAIL_PORT={self.port}",
            "EMAIL_TIMEOUT=10",
            f"EMAIL_HOST_USER={username}",
            "EMAIL_HOST_PASSWORD=your-password-here",
            f"EMAIL_USE_TLS={'true' if self.use_tls else 'false'}",
            f"EMAIL_USE_SSL={'true' if self.use_ssl else 'false'}",
            f"DEFAULT_FROM_EMAIL={email}",
        ]

        if self.note:
            lines.insert(1, f"# {self.note}")

        if self.auth_note:
            lines.append(f"\n# 认证说明: {self.auth_note}")

        return "\n".join(lines)


# 常见邮箱服务商 SMTP 配置数据库
SMTP_PROVIDERS: Dict[str, SMTPConfig] = {
    # 国内服务商
    "qq.com": SMTPConfig(
        host="smtp.qq.com",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="QQ 邮箱",
        auth_note="需要在 QQ 邮箱设置中开启 SMTP 服务并生成授权码，密码使用授权码而非 QQ 密码",
    ),
    "vip.qq.com": SMTPConfig(
        host="smtp.vip.qq.com",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="QQ VIP 邮箱",
        auth_note="需要在 QQ 邮箱设置中开启 SMTP 服务并生成授权码",
    ),
    "foxmail.com": SMTPConfig(
        host="smtp.qq.com",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="Foxmail (使用 QQ 邮箱 SMTP)",
        auth_note="需要在 QQ 邮箱设置中开启 SMTP 服务并生成授权码",
    ),
    "163.com": SMTPConfig(
        host="smtp.163.com",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="网易 163 邮箱",
        auth_note="需要在邮箱设置中开启 SMTP 服务并生成授权码",
    ),
    "126.com": SMTPConfig(
        host="smtp.126.com",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="网易 126 邮箱",
        auth_note="需要在邮箱设置中开启 SMTP 服务并生成授权码",
    ),
    "yeah.net": SMTPConfig(
        host="smtp.yeah.net",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="网易 yeah 邮箱",
        auth_note="需要在邮箱设置中开启 SMTP 服务并生成授权码",
    ),
    "sina.com": SMTPConfig(
        host="smtp.sina.com",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="新浪邮箱",
        auth_note="需要在邮箱设置中开启 SMTP 服务",
    ),
    "sina.cn": SMTPConfig(
        host="smtp.sina.cn",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="新浪邮箱",
        auth_note="需要在邮箱设置中开启 SMTP 服务",
    ),
    "sohu.com": SMTPConfig(
        host="smtp.sohu.com",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="搜狐邮箱",
    ),
    "aliyun.com": SMTPConfig(
        host="smtp.aliyun.com",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="阿里云邮箱",
        auth_note="需要在邮箱设置中开启 SMTP 服务并生成授权码",
    ),
    # 阿里云邮件推送专用
    "dm.aliyun.com": SMTPConfig(
        host="smtpdm.aliyun.com",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="阿里云邮件推送服务",
        auth_note="用户名格式: your-sender@your-domain.com, 密码为 SMTP 密码",
    ),
    # 国际服务商
    "gmail.com": SMTPConfig(
        host="smtp.gmail.com",
        port=587,
        use_tls=True,
        use_ssl=False,
        note="Gmail",
        auth_note="需要生成应用专用密码 (App Password)，不能使用账户密码",
    ),
    "googlemail.com": SMTPConfig(
        host="smtp.gmail.com",
        port=587,
        use_tls=True,
        use_ssl=False,
        note="Gmail",
        auth_note="需要生成应用专用密码 (App Password)",
    ),
    "outlook.com": SMTPConfig(
        host="smtp-mail.outlook.com",
        port=587,
        use_tls=True,
        use_ssl=False,
        note="Outlook.com",
    ),
    "hotmail.com": SMTPConfig(
        host="smtp-mail.outlook.com",
        port=587,
        use_tls=True,
        use_ssl=False,
        note="Hotmail",
    ),
    "live.com": SMTPConfig(
        host="smtp-mail.outlook.com",
        port=587,
        use_tls=True,
        use_ssl=False,
        note="Live.com",
    ),
    "office365.com": SMTPConfig(
        host="smtp.office365.com",
        port=587,
        use_tls=True,
        use_ssl=False,
        note="Office 365",
    ),
    "yahoo.com": SMTPConfig(
        host="smtp.mail.yahoo.com",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="Yahoo Mail",
        auth_note="需要生成应用专用密码",
    ),
    "aol.com": SMTPConfig(
        host="smtp.aol.com",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="AOL Mail",
    ),
    "icloud.com": SMTPConfig(
        host="smtp.mail.me.com",
        port=587,
        use_tls=True,
        use_ssl=False,
        note="iCloud Mail",
        auth_note="需要生成应用专用密码",
    ),
    "me.com": SMTPConfig(
        host="smtp.mail.me.com",
        port=587,
        use_tls=True,
        use_ssl=False,
        note="iCloud Mail",
        auth_note="需要生成应用专用密码",
    ),
    "mac.com": SMTPConfig(
        host="smtp.mail.me.com",
        port=587,
        use_tls=True,
        use_ssl=False,
        note="iCloud Mail",
        auth_note="需要生成应用专用密码",
    ),
    "zoho.com": SMTPConfig(
        host="smtp.zoho.com",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="Zoho Mail",
    ),
    "protonmail.com": SMTPConfig(
        host="127.0.0.1",
        port=1025,
        use_tls=False,
        use_ssl=False,
        note="ProtonMail",
        auth_note="需要安装 ProtonMail Bridge 在本地运行",
    ),
    "yandex.com": SMTPConfig(
        host="smtp.yandex.com",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="Yandex Mail",
    ),
    "mail.ru": SMTPConfig(
        host="smtp.mail.ru",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="Mail.ru",
    ),
    # 专用邮件服务
    "sendgrid.net": SMTPConfig(
        host="smtp.sendgrid.net",
        port=587,
        use_tls=True,
        use_ssl=False,
        note="SendGrid",
        auth_note="用户名固定为 'apikey'，密码为 API Key",
    ),
    "mailgun.org": SMTPConfig(
        host="smtp.mailgun.org",
        port=587,
        use_tls=True,
        use_ssl=False,
        note="Mailgun",
        auth_note="用户名格式: postmaster@your-domain.com",
    ),
    "sparkpostmail.com": SMTPConfig(
        host="smtp.sparkpostmail.com",
        port=587,
        use_tls=True,
        use_ssl=False,
        note="SparkPost",
        auth_note="用户名固定为 'SMTP_Injection'",
    ),
}

# 企业邮箱域名模式
ENTERPRISE_PATTERNS = {
    r"exmail\.qq\.com$": SMTPConfig(
        host="smtp.exmail.qq.com",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="腾讯企业邮箱",
        auth_note="使用完整邮箱地址作为用户名",
    ),
    r"\.mail\.aliyun\.com$": SMTPConfig(
        host="smtp.mxhichina.com",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="阿里企业邮箱",
        auth_note="使用完整邮箱地址作为用户名",
    ),
    r"\.coremail\.cn$": SMTPConfig(
        host="smtp.{domain}",
        port=465,
        use_tls=False,
        use_ssl=True,
        note="Coremail 企业邮箱",
        auth_note="SMTP 服务器通常为 smtp.您的域名",
    ),
}


def extract_domain(email: str) -> Optional[str]:
    """从邮箱地址提取域名"""
    match = re.match(r"^[^@]+@([^@]+)$", email.strip().lower())
    return match.group(1) if match else None


def detect_smtp_config(email: str) -> Optional[Tuple[SMTPConfig, bool]]:
    """
    检测邮箱的 SMTP 配置

    返回: (SMTPConfig, is_enterprise)
        - SMTPConfig: SMTP 配置对象
        - is_enterprise: 是否为企业邮箱（需要替换域名）
    """
    domain = extract_domain(email)
    if not domain:
        return None

    # 1. 查找精确匹配的服务商
    if domain in SMTP_PROVIDERS:
        return SMTP_PROVIDERS[domain], False

    # 2. 检查企业邮箱模式
    for pattern, config in ENTERPRISE_PATTERNS.items():
        if re.search(pattern, domain):
            # 企业邮箱需要替换 {domain}
            if "{domain}" in config.host:
                config = SMTPConfig(
                    host=config.host.replace("{domain}", domain),
                    port=config.port,
                    use_tls=config.use_tls,
                    use_ssl=config.use_ssl,
                    note=config.note,
                    auth_note=config.auth_note,
                )
            return config, True

    # 3. 尝试推测通用配置
    # 大多数邮件服务器遵循 smtp.domain.com 的命名规则
    return (
        SMTPConfig(
            host=f"smtp.{domain}",
            port=587,
            use_tls=True,
            use_ssl=False,
            note=f"推测配置 (未验证)",
            auth_note="请咨询邮件服务商确认 SMTP 服务器地址、端口和加密方式",
        ),
        False,
    )


def print_config(email: str, config: SMTPConfig, is_enterprise: bool) -> None:
    """打印 SMTP 配置"""
    print("\n" + "=" * 70)
    print(f"📧 邮箱: {email}")
    print(f"🏷️  服务商: {config.note}")
    print("=" * 70)
    print(f"\n📌 SMTP 配置")
    print(f"   服务器: {config.host}")
    print(f"   端口: {config.port}")
    print(f"   加密方式: {'STARTTLS' if config.use_tls else 'SSL/TLS' if config.use_ssl else '无'}")
    print(f"   用户名: {email}")

    if config.auth_note:
        print(f"\n⚠️  认证说明")
        print(f"   {config.auth_note}")

    print(f"\n📝 .env 配置格式")
    print("-" * 70)
    print(config.to_env_format(email))
    print("-" * 70)

    if "推测" in config.note:
        print("\n⚠️  这是推测的配置，可能不准确！")
        print("   请访问邮件服务商官网查询正确的 SMTP 配置。")
    elif is_enterprise:
        print("\n💼 检测到企业邮箱")
        print("   请联系您的企业 IT 管理员确认 SMTP 配置。")

    print("\n📚 配置测试")
    print("   将以上配置添加到 .env 文件后运行:")
    print("   uv run --env-file .env scripts/test_smtp.py")
    print()


def interactive_mode() -> None:
    """交互模式"""
    print("=" * 70)
    print("🔍 SMTP 配置自动检测工具")
    print("=" * 70)
    print("\n请输入邮箱地址，程序将自动推荐 SMTP 配置。")
    print("输入 'quit' 或 'exit' 退出。\n")

    while True:
        try:
            email = input("📧 邮箱地址: ").strip()
            if email.lower() in ("quit", "exit", "q"):
                print("\n👋 再见！")
                break

            if not email:
                continue

            if "@" not in email:
                print("❌ 无效的邮箱地址，请重新输入。\n")
                continue

            result = detect_smtp_config(email)
            if result:
                config, is_enterprise = result
                print_config(email, config, is_enterprise)
            else:
                print("❌ 无效的邮箱地址格式。\n")

        except KeyboardInterrupt:
            print("\n\n👋 再见！")
            break
        except EOFError:
            print("\n\n👋 再见！")
            break


def main() -> None:
    parser = argparse.ArgumentParser(
        description="根据邮箱地址自动检测 SMTP 配置",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s user@gmail.com                    # 检测单个邮箱
  %(prog)s user@qq.com user@163.com          # 批量检测
  %(prog)s --interactive                     # 交互模式
  %(prog)s -i                                # 交互模式（简写）

支持的邮箱服务商:
  - QQ 邮箱 / Foxmail / 腾讯企业邮
  - 网易邮箱 (163/126/yeah)
  - 阿里云邮箱 / 阿里云邮件推送
  - 新浪邮箱 / 搜狐邮箱
  - Gmail / Outlook / Yahoo / iCloud
  - SendGrid / Mailgun / SparkPost
  - 以及更多...
        """,
    )
    parser.add_argument(
        "emails",
        nargs="*",
        help="邮箱地址（一个或多个）",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="启动交互模式",
    )
    parser.add_argument(
        "--list-providers",
        action="store_true",
        help="列出所有支持的邮箱服务商",
    )

    args = parser.parse_args()

    if args.list_providers:
        print("=" * 70)
        print("📋 支持的邮箱服务商")
        print("=" * 70)
        for domain, config in sorted(SMTP_PROVIDERS.items()):
            print(f"\n• {domain}")
            print(f"  服务商: {config.note}")
            print(f"  SMTP: {config.host}:{config.port}")
        print()
        return

    if args.interactive:
        interactive_mode()
        return

    if not args.emails:
        parser.print_help()
        sys.exit(1)

    for email in args.emails:
        result = detect_smtp_config(email)
        if result:
            config, is_enterprise = result
            print_config(email, config, is_enterprise)
        else:
            print(f"\n❌ 无效的邮箱地址: {email}\n")


if __name__ == "__main__":
    main()
