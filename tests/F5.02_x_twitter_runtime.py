"""FB2/FB3/FB4/FB5/FB6/FB7: loopback X protocol, token refresh, errors, cancellation and both runtimes.

Every provider exchange targets the loopback double in x_fixture.py. The wallet
and its fixture credentials exist only in disposable test storage.
"""

import base64
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(Path(__file__).resolve().parent)]
from blocs.x_twitter import files, logic, operations, transport
from blocs.x_twitter.block import XTwitterBlock
from block_test_packages import install_test_package, prepare_release_run, surface_payload
from x_fixture import (ACCESS, ADA, APP_REF, BEARER, CAPPED, CLIENT_ID, CLIENT_SECRET, LIMITED, ME, PNG, PROTECTED, REFRESH,
                       USER_REF, USERS, grant, invalid, problem, settings, x_api)
from ui_smoke_common import (create_project_api, create_run_api, data_edge, display_node, graph_payload, http_json,
                             isolated_server, stop_run_api, text_node, wait_for_run_predicate, wait_for_run_terminal)

READS = ["search_recent", "get_user", "user_posts"]
ALL = ["post", "delete_post", *READS]
BASE_SECRETS = (ACCESS, REFRESH, BEARER, CLIENT_SECRET)


def credentials(value=None, bearer=None):
    """What the block reads from the wallet: a user grant, an app-only bearer, or both."""
    return {"ref": USER_REF if value is not None else APP_REF, "user": logic.user_grant(value) if value is not None else None,
            "app": logic.app_bearer(bearer) if bearer is not None else None}


def run(config, value, creds=None, cancel=lambda: False, media=None):
    """Execute one request through the real image reader, session and operations."""
    request = logic.parse_request(value, config) if media is None else logic.combined_request(value, media, config)
    image = files.read_image(request["media_path"], config) if request.get("media_path") else None
    session = transport.Session(config, creds or credentials(grant()), cancel)
    return operations.run(session, request, config, image)


def leaked(api, text):
    secrets = [*BASE_SECRETS, *(item for pair in api.issued for item in pair)]
    return [secret for secret in secrets if secret in text]


def failure(api, call, code):
    try:
        call()
    except logic.XError as exc:
        assert exc.code == code, (code, exc.code, str(exc))
        assert not leaked(api, json.dumps(exc.envelope())), exc.envelope()
        return exc
    raise AssertionError("Expected " + code)


