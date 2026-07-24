# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from custom_components.cpu_capacity.coordinator import (
    CpuCapacitySampler,
    CpuRollingAverages,
)

_LOGGER = logging.getLogger(__name__)
_PUBLISH = {"1m": 60.0, "5m": 300.0, "15m": 900.0}


def _make_sampler(sample_interval_seconds: float) -> CpuCapacitySampler:
    # __init__ only stores hass and computes window sizes; it does not touch
    # hass or /proc, so a MagicMock hass is sufficient.
    return CpuCapacitySampler(
        MagicMock(),
        _LOGGER,
        sample_interval_seconds,
        dict(_PUBLISH),
    )


# ---------------------------------------------------------------------------
# CpuCapacitySampler._window_sizes
# ---------------------------------------------------------------------------


class TestWindowSizes:
    def test_rounds_up_when_interval_does_not_divide_evenly(self) -> None:
        # interval 7s: 60/7 = 8.57 -> ceil 9 (truncation would give 8)
        sizes = _make_sampler(7.0)._window_sizes
        assert sizes == {"1m": 9, "5m": 43, "15m": 129}
        assert all(isinstance(v, int) for v in sizes.values())

    def test_exact_division(self) -> None:
        sizes = _make_sampler(2.0)._window_sizes
        assert sizes == {"1m": 30, "5m": 150, "15m": 450}

    def test_floored_to_at_least_one_sample(self) -> None:
        # interval far larger than any window -> each window still needs >=1
        sizes = _make_sampler(10000.0)._window_sizes
        assert sizes == {"1m": 1, "5m": 1, "15m": 1}


# ---------------------------------------------------------------------------
# CpuCapacitySampler._take_sample_sync  (busy clamp)
# ---------------------------------------------------------------------------


class TestTakeSampleBusyClamp:
    def _prime(self, sampler: CpuCapacitySampler, prev: tuple[int, int]) -> None:
        sampler._cpu_ids = [0]
        sampler._prev_totals = {0: prev}
        sampler._max_mhz_by_cpu = {0: 0.0}  # no capacity-adjusted metric
        sampler._averages_by_cpu = {0: CpuRollingAverages(sampler._window_sizes)}

    def test_negative_busy_is_clamped_to_zero(self) -> None:
        sampler = _make_sampler(1.0)
        # prev total/idle then a sample where idle grows more than total:
        # dt = 110-100 = 10, di = 130-50 = 80, busy = -70 -> clamp 0
        self._prime(sampler, (100, 50))
        with (
            patch(
                "custom_components.cpu_capacity.coordinator._read_proc_stat_totals",
                return_value={0: (110, 130)},
            ),
            patch(
                "custom_components.cpu_capacity.coordinator._read_current_mhz",
                return_value={0: 2000.0},
            ),
        ):
            sampler._take_sample_sync()

        # without the clamp this would be -700.0
        assert sampler._averages_by_cpu[0].mean("load_pct", "1m") == 0.0
        assert sampler._sample_count == 1
        assert sampler._prev_totals[0] == (110, 130)

    def test_positive_busy_is_not_clamped(self) -> None:
        # proves the clamp only floors negatives, it does not zero real load
        sampler = _make_sampler(1.0)
        self._prime(sampler, (100, 50))
        # dt = 200-100 = 100, di = 80-50 = 30, busy = 70 -> 70% load
        with (
            patch(
                "custom_components.cpu_capacity.coordinator._read_proc_stat_totals",
                return_value={0: (200, 80)},
            ),
            patch(
                "custom_components.cpu_capacity.coordinator._read_current_mhz",
                return_value={0: 2000.0},
            ),
        ):
            sampler._take_sample_sync()

        assert sampler._averages_by_cpu[0].mean("load_pct", "1m") == 70.0
