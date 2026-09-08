from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from diagnostics.run_report import format_failure, progress_summary_line
from domain.models import DocumentBundle, Result, Site
from fetching.client import FetchContext
from parsers.registry import ParserRegistry
from services.single_period import fetch_payload_for_period, parse_bundle_for_period


ProgressSink = Callable[[str], None]
PERIOD_SCOPED_PAYLOADS = {"topic_list_detail", "browser_rendered_page"}


def scrape_site_multi_results(
    site: Site,
    periods: list[int],
    timeout: int,
    context: FetchContext,
    registry: ParserRegistry,
) -> list[Result]:
    bundles: dict[int, DocumentBundle] = {}
    errors: dict[int, BaseException] = {}
    if site.payload in PERIOD_SCOPED_PAYLOADS:
        for period in periods:
            try:
                bundles[period] = fetch_payload_for_period(site, period, timeout, context, registry)
            except Exception as exc:
                errors[period] = exc
    else:
        try:
            shared = fetch_payload_for_period(site, None, timeout, context, registry)
            bundles.update({period: shared for period in periods})
        except Exception as exc:
            errors.update({period: exc for period in periods})
    results: list[Result] = []
    for period in periods:
        try:
            if period not in bundles:
                raise errors.get(period, ValueError("未抓到有效候选"))
            _records, selected = parse_bundle_for_period(bundles[period], site, period, registry)
            results.append(Result(site, selected))
        except Exception as exc:
            results.append(Result(site, None, format_failure(exc)))
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
) -> dict[int, list[Result]]:
    if not sites:
        return {period: [] for period in periods}
    active_context = context if context is not None else FetchContext()
    owns_context = context is None
    active_registry = registry or ParserRegistry.bind_sites(sites)
    by_period: dict[int, list[Result | None]] = {period: [None] * len(sites) for period in periods}
    started_at = time.monotonic()
    success = failed = done = 0
    scheduled_indices = range(len(sites))
    try:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(sites)))) as executor:
            futures = {
                executor.submit(
                    scrape_site_multi_results,
                    sites[index],
                    periods,
                    timeout,
                    active_context,
                    active_registry,
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
    finally:
        if owns_context:
            active_context.close()
