# Copyright (c) 2026 Kenneth Baker <bakerkj@umich.edu>
# All rights reserved.

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from custom_components.cpu_capacity.coordinator import (
    CpuRollingAverages,
    RollingWindow,
    _parse_proc_cpuinfo_mhz_map,
    _read_proc_stat_totals,
    _safe_read_text,
)


# ---------------------------------------------------------------------------
# RollingWindow
# ---------------------------------------------------------------------------


class TestRollingWindow:
    def test_empty_mean_returns_none(self) -> None:
        w = RollingWindow(max_samples=5)
        assert w.mean() is None

    def test_single_sample(self) -> None:
        w = RollingWindow(max_samples=5)
        w.add(42.0)
        assert w.mean() == pytest.approx(42.0)

    def test_mean_of_multiple_samples(self) -> None:
        w = RollingWindow(max_samples=10)
        for v in [10.0, 20.0, 30.0]:
            w.add(v)
        assert w.mean() == pytest.approx(20.0)

    def test_oldest_sample_evicted_when_full(self) -> None:
        w = RollingWindow(max_samples=3)
        for v in [100.0, 200.0, 300.0]:
            w.add(v)
        # mean = 200; now add 400, evicting 100
        w.add(400.0)
        assert w.mean() == pytest.approx(300.0)

    def test_max_samples_clamped_to_one(self) -> None:
        w = RollingWindow(max_samples=0)
        assert w.max_samples == 1
        w.add(7.0)
        assert w.mean() == pytest.approx(7.0)

    def test_running_sum_stays_accurate_after_many_evictions(self) -> None:
        w = RollingWindow(max_samples=2)
        for v in range(100):
            w.add(float(v))
        # last two values: 98, 99 → mean = 98.5
        assert w.mean() == pytest.approx(98.5)


# ---------------------------------------------------------------------------
# CpuRollingAverages
# ---------------------------------------------------------------------------


class TestCpuRollingAverages:
    def _averages(self) -> CpuRollingAverages:
        return CpuRollingAverages(window_sizes={"1m": 10, "5m": 50, "15m": 150})

    def test_mean_returns_none_before_any_samples(self) -> None:
        avgs = self._averages()
        assert avgs.mean("mhz", "1m") is None
        assert avgs.mean("load_pct", "1m") is None
        assert avgs.mean("capacity_adjusted_load_pct", "1m") is None

    def test_mean_after_sample_without_capacity(self) -> None:
        avgs = self._averages()
        avgs.add_sample(mhz=3000.0, load_pct=50.0, capacity_adjusted_load_pct=None)
        assert avgs.mean("mhz", "1m") == pytest.approx(3000.0)
        assert avgs.mean("load_pct", "1m") == pytest.approx(50.0)
        # capacity window had no samples added
        assert avgs.mean("capacity_adjusted_load_pct", "1m") is None

    def test_mean_after_sample_with_capacity(self) -> None:
        avgs = self._averages()
        avgs.add_sample(mhz=2000.0, load_pct=40.0, capacity_adjusted_load_pct=25.0)
        assert avgs.mean("capacity_adjusted_load_pct", "1m") == pytest.approx(25.0)

    def test_unknown_metric_returns_none(self) -> None:
        avgs = self._averages()
        assert avgs.mean("nonexistent", "1m") is None

    def test_unknown_window_returns_none(self) -> None:
        avgs = self._averages()
        avgs.add_sample(mhz=1000.0, load_pct=10.0, capacity_adjusted_load_pct=None)
        assert avgs.mean("mhz", "99m") is None


# ---------------------------------------------------------------------------
# _safe_read_text
# ---------------------------------------------------------------------------


class TestSafeReadText:
    def test_returns_content(self, tmp_path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello\n")
        assert _safe_read_text(str(f)) == "hello"

    def test_returns_none_for_missing_file(self) -> None:
        assert _safe_read_text("/nonexistent/path/file.txt") is None

    def test_returns_none_for_empty_file(self, tmp_path) -> None:
        f = tmp_path / "empty.txt"
        f.write_text("")
        assert _safe_read_text(str(f)) is None


# ---------------------------------------------------------------------------
# _read_proc_stat_totals
# ---------------------------------------------------------------------------

PROC_STAT_SAMPLE = """\
cpu  123456 0 654321 9876543 0 0 0 0 0 0
cpu0 10000 0 5000 90000 0 0 0 0 0 0
cpu1 20000 0 8000 70000 100 0 0 0 0 0
cpu2 invalid_line_skipped
"""


class TestReadProcStatTotals:
    def test_parses_per_cpu_entries(self) -> None:
        with patch("builtins.open", return_value=StringIO(PROC_STAT_SAMPLE)):
            totals = _read_proc_stat_totals()

        assert 0 in totals
        assert 1 in totals
        # aggregate "cpu" line should be ignored (no integer after "cpu")
        assert len(totals) == 2

    def test_idle_includes_iowait(self) -> None:
        # cpu0: values[3]=90000 (idle), values[4]=0 (iowait) → idle=90000
        with patch("builtins.open", return_value=StringIO(PROC_STAT_SAMPLE)):
            totals = _read_proc_stat_totals()
        _total0, idle0 = totals[0]
        assert idle0 == 90000

    def test_total_is_sum_of_all_fields(self) -> None:
        with patch("builtins.open", return_value=StringIO(PROC_STAT_SAMPLE)):
            totals = _read_proc_stat_totals()
        total0, _ = totals[0]
        assert total0 == 10000 + 0 + 5000 + 90000 + 0 + 0 + 0 + 0 + 0 + 0

    def test_raises_if_no_cpu_entries(self) -> None:
        empty = "no cpu lines here\n"
        with patch("builtins.open", return_value=StringIO(empty)):
            with pytest.raises(RuntimeError, match="No per-CPU entries found"):
                _read_proc_stat_totals()


# ---------------------------------------------------------------------------
# _parse_proc_cpuinfo_mhz_map
# ---------------------------------------------------------------------------

PROC_CPUINFO_SAMPLE = """\
processor\t: 0
cpu MHz\t\t: 3600.000

processor\t: 1
cpu MHz\t\t: 2400.500

processor\t: 2
"""


class TestParseProcCpuinfoMhzMap:
    def test_parses_mhz_for_each_cpu(self) -> None:
        with patch("builtins.open", return_value=StringIO(PROC_CPUINFO_SAMPLE)):
            result = _parse_proc_cpuinfo_mhz_map()
        assert result[0] == pytest.approx(3600.0)
        assert result[1] == pytest.approx(2400.5)

    def test_cpu_without_mhz_line_not_in_result(self) -> None:
        with patch("builtins.open", return_value=StringIO(PROC_CPUINFO_SAMPLE)):
            result = _parse_proc_cpuinfo_mhz_map()
        assert 2 not in result

    def test_returns_empty_dict_on_oserror(self) -> None:
        with patch("builtins.open", side_effect=OSError("no such file")):
            result = _parse_proc_cpuinfo_mhz_map()
        assert result == {}

    def test_logs_debug_on_error(self) -> None:
        logger = MagicMock()
        with patch("builtins.open", side_effect=OSError("boom")):
            with patch(
                "custom_components.cpu_capacity.coordinator.logging.getLogger",
                return_value=logger,
            ):
                _parse_proc_cpuinfo_mhz_map()
        logger.debug.assert_called_once()
        assert "MHz fallback" in logger.debug.call_args[0][0]
