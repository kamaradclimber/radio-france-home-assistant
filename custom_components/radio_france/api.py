import logging
import aiohttp
from typing import Optional, Tuple
from aiohttp.client import ClientTimeout
from homeassistant.helpers.update_coordinator import UpdateFailed
from .const import GEOAPI_GOUV_URL, ADDRESS_API_URL, VIGIEAU_API_URL
import re

DEFAULT_TIMEOUT = 120
CLIENT_TIMEOUT = ClientTimeout(total=DEFAULT_TIMEOUT)

_LOGGER = logging.getLogger(__name__)


class RadioFranceApi:
    """Api to get Radio France data"""

    def __init__(
        self, session: Optional[aiohttp.ClientSession] = None, timeout=CLIENT_TIMEOUT
    ) -> None:
        self._timeout = timeout
        self._session = session or aiohttp.ClientSession()


    async def get_data(self, zipcode) -> dict:

        # FIXME: data fetch should be implemented here

        return data
