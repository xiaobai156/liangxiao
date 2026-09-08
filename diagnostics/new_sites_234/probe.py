from __future__ import annotations

import hashlib
import json
from pathlib import Path

from domain.models import Site
from fetching.client import FetchContext
from fetching.dynamic_article import (
    admin_article_id,
    decode_admin_article_api_response,
    landing_page_data_api_url,
)
from parsers.dynamic import parse_manager_article_two_zodiac_records
from validation.direction import direction_window, select_record


ROOT = Path(__file__).resolve().parents[2]
FORMAL_FILES = (ROOT / "config" / "sites.json", ROOT / "recent_10_cache.json")
TARGETS = (
    (
        "妆罢低声",
        "https://fansiggg.jc2q8-whfmg-vajjui.xyz:29433/article/manager/6a4e87bd57dc857ae1c13558?url=dyj",
    ),
    (
        "燕姬独擅",
        "https://fansiggg.jc2q8-whfmg-vajjui.xyz:29433/article/manager/6a64ec62b3f65fed7d6286e7?url=dyj",
    ),
    (
        "金码赌神",
        "https://vnxqseiu.ymm13-381iq-zcgmtu.xyz:29400/article/manager/6a081b9be0d076537e1df8a6?url=jdb",
    ),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    before = {str(path): digest(path) for path in FORMAL_FILES}
    context = FetchContext()
    output: dict[str, object] = {"before": before, "sites": {}}
    for name, url in TARGETS:
        site = Site(
            name=name,
            pick="bottom",
            url=url,
            parser="manager_article_two_zodiac",
            payload="admin_article_api",
            api_url=landing_page_data_api_url(url),
        )
        api_source = context.get_text(site.api_url, 25)
        bundle = decode_admin_article_api_response(api_source, admin_article_id(url), site.api_url)
        if len(bundle.documents) != 1:
            output["sites"][name] = {
                "article_id": admin_article_id(url),
                "api_url": site.api_url,
                "error": "接口未唯一返回目标文章",
            }
            continue
        document = bundle.documents[0]
        records = parse_manager_article_two_zodiac_records(document.source, site)
        window = direction_window(records, site)
        try:
            selected = select_record(records, 234, site)
            selected_value: dict[str, object] = {
                "ok": True,
                "zodiac": selected.zodiac,
                "position": selected.position,
            }
        except ValueError as exc:
            selected_value = {"ok": False, "error": str(exc)}
        output["sites"][name] = {
            "article_id": admin_article_id(url),
            "api_url": site.api_url,
            "document_record_id": document.record_id,
            "record_path": document.record_path,
            "record_count": document.record_count,
            "records": [
                {
                    "period": record.period,
                    "zodiac": record.zodiac,
                    "position": record.position,
                    "raw": record.raw,
                }
                for record in records
            ],
            "window": [
                {
                    "period": record.period,
                    "zodiac": record.zodiac,
                    "position": record.position,
                }
                for record in window
            ],
            "selected_234": selected_value,
        }
    after = {str(path): digest(path) for path in FORMAL_FILES}
    output["after"] = after
    output["formal_unchanged"] = before == after
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
