import logging
from typing import Optional, Tuple
from homeassistant.helpers.update_coordinator import UpdateFailed
import re
from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport
import os

from .const import STATIONS_LIST_STUB

_LOGGER = logging.getLogger(__name__)


class RadioFranceApi:
    """Api to get Radio France data"""


    def __init__(
        self,
        token: str,
    ) -> None:
        self._transport = AIOHTTPTransport(url=f"https://openapi.radiofrance.fr/v1/graphql?x-token={token}")

    async def get_stations(self) -> dict[str,str]:
        """Get stations list"""

        station_list_query = """
                query {
                  brands {
                     id
                     title
                     baseline
                     description
                     websiteUrl
                     playerUrl
                     liveStream
                     localRadios {
                       id
                       title
                       description
                       liveStream
                       playerUrl
                     }
                     webRadios {
                       id
                       title
                       description
                       liveStream
                       playerUrl
                     }
                   }
                }
                """


        if os.getenv("RADIOFRANCE_STUB"):
            result = STATIONS_LIST_STUB
        else:
            async with Client(
                transport=self._transport,
                fetch_schema_from_transport=True,
            ) as session:
                query = gql(station_list_query)
                result = await session.execute(query)
                _LOGGER.debug(result)
        stations = {}
        for brand in result["brands"]:
            stations[brand["id"]] = brand["title"]
        return stations

    async def get_data(self, zipcode) -> dict:

        # FIXME: data fetch should be implemented here

        return data
