"""Correctness tests for the audit solver and HTTP layer.

Small cases are cross-checked against an exhaustive reference; larger
cases exercise performance, boundary behavior and validation paths.
"""

import json
import os
import random
import sys
import threading
import time
import unittest
import urllib.request
from urllib.error import HTTPError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.solver import Hit, solve  # noqa: E402
from app.validation import validate  # noqa: E402
from tests.brute_force import brute_force  # noqa: E402


def make_hits(specs):
    """specs: list of (id, detector, time, confidence)."""
    return [Hit(*s) for s in specs]


def result_matches_brute(hits, window):
    res = solve(list(hits), window)
    ref = brute_force(list(hits), window)
    assert res["grouped_confidence"] == ref["weight"], (res, ref)
    assert res["event_count"] == ref["events"], (res, ref)
    assert res["optimal_solution_count"] == ref["count"], (res, ref)
    assert res["canonical_groups"] == ref["canonical"], (res, ref)
    got_pairs = {tuple(e["pair"]): e["status"] for e in res["pair_membership"]}
    assert got_pairs == ref["pairs"], (got_pairs, ref["pairs"])
    # Feasibility of canonical grouping.
    ids = {h.id: h for h in hits}
    used = set()
    for ev in res["canonical_groups"]:
        assert len(ev) >= 2
        assert len(set(ev)) == len(ev)
        dets = {ids[x].detector for x in ev}
        assert len(dets) == len(ev)
        times = [ids[x].time for x in ev]
        assert max(times) - min(times) <= window
        for x in ev:
            assert x not in used
            used.add(x)
    assert len(used) + len(res["noise"]) == len(hits)
    return res


class BruteForceCrossCheck(unittest.TestCase):
    def test_all_noise_when_detectors_repeat(self):
        # Four hits but only one detector -> no feasible event.
        hits = make_hits([
            ("a", "D1", 0, 5), ("b", "D1", 1, 5),
            ("c", "D1", 2, 5), ("d", "D1", 3, 5),
        ])
        r = result_matches_brute(hits, 10)
        self.assertEqual(r["optimal_solution_count"], 1)
        self.assertEqual(r["canonical_groups"], [])
        self.assertEqual(r["pair_membership"], [])

    def test_simple_pair(self):
        hits = make_hits([
            ("a", "D1", 0, 5), ("b", "D2", 0, 5),
            ("c", "D1", 100, 1), ("d", "D2", 100, 1),
        ])
        r = result_matches_brute(hits, 1)
        self.assertEqual(r["canonical_groups"], [["a", "b"], ["c", "d"]])
        self.assertEqual(r["event_count"], 2)

    def test_window_boundaries_inclusive(self):
        # span == window is feasible; span == window+1 is not.
        base = make_hits([
            ("a", "D1", 0, 1), ("b", "D2", 5, 1),
            ("c", "D1", 20, 1), ("d", "D2", 21, 1),
        ])
        r = solve(base, 5)
        self.assertEqual(r["grouped_confidence"], 4)
        r2 = solve(base, 4)
        self.assertEqual(r2["grouped_confidence"], 2)  # c,d pair survives
        self.assertEqual(
            {tuple(e["pair"]): e["status"] for e in r2["pair_membership"]},
            {("c", "d"): "always"},
        )

    def test_zero_window(self):
        hits = make_hits([
            ("a", "D1", 7, 9), ("b", "D2", 7, 9),
            ("c", "D3", 8, 1), ("d", "D1", 8, 1),
        ])
        result_matches_brute(hits, 0)

    def test_random_small_cases(self):
        rng = random.Random(20260923)
        for trial in range(250):
            n = rng.randint(4, 11)
            ndet = rng.randint(2, 5)
            dets = [f"D{k}" for k in range(ndet)]
            window = rng.randint(0, 6)
            specs = []
            for k in range(n):
                specs.append((
                    f"h{k:02d}",
                    rng.choice(dets),
                    rng.randint(0, 12),
                    rng.randint(1, 9),
                ))
            hits = make_hits(specs)
            with self.subTest(trial=trial, specs=specs, window=window):
                result_matches_brute(hits, window)

    def test_random_medium_cases(self):
        rng = random.Random(4242)
        for trial in range(40):
            n = rng.randint(12, 16)
            ndet = rng.randint(2, 8)
            dets = [f"D{k}" for k in range(ndet)]
            window = rng.randint(1, 5)
            specs = []
            for k in range(n):
                specs.append((
                    f"h{k:02d}", rng.choice(dets),
                    rng.randint(0, 10), rng.randint(1, 20),
                ))
            hits = make_hits(specs)
            # Brute force may be slow on 16; restrict to <=14 here.
            if n <= 14:
                with self.subTest(trial=trial):
                    result_matches_brute(hits, window)


