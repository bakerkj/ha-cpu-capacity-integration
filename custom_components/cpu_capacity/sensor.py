# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfFrequency
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CpuCapacityEntryData
from .const import DOMAIN
from .coordinator import CpuCapacityCoordinator


@dataclass(frozen=True, kw_only=True)
class CpuMetricDescription(SensorEntityDescription):
    metric_key: str


def _build_descriptions(
    supports_capacity_adjusted: bool,
) -> list[CpuMetricDescription]:
    descriptions: list[CpuMetricDescription] = []

    for window in ("1m", "5m", "15m"):
        descriptions.append(
            CpuMetricDescription(
                key=f"mhz_{window}",
                metric_key=f"mhz_{window}",
                name=f"MHz {window}",
                native_unit_of_measurement=UnitOfFrequency.MEGAHERTZ,
                device_class=SensorDeviceClass.FREQUENCY,
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:sine-wave",
                suggested_display_precision=0,
            )
        )
        descriptions.append(
            CpuMetricDescription(
                key=f"load_pct_{window}",
                metric_key=f"load_pct_{window}",
                name=f"Load {window}",
                native_unit_of_measurement=PERCENTAGE,
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:gauge",
                suggested_display_precision=2,
            )
        )

        if supports_capacity_adjusted:
            descriptions.append(
                CpuMetricDescription(
                    key=f"capacity_adjusted_load_pct_{window}",
                    metric_key=f"capacity_adjusted_load_pct_{window}",
                    name=f"Capacity-Adjusted Load {window}",
                    native_unit_of_measurement=PERCENTAGE,
                    state_class=SensorStateClass.MEASUREMENT,
                    icon="mdi:speedometer",
                    suggested_display_precision=2,
                )
            )

    descriptions.append(
        CpuMetricDescription(
            key="max_mhz",
            metric_key="max_mhz",
            name="Max MHz",
            native_unit_of_measurement=UnitOfFrequency.MEGAHERTZ,
            device_class=SensorDeviceClass.FREQUENCY,
            icon="mdi:sine-wave",
            entity_category=EntityCategory.DIAGNOSTIC,
            suggested_display_precision=0,
        )
    )
    descriptions.append(
        CpuMetricDescription(
            key="epp",
            metric_key="epp",
            name="Energy Performance Preference (EPP)",
            icon="mdi:tune-vertical",
            entity_category=EntityCategory.DIAGNOSTIC,
        )
    )
    descriptions.append(
        CpuMetricDescription(
            key="epb",
            metric_key="epb",
            name="Energy Performance Bias (EPB)",
            icon="mdi:tune-vertical",
            entity_category=EntityCategory.DIAGNOSTIC,
            suggested_display_precision=0,
        )
    )

    return descriptions


class CpuCapacitySensor(CoordinatorEntity[CpuCapacityCoordinator], SensorEntity):
    entity_description: CpuMetricDescription

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry_id: str,
        coordinator: CpuCapacityCoordinator,
        cpu: int,
        description: CpuMetricDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._cpu = cpu

        self._attr_unique_id = f"{entry_id}_cpu{cpu}_{description.metric_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_cpu_{cpu}")},
            name=f"CPU Capacity {cpu}",
            manufacturer="Linux",
            model="Per-CPU capacity telemetry",
        )

    @property
    def _cpu_data(self) -> dict[str, Any] | None:
        cpus = self.coordinator.data.get("cpus") if self.coordinator.data else None
        if not isinstance(cpus, dict):
            return None
        data = cpus.get(self._cpu)
        if not isinstance(data, dict):
            return None
        return data

    @property
    def native_value(self) -> Any:
        cpu_data = self._cpu_data
        if cpu_data is None:
            return None
        return cpu_data.get(self.entity_description.metric_key)

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        return self.native_value is not None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data: CpuCapacityEntryData = hass.data[DOMAIN][entry.entry_id]
    coordinator = entry_data.coordinator
    supports = entry_data.sampler.supports_capacity_adjusted

    entities: list[CpuCapacitySensor] = []
    for cpu in entry_data.sampler.cpu_ids:
        descriptions = _build_descriptions(supports.get(cpu, False))
        for description in descriptions:
            entities.append(
                CpuCapacitySensor(entry.entry_id, coordinator, cpu, description)
            )

    async_add_entities(entities)
