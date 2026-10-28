"""封存站点「金禅彩票」（301 期，用户点名封存）。

只改 config/sites.json、config/archived_sites.json、recent_10_cache.json，
并清理该站的 301 期失败记录（封存站不再抓取，失败记录无意义）。
备份写进 audit/archive-jinchan-301-<时间戳>/，不生成 .bak。

封存原因（实测）：后台文章已被站方下架 ——
- /api/proxy/admin-articles/6a324276f21d7a093399ef17 -> 404
- /api/proxy/manager-articles/6a324276f21d7a093399ef17 -> 404「AdminArticle 不存在」
- landing-page-data?url=jbx（9.8MB、261 篇文章、86 站点）全文搜「金禅」「禅」均为 0 次
- 浏览器打开文章页仅 45 字并显示「Failed to fetch」
- 301 期两篇【绝杀二肖】文章（妙手回春/稳定下注）正文站名只有《一笔抹杀》《富贵可得》，非换 ID 重发
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cache.contracts import config_fingerprint  # noqa: E402
from cache.duplicates import audit_cache_coverage  # noqa: E402
from config.loader import load_sites  # noqa: E402
from output.transaction import atomic_write_bytes  # noqa: E402
from parsers.registry import ENGINE_REGISTRY  # noqa: E402

CONFIG = ROOT / "config" / "sites.json"
ARCHIVED = ROOT / "config" / "archived_sites.json"
CACHE = ROOT / "recent_10_cache.json"
FAILURE = Path(r"C:\Users\Administrator\Desktop\每天工具\爬虫合集\七类数据统一归纳失败\301期-二肖-失败.txt")

NAME = "金禅彩票"
URL = "https://xjsjbkv.b3e4x-wxhjo-lbdbjg.xyz:29477/article/manager/6a324276f21d7a093399ef17?url=jbx"
REASON = (
    "用户点名封存（301 期）：站方后台文章已下架 —— "
    "/api/proxy/admin-articles/6a324276f21d7a093399ef17 与 /api/proxy/manager-articles/6a324276f21d7a093399ef17 均 404；"
    "landing-page-data?url=jbx（9,799,257 B、261 篇文章、86 个站点）全文搜「金禅」「禅」均 0 次；"
    "浏览器打开文章页仅 45 字并显示「Failed to fetch」；"
    "301 期两篇【绝杀二肖】文章正文站名仅《一笔抹杀》《富贵可得》，非换 ID 重发。"
    "该站 300 期仍正常（缓存 300=狗蛇），300->301 之间被删帖，301 期无法取值，不再抓取"
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encode(data: object, original: bytes) -> bytes:
    """保持原文件的 BOM、换行风格与结尾换行，避免改出无关 diff。"""
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    if b"\r\n" in original:
        body = body.replace(b"\n", b"\r\n")
    bom = b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b""
    tail = b"\r\n" if original.endswith(b"\r\n") else b"\n" if original.endswith(b"\n") else b""
    return bom + body + tail


def main() -> int:
    config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    archived = json.loads(ARCHIVED.read_text(encoding="utf-8-sig"))
    cache = json.loads(CACHE.read_text(encoding="utf-8-sig"))

    config_matches = [item for item in config if item.get("name") == NAME and item.get("url") == URL]
    if len(config_matches) != 1:
        raise RuntimeError(f"活跃配置目标数量异常：{NAME} {len(config_matches)}")
    if any(item.get("name") == NAME or item.get("url") == URL for item in archived):
        raise RuntimeError(f"封存配置已存在同名或同URL记录：{NAME}")
    cache_matches = [item for item in cache["sites"] if item.get("name") == NAME and item.get("url") == URL]
    if len(cache_matches) != 1:
        raise RuntimeError(f"缓存目标数量异常：{NAME} {len(cache_matches)}")

    audit = ROOT / "audit" / f"archive-jinchan-301-{datetime.now():%Y%m%d_%H%M%S}"
    audit.mkdir(parents=True, exist_ok=False)
    before: dict[str, str] = {}
    backup_targets = [CONFIG, ARCHIVED, CACHE]
    if FAILURE.exists():
        backup_targets.append(FAILURE)
    for path in backup_targets:
        shutil.copy2(path, audit / f"{path.name}.before")
        before[path.name] = digest(path)
    print(f"隔离备份目录：{audit}")

    entry = dict(config_matches[0])
    entry["archive_reason"] = REASON

    new_config = [item for item in config if item is not config_matches[0]]
    new_archived = list(archived) + [entry]
    cache_before = len(cache["sites"])
    cache["sites"] = [item for item in cache["sites"] if item is not cache_matches[0]]
    cache["updated_at"] = datetime.now().isoformat(timespec="seconds")
    print(f"配置站点数：{len(config)} -> {len(new_config)}｜归档条数：{len(archived)} -> {len(new_archived)}"
          f"｜缓存站点数：{cache_before} -> {len(cache['sites'])}")

    snapshots = {path: path.read_bytes() for path in backup_targets}
    try:
        atomic_write_bytes(CONFIG, encode(new_config, snapshots[CONFIG]))
        atomic_write_bytes(ARCHIVED, encode(new_archived, snapshots[ARCHIVED]))
        loaded = load_sites(CONFIG, allowed_parsers=ENGINE_REGISTRY)
        if any(site.name == NAME or site.url == URL for site in loaded):
            raise RuntimeError(f"封存后目标仍在活跃加载器：{NAME}")
        cache["config_fingerprint"] = config_fingerprint(loaded)
        atomic_write_bytes(CACHE, encode(cache, snapshots[CACHE]))

        # 清理该站失败记录（封存站不再抓取）
        removed_lines = []
        if FAILURE.exists():
            kept = []
            for raw in snapshots[FAILURE].splitlines(keepends=True):
                text = raw.decode("utf-8-sig").strip()
                if text and text.split(" ")[0] == NAME:
                    removed_lines.append(NAME)
                    continue
                kept.append(raw)
            if removed_lines:
                remaining = [l for l in b"".join(kept).decode("utf-8-sig").splitlines() if l.strip()]
                if remaining:
                    atomic_write_bytes(FAILURE, b"".join(kept))
                    print(f"失败清单已移除 {NAME}，剩余 {len(remaining)} 行")
                else:
                    FAILURE.unlink()
                    print(f"失败清单已移除 {NAME}，清单为空已删除文件")
        print("清除失败行：", removed_lines or "无")

        check_archived = json.loads(ARCHIVED.read_text(encoding="utf-8-sig"))
        check_cache = json.loads(CACHE.read_text(encoding="utf-8-sig"))
        if sum(item.get("name") == NAME for item in check_archived) != 1:
            raise RuntimeError(f"封存配置目标数量异常：{NAME}")
        if any(item.get("name") == NAME or item.get("url") == URL for item in check_cache["sites"]):
            raise RuntimeError(f"封存后缓存仍有目标残留：{NAME}")
        coverage = audit_cache_coverage(check_cache, loaded)
        if coverage.missing_sites:
            raise RuntimeError(f"缓存缺失目录：{coverage.missing_sites}")
    except BaseException:
        for path, payload in snapshots.items():
            atomic_write_bytes(path, payload)
        raise

    ok = [item for item in check_archived if item.get("name") == NAME and item.get("url") == URL][0]
    restored = {key: value for key, value in ok.items() if key != "archive_reason"}
    print(f"[{NAME}] 归档条目与原始条目逐字段一致：", restored == config_matches[0])
    print("封存后活跃站点数：", len(loaded))
    print("归档条数：", len(check_archived))
    print("缓存站点数：", len(check_cache["sites"]))
    print("缓存指纹已重算：", check_cache["config_fingerprint"][:16])
    print("缓存覆盖校验通过；不足10期目录：", ",".join(coverage.incomplete_sites) or "无")
    print()
    print("=== 文件哈希（前 32 位）===")
    for path in backup_targets:
        if not path.exists():
            print(f"  {path.name}: {before[path.name][:32]} -> （已删除）")
            continue
        after = digest(path)
        print(f"  {path.name}: {before[path.name][:32]} -> {after[:32]}"
              f"{'（未变）' if before[path.name] == after else '（已变）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
