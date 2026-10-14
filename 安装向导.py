#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GoldAlert 首次安装向导。

问几个问题，把 config.json 和 .env 写好，让程序在新电脑上直接能跑。
只依赖标准库，任何 Python 3.10+ 都能运行它。

用法：
    python 安装向导.py            # 交互式
    python 安装向导.py --help     # 看可选参数
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
TEMPLATE_PATH = ROOT / "config.example.json"
ENV_PATH = ROOT / ".env"

BAR = "=" * 62

# 常见邮箱服务商的 SMTP 服务器；value = (服务器, 端口, 是否用 STARTTLS)
SMTP_BY_DOMAIN = {
    "qq.com": ("smtp.qq.com", 465, False),
    "vip.qq.com": ("smtp.qq.com", 465, False),
    "foxmail.com": ("smtp.qq.com", 465, False),
    "163.com": ("smtp.163.com", 465, False),
    "126.com": ("smtp.126.com", 465, False),
    "yeah.net": ("smtp.yeah.net", 465, False),
    "sina.com": ("smtp.sina.com", 465, False),
    "sohu.com": ("smtp.sohu.com", 465, False),
    "aliyun.com": ("smtp.aliyun.com", 465, False),
    "gmail.com": ("smtp.gmail.com", 465, False),
    "outlook.com": ("smtp.office365.com", 587, True),
    "hotmail.com": ("smtp.office365.com", 587, True),
    "live.com": ("smtp.office365.com", 587, True),
}

ENV_TEMPLATE = """# 邮件授权码（不是邮箱登录密码！）
# QQ 邮箱：网页版邮箱 -> 设置 -> 账户 -> 开启 SMTP 服务 -> 生成授权码
GOLDALERT_SMTP_PASSWORD=

# DeepSeek API Key（https://platform.deepseek.com -> API keys）
GOLDALERT_DEEPSEEK_API_KEY=
"""


# --------------------------------------------------------------------- 输出
def hr(char: str = "-") -> None:
    print(char * 62)


def banner() -> None:
    print(BAR)
    print("   GoldAlert 现货黄金监控 · 首次安装向导")
    print(BAR)
    print()
    print("接下来会问你几个问题，回答完配置就写好了。")
    print("填错也不要紧，重新运行本向导即可。中途按 Ctrl+C 可以退出。")
    print()


