# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.

"""Tests for the event-loop lag monitor's stall tracking."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.cpu_capacity.coordinator import (
    STALL_THRESHOLD_MS,
    EventLoopLagMonitor,
)


def _monitor(interval: float = 0.25) -> tuple[EventLoopLagMonitor, MagicMock]:
    hass = MagicMock()
    mon = EventLoopLagMonitor(hass, interval=interval)
    mon._running = True
    return mon, hass


def _fire_probe(
    mon: EventLoopLagMonitor, hass: MagicMock, *, lag_seconds: float
) -> None:
    """Arm at a known loop time, then fire the probe ``lag_seconds`` late."""
    hass.loop.time.return_value = 100.0
    mon._arm()  # _expected = 100.0 + interval
    hass.loop.time.return_value = 100.0 + mon._interval + lag_seconds
    mon._probe()


class TestEventLoopLagMonitorStalls:
    def test_no_stalls_initially(self) -> None:
        mon, _ = _monitor()
        data = mon.compute()
        assert data["stall_count"] == 0
        assert data["last_stall_ms"] is None
        assert data["worst_stall_ms"] is None
        assert data["last_stall_epoch"] is None

    def test_small_lag_is_not_a_stall(self) -> None:
        mon, hass = _monitor()
        _fire_probe(mon, hass, lag_seconds=0.05)  # 50 ms
        data = mon.compute()
        assert data["stall_count"] == 0
        assert data["last_stall_ms"] is None
        assert data["current_ms"] == pytest.approx(50.0, abs=1.0)

    def test_large_lag_counts_as_a_stall(self) -> None:
        mon, hass = _monitor()
        _fire_probe(mon, hass, lag_seconds=2.0)  # 2000 ms
        data = mon.compute()
        assert data["stall_count"] == 1
        assert data["last_stall_ms"] == pytest.approx(2000.0, abs=1.0)
        assert data["worst_stall_ms"] == pytest.approx(2000.0, abs=1.0)
        assert data["last_stall_epoch"] is not None

    def test_worst_stall_keeps_the_maximum(self) -> None:
        mon, hass = _monitor()
        _fire_probe(mon, hass, lag_seconds=3.0)
        _fire_probe(mon, hass, lag_seconds=1.5)
        data = mon.compute()
        assert data["stall_count"] == 2
        assert data["last_stall_ms"] == pytest.approx(1500.0, abs=1.0)
        assert data["worst_stall_ms"] == pytest.approx(3000.0, abs=1.0)

    def test_lag_at_threshold_counts(self) -> None:
        mon, hass = _monitor()
        _fire_probe(mon, hass, lag_seconds=STALL_THRESHOLD_MS / 1000.0)
        data = mon.compute()
        assert data["stall_count"] == 1
