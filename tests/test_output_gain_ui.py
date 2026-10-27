"""Cockpit DOM contracts against real assets; audio and host devices are not opened."""
import base64
import functools
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api", reason="optional browser coverage needs playwright")


@pytest.fixture(scope="module")
def cockpit_url():
    assets = Path(__file__).resolve().parents[1] / "apps" / "web_cockpit"

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(assets)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def browser_page(cockpit_url):
    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(
                headless=True, executable_path=os.environ.get("WLBS_TEST_CHROMIUM")
            )
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("optional browser coverage needs playwright install chromium")
            raise
        page = browser.new_page(viewport={"width": 1660, "height": 960})
        # A fake transport prevents reconnect timers and camera retries while preserving real DOM JS.
        page.add_init_script("""window.EventSource = class { constructor() { window.testEventSource=this; } close() {} };""")
        state = {"gain": None, "status": 200, "posts": []}

        def config(route):
            if state.get("hold_config"):
                state.setdefault("config_routes", []).append(route)
                return
            if state["gain"] is None:
                route.fulfill(status=503, body="config not ready")
            else:
                route.fulfill(json={"output_gain_db": state["gain"]})

        def command(route):
            state["posts"].append({"body": route.request.post_data_json,
                                   "content_type": route.request.headers.get("content-type")})
            if state.get("hold_command"):
                state.setdefault("command_routes", []).append(route)
                return
            route.fulfill(status=state["status"], json={})

        page.route("**/api/get_config", config)
        page.route("**/api/set_output_gain", command)
        page.route("**/events", lambda route: route.fulfill(content_type="text/event-stream", body=": test\n\n"))
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
        page.route("**/camera*", lambda route: route.fulfill(content_type="image/png", body=png))
        yield page, state, cockpit_url
        browser.close()


def live(page, gain=0, peak=-12, rms=-18, clipped=0, invalid=0):
    page.evaluate("s => window.cockpit.applyState({output_level:s})", {
        "gain_db": gain, "peak_dbfs": peak, "rms_dbfs": rms,
        "clipped_total": clipped, "non_finite_total": invalid,
    })


def release(page, value):
    page.locator("#output-gain").evaluate("(el, value) => { el.value=value; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }", value)


def test_initial_disabled_then_live_and_config_gain(browser_page):
    page, state, url = browser_page
    page.goto(url)
    playwright.expect(page.locator("#output-gain")).to_be_disabled()
    live(page, gain=4.5)
    playwright.expect(page.locator("#output-gain")).to_be_enabled()
    playwright.expect(page.locator("#output-gain-value")).to_have_text("+4.5 dB")
    state["gain"] = -2
    page.reload()
    playwright.expect(page.locator("#output-gain-value")).to_have_text("-2.0 dB")
    playwright.expect(page.locator("#output-gain")).to_have_value("-2")


def test_release_posts_once_pending_resists_stale_state_then_confirms_and_resets(browser_page):
    page, state, url = browser_page
    state["gain"] = 0
    page.goto(url)
    playwright.expect(page.locator("#output-gain")).to_be_enabled()
    slider = page.locator("#output-gain")
    slider.evaluate("el => { for (const v of [1,2,3,6]) { el.value=v; el.dispatchEvent(new Event('input')); } }")
    assert state["posts"] == []
    with page.expect_response("**/api/set_output_gain"):
        release(page, 6)
    playwright.expect(slider).to_be_disabled()
    page.wait_for_function("document.querySelector('#output-gain-help').textContent === 'Applying gain…'")
    live(page, gain=0)
    playwright.expect(page.locator("#output-gain-value")).to_have_text("+6.0 dB")
    playwright.expect(slider).to_have_value("6")
    live(page, gain=6)
    playwright.expect(slider).to_be_enabled()
    assert state["posts"] == [{"body": [6], "content_type": "application/json"}]
    with page.expect_response("**/api/set_output_gain"):
        page.locator("#output-gain-reset").click()
    live(page, gain=0)
    playwright.expect(page.locator("#output-gain-value")).to_have_text("0.0 dB")
    playwright.expect(slider).to_be_enabled()
    assert [post["body"] for post in state["posts"]] == [[6], [0]]


