"""Real Chromium checks using the locally downloaded public exercise records."""

import json
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
    directory = tmp_path_factory.mktemp("public-browser-data")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    log = (directory / "server.log").open("w+")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.api.app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env={**os.environ, "REPULSE_DATA_DIR": str(directory / "runtime")},
        stdout=log,
        stderr=log,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            with urllib.request.urlopen(url + "/api/bootstrap", timeout=1) as response:
                bootstrap = json.load(response)
                assert bootstrap["datasets"], "Download the public exercise records before browser tests"
            break
        except OSError:
            time.sleep(0.1)
    else:
        process.terminate()
        log.seek(0)
        pytest.fail("Local test service did not start: " + log.read())
    yield url
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)
    log.close()


@pytest.fixture()
def browser_page():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path(sys.prefix) / "share/playwright-browsers"))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        page.errors = []
        page.on("pageerror", lambda error: page.errors.append(str(error)))
        yield page
        browser.close()


def visit(page, url, route="/"):
    page.goto(url + "/#" + route, wait_until="networkidle")
    page.locator("h1").wait_for()
    assert page.locator(".error-block").count() == 0


def start_demo(page, url, name, mode="PYTHON", speed="10"):
    visit(page, url)
    page.locator("#session-name").fill(name)
    page.locator("#session-source").select_option(mode)
    page.locator("#session-speed").select_option(speed)
    page.locator("#start-session").click()
    page.wait_for_url("**/#/monitor")
    page.locator("#monitor-origin").wait_for()
    assert "公开人体记录" in page.locator("#monitor-origin").inner_text()
    assert page.locator("#mark-end").count() == 0


def wait_complete(page):
    page.locator("#view-result").wait_for(timeout=90000)
    assert "已完成" in page.locator("#session-controls").inner_text()


def test_eight_routes_and_mobile_readability(app_url, browser_page):
    page = browser_page
    for route in ("/", "/monitor", "/sessions", "/replay", "/compare", "/lab", "/system", "/settings"):
        visit(page, app_url, route)
        assert page.locator("h1").inner_text()
        for removed in ("随机种子", "正常恢复", "故障注入", "WORKSPACE", "每一次恢复"):
            assert removed not in page.locator("body").inner_text()
    visit(page, app_url)
    assert (
        page.locator("#session-dataset").input_value()
        == page.request.get(app_url + "/api/bootstrap").json()["defaults"]["dataset_id"]
    )
    assert page.locator('[data-strategy="continuous"]').get_attribute("class").endswith("selected")
    visit(page, app_url, "/lab")
    page.locator("summary", has_text="查看原始文件与校验值").click()
    with page.expect_download() as raw:
        page.locator("#data-files a[download]").first.click()
    assert raw.value.suggested_filename.endswith(".csv")
    page.set_viewport_size({"width": 360, "height": 780})
    for route in ("/", "/monitor", "/sessions", "/replay", "/compare", "/lab", "/system", "/settings"):
        visit(page, app_url, route)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), route
    assert page.locator('[data-nav="/settings"]').is_visible()
    assert not page.errors


