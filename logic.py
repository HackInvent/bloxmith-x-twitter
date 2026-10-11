"""Pure X API v2 contracts: settings, requests, weighted text length, responses and errors.

No network, filesystem, framework or third-party import happens here. Every
function is deterministic, so the policy can be checked directly by the tests.
"""

from collections.abc import Mapping
from datetime import datetime, timezone
import ipaddress
import json
import math
import re
import unicodedata
from urllib.parse import urlsplit

OFFICIAL_API = "https://api.x.com"
OPERATIONS = ("post", "delete_post", "search_recent", "get_user", "user_posts")
WRITES = frozenset(("post", "delete_post"))
READS = frozenset(("search_recent", "get_user", "user_posts"))
DEFAULTS = {
    "enabled": False, "secret_ref": "", "bearer_secret_ref": "", "operation": "search_recent",
    "allowed_operations": ["search_recent"], "allow_writes": False, "allow_delete": False, "media_root": "",
    "max_media_bytes": 5242880, "max_weighted_length": 280, "page_size": 10, "max_pages": 1, "timeout_sec": 60,
    "max_retries": 2, "max_retry_wait_sec": 30, "max_request_bytes": 65536, "max_response_bytes": 4194304,
    "api_base_url": OFFICIAL_API, "ca_file": "",
}
FRAMEWORK_KEYS = {"execution", "runtime_path", "runtime_path_label"}
BOUNDS = {
    "max_media_bytes": (1024, 5242880), "max_weighted_length": (1, 25000), "page_size": (10, 100),
    "max_pages": (1, 20), "timeout_sec": (5, 300), "max_retries": (0, 5), "max_retry_wait_sec": (1, 300),
    "max_request_bytes": (1024, 1048576), "max_response_bytes": (65536, 33554432),
}
BOOLEANS = ("enabled", "allow_writes", "allow_delete")
SECRET_REF = re.compile(r"secret://(?:workspace/[A-Za-z0-9_.-]{1,80}|project/[A-Za-z0-9_.-]{1,80}/[A-Za-z0-9_.-]{1,80})\Z")
SNOWFLAKE = re.compile(r"[0-9]{1,19}\Z")
USERNAME = re.compile(r"[A-Za-z0-9_]{1,15}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
TOKEN_TEXT = re.compile(r"[\x21-\x7e]{1,8192}\Z")
CLIENT_TEXT = re.compile(r"[\x21-\x7e]{1,512}\Z")
PAGE_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,512}\Z")
MEDIA_ID = re.compile(r"[0-9]{1,24}\Z")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z")
HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
QUERY_LIMIT = 4096  # X accepts 512 characters on lower access levels and 4,096 on higher ones; X decides.
SEARCH_RESULTS = (10, 100)
TIMELINE_RESULTS = (5, 100)
EXCLUDES = ("retweets", "replies")
SORT_ORDERS = ("recency", "relevancy")
MEDIA_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
REQUEST_KEYS = {
    "post": {"operation", "request_id", "text", "reply_to", "quote", "media_path"},
    "delete_post": {"operation", "request_id", "post_id"},
    "search_recent": {"operation", "request_id", "query", "max_results", "max_pages", "next_token", "since_id",
                      "until_id", "start_time", "end_time", "sort_order"},
    "get_user": {"operation", "request_id", "username"},
    "user_posts": {"operation", "request_id", "user_id", "username", "max_results", "max_pages", "pagination_token",
                   "exclude", "since_id", "until_id", "start_time", "end_time"},
}
MEDIA_KEYS = {"path", "text", "reply_to", "quote", "request_id"}
POST_FIELDS = ("id,text,author_id,created_at,conversation_id,in_reply_to_user_id,lang,public_metrics,"
               "referenced_tweets,attachments,note_tweet")
USER_FIELDS = "id,name,username,created_at,description,location,profile_image_url,protected,public_metrics,verified"

# twitter-text v3 weighting: these code point ranges weigh 1, everything else weighs 2; a URL counts as 23
# and an emoji sequence counts as 2. Weights are stored at a scale of 100 as twitter-text does.
WEIGHT_RANGES = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
URL_WEIGHT = 23
BARE_TLDS = ("com", "net", "org", "io", "ai", "dev", "app", "co", "me", "info", "biz", "xyz")
SCHEME_URL = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
BARE_URL = re.compile(r"(?<![\w@.#/-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:" + "|".join(BARE_TLDS)
                      + r")(?![\w-])(?::\d{1,5})?(?:[/?#][^\s<>\"]*)?", re.IGNORECASE)
TRAILING = ".,;:!?'\")]}"
_EMOJI_BASE = ("‼⁉™ℹ↔-↪⌚-⏿Ⓜ▪-➿⤴⤵"
               "⬅-⭕〰〽㊗㊙\U0001f000-\U0001faff")
