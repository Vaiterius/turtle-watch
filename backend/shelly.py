"""Shelly local RPC: read one DS18B20 temperature. Read-only toward the device.

Verified against the Shelly docs: POST /rpc/<Method> with JSON params returns
the bare result; Temperature status is {id, tC, tF, errors?}; components are
keyed "temperature:<id>". NOT verified on a real Pill: which id the probe gets.
"""

import http.client
import json
import math
import re
import urllib.error
import urllib.request

TEMP_KEY = re.compile(r"temperature:(\d+)")
MAX_PAGES = 20
# No proxies: the Shelly is always a direct LAN address.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ShellyError(Exception):
    """str(e) is a short fixed-vocabulary message that is safe to show in the UI."""


def rpc(host, method, params, timeout=5):
    request = urllib.request.Request(
        f"http://{host}/rpc/{method}",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with OPENER.open(request, timeout=timeout) as response:
            body = response.read(1_000_000)
    except urllib.error.HTTPError as e:
        raise ShellyError("auth required" if e.code == 401 else f"http {e.code}") from e
    except urllib.error.URLError as e:
        timed_out = isinstance(e.reason, TimeoutError)
        raise ShellyError("timeout" if timed_out else "unreachable") from e
    except TimeoutError as e:
        raise ShellyError("timeout") from e
    except (OSError, http.client.HTTPException, ValueError) as e:
        raise ShellyError("unreachable") from e
    try:
        result = json.loads(body)
    except ValueError as e:
        raise ShellyError("invalid response") from e
    if not isinstance(result, dict):
        raise ShellyError("invalid response")
    return result


def parse_status(status):
    """Celsius from a Temperature status object, or ShellyError. Never a guess."""
    if not isinstance(status, dict):
        raise ShellyError("invalid response")
    errors = status.get("errors")
    if errors:
        names = [e for e in errors if isinstance(e, str)] if isinstance(errors, list) else []
        raise ShellyError("sensor error: " + ", ".join(names)[:60] if names else "sensor error")
    temp = status.get("tC")
    if temp is None:
        raise ShellyError("no valid reading")
    if isinstance(temp, bool) or not isinstance(temp, (int, float)):
        raise ShellyError("invalid response")
    if not math.isfinite(temp) or not -55 <= temp <= 125:
        raise ShellyError("out of range")
    return float(temp)


def discover(host, timeout=5):
    """{id: status} for every top-level temperature:<id> component."""
    found, offset = {}, 0
    for _ in range(MAX_PAGES):
        page = rpc(host, "Shelly.GetComponents", {"include": ["status"], "offset": offset}, timeout)
        components = page.get("components")
        if not isinstance(components, list):
            raise ShellyError("invalid response")
        for component in components:
            if not isinstance(component, dict):
                raise ShellyError("invalid response")
            match = TEMP_KEY.fullmatch(str(component.get("key")))
            if match:
                found[int(match.group(1))] = component.get("status")
        offset += len(components)
        total = page.get("total")
        if not components or not isinstance(total, int) or offset >= total:
            break
    return found


def read_temperature(host, component_id=None, timeout=5):
    """(component id, Celsius). Uses the configured id, else discovers the only one."""
    if component_id is not None:
        status = rpc(host, "Temperature.GetStatus", {"id": component_id}, timeout)
    else:
        found = discover(host, timeout)
        if not found:
            raise ShellyError("no temperature component")
        if len(found) > 1:
            ids = ", ".join(str(i) for i in sorted(found))
            raise ShellyError(f"multiple temperature components: {ids}")
        component_id, status = next(iter(found.items()))
    return component_id, parse_status(status)
