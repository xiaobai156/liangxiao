# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse

import requests

from cache.contracts import CACHE_POSITION_KIND, validate_cache_position_contract
from domain.identity import detail_record_identity
from domain.models import DocumentBundle, PayloadDocument, Record
from structure_profiles import (
    ProfileStoreError,
    StructureProfileStore,
    element_to_profile,
    site_anchors,
    structural_fingerprint,
)
from validation.boundaries import (
    expected_record_id,
    validate_bundle_boundaries,
    validate_document_relationships,
)
from validation.records import merge_equivalent_records, normalize_zodiac

try:
    from scrapling import Selector
except Exception as exc:  # pragma: no cover - covered through dependency_error
    Selector = None
    _SCRAPLING_IMPORT_ERROR: Exception | None = exc
else:
    _SCRAPLING_IMPORT_ERROR = None


TARGET_IDENTIFIER = "dedicated-material-block"
FAILURE_NETWORK_TIMEOUT = "网络请求超时"
FAILURE_SSL = "SSL连接失败"
FAILURE_HTTP = "HTTP请求失败"
FAILURE_NOT_PUBLISHED = "内容尚未发布"
FAILURE_STRUCTURE_CHANGED = "页面结构已变化"
FAILURE_ANCHOR_NOT_FOUND = "未找到专属锚点"
FAILURE_TARGET_NOT_FOUND = "未找到指定目标"
FAILURE_FIELD_VALIDATION = "字段校验未通过"
FAILURE_DATA_CONFLICT = "数据存在冲突"
FAILURE_ADAPTIVE_REJECTED = "自适应匹配被拒绝"
FAILURE_BROWSER = "浏览器渲染失败"
FAILURE_PROFILE = "结构档案不可用"

STRUCTURAL_FAILURES = {
    FAILURE_STRUCTURE_CHANGED,
    FAILURE_ANCHOR_NOT_FOUND,
}


class AdaptiveRecoveryError(ValueError):
    def __init__(self, category: str, detail: str):
        self.category = category
        self.detail = detail
        super().__init__(f"{category}：{detail}")


@dataclass(frozen=True)
class AdaptiveRecovery:
    records: tuple[Any, ...]
    record: Any
    source_label: str
    source_url: str
    documents_checked: int


@dataclass(frozen=True)
class _Candidate:
    document: PayloadDocument
    element: dict[str, Any] | None = None

    @property
    def label(self) -> str:
        return self.document.label

    @property
    def url(self) -> str:
        return self.document.url

    @property
    def source(self) -> str:
        return self.document.source


class _TrustedElementStorage:
    def __init__(self, store: StructureProfileStore, site: object):
        self.store = store
        self.site = site

    def retrieve(self, identifier: str) -> dict[str, Any] | None:
        return self.store.get_trusted_element(self.site, identifier)

    def save(self, element: Any, identifier: str) -> None:
        raise RuntimeError("自适应定位阶段禁止直接覆盖可信结构")


def dependency_error() -> str:
    if _SCRAPLING_IMPORT_ERROR is None:
        return ""
    return f"Scrapling不可用：{_SCRAPLING_IMPORT_ERROR}"


