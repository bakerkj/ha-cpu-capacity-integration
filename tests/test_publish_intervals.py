# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.

from __future__ import annotations

import pytest

from custom_components.cpu_capacity.config_flow import (
    _build_schema,
    _publish_error,
)
from custom_components.cpu_capacity.const import (
    CONF_PUBLISH_INTERVAL_1M_SECONDS,
    CONF_PUBLISH_INTERVAL_5M_SECONDS,
    CONF_PUBLISH_INTERVAL_15M_SECONDS,
    CONF_PUBLISH_INTERVAL_SECONDS,
    CONF_SAMPLE_INTERVAL_SECONDS,
    DEFAULT_PUBLISH_INTERVAL_SECONDS,
    PUBLISH_INTERVAL_CONF_BY_WINDOW,
    resolve_publish_interval,
)
from custom_components.cpu_capacity.sensor import _window_for_metric

# ---------------------------------------------------------------------------
# resolve_publish_interval
# ---------------------------------------------------------------------------


class TestResolvePublishInterval:
    def test_per_window_value_wins(self) -> None:
        source = {
            CONF_PUBLISH_INTERVAL_1M_SECONDS: 10.0,
            CONF_PUBLISH_INTERVAL_SECONDS: 99.0,
        }
        assert resolve_publish_interval("1m", source) == pytest.approx(10.0)

    def test_legacy_single_value_seeds_all_windows(self) -> None:
        source = {CONF_PUBLISH_INTERVAL_SECONDS: 30.0}
        for window in ("1m", "5m", "15m"):
            assert resolve_publish_interval(window, source) == pytest.approx(30.0)

    def test_default_when_nothing_set(self) -> None:
        assert resolve_publish_interval("15m", {}) == pytest.approx(
            DEFAULT_PUBLISH_INTERVAL_SECONDS
        )

    def test_invalid_value_falls_back_to_default(self) -> None:
        source = {CONF_PUBLISH_INTERVAL_5M_SECONDS: "not-a-number"}
        assert resolve_publish_interval("5m", source) == pytest.approx(
            DEFAULT_PUBLISH_INTERVAL_SECONDS
        )

    def test_windows_independent(self) -> None:
        source = {
            CONF_PUBLISH_INTERVAL_1M_SECONDS: 15.0,
            CONF_PUBLISH_INTERVAL_5M_SECONDS: 60.0,
            CONF_PUBLISH_INTERVAL_15M_SECONDS: 300.0,
        }
        assert resolve_publish_interval("1m", source) == pytest.approx(15.0)
        assert resolve_publish_interval("5m", source) == pytest.approx(60.0)
        assert resolve_publish_interval("15m", source) == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# _window_for_metric
# ---------------------------------------------------------------------------


class TestWindowForMetric:
    @pytest.mark.parametrize(
        ("metric_key", "expected"),
        [
            ("mhz_1m", "1m"),
            ("load_pct_5m", "5m"),
            ("capacity_adjusted_load_pct_15m", "15m"),
            # Non-window metrics fall back to the primary window.
            ("max_mhz", "1m"),
            ("epp", "1m"),
            ("epb", "1m"),
        ],
    )
    def test_routing(self, metric_key: str, expected: str) -> None:
        assert _window_for_metric(metric_key) == expected


# ---------------------------------------------------------------------------
# config flow schema / validation
# ---------------------------------------------------------------------------


class TestConfigFlowSchema:
    def test_schema_exposes_sample_plus_three_windows(self) -> None:
        schema = _build_schema(
            0.5, {window: 15.0 for window in PUBLISH_INTERVAL_CONF_BY_WINDOW}
        )
        keys = {str(marker) for marker in schema.schema}
        assert CONF_SAMPLE_INTERVAL_SECONDS in keys
        for conf in PUBLISH_INTERVAL_CONF_BY_WINDOW.values():
            assert conf in keys

    def test_publish_error_when_a_window_faster_than_sample(self) -> None:
        user_input = {
            CONF_SAMPLE_INTERVAL_SECONDS: 2.0,
            CONF_PUBLISH_INTERVAL_1M_SECONDS: 10.0,
            CONF_PUBLISH_INTERVAL_5M_SECONDS: 1.0,  # faster than sample
            CONF_PUBLISH_INTERVAL_15M_SECONDS: 30.0,
        }
        assert _publish_error(user_input) == "publish_too_small"

    def test_publish_error_none_when_all_valid(self) -> None:
        user_input = {
            CONF_SAMPLE_INTERVAL_SECONDS: 0.5,
            CONF_PUBLISH_INTERVAL_1M_SECONDS: 15.0,
            CONF_PUBLISH_INTERVAL_5M_SECONDS: 60.0,
            CONF_PUBLISH_INTERVAL_15M_SECONDS: 300.0,
        }
        assert _publish_error(user_input) is None
