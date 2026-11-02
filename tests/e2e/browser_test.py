# 启动隔离服务，通过真实Chromium验证主要页面与完整操作流程。
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
from playwright.sync_api import expect, sync_playwright

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


def start_demo(page, url, name, speed="10"):
    if not page.request.get(url + "/api/bootstrap").json()["capabilities"]["qemu"]:
        pytest.skip("Build QEMU to run firmware browser verification")
    visit(page, url, "/workbench")
    page.locator("#session-name").fill(name)
    expect(page.locator("#session-source")).to_have_value("FreeRTOS / QEMU")
    expect(page.locator("#session-source")).not_to_be_editable()
    page.locator("#session-speed").select_option(speed)
    with page.expect_response(
        lambda r: r.url.endswith("/api/sessions") and r.request.method == "POST"
    ) as created:
        page.locator("#start-session").click()
    assert created.value.request.post_data_json["execution_mode"] == "FREERTOS_QEMU"
    assert created.value.json()["execution_mode"] == "FREERTOS_QEMU"
    page.wait_for_url("**/#/monitor")
    page.locator("#monitor-origin").wait_for()
    assert "公开人体记录" in page.locator("#monitor-origin").inner_text()
    assert page.locator("#mark-end").count() == 0


def wait_complete(page):
    page.locator("#view-result").wait_for(timeout=90000)
    assert "已完成" in page.locator("#session-controls").inner_text()


def test_nine_routes_and_mobile_readability(app_url, browser_page):
    page = browser_page
    for route in (
        "/",
        "/monitor",
        "/workbench",
        "/sessions",
        "/replay",
        "/compare",
        "/lab",
        "/system",
        "/settings",
    ):
        visit(page, app_url, route)
        assert page.locator("h1").inner_text()
        for removed in ("随机种子", "正常恢复", "故障注入", "WORKSPACE", "每一次恢复"):
            assert removed not in page.locator("body").inner_text()
    visit(page, app_url, "/workbench")
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
    for route in (
        "/",
        "/monitor",
        "/workbench",
        "/sessions",
        "/replay",
        "/compare",
        "/lab",
        "/system",
        "/settings",
    ):
        visit(page, app_url, route)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), route
    assert page.locator('[data-nav="/settings"]').is_visible()
    assert not page.errors


def test_public_data_complete_export_and_independent_replay(app_url, browser_page):
    page = browser_page
    # 保留真实回放连接句柄，测试断开后的提示和自动重连。
    page.add_init_script(
        """(() => {
          const NativeWebSocket = window.WebSocket;
          window.WebSocket = class extends NativeWebSocket {
            constructor(url, protocols) {
              super(url, ...(protocols === undefined ? [] : [protocols]));
              if (String(url).includes('/api/replays/')) window.__replaySocket = this;
            }
          };
        })();"""
    )
    start_demo(page, app_url, "真实记录浏览器验收")
    page.wait_for_function(
        "document.querySelector('#session-controls')?.dataset.phase === 'RECOVERING'", timeout=60000
    )
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
    page.wait_for_function("window.__replaySocket?.readyState === WebSocket.OPEN")
    assert page.locator("#replay-disconnected").is_hidden()
    page.evaluate("window.__replaySocket.close()")
    page.locator("#replay-disconnected").wait_for(state="visible")
    page.wait_for_function("window.__replaySocket?.readyState === WebSocket.OPEN")
    page.locator("#replay-disconnected").wait_for(state="hidden")
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
    page.wait_for_function("document.querySelector('#compare-badge')?.innerText === '已完成'", timeout=180000)
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
    start_demo(page, app_url, "FreeRTOS真实记录验收")
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


# 腕端验收通过真实按键、画布的可访问状态与后端结果检查交互，不调用腕端内部实现。
def wrist_key(page, key):
    page.locator("#main").focus()
    page.keyboard.press(key)


def wrist_screen(page, screen, focus=None):
    stage = page.locator("#wrist-stage")
    expect(stage).to_have_attribute("data-screen", screen)
    if focus is not None:
        expect(stage).to_have_attribute("data-focus", focus)


