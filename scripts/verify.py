#!/usr/bin/env python3
"""One-shot verification entrypoint (the `verify` compose service).

Runs, in order:
  1. build checks  - byte-compile every source file;
  2. test-suite    - unittest: multi-solution counts, canonical
                     arbitration, pair membership, window boundaries,
                     validation paths, full-scale performance;
  3. HTTP smoke    - /health and POST /audit against AUDIT_BASE_URL.

Exits 0 only if every stage passes.
"""

import json
import os
import py_compile
import subprocess
import sys
import time
import urllib.request
from urllib.error import HTTPError, URLError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_URL = os.environ.get("AUDIT_BASE_URL", "http://127.0.0.1:8080")


def stage(name):
    print(f"\n=== verify: {name} ===", flush=True)


def build_checks():
    stage("build checks")
    ok = True
    for dirname in ("app", "tests", "scripts"):
        root = os.path.join(ROOT, dirname)
        for name in sorted(os.listdir(root)):
            if name.endswith(".py"):
                path = os.path.join(root, name)
                try:
                    py_compile.compile(path, doraise=True, quiet=2)
                    print(f"  compiled {dirname}/{name}")
                except py_compile.PyCompileError as exc:
                    ok = False
                    print(f"  FAIL compiling {path}: {exc}")
    return ok


def run_tests():
    stage("unit / property tests")
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "tests.test_audit", "-v"],
        cwd=ROOT,
    )
    return proc.returncode == 0


def wait_for_health(timeout=30.0):
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except (URLError, HTTPError, OSError) as exc:
            last_err = exc
            time.sleep(0.5)
    print(f"  service did not become healthy: {last_err}")
    return False


def http_smoke():
    stage(f"HTTP smoke against {BASE_URL}")
    if not wait_for_health():
        return False
    print("  GET /health -> 200")

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
    req = urllib.request.Request(
        f"{BASE_URL}/audit",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status != 200:
            print(f"  unexpected status {resp.status}")
            return False
        body = json.loads(resp.read())

    result = body.get("result")
    if result is None or "errors" in body:
        print(f"  malformed success body: {body}")
        return False
    expected_groups = [["a", "b", "c"]]
    if result["canonical_groups"] != expected_groups:
        print(f"  bad groups: {result['canonical_groups']}")
        return False
    if result["optimal_solution_count"] != 1:
        print("  bad optimal_solution_count")
        return False
    print(f"  POST /audit -> 200 groups={result['canonical_groups']} "
          f"confidence={result['grouped_confidence']}")

    # Error path: errors only, no results, per-path entries.
    bad = {"window": -2, "detectors": ["x"], "hits": []}
    req = urllib.request.Request(
        f"{BASE_URL}/audit",
        data=json.dumps(bad).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        print("  expected 400 for invalid payload")
        return False
    except HTTPError as exc:
        if exc.code != 400:
            print(f"  expected 400, got {exc.code}")
            return False
        err_body = json.loads(exc.read())
        if "result" in err_body or "errors" not in err_body:
            print("  error response must contain only errors")
            return False
        if not all("path" in e for e in err_body["errors"]):
            print("  every error must carry a path")
            return False
        print(f"  POST /audit invalid -> 400 with {len(err_body['errors'])} path errors")

    return True


def main():
    results = {
        "build": build_checks(),
        "tests": run_tests(),
        "http": http_smoke(),
    }
    print("\n=== verify summary ===")
    for name, ok in results.items():
        print(f"  {name}: {'PASS' if ok else 'FAIL'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