# --------------------------------------------------------------------- 提问
def _read(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        print()
        print("没有读到输入，安装中断。请重新运行本向导。")
        raise SystemExit(1)


def ask(question: str, default: str = "", note: str = "") -> str:
    if note:
        for line in note.splitlines():
            print("  " + line)
    tail = f" [{default}]" if default else ""
    while True:
        value = _read(f"{question}{tail}: ").strip().strip('"').strip("'").strip()
        if value:
            return value
        if default:
            return default
        print("  这一项不能空着，再填一次。")


def ask_secret(question: str, note: str = "") -> str:
    if note:
        for line in note.splitlines():
            print("  " + line)
    while True:
        try:
            if sys.stdin.isatty():
                import getpass

                value = getpass.getpass(f"{question}: ")
            else:
                value = _read(f"{question}: ")
        except Exception:
            value = _read(f"{question}: ")
        value = value.strip().strip('"').strip("'").strip()
        if value:
            return value
        print("  这一项不能空着，再填一次。")


def yes_no(question: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        value = _read(f"{question} [{hint}]: ").strip().lower()
        if not value:
            return default
        if value in ("y", "yes", "是", "1"):
            return True
        if value in ("n", "no", "否", "0"):
            return False
        print("  请回答 y 或 n。")


def looks_like_email(value: str) -> bool:
    head, _, tail = value.partition("@")
    return bool(head) and "." in tail and " " not in value


# --------------------------------------------------------------------- 步骤
def step_email(args) -> str:
    print("[1/5] 用哪个邮箱发送预警邮件")
    print("  这个邮箱负责'发信'，需要开启 SMTP 服务并拿到授权码。")
    while True:
        value = args.email or ask("发件邮箱")
        if looks_like_email(value):
            return value
        print("  这不像一个邮箱地址，检查一下有没有 @ 和 . 。")
        args.email = ""


def step_smtp(args, sender: str):
    domain = sender.rpartition("@")[2].lower()
    if domain in SMTP_BY_DOMAIN:
        host, port, starttls = SMTP_BY_DOMAIN[domain]
        print(f"  识别到 {domain}，自动使用 {host}:{port}")
        return host, port, starttls
    print(f"  没见过的邮箱域名 {domain}，请手动填 SMTP 服务器。")
    host = args.smtp_host or ask("SMTP 服务器", default="smtp." + domain)
    raw_port = args.smtp_port or ask("端口（465 为 SSL，587 为 STARTTLS）", default="465")
    try:
        port = int(raw_port)
    except ValueError:
        port = 465
    return host, port, port == 587


def step_authcode(args, sender: str) -> str:
    print()
    print("[2/5] 邮箱授权码")
    print("  不是邮箱登录密码，是一串单独生成的码。")
    print("  QQ 邮箱：网页版邮箱 -> 设置 -> 账户 -> 开启 IMAP/SMTP 服务 -> 生成授权码")
    if args.authcode:
        return args.authcode
    while True:
        value = ask_secret("授权码")
        if " " in value:
            print("  授权码里不应该有空格，再确认一下（通常是 16 位字母数字）。")
            continue
        return value


def step_recipient(args, sender: str) -> str:
    print()
    print("[3/5] 预警短信发到哪个邮箱")
    print("  默认就是上面的发件邮箱（等于自己给自己发）。")
    print("  想收到手机通知的话，填手机号对应的邮箱，例如：")
    print("    139 邮箱：你的手机号@139.com      联通：手机号@wo.cn")
    print("    电信：手机号@189.cn               也可以继续用 QQ 邮箱，在手机上装 QQ 邮箱 App 开推送")
    while True:
        value = args.recipient or ask("收件邮箱", default=sender)
        if looks_like_email(value):
            return value
        print("  这不像一个邮箱地址，再填一次。")
        args.recipient = ""


def step_apikey(args) -> str:
    print()
    print("[4/5] DeepSeek API Key（用于 AI 盘面小结和自动关键价位）")
    print("  到 platform.deepseek.com 注册，在 'API keys' 里创建一个，形如 sk-xxxx")
    print("  不想用 AI 就直接回车跳过，其它功能照常工作。")
    if args.api_key is not None:
        return args.api_key.strip()
    try:
        if sys.stdin.isatty():
            import getpass

            value = getpass.getpass("DeepSeek API Key（可留空）: ")
        else:
            value = _read("DeepSeek API Key（可留空）: ")
    except Exception:
        value = _read("DeepSeek API Key（可留空）: ")
    return value.strip().strip('"').strip("'").strip()


def step_proxy(args):
    print()
    print("[5/5] 代理设置")
    print("  TradingView 的数据源在国内需要科学上网，如果你电脑上装了 Clash 一类的代理，")
    print("  这里填代理地址；没有代理的话程序会自动改用国内数据源（新浪 / 腾讯）。")
    if args.proxy is not None:
        return args.proxy, ("always" if args.proxy else "auto")
    if yes_no("这台电脑有可用代理吗？", default=False):
        value = ask("代理地址", default="http://127.0.0.1:7890")
        return value, "always"
    return "", "auto"


# --------------------------------------------------------------------- 写文件
def load_template() -> dict:
    if TEMPLATE_PATH.exists():
        try:
            data = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as exc:
            print(f"  读取 config.example.json 失败（{exc}），改用内置默认值。")
    return {}


def build_config(sender, recipient, host, port, starttls, proxy, tv_mode) -> dict:
    data = load_template()
    data.setdefault("sources", ["tradingview", "sina", "tencent"])
    data["proxy"] = proxy
    modes = data.setdefault("proxy_modes", {})
    modes["tradingview"] = tv_mode
    modes.setdefault("sina", "auto")
    modes.setdefault("tencent", "auto")
    modes.setdefault("gold_api", "auto")

    email = data.setdefault("email", {})
    email["enabled"] = True
    email["smtp_host"] = host
    email["smtp_port"] = int(port)
    email["use_ssl"] = not starttls
    email["use_starttls"] = bool(starttls)
    email["username"] = sender
    email["sender"] = sender
    email["recipients"] = [recipient]
    email.setdefault("password_env", "GOLDALERT_SMTP_PASSWORD")
    email.setdefault("subject_prefix", "[GoldAlert 金价提醒]")

    data.setdefault("state_file", "data/state.json")
    data.setdefault("levels", {}).setdefault("file", "data/levels.json")
    return data


def write_env(authcode: str, api_key: str) -> None:
    lines = [
        "# 邮件授权码（不是邮箱登录密码！）",
        "GOLDALERT_SMTP_PASSWORD=" + authcode,
        "",
        "# DeepSeek API Key（不用 AI 就留空）",
        "GOLDALERT_DEEPSEEK_API_KEY=" + api_key,
        "",
    ]
    ENV_PATH.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def write_config(data: dict) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    CONFIG_PATH.write_text(text, encoding="utf-8", newline="\n")


# --------------------------------------------------------------------- 主流程
def parse_args(argv):
    parser = argparse.ArgumentParser(description="GoldAlert 首次安装向导")
    parser.add_argument("--email", help="发件邮箱")
    parser.add_argument("--authcode", help="邮箱授权码")
    parser.add_argument("--recipient", help="收件邮箱")
    parser.add_argument("--api-key", dest="api_key", help="DeepSeek API Key")
    parser.add_argument("--proxy", help="代理地址，例如 http://127.0.0.1:7890")
    parser.add_argument("--no-proxy", dest="proxy", action="store_const", const="", help="不使用代理")
    parser.add_argument("--smtp-host", dest="smtp_host", help="手动指定 SMTP 服务器")
    parser.add_argument("--smtp-port", dest="smtp_port", help="手动指定 SMTP 端口")
    parser.add_argument("--force", action="store_true", help="已存在 config.json 时直接重新配置")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    banner()

    if CONFIG_PATH.exists() and not args.force:
        print(f"检测到已经存在 {CONFIG_PATH.name}。")
        if not yes_no("要重新填写一遍吗？（选 n 就保持现有配置不动）", default=False):
            print()
            print("保持现有配置，没有做任何修改。")
            return 0
        print()

    sender = step_email(args)
    host, port, starttls = step_smtp(args, sender)
    authcode = step_authcode(args, sender)
    recipient = step_recipient(args, sender)
    api_key = step_apikey(args)
    proxy, tv_mode = step_proxy(args)

    print()
    print(BAR)
    print("  请确认一下：")
    hr()
    print(f"  发件邮箱     {sender}")
    print(f"  SMTP 服务器  {host}:{port}" + ("（STARTTLS）" if starttls else "（SSL）"))
    print(f"  收件邮箱     {recipient}")
    print(f"  授权码       {'*' * 8}（{len(authcode)} 位）")
    print(f"  DeepSeek     {'已填写' if api_key else '留空（不启用 AI）'}")
    print(f"  代理         {proxy or '不使用'}")
    print(BAR)
    if not yes_no("写入配置？", default=True):
        print("已取消，没有修改任何文件。")
        return 1

    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)

    config = build_config(sender, recipient, host, port, starttls, proxy, tv_mode)
    try:
        write_config(config)
        write_env(authcode, api_key)
    except Exception as exc:
        print(f"写文件失败：{exc}")
        return 1

    print()
    print("配置已写好：")
    print(f"  {CONFIG_PATH}")
    print(f"  {ENV_PATH}")
    print()
    print("提醒：这两个文件里有你的授权码和密钥，不要发给别人、也不要传网盘。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print()
        print("已取消，没有修改任何文件。")
        raise SystemExit(1)