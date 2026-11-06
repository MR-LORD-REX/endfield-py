"""Tests for the tracked upstream asset synchronizer."""

from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from tools.asset_sync.compare import diff_assets, semantic_equal
from tools.asset_sync.cli import build_report, compute_asset_revision
from tools.asset_sync.convert import (
    CHAR_FIELDS,
    convert_snapshot,
    detect_medal_changes,
    detect_name_map_changes,
)
from tools.asset_sync.source import Snapshot, SourceError, SourceFile, git_blob_sha
from tools.asset_sync.validate import ValidationError, validate_candidate


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src/endfield/assets"


def _load(path: str):
    return json.loads((ASSETS / path).read_text(encoding="utf-8"))


def _local() -> dict:
    from tools.asset_sync.convert import MANUAL_OWNED, SOURCE_OWNED
    return {path: _load(path) for path in SOURCE_OWNED + MANUAL_OWNED}


def _file(path: str, data):
    return SourceFile(path=path, data=data, blob_sha="a" * 40, sha256="b" * 64, size=1)


def _snapshot() -> Snapshot:
    """Small, self-contained upstream fixture that exercises every adapter."""
    avatars = {
        "1": {
            "StrId": "chr_test",
            "NameHash": "hash-1",
            "Rarity": 5,
            "Element": "Pulse",
            "Profession": "VANGUARD",
            "WeaponType": "Sword",
            "MainAttrId": 40,
            "SubAttrId": 41,
            "AttributeNodes": {"node": {"40": 15.0}},
            "SkillInfoMap": {"chr_test_NormalAttack": {"Icon": "/x.png", "Element": "Physical"}},
            "NodeSkillMap": {"node": {"Icon": "/x.png", "Level": 1, "Index": 0, "Type": 4}},
            "PotAttributes": [{"Level": 2, "Attrs": {"40": {"Value": 15.0, "Formula": "BaseAddition"}}}],
            "BaseHpByLevel": list(range(1, 100)),
            "BaseAtkByLevel": list(range(1, 100)),
            "BaseAttributes": {
                "15": {"BaseValue": 100.0, "AddValue": 0.0},
                "40": {"BaseValue": 15.0, "AddValue": 1.0},
            },
        }
    }
    equips = {
        "Items": {"1": {"Rarity": 5, "Icon": "/item.png", "SuitId": "suit_test",
                         "AttrModifiers": [{"AttrType": 3, "Formula": "BaseAddition", "Values": "1 2"}]}},
        "Suits": {"suit_test": {"Icon": "/suit.png", "NameHash": "hash-suit", "SkillId": 1}},
    }
    return Snapshot(
        commit="0" * 40,
        fetched_at="2026-01-01T00:00:00Z",
        files={
            "store/ef/avatars.json": _file("store/ef/avatars.json", avatars),
            "store/ef/equips.json": _file("store/ef/equips.json", equips),
            "store/ef/gems.json": _file("store/ef/gems.json", {
                "TermNums": {"42": {"TermType": 1, "TagId": "attr_agi",
                                    "TagIcon": "/gem.png", "TagNameHash": "gem-hash"}},
                "TemplateItems": {"1066": {"Icon": "/ui/ef/item_gem_rarity_3.png"}},
            }),
            "store/ef/locs.json": _file("store/ef/locs.json", {"en": {
                "hash-1": "Test", "hash-suit": "Suit", "gem-hash": "Gem", "new": "New",
            }}),
            "store/ef/weapon_meta.json": _file("store/ef/weapon_meta.json", {
                "LevelCurves": {"curve": [1]},
                "BreakSkillLevelBounds": {"break": {"0": [{"lowerBound": 1, "upperBound": 3}]}},
                "TalentSkillLevelBounds": {"talent": {"0": [{"lowerBound": 1, "upperBound": 3}]}},
            }),
            "store/ef/weapons.json": _file("store/ef/weapons.json", {
                "1": {"Rarity": 4, "NameHash": "hash-1", "WeaponType": "Sword", "Icon": "/w.png",
                      "LevelTemplateId": "curve", "BreakthroughTemplateId": "break",
                      "TalentTemplateId": "talent", "SkillList": [1]},
            }),
            "store/ef/skills.json": _file("store/ef/skills.json", {
                "1": {"TagId": "", "PropMap": {"2": {"Values": [0.1], "Formula": "BaseMultiplier"}}},
            }),
            "store/ef/pfps.json": _file("store/ef/pfps.json", {"1": {"Icon": "/p.png"}}),
            "store/ef/namecards.json": _file("store/ef/namecards.json", {
                "1": {"Icon": "/ui/ef/businesscardbg/card.png"},
            }),
            "tooltip-data/ef/EN/medals.json": _file("tooltip-data/ef/EN/medals.json", {
                "1": {"Name": "Medal", "LevelInfos": {"1": {"Icon": "i", "ConditionDesc": "d"}}},
            }),
        },
    )


def _convert():
    local = _local()
    candidate = convert_snapshot(_snapshot(), local)
    return local, candidate