_MODIFIER = "(?:️|[\U0001f3fb-\U0001f3ff])?"
EMOJI = re.compile(
    "[\U0001f1e6-\U0001f1ff]{2}"                       # flag pairs
    "|[0-9#*]️?⃣"                            # keycaps
    "|\U0001f3f4[\U000e0020-\U000e007e]+\U000e007f"    # subdivision flags
    "|[©®]️"                            # (c) and (r) only with emoji presentation
    "|[" + _EMOJI_BASE + "]" + _MODIFIER + "(?:‍[" + _EMOJI_BASE + "]" + _MODIFIER + ")*")

MESSAGES = {
    "config": "Invalid X settings.",
    "disabled": "Remote access is disabled; enable the block explicitly before contacting X.",
    "secret": "Unlock the wallet and store the X credential described in the README under the configured reference.",
    "invalid_input": "The request does not follow the documented contract; nothing was sent.",
    "operation_not_allowed": "This operation is not listed in allowed_operations; nothing was sent.",
    "writes_disabled": "Writes are disabled; enable allow_writes in the properties; nothing was posted.",
    "delete_disabled": "delete_post needs allow_delete in the properties; nothing was deleted.",
    "request_too_large": "The request exceeds max_request_bytes; nothing was sent.",
    "text_too_long": "The post text exceeds max_weighted_length by X's weighted count; nothing was sent.",
    "media_root_required": "Set media_root to a dedicated directory before attaching an image; nothing was sent.",
    "media_not_allowed": "The image path is outside media_root, is a link or is not a regular file; nothing was sent.",
    "media_not_found": "The image file does not exist inside media_root; nothing was sent.",
    "media_too_large": "The image exceeds max_media_bytes; nothing was sent.",
    "media_empty": "The image file is empty; nothing was sent.",
    "media_type": "Only JPEG, PNG and WEBP images can be attached; nothing was sent.",
    "media_rejected": "X could not process the uploaded image; nothing was posted.",
    "bad_request": "X rejected the request (HTTP 400); nothing was changed.",
    "unauthorized": "X rejected the credential (HTTP 401); check or replace the wallet secret.",
    "reauthorization_required": "X refused the refresh token (it is used only once and may already be consumed, "
                                "expired or revoked); authorize again and store the new tokens in the wallet.",
    "quota_exceeded": "The X developer account has no remaining paid access for this request (HTTP 402).",
    "forbidden": "X refused this action for the credential or access level (HTTP 403); nothing was changed.",
    "not_found": "The post or user was not found, or is not visible to this credential.",
    "conflict": "X reported a conflict with the current state (HTTP 409); nothing was changed.",
    "provider_too_large": "X refused the request body as too large (HTTP 413); nothing was changed.",
    "rate_limited": "X rate limit reached (HTTP 429); retry after the indicated delay.",
    "usage_capped": "The X usage cap of the developer account is reached (HTTP 429); retrying will not help.",
    "provider_error": "X returned an unexpected error status; nothing is assumed changed.",
    "provider_unavailable": "X is temporarily unavailable (HTTP 5xx); nothing was changed.",
    "provider_unavailable_uncertain": "X answered HTTP 5xx after the write was sent; the change is uncertain.",
    "not_deleted": "X answered that the post was not deleted.",
    "network": "The connection to X failed before the request was complete; nothing was changed.",
    "network_uncertain": "The connection failed after the write was sent; the change is uncertain.",
    "tls": "TLS verification failed for the X API origin; nothing was sent.",
    "timeout": "The operation exceeded timeout_sec before any write was complete; nothing was changed.",
    "timeout_uncertain": "The operation exceeded timeout_sec after the write was sent; the change is uncertain.",
    "cancelled": "The operation was cancelled after the write was sent; the change is uncertain.",
    "cancelled_before_send": "The operation was cancelled before the next request was sent.",
    "protocol": "X returned an invalid, compressed or oversized response.",
    "protocol_uncertain": "X accepted the write but returned an unexpected receipt; the change is likely.",
    "refresh_uncertain": "The token refresh was sent but its answer was lost; the stored refresh token may be consumed.",
    "internal": "The X operation failed without a complete result.",
}


class XError(ValueError):
    """A content-free diagnostic with a stable machine code and an English message."""

    def __init__(self, code, detail=None, *, uncertain=False, http_status=None, retry_after=None, provider_title=None,
                 provider_type=None, provider_detail=None, retryable=False):
        super().__init__(detail or MESSAGES.get(code, MESSAGES["internal"]))
        self.code, self.uncertain, self.retryable = code, bool(uncertain), bool(retryable)
        self.http_status, self.retry_after = http_status, retry_after
        self.provider_title, self.provider_type, self.provider_detail = provider_title, provider_type, provider_detail
        self.attempts = 0
        self.extra = {}

    def envelope(self, **context):
        """Return the public error document; callers add identifiers only, never post text or tokens."""
        result = {"code": self.code, "message": str(self), "uncertain": self.uncertain}
        for key, value in (("http_status", self.http_status), ("retry_after", self.retry_after),
                           ("provider_title", self.provider_title), ("provider_type", self.provider_type),
                           ("provider_detail", self.provider_detail)):
            if value is not None:
                result[key] = value
        if self.attempts:
            result["attempts"] = self.attempts
        result.update(self.extra)
        for key, value in context.items():
            if value is not None:
                result[key] = value
        return result


