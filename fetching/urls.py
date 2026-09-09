from __future__ import annotations

from urllib.parse import urlparse

import requests

from domain.identity import detail_record_identity, identity_query


class SourceBoundaryError(requests.RequestException):
    """A final URL cannot be proven to belong to the requested resource."""


class FetchedText(str):
    """String-compatible payload retaining transport provenance."""
    def __new__(cls, text: str, requested_url: str, final_url: str,
                redirect_chain: tuple[str, ...] = ()):
        value = super().__new__(cls, text)
        value.requested_url = requested_url
        value.final_url = final_url
        value.redirect_chain = redirect_chain or (requested_url,)
        return value


def origin(url: str) -> tuple[str, str, int]:
    try:
        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("HTTP/HTTPS URL required")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("URL credentials are not allowed")
        return (parsed.scheme.lower(), parsed.hostname.lower(),
                parsed.port or (443 if parsed.scheme.lower() == "https" else 80))
    except ValueError as exc:
        raise SourceBoundaryError(f"来源URL无效：{url}") from exc


def validate_redirect(requested: str, target: str,
                      allowed_origins: tuple[str, ...] = ()) -> None:
    before, after = origin(requested), origin(target)
    if before[0] == "https" and after[0] != "https":
        raise SourceBoundaryError("来源边界冲突：禁止HTTPS降级到HTTP")
    upgrade = (before[0] == "http" and after[0] == "https" and before[1] == after[1]
               and before[2] == 80 and after[2] == 443)
    if before != after and not upgrade and after not in {origin(url) for url in allowed_origins}:
        raise SourceBoundaryError(f"来源边界冲突：未授权重定向 {requested} -> {target}")
    try:
        expected = detail_record_identity(requested)
        actual = detail_record_identity(target)
        left, right = identity_query(requested), identity_query(target)
    except ValueError as exc:
        raise SourceBoundaryError(str(exc)) from exc
    if expected:
        if actual != expected:
            raise SourceBoundaryError(f"记录边界冲突：重定向目标{actual or '缺失'}不等于{expected}")
    elif urlparse(requested).path.rstrip("/") != urlparse(target).path.rstrip("/"):
        raise SourceBoundaryError("来源边界冲突：未知资源路径发生变化")
    # A redirect may reorder query parameters, not switch article/page/field IDs.
    identity_keys = {"id", "tid", "listid", "user_id", "userid", "url", "page", "period", "contenttype"}
    for key in identity_keys & (left.keys() | right.keys()):
        if left.get(key) != right.get(key):
            raise SourceBoundaryError(f"记录边界冲突：重定向改变参数{key}")
