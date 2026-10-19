"""Rich TUI for the budget app.  python3 tui.py

Three tabs, mirroring the original spec:
  1 输入   one command line, nothing else on screen
  2 总览   the selected day, switchable, plus the monthly report
  3 浏览   one day at a time: edit / tag / delete, and manage profiles
"""
import datetime as dt
import os
import sys

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import actions
import db
import recurrence

console = Console()

WEEK_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
TAB_NAMES = ["输入", "总览", "浏览"]

HELP_LINES = [
    ("35 午饭 #餐饮 @同事", "记一笔支出"),
    ("昨天 42 打车 #交通", "指定日期"),
    ("b 50 每周三 12:00 咖啡 #餐饮", "建预算（周期可自定义）"),
    ("b 1200 每月1号 房租 #生活", "月度预算"),
    ("b 30 每2天 早餐", "间隔型预算"),
    ("p 咖啡 18 #餐饮 12:00", "建行动模板"),
    ("tag 12 +报销 -待定", "改标签"),
]


def money(v):
    v = float(v or 0)
    return ("-¥%.2f" % abs(v)) if v < 0 else ("¥%.2f" % v)


def fmt_date(iso):
    d = dt.date.fromisoformat(iso)
    return "%s %s" % (iso, WEEK_CN[d.weekday()])


def shift(iso, days):
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=days)).isoformat()


# ------------------------------------------------------------------- views
def header(state):
    tabs = Text()
    for i, name in enumerate(TAB_NAMES):
        active = i == state["tab"]
        tabs.append("  %s  " % name, style="bold reverse" if active else "dim")
        tabs.append(" ")
    right = Text(state["today"], style="dim")
    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="right")
    grid.add_row(Text("budget", style="bold cyan") + Text("  "), right)
    return Panel(
        Group(grid, Text(""), tabs),
        border_style="grey30", box=box.ROUNDED, padding=(0, 1),
    )


def footer(state, msg=""):
    if msg:
        style = "red" if state.get("msg_kind") == "err" else "green"
        return Text(msg, style=style)
    hints = {
        0: "Enter 执行  ·  Tab 切换  ·  ?  帮助  ·  q 退出",
        1: "← → 切换日期  ·  m 月度报表  ·  Tab 切换  ·  q 退出",
        2: "← → 切换日期  ·  e 改金额  ·  t 打标签  ·  d 删除  ·  p 建模板  ·  q 退出",
    }[state["tab"]]
    return Text(hints, style="dim")


def view_input(state, conn):
    hint = Text()
    for label in ("b 预算", "p 模板", "昨天 改日期", "# 分类", "@ 标签", "? 帮助"):
        hint.append("   %s   " % label, style="dim")
    return Group(
        Text(""),
        Text("   › ", style="bold cyan")
        + Text("35 午饭 #餐饮 @同事", style="grey42"),
        Text(""),
        hint,
        Text(""),
    )


def view_overview(state, conn):
    day = actions.day_view(conn, state["ov_date"])
    blocks = []

    stats = Table.grid(expand=True, padding=(0, 2))
    stats.add_column(justify="left")
    stats.add_column(justify="left")
    stats.add_column(justify="left")
    stats.add_row(
        Text.assemble(("当天支出\n", "dim"), (money(day["total"]), "bold")),
        Text.assemble(("预算计划\n", "dim"), (money(day["planned_total"]), "dim")),
        Text.assemble(("笔数\n", "dim"), (str(len(day["records"])), "dim")),
    )
    blocks.append(stats)

    # category breakdown
    by_cat = {}
    for r in day["records"]:
        key = r.get("category_name") or "未分类"
        by_cat[key] = by_cat.get(key, 0) + r["amount"]
    if by_cat:
        t = Table(box=box.SIMPLE, expand=True, show_header=True, header_style="dim")
        t.add_column("分类", width=12)
        t.add_column("金额", justify="right", width=12)
        t.add_column("占比", width=24)
        total = sum(by_cat.values()) or 1
        for name, amt in sorted(by_cat.items(), key=lambda x: -x[1]):
            bar = "█" * max(1, int(amt / total * 20))
            t.add_row(name, money(amt), Text(bar, style="cyan"))
        blocks.append(t)
    else:
        blocks.append(Text("  这一天没有支出记录", style="dim italic"))

    # planned budgets for the day
    if day["planned"]:
        t = Table(box=box.SIMPLE, expand=True, header_style="dim")
        t.add_column("今日计划", width=16)
        t.add_column("周期", width=16)
        t.add_column("金额", justify="right", width=12)
        t.add_column("状态", width=6)
        for p in day["planned"]:
            t.add_row(p["title"], p["rule_text"], money(p["amount"]),
                      Text("已记", style="green") if p["done"] else Text("未记", style="yellow"))
        blocks.append(t)
    else:
        blocks.append(Text("  今天没有预算计划", style="dim italic"))

    return Panel(
        Group(*blocks),
        title="[bold]总览[/]  " + fmt_date(state["ov_date"]),
        subtitle="m 查看月度报表", border_style="grey30", box=box.ROUNDED, padding=(1, 2),
    )