def test_public_data_complete_export_and_independent_replay(app_url, browser_page):
    page = browser_page
    start_demo(page, app_url, "真实记录浏览器验收")
    page.wait_for_function("document.querySelector('#session-controls')?.dataset.phase === 'RECOVERING'")
    t0 = page.request.get(app_url + "/api/bootstrap").json()["active_session"]["t0_us"]
    assert t0 == 10_000_000
    page.locator("#freeze-view").click()
    assert "视图已冻结" in page.locator("#frozen-notice").inner_text()
    frozen = page.locator("#frozen-notice").inner_text()
    page.locator('[data-nav="/lab"]').click()
    page.locator("#data-record").wait_for()
    assert "64 Hz" in page.locator("#data-metadata").inner_text()
    assert page.locator("#apply-fault").count() == 0
    page.locator('[data-nav="/monitor"]').click()
    page.locator("#frozen-notice").wait_for()
    assert page.locator("#frozen-notice").inner_text() == frozen
    page.locator("#freeze-view").click()
    page.set_viewport_size({"width": 360, "height": 780})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.set_viewport_size({"width": 1440, "height": 1000})
    wait_complete(page)
    page.locator("#view-result").click()
    page.locator("#annotation-notes").fill("来自公开人体记录的完整演示")
    page.locator("#annotation-tags").fill("真实记录,验收")
    page.locator("#save-annotation").click()
    page.wait_for_function("document.querySelector('#annotation-status')?.innerText === '已保存'")
    for format, suffix in (("summary", ".json"), ("csv", ".csv"), ("raw", ".zip")):
        with page.expect_download() as download:
            page.locator(f'[data-export="{format}"]').click()
        assert download.value.suggested_filename.endswith(suffix)
    page.locator("#detail-replay").click()
    page.locator("#replay-play").wait_for()
    assert page.locator("#replay-state").inner_text() == "已暂停"
    assert page.locator("#replay-windows .badge").all_inner_texts() == ["尚未到达"] * 3
    page.locator("#replay-step").click()
    page.wait_for_function("parseFloat(document.querySelector('#replay-cursor').innerText) >= 1")
    page.locator("#replay-seek").fill("76")
    page.locator("#replay-seek").dispatch_event("change")
    page.wait_for_function("parseFloat(document.querySelector('#replay-cursor').innerText) >= 76")
    assert page.locator("#replay-windows .badge").nth(2).inner_text() == "尚未到达"
    page.locator("#original-view").click()
    page.wait_for_function(
        "document.querySelector('#replay-mode-explanation')?.innerText.includes('不随回放游标变化')"
    )
    page.locator("#derived-view").click()
    page.locator("#replay-restart").click()
    page.wait_for_function("parseFloat(document.querySelector('#replay-cursor').innerText) < .2")
    assert page.locator("#replay-windows .badge").all_inner_texts() == ["尚未到达"] * 3
    with page.expect_download() as download:
        page.locator("#replay-png").click()
    assert download.value.suggested_filename.endswith(".png")
    page.locator('[data-nav="/sessions"]').click()
    row = page.locator("tbody tr").filter(has_text="真实记录浏览器验收")
    row.locator("[data-detail]").click()
    page.locator("#close-drawer").wait_for()
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("document.activeElement.id") == "delete-record"
    page.keyboard.press("Escape")
    assert page.locator(".drawer").count() == 0
    row.locator("[data-detail]").click()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#delete-record").click()
    page.wait_for_function("!document.querySelector('.drawer')")
    page.wait_for_function(
        "!document.querySelector('#sessions-table')?.innerText.includes('真实记录浏览器验收')"
    )
    page.locator('[data-nav="/monitor"]').click()
    page.wait_for_function("document.querySelector('#monitor-content')?.innerText.includes('当前没有演示')")
    assert not page.errors


def test_settings_drafts_and_real_record_comparison(app_url, browser_page):
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
    page.locator("#compare-phases").fill("0")
    page.locator("#start-compare").click()
    page.wait_for_function("document.querySelector('#compare-badge')?.innerText === '已完成'", timeout=60000)
    assert page.locator("#compare-results > .table-wrap tbody tr").count() == 3
    assert page.locator("#compare-seeds").count() == 0
    for format in ("json", "csv"):
        with page.expect_download() as download:
            page.locator(f"#export-compare-{format}").click()
        assert download.value.suggested_filename.endswith("." + format)
    assert not page.errors


def test_qemu_consumes_public_record_and_reports_real_tasks(app_url, browser_page):
    page = browser_page
    capabilities = page.request.get(app_url + "/api/bootstrap").json()["capabilities"]
    if not capabilities["qemu"]:
        pytest.skip("Build QEMU to run real firmware browser verification")
    start_demo(page, app_url, "FreeRTOS真实记录验收", mode="FREERTOS_QEMU")
    page.wait_for_function(
        "document.querySelector('#session-controls')?.dataset.phase === 'RECOVERING'", timeout=60000
    )
    page.locator('[data-nav="/system"]').click()
    page.locator("#system-tasks tbody tr").first.wait_for()
    assert page.locator("#system-tasks tbody tr").count() == 6
    assert "真实FreeRTOS" in page.locator("#system-task-note").inner_text()
    page.locator('[data-nav="/monitor"]').click()
    wait_complete(page)
    assert "FreeRTOS" in page.locator("#monitor-origin").inner_text()
    assert not page.errors
