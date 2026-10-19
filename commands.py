"""Command interpreter: every user action is a command string.

The TUI does not contain action logic. Bare keys never trigger actions; they
only feed the command buffer. Modifier keys (Ctrl+X) and non-letter keys
(arrows, Tab, Enter, Esc) are the only shortcuts, and each shortcut simply
invokes one of the commands defined here.

`execute()` mutates the shared `state` dict for view/navigation verbs and
delegates all data mutations to `actions`.
"""
import datetime as dt
import re

import actions
import db
import fmt

# View state keys (shared with the TUI):
#   date   ISO date currently shown
#   tab    0 input / 1 overview / 2 browse
#   mode   normal | report | help
#   month  YYYY-MM used by the report
#   sel    selected row index in the browse view

# Help content: scenarios, not a command dump. Each entry answers
#   1. when you would reach for it,
#   2. the exact command,
#   3. why the command looks the way it does.
SCENARIOS = [
    {
        "title": "记一笔日常开销",
        "when": "刚付完钱，想立刻记下来。",
        "cmds": ["35 午饭 #餐饮 @同事"],
        "why": "金额打头，因为这是你唯一一定要填的东西；其余按「想到什么写什么」的顺序拼，"
               "顺序无关。# 是分类，@ 是标签，分类进报表，标签进检索。",
    },
    {
        "title": "补记前几天的账",
        "when": "昨天忘了记，今天才想起来。",
        "cmds": ["昨天 42 打车 #交通", "前天 18 早餐", "-3d 60 加油"],
        "why": "日期词放在最前面。它修饰的是「哪一天」，而不是某一笔的属性，"
               "所以语法上必须先说清楚语境，后面的数字才不会歧义。",
    },
    {
        "title": "同一笔开销反复发生",
        "when": "每周三中午都买咖啡，不想每次都敲一遍。",
        "cmds": ["p 咖啡 18 #餐饮 12:00", "use 咖啡", "use 咖啡 22"],
        "why": "p 建模板，use 用模板。模板存的是「常量和默认值」，"
               "use 时还能覆盖金额——因为价格会变，但你不该为此重建模板。",
    },
    {
        "title": "给某类开销定个计划",
        "when": "想约束每周咖啡花多少、每月房租多少。",
        "cmds": ["b 50 每周三 12:00 咖啡 #餐饮", "b 1200 每月1号 房租 #生活",
                 "b 30 每2天 早餐"],
        "why": "b 后面紧跟金额和周期，标题和分类放最后。周期是自然语言"
               "（每天/每周三/每2天/每月1号），因为记账时人不该换算成 cron 表达式。",
    },
    {
        "title": "查看某天的花销",
        "when": "想确认今天/某天花了多少、花在哪。",
        "cmds": ["day 昨天", "day 2026-10-21", "next", "prev", "today"],
        "why": "day 跳到指定日期，next/prev 相对移动，today 兜底。"
               "三个动词分别覆盖「绝对定位」「相对移动」「归位」三种意图，"
               "比让一个键兼做三件事更不容易按错。",
    },
    {
        "title": "看这个月预算执行得怎么样",
        "when": "月底了，想知道哪项超支。",
        "cmds": ["report", "month 2026-09", "next", "prev"],
        "why": "report 打开报表，next/prev 在报表里自动变成换月——"
               "同一个键在不同语境下含义随上下文变化，因为「前后」这个意图是稳定的，"
               "变的是被移动的粒度。",
    },
    {
        "title": "改一条记错的记录",
        "when": "金额敲错、分类选错、忘了打标签。",
        "cmds": ["edit 12 42", "note 12 改备注", "cat 12 交通",
                 "tag 12 +报销 -待定", "tags 12 报销,同事"],
        "why": "每条命令都是「动词 + id + 新值」，因为修改必须先定位。"
               "tag 做增量（+/-），tags 做整体替换——两种意图分开，"
               "避免「我只想加一个标签，结果把其他的冲掉了」。",
    },
    {
        "title": "删掉不要的东西",
        "when": "重复记录、废弃的预算或模板。",
        "cmds": ["del 12", "bdel 2", "dprofile 3"],
        "why": "三个删除动词分开，而不是 del <类型> <id>。"
               "因为误删的代价不对称——记录删了能补，预算删了整月报表就变了，"
               "多打几个字母换一次确认是划算的。",
    },
    {
        "title": "在页面之间跳转",
        "when": "从输入切到总览，或回到浏览页。",
        "cmds": ["tab", "tab overview", "tab browse", "back"],
        "why": "tab 不带参数就是循环切页，带参数是直达。"
               "back 只做一件事：从报表/帮助退回正常视图。",
    },
]

