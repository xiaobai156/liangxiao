# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


PROFILE_SCHEMA_VERSION = 1
_PROFILE_LOCK = threading.RLock()
_ZODIAC_CHARS = "牛馬马羊雞鸡狗豬猪鼠虎兔龍龙蛇猴"
_SAFE_ELEMENT_ATTRIBUTES = {
    "class",
    "id",
    "name",
    "role",
    "title",
    "data-type",
    "data-name",
    "aria-label",
}


class ProfileStoreError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def site_profile_key(site: object) -> str:
    configured = str(getattr(site, "profile_id", "") or "").strip()
    if configured:
        return configured
    identity = "\0".join(
        (
            str(getattr(site, "name", "")),
            str(getattr(site, "pick", "")),
            str(getattr(site, "parser", "")),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def site_anchors(site: object) -> list[str]:
    values = [str(getattr(site, "name", "") or "").strip()]
    values.extend(str(value).strip() for value in (getattr(site, "keywords", ()) or ()))
    title = str(getattr(site, "title", "") or "")
    values.extend(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,24}", title))
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def structural_fingerprint(source: str) -> str:
    tags: list[str] = []
    for match in re.finditer(r"<\s*([A-Za-z][\w:-]*)([^>]*)>", source):
        tag = match.group(1).lower()
        attributes = match.group(2)
        names = sorted(
            {
                name.lower()
                for name in re.findall(r"([A-Za-z_:][\w:.-]*)\s*=", attributes)
                if name.lower() not in {"href", "src", "style", "value"}
            }
        )
        stable_values: list[str] = []
        for name in ("class", "id", "data-type", "data-name"):
            value_match = re.search(rf"\b{name}\s*=\s*['\"]([^'\"]+)['\"]", attributes, flags=re.I)
            if value_match:
                value = re.sub(r"\d+", "#", value_match.group(1).strip())
                stable_values.append(f"{name}={value[:80]}")
        tags.append(f"{tag}[{','.join(names)}]({','.join(stable_values)})")
        if len(tags) >= 800:
            break
    if not tags:
        normalized = re.sub(r"\d+", "#", re.sub(r"\s+", " ", source)).strip()[:12000]
    else:
        normalized = "|".join(tags)
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()


def _sanitize_profile_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"\d{3,4}\s*期", " ", text)

    def redact_bracket(match: re.Match[str]) -> str:
        inner = match.group(1)
        return " " if len(re.findall(f"[{_ZODIAC_CHARS}]", inner)) >= 2 else match.group(0)

    text = re.sub(r"[【\[（(]([^】\]）)]{0,80})[】\]）)]", redact_bracket, text)
    text = re.sub(
        rf"(?:[{_ZODIAC_CHARS}][\s,，、/|+·]*){{2,12}}",
        " ",
        text,
    )
    text = re.sub(rf"[{_ZODIAC_CHARS}]", " ", text)
    text = re.sub(r"\d+", "#", text)
    text = re.sub(r"\s+", " ", text).strip(" -_：:，,、|/@")
    return text[:160] or None


def _safe_profile_attributes(values: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in dict(values or {}).items():
        name = str(key).lower()
        if name not in _SAFE_ELEMENT_ATTRIBUTES:
            continue
        cleaned = _sanitize_profile_text(value)
        if cleaned:
            result[str(key)] = cleaned
    return result


def element_to_profile(element: Any) -> dict[str, Any]:
    root = getattr(element, "_root", element)
    parent = root.getparent() if hasattr(root, "getparent") else None
    attributes = _safe_profile_attributes(getattr(root, "attrib", {}))
    path: list[str] = []
    node = root
    while node is not None:
        path.append(str(getattr(node, "tag", "")))
        node = node.getparent() if hasattr(node, "getparent") else None
    result: dict[str, Any] = {
        "tag": str(getattr(root, "tag", "")),
        "attributes": attributes,
        "text": _sanitize_profile_text(getattr(root, "text", "")),
        "path": list(reversed(path)),
    }
    if parent is not None:
        result.update(
            {
                "parent_name": str(getattr(parent, "tag", "")),
                "parent_attribs": _safe_profile_attributes(getattr(parent, "attrib", {})),
                "parent_text": _sanitize_profile_text(getattr(parent, "text", "")),
            }
        )
        siblings = [str(getattr(child, "tag", "")) for child in parent.iterchildren() if child is not root]
        if siblings:
            result["siblings"] = siblings
    if hasattr(root, "iterchildren"):
        children = [str(getattr(child, "tag", "")) for child in root.iterchildren()]
        if children:
            result["children"] = children
    return result


class StructureProfileStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def _process_lock(self, timeout: float = 30.0):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        try:
            handle = lock_path.open("a+b")
        except OSError as exc:
            raise ProfileStoreError(f"结构档案锁打开失败：{lock_path}：{exc}") from exc

        acquired = False
        deadline = time.monotonic() + timeout
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
            while not acquired:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise ProfileStoreError(
                            f"等待结构档案锁超时：{lock_path}"
                        ) from exc
                    time.sleep(0.05)
            yield
        finally:
            if acquired:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()

    @contextmanager
    def _payload(self, write: bool = False):
        with _PROFILE_LOCK:
            with self._process_lock():
                payload = self._read_unlocked()
                yield payload
                if write:
                    self._write_unlocked(payload)

    @staticmethod
    def empty_payload() -> dict[str, Any]:
        return {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "description": "Scrapling页面结构档案；只保存定位特征，不保存正式业务数据。",
            "updated_at": "",
            "sources": {},
        }

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self.empty_payload()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProfileStoreError(f"结构档案读取失败：{self.path}：{exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("sources"), dict):
            raise ProfileStoreError(f"结构档案格式错误：{self.path}")
        if payload.get("schema_version") != PROFILE_SCHEMA_VERSION:
            raise ProfileStoreError(f"不支持的结构档案版本：{payload.get('schema_version')}")
        return payload

    def _write_unlocked(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload["updated_at"] = _now()
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError as exc:
            raise ProfileStoreError(f"结构档案写入失败：{self.path}：{exc}") from exc
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _site_metadata(site: object) -> dict[str, Any]:
        paths = [str(getattr(site, "url", "") or "")]
        api_url = str(getattr(site, "api_url", "") or "")
        if api_url:
            paths.append(api_url)
        return {
            "name": str(getattr(site, "name", "")),
            "url": str(getattr(site, "url", "")),
            "pick": str(getattr(site, "pick", "")),
            "parser": str(getattr(site, "parser", "")),
            "payload": str(getattr(site, "payload", "")),
            "anchors": site_anchors(site),
            "element_features": [],
            "selectors": [],
            "script_patterns": [],
            "iframe_patterns": [],
            "api_patterns": [api_url] if api_url else [],
            "structure_fingerprint": "",
            "last_observed_fingerprint": "",
            "fallback_paths": [value for value in paths if value],
            "candidate_profiles": [],
            "trusted_elements": {},
            "status": "unverified",
            "success_count": 0,
            "failure_count": 0,
            "last_verified_at": "",
            "last_failure": {},
        }

    def _entry(self, payload: dict[str, Any], site: object) -> dict[str, Any]:
        sources = payload["sources"]
        key = site_profile_key(site)
        entry = sources.get(key)
        if not isinstance(entry, dict):
            entry = self._site_metadata(site)
            sources[key] = entry
        else:
            latest = self._site_metadata(site)
            for field in ("name", "url", "pick", "parser", "payload", "anchors", "api_patterns"):
                entry[field] = latest[field]
            entry.setdefault("candidate_profiles", [])
            entry.setdefault("trusted_elements", {})
            entry.setdefault("fallback_paths", [])
            entry.setdefault("last_observed_fingerprint", "")
            entry.setdefault("success_count", 0)
            entry.setdefault("failure_count", 0)
        return entry

    def register_sites(self, sites: Iterable[object]) -> None:
        with self._payload(write=True) as payload:
            for site in sites:
                self._entry(payload, site)

    def get_site(self, site: object) -> dict[str, Any]:
        with self._payload() as payload:
            entry = payload["sources"].get(site_profile_key(site), {})
            return json.loads(json.dumps(entry, ensure_ascii=False)) if isinstance(entry, dict) else {}

    def get_trusted_element(self, site: object, identifier: str) -> dict[str, Any] | None:
        entry = self.get_site(site)
        value = entry.get("trusted_elements", {}).get(identifier)
        return value if isinstance(value, dict) else None

    def mark_primary_success(
        self,
        site: object,
        fingerprint: str,
        element: dict[str, Any] | None,
        identifier: str,
        source_paths: Iterable[str] = (),
    ) -> None:
        with self._payload(write=True) as payload:
            entry = self._entry(payload, site)
            entry["success_count"] = int(entry.get("success_count", 0)) + 1
            entry["failure_count"] = 0
            entry["last_verified_at"] = _now()
            entry["last_failure"] = {}
            if element:
                entry["status"] = "trusted"
                entry["structure_fingerprint"] = fingerprint
                entry.setdefault("trusted_elements", {})[identifier] = element
                entry["element_features"] = [element]
            else:
                if entry.get("status") != "trusted":
                    entry["status"] = "observed"
                entry["last_observed_fingerprint"] = fingerprint
            paths = entry.setdefault("fallback_paths", [])
            for value in source_paths:
                if value and value not in paths:
                    paths.append(value)

    def mark_adaptive_success(
        self,
        site: object,
        fingerprint: str,
        element: dict[str, Any] | None,
        identifier: str,
        source_paths: Iterable[str] = (),
        validation_token: str = "",
    ) -> None:
        with self._payload(write=True) as payload:
            entry = self._entry(payload, site)
            trusted_element = entry.get("trusted_elements", {}).get(identifier)
            if fingerprint == entry.get("structure_fingerprint") and trusted_element:
                entry["success_count"] = int(entry.get("success_count", 0)) + 1
                entry["last_verified_at"] = _now()
                entry["failure_count"] = 0
                return

            candidates = entry.setdefault("candidate_profiles", [])
            candidate = next(
                (value for value in candidates if value.get("structure_fingerprint") == fingerprint),
                None,
            )
            if candidate is None:
                candidate = {
                    "structure_fingerprint": fingerprint,
                    "success_count": 0,
                    "first_seen_at": _now(),
                    "last_verified_at": "",
                    "element": element or {},
                    "source_paths": [],
                    "validation_tokens": [],
                }
                candidates.append(candidate)
            elif element and not candidate.get("element"):
                candidate["element"] = element
            candidate_tokens = candidate.setdefault("validation_tokens", [])
            if validation_token:
                if validation_token not in candidate_tokens:
                    candidate_tokens.append(validation_token)
                    candidate["success_count"] = int(candidate.get("success_count", 0)) + 1
            else:
                candidate["success_count"] = int(candidate.get("success_count", 0)) + 1
            candidate["last_verified_at"] = _now()
            candidate_paths = candidate.setdefault("source_paths", [])
            for value in source_paths:
                if value and value not in candidate_paths:
                    candidate_paths.append(value)

            if int(candidate.get("success_count", 0)) >= 2 and candidate.get("element"):
                entry["status"] = "trusted"
                entry["structure_fingerprint"] = fingerprint
                entry["success_count"] = int(candidate["success_count"])
                entry["failure_count"] = 0
                entry["last_verified_at"] = _now()
                entry.setdefault("trusted_elements", {})[identifier] = candidate["element"]
                entry["element_features"] = [candidate["element"]]
                paths = entry.setdefault("fallback_paths", [])
                for value in candidate_paths:
                    if value not in paths:
                        paths.append(value)
                entry["candidate_profiles"] = [value for value in candidates if value is not candidate]
            elif entry.get("status") != "trusted":
                entry["status"] = "candidate"

    def mark_failure(self, site: object, category: str, detail: str) -> None:
        with self._payload(write=True) as payload:
            entry = self._entry(payload, site)
            entry["failure_count"] = int(entry.get("failure_count", 0)) + 1
            entry["last_failure"] = {
                "category": category,
                "detail": _sanitize_profile_text(detail) or "",
                "at": _now(),
            }


