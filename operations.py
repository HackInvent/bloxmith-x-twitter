"""The five X operations, built from Session calls and pure logic.

Reads go through pages bounded by max_results and max_pages and always return
X's next token when more posts remain. A post with an image uploads the image
first (POST /2/media/upload), then creates the post (POST /2/tweets).
"""

from urllib.parse import quote, urlencode

from . import logic, transport


def identity(request):
    return {"request_id": request["request_id"], "operation": request["operation"]}


def receipt(session, result):
    result["requests"] = session.requests
    if session.refreshes:
        result["token_refreshed"] = True
    if session.rate_limit:
        result["rate_limit"] = session.rate_limit
    return result


def create_post(session, request, config, image):
    """Upload the optional image, then POST /2/tweets; the upload alone has no visible effect."""
    media_id = media_key = None
    if image is not None:
        data, media_type = image
        body, content_type = logic.multipart(data, media_type, transport.boundary())
        media_id, media_key = logic.uploaded(session.call("POST", "/2/media/upload", None, body, content_type,
                                                          auth="user", mutation=False))
    body = logic.post_body(request, media_id, config["max_request_bytes"])
    try:
        answer = session.call("POST", "/2/tweets", None, body, "application/json; charset=utf-8", auth="user",
                              mutation=True)
    except logic.XError as exc:
        if media_id:
            exc.extra["media_id"] = media_id
        raise
    post_id, text = logic.created(answer, request)
    result = {**identity(request), "post_id": post_id, "text": text, "url": "https://x.com/i/web/status/" + post_id,
              "weighted_length": request["weighted_length"], "reply_to": request["reply_to"], "quote": request["quote"],
              "media_id": media_id, "media_key": media_key}
    return {"result": receipt(session, result)}


def delete_post(session, request, config, _image):
    """DELETE /2/tweets/:id: X keeps no trash, so a deletion is final."""
    answer = session.call("DELETE", "/2/tweets/" + request["post_id"], auth="user", mutation=True)
    logic.deleted(answer)
    return {"result": receipt(session, {**identity(request), "post_id": request["post_id"], "deleted": True})}


def missing(body, secrets):
    """A 200 answer with errors and neither data nor meta names a missing or hidden resource."""
    if isinstance(body, dict) and "data" not in body and "meta" not in body and body.get("errors"):
        logic.partial_failure(body, secrets)


def read_pages(session, request, path, params, token):
    """Read pages until X gives no next token or max_pages is reached; whole pages only."""
    posts, pages, newest, oldest = [], 0, None, None
    while True:
        body = session.call("GET", path, urlencode(params(request, token)), auth="read", mutation=False)
        missing(body, session.secrets)
        pages += 1
        items, token, meta = logic.page(body, request["max_results"])
        posts.extend(items)
        if newest is None and isinstance(meta.get("newest_id"), str):
            newest = meta["newest_id"]
        if isinstance(meta.get("oldest_id"), str):
            oldest = meta["oldest_id"]
        if not token or pages >= request["max_pages"]:
            break
    return posts, {"count": len(posts), "pages": pages, "truncated": token is not None, "next_token": token,
                   "newest_id": newest, "oldest_id": oldest}


def search_recent(session, request, config, _image):
    """GET /2/tweets/search/recent: posts of the last seven days matching the query."""
    posts, summary = read_pages(session, request, "/2/tweets/search/recent", logic.search_params, request["next_token"])
    result = {**identity(request), "query": request["query"], **summary, "auth": session.auth}
    return {"posts": posts, "result": receipt(session, result)}


def lookup_user(session, name):
    body = session.call("GET", "/2/users/by/username/" + quote(name, safe=""),
                        urlencode([("user.fields", logic.USER_FIELDS)]), auth="read", mutation=False)
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return logic.user(body["data"])
    missing({"errors": body.get("errors")} if isinstance(body, dict) else None, session.secrets)
    logic.fail("protocol")


def get_user(session, request, config, _image):
    """GET /2/users/by/username/:username."""
    found = lookup_user(session, request["username"])
    result = {**identity(request), "username": request["username"], "user": found, "auth": session.auth}
    return {"result": receipt(session, result)}


def user_posts(session, request, config, _image):
    """GET /2/users/:id/tweets, after resolving a username when one is given."""
    user_id, name = request["user_id"], request["username"]
    if user_id is None:
        found = lookup_user(session, name)
        user_id, name = found["id"], found["username"]
    posts, summary = read_pages(session, request, "/2/users/" + user_id + "/tweets", logic.timeline_params,
                                request["pagination_token"])
    result = {**identity(request), "user_id": user_id, "username": name, **summary, "auth": session.auth}
    return {"posts": posts, "result": receipt(session, result)}


HANDLERS = {"post": create_post, "delete_post": delete_post, "search_recent": search_recent, "get_user": get_user,
            "user_posts": user_posts}


def run(session, request, config, image=None):
    return HANDLERS[request["operation"]](session, request, config, image)
