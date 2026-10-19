"""CLI for the budget app.  python3 tui.py

Behaves like a shell rather than a full-screen TUI: output scrolls up in the
transcript, and a fixed `budget>` prompt sits at the bottom. Typing repaints
only that bottom bar, never the whole screen.

Pages (Tab): input / overview / browse.
"""
import datetime as dt
import sys

from rich import box
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

money = fmt.money
fmt_date = fmt.fmt_date
shift = fmt.shift


# ------------------------------------------------------------------ layout
# CLI model: completed output scrolls up in the transcript; the prompt bar is
# pinned to the bottom and repainted in place (no full-screen redraw).
BAR_HEIGHT = 3          # separator + hint line + prompt line


class Screen:
    """Owns the terminal: scrolling transcript + a fixed bottom bar."""

    def __init__(self, console_):
        self.console = console_
        self.bar_lines = 0

    def _write(self, text):
        self.console.file.write(text)
        self.console.file.flush()

    def clear_bar(self):
        if self.bar_lines:
            self._write("\033[%dA\r\033[0J" % self.bar_lines)
            self.bar_lines = 0

    def draw_bar(self, renderable):
        """Repaint the bottom bar. `renderable` is a rich renderable."""
        self.clear_bar()
        with self.console.capture() as cap:
            self.console.print(renderable, end="")
        text = cap.get()
        self._write(text)
        self.bar_lines = text.count("\n") + (0 if text.endswith("\n") else 1)

    def emit(self, renderable):
        """Append finished output to the transcript (above the bar)."""
        self.clear_bar()
        self.console.print(renderable)
        self.bar_lines = 0


def hint_line(state):
    """The command-word row, CLI style."""
    words = ("b", "p", "day", "type", "tag", "use", "report", "help")
    t = Text("  ", style="")
    for i, w in enumerate(words):
        if i:
            t.append("  ", style="dim")
        t.append(w, style="cyan")
    return t


def prompt_bar(state):
    buf = state["buffer"]
    line = Text.assemble(
        ("  budget", "bold cyan"),
        ("> ", "bold cyan"),
        (buf, ""),
        ("\u258f", "cyan"),
    )
    return Group(
        Text("  " + "\u2500" * max(10, console.width - 4), style="grey30"),
        hint_line(state),
        line,
    )


def view_input(state, conn):
    """Input tab has no body of its own; the bar carries the interaction."""
    day = actions.day_view(conn, state["date"])
    line = Text("  " + fmt_date(state["date"]), style="dim")
    if day["records"]:
        line.append("   ", style="")
        line.append("%d 笔  %s" % (len(day["records"]), money(day["total"])), style="dim")
    return Group(line)


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
        p = Text("行动模板  ", style="dim")
        for prof in state["profiles"]:
            p.append(" %s " % prof["name"], style="cyan")
            if prof.get("default_amount"):
                p.append(money(prof["default_amount"]) + " ", style="dim")
        body.append(p)
    else:
        body.append(Text("  还没有模板：p 咖啡 18 #餐饮", style="dim italic"))

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


def _wrap(text, width):
    """Wrap CJK text by display width, not character count."""
    import unicodedata
    lines, cur, w = [], "", 0
    for ch in text:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if w + cw > width:
            lines.append(cur)
            cur, w = "", 0
        cur += ch
        w += cw
    if cur:
        lines.append(cur)
    return lines


