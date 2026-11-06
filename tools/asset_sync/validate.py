"""Validate a complete converted asset candidate before it can be published."""

from __future__ import annotations

from typing import Any

from .compare import semantic_equal
from .convert import CHAR_FIELDS, CHAR_FULL_FIELDS, MANUAL_OWNED


class ValidationError(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _map(value: Any, label: str) -> dict[str, Any]:
    _require(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    _require(isinstance(value, list), f"{label} must be a JSON array")
    return value


def _record(value: Any, label: str, fields: tuple[str, ...]) -> dict[str, Any]:
    record = _map(value, label)
    missing = [field for field in fields if field not in record]
    _require(not missing, f"{label} is missing fields: {', '.join(missing)}")
    return record


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    if isinstance(value, dict):
        return all(_finite(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite(item) for item in value)
    return True


def _string_keys(value: dict[str, Any]) -> None:
    _require(all(isinstance(key, str) for key in value), "JSON map keys must be strings")


def _formula_tokens(candidate: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for group in ("relic/relic_item.json", "weapon/weapon_skill.json", "relic/effects_map.json"):
        for record in candidate[group].values():
            if "Formula" in record:
                tokens.add(str(record["Formula"]))
            for prop in record.get("AttrModifiers", []):
                tokens.add(str(prop.get("Formula", "")))
            for prop in record.get("PropMap", {}).values():
                tokens.add(str(prop.get("Formula", "")))
    for character in candidate["character/character_full.json"].values():
        for potential in character.get("PotAttributes", []):
            for attr in potential.get("Attrs", {}).values():
                tokens.add(str(attr.get("Formula", "")))
    return tokens


def validate_candidate(candidate: dict[str, Any], local: dict[str, Any]) -> dict[str, Any]:
    avatars = _map(candidate["character/character_full.json"], "character_full")
    short = _map(candidate["character/character.json"], "character")
    weapons = _map(candidate["weapon/weapon.json"], "weapons")
    skills = _map(candidate["weapon/weapon_skill.json"], "weapon_skill")
    effects = _map(candidate["relic/effects_map.json"], "effects_map")
    equips = _map(candidate["relic/relic_item.json"], "relic_item")
    suits = _map(candidate["relic/relic_suit.json"], "relic_suit")
    terms = _map(candidate["weapon/weapon_gem_term.json"], "weapon_gem_term")
    templates = _map(candidate["weapon/weapon_gem_template.json"], "weapon_gem_template")
    meta = _map(candidate["weapon/weapon_breakthrough_template.json"], "weapon_breakthrough_template")
    medals = _map(candidate["profile/medals.json"], "medals")
    names = _map(candidate["common/nameMap.json"], "nameMap")

    for label, value in (
        ("character_full", avatars), ("character", short), ("weapons", weapons),
        ("weapon_skill", skills), ("effects_map", effects), ("relic_item", equips),
        ("relic_suit", suits), ("weapon_gem_term", terms), ("weapon_gem_template", templates),
        ("weapon_breakthrough_template", meta), ("medals", medals), ("nameMap", names),
    ):
        _string_keys(value)
        _require(_finite(value), f"{label} contains a non-finite number")

    _require(avatars.keys() == short.keys(), "character and character_full IDs differ")
    for key, record in avatars.items():
        _record(record, f"character_full/{key}", CHAR_FULL_FIELDS)
        _require(len(record["BaseHpByLevel"]) == 99, f"character_full/{key} has {len(record['BaseHpByLevel'])} HP levels")
        _require(len(record["BaseAtkByLevel"]) == 99, f"character_full/{key} has {len(record['BaseAtkByLevel'])} ATK levels")
        _require(isinstance(record["BaseAttributes"], dict), f"character_full/{key}/BaseAttributes is not a map")
        expected = {field: record[field] for field in CHAR_FIELDS}
        _require(semantic_equal(short[key], expected), f"character/{key} is not the 12-field projection")

    _require(semantic_equal(skills, effects), "effects_map must equal weapon_skill")

    for key, record in weapons.items():
        _record(record, f"weapons/{key}", ("Rarity", "NameHash", "WeaponType", "Icon",
                                           "LevelTemplateId", "BreakthroughTemplateId", "TalentTemplateId", "SkillList"))
        _require(record["LevelTemplateId"] in meta["LevelCurves"], f"weapons/{key} has an unknown level template")
        _require(record["BreakthroughTemplateId"] in meta["BreakSkillLevelBounds"], f"weapons/{key} has an unknown breakthrough template")
        _require(record["TalentTemplateId"] in meta["TalentSkillLevelBounds"], f"weapons/{key} has an unknown talent template")
        for skill_id in record["SkillList"]:
            _require(str(skill_id) in skills, f"weapons/{key} references missing skill {skill_id}")

    for key, record in skills.items():
        _record(record, f"weapon_skill/{key}", ("TagId", "PropMap"))
        _require(isinstance(record["PropMap"], dict), f"weapon_skill/{key}/PropMap is not a map")
        for prop_id, prop in record["PropMap"].items():
            _record(prop, f"weapon_skill/{key}/PropMap/{prop_id}", ("Values", "Formula"))

    for key, record in equips.items():
        _record(record, f"relic_item/{key}", ("Rarity", "Icon", "SuitId", "AttrModifiers"))
        _require(isinstance(record["AttrModifiers"], list), f"relic_item/{key}/AttrModifiers is not a list")
        if record["SuitId"]:
            _require(record["SuitId"] in suits, f"relic_item/{key} references missing suit {record['SuitId']}")

    for key, record in suits.items():
        _record(record, f"relic_suit/{key}", ("Icon", "NameHash", "SkillId"))
        _require(str(record["SkillId"]) in skills, f"relic_suit/{key} references missing skill {record['SkillId']}")

    for label, values in (("weapon_gem_term", terms), ("weapon_gem_template", templates)):
        _require(values, f"{label} is empty")
    for key, record in templates.items():
        _record(record, f"weapon_gem_template/{key}", ("Icon",))
        _require(str(record["Icon"]).startswith("/ui/ef/itemicon/"), f"weapon_gem_template/{key} has a bad icon path")

    for key, record in medals.items():
        _record(record, f"medals/{key}", ("Name", "LevelInfos"))
        _require(isinstance(record["LevelInfos"], dict), f"medals/{key}/LevelInfos is not a map")
        for level, info in record["LevelInfos"].items():
            _record(info, f"medals/{key}/LevelInfos/{level}", ("Icon", "ConditionDesc"))

    codex = _map(local["common/element.json"], "common/element.json")
    roles = _map(local["character/role.json"], "character/role.json")
    weapon_types = _map(local["weapon/weapon_type.json"], "weapon/weapon_type.json")
    formulas = _map(local["common/formula_suffix.json"], "common/formula_suffix.json")
    prop_ids = _map(local["stats/prop_by_id.json"], "stats/prop_by_id.json")
    for key, record in avatars.items():
        _require(record["Element"] in codex, f"character_full/{key} has an unknown element {record['Element']}")
        _require(record["Profession"] in roles, f"character_full/{key} has an unknown profession {record['Profession']}")
        _require(record["WeaponType"] in weapon_types, f"character_full/{key} has an unknown weapon type")
        for prop_id in record["BaseAttributes"]:
            _require(prop_id in prop_ids, f"character_full/{key} has an unknown property {prop_id}")
        for skill in record["SkillInfoMap"].values():
            _require(skill.get("Element") in codex, f"character_full/{key} has an unknown skill element")
    for key, record in weapons.items():
        _require(record["WeaponType"] in weapon_types, f"weapons/{key} has an unknown weapon type")
    unknown_formulas = _formula_tokens(candidate) - formulas.keys()
    _require(not unknown_formulas, "unknown formulas: " + ", ".join(sorted(unknown_formulas)))

    for key, record in avatars.items():
        _require(record["NameHash"] in names, f"character_full/{key} has an unresolved name hash")
    for key, record in weapons.items():
        _require(record["NameHash"] in names, f"weapons/{key} has an unresolved name hash")
    for key, record in suits.items():
        _require(record["NameHash"] in names, f"relic_suit/{key} has an unresolved name hash")
    for key, record in terms.items():
        _require(record["TagNameHash"] in names, f"weapon_gem_term/{key} has an unresolved name hash")

    for path in MANUAL_OWNED:
        if path in local:
            _require(path not in candidate, f"manual-owned {path} must not be source generated")

    return {
        "characters": len(avatars), "weapons": len(weapons), "skills": len(skills),
        "relic_items": len(equips), "relic_suits": len(suits), "medals": len(medals),
        "name_map": len(names), "formulas": sorted(_formula_tokens(candidate)),
    }
