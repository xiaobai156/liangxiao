from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

import requests

import adaptive_scrapling as adaptive

from diagnostics.run_report import format_failure, progress_summary_line
from domain.models import DOCUMENT_BOUNDARY, DocumentBundle, PayloadDocument, Record, Result, Site
from fetching.client import FetchContext
from fetching.browser import render_browser_text
from fetching.page import decode_strdecode_blocks, fetch_payload
from parsers.registry import ParserRegistry
from validation.boundaries import (
    linked_document_is_authorized,
    validate_bundle_boundaries,
    validate_document_relationships,
)
from validation.conflicts import validate_document_windows
from validation.direction import select_record
from validation.records import record_value_signature, validate_selected_record


ProgressSink = Callable[[str], None]


def parse_bundle_for_period(
    bundle: DocumentBundle,
    site: Site,
    period: int,
    registry: ParserRegistry,
) -> tuple[list[Record], Record]:
    validate_bundle_boundaries(bundle, site)
    validate_document_relationships(bundle)
    observed: list[tuple[str, list[Record]]] = []
    successful: list[tuple[str, list[Record], Record]] = []
    selection_failures: list[tuple[str, str]] = []
    for document in bundle.documents:
        records = registry.parse(document, site)
        if not records:
            continue
        observed.append((document.label, records))
        try:
            selected = select_record(records, period, site)
        except ValueError as exc:
            if "数据存在冲突" in str(exc):
                raise ValueError(f"数据存在冲突：{document.label}内{exc}") from exc
            selection_failures.append((document.label, str(exc)))
            continue
        successful.append((document.label, records, selected))
    if observed:
        validate_document_windows(observed, site, "多文档", period=period)

    pair_observed: list[tuple[str, list[Record]]] = []
    pair_successes: list[tuple[str, list[Record], Record]] = []
    if len(bundle.documents) > 1 and site.linked_document_pattern:
        anchor_candidates = list(bundle.documents)
        documents_by_url: dict[str, list[PayloadDocument]] = {}
        for document in bundle.documents:
            if document.url:
                documents_by_url.setdefault(document.url, []).append(document)
        for url, documents in documents_by_url.items():
            if len(documents) < 2:
                continue
            record_ids = {document.record_id for document in documents if document.record_id}
            if len(record_ids) > 1:
                raise ValueError(f"记录边界冲突：{url}同URL文档记录ID不同")
            anchor_candidates.append(
                PayloadDocument(
                    "+".join(document.label for document in documents),
                    url,
                    DOCUMENT_BOUNDARY.join(document.source for document in documents),
                    record_id=next(iter(record_ids), ""),
                    record_path=next(
                        (document.record_path for document in documents if document.record_path),
                        "",
                    ),
                    record_count=max((document.record_count for document in documents), default=0),
                    parent_url=next((document.parent_url for document in documents if document.parent_url), ""),
                    link_reference=next(
                        (document.link_reference for document in documents if document.link_reference),
                        "",
                    ),
                )
            )
        anchors = [
            document
            for document in anchor_candidates
            if site.name in document.source or any(keyword in document.source for keyword in site.keywords)
        ]
        for anchor in anchors:
            for body in bundle.documents:
                if not linked_document_is_authorized(site, anchor, body):
                    continue
                record_ids = {value for value in (anchor.record_id, body.record_id) if value}
                if len(record_ids) > 1:
                    raise ValueError(f"记录边界冲突：{anchor.label}+{body.label}记录ID不同")
                combined_source = anchor.source + DOCUMENT_BOUNDARY + body.source
                paired = PayloadDocument(
                    f"{anchor.label}+{body.label}",
                    body.url,
                    combined_source,
                    record_id=next(iter(record_ids), ""),
                    anchor_record_id=anchor.record_id,
                    anchor_record_path=anchor.record_path,
                    anchor_record_count=anchor.record_count,
                    body_record_id=body.record_id,
                    body_record_path=body.record_path,
                    body_record_count=body.record_count,
                    parent_url=anchor.url,
                    link_reference=body.link_reference,
                    body_source_start=len(anchor.source) + len(DOCUMENT_BOUNDARY),
                )
                records = registry.parse(paired, site)
                if not records:
                    continue
                pair_observed.append((paired.label, records))
                try:
                    selected = select_record(records, period, site)
                except ValueError as exc:
                    if "数据存在冲突" in str(exc):
                        raise ValueError(f"数据存在冲突：{paired.label}内{exc}") from exc
                    selection_failures.append((paired.label, str(exc)))
                    continue
                pair_successes.append((paired.label, records, selected))
        if pair_observed:
            validate_document_windows(pair_observed, site, "标题正文文档", period=period)

    if successful:
        independent = {record_value_signature(selected) for _label, _records, selected in successful}
        paired = {record_value_signature(selected) for _label, _records, selected in pair_successes}
        if paired and independent != paired:
            details = "、".join(sorted(independent | paired))
            raise ValueError(f"数据存在冲突：{period}期独立文档与标题正文结果不同：{details}")
        _label, records, selected = max(successful, key=lambda item: len(item[1]))
    elif pair_successes:
        _label, records, selected = max(pair_successes, key=lambda item: len(item[1]))
    elif selection_failures:
        details = "；".join(
            f"{label}：{detail}"
            for label, detail in selection_failures
        )
        raise ValueError(details)
    elif len(bundle.documents) > 1:
        raise ValueError(f"多文档内未能独立验证 {period} 期目标")
    elif bundle.documents:
        records = registry.parse(bundle.documents[0], site)
        selected = select_record(records, period, site)
    else:
        raise ValueError("未抓到有效候选")
    if not validate_selected_record(selected, period):
        raise ValueError("期数、生肖唯一性或原始位置不合法")
    return records, selected


