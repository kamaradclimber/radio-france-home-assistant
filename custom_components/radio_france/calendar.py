import logging
from datetime import timedelta
import asyncio

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import EntityPlatformState

from .const import DOMAIN
from . import AiringCalendar

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    api_coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    async_add_entities([AiringCalendar(api_coordinator, hass, entry)])
    await asyncio.sleep(0.2)  # FIXME: we should not need to sleep here!
    await api_coordinator.async_request_refresh()
