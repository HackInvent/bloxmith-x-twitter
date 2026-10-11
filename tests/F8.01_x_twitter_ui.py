"""FB8: real release UI in English and French, responsive geometry, the conditional write section, drafts and persistence.

# - FB8 - checked through the real editor in managed and linked package installations.
"""

import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from playwright.sync_api import sync_playwright, expect, TimeoutError as PlaywrightTimeoutError
from block_test_artifacts import artifact_path
from block_test_packages import install_test_package, surface_payload
from ui_smoke_common import isolated_server, graph_payload, create_project_api, project_editor_url
from blocs.x_twitter.block import XTwitterBlock

VIEWPORTS = ((1440, 900), (800, 700), (390, 740), (320, 568))


# Count the surfaces the shell writes into its modal mount: each modal request ends in one.
SURFACES = """() => {
    const mount = document.getElementById('blockUiModalMount');
    if (!mount) return -1;
    if (!mount.xTwitterSurfaces) {
        mount.xTwitterSurfaces = {count: 0};
        new MutationObserver(records => {
            for (const record of records) for (const node of record.addedNodes) {
                if (node.nodeType === 1 && node.matches('.owned-modal')) mount.xTwitterSurfaces.count += 1;
            }
        }).observe(mount, {childList: true});
    }
    return mount.xTwitterSurfaces.count;
}"""


class ModalRequests:
    """Track the editor's modal requests: each one replaces the open surface when it lands."""

    def __init__(self, page):
        self.page, self.sent, self.pending = page, 0, set()
        page.on("request", self.started)
        page.on("requestfinished", self.ended)
        page.on("requestfailed", self.ended)

    def started(self, request):
        if request.method == "POST" and request.url.split("?")[0].endswith("/modal"):
            self.sent += 1
            self.pending.add(request)

    def ended(self, request):
        self.pending.discard(request)

    def wait(self, condition, seconds):
        """Poll through Playwright so its events keep being delivered."""
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            self.page.wait_for_timeout(100)
        return condition()


def open_modal(card, modal, requests):
    """Open the modal from the canvas card once, then wait for a stable mounted surface.

    On a loaded host a double-click can land before the editor has bound its handlers, so it
    is repeated, but only while no modal was requested: a second request would replace the
    surface under the test when it lands, losing the draft and the status of an Apply.
    """
    expect(card.locator('h3')).to_be_visible(timeout=30000)
    page = requests.page
    rendered, sent = page.evaluate(SURFACES), requests.sent
    assert rendered >= 0, "the editor has no block modal mount"
    for _ in range(6):
        if modal.is_visible() or requests.sent > sent:
            break
        try:
            card.locator('h3').dblclick(timeout=5000)
        except PlaywrightTimeoutError:
            continue
        requests.wait(lambda: requests.sent > sent or modal.is_visible(), 5)
    assert requests.wait(lambda: not requests.pending, 60), "modal request still pending"
    assert requests.wait(lambda: page.evaluate(SURFACES) - rendered >= requests.sent - sent, 30), \
        "a requested modal surface never landed"
    expect(modal).to_be_visible(timeout=30000)
    expect(modal.locator('.owned-form-status')).to_have_count(1)


def close_modal(modal, which="first"):
    """Close the modal and wait until it is gone, so a reopen binds to a fresh surface."""
    buttons = modal.locator('[data-close-block-modal]')
    (buttons.last if which == "last" else buttons.first).click()
    expect(modal).to_be_hidden()


def geometry(modal):
    """Require an opaque panel with reachable actions, no page overflow and labelled controls."""
    deadline = time.monotonic() + 5
    while True:
        measured = measure(modal)
        if (measured["inside"] and measured["controls"] and not measured["overflow"]) or time.monotonic() > deadline:
            break
        time.sleep(0.1)
    assert measured["inside"] and measured["controls"] and not measured["overflow"], measured
    assert not measured["unlabeled"], measured
    assert measured["background"] not in {"transparent", "rgba(0, 0, 0, 0)"}, measured


