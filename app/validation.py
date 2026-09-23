"""Request validation for POST /audit.

Every structural/semantic problem is reported separately with a path.
On failure the response contains *only* errors, never partial results.
"""

from typing import Any, List, Tuple

from .solver import Hit

MIN_DETECTORS = 2
MAX_DETECTORS = 8
MIN_HITS = 4
MAX_HITS = 80
WINDOW_CAPACITY = 10
REQUIRED_HIT_FIELDS = ("id", "detector", "time", "confidence")


def _is_int(value: Any) -> bool:
    # bool is a subclass of int but is not an acceptable integer here.
    return isinstance(value, int) and not isinstance(value, bool)


def _is_ascii_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) >= 1
        and value.isascii()
        and all(ch.isprintable() for ch in value)
    )


def validate(payload: Any) -> Tuple[List[Hit], int, List[dict]]:
    """Return (hits, window, errors); hits/window valid only when empty."""
    errors: List[dict] = []

    def err(path: str, message: str) -> None:
        errors.append({"path": path, "message": message})

    if not isinstance(payload, dict):
        return [], 0, [{"path": "", "message": "request body must be a JSON object"}]

    # ---- window --------------------------------------------------------
    window = 0
    if "window" not in payload:
        err("window", "field is required")
    elif not _is_int(payload["window"]):
        err("window", "must be a non-negative integer")
    elif payload["window"] < 0:
        err("window", "must be a non-negative integer")
    else:
        window = payload["window"]

    # ---- detectors -----------------------------------------------------
    detector_set: set = set()
    detectors = payload.get("detectors")
    if "detectors" not in payload:
        err("detectors", "field is required")
    elif not isinstance(detectors, list):
        err("detectors", "must be an array")
    else:
        if not (MIN_DETECTORS <= len(detectors) <= MAX_DETECTORS):
            err("detectors",
                f"must contain between {MIN_DETECTORS} and {MAX_DETECTORS} detectors")
        for k, det in enumerate(detectors):
            path = f"detectors[{k}]"
            if not _is_ascii_id(det):
                err(path, "must be a non-empty printable ASCII string")
            elif det in detector_set:
                err(path, "duplicate detector id")
            else:
                detector_set.add(det)

    # ---- hits ----------------------------------------------------------
    hits: List[Hit] = []
    raw_hits = payload.get("hits")
    if "hits" not in payload:
        err("hits", "field is required")
    elif not isinstance(raw_hits, list):
        err("hits", "must be an array")
    else:
        if not (MIN_HITS <= len(raw_hits) <= MAX_HITS):
            err("hits",
                f"must contain between {MIN_HITS} and {MAX_HITS} hits")

        seen_ids: set = set()
        for k, item in enumerate(raw_hits):
            path = f"hits[{k}]"
            if not isinstance(item, dict):
                err(path, "must be an object")
                continue
            for key in item:
                if key not in REQUIRED_HIT_FIELDS:
                    err(f"{path}.{key}", "unknown field")

            hid = item.get("id")
            if "id" not in item:
                err(f"{path}.id", "field is required")
            elif not _is_ascii_id(hid):
                err(f"{path}.id", "must be a non-empty printable ASCII string")
            elif hid in seen_ids:
                err(f"{path}.id", f"duplicate hit id {hid!r}")
            else:
                seen_ids.add(hid)

            det = item.get("detector")
            if "detector" not in item:
                err(f"{path}.detector", "field is required")
            elif not isinstance(det, str) or not det:
                err(f"{path}.detector", "must be a non-empty string")
            elif detector_set and det not in detector_set:
                err(f"{path}.detector", f"unknown detector {det!r}")

            if "time" not in item:
                err(f"{path}.time", "field is required")
            elif not _is_int(item["time"]):
                err(f"{path}.time", "must be an integer")

            conf = item.get("confidence")
            if "confidence" not in item:
                err(f"{path}.confidence", "field is required")
            elif not _is_int(conf):
                err(f"{path}.confidence", "must be a positive integer")
            elif conf <= 0:
                err(f"{path}.confidence", "must be a positive integer")

            # Construct only when this hit is internally valid.
            if (
                _is_ascii_id(hid)
                and hid not in {h.id for h in hits}
                and isinstance(det, str)
                and det in detector_set
                and _is_int(item.get("time"))
                and _is_int(conf)
                and conf > 0
            ):
                hits.append(Hit(hid, det, item["time"], conf))

    # ---- window capacity precondition ---------------------------------
    if hits and "window" in payload and _is_int(payload["window"]) and payload["window"] >= 0:
        ordered = sorted(h.time for h in hits)
        left = 0
        for right in range(len(ordered)):
            while ordered[right] - ordered[left] > window:
                left += 1
            if right - left + 1 > WINDOW_CAPACITY:
                err("window",
                    f"more than {WINDOW_CAPACITY} hits fall within a closed "
                    f"window of length {window}")
                break

    errors.sort(key=lambda e: (e["path"], e["message"]))
    return hits, window, errors
