# Asset sync

This utility converts the public Enka `API-docs` EF JSON store into the existing `src/endfield/assets` formats. It does not change the runtime models or the `endfield-cards` contract.

## Local checks

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
python -m tools.asset_sync check
```

`check` fetches the current upstream `master` commit, builds the candidate in memory, validates it, and writes a report to `.asset-sync/`. It never changes assets.

To pin a source revision:

```powershell
python -m tools.asset_sync check --commit <40-character-sha>
```

To inspect the converted files without touching the repo:

```powershell
python -m tools.asset_sync check --emit-candidate .candidate-assets
```

## Applying

```powershell
python -m tools.asset_sync apply --yes
```

Apply validates the entire candidate first, writes it to a staging directory, then replaces only the source-owned asset files. Manual-owned files and `factory/` are never rewritten.

## Policy

- Existing local name-map and medal wording is preserved.
- New upstream name-map keys and medals are added.
- The three compatibility adapters are `AttackRate` scale, gem icon prefix, and business-card path convention.
- A second apply of the same upstream revision must be a no-op.
- Each successful apply writes a content-derived `assetRevision` to `version.json`.
  The existing `version` and `libVersion` fields are left untouched, and
  `check_update()` prefers `assetRevision` when both copies provide it.

## GitHub Actions

`.github/workflows/update-assets.yml` runs at 09:00 IST (03:30 UTC). It:

1. runs the tests;
2. checks the current upstream snapshot;
3. applies and validates when assets or revision metadata changed;
4. verifies idempotence;
5. opens or refreshes an `asset-sync` PR against the branch that triggered it.

Manual dispatch is available from the Actions tab. It accepts an optional
`source_commit` SHA for testing an older or pinned snapshot. The branch current
when the workflow starts is used as the PR base. The schedule should be enabled
after one successful review and merge.
