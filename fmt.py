"""Shared formatting helpers (imported by both the command layer and the TUI)."""
import datetime as dt

WEEK_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def money(v):
    v = float(v or 0)
    return ("-¥%.2f" % abs(v)) if v < 0 else ("¥%.2f" % v)


def fmt_date(iso):
    d = dt.date.fromisoformat(iso)
    return "%s %s" % (iso, WEEK_CN[d.weekday()])


def shift(iso, days):
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=days)).isoformat()


def shift_month(month, n):
    y, m = (int(x) for x in month.split("-"))
    idx = (y * 12 + (m - 1)) + n
    return "%04d-%02d" % (idx // 12, idx % 12 + 1)
