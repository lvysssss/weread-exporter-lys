from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from .config import SUPPORTED_CRAWL_METHODS, SUPPORTED_FORMATS, AppConfig, load_config
from .platforms import ExportRequest, available_platforms, get_platform
from .progress import ProgressRenderer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="weread-exporter-lys",
        description="从微信读书等阅读平台导出书籍。",
    )
    parser.add_argument("--platform", help="阅读平台，当前支持 weread")
    parser.add_argument("--book-id", help="书本 id，例如 7cb324e0727ab1f17cbf4c1")
    parser.add_argument(
        "--url",
        help="书本链接，例如 https://weread.qq.com/web/reader/7cb324e0727ab1f17cbf4c1",
    )
    parser.add_argument("--format", choices=SUPPORTED_FORMATS, help="目标文件格式")
    parser.add_argument("--cache-dir", type=Path, help="缓存目录")
    parser.add_argument("--output-dir", type=Path, help="输出目录")
    parser.add_argument("--delay", type=float, help="爬取停顿秒数，防止过快触发风控")
    parser.add_argument("--config", type=Path, help="JSON 配置文件路径")
    parser.add_argument("--auth-state", type=Path, help="微信读书登录态 storage_state 文件路径")
    parser.add_argument("--headless", action="store_true", help="后续爬虫层使用无头浏览器")
    parser.add_argument(
        "--crawl-method",
        choices=SUPPORTED_CRAWL_METHODS,
        default="canvas",
        help="爬取方法：xhtml（默认，结构化源直取，生僻字零误差）或 canvas（旧法，坐标合并）",
    )
    parser.add_argument(
        "--max-chapters", type=int, default=0,
        help="最多爬取章节数（0=全部）",
    )
    parser.add_argument("--debug", action="store_true", help="调试模式，输出详细提取环节信息")
    parser.add_argument("--interactive", action="store_true", help="强制进入交互式向导")
    return parser


def build_request(args: argparse.Namespace, config: AppConfig) -> ExportRequest | None:
    platform_name = args.platform or config.default_platform
    output_format = args.format or config.default_format
    cache_dir = args.cache_dir or config.cache_dir
    output_dir = args.output_dir or config.output_dir
    delay = args.delay if args.delay is not None else config.delay
    headless = args.headless or config.headless
    auth_state_path = args.auth_state or config.auth_state_path
    crawl_method = args.crawl_method or config.crawl_method
    max_chapters = args.max_chapters if args.max_chapters is not None else 0
    debug = args.debug if hasattr(args, "debug") else False

    raw_book = args.url or args.book_id
    if raw_book is None:
        return None

    platform = get_platform(platform_name)
    book_id = platform.normalize_book_id(raw_book)

    return ExportRequest(
        platform=platform.name,
        book_id=book_id,
        output_format=output_format,
        cache_dir=cache_dir,
        output_dir=output_dir,
        delay=delay,
        headless=headless,
        auth_state_path=auth_state_path,
        crawl_method=crawl_method,
        max_chapters=max_chapters,
        debug=debug,
    )


def run_interactive(config: AppConfig) -> ExportRequest:
    print("微信读书导出工具")
    print("请先打开书本网站，形如：")
    print("  https://weread.qq.com/web/reader/7cb324e0727ab1f17cbf4c1")
    print("书本 id 就是链接最后一段。")
    print()

    platforms = available_platforms()
    platform = _choose_platform(platforms, config.default_platform)
    raw_book = _prompt_required("请输入书本链接或书本 id: ")
    book_id = platform.normalize_book_id(raw_book)
    output_format = _choose_format(config.default_format)
    crawl_method = _choose_crawl_method(config.crawl_method)
    max_chapters = _prompt_int("最多爬取章数（0=全部，回车默认全部）", 0)
    debug = config.debug if hasattr(config, "debug") else False

    request = ExportRequest(
        platform=platform.name,
        book_id=book_id,
        output_format=output_format,
        cache_dir=config.cache_dir,
        output_dir=config.output_dir,
        delay=config.delay,
        headless=config.headless,
        auth_state_path=config.auth_state_path,
        crawl_method=crawl_method,
        max_chapters=max_chapters,
        debug=debug,
    )
    print_request_summary(request)
    return request


def execute_request(request: ExportRequest) -> int:
    platform = get_platform(request.platform)
    if request.on_progress is None:
        renderer = ProgressRenderer()
        request = replace(request, on_progress=renderer.handle)
    print("开始爬取...")
    result = platform.export(request)
    print(result.message)
    if result.output_path is not None:
        print(f"输出文件：{result.output_path}")
    return 0 if result.ok else 2


