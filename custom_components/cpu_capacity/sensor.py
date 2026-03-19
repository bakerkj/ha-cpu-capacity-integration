# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
import re
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
    supports_epp: bool,
    supports_epb: bool,
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
    if supports_epp:
        descriptions.append(
            CpuMetricDescription(
                key="epp",
                metric_key="epp",
                name="Energy Performance Preference",
                icon="mdi:tune-vertical",
                entity_category=EntityCategory.DIAGNOSTIC,
            )
        )
    if supports_epb:
        descriptions.append(
            CpuMetricDescription(
                key="epb",
                metric_key="epb",
                name="Energy Performance Bias",
                icon="mdi:tune-vertical",
                entity_category=EntityCategory.DIAGNOSTIC,
                suggested_display_precision=0,
            )
        )

    return descriptions


def _cpu_device_info(entry_id: str, cpu: int) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_cpu_{cpu}")},
        name=f"CPU Capacity {cpu}",
        manufacturer="Linux",
        model="Per-CPU capacity telemetry",
    )


def _cpu_snapshot(
    coordinator: CpuCapacityCoordinator,
    cpu: int,
) -> dict[str, Any] | None:
    cpus = coordinator.data.get("cpus") if coordinator.data else None
    if not isinstance(cpus, dict):
        return None
    data = cpus.get(cpu)
    if not isinstance(data, dict):
        return None
    return data


def _normalize_attribute_key(raw_key: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", raw_key.strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "value"


def _round_summary_value(key: str, value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if not isinstance(value, (int, float)):
        return value

    number = float(value)
    if key == "max_mhz" or key.startswith("mhz_"):
        return int(round(number))
    if key.startswith("load_pct_") or key.startswith("capacity_adjusted_load_pct_"):
        return round(number, 4)
    return value


class CpuCapacityBaseSensor(CoordinatorEntity[CpuCapacityCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry_id: str,
        coordinator: CpuCapacityCoordinator,
        cpu: int,
    ) -> None:
        super().__init__(coordinator)
        self._cpu = cpu
        self._attr_device_info = _cpu_device_info(entry_id, cpu)

    @property
    def _cpu_data(self) -> dict[str, Any] | None:
        return _cpu_snapshot(self.coordinator, self._cpu)

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        return self._cpu_data is not None


class CpuCapacitySensor(CpuCapacityBaseSensor):
    entity_description: CpuMetricDescription

    def __init__(
        self,
        entry_id: str,
        coordinator: CpuCapacityCoordinator,
        cpu: int,
        description: CpuMetricDescription,
    ) -> None:
        super().__init__(entry_id, coordinator, cpu)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_cpu{cpu}_{description.metric_key}"

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


class CpuCapacitySummarySensor(CpuCapacityBaseSensor):
    _attr_name = "Summary"
    _attr_icon = "mdi:table"

    def __init__(
        self,
        entry_id: str,
        coordinator: CpuCapacityCoordinator,
        cpu: int,
    ) -> None:
        super().__init__(entry_id, coordinator, cpu)
        self._attr_unique_id = f"{entry_id}_cpu{cpu}_summary"

    @property
    def native_value(self) -> Any:
        if self._cpu_data is None:
            return None
        return self._cpu

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        cpu_data = self._cpu_data
        if cpu_data is None:
            return None

        attributes: dict[str, Any] = {"cpu": self._cpu}
        for raw_key, raw_value in cpu_data.items():
            normalized_key = _normalize_attribute_key(str(raw_key))
            attributes[normalized_key] = _round_summary_value(normalized_key, raw_value)
        return attributes


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data: CpuCapacityEntryData = hass.data[DOMAIN][entry.entry_id]
    coordinator = entry_data.coordinator
    supports = entry_data.sampler.supports_capacity_adjusted

    entities: list[SensorEntity] = []
    for cpu in entry_data.sampler.cpu_ids:
        entities.append(CpuCapacitySummarySensor(entry.entry_id, coordinator, cpu))

        cpu_data = _cpu_snapshot(coordinator, cpu) or {}
        descriptions = _build_descriptions(
            supports_capacity_adjusted=supports.get(cpu, False),
            supports_epp=cpu_data.get("epp") is not None,
            supports_epb=cpu_data.get("epb") is not None,
        )
        for description in descriptions:
            entities.append(
                CpuCapacitySensor(entry.entry_id, coordinator, cpu, description)
            )

    async_add_entities(entities)
