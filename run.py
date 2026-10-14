"""命令行入口。

用法::

    python run.py                 # 启动实时监控（默认）
    python run.py once            # 只取一次当前金价
    python run.py report          # 立即发一封金价播报（含 AI 小结）
    python run.py doctor          # 自检：代理、数据源、邮件、AI
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from goldalert.ai import MarketAnalyzer
from goldalert.config import Config, EmailConfig
from goldalert.display import Dashboard
from goldalert.monitor import Monitor
from goldalert.notify import (
    ConsoleNotifier,
    DashboardNotifier,
    EmailNotifier,
    LogNotifier,
    NotifierHub,
)
from goldalert.levels import Level, LevelService, LevelStore, LevelWatcher, format_levels
from goldalert.levels_ui import start_settings_service
from goldalert.reports import PriceReporter, compute_next_fire, format_report
from goldalert.router import SourceManager
from goldalert.volatility import VolatilityService
from goldalert.sources import SourceError, build_sources, create_session
from goldalert.store import HistoryStore, StateStore
from goldalert.util import (
    ensure_utf8_streams,
    fmt_dt,
    now_cn,
    parse_proxy,
    probe_tcp,
    setup_logging,
)

PROJECT_ROOT = Path(__file__).resolve().parent



def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="goldalert",
        description="现货黄金（XAU）实时监控与提醒",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="monitor",
        choices=["monitor", "once", "doctor", "report", "levels"],
        help="monitor=持续监控（默认），once=取一次，doctor=环境自检，report=立即播报一次，levels=管理关键价位",
    )
    parser.add_argument("extra", nargs="*", help="levels 命令的附加参数，例如 add 4250 上限")
    parser.add_argument("-c", "--config", default="config.json", help="配置文件路径")
    parser.add_argument("-i", "--interval", type=float, default=None, help="刷新间隔（秒）")
    parser.add_argument("--no-display", action="store_true", help="不使用实时面板，改为逐行日志")
    parser.add_argument("--cycles", type=int, default=None, help="只跑指定轮数后退出（调试用）")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    return parser.parse_args(argv)


def build_email_notifier(email: EmailConfig, logger):
    """按配置构造邮件渠道；缺少授权码或收件人时跳过并给出提示。"""
    if not email.recipients:
        logger.warning("邮件通知未配置收件人（email.recipients），已跳过邮件渠道")
        return None
    password = os.environ.get(email.password_env, "")
    if not password:
        logger.warning(
            "未找到环境变量 %s，邮件通知未启用；请在 .env 里填入邮件授权码",
            email.password_env,
        )
        return None
    return EmailNotifier(
        smtp_host=email.smtp_host,
        smtp_port=email.smtp_port,
        use_ssl=email.use_ssl,
        use_starttls=email.use_starttls,
        username=email.username,
        password=password,
        sender=email.sender,
        recipients=email.recipients,
        subject_prefix=email.subject_prefix,
        timeout=email.timeout,
        logger=logger,
    )


def build_analyzer(config: Config, logger):
    """构造 AI 分析器；缺少 API Key 时提示但仍返回（analyze 会自动跳过）。"""
    if not config.ai.enabled:
        return None
    analyzer = MarketAnalyzer(config.ai, create_session(), logger)
    if not analyzer.available:
        logger.warning(
            "未找到环境变量 %s，AI 盘面解读不会启用；请在 .env 里填入 DeepSeek API Key",
            config.ai.api_key_env,
        )
    return analyzer


def build_hub(config: Config, logger, dashboard=None) -> NotifierHub:
    hub = NotifierHub()
    if dashboard is not None:
        hub.add(DashboardNotifier(dashboard, logger))
    else:
        hub.add(ConsoleNotifier())
    hub.add(LogNotifier(logger))

    if config.email.enabled:
        notifier = build_email_notifier(config.email, logger)
        if notifier is not None:
            hub.add(notifier)
    return hub


def build_manager(config: Config, logger, dashboard=None) -> SourceManager:
    session = create_session()
    sources = build_sources(
        config.sources,
        session,
        timeout=config.request_timeout,
        proxy=config.proxy,
        tradingview_symbol=config.tradingview_symbol,
        proxy_modes=config.proxy_modes,
    )
    return SourceManager(
        sources,
        build_hub(config, logger, dashboard),
        failure_threshold=config.failure_threshold,
        proxy=config.proxy,
        proxy_check_interval=config.proxy_check_interval,
        logger=logger,
    )


def build_level_service(
    config: Config,
    logger,
    hub: NotifierHub,
    history=None,
    state=None,
    analyzer=None,
) -> "LevelService | None":
    """装配关键价位提醒；未开启时返回 None。"""
    if not config.levels.enabled:
        return None
    if state is None:
        state = StateStore(config.state_file)
    store = LevelStore(config.levels.file, logger)
    watcher = LevelWatcher(state, logger)
    return LevelService(
        store,
        watcher,
        hub,
        history=history,
        analyzer=analyzer if config.levels.ai_enabled else None,
        state=state,
        ai_refresh_minutes=config.levels.ai_refresh_minutes,
        ai_max_levels=config.levels.ai_max_levels,
        logger=logger,
    )


def cmd_once(config: Config, manager: SourceManager) -> int:
    try:
        quote = manager.fetch(config.symbol)
    except SourceError as exc:
        print(f"取数失败：{exc}")
        return 1
    pct = f"  ({quote.change_pct:+.2f}%)" if quote.change_pct is not None else ""
    print(
        f"{quote.name} {quote.symbol}  最新价 {quote.price:,.2f} 美元/盎司{pct}"
        f"  数据源={quote.source}"
    )
    return 0


def cmd_report(config: Config, manager: SourceManager, logger) -> int:
    """立即取一次价并发一封播报（含 AI 小结），用于验证内容。"""
    try:
        quote = manager.fetch(config.symbol)
    except SourceError as exc:
        print(f"取数失败：{exc}")
        return 1

    history = HistoryStore(config.history_file, config.max_history_points)
    history.append(quote)

    previous = None
    window = config.report.compare_window_minutes
    if window > 0:
        found = history.price_at_or_before(quote.fetched_at - window * 60.0)
        if found is not None:
            previous = found[0]

    body = format_report(quote, now_cn(), previous, window)

    analyzer = build_analyzer(config, logger)
    if analyzer is not None and analyzer.available:
        records = history.records_since(quote.fetched_at - config.ai.lookback_hours * 3600.0)
        analysis = analyzer.analyze(quote, records)
        if analysis:
            body += "\n\n———— AI 盘面小结 ————\n" + analysis
        else:
            print("（AI 小结未生成，仅发送报价）")

    title = f"📊 金价播报 {quote.price:,.2f} 美元/盎司"
    print("-" * 64)
    print(f"主题: {config.email.subject_prefix} {title}")
    print(body)
    print("-" * 64)
    results = manager.notifier.send(title, body, "info")
    for name, ok in results.items():
        print(f"  {'✔' if ok else '✘'} {name}")
    return 0


def cmd_doctor(config: Config, manager: SourceManager, logger) -> int:
    print("=" * 64)
    print("配置检查")
    problems = config.validate()
    if problems:
        for problem in problems:
            print(f"  ✘ {problem}")
    else:
        print("  ✔ 配置无问题")
    print(f"  监控品种: {config.symbol}   刷新间隔: {config.interval_seconds:g}s")
    print(f"  数据源优先级: {', '.join(s.describe() for s in manager.sources)}")

    print("-" * 64)
    print(f"代理探测: {config.proxy or '（未配置）'}")
    target = parse_proxy(config.proxy)
    if target:
        alive = probe_tcp(target[0], target[1], timeout=3.0)
        print(f"  {'✔' if alive else '✘'} TCP {target[0]}:{target[1]} -> {'可连接' if alive else '连不上'}")
    else:
        print("  · 未配置代理，跳过")

    print("-" * 64)
    print("各数据源实测:")
    for source in manager.sources:
        try:
            quote = source.fetch(config.symbol)
            print(f"  ✔ {source.describe():22s} {quote.price:,.2f}  ({quote.source})")
        except Exception as exc:
            print(f"  ✘ {source.describe():22s} {type(exc).__name__}: {exc}")

    print("-" * 64)
    print("定时播报:")
    if not config.report.enabled:
        print("  · 未开启（config.json 里把 report.enabled 设为 true 即可）")
    else:
        align = "整点对齐" if config.report.align_to_clock else "从启动时刻顺延"
        print(f"  每 {config.report.every_minutes} 分钟一次（{align}）")
        print(f"  对比窗口 {config.report.compare_window_minutes} 分钟")
        print(f"  下次播报时间 {fmt_dt(compute_next_fire(config.report))}")

    print("-" * 64)
    print("关键价位提醒:")
    if not config.levels.enabled:
        print("  · 未开启（config.json 里把 levels.enabled 设为 true 即可）")
    else:
        store = LevelStore(config.levels.file, logger)
        settings = store.settings
        print(f"  价位文件: {store.path}")
        print(
            f"  总开关 {'开' if settings.enabled else '关'}   "
            f"接近距离 {settings.proximity_usd:g} 美元   "
            f"冷却 {settings.cooldown_minutes:g} 分钟   "
            f"重新武装 {settings.rearm_usd:g} 美元"
        )
        levels = store.all_levels()
        print(f"  现有价位 {len(levels)} 个:")
        print(format_levels(levels))
        if config.levels.ai_enabled:
            print(
                f"  AI 更新: 每 {config.levels.ai_refresh_minutes:g} 分钟一次，"
                f"最多给 {config.levels.ai_max_levels} 个"
            )
        else:
            print("  AI 更新: 未开启")

    print("-" * 64)
    print("异常波动提醒:")
    if not config.volatility.enabled:
        print("  · 未开启（config.json 里把 volatility.enabled 设为 true 即可）")
    else:
        print(
            f"  {config.volatility.window_minutes:g} 分钟内最高价与最低价相差 "
            f"≥ {config.volatility.threshold_usd:g} 美元就提醒"
        )
        print(
            f"  冷却 {config.volatility.cooldown_minutes:g} 分钟；"
            f"波动收窄到 {config.volatility.threshold_usd * config.volatility.rearm_ratio:g} "
            f"美元以内才重新开始盯"
        )

    print("-" * 64)
    print("邮件通知:")
    if not config.email.enabled:
        print("  · 未开启（config.json 里把 email.enabled 设为 true 即可）")
    else:
        print(f"  收件人: {', '.join(config.email.recipients) or '（未填写）'}")
        print(f"  SMTP: {config.email.smtp_host}:{config.email.smtp_port}  SSL={config.email.use_ssl}")
        if os.environ.get(config.email.password_env):
            print(f"  ✔ 已读到环境变量 {config.email.password_env}")
        else:
            print(f"  ✘ 环境变量 {config.email.password_env} 未设置（在 .env 里填授权码）")

    print("-" * 64)
    print("AI 盘面解读:")
    if not config.ai.enabled:
        print("  · 未开启（config.json 里把 ai.enabled 设为 true 即可）")
    else:
        print(f"  模型: {config.ai.provider} / {config.ai.model}")
        print(f"  接口: {config.ai.base_url}")
        print(f"  回看 {config.ai.lookback_hours:g} 小时，压缩成 {config.ai.max_points} 个采样点")
        if os.environ.get(config.ai.api_key_env):
            print(f"  ✔ 已读到环境变量 {config.ai.api_key_env}")
            analyzer = build_analyzer(config, logger)
            try:
                quote = manager.fetch(config.symbol)
                history = HistoryStore(config.history_file, config.max_history_points)
                records = history.records_since(quote.fetched_at - config.ai.lookback_hours * 3600.0)
                text = analyzer.analyze(quote, records)
                if text:
                    print("  试跑结果:")
                    for line in text.splitlines():
                        print(f"    {line}")
                else:
                    print("  ✘ 试跑没有返回内容（检查 Key 或额度）")
            except Exception as exc:
                print(f"  ✘ 试跑失败: {type(exc).__name__}: {exc}")
        else:
            print(f"  ✘ 环境变量 {config.ai.api_key_env} 未设置（在 .env 里填 DeepSeek API Key）")

    print("-" * 64)
    print("通知渠道自测:")
    results = manager.notifier.send(
        "GoldAlert 自检", "如果你收到这条消息，说明通知渠道工作正常。", "info"
    )
    for name, ok in results.items():
        print(f"  {'✔' if ok else '✘'} {name}")
    print("=" * 64)
    return 0


def cmd_levels(config: Config, manager: SourceManager, extra: list, logger) -> int:
    """命令行管理关键价位；不带参数时只列出当前设置。"""
    store = LevelStore(config.levels.file, logger)
    action = (extra[0].lower() if extra else "list")

    if action in ("list", "ls", "show"):
        settings = store.settings
        print(f"价位文件: {store.path}")
        print(
            f"提醒总开关 {'开' if settings.enabled else '关'}   "
            f"接近 {settings.proximity_usd:g} 美元   "
            f"冷却 {settings.cooldown_minutes:g} 分钟   "
            f"重新武装 {settings.rearm_usd:g} 美元"
        )
        print(format_levels(store.all_levels()))
        print("用法: run.py levels add 4250 上限   |   run.py levels ui（打开窗口）")
        return 0

    if action in ("add", "a"):
        if len(extra) < 2:
            print("用法: run.py levels add 4250 说明文字")
            return 2
        try:
            price = float(extra[1])
        except ValueError:
            print(f"价位得是数字: {extra[1]!r}")
            return 2
        level, created = store.upsert(price, " ".join(extra[2:]).strip())
        if created:
            print(f"已添加 {level.price:,.2f}（{level.label or '未命名'}）")
        else:
            print(f"「{level.label}」已经在列表里了，已直接改成 {level.price:,.2f}")
        return 0

    if action in ("rm", "remove", "del", "delete"):
        if len(extra) < 2:
            print("用法: run.py levels rm <编号或价位>")
            return 2
        target = extra[1]
        level = store.get(target)
        if level is None:
            try:
                price = float(target)
            except ValueError:
                price = None
            if price is not None:
                hits = [item for item in store.all_levels() if abs(item.price - price) < 0.005]
                if len(hits) == 1:
                    level = hits[0]
                elif len(hits) > 1:
                    print(f"有 {len(hits)} 个价位都是 {price:,.2f}，请用编号删:")
                    for item in hits:
                        print(f"  {item.id}")
                    return 2
        if level is None:
            print(f"没找到价位 {target!r}")
            return 1
        store.remove(level.id)
        print(f"已删除 {level.price:,.2f}（{level.label or '未命名'}）")
        return 0

    if action in ("ai", "refresh"):
        analyzer = build_analyzer(config, logger)
        if analyzer is None or not analyzer.available:
            print(f"AI 未启用（检查 .env 里的 {config.ai.api_key_env}）")
            return 1
        try:
            quote = manager.fetch(config.symbol)
        except SourceError as exc:
            print(f"取数失败：{exc}")
            return 1
        history = HistoryStore(config.history_file, config.max_history_points)
        records = history.records_since(quote.fetched_at - config.ai.lookback_hours * 3600.0)
        suggestions = analyzer.suggest_levels(quote, records, config.levels.ai_max_levels)
        if not suggestions:
            print("AI 没有给出可用价位，过会儿再试。")
            return 1
        store.replace_ai_levels(
            [
                Level(price=item["price"], label=item.get("label") or "AI 关键价位", source="ai")
                for item in suggestions
            ]
        )
        print(f"现价 {quote.price:,.2f}，AI 给的关键价位已写入:")
        print(format_levels(store.by_source("ai"), quote.price))
        return 0

    if action in ("clear-ai", "clear"):
        removed = store.clear_source("ai")
        print(f"已清除 {removed} 个 AI 价位（手动的一个没动）。")
        return 0

    if action in ("ui", "window", "gui"):
        launcher = Path(sys.executable).with_name("pythonw.exe")
        exe = str(launcher) if launcher.exists() else sys.executable
        subprocess.Popen([exe, "-m", "goldalert.levels_ui"], cwd=str(PROJECT_ROOT))
        print("已打开「目标价位设置」窗口。")
        return 0

    print(f"不认识的用法: {action}")
    print("可用: list / add / rm / ai / clear-ai / ui")
    return 2


def main(argv=None) -> int:
    ensure_utf8_streams()
    args = parse_args(argv)
    load_dotenv(PROJECT_ROOT / ".env")

    config = Config.load(args.config)
    if args.interval is not None:
        config.interval_seconds = args.interval

    use_display = (
        args.command == "monitor"
        and config.live_display
        and not args.no_display
        and sys.stdout.isatty()
    )

    logger = setup_logging(
        config.log_file,
        level=logging.DEBUG if args.verbose else logging.INFO,
        quiet=use_display,
    )
    logger.debug("配置: %s", config)

    dashboard = None
    if use_display:
        dashboard = Dashboard(config.symbol, config.interval_seconds, config.sparkline_points)

    manager = build_manager(config, logger, dashboard)

    if args.command == "once":
        return cmd_once(config, manager)
    if args.command == "doctor":
        return cmd_doctor(config, manager, logger)
    if args.command == "report":
        return cmd_report(config, manager, logger)
    if args.command == "levels":
        return cmd_levels(config, manager, args.extra, logger)

    history = HistoryStore(config.history_file, config.max_history_points)
    state = StateStore(config.state_file)
    analyzer = build_analyzer(config, logger)
    reporter = PriceReporter(
        config.report, state, manager.notifier, history,
        analyzer=analyzer, logger=logger,
    )
    level_service = build_level_service(
        config, logger, manager.notifier, history=history, state=state, analyzer=analyzer
    )
    volatility_service = None
    if config.volatility.enabled:
        volatility_service = VolatilityService(
            config.volatility, manager.notifier, state=state, logger=logger
        )
    monitor = Monitor(
        config, manager, history, dashboard,
        reporter=reporter, level_service=level_service,
        volatility_service=volatility_service,
        config_path=args.config, logger=logger,
    )

    # 设置页挂在监控进程里：只要监控在跑，它就一直能用
    settings_server = None
    if config.levels.enabled:
        try:
            settings_server, settings_url = start_settings_service(config, logger)
            logger.info("目标价位设置页: %s", settings_url)
        except Exception as exc:
            logger.warning("设置页没能启动（不影响监控）: %s", exc)

    try:
        monitor.run(max_cycles=args.cycles)
    finally:
        if settings_server is not None:
            settings_server.shutdown()
            settings_server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())