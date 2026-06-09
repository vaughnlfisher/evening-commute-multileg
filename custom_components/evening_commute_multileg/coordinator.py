"""Coordinator for Evening Commute Multileg."""
from __future__ import annotations

import base64
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
    HSP_URL, HSP_USERNAME, HSP_PASSWORD, HSP_LEGS, HSP_FROM_TIME, HSP_TO_TIME,
    LEG2_HISTORY_PROXY_ENTITY,
)

_LOGGER = logging.getLogger(__name__)

HSP_REFRESH = timedelta(hours=1)

HUXLEY_DEP = (
    "https://huxley2.azurewebsites.net/departures/{frm}/to/{to}/{rows}"
    "?expand=true&accessToken={token}"
)


def _parse_hhmm_after(val, ref):
    """Parse HH:MM into a datetime on/after ref (handles midnight rollover)."""
    try:
        h, m = map(int, val.split(":"))
        dt = ref.replace(hour=h, minute=m, second=0, microsecond=0)
        if (dt - ref).total_seconds() < -3600:
            dt += timedelta(days=1)
        return dt
    except (ValueError, TypeError, AttributeError):
        return None


def _arrival_at(svc, dest_names, dep_dt):
    """Extract arrival datetime at the filtered destination from calling points.

    Returns (arrival_dt, in_transit_mins) or (None, None) if unavailable.
    """
    scp = svc.get("subsequentCallingPoints")
    if not scp or not isinstance(scp, list):
        return None, None
    pts = scp[0].get("callingPoint", []) if isinstance(scp[0], dict) else []
    for p in pts:
        name = (p.get("locationName") or "").lower()
        if any(d in name for d in dest_names):
            t = (p.get("et") or "").strip()
            if t in ("", "On time", "Delayed", "Cancelled"):
                t = (p.get("st") or "").strip()
            arr_dt = _parse_hhmm_after(t, dep_dt)
            if arr_dt:
                transit = max(0, round((arr_dt - dep_dt).total_seconds() / 60))
                return arr_dt, transit
            break
    return None, None


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
        self._history: dict = {}          # {leg_key: result dict}
        self._history_last_fetch: datetime | None = None

    def schedule_hsp_fetch(self) -> None:
        """Schedule HSP fetch for all 3 legs as a background task."""
        _LOGGER.warning("HSP: scheduling evening background task")
        self.hass.async_create_background_task(
            self._async_hsp_fetch(),
            name="evening_commute_multileg_hsp_fetch",
        )

    async def _async_hsp_fetch(self) -> None:
        import asyncio as _aio
        await _aio.sleep(30)
        _LOGGER.warning("HSP: evening fetch starting")
        try:
            result = await self._fetch_all_history()
            if result:
                self._history = result
                if self.data:
                    self.data["history"] = result
                    if isinstance(self.data.get("summary"), dict):
                        self.data["summary"]["history"] = result
                    self.async_set_updated_data(self.data)
                _LOGGER.warning("HSP evening: fetched %d legs", len(result))
        except Exception as err:
            _LOGGER.warning("HSP evening error: %s (%s)", type(err).__name__, err)

    async def _fetch_all_history(self) -> dict:
        now = datetime.now()
        if (
            self._history_last_fetch is not None
            and (now - self._history_last_fetch) < HSP_REFRESH
            and self._history
        ):
            return self._history

        today = now.date()
        from_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")
        auth = base64.b64encode(f"{HSP_USERNAME}:{HSP_PASSWORD}".encode()).decode()
        headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}

        out = {}
        for leg in HSP_LEGS:
            payload = {
                "from_loc": leg["from"],
                "to_loc": leg["to"],
                "from_time": HSP_FROM_TIME,
                "to_time": HSP_TO_TIME,
                "from_date": from_date,
                "to_date": to_date,
                "days": "WEEKDAY",
                "tolerance": [0, 5, 10, 15, 30],
            }
            try:
                connector = aiohttp.TCPConnector(ssl=False)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.post(
                        HSP_URL, json=payload, headers=headers,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        if resp.status != 200:
                            body = await resp.text()
                            _LOGGER.warning("HSP %s HTTP %s: %s", leg["key"], resp.status, body[:200])
                            continue
                        data = await resp.json(content_type=None)
                        services = data.get("Services", [])
            except Exception as err:
                _LOGGER.warning("HSP %s error: %s", leg["key"], err)
                continue

            parsed = self._parse_hsp(services, today)
            if parsed:
                parsed["label"] = leg["label"]
                out[leg["key"]] = parsed
                _LOGGER.warning("HSP %s: 7-day %.1f%%", leg["key"], parsed.get("on_time_pct_7day") or 0)

        # Inject leg2 proxy from morning commute sensor (Elizabeth Line — not on NR HSP)
        try:
            s = self.hass.states.get(LEG2_HISTORY_PROXY_ENTITY)
            if s and s.state not in (None, "unknown", "unavailable", ""):
                attrs = s.attributes
                out["leg2"] = {
                    "label": "Farringdon → Paddington (Elizabeth line)",
                    "on_time_pct_today": attrs.get("on_time_pct_today"),
                    "on_time_pct_7day": attrs.get("on_time_pct_7day"),
                    "on_time_pct_30day": attrs.get("on_time_pct_30day"),
                    "daily_breakdown": attrs.get("daily_breakdown", []),
                    "best_day": attrs.get("best_day"),
                    "worst_day": attrs.get("worst_day"),
                    "proxy": True,
                }
                _LOGGER.warning("HSP leg2 proxy: 7-day %.1f%%", attrs.get("on_time_pct_7day") or 0)
        except Exception as err:
            _LOGGER.warning("HSP leg2 proxy error: %s", err)

        if out:
            self._history = out
            self._history_last_fetch = now
        return out

    @staticmethod
    def _parse_hsp(all_services, today) -> dict | None:
        by_date: dict = {}
        for svc in all_services:
            if not isinstance(svc, dict):
                continue
            sam = svc.get("serviceAttributesMetrics", {})
            if not isinstance(sam, dict):
                continue
            rids = sam.get("rids", [])
            if not rids:
                continue
            metrics = svc.get("Metrics", [])
            pct_at_5 = None
            for m in (metrics if isinstance(metrics, list) else []):
                if isinstance(m, dict) and str(m.get("tolerance_value", "")) == "5":
                    pct_at_5 = m.get("percent_tolerance")
                    break
            if pct_at_5 is None:
                continue
            for rid in rids:
                raw = str(rid)[:8]
                if raw.isdigit() and len(raw) == 8:
                    ds = raw[:4] + "-" + raw[4:6] + "-" + raw[6:8]
                    if ds not in by_date:
                        by_date[ds] = {"pct_sum": 0.0, "pct_count": 0}
                    by_date[ds]["pct_sum"] += float(pct_at_5)
                    by_date[ds]["pct_count"] += 1
        if not by_date:
            return None
        daily = []
        for ds in sorted(by_date.keys())[-30:]:
            d = by_date[ds]
            pct = round(d["pct_sum"] / d["pct_count"], 2) if d["pct_count"] else None
            daily.append({"date": ds, "on_time_pct": pct, "total_observations": d["pct_count"]})
        dwd = [d for d in daily if d["on_time_pct"] is not None]
        today_str = today.strftime("%Y-%m-%d")
        last7 = [d for d in dwd if d["date"] >= (today - timedelta(days=7)).strftime("%Y-%m-%d")]

        def avg(days):
            v = [d["on_time_pct"] for d in days if d["on_time_pct"] is not None]
            return round(sum(v) / len(v), 1) if v else None

        td = next((d for d in daily if d["date"] == today_str), None)
        best = max(dwd, key=lambda d: d["on_time_pct"] or 0) if dwd else None
        worst = min(dwd, key=lambda d: d["on_time_pct"] if d["on_time_pct"] is not None else 100) if dwd else None
        return {
            "on_time_pct_today": td["on_time_pct"] if td else None,
            "on_time_pct_7day": avg(last7),
            "on_time_pct_30day": avg(dwd),
            "daily_breakdown": daily,
            "best_day": best,
            "worst_day": worst,
        }

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
        """Sorted list of dicts departing >= after_dt."""
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
                "operator": svc.get("operator"),
                "operator_code": svc.get("operatorCode"),
                "_svc": svc,
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
                # Real Leg1 arrival at Farringdon from calling points
                l1_arr, l1_transit = _arrival_at(l1["_svc"], ["farringdon"], l1["dt"])
                if l1_arr is None:
                    l1_arr = l1["dt"] + timedelta(minutes=3)
                    l1_transit = 3
                board2 = l1_arr + timedelta(minutes=FARRINGDON_INTERCHANGE_MINS)

                leg2_opts = []
                for l2 in self._upcoming(leg2_services, board2):
                    # Real Leg2 arrival at Paddington from calling points
                    l2_arr, l2_transit = _arrival_at(l2["_svc"], ["paddington"], l2["dt"])
                    if l2_arr is None:
                        l2_arr = l2["dt"] + timedelta(minutes=10)
                        l2_transit = 10
                    board3 = l2_arr + timedelta(minutes=PADDINGTON_INTERCHANGE_MINS)
                    leg3_opts = []
                    for l3 in self._upcoming(leg3_services, board3, TWYFORD_TERMINI):
                        _, l3_transit = _arrival_at(l3["_svc"], ["twyford"], l3["dt"])
                        if l3_transit is None:
                            l3_transit = 25
                        total = (l1_transit or 0) + (l2_transit or 0) + (l3_transit or 0)
                        leg3_opts.append({
                            "time": l3["time"],
                            "destination": l3["destination"],
                            "status": l3["status"],
                            "delay_minutes": l3["delay_minutes"],
                            "platform": l3["platform"],
                            "operator": l3["operator"],
                            "operator_code": l3["operator_code"],
                            "transit_mins": l3_transit,
                            "total_transit_mins": total,
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
                        "operator": l2["operator"],
                        "operator_code": l2["operator_code"],
                        "wait_mins": wait2,
                        "transit_mins": l2_transit,
                        "leg3": leg3_opts,
                    })
                    if len(leg2_opts) >= MAX_LEG2:
                        break

                # Best-case total in-transit time (first catchable l2 + its first l3)
                total_transit = None
                if leg2_opts and leg2_opts[0]["leg3"]:
                    total_transit = leg2_opts[0]["leg3"][0]["total_transit_mins"]
                elif leg2_opts:
                    total_transit = (l1_transit or 0) + (leg2_opts[0]["transit_mins"] or 0)

                trains.append({
                    "time": l1["time"],
                    "destination": l1["destination"],
                    "status": l1["status"],
                    "delay_minutes": l1["delay_minutes"],
                    "platform": l1["platform"],
                    "operator": l1["operator"],
                    "operator_code": l1["operator_code"],
                    "transit_mins": l1_transit,
                    "total_transit_mins": total_transit,
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
                    "history": self._history,
                },
                "history": self._history,
            }
            for i, t in enumerate(trains, 1):
                data[f"train_{i}"] = {
                    "state": t["time"],
                    **t,
                }
            return data
        except Exception as err:
            raise UpdateFailed(f"Error updating evening commute: {err}") from err
