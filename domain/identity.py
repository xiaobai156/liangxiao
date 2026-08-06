from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse


def detail_record_identity(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or ""
    lowered_path = path.lower()
    topic_match = re.search(r"/topic/([^/?#]+)", path, flags=re.I)
    if topic_match:
        topic_id = re.sub(r"\.html?$", "", topic_match.group(1), flags=re.I)
        return f"topic:{topic_id}"
    if re.search(r"/(?:view|read)\.php$", lowered_path, flags=re.I):
        query = {key.lower(): values for key, values in parse_qs(parsed.query).items()}
        key = "id" if lowered_path.endswith("/view.php") else "tid"
        value = (query.get(key) or [""])[0].strip()
        if value:
            return f"{'view' if key == 'id' else 'read'}:{value}"
    if lowered_path.endswith("/article.aspx") or lowered_path.endswith("/article.asp"):
        query = {key.lower(): values for key, values in parse_qs(parsed.query).items()}
        record_id = (query.get("id") or [""])[0].strip()
        if record_id:
            list_id = (query.get("listid") or [""])[0].strip()
            return f"article:{list_id}:{record_id}" if list_id else f"article:{record_id}"
    if lowered_path.endswith(".aspx") or lowered_path.endswith("/topic.php"):
        query = {key.lower(): values for key, values in parse_qs(parsed.query).items()}
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