def read_protocol():
    """# - FB4 - recent search pages and continuation, user lookup, user timelines, app-only reads and X refusals."""
    transport.forget_grants()
    with x_api() as api:
        config = logic.configuration(settings(api, allowed_operations=READS, max_pages=3))
        first = run(config, {"query": "workflow", "max_pages": 1, "request_id": "s-1"})
        assert len(first["posts"]) == 10 and first["result"]["count"] == 10 and first["result"]["truncated"] is True
        result = first["result"]
        assert result["next_token"] == "fixturepage10" and result["pages"] == 1 and result["requests"] == 1
        assert result["request_id"] == "s-1" and result["auth"] == "user" and result["query"] == "workflow"
        assert result["newest_id"] == first["posts"][0]["id"] and result["oldest_id"] == first["posts"][-1]["id"]
        assert result["rate_limit"]["limit"] == 300 and result["rate_limit"]["remaining"] == 299
        call = api.calls[-1]
        assert call["method"] == "GET" and call["path"] == "/2/tweets/search/recent"
        assert call["query"]["query"] == ["workflow"] and call["query"]["max_results"] == ["10"]
        assert call["query"]["tweet.fields"] == [logic.POST_FIELDS] and call["query"]["expansions"] == ["author_id"]
        assert call["query"]["user.fields"] == [logic.USER_FIELDS] and "next_token" not in call["query"]
        assert call["auth"] == "Bearer " + ACCESS and call["accept_encoding"] == "identity"
        assert call["user_agent"] == transport.USER_AGENT
        ada = next(post for post in first["posts"] if post["author_id"] == ADA["id"])
        assert ada["author_username"] == "ada_fixture" and ada["url"] == "https://x.com/ada_fixture/status/" + ada["id"]
        assert ada["metrics"]["like_count"] == 3 and ada["text"].startswith("Workflow note")
        rest = run(config, {"query": "workflow", "next_token": result["next_token"], "max_pages": 3})
        assert api.calls[-2]["query"]["next_token"] == ["fixturepage10"] and api.calls[-1]["query"]["next_token"] == ["fixturepage20"]
        assert rest["result"]["count"] == 16 and rest["result"]["truncated"] is False and rest["result"]["next_token"] is None
        ids = [post["id"] for post in first["posts"] + rest["posts"]]
        assert len(ids) == len(set(ids)) == 26, "no post skipped or repeated across continuation"
        whole = run(config, {"query": "workflow", "max_results": 100})
        assert whole["result"]["count"] == 26 and whole["result"]["pages"] == 1
        capped = run(config, "workflow")
        assert capped["result"]["pages"] == 3 and capped["result"]["count"] == 26 and capped["result"]["truncated"] is False
        only = run(config, {"query": "from:ada_fixture note 25"})
        assert [post["text"] for post in only["posts"]] == ["Workflow note 25 about automation"]
        empty = run(config, {"query": "nothing matches this"})
        assert empty["posts"] == [] and empty["result"]["count"] == 0 and empty["result"]["newest_id"] is None
        # App-only bearer for reads, when configured.
        apps = logic.configuration(settings(api, bearer_secret_ref=APP_REF, allowed_operations=READS))
        app_read = run(apps, "workflow", creds=credentials(bearer=BEARER))
        assert app_read["result"]["auth"] == "app" and api.calls[-1]["auth"] == "Bearer " + BEARER
        # User lookup and timelines.
        user = run(config, {"operation": "get_user", "username": "@ada_fixture", "request_id": "u-1"})["result"]
        assert user["user"]["id"] == ADA["id"] and user["user"]["metrics"]["followers_count"] == 10
        assert user["user"]["profile_url"] == "https://x.com/ada_fixture" and user["request_id"] == "u-1"
        assert api.calls[-1]["path"] == USERS + "/by/username/ada_fixture"
        assert api.calls[-1]["query"]["user.fields"] == [logic.USER_FIELDS]
        missing = failure(api, lambda: run(config, {"operation": "get_user", "username": "nobody_here"}), "not_found")
        assert missing.http_status == 200 and missing.provider_title == "Not Found Error"
        assert missing.provider_type.endswith("resource-not-found")
        before = len(api.calls)
        timeline = run(config, {"operation": "user_posts", "username": "ada_fixture", "max_results": 5, "max_pages": 2})
        assert [call["kind"] for call in api.calls[before:]] == ["user", "timeline", "timeline"]
        assert timeline["result"]["user_id"] == ADA["id"] and timeline["result"]["username"] == "ada_fixture"
        assert timeline["result"]["count"] == 10 and timeline["result"]["next_token"] == "fixturepage10"
        assert all(post["author_id"] == ADA["id"] for post in timeline["posts"])
        assert api.calls[-1]["path"] == USERS + "/" + ADA["id"] + "/tweets"
        assert api.calls[-1]["query"]["pagination_token"] == ["fixturepage5"] and api.calls[-1]["query"]["max_results"] == ["5"]
        following = run(config, {"operation": "user_posts", "user_id": ADA["id"], "exclude": ["replies"],
                                 "pagination_token": "fixturepage10", "max_pages": 3})
        assert following["result"]["count"] == 15 and following["result"]["truncated"] is False
        assert api.calls[-1]["query"]["exclude"] == ["replies"] and following["result"]["username"] is None
        hidden = failure(api, lambda: run(apps, {"operation": "user_posts", "user_id": PROTECTED["id"]},
                                          creds=credentials(bearer=BEARER)), "forbidden")
        assert hidden.provider_type.endswith("not-authorized-for-resource")
        failure(api, lambda: run(config, {"operation": "user_posts", "user_id": "1000000000000000999"}), "not_found")
        api.script["search"] = [{"status": 400, "body": invalid("The `query` operator is not supported")}]
        refused = failure(api, lambda: run(config, "workflow"), "bad_request")
        assert refused.provider_detail == "The `query` operator is not supported" and refused.http_status == 400
        api.script["user"] = [{"status": 404, "body": problem(404, "Not Found Error", "Not found")}]
        assert failure(api, lambda: run(config, {"operation": "get_user", "username": "ada_fixture"}),
                       "not_found").http_status == 404
    print("[ok] X search pages, continuation, users, timelines, app-only reads and refusals", flush=True)


@contextmanager
def image_root():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "image.png").write_bytes(PNG)
        (root / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4)
        yield root


