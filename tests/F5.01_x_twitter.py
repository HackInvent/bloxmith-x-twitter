"""FB1/FB2/FB3/FB4/FB6/FB8: pure settings, credentials, weighted length, requests, encodings, provider errors and hooks.

No socket is opened and no provider is contacted; the wallet resolver is a local double.
"""

import json
import os
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(Path(__file__).resolve().parent)]
from blocs.x_twitter import files, logic, transport
from blocs.x_twitter.block import XTwitterBlock
from bloxsmith_app.block_api import BlockRuntimeContext, BlockRuntimePreparationContext
from x_fixture import ACCESS, APP_REF, BEARER, CLIENT_ID, CLIENT_SECRET, PNG, REFRESH, USER_REF

READER = {"enabled": True, "secret_ref": USER_REF, "allowed_operations": ["search_recent", "get_user", "user_posts"]}
WRITER = {**READER, "allow_writes": True, "operation": "post",
          "allowed_operations": ["post", "search_recent", "get_user", "user_posts"]}
DELETER = {**WRITER, "allow_delete": True, "allowed_operations": [*WRITER["allowed_operations"], "delete_post"]}
POST_ID = "1900000000000000001"
USER_ID = "1000000000000000002"
SECRETS = (ACCESS, REFRESH, BEARER, CLIENT_SECRET)


def rejected(call, code=None):
    """A contract violation must raise a coded error, never fall back silently."""
    try:
        call()
    except logic.XError as exc:
        assert code is None or exc.code == code, (code, exc.code, str(exc))
        assert not any(secret in str(exc) for secret in SECRETS)
        return exc
    raise AssertionError("Invalid value accepted")


def settings_contract():
    """# - FB1 - remote access and writes stay off by default and every setting is bounded."""
    defaults = logic.configuration({})
    assert defaults["enabled"] is False and defaults["allow_writes"] is False and defaults["allow_delete"] is False
    assert defaults["api_base_url"] == logic.OFFICIAL_API == "https://api.x.com"
    assert defaults["operation"] == "search_recent" and logic.selectable_operations(defaults) == ["search_recent"]
    assert defaults["max_weighted_length"] == 280 and defaults["page_size"] == 10 and defaults["max_pages"] == 1
    assert set(XTwitterBlock().default_config()) == set(logic.DEFAULTS)
    assert XTwitterBlock().default_config() == logic.DEFAULTS
    config = logic.configuration({**WRITER, "execution": {"inhibited": False}, "secret_ref": " " + USER_REF + " ",
                                  "media_root": " /srv/images "})
    assert config["secret_ref"] == USER_REF and config["media_root"] == "/srv/images"
    assert logic.selectable_operations(config) == ["post", "search_recent", "get_user", "user_posts"]
    frozen = logic.configuration(MappingProxyType({**READER, "allowed_operations": ("get_user",)}))
    assert frozen["allowed_operations"] == ["get_user"] and logic.selectable_operations(frozen) == ["search_recent", "get_user"]
    app_only = logic.configuration({"enabled": True, "bearer_secret_ref": APP_REF, "allowed_operations": ["get_user"]})
    assert app_only["secret_ref"] == "" and app_only["bearer_secret_ref"] == APP_REF
    assert "delete_post" in logic.selectable_operations(logic.configuration(DELETER))
    for raw in ({"enabled": True}, {**READER, "secret_ref": ""},
                {"enabled": True, "bearer_secret_ref": APP_REF, "allow_writes": True, "operation": "post"},
                {**READER, "secret_ref": ACCESS}, {**READER, "bearer_secret_ref": BEARER},
                {**READER, "secret_ref": "env://X_TOKEN"}, {**READER, "token": ACCESS}, {**READER, "client_id": CLIENT_ID},
                {**READER, "access_token": ACCESS}, {**READER, "operation": "like"}, {**READER, "operation": "post"},
                {**READER, "allowed_operations": ["post"]}, {**READER, "allowed_operations": []},
                {**READER, "allowed_operations": ["get_user", "get_user"]}, {**READER, "allowed_operations": "get_user"},
                {**WRITER, "allowed_operations": ["delete_post"]},
                {**READER, "allow_delete": True, "allowed_operations": ["delete_post"]},
                {**READER, "enabled": "yes"}, {**READER, "allow_writes": 1}, {**READER, "allow_delete": "false"},
                {**READER, "media_root": "images"}, {**READER, "media_root": "/srv/../etc"}, {**READER, "media_root": 7},
                {**READER, "ca_file": "ca.pem"}, {**READER, "ca_file": "/etc/../ca.pem"},
                {**READER, "api_base_url": "http://api.x.com"}, {**READER, "api_base_url": "https://api.x.com/2"},
                {**READER, "api_base_url": "https://user:pw@api.x.com"}, {**READER, "api_base_url": "https://x.test/?a=1"},
                {**READER, "api_base_url": "ftp://api.x.com"}, {**READER, "api_base_url": "https://bad host"},
                {**READER, "api_base_url": "https://-bad-.example"}, {**READER, "api_base_url": "http://example.test:8080"}):
        rejected(lambda raw=raw: logic.configuration(raw), "config")
    for key, (low, high) in logic.BOUNDS.items():
        assert logic.configuration({key: low})[key] == low and logic.configuration({key: high})[key] == high
        for value in (low - 1, high + 1, str(low), float(low), True):
            rejected(lambda key=key, value=value: logic.configuration({key: value}), "config")
    assert logic.configuration({"api_base_url": "http://127.0.0.1:8080/"})["api_base_url"] == "http://127.0.0.1:8080"
    assert logic.configuration({"api_base_url": "http://[::1]:9"})["api_base_url"] == "http://[::1]:9"
    assert logic.configuration({"api_base_url": "http://localhost:9"})["api_base_url"] == "http://localhost:9"
    print("[ok] X settings", flush=True)


