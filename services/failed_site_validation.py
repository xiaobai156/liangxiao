from __future__ import annotations

from collections.abc import Callable

from domain.models import Result, Site
from fetching.client import FetchContext
from parsers.registry import ParserRegistry
from services.single_period import scrape_sites


def validate_failed_sites(
    sites: list[Site],
    period: int,
    timeout: int = 25,
    workers: int = 10,
    *,
    context: FetchContext | None = None,
    registry: ParserRegistry | None = None,
    progress: Callable[[str], None] | None = print,
) -> list[Result]:
    return scrape_sites(
        sites,
        period,
        timeout,
        workers,
        context=context,
        registry=registry,
        progress=progress,
    )


def validation_summary(result: Result, period: int) -> str:
    if result.ok and result.record is not None:
        return (
            f"{result.site.name}：抓到{period}期；实际生肖={result.record.zodiac}；"
            f"方向={result.site.pick}；位置={result.record.position}；锚点/关键词/生肖=通过；同期冲突=无"
        )
    return f"{result.site.name}：未抓到{period}期；方向={result.site.pick}；失败原因={result.error}"