def fail(code, detail=None, **extra):
    """Raise a structured failure."""
    raise XError(code, detail, **extra)


def thaw(value):
    """Turn immutable preparation snapshots back into plain JSON containers."""
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value


def encoded(value, limit, code="invalid_input"):
    """Serialize strict finite JSON with bounded depth, element count and bytes."""
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 24 or count > 200000:
            fail(code, "JSON exceeds its depth or element limit.")
        if isinstance(item, dict):
            if any(type(key) is not str for key in item):
                fail(code, "JSON object keys must be strings.")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend((child, depth + 1) for child in item)
        elif item is not None and type(item) not in (str, int, float, bool):
            fail(code, "A strict JSON value is required.")
        elif type(item) is float and not math.isfinite(item):
            fail(code, "Non-finite numbers are not accepted.")
    try:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        fail(code, "Finite UTF-8 JSON is required.")
    if len(data) > limit:
        fail("request_too_large" if code == "invalid_input" else code, "JSON exceeds its byte limit.")
    return data


def strict_json(text, limit, code="invalid_input"):
    """Parse JSON text, rejecting duplicate keys, non-finite numbers and oversized input."""
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8", errors="strict")
        except UnicodeError:
            fail(code, "UTF-8 JSON is required.")
    if not isinstance(text, str):
        fail(code, "JSON text is required.")
    if len(text.encode("utf-8", errors="replace")) > limit:
        fail("request_too_large" if code == "invalid_input" else code, "JSON exceeds its byte limit.")

    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                fail(code, "Duplicate JSON property.")
            result[key] = item
        return result

    def constant(_name):
        fail(code, "Non-finite numbers are not accepted.")

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except XError:
        raise
    except (ValueError, RecursionError):
        fail(code, "Malformed JSON.")
    encoded(value, limit, code)
    return value


def boolean(value, name, code="config"):
    if type(value) is not bool:
        fail(code, f"{name} must be a boolean.")
    return value


def integer(value, name, low, high, code="config"):
    if type(value) is not int or not low <= value <= high:
        fail(code, f"{name} must be an integer between {low} and {high}.")
    return value


def path_text(value, name):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        fail("config", f"{name} must be a bounded path without control characters.")
    value = value.strip()
    if value and (not value.startswith("/") or ".." in value.split("/")):
        fail("config", f"{name} must be empty or an absolute path without '..' components.")
    return value


