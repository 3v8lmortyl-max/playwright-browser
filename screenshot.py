import re
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

targets_file = Path("capture_targets.txt")
if not targets_file.exists():
    raise SystemExit("capture_targets.txt is missing.")

targets = [
    line.strip() for line in targets_file.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.lstrip().startswith("#")
]
if not targets:
    raise SystemExit("No URLs found in capture_targets.txt.")

output_dir = Path("screenshots")
output_dir.mkdir(exist_ok=True)

def safe_host(url):
    host = urlparse(url).netloc or "page"
    return re.sub(r"[^a-zA-Z0-9.-]+", "_", host)

with sync_playwright() as p:
    browser = p.chromium.launch()
    for index, raw_url in enumerate(targets, start=1):
        url = raw_url if urlparse(raw_url).scheme else "https://" + raw_url
        if urlparse(url).scheme not in {"http", "https"}:
            print(f"SKIP unsupported URL: {url}")
            continue

        context = browser.new_context(
            viewport={"width": 432, "height": 768},
            screen={"width": 432, "height": 768},
            device_scale_factor=2.5,
            is_mobile=True,
            has_touch=True,
        )
        page = context.new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2200)
            # Capture successive mobile screenfuls; each output is 1080x1920.
            height = page.evaluate("Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)")
            positions = list(range(0, max(1, height), 650))[:10]
            for part, y in enumerate(positions, start=1):
                page.evaluate("(y) => window.scrollTo(0, y)", y)
                page.wait_for_timeout(350)
                output_file = output_dir / f"{index:02d}_{safe_host(page.url)}_{part:02d}.png"
                page.screenshot(path=str(output_file), full_page=False, animations="disabled")
                print(f"CAPTURED | {output_file} | scroll={y}")
            print(f"SOURCE | {response.status if response else 'no response'} | {page.title()} | {page.url}")
        except Exception as exc:
            print(f"FAILED | {url} | {type(exc).__name__}: {exc}")
        finally:
            context.close()
    browser.close()
