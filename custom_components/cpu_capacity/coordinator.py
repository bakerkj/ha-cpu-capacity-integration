# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
from collections import deque
from contextlib import suppress
from datetime import datetime, timedelta
from typing import TypedDict

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    EPB_PATH_TEMPLATE,
    EPP_PATH_TEMPLATE,
    STALE_DATA_TIMEOUT_MULTIPLIER,
)

WINDOW_SECONDS: dict[str, float] = {
    "1m": 60.0,
    "5m": 300.0,
    "15m": 900.0,
}

# Event-loop lag is sampled fast (on the loop) to catch sub-second stalls.
LAG_SAMPLE_INTERVAL_SECONDS = 0.25

# A lag sample at or above this is counted as a "stall": a serious loop freeze
# (over a second) worth its own count/duration/time sensors, as opposed to the
# routine sub-second drift the lag percentiles already track. Because the probe
# fires late, a GIL-held freeze still shows up here as one large sample once the
# loop resumes, so stalls are captured regardless of cause.
STALL_THRESHOLD_MS = 1000.0


class CpuSnapshot(TypedDict):
    supports_capacity_adjusted: bool
    max_mhz: float | None
    epp: str | None
    epb: int | None
    mhz_1m: float | None
    mhz_5m: float | None
    mhz_15m: float | None
    load_pct_1m: float | None
    load_pct_5m: float | None
    load_pct_15m: float | None
    capacity_adjusted_load_pct_1m: float | None
    capacity_adjusted_load_pct_5m: float | None
    capacity_adjusted_load_pct_15m: float | None


class EventLoopLagData(TypedDict):
    current_ms: float | None
    p95_1m_ms: float | None
    p95_5m_ms: float | None
    p95_15m_ms: float | None
    max_1m_ms: float | None
    max_5m_ms: float | None
    max_15m_ms: float | None
    # Loop-stall stats (samples >= STALL_THRESHOLD_MS): cumulative since start.
    stall_count: int
    last_stall_ms: float | None
    worst_stall_ms: float | None
    last_stall_epoch: float | None  # wall-clock time.time() of the last stall


