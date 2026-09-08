from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from config.loader import load_sites
from output.transaction import atomic_write_bytes
from parsers.registry import ENGINE_REGISTRY


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "sites.json"
ARCHIVED = ROOT / "config" / "archived_sites.json"
CACHE = ROOT / "recent_10_cache.json"
NAME = "超越自我"
URL = "https://cahgjib.5blx9-z8506-ekiwxc.work:29488/article/manager/6a1446e9bf0a6cb1dd38fbad?url=lqz"
REASON = "未找到指定目标：接口返回内容内未找到对应后台文章"


def main() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    archived = json.loads(ARCHIVED.read_text(encoding="utf-8-sig"))
    cache = json.loads(CACHE.read_text(encoding="utf-8-sig"))
    matches = [item for item in config if item.get("name") == NAME and item.get("url") == URL]
    if len(matches) != 1:
        raise RuntimeError(f"活跃配置目标数量异常：{len(matches)}")
    if any(item.get("name") == NAME or item.get("url") == URL for item in archived):
        raise RuntimeError("封存配置已存在同名或同URL记录")
    cache_matches = [
        item for item in cache.get("sites", [])
        if item.get("name") == NAME and item.get("url") == URL
    ]
    if len(cache_matches) != 1:
        raise RuntimeError(f"缓存目标数量异常：{len(cache_matches)}")

    archived_item = dict(matches[0])
    archived_item["archive_reason"] = REASON
    new_config = [item for item in config if item is not matches[0]]
    new_archived = [*archived, archived_item]
    cache["sites"] = [
        item for item in cache["sites"]
        if not (item.get("name") == NAME and item.get("url") == URL)
    ]
    cache["updated_at"] = datetime.now().isoformat(timespec="seconds")

    payloads = {
        CONFIG: json.dumps(new_config, ensure_ascii=False, indent=2).encode("utf-8"),
        ARCHIVED: json.dumps(new_archived, ensure_ascii=False, indent=2).encode("utf-8"),
        CACHE: json.dumps(cache, ensure_ascii=False, indent=2).encode("utf-8"),
    }
    snapshots = {path: path.read_bytes() for path in payloads}
    try:
        for path, payload in payloads.items():
            atomic_write_bytes(path, payload)
        loaded = load_sites(CONFIG, allowed_parsers=ENGINE_REGISTRY)
        if any(site.name == NAME or site.url == URL for site in loaded):
            raise RuntimeError("封存后目标仍在活跃加载器")
        check_archived = json.loads(ARCHIVED.read_text(encoding="utf-8-sig"))
        check_cache = json.loads(CACHE.read_text(encoding="utf-8-sig"))
        if sum(item.get("name") == NAME for item in check_archived) != 1:
            raise RuntimeError("封存配置目标数量异常")
        if any(item.get("name") == NAME or item.get("url") == URL for item in check_cache["sites"]):
            raise RuntimeError("封存后缓存仍有目标残留")
    except BaseException:
        for path, payload in snapshots.items():
            atomic_write_bytes(path, payload)
        raise

    print(
        json.dumps(
            {
                "archived": NAME,
                "active_count": len(new_config),
                "archived_count": len(new_archived),
                "cache_count": len(cache["sites"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
