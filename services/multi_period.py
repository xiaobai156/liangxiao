from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

import adaptive_scrapling as adaptive

from diagnostics.run_report import format_failure, progress_summary_line
from domain.models import DocumentBundle, Result, Site
from fetching.client import FetchContext
from fetching.browser import render_browser_text
from fetching.page import decode_strdecode_blocks, fetch_payload
from parsers.registry import ParserRegistry
from services.single_period import parse_bundle_for_period
from validation.direction import select_record
from validation.records import validate_selected_record


ProgressSink = Callable[[str], None]
PERIOD_SCOPED_PAYLOADS = {"topic_list_detail", "browser_rendered_page"}


def scrape_site_multi_results(
    site: Site,
    periods: list[int],
    timeout: int,
    context: FetchContext,
    registry: ParserRegistry,
    adaptive_manager: adaptive.AdaptiveManager | None = None,
) -> list[Result]:
    bundles: dict[int, DocumentBundle] = {}
    errors: dict[int, BaseException] = {}
    if site.payload in PERIOD_SCOPED_PAYLOADS:
        for period in periods:
            try:
                bundles[period] = fetch_payload(site, period, timeout, context, lambda *_args: True)
            except Exception as exc:
                errors[period] = exc
    else:
        try:
            shared = fetch_payload(site, None, timeout, context, lambda *_args: True)
            bundles.update({period: shared for period in periods})
        except Exception as exc:
            errors.update({period: exc for period in periods})
    results: list[Result] = []
    failures: dict[int, BaseException] = {}
    for period in periods:
        try:
            if period not in bundles:
                raise errors.get(period, ValueError("未抓到有效候选"))
            _records, selected = parse_bundle_for_period(bundles[period], site, period, registry)
            results.append(Result(site, selected))
        except Exception as exc:
            failures[period] = exc
            results.append(Result(site, None, format_failure(exc)))
    if (
        failures
        and adaptive_manager is not None
        and adaptive_manager.enabled
        and site.payload not in {"admin_article_api", "tuku_user_forums"}
    ):
        primary_bundle = next(iter(bundles.values())) if bundles else DocumentBundle(())
        recoverable = [
            period
            for period, exc in failures.items()
            if adaptive.should_attempt_recovery(site, adaptive.classify_failure(exc), str(exc))
        ]
        if recoverable:
            try:
                documents = adaptive_manager.collect_documents(
                    site,
                    timeout,
                    primary_bundle,
                    context.get_text,
                    decode_strdecode_blocks,
                    render_browser_text,
                )
                result_index = {period: index for index, period in enumerate(periods)}
                for period in recoverable:
                    recovery = adaptive_manager.recover_from_documents(
                        site,
                        period,
                        documents,
                        registry.parse,
                        select_record,
                        lambda record, issue=period: validate_selected_record(record, issue),
                    )
                    index = result_index[period]
                    if adaptive_manager.mode == "shadow":
                        results[index] = Result(
                            site,
                            None,
                            f"{results[index].error}；自适应影子验证已找到："
                            f"{recovery.record.zodiac}@位置{recovery.record.position}，未写入正式结果",
                        )
                    else:
                        results[index] = Result(site, recovery.record)
            except Exception as recovery_exc:
                for index, period in enumerate(periods):
                    if period in recoverable and not results[index].ok:
                        results[index] = Result(site, None, f"{results[index].error}；{recovery_exc}")
    return results


def scrape_sites_for_periods(
    sites: list[Site],
    periods: list[int],
    timeout: int,
    workers: int,
    *,
    context: FetchContext | None = None,
    registry: ParserRegistry | None = None,
    progress: ProgressSink | None = print,
    adaptive_manager: adaptive.AdaptiveManager | None = None,
) -> dict[int, list[Result]]:
    if not sites:
        return {period: [] for period in periods}
    active_context = context or FetchContext()
    active_registry = registry or ParserRegistry.bind_sites(sites)
    by_period: dict[int, list[Result | None]] = {period: [None] * len(sites) for period in periods}
    started_at = time.monotonic()
    success = failed = done = 0
    scheduled_indices = range(len(sites))
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(sites)))) as executor:
        futures = {
            executor.submit(
                scrape_site_multi_results,
                sites[index],
                periods,
                timeout,
                active_context,
                active_registry,
                adaptive_manager,
            ): index
            for index in scheduled_indices
        }
        for future in as_completed(futures):
            index = futures[future]
            site = sites[index]
            try:
                site_results = future.result()
            except Exception as exc:
                site_results = [Result(site, None, format_failure(exc)) for _period in periods]
            for period, result in zip(periods, site_results):
                by_period[period][index] = result
            done += 1
            site_ok = any(result.ok for result in site_results)
            success += int(site_ok)
            failed += int(not site_ok)
            if progress is not None:
                errors = "；".join(
                    f"{period}期：{result.error}"
                    for period, result in zip(periods, site_results)
                    if not result.ok
                )
                progress(
                    progress_summary_line(
                        done,
                        len(sites),
                        success,
                        failed,
                        time.monotonic() - started_at,
                        site.name,
                        site_ok,
                        errors,
                    )
                )
    return {period: [result for result in results if result is not None] for period, results in by_period.items()}