def test_wrist_full_workspace_layout_and_button_navigation(app_url, browser_page):
    page = browser_page
    visit(page, app_url)
    canvas = page.locator("#wrist-screen")
    assert canvas.get_attribute("width") == "240"
    assert canvas.get_attribute("height") == "280"
    for width, height in ((1440, 1000), (1024, 768), (360, 780)):
        page.set_viewport_size({"width": width, "height": height})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        stage = page.locator("#wrist-stage").bounding_box()
        content = page.locator(".content").bounding_box()
        assert abs(stage["x"] - content["x"]) < 1
        assert abs(stage["width"] - content["width"]) < 1
        assert stage["height"] >= height - (108 if width == 360 else 0)
        # resize 处理器在浏览器下一次布局时调整设备缩放，等其应用后检查屏幕边界。
        page.wait_for_function(
            """() => {
              const rect = document.querySelector('#wrist-screen').getBoundingClientRect();
              return rect.x >= 0 && rect.width > 0 && rect.right <= innerWidth;
            }"""
        )
        assert page.locator(".topbar").is_hidden()
    page.set_viewport_size({"width": 1440, "height": 1000})
    # 点击表壳实体键进入菜单，再仅用键盘逐项打开八个腕端应用。
    page.locator('.pusher[data-key="ok"]').click()
    wrist_screen(page, "menu", "新建演示")
    for screen, label in (
        ("start", "新建演示"),
        ("monitor", "恢复过程"),
        ("records", "记录库"),
        ("replays", "记录回放"),
        ("compare", "策略比较"),
        ("data", "数据与片段"),
        ("system", "系统状态"),
        ("settings", "设置"),
    ):
        expect(page.locator("#wrist-stage")).to_have_attribute("data-focus", label)
        wrist_key(page, "Enter")
        wrist_screen(page, screen)
        assert label in canvas.get_attribute("aria-label") or screen in ("monitor", "system")
        wrist_key(page, "Escape")
        wrist_screen(page, "menu", label)
        wrist_key(page, "ArrowDown")
    assert not page.errors


def test_wrist_settings_draft_protection_and_persistence(app_url, browser_page):
    page = browser_page
    visit(page, app_url)
    saved = page.request.get(app_url + "/api/settings").json()
    new_seconds = 10 if saved["chart_seconds"] == 30 else 30
    page.locator('.map-node[data-jump="settings"]').click()
    wrist_key(page, "ArrowDown")
    wrist_screen(page, "settings", f"电脑端图表时长：最近 {saved['chart_seconds']} 秒")
    wrist_key(page, "Enter")
    wrist_screen(page, "picker")
    wrist_key(page, "ArrowDown")
    wrist_key(page, "Enter")
    wrist_screen(page, "settings", f"电脑端图表时长：最近 {new_seconds} 秒")
    page.locator('.map-node[data-jump="face"]').click()
    wrist_screen(page, "confirm", "继续编辑")
    wrist_key(page, "Enter")
    wrist_screen(page, "settings", f"电脑端图表时长：最近 {new_seconds} 秒")
    # 切换电脑页面同样保护未保存的腕端草稿；拒绝后仍可继续编辑。
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator('[data-nav="/monitor"]').click()
    page.wait_for_url("**/#/")
    wrist_screen(page, "settings")
    for _ in range(3):
        wrist_key(page, "ArrowDown")
    wrist_screen(page, "settings", "保存设置")
    with page.expect_response(
        lambda r: r.url.endswith("/api/settings") and r.request.method == "PUT"
    ) as response:
        wrist_key(page, "Enter")
    assert response.value.ok
    assert response.value.json()["chart_seconds"] == new_seconds
    wrist_screen(page, "settings", "设置已保存")
    visit(page, app_url, "/settings")
    assert page.locator('[data-setting="chart_seconds"]').input_value() == str(new_seconds)
    # 刷新后从服务读取已保存值；第二份草稿放弃后不能覆盖保存结果。
    visit(page, app_url)
    page.locator('.map-node[data-jump="settings"]').click()
    wrist_key(page, "ArrowDown")
    wrist_screen(page, "settings", f"电脑端图表时长：最近 {new_seconds} 秒")
    wrist_key(page, "Enter")
    wrist_key(page, "ArrowDown")
    wrist_key(page, "Enter")
    page.locator('.map-node[data-jump="face"]').click()
    wrist_screen(page, "confirm", "继续编辑")
    wrist_key(page, "ArrowDown")
    wrist_screen(page, "confirm", "放弃修改")
    wrist_key(page, "Enter")
    wrist_screen(page, "face")
    assert page.request.get(app_url + "/api/settings").json()["chart_seconds"] == new_seconds
    page.locator('.map-node[data-jump="settings"]').click()
    wrist_key(page, "ArrowDown")
    wrist_screen(page, "settings", f"电脑端图表时长：最近 {new_seconds} 秒")
    # 不重载应用：电脑端保存后，恢复保留的腕端设置栈必须同步服务新值。
    desktop_seconds = saved["chart_seconds"]
    page.locator('[data-nav="/settings"]').click()
    page.locator('[data-setting="chart_seconds"]').select_option(str(desktop_seconds))
    with page.expect_response(
        lambda r: r.url.endswith("/api/settings") and r.request.method == "PUT"
    ) as desktop_saved:
        page.locator("#save-settings").click()
    assert desktop_saved.value.json()["chart_seconds"] == desktop_seconds
    page.locator('[data-nav="/"]').click()
    wrist_screen(page, "settings", f"电脑端图表时长：最近 {desktop_seconds} 秒")
    # 接着只改刷新率并保存，不能把电脑端刚保存的图表时长回写成旧值。
    new_hz = 5 if saved["ui_hz"] == 10 else 10
    wrist_key(page, "ArrowUp")
    wrist_key(page, "Enter")
    wrist_key(page, "ArrowDown")
    wrist_key(page, "Enter")
    wrist_screen(page, "settings", f"界面刷新：每秒 {new_hz} 次")
    for _ in range(4):
        wrist_key(page, "ArrowDown")
    wrist_screen(page, "settings", "保存设置")
    with page.expect_response(
        lambda r: r.url.endswith("/api/settings") and r.request.method == "PUT"
    ) as wrist_saved:
        wrist_key(page, "Enter")
    assert wrist_saved.value.json()["chart_seconds"] == desktop_seconds
    assert wrist_saved.value.json()["ui_hz"] == new_hz
    wrist_screen(page, "settings", "设置已保存")
    assert page.request.get(app_url + "/api/settings").json()["chart_seconds"] == desktop_seconds
    assert not page.errors


