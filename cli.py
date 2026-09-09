from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from cache.duplicates import audit_cache_coverage, detect_duplicate_findings
from cache.repository import RecentCacheRepository
from config.loader import load_sites
from domain.errors import ConfigurationError
from output.formatter import multi_failure_text
from output.transaction import (
    CacheUpdateError,
    atomic_write_text,
    write_formal_outputs_and_cache,
    write_outputs,
)
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from services.multi_period import scrape_sites_for_periods
from services.recent_history import scrape_history_sites
from services.single_period import scrape_sites
from services.failed_retry import apply_retry, sites_from_failure_file

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = Path(r"C:\Users\Administrator\Desktop\每天工具\爬虫合集\七类数据统一归纳")
FAILURE_OUTPUT_DIR = Path(r"C:\Users\Administrator\Desktop\每天工具\爬虫合集\七类数据统一归纳失败")
DEFAULT_SITES = ROOT / "config" / "sites.json"
DEFAULT_HISTORY_CACHE = ROOT / "recent_10_cache.json"
CACHE_UPDATE_SUCCESS_RATIO = 0.85


def success_path(period: int, output_dir: Path = OUTPUT_DIR) -> Path:
    return output_dir / f"{period}期-二肖.txt"


def failure_path(period: int, output_dir: Path = FAILURE_OUTPUT_DIR) -> Path:
    return output_dir / f"{period}期-二肖-失败.txt"


def multi_failure_path(periods: list[int], output_dir: Path = FAILURE_OUTPUT_DIR) -> Path:
    return output_dir / f"{'_'.join(str(period) for period in periods)}期-二肖-多期全部失败.txt"


def parse_period(value: str) -> int:
    period = value.strip().removesuffix("期")
    if not re.fullmatch(r"\d{3}", period):
        raise argparse.ArgumentTypeError("期数必须是 3 位数字")
    parsed = int(period)
    if not 1 <= parsed <= 365:
        raise argparse.ArgumentTypeError("期数必须在 001-365 之间")
    return parsed


def should_update_cache(success_count: int, total_count: int) -> bool:
    """Allow a single-period cache commit only when success is strictly over 85%."""
    if total_count <= 0 or success_count < 0 or success_count > total_count:
        return False
    return success_count / total_count > CACHE_UPDATE_SUCCESS_RATIO


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="统一抓取杀两肖站点数据")
    parser.add_argument("-p", "--period", type=parse_period, help="手动指定单个期数，例如 210")
    parser.add_argument("--periods", type=parse_period, nargs="+", help="手动指定多个期数，例如 207 208 209 210")
    parser.add_argument("--sites-file", default=str(DEFAULT_SITES), help="站点配置 JSON")
    parser.add_argument("--output", default="", help="成功结果 txt 文件")
    parser.add_argument("--errors", default="", help="失败结果 txt 文件")
    parser.add_argument("--timeout", type=int, default=25, help="单站请求超时秒数")
    parser.add_argument("--workers", type=int, default=10, help="并发抓取数量")
    parser.add_argument("--limit", type=int, default=0, help="只抓前 N 个站点")
    parser.add_argument("--include-url", action="store_true", help="成功行附带 URL")
    parser.add_argument("--history-cache", action="store_true", help="抓取最近10期并生成重复检测缓存")
    parser.add_argument("--history-cache-file", default=str(DEFAULT_HISTORY_CACHE), help="重复检测缓存 JSON")
    parser.add_argument("--duplicate-check", action="store_true", help="使用 recent_10_cache.json 执行正式重复检测")
    parser.add_argument("--retry-failures", action="store_true", help="只重抓当前期失败TXT中的站点")
    args = parser.parse_args(argv)
    if args.timeout < 1:
        parser.error("--timeout 必须大于等于1")
    if args.workers < 1:
        parser.error("--workers 必须大于等于1")
    if args.limit < 0:
        parser.error("--limit 必须大于等于0")
    if args.duplicate_check and (args.period is not None or args.periods):
        parser.error("--duplicate-check 不需要指定 --period 或 --periods")
    if args.period is None and not args.periods and not args.duplicate_check:
        parser.error("必须指定 --period 或 --periods")
    if args.period is not None and args.periods:
        parser.error("--period 和 --periods 不能同时使用")
    if args.history_cache and args.period is None:
        parser.error("--history-cache 只能配合单个 --period 使用")
    if args.retry_failures and args.history_cache:
        parser.error("--retry-failures 不能与 --history-cache 同时使用")
    if args.retry_failures and (args.period is None or args.periods or args.limit):
        parser.error("--retry-failures 只能配合单个 --period 使用，且不能限量")
    if args.periods and (args.output or args.errors):
        parser.error("--periods 会按原单期文件名输出，不能同时指定 --output/--errors")
    if args.limit > 0 and (args.periods or args.history_cache):
        parser.error("--limit 仅用于单期只读诊断，不能配合多期或缓存重建")
    return args


