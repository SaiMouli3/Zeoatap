import math

import pytest

from zeoatap_eval.goals import min_n_exact, required_hours
from zeoatap_eval.stats import (clopper_pearson, percentile, poisson_rate_interval, rule_of_three,
                                rule_of_three_n)


def test_zero_misses_in_30_exact_upper_bound_is_0_116_and_rule_of_three_is_0_1():
    iv = clopper_pearson(0, 30)  # 0 misses in 30 positives
    assert iv.lower == 0.0
    assert iv.upper == pytest.approx(1 - 0.025 ** (1 / 30), abs=1e-12)
    assert iv.upper == pytest.approx(0.1157, abs=5e-5)
    assert rule_of_three(30) == pytest.approx(0.1)
    # The rule of three approximates the ONE-sided 95% bound, not the two-sided one.
    assert clopper_pearson(0, 30, sided="upper").upper == pytest.approx(1 - 0.05 ** (1 / 30))
    assert clopper_pearson(0, 30, sided="upper").upper == pytest.approx(0.0950, abs=5e-5)


def test_all_detected_lower_bound_mirrors_zero_misses():
    assert clopper_pearson(30, 30).lower == pytest.approx(0.025 ** (1 / 30))
    assert clopper_pearson(30, 30).upper == 1.0


def test_symmetric_interval_known_values():
    iv = clopper_pearson(5, 10)
    assert iv.lower == pytest.approx(0.18709, abs=1e-4)
    assert iv.upper == pytest.approx(0.81291, abs=1e-4)


def test_empty_and_invalid():
    assert clopper_pearson(0, 0).as_dict() == {"lower": 0.0, "upper": 1.0}
    with pytest.raises(ValueError):
        clopper_pearson(4, 3)
    assert rule_of_three(0) is None


def test_rule_of_three_sample_sizes_match_safety_validation():
    assert rule_of_three_n(0.10) == 30  # "<10% miss rate needs >= 30 positives with 0 misses"
    assert rule_of_three_n(0.05) == 60
    assert rule_of_three_n(0.01) == 300


def test_exact_minimum_n_for_a_recall_bound():
    assert min_n_exact(0.90, 0.95, "two") == 36   # 0.025**(1/36) = 0.9026
    assert min_n_exact(0.90, 0.95, "one") == 29   # 0.05**(1/29) = 0.9018
    assert 0.025 ** (1 / 35) < 0.90 <= 0.025 ** (1 / 36)


def test_poisson_rate_bounds_and_required_camera_hours():
    # 0 false alerts in 300 camera-hours bounds the rate near 0.01 (SAFETY_VALIDATION §4.2).
    up = poisson_rate_interval(0, 300, sided="upper").upper
    assert up == pytest.approx(-math.log(0.05) / 300)
    assert up == pytest.approx(0.00999, abs=1e-4)
    assert required_hours(0.01, 0.95, "one") == pytest.approx(299.57, abs=0.01)
    assert required_hours(0.01, 0.95, "two") == pytest.approx(368.89, abs=0.01)
    iv = poisson_rate_interval(4, 100)
    assert iv.lower == pytest.approx(1.0899 / 100, abs=1e-4)
    assert iv.upper == pytest.approx(10.2416 / 100, abs=1e-4)
    assert poisson_rate_interval(1, 0) is None


def test_percentile():
    assert percentile([], 95) is None
    assert percentile([1.0], 95) == 1.0
    assert percentile(list(map(float, range(1, 21))), 95) == pytest.approx(19.05)