def is_loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def api_origin(value, name):
    """Accept an HTTPS origin; plain HTTP only for a loopback host such as a local test double."""
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or any(ord(char) <= 32 or ord(char) >= 127 for char in value):
        fail("config", f"{name} must be an ASCII URL without spaces.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        fail("config", f"{name} has an invalid host or port.")
    host = (parsed.hostname or "").lower()
    if (parsed.scheme not in ("https", "http") or not host or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment or "?" in value or "#" in value
            or parsed.path not in ("", "/") or "\\" in value or "%" in host):
        fail("config", f"{name} must be a bare https:// origin without credentials, path, query or fragment.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or host.endswith(".") or any(not HOST_LABEL.fullmatch(label) for label in host.split(".")):
            fail("config", f"{name} has an invalid host name.")
    if port is not None and not 1 <= port <= 65535:
        fail("config", f"{name} has an invalid port.")
    if parsed.scheme == "http" and not is_loopback(host):
        fail("config", f"Plain HTTP is accepted for {name} only with a loopback test host; use HTTPS.")
    netloc = "[" + host + "]" if ":" in host else host
    return parsed.scheme + "://" + netloc + (":" + str(port) if port is not None else "")


def wallet_ref(value, name):
    if not isinstance(value, str) or len(value) > 256:
        fail("config", f"{name} must be a wallet reference such as secret://workspace/x_twitter.")
    value = value.strip()
    if value and not SECRET_REF.fullmatch(value):
        fail("config", f"{name} must be a wallet reference such as secret://workspace/x_twitter, never a token.")
    return value


def operations_list(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= len(OPERATIONS)
            or any(item not in OPERATIONS for item in value) or len(set(value)) != len(value)):
        fail("config", "allowed_operations must list distinct values among " + ", ".join(OPERATIONS) + ".")
    return value


def configuration(raw):
    """Pure settings validation; no wallet, filesystem or network access."""
    raw = thaw(raw)
    if not isinstance(raw, Mapping):
        fail("config", "Settings must be a JSON object.")
    unknown = set(raw) - set(DEFAULTS) - FRAMEWORK_KEYS
    if unknown:
        fail("config", "Unknown setting: " + sorted(unknown)[0] + ". Credentials belong in the wallet.")
    config = {**{key: thaw(value) for key, value in DEFAULTS.items()},
              **{key: value for key, value in raw.items() if key in DEFAULTS}}
    for key in BOOLEANS:
        boolean(config[key], key)
    for key, (low, high) in BOUNDS.items():
        integer(config[key], key, low, high)
    config["secret_ref"] = wallet_ref(config["secret_ref"], "secret_ref")
    config["bearer_secret_ref"] = wallet_ref(config["bearer_secret_ref"], "bearer_secret_ref")
    if config["operation"] not in OPERATIONS:
        fail("config", "operation must be one of: " + ", ".join(OPERATIONS) + ".")
    config["allowed_operations"] = operations_list(config["allowed_operations"])
    selectable = set(config["allowed_operations"]) | {config["operation"]}
    if selectable & WRITES and not config["allow_writes"]:
        fail("config", "post and delete_post need allow_writes; enable it or remove them.")
    if "delete_post" in selectable and not config["allow_delete"]:
        fail("config", "delete_post needs allow_delete; enable it or remove delete_post.")
    config["media_root"] = path_text(config["media_root"], "media_root")
    config["ca_file"] = path_text(config["ca_file"], "ca_file")
    config["api_base_url"] = api_origin(config["api_base_url"], "api_base_url")
    if config["enabled"]:
        if selectable & WRITES and not config["secret_ref"]:
            fail("config", "post and delete_post need secret_ref, a wallet reference holding the OAuth 2.0 user "
                           "token grant.")
        if not config["secret_ref"] and not config["bearer_secret_ref"]:
            fail("config", "Remote access requires secret_ref (user context) or bearer_secret_ref (app-only reads).")
    return config


def selectable_operations(config):
    return [item for item in OPERATIONS if item == config["operation"] or item in config["allowed_operations"]]


# ---------------------------------------------------------------------------
# Credentials


def wallet_document(value, accepted):
    """A wallet value: bare token text or a JSON object whose keys are all among `accepted`."""
    secret = value
    if isinstance(secret, str):
        text = secret.strip()
        if text.startswith(("{", "[")):
            try:
                secret = strict_json(text, 65536, "secret")
            except XError:
                fail("secret")
        else:
            secret = {"access_token": text}
    if not isinstance(secret, Mapping) or not secret or not set(secret) <= accepted:
        fail("secret")
    return dict(secret)


def user_grant(value):
    """The OAuth 2.0 user-context grant: access_token and/or refresh_token with client_id."""
    grant = wallet_document(value, {"access_token", "refresh_token", "client_id", "client_secret", "token_type",
                                    "expires_in", "scope", "expires_at"})
    result = {}
    for key in ("access_token", "refresh_token"):
        item = grant.get(key)
        if item is not None and (not isinstance(item, str) or not TOKEN_TEXT.fullmatch(item)):
            fail("secret")
        result[key] = item
    for key in ("client_id", "client_secret"):
        item = grant.get(key)
        if item is not None and (not isinstance(item, str) or not CLIENT_TEXT.fullmatch(item)):
            fail("secret")
        result[key] = item
    if result["refresh_token"] and not result["client_id"]:
        fail("secret", "A refresh_token in the wallet needs the client_id of the X app that issued it.")
    if not result["access_token"] and not result["refresh_token"]:
        fail("secret")
    if result["client_secret"] and not result["client_id"]:
        fail("secret")
    return result


def app_bearer(value):
    """The app-only bearer token: bare text, {"bearer_token": ...} or {"access_token": ...}."""
    if isinstance(value, str) and not value.strip().startswith(("{", "[")):
        token = value.strip()
    else:
        document = wallet_document(value, {"bearer_token", "access_token", "token_type"})
        tokens = [document.get(key) for key in ("bearer_token", "access_token") if document.get(key) is not None]
        if len(tokens) != 1:
            fail("secret")
        token = tokens[0]
    if not isinstance(token, str) or not TOKEN_TEXT.fullmatch(token):
        fail("secret")
    return token


def secrets_of(*documents):
    values = []
    for document in documents:
        if isinstance(document, str):
            values.append(document)
        elif isinstance(document, Mapping):
            values.extend(item for key, item in document.items() if key != "client_id" and isinstance(item, str))
    return [item for item in values if item]


def redact(text, secrets):
    """Mask every credential value wherever it could appear."""
    text = "" if text is None else str(text)
    for secret in sorted((item for item in secrets if item and len(item) >= 4), key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


def clean(value, limit):
    """Bounded single-line provider text with control characters removed."""
    if not isinstance(value, str):
        return None
    value = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in value).strip()
    return value[:limit] if value else None


# ---------------------------------------------------------------------------
# Weighted text length


def url_spans(text):
    """Spans counted as one link of 23: scheme URLs and bare domains of a fixed list of common TLDs."""
    spans = []
    for pattern in (SCHEME_URL, BARE_URL):
        for match in pattern.finditer(text):
            start, end = match.span()
            while end > start and text[end - 1] in TRAILING:
                end -= 1
            if end - start < 4 or any(start < other_end and other_start < end for other_start, other_end in spans):
                continue
            if pattern is SCHEME_URL and "." not in text[start:end].split("://", 1)[1].split("/", 1)[0]:
                continue
            spans.append((start, end))
    return sorted(spans)


def weighted_length(text):
    """Approximate twitter-text v3 weighted length of `text` after NFC normalization.

    Code points in the weight-1 ranges count 1, others 2; each link counts 23; an
    emoji sequence (ZWJ family, flag, keycap, skin tone, variation selector) counts 2.
    X remains the final judge: the API refuses a post that is too long.
    """
    text = unicodedata.normalize("NFC", text)
    total, index = 0, 0
    links = url_spans(text)
    while index < len(text):
        link = next((span for span in links if span[0] == index), None)
        if link:
            total += URL_WEIGHT * 100
            index = link[1]
            continue
        emoji = EMOJI.match(text, index)
        if emoji and emoji.end() > index:
            total += 200
            index = emoji.end()
            continue
        point = ord(text[index])
        total += 100 if any(low <= point <= high for low, high in WEIGHT_RANGES) else 200
        index += 1
    return total // 100


# ---------------------------------------------------------------------------
# Requests


def identifier(value):
    if value is None:
        return None
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        fail("invalid_input", "request_id needs 1-128 ASCII letters, digits or ._:- characters.")
    return value


def snowflake(value, label):
    """A post or user ID: X sends them as decimal strings; an integer is accepted and converted."""
    if type(value) is int and value > 0:
        value = str(value)
    if not isinstance(value, str) or not SNOWFLAKE.fullmatch(value.strip()) or int(value) <= 0:
        fail("invalid_input", f"{label} must be a numeric X ID, given as a string of up to 19 digits.")
    return value.strip()


def username(value):
    if not isinstance(value, str):
        fail("invalid_input", "username must be text.")
    text = value.strip()
    text = text[1:] if text.startswith("@") else text
    if not USERNAME.fullmatch(text):
        fail("invalid_input", "username must be 1-15 letters, digits or underscores, with an optional leading @.")
    return text


def timestamp(value, label):
    """RFC 3339 time, normalized to the YYYY-MM-DDTHH:MM:SSZ form X documents."""
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value.strip()):
        fail("invalid_input", f"{label} must be an RFC 3339 time such as 2026-01-31T12:00:00Z.")
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        fail("invalid_input", f"{label} must be a valid date and time.")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def page_token(value, label):
    if not isinstance(value, str) or not PAGE_TOKEN.fullmatch(value):
        fail("invalid_input", f"{label} must be the token returned by a previous result.")
    return value


def text_value(value, label, limit):
    """Post text: NUL and other control characters except tab and line breaks are refused."""
    if not isinstance(value, str):
        fail("invalid_input", f"{label} must be text.")
    if len(value) > limit or any((ord(char) < 32 and char not in "\t\r\n") or ord(char) == 127 for char in value):
        fail("invalid_input", f"{label} must be bounded text without control characters.")
    return value.strip()


def document(value, config, primary):
    """Read a JSON object, or plain text as {primary: text}; '{' must start valid JSON."""
    if isinstance(value, Mapping):
        result = thaw(value)
        encoded(result, config["max_request_bytes"])
        return result
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="strict")
        except UnicodeError:
            fail("invalid_input", "The request must be UTF-8 text or a JSON object.")
    if not isinstance(value, str):
        fail("invalid_input", "The request must be UTF-8 text or a JSON object.")
    if len(value.encode("utf-8", errors="replace")) > config["max_request_bytes"]:
        fail("request_too_large")
    if value.lstrip().startswith("{"):
        parsed = strict_json(value.strip(), config["max_request_bytes"])
        if not isinstance(parsed, dict):
            fail("invalid_input", "A value starting with { must be one JSON object.")
        return parsed
    return {primary: value} if primary else {}


