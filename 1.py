from pathlib import Path
import os
import re
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import Locator, Page, TimeoutError, sync_playwright


BASE_URL = "https://www.youtube.com"
SEARCH_QUERY = os.getenv("VIDEO_QUERY", "Never Gonna Give You Up")
ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", "artifacts")).resolve()
SLOW_MO_MS = int(os.getenv("SLOW_MO_MS", "250"))
CDP_URL = os.getenv("CDP_URL")


def normalize_env_path(value: str) -> str:
    return value.strip().strip("\"'")


def resolve_profile_dir() -> Path:
    configured_profile_dir = os.getenv("YOUTUBE_PROFILE_DIR")
    if configured_profile_dir:
        return Path(normalize_env_path(configured_profile_dir)).resolve()

    chrome_cdp_profile = Path("chrome-cdp-profile")
    if chrome_cdp_profile.exists():
        return chrome_cdp_profile.resolve()

    return Path("youtube-profile").resolve()


def resolve_chrome_executable() -> str | None:
    configured_executable = os.getenv("CHROME_EXECUTABLE")
    if configured_executable:
        return normalize_env_path(configured_executable)

    if os.name == "nt":
        candidates = [
            Path(os.getenv("ProgramFiles", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.getenv("ProgramFiles(x86)", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.getenv("LocalAppData", "")) / "Google/Chrome/Application/chrome.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)

    return None


PROFILE_DIR = resolve_profile_dir()
CHROME_EXECUTABLE = resolve_chrome_executable()


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_cdp_endpoint(cdp_url: str, timeout_seconds: float = 15.0) -> None:
    deadline = time.time() + timeout_seconds
    endpoint = f"{cdp_url}/json/version"

    while time.time() < deadline:
        try:
            with urlopen(endpoint, timeout=1.5) as response:
                if response.status == 200:
                    return
        except URLError:
            time.sleep(0.25)

    raise RuntimeError(f"Chrome did not expose DevTools at {endpoint} in time.")


def launch_chrome_for_cdp(profile_dir: Path, executable_path: str) -> tuple[subprocess.Popen, str]:
    port = find_free_port()
    cdp_url = f"http://127.0.0.1:{port}"
    args = [
        executable_path,
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
    ]

    process = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        wait_for_cdp_endpoint(cdp_url)
        return process, cdp_url
    except Exception:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        raise


def require_display_for_headed_browser() -> None:
    if sys.platform.startswith("linux") and not os.getenv("DISPLAY"):
        raise RuntimeError(
            "This script launches a headed browser, but DISPLAY is not set. "
            "Run it in a graphical session, use X forwarding/VNC, or try "
            "`xvfb-run -a python3 1.py`."
        )


def accept_optional_dialogs(page: Page) -> None:
    buttons = [
        page.get_by_role("button", name=re.compile("Accept all", re.I)),
        page.get_by_role("button", name=re.compile("I agree", re.I)),
        page.get_by_role("button", name=re.compile("Not now", re.I)),
        page.get_by_role("button", name=re.compile("Принять все", re.I)),
        page.get_by_role("button", name=re.compile("Согласен|Принимаю", re.I)),
        page.get_by_role("button", name=re.compile("Не сейчас", re.I)),
    ]

    for button in buttons:
        try:
            button.click(timeout=2_000)
        except TimeoutError:
            pass


def find_like_button(page: Page) -> Locator:
    candidates = [
        page.get_by_role("button", name=re.compile("like this video|нравится", re.I)),
        page.locator(
            "ytd-segmented-like-dislike-button-renderer "
            "#segmented-like-button button[aria-pressed]"
        ),
        page.locator(
            "like-button-view-model "
            "toggle-button-view-model button[aria-pressed]"
        ),
        page.locator(
            "ytd-segmented-like-dislike-button-renderer "
            "#segmented-like-button [aria-label*='like this video' i]"
        ),
        page.locator("like-button-view-model [aria-label*='like this video' i]"),
        page.locator("[class*='ytSpecButtonShapeNextHost'][aria-label*='like this video' i]"),
        page.locator("button[aria-label*='like this video' i][aria-pressed]"),
        page.locator("[aria-label*='like this video' i]"),
        page.locator("button[aria-label*='нравится' i][aria-pressed]"),
        page.locator("[aria-label*='нравится' i]"),
    ]

    for candidate in candidates:
        try:
            candidate.first.wait_for(state="visible", timeout=8_000)
            return candidate.first
        except TimeoutError:
            continue

    raise RuntimeError("Could not find the like button on the video page.")


def get_like_state_locator(like_button: Locator) -> Locator:
    if like_button.get_attribute("aria-pressed") is not None:
        return like_button

    nested_pressed = like_button.locator("[aria-pressed]").first
    try:
        nested_pressed.wait_for(state="attached", timeout=1_000)
        return nested_pressed
    except TimeoutError:
        return like_button


def is_liked(page: Page) -> bool:
    like_button = find_like_button(page)
    return get_like_state_locator(like_button).get_attribute("aria-pressed") == "true"


def click_like_button(page: Page) -> None:
    like_button = find_like_button(page)
    like_button.scroll_into_view_if_needed()
    like_button.click()
    page.wait_for_timeout(1_500)


def wait_for_manual_login(page: Page) -> None:
    print(
        "YouTube did not accept the like action. If the sign-in screen is open, "
        "log into the account in this browser window."
    )
    input("After login, return to the terminal and press Enter...")
    page.reload(wait_until="domcontentloaded")
    page.locator("video").first.wait_for(state="attached", timeout=20_000)


def put_like_with_login_retry(page: Page) -> None:
    click_like_button(page)

    if is_liked(page):
        return

    wait_for_manual_login(page)
    click_like_button(page)

    if not is_liked(page):
        raise RuntimeError(
            "Could not set the like. Make sure you are logged into YouTube in "
            "the browser instance opened by this script."
        )


def open_first_video_from_search(page: Page) -> None:
    print(f"Opening YouTube and searching for: {SEARCH_QUERY}")
    page.goto(BASE_URL, wait_until="domcontentloaded")
    accept_optional_dialogs(page)

    search_input = page.locator("input[name='search_query']")
    search_input.wait_for(state="visible", timeout=20_000)
    search_input.fill(SEARCH_QUERY)
    search_input.press("Enter")

    first_video = page.locator("ytd-video-renderer a#video-title").first
    first_video.wait_for(state="visible", timeout=20_000)
    print("Opening the first search result.")
    first_video.click()

    page.wait_for_url(re.compile(r".*/watch\?v=.*"), timeout=20_000)
    page.wait_for_load_state("domcontentloaded")
    page.locator("video").first.wait_for(state="attached", timeout=20_000)


def ensure_video_is_liked_after_reload(page: Page) -> None:
    if is_liked(page):
        print("The video is already liked. Removing the like, reloading, and liking again.")
        click_like_button(page)
        page.reload(wait_until="domcontentloaded")
    else:
        print("The video is not liked yet. Liking it now.")

    if is_liked(page):
        raise RuntimeError("The like remained active after it was supposed to be removed.")

    put_like_with_login_retry(page)

    page.reload(wait_until="domcontentloaded")
    if not is_liked(page):
        raise RuntimeError("The like did not persist after the page reload.")


def save_artifacts(page: Page) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=ARTIFACTS_DIR / "scenario-1-final.png", full_page=True)
    (ARTIFACTS_DIR / "scenario-1-final.html").write_text(
        page.content(),
        encoding="utf-8",
    )


def main() -> None:
    if not CDP_URL:
        require_display_for_headed_browser()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = None
        launched_chrome_process = None
        if CDP_URL:
            print(f"Connecting to existing Chrome via CDP: {CDP_URL}")
            browser = playwright.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            page.bring_to_front()
            print("Connected successfully and opened a new tab.")
        elif os.name == "nt":
            if not CHROME_EXECUTABLE:
                raise RuntimeError(
                    "Chrome executable was not found automatically. "
                    "Set the CHROME_EXECUTABLE environment variable."
                )

            print(f"Launching Chrome with profile: {PROFILE_DIR}")
            launched_chrome_process, launched_cdp_url = launch_chrome_for_cdp(
                PROFILE_DIR,
                CHROME_EXECUTABLE,
            )
            print(f"Connecting to launched Chrome via CDP: {launched_cdp_url}")
            browser = playwright.chromium.connect_over_cdp(launched_cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            page.bring_to_front()
        else:
            print("Launching Chromium with Playwright.")
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                headless=False,
                slow_mo=SLOW_MO_MS,
                viewport={"width": 1366, "height": 768},
                executable_path=CHROME_EXECUTABLE,
                args=["--start-maximized"],
            )
            page = context.pages[0] if context.pages else context.new_page()

        try:
            open_first_video_from_search(page)
            ensure_video_is_liked_after_reload(page)
            save_artifacts(page)
            print("Scenario 1 completed: the like remains active after reload.")
        finally:
            if browser and (CDP_URL or launched_chrome_process):
                browser.close()
            if launched_chrome_process:
                if launched_chrome_process.poll() is None:
                    launched_chrome_process.terminate()
                    try:
                        launched_chrome_process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        launched_chrome_process.kill()
            elif not CDP_URL:
                context.close()


if __name__ == "__main__":
    main()
