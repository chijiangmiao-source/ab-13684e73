"""确定性边界与语义测试。"""

from __future__ import annotations

import random
import time

from app.solver import SolveError, solve


def _ids(n):
    return [f"h{k:02d}" for k in range(n)]


def test_window_closed_boundary_inclusive():
    # t=0 与 t=W 在闭窗内可同组；t=W+1 不可
    times = [0, 5, 6]
    detectors = [0, 1, 1]
    weights = [5, 7, 9]
    got = solve(times, detectors, weights, 5, id_order=_ids(3))
    # (0,1) 可同组（差 5）；(0,2) 差 6 不可；1、2 同探测器
    assert got["best_score"] == 12
    assert (0, 1) in got["pair_count"]
    assert (0, 2) not in got["pair_count"]


def test_no_possible_events_all_noise():
    times = [0, 1, 2, 3]
    detectors = [0, 0, 0, 0]  # 全部同一探测器
    weights = [1, 1, 1, 1]
    got = solve(times, detectors, weights, 10)
    assert got["best_score"] == 0
    assert got["best_events"] == 0
    assert got["total_count"] == 1
    assert got["canonical"] == []
    assert got["pair_count"] == {}


def test_max_confidence_then_min_events():
    # 命中 0,1,2 互相兼容；两两可信度相同，三分组不可能（同一探测器限制）
    times = [0, 1, 2]
    detectors = [0, 1, 0]
    weights = [5, 4, 5]
    # 事件只能是 (0,1) 和 (1,2)；二者可信度 9，各 1 事件
    got = solve(times, detectors, weights, 10, id_order=_ids(3))
    assert got["best_score"] == 9
    assert got["best_events"] == 1
    assert got["total_count"] == 2
    # 规范裁决取标识序列更小者：[h00,h01] < [h01,h02]
    assert got["canonical"] == [(0, 1)]


def test_min_events_prefers_fewer_groups():
    # 四个命中两两兼容（探测器互异）：
    # 方案 A：一个四元事件 = 全部可信度
    # 方案 B：两个二元事件，可信度和相同（构造为等权）
    times = [0, 0, 0, 0]
    detectors = [0, 1, 2, 3]
    weights = [1, 1, 1, 1]
    got = solve(times, detectors, weights, 0, id_order=_ids(4))
    assert got["best_score"] == 4
    assert got["best_events"] == 1  # 四元事件最少
    assert (0, 1, 2, 3) in got["canonical"]


def test_pair_always_optional_never():
    # 0-1-2 链式兼容（探测器 0,1,0），等权；
    # 最优取一个二元事件（可信度 2），另一个噪声。
    times = [0, 1, 2]
    detectors = [0, 1, 0]
    weights = [1, 1, 1]
    got = solve(times, detectors, weights, 2, id_order=_ids(3))
    assert got["total_count"] == 2
    # (0,1) 与 (1,2) 各在恰好一个最优解中 -> 可选
    assert got["pair_count"][(0, 1)] == 1
    assert got["pair_count"][(1, 2)] == 1
    # 0 与 2 同探测器，从不兼容
    assert (0, 2) not in got["pair_count"]
    # 中间命中 1 在两个最优解中都被分组 -> 必然归属（成员层面）
    assert got["member_count"][1] == 2


def test_pair_always_relation():
    times = [0, 1, 1]
    detectors = [0, 1, 2]
    weights = [10, 10, 1]
    # 最优必含 (0,1)（可信度 20），命中 2 噪声
    got = solve(times, detectors, weights, 1, id_order=_ids(3))
    assert got["pair_count"][(0, 1)] == got["total_count"]
    assert got["total_count"] == 1


def test_zero_window_same_timestamp():
    times = [0, 0, 0, 1]
    detectors = [0, 1, 2, 0]
    weights = [3, 4, 5, 100]
    # W=0：前三个同刻可成三元事件（12）；命中 3 高可信但无人同时刻 -> 噪声
    got = solve(times, detectors, weights, 0, id_order=_ids(4))
    assert got["best_score"] == 12
    assert got["best_events"] == 1


def test_arbitrary_precision_count():
    # k 个相互独立的链式三元组（0,1,0 探测器），每组等权有 2 个最优，
    # 组间间隔超过符合窗 -> 总方案数 2^k，验证任意精度大整数。
    k = 26
    window = 2
    times, detectors, weights = [], [], []
    for i in range(k):
        base = 10 * i
        times += [base, base + 1, base + 2]
        detectors += [0, 1, 0]
        weights += [1, 1, 1]
    n = 3 * k
    got = solve(times, detectors, weights, window, id_order=_ids(n))
    assert got["total_count"] == 2 ** k
    assert isinstance(got["total_count"], int)
    assert got["best_score"] == 2 * k
    assert got["best_events"] == k


def test_full_scale_performance_and_density():
    rng = random.Random(2026)
    n = 80
    window = 5
    # 构造满足密度约束的时刻：逐个放置，确保任意窗 ≤10
    times = []
    t = 0
    while len(times) < n:
        window_count = sum(1 for x in times if t - window <= x <= t)
        if window_count < 10:
            times.append(t)
            if rng.random() < 0.5:
                t += 1
        else:
            t += 1
    times.sort()
    detectors = [rng.randrange(8) for _ in range(n)]
    weights = [rng.randint(1, 100) for _ in range(n)]
    start = time.perf_counter()
    got = solve(times, detectors, weights, window, id_order=_ids(n))
    elapsed = time.perf_counter() - start
    assert elapsed < 10.0, f"solve too slow: {elapsed:.2f}s"
    assert got["best_score"] > 0
    # 大整数计数一致性：必然成员计数之和等
    assert isinstance(got["total_count"], int) and got["total_count"] >= 1


def test_dense_window_rejected_via_solver():
    # 11 个命中落在同一闭窗 -> 拒绝
    times = [0] * 11
    detectors = [k % 8 for k in range(11)]
    weights = [1] * 11
    try:
        solve(times, detectors, weights, 0, id_order=[f"h{k}" for k in range(11)])
    except SolveError:
        return
    raise AssertionError("expected SolveError")
