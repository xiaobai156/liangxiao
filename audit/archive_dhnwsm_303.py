"""封存站点「催人奋进」「发奋图强」「志在四方」（303 期，用户点名封存）。

三站同属一台主机 dh-nwsm-0806.yqs01490156c.app，整机不可达：
- requests 报 SSLError(SSLEOFError(8, 'EOF occurred in violation of protocol'))
- 忽略证书后报 ConnectionError('Connection aborted.', FileNotFoundError(2, ...))
- Playwright 报 net::ERR_CONNECTION_CLOSED
- 302 期已扫过同族 444 个子域（yqs01490156c.app / cmw01490353c.app 的 dh-nwsm-0601~1231 及 www/m/wap），
  全部指向同一 IP 20.120.208.73 且同样不通，换子域无法修复

只改 config/sites.json、config/archived_sites.json、recent_10_cache.json，
并清理三站的 303 期失败记录。备份写进 audit/archive-dhnwsm-303-<时间戳>/，不生成 .bak。
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
FAILURE = Path(r"C:\Users\Administrator\Desktop\每天工具\爬虫合集\七类数据统一归纳失败\303期-二肖-失败.txt")

TARGETS = {
    "催人奋进": "https://dh-nwsm-0806.yqs01490156c.app/gst/20.htm",
    "发奋图强": "https://dh-nwsm-0806.yqs01490156c.app/gsb/11.htm",
    "志在四方": "https://dh-nwsm-0806.yqs01490156c.app/gst/58.htm",
}
REASON = (
    "用户点名封存（303 期）：主机 dh-nwsm-0806.yqs01490156c.app 整机不可达 —— "
    "requests 报 SSLError(SSLEOFError(8, 'EOF occurred in violation of protocol (_ssl.c:1007)'))，"
    "忽略证书后报 ConnectionError('Connection aborted.', FileNotFoundError(2))，"
    "Playwright 报 net::ERR_CONNECTION_CLOSED；"
    "302 期已扫过同族 444 个子域（yqs01490156c.app / cmw01490353c.app 的 dh-nwsm-0601~1231 及 www/m/wap），"
    "全部解析到同一 IP 20.120.208.73 且同样不通，换子域无法修复；"
    "303 期与 302 期均无任何可用入口，不再抓取"
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

    config_targets = []
    for name, url in TARGETS.items():
        matches = [item for item in config if item.get("name") == name and item.get("url") == url]
        if len(matches) != 1:
            raise RuntimeError(f"活跃配置目标数量异常：{name} {len(matches)}")
        if any(item.get("name") == name or item.get("url") == url for item in archived):
            raise RuntimeError(f"封存配置已存在同名或同URL记录：{name}")
        config_targets.append(matches[0])

    cache_targets = []
    for name, url in TARGETS.items():
        matches = [item for item in cache["sites"] if item.get("name") == name and item.get("url") == url]
        if len(matches) != 1:
            raise RuntimeError(f"缓存目标数量异常：{name} {len(matches)}")
        cache_targets.append(matches[0])

    audit = ROOT / "audit" / f"archive-dhnwsm-303-{datetime.now():%Y%m%d_%H%M%S}"
    audit.mkdir(parents=True, exist_ok=False)
    before: dict[str, str] = {}
    backup_targets = [CONFIG, ARCHIVED, CACHE]
    if FAILURE.exists():
        backup_targets.append(FAILURE)
    for path in backup_targets:
        shutil.copy2(path, audit / f"{path.name}.before")
        before[path.name] = digest(path)
    print(f"隔离备份目录：{audit}")

    entries = []
    for item in config_targets:
        entry = dict(item)
        entry["archive_reason"] = REASON
        entries.append(entry)

    drop_ids = {id(item) for item in config_targets}
    drop_cache_ids = {id(item) for item in cache_targets}
    new_config = [item for item in config if id(item) not in drop_ids]
    new_archived = list(archived) + entries
    cache_before = len(cache["sites"])
    cache["sites"] = [item for item in cache["sites"] if id(item) not in drop_cache_ids]
    cache["updated_at"] = datetime.now().isoformat(timespec="seconds")
    print(f"配置站点数：{len(config)} -> {len(new_config)}｜归档条数：{len(archived)} -> {len(new_archived)}"
          f"｜缓存站点数：{cache_before} -> {len(cache['sites'])}")

    snapshots = {path: path.read_bytes() for path in backup_targets}
    try:
        atomic_write_bytes(CONFIG, encode(new_config, snapshots[CONFIG]))
        atomic_write_bytes(ARCHIVED, encode(new_archived, snapshots[ARCHIVED]))
        loaded = load_sites(CONFIG, allowed_parsers=ENGINE_REGISTRY)
        leftovers = [site.name for site in loaded if site.name in TARGETS]
        if leftovers:
            raise RuntimeError(f"封存后目标仍在活跃加载器：{leftovers}")
        cache["config_fingerprint"] = config_fingerprint(loaded)
        atomic_write_bytes(CACHE, encode(cache, snapshots[CACHE]))

        # 清理三站失败记录（封存站不再抓取）
        removed_lines = []
        if FAILURE.exists():
            kept = []
            for raw in snapshots[FAILURE].splitlines(keepends=True):
                text = raw.decode("utf-8-sig").strip()
                if text and text.split(" ")[0] in TARGETS:
                    removed_lines.append(text.split(" ")[0])
                    continue
                kept.append(raw)
            if removed_lines:
                remaining = [l for l in b"".join(kept).decode("utf-8-sig").splitlines() if l.strip()]
                if remaining:
                    atomic_write_bytes(FAILURE, b"".join(kept))
                    print(f"失败清单已移除 {removed_lines}，剩余 {len(remaining)} 行")
                else:
                    FAILURE.unlink()
                    print(f"失败清单已移除 {removed_lines}，清单为空已删除文件")
        print("清除失败行：", removed_lines or "无")

        check_archived = json.loads(ARCHIVED.read_text(encoding="utf-8-sig"))
        check_cache = json.loads(CACHE.read_text(encoding="utf-8-sig"))
        for name in TARGETS:
            if sum(item.get("name") == name for item in check_archived) != 1:
                raise RuntimeError(f"封存配置目标数量异常：{name}")
            if any(item.get("name") == name for item in check_cache["sites"]):
                raise RuntimeError(f"封存后缓存仍有目标残留：{name}")
        coverage = audit_cache_coverage(check_cache, loaded)
        if coverage.missing_sites:
            raise RuntimeError(f"缓存缺失目录：{coverage.missing_sites}")
    except BaseException:
        for path, payload in snapshots.items():
            atomic_write_bytes(path, payload)
        raise

    for entry in entries:
        name = entry["name"]
        ok = [item for item in check_archived if item.get("name") == name][0]
        restored = {key: value for key, value in ok.items() if key != "archive_reason"}
        original = [item for item in config_targets if item.get("name") == name][0]
        print(f"[{name}] 归档条目与原始条目逐字段一致：", restored == original)
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