def test_wrist_buttons_complete_shared_session_and_independent_replay(app_url, browser_page):
    page = browser_page
    if not page.request.get(app_url + "/api/bootstrap").json()["capabilities"]["qemu"]:
        pytest.skip("Build QEMU to run firmware wrist verification")
    visit(page, app_url)
    wrist_key(page, "Enter")
    wrist_screen(page, "menu", "新建演示")
    wrist_key(page, "Enter")
    wrist_screen(page, "start")
    wrist_key(page, "ArrowDown")
    wrist_screen(page, "start", "处理环境：FreeRTOS / QEMU")
    wrist_key(page, "Enter")
    wrist_screen(page, "start", "处理环境：FreeRTOS / QEMU")
    for _ in range(3):
        wrist_key(page, "ArrowDown")
    wrist_screen(page, "start", "演示速度：1 倍")
    wrist_key(page, "Enter")
    for _ in range(3):
        wrist_key(page, "ArrowDown")
    wrist_key(page, "Enter")
    wrist_screen(page, "start", "演示速度：10 倍")
    wrist_key(page, "ArrowDown")
    with page.expect_response(
        lambda r: r.url.endswith("/api/sessions") and r.request.method == "POST"
    ) as created:
        wrist_key(page, "Enter")
    sid = created.value.json()["id"]
    assert created.value.request.post_data_json["execution_mode"] == "FREERTOS_QEMU"
    assert created.value.json()["execution_mode"] == "FREERTOS_QEMU"
    wrist_screen(page, "monitor")
    page.locator("#main").focus()
    page.keyboard.down("Enter")
    try:
        wrist_screen(page, "confirm", "继续演示")
    finally:
        page.keyboard.up("Enter")
    # 停止确认默认继续，防止长按误触直接终止处理。
    wrist_key(page, "Enter")
    wrist_screen(page, "monitor")
    assert page.request.get(app_url + f"/api/sessions/{sid}").json()["status"] in ("PREPARING", "RECOVERING")
    for index in range(1, 5):
        wrist_key(page, "ArrowDown")
        expect(page.locator("#wrist-stage")).to_have_attribute("data-page", str(index))
    page.locator('[data-nav="/monitor"]').click()
    page.locator("#wrist-mirror").wait_for()
    assert page.locator("#session-controls").get_attribute("data-phase") in ("PREPARING", "RECOVERING")
    page.locator(".mirror-link").click()
    wrist_screen(page, "monitor")
    expect(page.locator("#wrist-stage")).to_have_attribute("data-page", "4")
    expect(page.locator("#stage-session")).to_have_text("最近演示：已完成", timeout=60000)
    completed = page.request.get(app_url + f"/api/sessions/{sid}").json()
    assert completed["execution_mode"] == "FREERTOS_QEMU"
    assert completed["engine_time_us"] == 134_000_000
    assert len(completed["key_windows"]) == 3
    wrist_key(page, "Escape")
    wrist_screen(page, "menu", "新建演示")
    wrist_key(page, "ArrowDown")
    wrist_key(page, "ArrowDown")
    wrist_key(page, "Enter")
    wrist_screen(page, "records")
    expect(page.locator("#wrist-stage")).not_to_have_attribute("data-focus", "")
    wrist_key(page, "Enter")
    wrist_screen(page, "record", "窗口结果")
    wrist_key(page, "ArrowDown")
    wrist_screen(page, "record", "回放此记录")
    with page.expect_response(
        lambda r: r.url.endswith("/api/replays") and r.request.method == "POST"
    ) as opened:
        wrist_key(page, "Enter")
    rid = opened.value.json()["id"]
    assert opened.value.json()["source_session_id"] == sid
    wrist_screen(page, "replay", "播放或暂停")
    wrist_key(page, "ArrowDown")
    with page.expect_response(
        lambda r: r.url.endswith(f"/api/replays/{rid}") and r.request.method == "GET"
    ) as stepped:
        wrist_key(page, "Enter")
    assert stepped.value.json()["cursor_us"] == 1_000_000
    # 离开腕端回放会关闭其独立实例，且不改动保存的会话结果。
    with page.expect_response(
        lambda r: r.url.endswith(f"/api/replays/{rid}/commands") and r.request.method == "POST"
    ) as closed:
        page.locator('[data-nav="/sessions"]').click()
    assert closed.value.ok
    assert page.request.get(app_url + f"/api/replays/{rid}").status == 404
    after = page.request.get(app_url + f"/api/sessions/{sid}").json()
    assert after["key_windows"] == completed["key_windows"]
    assert after["status"] == "COMPLETED"
    assert not page.errors