def write_protocol():
    """# - FB2 - # - FB3 - posts with replies, quotes and an uploaded image; deletion; refusals; no replay."""
    transport.forget_grants()
    with x_api() as api, image_root() as root:
        config = logic.configuration(settings(api, allow_writes=True, allow_delete=True, allowed_operations=ALL,
                                              operation="post", media_root=str(root)))
        created = run(config, {"text": "Hello from a workflow 👋 https://example.com/a/long/path", "request_id": "p-1"})
        result = created["result"]
        assert set(created) == {"result"} and result["request_id"] == "p-1" and result["operation"] == "post"
        assert result["post_id"] in api.posts and result["url"] == "https://x.com/i/web/status/" + result["post_id"]
        assert result["weighted_length"] == 48 and result["media_id"] is None and result["requests"] == 1
        call = api.calls[-1]
        assert call["method"] == "POST" and call["path"] == "/2/tweets" and call["content_type"].startswith("application/json")
        assert call["body"] == {"text": "Hello from a workflow 👋 https://example.com/a/long/path"}
        assert call["auth"] == "Bearer " + ACCESS
        target = result["post_id"]
        reply = run(config, {"text": "A reply", "reply_to": target, "quote": target})["result"]
        assert api.calls[-1]["body"] == {"text": "A reply", "reply": {"in_reply_to_tweet_id": target}, "quote_tweet_id": target}
        assert api.posts[reply["post_id"]]["in_reply_to_user_id"] == ME["id"] and reply["reply_to"] == target
        before = len(api.calls)
        pictured = run(config, {"text": "With a picture", "media_path": "image.png"})["result"]
        upload, create = api.calls[before:]
        assert upload["kind"] == "upload" and create["kind"] == "create" and upload["auth"] == "Bearer " + ACCESS
        assert upload["content_type"].startswith("multipart/form-data; boundary=BloxSmithX")
        assert upload["body"]["fields"] == {"media_category": "tweet_image", "media_type": "image/png"}
        assert upload["body"]["files"]["media"] == {"filename": "image.png", "type": "image/png", "data": len(PNG)}
        assert create["body"] == {"text": "With a picture", "media": {"media_ids": [pictured["media_id"]]}}
        assert pictured["media_key"] == "3_" + pictured["media_id"] and pictured["requests"] == 2
        assert api.uploads[pictured["media_id"]]["filename"] == "image.png", "the local file name is never sent"
        combined = run(config, {"text": "Caption"}, media=str(root / "photo.jpg"))["result"]
        assert api.uploads[combined["media_id"]]["type"] == "image/jpeg" and api.calls[-1]["body"]["text"] == "Caption"
        only_image = run(config, {"media_path": "image.png"})["result"]
        assert api.calls[-1]["body"] == {"media": {"media_ids": [only_image["media_id"]]}}
        # Posts always use the user grant, even when an app-only bearer is configured for reads.
        both = run(logic.configuration({**config, "bearer_secret_ref": APP_REF}), "Both credentials",
                   creds={**credentials(grant(), BEARER), "ref": USER_REF})
        assert api.calls[-1]["auth"] == "Bearer " + ACCESS and both["result"]["post_id"]
        before = len(api.calls)
        failure(api, lambda: run(config, "No user grant", creds=credentials(bearer=BEARER)), "secret")
        assert len(api.calls) == before
        duplicate = failure(api, lambda: run(config, "A reply"), "forbidden")
        assert "duplicate content" in duplicate.provider_detail and not duplicate.uncertain and duplicate.http_status == 403
        failure(api, lambda: run(config, {"text": "Reply to nothing", "reply_to": "1900000000000009999"}), "forbidden")
        # Deletion is final; X keeps no trash.
        removed = run(config, {"operation": "delete_post", "post_id": target, "request_id": "d-1"})["result"]
        assert removed == {"request_id": "d-1", "operation": "delete_post", "post_id": target, "deleted": True,
                           "requests": 1, "rate_limit": removed["rate_limit"]}
        assert api.calls[-1]["method"] == "DELETE" and api.calls[-1]["path"] == "/2/tweets/" + target and target not in api.posts
        assert failure(api, lambda: run(config, {"operation": "delete_post", "post_id": target}), "not_found").http_status == 404
        someone = next(ident for ident, post in api.posts.items() if post["author_id"] == ADA["id"])
        failure(api, lambda: run(config, {"operation": "delete_post", "post_id": someone}), "forbidden")
        assert someone in api.posts
        api.script["delete"] = [{"status": 200, "body": {"data": {"deleted": False}}}]
        failure(api, lambda: run(config, {"operation": "delete_post", "post_id": someone}), "not_deleted")
        # A post lost after the image upload is uncertain, never replayed, and names the uploaded media.
        api.script["create"] = ["lost"]
        before = len(api.calls)
        lost = failure(api, lambda: run(config, {"text": "Lost answer", "media_path": "image.png"}), "network")
        assert lost.uncertain and api.kinds()[before:] == ["upload", "create"]
        assert lost.envelope()["media_id"] in api.uploads
        api.script["create"] = [{"status": 201, "body": {"data": {"text": "no id"}}}]
        assert failure(api, lambda: run(config, "Odd receipt"), "protocol").uncertain
        # The write switches are enforced before any request.
        before = len(api.calls)
        failure(api, lambda: run(logic.configuration(settings(api, allowed_operations=READS)), {"operation": "post", "text": "x"}),
                "operation_not_allowed")
        failure(api, lambda: logic.parse_request({"operation": "delete_post", "post_id": someone}, {**config, "allow_delete": False}),
                "delete_disabled")
        failure(api, lambda: run(config, {"text": "x", "media_path": "../image.png"}), "media_not_allowed")
        assert len(api.calls) == before
    print("[ok] X posts, replies, quotes, image upload, deletion and write refusals", flush=True)


