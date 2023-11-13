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
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
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
    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform.SENSOR, Platform.CALENDAR]
    )

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

    # this method is called every 30 seconds
    async def async_update(self) -> None:
        _LOGGER.debug(f"Starting update of {self._attr_unique_id}")
        now = int(datetime.now().timestamp())
        programs = self.coordinator.data
        current_program = None
        for p in programs:
            if now in range(p["start"], p["end"]):
                current_program = p
                break
        if current_program is None:
            now_dt = datetime.fromtimestamp(now, self.timezone())
            first_program_start = datetime.fromtimestamp(programs[0]["start"], self.timezone())
            last_program_end = datetime.fromtimestamp(programs[-1]["end"], self.timezone())
            raise Exception(
                f"Unable to find currently airing program. Now is {now_dt}. First program starts at {first_program_start}, last program starts at {last_program_end}"
            )
        old_value = self._attr_native_value
        if current_program["diffusion"] is not None:
            self._attr_native_value = current_program["diffusion"]["title"]
            if "standFirst" in current_program["diffusion"]:
                self._attr_state_attributes["description"] = current_program["diffusion"][
                    "standFirst"
                ]
            if "url" in current_program["diffusion"]:
                self._attr_state_attributes["url"] = current_program["diffusion"]["url"]
        else:
            self._attr_native_value = "No diffusion"
            self._attr_state_attributes = current_program

        if old_value != self._attr_native_value:
            self.async_write_ha_state()

    def timezone(self):
        timezone = self.hass.config.as_dict()["time_zone"]
        return tz.gettz(timezone)

    @property
    def state_attributes(self):
        return self._attr_state_attributes

    @property
    def should_poll(self) -> bool:
        # by default, coordinatorentity are not polled
        return True


class AiringCalendar(CoordinatorEntity, CalendarEntity):
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
        self._attr_name = f"{self.config_entry.data[CONF_RADIO_STATION]} calendar"
        self._attr_unique_id = f"calendar.radio_france.{self.config_entry.entry_id}.{self.config_entry.data[CONF_RADIO_STATION]}"
        self._events = []

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
        _LOGGER.debug(f"Receiving an update for {self.unique_id} calendar")
        if not self.coordinator.last_update_success:
            _LOGGER.debug("Last coordinator failed, assuming state has not changed")
            return
        programs = self.coordinator.data

        self._events = []
        for p in programs:
            if p["diffusion"] is None:
                self._events.append(
                    CalendarEvent(
                        start=datetime.fromtimestamp(p["start"], self.timezone()),
                        end=datetime.fromtimestamp(p["end"], self.timezone()),
                        summary="No information",
                        description="there is no information on this diffusion",
                        uid=p["id"],
                    )
                )
            else:
                self._events.append(
                    CalendarEvent(
                        start=datetime.fromtimestamp(p["start"], self.timezone()),
                        end=datetime.fromtimestamp(p["end"], self.timezone()),
                        summary=p["diffusion"]["title"],
                        description=p["diffusion"]["standFirst"],
                        location=p["diffusion"].get("url", None),
                        uid=p["id"],
                    )
                )
        self.async_write_ha_state()

    def timezone(self):
        timezone = self.hass.config.as_dict()["time_zone"]
        return tz.gettz(timezone)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [e for e in self._events if e.end >= start_date and e.start <= end_date]

    @property
    def event(self) -> CalendarEvent | None:
        now = int(datetime.now().timestamp())
        now_dt = datetime.fromtimestamp(now, self.timezone())
        for e in self._events:
            if e.start <= now_dt and e.end >= now_dt:
                return e
        return None