def test_wrist_new_comparison_survives_delayed_initial_list(app_url, browser_page):
    page = browser_page
    delayed_lists = []
    job = {"id": "wrist-race-job", "status": "RUNNING", "progress": {"total": 12, "completed": 0}}

    def comparison_requests(route):
        if route.request.method == "GET":
            # 挂起打开页面时读取的旧列表，直到新建任务已成功。
            delayed_lists.append(route)
        else:
            route.fulfill(json=job)

    page.route("**/api/comparisons", comparison_requests)
    page.route("**/api/comparisons/wrist-race-job", lambda route: route.fulfill(json=job))
    visit(page, app_url)
    page.locator('.map-node[data-jump="compare"]').click()
    wrist_screen(page, "compare")
    wrist_key(page, "ArrowDown")
    wrist_key(page, "ArrowDown")
    wrist_screen(page, "compare", "开始比较")
    assert len(delayed_lists) == 1
    wrist_key(page, "Enter")
    wrist_screen(page, "compare", "停止比较")
    # 旧列表为空；它迟到后不能清掉新任务或停止该任务的后续轮询。
    with page.expect_response(lambda r: r.url.endswith("/api/comparisons/wrist-race-job")) as polled:
        delayed_lists[0].fulfill(json={"items": []})
    assert polled.value.json()["id"] == job["id"]
    wrist_screen(page, "compare", "停止比较")
    assert not page.errors


