"""PyPI update check: post-command reminder plus a cache-based serve-banner hint."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request

from awerouter import __version__
from awerouter.config import config_dir

CHECK_INTERVAL_S = 24 * 60 * 60
REMIND_INTERVAL_S = 24 * 60 * 60


def _parse_version(v):
    try:
        parts = re.findall(r"\d+", v)
        return tuple(int(x) for x in parts[:3]) if parts else (0,)
    except (ValueError, AttributeError):
        return (0,)


def _version_gte(a, b):
    return _parse_version(a) >= _parse_version(b)


def _cache_path():
    return config_dir() / "update-check.json"


def _load_cache(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _save_cache(path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")
    except OSError:
        pass


def get_pypi_latest():
    url = "https://pypi.org/pypi/awerouter/json"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read())
    return data["info"]["version"]


def _should_skip(args):
    if "-h" in args or "--help" in args or "-v" in args or "-V" in args or "--version" in args:
        return True
    return bool(args) and args[0] == "self-update"


def check_async(args):
    """Start an update check in a background thread. Returns a callable that
    yields a reminder string or None (call it after the command finishes).

    serve keeps the check enabled: the thread refreshes the cache while the
    server runs (keeping the banner hint fresh), and the reminder printing
    after Ctrl-C is the natural moment to upgrade.
    """
    if os.environ.get("AWEROUTER_NO_UPDATE_CHECK") == "1":
        return lambda: None
    if _should_skip(list(args)):
        return lambda: None

    result = [None]
    done = threading.Event()

    def _run():
        try:
            result[0] = _check()
        except Exception:
            pass
        finally:
            done.set()

    threading.Thread(target=_run, daemon=True).start()

    def get_result():
        done.wait(timeout=6)   # urlopen's timeout is 5; never block the CLI past that
        return result[0]

    return get_result


def _check():
    cache_path = _cache_path()
    cache = _load_cache(cache_path)
    now = time.time()

    latest = None
    if cache and now - cache.get("lastChecked", 0) < CHECK_INTERVAL_S:
        latest = cache.get("latestVersion")
    else:
        latest = get_pypi_latest()
        _save_cache(cache_path, {
            "lastChecked": now,
            "latestVersion": latest,
            "lastReminded": cache.get("lastReminded", 0) if cache else 0,
        })

    if not latest or _version_gte(__version__, latest):
        return None

    last_reminded = cache.get("lastReminded", 0) if cache else 0
    if now - last_reminded < REMIND_INTERVAL_S:
        return None

    _save_cache(cache_path, {
        "lastChecked": now,
        "latestVersion": latest,
        "lastReminded": now,
    })

    return f"Update available: {__version__} → {latest}. Run `awerouter self-update` to update."


def cached_update_hint() -> "str | None":
    """Serve-banner hint from the local cache only — no network, no thread.

    Serves the line before the background check has necessarily finished, so
    it can lag the newest release by up to CHECK_INTERVAL_S.
    """
    cache = _load_cache(_cache_path())
    latest = cache.get("latestVersion") if cache else None
    if not latest or _version_gte(__version__, latest):
        return None
    return f"update available: {__version__} → {latest}  (awerouter self-update)"
