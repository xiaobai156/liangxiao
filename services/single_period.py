from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from domain.identity import detail_record_identity

import requests

from diagnostics.run_report import format_failure, progress_summary_line
from domain.errors import ErrorCategory, ScrapeFailure
from domain.models import (
    DOCUMENT_BOUNDARY,
    DocumentBundle,
    PayloadDocument,
    Record,
    RecordEvidence,
    Result,
    Site,
)
from fetching.client import FetchContext, get_site_text, get_site_rendered
from fetching.page import (
    fetch_payload,
    fetch_topic_detail_documents,
    make_document_bundle,
    next_topic_listing_url,
    topic_listing_links,
    visible_text,
)
from parsers.registry import ParserRegistry
from validation.boundaries import (
    linked_document_is_authorized,
    validate_bundle_boundaries,
    validate_document_relationships,
)
from validation.conflicts import validate_document_windows
from validation.direction import direction_window, select_record
from validation.records import normalize_zodiac, record_value_signature, validate_selected_record

ProgressSink = Callable[[str], None]


def source_has_target(
    registry: ParserRegistry,
    source: str,
    site: Site,
    period: int | None,
) -> bool:
    try:
        candidates = registry.parse(source, site)
        if period is None:
            return bool(candidates)
        selected = select_record(candidates, period, site)
        return validate_selected_record(selected, period)
    except (ValueError, LookupError):
        return False


def _topic_list_item_matches(title: str, site: Site) -> bool:
    if site.title:
        try:
            return bool(re.search(site.title, title, flags=re.I))
        except re.error:
            return False
    return (
        site.name in title
        and "绝杀" in title
        and any(marker in title for marker in ("二肖", "两肖", "②肖", "2肖", "２肖"))
    )


@dataclass(frozen=True, slots=True)
class TopicListCandidate:
    index: int
    period: int
    url: str
    title: str
    record_id: str


def fetch_topic_list_detail_for_period(
    site: Site,
    period: int | None,
    timeout: int,
    context: FetchContext,
) -> DocumentBundle:
    if period is None:
        raise ValueError("列表详情站必须指定期数")
    visited_listing_urls: set[str] = set()
    entries: list[TopicListCandidate] = []
    seen_urls: set[str] = set()
    listing_url = site.url
    for _ in range(32):
        if listing_url in visited_listing_urls:
            break
        visited_listing_urls.add(listing_url)
        listing = get_site_text(context, listing_url, timeout, site)
        next_url = next_topic_listing_url(listing, listing_url)
        for detail_url, title in topic_listing_links(listing, listing_url):
            period_match = re.search(r"(?<!\d)([0-9]{3})\s*期", title)
            if not period_match or not _topic_list_item_matches(title, site):
                continue
            candidate_period = int(period_match.group(1))
            if not 1 <= candidate_period <= 365:
                continue
            if detail_url in seen_urls:
                continue
            seen_urls.add(detail_url)
            entries.append(TopicListCandidate(
                len(entries), candidate_period, detail_url, title,
                detail_record_identity(detail_url),
            ))
        if not next_url or next_url in visited_listing_urls:
            break
        listing_url = next_url
    else:
        raise ValueError("列表分页超过扫描上限")

    window = entries[:3] if site.pick == "top" else entries[-3:]
    targets = [item for item in window if item.period == period]
    if not targets:
        raise ValueError(f"{site.pick} 列表候选内未找到 {period} 期")

    documents: list[PayloadDocument] = []
    for candidate in targets:
        detail_url = candidate.url
        detail_documents = fetch_topic_detail_documents(detail_url, timeout, context, site)
        detail_page = detail_documents[0]
        matching_documents: list[PayloadDocument] = []
        detail_text = visible_text(detail_page.source)
        if re.search(rf"{period}\s*期", detail_text) and _topic_list_item_matches(detail_text, site):
            matching_documents.append(detail_page)
        for document in detail_documents[1:]:
            text = visible_text(document.source)
            if _topic_list_item_matches(text, site) and re.search(rf"{period}\s*期", text):
                matching_documents.append(document)
        if not matching_documents:
            raise ValueError("详情页脚本内未找到指定期数或专属关键词")
        if detail_page not in matching_documents:
            matching_documents.insert(0, detail_page)
        documents.extend(matching_documents)
    return make_document_bundle(documents)


