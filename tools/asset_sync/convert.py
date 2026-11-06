"""Convert the pinned upstream snapshot into this repository's asset schemas.

The public JSON shapes are intentionally frozen. Only the adapters in
``_adapt_avatars``, ``_adapt_gem_templates``, and ``_adapt_cards`` are allowed
to bridge upstream conventions to the local runtime contracts.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .compare import merge_order
from .source import Snapshot, SourceError


CHAR_FIELDS = (
    "StrId",
    "NameHash",
    "Rarity",
    "Element",
    "Profession",
    "WeaponType",
    "MainAttrId",
    "SubAttrId",
    "AttributeNodes",
    "SkillInfoMap",
    "NodeSkillMap",
    "PotAttributes",
)
CHAR_FULL_FIELDS = CHAR_FIELDS + ("BaseHpByLevel", "BaseAtkByLevel", "BaseAttributes")

ATTACK_RATE_PROPERTY = "15"
ATTACK_RATE_UPSTREAM = 100.0

SOURCE_OWNED = (
    "character/character_full.json",
    "character/character.json",
    "weapon/weapon.json",
    "weapon/weapon_skill.json",
    "relic/effects_map.json",
    "weapon/weapon_breakthrough_template.json",
    "weapon/weapon_gem_term.json",
    "weapon/weapon_gem_template.json",
    "relic/relic_item.json",
    "relic/relic_suit.json",
    "profile/avatar_icon.json",
    "profile/business_card_bg.json",
    "profile/medals.json",
    "common/nameMap.json",
)

MANUAL_OWNED = (
    "asset_metadata.json",
    "version.json",
    "character/role.json",
    "common/ascension_break_node.json",
    "common/element.json",
    "common/formula_suffix.json",
    "common/gender.json",
    "common/node_skill_type.json",
    "common/splash_offset.json",
    "relic/equip_type.json",
    "stats/prop_by_id.json",
    "stats/prop_by_name.json",
    "weapon/weapon_type.json",
)

# Values whose runtime type is semantically significant. When a value is
# unchanged, keep the local int/float representation; when it changes, use the
# upstream numeric type rather than silently coercing it.
TYPE_SENSITIVE_FIELDS = frozenset({"Value", "BaseValue", "AddValue"})


def _require_map(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SourceError(f"{label} must be a JSON object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise SourceError(f"{label} must be a JSON array")
    return value


def _require_record(record: Any, label: str, fields: tuple[str, ...]) -> None:
    if not isinstance(record, dict):
        raise SourceError(f"{label} must be a JSON object")
    missing = [field for field in fields if field not in record]
    if missing:
        raise SourceError(f"{label} is missing fields: {', '.join(missing)}")


def _has_semantic_value(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _has_semantic_value(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _has_semantic_value(a, b) for a, b in zip(left, right)
        )
    return left == right


def _adopt_layout(reference: Any, candidate: Any, field: str = "") -> Any:
    """Keep the local layout where equivalent, but never hide a changed value."""
    if isinstance(reference, dict) and isinstance(candidate, dict):
        result: dict[str, Any] = {}
        for key in candidate:
            result[key] = (
                _adopt_layout(reference[key], candidate[key], str(key))
                if key in reference
                else deepcopy(candidate[key])
            )
        return result
    if isinstance(reference, list) and isinstance(candidate, list) and len(reference) == len(candidate):
        return [_adopt_layout(old, new, field) for old, new in zip(reference, candidate)]
    if value_types_compatible_for_reuse(reference, candidate, field):
        return deepcopy(reference)
    return candidate


def value_types_compatible_for_reuse(reference: Any, candidate: Any, field: str) -> bool:
    if not _has_semantic_value(reference, candidate):
        return False
    if field in TYPE_SENSITIVE_FIELDS:
        return type(reference) is type(candidate)
    return not isinstance(reference, (dict, list))


def _adapt_avatars(raw: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, record in raw.items():
        _require_record(record, f"avatars/{key}", CHAR_FULL_FIELDS)
        adapted = deepcopy(record)
        base_attributes = _require_map(adapted["BaseAttributes"], f"avatars/{key}/BaseAttributes")
        attack_rate = base_attributes.get(ATTACK_RATE_PROPERTY)
        if not isinstance(attack_rate, dict):
            raise SourceError(f"avatars/{key}/BaseAttributes/15 is missing")
        base_value = attack_rate.get("BaseValue")
        add_value = attack_rate.get("AddValue")
        for field, value in (("BaseValue", base_value), ("AddValue", add_value)):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise SourceError(f"avatars/{key}/BaseAttributes/15/{field} must be numeric")
        if float(base_value) != ATTACK_RATE_UPSTREAM:
            raise SourceError(
                f"avatars/{key}/BaseAttributes/15/BaseValue is {base_value!r}; expected {ATTACK_RATE_UPSTREAM}"
            )
        attack_rate["BaseValue"] = float(base_value) / 100.0
        attack_rate["AddValue"] = float(add_value) / 100.0
        if attack_rate["BaseValue"].is_integer():
            attack_rate["BaseValue"] = int(attack_rate["BaseValue"])
        if attack_rate["AddValue"].is_integer():
            attack_rate["AddValue"] = int(attack_rate["AddValue"])
        _require_list(adapted["BaseHpByLevel"], f"avatars/{key}/BaseHpByLevel")
        _require_list(adapted["BaseAtkByLevel"], f"avatars/{key}/BaseAtkByLevel")
        result[str(key)] = adapted
    return result


def _project_character(record: dict[str, Any]) -> dict[str, Any]:
    _require_record(record, "character", CHAR_FIELDS)
    return {field: deepcopy(record[field]) for field in CHAR_FIELDS}


def _adapt_skills(raw: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, record in raw.items():
        _require_record(record, f"skills/{key}", ("TagId", "PropMap"))
        _require_map(record["PropMap"], f"skills/{key}/PropMap")
        result[str(key)] = deepcopy(record)
    return result


def _adapt_gem_terms(raw: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, record in raw.items():
        _require_record(record, f"gems/TermNums/{key}", ("TermType", "TagId", "TagIcon", "TagNameHash"))
        result[str(key)] = deepcopy(record)
    return result


def _adapt_gem_templates(raw: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, record in raw.items():
        _require_record(record, f"gems/TemplateItems/{key}", ("Icon",))
        icon = record["Icon"]
        if not isinstance(icon, str):
            raise SourceError(f"gems/TemplateItems/{key}/Icon must be a string")
        basename = icon.rsplit("/", 1)[-1]
        if not basename.startswith("item_gem_rarity_") or not basename.endswith(".png"):
            raise SourceError(f"gems/TemplateItems/{key}/Icon has an unexpected form: {icon}")
        result[str(key)] = {"Icon": f"/ui/ef/itemicon/{basename}"}
    return result


def _adapt_items(equips: dict[str, Any]) -> dict[str, Any]:
    raw = _require_map(equips.get("Items"), "equips/Items")
    result: dict[str, Any] = {}
    for key, record in raw.items():
        _require_record(record, f"equips/Items/{key}", ("Rarity", "Icon", "SuitId", "AttrModifiers"))
        result[str(key)] = deepcopy(record)
    return result


def _adapt_suits(equips: dict[str, Any]) -> dict[str, Any]:
    raw = _require_map(equips.get("Suits"), "equips/Suits")
    result: dict[str, Any] = {}
    for key, record in raw.items():
        _require_record(record, f"equips/Suits/{key}", ("Icon", "NameHash", "SkillId"))
        result[str(key)] = deepcopy(record)
    return result


def _adapt_weapon_meta(raw: dict[str, Any]) -> dict[str, Any]:
    for section in ("LevelCurves", "BreakSkillLevelBounds", "TalentSkillLevelBounds"):
        _require_map(raw.get(section), f"weapon_meta/{section}")
    return deepcopy(raw)


def _adapt_cards(raw: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, record in raw.items():
        _require_record(record, f"namecards/{key}", ("Icon",))
        icon = record["Icon"]
        if not isinstance(icon, str):
            raise SourceError(f"namecards/{key}/Icon must be a string")
        result[str(key)] = {"Icon": icon.replace("/ui/ef/businesscardbg/", "/ui/ef/friendlistbg/")}
    return result


def _merge_english_names(upstream_names: dict[str, Any], local_names: dict[str, Any]) -> dict[str, Any]:
    if not all(isinstance(value, str) for value in upstream_names.values()):
        raise SourceError("locs/en must be a map of strings")
    result = deepcopy(local_names)
    for key, value in upstream_names.items():
        result.setdefault(str(key), value)
    return result


def _merge_medals(upstream_medals: dict[str, Any], local_medals: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(local_medals)
    for key, record in upstream_medals.items():
        _require_record(record, f"medals/{key}", ("Name", "LevelInfos"))
        _require_map(record["LevelInfos"], f"medals/{key}/LevelInfos")
        if str(key) not in result:
            result[str(key)] = deepcopy(record)
    return result


def convert_snapshot(snapshot: Snapshot, local: dict[str, Any]) -> dict[str, Any]:
    """Return every source-owned output, preserving local representation where possible."""
    avatars = _adapt_avatars(_require_map(snapshot.get("store/ef/avatars.json"), "store/ef/avatars.json"))
    skills = _adapt_skills(_require_map(snapshot.get("store/ef/skills.json"), "store/ef/skills.json"))
    gems = _require_map(snapshot.get("store/ef/gems.json"), "store/ef/gems.json")
    equips = _require_map(snapshot.get("store/ef/equips.json"), "store/ef/equips.json")

    candidate = {
        "character/character_full.json": avatars,
        "character/character.json": {key: _project_character(record) for key, record in avatars.items()},
        "weapon/weapon.json": _require_map(snapshot.get("store/ef/weapons.json"), "store/ef/weapons.json"),
        "weapon/weapon_skill.json": skills,
        "relic/effects_map.json": deepcopy(skills),
        "weapon/weapon_breakthrough_template.json": _adapt_weapon_meta(
            _require_map(snapshot.get("store/ef/weapon_meta.json"), "store/ef/weapon_meta.json")
        ),
        "weapon/weapon_gem_term.json": _adapt_gem_terms(_require_map(gems.get("TermNums"), "gems/TermNums")),
        "weapon/weapon_gem_template.json": _adapt_gem_templates(
            _require_map(gems.get("TemplateItems"), "gems/TemplateItems")
        ),
        "relic/relic_item.json": _adapt_items(equips),
        "relic/relic_suit.json": _adapt_suits(equips),
        "profile/avatar_icon.json": _require_map(snapshot.get("store/ef/pfps.json"), "store/ef/pfps.json"),
        "profile/business_card_bg.json": _adapt_cards(
            _require_map(snapshot.get("store/ef/namecards.json"), "store/ef/namecards.json")
        ),
        "profile/medals.json": _merge_medals(
            _require_map(snapshot.get("tooltip-data/ef/EN/medals.json"), "tooltip-data/ef/EN/medals.json"),
            _require_map(local["profile/medals.json"], "local profile/medals.json"),
        ),
        "common/nameMap.json": _merge_english_names(
            _require_map(
                _require_map(snapshot.get("store/ef/locs.json"), "store/ef/locs.json").get("en"),
                "locs/en",
            ),
            _require_map(local["common/nameMap.json"], "local common/nameMap.json"),
        ),
    }
    return {
        path: merge_order(local[path], _adopt_layout(local[path], value)) if path in local else value
        for path, value in candidate.items()
    }


def detect_name_map_changes(snapshot: Snapshot, local_names: dict[str, Any]) -> list[dict[str, Any]]:
    upstream = _require_map(
        _require_map(snapshot.get("store/ef/locs.json"), "store/ef/locs.json").get("en"),
        "locs/en",
    )
    return [
        {"key": key, "local": local_names[key], "upstream": upstream[key]}
        for key in sorted(set(local_names) & set(upstream))
        if local_names[key] != upstream[key]
    ]


def detect_medal_changes(snapshot: Snapshot, local_medals: dict[str, Any]) -> list[dict[str, Any]]:
    upstream = _require_map(
        snapshot.get("tooltip-data/ef/EN/medals.json"),
        "tooltip-data/ef/EN/medals.json",
    )
    return [
        {"id": key, "local": local_medals[key], "upstream": upstream[key]}
        for key in sorted(set(local_medals) & set(upstream))
        if local_medals[key] != upstream[key]
    ]
