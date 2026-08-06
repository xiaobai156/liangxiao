from __future__ import annotations

import base64
import hashlib
import html
import json
import re
from urllib.parse import urljoin, urlparse

import requests

from domain.identity import detail_record_identity
from domain.models import DocumentBundle, PayloadDocument, Site
from fetching.client import FetchContext, fetch_curl_text
from fetching.dynamic_article import TargetProbe, admin_article_id, fetch_admin_article_payload
from fetching.user_forum import filter_user_forums, user_forums_api_url, user_id


MAX_PAYLOAD_DOCUMENTS = 96


def make_document_bundle(
    documents: list[PayloadDocument],
    *,
    scan_complete: bool = True,
) -> DocumentBundle:
    unique: list[PayloadDocument] = []
    seen_documents: set[tuple[str, str, str]] = set()
    for document in documents:
        if not document.source:
            continue
        digest = hashlib.sha256(document.source.encode("utf-8", errors="replace")).hexdigest()
        signature = (document.url, document.record_id, digest)
        if signature in seen_documents:
            continue
        seen_documents.add(signature)
        unique.append(document)
    return DocumentBundle(tuple(unique), scan_complete=scan_complete)


def visible_text(source: str) -> str:
    value = re.sub(r"<script[\s\S]*?</script>", " ", source, flags=re.I)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def decode_strdecode_blocks(source: str) -> str:
    decoded: list[str] = []
    encoded_blocks = re.findall(r'(?:strdecode|atob|decodeB64)\(\s*["\']([A-Za-z0-9+/=]+)["\']\s*\)', source)
    encoded_blocks.extend(re.findall(r'__PAGE_DATA__\s*=\s*["\']([A-Za-z0-9+/=]+)["\']', source))
    for encoded in encoded_blocks:
        try:
            decoded.append(base64.b64decode(encoded).decode("utf-8", errors="replace"))
        except Exception:
            continue
    return "\n".join(decoded)


def source_boundary_id(site: Site) -> str:
    if site.payload == "admin_article_api":
        return admin_article_id(site.url)
    if site.payload == "tuku_user_forums":
        return f"user:{user_id(site.url)}"
    if site.payload == "topic_list_detail":
        return ""
    return detail_record_identity(site.url)


def canonical_detail_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.path.lower().endswith(".aspx"):
        return parsed._replace(fragment="").geturl()
    return parsed._replace(query="", fragment="").geturl()


def _list_item_matches(title: str, site: Site) -> bool:
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


def _next_list_url(listing: str, current_url: str) -> str:
    current = urlparse(current_url)
    for match in re.finditer(
        r'''<a\b[^>]+href=["'](?P<href>[^"']+)["'][^>]*>(?P<body>[\s\S]*?)</a>''',
        listing,
        flags=re.I,
    ):
        if "下一页" not in visible_text(match.group("body")):
            continue
        href = html.unescape(match.group("href")).strip()
        if not href or href == "#":
            continue
        candidate = urlparse(urljoin(current_url, href))
        if (
            candidate.scheme.lower() != current.scheme.lower()
            or candidate.netloc.lower() != current.netloc.lower()
            or candidate.path.lower() != current.path.lower()
        ):
            continue
        return candidate._replace(fragment="").geturl()
    return ""


def is_allowed_nested_document_url(url: str) -> bool:
    lowered = url.lower().split("?", 1)[0]
    return (
        lowered.endswith(".js")
        or "/topic/" in lowered
        or "/bbs" in lowered
        or "/htm/" in lowered
        or "/article" in lowered
    )