def print_request_summary(request: ExportRequest) -> None:
    platform = get_platform(request.platform)
    print()
    print("即将执行：")
    print(f"  平台：{platform.display_name} ({platform.name})")
    print(f"  书本 id：{request.book_id}")
    print(f"  目标格式：{request.output_format}")
    print(f"  缓存目录：{request.cache_dir}")
    print(f"  输出目录：{request.output_dir}")
    print(f"  停顿秒数：{request.delay}")
    print(f"  无头模式：{'是' if request.headless else '否'}")
    print(f"  爬取方法：{request.crawl_method}")
    print(f"  最多章数：{'全部' if not request.max_chapters else request.max_chapters}")
    print(f'  调试模式：{"是" if request.debug else "否"}')
    print(f"  登录态文件：{request.auth_state_path or request.cache_dir / 'auth' / 'weread-storage-state.json'}")
    print()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        config = config.with_overrides(
            cache_dir=args.cache_dir,
            output_dir=args.output_dir,
            delay=args.delay,
            headless=True if args.headless else None,
            auth_state_path=args.auth_state,
            crawl_method=args.crawl_method,
        )

        request = None if args.interactive else build_request(args, config)
        if request is None:
            request = run_interactive(config)
        else:
            print_request_summary(request)

        return execute_request(request)
    except (ValueError, OSError) as error:
        parser.exit(1, f"错误：{error}\n")


def _choose_platform(platforms, default_name: str):
    default_index = next(
        (index for index, platform in enumerate(platforms, start=1) if platform.name == default_name),
        1,
    )
    print("请选择阅读平台：")
    for index, platform in enumerate(platforms, start=1):
        default_mark = "（默认）" if index == default_index else ""
        print(f"  {index}. {platform.display_name} ({platform.name}){default_mark}")
    while True:
        raw_choice = input(f"请输入序号 [{default_index}]: ").strip()
        if not raw_choice:
            raw_choice = str(default_index)
        if raw_choice.isdigit():
            index = int(raw_choice)
            if 1 <= index <= len(platforms):
                return platforms[index - 1]
        print("请输入有效的平台序号。")


def _choose_format(default_format: str) -> str:
    print("请选择目标格式：")
    for index, output_format in enumerate(SUPPORTED_FORMATS, start=1):
        default_mark = "（默认）" if output_format == default_format else ""
        print(f"  {index}. {output_format}{default_mark}")
    default_index = SUPPORTED_FORMATS.index(default_format) + 1
    while True:
        raw_choice = input(f"请输入序号 [{default_index}]: ").strip()
        if not raw_choice:
            return default_format
        if raw_choice.isdigit():
            index = int(raw_choice)
            if 1 <= index <= len(SUPPORTED_FORMATS):
                return SUPPORTED_FORMATS[index - 1]
        if raw_choice in SUPPORTED_FORMATS:
            return raw_choice
        print("请输入有效的目标格式。")


def _choose_crawl_method(default_method: str) -> str:
    descriptions = {
        "xhtml": "结构化源直取，生僻字零误差（推荐）",
        "canvas": "旧法，canvas 坐标合并",
    }
    print("请选择爬取方法：")
    for index, method in enumerate(SUPPORTED_CRAWL_METHODS, start=1):
        default_mark = "（默认）" if method == default_method else ""
        desc = descriptions.get(method, "")
        print(f"  {index}. {method} — {desc}{default_mark}")
    default_index = SUPPORTED_CRAWL_METHODS.index(default_method) + 1
    while True:
        raw_choice = input(f"请输入序号 [{default_index}]: ").strip()
        if not raw_choice:
            return default_method
        if raw_choice.isdigit():
            index = int(raw_choice)
            if 1 <= index <= len(SUPPORTED_CRAWL_METHODS):
                return SUPPORTED_CRAWL_METHODS[index - 1]
        if raw_choice in SUPPORTED_CRAWL_METHODS:
            return raw_choice
        print("请输入有效的爬取方法序号。")


def _prompt_required(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("输入不能为空。")


def _prompt_int(prompt: str, default: int = 0) -> int:
    while True:
        value = input(f"{prompt}: ").strip()
        if not value:
            return default
        if value.isdigit():
            return int(value)
        print("请输入有效数字。")