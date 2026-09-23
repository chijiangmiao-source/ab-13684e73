"""随机小规模用例：DP 结果与暴力枚举逐项一致。"""

from __future__ import annotations

import itertools
import random

import pytest

from app.solver import solve

from .brute import brute_solve


def _normalize(canonical):
    return tuple(sorted(tuple(sorted(g)) for g in canonical))


@pytest.mark.parametrize("seed", range(120))
def test_matches_brute(seed):
    rng = random.Random(seed)
    n = rng.randint(4, 12)
    ndet = rng.randint(2, min(5, n))
    window = rng.randint(0, 6)

    # 生成时刻并保证任意长度 window 的闭窗内至多 10 个命中
    times = sorted(rng.randint(0, 12) for _ in range(n))
    for _ in range(100):
        if all(
            sum(1 for t2 in times if t1 <= t2 <= t1 + window) <= 10
            for t1 in times
        ):
            break
        times = sorted(rng.randint(0, 12) for _ in range(n))
    detectors = [rng.randrange(ndet) for _ in range(n)]
    weights = [rng.randint(1, 8) for _ in range(n)]

    expected = brute_solve(times, detectors, weights, window)
    got = solve(times, detectors, weights, window)

    assert got["best_score"] == expected["best_score"]
    assert got["best_events"] == expected["best_events"]
    assert got["total_count"] == expected["total_count"]
    assert _normalize(got["canonical"]) == _normalize(expected["canonical"])
    assert got["pair_count"] == expected["pair_count"]
    assert got["member_count"] == expected["member_count"]


def test_sorted_ids_break_ties_by_time_then_id():
    # 同一时刻多命中时按标识排序，规范事件内成员标识升序
    raw = [
        ("z", 0, 0, 1),
        ("a", 0, 1, 1),
        ("m", 0, 2, 1),
    ]
    raw.sort(key=lambda r: (r[1], r[0]))
    ids = [r[0] for r in raw]
    times = [r[1] for r in raw]
    detectors = [r[2] for r in raw]
    weights = [r[3] for r in raw]
    got = solve(times, detectors, weights, 0, id_order=ids)
    assert len(got["canonical"]) == 1
    assert tuple(ids[j] for j in got["canonical"][0]) == ("a", "m", "z")


def test_window_too_dense_rejected():
    from app.solver import SolveError

    times = list(range(11))
    detectors = [k % 8 for k in range(11)]
    weights = [1] * 11
    with pytest.raises(SolveError):
        solve(times, detectors, weights, 100)
