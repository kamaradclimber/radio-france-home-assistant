import os
import re
import json
import urllib.parse
import logging
from datetime import timedelta, datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict, Optional, Tuple
from dateutil import tz
from itertools import dropwhile, takewhile


from homeassistant.const import Platform, STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.typing import ConfigType
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.components.sensor import RestoreSensor, SensorEntity
from .const import (
    DOMAIN,
    NAME,
    CONF_RADIO_STATION,
    CONF_API_KEY,
)
from .api import RadioFranceApi, RadioFranceApiError


_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass, config_entry: ConfigEntry):
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    # here we store the coordinator for future access
    if entry.entry_id not in hass.data[DOMAIN]:
        hass.data[DOMAIN][entry.entry_id] = {}
    hass.data[DOMAIN][entry.entry_id]["coordinator"] = RadioFranceAPICoordinator(
        hass, dict(entry.data)
    )

    # will make sure async_setup_entry from sensor.py is called
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])

    # subscribe to config updates
    entry.async_on_unload(entry.add_update_listener(update_entry))

    return True


async def update_entry(hass, entry):
    """
    This method is called when options are updated
    We trigger the reloading of entry (that will eventually call async_unload_entry)
    """
    _LOGGER.debug("update_entry method called")
    # will make sure async_setup_entry from sensor.py is called
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """This method is called to clean all sensors before re-adding them"""
    _LOGGER.debug("async_unload_entry method called")
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, [Platform.SENSOR]
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


class RadioFranceAPICoordinator(DataUpdateCoordinator):
    """A coordinator to fetch data from the api only once"""

    def __init__(self, hass, config: ConfigType):
        super().__init__(
            hass,
            _LOGGER,
            name="radio france api",  # for logging purpose
            update_interval=timedelta(minutes=60),
            update_method=self.update_method,
        )
        self.config = config
        self.hass = hass
        self.station_code = config[CONF_RADIO_STATION]
        self.api_token = config[CONF_API_KEY]

    async def update_method(self):
        """Fetch data from API endpoint."""
        try:
            _LOGGER.debug(
                f"Calling update method, {len(self._listeners)} listeners subscribed"
            )
            if "RADIOFRANCE_APIFAIL" in os.environ:
                raise UpdateFailed(
                    "Failing update on purpose to test state restoration"
                )
            _LOGGER.debug("Starting collecting data")

            api = RadioFranceApi(self.api_token)
            try:
                data = await api.get_programs(self.station_code)
            except RadioFranceApiError as e:
                raise UpdateFailed(
                    f"Failed fetching data from radio france api: {e.text}"
                )

            return data
        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}")


class AiringNowEntity(CoordinatorEntity, SensorEntity):
    """Expose the program airing now on the given station"""

    def __init__(
        self,
        coordinator: RadioFranceAPICoordinator,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
    ):
        CoordinatorEntity.__init__(self, coordinator)
        self._coordinator = coordinator
        self.hass = hass
        self.config_entry = config_entry
        self._attr_name = f"Airing now on {self.config_entry.data[CONF_RADIO_STATION]}"
        self._attr_native_value = None
        self._attr_state_attributes = {}
        self._attr_unique_id = f"sensor.radio_france.{self.config_entry.entry_id}.{self.config_entry.data[CONF_RADIO_STATION]}-airing-now"

        self._attr_device_info = DeviceInfo(
            name=f"{NAME} {config_entry.data.get(CONF_RADIO_STATION)}",
            entry_type=DeviceEntryType.SERVICE,
            identifiers={
                (
                    DOMAIN,
                    str(config_entry.data.get(CONF_RADIO_STATION)),
                )
            },
            manufacturer=NAME,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        _LOGGER.debug(f"Receiving an update for {self.unique_id} sensor")
        if not self.coordinator.last_update_success:
            _LOGGER.debug("Last coordinator failed, assuming state has not changed")
            return
        now = int(datetime.now().timestamp())
        programs = self.coordinator.data
        consecutive_programs = [
            (programs[i], programs[i + 1]) for i in range(len(programs) - 1)
        ]
        current_program = None
        for p1, p2 in consecutive_programs:
            if now in range(
                int(p1["diffusion"]["published_date"]),
                int(p2["diffusion"]["published_date"]),
            ):
                current_program = p1
                break
        if current_program is None:
            now_dt = datetime.fromtimestamp(now)
            first_program_start = datetime.fromtimestamp(
                int(programs[0]["diffusion"]["published_date"])
            )
            last_program_start = datetime.fromtimestamp(
                int(programs[-1]["diffusion"]["published_date"])
            )
            raise Exception(
                f"Unable to find currently airing program. Now is {now_dt}. First program starts at {first_program_start}, last program starts at {last_program_start}"
            )
        self._attr_native_value = current_program["diffusion"]["title"]
        self._attr_state_attributes["description"] = current_program["diffusion"][
            "standFirst"
        ]
        self._attr_state_attributes["url"] = current_program["diffusion"]["url"]

        self.async_write_ha_state()

    @property
    def state_attributes(self):
        return self._attr_state_attributes
