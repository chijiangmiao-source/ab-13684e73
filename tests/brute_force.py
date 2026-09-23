"""Brute-force reference solver used only by the test-suite.

Enumerates every feasible event (pairwise-compatible hit subsets, >=2
members) and every set-packing of those events (each hit used at most
once).  Tracks the Pareto-optimal packings (max grouped confidence, then
min event count) and derives count/canonical/pair truth directly.

Intentionally exponential: tests keep it below ~14 hits.
"""

from itertools import combinations


def brute_force(hits, window):
    n = len(hits)
    by_id = {h.id: h for h in hits}

    def compatible(i, j):
        a, b = hits[i], hits[j]
        return a.detector != b.detector and abs(a.time - b.time) <= window

    compat = [0] * n
    for i in range(n):
        for j in range(i + 1, n):
            if compatible(i, j):
                compat[i] |= 1 << j
                compat[j] |= 1 << i

    # All feasible events as member bitmasks.
    events = []
    for size in range(2, n + 1):
        for comb in combinations(range(n), size):
            mask = 0
            for p in comb:
                mask |= 1 << p
            ok = True
            for a_i in range(len(comb)):
                if (compat[comb[a_i]] & mask) != mask ^ (1 << comb[a_i]):
                    ok = False
                    break
            if ok:
                events.append(mask)

    events_by_hit = [[] for _ in range(n)]
    for mask in events:
        low = (mask & -mask).bit_length() - 1
        events_by_hit[low].append(mask)

    best_weight = [-1]
    best_events = [10**9]
    opt_packings = []

    def weight_of(mask):
        return sum(hits[p].confidence for p in range(n) if (mask >> p) & 1)

    def recurse(free, chosen, w, ev_count):
        if free == (1 << n) - 1:
            if w > best_weight[0] or (
                w == best_weight[0] and ev_count < best_events[0]
            ):
                best_weight[0] = w
                best_events[0] = ev_count
                opt_packings[:] = [frozenset(chosen)]
            elif w == best_weight[0] and ev_count == best_events[0]:
                opt_packings.append(frozenset(chosen))
            return
        # lowest undecided hit
        rem = ((1 << n) - 1) ^ free
        i = (rem & -rem).bit_length() - 1
        # i as noise
        recurse(free | (1 << i), chosen, w, ev_count)
        for mask in events_by_hit[i]:
            if mask & free:
                continue
            recurse(free | mask, chosen + [mask], w + weight_of(mask),
                    ev_count + 1)

    recurse(0, [], 0, 0)
    opt_packings = set(opt_packings)

    # Canonical: sorted list of sorted id tuples, lexicographic minimum.
    def seq(packing):
        tuples = [
            tuple(sorted(hits[p].id for p in range(n) if (m >> p) & 1))
            for m in packing
        ]
        return sorted(tuples)

    canonical = min((seq(p) for p in opt_packings), default=[])

    # Pair truth over co-groupable pairs.
    pair_info = {}
    for i in range(n):
        for j in range(i + 1, n):
            if not (compat[i] >> j) & 1:
                continue
            together = sum(
                1 for p in opt_packings
                if any((m >> i) & 1 and (m >> j) & 1 for m in p)
            )
            if together == 0:
                status = "never"
            elif together == len(opt_packings):
                status = "always"
            else:
                status = "sometimes"
            x, y = hits[i].id, hits[j].id
            if x > y:
                x, y = y, x
            pair_info[(x, y)] = status

    return {
        "weight": best_weight[0],
        "events": best_events[0] if opt_packings else 0,
        "count": len(opt_packings),
        "canonical": [list(t) for t in canonical],
        "pairs": pair_info,
    }
