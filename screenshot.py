import os
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

url = os.environ.get("TARGET_URL", "https://example.com").strip()
if not urlparse(url).scheme:
    url = "https://" + url

if urlparse(url).scheme not in {"http", "https"}:
    raise SystemExit("Only http:// and https:// URLs are supported.")

full_page = os.environ.get("FULL_PAGE", "false").lower() == "true"
output_dir = Path("screenshots")
output_dir.mkdir(exist_ok=True)
output_file = output_dir / "page.png"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(
        viewport={"width": 1440, "height": 1000},
        device_scale_factor=1,
    )
    response = page.goto(url, wait_until="networkidle", timeout=60000)
    page.screenshot(path=str(output_file), full_page=full_page)
    print(f"URL: {page.url}")
    print(f"HTTP status: {response.status if response else 'no response'}")
    print(f"Title: {page.title()}")
    print(f"Screenshot: {output_file}")
    browser.close()