def measure(modal):
    return modal.evaluate("""el => {
        const r=el.getBoundingClientRect(), body=el.querySelector('.owned-body');
        const actions=[...el.querySelectorAll('[data-close-block-modal], [data-owned-apply]')];
        const visible=[...el.querySelectorAll('input,select,textarea')].filter(c=>c.getClientRects().length);
        return {inside:r.left>=-1&&r.right<=innerWidth+1&&r.top>=-1&&r.bottom<=innerHeight+1,
          overflow:el.scrollWidth>el.clientWidth+2||body.scrollWidth>body.clientWidth+2,
          controls:actions.every(a=>{const b=a.getBoundingClientRect();return b.top>=0&&b.bottom<=innerHeight+1}),
          background:getComputedStyle(el).backgroundColor,
          unlabeled:visible.filter(c=>!c.labels?.length&&!c.getAttribute('aria-label')&&!c.getAttribute('aria-labelledby')).length};
    }""")


def ui_action(page, click):
    """Run one Apply and return the JSON answer of its ui-action request."""
    with page.expect_response(lambda r: r.url.endswith('/ui-action') and r.request.method == 'POST') as answer:
        click()
    return answer.value.json()


def main():
    """Exercise current package files in real managed and linked browser hosts."""
    block = XTwitterBlock()
    # Managed hosts render surfaces in spawned supervised processes: allow for a busy test host.
    expect.set_options(timeout=15000)
    for origin in ("managed", "linked"):
        with isolated_server() as server, sync_playwright() as playwright:
            model = install_test_package(server, "x_twitter", origin=origin)
            candidate = block.build_node_payload(node_id="ui-case", position={"x": 160, "y": 160})
            candidate["block_version"] = model["version"]
            for surface in ("modal", "inspector_panel", "node_card"):
                html = surface_payload(server, model, candidate, surface=surface)["html"]
                assert "{{" not in html
            project = create_project_api(server, document=graph_payload("Owned form", [candidate], []))["project"]
            url = project_editor_url(server.base_url, project["project_id"], workspace_project_id=project["workspace_project_id"])
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.add_init_script("window.localStorage.setItem('bloxsmith.inspectorPinned','true')")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                requests = ModalRequests(page)
                page.goto(url)
                card = page.locator('.canvas-node[data-node-id="ui-case"]')
                expect(card.locator('h3')).to_be_visible()
                expect(card.locator('.node-data-preview')).to_have_text("Remote access off")
                modal = page.locator('.owned-modal')
                open_modal(card, modal, requests)
                apply = modal.locator('[data-owned-apply]')
                expect(apply).to_be_disabled()
                writes = modal.locator('[data-block-config-field="allow_writes"]')
                length = modal.locator('[data-block-config-field="max_weighted_length"]')
                expect(length).to_be_hidden()
                expect(modal.locator('[data-block-config-field="media_root"]')).to_be_hidden()
                writes.check()
                expect(length).to_be_visible()
                expect(modal.locator('[data-block-config-field="media_root"]')).to_be_visible()
                expect(modal.locator('[data-block-config-field="allow_delete"]')).to_be_visible()
                expect(apply).to_be_enabled()
                close_modal(modal, "last")
                open_modal(card, modal, requests)
                expect(writes).not_to_be_checked()
                expect(length).to_be_hidden()
                # Invalid combinations are refused on Apply without changing the saved settings.
                modal.locator('[data-block-config-field="enabled"]').check()
                refused = ui_action(page, apply.click)
                assert "secret_ref" in refused.get("error", ""), refused
                expect(modal.locator('.owned-form-status')).to_contain_text("secret_ref")
                allowed = modal.locator('[data-block-config-field="allowed_operations"]')
                allowed.fill('["search_recent",')
                apply.click()
                expect(modal.locator('.owned-form-status')).to_have_text("Enter valid JSON before applying.")
                modal.locator('[data-block-config-field="secret_ref"]').fill("secret://workspace/x_user")
                modal.locator('[data-block-config-field="operation"]').select_option("post")
                allowed.fill('["search_recent", "user_posts"]')
                gated = ui_action(page, apply.click)
                assert "allow_writes" in gated.get("error", ""), gated
                writes.check()
                length.fill("4000")
                saved = ui_action(page, apply.click)
                assert not saved.get("error"), saved
                if modal.is_visible():
                    close_modal(modal)
                page.reload()
                expect(card.locator('.node-data-preview')).to_have_text("Read and post · post")
                open_modal(card, modal, requests)
                assert json.loads(allowed.input_value()) == ["search_recent", "user_posts"]
                expect(modal.locator('[data-block-config-field="operation"]')).to_have_value("post")
                expect(modal.locator('[data-block-config-field="secret_ref"]')).to_have_value("secret://workspace/x_user")
                expect(writes).to_be_checked()
                expect(length).to_have_value("4000")
                expect(modal.locator('[data-block-config-field="allow_delete"]')).not_to_be_checked()
                for width, height in VIEWPORTS:
                    page.set_viewport_size({"width": width, "height": height})
                    page.wait_for_timeout(100)
                    geometry(modal)
                    page.screenshot(path=artifact_path("x_twitter-" + origin + "-" + str(width) + ".png"))
                summary = modal.locator('.owned-advanced').first.locator('summary')
                summary.scroll_into_view_if_needed()
                summary.focus()
                summary.press('Enter')
                expect(modal.locator('.owned-advanced').first).to_have_attribute('open', '')
                expect(modal.locator('[data-block-config-field="max_pages"]')).to_be_visible()
                expect(modal.locator('[data-block-config-field="max_media_bytes"]')).to_be_visible()
                geometry(modal)
                page.screenshot(path=artifact_path("x_twitter-" + origin + "-advanced-320.png"))
                page.set_viewport_size({"width": 1440, "height": 900})
                close_modal(modal)
                card.click(position={"x": 20, "y": 20})
                inspector = page.locator('[data-properties-surface="inspector"][data-node-id="ui-case"]:visible')
                expect(inspector).to_be_visible()
                assert inspector.evaluate('el => el.scrollWidth <= el.clientWidth+2')
                panel_writes = inspector.locator('[data-block-config-field="allow_writes"]')
                expect(panel_writes).to_be_checked()
                expect(inspector.locator('[data-block-config-field="max_weighted_length"]')).to_be_visible()
                panel_writes.uncheck()
                expect(inspector.locator('[data-block-config-field="max_weighted_length"]')).to_be_hidden()
                inspector.locator('[data-inspector-tab="ports"]').click()
                expect(inspector.locator('[data-inspector-panel-tab="ports"]')).to_be_visible()
                for port in ("request", "media", "posts", "result", "error"):
                    expect(inspector.locator('[data-inspector-panel-tab="ports"]')).to_contain_text(port)
                inspector.locator('[data-inspector-tab="general"]').click()
                expect(inspector.locator('[data-inspector-panel-tab="general"]')).to_be_visible()
                page.screenshot(path=artifact_path("x_twitter-" + origin + "-inspector.png"))
                page.goto(server.base_url + "/")
                page.locator("#homeApplicationSettingsButton").click()
                page.locator("#applicationLanguageSelect").select_option("fr")
                page.wait_for_function("window.CWMessages.getLanguage() === 'fr'")
                page.goto(url)
                # The card preview follows the interface language (the inspector draft above was not applied).
                expect(card.locator('.node-data-preview')).to_have_text("Lecture et publication · post")
                open_modal(card, modal, requests)
                expect(modal.locator('.owned-advanced').first.locator('summary')).to_have_text("Réglages avancés")
                expect(modal.locator('.owned-notice')).to_contain_text("Les jetons restent dans le wallet")
                expect(modal.locator('label[for$="-enabled"]')).to_have_text("Activer l’accès distant")
                expect(modal.locator('label[for$="-allow_writes"]')).to_have_text("Autoriser la publication")
                expect(modal.locator('label[for$="-bearer_secret_ref"]')).to_have_text("Secret du jeton d’application")
                expect(modal.locator('[data-owned-apply]')).to_have_text("Appliquer")
                for width, height in ((1440, 900), (390, 740)):
                    page.set_viewport_size({"width": width, "height": height})
                    page.wait_for_timeout(100)
                    geometry(modal)
                    page.screenshot(path=artifact_path("x_twitter-" + origin + "-fr-" + str(width) + ".png"))
                assert not errors, errors
                assert page.evaluate("!window.CWBlockUiBlocks?.x_twitter")
            finally:
                browser.close()
        print("[ok] x_twitter " + origin + " UI", flush=True)


if __name__ == "__main__":
    main()
