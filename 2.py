import os
import re
from pathlib import Path

from playwright.sync_api import TimeoutError, sync_playwright


YOUTUBE_URL = "https://www.youtube.com/"
SEARCH_QUERY = os.getenv("VIDEO_QUERY", "видик")
ARTIFACTS_DIR = Path("artifacts")
SCREENSHOT_PATH = ARTIFACTS_DIR / "scenario-2-final.png"
HTML_PATH = ARTIFACTS_DIR / "scenario-2-final.html"


def click_if_visible(locator, timeout=3_000):
    try:
        locator.click(timeout=timeout)
        return True
    except TimeoutError:
        return False


def dismiss_youtube_dialogs(page):
    patterns = [
        re.compile(r"Accept all|I agree", re.I),
        re.compile(r"Reject all|No thanks|Not now", re.I),
        re.compile(r"Accept the use of cookies", re.I),
    ]
    for pattern in patterns:
        click_if_visible(page.get_by_role("button", name=pattern).first, timeout=1_500)


def open_youtube(page):
    page.goto(YOUTUBE_URL, wait_until="domcontentloaded")
    dismiss_youtube_dialogs(page)


def first_video_locator(page):
    candidates = (
        page.locator("ytd-video-renderer a#video-title").first,
        page.locator("a[href*='/watch?v=']").first,
    )
    for candidate in candidates:
        try:
            candidate.wait_for(state="visible", timeout=15_000)
            return candidate
        except TimeoutError:
            continue
    raise TimeoutError("No visible video result was found")


def submit_search(page, search_input):
    search_button = page.locator("button[aria-label='Search'], #search-icon-legacy").first

    try:
        search_input.press("Enter")
        page.wait_for_url(
            re.compile(r"https://www\.youtube\.com/results\?search_query="),
            wait_until="domcontentloaded",
            timeout=15_000,
        )
    except TimeoutError:
        if not click_if_visible(search_button, timeout=5_000):
            raise
        page.wait_for_url(
            re.compile(r"https://www\.youtube\.com/results\?search_query="),
            wait_until="domcontentloaded",
            timeout=15_000,
        )


def open_first_video(page):
    search_input = page.locator("input[name='search_query']").first
    search_input.wait_for(state="visible", timeout=30_000)
    search_input.click()
    search_input.fill(SEARCH_QUERY)
    submit_search(page, search_input)

    first_video = first_video_locator(page)
    first_video.click()

    page.wait_for_url(re.compile(r"https://www\.youtube\.com/watch\?"), timeout=30_000)
    page.locator("#movie_player, video").first.wait_for(state="attached", timeout=30_000)
    wait_for_video_ready(page)


def wait_for_video_ready(page):
    dismiss_youtube_dialogs(page)
    page.locator("#movie_player, video").first.wait_for(state="attached", timeout=60_000)
    click_if_visible(page.locator("button.ytp-large-play-button").first, timeout=3_000)
    page.wait_for_timeout(10_000)


def save_artifacts(page):
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_PATH), full_page=True)
    HTML_PATH.write_text(page.content(), encoding="utf-8")


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(locale="en-US", viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.set_default_timeout(30_000)

        try:
            open_youtube(page)
            open_first_video(page)
            save_artifacts(page)
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
