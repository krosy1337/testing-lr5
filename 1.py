from contextlib import suppress
import os
from pathlib import Path
import re
import socket
import subprocess
import time
from urllib.request import urlopen

from playwright.sync_api import TimeoutError, sync_playwright


QUERY = os.getenv("VIDEO_QUERY", "Never Gonna Give You Up")
PROFILE = Path("chrome-cdp-profile").resolve()
DEFAULT_TIMEOUT_MS = 4_000
NAVIGATION_TIMEOUT_MS = 8_000
SHORT_TIMEOUT_MS = 1_000
POLL_INTERVAL_S = 0.1
RETRY_PAUSE_S = 0.25


def wait_until(fn, timeout_ms=SHORT_TIMEOUT_MS, interval_s=POLL_INTERVAL_S):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        with suppress(Exception):
            if fn():
                return True
        time.sleep(interval_s)
    return False


def launch():
    for env in "ProgramFiles", "ProgramFiles(x86)", "LocalAppData":
        if (chrome := Path(os.getenv(env, ""), "Google/Chrome/Application/chrome.exe")).exists():
            break
    else:
        return

    PROFILE.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [
            str(chrome),
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
            "--disable-extensions",
            "--disable-component-extensions-with-background-pages",
            "--disable-background-networking",
            "--disable-sync",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(20):
        with suppress(Exception):
            with urlopen(f"{url}/json/version", timeout=0.3) as r:
                if r.status == 200:
                    return proc, url
        time.sleep(POLL_INTERVAL_S)
    proc.terminate()


def click(x, timeout=1_500):
    with suppress(TimeoutError):
        x.click(timeout=timeout)
        return True


def crashed(page):
    with suppress(TimeoutError):
        page.locator("text=/Aw, Snap|STATUS_BREAKPOINT/i").first.wait_for(state="visible", timeout=500)
        return True
    return False


def button(page):
    for x in (
        page.locator("button[aria-label^='Like this video' i]").first,
        page.get_by_role("button", name=re.compile(r"^Like this video", re.I)).first,
        page.locator("like-button-view-model button,[aria-label^='Like' i][aria-pressed]").first,
        page.locator("#segmented-like-button button,#segmented-like-button [aria-pressed]").first,
    ):
        with suppress(TimeoutError):
            x.wait_for(state="visible", timeout=SHORT_TIMEOUT_MS)
            return x


def liked(page):
    if not (x := button(page)):
        return False
    if (state := x.get_attribute("aria-pressed")) is not None:
        return state == "true"
    with suppress(TimeoutError):
        x = x.locator("[aria-pressed]").first
        x.wait_for(state="attached", timeout=500)
        return x.get_attribute("aria-pressed") == "true"
    return False


def like(page):
    if x := button(page):
        x.scroll_into_view_if_needed()
        click(x, SHORT_TIMEOUT_MS)


def set_like(page, value):
    for _ in range(3):
        if liked(page) == value:
            return True
        like(page)
        if wait_until(lambda: liked(page) == value, timeout_ms=1_500):
            return True
        time.sleep(RETRY_PAUSE_S)
    return False


def reload_video(page):
    for _ in range(3):
        with suppress(Exception):
            page.reload(wait_until="domcontentloaded")
            if not crashed(page):
                page.locator("video").first.wait_for(state="attached", timeout=DEFAULT_TIMEOUT_MS)
                return True
        time.sleep(RETRY_PAUSE_S)
    return False


def open_video(page):
    for _ in range(3):
        with suppress(Exception):
            page.goto("https://www.youtube.com", wait_until="domcontentloaded")
            if crashed(page):
                time.sleep(RETRY_PAUSE_S)
                continue
            click(page.get_by_role("button", name=re.compile("Accept all|I agree|Not now|Accept the use of cookies", re.I)))
            x = page.locator("input[name='search_query']")
            x.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
            x.fill(QUERY)
            click(page.locator("button[aria-label='Search'], #search-icon-legacy").first)
            x = page.locator("ytd-video-renderer a#video-title").first
            x.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
            x.click()
            page.wait_for_url(re.compile(r".*/watch\?v=.*"), timeout=NAVIGATION_TIMEOUT_MS)
            if not crashed(page):
                page.locator("video").first.wait_for(state="attached", timeout=DEFAULT_TIMEOUT_MS)
                return True
        time.sleep(RETRY_PAUSE_S)
    return False


def ensure_like(page):
    if liked(page):
        if not set_like(page, False) or not reload_video(page) or liked(page):
            return False

    if set_like(page, True):
        return reload_video(page) and liked(page)

    input("Log into YouTube, then press Enter...")
    if not reload_video(page) or not set_like(page, True) or not reload_video(page):
        return False
    return liked(page)


def main():
    if not (data := launch()):
        return
    proc, url = data
    browser = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            page.set_default_timeout(DEFAULT_TIMEOUT_MS)
            page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
            page.bring_to_front()
            if open_video(page):
                ensure_like(page)
        except Exception:
            pass
        finally:
            if browser:
                browser.close()
            if proc.poll() is None:
                proc.terminate()
                with suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=10)
                if proc.poll() is None:
                    proc.kill()


if __name__ == "__main__":
    main()
