base="https://rmjxwmsbfgvgjpoqvosg.supabase.co/rest/v1/factory_plans?apikey={key}"
slugs_url="https://endfieldtools.dev/localdb/optimized/factory/item-slugs.json"

import aiohttp
from pathlib import Path
import json
from datetime import datetime, timezone

asset_path=Path(__file__).parent.parent/"assets"

def check_last(field="last_updated"):
    meta_path=asset_path/"factory/fact_meta.json"
    if not meta_path.exists():
        return False
    with open(meta_path,"r",encoding="utf-8") as f:
        meta_data=json.load(f)
    last_updated=meta_data.get(field, 0)
    if datetime.now(timezone.utc).timestamp() - last_updated > 60*60*24*2: # 2 days
        return False
    return True

def get_token():
    meta_path=asset_path/"factory/fact_meta.json"
    if not meta_path.exists():
        return None
    with open(meta_path,"r",encoding="utf-8") as f:
        meta_data=json.load(f)
    return meta_data.get("id",None)

async def get_or_update_blueprints()-> dict:
    path=asset_path/"factory/blueprints.json"
    meta_path=asset_path/"factory/fact_meta.json"
    await get_or_update_slugs()
    if not check_last():
        token=get_token()
        if token:
            async with aiohttp.ClientSession() as session:
                url=base.format(key=token)
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data=await resp.json()
                        print("Updating the blueprints...")
                        with open(path,"w",encoding="utf-8") as f:
                            json.dump(data,f,ensure_ascii=False,indent=2)
                        with open(meta_path,"w",encoding="utf-8") as f:
                            json.dump({"last_updated":datetime.now(timezone.utc).timestamp(),"id":token},f,ensure_ascii=False,indent=2)
                        return data
                    else:
                        print(f"Failed to fetch blueprints. Status code: {resp.status}")
                        with open(path,"r",encoding="utf-8") as f:
                            return json.load(f)
        else:
            print("No valid token found for fetching blueprints.")
            raise Exception("No valid token")
    else:
        with open(path,"r",encoding="utf-8") as f:
            return json.load(f)


async def get_or_update_slugs() -> dict:
    """Refresh the local item slug asset at most once every two days."""
    path = asset_path / "factory/slugs.json"
    meta_path = asset_path / "factory/fact_meta.json"

    if check_last("slugs_last_updated"):
        with open(path, encoding="utf-8") as file:
            return json.load(file)

    async with aiohttp.ClientSession() as session:
        async with session.get(slugs_url) as resp:
            if resp.status == 200:
                data = await resp.json()
                print("Updating the factory item slugs...")
                with open(path, "w", encoding="utf-8") as file:
                    json.dump(data, file, ensure_ascii=False, indent=2)

                metadata = {}
                if meta_path.exists():
                    with open(meta_path, encoding="utf-8") as file:
                        metadata = json.load(file)
                metadata["slugs_last_updated"] = datetime.now(timezone.utc).timestamp()
                with open(meta_path, "w", encoding="utf-8") as file:
                    json.dump(metadata, file, ensure_ascii=False, indent=2)
                return data

            print(f"Failed to fetch factory item slugs. Status code: {resp.status}")

    with open(path, encoding="utf-8") as file:
        return json.load(file)