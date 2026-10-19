"""Recurrence engine + monthly report.

A budget stores a JSON `rule`; occurrences are computed on the fly (never
materialised), so a budget and the real records can never drift apart.

Supported rules
---------------
{"freq": "daily",   "time": "12:00"}
{"freq": "weekly",  "weekdays": [0,2,4], "time": "12:00"}   # 0 = Monday
{"freq": "monthly", "days": [1,15], "time": "12:00"}
{"freq": "yearly",  "month": 3, "days": [1], "time": "12:00"}
{"freq": "interval","every": 3, "unit": "day"|"week"|"month", "time": "12:00"}
"""
import calendar
import datetime as dt

MAX_OCCURRENCES = 5000


def parse_date(s):
    return dt.date.fromisoformat(str(s)[:10])


def _month_add(d, months):
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return dt.date(y, m, day)


def _rule_bounds(rule):
    """Return (anchor_date, every_n, unit) for interval-style rules."""
    unit = rule.get("unit") or "day"
    every = max(1, int(rule.get("every") or 1))
    return unit, every


def occurs_on(rule, day, start_date):
    """Does `rule` fire on `day`? Pure function, no DB."""
    if day < start_date:
        return False
    freq = (rule.get("freq") or "weekly").lower()

    if freq == "daily":
        return True

    if freq == "weekly":
        weekdays = rule.get("weekdays") or []
        if not weekdays:
            weekdays = [start_date.weekday()]
        return day.weekday() in [int(w) for w in weekdays]

    if freq == "monthly":
        days = rule.get("days") or [start_date.day]
        days = [int(x) for x in days]
        last = calendar.monthrange(day.year, day.month)[1]
        # day 31 in a 30-day month falls back to the last day
        targets = {min(d, last) for d in days}
        return day.day in targets

    if freq == "yearly":
        month = int(rule.get("month") or start_date.month)
        days = rule.get("days") or [start_date.day]
        if day.month != month:
            return False
        last = calendar.monthrange(day.year, day.month)[1]
        return day.day in {min(int(d), last) for d in days}

    if freq == "interval":
        unit, every = _rule_bounds(rule)
        if unit == "day":
            return (day - start_date).days % every == 0
        if unit == "week":
            return (day - start_date).days % (7 * every) == 0
        if unit == "month":
            months = (day.year - start_date.year) * 12 + (day.month - start_date.month)
            return months >= 0 and months % every == 0 and day.day == min(
                start_date.day, calendar.monthrange(day.year, day.month)[1])
        return False

    return False


def occurrences(budget, start, end):
    """List of dates in [start, end] where this budget fires."""
    rule = budget.get("rule") or {}
    b_start = parse_date(budget["start_date"])
    b_end = parse_date(budget["end_date"]) if budget.get("end_date") else None
    if not budget.get("active", 1):
        return []

    lo = max(parse_date(start), b_start)
    hi = parse_date(end)
    if b_end:
        hi = min(hi, b_end)
    if lo > hi:
        return []

    out = []
    day = lo
    guard = 0
    while day <= hi and guard < MAX_OCCURRENCES:
        if occurs_on(rule, day, b_start):
            out.append(day.isoformat())
        day += dt.timedelta(days=1)
        guard += 1
    return out


def rule_text(rule):
    """Human readable Chinese description of a rule."""
    rule = rule or {}
    freq = (rule.get("freq") or "weekly").lower()
    t = rule.get("time")
    suffix = (" " + t) if t else ""
    cn = "一二三四五六日"
    if freq == "daily":
        return "每天" + suffix
    if freq == "weekly":
        wd = sorted(int(w) for w in (rule.get("weekdays") or []))
        if not wd:
            return "每周" + suffix
        names = "、".join("周" + cn[w % 7] for w in wd)
        return "每" + names + suffix
    if freq == "monthly":
        ds = sorted(int(d) for d in (rule.get("days") or []))
        return "每月 " + "、".join("%d 号" % d for d in ds) + suffix
    if freq == "yearly":
        ds = sorted(int(d) for d in (rule.get("days") or []))
        return "每年 %d 月 %s" % (int(rule.get("month") or 1),
                                 "、".join("%d 号" % d for d in ds)) + suffix
    if freq == "interval":
        unit, every = _rule_bounds(rule)
        unit_cn = {"day": "天", "week": "周", "month": "个月"}.get(unit, unit)
        return "每 %d %s%s" % (every, unit_cn, suffix)
    return "未知规则"


def budget_month_report(conn, month, list_records):
    """Planned vs actual for one month ("YYYY-MM").

    Matching: a record counts toward a budget if it falls on an occurrence date
    and matches the budget's category and/or tag (when those are set).
    """
    import db as _db

    year, mon = (int(x) for x in month.split("-"))
    first = dt.date(year, mon, 1)
    last = dt.date(year, mon, calendar.monthrange(year, mon)[1])
    records = list_records(conn, start=first.isoformat(), end=last.isoformat())

    rows = []
    planned_total = 0.0
    actual_total = 0.0

    for b in _db.list_budgets(conn, active_only=False):
        dates = occurrences(b, first, last)
        if not dates:
            continue
        planned = round(b["amount"] * len(dates), 2)

        # Matching semantics (deliberately narrow, so numbers stay trustworthy):
        #   - a record explicitly linked to this budget always counts;
        #   - otherwise auto-match only when the budget declares a category
        #     and/or tag, and the record falls on an occurrence date.
        # A budget with neither is plan-only: it never swallows stray spending.
        has_filter = bool(b.get("category_id") or b.get("tag"))
        matched = []
        for r in records:
            if r.get("budget_id") == b["id"]:
                matched.append(r)
                continue
            if not has_filter or r.get("budget_id"):
                continue
            if b.get("category_id") and r.get("category_id") != b["category_id"]:
                continue
            if b.get("tag") and b["tag"] not in (r.get("tags") or []):
                continue
            if r["date"] in dates:
                matched.append(r)

        actual = round(sum(r["amount"] for r in matched), 2)
        planned_total += planned
        actual_total += actual
        rows.append({
            "budget_id": b["id"],
            "title": b["title"],
            "category_name": b.get("category_name"),
            "category_color": b.get("category_color"),
            "rule_text": rule_text(b["rule"]),
            "unit_amount": b["amount"],
            "count": len(dates),
            "planned": planned,
            "actual": actual,
            "diff": round(actual - planned, 2),
            "matched_count": len(matched),
            "dates": dates,
        })

    by_cat = {}
    for r in records:
        key = r.get("category_name") or "未分类"
        by_cat.setdefault(key, {"name": key, "color": r.get("category_color") or "#8b8b8b",
                                "total": 0.0, "count": 0})
        by_cat[key]["total"] = round(by_cat[key]["total"] + r["amount"], 2)
        by_cat[key]["count"] += 1

    daily = {}
    for r in records:
        daily[r["date"]] = round(daily.get(r["date"], 0.0) + r["amount"], 2)

    return {
        "month": month,
        "from": first.isoformat(),
        "to": last.isoformat(),
        "budgets": rows,
        "planned_total": round(planned_total, 2),
        "actual_total": round(actual_total, 2),
        "spent_total": round(sum(r["amount"] for r in records), 2),
        "record_count": len(records),
        "by_category": sorted(by_cat.values(), key=lambda x: -x["total"]),
        "daily": [{"date": d, "total": daily[d]} for d in sorted(daily)],
        "unbudgeted": round(sum(r["amount"] for r in records) - actual_total, 2),
    }