def classify_failure(exc: BaseException) -> str:
    if isinstance(exc, requests.exceptions.Timeout):
        return FAILURE_NETWORK_TIMEOUT
    if isinstance(exc, requests.exceptions.SSLError):
        return FAILURE_SSL
    if isinstance(exc, requests.exceptions.HTTPError):
        return FAILURE_HTTP
    detail = str(exc)
    if "浏览器渲染" in detail:
        return FAILURE_BROWSER
    request_detail = re.sub(r"https?://\S+", " ", detail, flags=re.I)
    if isinstance(exc, requests.RequestException) and re.search(
        r"(?i)(?:ssl(?:error|eof|\s*[:：]|\s*(?:兼容|连接|握手|证书|失败))|"
        r"tls(?:v?\d(?:\.\d+)?|\s*[:：]|\s+(?:连接|握手|失败))|"
        r"证书(?:错误|失败|无效)|handshake)",
        request_detail,
    ):
        return FAILURE_SSL
    if re.search(r"(?:top|bottom)\s*(?:列表)?候选内未找到\s*\d{3,4}\s*期", detail):
        return FAILURE_NOT_PUBLISHED
    if "未找到" in detail and "锚点" in detail:
        return FAILURE_ANCHOR_NOT_FOUND
    if any(marker in detail for marker in ("目标脚本", "页面结构", "详情页脚本")):
        return FAILURE_STRUCTURE_CHANGED
    if any(marker in detail for marker in ("未抓到有效候选", "未找到指定期数", "未找到对应后台文章")):
        return FAILURE_TARGET_NOT_FOUND
    if "冲突" in detail:
        return FAILURE_DATA_CONFLICT
    if isinstance(exc, requests.RequestException):
        return FAILURE_HTTP
    return FAILURE_FIELD_VALIDATION


def should_attempt_recovery(site: object, category: str, detail: str) -> bool:
    if category not in STRUCTURAL_FAILURES:
        return False
    payload = str(getattr(site, "payload", ""))
    if payload == "admin_article_api" and "接口返回内容内未找到对应后台文章" in detail:
        return False
    return True


def format_failure(category: str, detail: str) -> str:
    prefix = f"{category}："
    return detail if detail.startswith(prefix) else f"{prefix}{detail}"


def _record_key(record: Any) -> tuple[int, str, int]:
    return (
        int(getattr(record, "period")),
        str(getattr(record, "zodiac")),
        int(getattr(record, "position", -1)),
    )


def _records_signature(records: Iterable[Any]) -> tuple[tuple[int, str], ...]:
    return tuple((int(getattr(record, "period")), str(getattr(record, "zodiac"))) for record in records)


def _selector_source(element: Any) -> str:
    return str(getattr(element, "html_content", "") or "")


def _as_elements(value: Any) -> list[Any]:
    if value is None:
        return []
    if hasattr(value, "_root"):
        return [value]
    try:
        return list(value)
    except TypeError:
        return []


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _as_document_bundle(documents: DocumentBundle, site: object) -> DocumentBundle:
    if not isinstance(documents, DocumentBundle):
        raise ValueError("自适应恢复必须使用DocumentBundle，禁止使用未绑定边界的旧文档三元组")
    return documents


