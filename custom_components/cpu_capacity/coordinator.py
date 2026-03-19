# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections import deque
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any, Optional

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

WINDOW_SECONDS: dict[str, float] = {
    "1m": 60.0,
    "5m": 300.0,
    "15m": 900.0,
}


class RollingWindow:
    def __init__(self, max_samples: int) -> None:
        self.max_samples = max(1, int(max_samples))
        self._buf: deque[float] = deque()
        self._sum: float = 0.0

    def add(self, value: float) -> None:
        self._buf.append(value)
        self._sum += value
        if len(self._buf) > self.max_samples:
            self._sum -= self._buf.popleft()

    def mean(self) -> Optional[float]:
        if not self._buf:
            return None
        return self._sum / float(len(self._buf))


class CpuRollingAverages:
    def __init__(self, window_sizes: dict[str, int]) -> None:
        self.windows: dict[str, dict[str, RollingWindow]] = {
            "mhz": {k: RollingWindow(v) for k, v in window_sizes.items()},
            "load_pct": {k: RollingWindow(v) for k, v in window_sizes.items()},
            "capacity_adjusted_load_pct": {
                k: RollingWindow(v) for k, v in window_sizes.items()
            },
        }

    def add_sample(
        self, mhz: float, load_pct: float, capacity_adjusted_load_pct: Optional[float]
    ) -> None:
        for win in self.windows["mhz"].values():
            win.add(mhz)
        for win in self.windows["load_pct"].values():
            win.add(load_pct)
        if capacity_adjusted_load_pct is not None:
            for win in self.windows["capacity_adjusted_load_pct"].values():
                win.add(capacity_adjusted_load_pct)

    def mean(self, metric: str, window: str) -> Optional[float]:
        metric_windows = self.windows.get(metric)
        if metric_windows is None:
            return None
        rolling = metric_windows.get(window)
        if rolling is None:
            return None
        return rolling.mean()


def _safe_read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = f.read().strip()
    except OSError:
        return None
    return value if value else None


