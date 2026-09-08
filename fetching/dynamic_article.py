from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
import html
import json
import re
from urllib.parse import parse_qs, quote, urlparse

import requests

from domain.models import AdminArticleMatch, DocumentBundle, PayloadDocument, Site
from fetching.client import FetchContext


TargetProbe = Callable[[str, Site, int | None], bool]


def admin_article_id(url: str) -> str:
    parsed = urlparse(url)
    match = re.search(r"/article/(?:admin|manager|lottery)/([^/?#]+)", parsed.path)
    if not match:
        raise ValueError(f"无法识别后台文章 ID：{url}")
    return match.group(1)


def admin_article_api_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/api/proxy/admin-articles/{admin_article_id(url)}"


def landing_page_data_api_url(url: str) -> str:
    parsed = urlparse(url)
    slug = (parse_qs(parsed.query).get("url") or [""])[0].strip()
    if not slug:
        raise ValueError(f"无法识别落地页 url 参数：{url}")
    return f"{parsed.scheme}://{parsed.netloc}/api/proxy/landing-page-data?url={quote(slug)}"


def is_http_status(exc: BaseException, status_code: int) -> bool:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) == status_code


def is_admin_article_record(value: object) -> bool:
    return (
        isinstance(value, dict)
        and bool(value.get("id"))
        and isinstance(value.get("title"), str)
        and isinstance(value.get("html"), str)
    )


def find_unique_admin_article_record(source: str, article_id: str) -> AdminArticleMatch | None:
    data = json.loads(source)
    article_count = 0
    matching_record: dict[str, object] | None = None
    matching_path = ""
    matching_count = 0

    def collect(value: object, path: str) -> None:
        nonlocal article_count, matching_count, matching_path, matching_record
        if isinstance(value, dict):
            if is_admin_article_record(value):
                article_count += 1
                if str(value.get("id") or "") == article_id:
                    matching_count += 1
                    if matching_count == 1:
                        matching_path = path
                        matching_record = value
            for key, child in value.items():
                collect(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                collect(child, f"{path}[{index}]")

    collect(data, "root")
    if matching_count > 1:
        raise ValueError(f"接口内匹配到多个同ID文章：{article_id}")
    if matching_record is None:
        return None
    return AdminArticleMatch(matching_record, matching_path, article_count)


def decode_admin_article_record(record: dict[str, object]) -> str:
    payloads: list[str] = []
    for key in ("title", "html"):
        encoded = record.get(key)
        if not isinstance(encoded, str) or not encoded:
            raise ValueError(f"目标后台文章缺少{key}字段")
        try:
            normalized = "".join(encoded.split())
            decoded = base64.b64decode(normalized, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValueError(f"目标后台文章{key}字段解码失败") from exc
        if not decoded.strip():
            raise ValueError(f"目标后台文章{key}字段为空")
        payloads.append(decoded)
    return "\n".join(payloads)


def decode_admin_article_api_response(
    source: str,
    article_id: str,
    document_url: str = "",
) -> DocumentBundle:
    match = find_unique_admin_article_record(source, article_id)
    if match is None:
        return DocumentBundle(())
    return DocumentBundle(
        (
            PayloadDocument(
                "后台文章",
                document_url,
                decode_admin_article_record(match.record),
                record_id=article_id,
                record_path=match.path,
                record_count=match.article_count,
            ),
        )
    )


def page_has_record_boundary(source: str, article_id: str) -> bool:
    route_ids = set(
        re.findall(
            r"(?<![A-Za-z0-9])/?article/(?:admin|manager|lottery)/([^/?#\"'\s]+)",
            html.unescape(source),
            flags=re.I,
        )
    )
    return route_ids == {article_id}


def fetch_admin_article_page_or_render(
    site: Site,
    period: int | None,
    timeout: int,
    context: FetchContext,
    target_probe: TargetProbe,
) -> DocumentBundle:
    article_id = admin_article_id(site.url)
    raw_error: requests.RequestException | None = None
    try:
        source = context.get_text(site.url, timeout)
    except requests.RequestException as exc:
        raw_error = exc
    else:
        if page_has_record_boundary(source, article_id) and target_probe(source, site, period):
            return DocumentBundle(
                (
                    PayloadDocument(
                        "后台文章页面",
                        site.url,
                        source,
                        record_id=article_id,
                        record_path=f"page:{urlparse(site.url).path}",
                        record_count=1,
                    ),
                )
            )
    try:
        rendered = context.get_rendered(site.url, timeout)
    except requests.RequestException as exc:
        if raw_error is not None:
            raise requests.RequestException(f"原始页面与浏览器均抓取失败：{raw_error}；{exc}") from exc
        raise
    if not page_has_record_boundary(rendered, article_id) or not target_probe(rendered, site, period):
        raise ValueError("浏览器渲染后仍未找到指定期数或栏目关键词")
    return DocumentBundle(
        (
            PayloadDocument(
                "浏览器渲染后台文章",
                site.url,
                rendered,
                record_id=article_id,
                record_path=f"rendered:{urlparse(site.url).path}",
                record_count=1,
            ),
        )
    )


def fetch_admin_article_payload(
    site: Site,
    period: int | None,
    timeout: int,
    context: FetchContext,
    target_probe: TargetProbe,
) -> DocumentBundle:
    article_id = admin_article_id(site.url)
    api_url = site.api_url or admin_article_api_url(site.url)
    try:
        bundle = decode_admin_article_api_response(
            context.get_text(api_url, timeout),
            article_id,
            api_url,
        )
        if bundle.documents:
            return bundle
        raise ValueError("接口返回内容内未找到对应后台文章")
    except requests.RequestException as exc:
        if is_http_status(exc, 404):
            return fetch_admin_article_page_or_render(site, period, timeout, context, target_probe)
        raise
