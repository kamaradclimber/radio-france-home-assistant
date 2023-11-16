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
    LOW_HEADSUP_STATIONS,
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
        self.station_code = config[CONF_RADIO_STATION]
        self.logger = logging.getLogger(f"{__name__}.{self.station_code}.coordinator")

        update_interval = timedelta(minutes=60)
        for station_matcher in LOW_HEADSUP_STATIONS:
            if re.search(station_matcher, self.station_code):
                self.logger.warn(
                    "Data will be refreshed every two minutes because station does not publish program in advance"
                )
                update_interval = timedelta(minutes=2)

        super().__init__(
            hass,
            self.logger,
            name="radio france api",  # for logging purpose
            update_interval=update_interval,
            update_method=self.update_method,
        )
        self.config = config
        self.hass = hass
        self.api_token = config[CONF_API_KEY]

    async def update_method(self):
        """Fetch data from API endpoint."""
        try:
            self.logger.debug(
                f"Calling update method, {len(self._listeners)} listeners subscribed"
            )
            if "RADIOFRANCE_APIFAIL" in os.environ:
                raise UpdateFailed(
                    "Failing update on purpose to test state restoration"
                )
            self.logger.debug("Starting collecting data")

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


class AiringNowProgramEntity(CoordinatorEntity, SensorEntity):
    """Expose the program airing now on the given station"""

    def __init__(
        self,
        coordinator: RadioFranceAPICoordinator,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
    ):
        self.logger = logging.getLogger(
            f"{__name__}.{config_entry.data[CONF_RADIO_STATION]}"
        )
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
        self.logger.debug(f"Receiving an update for {self.unique_id} sensor")
        if not self.coordinator.last_update_success:
            self.logger.debug("Last coordinator failed, assuming state has not changed")
            return

    # this method is called every 30 seconds
    async def async_update(self) -> None:
        self.logger.debug(f"Starting update of {self._attr_unique_id}")
        now = int(datetime.now().timestamp())
        programs = self.coordinator.data
        current_program = None
        for p in programs:
            if "diffusion" not in p or p["diffusion"] is None:
                continue
            if now in range(p["start"], p["end"]):
                current_program = p
                break
        old_value = self._attr_native_value
        if current_program is None:
            now_dt = datetime.fromtimestamp(now, self.timezone())
            first_program_start = datetime.fromtimestamp(
                programs[0]["start"], self.timezone()
            )
            last_program_end = datetime.fromtimestamp(
                programs[-1]["end"], self.timezone()
            )
            self._attr_native_value = None
            self._attr_state_attributes = {}
            if old_value != self._attr_native_value:
                self.async_write_ha_state()
            if now_dt >= last_program_end:
                # this is the case of FIP and other music-only station. See https://github.com/kamaradclimber/radio-france-home-assistant/issues/1
                raise Exception(
                    f"Unable to find currently airing program. Now is {now_dt}. First program starts at {first_program_start}, last program stops at {last_program_end}"
                )
            else:
                return
        if current_program["diffusion"] is not None:
            self._attr_native_value = current_program["diffusion"]["title"]
            if "standFirst" in current_program["diffusion"]:
                self._attr_state_attributes["description"] = current_program[
                    "diffusion"
                ]["standFirst"]
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


class AiringNowTrackEntity(CoordinatorEntity, SensorEntity):
    """Expose the track airing now on the given station"""

    def __init__(
        self,
        coordinator: RadioFranceAPICoordinator,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
    ):
        self.logger = logging.getLogger(
            f"{__name__}.{config_entry.data[CONF_RADIO_STATION]}"
        )
        CoordinatorEntity.__init__(self, coordinator)
        self._coordinator = coordinator
        self.hass = hass
        self.config_entry = config_entry
        self._attr_name = (
            f"Current track on {self.config_entry.data[CONF_RADIO_STATION]}"
        )
        self._attr_native_value = None
        self._attr_state_attributes = {}
        self._attr_unique_id = f"sensor.radio_france.{self.config_entry.entry_id}.{self.config_entry.data[CONF_RADIO_STATION]}-airing-now-track"

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
        self.logger.debug(f"Receiving an update for {self.unique_id} sensor")
        if not self.coordinator.last_update_success:
            self.logger.debug("Last coordinator failed, assuming state has not changed")
            return

    # this method is called every 30 seconds
    async def async_update(self) -> None:
        self.logger.debug(f"Starting update of {self._attr_unique_id}")
        now = int(datetime.now().timestamp())
        programs = self.coordinator.data
        current_program = None
        for p in programs:
            if "track" not in p or p["track"] is None:
                continue
            if now in range(p["start"], p["end"]):
                current_program = p
                break
        old_value = self._attr_native_value
        if current_program is None:
            now_dt = datetime.fromtimestamp(now, self.timezone())
            first_program_start = datetime.fromtimestamp(
                programs[0]["start"], self.timezone()
            )
            last_program_end = datetime.fromtimestamp(
                programs[-1]["end"], self.timezone()
            )
            self._attr_native_value = None
            self._attr_state_attributes = {}
            if old_value != self._attr_native_value:
                self.async_write_ha_state()
            if now_dt >= last_program_end:
                # this is the case of FIP and other music-only station. See https://github.com/kamaradclimber/radio-france-home-assistant/issues/1
                raise Exception(
                    f"Unable to find currently airing track. Now is {now_dt}. First track starts at {first_program_start}, last track stops at {last_program_end}"
                )
            else:
                return
        if current_program["track"] is not None:
            self._attr_native_value = current_program["track"]["title"]
            if "albumTitle" in current_program["track"]:
                self._attr_state_attributes["description"] = current_program["track"][
                    "albumTitle"
                ]
        else:
            self._attr_native_value = "No music currently played"
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
        self.logger = logging.getLogger(
            f"{__name__}.{config_entry.data[CONF_RADIO_STATION]}"
        )
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
        self.logger.debug(f"Receiving an update for {self.unique_id} calendar")
        if not self.coordinator.last_update_success:
            self.logger.debug("Last coordinator failed, assuming state has not changed")
            return
        programs = self.coordinator.data

        self._events = []
        for p in programs:
            if "track" in p and p["track"] is not None:
                self._events.append(
                    CalendarEvent(
                        start=datetime.fromtimestamp(p["start"], self.timezone()),
                        end=datetime.fromtimestamp(p["end"], self.timezone()),
                        summary=p["track"]["title"],
                        description=f"from album '{p['track']['albumTitle']}'",
                        uid=p["id"],
                    )
                )
            elif "diffusion" in p and p["diffusion"] is not None:
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
            elif "title" in p:
                self._events.append(
                    CalendarEvent(
                        start=datetime.fromtimestamp(p["start"], self.timezone()),
                        end=datetime.fromtimestamp(p["end"], self.timezone()),
                        summary=p["title"],
                        uid=p["id"],
                    )
                )
            else:
                self.logger.warning(f"Event {p} is not handled yet by this integration")
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
        matching_events = [
            e for e in self._events if e.start <= now_dt and e.end >= now_dt
        ]
        matching_events.sort(key=lambda e: e.end - e.start)
        if len(matching_events) > 0:
            if len(matching_events) > 1:
                self.logger.debug(
                    f"Found {len(matching_events)} diffusions running simultaneously. Selecting the shortest one (likely a track during a longer show)"
                )
                self.logger.debug(
                    f"Shortest one is {matching_events[0].summary}. Longest one is {matching_events[-1].summary}"
                )
            return matching_events[0]
        return None