def view_browse(state, conn):
    day = actions.day_view(conn, state["br_date"])
    body = []

    if day["records"]:
        t = Table(box=box.SIMPLE, expand=True, header_style="dim")
        t.add_column("#", width=4, justify="right")
        t.add_column("金额", width=11, justify="right")
        t.add_column("备注", ratio=2)
        t.add_column("分类", width=10)
        t.add_column("标签", ratio=1)
        t.add_column("时间", width=6)
        for i, r in enumerate(day["records"]):
            sel = i == state["br_sel"]
            style = "reverse" if sel else ""
            t.add_row(
                str(r["id"]), money(r["amount"]), r["note"] or "—",
                r.get("category_name") or "未分类",
                " ".join("@" + x for x in r["tags"]) or "",
                r["time"] or "",
                style=style,
            )
        body.append(t)
    else:
        body.append(Text("  这一天还没有记录", style="dim italic"))

    if state["profiles"]:
        p = Text()
        p.append("\n行动模板  ", style="dim")
        for prof in state["profiles"]:
            p.append(" %s " % prof["name"], style="cyan")
            if prof.get("default_amount"):
                p.append(money(prof["default_amount"]) + " ", style="dim")
        body.append(p)
    else:
        body.append(Text("\n  还没有模板，按 p 创建", style="dim italic"))

    return Panel(
        Group(*body),
        title="[bold]浏览[/]  " + fmt_date(state["br_date"]),
        subtitle="e 改金额 · t 标签 · d 删除 · p 模板",
        border_style="grey30", box=box.ROUNDED, padding=(1, 2),
    )


def view_report(state, conn):
    rep = actions.month_report(conn, state["month"])
    blocks = []

    s = Table.grid(expand=True, padding=(0, 2))
    for _ in range(4):
        s.add_column(justify="left")
    diff = rep["actual_total"] - rep["planned_total"]
    s.add_row(
        Text.assemble(("总支出\n", "dim"), (money(rep["spent_total"]), "bold")),
        Text.assemble(("预算计划\n", "dim"), (money(rep["planned_total"]), "")),
        Text.assemble(("预算内实际\n", "dim"), (money(rep["actual_total"]), "")),
        Text.assemble(("差额\n", "dim"),
                      (("+" if diff > 0 else "") + money(diff),
                       "red" if diff > 0 else "green")),
    )
    blocks.append(s)

    if rep["budgets"]:
        t = Table(box=box.SIMPLE, expand=True, header_style="dim")
        t.add_column("预算", width=14)
        t.add_column("周期", width=18)
        t.add_column("次数", width=5, justify="right")
        t.add_column("计划", width=11, justify="right")
        t.add_column("实际", width=11, justify="right")
        t.add_column("进度", width=14)
        for b in rep["budgets"]:
            ratio = b["actual"] / b["planned"] if b["planned"] else 0
            bar = "█" * max(0, min(10, int(ratio * 10)))
            color = "red" if b["actual"] > b["planned"] else "green"
            t.add_row(
                b["title"], b["rule_text"], str(b["count"]),
                money(b["planned"]), money(b["actual"]),
                Text(bar, style=color),
            )
        blocks.append(t)
    else:
        blocks.append(Text("  这个月还没有预算", style="dim italic"))

    if rep["by_category"]:
        c = Text("\n分类合计  ", style="dim")
        for cat in rep["by_category"]:
            c.append(" %s %s " % (cat["name"], money(cat["total"])), style="cyan")
        blocks.append(c)

    return Panel(
        Group(*blocks),
        title="[bold]月度报表[/]  " + state["month"],
        subtitle="← → 换月 · Esc 返回",
        border_style="grey30", box=box.ROUNDED, padding=(1, 2),
    )


