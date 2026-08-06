from __future__ import annotations

from pathlib import Path

from config.loader import load_sites
from parsers.registry import ENGINE_REGISTRY


ROOT = Path(__file__).resolve().parents[1]


EXPECTED = {
    "催人奋进": {
        "pick": "top",
        "url": "https://dh-nwsm-0806.yqs01490156c.app/gst/20.htm",
        "parser": "site_scoped_two_zodiac",
        "payload": "page",
    },
    "发奋图强": {
        "pick": "top",
        "url": "https://dh-nwsm-0806.yqs01490156c.app/gsb/11.htm",
        "parser": "site_scoped_two_zodiac",
        "payload": "page",
    },
    "志在四方": {
        "pick": "top",
        "url": "https://dh-nwsm-0806.yqs01490156c.app/gst/58.htm",
        "parser": "site_scoped_two_zodiac",
        "payload": "page",
    },
    "周大发财": {
        "pick": "top",
        "url": "https://dh-nwsm-0806.yqs01490156c.app/cm/cm101.html",
        "parser": "site_scoped_two_zodiac",
        "payload": "page",
    },
    "超越自我": {
        "pick": "bottom",
        "url": "https://cahgjib.5blx9-z8506-ekiwxc.work:29488/article/manager/6a1446e9bf0a6cb1dd38fbad?url=lqz",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://cahgjib.5blx9-z8506-ekiwxc.work:29488/api/proxy/landing-page-data?url=lqz",
    },
    "四季如春": {
        "pick": "bottom",
        "url": "https://pgyzulb.iwnn7-gyyip-pnpfqv.work:29477/article/manager/6a153c328be59b17287c6ceb?url=bflc",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://pgyzulb.iwnn7-gyyip-pnpfqv.work:29477/api/proxy/landing-page-data?url=bflc",
    },
    "原原本本": {
        "pick": "bottom",
        "url": "https://pgyzulb.iwnn7-gyyip-pnpfqv.work:29477/article/manager/6a153eb8d9d9fc2cea52419b?url=bflc",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://pgyzulb.iwnn7-gyyip-pnpfqv.work:29477/api/proxy/landing-page-data?url=bflc",
    },
    "会员福利": {
        "pick": "bottom",
        "url": "https://sheuzjss.tgpcj-9w0vl-mwyhly.work:29422/article/manager/6a0458354ea5c20141013eb4?url=zfw",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://sheuzjss.tgpcj-9w0vl-mwyhly.work:29422/api/proxy/landing-page-data?url=zfw",
    },
    "其乐融融": {
        "pick": "bottom",
        "url": "https://sheuzjss.tgpcj-9w0vl-mwyhly.work:29422/article/manager/6a62b371b3f65fed7d61cb4b?url=zfw",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://sheuzjss.tgpcj-9w0vl-mwyhly.work:29422/api/proxy/landing-page-data?url=zfw",
    },
    "澳门马头": {
        "pick": "bottom",
        "url": "https://qgyhdauu.avht7-lah7b-oavfmr.work:29444/article/manager/6a6c7d332822d465035d6d7e?url=wzw",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://qgyhdauu.avht7-lah7b-oavfmr.work:29444/api/proxy/landing-page-data?url=wzw",
    },
    "极目远眺": {
        "pick": "bottom",
        "url": "https://uqdccnri.oy2bh-swrfr-gvzxkk.work:29499/article/manager/6a03354dcd2fbc900f6db83a?url=lcz",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://uqdccnri.oy2bh-swrfr-gvzxkk.work:29499/api/proxy/landing-page-data?url=lcz",
    },
    "再创六合": {
        "pick": "bottom",
        "url": "https://uqdccnri.oy2bh-swrfr-gvzxkk.work:29499/article/manager/6a03230a56a1d26b7774455f?url=lcz",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://uqdccnri.oy2bh-swrfr-gvzxkk.work:29499/api/proxy/landing-page-data?url=lcz",
    },
    "马会综合": {
        "pick": "bottom",
        "url": "https://herymche.x6l2j-h6kfu-qdresg.work:29466/article/manager/6a0452024ea5c20141013e9a?url=txbb",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://herymche.x6l2j-h6kfu-qdresg.work:29466/api/proxy/landing-page-data?url=txbb",
    },
    "百紫千红": {
        "pick": "bottom",
        "url": "https://herymche.x6l2j-h6kfu-qdresg.work:29466/article/manager/6a043cad4ea5c20141013e55?url=txbb",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://herymche.x6l2j-h6kfu-qdresg.work:29466/api/proxy/landing-page-data?url=txbb",
    },
    "心情愉悦": {
        "pick": "bottom",
        "url": "https://asmfkb.dfkxu-0rwnp-wevqzo.work:29466/article/manager/6a106fb2e55f3de51a257209?url=fcw",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://asmfkb.dfkxu-0rwnp-wevqzo.work:29466/api/proxy/landing-page-data?url=fcw",
    },
    "神机妙算": {
        "pick": "bottom",
        "url": "https://asmfkb.dfkxu-0rwnp-wevqzo.work:29466/article/manager/6a4b0f8f57dc857ae1c0289a?url=fcw",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://asmfkb.dfkxu-0rwnp-wevqzo.work:29466/api/proxy/landing-page-data?url=fcw",
    },
    "实力巨献": {
        "pick": "bottom",
        "url": "https://asmfkb.dfkxu-0rwnp-wevqzo.work:29466/article/manager/6a09b3e6291caff3edcb90a7?url=fcw",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://asmfkb.dfkxu-0rwnp-wevqzo.work:29466/api/proxy/landing-page-data?url=fcw",
    },
    "一笔抹杀": {
        "pick": "bottom",
        "url": "https://sndaygl.egjtc-sgs8w-taclan.work:29400/article/manager/6a140a07597e16d57eacb49b?url=nmw",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://sndaygl.egjtc-sgs8w-taclan.work:29400/api/proxy/landing-page-data?url=nmw",
    },
    "起早摸黑": {
        "pick": "bottom",
        "url": "https://sndaygl.egjtc-sgs8w-taclan.work:29400/article/manager/6a140d934346bc68aea4ef37?url=nmw",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://sndaygl.egjtc-sgs8w-taclan.work:29400/api/proxy/landing-page-data?url=nmw",
    },
    "最猛夺彩": {
        "pick": "bottom",
        "url": "https://ztpqrap.m8sbq-na911-wmojzb.work:29444/article/manager/6a1450cb597e16d57eacb699?url=gsw",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://ztpqrap.m8sbq-na911-wmojzb.work:29444/api/proxy/landing-page-data?url=gsw",
    },
    "飘香一剑": {
        "pick": "bottom",
        "url": "https://ifnkblf.e4kce-krr7o-vuvqhd.work:29411/article/manager/6a145e517b484ee0e062924d?url=scyd",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://ifnkblf.e4kce-krr7o-vuvqhd.work:29411/api/proxy/landing-page-data?url=scyd",
    },
    "塞纳河畔": {
        "pick": "bottom",
        "url": "https://plwyrcj.4ai8j-p62x5-pnsukp.work:29411/article/manager/6a1721a81d09553bfe753e03?url=gjp",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://plwyrcj.4ai8j-p62x5-pnsukp.work:29411/api/proxy/landing-page-data?url=gjp",
    },
    "神童网料": {
        "pick": "bottom",
        "url": "https://fymxwnyg.ttmzc-muvns-udlfln.work:29455/article/manager/6a096b5f291caff3edcb8b9e?url=lhbd",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://fymxwnyg.ttmzc-muvns-udlfln.work:29455/api/proxy/landing-page-data?url=lhbd",
    },
    "少女臆想": {
        "pick": "bottom",
        "url": "https://jusbfyu.mkdwi-xa2ua-rsovan.work:29422/article/manager/6a322f04f21d7a093399eeac?url=hjc",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://jusbfyu.mkdwi-xa2ua-rsovan.work:29422/api/proxy/landing-page-data?url=hjc",
    },
    "风雨无阻": {
        "pick": "bottom",
        "url": "https://dgjdlk.0hrwo-8qjsc-spwvba.xyz:29411/article/manager/6a12d7686cb8d34478078ae8?url=cf",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://dgjdlk.0hrwo-8qjsc-spwvba.xyz:29411/api/proxy/landing-page-data?url=cf",
    },
    "黄大仙道": {
        "pick": "bottom",
        "url": "https://hl.www25195a.com/read.php?tid=684",
        "parser": "site_scoped_two_zodiac",
        "payload": "page",
        "title": "黄大仙道",
    },
    "曲尽其妙": {
        "pick": "bottom",
        "url": "https://18118.73829.com/read.php?tid=473",
        "parser": "site_scoped_two_zodiac",
        "payload": "page",
    },
    "官网透码": {
        "pick": "bottom",
        "url": "https://aszmkf.c3z3l-qrlqm-mwgccr.work:29411/article/manager/6a0833cc08adb5ed7357ef83?url=lhw",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://aszmkf.c3z3l-qrlqm-mwgccr.work:29411/api/proxy/landing-page-data?url=lhw",
    },
    "马上有钱": {
        "pick": "bottom",
        "url": "https://jkofyya.6sf58-wbz5a-thviwx.work:29477/article/manager/6a13e385741e3e91a04e5954?url=jbp",
        "parser": "manager_article_two_zodiac",
        "payload": "admin_article_api",
        "api_url": "https://jkofyya.6sf58-wbz5a-thviwx.work:29477/api/proxy/landing-page-data?url=jbp",
    },
}


def test_pending_sites_have_unique_names_and_explicit_bindings() -> None:
    sites = load_sites(ROOT / "config" / "sites.json", allowed_parsers=ENGINE_REGISTRY)
    configured = {site.name: site for site in sites}

    assert set(EXPECTED) <= set(configured)
    for name, expected in EXPECTED.items():
        site = configured[name]
        assert site.pick == expected["pick"]
        assert site.url == expected["url"]
        assert site.parser == expected["parser"]
        assert site.payload == expected["payload"]
        if "api_url" in expected:
            assert site.api_url == expected["api_url"]
