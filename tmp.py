from pathlib import Path
import os
import re
import socket
import subprocess
import time
from urllib.request import urlopen

from playwright.sync_api import Locator, Page, TimeoutError, sync_playwright


SEARCH_QUERY = os.getenv("VIDEO_QUERY", "Never Gonna Give You Up")
PROFILE_DIR = Path("chrome-cdp-profile").resolve()


def chrome_executable() -> str:
    candidates = [
        Path(os.getenv("ProgramFiles", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.getenv("ProgramFiles(x86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.getenv("LocalAppData", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise RuntimeError("Chrome was not found.")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def launch_chrome() -> tuple[subprocess.Popen, str]:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    port = free_port()
    cdp_url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            chrome_executable(),
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urlopen(f"{cdp_url}/json/version", timeout=1.5) as response:
                if response.status == 200:
                    return process, cdp_url
        except Exception:
            time.sleep(0.25)

    process.terminate()
    raise RuntimeError("Chrome did not start DevTools in time.")


def click_if_visible(locator: Locator) -> None:
    try:
        locator.click(timeout=2_000)
    except TimeoutError:
        pass


def close_dialogs(page: Page) -> None:
    click_if_visible(page.get_by_role("button", name=re.compile("Accept all|I agree|Not now", re.I)))
    click_if_visible(page.get_by_role("button", name=re.compile("Accept the use of cookies", re.I)))


def open_first_video(page: Page) -> None:
    print(f"Searching YouTube for: {SEARCH_QUERY}")
    page.goto("https://www.youtube.com", wait_until="domcontentloaded")
    close_dialogs(page)

    search = page.locator("input[name='search_query']")
    search.wait_for(state="visible", timeout=20_000)
    search.fill(SEARCH_QUERY)
    search.press("Enter")

    first_video = page.locator("ytd-video-renderer a#video-title").first
    first_video.wait_for(state="visible", timeout=20_000)
    first_video.click()

    page.wait_for_url(re.compile(r".*/watch\?v=.*"), timeout=20_000)
    page.locator("video").first.wait_for(state="attached", timeout=20_000)


def like_button(page: Page) -> Locator:
    candidates = [
        page.locator("#segmented-like-button button").first,
        page.locator("button[aria-label^='like this video' i]").first,
        page.get_by_role("button", name=re.compile(r"^like this video", re.I)).first,
    ]
    for candidate in candidates:
        try:
            candidate.wait_for(state="visible", timeout=8_000)
            return candidate
        except TimeoutError:
            pass
    raise RuntimeError("Could not find the like button.")


def like_state(button: Locator) -> str | None:
    pressed = button.get_attribute("aria-pressed")
    if pressed is not None:
        return pressed

    nested = button.locator("[aria-pressed]").first
    try:
        nested.wait_for(state="attached", timeout=1_000)
        return nested.get_attribute("aria-pressed")
    except TimeoutError:
        return None


def is_liked(page: Page) -> bool:
    return like_state(like_button(page)) == "true"


def click_like(page: Page) -> None:
    button = like_button(page)
    button.scroll_into_view_if_needed()
    button.click()
    page.wait_for_timeout(1_500)


def ensure_like(page: Page) -> None:
    if is_liked(page):
        click_like(page)
        page.reload(wait_until="domcontentloaded")
        if is_liked(page):
            raise RuntimeError("The like stayed active after click.")

    click_like(page)
    if is_liked(page):
        return

    input("Log into YouTube in the opened Chrome window, then press Enter...")
    page.reload(wait_until="domcontentloaded")
    page.locator("video").first.wait_for(state="attached", timeout=20_000)
    click_like(page)

    if not is_liked(page):
        raise RuntimeError("Could not set the like.")


def main() -> None:
    chrome_process, cdp_url = launch_chrome()
    print(f"Using profile: {PROFILE_DIR}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        page.bring_to_front()

        try:
            open_first_video(page)
            ensure_like(page)
            page.reload(wait_until="domcontentloaded")
            if not is_liked(page):
                raise RuntimeError("The like did not persist after reload.")
            print("Done.")
        finally:
            browser.close()
            if chrome_process.poll() is None:
                chrome_process.terminate()
                try:
                    chrome_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    chrome_process.kill()


if __name__ == "__main__":
    main()