def test_wrist_default_home_alias_and_first_key_after_long_idle(app_url, browser_page):
    page = browser_page
    page.clock.install()
    # 首次打开没有 hash 时直接进入腕端，旧 /wrist 地址继续兼容。
    page.goto(app_url, wait_until="networkidle")
    wrist_screen(page, "face")
    expect(page.locator('[data-nav="/"]')).to_have_class("nav-item active")
    assert page.locator(".topbar").is_hidden()
    guide = page.locator("#keyboard-guide")
    expect(guide).to_be_in_viewport(ratio=1)
    expect(page.locator("#keyboard-title")).to_have_text("用键盘，直接操控手表")
    expect(page.locator("#keyboard-help")).to_contain_text("无需点击表盘")
    expect(page.locator(".keyboard-keys button")).to_have_count(4)
    for key, label in (("back", "Esc"), ("up", "↑"), ("down", "↓"), ("ok", "Enter")):
        expect(guide.locator(f'[data-key="{key}"]')).to_contain_text(label)
    guide.locator('[data-key="ok"]').click()
    wrist_screen(page, "menu", "新建演示")
    guide.locator('[data-key="back"]').click()
    wrist_screen(page, "face")
    canvas = page.locator("#wrist-screen")
    before = canvas.evaluate("element => getComputedStyle(element).filter")
    page.clock.fast_forward(10 * 60 * 1000)
    assert canvas.evaluate("element => getComputedStyle(element).filter") == before
    wrist_screen(page, "face")
    # 长期无操作后的第一次确认键直接打开菜单，无需先唤醒。
    wrist_key(page, "Enter")
    wrist_screen(page, "menu", "新建演示")
    # /wrist 与 / 属于同一腕端页面；仅切换 hash 时保留当前菜单状态。
    visit(page, app_url, "/wrist")
    wrist_screen(page, "menu", "新建演示")
    visit(page, app_url)
    wrist_screen(page, "menu", "新建演示")
    page.locator('[data-nav="/workbench"]').click()
    page.locator("#session-name").wait_for()
    expect(page.locator("#session-source")).to_have_value("FreeRTOS / QEMU")
    expect(page.locator("#session-source")).not_to_be_editable()
    visit(page, app_url, "/settings")
    assert page.locator('[data-setting="screen_timeout_s"]').count() == 0
    assert not page.errors


@pytest.fixture()
def canvas_page_factory(browser_page):
    contexts = []

    def create(**options):
        context = browser_page.context.browser.new_context(
            accept_downloads=True, reduced_motion="reduce", **options
        )
        contexts.append(context)
        page = context.new_page()
        page.errors = []
        page.on("pageerror", lambda error: page.errors.append(str(error)))
        return page

    yield create
    for context in contexts:
        context.close()


def wrist_canvas_point(page, x, y):
    canvas = page.locator("#wrist-screen")
    canvas.scroll_into_view_if_needed()
    # 数据状态先更新，命中区域在下一帧绘制；等浏览器完成绘制再执行真实指针动作。
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    bounds = canvas.bounding_box()
    return bounds["x"] + x * bounds["width"] / 240, bounds["y"] + y * bounds["height"] / 280


def wrist_canvas_tap(page, x, y, touch=False):
    point = wrist_canvas_point(page, x, y)
    if touch:
        page.touchscreen.tap(*point)
    else:
        page.mouse.click(*point)


def wrist_canvas_wheel(page, delta):
    page.mouse.move(*wrist_canvas_point(page, 120, 160))
    page.mouse.wheel(0, delta)
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


def wrist_native_touch_drag(page, start, end=None, cancel=False):
    # CDP 注入浏览器原生触摸序列，触发受信任的 touch/pointer 事件；不派发 DOM 仿制触屏事件。
    x0, y0 = wrist_canvas_point(page, *start)
    x1, y1 = wrist_canvas_point(page, *(end or start))
    session = page.context.new_cdp_session(page)
    try:
        session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0}]})
        if end:
            for step in range(1, 9):
                x, y = x0 + (x1 - x0) * step / 8, y0 + (y1 - y0) * step / 8
                session.send(
                    "Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y}]}
                )
        session.send(
            "Input.dispatchTouchEvent", {"type": "touchCancel" if cancel else "touchEnd", "touchPoints": []}
        )
    finally:
        session.detach()


