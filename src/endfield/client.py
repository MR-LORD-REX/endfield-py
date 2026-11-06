from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional , Literal
import json

import aiohttp
import asyncio

from .basic.decoder import DataDecoder
from .utils import EndfieldHelpers, _retry_with_backoff, game_stats_cache, enka_data_cache

from .errors import CharacterNotFoundError
from .models import (
    ShowcaseData, PlayerProfile, CharacterData,
    StatDetail , ComputedStatsWithDetails
)
from .models.auth.game_stats import GameStats

from .models.factory.blueprint import Blueprint, OutputItems , FactoryPlans
from .models.auth.user import User
from .models.auth.indie_hard import IndieHardData
from .models.auth.allc import AllCharacters
from .basic.calculator import OBJ_TO_ID , OBJ_TO_NAME
from .proxy_pool import ProxyPool
from .resolver import AssetResolver

from .update import check_update, download_update
from .auth.api import GameApi
from .factory.api import get_or_update_blueprints
from .factory.factory import format_blueprints , items


logger = logging.getLogger(__name__)


class Endfield(EndfieldHelpers):

    def __init__(
        self,
        session: Optional[aiohttp.ClientSession] = None,
        debug: bool = False,
        timeout: int = 5,
        proxy_pool: Optional[ProxyPool] = None
    ) -> None:
        """
        Initialize the Endfield client.
        
        Args:
            session: Optional aiohttp.ClientSession to use for requests.
            debug: Enable debug logging.
            timeout: Timeout in seconds for requests.

            proxy_pool: Optional ProxyPool to use for auth requests
                (from endfield.proxy_pool import ProxyPool)
        """

        self._assets_path = Path(__file__).parent / "assets"
        self._external_session = session
        self._session: Optional[aiohttp.ClientSession] = None
        self._resolver = AssetResolver(self._assets_path, self._session)
        self._debug = debug
        self.stat_cache = game_stats_cache
        self._timeout = timeout
        self.proxies=proxy_pool
        self.enka_data_cache= enka_data_cache
        self.game_api = GameApi() 

        log_level = logging.DEBUG if debug else logging.WARNING
        logging.basicConfig(
            level=log_level,
            format="%(levelname)s [%(name)s] %(message)s",
        )
        logger.setLevel(log_level)

    async def __aenter__(self) -> "Endfield":
        await self._init_session()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    def __exit__(self, exc_type, exc, tb):
        pass


    async def close(self) -> None:
        """Close the internal aiohttp client session if it was not provided externally."""
        if self._session and not self._external_session:
            await self._session.close()
            
    def get_detailed_stats(self, char_data: CharacterData) -> ComputedStatsWithDetails:
        """
        Get detailed computed stats for a given character.
        
        Args:
            char_data: The CharacterData object to compute details for.
            
        Returns:
            ComputedStatsWithDetails: An object containing detailed stats, names, and icons.
        """
        details = []
        main_att= char_data.main_attribute.attri_id
        sub_att= char_data.sub_attribute.attri_id
        for obj_name, display_name in OBJ_TO_NAME.items():
            value = getattr(char_data.stats, obj_name, None)
            if value is not None:
                prop_id = OBJ_TO_ID.get(obj_name, "unknown")
                details.append(StatDetail(
                    value=value,
                    name=display_name,
                    icon_url=self._resolver.get_attribute_url(prop_id),
                    stat_id=prop_id,
                    main_attri= prop_id == main_att,
                    sub_attri= prop_id == sub_att
                ))
        return ComputedStatsWithDetails(all=details)

    
    async def verify_user(self, token: str) -> User | None:
        """
        Verify the user using the provided token and return a User object if valid.
        
        Args:
            token: The authentication token to verify.
        """
        if not self._session:
            await self._init_session()
        user = await self.game_api.verify_user(token, self._session)
        return user
            
    async def get_monument(self, token: str) -> IndieHardData | None:
        """
        Retrieve monument data for the user associated with the provided token.
        
        Args:
            token: The authentication token to retrieve monument data for.
        """
        if not self._session:
            await self._init_session()
        monument_data = await self.game_api.get_monument(self._session, token)
        return monument_data
        
    async def get_showcase(self, uid: int | str) -> ShowcaseData:
        """
        Retrieve showcase data (profile and characters) for a specific user ID.
        
        Args:
            uid: The user ID (UID) to fetch the showcase for.
            
        Returns:
            ShowcaseData: The retrieved showcase data including profile and all showcased characters.
        """
        if not self._session:
            await self._init_session()
        
        async def _fetch_and_decode():
            raw = await self._fetch_api(str(uid))
            return DataDecoder(raw).decode()
        
        decoded = await _retry_with_backoff(_fetch_and_decode, max_retries=3)
        char_data = decoded["playerInfo"].get("charData", [])
        gender= decoded["playerInfo"].get("businessCard",{}).get("gender",1) or 1
        if self._debug:
            with open(f"debug_{uid}.json", "w", encoding="utf-8") as f:
                json.dump(decoded, f, ensure_ascii=False, indent=2)
                
        profile= await self._build_player_profile(decoded)
        
        c_tasks = []
        for cd in char_data:
            c_tasks.append(self._build_character_data(cd,gender))
        characters = await asyncio.gather(*c_tasks)
        c_tasks.clear()
        
        for c in characters:
            st=self.get_detailed_stats(c)
            c.detailed_stats= st
            
        sk= ShowcaseData(
            profile=profile,
            characters=characters
        )
        if self._debug:
            with open(f"debug_showcase_{uid}.json", "w", encoding="utf-8") as f:
                js=sk.model_dump_json(indent=2)
                f.write(js)
        return sk
    
    async def get_character_showcase(self, uid: int | str, index: int = 0) -> CharacterData:
        """
        Retrieve character showcase data for a specific user ID and character index.
        
        Args:
            uid: The user ID (UID) to fetch the showcase for.
            index: The index of the character in the showcase (default 0).
            
        Returns:
            CharacterData: Detailed data for the specified character.
        """
        if not self._session:
            await self._init_session()
        
        async def _fetch_and_decode():
            raw = await self._fetch_api(str(uid))
            return DataDecoder(raw).decode()
        
        decoded = await _retry_with_backoff(_fetch_and_decode, max_retries=3)
        char_data = decoded["playerInfo"].get("charData", [])
        gender= decoded["playerInfo"].get("businessCard",{}).get("gender",1) or 1
        if index < 0 or index >= len(char_data):
            raise CharacterNotFoundError(f"Character index {index} out of range for user {uid}")
        char_data= char_data[index]
        character = await self._build_character_data(char_data, gender)
        character.detailed_stats= self.get_detailed_stats(character)
        return character

    async def get_game_character(self, token: str, char_id:str,gender:Literal["1","2"]="1") -> CharacterData:
        """
        Retrieve a specific game character's data using an authentication token and character ID.
        
        Args:
            token: The authentication token to verify the user.
            char_id: Hashed character ID to fetch the character data for.
            gender: The gender of the MC (1 for male, 2 for female, default is 1).
        """
        if not self._session:
            await self._init_session()
        data= await self.game_api.get_char(self._session, token, char_id)
        data= await self._convert_game_character(data, gender=int(gender))
        return data
    
    async def get_all_characters(self, token: str) -> AllCharacters | None:
        """
        Retrieve all characters for the user associated with the provided token.
        
        Args:
            token: The authentication token to retrieve all characters for.
        """
        if not self._session:
            await self._init_session()
        data= await self.game_api.get_all_characters(self._session, token)
        return data
    
    async def get_profile(self, uid: int | str) -> PlayerProfile:
        """
        Retrieve only the player profile data for a specific user ID.
        
        Args:
            uid: The user ID (UID) to fetch the profile for.
            
        Returns:
            PlayerProfile: The player's profile data.
        """
        if not self._session:
            await self._init_session()
        
        async def _fetch_and_decode():
            raw = await self._fetch_api(str(uid))
            return DataDecoder(raw).decode()
        
        decoded = await _retry_with_backoff(_fetch_and_decode, max_retries=3)
        return await self._build_player_profile(decoded)
    
    async def get_game_stats(self, token: str, server: int = 3) -> GameStats | None:
        """
        Fetch game statistics using an authentication token.
        
        Args:
            token: The authentication token.
            server: The server ID (default 3).
            
        Returns:
            GameStats | None: Game statistics if successful, None otherwise.
        """
        try:
            cached_stats = self.stat_cache.get(token)
            if cached_stats:
                logger.debug("Using cached game stats")
                return cached_stats
            else:
                logger.debug("No cached stats found, fetching new data")
                if self.proxies:
                    async with self.proxies.get_proxy() as p:
                        logger.debug(f"Using proxy: {p.proxy}")
                        stats = await self.game_api.game_stats(p.session, token, server)
                else:
                    logger.debug("No proxies found, using direct connection")
                    stats = await self.game_api.game_stats(self._session, token, server)
                if stats:
                    self.stat_cache.set(token, stats, ttl=300)  # Cache for 5 minutes
            return stats
        except Exception as e:
            logger.error(f"Error fetching game stats: {e}")
            return None
        
    async def perform_daily_sign(self, token: str) -> str:
        """
        Perform daily sign-in using an authentication token.
        
        Args:
            token: The authentication token.
            
        Returns:
            str: A message indicating the result of the sign-in.
        """
        msg = await self.game_api.perform_daily_sign(self._session, token)
        return msg
    
    async def check_for_updates(self):
        """
        Check for available asset updates.
        
        Returns:
            Any: The update check result based on the underlying check_update implementation.
        """
        return await check_update()
    
    async def update_assets(self) -> None:
        """Download and apply updates for assets."""
        await download_update()
    
    
    async def get_factory_blueprints(
        self,
        region:Literal["Americas/Europe","Asia","Both"]="Both",
        item: Literal["all"] | items = "all",
        start:int=0,
        end:int=10
    ) -> FactoryPlans :
        """
        Gets factory blueprints from the Endfieldtools API, 
        with optional filtering by region and output item, and pagination support.
        
        Args:
            region: Filter blueprints by region ("Americas/Europe", "Asia", or "Both"). Default is "Both".
            item: Filter blueprints by output item (e.g., "xiranite", "heavy-xiranite", or "all"). Default is "all".
            start: The starting index for pagination (0-based). Default is 0.
            end: The ending index for pagination (exclusive). Default is 10.
            
        Returns:
            FactoryPlans: An object containing a list of blueprints and the total count of blueprints matching the filters.
            (Note: The total count is the number of blueprints that match the filters, regardless of pagination.)
        """
        
        data= await get_or_update_blueprints()
        blueprints,total= format_blueprints(
            data=data,
            region=region,
            by_views=True,
            item_filter=item,
            _from=start,
            _to=end
        )
        bps=[]
        for bp in blueprints:
            id= bp.get("id", "")
            name= bp.get("name", "")
            description= bp.get("short_description", "") or ""
            s_url= bp.get("thumbnail_url", "") or ""
            code=bp.get("ingame_code", "") or ""
            region= bp.get("ingame_region", "") or ""
            output= bp.get("output_items_with_rates", [])
            ot=[]
            for i , o in enumerate(output):
                if isinstance(o,dict):
                    o_id= o.get("itemId", "")
                elif isinstance(o,list) and len(o) > 0:
                    o_id= o[0].get("itemId", "")
                else:
                    logger.warning(f"Unexpected output item format in blueprint {id} at index {i}: {o}")
                    continue   
                o_name= self._resolver.get_fact_slug_by_id(o_id) or "unknown"
                icon_url= self._resolver.get_fact_item_icon_url(o_id)
                rate= o.get("ratePerMinute", 0)
                ot.append(OutputItems(
                    id=o_id,    
                    name=o_name,
                    icon_url=icon_url,
                    per_minute=round(rate)
                ))
            bps.append(Blueprint(
                id=id,
                name=name,
                description=description,
                code=code,
                region=region,
                output_items=ot,
                screenshot_url=s_url
            ))
        return FactoryPlans(
            blueprints=bps,
            total=total
            )
        
        