def scrape_site(
    site: Site,
    period: int,
    timeout: int,
    context: FetchContext,
    registry: ParserRegistry,
    adaptive_manager: adaptive.AdaptiveManager | None = None,
) -> Result:
    source = ""
    bundle = DocumentBundle(())
    records: list[Record] = []
    try:
        bundle = fetch_payload(
            site,
            period,
            timeout,
            context,
            lambda source, target, issue: bool(registry.parse(source, target)) if issue is None else any(
                record.period == issue for record in registry.parse(source, target)
            ),
        )
        source = bundle.combined_source
        records, selected = parse_bundle_for_period(bundle, site, period, registry)
    except (requests.RequestException, ValueError, LookupError) as exc:
        if (
            adaptive_manager is None
            or not adaptive_manager.enabled
            or site.payload in {"admin_article_api", "tuku_user_forums"}
            or not adaptive.should_attempt_recovery(site, adaptive.classify_failure(exc), str(exc))
        ):
            return Result(site, None, format_failure(exc))
        try:
            recovery = adaptive_manager.recover(
                site,
                period,
                timeout,
                bundle,
                context.get_text,
                registry.parse,
                select_record,
                lambda record: validate_selected_record(record, period),
                decode_strdecode_blocks,
                render_browser_text,
            )
        except Exception as recovery_exc:
            return Result(site, None, f"{format_failure(exc)}；{recovery_exc}")
        if adaptive_manager.mode == "shadow":
            return Result(
                site,
                None,
                f"{format_failure(exc)}；自适应影子验证已找到："
                f"{recovery.record.zodiac}@位置{recovery.record.position}，未写入正式结果",
            )
        return Result(site, recovery.record)
    if adaptive_manager is not None and adaptive_manager.enabled:
        try:
            adaptive_manager.observe_primary_success(site, source, records, selected, registry.parse, select_record)
        except (RuntimeError, ValueError):
            pass
    return Result(site, selected)


def scrape_sites(
    sites: list[Site],
    period: int,
    timeout: int,
    workers: int,
    *,
    context: FetchContext | None = None,
    registry: ParserRegistry | None = None,
    progress: ProgressSink | None = print,
    adaptive_manager: adaptive.AdaptiveManager | None = None,
) -> list[Result]:
    if not sites:
        return []
    active_context = context or FetchContext()
    active_registry = registry or ParserRegistry.bind_sites(sites)
    results: list[Result | None] = [None] * len(sites)
    started_at = time.monotonic()
    success = failed = done = 0
    scheduled_indices = range(len(sites))
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(sites)))) as executor:
        futures = {
            executor.submit(
                scrape_site,
                sites[index],
                period,
                timeout,
                active_context,
                active_registry,
                adaptive_manager,
            ): index
            for index in scheduled_indices
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = Result(sites[index], None, format_failure(exc))
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
