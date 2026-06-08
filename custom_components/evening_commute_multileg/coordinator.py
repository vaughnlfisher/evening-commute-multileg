"""Coordinator for Evening Commute Multileg."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    DARWIN_TOKEN,
    LEG1_FROM, LEG1_TO,
    LEG2_FROM, LEG2_TO,
    LEG3_FROM, LEG3_TO,
    FARRINGDON_INTERCHANGE_MINS,
    PADDINGTON_INTERCHANGE_MINS,
    NUM_TRAINS, MAX_LEG2, MAX_LEG3,
    EARLIEST_HOUR,
    SCAN_INTERVAL_PEAK, SCAN_INTERVAL_OFFPEAK, SCAN_INTERVAL_NIGHT,
    HUXLEY_ROWS,
    NORTHBOUND_TERMINI, TWYFORD_TERMINI,
)

_LOGGER = logging.getLogger(__name__)

HUXLEY_DEP = (
    "https://huxley2.azurewebsites.net/departures/{frm}/to/{to}/{rows}"
    "?accessToken={token}"
)


def _get_scan_interval() -> timedelta:
    h = datetime.now().hour
    if 6 <= h < 10 or 16 <= h < 20:
        return timedelta(seconds=SCAN_INTERVAL_PEAK)
    if 23 <= h or h < 5:
        return timedelta(seconds=SCAN_INTERVAL_NIGHT)
    return timedelta(seconds=SCAN_INTERVAL_OFFPEAK)


def _svc_dest(svc):
    dest = svc.get("destination") or []
    if isinstance(dest, list) and dest:
        return dest[0].get("locationName", "")
    return str(dest)


def _svc_time(svc):
    """Return the best available departure datetime for a service."""
    now = datetime.now().astimezone()
    for key in ("etd", "std"):
        val = (svc.get(key) or "").strip()
        if val in ("", "Delayed", "Cancelled", "On time"):
            continue
        try:
            h, m = map(int, val.split(":"))
            dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if (dt - now).total_seconds() < -3600:
                dt += timedelta(days=1)
            return dt
        except (ValueError, TypeError):
            continue
    std = (svc.get("std") or "").strip()
    if std:
        try:
            h, m = map(int, std.split(":"))
            dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if (dt - now).total_seconds() < -3600:
                dt += timedelta(days=1)
            return dt
        except (ValueError, TypeError):
            pass
    return None


def _svc_status(svc):
    etd = (svc.get("etd") or "").strip()
    if etd == "Cancelled":
        return "Cancelled", None
    if etd == "On time" or etd == "":
        return "On time", 0
    if etd == "Delayed":
        return "Delayed", None
    # etd is a time -> compute delay vs std
    std = (svc.get("std") or "").strip()
    try:
        eh, em = map(int, etd.split(":"))
        sh, sm = map(int, std.split(":"))
        delay = (eh * 60 + em) - (sh * 60 + sm)
        if delay < 0:
            delay += 1440
        return ("On time" if delay == 0 else "Delayed"), delay
    except (ValueError, TypeError):
        return "On time", 0


def _is_to(svc, termini):
    dest = _svc_dest(svc).lower()
    return any(kw in dest for kw in termini)


class EveningCommuteCoordinator(DataUpdateCoordinator):

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=_get_scan_interval())
        self.entry = entry

    async def _fetch_leg(self, frm: str, to: str) -> list[dict]:
        url = HUXLEY_DEP.format(frm=frm, to=to, rows=HUXLEY_ROWS, token=DARWIN_TOKEN)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                    if resp.status != 200:
                        _LOGGER.warning("Huxley %s->%s HTTP %s", frm, to, resp.status)
                        return []
                    data = await resp.json(content_type=None)
                    return data.get("trainServices") or []
        except Exception as err:
            _LOGGER.warning("Huxley %s->%s error: %s", frm, to, err)
            return []

    def _upcoming(self, services, after_dt, termini=None):
        """Sorted list of (dt, dest, status, delay, platform, svc) departing >= after_dt."""
        out = []
        for svc in services:
            if termini and not _is_to(svc, termini):
                continue
            dt = _svc_time(svc)
            if not dt or dt < after_dt:
                continue
            status, delay = _svc_status(svc)
            out.append({
                "dt": dt,
                "time": dt.strftime("%H:%M"),
                "destination": _svc_dest(svc),
                "status": status,
                "delay_minutes": delay,
                "platform": svc.get("platform"),
            })
        out.sort(key=lambda x: x["dt"])
        return out

    async def _async_update_data(self) -> dict:
        self.update_interval = _get_scan_interval()
        try:
            now = datetime.now().astimezone()
            base = now

            leg1_services = await self._fetch_leg(LEG1_FROM, LEG1_TO)
            leg2_services = await self._fetch_leg(LEG2_FROM, LEG2_TO)
            leg3_services = await self._fetch_leg(LEG3_FROM, LEG3_TO)

            # Leg 1: CTK -> Farringdon (northbound Thameslink)
            leg1 = self._upcoming(leg1_services, base, NORTHBOUND_TERMINI)
            if not leg1:
                # fallback: any service calling at Farringdon
                leg1 = self._upcoming(leg1_services, base)

            trains = []
            for l1 in leg1[:NUM_TRAINS]:
                # Estimate Leg1 arrival at Farringdon: CTK->ZFD ~3 min
                l1_arr = l1["dt"] + timedelta(minutes=3)
                board2 = l1_arr + timedelta(minutes=FARRINGDON_INTERCHANGE_MINS)

                leg2_opts = []
                for l2 in self._upcoming(leg2_services, board2):
                    # Estimate Leg2 arrival at Paddington: ZFD->PAD ~10 min
                    l2_arr = l2["dt"] + timedelta(minutes=10)
                    board3 = l2_arr + timedelta(minutes=PADDINGTON_INTERCHANGE_MINS)
                    leg3_opts = []
                    for l3 in self._upcoming(leg3_services, board3, TWYFORD_TERMINI):
                        leg3_opts.append({
                            "time": l3["time"],
                            "destination": l3["destination"],
                            "status": l3["status"],
                            "delay_minutes": l3["delay_minutes"],
                            "platform": l3["platform"],
                        })
                        if len(leg3_opts) >= MAX_LEG3:
                            break
                    wait2 = max(0, round((l2["dt"] - l1_arr).total_seconds() / 60))
                    leg2_opts.append({
                        "time": l2["time"],
                        "destination": l2["destination"],
                        "status": l2["status"],
                        "delay_minutes": l2["delay_minutes"],
                        "platform": l2["platform"],
                        "wait_mins": wait2,
                        "leg3": leg3_opts,
                    })
                    if len(leg2_opts) >= MAX_LEG2:
                        break

                trains.append({
                    "time": l1["time"],
                    "destination": l1["destination"],
                    "status": l1["status"],
                    "delay_minutes": l1["delay_minutes"],
                    "platform": l1["platform"],
                    "leg2": leg2_opts,
                })

            data = {
                "summary": {
                    "state": trains[0]["time"] if trains else "No service",
                    "leg1_from": LEG1_FROM,
                    "leg1_to": LEG1_TO,
                    "leg2_to": LEG2_TO,
                    "leg3_to": LEG3_TO,
                    "farringdon_interchange_mins": FARRINGDON_INTERCHANGE_MINS,
                    "paddington_interchange_mins": PADDINGTON_INTERCHANGE_MINS,
                    "trains": trains,
                    "last_updated": now.isoformat(),
                },
            }
            for i, t in enumerate(trains, 1):
                data[f"train_{i}"] = {
                    "state": t["time"],
                    **t,
                }
            return data
        except Exception as err:
            raise UpdateFailed(f"Error updating evening commute: {err}") from err