def fetch_payload_for_period(
    site: Site,
    period: int | None,
    timeout: int,
    context: FetchContext,
    registry: ParserRegistry,
) -> DocumentBundle:
    if site.payload == "topic_list_detail":
        return fetch_topic_list_detail_for_period(site, period, timeout, context)
    bundle = fetch_payload(
        site,
        period,
        timeout,
        context,
        lambda source, target, issue: source_has_target(registry, source, target, issue),
    )
    if site.parser != "xiaosuan_bottom_two_zodiac":
        return bundle
    if period is None or len(bundle.documents) != 1:
        raise ValueError("小算算必须指定期数并使用唯一用户接口文档")
    document = bundle.documents[0]
    try:
        items = json.loads(document.source)
    except json.JSONDecodeError as exc:
        raise ValueError("小算算用户接口JSON无效") from exc
    draw_matches = [
        item
        for item in items
        if isinstance(item, dict)
        if item.get("draw") == period
    ]
    if not draw_matches:
        raise ScrapeFailure(
            ErrorCategory.CONTENT_NOT_PUBLISHED,
            f"用户接口内未找到{period}期帖子",
        )
    if len(draw_matches) > 1:
        raise ScrapeFailure(
            ErrorCategory.DATA_CONFLICT,
            f"用户接口内{period}期出现{len(draw_matches)}篇帖子",
        )
    matches = [
        item
        for item in draw_matches
        if item.get("status") == "published"
        and str(item.get("topic") or "").strip() == "杀肖"
        and isinstance(item.get("user"), dict)
        and str(item["user"].get("nickname") or "") == site.name
    ]
    if not matches:
        raise ScrapeFailure(
            ErrorCategory.TARGET_MISSING,
            f"{period}期帖子作者、栏目或发布状态不匹配",
        )
    article_id = matches[0].get("id")
    if not isinstance(article_id, int) or article_id <= 0:
        raise ScrapeFailure(
            ErrorCategory.FIELD_VALIDATION,
            f"{period}期目标帖子缺少文章ID",
        )
    return replace(
        bundle,
        documents=(
            replace(
                document,
                source=json.dumps(matches, ensure_ascii=False),
                record_path=f"root[id={article_id}]",
                record_count=1,
            ),
        ),
    )


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
    evidence: list[RecordEvidence] = []

    def collect_evidence(document, records, selected):
        index = next((index for index, item in enumerate(direction_window(records, site))
                      if item.period == selected.period and item.position == selected.position), -1)
        evidence.append(RecordEvidence(
            document.label, document.url, selected.record_id or document.record_id,
            selected.position, index, selected.block_id, selected.anchor_text,
            document.parent_url, document.link_reference,
            document.own_record_id, document.parent_record_id,
        ))

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
        collect_evidence(document, records, selected)
        successful.append((document.label, records, selected))
    if observed:
        validate_document_windows(observed, site, "多文档", period=period)

    pair_observed: list[tuple[str, list[Record]]] = []
    pair_successes: list[tuple[str, list[Record], Record]] = []
    if len(bundle.documents) > 1:
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
                    own_record_id=body.own_record_id,
                    parent_record_id=anchor.record_id,
                    identity_inherited=body.identity_inherited,
                )
                pair_site = site if site.linked_document_pattern else replace(
                    site,
                    linked_document_pattern=re.escape(body.url),
                )
                records = registry.parse(paired, pair_site)
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
                collect_evidence(paired, records, selected)
                pair_successes.append((paired.label, records, selected))
        if pair_observed:
            validate_document_windows(pair_observed, site, "标题正文文档", period=period)

    def carrier_key(item):
        label, _records, record = item
        # This is only the carrier for an already-agreed value. All proofs remain.
        return (record.document_url != site.url, record.document_url, label, record.position)

    if successful:
        independent = {record_value_signature(selected) for _label, _records, selected in successful}
        paired = {record_value_signature(selected) for _label, _records, selected in pair_successes}
        if paired and independent != paired:
            details = "、".join(sorted(independent | paired))
            raise ValueError(f"数据存在冲突：{period}期独立文档与标题正文结果不同：{details}")
        _label, records, selected = min(successful, key=carrier_key)
    elif pair_successes:
        _label, records, selected = min(pair_successes, key=carrier_key)
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
    ids = {item.record_id for item in evidence if item.record_id}
    if len(ids) > 1:
        raise ValueError(f"数据存在冲突：同一期结果属于不同记录身份：{','.join(sorted(ids))}")
    selected = replace(selected, zodiac=normalize_zodiac(selected.zodiac),
                       evidence=tuple(sorted(set(evidence), key=lambda item: (
                           item.document_url, item.document_label, item.position, item.record_id
                       ))))
    return records, selected


def scrape_site(
    site: Site,
    period: int,
    timeout: int,
    context: FetchContext,
    registry: ParserRegistry,
) -> Result:
    try:
        bundle = fetch_payload_for_period(site, period, timeout, context, registry)
        _records, selected = parse_bundle_for_period(bundle, site, period, registry)
    except (requests.RequestException, ValueError, LookupError) as exc:
        return Result(site, None, format_failure(exc))
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
) -> list[Result]:
    if not sites:
        return []
    active_context = context if context is not None else FetchContext()
    owns_context = context is None
    active_registry = registry or ParserRegistry.bind_sites(sites)
    results: list[Result | None] = [None] * len(sites)
    started_at = time.monotonic()
    success = failed = done = 0
    scheduled_indices = range(len(sites))
    try:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(sites)))) as executor:
            futures = {
                executor.submit(
                    scrape_site,
                    sites[index],
                    period,
                    timeout,
                    active_context,
                    active_registry,
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
    finally:
        if owns_context:
            active_context.close()
