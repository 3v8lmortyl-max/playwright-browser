import re
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

targets_file = Path("capture_targets.txt")
if not targets_file.exists():
    raise SystemExit("capture_targets.txt is missing.")

targets = [
    line.strip()
    for line in targets_file.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.lstrip().startswith("#")
]
if not targets:
    raise SystemExit("No URLs found in capture_targets.txt.")

output_dir = Path("screenshots")
output_dir.mkdir(exist_ok=True)

def safe_name(url, index):
    host = urlparse(url).netloc or "page"
    host = re.sub(r"[^a-zA-Z0-9.-]+", "_", host)
    return f"{index:02d}_{host}.png"

with sync_playwright() as p:
    browser = p.chromium.launch()
    for index, raw_url in enumerate(targets, start=1):
        url = raw_url if urlparse(raw_url).scheme else "https://" + raw_url
        if urlparse(url).scheme not in {"http", "https"}:
            print(f"SKIP unsupported URL: {url}")
            continue

        page = browser.new_page(
            viewport={"width": 1440, "height": 1000},
            device_scale_factor=1,
        )
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)
            output_file = output_dir / safe_name(page.url, index)
            page.screenshot(path=str(output_file), full_page=False, animations="disabled")
            status = response.status if response else "no response"
            print(f"CAPTURED | {status} | {page.title()} | {page.url} | {output_file}")
        except Exception as exc:
            print(f"FAILED | {url} | {type(exc).__name__}: {exc}")
        finally:
            page.close()
    browser.close()