def help_panel():
    t = Table(box=box.SIMPLE, show_header=False, expand=True)
    t.add_column("命令", style="cyan", width=34)
    t.add_column("说明", style="dim")
    for cmd, desc in HELP_LINES:
        t.add_row(cmd, desc)
    extra = Text(
        "\n周期写法：每天 / 每周三 / 每周一,三 / 每2天 / 每3周 / 每月1号 / 每年3月1号\n"
        "时间写法：12:00 / 中午 / 晚上",
        style="dim",
    )
    return Panel(Group(t, extra), title="[bold]帮助[/]",
                 border_style="cyan", box=box.ROUNDED, padding=(1, 2))


# -------------------------------------------------------------- rendering
def render(state, conn):
    if state["mode"] == "help":
        console.clear()
        console.print(header(state))
        console.print(help_panel())
        console.print(footer(state, "任意键返回"))
        return

    console.clear()
    console.print(header(state))
    if state["mode"] == "report":
        console.print(view_report(state, conn))
        console.print(footer(state))
        return

    view = [view_input, view_overview, view_browse][state["tab"]]
    console.print(view(state, conn))
    console.print(footer(state, state.get("message", "")))


# ------------------------------------------------------------------- input
def read_key():
    """Read one keypress (or a whole line on Enter). Returns (kind, value)."""
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            seq = sys.stdin.read(2)
            if seq == "[A":
                return ("up", "")
            if seq == "[B":
                return ("down", "")
            if seq == "[C":
                return ("right", "")
            if seq == "[D":
                return ("left", "")
            return ("esc", "")
        return ("char", ch)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_line(prompt="› ", default=""):
    """Cooked-mode line input. Ctrl-C / Esc(empty) cancels."""
    try:
        return console.input("  [bold cyan]%s[/] " % prompt, default=default)
    except (EOFError, KeyboardInterrupt):
        return None


def ask(question, default=""):
    ans = read_line(question, default)
    return None if ans is None else ans


