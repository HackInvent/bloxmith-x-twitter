"""Bounded, cancellable HTTPS exchanges with the X API v2 and its OAuth 2.0 token endpoint.

Standard library only. Each exchange runs in a helper thread so cancellation,
Stop and the activation deadline can shut the socket down at once. Reads are
retried after HTTP 429, 5xx or a lost connection; writes only after HTTP 429 or
a connection that failed before anything was sent. Ambiguous writes are
reported, never replayed.

User-context grants: X issues access tokens valid for about two hours and
rotates the refresh token on every refresh. The block cannot write to the
wallet, so a refreshed pair is kept in this process's memory only, keyed by the
wallet reference and a digest of the wallet value; the wallet value itself is
never changed. See the README for the consequences.
"""

import base64
from collections import OrderedDict
import hashlib
import http.client
import json
import secrets as random_source
import socket
import ssl
import threading
import time
from urllib.parse import urlencode, urlsplit

from . import logic

CHUNK = 65536
USER_AGENT = "BloxSmith-X/0.1.0"
TOKEN_PATH = "/2/oauth2/token"
ACCEPTED_TYPES = ("application/json", "application/problem+json")
EXPIRY_MARGIN = 60
_GRANTS_LOCK = threading.Lock()
_GRANTS = OrderedDict()
_GRANT_LOCKS = {}


class Failure(Exception):
    """A content-free transport outcome: code and the phase it happened in."""

    def __init__(self, code, phase):
        super().__init__(code)
        self.code, self.phase = code, phase


def reject_constant(_value):
    raise ValueError("Non-finite JSON number.")


def remaining(deadline, phase):
    left = deadline - time.monotonic()
    if left <= 0:
        raise Failure("timeout", phase)
    return max(0.001, left)


def perform(state, origin, ca_file, method, path, headers, body, deadline, limit):
    """One request/response without redirects, cookies, proxies or decompression."""
    parsed = urlsplit(origin)
    host, port = parsed.hostname, parsed.port
    try:
        if parsed.scheme == "https":
            context = ssl.create_default_context(cafile=ca_file or None)
            conn = http.client.HTTPSConnection(host, port or 443, timeout=remaining(deadline, "connect"), context=context)
        else:
            conn = http.client.HTTPConnection(host, port or 80, timeout=remaining(deadline, "connect"))
        state["conn"] = conn
        conn.connect()
        if state.get("aborted"):
            raise Failure("network", "connect")
        state["phase"] = "send"
        conn.putrequest(method, path, skip_host=False, skip_accept_encoding=True)
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.putheader("Content-Length", str(len(body or b"")))
        conn.sock.settimeout(remaining(deadline, "send"))
        conn.endheaders(message_body=body or None)
        state["phase"] = "receive"
        conn.sock.settimeout(remaining(deadline, "receive"))
        response = conn.getresponse()
        chunks, total = [], 0
        while total <= limit:
            if conn.sock is not None:
                conn.sock.settimeout(remaining(deadline, "receive"))
            piece = response.read1(min(CHUNK, limit + 1 - total))
            if not piece:
                break
            total += len(piece)
            chunks.append(piece)
        found = {key.lower(): value for key, value in response.getheaders()}
        retry = found.get("retry-after", "").strip()
        kept = {key: value for key, value in found.items() if key.startswith("x-rate-limit-")}
        result = {"status": response.status, "retry_after": int(retry) if retry.isdigit() and len(retry) <= 6 else None,
                  "headers": kept, "body": None, "body_error": None}
        media = found.get("content-type", "").split(";", 1)[0].strip().lower()
        if total > limit:
            result["body_error"] = "too_large"
        elif found.get("content-encoding", "identity").strip().lower() not in ("identity", ""):
            result["body_error"] = "encoded"
        elif media not in ACCEPTED_TYPES:
            result["body_error"] = "not_json"
        else:
            try:
                result["body"] = json.loads(b"".join(chunks).decode("utf-8", errors="strict"),
                                            parse_constant=reject_constant)
            except (ValueError, UnicodeError, RecursionError):
                result["body_error"] = "invalid_json"
        return result
    except Failure:
        raise
    except ssl.SSLCertVerificationError:
        raise Failure("tls", state["phase"]) from None
    except ssl.SSLError:
        raise Failure("tls" if state["phase"] == "connect" else "network", state["phase"]) from None
    except (socket.timeout, TimeoutError):
        raise Failure("timeout", state["phase"]) from None
    except (OSError, http.client.HTTPException, ValueError, AttributeError):
        raise Failure("network", state["phase"]) from None
    finally:
        conn = state.get("conn")
        if conn is not None:
            conn.close()


