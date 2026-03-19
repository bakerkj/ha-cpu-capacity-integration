# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_PUBLISH_INTERVAL_SECONDS,
    CONF_SAMPLE_INTERVAL_SECONDS,
    DEFAULT_PUBLISH_INTERVAL_SECONDS,
    DEFAULT_SAMPLE_INTERVAL_SECONDS,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import CpuCapacityCoordinator, CpuCapacitySampler

_LOGGER = logging.getLogger(__name__)


@dataclass
class CpuCapacityEntryData:
    sampler: CpuCapacitySampler
    coordinator: CpuCapacityCoordinator


def _entry_float(entry: ConfigEntry, key: str, default: float) -> float:
    value = entry.options.get(key, entry.data.get(key, default))
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


async def async_setup(_hass: HomeAssistant, _config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    sample_interval = max(
        0.1,
        _entry_float(
            entry, CONF_SAMPLE_INTERVAL_SECONDS, DEFAULT_SAMPLE_INTERVAL_SECONDS
        ),
    )
    publish_interval = max(
        1.0,
        _entry_float(
            entry, CONF_PUBLISH_INTERVAL_SECONDS, DEFAULT_PUBLISH_INTERVAL_SECONDS
        ),
    )
    if publish_interval < sample_interval:
        publish_interval = sample_interval

    logger = _LOGGER.getChild(entry.entry_id)

    sampler = CpuCapacitySampler(
        hass,
        logger,
        sample_interval_seconds=sample_interval,
        publish_interval_seconds=publish_interval,
    )

    await sampler.async_start()

    coordinator = CpuCapacityCoordinator(hass, logger, sampler)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = CpuCapacityEntryData(
        sampler=sampler,
        coordinator=coordinator,
    )

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await sampler.async_stop()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
        raise

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry_data: CpuCapacityEntryData = hass.data[DOMAIN][entry.entry_id]

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    await entry_data.sampler.async_stop()
    hass.data[DOMAIN].pop(entry.entry_id, None)
    if not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