def fetch_topic_list_detail_payload(
    site: Site,
    period: int | None,
    timeout: int,
    context: FetchContext,
) -> DocumentBundle:
    if period is None:
        raise ValueError("列表详情站必须指定期数")
    listings: list[tuple[str, str]] = []
    visited_listing_urls: set[str] = set()
    listing_url = site.url
    for _ in range(32):
        if listing_url in visited_listing_urls:
            break
        visited_listing_urls.add(listing_url)
        listing = context.get_text(listing_url, timeout)
        listings.append((listing_url, listing))
        next_url = _next_list_url(listing, listing_url)
        if not next_url or next_url in visited_listing_urls:
            break
        listing_url = next_url
    period_order: list[int] = []
    urls_by_period: dict[int, list[str]] = {}
    seen_urls: set[str] = set()
    for listing_url, listing in listings:
        for match in re.finditer(
            r'''<a[^>]+href=["'](?P<href>[^"']+)["'][^>]*>(?P<body>[\s\S]*?)</a>''',
            listing,
            flags=re.I,
        ):
            title = visible_text(match.group("body"))
            period_match = re.search(r"(\d{3})\s*期", title)
            if not period_match or not _list_item_matches(title, site):
                continue
            detail_url = canonical_detail_url(urljoin(listing_url, html.unescape(match.group("href"))))
            candidate_period = int(period_match.group(1))
            if detail_url in seen_urls:
                continue
            seen_urls.add(detail_url)
            if candidate_period not in urls_by_period:
                period_order.append(candidate_period)
                urls_by_period[candidate_period] = []
            urls_by_period[candidate_period].append(detail_url)
    candidates = period_order[:3] if site.pick == "top" else period_order[-3:]
    if period not in candidates:
        raise ValueError(f"{site.pick} 列表候选内未找到 {period} 期")
    documents: list[PayloadDocument] = []
    for detail_url in urls_by_period[period]:
        detail = context.get_text(detail_url, timeout)
        found = False
        detail_id = detail_record_identity(detail_url)
        detail_documents: list[PayloadDocument] = []
        detail_text = visible_text(detail)
        if re.search(rf"{period}\s*期", detail_text) and _list_item_matches(detail_text, site):
            detail_documents.append(
                PayloadDocument(
                    "详情页",
                    detail_url,
                    detail,
                    record_id=detail_id,
                    record_path=f"page:{urlparse(detail_url).path}",
                    record_count=1,
                )
            )
            found = True
        for script_src in re.findall(r'''<script[^>]+src=["']([^"']+)["']''', detail, flags=re.I):
            if "/upload/script/" not in script_src:
                continue
            script_url = urljoin(detail_url, script_src)
            script = context.get_text(script_url, timeout)
            payload = decode_strdecode_blocks(script) or script
            text = visible_text(payload)
            if _list_item_matches(text, site) and re.search(rf"{period}\s*期", text):
                if not detail_documents:
                    detail_documents.append(
                        PayloadDocument(
                            "详情页",
                            detail_url,
                            detail,
                            record_id=detail_id,
                            record_path=f"page:{urlparse(detail_url).path}",
                            record_count=1,
                        )
                    )
                documents.append(
                    PayloadDocument(
                        "详情脚本",
                        script_url,
                        payload,
                        record_id=detail_id,
                        record_path=f"script:{urlparse(script_url).path}",
                        record_count=1,
                        parent_url=detail_url,
                        link_reference=script_src,
                    )
                )
                found = True
        if not found:
            raise ValueError("详情页脚本内未找到指定期数或专属关键词")
        documents[0:0] = detail_documents
    return make_document_bundle(documents)


