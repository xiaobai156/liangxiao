from __future__ import annotations

from domain.models import Site
from fetching.client import FetchContext
from fetching.page import fetch_topic_list_detail_payload
from parsers.registry import ParserRegistry
from validation.direction import select_record


TITLE = r"斗转星移\s*[【〖\[]\s*绝\s*杀\s*(?:二|两|2|２|②)\s*肖\s*[】〗\]]"
RECORD = (
    r"(?P<period>\d{3})\s*期\s*斗转星移\s*[:：]?\s*"
    r"[【〖\[]\s*(?P<zodiac>[牛马羊鸡狗猪鼠虎兔龙蛇猴]"
    r"\s*[-－、,，.。· ]?\s*[牛马羊鸡狗猪鼠虎兔龙蛇猴])\s*[】〗\]]"
    r"\s*[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)"
)


def test_ttss_list_detail_follows_next_pages_and_reads_article_body() -> None:
    site = Site(
        "斗转星移",
        "top",
        "https://example.test/list.aspx?id=79&page=1",
        parser="named_block",
        title=TITLE,
        record=RECORD,
        payload="topic_list_detail",
    )
    pages = {
        "https://example.test/list.aspx?id=79&page=1": (
            '<a href="article.aspx?id=1">218期: 其他站【绝杀二肖】</a>'
            '<a href="list.aspx?id=79&page=2">下一页</a>'
        ),
        "https://example.test/list.aspx?id=79&page=2": '<a href="list.aspx?id=79&page=3">下一页</a>',
        "https://example.test/list.aspx?id=79&page=3": '<a href="list.aspx?id=79&page=4">下一页</a>',
        "https://example.test/list.aspx?id=79&page=4": '<a href="list.aspx?id=79&page=5">下一页</a>',
        "https://example.test/list.aspx?id=79&page=5": (
            '<a href="article.aspx?id=972402">218期: 斗转星移【绝杀二肖】已免费公开</a>'
        ),
        "https://example.test/article.aspx?id=972402": (
            '<div class="big-tit">218期: 斗转星移【绝杀二肖】已免费公开</div>'
            '<p>218期 斗转星移 :【兔鼠】 开 ?? 准</p>'
        ),
    }
    context = FetchContext(text_fetcher=lambda url, _timeout: pages[url], renderer=lambda *_args: "")

    bundle = fetch_topic_list_detail_payload(site, 218, 3, context)
    assert [document.label for document in bundle.documents] == ["详情页"]
    assert bundle.documents[0].record_id == "article:972402"

    registry = ParserRegistry.bind_sites([site])
    selected = select_record(registry.parse(bundle.documents[0], site), 218, site)
    assert selected.zodiac == "兔鼠"
