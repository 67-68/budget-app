"""Rich TUI for the budget app.  python3 tui.py

Three tabs, mirroring the original spec:
  1 输入   one command line, nothing else on screen
  2 总览   the selected day, switchable, plus the monthly report
  3 浏览   one day at a time: edit / tag / delete, and manage profiles
"""
import datetime as dt
import sys

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import actions
import commands
import db
import fmt

console = Console()

WEEK_CN = fmt.WEEK_CN
TAB_NAMES = ["输入", "总览", "浏览"]
HELP_LINES = commands.VERBS

money = fmt.money
fmt_date = fmt.fmt_date
shift = fmt.shift


# ------------------------------------------------------------------- views
def header(state):
    tabs = Text()
    for i, name in enumerate(TAB_NAMES):
        active = i == state["tab"]
        tabs.append("  %s  " % name, style="bold reverse" if active else "dim")
        tabs.append(" ")
    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="right")
    grid.add_row(
        Text("budget", style="bold cyan"),
        Text(fmt_date(state["date"]) if state["tab"] else state["today"], style="dim"),
    )
    return Panel(Group(grid, Text(""), tabs),
                 border_style="grey30", box=box.ROUNDED, padding=(0, 1))


HINTS = {
    0: "直接输入命令 · Tab 换页 · ↑↓ 历史 · Ctrl+H 帮助 · Ctrl+Q 退出",
    1: "← → 换日 · Tab 换页 · Ctrl+R 报表 · Ctrl+T 今天 · Ctrl+H 帮助",
    2: "← → 换日 · ↑↓ 选中 · Tab 换页 · Ctrl+E 改金额 · Ctrl+G 标签 · Ctrl+D 删除",
}


def footer(state, msg=""):
    if msg:
        style = "red" if state.get("msg_kind") == "err" else "green"
        return Text("  " + msg, style=style)
    return Text("  " + HINTS[state["tab"]], style="dim")


def prompt_line(state):
    """The always-visible command line."""
    buf = state["buffer"]
    return Text.assemble(
        ("  › ", "bold cyan"),
        (buf, "bold" if buf else "grey42"),
        ("" if buf else "  输入命令，? 看帮助", "grey42"),
    )


def view_input(state, conn):
    hint = Text()
    for label in ("b 预算", "p 模板", "day 昨天", "# 分类", "@ 标签", "? 帮助"):
        hint.append("   %s   " % label, style="dim")
    recent = actions.day_view(conn, state["date"])
    recent_line = Text()
    if recent["records"]:
        recent_line.append("  今天 ", style="dim")
        for r in recent["records"][:4]:
            recent_line.append("%s %s  " % (money(r["amount"]), r["note"] or "—"), style="dim")
    return Group(
        Text(""),
        Text(""),
        prompt_line(state),
        Text(""),
        hint,
        Text(""),
        recent_line,
        Text(""),
    )


def view_overview(state, conn):
    day = actions.day_view(conn, state["date"])
    blocks = []

    stats = Table.grid(expand=True, padding=(0, 2))
    for _ in range(3):
        stats.add_column(justify="left")
    stats.add_row(
        Text.assemble(("当天支出\n", "dim"), (money(day["total"]), "bold")),
        Text.assemble(("预算计划\n", "dim"), (money(day["planned_total"]), "dim")),
        Text.assemble(("笔数\n", "dim"), (str(len(day["records"])), "dim")),
    )
    blocks.append(stats)

    by_cat = {}
    for r in day["records"]:
        key = r.get("category_name") or "未分类"
        by_cat[key] = by_cat.get(key, 0) + r["amount"]
    if by_cat:
        t = Table(box=box.SIMPLE, expand=True, header_style="dim")
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

    return Panel(Group(*blocks),
                 title="[bold]总览[/]  " + fmt_date(state["date"]),
                 subtitle="Ctrl+R 月度报表", border_style="grey30",
                 box=box.ROUNDED, padding=(1, 2))


