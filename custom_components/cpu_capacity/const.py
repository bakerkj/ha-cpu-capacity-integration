# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.

from homeassistant.const import Platform

DOMAIN = "cpu_capacity"
PLATFORMS: list[Platform] = [Platform.SENSOR]

CONF_SAMPLE_INTERVAL_SECONDS = "sample_interval_seconds"
CONF_PUBLISH_INTERVAL_SECONDS = "publish_interval_seconds"

DEFAULT_SAMPLE_INTERVAL_SECONDS = 0.5
DEFAULT_PUBLISH_INTERVAL_SECONDS = 15.0
DEFAULT_NAME = "CPU Capacity"

UNIQUE_ID = "cpu_capacity_singleton"

EPP_PATH_TEMPLATE = (
    "/sys/devices/system/cpu/cpu{cpu}/cpufreq/energy_performance_preference"
)
EPB_PATH_TEMPLATE = "/sys/devices/system/cpu/cpu{cpu}/power/energy_perf_bias"