# Flat command reference, kept for the TUI's compact key hints.
VERBS = [
    ("35 午饭 #餐饮 @同事", "记一笔支出"),
    ("昨天 42 打车 #交通", "指定日期记账"),
    ("b 50 每周三 12:00 咖啡 #餐饮", "建预算（周期自定义）"),
    ("b 30 每2天 早餐", "间隔型预算"),
    ("p 咖啡 18 #餐饮 12:00", "建行动模板"),
    ("use 咖啡 | use 咖啡 20", "用模板记一笔（可覆盖金额）"),
    ("tag 12 +报销 -待定", "改记录标签"),
    ("", ""),
    ("day 昨天 | day 2026-10-21", "跳到某一天"),
    ("next | prev", "前后一天（报表里是月）"),
    ("today | 今天", "回到今天"),
    ("report | 报表", "月度报表"),
    ("month 2026-09", "指定报表月份"),
    ("tab input|overview|browse", "切换页面"),
    ("back", "退出报表/帮助"),
    ("edit 12 50", "改记录金额"),
    ("note 12 备注文字", "改记录备注"),
    ("cat 12 餐饮", "改记录分类"),
    ("tags 12 报销,待定", "整体设置标签（留空即清空）"),
    ("del 12", "删除记录"),
    ("dprofile 3", "删除模板"),
    ("bdel 2", "删除预算"),
    ("?", "显示本帮助"),
    ("q | quit", "退出"),
]

TAB_ALIASES = {
    "input": 0, "in": 0, "输入": 0,
    "overview": 1, "ov": 1, "总览": 1,
    "browse": 2, "br": 2, "浏览": 2,
}

DAY_WORDS = {"今天": 0, "今日": 0, "today": 0,
             "昨天": -1, "昨日": -1, "yesterday": -1,
             "前天": -2}


def _today(state):
    return dt.date.fromisoformat(state["today"])


def _parse_day(token, state):
    """Resolve a date token to an ISO date string."""
    token = token.strip()
    if token in DAY_WORDS:
        return ( _today(state) + dt.timedelta(days=DAY_WORDS[token]) ).isoformat()
    m = re.match(r"^([+-]?\d+)d?$", token)
    if m:
        return ( _today(state) + dt.timedelta(days=int(m.group(1))) ).isoformat()
    try:
        return dt.date.fromisoformat(token).isoformat()
    except ValueError:
        raise actions.ActionError("看不懂日期：%s" % token)


def _need_int(token, what="id"):
    if not token.isdigit():
        raise actions.ActionError("%s 必须是数字" % what)
    return int(token)