class AdaptiveManager:
    MODES = {"off", "shadow", "fallback"}

    def __init__(
        self,
        profile_path: Path,
        mode: str = "fallback",
        similarity: int = 70,
        max_documents: int = 16,
        browser_workers: int = 2,
        history_cache_path: Path | None = None,
        require_history: bool = False,
    ):
        if mode not in self.MODES:
            raise ValueError(f"未知自适应模式：{mode}")
        if not 40 <= similarity <= 100:
            raise ValueError("自适应相似度必须为40-100")
        self.store = StructureProfileStore(Path(profile_path))
        self.mode = mode
        self.similarity = similarity
        self.max_documents = max(1, max_documents)
        self.history_cache_path = Path(history_cache_path) if history_cache_path else None
        self.require_history = require_history
        self.validation_token = uuid.uuid4().hex
        self._browser_slots = BoundedSemaphore(max(1, browser_workers))

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    @property
    def available(self) -> bool:
        return Selector is not None

    def register_sites(self, sites: Iterable[object]) -> None:
        if self.enabled:
            self.store.register_sites(sites)

    def _selector(self, site: object, source: str, adaptive: bool = False) -> Any:
        if Selector is None:
            raise AdaptiveRecoveryError(FAILURE_ADAPTIVE_REJECTED, dependency_error())
        storage = _TrustedElementStorage(self.store, site) if adaptive else None
        return Selector(
            source,
            url=str(getattr(site, "url", "")),
            adaptive=adaptive,
            _storage=storage,
        )

    def _find_verified_element(
        self,
        site: object,
        source: str,
        records: list[Any],
        parse_records: Callable[[str, object], list[Any]],
    ) -> Any | None:
        if not records or Selector is None or "<" not in source:
            return None
        expected = _records_signature(records)
        selector = self._selector(site, source)
        candidates: list[tuple[int, Any]] = []
        seen: set[int] = set()
        for anchor in site_anchors(site):
            try:
                matches = selector.find_by_text(anchor, first_match=False, partial=True)
            except Exception:
                continue
            for match in _as_elements(matches):
                node = match
                for _ in range(7):
                    if node is None:
                        break
                    root = getattr(node, "_root", None)
                    identity = id(root)
                    tag = str(getattr(root, "tag", "")).lower()
                    if identity not in seen and tag not in {"html", "body"}:
                        seen.add(identity)
                        fragment = _selector_source(node)
                        if fragment and len(fragment) <= 2_000_000:
                            try:
                                parsed = parse_records(fragment, site)
                            except Exception:
                                parsed = []
                            if _records_signature(parsed) == expected:
                                candidates.append((len(fragment), node))
                    node = getattr(node, "parent", None)
        return min(candidates, key=lambda value: value[0])[1] if candidates else None

    def observe_primary_success(
        self,
        site: object,
        source: str,
        records: list[Any],
        selected: Any,
        parse_records: Callable[[str, object], list[Any]],
        select_record: Callable[[list[Any], int, object], Any],
    ) -> None:
        if not self.enabled or not self.available:
            return
        if int(getattr(selected, "period")) <= 0:
            return
        element = self._find_verified_element(site, source, records, parse_records)
        element_data = element_to_profile(element) if element is not None else None
        fingerprint_source = _selector_source(element) if element is not None else source
        self.store.mark_primary_success(
            site,
            structural_fingerprint(fingerprint_source),
            element_data,
            TARGET_IDENTIFIER,
            (str(getattr(site, "url", "")),),
        )

    def _adaptive_candidates(self, site: object, document: PayloadDocument) -> list[_Candidate]:
        if not self.available or not self.store.get_trusted_element(site, TARGET_IDENTIFIER):
            return []
        selector = self._selector(site, document.source, adaptive=True)
        try:
            relocated = selector.xpath(
                "//*[false()]",
                identifier=TARGET_IDENTIFIER,
                adaptive=True,
                auto_save=False,
                percentage=self.similarity,
            )
        except Exception:
            return []
        result: list[_Candidate] = []
        for element in _as_elements(relocated):
            fragment = _selector_source(element)
            if fragment:
                result.append(
                    _Candidate(
                        PayloadDocument(
                            "Scrapling自适应区域",
                            document.url,
                            fragment,
                            record_id=document.record_id,
                            record_path=document.record_path,
                            record_count=document.record_count,
                            parent_url=document.url,
                            link_reference=f"#{TARGET_IDENTIFIER}",
                        ),
                        element_to_profile(element),
                    )
                )
        return result

    def adaptive_candidate_sources(self, site: object, source: str) -> list[str]:
        document = PayloadDocument(
            "原始页面",
            str(getattr(site, "url", "")),
            source,
            record_id=detail_record_identity(str(getattr(site, "url", ""))) or expected_record_id(site),
        )
        return [candidate.source for candidate in self._adaptive_candidates(site, document)]

    def _matches_trusted_history(self, site: object, records: Iterable[Any]) -> tuple[bool, str]:
        if not self.require_history:
            return True, ""
        if self.history_cache_path is None or not self.history_cache_path.is_file():
            return False, "recent_10_cache.json不存在"
        try:
            cache = json.loads(self.history_cache_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"recent_10_cache.json无法读取：{exc}"
        if not isinstance(cache, dict):
            return False, "recent_10_cache.json结构无效"
        try:
            validate_cache_position_contract(cache)
        except ValueError as exc:
            return False, str(exc)

        issues = cache.get("issues")
        sites = cache.get("sites")
        if not isinstance(issues, list) or not isinstance(sites, list) or not issues:
            return False, "recent_10_cache.json结构无效"
        trusted_periods: list[int] = []
        for issue in issues[:2]:
            try:
                trusted_periods.append(int(issue))
            except (TypeError, ValueError):
                return False, "recent_10_cache.json最新两期无效"

        identity = (
            str(getattr(site, "name", "")),
            str(getattr(site, "url", "")),
            str(getattr(site, "pick", "")),
        )
        matching_sites = [
            entry
            for entry in sites
            if isinstance(entry, dict)
            and (
                str(entry.get("name", "")),
                str(entry.get("url", "")),
                str(entry.get("pick", "")),
            )
            == identity
        ]
        if len(matching_sites) != 1:
            if not matching_sites:
                return False, "缓存中不存在该站点的同名、同网址、同方向基准"
            return False, "缓存中存在多个同名、同网址、同方向基准"
        cached_site = matching_sites[0]
        values = cached_site.get("values")
        positions = cached_site.get("positions")
        if not isinstance(values, dict) or not isinstance(positions, dict):
            return False, "缓存站点缺少生肖值或原始位置"

        direction_window = records[:3] if str(getattr(site, "pick", "")) == "top" else records[-3:]
        records_by_period: dict[int, dict[str, set[int]]] = {}
        for record in direction_window:
            try:
                period = int(getattr(record, "period"))
                position = int(getattr(record, "position", -1))
                zodiac = normalize_zodiac(str(getattr(record, "zodiac", "")))
            except (AttributeError, TypeError, ValueError):
                continue
            if str(getattr(record, "position_kind", "")) != CACHE_POSITION_KIND:
                return False, f"候选记录位置语义不兼容：{period}期position_kind缺失或不匹配"
            source_positions = getattr(record, "source_positions", ()) or (position,)
            valid_positions: set[int] = set()
            for value in source_positions:
                try:
                    parsed_position = int(value)
                except (TypeError, ValueError):
                    continue
                if parsed_position >= 0:
                    valid_positions.add(parsed_position)
            if position >= 0:
                valid_positions.add(position)
            records_by_period.setdefault(period, {}).setdefault(zodiac, set()).update(valid_positions)

        matched = False
        for period in trusted_periods:
            expected_value = values.get(str(period))
            expected_position = positions.get(str(period))
            if expected_value is None or expected_position is None:
                continue
            try:
                expected_position = int(expected_position)
            except (TypeError, ValueError):
                continue
            if expected_position < 0:
                continue
            candidates = records_by_period.get(period, {})
            if len(candidates) > 1:
                details = "、".join(
                    f"{zodiac}@位置{','.join(str(value) for value in sorted(positions))}"
                    for zodiac, positions in sorted(candidates.items())
                )
                return False, f"缓存最新两期对应记录存在冲突：{period}期 {details}"
            if not candidates:
                continue
            actual_value, actual_positions = next(iter(candidates.items()))
            if normalize_zodiac(str(expected_value)) != actual_value or expected_position not in actual_positions:
                return (
                    False,
                    f"缓存最新两期对应记录不一致：{period}期缓存为"
                    f"{expected_value}@位置{expected_position}，候选为"
                    f"{actual_value}@位置{','.join(str(value) for value in sorted(actual_positions))}",
                )
            matched = True
        if matched:
            return True, ""
        return False, "缓存最新两期内没有生肖值与原始位置同时一致的记录"
    @staticmethod
    def _attribute_values(selector: Any, css: str) -> list[str]:
        try:
            values = selector.css(css)
            if hasattr(values, "getall"):
                return [str(value) for value in values.getall() if str(value)]
            return [str(value) for value in values if str(value)]
        except Exception:
            return []

    def collect_documents(
        self,
        site: object,
        timeout: int,
        primary_bundle: DocumentBundle | str,
        fetch_text: Callable[[str, int], str],
        decode_source: Callable[[str], str] | None = None,
        render_text: Callable[[str, int], str] | None = None,
    ) -> DocumentBundle:
        documents: list[PayloadDocument] = []
        seen_urls: set[str] = set()
        seen_documents: set[tuple[str, str, str]] = set()

        def add(document: PayloadDocument) -> bool:
            if not document.source or len(documents) >= self.max_documents:
                return False
            digest = hashlib.sha256(document.source.encode("utf-8", errors="replace")).hexdigest()
            signature = (document.url, document.record_id, digest)
            if signature in seen_documents:
                return False
            seen_documents.add(signature)
            documents.append(document)
            if document.url:
                seen_urls.add(document.url)
            return True

        page_url = str(getattr(site, "url", ""))
        root_record_id = detail_record_identity(page_url) or expected_record_id(site)
        if isinstance(primary_bundle, DocumentBundle):
            for document in primary_bundle.documents:
                add(document)
        elif primary_bundle:
            add(PayloadDocument("原始页面", page_url, primary_bundle, record_id=root_record_id))
        if not documents:
            page_source = fetch_text(page_url, timeout)
            add(PayloadDocument("原始页面", page_url, page_source, record_id=root_record_id))

        discovery_documents = [document for document in documents if document.url == page_url]
        if not discovery_documents and documents:
            discovery_documents = [documents[0]]

        pending: list[tuple[str, str, PayloadDocument, str]] = []
        for parent in discovery_documents:
            if not self.available or "<" not in parent.source:
                continue
            selector = self._selector(site, parent.source)
            discovered = (
                ("脚本", self._attribute_values(selector, "script::attr(src)")),
                ("iframe", self._attribute_values(selector, "iframe::attr(src)")),
            )
            for label, values in discovered:
                for value in values:
                    resolved = urljoin(parent.url or page_url, value)
                    if _is_http_url(resolved):
                        pending.append((label, resolved, parent, value))

        for label, url, parent, reference in pending:
            if len(documents) >= self.max_documents or url in seen_urls:
                continue
            seen_urls.add(url)
            child_record_id = detail_record_identity(url)
            if parent.record_id and child_record_id and parent.record_id != child_record_id:
                continue
            record_id = child_record_id or parent.record_id
            try:
                source = fetch_text(url, timeout)
            except requests.RequestException:
                continue
            add(
                PayloadDocument(
                    label,
                    url,
                    source,
                    record_id=record_id,
                    parent_url=parent.url,
                    link_reference=reference,
                )
            )
            if decode_source is not None:
                decoded = decode_source(source)
                add(
                    PayloadDocument(
                        f"{label}解码内容",
                        url,
                        decoded,
                        record_id=record_id,
                        parent_url=url,
                        link_reference="decode",
                    )
                )

        browser_error = ""
        scan_complete = primary_bundle.scan_complete if isinstance(primary_bundle, DocumentBundle) else True
        dynamic_path = any(marker in page_url.lower() for marker in ("/article/admin/", "/article/manager/"))
        if render_text is not None and dynamic_path and len(documents) < self.max_documents:
            try:
                with self._browser_slots:
                    rendered = render_text(page_url, timeout)
                add(
                    PayloadDocument(
                        "浏览器渲染页面",
                        page_url,
                        rendered,
                        record_id=root_record_id,
                        parent_url=page_url,
                        link_reference="browser-render",
                    )
                )
            except Exception as exc:
                browser_error = str(exc) or exc.__class__.__name__
        if len(documents) >= self.max_documents:
            scan_complete = False
        bundle = DocumentBundle(tuple(documents), browser_error, scan_complete)
        validate_bundle_boundaries(bundle, site)
        validate_document_relationships(bundle)
        return bundle

    def recover_from_documents(
        self,
        site: object,
        period: int,
        documents: DocumentBundle | Iterable[Any],
        parse_records: Callable[[PayloadDocument, object], list[Any]],
        select_record: Callable[[list[Any], int, object], Any],
        validate_record: Callable[[Any], bool],
    ) -> AdaptiveRecovery:
        if not self.available:
            raise AdaptiveRecoveryError(FAILURE_ADAPTIVE_REJECTED, dependency_error())
        bundle = _as_document_bundle(documents, site)
        validate_bundle_boundaries(bundle, site)
        validate_document_relationships(bundle)
        source_documents = list(bundle.documents)
        source_window_signatures: dict[int, set[str]] = {}
        source_window_conflicts: list[str] = []
        for document in source_documents:
            if not document.source:
                continue
            try:
                source_records = parse_records(document, site)
            except (ValueError, LookupError) as exc:
                if "数据存在冲突" in str(exc):
                    source_window_conflicts.append(f"{document.label}：{exc}")
                continue
            source_window = (
                source_records[:3]
                if str(getattr(site, "pick", "")) == "top"
                else source_records[-3:]
            )
            local_signatures: dict[int, set[str]] = {}
            for record in source_window:
                record_period, record_zodiac, _record_position = _record_key(record)
                signature = normalize_zodiac(record_zodiac)
                local_signatures.setdefault(record_period, set()).add(signature)
                source_window_signatures.setdefault(record_period, set()).add(signature)
            for record_period, signatures in local_signatures.items():
                if len(signatures) > 1:
                    details = "、".join(sorted(signatures))
                    source_window_conflicts.append(f"{document.label}：{record_period}期 {details}")
        for record_period, signatures in source_window_signatures.items():
            if len(signatures) > 1:
                details = "、".join(sorted(signatures))
                source_window_conflicts.append(f"多文档：{record_period}期 {details}")
        if source_window_conflicts:
            details = "；".join(source_window_conflicts)
            self.store.mark_failure(site, FAILURE_DATA_CONFLICT, details)
            raise AdaptiveRecoveryError(FAILURE_DATA_CONFLICT, f"数据存在冲突：{details}")

        candidates: list[_Candidate] = [_Candidate(document) for document in source_documents if document.source]
        for document in source_documents:
            for relocated in self._adaptive_candidates(site, document):
                candidates.append(
                    _Candidate(
                        PayloadDocument(
                            f"{document.label}/{relocated.label}",
                            relocated.url,
                            relocated.source,
                            record_id=relocated.document.record_id,
                            record_path=relocated.document.record_path,
                            record_count=relocated.document.record_count,
                            parent_url=relocated.document.parent_url,
                            link_reference=relocated.document.link_reference,
                        ),
                        relocated.element,
                    )
                )

        seen_sources: set[tuple[str, str]] = set()
        successes: dict[tuple[int, str], list[tuple[_Candidate, list[Any], Any]]] = {}
        window_conflicts: list[tuple[str, str]] = []
        for candidate in candidates:
            digest = hashlib.sha256(candidate.source.encode("utf-8", errors="replace")).hexdigest()
            source_signature = (candidate.url, digest)
            if source_signature in seen_sources:
                continue
            seen_sources.add(source_signature)
            try:
                records = parse_records(candidate.document, site)
                selected = select_record(records, period, site)
            except (ValueError, LookupError) as exc:
                if "数据存在冲突" in str(exc):
                    window_conflicts.append((candidate.label, str(exc)))
                continue
            if int(getattr(selected, "period", -1)) != period or not validate_record(selected):
                continue
            selected_period, selected_zodiac, _selected_position = _record_key(selected)
            successes.setdefault(
                (selected_period, normalize_zodiac(selected_zodiac)),
                [],
            ).append((candidate, records, selected))

        if window_conflicts:
            details = "；".join(f"{label}：{detail}" for label, detail in window_conflicts)
            self.store.mark_failure(site, FAILURE_DATA_CONFLICT, details)
            raise AdaptiveRecoveryError(FAILURE_DATA_CONFLICT, details)
        if not successes:
            browser_error = bundle.browser_error
            category = FAILURE_BROWSER if browser_error else FAILURE_ADAPTIVE_REJECTED
            detail = browser_error or f"检查{len(seen_sources)}份文档后仍未找到指定目标"
            self.store.mark_failure(site, category, detail)
            raise AdaptiveRecoveryError(category, detail)
        if len(successes) > 1:
            details = "、".join(key[1] for key in sorted(successes))
            self.store.mark_failure(site, FAILURE_DATA_CONFLICT, details)
            raise AdaptiveRecoveryError(FAILURE_DATA_CONFLICT, f"同一目标出现不同结果：{details}")

        matches = next(iter(successes.values()))
        history_signatures: dict[int, set[str]] = {}
        for _candidate, candidate_records, _selected in matches:
            candidate_window = (
                candidate_records[:3]
                if str(getattr(site, "pick", "")) == "top"
                else candidate_records[-3:]
            )
            for record in candidate_window:
                record_period, record_zodiac, _record_position = _record_key(record)
                history_signatures.setdefault(record_period, set()).add(normalize_zodiac(record_zodiac))
        historical_conflicts = {
            record_period: signatures
            for record_period, signatures in history_signatures.items()
            if len(signatures) > 1
        }
        if historical_conflicts:
            details = "；".join(
                f"{record_period}期 "
                + "、".join(sorted(signatures))
                for record_period, signatures in sorted(historical_conflicts.items())
            )
            self.store.mark_failure(site, FAILURE_DATA_CONFLICT, details)
            raise AdaptiveRecoveryError(FAILURE_DATA_CONFLICT, f"多文档历史记录冲突：{details}")

        trusted_matches: list[tuple[_Candidate, list[Any], Any]] = []
        history_failures: list[str] = []
        for match in matches:
            history_ok, history_detail = self._matches_trusted_history(site, match[1])
            if history_ok:
                trusted_matches.append(match)
            elif history_detail not in history_failures:
                history_failures.append(history_detail)
        if not trusted_matches:
            history_detail = "；".join(history_failures) or "没有候选通过历史可信数据校验"
            self.store.mark_failure(site, FAILURE_ADAPTIVE_REJECTED, history_detail)
            raise AdaptiveRecoveryError(
                FAILURE_ADAPTIVE_REJECTED,
                f"历史可信数据校验未通过：{history_detail}",
            )

        candidate, records, selected = max(
            trusted_matches,
            key=lambda value: (value[0].element is not None, len(value[1])),
        )
        equivalent_selected = [match[2] for match in trusted_matches]
        if equivalent_selected and all(isinstance(record, Record) for record in equivalent_selected):
            selected = merge_equivalent_records(
                equivalent_selected,
                prefer_last=str(getattr(site, "pick", "")) == "bottom",
            )
        self.store.mark_adaptive_success(
            site,
            structural_fingerprint(candidate.source),
            candidate.element,
            TARGET_IDENTIFIER,
            (candidate.url,),
            validation_token=self.validation_token,
        )
        return AdaptiveRecovery(
            tuple(records),
            selected,
            candidate.label,
            candidate.url,
            len(seen_sources),
        )

    def recover(
        self,
        site: object,
        period: int,
        timeout: int,
        primary_bundle: DocumentBundle | str,
        fetch_text: Callable[[str, int], str],
        parse_records: Callable[[PayloadDocument, object], list[Any]],
        select_record: Callable[[list[Any], int, object], Any],
        validate_record: Callable[[Any], bool],
        decode_source: Callable[[str], str] | None = None,
        render_text: Callable[[str, int], str] | None = None,
    ) -> AdaptiveRecovery:
        documents = self.collect_documents(
            site,
            timeout,
            primary_bundle,
            fetch_text,
            decode_source,
            render_text,
        )
        return self.recover_from_documents(
            site,
            period,
            documents,
            parse_records,
            select_record,
            validate_record,
        )







