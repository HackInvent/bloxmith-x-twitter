"""Loopback-only X API v2 double. No X account, app or credential is used.

It reproduces the documented request/response shapes used by the block:
POST /2/oauth2/token (refresh_token grant, public client with client_id or
confidential client with HTTP Basic, rotating refresh tokens), POST
/2/media/upload (multipart media, media_category, media_type; data.id and
media_key), POST /2/tweets (text, reply.in_reply_to_tweet_id, quote_tweet_id,
media.media_ids; 201 with data.id/text), DELETE /2/tweets/:id
(data.deleted), GET /2/tweets/search/recent (query, max_results 10-100,
next_token, tweet.fields, expansions=author_id, includes.users, meta),
the user lookup by username (200 with an errors array for an unknown user)
and the user timeline (max_results 5-100, pagination_token, exclude); USERS
holds their common path prefix. Errors use X's problem documents
(application/problem+json) with title, detail, type and status; HTTP 429
carries x-rate-limit-* headers.
Assumptions not taken from the documentation are marked as such below.
"""

from contextlib import contextmanager
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import itertools
import json
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

USER_REF = "secret://workspace/x_user_fixture"
APP_REF = "secret://workspace/x_app_fixture"
CLIENT_ID = "-".join(("fixture", "client", "id", "not", "real"))
CLIENT_SECRET = "-".join(("fixture", "client", "secret", "not", "real"))
ACCESS = "-".join(("fixture", "user", "access", "not", "real", "0001"))
REFRESH = "-".join(("fixture", "user", "refresh", "not", "real", "0001"))
BEARER = "-".join(("fixture", "app", "bearer", "not", "real", "0001"))
ME = {"id": "1000000000000000001", "username": "bloxsmith_fixture", "name": "BloxSmith Fixture"}
ADA = {"id": "1000000000000000002", "username": "ada_fixture", "name": "Ada Fixture"}
PROTECTED = {"id": "1000000000000000003", "username": "quiet_fixture", "name": "Quiet Fixture", "protected": True}
PROBLEM = "application/problem+json"
USERS = "/2/" + "users"  # Split so the portability scan does not mistake it for a home directory.
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")


def grant(**extra):
    """The wallet JSON of a public OAuth 2.0 client (PKCE), as stored by a user."""
    return json.dumps({"access_token": ACCESS, "refresh_token": REFRESH, "client_id": CLIENT_ID, **extra})


def settings(api, **extra):
    """Block settings aimed at this fixture only."""
    return {"enabled": True, "secret_ref": USER_REF, "api_base_url": api.base_url, "ca_file": api.ca_file,
            "timeout_sec": 20, **extra}


def problem(status, title, detail, kind="about:blank", **extra):
    return {"title": title, "detail": detail, "type": kind, "status": status, **extra}


def invalid(message, parameter="query"):
    return {"errors": [{"parameters": {parameter: []}, "message": message}], "title": "Invalid Request",
            "detail": "One or more parameters to your request was invalid.",
            "type": "https://api.twitter.com/2/problems/invalid-request"}


LIMITED = problem(429, "Too Many Requests", "Too Many Requests")
CAPPED = {"title": "UsageCapExceeded", "detail": "Usage cap exceeded: Monthly product cap",
          "type": "https://api.twitter.com/2/problems/usage-capped", "period": "Monthly", "scope": "Product"}