class AssetSyncTests(unittest.TestCase):
    def test_attack_rate_adapter_and_projection(self):
        _, candidate = _convert()
        full = candidate["character/character_full.json"]["1"]
        self.assertEqual(full["BaseAttributes"]["15"], {"BaseValue": 1, "AddValue": 0})
        self.assertEqual(
            candidate["character/character.json"]["1"],
            {field: full[field] for field in CHAR_FIELDS},
        )

    def test_gem_and_card_adapters(self):
        _, candidate = _convert()
        self.assertEqual(
            candidate["weapon/weapon_gem_template.json"]["1066"]["Icon"],
            "/ui/ef/itemicon/item_gem_rarity_3.png",
        )
        self.assertEqual(
            candidate["profile/business_card_bg.json"]["1"]["Icon"],
            "/ui/ef/friendlistbg/card.png",
        )

    def test_effects_map_equals_weapon_skill(self):
        _, candidate = _convert()
        self.assertEqual(candidate["relic/effects_map.json"], candidate["weapon/weapon_skill.json"])

    def test_new_name_and_medal_keys_are_added_without_overwriting_local_wording(self):
        local, candidate = _convert()
        self.assertEqual(candidate["common/nameMap.json"]["new"], "New")
        self.assertEqual(candidate["common/nameMap.json"]["MaxHp"], local["common/nameMap.json"]["MaxHp"])
        self.assertEqual(candidate["profile/medals.json"]["1"]["Name"], "Medal")

    def test_semantic_equality_ignores_int_float_and_key_order(self):
        self.assertTrue(semantic_equal({"a": 1, "b": 2.0}, {"b": 2, "a": 1.0}))
        self.assertFalse(semantic_equal({"a": 1}, {"a": 2}))

    def test_diff_reports_added_updated_removed(self):
        diff = diff_assets(
            {"file-a": {"a": {"x": 1}, "b": {"x": 1}}},
            {"file-a": {"a": {"x": 2}, "c": {"x": 1}}},
        )
        self.assertTrue(diff["changed"])
        self.assertEqual(diff["files"]["file-a"]["updated_ids"], ["a"])
        self.assertEqual(diff["files"]["file-a"]["removed_ids"], ["b"])
        self.assertEqual(diff["files"]["file-a"]["added_ids"], ["c"])

    def test_validate_candidate_accepts_fixture(self):
        local, candidate = _convert()
        result = validate_candidate(candidate, local)
        self.assertEqual(result["characters"], 1)
        self.assertEqual(result["weapons"], 1)

    def test_validate_rejects_broken_cross_reference(self):
        local, candidate = _convert()
        broken = copy.deepcopy(candidate)
        broken["weapon/weapon.json"]["1"]["SkillList"] = [999]
        with self.assertRaisesRegex(ValidationError, "missing skill"):
            validate_candidate(broken, local)

    def test_validate_rejects_non_projected_character(self):
        local, candidate = _convert()
        broken = copy.deepcopy(candidate)
        broken["character/character.json"]["1"]["Rarity"] = 6
        with self.assertRaisesRegex(ValidationError, "projection"):
            validate_candidate(broken, local)

    def test_unsupported_attack_rate_fails_closed(self):
        snapshot = _snapshot()
        avatars = copy.deepcopy(snapshot.get("store/ef/avatars.json"))
        avatars["1"]["BaseAttributes"]["15"]["BaseValue"] = 50.0
        files = dict(snapshot.files)
        files["store/ef/avatars.json"] = _file("store/ef/avatars.json", avatars)
        with self.assertRaisesRegex(SourceError, "expected 100"):
            convert_snapshot(Snapshot(snapshot.commit, files, snapshot.fetched_at), _local())

    def test_convert_is_idempotent_for_fixture(self):
        local, candidate = _convert()
        second = convert_snapshot(_snapshot(), {**local, **candidate})
        self.assertEqual(second, candidate)
        self.assertFalse(diff_assets(candidate, second)["changed"])

    def test_editorial_changes_are_reported_not_applied(self):
        snapshot = _snapshot()
        local = _local()
        local_names = dict(local["common/nameMap.json"])
        local_names["hash-1"] = "Local Name"
        self.assertTrue(detect_name_map_changes(snapshot, local_names))
        local_medals = dict(local["profile/medals.json"])
        local_medals["1"] = {"Name": "Local Medal", "LevelInfos": {"1": {"Icon": "i", "ConditionDesc": "local"}}}
        self.assertTrue(detect_medal_changes(snapshot, local_medals))

    def test_asset_revision_is_content_derived(self):
        _, candidate = _convert()
        first = compute_asset_revision(candidate)
        self.assertEqual(first, compute_asset_revision(copy.deepcopy(candidate)))
        changed = copy.deepcopy(candidate)
        changed["relic/relic_suit.json"]["suit_test"]["NameHash"] = "changed"
        self.assertNotEqual(first, compute_asset_revision(changed))

    def test_git_blob_sha_uses_git_object_format(self):
        payload = b'{"a": 1}\n'
        expected = hashlib.sha1(b"blob 9\0" + payload).hexdigest()
        self.assertEqual(git_blob_sha(payload), expected)


if __name__ == "__main__":
    unittest.main()