def view_browse(state, conn):
    day = actions.day_view(conn, state["date"])
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
            sel = i == state["sel"]
            t.add_row(str(r["id"]), money(r["amount"]), r["note"] or "—",
                      r.get("category_name") or "未分类",
                      " ".join("@" + x for x in r["tags"]) or "",
                      r["time"] or "", style="reverse" if sel else "")
        body.append(t)
        body.append(Text("  改金额: edit <id> <金额>   改标签: tag <id> +标签   "
                         "改备注: note <id> 文字", style="dim"))
    else:
        body.append(Text("  这一天还没有记录", style="dim italic"))

    if state["profiles"]:
        p = Text("\n行动模板  ", style="dim")
        for prof in state["profiles"]:
            p.append(" %s " % prof["name"], style="cyan")
            if prof.get("default_amount"):
                p.append(money(prof["default_amount"]) + " ", style="dim")
        body.append(p)
    else:
        body.append(Text("\n  还没有模板：p 咖啡 18 #餐饮", style="dim italic"))

    return Panel(Group(*body),
                 title="[bold]浏览[/]  " + fmt_date(state["date"]),
                 subtitle="↑↓ 选中 · Ctrl+E 改金额 · Ctrl+G 标签 · Ctrl+D 删除",
                 border_style="grey30", box=box.ROUNDED, padding=(1, 2))


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
            t.add_row(b["title"], b["rule_text"], str(b["count"]),
                      money(b["planned"]), money(b["actual"]), Text(bar, style=color))
        blocks.append(t)
    else:
        blocks.append(Text("  这个月还没有预算", style="dim italic"))

    if rep["by_category"]:
        c = Text("\n分类合计  ", style="dim")
        for cat in rep["by_category"]:
            c.append(" %s %s " % (cat["name"], money(cat["total"])), style="cyan")
        blocks.append(c)

    return Panel(Group(*blocks),
                 title="[bold]月度报表[/]  " + state["month"],
                 subtitle="← → 换月 · Esc 返回", border_style="grey30",
                 box=box.ROUNDED, padding=(1, 2))


def help_panel():
    t = Table(box=box.SIMPLE, show_header=False, expand=True)
    t.add_column("命令", style="cyan", width=34)
    t.add_column("说明", style="dim")
    for cmd, desc in HELP_LINES:
        if not cmd:
            t.add_row("", "")
            continue
        t.add_row(cmd, desc)
    extra = Text(
        "\n裸字母只进命令行，不触发动作。快捷键一律用 Ctrl：\n"
        "  Ctrl+H 帮助   Ctrl+Q 退出   Ctrl+R 报表   Ctrl+T 今天\n"
        "  Ctrl+E 改金额  Ctrl+G 标签  Ctrl+D 删除  Ctrl+N 新模板\n"
        "非字母键可直接按：← → ↑ ↓ 换日/选中，Tab 换页，Enter 执行，Esc 返回",
        style="dim")
    return Panel(Group(t, extra), title="[bold]帮助[/]",
                 border_style="cyan", box=box.ROUNDED, padding=(1, 2))


# -------------------------------------------------------------- rendering
def render(state, conn):
    console.clear()
    if state["mode"] == "help":
        console.print(header(state))
        console.print(help_panel())
        console.print(Text("  任意键返回", style="dim"))
        return

    console.print(header(state))
    if state["mode"] == "report":
        console.print(view_report(state, conn))
    else:
        console.print([view_input, view_overview, view_browse][state["tab"]](state, conn))
        if state["tab"] != 0:
            console.print(Text(""))
            console.print(prompt_line(state))
    console.print(footer(state, state.get("message", "")))


# ------------------------------------------------------------------- input
def read_key():
    """Read one keypress. Returns (kind, value).

    kinds: char, up, down, left, right, tab, enter, esc, backspace, ctrl
    """
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            nxt = sys.stdin.read(1)
            if nxt == "[":
                seq = sys.stdin.read(1)
                return ({"A": "up", "B": "down", "C": "right", "D": "left"}.get(seq, "esc"), "")
            return ("esc", "")
        if ch in ("\r", "\n"):
            return ("enter", "")
        if ch == "\t":
            return ("tab", "")
        if ch in ("\x7f", "\x08"):
            return ("backspace", "")
        code = ord(ch)
        if 1 <= code <= 26:          # Ctrl+A .. Ctrl+Z (Ctrl+C is code 3)
            return ("ctrl", chr(code + 96))
        return ("char", ch)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def prompt(question, default=""):
    try:
        return console.input("  [bold cyan]%s[/] " % question, default=default)
    except (EOFError, KeyboardInterrupt):
        return None