def help_page(state):
    """Render one screenful of the scenario help."""
    scen = commands.SCENARIOS
    per_page = 2
    pages = max(1, (len(scen) + per_page - 1) // per_page)
    page = state.get("help_page", 0) % pages
    chunk = scen[page * per_page:(page + 1) * per_page]

    body = []
    for item in chunk:
        head = Text()
        head.append("  %s\n" % item["title"], style="bold cyan")
        head.append("    什么时候用  ", style="dim")
        head.append(item["when"] + "\n", style="")
        body.append(head)

        for cmd in item["cmds"]:
            line = Text("    › ", style="bold cyan")
            line.append(cmd, style="green")
            body.append(line)

        body.append(Text(""))
        # "为什么" label sits on the first line, continuation lines align under it
        label = "为什么  "
        lines = _wrap(label + item["why"], max(20, console.width - 26))
        body.append(Text("      " + lines[0], style="dim italic"))
        for line in lines[1:]:
            body.append(Text("      " + line, style="dim italic"))
        body.append(Text(""))

    body.append(Text("  快捷键", style="dim"))
    for line in [
        "裸字母只进命令行，不触发动作。修饰键一律用 Ctrl（终端收不到 Cmd）。",
        "Ctrl+H 帮助   Ctrl+Q 退出   Ctrl+R 报表   Ctrl+T 今天",
        "Ctrl+E 改金额   Ctrl+G 标签   Ctrl+D 删除",
        "← → 换日   ↑ ↓ 选中/历史   Tab 换页   Enter 执行   Esc 返回",
    ]:
        body.append(Text("    " + line, style="dim"))

    return Panel(
        Group(*body),
        title="[bold]帮助[/]  %d/%d" % (page + 1, pages),
        subtitle="← → 翻页 · 任意其他键返回",
        border_style="cyan", box=box.ROUNDED, padding=(1, 2),
    )


# -------------------------------------------------------------- rendering
def current_view(state, conn):
    """The renderable for the active tab (or report/help overlay)."""
    if state["mode"] == "help":
        return help_page(state)
    if state["mode"] == "report":
        return view_report(state, conn)
    return [view_input, view_overview, view_browse][state["tab"]](state, conn)


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
        "profiles": [],
        "help_page": 0,
    }

    screen = Screen(console)

    def refresh():
        state["profiles"] = db.list_profiles(conn)

    def show(renderable=None):
        """Print the active view into the transcript (CLI: output accumulates)."""
        screen.emit(renderable if renderable is not None else current_view(state, conn))

    def run(text):
        """Execute one command; print its result into the transcript."""
        screen.clear_bar()
        console.print(Text("  budget> ", style="bold cyan") + Text(text))
        try:
            kind, msg = commands.execute(conn, text, state)
            if kind == "quit":
                return True
            refresh()
            if kind == "help":
                show()
                return False
            if msg:
                console.print(Text("  " + msg, style="green" if not msg.startswith("✗") else "red"))
            show()
        except actions.ActionError as e:
            console.print(Text("  ✗ " + e.message, style="red"))
        return False

    refresh()

    console.clear()
    console.print(Text("  budget", style="bold cyan") +
                  Text("  输入 ? 看帮助，Tab 切换页面", style="dim"))
    console.print(Text(""))
    show()

    try:
        while True:
            screen.draw_bar(prompt_bar(state))
            kind, val = read_key()

            if state["mode"] == "help":
                pages = max(1, (len(commands.SCENARIOS) + 1) // 2)
                if kind == "left":
                    state["help_page"] = (state.get("help_page", 0) - 1) % pages
                    show(help_page(state))
                    continue
                if kind == "right":
                    state["help_page"] = (state.get("help_page", 0) + 1) % pages
                    show(help_page(state))
                    continue
                state["mode"] = "normal"
                show()
                continue

            if kind == "ctrl":
                if val in ("c", "q"):
                    break
                if val == "h":
                    state["mode"] = "help"
                    state["help_page"] = 0
                    show()
                    continue
                if val == "r":
                    state["mode"] = "report"
                    show()
                    continue
                if val == "t":
                    run("today")
                    continue
                if val == "n":
                    state["tab"] = (state["tab"] + 1) % 3
                    show()
                    continue
                if val in ("e", "g", "d"):
                    try:
                        run(_browse_shortcut(conn, state, val))
                    except actions.ActionError as e:
                        screen.clear_bar()
                        console.print(Text("  ✗ " + e.message, style="red"))
                    continue
                continue

            if kind == "tab":
                state["tab"] = (state["tab"] + 1) % 3
                show()
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
                    show()
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
                else:
                    show()
                continue

            if kind == "char":
                state["buffer"] += val
                continue
    except KeyboardInterrupt:
        pass
    finally:
        screen.clear_bar()
        conn.close()
        console.print(Text("  bye", style="dim"))


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