def abort(state):
    """Shut the socket down from the supervising thread; the helper thread then fails fast."""
    state["aborted"] = True
    conn = state.get("conn")
    sock = getattr(conn, "sock", None) if conn is not None else None
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def exchange(origin, ca_file, method, path, headers, body, *, deadline, cancel, limit):
    """Run one request in a helper thread and supervise cancellation and the deadline."""
    state, box = {"phase": "connect", "conn": None}, {}

    def target():
        try:
            box["result"] = perform(state, origin, ca_file, method, path, headers, body, deadline, limit)
        except Failure as failure:
            box["failure"] = failure
        except Exception:  # Defensive: an unexpected library error is a transport failure.
            box["failure"] = Failure("network", state["phase"])

    worker = threading.Thread(target=target, name="x-twitter-http", daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(0.05)
        if not worker.is_alive():
            break
        if cancel():
            phase = state["phase"]
            abort(state)
            worker.join(2)
            raise Failure("cancelled", phase)
        if time.monotonic() >= deadline + 0.5:
            phase = state["phase"]
            abort(state)
            worker.join(2)
            raise Failure("timeout", phase)
    if "failure" in box:
        raise box["failure"]
    return box["result"]


def rate_wait(outcome, now=None):
    """Seconds to wait after HTTP 429: Retry-After, else x-rate-limit-reset (epoch seconds), else None."""
    if outcome.get("retry_after") is not None:
        return max(1, outcome["retry_after"])
    reset = (outcome.get("headers") or {}).get("x-rate-limit-reset", "").strip()
    if reset.isdigit() and len(reset) <= 12:
        now = time.time() if now is None else now
        return max(1, int(int(reset) - now + 0.999))
    return None


def grant_key(config, ref, grant):
    """Cache key: API origin, wallet reference and a digest of the wallet value (never the value itself)."""
    digest = hashlib.sha256(json.dumps(grant, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return (config["api_base_url"], ref, digest)


def cached_grant(key):
    with _GRANTS_LOCK:
        state = _GRANTS.get(key)
        return dict(state) if state else None


def store_grant(key, state):
    with _GRANTS_LOCK:
        _GRANTS[key] = dict(state)
        _GRANTS.move_to_end(key)
        while len(_GRANTS) > 64:
            stale, _value = _GRANTS.popitem(last=False)
            _GRANT_LOCKS.pop(stale, None)


def grant_lock(key):
    with _GRANTS_LOCK:
        return _GRANT_LOCKS.setdefault(key, threading.Lock())


def forget_grants():
    """Drop every refreshed pair held in memory (tests and process shutdown)."""
    with _GRANTS_LOCK:
        _GRANTS.clear()
        _GRANT_LOCKS.clear()


class Session:
    """One activation: one deadline, the wallet credentials and bounded requests."""

    def __init__(self, config, credentials, cancel=lambda: False):
        self.config = config
        self.ref = credentials.get("ref") or ""
        self.grant = credentials.get("user")
        self.bearer = credentials.get("app")
        self.cancel = cancel if callable(cancel) else (lambda: False)
        self.deadline = time.monotonic() + config["timeout_sec"]
        self.secrets = logic.secrets_of(self.grant, self.bearer)
        self.requests = 0
        self.refreshes = 0
        self.rate_limit = None
        self.auth = None
        self.key = grant_key(config, self.ref, self.grant) if self.grant else None

    def pause(self, seconds):
        """Cancellation-aware wait before a retry."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.cancel():
                logic.fail("cancelled", logic.MESSAGES["cancelled_before_send"])
            time.sleep(min(0.05, max(0.0, end - time.monotonic())))

    def send(self, method, path, headers, body, *, mutation):
        """One exchange mapped to a structured outcome; never raises a raw library error."""
        if self.cancel():
            logic.fail("cancelled", logic.MESSAGES["cancelled_before_send"])
        if self.deadline - time.monotonic() <= 0.2:
            logic.fail("timeout")
        self.requests += 1
        try:
            return exchange(self.config["api_base_url"], self.config["ca_file"], method, path, headers, body,
                            deadline=self.deadline, cancel=self.cancel, limit=self.config["max_response_bytes"])
        except Failure as failure:
            uncertain = mutation and failure.phase == "receive"
            if failure.code == "tls":
                logic.fail("tls")
            if failure.code == "timeout":
                logic.fail("timeout", logic.MESSAGES["timeout_uncertain" if uncertain else "timeout"], uncertain=uncertain)
            if failure.code == "cancelled":
                logic.fail("cancelled", logic.MESSAGES["cancelled" if uncertain else "cancelled_before_send"],
                           uncertain=uncertain)
            logic.fail("network", logic.MESSAGES["network_uncertain" if uncertain else "network"], uncertain=uncertain,
                       retryable=not mutation or failure.phase == "connect")

    def retrying(self, attempt, *, mutation):
        """Run attempt() with bounded retries: 429 always; 5xx and lost connections for reads only."""
        attempts = 0
        while True:
            attempts += 1
            try:
                return attempt()
            except logic.XError as exc:
                exc.attempts = attempts
                retryable = exc.code == "rate_limited" or (exc.code == "network" and exc.retryable) or (
                    not mutation and exc.code == "provider_unavailable")
                wait = exc.retry_after if exc.retry_after is not None else min(2 ** (attempts - 1), 8)
                if (retryable and attempts <= self.config["max_retries"] and wait <= self.config["max_retry_wait_sec"]
                        and time.monotonic() + wait + 1 < self.deadline):
                    self.pause(wait)
                    continue
                raise

    # -- OAuth 2.0 user context -------------------------------------------------

    def can_refresh(self, state):
        return bool(self.grant and state.get("refresh_token") and self.grant.get("client_id"))

    def hold(self, lock):
        """Wait for the grant lock without ignoring cancellation or the deadline."""
        while not lock.acquire(timeout=0.05):
            if self.cancel():
                logic.fail("cancelled", logic.MESSAGES["cancelled_before_send"])
            if time.monotonic() >= self.deadline:
                logic.fail("timeout")

    def user_token(self, *, stale=None):
        """Current user-context access token; refresh it when it is missing, expired or just refused."""
        if not self.grant:
            logic.fail("secret", "post and delete_post need secret_ref, the OAuth 2.0 user-context grant.")
        lock = grant_lock(self.key)
        self.hold(lock)
        try:
            state = cached_grant(self.key) or {"access_token": self.grant["access_token"],
                                               "refresh_token": self.grant["refresh_token"], "expires_at": None}
            for item in (state.get("access_token"), state.get("refresh_token")):
                if item and item not in self.secrets:
                    self.secrets.append(item)
            current = state.get("access_token")
            fresh = current and (state.get("expires_at") is None or state["expires_at"] - EXPIRY_MARGIN > time.time())
            if fresh and current != stale:
                return current
            if not self.can_refresh(state):
                if current and current != stale:
                    return current
                logic.fail("unauthorized", "X rejected the access token and the wallet holds no refresh_token and "
                                           "client_id to renew it; authorize again.", http_status=401)
            access, refresh, lifetime = self.refresh(state["refresh_token"])
            state = {"access_token": access, "refresh_token": refresh or state["refresh_token"],
                     "expires_at": time.time() + lifetime}
            store_grant(self.key, state)
            return access
        finally:
            lock.release()

    def refresh(self, refresh_token):
        """POST /2/oauth2/token with grant_type=refresh_token; never replayed after an ambiguous outcome."""
        form = [("grant_type", "refresh_token"), ("refresh_token", refresh_token)]
        headers = {"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": USER_AGENT,
                   "Connection": "close", "Content-Type": "application/x-www-form-urlencoded"}
        if self.grant.get("client_secret"):
            pair = (self.grant["client_id"] + ":" + self.grant["client_secret"]).encode("utf-8")
            basic = base64.b64encode(pair).decode("ascii")
            self.secrets.append(basic)
            headers["Authorization"] = "Basic " + basic
        else:
            form.append(("client_id", self.grant["client_id"]))
        body = urlencode(form).encode("ascii")

        def attempt():
            outcome = self.send("POST", TOKEN_PATH, headers, body, mutation=True)
            status = outcome["status"]
            if 200 <= status < 300:
                if outcome["body_error"]:
                    logic.fail("protocol", logic.MESSAGES["refresh_uncertain"], uncertain=True)
                result = logic.token_response(outcome["body"])
                self.secrets.extend(item for item in result[:2] if item)
                self.refreshes += 1
                return result
            if status in (400, 401):
                title, detail, _kind = logic.provider_detail(outcome["body"])
                logic.fail("reauthorization_required", http_status=status,
                           provider_title=logic.clean(logic.redact(title, self.secrets), 80),
                           provider_detail=logic.clean(logic.redact(detail, self.secrets), 300))
            if status >= 500:
                logic.fail("provider_unavailable", logic.MESSAGES["refresh_uncertain"], uncertain=True, http_status=status)
            logic.provider_failure(status, outcome["body"], self.secrets, mutation=False,
                                   retry_after=rate_wait(outcome) if status == 429 else None)

        try:
            return self.retrying(attempt, mutation=True)
        except logic.XError as exc:
            if exc.code in ("network", "timeout", "cancelled") and exc.uncertain:
                exc.args = (logic.MESSAGES["refresh_uncertain"],)
            raise

    # -- API calls ----------------------------------------------------------------

    def token(self, auth, stale=None):
        """Reads use the app-only bearer token when one is configured; everything else the user context."""
        if auth == "read" and self.bearer:
            return self.bearer, "app"
        return self.user_token(stale=stale), "user"

    def call(self, method, path, query=None, body=None, content_type=None, *, auth, mutation):
        """Call <api_base_url><path>[?query] and return its JSON body or raise a coded error."""
        target = path + ("?" + query if query else "")
        renewed = {"done": False}

        def attempt(stale=None):
            token, kind = self.token(auth, stale)
            self.auth = kind
            headers = {"Authorization": "Bearer " + token, "Accept": "application/json",
                       "Accept-Encoding": "identity", "User-Agent": USER_AGENT, "Connection": "close"}
            if content_type:
                headers["Content-Type"] = content_type
            outcome = self.send(method, target, headers, body, mutation=mutation)
            status = outcome["status"]
            self.rate_limit = logic.rate_limit(outcome["headers"]) or self.rate_limit
            if 200 <= status < 300:
                if outcome["body_error"] or not isinstance(outcome["body"], dict):
                    logic.fail("protocol", logic.MESSAGES["protocol_uncertain"] if mutation else None, uncertain=mutation)
                return outcome["body"]
            if status == 401 and kind == "user" and not renewed["done"]:
                state = cached_grant(self.key) or {"refresh_token": self.grant["refresh_token"]}
                if self.can_refresh(state):
                    # X did not process a request it refused with 401: renew once and send it again.
                    renewed["done"] = True
                    return attempt(stale=token)
            if status >= 500 and outcome["body_error"]:
                logic.fail("provider_unavailable", logic.MESSAGES["provider_unavailable_uncertain" if mutation else
                                                                  "provider_unavailable"], uncertain=mutation,
                           http_status=status)
            logic.provider_failure(status, outcome["body"], self.secrets, mutation=mutation,
                                   retry_after=rate_wait(outcome) if status == 429 else None)

        return self.retrying(attempt, mutation=mutation)


def boundary():
    return "BloxSmithX" + random_source.token_hex(16)
