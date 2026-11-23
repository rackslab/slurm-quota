"""Helpers to drive the slurm-quota web dashboard of the vortex development
cluster with Playwright, and save screenshots as evidence of functional tests.

The dashboard is reached through an SSH tunnel to the admin node, see
devs/testbed/vortex.adoc.

Environment:
  VTEST_WEB_URL      Base URL of the dashboard
                     (default: https://127.0.0.1:8443/quota/).
  VTEST_SCREENSHOTS  Directory of screenshots (required).
  VTEST_PASSWORD     Password of the cluster users (required to sign in).
"""

import contextlib
import os
import pathlib
import random
import string
import typing as t

from PIL import Image
from playwright.sync_api import Browser, Page, sync_playwright

BASE_URL = os.environ.get("VTEST_WEB_URL", "https://127.0.0.1:8443/quota/")
CHROMIUM = os.environ.get("VTEST_CHROMIUM", "/usr/bin/chromium")


def url(path: str = "") -> str:
    return BASE_URL + path.lstrip("/")


@contextlib.contextmanager
def browser() -> t.Iterator[Browser]:
    with sync_playwright() as playwright:
        _browser = playwright.chromium.launch(executable_path=CHROMIUM, headless=True)
        try:
            yield _browser
        finally:
            _browser.close()


def new_page(_browser: Browser) -> Page:
    """Return a page in a new browser context, ie. without session."""
    context = _browser.new_context(
        ignore_https_errors=True, viewport={"width": 1600, "height": 1000}
    )
    page = context.new_page()
    page.set_default_timeout(15000)
    return page


def login(page: Page, username: str, password: t.Optional[str] = None) -> None:
    """Sign in the dashboard. The password defaults to VTEST_PASSWORD."""
    if password is None:
        password = os.environ["VTEST_PASSWORD"]
    page.goto(url())
    page.fill("input[name=username]", username)
    page.fill("input[name=password]", password)
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")


def pixelate(
    path: pathlib.Path, box: tuple[int, int, int, int], square: int = 8
) -> None:
    """Replace a region (left, top, right, bottom) of an image with blurring
    squares of the given size."""
    image = Image.open(path)
    region = image.crop(box)
    width, height = region.size
    small = region.resize(
        (max(1, width // square), max(1, height // square)), Image.Resampling.BILINEAR
    )
    image.paste(small.resize(region.size, Image.Resampling.NEAREST), box)
    image.save(path)


def shot(
    page: Page,
    test_id: str,
    step: str,
    full_page: bool = True,
    hide: t.Sequence[str] = (),
) -> pathlib.Path:
    """Save a screenshot of the page as <test-id>-<step>.png.

    The input fields matching the selectors in hide, typically tokens, are
    covered with blurring squares. Their values are first replaced with random
    characters of the same length, so that secrets never appear in the image."""
    directory = pathlib.Path(os.environ["VTEST_SCREENSHOTS"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{test_id}-{step}.png"
    boxes: list[tuple[int, int, int, int]] = []
    for selector in hide:
        element = page.locator(selector)
        length = len(element.input_value())
        fake = "".join(random.choices(string.ascii_letters + string.digits, k=length))
        element.evaluate("(e, value) => { e.value = value; }", fake)
        bbox = element.bounding_box()
        if bbox is None:
            continue
        if full_page:
            # Bounding box is relative to the viewport, full page screenshots
            # start at the top of the document.
            scroll = page.evaluate("[window.scrollX, window.scrollY]")
            bbox["x"] += scroll[0]
            bbox["y"] += scroll[1]
        # Keep the field borders visible, blur its content only.
        boxes.append(
            (
                int(bbox["x"]) + 2,
                int(bbox["y"]) + 2,
                int(bbox["x"] + bbox["width"]) - 2,
                int(bbox["y"] + bbox["height"]) - 2,
            )
        )
    page.screenshot(path=str(path), full_page=full_page)
    for box in boxes:
        pixelate(path, box)
    print(f"screenshot: {path}")
    return path