def credential_contract():
    """# - FB1 - # - FB5 - wallet values: a user grant for writes, an optional app-only bearer for reads."""
    assert logic.user_grant(ACCESS) == {"access_token": ACCESS, "refresh_token": None, "client_id": None,
                                        "client_secret": None}
    full = logic.user_grant(json.dumps({"access_token": ACCESS, "refresh_token": REFRESH, "client_id": CLIENT_ID,
                                        "token_type": "bearer", "expires_in": 7200, "scope": "tweet.read"}))
    assert full["refresh_token"] == REFRESH and full["client_id"] == CLIENT_ID
    refresh_only = logic.user_grant({"refresh_token": REFRESH, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
    assert refresh_only["access_token"] is None and refresh_only["client_secret"] == CLIENT_SECRET
    for raw in ("", "two words", "{not json", json.dumps({"refresh_token": REFRESH}), json.dumps({"client_id": CLIENT_ID}),
                json.dumps({"access_token": ACCESS, "password": "x"}), json.dumps({"access_token": 7}),
                json.dumps({"access_token": ACCESS, "client_secret": CLIENT_SECRET}), json.dumps([ACCESS]), 7, None,
                json.dumps({"access_token": ACCESS, "access_token ": ACCESS}), json.dumps({})):
        exc = rejected(lambda raw=raw: logic.user_grant(raw), "secret")
        assert ACCESS not in json.dumps(exc.envelope()) and REFRESH not in json.dumps(exc.envelope())
    assert logic.app_bearer(BEARER) == BEARER and logic.app_bearer(" " + BEARER + "\n") == BEARER
    assert logic.app_bearer(json.dumps({"bearer_token": BEARER})) == BEARER
    assert logic.app_bearer({"access_token": BEARER, "token_type": "bearer"}) == BEARER
    for raw in ("", json.dumps({"bearer_token": BEARER, "access_token": BEARER}), json.dumps({"token": BEARER}),
                json.dumps({"bearer_token": "a b"}), 5):
        rejected(lambda raw=raw: logic.app_bearer(raw), "secret")
    secrets = logic.secrets_of(full, BEARER, {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
    assert CLIENT_ID not in secrets and {ACCESS, REFRESH, BEARER, CLIENT_SECRET} <= set(secrets)
    assert logic.redact("a " + ACCESS + " b " + REFRESH, secrets) == "a *** b ***"
    print("[ok] X credentials", flush=True)


def length_contract():
    """# - FB2 - X's weighted length: weight-2 scripts, links of 23, emoji sequences of 2, NFC, configurable limit."""
    cases = {"Hello": 5, "a" * 280: 280, "漢" * 140: 280, "café": 4, "café": 4, "Привет": 6,
             "see https://example.com/a/very/long/path?x=1 now": 31, "http://a.example/x.": 24, "example.com": 23,
             "docs.example.io/guide": 23, "file.txt": 8, "a.b": 3, "user@example.com": 16,
             "👨‍👩‍👧‍👦": 2, "🇫🇷": 2, "👍🏽": 2, "1️⃣": 2, "❤️": 2, "😀😀": 4, "©": 1, "©️": 2, "‘quoted’": 8,
             "https://example.com https://example.org": 47, "あ": 2, "\t\n": 2}
    for text, expected in cases.items():
        assert logic.weighted_length(text) == expected, (text, logic.weighted_length(text), expected)
    config = logic.configuration(WRITER)
    assert logic.parse_request("a" * 280, config)["weighted_length"] == 280
    exc = rejected(lambda: logic.parse_request("a" * 281, config), "text_too_long")
    assert "281 of 280" in str(exc)
    rejected(lambda: logic.parse_request("漢" * 141, config), "text_too_long")
    long_posts = logic.configuration({**WRITER, "max_weighted_length": 25000})
    assert logic.parse_request("a" * 4000, long_posts)["weighted_length"] == 4000
    link = "https://example.com/" + "x" * 500
    assert logic.parse_request("Read " + link, config)["weighted_length"] == 28
    print("[ok] X weighted length", flush=True)


def request_contract():
    """# - FB2 - # - FB3 - # - FB4 - one validated request per input, gated before the wallet is read."""
    reader, writer, deleter = (logic.configuration(item) for item in (READER, WRITER, DELETER))
    search = logic.parse_request("  workflow   automation ", reader)
    assert search == {"operation": "search_recent", "request_id": None, "query": "workflow automation", "max_results": 10,
                      "max_pages": 1, "next_token": None, "sort_order": None}
    paged = logic.parse_request({"operation": "search_recent", "query": "a", "max_results": 100, "next_token": "fixturepage10",
                                 "since_id": POST_ID, "start_time": "2026-01-02T03:04:05+02:00",
                                 "end_time": "2026-01-03T00:00:00Z", "sort_order": "relevancy", "request_id": "s-1"},
                                logic.configuration({**READER, "max_pages": 3}))
    assert paged["start_time"] == "2026-01-02T01:04:05Z" and paged["end_time"] == "2026-01-03T00:00:00Z"
    assert paged["since_id"] == POST_ID and paged["sort_order"] == "relevancy" and paged["request_id"] == "s-1"
    user = logic.parse_request({"operation": "get_user", "username": "@ada_fixture"}, reader)
    assert user == {"operation": "get_user", "request_id": None, "username": "ada_fixture"}
    assert logic.parse_request("@ada_fixture", logic.configuration({**READER, "operation": "get_user"}))["username"] == "ada_fixture"
    timeline = logic.parse_request({"operation": "user_posts", "user_id": int(USER_ID), "exclude": ["replies", "retweets"],
                                    "max_results": 5, "pagination_token": "fixturepage5"}, reader)
    assert timeline["user_id"] == USER_ID and timeline["exclude"] == ["retweets", "replies"] and timeline["username"] is None
    assert logic.parse_request("ada_fixture", logic.configuration({**READER, "operation": "user_posts"}))["username"] == "ada_fixture"
    post = logic.parse_request({"text": " Hello X ", "reply_to": POST_ID, "quote": int(POST_ID), "request_id": "p-1"}, writer)
    assert post == {"operation": "post", "request_id": "p-1", "text": "Hello X", "weighted_length": 7, "reply_to": POST_ID,
                    "quote": POST_ID, "media_path": None}
    assert logic.parse_request("Plain text post\n", writer)["text"] == "Plain text post"
    removal = logic.parse_request(" " + POST_ID + " ", logic.configuration({**DELETER, "operation": "delete_post"}))
    assert removal == {"operation": "delete_post", "request_id": None, "post_id": POST_ID}
    with_image = logic.configuration({**WRITER, "media_root": "/srv/images"})
    assert logic.parse_request({"media_path": "a.png"}, with_image)["media_path"] == "a.png"
    rejected(lambda: logic.parse_request({"text": "x", "media_path": "a.png"}, writer), "media_root_required")
    # The allowlist and both write switches are enforced before anything else.
    rejected(lambda: logic.parse_request({"operation": "post", "text": "x"}, reader), "operation_not_allowed")
    rejected(lambda: logic.parse_request({"operation": "post", "text": "x"}, {**writer, "allow_writes": False}), "writes_disabled")
    rejected(lambda: logic.parse_request({"operation": "delete_post", "post_id": POST_ID}, writer), "operation_not_allowed")
    rejected(lambda: logic.parse_request({"operation": "delete_post", "post_id": POST_ID}, {**deleter, "allow_delete": False}),
             "delete_disabled")
    rejected(lambda: logic.parse_request({"operation": "user_posts", "username": "x"},
                                         logic.configuration({**READER, "allowed_operations": ["get_user"]})),
             "operation_not_allowed")
    for value in ('{"query":', '{"operation": "drop"}', {"operation": ["post"]}, {"query": ""}, {"query": " "},
                  {"query": "x" * 4097}, {"query": "a\x00b"}, {"query": "a", "max_results": 9}, {"query": "a", "max_results": 101},
                  {"query": "a", "max_results": "10"}, {"query": "a", "max_pages": 2}, {"query": "a", "next_token": "bad token"},
                  {"query": "a", "since_id": "abc"}, {"query": "a", "until_id": "0"}, {"query": "a", "start_time": "yesterday"},
                  {"query": "a", "start_time": "2026-13-01T00:00:00Z"}, {"query": "a", "sort_order": "oldest"},
                  {"query": "a", "start_time": "2026-01-02T00:00:00Z", "end_time": "2026-01-01T00:00:00Z"},
                  {"query": "a", "text": "x"}, {"query": "a", "request_id": "bad id"}, {"query": "a", "request_id": "x" * 129},
                  {"operation": "get_user", "username": "has space"}, {"operation": "get_user", "username": "x" * 16},
                  {"operation": "get_user"}, {"operation": "get_user", "username": 7},
                  {"operation": "user_posts"}, {"operation": "user_posts", "user_id": USER_ID, "username": "ada"},
                  {"operation": "user_posts", "username": "ada", "max_results": 4}, {"operation": "user_posts", "username": "ada",
                                                                                      "exclude": ["likes"]},
                  {"operation": "user_posts", "username": "ada", "exclude": "replies"},
                  {"operation": "user_posts", "username": "ada", "next_token": "x"}, b"\xff\xfe"):
        rejected(lambda value=value: logic.parse_request(value, reader), "invalid_input")
    for value in ({}, {"text": ""}, {"text": "   "}, {"text": "a\x00"}, {"text": 7}, {"text": "x", "reply_to": "abc"},
                  {"text": "x", "quote": "-1"}, {"text": "x", "reply_to": "1" * 20}, {"text": "x", "media_path": ""},
                  {"text": "x", "media_path": "a\nb"}, {"text": "x", "path": "a.png"}, {"text": "x", "poll": {}}):
        rejected(lambda value=value: logic.parse_request(value, with_image), "invalid_input")
    for value in ({"operation": "delete_post"}, {"operation": "delete_post", "post_id": "x"}, "not-an-id"):
        rejected(lambda value=value: logic.parse_request(value, logic.configuration({**DELETER, "operation": "delete_post"})),
                 "invalid_input")
    rejected(lambda: logic.parse_request("x" * 70000, reader), "request_too_large")
    rejected(lambda: logic.parse_request({"query": "y" * 70000}, reader), "request_too_large")
    # The media input posts an image at once; with a request in the same activation the two form one post.
    single = logic.media_request("  shots/a.png ", with_image)
    assert single == {"operation": "post", "request_id": None, "text": "", "weighted_length": 0, "reply_to": None,
                      "quote": None, "media_path": "shots/a.png"}
    captioned = logic.media_request({"path": "a.png", "text": "Look", "reply_to": POST_ID, "request_id": "m-1"}, with_image)
    assert captioned["text"] == "Look" and captioned["reply_to"] == POST_ID and captioned["request_id"] == "m-1"
    rejected(lambda: logic.media_request("a.png", writer), "media_root_required")
    rejected(lambda: logic.media_request("a.png", logic.configuration({**READER, "media_root": "/srv/images"})),
             "operation_not_allowed")
    for value in ({"text": "no path"}, {"path": "a.png", "alt_text": "x"}, '{"path": 7}', {"path": ""}):
        rejected(lambda value=value: logic.media_request(value, with_image), "invalid_input")
    both = logic.combined_request({"text": "Caption", "quote": POST_ID, "request_id": "c-1"}, "a.png", with_image)
    assert both == {"operation": "post", "request_id": "c-1", "text": "Caption", "weighted_length": 7, "reply_to": None,
                    "quote": POST_ID, "media_path": "a.png"}
    assert logic.combined_request("Caption", {"path": "b.png"}, with_image)["media_path"] == "b.png"
    rejected(lambda: logic.combined_request({"operation": "search_recent", "query": "a"}, "a.png", with_image), "invalid_input")
    rejected(lambda: logic.combined_request({"text": "x", "media_path": "a.png"}, "b.png", with_image), "invalid_input")
    rejected(lambda: logic.combined_request("x", {"path": "a.png", "text": "y"}, with_image), "invalid_input")
    print("[ok] X requests", flush=True)


def encoding_contract():
    """# - FB2 - # - FB4 - exact query strings, post bodies and multipart uploads without the local file name."""
    reader = logic.configuration({**READER, "max_pages": 3})
    search = logic.parse_request({"query": "a b", "max_results": 50, "sort_order": "recency", "since_id": POST_ID}, reader)
    params = dict(logic.search_params(search, "fixturepage50"))
    assert params == {"query": "a b", "max_results": "50", "tweet.fields": logic.POST_FIELDS, "expansions": "author_id",
                      "user.fields": logic.USER_FIELDS, "since_id": POST_ID, "sort_order": "recency",
                      "next_token": "fixturepage50"}
    assert "note_tweet" in logic.POST_FIELDS and "author_id" in logic.POST_FIELDS
    timeline = logic.parse_request({"operation": "user_posts", "user_id": USER_ID, "exclude": ["replies"]}, reader)
    params = dict(logic.timeline_params(timeline, None))
    assert params["max_results"] == "10" and params["exclude"] == "replies" and "pagination_token" not in params
    assert dict(logic.timeline_params(timeline, "t1"))["pagination_token"] == "t1"
    writer = logic.configuration({**WRITER, "media_root": "/srv"})
    body = json.loads(logic.post_body(logic.parse_request({"text": "Hi", "reply_to": POST_ID, "quote": POST_ID}, writer),
                                      "1800000000000000001", 65536))
    assert body == {"text": "Hi", "reply": {"in_reply_to_tweet_id": POST_ID}, "quote_tweet_id": POST_ID,
                    "media": {"media_ids": ["1800000000000000001"]}}
    assert json.loads(logic.post_body(logic.parse_request({"media_path": "a.png"}, writer), "18", 65536)) == {
        "media": {"media_ids": ["18"]}}
    rejected(lambda: logic.post_body(logic.parse_request({"text": "é" * 600}, logic.configuration({
        **WRITER, "max_weighted_length": 25000})), None, 1024), "request_too_large")
    assert logic.image_type(PNG[:16]) == "image/png" and logic.image_type(b"\xff\xd8\xff\xe0" + b"0" * 12) == "image/jpeg"
    assert logic.image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    for head in (b"GIF89a" + b"0" * 10, b"%PDF-1.7", b"", b"RIFF\x00\x00\x00\x00WAVE"):
        rejected(lambda head=head: logic.image_type(head), "media_type")
    boundary = transport.boundary()
    assert boundary.startswith("BloxSmithX") and len(boundary) == 42
    data, content_type = logic.multipart(PNG, "image/png", boundary)
    assert content_type == "multipart/form-data; boundary=" + boundary
    assert b'name="media_category"\r\n\r\ntweet_image\r\n' in data and b'name="media_type"\r\n\r\nimage/png\r\n' in data
    assert b'name="media"; filename="image.png"\r\nContent-Type: image/png\r\n\r\n' + PNG + b"\r\n" in data
    assert data.endswith(("--" + boundary + "--\r\n").encode())
    rejected(lambda: logic.multipart(b"xx" + boundary.encode() + b"yy", "image/png", boundary), "internal")
    rejected(lambda: logic.multipart(PNG, "image/png", "short"), "internal")
    print("[ok] X encodings", flush=True)


def provider_contract():
    """# - FB4 - # - FB6 - X problem documents, partial errors, normalized posts and users, receipts and rate limits."""
    secrets = [ACCESS]
    for status, code in ((400, "bad_request"), (401, "unauthorized"), (402, "quota_exceeded"), (403, "forbidden"),
                         (404, "not_found"), (409, "conflict"), (413, "provider_too_large"), (429, "rate_limited"),
                         (500, "provider_unavailable"), (503, "provider_unavailable"), (418, "provider_error")):
        assert logic.map_status(status) == code
    exc = rejected(lambda: logic.provider_failure(403, {"title": "Forbidden", "detail": "Duplicate content " + ACCESS,
                                                        "type": "about:blank", "status": 403}, secrets, mutation=True), "forbidden")
    assert exc.provider_title == "Forbidden" and exc.provider_detail == "Duplicate content ***" and not exc.uncertain
    exc = rejected(lambda: logic.provider_failure(403, {"client_id": "1", "detail": "Use a Project app", "reason": "client-not-enrolled",
                                                        "title": "Client Forbidden", "required_enrollment": "Appropriate Level",
                                                        "type": "https://api.twitter.com/2/problems/client-forbidden"},
                                                  secrets, mutation=False), "forbidden")
    assert exc.provider_type == "https://api.twitter.com/2/problems/client-forbidden"
    exc = rejected(lambda: logic.provider_failure(429, {"title": "Too Many Requests"}, secrets, mutation=False,
                                                  retry_after=12), "rate_limited")
    assert exc.retry_after == 12 and exc.retryable
    capped = {"title": "UsageCapExceeded", "detail": "Usage cap exceeded: Monthly product cap",
              "type": "https://api.twitter.com/2/problems/usage-capped"}
    exc = rejected(lambda: logic.provider_failure(429, capped, secrets, mutation=False, retry_after=5), "usage_capped")
    assert exc.retry_after is None and not exc.retryable
    assert rejected(lambda: logic.provider_failure(502, None, secrets, mutation=True), "provider_unavailable").uncertain
    assert not rejected(lambda: logic.provider_failure(502, None, secrets, mutation=False), "provider_unavailable").uncertain
    invalid = {"errors": [{"parameters": {"max_results": ["5"]}, "message": "The `max_results` value is not between 10 and 100"}],
               "title": "Invalid Request", "detail": "One or more parameters to your request was invalid.",
               "type": "https://api.twitter.com/2/problems/invalid-request"}
    exc = rejected(lambda: logic.provider_failure(400, invalid, secrets, mutation=False), "bad_request")
    assert exc.provider_detail.startswith("The `max_results` value") and exc.provider_title == "Invalid Request"
    oauth = rejected(lambda: logic.provider_failure(400, {"error": "invalid_request", "error_description": "Bad"}, secrets,
                                                    mutation=False), "bad_request")
    assert oauth.provider_title == "invalid_request" and oauth.provider_detail == "Bad"
    envelope = exc.envelope(operation="search_recent", request_id="r-1", ignored=None)
    assert envelope["code"] == "bad_request" and envelope["http_status"] == 400 and "ignored" not in envelope
    missing = {"errors": [{"value": "nobody", "detail": "Could not find user with username: [nobody].", "title": "Not Found Error",
                           "type": "https://api.twitter.com/2/problems/resource-not-found"}]}
    assert rejected(lambda: logic.partial_failure(missing, secrets), "not_found").http_status == 200
    hidden = {"errors": [{"title": "Authorization Error", "type": "https://api.twitter.com/2/problems/not-authorized-for-resource"}]}
    rejected(lambda: logic.partial_failure(hidden, secrets), "forbidden")
    rejected(lambda: logic.partial_failure({"errors": [{"title": "Odd"}]}, secrets), "provider_error")
    users = [{"id": USER_ID, "username": "ada_fixture", "name": "Ada", "public_metrics": {"followers_count": 3, "x": "no"},
              "verified": False, "protected": False, "created_at": "2020-01-01T00:00:00.000Z"}]
    body = {"data": [{"id": POST_ID, "text": "Short…", "author_id": USER_ID, "note_tweet": {"text": "Long full text"},
                      "referenced_tweets": [{"type": "quoted", "id": "5"}, {"type": "other", "id": "6"}],
                      "attachments": {"media_keys": ["3_1"]}, "public_metrics": {"like_count": 2}},
                     {"id": "1900000000000000002", "text": "No author"}],
            "includes": {"users": users}, "meta": {"result_count": 2, "next_token": "fixturepage2", "newest_id": POST_ID}}
    posts, token, meta = logic.page(body, 10)
    assert token == "fixturepage2" and meta["newest_id"] == POST_ID
    assert posts[0] == {"id": POST_ID, "text": "Long full text", "author_id": USER_ID, "author_username": "ada_fixture",
                        "author_name": "Ada", "created_at": None, "conversation_id": None, "in_reply_to_user_id": None,
                        "lang": None, "referenced_posts": [{"type": "quoted", "id": "5"}], "media_keys": ["3_1"],
                        "metrics": {"like_count": 2}, "url": "https://x.com/ada_fixture/status/" + POST_ID}
    assert posts[1]["url"] == "https://x.com/i/web/status/1900000000000000002" and posts[1]["author_username"] is None
    assert logic.page({"meta": {"result_count": 0}}, 10) == ([], None, {"result_count": 0})
    for bad in (None, {"data": {}}, {"data": [{"id": "x", "text": "t"}]}, {"data": [{"id": "1"}]},
                {"data": [{"id": "1", "text": "t"}] * 11}, {"data": [], "meta": {"next_token": "a b"}},
                {"data": [], "includes": {"users": [{"id": "abc"}]}}):
        rejected(lambda bad=bad: logic.page(bad, 10), "protocol")
    found = logic.user(users[0])
    assert found["profile_url"] == "https://x.com/ada_fixture" and found["metrics"] == {"followers_count": 3}
    request = {"text": "Hi"}
    assert logic.created({"data": {"id": POST_ID, "text": "Hi"}}, request) == (POST_ID, "Hi")
    assert rejected(lambda: logic.created({"data": {"id": 5}}, request), "protocol").uncertain
    assert logic.uploaded({"data": {"id": "18", "media_key": "3_18", "processing_info": {"state": "succeeded"}}}) == ("18", "3_18")
    rejected(lambda: logic.uploaded({"data": {"id": "18", "processing_info": {"state": "failed"}}}), "media_rejected")
    rejected(lambda: logic.uploaded({"media_id": 18}), "protocol")
    assert logic.deleted({"data": {"deleted": True}}) is True
    rejected(lambda: logic.deleted({"data": {"deleted": False}}), "not_deleted")
    assert rejected(lambda: logic.deleted({"data": {}}), "protocol").uncertain
    assert logic.token_response({"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 7200}) == (ACCESS, REFRESH, 7200)
    assert logic.token_response({"access_token": ACCESS, "expires_in": "x"}) == (ACCESS, None, 7200)
    for bad in ({}, {"access_token": "a b"}, {"access_token": ACCESS, "refresh_token": 5}):
        assert rejected(lambda bad=bad: logic.token_response(bad), "protocol").uncertain
    assert logic.rate_limit({"x-rate-limit-limit": "300", "x-rate-limit-remaining": "0", "x-rate-limit-reset": "1767225600"}) == {
        "limit": 300, "remaining": 0, "reset": 1767225600}
    assert logic.rate_limit({"x-rate-limit-limit": "lots"}) is None and logic.rate_limit(None) is None
    assert transport.rate_wait({"retry_after": 7, "headers": {}}) == 7
    assert transport.rate_wait({"retry_after": None, "headers": {"x-rate-limit-reset": "1000"}}, now=990.5) == 10
    assert transport.rate_wait({"retry_after": None, "headers": {"x-rate-limit-reset": "1000"}}, now=2000) == 1
    assert transport.rate_wait({"retry_after": None, "headers": {}}) is None
    print("[ok] X provider documents", flush=True)


def files_contract():
    """# - FB2 - only single-link, non-empty JPEG, PNG or WEBP files inside media_root are read, never through a link."""
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        root = base / "images"
        (root / "sub").mkdir(parents=True)
        (root / "a.png").write_bytes(PNG)
        (root / "sub" / "b.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 64)
        (root / "big.png").write_bytes(PNG + b"0" * 4096)
        (root / "empty.png").write_bytes(b"")
        (root / "doc.png").write_bytes(b"%PDF-1.7 not an image")
        (base / "outside.png").write_bytes(PNG)
        os.symlink(base / "outside.png", root / "link.png")
        os.symlink(base, root / "dirlink")
        os.link(root / "a.png", base / "hardlinked.png")
        (root / "c.png").write_bytes(PNG)
        config = logic.configuration({**WRITER, "media_root": str(root), "max_media_bytes": 2048})
        assert files.read_image("c.png", config) == (PNG, "image/png")
        assert files.read_image(str(root / "sub" / "b.jpg"), config)[1] == "image/jpeg"
        assert files.read_image("sub/b.jpg", config)[1] == "image/jpeg"
        for path, code in (("a.png", "media_not_allowed"), ("link.png", "media_not_allowed"),
                           ("dirlink/outside.png", "media_not_allowed"), ("../outside.png", "media_not_allowed"),
                           (str(base / "outside.png"), "media_not_allowed"), ("sub", "media_not_allowed"),
                           ("missing.png", "media_not_found"),
                           ("big.png", "media_too_large"), ("empty.png", "media_empty"), ("doc.png", "media_type"),
                           ("/", "media_not_allowed")):
            rejected(lambda path=path: files.read_image(path, config), code)
        rejected(lambda: files.read_image("c.png", logic.configuration(WRITER)), "media_root_required")
        rejected(lambda: files.read_image("c.png", logic.configuration({**WRITER, "media_root": str(base / "missing")})),
                 "media_root_required")
        rejected(lambda: files.read_image("etc/hostname", logic.configuration({**WRITER, "media_root": "/"})),
                 "media_root_required")
    print("[ok] X image files", flush=True)


def block_contract():
    """# - FB1 - # - FB6 - # - FB8 - block hooks fail before side effects, and surfaces stay bounded and translated."""
    block = XTwitterBlock()
    ports_in = tuple(SimpleNamespace(id=port["id"], name=port["name"]) for port in block.model["ports"]["inputs"])
    ports_out = tuple(SimpleNamespace(id=port["id"], name=port["name"]) for port in block.model["ports"]["outputs"])
    assert [port.name for port in ports_in] == ["request", "media"]
    assert [port.name for port in ports_out] == ["posts", "result", "error"]
    assert all(port["execution_requirement"] == "not_required_for_execution" for port in block.model["ports"]["inputs"])
    assert "file/path" in block.model["ports"]["inputs"][1]["accepts"]
    assert block.model["version"] == "0.1.0" and block.model["repository_url"].endswith("/bloxmith-x-twitter")
    calls = []

    def resolver(ref):
        calls.append(ref)
        return "not json {" + ACCESS

    def run(config, inputs):
        context = BlockRuntimeContext(config={**block.default_config(), **config}, inputs=inputs, input_ports=ports_in,
                                      output_ports=ports_out, services={"resolve_secret": resolver})
        result = block.execute_runtime(context)
        assert result.status in ("success", "skipped"), result
        return result, {output.port_name: json.loads(output.value) for output in result.outputs}

    assert run(READER, {})[0].status == "skipped"
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / "doc.png").write_bytes(b"%PDF")
        images = {**WRITER, "media_root": folder}
        for config, inputs, code in (({**READER, "enabled": False}, {"request": "go"}, "disabled"),
                                     (READER, {"request": {"operation": "post", "text": "x"}}, "operation_not_allowed"),
                                     (READER, {"request": '{"query":'}, "invalid_input"),
                                     (WRITER, {"request": "a" * 281}, "text_too_long"),
                                     (WRITER, {"media": "a.png"}, "media_root_required"),
                                     (images, {"media": "missing.png"}, "media_not_found"),
                                     (images, {"media": "doc.png"}, "media_type"),
                                     (images, {"request": {"operation": "search_recent", "query": "a"}, "media": "doc.png"},
                                      "invalid_input"),
                                     ({**READER, "secret_ref": ACCESS}, {"request": "go"}, "config")):
            result, outputs = run(config, inputs)
            assert set(outputs) == {"error"} and outputs["error"]["code"] == code, outputs
            assert result.metadata["x_twitter"]["code"] == code and result.metadata["x_twitter"]["uncertain"] is False
            assert ACCESS not in json.dumps(outputs) and ACCESS not in " ".join(result.logs)
    assert not calls, "The wallet must not be read before settings, request and image validation"
    result, outputs = run(READER, {"request": {"request_id": "req-1", "query": "x"}})
    assert outputs["error"]["code"] == "secret" and outputs["error"]["request_id"] == "req-1" and calls == [USER_REF]
    assert outputs["error"]["operation"] == "search_recent"
    assert ACCESS not in json.dumps(outputs) and result.logs == ["[x_twitter-error] search_recent: secret"]
    calls.clear()
    run({"enabled": True, "bearer_secret_ref": APP_REF, "secret_ref": USER_REF}, {"request": "x"})
    assert calls == [APP_REF], "reads use the app-only bearer when it is configured"
    calls.clear()
    run({**WRITER, "bearer_secret_ref": APP_REF}, {"request": "Hello"})
    assert calls == [USER_REF], "posts always use the user-context grant"

    def prepared(config):
        context = BlockRuntimePreparationContext.from_context(SimpleNamespace(config=config, runtime_mode="zeromq_active"))
        return block.prepare_runtime(context)

    assert prepared(WRITER).listen_on_run is False
    rejected(lambda: prepared({**READER, "secret_ref": ACCESS}), "config")
    node = block.build_node_payload(node_id="x", position={"x": 0, "y": 0})
    accepted = block.handle_ui_action(node=node, action="save_settings", values={"node_patch": {"config": DELETER}})
    operation = accepted["graph_operations"][-1]
    assert operation["op"] == "update_node_config" and operation["replace"] is True
    assert operation["config"]["allowed_operations"] == DELETER["allowed_operations"]
    refused = block.handle_ui_action(node=node, action="save_settings",
                                     values={"node_patch": {"config": {**READER, "operation": "post"}}})
    assert "allow_writes" in refused["error"] and "graph_operations" not in refused
    html = block.render_modal(node=node)["html"]
    assert 'data-block-config-field="secret_ref"' in html and 'data-block-config-field="bearer_secret_ref"' in html
    assert 'data-x-twitter-show="write" hidden' in html and 'data-block-value-type="json"' in html and "{{" not in html
    assert 'data-block-config-field="media_root"' in html and 'data-i18n="block.x_twitter.help_media_root"' in html
    writing = {**node, "config": {**node["config"], **WRITER}}
    assert 'data-x-twitter-show="write" hidden' not in block.render_inspector_panel(node=writing)["html"]
    card = block.render_node_card(node=node)["html"]
    assert card.count("Remote access off") == 1 and 'data-i18n="block.x_twitter.card_disabled" data-i18n-params=""' in card
    assert "Read only · search_recent" in block.render_node_card(node={**node, "config": {**node["config"], **READER}})["html"]
    card = block.render_node_card(node=writing)["html"]
    assert ("Read and post · post" in card and 'data-i18n="block.x_twitter.card_write" '
            'data-i18n-params="{&quot;operation&quot;: &quot;post&quot;}"' in card)
    deleting = {**node, "config": {**node["config"], **DELETER, "operation": "delete_post"}}
    assert "Read, post and delete · delete_post" in block.render_node_card(node=deleting)["html"]
    assert USER_REF not in card
    assert block.translate("block.x_twitter.card_write", {"operation": "post"}, lang="fr") == "Lecture et publication · post"
    print("[ok] X block hooks and surfaces", flush=True)


def main():
    settings_contract()
    credential_contract()
    length_contract()
    request_contract()
    encoding_contract()
    provider_contract()
    files_contract()
    block_contract()
    print("[ok] X settings, credentials, length, requests, encodings, provider documents, files and hooks")


if __name__ == "__main__":
    main()
