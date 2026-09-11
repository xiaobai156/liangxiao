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
TARGETS = (
    (
        "真龙活现",
        "https://mbsqhpk.8ivvt-u3cx5-enwlld.xyz:29400/article/manager/"
        "6a33d2c6dfa16552b923d0ad?url=lhzj",
        "未找到指定目标：未抓到有效候选",
    ),
    (
        "会员福利",
        "https://sheuzjss.tgpcj-9w0vl-mwyhly.work:29422/article/manager/"
        "6a0458354ea5c20141013eb4?url=zfw",
        "未找到指定目标：接口返回内容内未找到对应后台文章",
    ),
    (
        "大有文章",
        "https://0130190808.880646.xyz/bbs/topic.php?id=20685",
        "未找到指定目标：未抓到有效候选",
    ),
)


def main() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    archived = json.loads(ARCHIVED.read_text(encoding="utf-8-sig"))
    cache = json.loads(CACHE.read_text(encoding="utf-8-sig"))

    archived_items: list[dict[str, object]] = []
    for name, url, reason in TARGETS:
        matches = [
            item for item in config
            if item.get("name") == name and item.get("url") == url
        ]
        if len(matches) != 1:
            raise RuntimeError(f"活跃配置目标数量异常：{name} -> {len(matches)}")
        if any(item.get("name") == name or item.get("url") == url for item in archived):
            raise RuntimeError(f"封存配置已存在同名或同URL记录：{name}")
        archived_item = dict(matches[0])
        archived_item["archive_reason"] = reason
        archived_items.append(archived_item)

    keys = {(name, url) for name, url, _reason in TARGETS}
    new_config = [
        item for item in config
        if (item.get("name"), item.get("url")) not in keys
    ]
    new_archived = [*archived, *archived_items]
    cache["sites"] = [
        item for item in cache["sites"]
        if (item.get("name"), item.get("url")) not in keys
    ]
    cache["updated_at"] = datetime.now().isoformat(timespec="seconds")

    payloads = {
        CONFIG: json.dumps(new_config, ensure_ascii=False, indent=2).encode("utf-8") + b"\n",
        ARCHIVED: json.dumps(new_archived, ensure_ascii=False, indent=2).encode("utf-8") + b"\n",
        CACHE: json.dumps(cache, ensure_ascii=False, indent=2).encode("utf-8"),
    }
    snapshots = {path: path.read_bytes() for path in payloads}
    try:
        for path, payload in payloads.items():
            atomic_write_bytes(path, payload)
        loaded = load_sites(CONFIG, allowed_parsers=ENGINE_REGISTRY)
        for name, url, _reason in TARGETS:
            if any(site.name == name or site.url == url for site in loaded):
                raise RuntimeError(f"封存后目标仍在活跃加载器：{name}")
        check_archived = json.loads(ARCHIVED.read_text(encoding="utf-8-sig"))
        check_cache = json.loads(CACHE.read_text(encoding="utf-8-sig"))
        for name, url, _reason in TARGETS:
            if sum(1 for item in check_archived if item.get("name") == name) != 1:
                raise RuntimeError(f"封存配置目标数量异常：{name}")
            if any(
                item.get("name") == name or item.get("url") == url
                for item in check_cache["sites"]
            ):
                raise RuntimeError(f"封存后缓存仍有目标残留：{name}")
    except BaseException:
        for path, payload in snapshots.items():
            atomic_write_bytes(path, payload)
        raise

    print(
        json.dumps(
            {
                "archived": [name for name, _url, _reason in TARGETS],
                "active_count": len(new_config),
                "archived_count": len(new_archived),
                "cache_count": len(cache["sites"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