class MultiplicityAndTies(unittest.TestCase):
    def test_multiple_optima_counting(self):
        # 4 hits at the same time, detectors D1,D2,D3,D1, equal confidence.
        # Two perfect pairings exist: {a,b}+{c,d} and {a,c}+{b,d}.
        hits = make_hits([
            ("a", "D1", 0, 1), ("b", "D2", 0, 1), ("c", "D3", 0, 1),
            ("d", "D1", 0, 1),
        ])
        r = result_matches_brute(hits, 1)
        self.assertEqual(r["grouped_confidence"], 4)
        self.assertEqual(r["event_count"], 2)
        self.assertEqual(r["optimal_solution_count"], 2)
        pairs = {tuple(e["pair"]): e["status"] for e in r["pair_membership"]}
        self.assertEqual(pairs[("a", "b")], "sometimes")
        self.assertEqual(pairs[("a", "c")], "sometimes")
        self.assertEqual(pairs[("b", "c")], "never")
        self.assertEqual(pairs[("b", "d")], "sometimes")
        self.assertEqual(pairs[("c", "d")], "sometimes")
        # Same-detector pair cannot co-occur and is not reported.
        self.assertNotIn(("a", "d"), pairs)

    def test_canonical_tiebreak(self):
        # a/b/c at t=0 (D1,D2,D3), d/e at t=1 D2,D3... craft symmetry:
        # two equally good pairings, canonical picks sorted tuple order.
        hits = make_hits([
            ("a", "D1", 0, 5),
            ("b", "D2", 0, 5),
            ("c", "D3", 1, 5),
            ("d", "D1", 1, 5),
        ])
        r = result_matches_brute(hits, 2)
        # Canonical first tuple starts with 'a'; its smallest partner is b.
        first = r["canonical_groups"][0]
        self.assertEqual(first[0], "a")
        self.assertEqual(first[1], "b")

    def test_min_events_secondary_objective(self):
        # A 3-hit event outranks two-pair alternatives only when weights
        # tie; construct: one triple event confidence 1+1+1 vs pairing...
        hits = make_hits([
            ("a", "D1", 0, 1), ("b", "D2", 0, 1), ("c", "D3", 0, 1),
        ] + [("z", "D4", 50, 1)])  # extra far hit to meet any count needs
        r = solve(hits, 1)
        self.assertEqual(r["event_count"], 1)
        self.assertEqual(r["grouped_confidence"], 3)

    def test_confidence_priority_beats_fewer_events(self):
        # More weight with more events must win over fewer, lighter events.
        hits = make_hits([
            ("a", "D1", 0, 10), ("b", "D2", 0, 10),
            ("c", "D1", 1, 10), ("d", "D2", 1, 10),
            ("e", "D3", 0, 1),
        ])
        # Option: (e,a?,b?) triple... e is D3 at t0. Triple (a,b,e) w21
        # plus pair (c,d) w20 => 41 weight, 2 events.
        # Alternative single event can't span both D1 hits.
        r = solve(hits, 1)
        self.assertEqual(r["grouped_confidence"], 41)
        self.assertEqual(r["event_count"], 2)


class FullScalePerformance(unittest.TestCase):
    def _dense_hits(self, n=80, ndet=8, window=100, seed=1):
        rng = random.Random(seed)
        dets = [f"D{k}" for k in range(ndet)]
        specs = []
        # Place <=10 hits per window bucket: use spaced times.
        for k in range(n):
            specs.append((
                f"h{k:03d}", dets[k % ndet],
                (k // 10) * (window + 1) + rng.randint(0, window),
                rng.randint(1, 1000),
            ))
        return make_hits(specs)

    def test_full_scale_runs_without_enumeration(self):
        hits = self._dense_hits()
        t0 = time.perf_counter()
        r = solve(hits, 100)
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 20.0, f"solver too slow: {elapsed:.2f}s")
        self.assertIsInstance(r["optimal_solution_count"], int)
        self.assertGreaterEqual(r["grouped_confidence"], 0)
        self.assertEqual(
            len(r["canonical_groups"]) + 0, r["event_count"]
        )

    def test_many_optima_big_integer(self):
        # Repeated independent identical pairs -> number of optimal
        # solutions is a power of two (choice of which hits are noise
        # when detectors repeat in each block).
        specs = []
        for block in range(20):
            t = block * 100
            specs.append((f"a{block}", "D1", t, 1))
            specs.append((f"b{block}", "D2", t, 1))
        hits = make_hits(specs)
        r = solve(hits, 0)
        self.assertEqual(r["optimal_solution_count"], 1)  # unique pairing
        self.assertEqual(r["event_count"], 20)
        # An ambiguous chain: two D1 + one D2 per block at equal time,
        # but blocks isolated -> factor 2 per block.
        specs = []
        for block in range(26):
            t = block * 100
            specs.append((f"x{block}", "D1", t, 1))
            specs.append((f"y{block}", "D1", t, 1))
            specs.append((f"z{block}", "D2", t, 1))
        hits = make_hits(specs)
        r = solve(hits, 0)
        self.assertEqual(r["optimal_solution_count"], 2 ** 26)
        self.assertEqual(r["event_count"], 26)


