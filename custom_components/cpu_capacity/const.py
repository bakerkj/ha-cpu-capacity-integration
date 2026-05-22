# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.

from collections.abc import Mapping
from typing import Any

from homeassistant.const import Platform

DOMAIN = "cpu_capacity"
PLATFORMS: list[Platform] = [Platform.SENSOR]

CONF_SAMPLE_INTERVAL_SECONDS = "sample_interval_seconds"
# Legacy single publish interval. Still read as the fallback for any window
# whose per-window key is absent, so pre-existing config entries keep their
# previous behaviour until the windows are tuned individually.
CONF_PUBLISH_INTERVAL_SECONDS = "publish_interval_seconds"
CONF_PUBLISH_INTERVAL_1M_SECONDS = "publish_interval_1m_seconds"
CONF_PUBLISH_INTERVAL_5M_SECONDS = "publish_interval_5m_seconds"
CONF_PUBLISH_INTERVAL_15M_SECONDS = "publish_interval_15m_seconds"

# Ordered so callers iterate windows deterministically (forms, refreshes).
PUBLISH_INTERVAL_CONF_BY_WINDOW: dict[str, str] = {
    "1m": CONF_PUBLISH_INTERVAL_1M_SECONDS,
    "5m": CONF_PUBLISH_INTERVAL_5M_SECONDS,
    "15m": CONF_PUBLISH_INTERVAL_15M_SECONDS,
}
# Non-window sensors (max_mhz, epp, epb, summary) follow this window's cadence.
PRIMARY_WINDOW = "1m"

DEFAULT_SAMPLE_INTERVAL_SECONDS = 0.5
DEFAULT_PUBLISH_INTERVAL_SECONDS = 15.0
DEFAULT_NAME = "CPU Capacity"

UNIQUE_ID = "cpu_capacity_singleton"

MIN_SAMPLE_INTERVAL = 0.1
MAX_SAMPLE_INTERVAL = 10.0
MIN_PUBLISH_INTERVAL = 1.0
MAX_PUBLISH_INTERVAL = 3600.0
STALE_DATA_TIMEOUT_MULTIPLIER = 3.0
SUMMARY_SENSOR_NAME = "Summary"


def resolve_publish_interval(window: str, source: Mapping[str, Any]) -> float:
    """Resolve the publish interval for a window from a config entry mapping.

    ``source`` is the merged ``{**entry.data, **entry.options}`` mapping. A
    per-window value wins; otherwise the legacy single
    ``publish_interval_seconds`` is used; otherwise the default. Invalid
    values fall back to the default rather than raising.
    """
    raw = source.get(PUBLISH_INTERVAL_CONF_BY_WINDOW[window])
    if raw is None:
        raw = source.get(
            CONF_PUBLISH_INTERVAL_SECONDS, DEFAULT_PUBLISH_INTERVAL_SECONDS
        )
    try:
        return float(raw)
    except TypeError, ValueError:
        return float(DEFAULT_PUBLISH_INTERVAL_SECONDS)


EPP_PATH_TEMPLATE = (
    "/sys/devices/system/cpu/cpu{cpu}/cpufreq/energy_performance_preference"
)
EPB_PATH_TEMPLATE = "/sys/devices/system/cpu/cpu{cpu}/power/energy_perf_bias"
