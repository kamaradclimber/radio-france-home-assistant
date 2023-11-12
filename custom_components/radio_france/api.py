import logging
from typing import Optional, Tuple
from homeassistant.helpers.update_coordinator import UpdateFailed
import re
from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport
from datetime import datetime
import os

from .const import STATIONS_LIST_STUB, GRID_STUB

_LOGGER = logging.getLogger(__name__)


class RadioFranceApiError(Exception):
    pass


class RadioFranceApi:
    """Api to get Radio France data"""

    def __init__(
        self,
        token: str,
    ) -> None:
        self._transport = AIOHTTPTransport(
            url=f"https://openapi.radiofrance.fr/v1/graphql?x-token={token}"
        )

    async def get_programs(self, station_code: str) -> list:
        start_ts = int(datetime.now().timestamp()) - 2 * 3600
        end_ts = int(datetime.now().timestamp() + 6 * 3600)
        # note: all { are doubled because we format the string
        programs_query = """
        query {{
          grid(
            start: {start_ts}
            end: {end_ts}
            station: {station_code}
            includeTracks: true
          ) {{
            ... on DiffusionStep {{
              id
              start
              end
              diffusion {{
                id
                title
                standFirst
                published_date
                url
                }}
            }}
            ... on TrackStep {{
              id
              start
              end
              track {{
                id
                title
                albumTitle
                }}
              }}
            ... on BlankStep {{
              id
              title
              start
              end
              }}
            }}
          }}
        """.format(
            start_ts=start_ts, end_ts=end_ts, station_code=station_code
        )
        _LOGGER.debug(programs_query)
        if os.getenv("RADIOFRANCE_STUB"):
            result = GRID_STUB
        else:
            async with Client(
                transport=self._transport,
                fetch_schema_from_transport=True,
            ) as session:
                query = gql(programs_query)
                result = await session.execute(query)
                _LOGGER.debug(result)

        # conformity check
        for p in result["grid"]:
            if "track" in p:
                _LOGGER.debug(
                    f"Convert track {p['track']['title']} to diffusion format"
                )
                p["diffusion"] = {
                    "title": p["track"]["title"],
                    "standFirst": f'From the album {p["track"]["albumTitle"]}',
                }
            if "diffusion" not in p:
                _LOGGER.warn(
                    f"Following program does not have the diffusion attribute: {p}. Please report this to the integration maintainer on https://github.com/kamaradclimber/radio-france-home-assistant/issues/new?assignees=&labels=&projects=&template=bug_report.md&title="
                )
        return result["grid"]

    async def get_stations(self) -> dict[str, str]:
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