class ValidationTests(unittest.TestCase):
    def _post_body(self, payload):
        return payload

    def test_missing_fields_report_paths(self):
        payload = {"detectors": ["D1", "D2"], "hits": []}
        hits, window, errors = validate(payload)
        paths = {e["path"] for e in errors}
        self.assertIn("window", paths)
        self.assertTrue(any(p.startswith("hits") for p in paths))

    def test_bad_hit_fields(self):
        payload = {
            "window": 3,
            "detectors": ["D1", "D2"],
            "hits": [
                {"id": "a", "detector": "D1", "time": 0, "confidence": 1},
                {"id": "", "detector": "D2", "time": "x", "confidence": 0},
                {"id": "a", "detector": "ZZ", "time": 1, "confidence": 2},
                {"id": "d", "detector": "D1", "time": 2, "confidence": 1,
                 "extra": 7},
            ],
        }
        _, _, errors = validate(payload)
        paths = {e["path"] for e in errors}
        self.assertIn("hits[1].id", paths)
        self.assertIn("hits[1].time", paths)
        self.assertIn("hits[1].confidence", paths)
        self.assertIn("hits[2].id", paths)  # duplicate
        self.assertIn("hits[2].detector", paths)
        self.assertIn("hits[3].extra", paths)

    def test_counts_out_of_range(self):
        payload = {
            "window": 0,
            "detectors": ["only"],
            "hits": [{"id": "a", "detector": "only", "time": 0,
                      "confidence": 1}],
        }
        _, _, errors = validate(payload)
        paths = {e["path"] for e in errors}
        self.assertIn("detectors", paths)
        self.assertIn("hits", paths)

    def test_negative_window_and_bool_rejection(self):
        payload = {
            "window": -1,
            "detectors": ["D1", "D2"],
            "hits": [
                {"id": "a", "detector": "D1", "time": True,
                 "confidence": 1},
                {"id": "b", "detector": "D2", "time": 0, "confidence": 1},
                {"id": "c", "detector": "D1", "time": 0, "confidence": 1},
                {"id": "d", "detector": "D2", "time": 0, "confidence": 1},
            ],
        }
        _, _, errors = validate(payload)
        paths = {e["path"] for e in errors}
        self.assertIn("window", paths)
        self.assertIn("hits[0].time", paths)

    def test_window_capacity_violation(self):
        payload = {
            "window": 5,
            "detectors": [f"D{k}" for k in range(8)],
            "hits": [
                {"id": f"h{k}", "detector": f"D{k % 8}", "time": k % 6,
                 "confidence": 1}
                for k in range(11)
            ],
        }
        _, _, errors = validate(payload)
        self.assertTrue(any(e["path"] == "window" for e in errors))


class HTTPSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.server import create_server
        cls.httpd = create_server("127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def _request(self, path, payload=None, method="POST"):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except HTTPError as e:
            return e.code, json.loads(e.read())

    def test_health(self):
        status, body = self._request("/health", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_audit_ok(self):
        payload = {
            "window": 2,
            "detectors": ["D1", "D2", "D3"],
            "hits": [
                {"id": "a", "detector": "D1", "time": 0, "confidence": 5},
                {"id": "b", "detector": "D2", "time": 1, "confidence": 5},
                {"id": "c", "detector": "D3", "time": 2, "confidence": 5},
                {"id": "d", "detector": "D1", "time": 9, "confidence": 1},
            ],
        }
        status, body = self._request("/audit", payload)
        self.assertEqual(status, 200)
        self.assertIn("result", body)
        self.assertNotIn("errors", body)
        self.assertEqual(body["result"]["canonical_groups"],
                         [["a", "b", "c"]])
        # Serialized integer is JSON number with full precision.
        raw = json.dumps(body)
        self.assertIn('"optimal_solution_count": 1', raw)

    def test_audit_errors_have_no_results(self):
        payload = {"window": -1, "detectors": [], "hits": []}
        status, body = self._request("/audit", payload)
        self.assertEqual(status, 400)
        self.assertIn("errors", body)
        self.assertNotIn("result", body)
        self.assertTrue(all("path" in e for e in body["errors"]))

    def test_malformed_json(self):
        url = f"http://127.0.0.1:{self.port}/audit"
        req = urllib.request.Request(url, data=b"{not json",
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("expected 400")
        except HTTPError as e:
            self.assertEqual(e.code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
