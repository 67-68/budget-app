"""Tiny command language for the one-line input box.

   35 午饭 #餐饮 @同事            -> expense today
   昨天 35 午饭 #餐饮             -> expense yesterday
   b 50 每周三 12:00 咖啡 #餐饮   -> budget (weekly, Wednesday noon)
   b 1200 每月1号 房租 #生活      -> budget (monthly)
   p 咖啡 #餐饮 18 12:00          -> profile (action template)
   tag 12 +报销 -待定            -> mutate tags of record 12
   ?                             -> help

Everything is whitespace separated; `#x` is a category, `@x` a tag.
Anything left over becomes the note/title.
"""
import datetime as dt
import re

CN_WEEK = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
          "七": 7, "八": 8, "九": 9, "十": 10, "半": 0.5}

TIME_WORDS = {"早上": "08:00", "早晨": "08:00", "上午": "09:00", "中午": "12:00",
              "下午": "14:00", "傍晚": "18:00", "晚上": "19:00", "夜里": "22:00",
              "半夜": "23:00"}

NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
TIME_RE = re.compile(r"^(\d{1,2})[:：](\d{2})$")

HELP_TEXT = """命令速查

  <金额> [备注] [#分类] [@标签]      记一笔支出，例如：35 午饭 #餐饮 @同事
  昨天/前天/前天 ...                 指定日期，例如：昨天 42 打车 #交通
  b <金额> <周期> [标题] [#分类]     建预算，例如：b 50 每周三 12:00 咖啡 #餐饮
  p <名称> [金额] [#分类] [@标签]    建行动模板，例如：p 咖啡 18 #餐饮 12:00
  tag <id> +标签 -标签               给记录打/去标签
  ?                                 显示本帮助

周期写法：每天 / 每周三 / 每周一,三 / 每2天 / 每3周 / 每月1号 / 每月1,15号 / 每年3月1号
时间写法：12:00 / 中午 / 晚上"""


def _cn_num(s):
    """'3' | '三' | '十' | '十二' -> int/float"""
    s = s.strip()
    if not s:
        return None
    if NUM_RE.match(s):
        return float(s) if "." in s else int(s)
    if s in CN_NUM:
        return CN_NUM[s]
    if s.startswith("十"):
        rest = s[1:]
        return 10 + (CN_NUM.get(rest, 0) if rest else 0)
    if "十" in s:
        a, _, b = s.partition("十")
        return CN_NUM.get(a, 1) * 10 + (CN_NUM.get(b, 0) if b else 0)
    return None


def parse_time(tok):
    m = TIME_RE.match(tok)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h < 24 and 0 <= mi < 60:
            return "%02d:%02d" % (h, mi)
    if tok in TIME_WORDS:
        return TIME_WORDS[tok]
    return None


def parse_rule_token(tok):
    """Return (rule_dict, consumed) or (None, 0). `tok` may encode a whole rule."""
    t = tok.strip()
    low = t.lower()

    if t in ("每天", "每日") or low in ("daily", "day"):
        return {"freq": "daily"}, 1
    if t in ("工作日",):
        return {"freq": "weekly", "weekdays": [0, 1, 2, 3, 4]}, 1
    if t in ("周末",):
        return {"freq": "weekly", "weekdays": [5, 6]}, 1

    # 每周 / 每周三 / 每周一,三 / 每周1
    m = re.match(r"^(?:每)?周([一二三四五六日天\d,，、]+)$", t)
    if m:
        body = m.group(1).replace("，", ",").replace("、", ",")
        days = []
        for part in body.split(","):
            if not part:
                continue
            if part.isdigit():
                n = int(part)
                if 1 <= n <= 7:
                    days.append(n - 1)
            elif part in CN_WEEK:
                days.append(CN_WEEK[part])
        if days:
            return {"freq": "weekly", "weekdays": sorted(set(days))}, 1

    # 每N天 / 每N周 / 每N个月
    m = re.match(r"^每([一二两三四五六七八九十\d]+)(天|日|周|星期|月)$", t)
    if m:
        n = _cn_num(m.group(1))
        unit_cn = m.group(2)
        unit = {"天": "day", "日": "day", "周": "week", "星期": "week", "月": "month"}[unit_cn]
        if n:
            return {"freq": "interval", "every": int(n), "unit": unit}, 1

    # 每月1号 / 每月1,15号 / 每月十五号
    m = re.match(r"^每?月([一二三四五六七八九十两\d,，、]+)号?$", t)
    if m:
        body = m.group(1).replace("，", ",").replace("、", ",")
        days = [int(x) for x in body.split(",") if x.isdigit()]
        if days:
            return {"freq": "monthly", "days": days}, 1
        # 中文数字逐个解析：十五号
        nums, buf = [], ""
        for ch in body:
            if ch in CN_NUM or ch == "十":
                buf += ch
            elif buf:
                nums.append(int(_cn_num(buf)))
                buf = ""
        if buf:
            nums.append(int(_cn_num(buf)))
        if nums:
            return {"freq": "monthly", "days": nums}, 1

    # 每年3月1号
    m = re.match(r"^每年(\d{1,2})月([\d,，、]+)号?$", t)
    if m:
        days = [int(x) for x in re.split(r"[,，、]", m.group(2)) if x.isdigit()]
        return {"freq": "yearly", "month": int(m.group(1)), "days": days or [1]}, 1

    return None, 0


