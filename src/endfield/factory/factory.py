import json
from pathlib import Path
from typing import Any, Dict, List, Literal

_SLUGS_PATH = Path(__file__).parent.parent / "assets" / "factory" / "slugs.json"


def _load_item_slugs() -> tuple[str, ...]:
    try:
        with open(_SLUGS_PATH, encoding="utf-8") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return ()
    slugs = data.get("allSlugs", ())
    return tuple(slug for slug in slugs if isinstance(slug, str))


def get_item_slugs() -> tuple[str, ...]:
    """Return the item slugs currently stored in the local asset."""
    return _load_item_slugs()


items: Any = Literal[tuple(get_item_slugs())]

def format_blueprints(
    data: list[Dict[str, Any]],
    by_views: bool = False,
    by_likes: bool = False,
    region:Literal["Americas/Europe","Asia","Both"]="Both",
    item_filter: Literal["all"] | str | list[str] = "all",
    _from:int=0,
    _to:int=10
)-> tuple[List[Dict[str, Any]], int]:
    """Format and filter blueprints.

    item_filter may be:
      - "all" (default, no filtering)
      - a string (partial slug)
    """
    if region!="Both":
        data=[d for d in data if d["ingame_region"]==region]
        
    view_key = "view_count"
    like_key = "like_count_cache"
    slug = "slug"
    # para sluge is partial , return all blueprints that start with the string
    if item_filter!="all":
        if isinstance(item_filter, str):
            data = [d for d in data if d[slug].startswith(item_filter)]
        elif isinstance(item_filter, list):
            data = [d for d in data if any(d[slug].startswith(f) for f in item_filter)]

    if by_views:
        data = sorted(data, key=lambda x: x.get(view_key, 0), reverse=True)
    if by_likes:
        data = sorted(data, key=lambda x: x.get(like_key, 0), reverse=True)

    return data[_from:_to] , len(data)