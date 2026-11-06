"""Fetch and verify a pinned Enka API-docs snapshot."""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


GITHUB_API = "https://api.github.com"
REPOSITORY = "EnkaNetwork/API-docs"
RAW_BASE = f"https://raw.githubusercontent.com/{REPOSITORY}"
BRANCH = "master"
STORE_PREFIX = "store/ef/"
TOOLTIP_PREFIX = "tooltip-data/ef/"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ATTEMPTS = 4


class SourceError(RuntimeError):
    """The upstream source could not be fetched or verified."""


@dataclass(frozen=True)
class SourceFile:
    path: str
    data: Any
    blob_sha: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Snapshot:
    commit: str
    files: dict[str, SourceFile]
    fetched_at: str

    def get(self, path: str) -> Any:
        return self.files[path].data

    def blob_sha(self, path: str) -> str:
        return self.files[path].blob_sha


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "endfield-asset-sync",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise SourceError(f"Non-finite JSON number: {value}")


def _request(url: str) -> bytes:
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        request = urllib.request.Request(url, headers=_headers())
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status != 200:
                    raise SourceError(f"{url} returned HTTP {response.status}")
                payload = response.read(MAX_RESPONSE_BYTES + 1)
                if len(payload) > MAX_RESPONSE_BYTES:
                    raise SourceError(f"{url} exceeded the {MAX_RESPONSE_BYTES} byte limit")
                return payload
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            if attempt + 1 < MAX_ATTEMPTS:
                time.sleep(2**attempt)
    raise SourceError(f"Failed to fetch {url}: {last_error}")


def parse_json(payload: bytes, label: str) -> Any:
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceError(f"Invalid JSON in {label}: {exc}") from exc


def git_blob_sha(payload: bytes) -> str:
    """Return the Git object ID for a blob with the given raw bytes."""
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def _resolve_commit(commit: str | None) -> str:
    if commit:
        if len(commit) != 40 or not all(character in "0123456789abcdef" for character in commit.lower()):
            raise SourceError("--commit must be a full 40-character Git SHA")
        return commit.lower()
    payload = _request(f"{GITHUB_API}/repos/{REPOSITORY}/commits/{BRANCH}")
    parsed = parse_json(payload, f"commit/{BRANCH}")
    sha = parsed.get("sha") if isinstance(parsed, dict) else None
    if not isinstance(sha, str) or len(sha) != 40:
        raise SourceError("GitHub did not return a valid commit SHA")
    return sha


def _tree(commit: str) -> dict[str, dict[str, Any]]:
    payload = _request(f"{GITHUB_API}/repos/{REPOSITORY}/git/trees/{commit}?recursive=1")
    parsed = parse_json(payload, f"tree/{commit}")
    if not isinstance(parsed, dict) or parsed.get("truncated"):
        raise SourceError("GitHub returned a truncated or invalid source tree")
    entries: dict[str, dict[str, Any]] = {}
    for item in parsed.get("tree", []):
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        path = item.get("path")
        if not isinstance(path, str):
            continue
        if path.startswith(STORE_PREFIX) or path.startswith(TOOLTIP_PREFIX):
            entries[path] = item
    return entries


def _select_paths(tree: dict[str, dict[str, Any]]) -> list[str]:
    paths = [path for path in tree if path.startswith(STORE_PREFIX)]
    for path in sorted(tree):
        if not path.startswith(TOOLTIP_PREFIX):
            continue
        relative = path[len(TOOLTIP_PREFIX):]
        if relative in {"EN/medals.json", "weapon_meta.json", "rich_text.json"}:
            paths.append(path)
    required = {
        "store/ef/avatars.json",
        "store/ef/equips.json",
        "store/ef/gems.json",
        "store/ef/locs.json",
        "store/ef/weapon_meta.json",
        "store/ef/weapons.json",
        "store/ef/skills.json",
        "store/ef/pfps.json",
        "store/ef/namecards.json",
        "tooltip-data/ef/EN/medals.json",
    }
    missing = sorted(required - set(paths))
    if missing:
        raise SourceError("Required upstream files are missing: " + ", ".join(missing))
    return sorted(set(paths))


def fetch_snapshot(commit: str | None = None, cache_dir: Path | None = None) -> Snapshot:
    resolved = _resolve_commit(commit)
    tree = _tree(resolved)
    paths = _select_paths(tree)
    files: dict[str, SourceFile] = {}
    for path in paths:
        payload = _request(f"{RAW_BASE}/{resolved}/{path}")
        expected_blob = str(tree[path].get("sha", ""))
        actual_blob = git_blob_sha(payload)
        if actual_blob != expected_blob:
            raise SourceError(
                f"{path} does not match the source tree (expected blob {expected_blob}, got {actual_blob})"
            )
        data = parse_json(payload, path)
        digest = hashlib.sha256(payload).hexdigest()
        files[path] = SourceFile(
            path=path,
            data=data,
            blob_sha=expected_blob,
            sha256=digest,
            size=len(payload),
        )
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        for path, source in files.items():
            destination = cache_dir / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(source.data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        (cache_dir / "snapshot.json").write_text(
            json.dumps(
                {
                    "commit": resolved,
                    "files": {
                        path: {
                            "blob_sha": source.blob_sha,
                            "sha256": source.sha256,
                            "size": source.size,
                        }
                        for path, source in files.items()
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return Snapshot(commit=resolved, files=files, fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