def test_wrist_canvas_mouse_settings_scaling_drag_and_reentry(app_url, canvas_page_factory):
    page = canvas_page_factory(viewport={"width": 1440, "height": 1000}, device_scale_factor=1.5)
    visit(page, app_url)
    assert page.evaluate("devicePixelRatio") == 1.5
    wrist_canvas_tap(page, 120, 100)
    wrist_screen(page, "menu", "新建演示")
    wrist_canvas_wheel(page, 600)
    wrist_canvas_tap(page, 120, 244)
    wrist_screen(page, "settings")
    settings = page.request.get(app_url + "/api/settings").json()
    new_hz = 5 if settings["ui_hz"] == 10 else 10
    # 在非整数 DPR 和视口重排后，画布坐标仍命中同一设置行与选择项。
    page.set_viewport_size({"width": 1280, "height": 900})
    wrist_canvas_tap(page, 120, 66)
    wrist_screen(page, "picker")
    wrist_canvas_tap(page, 120, 66 if new_hz == 5 else 122)
    wrist_screen(page, "settings", f"界面刷新：每秒 {new_hz} 次")
    wrist_canvas_wheel(page, 600)
    with page.expect_response(
        lambda r: r.url.endswith("/api/settings") and r.request.method == "PUT"
    ) as saved:
        wrist_canvas_tap(page, 120, 195)
    assert saved.value.json()["ui_hz"] == new_hz
    wrist_screen(page, "settings", "设置已保存")
    wrist_canvas_tap(page, 20, 18)
    wrist_screen(page, "menu")
    # 保存提示仍可见时，返回入口可直接使用；点提示只关闭提示，不点击穿透到菜单。
    wrist_canvas_tap(page, 120, 55)
    wrist_screen(page, "menu")
    # 列表拖动结束不执行落点项；无论滚动是否已到边界都应保持菜单。
    page.mouse.move(*wrist_canvas_point(page, 120, 244))
    page.mouse.down()
    page.mouse.move(*wrist_canvas_point(page, 120, 100), steps=8)
    page.mouse.up()
    wrist_screen(page, "menu")
    page.locator('[data-nav="/settings"]').click()
    page.locator('[data-nav="/"]').click()
    wrist_screen(page, "menu")
    wrist_canvas_wheel(page, -600)
    wrist_canvas_tap(page, 120, 64)
    wrist_screen(page, "start")
    # 单次返回只退一级；重入页面后不存在残留的重复输入监听器。
    wrist_canvas_tap(page, 20, 18)
    wrist_screen(page, "menu", "新建演示")
    wrist_key(page, "ArrowDown")
    wrist_canvas_tap(page, 120, 116)
    wrist_screen(page, "monitor")
    assert not page.errors


def test_wrist_canvas_native_touch_scroll_cancel_and_mixed_keys(app_url, canvas_page_factory):
    page = canvas_page_factory(
        viewport={"width": 360, "height": 780}, device_scale_factor=3, is_mobile=True, has_touch=True
    )
    visit(page, app_url)
    assert page.evaluate("devicePixelRatio") == 3
    wrist_canvas_tap(page, 120, 100, touch=True)
    wrist_screen(page, "menu")
    wrist_native_touch_drag(page, (120, 244), (120, 60))
    wrist_screen(page, "menu")
    wrist_canvas_tap(page, 120, 192, touch=True)
    wrist_screen(page, "system")
    wrist_canvas_tap(page, 200, 262, touch=True)
    expect(page.locator("#wrist-stage")).to_have_attribute("data-page", "1")
    wrist_key(page, "ArrowDown")
    expect(page.locator("#wrist-stage")).to_have_attribute("data-page", "2")
    wrist_canvas_tap(page, 20, 18, touch=True)
    wrist_screen(page, "menu")
    wrist_canvas_tap(page, 120, 244, touch=True)
    wrist_screen(page, "settings")
    settings = page.request.get(app_url + "/api/settings").json()
    new_hz = 5 if settings["ui_hz"] == 10 else 10
    wrist_canvas_tap(page, 120, 66, touch=True)
    wrist_screen(page, "picker")
    wrist_canvas_tap(page, 120, 66 if new_hz == 5 else 122, touch=True)
    wrist_screen(page, "settings", f"界面刷新：每秒 {new_hz} 次")
    wrist_canvas_tap(page, 20, 18, touch=True)
    wrist_screen(page, "confirm", "继续编辑")
    wrist_canvas_tap(page, 120, 213, touch=True)
    wrist_screen(page, "settings")
    wrist_canvas_tap(page, 20, 18, touch=True)
    wrist_screen(page, "confirm")
    wrist_canvas_tap(page, 120, 253, touch=True)
    wrist_screen(page, "menu")
    assert page.request.get(app_url + "/api/settings").json()["ui_hz"] == settings["ui_hz"]
    # 浏览器原生 touchCancel 不应激活按下的设置行，下一次完整点按仍正常。
    wrist_native_touch_drag(page, (120, 244), cancel=True)
    wrist_screen(page, "menu")
    wrist_canvas_tap(page, 120, 244, touch=True)
    wrist_screen(page, "settings")
    assert not page.errors


