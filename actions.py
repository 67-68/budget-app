"""Use-case layer: the only place that mutates data.

Both the TUI and any future transport (HTTP, CLI script) call into here, so
validation and side effects live in exactly one place.
"""
import datetime as dt

import db
import parser as cmd_parser
import recurrence


class ActionError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def _amount(payload, key="amount"):
    try:
        v = float(payload[key])
    except (KeyError, TypeError, ValueError):
        raise ActionError("金额无效")
    if v <= 0:
        raise ActionError("金额必须大于 0")
    return v


def _date(payload, key="date", default=None):
    v = payload.get(key) or default
    if not v:
        raise ActionError("缺少日期")
    try:
        dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        raise ActionError("日期格式应为 YYYY-MM-DD")
    return str(v)[:10]


def month_arg(value=None):
    v = value or dt.date.today().strftime("%Y-%m")
    try:
        dt.date.fromisoformat(v + "-01")
    except ValueError:
        raise ActionError("月份格式应为 YYYY-MM")
    return v


# ------------------------------------------------------------------ writes
def create_record(conn, payload):
    cat = db.resolve_category(conn, payload.get("category"))
    return db.create_record(
        conn,
        date=_date(payload),
        amount=_amount(payload),
        note=(payload.get("note") or "").strip(),
        category_id=cat["id"] if cat else None,
        tags=payload.get("tags") or [],
        tm=payload.get("time") or None,
        budget_id=payload.get("budget_id") or None,
    )


def update_record(conn, rid, payload):
    patch = {}
    if "amount" in payload:
        patch["amount"] = _amount(payload)
    if "date" in payload:
        patch["date"] = _date(payload)
    for k in ("note", "tags", "time", "budget_id"):
        if k in payload:
            patch[k] = payload[k]
    if "category" in payload:
        cat = db.resolve_category(conn, payload["category"])
        patch["category_id"] = cat["id"] if cat else None
    rec = db.update_record(conn, rid, patch)
    if not rec:
        raise ActionError("找不到记录", 404)
    return rec


def create_budget(conn, payload):
    rule = payload.get("rule") or {}
    if not isinstance(rule, dict) or not rule.get("freq"):
        raise ActionError("预算规则无效")
    cat = db.resolve_category(conn, payload.get("category"))
    return db.create_budget(
        conn,
        title=(payload.get("title") or "未命名预算").strip(),
        amount=_amount(payload),
        rule=rule,
        start_date=_date(payload, "start_date", dt.date.today().isoformat()),
        category_id=cat["id"] if cat else None,
        tag=(payload.get("tag") or None),
        end_date=payload.get("end_date") or None,
        active=1 if payload.get("active", True) else 0,
    )


def create_profile(conn, payload):
    cat = db.resolve_category(conn, payload.get("category"))
    return db.create_profile(
        conn,
        (payload.get("name") or "未命名").strip(),
        cat["id"] if cat else None,
        payload.get("default_amount") or None,
        payload.get("default_time") or None,
        payload.get("default_tags") or [],
    )


# ----------------------------------------------------------------- commands
def run_command(conn, text, today=None):
    """Parse and execute one command line. Returns a result dict."""
    today = today or dt.date.today()
    result = cmd_parser.parse(text, today=today)
    if not result.get("ok"):
        raise ActionError(result.get("message") or "无法解析")

    kind = result["kind"]
    if kind == "help":
        return result

    if kind == "record":
        rec = create_record(conn, {
            "date": result["date"], "amount": result["amount"], "note": result["note"],
            "category": result["category"], "tags": result["tags"], "time": result["time"],
        })
        return {"kind": "record", "record": rec}

    if kind == "budget":
        b = create_budget(conn, {
            "title": result["title"], "amount": result["amount"], "rule": result["rule"],
            "category": result["category"], "tag": result["tag"],
            "start_date": result["start_date"],
        })
        return {"kind": "budget", "budget": b, "rule_text": recurrence.rule_text(b["rule"])}

    if kind == "profile":
        p = create_profile(conn, {
            "name": result["name"], "default_amount": result.get("default_amount"),
            "category": result.get("category"), "default_time": result.get("default_time"),
            "default_tags": result.get("default_tags"),
        })
        return {"kind": "profile", "profile": p}

    if kind == "tag":
        rec = db.get_record(conn, result["record_id"])
        if not rec:
            raise ActionError("找不到记录 %s" % result["record_id"], 404)
        tags = [t for t in rec["tags"] if t not in result["remove"]]
        for t in result["add"]:
            if t not in tags:
                tags.append(t)
        return {"kind": "tag", "record": db.update_record(conn, rec["id"], {"tags": tags})}

    raise ActionError("未知命令类型")


# -------------------------------------------------------------------- reads
def day_view(conn, date=None):
    date = date or dt.date.today().isoformat()
    try:
        dt.date.fromisoformat(date)
    except ValueError:
        raise ActionError("日期格式应为 YYYY-MM-DD")

    records = db.list_records(conn, date=date)
    planned = []
    for b in db.list_budgets(conn, active_only=True):
        if date in recurrence.occurrences(b, date, date):
            planned.append({
                "budget_id": b["id"], "title": b["title"], "amount": b["amount"],
                "rule_text": recurrence.rule_text(b["rule"]),
                "category_name": b.get("category_name"),
                "category_color": b.get("category_color"),
                "done": any(r.get("budget_id") == b["id"] for r in records),
            })
    return {
        "date": date,
        "records": records,
        "planned": planned,
        "total": round(sum(r["amount"] for r in records), 2),
        "planned_total": round(sum(p["amount"] for p in planned), 2),
    }


def month_report(conn, month=None):
    return recurrence.budget_month_report(conn, month_arg(month), db.list_records)