def execute(conn, text, state):
    """Run one command. Returns (kind, message).

    kind: ok | err | help | quit | noop
    """
    text = (text or "").strip()
    if not text:
        return ("noop", "")

    parts = text.split()
    verb = parts[0].lower()
    args = parts[1:]

    # ---------------------------------------------------------- meta verbs
    if verb in ("?", "help", "h", "帮助"):
        state["mode"] = "help"
        return ("help", "")

    if verb in ("q", "quit", "exit", "退出"):
        return ("quit", "")

    if verb in ("back", "esc", "返回", "取消"):
        state["mode"] = "normal"
        return ("ok", "")

    # ------------------------------------------------------ navigation
    if verb in ("today", "今天", "t"):
        state["date"] = state["today"]
        state["sel"] = 0
        return ("ok", "回到今天 %s" % fmt.fmt_date(state["date"]))

    if verb in ("next", "n", ">"):
        return _step(state, +1)

    if verb in ("prev", "p<", "<", "-"):
        return _step(state, -1)

    if verb in ("day", "date"):
        if not args:
            return ("ok", fmt.fmt_date(state["date"]))
        state["date"] = _parse_day(args[0], state)
        state["sel"] = 0
        return ("ok", fmt.fmt_date(state["date"]))

    if verb in ("report", "报表", "m"):
        state["mode"] = "report"
        return ("ok", "")

    if verb in ("month",):
        if not args:
            state["mode"] = "report"
            return ("ok", "")
        state["month"] = actions.month_arg(args[0])
        state["mode"] = "report"
        return ("ok", "报表月份 %s" % state["month"])

    if verb in ("tab", "view"):
        if not args:
            state["tab"] = (state["tab"] + 1) % 3
            return ("ok", "")
        key = args[0].lower()
        if key not in TAB_ALIASES:
            raise actions.ActionError("未知页面：%s（可用 input/overview/browse）" % args[0])
        state["tab"] = TAB_ALIASES[key]
        return ("ok", "")

    # ------------------------------------------------- record mutations
    if verb in ("edit", "e"):
        if len(args) < 2:
            raise actions.ActionError("用法：edit <id> <金额>")
        rid = _need_int(args[0])
        try:
            amount = float(args[1])
        except ValueError:
            raise actions.ActionError("金额无效：%s" % args[1])
        rec = actions.update_record(conn, rid, {"amount": amount})
        return ("ok", "✓ 记录 %s 金额改为 %s" % (rec["id"], fmt.money(rec["amount"])))

    if verb in ("note",):
        if len(args) < 2:
            raise actions.ActionError("用法：note <id> <文字>")
        rid = _need_int(args[0])
        rec = actions.update_record(conn, rid, {"note": " ".join(args[1:])})
        return ("ok", "✓ 记录 %s 备注改为「%s」" % (rec["id"], rec["note"]))

    if verb in ("cat", "分类"):
        if len(args) < 2:
            raise actions.ActionError("用法：cat <id> <分类>")
        rid = _need_int(args[0])
        rec = actions.update_record(conn, rid, {"category": args[1]})
        return ("ok", "✓ 记录 %s 分类改为 %s" % (rec["id"], rec.get("category_name") or "未分类"))

    if verb in ("use", "用"):
        if not args:
            names = ", ".join(p["name"] for p in db.list_profiles(conn)) or "（还没有模板）"
            raise actions.ActionError("用法：use <模板名> [金额]。可用：" + names)
        name = args[0]
        profile = next((p for p in db.list_profiles(conn) if p["name"] == name), None)
        if not profile:
            raise actions.ActionError("找不到模板：%s" % name, 404)
        amount = profile.get("default_amount")
        if len(args) > 1:
            try:
                amount = float(args[1])
            except ValueError:
                raise actions.ActionError("金额无效：%s" % args[1])
        if not amount:
            raise actions.ActionError("模板「%s」没有默认金额，请补一个：use %s <金额>" % (name, name))
        rec = actions.create_record(conn, {
            "date": state["date"],
            "amount": amount,
            "note": profile["name"],
            "category": profile.get("category_name"),
            "tags": profile.get("default_tags") or [],
            "time": profile.get("default_time"),
        })
        return ("ok", "✓ 用模板记下 %s %s" % (fmt.money(rec["amount"]), rec["note"]))

    if verb in ("tags", "标签"):
        if len(args) < 1:
            raise actions.ActionError("用法：tags <id> [标签1,标签2]")
        rid = _need_int(args[0])
        raw = " ".join(args[1:]).replace("，", ",")
        tags = [t.strip() for t in raw.split(",") if t.strip()]
        rec = actions.update_record(conn, rid, {"tags": tags})
        return ("ok", "✓ 记录 %s 标签：%s" % (rec["id"], ", ".join(rec["tags"]) or "（已清空）"))

    if verb in ("del", "rm", "删除"):
        if not args:
            raise actions.ActionError("用法：del <id>")
        rid = _need_int(args[0])
        if not db.get_record(conn, rid):
            raise actions.ActionError("找不到记录 %s" % rid, 404)
        db.delete_record(conn, rid)
        return ("ok", "✓ 已删除记录 %s" % rid)

    if verb in ("dprofile", "delprofile"):
        if not args:
            raise actions.ActionError("用法：dprofile <id>")
        pid = _need_int(args[0])
        if not db.get_profile(conn, pid):
            raise actions.ActionError("找不到模板 %s" % pid, 404)
        db.delete_profile(conn, pid)
        return ("ok", "✓ 已删除模板 %s" % pid)

    if verb in ("bdel", "delbudget"):
        if not args:
            raise actions.ActionError("用法：bdel <id>")
        bid = _need_int(args[0])
        if not db.get_budget(conn, bid):
            raise actions.ActionError("找不到预算 %s" % bid, 404)
        db.delete_budget(conn, bid)
        return ("ok", "✓ 已删除预算 %s" % bid)

    # ------------------------------- record / budget / profile / tag syntax
    res = actions.run_command(conn, text, today=_today(state))
    return ("ok", describe(res))


def _step(state, direction):
    if state["mode"] == "report":
        state["month"] = fmt.shift_month(state["month"], direction)
        return ("ok", "报表月份 %s" % state["month"])
    state["date"] = fmt.shift(state["date"], direction)
    state["sel"] = 0
    return ("ok", fmt.fmt_date(state["date"]))


def describe(res):
    kind = res["kind"]
    if kind == "record":
        r = res["record"]
        bits = ["✓ %s %s" % (fmt.money(r["amount"]), r["note"])]
        if r.get("category_name"):
            bits.append("#" + r["category_name"])
        if r["tags"]:
            bits.append(" ".join("@" + t for t in r["tags"]))
        return "  ".join(bits)
    if kind == "budget":
        return "✓ 预算「%s」%s · %s" % (res["budget"]["title"],
                                     fmt.money(res["budget"]["amount"]), res["rule_text"])
    if kind == "profile":
        return "✓ 模板「%s」已创建" % res["profile"]["name"]
    if kind == "tag":
        return "✓ 记录 %s 标签：%s" % (res["record"]["id"],
                                    ", ".join(res["record"]["tags"]) or "（无）")
    return "✓ 完成"