PRIMARY = {"post": "text", "delete_post": "post_id", "search_recent": "query", "get_user": "username",
           "user_posts": "username"}


def gate(operation, config):
    """Enforce the operation allowlist and the write switches before any wallet access."""
    if operation not in OPERATIONS:
        fail("invalid_input", "operation must be one of: " + ", ".join(OPERATIONS) + ".")
    if operation not in selectable_operations(config):
        fail("operation_not_allowed")
    if operation in WRITES and not config["allow_writes"]:
        fail("writes_disabled")
    if operation == "delete_post" and not config["allow_delete"]:
        fail("delete_disabled")


def reading_bounds(data, config, low_high):
    low, high = low_high
    size = data.get("max_results", max(low, min(config["page_size"], high)))
    return (integer(size, "max_results", low, high, "invalid_input"),
            integer(data.get("max_pages", config["max_pages"]), "max_pages", 1, config["max_pages"], "invalid_input"))


def time_window(data, request):
    for key in ("since_id", "until_id"):
        if key in data:
            request[key] = snowflake(data[key], key)
    for key in ("start_time", "end_time"):
        if key in data:
            request[key] = timestamp(data[key], key)
    if request.get("start_time") and request.get("end_time") and request["start_time"] >= request["end_time"]:
        fail("invalid_input", "start_time must be before end_time.")


