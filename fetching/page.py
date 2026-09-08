from __future__ import annotations

import base64
import binascii
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
    encoded_blocks = re.findall(r'(?:strdecode|atob|decodeB64)\(\s*["\']([^"\']*)["\']\s*\)', source)
    encoded_blocks.extend(re.findall(r'__PAGE_DATA__\s*=\s*["\']([^"\']*)["\']', source))
    for encoded in encoded_blocks:
        try:
            normalized = "".join(encoded.split())
            if not normalized:
                continue
            decoded.append(base64.b64decode(normalized, validate=True).decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError):
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


def next_topic_listing_url(listing: str, current_url: str) -> str:
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


def topic_listing_links(listing: str, listing_url: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    for match in re.finditer(
        r'''<a[^>]+href=["'](?P<href>[^"']+)["'][^>]*>(?P<body>[\s\S]*?)</a>''',
        listing,
        flags=re.I,
    ):
        href = html.unescape(match.group("href")).strip()
        if not href or href == "#":
            continue
        links.append((canonical_detail_url(urljoin(listing_url, href)), visible_text(match.group("body"))))
    return links


def fetch_topic_detail_documents(
    detail_url: str,
    timeout: int,
    context: FetchContext,
    site: Site | None = None,
) -> list[PayloadDocument]:
    detail = context.get_text(detail_url, timeout)
    detail_id = detail_record_identity(detail_url)
    documents = [
        PayloadDocument(
            "详情页",
            detail_url,
            detail,
            record_id=detail_id,
            record_path=f"page:{urlparse(detail_url).path}",
            record_count=1,
        )
    ]
    for script_src in re.findall(r'''<script[^>]+src=["']([^"']+)["']''', detail, flags=re.I):
        if "/upload/script/" not in script_src:
            continue
        script_url = urljoin(detail_url, script_src)
        if site is not None and not _allowed_script_reference(script_url, detail_url, site):
            continue
        script = context.get_text(script_url, timeout)
        payload = decode_strdecode_blocks(script) or script
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
    return documents


def is_allowed_nested_document_url(url: str) -> bool:
    lowered = url.lower().split("?", 1)[0]
    return (
        lowered.endswith(".js")
        or "/topic/" in lowered
        or "/bbs" in lowered
        or "/htm/" in lowered
        or "/article" in lowered
    )


def _origin(url: str) -> tuple[str, str, int] | None:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if parsed.scheme.lower() == "https" else 80
    return parsed.scheme.lower(), parsed.hostname.lower(), port


def _same_url(left: str, right: str) -> bool:
    if not right:
        return False
    return urlparse(left)._replace(fragment="").geturl() == urlparse(right)._replace(fragment="").geturl()


def _allowed_reference(url: str, owner_url: str, site: Site) -> bool:
    if any(marker in url for marker in ("${", "{", "}", "[", "]")):
        return False
    if _origin(url) == _origin(owner_url):
        return True
    if _same_url(url, site.api_url):
        return True
    return bool(
        site.linked_document_pattern
        and re.search(site.linked_document_pattern, url, flags=re.I)
    )


def _allowed_script_reference(url: str, owner_url: str, site: Site) -> bool:
    if any(marker in url for marker in ("${", "{", "}", "[", "]")):
        return False
    if _allowed_reference(url, owner_url, site):
        return True
    path = urlparse(url).path.lower()
    return (
        site.payload
        in {"page_and_scripts", "curl_tls10_page_and_scripts", "scripts", "topic_list_detail"}
        and _origin(url) is not None
        and path.startswith("/upload/script/")
        and path.endswith(".js")
    )


def _reference_is_absent(exc: requests.RequestException) -> bool:
    return getattr(getattr(exc, "response", None), "status_code", None) in {404, 410}


def _required_data_script(url: str, site: Site) -> bool:
    path = urlparse(url).path.lower()
    return (
        site.payload == "scripts"
        or (path.startswith("/upload/script/") and path.endswith(".js"))
        or bool(
            site.linked_document_pattern
            and re.search(site.linked_document_pattern, url, flags=re.I)
        )
    )


def collect_page_and_scripts(
    site: Site,
    source: str,
    timeout: int,
    context: FetchContext,
    target_probe: TargetProbe | None = None,
) -> DocumentBundle:
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
        if not value:
            return
        digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
        signature = (url, record_id, digest)
        if signature in seen_documents:
            return
        if len(documents) >= MAX_PAYLOAD_DOCUMENTS:
            scan_complete = False
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
        frame_text = html.unescape(source_text.replace(r"\'", "'").replace(r'\"', '"'))
        frame_urls: list[tuple[str, str, str]] = []
        for frame_src in re.findall(r'''<iframe[^>]+src=["']([^"']+)["']''', frame_text, flags=re.I):
            frame_url = urljoin(base_url, frame_src)
            if not _allowed_reference(frame_url, base_url, site):
                continue
            if frame_url in visited_urls:
                continue
            frame_identity = detail_record_identity(frame_url) or inherited_identity
            if inherited_identity and frame_identity and frame_identity != inherited_identity:
                continue
            frame_urls.append((frame_url, frame_src, frame_identity))
        if depth > 2:
            if frame_urls:
                scan_complete = False
            return
        for frame_url, frame_src, frame_identity in frame_urls:
            if len(documents) >= MAX_PAYLOAD_DOCUMENTS:
                scan_complete = False
                continue
            visited_urls.add(frame_url)
            try:
                frame_source = context.get_text(frame_url, timeout)
            except (requests.RequestException, UnicodeError) as exc:
                if not isinstance(exc, requests.RequestException) or not _reference_is_absent(exc):
                    scan_complete = False
                continue
            if not frame_source:
                scan_complete = False
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
        if not _allowed_script_reference(script_url, site.url, site):
            continue
        if script_url in visited_urls:
            continue
        if len(documents) >= MAX_PAYLOAD_DOCUMENTS:
            scan_complete = False
            continue
        visited_urls.add(script_url)
        try:
            script = context.get_text(script_url, timeout)
        except (requests.RequestException, UnicodeError) as exc:
            if (
                not isinstance(exc, requests.RequestException)
                or not _reference_is_absent(exc)
                or _required_data_script(script_url, site)
            ):
                scan_complete = False
            continue
        if not script:
            if _required_data_script(script_url, site):
                scan_complete = False
            continue
        decoded = decode_strdecode_blocks(script)
        payload = decoded or script
        append_document(
            "脚本解码" if decoded else "脚本",
            script_url,
            payload,
            target_identity,
            parent_url=site.url,
            link_reference=script_src,
        )
        add_iframes(payload, script_url, target_identity)

        link_text = html.unescape((decoded or script).replace(r"\/", "/"))
        nested_urls: list[tuple[int, str]] = []
        reference_matches = []
        for match in re.finditer(r'https?://[^\s<>"\']+|/(?:topic|bbs|htm|Article)[^\s<>"\']+', link_text):
            nested_url = urljoin(script_url, match.group(0))
            if not _allowed_reference(nested_url, script_url, site):
                continue
            if not is_allowed_nested_document_url(nested_url) and not _same_url(nested_url, site.api_url):
                continue
            reference_matches.append((match, nested_url))
        for index, (match, nested_url) in enumerate(reference_matches):
            nearby = link_text[max(0, match.start() - 180) : match.end() + 500]
            previous_end = reference_matches[index - 1][0].end() if index else max(0, match.start() - 800)
            preceding = link_text[previous_end : match.start()]
            configured_reference = bool(
                site.linked_document_pattern
                and re.search(site.linked_document_pattern, nested_url, flags=re.I)
            )
            score = 0
            configured_title_link = bool(
                configured_reference
                and site.title
                and re.search(site.title, visible_text(preceding), flags=re.I)
            )
            if target_probe is not None:
                try:
                    probe_source = preceding if configured_reference and site.title else nearby
                    score = int(target_probe(probe_source, site, None))
                except (ValueError, LookupError):
                    score = 0
                if score == 0 and not configured_title_link:
                    continue
            score += int(configured_title_link)
            nested_urls.append((score, nested_url))
        ordered_nested: list[str] = []
        for _score, nested_url in sorted(nested_urls, key=lambda item: -item[0]):
            if nested_url not in ordered_nested:
                ordered_nested.append(nested_url)
        if len(ordered_nested) > 12:
            scan_complete = False
        for nested_url in ordered_nested[:12]:
            if nested_url in visited_urls:
                continue
            if len(documents) >= MAX_PAYLOAD_DOCUMENTS:
                scan_complete = False
                continue
            nested_identity = detail_record_identity(nested_url) or target_identity
            if target_identity and nested_identity and nested_identity != target_identity:
                continue
            visited_urls.add(nested_url)
            try:
                nested_source = context.get_text(nested_url, timeout)
            except (requests.RequestException, UnicodeError):
                scan_complete = False
                continue
            if not nested_source:
                scan_complete = False
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
                if not _allowed_script_reference(nested_script_url, nested_url, site):
                    continue
                if nested_script_url in visited_urls:
                    continue
                if len(documents) >= MAX_PAYLOAD_DOCUMENTS:
                    scan_complete = False
                    continue
                visited_urls.add(nested_script_url)
                try:
                    nested_script = context.get_text(nested_script_url, timeout)
                except (requests.RequestException, UnicodeError) as exc:
                    if (
                        not isinstance(exc, requests.RequestException)
                        or not _reference_is_absent(exc)
                        or _required_data_script(nested_script_url, site)
                    ):
                        scan_complete = False
                    continue
                if not nested_script:
                    scan_complete = False
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
    if site.payload == "topic_list_detail":
        raise ValueError("topic_list_detail必须由服务层获取")
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
    if site.payload == "curl_tls10_page_and_scripts":
        source = fetch_curl_text(site.url, timeout)
        return collect_page_and_scripts(site, source, timeout, context, target_probe)

    source = context.get_text(site.url, timeout)
    boundary_id = source_boundary_id(site)
    if site.payload == "page":
        return DocumentBundle((PayloadDocument("原始页面", site.url, source, record_id=boundary_id),))
    if site.payload == "page_and_scripts":
        return collect_page_and_scripts(site, source, timeout, context, target_probe)
    if site.payload == "scripts":
        documents: list[PayloadDocument] = [
            PayloadDocument("原始页面", site.url, source, record_id=boundary_id)
        ]
        scan_complete = True
        script_sources = re.findall(r'''<script[^>]+src=["']([^"']+)["']''', source, flags=re.I)
        for src in script_sources:
            script_url = urljoin(site.url, src)
            if not _allowed_script_reference(script_url, site.url, site):
                continue
            if len(documents) >= MAX_PAYLOAD_DOCUMENTS:
                scan_complete = False
                break
            try:
                script = context.get_text(script_url, timeout)
            except (requests.RequestException, UnicodeError) as exc:
                if (
                    not isinstance(exc, requests.RequestException)
                    or not _reference_is_absent(exc)
                    or _required_data_script(script_url, site)
                ):
                    scan_complete = False
                continue
            if not script:
                scan_complete = False
                continue
            documents.append(
                PayloadDocument(
                    "脚本",
                    script_url,
                    script,
                    record_id=boundary_id,
                    parent_url=site.url,
                    link_reference=src,
                )
            )
            decoded = decode_strdecode_blocks(script)
            if decoded:
                if len(documents) >= MAX_PAYLOAD_DOCUMENTS:
                    scan_complete = False
                    break
                documents.append(
                    PayloadDocument(
                        "脚本解码",
                        script_url,
                        decoded,
                        record_id=boundary_id,
                        parent_url=site.url,
                        link_reference=src,
                    )
                )
        bundle = make_document_bundle(documents, scan_complete=scan_complete)
        if not bundle.documents:
            raise ValueError(f"未抓到{site.name}原始脚本")
        return bundle
    raise ValueError(f"未知 payload：{site.payload}")