def run_duplicate_check(sites, path: Path) -> int:
    try:
        cache = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"重复检测缓存读取失败：{exc}")
        return 2
    if not isinstance(cache, dict):
        print("重复检测缓存格式错误")
        return 2
    try:
        coverage = audit_cache_coverage(cache, sites)
        findings = detect_duplicate_findings(cache)
    except ValueError as exc:
        print(f"重复检测缓存校验失败：{exc}")
        return 2
    if coverage.missing_sites:
        print(f"缓存缺失目录：{','.join(coverage.missing_sites)}")
    if coverage.incomplete_sites:
        print(f"缓存不足10期目录：{','.join(coverage.incomplete_sites)}")
    for finding in findings:
        label = "重复" if finding.classification == "duplicate" else "疑似重复"
        print(
            f"{label}：{finding.first} / {finding.second} "
            f"连续{finding.consecutive}期（{finding.start_period}-{finding.end_period}）"
        )
    if coverage.missing_sites or coverage.incomplete_sites or any(
        finding.classification == "duplicate" for finding in findings
    ):
        return 2
    return 1 if findings else 0


def run_multi_periods(
    sites,
    args: argparse.Namespace,
    registry: ParserRegistry,
) -> int:
    periods = list(dict.fromkeys(args.periods))
    period_results = scrape_sites_for_periods(
        sites,
        periods,
        args.timeout,
        args.workers,
        registry=registry,
    )
    total_ok = 0
    for period in periods:
        results = period_results[period]
        output = success_path(period)
        errors = failure_path(period)
        write_outputs(results, output, errors, include_url=args.include_url)
        ok_count = sum(result.ok for result in results)
        total_ok += ok_count
        print(f"{period}期完成：成功 {ok_count} 条，失败 {len(results) - ok_count} 条")
        print(f"{period}期成功结果：{output.resolve()}")
        print(f"{period}期失败记录：{errors.resolve() if errors.exists() else '无'}")
    summary = multi_failure_path(periods)
    atomic_write_text(summary, multi_failure_text(period_results))
    failed_all = sum(
        all(not period_results[period][index].ok for period in periods)
        for index in range(len(sites))
    )
    print("多期模式：未更新 recent_10_cache.json")
    print(f"多期全部失败目录：{failed_all} 个")
    print(f"多期汇总失败报告：{summary.resolve()}")
    return 0 if total_ok == len(periods) * len(sites) else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        sites = load_sites(Path(args.sites_file), allowed_parsers=ENGINE_REGISTRY)
        if args.limit > 0:
            sites = sites[: args.limit]
        registry = ParserRegistry.bind_sites(sites)
    except (ConfigurationError, ValueError) as exc:
        print(f"站点配置加载失败：{exc}")
        return 2
    repository = RecentCacheRepository(Path(args.history_cache_file))
    if args.duplicate_check:
        return run_duplicate_check(sites, repository.path)
    if args.retry_failures:
        errors = Path(args.errors) if args.errors else failure_path(args.period)
        try:
            targets = sites_from_failure_file(errors, sites)
        except (OSError, UnicodeError) as exc:
            print(f"失败TXT读取失败，未执行重抓：{exc}")
            return 2
        if not targets:
            print(f"未找到{args.period}期失败TXT中的可重抓站点：{errors.resolve()}")
            return 0
        results = scrape_sites(targets, args.period, args.timeout, args.workers, registry=registry)
        try:
            apply_retry(results, Path(args.output) if args.output else success_path(args.period), errors,
                        repository.path, args.period, args.include_url, sites=sites)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"失败站点更新失败：{exc}")
            return 2
        ok_count = sum(result.ok for result in results)
        print(f"失败站点重抓完成：成功 {ok_count} 条，失败 {len(results) - ok_count} 条")
        print(f"仅处理站点：{','.join(site.name for site in targets)}")
        if ok_count:
            print("原排行榜未重算")
        return 0 if ok_count == len(results) else 1
    if args.periods:
        try:
            return run_multi_periods(sites, args, registry)
        except (OSError, ValueError) as exc:
            print(f"多期输出写入未完成：{exc}")
            return 2
    if args.history_cache:
        results = scrape_history_sites(
            sites,
            args.period,
            args.timeout,
            args.workers,
            registry=registry,
        )
        try:
            repository.commit(repository.prepare_history_update(results, args.period))
        except (OSError, ValueError) as exc:
            print(f"缓存校验失败：{exc}")
            print("已拒绝覆盖 recent_10_cache.json")
            return 2
        ok_count = sum(result.ok for result in results)
        print(f"完成：缓存成功 {ok_count} 个，失败 {len(results) - ok_count} 个")
        print(f"重复检测缓存：{repository.path.resolve()}")
        return 0 if ok_count == len(results) else 1

    results = scrape_sites(
        sites,
        args.period,
        args.timeout,
        args.workers,
        registry=registry,
    )
    if args.limit > 0:
        ok_count = sum(result.ok for result in results)
        print(f"限量诊断完成：成功 {ok_count} 条，失败 {len(results) - ok_count} 条")
        print("限量诊断模式：未写正式TXT，未更新 recent_10_cache.json")
        return 0 if ok_count else 1
    output = Path(args.output) if args.output else success_path(args.period)
    errors = Path(args.errors) if args.errors else failure_path(args.period)
    ok_count = sum(result.ok for result in results)
    cache_allowed = should_update_cache(ok_count, len(sites))
    prepared: dict[str, object] | None = None
    cache_error: Exception | None = None
    if cache_allowed:
        try:
            prepared = repository.prepare_update(results, args.period)
        except (OSError, ValueError) as exc:
            cache_error = exc
            print(f"缓存准备失败：{exc}")
        if prepared is None and cache_error is None:
            print("当前期数与缓存窗口不连续，缓存未更新")
    try:
        cache_updated = write_formal_outputs_and_cache(
            results,
            output,
            errors,
            repository,
            prepared,
            include_url=args.include_url,
        )
    except CacheUpdateError as exc:
        cache_error = exc
        cache_updated = False
    except (OSError, ValueError) as exc:
        print(f"正式输出失败：{exc}")
        return 2
    success_rate = ok_count / len(sites) * 100 if sites else 0.0
    print(f"完成：成功 {ok_count} 条，失败 {len(results) - ok_count} 条")
    print(f"成功结果：{output.resolve()}")
    print(f"失败记录：{errors.resolve() if errors.exists() else '无'}")
    if not cache_allowed:
        print(f"成功率 {success_rate:.2f}% 不超过85%，保持 recent_10_cache.json 不变")
    print(f"最近10期基准缓存：{'已更新' if cache_updated else '未更新'} {repository.path.resolve()}")
    if cache_error is not None:
        if isinstance(cache_error, CacheUpdateError):
            print(str(cache_error))
        else:
            print(f"缓存更新未完成：{cache_error}")
        return 2
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
