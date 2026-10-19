"""Zero-dependency HTTP server: static files + JSON API.

    python3 server.py [--port 8765] [--host 127.0.0.1]
"""
import argparse
import datetime as dt
import json
import mimetypes
import os
import posixpath
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import actions
import db
import recurrence

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")


def log(msg):
    sys.stderr.write("[%s] %s\n" % (dt.datetime.now().strftime("%H:%M:%S"), msg))


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def cmd_create_record(conn, payload):
    rec = actions.create_record(conn, payload)
    log("record created id=%s amount=%s" % (rec["id"], rec["amount"]))
    return rec


def cmd_create_budget(conn, payload):
    b = actions.create_budget(conn, payload)
    log("budget created id=%s rule=%s" % (b["id"], b["rule"]))
    return b


def cmd_run_command(conn, payload):
    text = payload.get("text") or ""
    today = dt.date.fromisoformat(payload["today"]) if payload.get("today") else dt.date.today()
    result = actions.run_command(conn, text, today=today)
    log("command text=%r -> %s" % (text, result.get("kind")))
    return result


ROUTES = {
    ("GET", "/api/bootstrap"): lambda c, p, q: {
        "categories": db.list_categories(c),
        "budgets": db.list_budgets(c),
        "profiles": db.list_profiles(c),
        "today": dt.date.today().isoformat(),
    },
    ("GET", "/api/records"): lambda c, p, q: {
        "records": db.list_records(
            c,
            date=q.get("date", [None])[0],
            start=q.get("start", [None])[0],
            end=q.get("end", [None])[0],
            limit=int(q["limit"][0]) if q.get("limit") else None,
        )
    },
    ("GET", "/api/budgets"): lambda c, p, q: {"budgets": db.list_budgets(c)},
    ("GET", "/api/profiles"): lambda c, p, q: {"profiles": db.list_profiles(c)},
    ("GET", "/api/report"): lambda c, p, q: actions.month_report(
        c, q.get("month", [None])[0]),
    ("GET", "/api/day"): lambda c, p, q: actions.day_view(c, q.get("date", [None])[0]),
}


def _post_record(conn, payload, rid=None):
    if rid is None:
        return cmd_create_record(conn, payload)
    return actions.update_record(conn, rid, payload)


class Handler(BaseHTTPRequestHandler):
    server_version = "BudgetApp/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log("%s %s" % (self.address_string(), fmt % args))

    # ---------------------------------------------------------- plumbing
    def _send(self, status, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, default=str).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode())
        except ValueError:
            raise ApiError("请求体不是合法 JSON")

    def _dispatch(self, method):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if not path.startswith("/api/"):
            return self._static(path)

        conn = db.connect()
        try:
            payload = self._json_body() if method in ("POST", "PATCH", "PUT") else {}

            # explicit routes
            handler = ROUTES.get((method, path))
            if handler:
                log("%s %s" % (method, path))
                return self._send(200, handler(conn, payload, query))

            # /api/records/<id>
            if path.startswith("/api/records/"):
                rid = path.rsplit("/", 1)[-1]
                if not rid.isdigit():
                    raise ApiError("记录 id 无效", 404)
                rid = int(rid)
                if method == "PATCH":
                    log("PATCH /api/records/%s" % rid)
                    return self._send(200, _post_record(conn, payload, rid))
                if method == "DELETE":
                    log("DELETE /api/records/%s" % rid)
                    if not db.get_record(conn, rid):
                        raise ApiError("找不到记录", 404)
                    db.delete_record(conn, rid)
                    return self._send(200, {"ok": True})

            if method == "POST" and path == "/api/records":
                log("POST /api/records")
                return self._send(201, _post_record(conn, payload))

            if method == "POST" and path == "/api/budgets":
                log("POST /api/budgets")
                return self._send(201, cmd_create_budget(conn, payload))

            if method == "POST" and path == "/api/command":
                return self._send(200, cmd_run_command(conn, payload))

            if method == "POST" and path == "/api/categories":
                name = (payload.get("name") or "").strip()
                if not name:
                    raise ApiError("分类名不能为空")
                if db.get_category_by_name(conn, name):
                    raise ApiError("分类已存在")
                return self._send(201, db.create_category(
                    conn, name, payload.get("color") or "#8b8b8b",
                    payload.get("kind") or "life", int(payload.get("sort") or 100)))

            if method == "POST" and path == "/api/profiles":
                return self._send(201, actions.create_profile(conn, payload))

            # patch / delete by id for budgets, profiles, categories
            for prefix, getter, updater, deleter in (
                ("/api/budgets/", db.get_budget, db.update_budget, db.delete_budget),
                ("/api/profiles/", db.get_profile, db.update_profile, db.delete_profile),
                ("/api/categories/", db.get_category, db.update_category, db.delete_category),
            ):
                if path.startswith(prefix):
                    rid = path[len(prefix):]
                    if not rid.isdigit():
                        raise ApiError("id 无效", 404)
                    rid = int(rid)
                    if not getter(conn, rid):
                        raise ApiError("找不到对象", 404)
                    if method == "PATCH":
                        patch = dict(payload)
                        if "category" in patch:
                            cat = db.resolve_category(conn, patch.pop("category"))
                            patch["category_id"] = cat["id"] if cat else None
                        log("PATCH %s%s" % (prefix, rid))
                        return self._send(200, updater(conn, rid, patch))
                    if method == "DELETE":
                        log("DELETE %s%s" % (prefix, rid))
                        deleter(conn, rid)
                        return self._send(200, {"ok": True})

            raise ApiError("未知接口 %s %s" % (method, path), 404)
        except (ApiError, actions.ActionError) as e:
            log("API error: %s" % e.message)
            self._send(getattr(e, "status", 400), {"error": e.message})
        except Exception as e:  # noqa: BLE001 - surface any bug to the client
            log("unhandled error: %r" % e)
            self._send(500, {"error": "服务器内部错误: %s" % e})
        finally:
            conn.close()

    # ------------------------------------------------------------ static
    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        safe = posixpath.normpath(urllib.parse.unquote(path)).lstrip("/")
        full = os.path.join(STATIC, safe)
        if not os.path.abspath(full).startswith(STATIC) or not os.path.isfile(full):
            log("static 404: %s" % path)
            return self._send(404, "Not found", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            data = fh.read()
        self._send(200, data, ctype)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_HEAD(self):
        self._dispatch("GET")


def main():
    ap = argparse.ArgumentParser(description="Budget app server")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    db.init()
    log("db ready at %s" % db.DB_PATH)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    log("serving http://%s:%d" % (args.host, args.port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
        httpd.shutdown()


if __name__ == "__main__":
    main()
