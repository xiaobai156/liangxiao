from __future__ import annotations

import re

from domain.models import DOCUMENT_BOUNDARY, Record, Site
from parsers.dynamic import parse_manager_article_two_zodiac_records


def parse_exact_manager_article_records(
    source: str,
    site: Site,
    *,
    expected_name: str,
    expected_article_id: str,
) -> list[Record]:
    article_match = re.search(r"/article/manager/([^/?#]+)", site.url, flags=re.I)
    if (
        site.name != expected_name
        or site.pick != "bottom"
        or site.payload != "admin_article_api"
        or article_match is None
        or article_match.group(1) != expected_article_id
        or DOCUMENT_BOUNDARY in source
    ):
        return []
    return parse_manager_article_two_zodiac_records(source, site)


def parse_zhuangba_disheng_manager_article_records(source: str, site: Site) -> list[Record]:
    return parse_exact_manager_article_records(
        source,
        site,
        expected_name="妆罢低声",
        expected_article_id="6a4e87bd57dc857ae1c13558",
    )


def parse_yanji_dushan_manager_article_records(source: str, site: Site) -> list[Record]:
    return parse_exact_manager_article_records(
        source,
        site,
        expected_name="燕姬独擅",
        expected_article_id="6a64ec62b3f65fed7d6286e7",
    )


def parse_jinma_dushen_manager_article_records(source: str, site: Site) -> list[Record]:
    return parse_exact_manager_article_records(
        source,
        site,
        expected_name="金码赌神",
        expected_article_id="6a081b9be0d076537e1df8a6",
    )
