from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path

import pytest

from config.loader import ConfigError, load_sites, normalize_pick, site_from_mapping
from domain.errors import ErrorCategory, ScrapeFailure
from domain.models import DocumentBundle, PayloadDocument, Record, Result, Site


ROOT = Path(__file__).resolve().parents[1]
SITES_PATH = ROOT / "config/sites.json"
APPROVED_SITES_SHA256 = "4F621A5A19D3D1737957520E55CF9518D6A71128A49C0DCB025F886EBEB7680C"


def raw_sites() -> list[dict[str, object]]:
    return json.loads(SITES_PATH.read_text(encoding="utf-8"))


def configured_parser_names() -> set[str]:
    return {str(item.get("parser") or "named_block") for item in raw_sites()}


def test_domain_models_are_immutable_and_keep_provenance() -> None:
    site = Site("目录", "top", "https://example.test/topic/1", parser="site_parser")
    record = Record(209, "狗蛇", "0000", "209期狗蛇", 0, record_id="topic:1", document_url=site.url)
    result = Result(site, record)
    document = PayloadDocument("脚本解码", site.url, "正文", record_id="topic:1")
    bundle = DocumentBundle((document,))

    assert site.identity == ("目录", "https://example.test/topic/1", "top")
    assert result.ok is True
    assert record.record_id == "topic:1"
    assert record.document_url == site.url
    assert bundle.combined_source == "正文"
    with pytest.raises(FrozenInstanceError):
        site.name = "改名"  # type: ignore[misc]


def test_failure_taxonomy_renders_stable_chinese_message() -> None:
    failure = ScrapeFailure(ErrorCategory.DATA_CONFLICT, "209期出现狗蛇、龙虎")
    assert str(failure) == "数据存在冲突：209期出现狗蛇、龙虎"
    assert ErrorCategory.CONTENT_NOT_PUBLISHED.value == "内容尚未发布"
    assert ErrorCategory.FIELD_VALIDATION.value == "字段校验未通过"


def test_v2_sites_file_matches_approved_configuration() -> None:
    digest = hashlib.sha256(SITES_PATH.read_bytes()).hexdigest().upper()
    assert digest == APPROVED_SITES_SHA256


def test_loads_all_current_sites_with_unique_identity_and_name() -> None:
    sites = load_sites(SITES_PATH, allowed_parsers=configured_parser_names())
    assert len(sites) == 215
    assert len({site.name for site in sites}) == 215
    assert len({site.identity for site in sites}) == 215
    assert all(site.parser and site.payload for site in sites)
    assert all(site.pick in {"top", "bottom"} for site in sites)


def test_site_mapping_preserves_fields_and_normalizes_legacy_defaults() -> None:
    site = site_from_mapping(
        {
            "name": "测试目录",
            "pick": "顶部",
            "url": "https://example.test/topic/1",
            "payload": "page_and_scripts",
            "title": "测试目录",
            "record": "record-pattern",
            "keywords": ["绝杀二肖", "测试目录"],
        },
        allowed_parsers={"named_block"},
    )
    assert site.pick == "top"
    assert site.parser == "named_block"
    assert site.payload == "page_and_scripts"
    assert site.keywords == ("绝杀二肖", "测试目录")
    assert normalize_pick("尾部") == "bottom"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"name": "", "pick": "top", "url": "https://example.test", "payload": "page"}, "站点名称不能为空"),
        ({"name": "A", "pick": "middle", "url": "https://example.test", "payload": "page"}, "pick 必须"),
        ({"name": "A", "pick": "top", "url": "file:///tmp/a", "payload": "page"}, "URL 必须使用 http/https"),
        ({"name": "A", "pick": "top", "url": "https://example.test", "payload": ""}, "payload 不能为空"),
        ({"name": "A", "pick": "top", "url": "https://example.test", "payload": "page", "parser": "unknown"}, "未知 parser"),
    ],
)
def test_invalid_site_mapping_is_rejected(payload: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        site_from_mapping(payload, allowed_parsers={"named_block"})


def test_duplicate_name_or_identity_is_rejected(tmp_path: Path) -> None:
    data = [
        {"name": "A", "pick": "top", "url": "https://example.test/1", "payload": "page"},
        {"name": "A", "pick": "bottom", "url": "https://example.test/2", "payload": "page"},
    ]
    path = tmp_path / "sites.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="站点名称重复"):
        load_sites(path, allowed_parsers={"named_block"})


def test_invalid_json_or_non_list_root_is_rejected(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(ConfigError, match="JSON格式错误"):
        load_sites(invalid, allowed_parsers={"named_block"})

    wrong_root = tmp_path / "wrong-root.json"
    wrong_root.write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigError, match="根节点必须是数组"):
        load_sites(wrong_root, allowed_parsers={"named_block"})