def post_fields(data, request, config):
    """Text, reply target, quoted post and image path of a post request."""
    text = text_value(data.get("text", ""), "text", 100000)
    request["text"] = text
    request["weighted_length"] = weighted_length(text) if text else 0
    if request["weighted_length"] > config["max_weighted_length"]:
        fail("text_too_long", MESSAGES["text_too_long"] + f" Counted {request['weighted_length']} of "
                                                          f"{config['max_weighted_length']}.")
    request["reply_to"] = snowflake(data["reply_to"], "reply_to") if data.get("reply_to") is not None else None
    request["quote"] = snowflake(data["quote"], "quote") if data.get("quote") is not None else None
    path = data.get("media_path")
    if path is not None:
        if not isinstance(path, str) or not path.strip() or len(path) > 4096 or any(ord(char) < 32 for char in path):
            fail("invalid_input", "media_path must be a non-empty file path without control characters.")
        if not config["media_root"]:
            fail("media_root_required")
        path = path.strip()
    request["media_path"] = path
    if not text and path is None:
        fail("invalid_input", "A post needs text, an image, or both.")
    return request


def structured(value):
    """True when the value is a JSON object (or text starting with '{'), false for plain text."""
    if isinstance(value, Mapping):
        return True
    if isinstance(value, bytes):
        return value.lstrip().startswith(b"{")
    return isinstance(value, str) and value.lstrip().startswith("{")


def parse_request(value, config):
    """Return one normalized request from the request input; the allowlist is enforced before the wallet.

    Plain text is the main parameter of the default operation: the text of a post, a post ID,
    a search query or a username.
    """
    if structured(value):
        data = document(value, config, None)
        operation = data.get("operation", config["operation"])
    else:
        operation = config["operation"]
        data = document(value, config, PRIMARY[operation])
    gate(operation, config)
    request = {"operation": operation, "request_id": identifier(data.get("request_id"))}
    unknown = set(data) - REQUEST_KEYS[operation]
    if unknown:
        fail("invalid_input", "Unknown field for " + operation + ": " + sorted(unknown)[0][:64] + ".")
    if operation == "post":
        return post_fields(data, request, config)
    if operation == "delete_post":
        request["post_id"] = snowflake(data.get("post_id"), "post_id")
        return request
    if operation == "get_user":
        request["username"] = username(data.get("username"))
        return request
    if operation == "search_recent":
        query = data.get("query")
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > QUERY_LIMIT or any(
                ord(char) < 32 and char not in "\t\r\n" for char in query):
            fail("invalid_input", f"query must be X search text of 1-{QUERY_LIMIT} characters.")
        request["query"] = " ".join(query.split())
        request["max_results"], request["max_pages"] = reading_bounds(data, config, SEARCH_RESULTS)
        request["next_token"] = page_token(data["next_token"], "next_token") if data.get("next_token") else None
        sort = data.get("sort_order")
        if sort is not None and sort not in SORT_ORDERS:
            fail("invalid_input", "sort_order must be recency or relevancy.")
        request["sort_order"] = sort
        time_window(data, request)
        return request
    # user_posts
    if ("user_id" in data) == ("username" in data):
        fail("invalid_input", "user_posts needs exactly one of user_id or username.")
    request["user_id"] = snowflake(data["user_id"], "user_id") if "user_id" in data else None
    request["username"] = username(data["username"]) if "username" in data else None
    request["max_results"], request["max_pages"] = reading_bounds(data, config, TIMELINE_RESULTS)
    token = data.get("pagination_token")
    request["pagination_token"] = page_token(token, "pagination_token") if token else None
    exclude = data.get("exclude", [])
    if not isinstance(exclude, list) or any(item not in EXCLUDES for item in exclude) or len(set(exclude)) != len(exclude):
        fail("invalid_input", "exclude must be a JSON array among retweets and replies.")
    request["exclude"] = [item for item in EXCLUDES if item in exclude]
    time_window(data, request)
    return request


def media_request(value, config):
    """The media input: an image path, or {"path", "text", "reply_to", "quote", "request_id"}, posted at once."""
    data = document(value, config, "path")
    unknown = set(data) - MEDIA_KEYS
    if unknown:
        fail("invalid_input", "Unknown media field: " + sorted(unknown)[0][:64] + ".")
    if "path" not in data:
        fail("invalid_input", "The media input needs path, the image file inside media_root.")
    gate("post", config)
    request = {"operation": "post", "request_id": identifier(data.get("request_id"))}
    return post_fields({**{key: item for key, item in data.items() if key != "path"}, "media_path": data["path"]},
                       request, config)


def combined_request(request_value, media_value, config):
    """A request and an image delivered in the same activation form one post with that image."""
    request = parse_request(request_value, config)
    if request["operation"] != "post":
        fail("invalid_input", "An image on the media input can only go with a post request.")
    if request["media_path"] is not None:
        fail("invalid_input", "Give the image either on the media input or as media_path, not both.")
    image = document(media_value, config, "path")
    if set(image) != {"path"}:
        fail("invalid_input", "With a request in the same activation, the media input carries only the image path.")
    data = {"text": request["text"], "media_path": image["path"]}
    for key in ("reply_to", "quote"):
        if request[key] is not None:
            data[key] = request[key]
    return post_fields(data, {"operation": "post", "request_id": request["request_id"]}, config)


# ---------------------------------------------------------------------------
# Images


