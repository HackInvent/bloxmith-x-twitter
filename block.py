"""X (Twitter) posts through the X API v2 with wallet-held OAuth 2.0 credentials."""

import json
from bloxsmith_app.block_api import (APPLICATION_JSON, BlockDefinition, BlockRuntimeOutput, BlockRuntimePreparation,
                                     BlockRuntimeResult)
from . import files, logic, operations, transport, ui


# FB1 - Keep remote access off by default; require wallet references (OAuth 2.0 user grant, optional app-only bearer) and an HTTPS API origin.
# FB2 - Post text with X's weighted length rule, reply_to and quote, with an optional JPEG/PNG/WEBP image from media_root, only with allow_writes.
# FB3 - Delete a post only with allow_writes and allow_delete; X keeps no trash, so the deletion is final and documented as such.
# FB4 - Search recent posts, look up a user by username and read a user's posts in bounded pages with X's next token.
# FB5 - Refresh an expired or refused user access token once with the rotated refresh token kept in memory only, never written anywhere.
# FB6 - Bound size, time, pages and retries; honour Retry-After or x-rate-limit-reset on HTTP 429, never replay ambiguous writes, report coded English errors without tokens.
# FB7 - Apply one contract in both runtimes and every package origin, with prompt cancellation of pending requests.
# FB8 - Own translated responsive settings with draft Apply, a conditional write section and release-scoped assets.
class XTwitterBlock(BlockDefinition):
    """Post text read from X is untrusted data, never instructions for this block."""

    kind = "x_twitter"

    def ui_assets(self, surface="modal"):
        """Every installation uses the same static release manifest."""
        return list(self.model.get("ui_assets", {}).get(surface, []))

    def prepare_runtime(self, context):
        """Validate settings without wallet, filesystem or network access; no listener is declared."""
        logic.configuration(context.config)
        return BlockRuntimePreparation()

    def fresh_inputs(self, context):
        """Only fresh deliveries count: a retained request or image is never used again."""
        names = {int(port.id): str(port.name) for port in context.input_ports}
        if context.input_events:
            return [(names.get(event.input_port_id), event.value) for event in context.input_events]
        fresh = []
        for ident, name in names.items():
            attribute = context.input_attribute(name, str(ident))
            if attribute is not None and attribute.status == "updated" and attribute.value not in (None, ""):
                fresh.append((name, attribute.value))
        return fresh

    def outputs(self, context, values):
        return [BlockRuntimeOutput(port_id=int(port.id), port_name=port.name, content_type=APPLICATION_JSON,
                                   value=json.dumps(values[port.name], ensure_ascii=False, allow_nan=False))
                for port in context.output_ports if port.name in values]

    @staticmethod
    def request_of(items, config):
        """One activation: a request, an image, or both delivered together for one post."""
        names = [name for name, _value in items]
        if len(items) > 2 or len(set(names)) != len(names) or any(name not in ("request", "media") for name in names):
            logic.fail("invalid_input", "Send one request, one image, or one of each per activation; nothing was sent.")
        if not config["enabled"]:
            logic.fail("disabled")
        values = dict(items)
        if "request" in values and "media" in values:
            return logic.combined_request(values["request"], values["media"], config)
        if "request" in values:
            return logic.parse_request(values["request"], config)
        return logic.media_request(values["media"], config)

    @staticmethod
    def credentials(context, config, operation):
        """Read only the wallet values the operation needs, after every local check passed."""
        if operation in logic.WRITES or not config["bearer_secret_ref"]:
            if not config["secret_ref"]:
                logic.fail("secret", "This operation needs secret_ref, the OAuth 2.0 user-context grant.")
            try:
                raw = context.resolve_secret(config["secret_ref"])
            except Exception:
                logic.fail("secret")
            return {"ref": config["secret_ref"], "user": logic.user_grant(raw), "app": None}
        try:
            raw = context.resolve_secret(config["bearer_secret_ref"])
        except Exception:
            logic.fail("secret")
        return {"ref": config["bearer_secret_ref"], "user": None, "app": logic.app_bearer(raw)}

    @staticmethod
    def summary(operation, result):
        if operation == "post":
            return "post " + result["post_id"] + " published" + (" with 1 image" if result["media_id"] else "")
        if operation == "delete_post":
            return "post " + result["post_id"] + " deleted"
        if operation == "get_user":
            return "user " + result["user"]["id"] + " found"
        return f"{result['count']} post(s) in {result['pages']} page(s)"

    def execute_runtime(self, context):
        """One operation per activation; business failures go to the error port and the run continues."""
        request, secrets, config, read = {}, [], {}, False
        try:
            config = logic.configuration(context.config)
            items = self.fresh_inputs(context)
            if not items:
                return BlockRuntimeResult(status="skipped", outputs=[])
            request = self.request_of(items, config)
            image = files.read_image(request["media_path"], config) if request.get("media_path") else None
            credentials = self.credentials(context, config, request["operation"])
            read = True
            session = transport.Session(config, credentials, context.services.get("cancel_requested", lambda: False))
            secrets = session.secrets
            values = operations.run(session, request, config, image)
            result = values["result"]
            operation = request["operation"]
            summary = self.summary(operation, result)
            return BlockRuntimeResult(status="success", outputs=self.outputs(context, values),
                content_type=APPLICATION_JSON, last_message=operation + ": " + summary,
                metadata={self.kind: {"status": "success", "operation": operation, "requests": result["requests"]}},
                logs=[f"[x_twitter] {operation}: {summary} after {result['requests']} request(s)."])
        except logic.XError as exc:
            error = exc
        except (ValueError, TypeError, KeyError, OSError, RuntimeError):
            # Raw exception text could carry post text, paths or credentials: publish a fixed diagnostic.
            error = logic.XError("internal", uncertain=read and request.get("operation") in logic.WRITES)
        operation = request.get("operation")
        envelope = error.envelope(operation=operation, request_id=request.get("request_id"))
        text = context.redact_secrets(logic.redact(json.dumps(envelope, ensure_ascii=False, allow_nan=False), secrets))
        return BlockRuntimeResult(status="success",
            outputs=[BlockRuntimeOutput(port_id=int(port.id), port_name=port.name, value=text,
                                        content_type=APPLICATION_JSON)
                     for port in context.output_ports if port.name == "error"],
            content_type=APPLICATION_JSON, last_message=error.code,
            metadata={self.kind: {"status": "error", "operation": operation, "code": error.code,
                                  "uncertain": error.uncertain}},
            logs=[f"[x_twitter-error] {operation or 'request'}: {error.code}"])

    def handle_ui_action(self, *, node, action, values, payload=None):
        """Apply validates settings only; it never contacts X, reads the wallet or touches media_root."""
        if action in {"save_settings", "modal_update_fields", "inspector_update_fields"}:
            patch = (values or {}).get("node_patch") or {}
            if "config" in patch:
                try:
                    logic.configuration({**self.default_config(), **(node.get("config") or {}), **patch["config"]})
                except (ValueError, TypeError) as exc:
                    detail = str(exc) if isinstance(exc, logic.XError) else logic.MESSAGES["config"]
                    return {"error": self.translate("block.x_twitter.error", {"detail": detail},
                                                    fallback="X settings rejected: " + detail)}
        result = super().handle_ui_action(node=node, action="modal_update_fields" if action == "save_settings" else action,
                                          values=values, payload=payload)
        return ui.replace_config(result, node)

    def render_modal(self, *, node, payload=None):
        return ui.modal(self, node, payload)

    def render_inspector_panel(self, *, node, payload=None):
        return ui.inspector(self, node, payload)

    def render_node_card(self, *, node, payload=None):
        """Show the access mode and default operation, never a wallet reference."""
        config = {**self.default_config(), **(node.get("config") or {})}
        operation = config.get("operation") if config.get("operation") in logic.OPERATIONS else "search_recent"
        params = {"operation": operation}
        if config.get("enabled") is not True:
            key, params, fallback = "block.x_twitter.card_disabled", {}, "Remote access off"
        elif config.get("allow_writes") is not True:
            key, fallback = "block.x_twitter.card_read", f"Read only · {operation}"
        elif config.get("allow_delete") is True:
            key, fallback = "block.x_twitter.card_delete", f"Read, post and delete · {operation}"
        else:
            key, fallback = "block.x_twitter.card_write", f"Read and post · {operation}"
        return ui.card(self, node, key, params, self.translate(key, params, fallback=fallback))