def refresh_protocol():
    """# - FB5 - expired or missing access tokens are renewed once; rotated tokens stay in memory, never in the wallet."""
    transport.forget_grants()
    with x_api() as api:
        config = logic.configuration(settings(api, allow_writes=True, allowed_operations=ALL[:1] + READS, operation="post"))
        api.access.discard(ACCESS)
        before = len(api.calls)
        renewed = run(config, "First post after expiry")["result"]
        assert api.kinds()[before:] == ["create", "token", "create"] and renewed["token_refreshed"] is True
        token_call = api.calls[before + 1]
        assert token_call["body"] == {"grant_type": "refresh_token", "refresh_token": REFRESH, "client_id": CLIENT_ID}
        assert token_call["auth"] is None and token_call["content_type"] == "application/x-www-form-urlencoded"
        access_1, refresh_1 = api.issued[-1]
        assert api.calls[-1]["auth"] == "Bearer " + access_1 and REFRESH not in api.refresh, "X rotated the refresh token"
        # The same wallet value in a later activation reuses the renewed pair kept in memory.
        before = len(api.calls)
        again = run(config, "Second post")["result"]
        assert api.kinds()[before:] == ["create"] and api.calls[-1]["auth"] == "Bearer " + access_1
        assert "token_refreshed" not in again
        api.access.discard(access_1)
        before = len(api.calls)
        run(config, "Third post")
        assert api.kinds()[before:] == ["create", "token", "create"]
        assert api.calls[before + 1]["body"]["refresh_token"] == refresh_1, "the rotated refresh token is the one used"
        # After a restart the wallet still holds the consumed refresh token: re-authorization is required.
        transport.forget_grants()
        before = len(api.calls)
        stale = failure(api, lambda: run(config, "After restart"), "reauthorization_required")
        assert api.kinds()[before:] == ["create", "token"] and stale.http_status == 400 and not stale.uncertain
        assert stale.provider_title == "invalid_request" and "authorize again" in str(stale)
        # A new grant stored without an access token is refreshed before the first call.
        fresh = "-".join(("fixture", "user", "refresh", "reauthorized", "0002"))
        api.refresh.add(fresh)
        value = json.dumps({"refresh_token": fresh, "client_id": CLIENT_ID})
        before = len(api.calls)
        searched = run(config, {"operation": "search_recent", "query": "note 3"}, creds=credentials(value))
        assert api.kinds()[before:] == ["token", "search"] and searched["result"]["token_refreshed"] is True
        # A confidential client authenticates the refresh with HTTP Basic, without client_id in the form.
        secret_value = "-".join(("fixture", "user", "refresh", "confidential", "0003"))
        api.refresh.add(secret_value)
        value = json.dumps({"refresh_token": secret_value, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
        run(config, {"operation": "get_user", "username": "ada_fixture"}, creds=credentials(value))
        token_call = next(call for call in reversed(api.calls) if call["kind"] == "token")
        expected = "Basic " + base64.b64encode((CLIENT_ID + ":" + CLIENT_SECRET).encode()).decode()
        assert token_call["auth"] == expected and token_call["body"] == {"grant_type": "refresh_token",
                                                                         "refresh_token": secret_value}
        # A bare access token cannot be renewed: X's 401 is reported as is.
        before = len(api.calls)
        bare = failure(api, lambda: run(config, "Bare token", creds=credentials("-".join(("fixture", "expired", "access")))),
                       "unauthorized")
        assert api.kinds()[before:] == ["create"] and bare.http_status == 401
        # Concurrent activations on one grant refresh it once.
        shared = "-".join(("fixture", "user", "refresh", "shared", "0004"))
        api.refresh.add(shared)
        value = json.dumps({"access_token": "-".join(("fixture", "expired", "shared")), "refresh_token": shared,
                            "client_id": CLIENT_ID})
        before, results = len(api.calls), []
        workers = [threading.Thread(target=lambda: results.append(
            run(config, {"operation": "search_recent", "query": "note"}, creds=credentials(value))["result"]["count"]))
            for _ in range(3)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(30)
        assert results == [10, 10, 10] and api.kinds()[before:].count("token") == 1, api.kinds()[before:]
        # App-only reads never refresh.
        before = len(api.calls)
        failure(api, lambda: run(logic.configuration({**config, "bearer_secret_ref": APP_REF}), {"operation": "get_user",
                "username": "ada_fixture"}, creds=credentials(bearer="-".join(("fixture", "revoked", "bearer")))), "unauthorized")
        assert api.kinds()[before:] == ["user"]
        # A refresh whose answer is lost may have consumed the stored refresh token; it is not replayed.
        lost_value = "-".join(("fixture", "user", "refresh", "lost", "0005"))
        api.refresh.add(lost_value)
        api.script["token"] = ["lost"]
        before = len(api.calls)
        lost = failure(api, lambda: run(config, "Lost refresh", creds=credentials(json.dumps(
            {"refresh_token": lost_value, "client_id": CLIENT_ID}))), "network")
        assert lost.uncertain and "refresh" in str(lost) and api.kinds()[before:] == ["token"]
        api.script["token"] = [{"status": 401, "body": {"error": "unauthorized_client", "error_description": "Missing valid "
                                                         "authorization header"}}]
        wrong = failure(api, lambda: run(config, "Wrong client", creds=credentials(json.dumps(
            {"refresh_token": lost_value, "client_id": CLIENT_ID}))), "reauthorization_required")
        assert wrong.http_status == 401 and wrong.provider_title == "unauthorized_client"
    transport.forget_grants()
    print("[ok] X token refresh, rotation in memory, re-authorization and concurrency", flush=True)


def error_protocol():
    """# - FB6 - 429 waits, usage caps, retries, uncertainty, protocol limits, cancellation, deadlines and TLS."""
    transport.forget_grants()
    with x_api() as api:
        config = logic.configuration(settings(api, allow_writes=True, allowed_operations=["post", *READS], max_retry_wait_sec=2))
        api.script["search"] = [{"status": 429, "body": LIMITED, "headers": {"Retry-After": "1"}}]
        started = time.monotonic()
        assert run(config, "note")["result"]["requests"] == 2 and time.monotonic() - started >= 1
        api.script["search"] = [{"status": 429, "body": LIMITED, "headers": {"x-rate-limit-reset": str(int(time.time()) + 1),
                                                                           "x-rate-limit-remaining": "0"}}]
        assert run(config, "note")["result"]["requests"] == 2
        api.script["create"] = [{"status": 429, "body": LIMITED, "headers": {"Retry-After": "1"}}]
        before = len(api.calls)
        assert run(config, {"operation": "post", "text": "Rate limited once"})["result"]["post_id"] and api.kinds()[before:] == ["create", "create"]
        api.script["search"] = [{"status": 429, "body": LIMITED}]
        limited = failure(api, lambda: run(config, "note"), "rate_limited")
        assert limited.attempts == 1 and limited.retry_after >= 800, "a reset 15 minutes away is reported, not awaited"
        api.script["search"] = [{"status": 429, "body": CAPPED, "headers": {"Retry-After": "1"}}]
        before = len(api.calls)
        capped = failure(api, lambda: run(config, "note"), "usage_capped")
        assert capped.attempts == 1 and len(api.calls) == before + 1 and capped.provider_title == "UsageCapExceeded"
        api.script["search"] = [{"status": 429, "body": LIMITED, "headers": {"Retry-After": "1"}}] * 3
        assert failure(api, lambda: run(logic.configuration({**config, "max_retries": 0}), "note"), "rate_limited").attempts == 1
        api.script["search"] = []
        # Reads retry 5xx and lost connections; writes never replay an ambiguous outcome.
        api.script["search"] = ["html502", "lost"]
        before = len(api.calls)
        assert run(config, "note")["result"]["count"] == 10 and api.kinds()[before:] == ["search"] * 3
        for scripted, code in (({"status": 503, "body": problem(503, "Service Unavailable", "Try later")}, "provider_unavailable"),
                               ("html502", "provider_unavailable"), ("lost", "network"), ("oversized", "protocol"),
                               ("gzip", "protocol")):
            api.script["create"] = [scripted]
            before = len(api.calls)
            outcome = failure(api, lambda: run(logic.configuration({**config, "max_response_bytes": 65536}),
                                               {"operation": "post", "text": "Ambiguous " + str(len(api.calls))}), code)
            assert outcome.uncertain and api.kinds()[before:] == ["create"], scripted
        # The image upload has no visible effect, so it is retried like a read.
        with image_root() as root:
            pictures = logic.configuration({**config, "media_root": str(root)})
            api.script["upload"] = ["html502"]
            before = len(api.calls)
            assert run(pictures, {"operation": "post", "text": "Retried upload", "media_path": "image.png"})["result"]["media_id"]
            assert api.kinds()[before:] == ["upload", "upload", "create"]
        api.script["search"] = ["oversized"]
        assert not failure(api, lambda: run(logic.configuration({**config, "max_response_bytes": 65536, "max_retries": 0}), "note"),
                           "protocol").uncertain
        for status, code in ((402, "quota_exceeded"), (409, "conflict"), (413, "provider_too_large"), (418, "provider_error")):
            api.script["create"] = [{"status": status, "body": problem(status, "Refused", "Refused " + str(status))}]
            refused = failure(api, lambda: run(config, {"operation": "post", "text": "Refused " + str(status)}), code)
            assert refused.http_status == status and not refused.uncertain and refused.provider_detail == "Refused " + str(status)
        api.script["search"] = [{"status": 403, "body": {"client_id": "1", "detail": "Use an app attached to a Project",
                                                         "reason": "client-not-enrolled", "title": "Client Forbidden",
                                                         "type": "https://api.twitter.com/2/problems/client-forbidden"}}]
        assert failure(api, lambda: run(config, "note"), "forbidden").provider_title == "Client Forbidden"
        # Cancellation and the deadline shut the socket down while X holds the request.
        api.script["create"] = ["hold"]
        started = time.monotonic()
        cancelled = failure(api, lambda: run(config, {"operation": "post", "text": "Held post"}, cancel=lambda: api.holding >= 1), "cancelled")
        assert cancelled.uncertain and time.monotonic() - started < 2.5
        api.release.set()
        time.sleep(0.2)
        api.release.clear()
        api.script["search"] = ["hold"]
        started = time.monotonic()
        quiet = failure(api, lambda: run(config, "note", cancel=lambda: api.holding >= 1), "cancelled")
        assert not quiet.uncertain and time.monotonic() - started < 2.5
        api.release.set()
        time.sleep(0.2)
        api.release.clear()
        api.script["create"] = ["hold"]
        started = time.monotonic()
        timed = failure(api, lambda: run(logic.configuration({**config, "timeout_sec": 5}), {"operation": "post", "text": "Slow post"}), "timeout")
        assert timed.uncertain and 4 <= time.monotonic() - started < 8
        api.release.set()
        time.sleep(0.2)
        assert not [thread for thread in threading.enumerate() if thread.name == "x-twitter-http"]
        assert not failure(api, lambda: run(logic.configuration({**config, "api_base_url": "http://127.0.0.1:9", "max_retries": 0}),
                                            "note"), "network").uncertain
        before = len(api.calls)
        failure(api, lambda: run(config, "note", cancel=lambda: True), "cancelled")
        assert len(api.calls) == before
    with x_api(tls=True) as api:
        config = logic.configuration(settings(api, allowed_operations=READS))
        assert run(config, "note")["result"]["count"] == 10
        before = len(api.calls)
        untrusted = failure(api, lambda: run(logic.configuration({**config, "ca_file": ""}), "note"), "tls")
        assert not untrusted.uncertain and len(api.calls) == before
    transport.forget_grants()
    print("[ok] X rate limits, usage caps, retries, uncertainty, cancellation, deadline and TLS", flush=True)


def value(data, port):
    raw = data.get("output_values", {}).get("x:" + str(port), {}).get("value")
    return json.loads(raw) if raw else None


def wallet(server, secret):
    http_json(server.base_url, "/api/application/secrets/init", method="POST", payload={"password": "disposable-x-fixture"})
    http_json(server.base_url, "/api/application/secrets", method="POST", payload={"name": "x_user_fixture", "value": secret})


def graph_runs():
    """# - FB7 - real graphs in One Shot and Active Runtime for bundled, managed and linked packages, with prompt Stop."""
    block = XTwitterBlock()
    for mode in ("centralized", "zeromq_active"):
        for origin in (None, "managed", "linked"):
            full = origin is None
            with x_api() as api, isolated_server() as server, image_root() as root:
                node = block.build_node_payload(node_id="x", position={"x": 380, "y": 160}, config_overrides=settings(
                    api, allow_writes=True, operation="post", allowed_operations=READS, media_root=str(root), timeout_sec=20))
                if origin:
                    model = install_test_package(server, "x_twitter", origin=origin)
                    node["block_version"] = model["version"]
                    for surface in ("modal", "inspector_panel", "node_card"):
                        html = surface_payload(server, model, node, surface=surface)["html"]
                        assert "{{" not in html and ACCESS not in html
                        if surface != "node_card":
                            assert 'data-block-config-field="allowed_operations"' in html
                wallet(server, grant() if full else ACCESS)
                sinks = [display_node("posts", "Posts", 760, 40), display_node("result", "Result", 760, 200),
                         display_node("problem", "Problem", 760, 360)]
                edges = [data_edge("p", "x", 1, "posts", 1), data_edge("r", "x", 2, "result", 1),
                         data_edge("e", "x", 3, "problem", 1)]
                tag = mode + "-" + str(origin)
                if mode == "centralized":
                    def once(sources, expect="success"):
                        nodes = [text_node(name, name.title(), text, 60, 100 + 120 * index)
                                 for index, (name, text, _port) in enumerate(sources)]
                        links = [data_edge("in-" + name, name, 1, "x", port) for name, _text, port in sources]
                        graph = graph_payload("X one shot", [*nodes, node, *sinks], [*links, *edges])
                        project = create_project_api(server, document=graph)["project"]
                        created = create_run_api(server, graph, project_id=project["graph_id"], runtime_mode=mode)
                        data = wait_for_run_terminal(server, created["run_id"], timeout_sec=40)
                        assert data["status"] == expect, (origin, data.get("status"), data.get("logs"))
                        assert not leaked(api, json.dumps(data))
                        return data
                    data = once([("source", json.dumps({"operation": "search_recent", "request_id": "q-1",
                                                         "query": "workflow"}), 1)])
                    assert len(value(data, 1)) == 10 and value(data, 2)["request_id"] == "q-1" and value(data, 2)["truncated"]
                    data = once([("source", "Graph post " + tag, 1)])
                    assert api.posts[value(data, 2)["post_id"]]["text"] == "Graph post " + tag and value(data, 1) is None
                    if full:
                        before = len(api.calls)
                        data = once([("source", "Graph image " + tag, 1), ("image", "image.png", 2)])
                        assert value(data, 2)["media_id"] in api.uploads and api.kinds()[before:] == ["upload", "create"]
                        data = once([("image", json.dumps({"path": "image.png", "text": "Image only " + tag}), 2)])
                        assert api.posts[value(data, 2)["post_id"]]["text"] == "Image only " + tag
                        before = len(api.calls)
                        data = once([("source", json.dumps({"operation": "delete_post", "post_id": "1"}), 1)])
                        assert value(data, 3)["code"] == "operation_not_allowed" and len(api.calls) == before
                        assert data["results"]["x"]["x_twitter"]["code"] == "operation_not_allowed"
                        http_json(server.base_url, "/api/application/secrets/lock", method="POST", payload={})
                        data = once([("source", "Locked wallet", 1)])
                        assert value(data, 3)["code"] == "secret" and len(api.calls) == before
                        http_json(server.base_url, "/api/application/secrets/unlock", method="POST",
                                  payload={"password": "disposable-x-fixture"})
                        # Stop while X holds a search: the run ends promptly and nothing is retried.
                        api.script["search"] = ["hold"]
                        graph = graph_payload("X stop", [text_node("source", "Source", json.dumps(
                            {"operation": "search_recent", "query": "workflow"}), 60, 160), node, *sinks],
                            [data_edge("in", "source", 1, "x", 1), *edges])
                        project = create_project_api(server, document=graph)["project"]
                        rid = create_run_api(server, graph, project_id=project["graph_id"], runtime_mode=mode)["run_id"]
                        deadline = time.monotonic() + 20
                        while api.holding < 1 and time.monotonic() < deadline:
                            time.sleep(0.05)
                        assert api.holding == 1, "held search not reached"
                        held, started = len(api.calls), time.monotonic()
                        stop_run_api(server, rid)
                        stopped = wait_for_run_terminal(server, rid, timeout_sec=20)
                        assert time.monotonic() - started < 10, stopped.get("logs")
                        assert stopped["status"] == "cancelled", (stopped["status"], value(stopped, 3), stopped.get("logs"))
                        assert value(stopped, 1) is None and value(stopped, 2) is None, "a stopped search publishes no posts"
                        assert len(api.calls) == held, "the held search is neither retried nor followed by another request"
                        assert "shutdown_timeout" not in json.dumps(stopped.get("logs"))
                        api.release.set()
                else:
                    graph = graph_payload("X active", [text_node("cmd", "Command", "", 60, 100),
                                                       text_node("img", "Image", "", 60, 260), node, *sinks],
                                          [data_edge("in", "cmd", 1, "x", 1), data_edge("pic", "img", 1, "x", 2), *edges])
                    project = create_project_api(server, document=graph)["project"]

                    def publish(rid, source, text):
                        http_json(server.base_url, f"/api/runs/{rid}/active/control", method="POST", payload={
                            "action": "publish_output", "node_id": source, "port_id": 1, "value": text,
                            "content_type": "text/plain"})

                    def wait(rid, port, predicate, label):
                        data = wait_for_run_predicate(server, rid, lambda item: item.get("status") == "failed" or predicate(
                            value(item, port) or {}), label, timeout_sec=30)
                        assert data.get("status") != "failed", data.get("logs")
                        assert not leaked(api, json.dumps(data))
                        return data

                    rid = prepare_release_run(server, project["graph_id"], graph)["run_id"]
                    http_json(server.base_url, f"/api/runs/{rid}/play", method="POST", payload={})
                    try:
                        publish(rid, "cmd", json.dumps({"operation": "search_recent", "request_id": "live-1", "query": "workflow"}))
                        data = wait(rid, 2, lambda item: item.get("request_id") == "live-1", "search missing")
                        assert len(value(data, 1)) == 10
                        publish(rid, "img", json.dumps({"path": "image.png", "text": "Live image " + tag, "request_id": "live-2"}))
                        data = wait(rid, 2, lambda item: item.get("request_id") == "live-2", "image post missing")
                        assert value(data, 2)["media_id"] in api.uploads
                        assert api.posts[value(data, 2)["post_id"]]["text"] == "Live image " + tag
                        if full:
                            publish(rid, "cmd", json.dumps({"request_id": "live-3", "operation": "get_user", "username": "nobody_x"}))
                            data = wait(rid, 3, lambda item: item.get("request_id") == "live-3", "error missing")
                            assert value(data, 3)["code"] == "not_found"
                            api.script["search"] = ["hold"]
                            publish(rid, "cmd", json.dumps({"operation": "search_recent", "query": "workflow"}))
                            deadline = time.monotonic() + 20
                            while api.holding < 1 and time.monotonic() < deadline:
                                time.sleep(0.05)
                            assert api.holding == 1, "held search not reached"
                    finally:
                        started = time.monotonic()
                        stop_run_api(server, rid)
                        stopped = wait_for_run_terminal(server, rid, timeout_sec=20)
                        assert time.monotonic() - started < 10, stopped.get("logs")
                        assert "shutdown_timeout" not in json.dumps(stopped.get("logs"))
                        api.release.set()
                assert not leaked(api, "\n".join(server.logs))
            print("[ok] X graph " + mode + " " + str(origin), flush=True)


def main():
    read_protocol()
    write_protocol()
    refresh_protocol()
    error_protocol()
    graph_runs()


if __name__ == "__main__":
    main()