def _read_proc_stat_totals() -> dict[int, tuple[int, int]]:
    totals: dict[int, tuple[int, int]] = {}
    cpu_re = re.compile(r"^cpu(\d+)\s+(.+)$")

    with open("/proc/stat", "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            match = cpu_re.match(line)
            if not match:
                continue

            cpu = int(match.group(1))
            parts = match.group(2).split()
            if len(parts) < 5:
                continue

            values: list[int] = []
            valid = True
            for token in parts:
                try:
                    values.append(int(token))
                except ValueError:
                    valid = False
                    break
            if not valid or len(values) < 5:
                continue

            idle = values[3] + values[4]
            total = sum(values)
            totals[cpu] = (total, idle)

    if not totals:
        raise RuntimeError("No per-CPU entries found in /proc/stat")

    return totals


def _parse_proc_cpuinfo_mhz_map() -> dict[int, float]:
    out: dict[int, float] = {}
    current_cpu: Optional[int] = None

    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue

                if line.startswith("processor"):
                    _, value = line.split(":", 1)
                    current_cpu = int(value.strip())
                    continue

                if line.lower().startswith("cpu mhz") and current_cpu is not None:
                    _, value = line.split(":", 1)
                    out[current_cpu] = float(value.strip())
    except Exception:
        return {}

    return out


def _read_current_mhz(cpu_ids: list[int]) -> dict[int, float]:
    out: dict[int, float] = {}
    missing: list[int] = []

    for cpu in cpu_ids:
        freq_file = f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_cur_freq"
        text = _safe_read_text(freq_file)
        if text and text.isdigit():
            out[cpu] = float(int(text)) / 1000.0
        else:
            missing.append(cpu)

    if missing:
        fallback = _parse_proc_cpuinfo_mhz_map()
        for cpu in missing:
            mhz = fallback.get(cpu)
            if mhz is not None:
                out[cpu] = mhz

    return out


def _read_max_mhz(cpu_ids: list[int], logger: logging.Logger) -> dict[int, float]:
    out: dict[int, float] = {}
    missing: list[int] = []

    for cpu in cpu_ids:
        candidates = [
            f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/cpuinfo_max_freq",
            f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_max_freq",
        ]
        value: Optional[float] = None
        for path in candidates:
            text = _safe_read_text(path)
            if text and text.isdigit():
                value = float(int(text)) / 1000.0
                break

        if value is None:
            missing.append(cpu)
        else:
            out[cpu] = value

    if missing:
        fallback = _parse_proc_cpuinfo_mhz_map()
        for cpu in missing:
            mhz = fallback.get(cpu)
            if mhz is None:
                continue
            out[cpu] = mhz
            logger.warning(
                "cpu%s: unable to read max MHz from cpufreq; "
                "using current MHz estimate %.1f",
                cpu,
                mhz,
            )

    return out


def _read_epp(cpu: int) -> Optional[str]:
    return _safe_read_text(
        f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/energy_performance_preference"
    )


def _read_epb(cpu: int) -> Optional[int]:
    text = _safe_read_text(f"/sys/devices/system/cpu/cpu{cpu}/power/energy_perf_bias")
    if text is None:
        return None
    if re.fullmatch(r"[-+]?\d+", text):
        return int(text)
    return None


class CpuCapacitySampler:
    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        sample_interval_seconds: float,
        publish_interval_seconds: float,
    ) -> None:
        self.hass = hass
        self.logger = logger
        self._sample_interval_seconds = max(0.1, float(sample_interval_seconds))
        self._publish_interval_seconds = max(
            self._sample_interval_seconds,
            float(publish_interval_seconds),
        )

        self._window_sizes: dict[str, int] = {
            label: max(1, int(math.ceil(seconds / self._sample_interval_seconds)))
            for label, seconds in WINDOW_SECONDS.items()
        }

        self._cpu_ids: list[int] = []
        self._prev_totals: dict[int, tuple[int, int]] = {}
        self._max_mhz_by_cpu: dict[int, float] = {}
        self._supports_capacity_adjusted: dict[int, bool] = {}
        self._averages_by_cpu: dict[int, CpuRollingAverages] = {}

        self._sample_count = 0
        self._last_sample_epoch: float = 0.0

        self._lock = asyncio.Lock()
        self._running = False
        self._unsub_sample: CALLBACK_TYPE | None = None
        self._sample_task: asyncio.Task | None = None

    @property
    def cpu_ids(self) -> list[int]:
        return list(self._cpu_ids)

    @property
    def supports_capacity_adjusted(self) -> dict[int, bool]:
        return dict(self._supports_capacity_adjusted)

    @property
    def sample_interval_seconds(self) -> float:
        return self._sample_interval_seconds

    @property
    def publish_interval_seconds(self) -> float:
        return self._publish_interval_seconds

    async def async_start(self) -> None:
        if self._running:
            return

        await self.hass.async_add_executor_job(self._initialize_sync)
        self._running = True

        @callback
        def _schedule_sample(_now: datetime) -> None:
            if not self._running:
                return
            if self._sample_task and not self._sample_task.done():
                return
            self._sample_task = self.hass.async_create_task(self._async_take_sample())

        self._unsub_sample = async_track_time_interval(
            self.hass,
            _schedule_sample,
            timedelta(seconds=self._sample_interval_seconds),
        )

        self._sample_task = self.hass.async_create_task(self._async_take_sample())

    async def async_stop(self) -> None:
        self._running = False

        if self._unsub_sample is not None:
            self._unsub_sample()
            self._unsub_sample = None

        if self._sample_task is not None and not self._sample_task.done():
            self._sample_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._sample_task
        self._sample_task = None

    async def _async_take_sample(self) -> None:
        async with self._lock:
            try:
                await self.hass.async_add_executor_job(self._take_sample_sync)
            except Exception as err:  # noqa: BLE001
                self.logger.warning("CPU sampling failed: %s", err)

    async def async_get_snapshot(self) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(self._build_snapshot_sync)

    def _initialize_sync(self) -> None:
        self._prev_totals = _read_proc_stat_totals()
        self._cpu_ids = sorted(self._prev_totals.keys())
        if not self._cpu_ids:
            raise RuntimeError("No CPUs discovered from /proc/stat")

        self._max_mhz_by_cpu = _read_max_mhz(self._cpu_ids, self.logger)
        self._supports_capacity_adjusted = {
            cpu: self._max_mhz_by_cpu.get(cpu, 0.0) > 0.0 for cpu in self._cpu_ids
        }

        self._averages_by_cpu = {
            cpu: CpuRollingAverages(self._window_sizes) for cpu in self._cpu_ids
        }

        unsupported = [
            cpu for cpu in self._cpu_ids if not self._supports_capacity_adjusted[cpu]
        ]
        if unsupported:
            self.logger.warning(
                "Capacity-adjusted metrics disabled for CPUs without max MHz: %s",
                ", ".join(str(cpu) for cpu in unsupported),
            )

    def _take_sample_sync(self) -> None:
        if not self._cpu_ids:
            return

        current_totals = _read_proc_stat_totals()
        current_mhz_by_cpu = _read_current_mhz(self._cpu_ids)

        for cpu in self._cpu_ids:
            prev_total, prev_idle = self._prev_totals.get(cpu, (0, 0))
            cur_total, cur_idle = current_totals.get(cpu, (prev_total, prev_idle))

            dt = cur_total - prev_total
            di = cur_idle - prev_idle
            busy = dt - di
            if busy < 0:
                busy = 0

            load_pct = (float(busy) * 100.0 / float(dt)) if dt > 0 else 0.0
            mhz = current_mhz_by_cpu.get(cpu, 0.0)
            max_mhz = self._max_mhz_by_cpu.get(cpu, 0.0)

            capacity_adjusted = None
            if max_mhz > 0.0:
                capacity_adjusted = load_pct * (mhz / max_mhz)

            self._averages_by_cpu[cpu].add_sample(mhz, load_pct, capacity_adjusted)
            self._prev_totals[cpu] = (cur_total, cur_idle)

        self._sample_count += 1
        self._last_sample_epoch = time.time()

    def _build_snapshot_sync(self) -> dict[str, Any]:
        epp_by_cpu = {cpu: _read_epp(cpu) for cpu in self._cpu_ids}
        epb_by_cpu = {cpu: _read_epb(cpu) for cpu in self._cpu_ids}

        cpu_data: dict[int, dict[str, Any]] = {}
        for cpu in self._cpu_ids:
            averages = self._averages_by_cpu[cpu]
            supports_capacity = self._supports_capacity_adjusted.get(cpu, False)

            row: dict[str, Any] = {
                "supports_capacity_adjusted": supports_capacity,
                "max_mhz": self._max_mhz_by_cpu.get(cpu),
                "epp": epp_by_cpu.get(cpu),
                "epb": epb_by_cpu.get(cpu),
            }

            for window in ("1m", "5m", "15m"):
                row[f"mhz_{window}"] = averages.mean("mhz", window)
                row[f"load_pct_{window}"] = averages.mean("load_pct", window)
                if supports_capacity:
                    row[f"capacity_adjusted_load_pct_{window}"] = averages.mean(
                        "capacity_adjusted_load_pct",
                        window,
                    )

            cpu_data[cpu] = row

        return {
            "sample_count": self._sample_count,
            "last_sample_epoch": self._last_sample_epoch,
            "sample_interval_seconds": self._sample_interval_seconds,
            "publish_interval_seconds": self._publish_interval_seconds,
            "cpus": cpu_data,
        }


class CpuCapacityCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        sampler: CpuCapacitySampler,
    ) -> None:
        self.sampler = sampler
        super().__init__(
            hass,
            logger,
            name=DOMAIN,
            update_interval=timedelta(seconds=sampler.publish_interval_seconds),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        snapshot = await self.sampler.async_get_snapshot()

        sample_count = int(snapshot.get("sample_count", 0))
        if sample_count <= 0:
            raise UpdateFailed("No samples collected yet")

        last_sample_epoch = float(snapshot.get("last_sample_epoch", 0.0) or 0.0)
        if last_sample_epoch <= 0.0:
            raise UpdateFailed("No sample timestamp available")

        stale_timeout = max(5.0, self.sampler.publish_interval_seconds * 3.0)
        age = time.time() - last_sample_epoch
        if age > stale_timeout:
            raise UpdateFailed(
                (f"CPU sample data is stale ({age:.1f}s > {stale_timeout:.1f}s)")
            )

        return snapshot
