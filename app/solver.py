"""最优命中分组求解器。

目标层级：
  1. 最大化已分组命中的可信度之和；
  2. 在满足 1 的前提下最少化事件数；
  3. 规范解按成员标识排序后的事件序列做词典序裁决。

复杂度保证
----------
约定：任意长度为 ``W`` 的闭窗内至多 10 个命中。按 (时刻, 标识) 排序后，
处理第 i 个命中时，所有“已被早先事件占用、尚未关窗”的命中都落在
``[t_i, t_i+W]`` 内，至多 10 个。因此 DP 状态是至多 10 位的位掩码
（每步至多 1024 个状态），整体为 O(n * 2^10 * 2^10) 上界内的扫描线
动态规划，不枚举任何完整分组方案。方案计数为 Python 任意精度整数。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple


class SolveError(ValueError):
    """输入数据违反约束（如符合窗内命中数超限）。"""


def _sweep_groups(
    times: Sequence[int],
    detectors: Sequence[int],
    window: int,
) -> List[List[Tuple[int, ...]]]:
    """返回每个锚点 i 可以创建的事件。

    事件以其最小下标成员为锚点；成员下标升序、探测器互不相同，
    且最大时刻与最小时刻之差不超过 window。
    """
    n = len(times)
    groups: List[List[Tuple[int, ...]]] = [[] for _ in range(n)]
    for i in range(n):
        ti = times[i]
        det_used = {detectors[i]}
        chosen: List[int] = [i]
        open_hits: List[int] = []
        for j in range(i + 1, n):
            if times[j] - ti > window:
                break
            open_hits.append(j)

        def gen(pos: int) -> None:
            if pos == len(open_hits):
                if len(chosen) >= 2:
                    groups[i].append(tuple(chosen))
                return
            # 不选 open_hits[pos]
            gen(pos + 1)
            # 选（探测器须不冲突）
            j = open_hits[pos]
            d = detectors[j]
            if d not in det_used:
                det_used.add(d)
                chosen.append(j)
                gen(pos + 1)
                chosen.pop()
                det_used.remove(d)

        gen(0)
    return groups


def _open_members(times: Sequence[int], window: int) -> List[List[int]]:
    """open(i)：时刻落在 [t_i, t_i+W] 闭窗内的下标（含 i），至多 10 个。"""
    n = len(times)
    result: List[List[int]] = []
    for i in range(n):
        members = [i]
        for j in range(i + 1, n):
            if times[j] - times[i] > window:
                break
            members.append(j)
        result.append(members)
    return result


# (最优可信度和, 最少事件数, 最优方案数)
_Cell = Tuple[int, int, int]


def _better(a: _Cell, b: Optional[_Cell]) -> bool:
    if b is None:
        return True
    if a[0] != b[0]:
        return a[0] > b[0]
    return a[1] < b[1]


def solve(
    times: Sequence[int],
    detectors: Sequence[int],
    weights: Sequence[int],
    window: int,
    id_order: Optional[Sequence[str]] = None,
) -> dict:
    """计算最优分组、规范解、任意精度方案数与成对归属。

    调用前输入已按 (时刻, 标识) 稳定排序；id_order 给出排序后的标识，
    用于规范解的词典序比较（若为 None 则按下标比较）。
    """
    n = len(times)
    if n == 0:
        raise SolveError("no hits")

    groups = _sweep_groups(times, detectors, window)
    opens = _open_members(times, window)
    for members in opens:
        if len(members) > 10:
            raise SolveError(
                "coincidence window contains more than 10 hits"
            )

    group_masks: List[List[int]] = []
    group_gains: List[List[int]] = []
    open_masks: List[int] = []
    for i in range(n):
        ms, gains = [], []
        om = 0
        for g in groups[i]:
            m = 0
            gain = 0
            for j in g:
                m |= 1 << j
                gain += weights[j]
            ms.append(m)
            gains.append(gain)
        group_masks.append(ms)
        group_gains.append(gains)
        for j in opens[i]:
            om |= 1 << j
        open_masks.append(om)
    # gm -> gain 映射，配合子掩码枚举使用
    gain_maps = [
        dict(zip(group_masks[i], group_gains[i])) for i in range(n)
    ]

    def iter_event_masks(mask: int, i: int):
        """枚举在待决议占用 mask 下、以 i 为锚点可创建的事件 (gm, gain)。

        仅枚举“未被占用的开放命中（锚点除外）”的子掩码，
        三态计数 3^(k-1)（k≤10）。
        """
        bit_i = 1 << i
        free = open_masks[i] & ~mask & ~bit_i
        s = free
        while True:
            gm = s | bit_i
            gain = gain_maps[i].get(gm)
            if gain is not None:
                yield gm, gain
            if s == 0:
                break
            s = (s - 1) & free

    # ---------- 前向 DP ----------
    # F[i][mask]：处理完前 i 个命中后，待决议占用掩码为 mask 时的
    # (已锁定可信度和, 已锁定事件数, 方案数)。
    forward: List[Dict[int, _Cell]] = [{0: (0, 0, 1)}]
    for i in range(n):
        cur = forward[i]
        nxt: Dict[int, _Cell] = {}
        bit_i = 1 << i
        for mask, (sc, ev, cnt) in cur.items():
            # 选择一：i 作为噪声（若 i 已被早先事件占用则强制此路）
            nm = mask & ~bit_i
            cell = (sc, ev, cnt)
            old = nxt.get(nm)
            if old is None:
                nxt[nm] = cell
            elif _better(cell, old):
                nxt[nm] = cell
            elif not _better(old, cell):
                nxt[nm] = (old[0], old[1], old[2] + cnt)
            if mask & bit_i:
                continue
            # 选择二：以 i 为锚点创建事件（枚举空闲开放位的子掩码）
            for gm, gain in iter_event_masks(mask, i):
                nm2 = (mask | gm) & ~bit_i
                cell2 = (sc + gain, ev + 1, cnt)
                old2 = nxt.get(nm2)
                if old2 is None:
                    nxt[nm2] = cell2
                elif _better(cell2, old2):
                    nxt[nm2] = cell2
                elif not _better(old2, cell2):
                    nxt[nm2] = (old2[0], old2[1], old2[2] + cnt)
        forward.append(nxt)

    final = forward[n][0]
    best_score, best_events, total_count = final

    # ---------- 后向 DP ----------
    # B[i][mask]：从步骤 i、待决议掩码 mask 出发，后缀可达的
    # (可信度和, 事件数, 方案数) 最优值。只需前向可达的掩码（每步 ≤ 1024）。
    backward: List[Dict[int, _Cell]] = [{} for _ in range(n + 1)]
    backward[n] = {0: (0, 0, 1)}
    for i in range(n - 1, -1, -1):
        table: Dict[int, _Cell] = {}
        bit_i = 1 << i
        b_next = backward[i + 1]
        for mask in forward[i]:
            # 选择一：噪声（被占用时为强制转移）
            best: Optional[_Cell] = None
            nm0 = mask & ~bit_i
            suffix = b_next.get(nm0)
            if suffix is not None:
                best = suffix
            if not (mask & bit_i):
                # 选择二：以 i 为锚点创建事件（枚举空闲开放位的子掩码）
                for gm, gain in iter_event_masks(mask, i):
                    nm = (mask | gm) & ~bit_i
                    suffix = b_next.get(nm)
                    if suffix is None:
                        continue
                    cell = (suffix[0] + gain, suffix[1] + 1, suffix[2])
                    if best is None or _better(cell, best):
                        best = cell
                    elif not _better(best, cell):
                        best = (best[0], best[1], best[2] + cell[2])
            if best is not None:
                table[mask] = best
        backward[i] = table

    # ---------- 规范解（词典序裁决） ----------
    # 对每个可达状态 (i, mask) 求后缀的“成员标识排序后的事件序列”
    # 的词典序最小值（仅限达到该状态最优 (可信度, 事件数) 的转移）。
    # 事件在锚点处加入，与后缀已排序序列做单点插入后比较。
    # 预生成每个候选事件的比较键（按掩码索引）。
    # 有标识序列时按标识（字符串元组）裁决，否则直接按下标（整数元组）。
    def _key(j: int):
        return id_order[j] if id_order is not None else j

    group_by_mask: List[Dict[int, Tuple[int, ...]]] = [
        {m: g for g, m in zip(groups[i], group_masks[i])}
        for i in range(n)
    ]
    gm_to_keys: List[Dict[int, Tuple[object, ...]]] = [
        {
            m: tuple(_key(j) for j in g)
            for g, m in zip(groups[i], group_masks[i])
        }
        for i in range(n)
    ]

    memo: Dict[Tuple[int, int], Tuple[Tuple[object, ...], ...]] = {}

    def canonical_suffix(
        i: int, mask: int
    ) -> Tuple[Tuple[object, ...], ...]:
        if i == n:
            return ()
        key = (i, mask)
        if key in memo:
            return memo[key]
        target = backward[i][mask]
        bit_i = 1 << i
        best_seq: Optional[Tuple[Tuple[object, ...], ...]] = None

        def consider(
            gain: int,
            events_added: int,
            nm: int,
            inserted: Optional[Tuple[object, ...]],
        ) -> None:
            nonlocal best_seq
            suf = backward[i + 1].get(nm)
            if suf is None:
                return
            if gain + suf[0] != target[0] or events_added + suf[1] != target[1]:
                return
            suffix_seq = canonical_suffix(i + 1, nm)
            if inserted is None:
                cand = suffix_seq
            else:
                cand_list = list(suffix_seq)
                lo, hi = 0, len(cand_list)
                while lo < hi:
                    mid = (lo + hi) // 2
                    if cand_list[mid] < inserted:
                        lo = mid + 1
                    else:
                        hi = mid
                cand_list.insert(lo, inserted)
                cand = tuple(cand_list)
            if best_seq is None or cand < best_seq:
                best_seq = cand

        # 噪声（被占用时为强制转移）
        consider(0, 0, mask & ~bit_i, None)
        if not (mask & bit_i):
            for gm, gain in iter_event_masks(mask, i):
                nm = (mask | gm) & ~bit_i
                consider(gain, 1, nm, gm_to_keys[i][gm])

        assert best_seq is not None
        memo[key] = best_seq
        return best_seq

    canonical_seq = canonical_suffix(0, 0)
    # 将比较键还原为下标元组
    if id_order is not None:
        key_to_index = {id_order[j]: j for j in range(n)}
        canonical_groups = [
            tuple(key_to_index[x] for x in gt) for gt in canonical_seq
        ]
    else:
        canonical_groups = [tuple(gt) for gt in canonical_seq]

    # ---------- 逐事件 / 逐对在最优解中的出现次数 ----------
    member_count: Dict[int, int] = {}
    pair_count: Dict[Tuple[int, int], int] = {}
    for i in range(n):
        f_table = forward[i]
        b_next = backward[i + 1]
        for fmask, (fsc, fev, fcnt) in f_table.items():
            if fmask & (1 << i):
                continue
            for gm, gain in iter_event_masks(fmask, i):
                nm = (fmask | gm) & ~(1 << i)
                suf = b_next.get(nm)
                if suf is None:
                    continue
                if fsc + gain + suf[0] != best_score:
                    continue
                if fev + 1 + suf[1] != best_events:
                    continue
                g = group_by_mask[i][gm]
                ways = fcnt * suf[2]
                for j in g:
                    member_count[j] = member_count.get(j, 0) + ways
                for a_idx in range(len(g)):
                    for b_idx in range(a_idx + 1, len(g)):
                        a, b = g[a_idx], g[b_idx]
                        key = (a, b)
                        pair_count[key] = pair_count.get(key, 0) + ways

    return {
        "best_score": best_score,
        "best_events": best_events,
        "total_count": total_count,
        "canonical": canonical_groups,
        "member_count": member_count,
        "pair_count": pair_count,
        "groups": groups,
    }