def image_type(head):
    """Detect JPEG, PNG or WEBP by content, never by file name."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    fail("media_type")


def multipart(data, media_type, boundary):
    """multipart/form-data for POST /2/media/upload; the local file name is never sent."""
    if not re.fullmatch(r"[A-Za-z0-9]{16,70}", boundary) or boundary.encode("ascii") in data:
        fail("internal")
    name = "image." + MEDIA_TYPES[media_type]
    parts = []
    for field, value in (("media_category", "tweet_image"), ("media_type", media_type)):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n{value}\r\n'.encode("ascii"))
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="media"; filename="{name}"\r\n'
                  f"Content-Type: {media_type}\r\n\r\n").encode("ascii") + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


# ---------------------------------------------------------------------------
# Query strings and bodies


def search_params(request, token):
    params = [("query", request["query"]), ("max_results", str(request["max_results"])),
              ("tweet.fields", POST_FIELDS), ("expansions", "author_id"), ("user.fields", USER_FIELDS)]
    for key in ("since_id", "until_id", "start_time", "end_time", "sort_order"):
        if request.get(key):
            params.append((key, request[key]))
    if token:
        params.append(("next_token", token))
    return params


def timeline_params(request, token):
    params = [("max_results", str(request["max_results"])), ("tweet.fields", POST_FIELDS),
              ("expansions", "author_id"), ("user.fields", USER_FIELDS)]
    if request["exclude"]:
        params.append(("exclude", ",".join(request["exclude"])))
    for key in ("since_id", "until_id", "start_time", "end_time"):
        if request.get(key):
            params.append((key, request[key]))
    if token:
        params.append(("pagination_token", token))
    return params


def post_body(request, media_id, limit):
    body = {}
    if request["text"]:
        body["text"] = request["text"]
    if request["reply_to"]:
        body["reply"] = {"in_reply_to_tweet_id": request["reply_to"]}
    if request["quote"]:
        body["quote_tweet_id"] = request["quote"]
    if media_id:
        body["media"] = {"media_ids": [media_id]}
    return encoded(body, limit, "request_too_large")


# ---------------------------------------------------------------------------
# Provider responses


def provider_detail(body):
    """X problem documents: {"title", "detail", "type", "reason"}, {"errors": [...]} or OAuth {"error", "error_description"}."""
    if not isinstance(body, dict):
        return None, None, None
    title, detail, kind = body.get("title"), body.get("detail"), body.get("type")
    errors = body.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        first = errors[0]
        title = title or first.get("title")
        detail = first.get("message") or first.get("detail") or detail
        kind = kind or first.get("type")
    if isinstance(body.get("error"), str):
        title = title or body["error"]
        detail = detail or body.get("error_description")
    if isinstance(body.get("reason"), str):
        kind = kind or body["reason"]
    return title, detail, kind


def map_status(status):
    return {400: "bad_request", 401: "unauthorized", 402: "quota_exceeded", 403: "forbidden", 404: "not_found",
            409: "conflict", 413: "provider_too_large", 429: "rate_limited"}.get(
                status, "provider_unavailable" if status >= 500 else "provider_error")


def usage_capped(body):
    title, _detail, kind = provider_detail(body)
    return (isinstance(title, str) and title.lower().replace(" ", "") == "usagecapexceeded") or (
        isinstance(kind, str) and kind.rstrip("/").endswith("usage-capped"))


def provider_failure(status, body, secrets, *, mutation, retry_after=None):
    """Raise the coded error for a non-2xx X response."""
    title, detail, kind = provider_detail(body)
    code = map_status(status)
    if code == "rate_limited" and usage_capped(body):
        code = "usage_capped"
    uncertain = mutation and code == "provider_unavailable"
    fail(code, MESSAGES["provider_unavailable_uncertain"] if uncertain else None, uncertain=uncertain,
         http_status=status, retry_after=retry_after if code == "rate_limited" else None,
         provider_title=clean(redact(title, secrets), 80), provider_type=clean(redact(kind, secrets), 160),
         provider_detail=clean(redact(detail, secrets), 300), retryable=code == "rate_limited")


def partial_failure(body, secrets):
    """HTTP 200 without data but with errors: X's way of saying a user or post is missing or hidden."""
    title, detail, kind = provider_detail(body)
    kind_text = kind if isinstance(kind, str) else ""
    if kind_text.endswith("resource-not-found"):
        code = "not_found"
    elif kind_text.endswith("not-authorized-for-resource") or kind_text.endswith("resource-unavailable"):
        code = "forbidden"
    else:
        code = "provider_error"
    fail(code, None if code != "forbidden" else "The resource is protected, suspended or not visible to this credential.",
         http_status=200, provider_title=clean(redact(title, secrets), 80),
         provider_type=clean(redact(kind, secrets), 160), provider_detail=clean(redact(detail, secrets), 300))


def text_of(item, key, limit=None):
    value = item.get(key) if isinstance(item, dict) else None
    if not isinstance(value, str):
        return None
    return value[:limit] if limit else value


def metrics(value):
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str) and type(item) is int}


