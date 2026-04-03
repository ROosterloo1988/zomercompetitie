from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import Request, urlopen


@dataclass
class UpdateInfo:
    available: bool
    current_version: str
    latest_version: str
    release_url: str
    release_name: str


def _normalize_version(value: str) -> tuple[int, ...]:
    cleaned = value.strip().lower().lstrip("v")
    parts = re.findall(r"\d+", cleaned)
    if not parts:
        return (0,)
    return tuple(int(part) for part in parts)


def is_newer_version(latest: str, current: str) -> bool:
    latest_parts = _normalize_version(latest)
    current_parts = _normalize_version(current)
    max_len = max(len(latest_parts), len(current_parts))
    latest_parts += (0,) * (max_len - len(latest_parts))
    current_parts += (0,) * (max_len - len(current_parts))
    return latest_parts > current_parts


def check_github_update(repo: str, current_version: str, timeout_seconds: float = 2.5) -> UpdateInfo | None:
    if not repo:
        return None

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "zomercompetitie-update-checker"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, ValueError):
        return None

    latest_tag = str(data.get("tag_name") or data.get("name") or "").strip()
    if not latest_tag:
        return None

    latest_version = latest_tag.lstrip("v")
    release_url = str(data.get("html_url") or f"https://github.com/{repo}/releases")
    release_name = str(data.get("name") or latest_tag)
    available = is_newer_version(latest_version, current_version)
    return UpdateInfo(
        available=available,
        current_version=current_version,
        latest_version=latest_version,
        release_url=release_url,
        release_name=release_name,
    )