# -------------------------------------------------------------------- main
def main():
    db.init()
    conn = db.connect()
    today = dt.date.today().isoformat()
    state = {
        "tab": 0,
        "mode": "normal",
        "today": today,
        "date": today,
        "month": dt.date.today().strftime("%Y-%m"),
        "sel": 0,
        "buffer": "",
        "history": [],
        "hist": -1,
        "message": "",
        "msg_kind": "ok",
        "profiles": [],
    }

    def refresh():
        state["profiles"] = db.list_profiles(conn)

    def run(text):
        """Execute a command string and fold the result into state."""
        state["message"] = ""
        try:
            kind, msg = commands.execute(conn, text, state)
            if kind == "quit":
                return True
            if msg:
                state["message"], state["msg_kind"] = msg, "ok"
            refresh()
        except actions.ActionError as e:
            state["message"], state["msg_kind"] = "✗ " + e.message, "err"
        return False

    refresh()

    try:
        while True:
            render(state, conn)
            kind, val = read_key()

            if state["mode"] == "help":
                state["mode"] = "normal"
                continue

            if kind == "ctrl":
                if val == "c":
                    break
                if val == "q":
                    break
                if val == "h":
                    state["mode"] = "help"
                    continue
                if val == "r":
                    state["mode"] = "report"
                    continue
                if val == "t":
                    run("today")
                    continue
                if val == "c":
                    state["mode"] = "normal"
                    continue
                if val == "n":
                    state["tab"] = (state["tab"] + 1) % 3
                    continue
                if val in ("e", "g", "d"):
                    run(_browse_shortcut(conn, state, val))
                    continue
                continue

            if kind == "tab":
                state["tab"] = (state["tab"] + 1) % 3
                continue

            if kind == "left":
                run("prev")
                continue
            if kind == "right":
                run("next")
                continue

            if kind == "up":
                if state["tab"] == 2:
                    state["sel"] = max(0, state["sel"] - 1)
                else:
                    _history(state, -1)
                continue
            if kind == "down":
                if state["tab"] == 2:
                    day = actions.day_view(conn, state["date"])
                    state["sel"] = min(max(0, len(day["records"]) - 1), state["sel"] + 1)
                else:
                    _history(state, +1)
                continue

            if kind == "esc":
                if state["mode"] == "report":
                    state["mode"] = "normal"
                state["buffer"] = ""
                continue

            if kind == "backspace":
                state["buffer"] = state["buffer"][:-1]
                continue

            if kind == "enter":
                text = state["buffer"].strip()
                state["buffer"] = ""
                state["hist"] = -1
                if text:
                    state["history"].append(text)
                    if run(text):
                        break
                continue

            if kind == "char":
                state["buffer"] += val
                continue
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()
        console.clear()
        console.print("[dim]bye[/]")


def _history(state, direction):
    hist = state["history"]
    if not hist:
        return
    if state["hist"] == -1:
        state["hist"] = len(hist) - 1 if direction < 0 else -1
    else:
        state["hist"] = max(0, min(len(hist) - 1, state["hist"] + direction))
    state["buffer"] = hist[state["hist"]] if state["hist"] >= 0 else ""


def _browse_shortcut(conn, state, key):
    """Ctrl+E/G/D act on the selected browse row, expressed as commands."""
    day = actions.day_view(conn, state["date"])
    if not day["records"]:
        raise actions.ActionError("这一天没有记录")
    rec = day["records"][min(state["sel"], len(day["records"]) - 1)]

    if key == "d":
        return "del %s" % rec["id"]
    if key == "e":
        ans = prompt("新金额", str(rec["amount"]))
        if ans is None:
            return ""
        return "edit %s %s" % (rec["id"], ans)
    if key == "g":
        ans = prompt("标签（逗号分隔，留空清空）", ",".join(rec["tags"]))
        if ans is None:
            return ""
        tags = [x.strip() for x in ans.replace("，", ",").split(",") if x.strip()]
        return "tags %s %s" % (rec["id"], ",".join(tags))
    return ""


if __name__ == "__main__":
    main()
