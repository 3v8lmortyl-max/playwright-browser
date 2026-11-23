"""Capture mobile source pages and write a traceable image manifest."""
from __future__ import annotations
import hashlib, json, math, re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from PIL import Image
from playwright.sync_api import sync_playwright

TARGETS_FILE = Path("capture_targets.txt")
OUTPUT_DIR = Path("screenshots")
MANIFEST = Path("capture_manifest.json")
VIEWPORT, DPR, MAX_CAPTURES_PER_PAGE = {"width": 432, "height": 768}, 2.5, 12

def safe_host(url: str) -> str:
    return re.sub(r"[^a-zA-Z0-9.-]+", "_", urlparse(url).netloc or "page")

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def dismiss_cookie_notice(page) -> str:
    # Prefer privacy-preserving choices. Never click "accept all".
    choices = [
        (re.compile(r"reject all", re.I), "reject_all"),
        (re.compile(r"accept only essential cookies|only essential cookies", re.I), "essential_only"),
        (re.compile(r"decline all|refuse all|reject optional", re.I), "decline_optional"),
    ]
    for pattern, action in choices:
        try:
            buttons = page.get_by_role("button", name=pattern)
            if buttons.count():
                buttons.first.click(timeout=2500)
                page.wait_for_timeout(700)
                return action
        except Exception:
            pass
        try:
            candidates = page.locator("button, [role=button], a").filter(has_text=pattern)
            if candidates.count():
                candidates.first.click(timeout=2500)
                page.wait_for_timeout(700)
                return action
        except Exception:
            pass
    return "no_consent_control_found"

def visible_text(page) -> str:
    script = """
    () => Array.from(document.querySelectorAll('body *'))
      .filter(el => el.children.length === 0)
      .map(el => {
        const r = el.getBoundingClientRect(), s = getComputedStyle(el);
        if (!r.width || !r.height || s.visibility === 'hidden' || s.display === 'none' ||
            r.bottom <= 0 || r.top >= innerHeight || r.right <= 0 || r.left >= innerWidth) return '';
        return (el.innerText || el.textContent || '').trim();
      }).filter(Boolean).slice(0, 100).join(' ').replace(/\\s+/g, ' ').slice(0, 2400)
    """
    try:
        return page.evaluate(script)
    except Exception:
        return ""

def main() -> None:
    if not TARGETS_FILE.exists():
        raise SystemExit("capture_targets.txt is missing.")
    targets = [
        line.strip() for line in TARGETS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not targets:
        raise SystemExit("No URLs found in capture_targets.txt.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for old_image in OUTPUT_DIR.glob("*.png"):
        old_image.unlink()
    assets, failures = [], []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for source_index, raw_url in enumerate(targets, start=1):
            url = raw_url if urlparse(raw_url).scheme else "https://" + raw_url
            if urlparse(url).scheme not in {"http", "https"}:
                failures.append({"url": url, "error": "Unsupported URL scheme"})
                continue
            context = browser.new_context(
                viewport=VIEWPORT, screen=VIEWPORT, device_scale_factor=DPR,
                is_mobile=True, has_touch=True,
            )
            page = context.new_page()
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(1800)
                consent_action = dismiss_cookie_notice(page)
                page_height = page.evaluate(
                    "() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
                )
                max_scroll = max(0, page_height - VIEWPORT["height"])
                count = min(MAX_CAPTURES_PER_PAGE, max(1, math.ceil(max(1, page_height) / 650)))
                positions = [0] if count == 1 else [
                    round(i * max_scroll / (count - 1)) for i in range(count)
                ]
                for capture_index, scroll_y in enumerate(positions, start=1):
                    page.evaluate("(y) => window.scrollTo(0, y)", scroll_y)
                    page.wait_for_timeout(300)
                    output_path = OUTPUT_DIR / f"{source_index:02d}_{safe_host(page.url)}_{capture_index:02d}.png"
                    page.screenshot(path=str(output_path), full_page=False, animations="disabled", caret="hide")
                    with Image.open(output_path) as image:
                        width, height = image.size
                    assets.append({
                        "asset_id": f"source-{source_index:02d}-capture-{capture_index:02d}",
                        "path": str(output_path), "file_name": output_path.name,
                        "sha256": sha256_file(output_path), "bytes": output_path.stat().st_size,
                        "width": width, "height": height, "source_index": source_index,
                        "source_url": page.url, "source_title": page.title(),
                        "http_status": response.status if response else None,
                        "scroll_y": scroll_y, "cookie_consent_action": consent_action,
                        "visible_text": visible_text(page),
                    })
                    print(f"CAPTURED | {output_path} | {width}x{height} | scroll={scroll_y}")
                print(f"SOURCE | {response.status if response else 'no response'} | {page.title()} | {page.url}")
            except Exception as exc:
                failures.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
                print(f"FAILED | {url} | {type(exc).__name__}: {exc}")
            finally:
                context.close()
        browser.close()

    manifest = {
        "version": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
        "viewport_css": VIEWPORT, "device_scale_factor": DPR,
        "expected_output_pixels": {"width": 1080, "height": 1920},
        "targets": targets, "assets": assets, "failures": failures,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"MANIFEST | {MANIFEST} | assets={len(assets)} | failures={len(failures)}")
    if not assets:
        raise SystemExit("No screenshots were captured successfully.")

if __name__ == "__main__":
    main()
