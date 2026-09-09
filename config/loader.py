from __future__ import annotations

from collections.abc import Collection, Mapping
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from domain.identity import normalized_source_identity

from domain.errors import ConfigurationError
from domain.models import Site


ConfigError = ConfigurationError

PICK_ALIASES = {
    "top": "top",
    "顶部": "top",
    "上": "top",
    "bottom": "bottom",
    "尾部": "bottom",
    "下": "bottom",
}
ALLOWED_PAYLOADS = frozenset(
    {
        "page",
        "page_and_scripts",
        "browser_rendered_page",
        "topic_list_detail",
        "admin_article_api",
        "tuku_user_forums",
        "curl_tls10_page_and_scripts",
        "scripts",
    }
)

def normalize_pick(value: str) -> str:
    pick = PICK_ALIASES.get(str(value).strip().lower())
    if pick not in {"top", "bottom"}:
        raise ConfigError(f"pick 必须是 top/bottom/顶部/尾部：{value}")
    return pick


ALLOWED_SITE_FIELDS = frozenset({
    "name", "pick", "url", "parser", "title", "record", "stop", "payload", "api_url",
    "keywords", "profile_id", "linked_document_pattern", "history_authorization",
    "history_missing_periods", "history_valid_periods", "allowed_redirect_origins",
    "allowed_document_origins",
})


def required_text(item: Mapping[str, object], key: str, message: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip() or any(ch in value for ch in "\r\n\x00"):
        raise ConfigError(message)
    return value.strip()


def _origin_options(item: Mapping[str, object], field: str, name: str) -> tuple[str, ...]:
    values = item.get(field, ())
    if not isinstance(values, (list, tuple)):
        raise ConfigError(f"{name} {field}必须为来源地址数组")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ConfigError(f"{name} {field}来源必须为字符串")
        try:
            parsed = urlparse(value)
            port = parsed.port
        except ValueError as exc:
            raise ConfigError(f"{name} {field}来源无效") from exc
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ConfigError(f"{name} {field}只允许完整HTTP/HTTPS来源，不允许路径或通配符")
        if "*" in parsed.netloc or any(ch.isspace() for ch in value):
            raise ConfigError(f"{name} {field}来源无效")
        normalized.append(f"{parsed.scheme}://{parsed.netloc.lower()}")
    return tuple(dict.fromkeys(normalized))


def site_from_mapping(
    item: Mapping[str, object],
    *,
    allowed_parsers: Collection[str] | None = None,
) -> Site:
    if not isinstance(item, Mapping):
        raise ConfigError("每个站点配置必须是对象")
    unknown = set(item) - ALLOWED_SITE_FIELDS
    if unknown:
        raise ConfigError(f"站点配置包含未知字段：{','.join(sorted(unknown))}")
    name = required_text(item, "name", "站点名称必须是非空单行字符串")
    pick = normalize_pick(str(item.get("pick") or ""))
    url = required_text(item, "url", f"{name} URL不能为空")
    try:
        parsed_url = urlparse(url)
        hostname, _ = parsed_url.hostname, parsed_url.port
    except ValueError as exc:
        raise ConfigError(f"{name} URL 必须使用 http/https：{url}") from exc
    if (parsed_url.scheme.lower() not in {"http", "https"} or not hostname
            or parsed_url.username is not None or parsed_url.password is not None):
        raise ConfigError(f"{name} URL 必须使用 http/https：{url}")
    parser = str(item.get("parser") or "named_block").strip()
    if not parser:
        raise ConfigError(f"{name} parser 不能为空")
    if allowed_parsers is not None and parser not in set(allowed_parsers):
        raise ConfigError(f"{name} 使用未知 parser：{parser}")
    payload = required_text(item, "payload", f"{name} payload 不能为空")
    if payload not in ALLOWED_PAYLOADS:
        raise ConfigError(f"{name} payload 不支持：{payload}")
    for field in ("title", "record", "stop", "linked_document_pattern"):
        value = str(item.get(field) or "").strip()
        if not value:
            continue
        try:
            compiled = re.compile(value)
            if field == "record" and parser == "named_block":
                missing = {"period", "zodiac", "open"} - compiled.groupindex.keys()
                if missing:
                    raise ConfigError(f"{name} record缺少命名组：{','.join(sorted(missing))}")
        except re.error as exc:
            raise ConfigError(f"{name} {field} 正则无效：{exc}") from exc
    api_url = str(item.get("api_url") or "").strip()
    if api_url:
        try:
            parsed_api_url = urlparse(api_url)
            api_hostname, _ = parsed_api_url.hostname, parsed_api_url.port
        except ValueError as exc:
            raise ConfigError(f"{name} api_url 必须使用 http/https 且包含主机：{api_url}") from exc
        if (parsed_api_url.scheme.lower() not in {"http", "https"} or not api_hostname
                or parsed_api_url.username is not None or parsed_api_url.password is not None):
            raise ConfigError(f"{name} api_url 必须使用 http/https 且包含主机：{api_url}")
    raw_keywords = item.get("keywords") or ()
    if isinstance(raw_keywords, str):
        keywords = (raw_keywords,) if raw_keywords else ()
    elif isinstance(raw_keywords, (list, tuple)):
        keywords = tuple(str(value) for value in raw_keywords if str(value))
    else:
        raise ConfigError(f"{name} keywords 必须是字符串或数组")
    return Site(
        name=name,
        pick=pick,
        url=url,
        parser=parser,
        title=str(item.get("title") or ""),
        record=str(item.get("record") or ""),
        stop=str(item.get("stop") or ""),
        payload=payload,
        api_url=api_url,
        keywords=keywords,
        profile_id=str(item.get("profile_id") or "").strip(),
        linked_document_pattern=str(item.get("linked_document_pattern") or "").strip(),
        allowed_redirect_origins=_origin_options(item, "allowed_redirect_origins", name),
        allowed_document_origins=_origin_options(item, "allowed_document_origins", name),
    )


def load_sites(
    path: Path,
    *,
    allowed_parsers: Collection[str] | None = None,
) -> list[Site]:
    if not path.is_file():
        raise ConfigError(f"站点配置不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"站点配置 JSON格式错误：{exc}") from exc
    except OSError as exc:
        raise ConfigError(f"站点配置读取失败：{exc}") from exc
    if not isinstance(data, list):
        raise ConfigError("站点配置根节点必须是数组")
    if not data:
        raise ConfigError("站点配置不能为空")
    sites = [site_from_mapping(item, allowed_parsers=allowed_parsers) for item in data]
    seen_names: set[str] = set()
    seen_identities: set[tuple[str, str, str]] = set()
    seen_sources: dict[tuple, str] = {}
    for site in sites:
        if site.parser == "named_block" and (not site.title or not site.record):
            raise ConfigError(f"{site.name} named_block必须配置title和record")
        source_key = normalized_source_identity(site)
        if source_key in seen_sources:
            raise ConfigError(f"站点来源身份重复：{seen_sources[source_key]} / {site.name}")
        seen_sources[source_key] = site.name
        if site.name in seen_names:
            raise ConfigError(f"站点名称重复：{site.name}")
        if site.identity in seen_identities:
            raise ConfigError(f"站点身份重复：{site.identity}")
        seen_names.add(site.name)
        seen_identities.add(site.identity)
    return sites
