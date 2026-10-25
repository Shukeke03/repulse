"""Real Chromium checks against an isolated local service (no user records changed)."""

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def app_url(tmp_path_factory):
    data = tmp_path_factory.mktemp("browser-data")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {**os.environ, "REPULSE_DATA_DIR": str(data)}
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.api.app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(url + "/api/bootstrap", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    else:
        process.terminate()
        pytest.fail("Local test service did not start")
    yield url
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()


@pytest.fixture()
def browser_page():
    browser_dir = Path(sys.prefix) / "share" / "playwright-browsers"
    if browser_dir.exists():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browser_dir))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        yield page
        browser.close()


def visit(page, url, route="/"):
    page.goto(url + "/#" + route, wait_until="networkidle")
    page.locator("h1").wait_for()
    assert page.locator(".error-block").count() == 0


def test_eight_routes_and_mobile_no_overflow(app_url, browser_page):
    page = browser_page
    for route in ("/", "/monitor", "/sessions", "/replay", "/compare", "/lab", "/system", "/settings"):
        visit(page, app_url, route)
        assert page.locator("h1").inner_text()
    page.set_viewport_size({"width": 360, "height": 780})
    for route in ("/", "/monitor", "/sessions", "/replay", "/compare", "/lab", "/system", "/settings"):
        visit(page, app_url, route)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), route
    assert not page.errors


def test_live_fault_cancel_annotation_replay_and_export(app_url, browser_page):
    page = browser_page
    visit(page, app_url)
    page.locator("#session-name").fill("浏览器验证记录")
    page.locator("#start-session").click()
    page.wait_for_url("**/#/monitor")
    page.locator("#mark-end").wait_for()
    page.wait_for_timeout(1300)
    page.set_viewport_size({"width": 360, "height": 780})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.locator("#freeze-view").click()
    assert "视图已冻结" in page.locator("#frozen-notice").inner_text()
    page.locator("#freeze-view").click()
    page.locator("#mark-end").click()
    page.wait_for_function("document.querySelector('#session-controls')?.dataset.phase === 'RECOVERING'")
    page.locator('[data-nav="/lab"]').click()
    page.locator("#apply-fault").wait_for()
    page.locator("#fault-type").select_option("noise")
    page.locator("#fault-amplitude").fill("2500")
    page.locator("#fault-offset").fill("20")
    page.locator("#apply-fault").click()
    page.wait_for_function("document.querySelector('#fault-plans')?.innerText.includes('白噪声')")
    page.locator("[data-fault]").click()
    page.wait_for_function("document.querySelector('#fault-plans')?.innerText.includes('REVOKED')")
    page.locator('[data-nav="/monitor"]').click()
    page.locator("#cancel-session").wait_for()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#cancel-session").click()
    page.locator("#view-result").wait_for()
    page.locator("#view-result").click()
    page.locator("#annotation-notes").fill("端到端验证备注")
    page.locator("#annotation-tags").fill("浏览器, 验证")
    page.locator("#save-annotation").click()
    page.wait_for_function(
        "Array.from(document.querySelectorAll('.toast')).some(x=>x.innerText.includes('备注与标签已保存'))"
    )
    with page.expect_download() as info:
        page.locator('[data-export="summary"]').click()
    download = info.value
    assert download.suggested_filename.endswith(".json")
    page.locator("#detail-replay").click()
    page.locator("#replay-play").wait_for()
    assert page.locator("#replay-state").inner_text() == "已暂停"
    page.locator("#replay-step").click()
    page.wait_for_function("parseFloat(document.querySelector('#replay-cursor').innerText) >= 1")
    page.locator("#replay-play").click()
    page.locator("#replay-restart").click()
    page.wait_for_function("parseFloat(document.querySelector('#replay-cursor').innerText) < 0.2")
    with page.expect_download() as png:
        page.locator("#replay-png").click()
    assert png.value.suggested_filename.endswith(".png")
    assert not page.errors


def test_settings_and_background_compare(app_url, browser_page):
    page = browser_page
    visit(page, app_url, "/settings")
    page.locator('[data-setting="chart_seconds"]').select_option("30")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator('[data-nav="/"]').click()
    page.wait_for_url("**/#/settings")
    assert page.locator('[data-setting="chart_seconds"]').input_value() == "30"
    page.locator("#save-settings").click()
    page.wait_for_function("document.querySelector('#dirty-label')?.innerText === '所有修改已保存'")
    page.reload(wait_until="networkidle")
    assert page.locator('[data-setting="chart_seconds"]').input_value() == "30"
    page.locator('[data-nav="/compare"]').click()
    page.locator("#compare-seeds").fill("1")
    page.locator("#compare-phases").fill("0")
    page.locator("#start-compare").click()
    page.wait_for_function("document.querySelector('#compare-progress')?.innerText.includes('已完成')")
    page.wait_for_function(
        "document.querySelector('#compare-badge')?.innerText.includes('已完成')", timeout=60000
    )
    assert page.locator("#compare-results tbody tr").count() == 3
    assert not page.errors


def test_qemu_source_controls_and_real_tasks(app_url, browser_page):
    page = browser_page
    visit(page, app_url)
    capabilities = page.request.get(app_url + "/api/bootstrap").json()["capabilities"]
    if not capabilities.get("qemu"):
        pytest.skip("QEMU or firmware is unavailable")
    page.locator("#session-source").select_option("FREERTOS_QEMU")
    assert page.locator('#session-scenario option[value="interference"]').is_disabled()
    assert page.locator('[data-strategy="periodic"]').is_disabled()
    page.locator("#session-name").fill("QEMU 浏览器验证")
    page.locator("#start-session").click()
    page.wait_for_url("**/#/monitor")
    page.locator("#mark-end").wait_for()
    page.wait_for_timeout(1100)
    page.locator('[data-nav="/system"]').click()
    page.locator(".system-card").first.wait_for()
    assert "FREERTOS_QEMU" in page.locator(".system-card").nth(1).inner_text()
    assert page.locator("table tbody tr").count() == 6
    page.locator('[data-nav="/lab"]').click()
    page.locator("#apply-fault").wait_for()
    assert page.locator("#apply-fault").is_disabled()
    assert "QEMU" in page.locator(".card-footer").inner_text()
    page.locator('[data-nav="/monitor"]').click()
    page.locator("#cancel-session").wait_for()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#cancel-session").click()
    page.locator("#view-result").wait_for()
    assert not page.errors