def test_failed_post_restores_confirmed_gain(browser_page):
    page, state, url = browser_page
    state.update(gain=2, status=500)
    page.goto(url)
    playwright.expect(page.locator("#output-gain")).to_be_enabled()
    release(page, 8)
    playwright.expect(page.locator("#output-gain-help")).to_contain_text("not confirmed")
    playwright.expect(page.locator("#output-gain")).to_have_value("2")
    playwright.expect(page.locator("#output-gain-value")).to_have_text("+2.0 dB")
    playwright.expect(page.locator("#output-gain")).to_be_enabled()


@pytest.mark.parametrize("width,height", [(1660, 960), (900, 820)])
def test_meter_silence_positive_peak_persistent_clipping_and_layout(browser_page, width, height):
    page, state, url = browser_page
    page.set_viewport_size({"width": width, "height": height})
    state["gain"] = 0
    page.goto(url)
    live(page, peak=None, rms=None)
    playwright.expect(page.locator("#output-level-text")).to_have_text("Peak −∞ · RMS −∞ dBFS")
    assert page.locator("#output-level-fill").evaluate("el => el.style.width") == "0%"
    live(page, gain=24, peak=6.2, rms=-0.5, clipped=123456789, invalid=12)
    playwright.expect(page.locator("#output-level-text")).to_have_text("Peak +6.2 · RMS -0.5 dBFS")
    playwright.expect(page.locator("#output-level-fill")).to_have_class("bar-fill bad")
    playwright.expect(page.locator("#output-clipping")).to_contain_text("123,456,789 since start")
    live(page, gain=24, peak=None, rms=None, clipped=123456789, invalid=12)
    playwright.expect(page.locator("#output-clipping")).to_contain_text("123,456,789 since start")
    # Measure the rendered text rectangles, including wrapped hints, against their real containers.
    overflow = page.locator(".output-panel").evaluate("""panel => {
      const box = panel.getBoundingClientRect();
      return [...panel.querySelectorAll('label,output,.meter-val,.output-hint,button')].flatMap(el => {
        const range = document.createRange(); range.selectNodeContents(el);
        return [...range.getClientRects()].filter(r => r.left < box.left-1 || r.right > box.right+1 || r.top < box.top-1 || r.bottom > box.bottom+1).map(() => el.id || el.textContent);
      });
    }""")
    assert overflow == []



def test_hanging_fallback_config_releases_pending_controls(browser_page):
    page, state, url = browser_page
    state["gain"] = 2
    page.goto(url)
    playwright.expect(page.locator("#output-gain")).to_be_enabled()
    state["hold_config"] = True
    with page.expect_response("**/api/set_output_gain"):
        release(page, 8)
    playwright.expect(page.locator("#output-gain")).to_be_disabled()
    # Exercise the real four-second confirmation timer and one-second GET deadline.
    playwright.expect(page.locator("#output-gain-help")).to_contain_text("not confirmed", timeout=7000)
    assert len(state.get("config_routes", [])) == 1
    playwright.expect(page.locator("#output-gain")).to_be_enabled()
    playwright.expect(page.locator("#output-gain")).to_have_value("2")


def test_disconnect_invalidates_pending_reply_and_reconnect_receives_gain(browser_page):
    page, state, url = browser_page
    state.update(gain=2, hold_command=True)
    page.goto(url)
    playwright.expect(page.locator("#output-gain")).to_be_enabled()
    with page.expect_request("**/api/set_output_gain"):
        release(page, 8)
    page.evaluate("window.testEventSource.onerror()")
    playwright.expect(page.locator("#output-gain")).to_be_disabled()
    playwright.expect(page.locator("#output-gain-value")).to_have_text("Disconnected")
    page.evaluate("window.testEventSource.onopen()")
    live(page, gain=3)
    playwright.expect(page.locator("#output-gain")).to_be_enabled()
    state["command_routes"][0].fulfill(status=500, json={})
    playwright.expect(page.locator("#output-gain-value")).to_have_text("+3.0 dB")
    playwright.expect(page.locator("#output-gain")).to_have_value("3")
    assert "not confirmed" not in page.locator("#output-gain-help").inner_text()


def test_delayed_initial_config_does_not_override_newer_live_gain(browser_page):
    page, state, url = browser_page
    state["hold_config"] = True
    with page.expect_request("**/api/get_config"):
        page.goto(url, wait_until="domcontentloaded")
    live(page, gain=5)
    with page.expect_response("**/api/get_config"):
        state["config_routes"][0].fulfill(json={"output_gain_db": -4})
    playwright.expect(page.locator("#output-gain-value")).to_have_text("+5.0 dB")
    playwright.expect(page.locator("#output-gain")).to_have_value("5")
