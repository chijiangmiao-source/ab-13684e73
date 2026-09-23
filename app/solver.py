"""Coincidence grouping solver.

Given detector hits (id, detector, integer time, positive integer confidence)
and a non-negative coincidence window, partition the hits into feasible
physical events (>=2 hits, at most one hit per detector, time span <= window)
plus noise.  Optimize lexicographically:

    1. maximize the summed confidence of grouped hits;
    2. minimize the number of events.

Besides the optimum we return:

* the number of optimal groupings (arbitrary precision integer);
* the canonical optimal grouping, where event lists are compared as
  sequences of member-id tuples (tuples sorted by id), the sequence itself
  sorted, and the lexicographically smallest sequence wins;
* for every pair of hits that can co-occur in a feasible event, whether
  they are always / sometimes / never together in an optimal grouping.

The solver never enumerates complete groupings.  It is a sweep dynamic
program: events are started at their earliest hit and the only state is the
set of at most the next ten hits already claimed by an earlier event.
The input guarantee (at most ten hits in any closed window of window length)
keeps that state ten bits wide: a hit can only coincide with at most nine
later hits.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

WINDOW_BITS = 10
STATE_MASK = (1 << WINDOW_BITS) - 1

ALWAYS = "always"
SOMETIMES = "sometimes"
NEVER = "never"


@dataclass(frozen=True)
class Hit:
    id: str
    detector: str
    time: int
    confidence: int


@dataclass
class Option:
    """A feasible event started at some sweep position."""

    local_mask: int       # 10-bit mask, bit 0 is the starter, bits k -> i+k
    members: int          # global n-bit member mask
    reward: int           # summed confidence
    pair_bits: int        # bitset over pair indices contained in the event


@dataclass
class _Rec:
    weight: int
    events: int
    ways: int = 0
    mand: int = 0         # pairs present in every prefix reaching the state
    union: int = 0        # pairs present in some prefix reaching the state


def _compatible(a: Hit, b: Hit, window: int) -> bool:
    """Whether two hits may belong to the same event."""
    if a.detector == b.detector:
        return False
    return abs(a.time - b.time) <= window


def _build_options(
    hits: List[Hit], window: int
) -> Tuple[List[List[Option]], List[Tuple[int, int]], List[int]]:
    """Precompute, for every position, every feasible event starting there.

    Also assigns a bit index to every co-groupable pair of hits.
    """
    n = len(hits)

    compat_global = [0] * n
    for i in range(n):
        for j in range(i + 1, n):
            if _compatible(hits[i], hits[j], window):
                compat_global[i] |= 1 << j
                compat_global[j] |= 1 << i

    pair_index: Dict[Tuple[int, int], int] = {}
    pair_list: List[Tuple[int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            if (compat_global[i] >> j) & 1:
                pair_index[(i, j)] = len(pair_list)
                pair_list.append((i, j))

    options: List[List[Option]] = []
    for i in range(n):
        # Future hits within the window occupy at most bits 1..9.
        base = 0
        for k in range(1, WINDOW_BITS):
            j = i + k
            if j < n and (compat_global[i] >> j) & 1:
                base |= 1 << k

        opts: List[Option] = []
        sub = base
        while True:
            if sub:
                member_positions = [i]
                b = sub
                while b:
                    low = b & -b
                    k = low.bit_length() - 1
                    member_positions.append(i + k)
                    b ^= low
                # Pairwise compatibility is equivalent to feasibility:
                # sorted times make the largest time gap the span, and
                # pairwise detector inequality means distinct detectors.
                clique = True
                for a in range(len(member_positions)):
                    pa = member_positions[a]
                    for b_idx in range(a + 1, len(member_positions)):
                        pb = member_positions[b_idx]
                        if not ((compat_global[pa] >> pb) & 1):
                            clique = False
                            break
                    if not clique:
                        break
                if clique:
                    members = 0
                    reward = 0
                    pb = 0
                    for a in range(len(member_positions)):
                        pa = member_positions[a]
                        members |= 1 << pa
                        reward += hits[pa].confidence
                        for b_idx in range(a + 1, len(member_positions)):
                            pb |= 1 << pair_index[(pa, member_positions[b_idx])]
                    opts.append(Option(sub | 1, members, reward, pb))
            if sub == 0:
                break
            sub = (sub - 1) & base
        options.append(opts)

    return options, pair_list, compat_global


def solve(hits_input: List[Hit], window: int) -> dict:
    # Sweep order: time first, then id (ids are unique, so the order is
    # total; the id tie-break only affects presentation-free internals).
    hits = sorted(hits_input, key=lambda h: (h.time, h.id))
    n = len(hits)
    options, pair_list, _ = _build_options(hits, window)

    # ------------------------------------------------------------------
    # Forward aggregation DP with locked mask 0: optimum weight/event
    # count, number of optimal solutions and the pair intersection/union.
    # ------------------------------------------------------------------
    def aggregate() -> _Rec:
        layers: List[Dict[int, _Rec]] = [{0: _Rec(0, 0, 1, 0, 0)}]
        for i in range(n):
            opts = options[i]
            nxt: Dict[int, _Rec] = {}
            for state, src in layers[i].items():
                if state & 1:
                    # Hit already claimed by an earlier event.
                    edges = ((state >> 1, 0, 0, 0),)
                else:
                    edges = [(state >> 1, 0, 0, 0)]  # noise
                    for o in opts:
                        if (o.local_mask & state) == 0:
                            edges.append(
                                (((state | o.local_mask) >> 1) & STATE_MASK,
                                 o.reward, 1, o.pair_bits)
                            )
                for new_state, reward, ev, pbits in edges:
                    w = src.weight + reward
                    e = src.events + ev
                    em = src.mand | pbits
                    eu = src.union | pbits
                    cur = nxt.get(new_state)
                    if cur is None or w > cur.weight or (w == cur.weight
                                                          and e < cur.events):
                        nxt[new_state] = _Rec(w, e, src.ways, em, eu)
                    elif w == cur.weight and e == cur.events:
                        cur.ways += src.ways
                        cur.mand &= em
                        cur.union |= eu
            layers.append(nxt)
        return layers[n][0]

    best = aggregate()

    # ------------------------------------------------------------------
    # Value-only forward/backward DP used for canonical arbitration.
    # Locked positions are hits already fixed by the greedy arbitration.
    # ------------------------------------------------------------------
    def value_dp(locked: int):
        live_opts = [
            [o for o in options[i] if not (o.members & locked)]
            for i in range(n)
        ]
        forward: List[Dict[int, Tuple[int, int]]] = [{0: (0, 0)}]
        for i in range(n):
            locked_here = (locked >> i) & 1
            nxt: Dict[int, Tuple[int, int]] = {}
            for state, (fw, fe) in forward[i].items():
                if locked_here or (state & 1):
                    candidates = ((state >> 1, 0, 0),)
                else:
                    candidates = [(state >> 1, 0, 0)]
                    for o in live_opts[i]:
                        if (o.local_mask & state) == 0:
                            candidates.append(
                                (((state | o.local_mask) >> 1) & STATE_MASK,
                                 o.reward, 1)
                            )
                for new_state, reward, ev in candidates:
                    w = fw + reward
                    e = fe + ev
                    cur = nxt.get(new_state)
                    if cur is None or w > cur[0] or (w == cur[0] and e < cur[1]):
                        nxt[new_state] = (w, e)
            forward.append(nxt)

        backward: List[Dict[int, Tuple[int, int]]] = [
            {} for _ in range(n + 1)
        ]
        backward[n] = {0: (0, 0)}
        for i in range(n - 1, -1, -1):
            locked_here = (locked >> i) & 1
            layer: Dict[int, Tuple[int, int]] = {}
            for state in forward[i]:
                if locked_here or (state & 1):
                    candidates = ((state >> 1, 0, 0),)
                else:
                    candidates = [(state >> 1, 0, 0)]
                    for o in live_opts[i]:
                        if (o.local_mask & state) == 0:
                            candidates.append(
                                (((state | o.local_mask) >> 1) & STATE_MASK,
                                 o.reward, 1)
                            )
                best_val: Optional[Tuple[int, int]] = None
                for new_state, reward, ev in candidates:
                    tail = backward[i + 1].get(new_state)
                    if tail is None:
                        continue
                    val = (reward + tail[0], ev + tail[1])
                    if best_val is None or val[0] > best_val[0] or (
                        val[0] == best_val[0] and val[1] < best_val[1]
                    ):
                        best_val = val
                layer[state] = best_val  # type: ignore[assignment]
            backward[i] = layer
        return forward, backward, live_opts

    # Canonical arbitration: walk hits in ascending id order.  The first
    # event tuple in the canonical sequence is the event containing the
    # smallest-id hit that gets grouped; among its feasible optimal
    # memberships choose the lexicographically smallest id tuple.
    def canonical_groups() -> List[List[str]]:
        locked = 0
        id_order = sorted(range(n), key=lambda p: hits[p].id)
        result: List[List[str]] = []
        for p in id_order:
            if (locked >> p) & 1:
                continue
            forward, backward, live_opts = value_dp(locked)
            total_w, total_e = forward[n][0]

            best_tuple: Optional[Tuple[int, ...]] = None
            best_members = 0
            # An event containing p is chosen as an edge at the layer of
            # its earliest-time member i (i <= p).
            for i in range(0, p + 1):
                if (locked >> i) & 1:
                    continue
                for state, (fw, fe) in forward[i].items():
                    if state & 1:
                        continue
                    for o in live_opts[i]:
                        if not ((o.members >> p) & 1):
                            continue
                        if o.local_mask & state:
                            continue
                        new_state = ((state | o.local_mask) >> 1) & STATE_MASK
                        tail = backward[i + 1].get(new_state)
                        if tail is None:
                            continue
                        if fw + o.reward + tail[0] != total_w:
                            continue
                        if fe + 1 + tail[1] != total_e:
                            continue
                        id_tuple = tuple(
                            sorted(
                                hits[q].id
                                for q in range(n)
                                if (o.members >> q) & 1
                            )
                        )
                        if best_tuple is None or id_tuple < best_tuple:
                            best_tuple = id_tuple
                            best_members = o.members
            if best_tuple is not None:
                result.append(sorted(best_tuple))
                locked |= best_members
            else:
                # No optimal completion groups this hit: it is noise.
                locked |= 1 << p
        return result

    canonical = canonical_groups()

    pair_membership = []
    for idx, (a, b) in enumerate(pair_list):
        bit = 1 << idx
        if best.mand & bit:
            status = ALWAYS
        elif best.union & bit:
            status = SOMETIMES
        else:
            status = NEVER
        x, y = hits[a].id, hits[b].id
        if x > y:
            x, y = y, x
        pair_membership.append({"pair": [x, y], "status": status})
    pair_membership.sort(key=lambda e: e["pair"])

    grouped_ids = {h for ev in canonical for h in ev}
    noise = sorted(h.id for h in hits if h.id not in grouped_ids)

    return {
        "grouped_confidence": best.weight,
        "event_count": best.events,
        "optimal_solution_count": best.ways,
        "canonical_groups": canonical,
        "noise": noise,
        "pair_membership": pair_membership,
    }
