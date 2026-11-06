"""Semantic comparison for generated asset maps and reports."""

from __future__ import annotations

from typing import Any


def semantic_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            semantic_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            semantic_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def _pointer(path: str, key: str) -> str:
    return path + "/" + key.replace("~", "~0").replace("/", "~1")


def differences(left: Any, right: Any, path: str = "") -> list[dict[str, Any]]:
    if semantic_equal(left, right):
        return []
    if isinstance(left, dict) and isinstance(right, dict):
        result: list[dict[str, Any]] = []
        for key in sorted(left.keys() | right.keys(), key=lambda item: (str(type(item)), str(item))):
            pointer = _pointer(path, str(key))
            if key not in left:
                result.append({"path": pointer, "change": "added", "new": right[key]})
            elif key not in right:
                result.append({"path": pointer, "change": "removed", "old": left[key]})
            else:
                result.extend(differences(left[key], right[key], pointer))
        return result
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        return [
            change
            for index, (old, new) in enumerate(zip(left, right))
            for change in differences(old, new, f"{path}/{index}")
        ]
    return [{"path": path or "/", "change": "updated", "old": left, "new": right}]


def map_diff(local: dict[str, Any], candidate: dict[str, Any], limit: int | None = None) -> dict[str, Any]:
    added_ids = sorted(candidate.keys() - local.keys())
    removed_ids = sorted(local.keys() - candidate.keys())
    updated_ids: list[str] = []
    updated_entries: dict[str, list[dict[str, Any]]] = {}
    for key in sorted(local.keys() & candidate.keys()):
        changes = differences(local[key], candidate[key], "")
        if changes:
            updated_ids.append(key)
            updated_entries[key] = changes[:limit] if limit else changes
    return {
        "local_entries": len(local),
        "source_entries": len(candidate),
        "added_count": len(added_ids),
        "updated_count": len(updated_ids),
        "removed_count": len(removed_ids),
        "added_ids": added_ids,
        "removed_ids": removed_ids,
        "updated_ids": updated_ids,
        "updated_entries": updated_entries,
    }


def diff_assets(local: dict[str, Any], candidate: dict[str, Any], detail_limit: int = 50) -> dict[str, Any]:
    files: dict[str, Any] = {}
    changed_files: list[str] = []
    all_files = sorted(set(local) | set(candidate))
    for path in all_files:
        report = map_diff(local.get(path, {}), candidate.get(path, {}), detail_limit)
        files[path] = report
        if report["added_count"] or report["updated_count"] or report["removed_count"]:
            changed_files.append(path)
    return {
        "changed": bool(changed_files),
        "changed_files": changed_files,
        "files": files,
    }


def summarize(diff: dict[str, Any]) -> dict[str, Any]:
    return {
        "changed": diff["changed"],
        "changed_files": diff["changed_files"],
        "totals": {
            "added": sum(report["added_count"] for report in diff["files"].values()),
            "updated": sum(report["updated_count"] for report in diff["files"].values()),
            "removed": sum(report["removed_count"] for report in diff["files"].values()),
        },
    }


def merge_order(reference: Any, candidate: Any) -> Any:
    """Keep existing map order while retaining genuine structural changes."""
    if isinstance(reference, dict) and isinstance(candidate, dict):
        result: dict[str, Any] = {}
        for key in reference:
            if key in candidate:
                result[key] = merge_order(reference[key], candidate[key])
        for key in candidate:
            if key not in result:
                result[key] = candidate[key]
        return result
    if isinstance(reference, list) and isinstance(candidate, list) and len(reference) == len(candidate):
        return [merge_order(old, new) for old, new in zip(reference, candidate)]
    return candidate
