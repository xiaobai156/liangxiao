from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

import adaptive_scrapling as adaptive

from diagnostics.run_report import format_failure, progress_summary_line
from domain.models import DocumentBundle, HistoryResult, Record, Site
from fetching.client import FetchContext
from fetching.browser import render_browser_text
from fetching.page import decode_strdecode_blocks, fetch_payload
from parsers.registry import ParserRegistry
from services.single_period import parse_bundle_for_period
from validation.direction import select_record
from validation.records import merge_equivalent_records, record_value_signature


HISTORY_BACK_PERIODS = 9
ProgressSink = Callable[[str], None]


def recent_periods(current_period: int, count: int = 10) -> list[int]:
    if not 1 <= current_period <= 365:
        raise ValueError(f"期数必须在1-365内：{current_period}")
    return [((current_period - offset - 1) % 365) + 1 for offset in range(max(0, count))]


def history_result_from_records(site: Site, records: list[Record], current_period: int) -> HistoryResult:
    try:
        selected = select_record(records, current_period, site)
    except (ValueError, LookupError) as exc:
        return HistoryResult(site, (), str(exc))
    selected_positions = set(selected.source_positions or (selected.position,))
    selected_indices = [
        index
        for index, record in enumerate(records)
        if record.period == selected.period
        and record.zodiac == selected.zodiac
        and (record is selected or record.position in selected_positions)
    ]
    selected_index = (max(selected_indices) if site.pick == "bottom" else min(selected_indices)) if selected_indices else -1
    if selected_index < 0:
        return HistoryResult(site, (), "指定期数记录位置无效")
    if site.pick == "top":
        selected_start = min(selected_indices)
        selected_end = max(selected_indices)
        cycle_end = next(
            (index for index in range(selected_end + 1, len(records)) if records[index].period == current_period),
            len(records),
        )
        window = records[selected_start:cycle_end]
    else:
        selected_start = min(selected_indices)
        previous_cycle = next(
            (index for index in range(selected_start - 1, -1, -1) if records[index].period == current_period),
            -1,
        )
        window = records[previous_cycle + 1 : selected_index + 1]
    expected = recent_periods(current_period, HISTORY_BACK_PERIODS + 1)
    grouped: dict[int, list[Record]] = {}
    for record in window:
        if record.period in expected:
            grouped.setdefault(record.period, []).append(record)
    selected_by_period: dict[int, Record] = {}
    for period, candidates in grouped.items():
        signatures = {record_value_signature(record) for record in candidates}
        if len(signatures) > 1:
            details = "、".join(
                f"{record.zodiac}@位置{record.position}"
                for record in candidates
            )
            return HistoryResult(site, (), f"数据存在冲突：{period}期出现多个候选：{details}")
        selected_by_period[period] = merge_equivalent_records(
            candidates,
            prefer_last=site.pick == "bottom",
        )
    ordered = tuple(selected_by_period[period] for period in expected if period in selected_by_period)
    missing = [period for period in expected if period not in selected_by_period]
    if missing:
        return HistoryResult(site, ordered, f"缺少 {','.join(str(period) for period in missing)} 期数据")
    return HistoryResult(site, ordered)


def scrape_site_history(
    site: Site,
    current_period: int,
    timeout: int,
    context: FetchContext,
    registry: ParserRegistry,
    adaptive_manager: adaptive.AdaptiveManager | None = None,
) -> HistoryResult:
    source = ""
    bundle = DocumentBundle(())
    try:
        bundle = fetch_payload(site, current_period, timeout, context, lambda *_args: True)
        source = bundle.combined_source
        records, selected = parse_bundle_for_period(bundle, site, current_period, registry)
    except Exception as exc:
        if (
            adaptive_manager is None
            or not adaptive_manager.enabled
            or site.payload in {"admin_article_api", "tuku_user_forums"}
            or not adaptive.should_attempt_recovery(site, adaptive.classify_failure(exc), str(exc))
        ):
            return HistoryResult(site, (), format_failure(exc))
        try:
            recovery = adaptive_manager.recover(
                site,
                current_period,
                timeout,
                bundle,
                context.get_text,
                registry.parse,
                select_record,
                lambda record: record.period == current_period,
                decode_strdecode_blocks,
                render_browser_text,
            )
        except Exception as recovery_exc:
            return HistoryResult(site, (), f"{format_failure(exc)}；{recovery_exc}")
        if adaptive_manager.mode == "shadow":
            return HistoryResult(site, (), f"{format_failure(exc)}；自适应影子验证通过，未写入正式结果")
        records = list(recovery.records)
        selected = recovery.record
    result = history_result_from_records(site, records, current_period)
    if result.ok and adaptive_manager is not None and adaptive_manager.enabled:
        try:
            adaptive_manager.observe_primary_success(site, source, records, selected, registry.parse, select_record)
        except (RuntimeError, ValueError):
            pass
    return result


def scrape_history_sites(
    sites: list[Site],
    current_period: int,
    timeout: int,
    workers: int,
    *,
    context: FetchContext | None = None,
    registry: ParserRegistry | None = None,
    progress: ProgressSink | None = print,
    adaptive_manager: adaptive.AdaptiveManager | None = None,
) -> list[HistoryResult]:
    if not sites:
        return []
    active_context = context or FetchContext()
    active_registry = registry or ParserRegistry.bind_sites(sites)
    results: list[HistoryResult | None] = [None] * len(sites)
    started_at = time.monotonic()
    success = failed = done = 0
    scheduled_indices = range(len(sites))
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(sites)))) as executor:
        futures = {
            executor.submit(
                scrape_site_history,
                sites[index],
                current_period,
                timeout,
                active_context,
                active_registry,
                adaptive_manager,
            ): index
            for index in scheduled_indices
        }
        for future in as_completed(futures):
            index = futures[future]
            result = future.result()
            results[index] = result
            done += 1
            success += int(result.ok)
            failed += int(not result.ok)
            if progress is not None:
                progress(
                    progress_summary_line(
                        done,
                        len(sites),
                        success,
                        failed,
                        time.monotonic() - started_at,
                        result.site.name,
                        result.ok,
                        result.error,
                    )
                )
    return [result for result in results if result is not None]