def _split_tokens(text):
    return [t for t in re.split(r"\s+", text.strip()) if t]


def parse(text, today=None):
    """Parse one command line into a structured dict."""
    today = today or dt.date.today()
    text = (text or "").strip()
    if not text:
        return {"ok": False, "kind": "error", "message": "空命令"}
    if text in ("?", "help", "帮助"):
        return {"ok": True, "kind": "help", "text": HELP_TEXT}

    toks = _split_tokens(text)
    head = toks[0].lower()

    if head in ("tag", "标签"):
        return _parse_tag(toks)

    kind = "record"
    if head in ("b", "budget", "预算"):
        kind = "budget"
        toks = toks[1:]
    elif head in ("p", "profile", "模板"):
        kind = "profile"
        toks = toks[1:]

    if not toks:
        return {"ok": False, "kind": "error", "message": "缺少内容"}

    # leading date words
    date = today
    while toks:
        h = toks[0]
        if h in ("今天", "今日"):
            date, toks = today, toks[1:]
        elif h in ("昨天", "昨日"):
            date, toks = today - dt.timedelta(days=1), toks[1:]
        elif h in ("前天",):
            date, toks = today - dt.timedelta(days=2), toks[1:]
        elif re.match(r"^-\d+d$", h):
            date, toks = today - dt.timedelta(days=int(h[1:-1])), toks[1:]
        else:
            break

    if kind == "profile":
        return _parse_profile(toks)

    # rule tokens (budget only, but tolerated for records)
    rule, rule_words, tm = {}, [], None
    rest = []
    i = 0
    while i < len(toks):
        tok = toks[i]
        r, used = parse_rule_token(tok)
        if r and not rule:
            rule = r
            rule_words.append(tok)
            i += used
            continue
        p = parse_time(tok)
        if p and tm is None:
            tm = p
            i += 1
            continue
        rest.append(tok)
        i += 1

    amount = None
    note_parts, tags, cat = [], [], None
    for tok in rest:
        if amount is None and NUM_RE.match(tok):
            amount = float(tok)
        elif tok.startswith("#") and len(tok) > 1:
            cat = tok[1:]
        elif tok.startswith("@") and len(tok) > 1:
            tags.append(tok[1:])
        else:
            note_parts.append(tok)

    note = " ".join(note_parts).strip()

    if amount is None:
        return {"ok": False, "kind": "error", "message": "没看懂金额：%s" % text}

    if rule and kind == "record":
        return {"ok": False, "kind": "error",
                "message": "「%s」像是周期预算，请加 b 前缀：b %s" % (rule_words[0], text)}

    if kind == "budget":
        if not rule:
            return {"ok": False, "kind": "error",
                    "message": "预算需要周期，例如：b 50 每周三 12:00 咖啡"}
        rule["time"] = tm
        return {"ok": True, "kind": "budget", "amount": amount, "title": note or "未命名预算",
                "rule": rule, "rule_text": _rule_hint(rule_words, tm),
                "category": cat, "tag": tags[0] if tags else None,
                "start_date": date.isoformat()}

    return {"ok": True, "kind": "record", "amount": amount, "note": note,
            "category": cat, "tags": tags, "date": date.isoformat(), "time": tm,
            "raw": text}


def _rule_hint(words, tm):
    return " ".join(words) + ((" " + tm) if tm else "")


def _parse_profile(toks):
    if not toks:
        return {"ok": False, "kind": "error", "message": "模板需要名称"}
    name, amount, cat, tags, tm = None, None, None, [], None
    for tok in toks:
        if tok.startswith("#") and len(tok) > 1:
            cat = tok[1:]
        elif tok.startswith("@") and len(tok) > 1:
            tags.append(tok[1:])
        elif NUM_RE.match(tok) and amount is None:
            amount = float(tok)
        elif (p := parse_time(tok)) and tm is None:
            tm = p
        elif name is None:
            name = tok
        else:
            name += " " + tok
    return {"ok": True, "kind": "profile", "name": name or "未命名",
            "default_amount": amount, "category": cat, "default_tags": tags,
            "default_time": tm}


def _parse_tag(toks):
    if len(toks) < 2:
        return {"ok": False, "kind": "error", "message": "用法：tag 12 +报销 -待定"}
    rid = toks[1]
    if not rid.isdigit():
        return {"ok": False, "kind": "error", "message": "记录 id 必须是数字"}
    add = [t[1:] for t in toks[2:] if t.startswith("+") and len(t) > 1]
    rem = [t[1:] for t in toks[2:] if t.startswith("-") and len(t) > 1]
    return {"ok": True, "kind": "tag", "record_id": int(rid), "add": add, "remove": rem}
