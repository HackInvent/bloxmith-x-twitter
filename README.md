# X (Twitter)

Publish, search and read posts on X through the X API v2. The block publishes
text posts (with replies, quotes and one optional image), deletes posts, searches
recent posts, looks up a user and reads a user's posts, and returns them as plain
JSON. Tokens never leave the BloxSmith wallet, remote access stays off until you
enable it, nothing is published until you also enable posting, and deleting a
post needs a third, separate switch.

<!-- block-metadata:start -->
[![Block version: 0.1.0](https://img.shields.io/badge/block-0.1.0-blue)](model.json)
[![BloxSmith compatibility: 1.0.9](https://img.shields.io/badge/BloxSmith-1.0.9-brightgreen)](compatibility.json)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Verified BloxSmith versions: **1.0.9** (bundled-block tests; see [test evidence](compatibility.json)).
<!-- block-metadata:end -->

[![X (Twitter) — publish, search and read posts](media/thumbnail.webp)](media/cover.png)

## When to use it

- Announce a release, a report or a detected event as a post, optionally with a chart
  or a photo produced earlier in the workflow.
- Reply to or quote a post from an automated pipeline.
- Watch what is said about a product or a topic over the last seven days.
- Read the latest posts of an account to summarize or archive them.
- Remove a post by ID, with the explicit `allow_delete` switch.

The block does not like, repost, follow, send direct messages, stream, or listen for
mentions: it opens no listening port and only makes outbound requests.

## Ports

| Direction | Port | Value |
| --- | --- | --- |
| Input | `request` | A JSON object with the operation parameters, or plain text used as the main parameter of the default operation |
| Input | `media` | Optional. An image path inside `media_root`, or `{"path", "text", "reply_to", "quote", "request_id"}` |
| Output | `posts` | `search_recent` and `user_posts`: a JSON array of normalized posts |
| Output | `result` | Operation receipt: IDs, counts, continuation token, request ID, rate-limit headers |
| Output | `error` | Machine `code`, English `message`, `uncertain` flag and X's problem details |

Each fresh delivery runs exactly one operation. A retained value is never used
again, so a replayed or forced activation does not repeat a post. Business failures
are published on `error` and the run continues; they never stop an Active Runtime
worker.

- A `request` alone runs the operation it names (or the default operation).
- A `media` value alone publishes that image at once, as a post with the optional
  `text`, `reply_to` and `quote` it carries. It needs the `post` operation to be
  allowed and `allow_writes`.
- A `request` and a `media` value delivered **in the same activation** (One Shot:
  both upstream nodes produced a value) form one post: the request gives the text,
  reply and quote, the media input gives the image. The request must be a `post`
  without `media_path`, and the media value must carry only a path.
- In Active Runtime every message is its own activation; to attach an image there,
  send `media_path` in the request or `{"path", "text"}` on `media`.

### `request` input

| Field | Operations | Meaning |
| --- | --- | --- |
| `operation` | all | `post`, `delete_post`, `search_recent`, `get_user` or `user_posts`; defaults to the `operation` setting and must be allowed |
| `request_id` | all | Optional correlation ID (1–128 of `A-Za-z0-9._:-`), echoed in `result` and `error` |
| `text` | `post` | Post text; leading and trailing spaces are removed. Optional when an image is attached |
| `reply_to` | `post` | ID of the post to reply to |
| `quote` | `post` | ID of the post to quote |
| `media_path` | `post` | Image file inside `media_root` (relative, or absolute inside it) |
| `post_id` | `delete_post` | ID of one of the authenticated user's posts |
| `query` | `search_recent` | X search query, for example `bloxsmith -is:retweet lang:en` |
| `username` | `get_user`, `user_posts` | Account name, with or without `@` |
| `user_id` | `user_posts` | Numeric account ID, instead of `username` |
| `max_results` | `search_recent` (10–100), `user_posts` (5–100) | Posts per page; defaults to `page_size` |
| `max_pages` | `search_recent`, `user_posts` | Pages to read, 1 to `max_pages` |
| `next_token` | `search_recent` | The `next_token` of a previous result, to continue |
| `pagination_token` | `user_posts` | The `next_token` of a previous `user_posts` result, to continue |
| `since_id`, `until_id` | `search_recent`, `user_posts` | Only posts newer than / older than this ID |
| `start_time`, `end_time` | `search_recent`, `user_posts` | RFC 3339 times, sent as `YYYY-MM-DDTHH:MM:SSZ` |
| `sort_order` | `search_recent` | `recency` or `relevancy` |
| `exclude` | `user_posts` | Array among `retweets` and `replies` |

Unknown fields are refused. IDs are decimal strings (integers are accepted and
converted). Plain text is the main parameter of the default operation: the text of
a `post`, the ID for `delete_post`, the query for `search_recent`, the username for
`get_user` and `user_posts`.

```json
{"operation": "post", "text": "Release 2.4 is out: https://example.com/notes", "request_id": "rel-24"}
{"operation": "post", "text": "Thanks!", "reply_to": "1900000000000000001"}
{"operation": "post", "text": "Weekly chart", "media_path": "charts/weekly.png"}
{"operation": "search_recent", "query": "bloxsmith -is:retweet", "max_results": 50, "max_pages": 2}
{"operation": "get_user", "username": "@example"}
{"operation": "user_posts", "username": "example", "exclude": ["replies"], "max_results": 20}
{"operation": "delete_post", "post_id": "1900000000000000001"}
```

### `posts` output

```json
[
  {"id": "1900000000000000001", "text": "Full text, long posts included", "author_id": "1000000000000000002",
   "author_username": "example", "author_name": "Example", "created_at": "2026-01-02T10:00:00.000Z",
   "conversation_id": "1900000000000000001", "in_reply_to_user_id": null, "lang": "en",
   "referenced_posts": [{"type": "quoted", "id": "1890000000000000000"}], "media_keys": ["3_1880000000000000000"],
   "metrics": {"like_count": 3, "reply_count": 0, "retweet_count": 1, "quote_count": 0},
   "url": "https://x.com/example/status/1900000000000000001"}
]
```

The text of a long post comes from X's `note_tweet` field when X provides it.
`metrics` holds the integer counters X returns for the credential's access level.

### `result` output

- `post`: `post_id`, `text`, `url`, `weighted_length`, `reply_to`, `quote`, `media_id`
  and `media_key` (when an image was attached).
- `delete_post`: `post_id` and `deleted` (`true`).
- `search_recent`: `query`, `count`, `pages`, `truncated`, `next_token`, `newest_id`,
  `oldest_id` and `auth` (`app` or `user`). Pass `next_token` back to continue.
- `get_user`: `username` and `user` (`id`, `username`, `name`, `description`,
  `created_at`, `location`, `protected`, `verified`, `profile_image_url`, `metrics`,
  `profile_url`).
- `user_posts`: `user_id`, `username`, `count`, `pages`, `truncated`, `next_token`,
  `newest_id`, `oldest_id` and `auth`. Pass `next_token` back as `pagination_token`.

All receipts carry `request_id`, `operation`, `requests` (HTTP requests used,
retries, token refresh and image upload included), `rate_limit` (`limit`,
`remaining`, `reset` from X's `x-rate-limit-*` headers, when sent) and
`token_refreshed: true` when the access token was renewed. They never contain a
token.

### `error` output

`{"code", "message", "uncertain", "operation", "request_id"}` plus, when X provides
them, `http_status`, `retry_after`, `provider_title`, `provider_type`,
`provider_detail` (bounded, credentials removed) and `attempts`. A post that failed
after its image was uploaded adds `media_id` (X discards unused uploads after about
a day). `uncertain: true` means the post or deletion may have been applied: check
the account before sending it again.

| Code | Meaning |
| --- | --- |
| `config`, `disabled`, `secret`, `invalid_input`, `request_too_large` | Refused locally; nothing sent |
| `operation_not_allowed`, `writes_disabled`, `delete_disabled` | The operation is not enabled in the properties |
| `text_too_long` | The weighted length exceeds `max_weighted_length`; nothing sent |
| `media_root_required`, `media_not_allowed`, `media_not_found`, `media_too_large`, `media_empty`, `media_type` | The image was refused locally; nothing sent |
| `media_rejected` | X could not process the uploaded image |
| `bad_request` (400), `unauthorized` (401), `quota_exceeded` (402), `forbidden` (403), `not_found` (404 or a missing resource), `conflict` (409), `provider_too_large` (413), `rate_limited` (429), `usage_capped` (429 usage cap), `provider_error` (other) | X refused the request |
| `reauthorization_required` | X refused the refresh token or the client ID; authorize again |
| `not_deleted` | X answered that the post was not deleted |
| `provider_unavailable` (5xx), `network`, `timeout`, `cancelled`, `protocol` | Transport outcome; `uncertain` tells whether a write may have been applied |
| `tls` | Certificate verification failed; nothing sent |

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Remote access switch. While off, nothing reaches X and the wallet is not read |
| `secret_ref` | empty | Wallet reference of the OAuth 2.0 user-context grant (see Authentication). Required for `post` and `delete_post` |
| `bearer_secret_ref` | empty | Optional wallet reference of the app-only bearer token. When set, reads use it |
| `operation` | `search_recent` | Operation used when a request names none; always allowed |
| `allowed_operations` | `["search_recent"]` | Other operations a request may select |
| `page_size` | 10 | `max_results` per page for reads, 10–100 |
| `allow_writes` | `false` | Required when `post` or `delete_post` is selectable |
| `max_weighted_length` | 280 | Weighted length checked before sending, 1–25000 |
| `media_root` | empty | Directory of attachable images; empty disables images |
| `allow_delete` | `false` | Additional switch required for `delete_post` |
| `max_pages` | 1 | Pages per read, 1–20 |
| `max_media_bytes` | 5242880 | Largest image, 1 KiB–5 MiB |
| `timeout_sec` | 60 | Deadline for the whole operation, refresh, upload, pages and retries included, 5–300 |
| `max_retries` | 2 | Retries, 0–5 (see Limits) |
| `max_retry_wait_sec` | 30 | Longest wait before a retry, 1–300 |
| `max_request_bytes` | 65536 | Bound for the incoming request and each JSON body sent, 1 KiB–1 MiB |
| `max_response_bytes` | 4194304 | Bound for each X response, 64 KiB–32 MiB |
| `api_base_url` | `https://api.x.com` | API origin; plain HTTP only for a loopback test host |
| `ca_file` | empty | Optional absolute PEM file for a privately signed HTTPS origin |

The settings are validated on Apply and again before a Run starts. `post` or
`delete_post` in `operation` or `allowed_operations` is refused unless
`allow_writes` is on, and `delete_post` also needs `allow_delete`. Restart a loaded
Run to use changed settings.

## Authentication

The block reads two kinds of credentials from the wallet, both obtained **outside
the block**. The values below are placeholders, not credentials.

### User context (OAuth 2.0 with PKCE) for posting

1. In the X developer portal, create a Project and an App, open *User authentication
   settings*, enable OAuth 2.0 and choose the app type: *Native App* (public client)
   or *Web App* (confidential client, with a client secret). Register a callback URL
   you control, for example a local page.
2. Run the authorization code flow with PKCE once, with any OAuth tool or a short
   script: open `https://x.com/i/oauth2/authorize?response_type=code&client_id=…&redirect_uri=…&scope=tweet.read%20tweet.write%20users.read%20offline.access%20media.write&state=…&code_challenge=…&code_challenge_method=S256`,
   approve, then exchange the returned `code` at `POST https://api.x.com/2/oauth2/token`
   with `grant_type=authorization_code`, `code`, `redirect_uri`, `code_verifier` and
   `client_id` (HTTP Basic with the client secret for a confidential client).
   `offline.access` is what returns a refresh token; `media.write` is needed only
   to attach images.
3. Store the answer with your client ID in an unlocked wallet secret and configure
   its reference as `secret_ref`, for example `secret://workspace/x_user`:

```json
{"access_token": "YOUR_ACCESS_TOKEN", "refresh_token": "YOUR_REFRESH_TOKEN", "client_id": "YOUR_CLIENT_ID"}
```

Add `"client_secret": "YOUR_CLIENT_SECRET"` for a confidential client. The other
fields of X's token answer (`token_type`, `expires_in`, `scope`) are accepted and
ignored. A bare access token also works, for about two hours.

**Token refresh and rotation.** X access tokens are short-lived (about two hours).
When X refuses the access token with 401, or when the wallet holds no access token,
the block refreshes it once with `grant_type=refresh_token` and sends the request
again. X returns a new refresh token with each refresh and the previous one should
be treated as used. The framework offers blocks no way to write to the wallet, so
the renewed pair is kept **in this process's memory only**, keyed by the wallet
reference and a digest of the wallet value; it is never written to disk, outputs or
logs, and concurrent activations of one process share a single refresh. Consequences:

- While the process runs, posting keeps working across refreshes.
- After a restart (or in another process that does not share this memory), the
  wallet still holds the refresh token already used: X refuses it and the block
  reports `reauthorization_required`. Authorize again and store the new tokens.
- Two separate processes using the same grant can invalidate each other's refresh
  token. Prefer one X node per grant in Active Runtime.
- Replacing the wallet value takes effect at the next activation.

### App-only bearer token for reading (optional)

In the developer portal, under *Keys and tokens*, generate the app's bearer token
and store it as bare text or `{"bearer_token": "YOUR_BEARER_TOKEN"}`, then configure
`bearer_secret_ref`. When it is set, `search_recent`, `get_user` and `user_posts`
use it and the user grant is not read. Without it, reads use the user grant.
`post` and `delete_post` always use the user grant.

### Access levels

X sells API access by levels (Free, Basic, Pro, Enterprise, and pay-per-use where it
is offered) with monthly caps and per-endpoint rate limits that change over time.
At the time of writing, the **Free level is mainly write-oriented**: it allows a
small monthly number of posts and very limited reads, and search or timeline reads
may be capped or unavailable. Search queries longer than 512 characters need a
higher level. Check the current limits in the X developer portal; the block reports
X's answers as `forbidden`, `rate_limited`, `usage_capped` or `quota_exceeded` and
does not try to work around them. These limits are not verified by the tests.

## Weighted length

X counts post length with the twitter-text v3 rule: most Latin, Greek, Cyrillic and
punctuation characters count 1, other characters (CJK, most symbols) count 2, every
link counts 23 whatever its length, and an emoji sequence (family, flag, keycap,
skin tone) counts 2. The text is normalized to NFC before counting. The block
checks this before sending and refuses a longer text with `text_too_long`, so no
request is spent on it.

The check is an approximation of twitter-text, and X remains the final judge:

- Links are detected when they start with `http://` or `https://`, or when they are
  bare domain names ending in `com`, `net`, `org`, `io`, `ai`, `dev`, `app`, `co`, `me`,
  `info`, `biz` or `xyz`. Other bare domains count character by character, which can
  only overestimate.
- Emoji sequences are recognized from the main emoji blocks; a rare sequence may be
  counted 2 per part.
- Accounts allowed to publish long posts can raise `max_weighted_length` (up to
  25000); X still refuses a text above the account's own limit.

## Example

1. Create the app and the user grant as described above and store it in the wallet
   as `x_user`.
2. Set `enabled = true`, `secret_ref = secret://workspace/x_user`, `allow_writes = true`,
   `operation = post` and `allowed_operations = ["search_recent"]`.
3. Wire a Text block holding the announcement to `request`: One Shot publishes it and
   `result` gives the post ID and URL.
4. To attach a chart, set `media_root` to the folder where the chart is saved and wire
   its path to `media` in the same graph: the text and the image form one post.
5. To read mentions of a topic, send
   `{"operation": "search_recent", "query": "bloxsmith -is:retweet"}` and wire `posts`
   to a Display.

## How the operations work

- `post`: when an image is attached, it is read inside `media_root`, checked (JPEG,
  PNG or WEBP by content, at most `max_media_bytes`) and uploaded with
  `POST /2/media/upload` (multipart `media`, `media_category=tweet_image`,
  `media_type`; the local file name is never sent). Then `POST /2/tweets` sends
  `text`, `reply.in_reply_to_tweet_id`, `quote_tweet_id` and `media.media_ids`.
- `delete_post`: `DELETE /2/tweets/{id}`.
- `search_recent`: `GET /2/tweets/search/recent` with `query`, `max_results`,
  `next_token`, the optional time and ID bounds, `sort_order`, `tweet.fields`,
  `expansions=author_id` and `user.fields`, page after page.
- `get_user`: `GET /2/users/by/username/{username}` with `user.fields`.
- `user_posts`: `GET /2/users/{id}/tweets` with `max_results`, `pagination_token`,
  `exclude` and the optional bounds, after resolving `username` when one is given.

Reads stop at the last page or at `max_pages` and always keep whole pages.

## Limits and warnings

- **A deletion is final.** X keeps no trash or archive for posts; `delete_post` is
  behind `allow_writes`, `allow_delete` and the operation allowlist.
- **Posts are public.** Do not publish text that comes straight from users, chats
  or web pages without a review step such as Human Approval.
- Reads are retried after HTTP 429, 5xx or a lost connection. `post` and
  `delete_post` are retried only after HTTP 429 or a connection that never opened;
  a write that fails after it was sent is reported with `uncertain: true` and never
  replayed (X offers no idempotency key). The image upload, which has no visible
  effect, is retried like a read. A token refresh whose answer is lost is not
  replayed either: the stored refresh token may then be consumed.
- After HTTP 429 the block waits for `Retry-After`, or else until X's
  `x-rate-limit-reset`, up to `max_retry_wait_sec` and within the deadline; a longer
  wait returns `rate_limited` with `retry_after`. A usage-cap answer
  (`UsageCapExceeded`) returns `usage_capped` at once.
- Search covers the last seven days. A user timeline returns at most the most recent
  3,200 posts. Protected or suspended accounts return `forbidden` or `not_found`.
- X returns some missing resources as HTTP 200 with an `errors` array; the block
  reports them as `not_found` or `forbidden` with `http_status: 200`.
- Only one image per post; GIF, video, alt text, polls and reply settings are not
  supported in 0.1.0.
- No HTTP proxy support; the host needs direct outbound HTTPS to `api.x.com`.
- Post text read from X is untrusted data. Do not feed it to tools that execute
  commands.

## Dependencies

Python standard library only (`http.client`, `ssl`, `json`, `unicodedata`); no
`requirements.txt`. No X SDK is used.

## Security

- Wallet values are read at run time, only after the settings, the request and the
  image are validated, and only the credential the operation needs is read. Tokens
  never appear in settings, outputs, logs, run metadata or error messages; provider
  messages are bounded and credentials are removed.
- Renewed tokens live in process memory only and are dropped when the wallet value
  changes or the process ends.
- Images are opened from `media_root` component by component without following
  symbolic links; hard-linked files, special files and paths leaving the directory
  are refused.
- TLS certificates and host names are always verified; there is no insecure mode.
- Redirects, cookies, proxies and compressed responses are not accepted.
- An `api_base_url` other than the official origin receives the tokens: change it
  only for a trusted gateway or a local test double.
- The block opens no listening port; it only makes outbound requests.

## X API usage

X API **v2** on `https://api.x.com`, bearer authentication (OAuth 2.0 user context,
or app-only for reads):

- `POST /2/oauth2/token` (refresh token grant)
- `POST /2/media/upload` (simple image upload)
- `POST /2/tweets`, `DELETE /2/tweets/{id}`
- `GET /2/tweets/search/recent`
- `GET /2/users/by/username/{username}`, `GET /2/users/{id}/tweets`

Errors are read from X's problem documents (`title`, `detail`, `type`, `status`,
`reason`), from `errors` arrays and from OAuth error answers (`error`,
`error_description`).

**Real-provider qualification is pending.** All tests use a loopback double that
reproduces these documented request and response shapes; no X account, app, token
or post was contacted. In particular, the media upload answer, the refresh token
rotation, the exact answer to deleting an unknown post (assumed 404), the usage-cap
document and the weighted-length approximation follow the documentation and have
not been checked against the live service.

## Framing decisions

- One block with five operations, chosen per request inside an allowlist, like the
  other BloxSmith connectors. Three gates for writes: `enabled`, `allow_writes` and
  the allowlist; `delete_post` needs a fourth switch, `allow_delete`.
- Two credentials: the user grant for writes (and reads by default), an optional
  app-only bearer token for reads, so a read-heavy workflow does not spend the
  user's limits and does not need the user grant at all.
- Rotated refresh tokens are kept in memory only, because blocks cannot write to the
  wallet and a token on disk would sit outside it. The consequence (re-authorize
  after a restart once a refresh happened) is documented rather than hidden.
- The image comes from a directory chosen in the properties, never from an arbitrary
  path, and is detected by content. One image per post keeps the simple upload
  endpoint, which X documents for images.
- The weighted length is checked locally to spare quota, with a documented
  approximation, and `max_weighted_length` lets long-post accounts raise it.
- Reads return X's own pagination tokens; nothing is cut inside a page.
- Field names follow X where they map one-to-one (`max_results`, `next_token`,
  `pagination_token`, `since_id`) and use `reply_to`, `quote` and `media_path` for the
  nested post fields.
- The API origin is fixed but overridable for tests; plain HTTP is accepted only for
  loopback hosts.

## Tests

`tests/F5.01_x_twitter.py` checks settings, wallet values, the weighted length,
request contracts, query and multipart encoding, X problem documents and partial
errors, post and user normalization, image file checks and block hooks without a
socket. `tests/F5.02_x_twitter_runtime.py` runs the real client against the loopback
double (`tests/x_fixture.py`, HTTP and HTTPS): recent search with continuation, user
lookup and timelines, app-only reads, posts with replies, quotes and an uploaded
image, the combined request-and-image post, deletion, token refresh with rotation,
re-authorization, a confidential client and concurrent refreshes,
400/401/402/403/404/409/413/429/5xx, usage caps, lost connections, oversized and
compressed bodies, cancellation and the deadline, then real graphs in One Shot and
Active Runtime for bundled, managed and linked packages, including Stop while a
request is pending. `tests/F8.01_x_twitter_ui.py` covers the English and French
forms at desktop and mobile widths. They run in the private BloxSmith workspace
against a pinned framework; the initial development version does not imply a
release.

## License

Block code: Apache-2.0. X and Twitter are trademarks of X Corp.; the service is
governed by its own developer terms. No X SDK, logo, account data or credential is
bundled.