class CoordinatorSnapshot(TypedDict):
    sample_count: int
    last_sample_epoch: float
    sample_interval_seconds: float
    publish_intervals_by_window: dict[str, float]
    cpus: dict[int, CpuSnapshot]
    event_loop_lag: EventLoopLagData | None


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

    def mean(self) -> float | None:
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
        self, mhz: float, load_pct: float, capacity_adjusted_load_pct: float | None
    ) -> None:
        for win in self.windows["mhz"].values():
            win.add(mhz)
        for win in self.windows["load_pct"].values():
            win.add(load_pct)
        if capacity_adjusted_load_pct is not None:
            for win in self.windows["capacity_adjusted_load_pct"].values():
                win.add(capacity_adjusted_load_pct)

    def mean(self, metric: str, window: str) -> float | None:
        metric_windows = self.windows.get(metric)
        if metric_windows is None:
            return None
        rolling = metric_windows.get(window)
        if rolling is None:
            return None
        return rolling.mean()


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Nearest-rank percentile of an already-sorted, non-empty list."""
    n = len(sorted_vals)
    k = min(n - 1, max(0, math.ceil(pct / 100.0 * n) - 1))
    return sorted_vals[k]


class EventLoopLagMonitor:
    """Measures asyncio event-loop scheduling drift (loop lag).

    Re-arms a probe every ``interval``; the gap between the probe's deadline and
    when the loop actually runs it is the lag — i.e. how long a ready callback
    waited. Runs entirely on the loop, so the sample deque is single-threaded;
    ``compute`` must also be called on the loop.
    """

    def __init__(
        self, hass: HomeAssistant, interval: float = LAG_SAMPLE_INTERVAL_SECONDS
    ) -> None:
        self.hass = hass
        self._interval = max(0.05, float(interval))
        maxlen = max(1, math.ceil(WINDOW_SECONDS["15m"] / self._interval))
        self._samples: deque[float] = deque(maxlen=maxlen)
        self._expected: float | None = None
        self._handle: asyncio.TimerHandle | None = None
        self._running = False
        # Cumulative loop-stall stats (samples >= STALL_THRESHOLD_MS).
        self._stall_count = 0
        self._last_stall_ms: float | None = None
        self._worst_stall_ms: float | None = None
        self._last_stall_epoch: float | None = None

    @callback
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._arm()

    @callback
    def _arm(self) -> None:
        self._expected = self.hass.loop.time() + self._interval
        self._handle = self.hass.loop.call_later(self._interval, self._probe)

    @callback
    def _probe(self) -> None:
        if not self._running:
            return
        now = self.hass.loop.time()
        expected = self._expected if self._expected is not None else now
        lag = max(0.0, now - expected)
        self._samples.append(lag)
        lag_ms = lag * 1000.0
        if lag_ms >= STALL_THRESHOLD_MS:
            self._stall_count += 1
            self._last_stall_ms = lag_ms
            self._last_stall_epoch = time.time()
            self._worst_stall_ms = (
                lag_ms
                if self._worst_stall_ms is None
                else max(self._worst_stall_ms, lag_ms)
            )
        self._arm()

    @callback
    def stop(self) -> None:
        self._running = False
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None

    @callback
    def compute(self) -> EventLoopLagData:
        data = list(self._samples)

        def agg(seconds: float) -> tuple[float | None, float | None]:
            if not data:
                return None, None
            n = min(len(data), max(1, math.ceil(seconds / self._interval)))
            window = sorted(data[-n:])
            return _percentile(window, 95.0) * 1000.0, window[-1] * 1000.0

        p1, m1 = agg(WINDOW_SECONDS["1m"])
        p5, m5 = agg(WINDOW_SECONDS["5m"])
        p15, m15 = agg(WINDOW_SECONDS["15m"])
        return EventLoopLagData(
            current_ms=(data[-1] * 1000.0 if data else None),
            p95_1m_ms=p1,
            p95_5m_ms=p5,
            p95_15m_ms=p15,
            max_1m_ms=m1,
            max_5m_ms=m5,
            max_15m_ms=m15,
            stall_count=self._stall_count,
            last_stall_ms=self._last_stall_ms,
            worst_stall_ms=self._worst_stall_ms,
            last_stall_epoch=self._last_stall_epoch,
        )


def _safe_read_text(path: str) -> str | None:
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
    current_cpu: int | None = None

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
    except (OSError, ValueError) as err:
        logging.getLogger(__name__).debug(
            "Failed to parse /proc/cpuinfo for MHz fallback: %s", err
        )
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
            logging.getLogger(__name__).debug(
                "cpu%s: unable to read current frequency from %s", cpu, freq_file
            )
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
        value: float | None = None
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


def _epp_path(cpu: int) -> str:
    return EPP_PATH_TEMPLATE.format(cpu=cpu)


def _epb_path(cpu: int) -> str:
    return EPB_PATH_TEMPLATE.format(cpu=cpu)


def _read_epp(cpu: int) -> str | None:
    return _safe_read_text(_epp_path(cpu))


def _read_epb(cpu: int) -> int | None:
    text = _safe_read_text(_epb_path(cpu))
    if text is None:
        return None
    if re.fullmatch(r"[-+]?\d+", text):
        return int(text)
    return None


def _supports_epp(cpu: int) -> bool:
    return os.path.exists(_epp_path(cpu))


def _supports_epb(cpu: int) -> bool:
    return os.path.exists(_epb_path(cpu))


class CpuCapacitySampler:
    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        sample_interval_seconds: float,
        publish_intervals_by_window: dict[str, float],
    ) -> None:
        self.hass = hass
        self.logger = logger
        self._sample_interval_seconds = max(0.1, float(sample_interval_seconds))
        # Each window publishes no faster than the sampler produces data.
        self._publish_intervals_by_window: dict[str, float] = {
            window: max(
                self._sample_interval_seconds,
                float(publish_intervals_by_window[window]),
            )
            for window in WINDOW_SECONDS
            if window in publish_intervals_by_window
        }

        self._window_sizes: dict[str, int] = {
            label: max(1, math.ceil(seconds / self._sample_interval_seconds))
            for label, seconds in WINDOW_SECONDS.items()
        }

        self._cpu_ids: list[int] = []
        self._prev_totals: dict[int, tuple[int, int]] = {}
        self._max_mhz_by_cpu: dict[int, float] = {}
        self._supports_capacity_adjusted: dict[int, bool] = {}
        self._supports_epp: dict[int, bool] = {}
        self._supports_epb: dict[int, bool] = {}
        self._averages_by_cpu: dict[int, CpuRollingAverages] = {}

        self._sample_count = 0
        self._last_sample_epoch: float = 0.0

        self._lock = asyncio.Lock()
        self._running = False
        self._unsub_sample: CALLBACK_TYPE | None = None
        self._sample_task: asyncio.Task | None = None
        # Event-loop lag runs on the loop (not the executor sampler above).
        self._lag = EventLoopLagMonitor(hass)

    @property
    def cpu_ids(self) -> list[int]:
        return list(self._cpu_ids)

    @property
    def supports_capacity_adjusted(self) -> dict[int, bool]:
        return dict(self._supports_capacity_adjusted)

    @property
    def supports_epp(self) -> dict[int, bool]:
        return dict(self._supports_epp)

    @property
    def supports_epb(self) -> dict[int, bool]:
        return dict(self._supports_epb)

    @property
    def sample_interval_seconds(self) -> float:
        return self._sample_interval_seconds

    @property
    def publish_intervals_by_window(self) -> dict[str, float]:
        return dict(self._publish_intervals_by_window)

    async def async_start(self) -> None:
        if self._running:
            return

        await self.hass.async_add_executor_job(self._initialize_sync)
        self._running = True
        self._lag.start()

        @callback
        def _schedule_sample(_now: datetime) -> None:
            if not self._running:
                return
            if self._sample_task and not self._sample_task.done():
                return
            self._sample_task = self.hass.async_create_task(self._async_take_sample())

        await self._async_take_sample()

        self._unsub_sample = async_track_time_interval(
            self.hass,
            _schedule_sample,
            timedelta(seconds=self._sample_interval_seconds),
        )

    async def async_stop(self) -> None:
        self._running = False
        self._lag.stop()

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
            except (OSError, ValueError) as err:
                self.logger.warning("CPU sampling failed: %s", err)
            except Exception:
                self.logger.exception("Unexpected error during CPU sampling")

    async def async_get_snapshot(self) -> CoordinatorSnapshot:
        async with self._lock:
            snapshot = await self.hass.async_add_executor_job(self._build_snapshot_sync)
        # Lag is computed here on the loop (its deque is loop-owned), not in the
        # executor snapshot build above.
        snapshot["event_loop_lag"] = self._lag.compute()
        return snapshot

    def _initialize_sync(self) -> None:
        if not os.path.exists("/proc/stat"):
            raise RuntimeError(
                "CPU Capacity integration requires Linux /proc/stat. "
                "This integration only works on Linux systems."
            )
        self._prev_totals = _read_proc_stat_totals()
        self._cpu_ids = sorted(self._prev_totals.keys())
        if not self._cpu_ids:
            raise RuntimeError("No CPUs discovered from /proc/stat")

        self._max_mhz_by_cpu = _read_max_mhz(self._cpu_ids, self.logger)
        self._supports_capacity_adjusted = {
            cpu: self._max_mhz_by_cpu.get(cpu, 0.0) > 0.0 for cpu in self._cpu_ids
        }
        self._supports_epp = {cpu: _supports_epp(cpu) for cpu in self._cpu_ids}
        self._supports_epb = {cpu: _supports_epb(cpu) for cpu in self._cpu_ids}

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
            busy = max(busy, 0)

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

    def _build_snapshot_sync(self) -> CoordinatorSnapshot:
        epp_by_cpu = {cpu: _read_epp(cpu) for cpu in self._cpu_ids}
        epb_by_cpu = {cpu: _read_epb(cpu) for cpu in self._cpu_ids}

        cpu_data: dict[int, CpuSnapshot] = {}
        for cpu in self._cpu_ids:
            averages = self._averages_by_cpu[cpu]
            supports_capacity = self._supports_capacity_adjusted.get(cpu, False)

            cpu_data[cpu] = CpuSnapshot(
                supports_capacity_adjusted=supports_capacity,
                max_mhz=self._max_mhz_by_cpu.get(cpu),
                epp=epp_by_cpu.get(cpu),
                epb=epb_by_cpu.get(cpu),
                mhz_1m=averages.mean("mhz", "1m"),
                mhz_5m=averages.mean("mhz", "5m"),
                mhz_15m=averages.mean("mhz", "15m"),
                load_pct_1m=averages.mean("load_pct", "1m"),
                load_pct_5m=averages.mean("load_pct", "5m"),
                load_pct_15m=averages.mean("load_pct", "15m"),
                capacity_adjusted_load_pct_1m=averages.mean(
                    "capacity_adjusted_load_pct", "1m"
                )
                if supports_capacity
                else None,
                capacity_adjusted_load_pct_5m=averages.mean(
                    "capacity_adjusted_load_pct", "5m"
                )
                if supports_capacity
                else None,
                capacity_adjusted_load_pct_15m=averages.mean(
                    "capacity_adjusted_load_pct", "15m"
                )
                if supports_capacity
                else None,
            )

        return CoordinatorSnapshot(
            sample_count=self._sample_count,
            last_sample_epoch=self._last_sample_epoch,
            sample_interval_seconds=self._sample_interval_seconds,
            publish_intervals_by_window=dict(self._publish_intervals_by_window),
            cpus=cpu_data,
            event_loop_lag=None,  # filled on the loop in async_get_snapshot
        )


class CpuCapacityCoordinator(DataUpdateCoordinator[CoordinatorSnapshot]):
    """Publishes the shared sampler's data at one window's cadence.

    One instance per averaging window (1m/5m/15m); all instances share a
    single :class:`CpuCapacitySampler`, so each refresh only snapshots the
    already-collected rolling averages.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        sampler: CpuCapacitySampler,
        window: str,
        publish_interval_seconds: float,
    ) -> None:
        self.sampler = sampler
        self.window = window
        self._publish_interval_seconds = publish_interval_seconds
        super().__init__(
            hass,
            logger,
            name=f"{DOMAIN}_{window}",
            update_interval=timedelta(seconds=publish_interval_seconds),
        )

    async def _async_update_data(self) -> CoordinatorSnapshot:
        snapshot = await self.sampler.async_get_snapshot()

        if snapshot["sample_count"] <= 0:
            raise UpdateFailed("No samples collected yet")

        if snapshot["last_sample_epoch"] <= 0.0:
            raise UpdateFailed("No sample timestamp available")

        stale_timeout = max(
            5.0, self._publish_interval_seconds * STALE_DATA_TIMEOUT_MULTIPLIER
        )
        age = time.time() - snapshot["last_sample_epoch"]
        if age > stale_timeout:
            raise UpdateFailed(
                f"CPU sample data is stale ({age:.1f}s > {stale_timeout:.1f}s)"
            )

        return snapshot
