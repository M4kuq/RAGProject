from __future__ import annotations

import json
import sys
from urllib.request import urlopen


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/ready"
    required_checks = sys.argv[2:]
    try:
        with urlopen(url, timeout=3) as response:
            if response.status < 200 or response.status >= 300:
                return 1
            raw = response.read()
    except Exception:
        return 1

    if not required_checks:
        return 0

    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return 1

    if payload.get("status") != "ok":
        return 1
    checks = payload.get("checks", {})
    if not isinstance(checks, dict):
        return 1
    return 0 if all(checks.get(name) is True for name in required_checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
