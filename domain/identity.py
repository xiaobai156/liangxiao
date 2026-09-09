from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import parse_qsl, urlparse


def identity_query(url: str) -> dict[str, list[str]]:
    query: dict[str, list[str]] = {}
    for key, value in parse_qsl(urlparse(url).query, keep_blank_values=True):
        query.setdefault(key.lower(), []).append(value)
    identity_keys = {"id", "tid", "listid", "user_id", "userid", "url", "page", "period", "contenttype"}
    for key in identity_keys & query.keys():
        if len(query[key]) > 1:
            raise ValueError(f"URL身份参数不唯一：{key}")
    return query


def detail_record_identity(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or ""
    lowered_path = path.lower()
    query = identity_query(url)
    article = re.search(r"/(?:article/(?:admin|manager|lottery)|api/proxy/admin-articles)/([^/?#]+)", path, re.I)
    if article:
        return f"admin_article:{article.group(1)}"
    user = re.search(r"(?:^|/)(?:api/v1/)?users/(\d+)(?:/|$)", parsed.fragment or path, re.I)
    if user:
        return f"user:{user.group(1)}"
    topic_match = re.search(r"/topic/([^/?#]+)", path, flags=re.I)
    if topic_match:
        topic_id = re.sub(r"\.(?:html?|js)$", "", topic_match.group(1), flags=re.I)
        return f"topic:{topic_id}"
    if re.search(r"/(?:view|read)\.php$", lowered_path, flags=re.I):
        key = "id" if lowered_path.endswith("/view.php") else "tid"
        value = (query.get(key) or [""])[0].strip()
        if value:
            return f"{'view' if key == 'id' else 'read'}:{value}"
    if lowered_path.endswith("/article.aspx") or lowered_path.endswith("/article.asp"):
        record_id = (query.get("id") or [""])[0].strip()
        if record_id:
            list_id = (query.get("listid") or [""])[0].strip()
            return f"article:{list_id}:{record_id}" if list_id else f"article:{record_id}"
    if lowered_path.endswith(".aspx") or lowered_path.endswith("/topic.php"):
        record_id = (query.get("id") or [""])[0].strip()
        if record_id:
            return f"{Path(path).name.lower()}:{record_id}"
    bbs_match = re.search(r"/bbs/(\d+)(?:\.html)?$", path, flags=re.I)
    if bbs_match:
        return f"bbs:{bbs_match.group(1)}"
    article_match = re.search(r"/art_zhuanqu/(\d+)\.html$", path, flags=re.I)
    if article_match:
        return f"art_zhuanqu:{article_match.group(1)}"
    return ""


def normalized_source_identity(site) -> tuple:
    """Compare declared resource and field identity, not display names."""
    parsed = urlparse(site.url)
    host = (parsed.hostname or "").lower()
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    origin = (parsed.scheme.lower(), host, port)
    aliases = [origin]
    for value in site.allowed_redirect_origins:
        item = urlparse(value)
        aliases.append((item.scheme.lower(), (item.hostname or "").lower(),
                        item.port or (443 if item.scheme.lower() == "https" else 80)))
    resource = detail_record_identity(site.url)
    if not resource:
        query = tuple(sorted((key, value) for key, value in parse_qsl(parsed.query)
                             if not key.lower().startswith("utm_")))
        resource = (parsed.path.rstrip("/") or "/", query, parsed.fragment)
    return (min(aliases), resource, site.pick, site.payload, site.parser,
            site.title.strip(), site.record.strip(), site.stop.strip())
