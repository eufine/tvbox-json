#!/usr/bin/env python3
"""Fetch a TVBox config, drop netdisk-based sources, and write a clean JSON file.

The upstream config is normally a plain JSON document.  It can also be hidden
inside an image: the 饭太硬 endpoint returns a JPEG plus a Base64-encoded JSON
document when it is requested with a TVBox/OkHttp User-Agent.  This script
handles both forms, tolerates // comments in the JSON, and keeps every
top-level section except the filtered `sites`/`lives` entries.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_FILTER_CONFIG: dict[str, Any] = {
    # The first URL is the normal 饭太硬 endpoint.  The second one is the same
    # file published by the site as `in.bmp` on GitLink, used as a fallback.
    "source_urls": [
        "http://www.xn--sss604efuw.cc/tv",
        "https://cdn09022024.gitlink.org.cn/api/v1/repos/xxooo/in/raw/in.bmp",
    ],
    # Exact site keys that are known to be netdisk-based.
    "explicit_keys": [
        "MDrive",
        "玩偶",
        "seed",
        "ZPan",
        "JPan",
        "YpanSo",
        "BpanSo",
        "抠搜",
        "UC",
        "push_agent",
    ],
    # Substrings in a site/live name that identify netdisk content.
    "name_keywords": [
        "网盘",
        "云盘",
        "盘搜",
        "盘她",
        "盘他",
        "四盘",
        "夸克",
        "阿里云盘",
        "百度网盘",
        "天翼云盘",
        "115网盘",
        "迅雷云盘",
        "pikpak",
        "小雅",
        "alist",
        "openlist",
        "clouddrive",
    ],
    # Regexes matched against a site's `api` field (spider class name or URL).
    "api_patterns": [
        "MyDrive",
        "CloudDrive",
        "Quark",
        "Aliyun",
        "AliPan",
        "UCDrive",
        "BaiduPan",
        "Tianyi",
        "Xunlei",
        "PikPak",
        "Alist",
        "Openlist",
        "Ypan",
        "Bpan",
        "S_zps",
        "KkSs",
        "UuSs",
        "Seedhub",
        "PushGuard",
    ],
    # Sites that have a `Cloud-drive` extension but are primarily normal
    # video sites.  They are kept, and the Cloud-drive extension is removed.
    "keep_sites": ["立播"],
    "strip_cloud_drive_from_kept": True,
    "filter_lives": True,
}

HEADERS = {
    "User-Agent": "okhttp/3.15",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "identity",
}

# Base64 encodings of `{` followed by typical config keys, and the two most
# common forms of `{` + newline.  Searching for these avoids treating the
# random JPEG data before the payload as Base64.
B64_JSON_MARKERS = (
    "ew0KInNwaWRlciI6",  # {\r\n"spider":
    'ewoKInNwaWRlciI6',  # {\n\n"spider":
    'ew0KInNzaXRlcyI6',  # {\r\n"sites":
    'ewoKInNzaXRlcyI6',  # {\n\n"sites":
    'eyJzcGlkZXIi',      # {"spider"
    'eyJzaXRlcyI',       # {"sites"
    'eyJ',               # {"
    'ew0K',              # {\r\n
    'ewoK',              # {\n
)


def log(*args: Any) -> None:
    print(*args, file=sys.stderr)


def strip_json_comments(text: str) -> str:
    """Remove // and /* */ comments while respecting JSON strings."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    escaped = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                end = text.find("\n", i + 2)
                if end == -1:
                    break
                out.append("\n")
                i = end + 1
                continue
            if nxt == "*":
                end = text.find("*/", i + 2)
                if end == -1:
                    break
                out.append("\n" * text.count("\n", i, end + 2))
                i = end + 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def strip_trailing_commas(text: str) -> str:
    """Remove commas immediately before } or ] while respecting strings."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    escaped = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1  # drop the comma
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def loads_tolerant(text: str) -> Any:
    """Parse JSON that may contain comments and trailing commas."""
    cleaned = strip_trailing_commas(strip_json_comments(text))
    return json.loads(cleaned)


def try_decode_text(payload: bytes, encoding: str) -> str | None:
    try:
        return payload.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return None


def decode_if_base64(raw: str, index: int) -> str | None:
    suffix = raw[index:]
    # Base64 payloads may be line-wrapped; discard any non-alphabet bytes so a
    # trailing newline or HTML fragment cannot break decoding.
    b64 = re.sub(r"[^A-Za-z0-9+/=]", "", suffix)
    if len(b64) < 16:
        return None
    b64 = b64.rstrip("=")
    b64 += "=" * (-len(b64) % 4)
    try:
        decoded = base64.b64decode(b64, validate=False)
    except (ValueError, binascii.Error):
        return None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        text = try_decode_text(decoded, encoding)
        if text and text.lstrip().startswith("{"):
            return text
    return None


def extract_config(payload: bytes) -> tuple[dict[str, Any], str]:
    """Return (config, format) from a plain JSON or image-wrapped response."""
    if not payload:
        raise ValueError("empty response")

    # 1. A plain JSON response, possibly with a BOM or JSON comments.
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        text = try_decode_text(payload, encoding)
        if text is None:
            continue
        stripped = text.lstrip("\ufeff \t\r\n")
        if stripped.startswith("{"):
            try:
                value = loads_tolerant(stripped)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(value, dict):
                    return value, "plain-json"

    # 2. A Base64 JSON document appended to an image (JPEG/BMP/PNG).
    raw = payload.decode("latin-1")
    candidates: set[int] = set()
    for marker in B64_JSON_MARKERS:
        start = 0
        while True:
            index = raw.find(marker, start)
            if index < 0:
                break
            candidates.add(index)
            start = index + 1
            if len(candidates) >= 40:
                break
    for index in sorted(candidates):
        text = decode_if_base64(raw, index)
        if text is None:
            continue
        try:
            value = loads_tolerant(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value, "image+base64"

    # 3. Some mirrors return a Base64 document with no image prefix.
    raw_stripped = raw.strip()
    if raw_stripped:
        text = decode_if_base64(raw_stripped, 0)
        if text:
            try:
                value = loads_tolerant(text)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(value, dict):
                    return value, "base64"

    preview = payload[:160].decode("latin-1", errors="replace")
    raise ValueError(f"response is neither JSON nor an image-wrapped config: {preview!r}")


def http_get(url: str, timeout: int) -> tuple[bytes, str, str | None, str | None]:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
        if response.headers.get("Content-Encoding", "").lower() == "gzip":
            payload = gzip.decompress(payload)
        content_type = response.headers.get("Content-Type", "")
        etag = response.headers.get("ETag")
        last_modified = response.headers.get("Last-Modified")
    return payload, content_type, etag, last_modified


def fetch_config(
    urls: list[str], timeout: int = 40, retries: int = 2
) -> tuple[dict[str, Any], str, str, str | None, str | None]:
    errors: list[str] = []
    for url in urls:
        for attempt in range(1, retries + 1):
            try:
                payload, content_type, etag, last_modified = http_get(url, timeout)
                config, fmt = extract_config(payload)
                log(f"Fetched {url} ({content_type or 'unknown type'}, {len(payload)} bytes, {fmt})")
                return config, fmt, url, etag, last_modified
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError) as exc:
                message = f"{url} (attempt {attempt}/{retries}): {exc}"
                errors.append(message)
                log(f"WARNING: {message}")
                if attempt < retries:
                    time.sleep(2 * attempt)
    raise RuntimeError("could not fetch a valid TVBox config:\n" + "\n".join(errors))


def load_filter_config(path: str | None) -> dict[str, Any]:
    config = {key: (value.copy() if isinstance(value, list) else value) for key, value in DEFAULT_FILTER_CONFIG.items()}
    if path and Path(path).exists():
        user_config = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(user_config, dict):
            raise ValueError(f"{path}: expected a JSON object")
        config.update(user_config)
    return config


def matches_keyword(value: str, keywords: list[str]) -> str | None:
    folded = value.casefold()
    for keyword in keywords:
        if keyword.casefold() in folded:
            return keyword
    return None


def has_cloud_drive_ext(ext: Any) -> bool:
    if isinstance(ext, dict):
        return any(str(key).casefold() == "cloud-drive" for key in ext)
    if isinstance(ext, str):
        return "cloud-drive" in ext.casefold()
    return False


def strip_cloud_drive_from_site(site: dict[str, Any]) -> None:
    ext = site.get("ext")
    if isinstance(ext, dict):
        for key in list(ext):
            if str(key).casefold() == "cloud-drive":
                del ext[key]
    elif isinstance(ext, str) and "cloud-drive" in ext.casefold():
        try:
            parsed = json.loads(ext)
        except json.JSONDecodeError:
            return
        if isinstance(parsed, dict):
            for key in list(parsed):
                if str(key).casefold() == "cloud-drive":
                    del parsed[key]
            site["ext"] = parsed


def is_netdisk_site(site: dict[str, Any], filter_config: dict[str, Any]) -> str | None:
    key = str(site.get("key", ""))
    name = str(site.get("name", ""))
    api = str(site.get("api", ""))

    if key in filter_config.get("explicit_keys", []):
        return "explicit key"

    keyword = matches_keyword(name, filter_config.get("name_keywords", []))
    if keyword:
        return f"name contains {keyword!r}"

    for pattern in filter_config.get("api_patterns", []):
        if re.search(pattern, api, re.IGNORECASE):
            return f"api matches /{pattern}/"

    keep_sites = filter_config.get("keep_sites", [])
    keep_override = key in keep_sites or name in keep_sites
    if has_cloud_drive_ext(site.get("ext")) and not keep_override:
        return "Cloud-drive extension"

    return None


def apply_filter(config: dict[str, Any], filter_config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    sites = config.get("sites")
    if not isinstance(sites, list):
        raise ValueError("config has no 'sites' array")

    kept_sites: list[dict[str, Any]] = []
    removed_sites: list[dict[str, Any]] = []
    keep_sites = filter_config.get("keep_sites", [])
    for site in sites:
        if not isinstance(site, dict):
            kept_sites.append(site)
            continue
        reason = is_netdisk_site(site, filter_config)
        if reason:
            removed_sites.append(
                {
                    "key": site.get("key"),
                    "name": site.get("name"),
                    "api": site.get("api"),
                    "reason": reason,
                }
            )
            continue
        key = str(site.get("key", ""))
        name = str(site.get("name", ""))
        if filter_config.get("strip_cloud_drive_from_kept", True) and (
            key in keep_sites or name in keep_sites
        ):
            strip_cloud_drive_from_site(site)
        kept_sites.append(site)

    removed_lives: list[dict[str, Any]] = []
    kept_lives = config.get("lives")
    if filter_config.get("filter_lives") and isinstance(kept_lives, list):
        new_lives = []
        for live in kept_lives:
            if isinstance(live, dict):
                keyword = matches_keyword(str(live.get("name", "")), filter_config.get("name_keywords", []))
                if keyword:
                    removed_lives.append(
                        {
                            "name": live.get("name"),
                            "url": live.get("url"),
                            "reason": f"name contains {keyword!r}",
                        }
                    )
                    continue
            new_lives.append(live)
        config["lives"] = new_lives

    result = dict(config)
    result["sites"] = kept_sites
    report = {
        "total_sites": len(sites),
        "kept_sites": len(kept_sites),
        "removed_sites": removed_sites,
        "total_lives": len(kept_lives) if isinstance(kept_lives, list) else 0,
        "kept_lives": len(result.get("lives", [])) if isinstance(result.get("lives"), list) else 0,
        "removed_lives": removed_lives,
    }
    return result, report


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/filter.json", help="filter rules JSON (optional)")
    parser.add_argument("--output", default="tvbox.json", help="output config path")
    parser.add_argument("--report", default="filter-report.json", help="removal report path")
    parser.add_argument("--input", help="read a saved response instead of fetching the network")
    parser.add_argument("--source", action="append", help="override source URL; may be repeated")
    parser.add_argument("--timeout", type=int, default=40, help="per-request timeout in seconds")
    parser.add_argument("--no-report", action="store_true", help="do not write the report file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    filter_config = load_filter_config(args.config)

    if args.input:
        payload = Path(args.input).read_bytes()
        config, fmt = extract_config(payload)
        source_url = f"file://{Path(args.input).resolve()}"
        etag = last_modified = None
    else:
        source_urls = args.source or os.environ.get("TVBOX_SOURCE_URL", "").split() or filter_config["source_urls"]
        config, fmt, source_url, etag, last_modified = fetch_config(
            source_urls, timeout=args.timeout
        )

    result, report = apply_filter(config, filter_config)
    report.update(
        {
            "source_url": source_url,
            "format": fmt,
        }
    )

    write_json(Path(args.output), result)
    if not args.no_report:
        write_json(Path(args.report), report)

    log(
        f"Kept {report['kept_sites']}/{report['total_sites']} sites, "
        f"removed {len(report['removed_sites'])} netdisk source(s)."
    )
    for item in report["removed_sites"]:
        log(f"  - {item['key']} | {item['name']} | {item['reason']}")
    for item in report["removed_lives"]:
        log(f"  - live: {item['name']} | {item['reason']}")
    log(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