def collect_page_and_scripts(site: Site, source: str, timeout: int, context: FetchContext) -> DocumentBundle:
    documents: list[PayloadDocument] = []
    seen_documents: set[tuple[str, str, str]] = set()
    visited_urls = {site.url}
    target_identity = source_boundary_id(site)
    scan_complete = True

    def append_document(
        label: str,
        url: str,
        value: str,
        record_id: str = "",
        *,
        parent_url: str = "",
        link_reference: str = "",
    ) -> None:
        nonlocal scan_complete
        if not value or len(documents) >= MAX_PAYLOAD_DOCUMENTS:
            if value and len(documents) >= MAX_PAYLOAD_DOCUMENTS:
                scan_complete = False
            return
        digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
        signature = (url, record_id, digest)
        if signature in seen_documents:
            return
        seen_documents.add(signature)
        documents.append(
            PayloadDocument(
                label,
                url,
                value,
                record_id=record_id,
                parent_url=parent_url,
                link_reference=link_reference,
            )
        )

    def add_iframes(source_text: str, base_url: str, inherited_identity: str, depth: int = 0) -> None:
        nonlocal scan_complete
        if depth > 2 or len(documents) >= MAX_PAYLOAD_DOCUMENTS:
            if depth > 2 or len(documents) >= MAX_PAYLOAD_DOCUMENTS:
                scan_complete = False
            return
        frame_text = html.unescape(source_text.replace(r"\'", "'").replace(r'\"', '"'))
        for frame_src in re.findall(r'''<iframe[^>]+src=["']([^"']+)["']''', frame_text, flags=re.I):
            frame_url = urljoin(base_url, frame_src)
            if frame_url in visited_urls or len(documents) >= MAX_PAYLOAD_DOCUMENTS:
                if len(documents) >= MAX_PAYLOAD_DOCUMENTS:
                    scan_complete = False
                continue
            frame_identity = detail_record_identity(frame_url) or inherited_identity
            if inherited_identity and frame_identity and frame_identity != inherited_identity:
                continue
            visited_urls.add(frame_url)
            try:
                frame_source = context.get_text(frame_url, timeout)
            except requests.RequestException:
                continue
            append_document(
                "iframe",
                frame_url,
                frame_source,
                frame_identity,
                parent_url=base_url,
                link_reference=frame_src,
            )
            add_iframes(frame_source, frame_url, frame_identity, depth + 1)

    append_document("原始页面", site.url, source, target_identity)
    append_document("原始页面解码", site.url, decode_strdecode_blocks(source), target_identity)
    script_sources = re.findall(r'''<script[^>]+src=["']([^"']+)["']''', source, flags=re.I)
    script_sources.sort(key=lambda value: "/upload/script/" not in value.lower())
    for script_src in script_sources:
        script_url = urljoin(site.url, script_src)
        if script_url in visited_urls or len(documents) >= MAX_PAYLOAD_DOCUMENTS:
            if len(documents) >= MAX_PAYLOAD_DOCUMENTS:
                scan_complete = False
            continue
        visited_urls.add(script_url)
        try:
            script = context.get_text(script_url, timeout)
        except requests.RequestException:
            continue
        decoded = decode_strdecode_blocks(script)
        append_document(
            "脚本",
            script_url,
            script,
            target_identity,
            parent_url=site.url,
            link_reference=script_src,
        )
        append_document(
            "脚本解码",
            script_url,
            decoded,
            target_identity,
            parent_url=site.url,
            link_reference=script_src,
        )
        add_iframes(script, script_url, target_identity)
        add_iframes(decoded, script_url, target_identity)

        link_text = html.unescape((decoded or script).replace(r"\/", "/"))
        nested_urls: list[tuple[int, str]] = []
        target_words = ("二肖", "两肖", "②肖", "2肖", "２肖")
        exact_targets = ("绝杀二肖", "绝杀两肖", "绝杀②肖", "禁杀两肖", "绝禁两肖")
        for alias in (site.name, "澳门" + site.name, "澳門" + site.name):
            for alias_match in re.finditer(re.escape(alias), link_text):
                nearby = link_text[max(0, alias_match.start() - 300) : alias_match.start() + 2500]
                if not any(word in nearby for word in target_words):
                    continue
                for nested in re.findall(r'https?://[^\s<>"\']+|/(?:topic|bbs|htm|Article)[^\s<>"\']+', nearby):
                    location = nearby.find(nested)
                    snippet = nearby[max(0, location - 120) : location + len(nested) + 120]
                    score = 10 if site.name in snippet else 0
                    score += 10 if any(word in snippet for word in target_words) else 0
                    score += 100 if any(word in snippet for word in exact_targets) else 0
                    score += 5 if any(word in snippet for word in ("绝杀", "稳杀", "杀")) else 0
                    nested_url = urljoin(script_url, nested)
                    if is_allowed_nested_document_url(nested_url):
                        nested_urls.append((score, nested_url))
        for match in re.finditer(r'https?://[^\s<>"\']+|/(?:topic|bbs|htm|Article)[^\s<>"\']+', link_text):
            nearby = link_text[max(0, match.start() - 180) : match.end() + 500]
            if not any(word in nearby for word in target_words):
                continue
            if site.name not in nearby and not re.search(r"\d{3}\s*期", nearby):
                continue
            nested_url = urljoin(script_url, match.group(0))
            if not is_allowed_nested_document_url(nested_url):
                continue
            score = 20 if site.name in nearby else 10
            score += 5 if any(word in nearby for word in ("绝杀", "稳杀", "杀")) else 0
            score += 100 if any(word in nearby for word in exact_targets) else 0
            nested_urls.append((score, nested_url))
        ordered_nested: list[str] = []
        for _score, nested_url in sorted(nested_urls, key=lambda item: -item[0]):
            if nested_url not in ordered_nested:
                ordered_nested.append(nested_url)
        if len(ordered_nested) > 12:
            scan_complete = False
        for nested_url in ordered_nested[:12]:
            if nested_url in visited_urls or len(documents) >= MAX_PAYLOAD_DOCUMENTS:
                if len(documents) >= MAX_PAYLOAD_DOCUMENTS:
                    scan_complete = False
                continue
            nested_identity = detail_record_identity(nested_url) or target_identity
            if target_identity and nested_identity and nested_identity != target_identity:
                continue
            visited_urls.add(nested_url)
            try:
                nested_source = context.get_text(nested_url, timeout)
            except requests.RequestException:
                continue
            nested_decoded = decode_strdecode_blocks(nested_source)
            append_document(
                "嵌套页面",
                nested_url,
                nested_source,
                nested_identity,
                parent_url=script_url,
                link_reference=nested_url,
            )
            append_document(
                "嵌套页面解码",
                nested_url,
                nested_decoded,
                nested_identity,
                parent_url=script_url,
                link_reference=nested_url,
            )
            add_iframes(nested_source, nested_url, nested_identity)
            add_iframes(nested_decoded, nested_url, nested_identity)
            for nested_src in re.findall(r'''<script[^>]+src=["']([^"']+)["']''', nested_source, flags=re.I):
                nested_script_url = urljoin(nested_url, nested_src)
                if nested_script_url in visited_urls or len(documents) >= MAX_PAYLOAD_DOCUMENTS:
                    if len(documents) >= MAX_PAYLOAD_DOCUMENTS:
                        scan_complete = False
                    continue
                visited_urls.add(nested_script_url)
                try:
                    nested_script = context.get_text(nested_script_url, timeout)
                except requests.RequestException:
                    continue
                append_document(
                    "嵌套脚本",
                    nested_script_url,
                    nested_script,
                    nested_identity,
                    parent_url=nested_url,
                    link_reference=nested_src,
                )
                nested_script_decoded = decode_strdecode_blocks(nested_script)
                append_document(
                    "嵌套脚本解码",
                    nested_script_url,
                    nested_script_decoded,
                    nested_identity,
                    parent_url=nested_url,
                    link_reference=nested_src,
                )
                add_iframes(nested_script, nested_script_url, nested_identity)
                add_iframes(nested_script_decoded, nested_script_url, nested_identity)
    add_iframes(source, site.url, target_identity)
    return DocumentBundle(tuple(documents), scan_complete=scan_complete)


