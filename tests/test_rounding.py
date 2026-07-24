# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.

from __future__ import annotations

import pytest

from custom_components.cpu_capacity.sensor import (
    _MHZ_NATIVE_STEP,
    _round_native_value,
    _round_summary_value,
)

# ---------------------------------------------------------------------------
# _round_summary_value
# ---------------------------------------------------------------------------


class TestRoundSummaryValue:
    @pytest.mark.parametrize("value", [None, True, False, "balance_performance"])
    def test_non_numeric_and_bool_pass_through(self, value) -> None:
        # bools and non-int/float values are returned untouched
        assert _round_summary_value("load_pct_1m", value) is value

    @pytest.mark.parametrize("key", ["max_mhz", "mhz_1m", "mhz_15m"])
    def test_mhz_rounds_to_nearest_integer(self, key) -> None:
        result = _round_summary_value(key, 2999.7)
        assert result == 3000
        assert isinstance(result, int)

    @pytest.mark.parametrize("key", ["load_pct_1m", "capacity_adjusted_load_pct_5m"])
    def test_load_rounds_to_four_decimals(self, key) -> None:
        assert _round_summary_value(key, 12.3456789) == pytest.approx(12.3457)

    def test_unmatched_numeric_key_returns_value_unchanged(self) -> None:
        # e.g. epb: numeric but not an mhz/load metric -> passthrough as-is
        assert _round_summary_value("epb", 7) == 7


# ---------------------------------------------------------------------------
# _round_native_value
# ---------------------------------------------------------------------------


class TestRoundNativeValue:
    @pytest.mark.parametrize("value", [None, True, False, "balance_performance"])
    def test_non_numeric_and_bool_pass_through(self, value) -> None:
        assert _round_native_value("mhz_1m", value) is value

    @pytest.mark.parametrize("key", ["max_mhz", "mhz_1m", "mhz_15m"])
    def test_mhz_quantized_to_step(self, key) -> None:
        assert _MHZ_NATIVE_STEP == 50
        # 3024 -> 60.48 steps -> 60 -> 3000; 3026 -> 60.52 -> 61 -> 3050
        low = _round_native_value(key, 3024.0)
        high = _round_native_value(key, 3026.0)
        assert low == 3000
        assert high == 3050
        assert isinstance(low, int)
        # every quantized value is a multiple of the step
        assert low % _MHZ_NATIVE_STEP == 0
        assert high % _MHZ_NATIVE_STEP == 0

    @pytest.mark.parametrize("key", ["load_pct_1m", "capacity_adjusted_load_pct_15m"])
    def test_load_rounds_to_one_decimal(self, key) -> None:
        assert _round_native_value(key, 12.36) == pytest.approx(12.4)
        assert _round_native_value(key, 12.34) == pytest.approx(12.3)

    def test_unmatched_numeric_key_returns_value_unchanged(self) -> None:
        assert _round_native_value("epb", 7) == 7
