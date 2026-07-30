"""
crawler.py
==========
Playwright-based site archiver.

For every URL in config.json this script:
  1. Loads the page in headless Chromium.
  2. Captures every network response (css/js/img/fonts/other) as it happens,
     including resources that only load after scrolling.
  3. Auto-scrolls the page to trigger lazy-loaded content.
  4. Takes the final rendered HTML and rewrites every resource reference to
     point at the locally saved copy.
  5. Rewrites url()/@import references inside every saved CSS file too.
  6. Saves everything under full-archive/<site>/<timestamp>/ using the
     conventions defined in archive_utils.py.

Run:
    python crawler/crawler.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Response, BrowserContext

# archive_utils.py lives at the project root, one level up from this file.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import archive_utils as au

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

# Playwright's own resource_type labels we consider worth archiving.
# ("document" is excluded -- the main page HTML is captured separately via
# page.content() *after* scrolling, so it reflects the fully rendered DOM.)
CAPTURABLE_RESOURCE_TYPES = {
    "stylesheet", "script", "image", "font", "media", "other",
    "xhr", "fetch",
}

# Attribute names that can hold a resource URL, per HTML tag.
URL_ATTRS_BY_TAG = {
    "img": ["src"],
    "script": ["src"],
    "link": ["href"],
    "source": ["src"],
    "video": ["src", "poster"],
    "audio": ["src"],
    "embed": ["src"],
    "input": ["src"],  # <input type="image">
    "iframe": ["src"],
    "object": ["data"],
}

CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
CSS_IMPORT_RE = re.compile(r'@import\s+(?:url\()?["\']?([^"\')\s]+)["\']?\)?', re.IGNORECASE)
SRCSET_SPLIT_RE = re.compile(r"\s*,\s*")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Scrolling (triggers lazy-loaded content)
# ---------------------------------------------------------------------------

def auto_scroll(page: Page, max_steps: int, step_px: int, step_delay_ms: int) -> None:
    """
    Scroll down in fixed increments, pausing between each so lazy-loaded
    images/content have time to fire their network requests (which the
    response listener will catch). Stops early if we've hit the bottom.
    """
    previous_height = -1
    for _ in range(max_steps):
        page.evaluate(f"window.scrollBy(0, {step_px})")
        page.wait_for_timeout(step_delay_ms)
        current_height = page.evaluate("document.body.scrollHeight")
        if current_height == previous_height:
            break
        previous_height = current_height
    # Scroll back to top so the archived page opens the same way the
    # original did.
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(200)


# ---------------------------------------------------------------------------
# Resource capture
# ---------------------------------------------------------------------------

def make_response_handler(snapshot_dir: Path, resource_map: dict, seen: set):
    """
    Returns a function suitable for page.on("response", ...).
    Downloads the response body, classifies it, saves it under
    resources/<category>/, and records url -> local relative path in
    resource_map. Skips anything already captured or not worth archiving.
    """

    def handle_response(response: Response) -> None:
        try:
            request = response.request
            url = response.url

            if url in seen:
                return
            if request.resource_type not in CAPTURABLE_RESOURCE_TYPES:
                return
            if response.status >= 400:
                return
            if url.startswith("data:") or url.startswith("blob:"):
                return

            content_type = response.headers.get("content-type", "")
            body = response.body()  # bytes
            if not body:
                return

            category = au.classify_resource(url, content_type)
            filename = au.local_filename_for(url, content_type)
            local_path = snapshot_dir / "resources" / category / filename
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(body)

            relative_path = f"resources/{category}/{filename}"
            resource_map[url] = relative_path
            seen.add(url)

        except Exception as exc:
            # Individual resource failures (aborted requests, redirects that
            # never resolve, etc.) should never crash the whole crawl.
            print(f"    [resource skipped] {response.url} ({exc})")

    return handle_response


# ---------------------------------------------------------------------------
# HTML rewriting
# ---------------------------------------------------------------------------

def _resolve_and_map(raw_url: str, base_url: str, resource_map: dict) -> str | None:
    """Resolve a possibly-relative URL against base_url and look it up in the map."""
    if not raw_url or raw_url.startswith("data:") or raw_url.startswith("#"):
        return None
    absolute = urljoin(base_url, raw_url)
    return resource_map.get(absolute)


def _rewrite_srcset(srcset_value: str, base_url: str, resource_map: dict) -> str:
    """srcset="url1 1x, url2 2x" -- rewrite each URL, keep the descriptors."""
    parts = SRCSET_SPLIT_RE.split(srcset_value.strip())
    rewritten = []
    for part in parts:
        bits = part.strip().split(" ", 1)
        url_part = bits[0]
        descriptor = f" {bits[1]}" if len(bits) > 1 else ""
        local = _resolve_and_map(url_part, base_url, resource_map)
        rewritten.append(f"{local or url_part}{descriptor}")
    return ", ".join(rewritten)


def rewrite_css_text(css_text: str, base_url: str, resource_map: dict) -> str:
    """Rewrite url(...) and @import references inside a block of CSS text."""

    def replace_url(match: re.Match) -> str:
        quote, raw_url = match.group(1), match.group(2)
        local = _resolve_and_map(raw_url, base_url, resource_map)
        return f"url({quote}{local or raw_url}{quote})"

    def replace_import(match: re.Match) -> str:
        raw_url = match.group(1)
        local = _resolve_and_map(raw_url, base_url, resource_map)
        return f'@import "{local or raw_url}"'

    css_text = CSS_URL_RE.sub(replace_url, css_text)
    css_text = CSS_IMPORT_RE.sub(replace_import, css_text)
    return css_text


def rewrite_html(html: str, base_url: str, resource_map: dict) -> str:
    """Rewrite every resource reference in the rendered HTML to a local path."""
    soup = BeautifulSoup(html, "html.parser")

    # 1. Standard src/href attributes.
    for tag_name, attrs in URL_ATTRS_BY_TAG.items():
        for tag in soup.find_all(tag_name):
            for attr in attrs:
                if tag.has_attr(attr):
                    local = _resolve_and_map(tag[attr], base_url, resource_map)
                    if local:
                        tag[attr] = local

    # 2. srcset (img, source).
    for tag in soup.find_all(["img", "source"]):
        if tag.has_attr("srcset"):
            tag["srcset"] = _rewrite_srcset(tag["srcset"], base_url, resource_map)

    # 3. Inline style="...url(...)..." attributes.
    for tag in soup.find_all(style=True):
        tag["style"] = rewrite_css_text(tag["style"], base_url, resource_map)

    # 4. <style>...</style> blocks.
    for style_tag in soup.find_all("style"):
        if style_tag.string:
            style_tag.string.replace_with(
                rewrite_css_text(style_tag.string, base_url, resource_map)
            )

    return str(soup)


def post_process_css_files(snapshot_dir: Path, resource_map: dict) -> None:
    """Rewrite url()/@import references inside every saved CSS file."""
    css_dir = snapshot_dir / "resources" / "css"
    if not css_dir.exists():
        return
    for css_file in css_dir.glob("*.css"):
        original_url = next(
            (u for u, p in resource_map.items() if p.endswith(css_file.name)), None
        )
        base_url = original_url or ""
        text = css_file.read_text(encoding="utf-8", errors="ignore")
        rewritten = rewrite_css_text(text, base_url, resource_map)
        css_file.write_text(rewritten, encoding="utf-8")


# ---------------------------------------------------------------------------
# Per-URL crawl
# ---------------------------------------------------------------------------

def crawl_url(context: BrowserContext, url: str, scroll_cfg: dict, timeout_ms: int) -> bool:
    """Archive a single URL. Returns True on success, False on failure."""
    site_name = au.sanitize_site_name(url)
    capture_date = au.make_capture_date()
    timestamp = au.make_capture_timestamp()
    snapshot_dir = au.get_snapshot_dir(site_name, capture_date, create=True)

    page = context.new_page()
    resource_map: dict[str, str] = {}
    seen: set[str] = set()
    handler = make_response_handler(snapshot_dir, resource_map, seen)
    page.on("response", handler)

    try:
        print(f"  -> navigating")
        page.goto(url, timeout=timeout_ms, wait_until="load")
        page.wait_for_load_state("networkidle", timeout=timeout_ms)

        print(f"  -> scrolling to trigger lazy-loaded content")
        auto_scroll(
            page,
            max_steps=scroll_cfg.get("max_steps", 12),
            step_px=scroll_cfg.get("step_px", 1200),
            step_delay_ms=scroll_cfg.get("step_delay_ms", 350),
        )
        page.wait_for_timeout(500)

        title = page.title()
        html = page.content()

        print(f"  -> rewriting HTML references ({len(resource_map)} resources captured)")
        rewritten_html = rewrite_html(html, url, resource_map)
        post_process_css_files(snapshot_dir, resource_map)

        (snapshot_dir / "index.html").write_text(rewritten_html, encoding="utf-8")
        au.write_json(snapshot_dir / "resource_map.json", resource_map)
        au.write_json(
            snapshot_dir / "meta.json",
            au.build_meta(url, title, capture_date, timestamp, len(resource_map)),
        )

        print(f"  -> saved snapshot: {snapshot_dir}")
        return True

    except Exception as exc:
        print(f"  !! FAILED to archive {url}: {exc}")
        return False

    finally:
        page.remove_listener("response", handler)
        page.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    config = load_config()
    urls = config.get("urls", [])
    scroll_cfg = config.get("scroll", {})
    timeout_ms = config.get("timeout_ms", 45000)

    if not urls:
        print("No URLs configured in config.json -- nothing to do.")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/129.0 Safari/537.36 LocalArchiver/1.0"
            ),
        )

        results = []
        for url in urls:
            print(f"\nArchiving: {url}")
            start = time.time()
            success = crawl_url(context, url, scroll_cfg, timeout_ms)
            elapsed = time.time() - start
            results.append((url, success, elapsed))

        browser.close()

    print("\n--- Crawl summary ---")
    for url, success, elapsed in results:
        status = "OK" if success else "FAILED"
        print(f"  [{status}] {url} ({elapsed:.1f}s)")


if __name__ == "__main__":
    main()