def user(item):
    """Normalize one X user object."""
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not SNOWFLAKE.fullmatch(item["id"]):
        fail("protocol")
    name = text_of(item, "username", 64)
    return {"id": item["id"], "username": name, "name": text_of(item, "name", 200),
            "description": text_of(item, "description", 2000), "created_at": text_of(item, "created_at", 40),
            "location": text_of(item, "location", 200), "protected": item.get("protected") is True,
            "verified": item.get("verified") is True, "profile_image_url": text_of(item, "profile_image_url", 2048),
            "metrics": metrics(item.get("public_metrics")),
            "profile_url": ("https://x.com/" + name) if name and USERNAME.fullmatch(name) else None}


def post(item, users):
    """Normalize one X post; a long post's full text comes from note_tweet when X provides it."""
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not SNOWFLAKE.fullmatch(item["id"]):
        fail("protocol")
    if not isinstance(item.get("text"), str):
        fail("protocol")
    note = item.get("note_tweet")
    text = note["text"] if isinstance(note, dict) and isinstance(note.get("text"), str) else item["text"]
    author = item.get("author_id") if isinstance(item.get("author_id"), str) else None
    known = users.get(author) or {}
    handle = known.get("username")
    references = []
    for reference in item.get("referenced_tweets") or []:
        if isinstance(reference, dict) and isinstance(reference.get("id"), str) and reference.get("type") in (
                "replied_to", "quoted", "retweeted"):
            references.append({"type": reference["type"], "id": reference["id"]})
    attachments = item.get("attachments") if isinstance(item.get("attachments"), dict) else {}
    keys = [key for key in attachments.get("media_keys") or [] if isinstance(key, str)]
    url = ("https://x.com/" + handle + "/status/" + item["id"]) if handle else "https://x.com/i/web/status/" + item["id"]
    return {"id": item["id"], "text": text, "author_id": author, "author_username": handle,
            "author_name": known.get("name"), "created_at": text_of(item, "created_at", 40),
            "conversation_id": text_of(item, "conversation_id", 24), "in_reply_to_user_id": text_of(item, "in_reply_to_user_id", 24),
            "lang": text_of(item, "lang", 16), "referenced_posts": references, "media_keys": keys,
            "metrics": metrics(item.get("public_metrics")), "url": url}


def page(body, size):
    """One search or timeline page: posts, users by ID, the meta block and the next token."""
    if not isinstance(body, dict):
        fail("protocol")
    data = body.get("data", [])
    meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
    if not isinstance(data, list) or len(data) > size:
        fail("protocol")
    includes = body.get("includes") if isinstance(body.get("includes"), dict) else {}
    users = {}
    for item in includes.get("users") or []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            normalized = user(item)
            users[normalized["id"]] = normalized
    token = meta.get("next_token")
    if token is not None and (not isinstance(token, str) or not PAGE_TOKEN.fullmatch(token)):
        fail("protocol")
    return [post(item, users) for item in data], token, meta


def created(body, request):
    """Receipt of POST /2/tweets: {"data": {"id", "text"}}."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not SNOWFLAKE.fullmatch(data["id"]):
        fail("protocol", MESSAGES["protocol_uncertain"], uncertain=True)
    text = data.get("text") if isinstance(data.get("text"), str) else request["text"]
    return data["id"], text


def uploaded(body):
    """Receipt of POST /2/media/upload: {"data": {"id", "media_key", "processing_info"?}}."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        fail("protocol")
    ident = data.get("id")
    if not isinstance(ident, str) or not MEDIA_ID.fullmatch(ident):
        fail("protocol")
    info = data.get("processing_info")
    if isinstance(info, dict) and info.get("state") == "failed":
        fail("media_rejected")
    return ident, text_of(data, "media_key", 64)


def deleted(body):
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict) or type(data.get("deleted")) is not bool:
        fail("protocol", MESSAGES["protocol_uncertain"], uncertain=True)
    if not data["deleted"]:
        fail("not_deleted")
    return True


def token_response(body):
    """OAuth 2.0 token endpoint answer: a new access token and, as X rotates them, a new refresh token."""
    if not isinstance(body, dict) or not isinstance(body.get("access_token"), str) or not TOKEN_TEXT.fullmatch(
            body["access_token"]):
        fail("protocol", MESSAGES["refresh_uncertain"], uncertain=True)
    refresh = body.get("refresh_token")
    if refresh is not None and (not isinstance(refresh, str) or not TOKEN_TEXT.fullmatch(refresh)):
        fail("protocol", MESSAGES["refresh_uncertain"], uncertain=True)
    lifetime = body.get("expires_in")
    lifetime = lifetime if type(lifetime) is int and 0 < lifetime <= 31536000 else 7200
    return body["access_token"], refresh, lifetime


def rate_limit(headers):
    """x-rate-limit-limit / -remaining / -reset of the last response, when X sends them."""
    result = {}
    for key, name in (("limit", "x-rate-limit-limit"), ("remaining", "x-rate-limit-remaining"),
                      ("reset", "x-rate-limit-reset")):
        value = (headers or {}).get(name, "")
        if isinstance(value, str) and value.strip().isdigit() and len(value.strip()) <= 12:
            result[key] = int(value.strip())
    return result or None
