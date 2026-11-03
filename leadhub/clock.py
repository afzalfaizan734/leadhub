"""Accurate time + business timezone.

Two jobs:
  1. Get the TRUE current time from the internet, so a wrong/drifting PC clock
     never makes dates or the call-window wrong. Falls back to the PC clock if
     offline (best effort), and reports whether it is verified.
  2. Convert that true UTC into the admin-chosen US business timezone, with
     correct US daylight-saving - using only the standard library (no external
     timezone database, so it runs on the bundled portable Python on Windows).

Everything in the app that needs "now" / "today" goes through here.
"""
import datetime
import threading
import time
import urllib.request
from email.utils import parsedate_to_datetime

_lock = threading.Lock()
_offset = 0.0        # seconds to add to the PC clock to get true UTC
_verified = False
_tz = None           # chosen business timezone key

# Reliable hosts whose HTTP Date header is accurate; tried in order.
_TIME_HOSTS = ("https://www.google.com", "https://www.cloudflare.com",
               "https://data.transportation.gov")

# US business zones the admin can choose: key -> (standard UTC offset hrs, observes DST)
US_ZONES = {
    "America/New_York":    (-5, True),    # Eastern
    "America/Chicago":     (-6, True),    # Central
    "America/Denver":      (-7, True),    # Mountain
    "America/Phoenix":     (-7, False),   # Arizona (no DST)
    "America/Los_Angeles": (-8, True),    # Pacific
    "America/Anchorage":   (-9, True),    # Alaska
    "Pacific/Honolulu":    (-10, False),  # Hawaii (no DST)
}
ZONE_LABELS = {
    "America/New_York":    "Eastern (ET)",
    "America/Chicago":     "Central (CT)",
    "America/Denver":      "Mountain (MT)",
    "America/Phoenix":     "Arizona (MST, no DST)",
    "America/Los_Angeles": "Pacific (PT)",
    "America/Anchorage":   "Alaska (AKT)",
    "Pacific/Honolulu":    "Hawaii (HST, no DST)",
}
DEFAULT_TZ = "America/New_York"


def set_business_tz(tz):
    global _tz
    if tz in US_ZONES:
        _tz = tz
        return True
    return False


def business_tz():
    return _tz or DEFAULT_TZ


def _nth_sunday(year, month, n):
    d = datetime.date(year, month, 1)
    first = d + datetime.timedelta(days=(6 - d.weekday()) % 7)
    return first + datetime.timedelta(days=7 * (n - 1))


def _us_dst_active(local_std):
    """local_std: naive datetime in the zone's STANDARD time. US DST runs from the
    2nd Sunday of March 02:00 to the 1st Sunday of November 02:00."""
    y = local_std.year
    start = datetime.datetime.combine(_nth_sunday(y, 3, 2), datetime.time(2))
    end = datetime.datetime.combine(_nth_sunday(y, 11, 1), datetime.time(2))
    return start <= local_std < end


def _offset_hours(tz, u_naive_utc):
    std, dst = US_ZONES.get(tz, US_ZONES[DEFAULT_TZ])
    if dst and _us_dst_active(u_naive_utc + datetime.timedelta(hours=std)):
        return std + 1
    return std


def _true_epoch():
    with _lock:
        return time.time() + _offset


def now_utc():
    """Naive UTC datetime, corrected to true time."""
    return datetime.datetime.fromtimestamp(
        _true_epoch(), datetime.timezone.utc).replace(tzinfo=None)


def biz_now():
    """Naive wall-clock datetime in the business timezone."""
    u = now_utc()
    return u + datetime.timedelta(hours=_offset_hours(business_tz(), u))


def now_str():
    return biz_now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return biz_now().strftime("%Y-%m-%d")


def today():
    return biz_now().date()


def info():
    """For the frontend: true UTC in ms, the business tz, and whether verified."""
    return {"utc_ms": int(_true_epoch() * 1000),
            "tz": business_tz(),
            "verified": _verified}


def _fetch_true_epoch():
    for host in _TIME_HOSTS:
        try:
            req = urllib.request.Request(host, method="HEAD", headers={"User-Agent": "LeadHub"})
            with urllib.request.urlopen(req, timeout=8) as r:
                d = r.headers.get("Date")
            if d:
                return parsedate_to_datetime(d).timestamp()
        except Exception:
            continue
    return None


def sync():
    """Refresh the offset from the internet. Returns True if verified."""
    global _offset, _verified
    epoch = _fetch_true_epoch()
    if epoch is not None:
        with _lock:
            _offset = epoch - time.time()
            _verified = True
    return _verified


def start_background(interval=3600):
    def loop():
        while True:
            try:
                sync()
            except Exception:
                pass
            time.sleep(interval)
    threading.Thread(target=loop, daemon=True).start()
