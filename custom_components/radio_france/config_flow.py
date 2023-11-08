import logging
from typing import Any, Optional, Tuple
import voluptuous as vol
from homeassistant.core import callback, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant import config_entries
from .api import RadioFranceApi
from .const import (
    DOMAIN,
    CONF_API_KEY,
    CONF_RADIO_STATION,
)

_LOGGER = logging.getLogger(__name__)

# Description of the config flow:
# async_step_user is called when user starts to configure the integration
# we follow with a flow of form/menu
# eventually we call async_create_entry with a dictionnary of data
# HA calls async_setup_entry with a ConfigEntry which wraps this data (defined in __init__.py)
# in async_setup_entry we call hass.config_entries.async_forward_entry_setups to setup each relevant platform (sensor in our case)
# HA calls async_setup_entry from sensor.py

API_KEY_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_API_KEY, default="api key from developers.radiofrance.fr"
        ): cv.string
    }
)


async def get_radio_stations(hass: HomeAssistant, token: str) -> dict[str, str]:
    try:
        client = RadioFranceApi(token)
        return await client.get_stations()
    except ValueError as exc:
        raise exc


class SetupConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        """Initialize"""
        self.data = {}

    @callback
    def _show_setup_form(self, step_id=None, user_input=None, schema=None, errors=None):
        """Show the setup form to the user."""

        if user_input is None:
            user_input = {}

        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors=errors or {},
        )

    async def async_step_user(self, user_input: Optional[dict[str, Any]] = None):
        """Called once with None as user_input, then a second time with user provided input"""
        errors = {}
        if user_input is not None:
            self.data = user_input
            return await self.async_step_radio_station_selection()
        return self._show_setup_form("user", user_input, API_KEY_SCHEMA, errors)

    async def async_step_radio_station_selection(self, user_input=None):
        """Handle selection of radio station amongst possible values"""
        errors = {}
        if user_input is not None:
            radio_station = user_input.get(CONF_RADIO_STATION)
            self.data[CONF_RADIO_STATION] = radio_station
            return self.async_create_entry(title="radio_france", data=self.data)
        all_stations = await get_radio_stations(self.hass, self.data[CONF_API_KEY])
        default_station = list(all_stations.keys())[0]
        RADIO_STATIONS_SCHEMA = vol.Schema(
            {
                vol.Required(CONF_RADIO_STATION, default=default_station): vol.In(
                    all_stations
                )
            }
        )
        return self._show_setup_form(
            "radio_station_selection", None, RADIO_STATIONS_SCHEMA, errors
        )