def fetch_payload(
    site: Site,
    period: int | None,
    timeout: int,
    context: FetchContext,
    target_probe: TargetProbe,
) -> DocumentBundle:
    if site.payload == "browser_rendered_page":
        boundary_id = source_boundary_id(site)
        raw_error: requests.RequestException | None = None
        try:
            raw = context.get_text(site.url, timeout)
        except requests.RequestException as exc:
            raw_error = exc
        else:
            if target_probe(raw, site, period):
                return DocumentBundle((PayloadDocument("原始页面", site.url, raw, record_id=boundary_id),))
        try:
            rendered = context.get_rendered(site.url, timeout)
        except requests.RequestException as exc:
            if raw_error is not None:
                raise requests.RequestException(f"原始页面与浏览器均抓取失败：{raw_error}；{exc}") from exc
            raise
        if not target_probe(rendered, site, period):
            raise ValueError("浏览器渲染后仍未找到指定期数或专属目标")
        return DocumentBundle((PayloadDocument("浏览器渲染页面", site.url, rendered, record_id=boundary_id),))
    if site.payload == "admin_article_api":
        return fetch_admin_article_payload(site, period, timeout, context, target_probe)
    if site.payload == "tuku_user_forums":
        api_url = user_forums_api_url(site.url)
        expected_id = user_id(site.url)
        items, item_count = filter_user_forums(context.get_text(api_url, timeout), expected_id)
        return DocumentBundle(
            (
                PayloadDocument(
                    "用户论坛接口",
                    api_url,
                    json.dumps(items, ensure_ascii=False),
                    record_id=f"user:{expected_id}",
                    record_path="root.items[*]",
                    record_count=item_count,
                ),
            )
        )
    if site.payload == "topic_list_detail":
        return fetch_topic_list_detail_payload(site, period, timeout, context)
    if site.payload == "curl_tls10_page_and_scripts":
        source = fetch_curl_text(site.url, timeout)
        return collect_page_and_scripts(site, source, timeout, context)

    source = context.get_text(site.url, timeout)
    boundary_id = source_boundary_id(site)
    if site.payload == "page":
        return DocumentBundle((PayloadDocument("原始页面", site.url, source, record_id=boundary_id),))
    if site.payload == "page_and_scripts":
        return collect_page_and_scripts(site, source, timeout, context)
    if site.payload == "scripts":
        documents: list[PayloadDocument] = [
            PayloadDocument("原始页面", site.url, source, record_id=boundary_id)
        ]
        target_found = False
        for src in re.findall(r'''<script[^>]+src=["']([^"']+)["']''', source, flags=re.I):
            script_url = urljoin(site.url, src)
            try:
                script = context.get_text(script_url, timeout)
            except requests.RequestException:
                continue
            candidate = decode_strdecode_blocks(script) or script
            if all(keyword in candidate for keyword in site.keywords):
                target_found = True
                documents.append(
                    PayloadDocument(
                        "目标脚本",
                        script_url,
                        candidate,
                        record_id=boundary_id,
                        parent_url=site.url,
                        link_reference=src,
                    )
                )
        if not target_found:
            raise ValueError(f"未找到{site.name}目标脚本")
        return make_document_bundle(documents)
    raise ValueError(f"未知 payload：{site.payload}")