# -------------------------------------------------------------------- main
def main():
    db.init()
    conn = db.connect()
    state = {
        "tab": 0,
        "mode": "normal",
        "today": dt.date.today().isoformat(),
        "ov_date": dt.date.today().isoformat(),
        "br_date": dt.date.today().isoformat(),
        "month": dt.date.today().strftime("%Y-%m"),
        "br_sel": 0,
        "buffer": "",
        "message": "",
        "msg_kind": "ok",
        "profiles": [],
    }

    def refresh():
        state["profiles"] = db.list_profiles(conn)

    refresh()

    try:
        while True:
            state["message"] = ""
            render(state, conn)
            kind, val = read_key()

            if state["mode"] == "help":
                state["mode"] = "normal"
                continue

            if state["mode"] == "report":
                if kind == "left":
                    y, m = (int(x) for x in state["month"].split("-"))
                    state["month"] = (dt.date(y, m, 1) - dt.timedelta(days=1)).strftime("%Y-%m")
                elif kind == "right":
                    y, m = (int(x) for x in state["month"].split("-"))
                    state["month"] = (dt.date(y, m, 28) + dt.timedelta(days=7)).strftime("%Y-%m")
                elif kind in ("esc", "char"):
                    state["mode"] = "normal"
                continue

            if kind == "char" and val in ("q", "\x03"):
                break
            if kind == "char" and val == "\t":
                state["tab"] = (state["tab"] + 1) % 3
                continue
            if kind == "char" and val == "?":
                state["mode"] = "help"
                continue

            if state["tab"] == 0:
                # any keypress on the input tab opens the command line
                if kind == "char" and val >= " ":
                    text = read_line("›")
                    if text and text.strip():
                        try:
                            res = actions.run_command(
                                conn, text.strip(),
                                today=dt.date.fromisoformat(state["today"]))
                            state["message"], state["msg_kind"] = describe(res), "ok"
                            refresh()
                        except actions.ActionError as e:
                            state["message"], state["msg_kind"] = "✗ " + e.message, "err"
                continue

            if state["tab"] == 1:
                if kind == "left":
                    state["ov_date"] = shift(state["ov_date"], -1)
                elif kind == "right":
                    state["ov_date"] = shift(state["ov_date"], 1)
                elif kind == "char" and val == "m":
                    state["mode"] = "report"
                elif kind == "char" and val == "t":
                    state["ov_date"] = state["today"]
                continue

            if state["tab"] == 2:
                day = actions.day_view(conn, state["br_date"])
                n = len(day["records"])
                if kind == "left":
                    state["br_date"] = shift(state["br_date"], -1)
                    state["br_sel"] = 0
                elif kind == "right":
                    state["br_date"] = shift(state["br_date"], 1)
                    state["br_sel"] = 0
                elif kind == "up":
                    state["br_sel"] = max(0, state["br_sel"] - 1)
                elif kind == "down":
                    state["br_sel"] = min(max(0, n - 1), state["br_sel"] + 1)
                elif kind == "char" and val == "t":
                    state["br_date"] = state["today"]
                    state["br_sel"] = 0
                elif kind == "char" and val in ("e", "d", "g") and n:
                    rec = day["records"][state["br_sel"]]
                    if val == "d":
                        db.delete_record(conn, rec["id"])
                        state["message"] = "已删除 #%s" % rec["id"]
                        state["br_sel"] = max(0, state["br_sel"] - 1)
                    elif val == "e":
                        ans = ask("新金额", str(rec["amount"]))
                        if ans:
                            try:
                                actions.update_record(conn, rec["id"], {"amount": float(ans)})
                                state["message"] = "已更新金额"
                            except (ValueError, actions.ActionError) as e:
                                state["message"], state["msg_kind"] = "✗ " + str(e), "err"
                    elif val == "g":
                        ans = ask("标签（逗号分隔，留空清空）",
                                  ",".join(rec["tags"]))
                        tags = [x.strip() for x in ans.replace("，", ",").split(",") if x.strip()]
                        actions.update_record(conn, rec["id"], {"tags": tags})
                        state["message"] = "标签已更新"
                elif kind == "char" and val == "p":
                    name = ask("模板名")
                    if name:
                        amt = ask("默认金额", "")
                        cat = ask("分类", "")
                        try:
                            actions.create_profile(conn, {
                                "name": name,
                                "default_amount": float(amt) if amt else None,
                                "category": cat or None,
                            })
                            state["message"] = "模板已创建"
                            refresh()
                        except (ValueError, actions.ActionError) as e:
                            state["message"], state["msg_kind"] = "✗ " + str(e), "err"
                continue
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()
        console.clear()
        console.print("[dim]bye[/]")


def describe(res):
    kind = res["kind"]
    if kind == "record":
        r = res["record"]
        bits = ["✓ %s %s" % (money(r["amount"]), r["note"])]
        if r.get("category_name"):
            bits.append("#" + r["category_name"])
        if r["tags"]:
            bits.append(" ".join("@" + t for t in r["tags"]))
        return "  ".join(bits)
    if kind == "budget":
        return "✓ 预算「%s」%s · %s" % (res["budget"]["title"],
                                     money(res["budget"]["amount"]), res["rule_text"])
    if kind == "profile":
        return "✓ 模板「%s」已创建" % res["profile"]["name"]
    if kind == "tag":
        return "✓ 记录 %s 标签：%s" % (res["record"]["id"],
                                    ", ".join(res["record"]["tags"]) or "（无）")
    return "✓ 完成"


if __name__ == "__main__":
    main()