def test_wrist_canvas_mouse_qemu_creation_completion_and_replay(app_url, browser_page):
    page = browser_page
    if not page.request.get(app_url + "/api/bootstrap").json()["capabilities"]["qemu"]:
        pytest.skip("Build QEMU to run firmware canvas verification")
    page.emulate_media(reduced_motion="reduce")
    visit(page, app_url)
    wrist_canvas_tap(page, 120, 100)
    wrist_canvas_tap(page, 120, 64)
    wrist_screen(page, "start")
    wrist_canvas_tap(page, 120, 66)
    wrist_screen(page, "picker")
    wrist_canvas_tap(page, 120, 122)
    wrist_screen(page, "start")
    wrist_canvas_wheel(page, 600)
    wrist_canvas_tap(page, 120, 192)
    wrist_screen(page, "picker")
    wrist_canvas_tap(page, 120, 214)
    wrist_screen(page, "start", "演示速度：10 倍")
    with page.expect_response(
        lambda r: r.url.endswith("/api/sessions") and r.request.method == "POST"
    ) as created:
        wrist_canvas_tap(page, 120, 244)
    sid = created.value.json()["id"]
    assert created.value.request.post_data_json["execution_mode"] == "FREERTOS_QEMU"
    wrist_screen(page, "monitor")
    wrist_canvas_tap(page, 200, 262)
    expect(page.locator("#wrist-stage")).to_have_attribute("data-page", "1")
    expect(page.locator("#stage-session")).to_have_text("最近演示：已完成", timeout=90000)
    completed = page.request.get(app_url + f"/api/sessions/{sid}").json()
    assert completed["execution_mode"] == "FREERTOS_QEMU"
    assert completed["engine_time_us"] == 134_000_000
    wrist_canvas_tap(page, 120, 262)
    wrist_screen(page, "actions")
    wrist_canvas_tap(page, 120, 168)
    wrist_screen(page, "record")
    expect(page.locator("#wrist-stage")).to_have_attribute("data-focus", "窗口结果")
    with page.expect_response(
        lambda r: r.url.endswith("/api/replays") and r.request.method == "POST"
    ) as opened:
        wrist_canvas_tap(page, 120, 163)
    rid = opened.value.json()["id"]
    assert opened.value.json()["source_session_id"] == sid
    wrist_screen(page, "replay")
    with page.expect_response(
        lambda r: r.url.endswith(f"/api/replays/{rid}") and r.request.method == "GET"
    ) as stepped:
        wrist_canvas_tap(page, 162, 256)
    assert stepped.value.json()["cursor_us"] == 1_000_000
    with page.expect_response(
        lambda r: r.url.endswith(f"/api/replays/{rid}") and r.request.method == "GET"
    ) as sought:
        wrist_canvas_tap(page, 120, 103)
    assert 66_000_000 <= sought.value.json()["cursor_us"] <= 68_000_000
    with page.expect_response(
        lambda r: r.url.endswith(f"/api/replays/{rid}") and r.request.method == "GET"
    ) as restarted:
        wrist_canvas_tap(page, 36, 256)
    assert restarted.value.json()["cursor_us"] == 0
    with page.expect_response(
        lambda r: r.url.endswith(f"/api/replays/{rid}/commands") and r.request.method == "POST"
    ) as closed:
        wrist_canvas_tap(page, 20, 18)
    assert closed.value.ok
    assert page.request.get(app_url + f"/api/replays/{rid}").status == 404
    assert (
        page.request.get(app_url + f"/api/sessions/{sid}").json()["key_windows"] == completed["key_windows"]
    )
    assert not page.errors
