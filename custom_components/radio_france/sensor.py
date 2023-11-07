import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN
from . import AiringNowEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    api_coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    sensors = []
    sensors.append(AiringNowEntity(api_coordinator, hass, entry))

    async_add_entities(sensors)
    await api_coordinator.async_config_entry_first_refresh()
