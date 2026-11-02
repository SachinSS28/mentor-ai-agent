"""Hong Kong Observatory (HKO) weather and severe weather warnings.

Uses the Hong Kong Observatory's free open data API (no key required):
- Real-time weather observations (temperature, humidity, rain)
- Severe weather warnings (Typhoon signals, Rainstorm warnings, Landslip, Extreme heat)
"""

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

HKO_WARN_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=warnsum&lang=en"
HKO_WEATHER_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=rhrread&lang=en"
TIMEOUT = 6.0


def active_warnings() -> list[str]:
    """List of active severe weather warnings in Hong Kong."""
    try:
        resp = httpx.get(HKO_WARN_URL, timeout=TIMEOUT)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    except Exception as error:
        log.warning("Could not fetch HKO warnings: %s", error)
        return []

    signals: list[str] = []
    # warnsum returns a dictionary where each key represents a warning type
    for code, info in data.items():
        if isinstance(info, dict):
            name = info.get("name") or code
            action = info.get("actionCode", "")
            if action in ("ISSUE", "REISSUE", "EXTEND", ""):
                signals.append(str(name))
    return signals


def current_conditions() -> dict[str, Any]:
    """Returns temperature, humidity, and rainfall summary for Hong Kong."""
    try:
        resp = httpx.get(HKO_WEATHER_URL, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as error:
        log.warning("Could not fetch HKO current weather: %s", error)
        return {}

    temp_list = data.get("temperature", {}).get("data", [])
    # Prefer Observatory or King's Park temperature
    temp = None
    for entry in temp_list:
        if entry.get("place") in ("Hong Kong Observatory", "King's Park"):
            temp = entry.get("value")
            break
    if temp is None and temp_list:
        temp = temp_list[0].get("value")

    humidity_list = data.get("humidity", {}).get("data", [])
    humidity = humidity_list[0].get("value") if humidity_list else None

    rainfall_data = data.get("rainfall", {}).get("data", [])
    max_rain = 0.0
    for r in rainfall_data:
        max_rain = max(max_rain, float(r.get("max", 0) or 0))

    return {
        "temperature": temp,
        "humidity": humidity,
        "max_rainfall_mm": max_rain,
    }


def morning_weather_section() -> str | None:
    """A concise weather section for the 08:00 morning message."""
    warnings = active_warnings()
    conditions = current_conditions()

    temp = conditions.get("temperature")
    humidity = conditions.get("humidity")

    parts: list[str] = []
    if warnings:
        alert_text = " • ".join(warnings)
        parts.append(f"⚠️ **Weather Warning:** {alert_text}")

    status_items: list[str] = []
    if temp is not None:
        status_items.append(f"{temp}°C")
    if humidity is not None:
        status_items.append(f"{humidity}% humidity")
    if conditions.get("max_rainfall_mm", 0) > 0:
        status_items.append(f"Rain: {conditions['max_rainfall_mm']}mm")

    if status_items:
        icon = "🌧" if conditions.get("max_rainfall_mm", 0) > 5 else "⛅"
        parts.append(f"{icon} **Hong Kong Weather:** {', '.join(status_items)}")

    return "\n".join(parts) if parts else None