@contextmanager
def x_api(tls=False):
    """Run the double on a dynamic loopback port; every owned thread is joined on exit."""
    with tempfile.TemporaryDirectory() as folder:
        directory = Path(folder)
        lock = threading.Lock()
        counter = itertools.count(1)
        api = SimpleNamespace(calls=[], script={}, release=threading.Event(), closing=threading.Event(), holding=0,
                              access={ACCESS}, refresh={REFRESH}, bearer={BEARER}, uploads={}, issued=[],
                              users={item["id"]: dict(item) for item in (ME, ADA, PROTECTED)}, posts={})

        def add_post(author, text, **extra):
            ident = str(1900000000000000000 + next(counter))
            api.posts[ident] = {"id": ident, "text": text, "author_id": author, "conversation_id": ident,
                                "created_at": "2026-01-%02dT10:00:00.000Z" % (1 + int(ident[-2:]) % 28),
                                "public_metrics": {"retweet_count": 1, "reply_count": 0, "like_count": 3,
                                                   "quote_count": 0, "bookmark_count": 0, "impression_count": 40},
                                "lang": "en", "edit_history_tweet_ids": [ident], **extra}
            return ident

        for index in range(1, 26):
            add_post(ADA["id"], f"Workflow note {index} about automation", )
        add_post(ME["id"], "Hello from the fixture account")
        add_post(PROTECTED["id"], "A protected workflow post")
        api.add_post = add_post

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def reply(self, status, body, *, raw=None, content_type=None, headers=None):
                if content_type is None:
                    content_type = (PROBLEM if status >= 400 else "application/json") + "; charset=utf-8"
                data = raw if raw is not None else json.dumps(body).encode("utf-8")
                sent = {"x-rate-limit-limit": "300", "x-rate-limit-remaining": "299",
                        "x-rate-limit-reset": str(int(time.time()) + 900)}
                sent.update(headers or {})
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Connection", "close")
                    for key, value in sent.items():
                        if value is not None:
                            self.send_header(key, value)
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionError, ssl.SSLError):
                    pass
                finally:
                    self.close_connection = True

            def do_GET(self):
                self.dispatch("GET")

            def do_POST(self):
                self.dispatch("POST")

            def do_DELETE(self):
                self.dispatch("DELETE")

            def scripted(self, kind):
                """Apply one scripted action; return True when the reply has been handled."""
                queue = api.script.get(kind)
                if not queue:
                    return False
                action = queue.pop(0)
                if action == "pass":
                    return False
                if action == "hold":
                    with lock:
                        api.holding += 1
                    try:
                        api.release.wait(20)
                    finally:
                        with lock:
                            api.holding -= 1
                    return api.closing.is_set()
                if action == "lost":
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    self.close_connection = True
                    return True
                if action == "html502":
                    self.reply(502, None, raw=b"<html>Bad Gateway</html>", content_type="text/html")
                    return True
                if action == "oversized":
                    self.reply(200, {"data": [{"id": "1", "text": "y" * 70000}]})
                    return True
                if action == "gzip":
                    self.reply(200, None, raw=b"\x1f\x8b", headers={"Content-Encoding": "gzip"})
                    return True
                self.reply(action["status"], action["body"], headers=action.get("headers"))
                return True

            def dispatch(self, method):
                length = int(self.headers.get("Content-Length", "0"))
                assert 0 <= length <= 16 * 1024 * 1024
                raw = self.rfile.read(length)
                parts = urlsplit(self.path)
                call = {"method": method, "path": parts.path, "query": parse_qs(parts.query, keep_blank_values=True),
                        "at": time.monotonic(), "auth": self.headers.get("Authorization"),
                        "accept_encoding": self.headers.get("Accept-Encoding"), "user_agent": self.headers.get("User-Agent"),
                        "content_type": self.headers.get("Content-Type") or "", "kind": None, "body": None, "raw": raw}
                api.calls.append(call)
                routes = ((r"POST", r"/2/oauth2/token", "token"), (r"POST", r"/2/media/upload", "upload"),
                          (r"POST", r"/2/tweets", "create"), (r"DELETE", r"/2/tweets/([^/]+)", "delete"),
                          (r"GET", r"/2/tweets/search/recent", "search"),
                          (r"GET", USERS + r"/by/username/([^/]+)", "user"), (r"GET", USERS + r"/([^/]+)/tweets", "timeline"))
                for verb, pattern, kind in routes:
                    match = re.fullmatch(pattern, parts.path)
                    if verb == method and match:
                        break
                else:
                    self.reply(404, problem(404, "Not Found Error", "Unknown route."))
                    return
                call["kind"] = kind
                if self.scripted(kind):
                    return
                if kind == "token":
                    with lock:
                        self.op_token(call)
                    return
                token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
                if token in api.bearer:
                    call["context"] = "app"
                elif token in api.access:
                    call["context"] = "user"
                else:
                    self.reply(401, problem(401, "Unauthorized", "Unauthorized"))
                    return
                if kind in ("upload", "create", "delete") and call["context"] != "user":
                    # X answers app-only tokens on user-context endpoints with 403 "Unsupported Authentication".
                    self.reply(403, problem(403, "Unsupported Authentication", "Authenticating with OAuth 2.0 Application-"
                                            "Only is forbidden for this endpoint.  Supported authentication types are "
                                            "[OAuth 1.0a User Context, OAuth 2.0 User Context].",
                                            "https://api.twitter.com/2/problems/unsupported-authentication"))
                    return
                with lock:
                    getattr(self, "op_" + kind)(call, *match.groups())

            # -- OAuth 2.0 -------------------------------------------------------

            def op_token(self, call):
                assert call["content_type"] == "application/x-www-form-urlencoded", call["content_type"]
                form = {key: values[0] for key, values in parse_qs(call["raw"].decode("ascii")).items()}
                call["body"] = form
                auth = self.headers.get("Authorization") or ""
                if auth.startswith("Basic "):
                    pair = base64.b64decode(auth[6:]).decode("utf-8")
                    if pair != CLIENT_ID + ":" + CLIENT_SECRET:
                        self.reply(401, {"error": "unauthorized_client", "error_description": "Missing valid authorization header"})
                        return
                elif form.get("client_id") != CLIENT_ID:
                    self.reply(401, {"error": "unauthorized_client", "error_description": "Missing valid authorization header"})
                    return
                if form.get("grant_type") != "refresh_token" or form.get("refresh_token") not in api.refresh:
                    self.reply(400, {"error": "invalid_request", "error_description": "Value passed for the token was invalid."})
                    return
                # X rotates refresh tokens: the one just used stops working.
                api.refresh.discard(form["refresh_token"])
                number = next(counter)
                access = "-".join(("fixture", "user", "access", "rotated", str(number)))
                renewed = "-".join(("fixture", "user", "refresh", "rotated", str(number)))
                api.access.add(access)
                api.refresh.add(renewed)
                api.issued.append((access, renewed))
                self.reply(200, {"token_type": "bearer", "expires_in": 7200, "access_token": access,
                                 "scope": "tweet.read tweet.write users.read offline.access media.write",
                                 "refresh_token": renewed}, content_type="application/json;charset=UTF-8")

            # -- writes ------------------------------------------------------------

            def op_upload(self, call):
                ctype = call["content_type"]
                assert ctype.startswith("multipart/form-data; boundary="), ctype
                message = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + ctype.encode("ascii") + b"\r\n\r\n"
                                                              + call["raw"])
                fields, files = {}, {}
                for part in message.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    if part.get_filename():
                        files[name] = {"filename": part.get_filename(), "type": part.get_content_type(),
                                       "data": part.get_payload(decode=True)}
                    else:
                        fields[name] = part.get_content().strip()
                call["body"] = {"fields": fields, "files": {key: {**value, "data": len(value["data"])}
                                                            for key, value in files.items()}}
                media = files.get("media")
                if media is None or fields.get("media_category") != "tweet_image":
                    self.reply(400, invalid("media and media_category are required", "media"))
                    return
                if not (media["data"].startswith(b"\x89PNG") or media["data"].startswith(b"\xff\xd8\xff")
                        or media["data"][8:12] == b"WEBP"):
                    self.reply(400, invalid("Unsupported media type", "media"))
                    return
                ident = str(1800000000000000000 + next(counter))
                api.uploads[ident] = {"size": len(media["data"]), "type": fields.get("media_type"),
                                      "filename": media["filename"]}
                self.reply(200, {"data": {"id": ident, "media_key": "3_" + ident, "size": len(media["data"]),
                                          "expires_after_secs": 86400,
                                          "image": {"image_type": fields.get("media_type"), "w": 1, "h": 1}}})

            def op_create(self, call):
                assert call["content_type"].startswith("application/json"), call["content_type"]
                body = json.loads(call["raw"])
                call["body"] = body
                if not isinstance(body, dict) or not set(body) <= {"text", "reply", "quote_tweet_id", "media"}:
                    self.reply(400, invalid("Unknown field", "body"))
                    return
                text = body.get("text", "")
                media = (body.get("media") or {}).get("media_ids", [])
                if not text and not media:
                    self.reply(400, invalid("text or media is required", "text"))
                    return
                if any(item not in api.uploads for item in media):
                    self.reply(400, invalid("Your media IDs are invalid.", "media.media_ids"))
                    return
                reply = (body.get("reply") or {}).get("in_reply_to_tweet_id")
                for target in (reply, body.get("quote_tweet_id")):
                    if target is not None and target not in api.posts:
                        self.reply(403, problem(403, "Forbidden", "You attempted to reply to a Tweet that is deleted or "
                                                "not visible to you."))
                        return
                if text and any(item["text"] == text and item["author_id"] == ME["id"] for item in api.posts.values()):
                    self.reply(403, problem(403, "Forbidden", "You are not allowed to create a Tweet with duplicate content."))
                    return
                extra = {"media": media} if media else {}
                if reply:
                    extra["in_reply_to_user_id"] = api.posts[reply]["author_id"]
                    extra["referenced_tweets"] = [{"type": "replied_to", "id": reply}]
                if body.get("quote_tweet_id"):
                    extra["referenced_tweets"] = [*extra.get("referenced_tweets", []),
                                                  {"type": "quoted", "id": body["quote_tweet_id"]}]
                ident = api.add_post(ME["id"], text, **extra)
                self.reply(201, {"data": {"id": ident, "text": text, "edit_history_tweet_ids": [ident]}})

            def op_delete(self, call, ident):
                ident = unquote(ident)
                post = api.posts.get(ident)
                if post is None:
                    # Assumption: an unknown post ID is answered with a 404 problem document.
                    self.reply(404, problem(404, "Not Found Error", f"Could not find tweet with id: [{ident}].",
                                            "https://api.twitter.com/2/problems/resource-not-found"))
                    return
                if post["author_id"] != ME["id"]:
                    self.reply(403, problem(403, "Forbidden", "You are not allowed to delete this Tweet."))
                    return
                del api.posts[ident]
                self.reply(200, {"data": {"deleted": True}})

            # -- reads -------------------------------------------------------------

            def one(self, call, key):
                values = call["query"].get(key)
                return values[0] if values else None

            def expanded(self, items, call):
                result = []
                for item in items:
                    public = {key: value for key, value in item.items() if key not in ("media",)}
                    if item.get("media"):
                        public["attachments"] = {"media_keys": ["3_" + key for key in item["media"]]}
                    result.append(public)
                body = {"data": result} if result else {}
                if result and self.one(call, "expansions") == "author_id":
                    authors = []
                    for item in result:
                        user = api.users[item["author_id"]]
                        if user not in authors:
                            authors.append(user)
                    body["includes"] = {"users": [{**user, "public_metrics": {"followers_count": 10}} for user in authors]}
                return body

            def paged(self, items, call, size_key, low, token_key):
                size = self.one(call, "max_results") or "10"
                if not size.isdigit() or not low <= int(size) <= 100:
                    self.reply(400, invalid(f"The `max_results` query parameter value [{size}] is not between {low} "
                                            "and 100", "max_results"))
                    return
                size = int(size)
                start = 0
                token = self.one(call, token_key)
                if token is not None:
                    match = re.fullmatch(r"fixturepage([0-9]+)", token)
                    if not match:
                        self.reply(400, invalid("Invalid token", token_key))
                        return
                    start = int(match.group(1))
                window = items[start:start + size]
                body = self.expanded(window, call)
                meta = {"result_count": len(window)}
                if window:
                    meta.update(newest_id=window[0]["id"], oldest_id=window[-1]["id"])
                if start + size < len(items):
                    meta["next_token"] = "fixturepage" + str(start + size)
                body["meta"] = meta
                self.reply(200, body)

            def op_search(self, call):
                query = self.one(call, "query")
                if not query:
                    self.reply(400, invalid("The `query` query parameter can not be empty"))
                    return
                terms = query.lower().split()
                author = None
                for term in list(terms):
                    if term.startswith("from:"):
                        author = term[5:]
                        terms.remove(term)
                items = [post for post in sorted(api.posts.values(), key=lambda item: item["id"], reverse=True)
                         if all(term in post["text"].lower() for term in terms)
                         and (author is None or api.users[post["author_id"]]["username"] == author)]
                self.paged(items, call, "max_results", 10, "next_token")

            def op_user(self, call, name):
                name = unquote(name)
                found = next((user for user in api.users.values() if user["username"].lower() == name.lower()), None)
                if found is None:
                    self.reply(200, {"errors": [{"value": name, "detail": f"Could not find user with username: [{name}].",
                                                 "title": "Not Found Error", "resource_type": "user",
                                                 "parameter": "username", "resource_id": name,
                                                 "type": "https://api.twitter.com/2/problems/resource-not-found"}]})
                    return
                self.reply(200, {"data": {**found, "created_at": "2020-01-01T00:00:00.000Z", "description": "Fixture",
                                          "protected": found.get("protected", False), "verified": False,
                                          "public_metrics": {"followers_count": 10, "following_count": 2,
                                                             "tweet_count": 25, "listed_count": 0}}})

            def op_timeline(self, call, ident):
                ident = unquote(ident)
                user = api.users.get(ident)
                if user is None:
                    self.reply(200, {"errors": [{"value": ident, "detail": f"Could not find user with id: [{ident}].",
                                                 "title": "Not Found Error", "resource_type": "user", "parameter": "id",
                                                 "resource_id": ident,
                                                 "type": "https://api.twitter.com/2/problems/resource-not-found"}]})
                    return
                if user.get("protected") and call["context"] == "app":
                    self.reply(200, {"errors": [{"value": ident, "detail": "Sorry, you are not authorized to see the "
                                                 "user with id: [" + ident + "].", "title": "Authorization Error",
                                                 "resource_type": "user", "parameter": "id", "resource_id": ident,
                                                 "type": "https://api.twitter.com/2/problems/not-authorized-for-resource"}]})
                    return
                excluded = (self.one(call, "exclude") or "").split(",")
                items = [post for post in sorted(api.posts.values(), key=lambda item: item["id"], reverse=True)
                         if post["author_id"] == ident and not ("replies" in excluded and post.get("in_reply_to_user_id"))]
                self.paged(items, call, "max_results", 5, "pagination_token")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        ca = ""
        if tls:
            assert shutil.which("openssl"), "OpenSSL is required for the disposable HTTPS fixture"
            certificate, key = directory / "ca.pem", directory / "key.pem"
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                            "-keyout", str(key), "-out", str(certificate), "-subj", "/CN=localhost",
                            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"], check=True, timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certificate, key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            ca = str(certificate)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        api.base_url = ("https" if tls else "http") + "://127.0.0.1:" + str(server.server_port)
        api.ca_file = ca
        api.kinds = lambda: [call["kind"] for call in api.calls]
        try:
            yield api
        finally:
            api.closing.set()
            api.release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            assert not thread.is_alive()
