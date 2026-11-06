"""Command-line entry point for the Endfield asset synchronizer."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from .compare import diff_assets, summarize
from .convert import (
    MANUAL_OWNED,
    SOURCE_OWNED,
    convert_snapshot,
    detect_medal_changes,
    detect_name_map_changes,
)
from .jsonpatch import patch
from .source import Snapshot, SourceError, fetch_snapshot
from .validate import ValidationError, validate_candidate


ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "src/endfield/assets"


class SyncError(RuntimeError):
    pass


def _load_local(assets: Path) -> dict[str, Any]:
    local: dict[str, Any] = {}
    for path in SOURCE_OWNED + MANUAL_OWNED:
        file = assets / path
        if not file.exists():
            raise SyncError(f"Required local asset is missing: {file}")
        try:
            local[path] = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SyncError(f"Invalid local JSON in {path}: {exc}") from exc
    return local


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=4, allow_nan=False) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _patched_text(source: Path, value: Any) -> str:
    text = source.read_text(encoding="utf-8")
    return patch(text, value)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_asset_revision(candidate: dict[str, Any]) -> int:
    """Derive a stable revision from source-owned content, independent of formatting."""
    digest = hashlib.sha256(_canonical_json(candidate)).hexdigest()
    return int(digest[:16], 16)


def update_version_record(local_version: dict[str, Any], revision: int) -> dict[str, Any]:
    """Keep the existing version fields and attach the content revision."""
    result = dict(local_version)
    result["assetRevision"] = revision
    return result


def build_report(snapshot: Snapshot, local: dict[str, Any]) -> dict[str, Any]:
    candidate = convert_snapshot(snapshot, local)
    if set(candidate) != set(SOURCE_OWNED):
        raise SyncError("Candidate paths do not match the source-owned allowlist")
    validation = validate_candidate(candidate, local)
    diff = diff_assets({path: local[path] for path in SOURCE_OWNED}, candidate)
    revision = compute_asset_revision(candidate)
    return {
        "source": {
            "repository": "EnkaNetwork/API-docs",
            "commit": snapshot.commit,
            "fetched_at": snapshot.fetched_at,
            "files": {
                path: {
                    "blob_sha": source.blob_sha,
                    "sha256": source.sha256,
                    "size": source.size,
                }
                for path, source in sorted(snapshot.files.items())
            },
        },
        "summary": summarize(diff),
        "diff": diff,
        "validation": validation,
        "revision": {
            "assetRevision": revision,
            "version_record": update_version_record(local["version.json"], revision),
            "source_owned_canonical_sha256": hashlib.sha256(_canonical_json(candidate)).hexdigest(),
        },
        "editorial": {
            "name_map_changes": len(detect_name_map_changes(snapshot, local["common/nameMap.json"])),
            "medal_changes": len(detect_medal_changes(snapshot, local["profile/medals.json"])),
            "policy": "Existing local wording is preserved; upstream wording is used for new keys only.",
        },
        "candidate": candidate,
    }


def _write_reports(out: Path, report: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "report.json", {key: value for key, value in report.items() if key != "candidate"})
    lines = [
        "# Endfield asset sync report",
        "",
        f"- Upstream commit: `{report['source']['commit']}`",
        f"- Fetched at: {report['source']['fetched_at']}",
        f"- Changed: {'yes' if report['summary']['changed'] else 'no'}",
        f"- Added / updated / removed: {report['summary']['totals']['added']} / "
        f"{report['summary']['totals']['updated']} / {report['summary']['totals']['removed']}",
        "",
        "## Validation",
        "",
        "```json",
        json.dumps(report["validation"], indent=2),
        "```",
    ]
    if report["summary"]["changed_files"]:
        lines += ["", "## Changed files", ""]
        for path in report["summary"]["changed_files"]:
            entry = report["diff"]["files"][path]
            lines.append(
                f"- `{path}`: +{entry['added_count']} ~{entry['updated_count']} -{entry['removed_count']}"
            )
            if entry["added_ids"]:
                lines.append(f"  - Added: {', '.join(entry['added_ids'][:30])}")
            if entry["removed_ids"]:
                lines.append(f"  - Removed: {', '.join(entry['removed_ids'][:30])}")
            if entry["updated_ids"]:
                lines.append(f"  - Updated: {', '.join(entry['updated_ids'][:30])}")
        lines += ["", "## Editorial-only source changes", "",
                  f"- Name-map wording changes not applied: {report['editorial']['name_map_changes']}",
                  f"- Medal wording changes not applied: {report['editorial']['medal_changes']}"]
    _write_text(out / "report.md", "\n".join(lines) + "\n")


def write_candidate(candidate: dict[str, Any], destination: Path, assets: Path) -> None:
    if set(candidate) != set(SOURCE_OWNED):
        raise SyncError("Candidate paths do not match the source-owned allowlist")
    for path, value in candidate.items():
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_text(target, _patched_text(assets / path, value))


def write_revision_record(revision_record: dict[str, Any], destination: Path, assets: Path) -> None:
    target = destination / "version.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_text(target, _patched_text(assets / "version.json", revision_record))


def apply_candidate(candidate: dict[str, Any], assets: Path) -> None:
    if set(candidate) != set(SOURCE_OWNED):
        raise SyncError("Refusing to write paths outside the source-owned allowlist")
    staged = Path(tempfile.mkdtemp(prefix="endfield-assets-"))
    try:
        for path, value in candidate.items():
            target = staged / path
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_text(target, _patched_text(assets / path, value))
            json.loads(target.read_text(encoding="utf-8"))
        for path in candidate:
            target = assets / path
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged / path, target)
    finally:
        if staged.exists():
            import shutil
            shutil.rmtree(staged, ignore_errors=True)


def apply_revision(revision_record: dict[str, Any], assets: Path) -> None:
    target = assets / "version.json"
    _write_text(target, _patched_text(target, revision_record))


def _markdown_summary(report: dict[str, Any]) -> str:
    lines = [
        f"Upstream commit: `{report['source']['commit']}`",
        f"Changed: **{'yes' if report['summary']['changed'] else 'no'}**",
        f"Added / updated / removed: **{report['summary']['totals']['added']} / "
        f"{report['summary']['totals']['updated']} / {report['summary']['totals']['removed']}**",
    ]
    if report["summary"]["changed_files"]:
        lines.append("Changed files: " + ", ".join(f"`{path}`" for path in report["summary"]["changed_files"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synchronize Endfield assets from EnkaNetwork/API-docs.")
    parser.add_argument("command", choices=("check", "apply"))
    parser.add_argument("--commit", help="Full upstream Git SHA; defaults to the current master commit.")
    parser.add_argument("--assets", type=Path, default=ASSETS)
    parser.add_argument("--out", type=Path, default=ROOT / ".asset-sync")
    parser.add_argument("--emit-candidate", type=Path, default=None,
                        help="Write the converted candidate tree for inspection without changing assets.")
    parser.add_argument("--cache", type=Path, default=None, help="Optional directory for raw source snapshots.")
    parser.add_argument("--yes", action="store_true", help="Required with apply.")
    parser.add_argument("--github-summary", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "apply" and not args.yes:
        parser.error("apply requires --yes")

    try:
        local = _load_local(args.assets)
        snapshot = fetch_snapshot(args.commit, args.cache)
        report = build_report(snapshot, local)
        _write_reports(args.out, report)
        if args.emit_candidate is not None:
            write_candidate(report["candidate"], args.emit_candidate, args.assets)
            write_revision_record(report["revision"]["version_record"], args.emit_candidate, args.assets)
        print(_markdown_summary(report))
        if args.github_summary and os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
                summary.write("# Asset sync\n\n" + _markdown_summary(report) + "\n")
        revision_changed = local["version.json"].get("assetRevision") != report["revision"]["assetRevision"]
        if args.command == "apply":
            apply_candidate(report["candidate"], args.assets)
            apply_revision(report["revision"]["version_record"], args.assets)
            if report["summary"]["changed"] or revision_changed:
                print("Applied validated candidate.")
        return 0
    except (SourceError, ValidationError, SyncError) as exc:
        print(f"asset sync failed: {exc}", file=sys.stderr)
        return 1
