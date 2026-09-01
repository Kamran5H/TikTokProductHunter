"""
TikTok Shop confirmation — v2.

Two tiered fallbacks added since v1:

1. DuckDuckGo: tries the `ddgs` library first; if it errors out or returns
   fewer than 3 URLs, falls back to loading DDG's HTML endpoint in Playwright
   and parsing the results page directly. Avoids the Google-fallback decoder
   errors that v1 hit.

2. shop.tiktok.com page fetch: tries headless Chromium first; if blocked or
   empty, opens a SEPARATE VISIBLE Chromium window and pauses with a clear
   prompt. You solve any CAPTCHA / scroll / dismiss popups manually, then
   press Enter in the terminal. The script captures the page and continues.

Also improved:
  - URL-structure-aware confidence: shop.tiktok.com/.../pdp/ URLs are real
    product pages by definition, so even an unfetchable pdp URL is at least
    MEDIUM confidence.
  - Brand storefront detection (shop.tiktok.com/@username) is flagged.
  - Cap on visible-browser pauses so you can step away if needed.

Setup: same packages as v4.

Run:
    python tiktok_shop_confirm_v2.py
"""

import os
import re
import sys
import time
import random
import logging
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page, BrowserContext

try:
    from colorama import init as colorama_init, Fore, Style
    colorama_init()
    C_INFO = Fore.LIGHTBLACK_EX
    C_OK   = Fore.GREEN
    C_WARN = Fore.YELLOW
    C_ERR  = Fore.RED
    C_PROMPT = Fore.CYAN
    C_RST  = Style.RESET_ALL
except ImportError:
    C_INFO = C_OK = C_WARN = C_ERR = C_PROMPT = C_RST = ""

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

# ============================================================
# CONFIG
# ============================================================

# Must match the hunter script's output folder. Same TIKTOK_HUNTER_OUT override.
BASE_OUTPUT = Path(os.environ.get(
    "TIKTOK_HUNTER_OUT",
    r"C:\Users\chkam\OneDrive\Desktop\Toktok Business\Top selling products",
))
HEADLESS = True                     # initial mode for headless fetches
ALLOW_VISIBLE_FALLBACK = True       # open visible browser when headless fails
MAX_VISIBLE_PAUSES = 12             # safety cap on manual-CAPTCHA prompts
DDG_MAX_RESULTS = 10
URLS_TO_VISIT_PER_PRODUCT = 3
DDG_DELAY = (1.5, 3.0)
VISIBLE_PAUSE_TIMEOUT = 180         # seconds; auto-continue if no Enter pressed

REGION_INFO = {
    "US": {"ddg_region": "us-en"},
    "UK": {"ddg_region": "uk-en"},
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)

# ============================================================
# LOG
# ============================================================

class ColourFormatter(logging.Formatter):
    LEVEL_COLOURS = {"DEBUG": C_INFO, "INFO": C_INFO,
                     "WARNING": C_WARN, "ERROR": C_ERR, "CRITICAL": C_ERR}
    def format(self, record):
        colour = self.LEVEL_COLOURS.get(record.levelname, "")
        msg = record.getMessage()
        if msg.startswith(("===", "---", "Product ")) and record.levelno == logging.INFO:
            colour = C_OK
        elif "✓" in msg or "HIGH" in msg or "saved" in msg.lower():
            colour = C_OK
        elif "MEDIUM" in msg:
            colour = C_WARN
        elif " NONE" in msg or " LOW" in msg:
            colour = C_INFO
        return f"{colour}{self.formatTime(record, '%H:%M:%S')} {record.levelname:7s} {msg}{C_RST}"

handler = logging.StreamHandler()
handler.setFormatter(ColourFormatter())
log = logging.getLogger("confirm")
log.setLevel(logging.INFO)
log.handlers = [handler]
log.propagate = False


# ============================================================
# BROWSER — headless + on-demand visible fallback
# ============================================================

class Browser:
    def __enter__(self):
        self._pw = sync_playwright().start()
        # headless context (default)
        self.h_browser = self._pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        self.h_ctx: BrowserContext = self.h_browser.new_context(
            user_agent=USER_AGENT, locale="en-US",
            viewport={"width": 1366, "height": 900},
        )
        self.h_ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        # visible context (lazy — only created when needed)
        self.v_browser = None
        self.v_ctx: Optional[BrowserContext] = None
        self.visible_pauses_used = 0
        return self

    def _ensure_visible(self):
        if self.v_ctx is None:
            log.info(C_PROMPT + "  launching visible browser (one-time setup)..." + C_RST)
            self.v_browser = self._pw.chromium.launch(headless=False, args=["--no-sandbox"])
            self.v_ctx = self.v_browser.new_context(
                user_agent=USER_AGENT, locale="en-US",
                viewport={"width": 1366, "height": 900},
            )
        return self.v_ctx

    def __exit__(self, *a):
        for fn in (
            lambda: self.h_ctx.close(),
            lambda: self.h_browser.close(),
            lambda: self.v_ctx.close() if self.v_ctx else None,
            lambda: self.v_browser.close() if self.v_browser else None,
            lambda: self._pw.stop(),
        ):
            try: fn()
            except Exception: pass

    def fetch_headless(self, url, scroll=2, settle=2.5, retries=2):
        for attempt in range(retries + 1):
            page: Optional[Page] = None
            try:
                page = self.h_ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                try: page.wait_for_load_state("networkidle", timeout=10000)
                except Exception: pass
                time.sleep(settle + random.uniform(0, 1.0))
                for _ in range(scroll):
                    page.evaluate("window.scrollBy(0, 600)")
                    time.sleep(random.uniform(0.6, 1.2))
                html = page.content()
                page.close()
                if html and "captcha" not in html.lower()[:5000] and len(html) > 5000:
                    return html
            except Exception as e:
                log.warning(f"  headless retry {attempt+1}: {str(e)[:90]}")
                if page is not None:
                    try: page.close()
                    except Exception: pass
            time.sleep(random.uniform(3, 6))
        return None

    def fetch_visible_with_pause(self, url, scroll=2):
        """Open URL in a visible window, pause for manual interaction, capture."""
        if not ALLOW_VISIBLE_FALLBACK:
            return None
        if self.visible_pauses_used >= MAX_VISIBLE_PAUSES:
            log.warning(f"  visible-pause cap reached ({MAX_VISIBLE_PAUSES}); skipping")
            return None

        ctx = self._ensure_visible()
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try: page.wait_for_load_state("networkidle", timeout=15000)
            except Exception: pass

            self.visible_pauses_used += 1
            print()
            print(C_PROMPT + "  " + "=" * 68 + C_RST)
            print(C_PROMPT + "  VISIBLE BROWSER OPEN" + C_RST)
            print(C_PROMPT + "  URL: " + url[:90] + C_RST)
            print(C_PROMPT + "  Solve any CAPTCHA / scroll the product page so it loads fully," + C_RST)
            print(C_PROMPT + f"  then press ENTER here. (auto-continue after {VISIBLE_PAUSE_TIMEOUT}s)" + C_RST)
            print(C_PROMPT + f"  Pauses used: {self.visible_pauses_used} / {MAX_VISIBLE_PAUSES}" + C_RST)
            print(C_PROMPT + "  " + "=" * 68 + C_RST)

            _input_with_timeout(VISIBLE_PAUSE_TIMEOUT)

            for _ in range(scroll):
                try:
                    page.evaluate("window.scrollBy(0, 600)")
                    time.sleep(0.6)
                except Exception:
                    break
            html = page.content()
            page.close()
            return html if html and len(html) > 3000 else None
        except Exception as e:
            log.warning(f"  visible fetch error: {str(e)[:120]}")
            try: page.close()
            except Exception: pass
            return None


def _input_with_timeout(seconds: int):
    """Wait for ENTER but auto-continue after `seconds` (Windows-compatible)."""
    try:
        import msvcrt
        end = time.time() + seconds
        while time.time() < end:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\r", "\n"):
                    return
            time.sleep(0.1)
    except ImportError:
        # non-Windows: simple input with no timeout
        try:
            input()
        except EOFError:
            pass


# ============================================================
# DDG — tiered: library → Playwright HTML endpoint
# ============================================================

def ddg_search_library(query: str, region: str) -> List[str]:
    if DDGS is None:
        return []
    urls = []
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(
                query, region=region, safesearch="moderate",
                max_results=DDG_MAX_RESULTS,
            )) or []
    except Exception as e:
        log.warning(f"  ddg library: {str(e)[:90]}")
        return []
    for r in results:
        href = (r.get("href") or "")
        if "shop.tiktok.com" in href.lower() and href not in urls:
            urls.append(href)
    return urls


def ddg_search_playwright(br: Browser, query: str) -> List[str]:
    """Load DDG's HTML endpoint in headless Chromium and parse results."""
    q = urllib.parse.quote_plus(query)
    url = f"https://html.duckduckgo.com/html/?q={q}"
    html = br.fetch_headless(url, scroll=0, settle=2.0)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    urls = []
    for a in soup.select("a.result__a, a.result__url, a[href*='shop.tiktok.com']"):
        href = a.get("href", "") or ""
        # DDG sometimes wraps in a redirect
        if "uddg=" in href:
            m = re.search(r"uddg=([^&]+)", href)
            if m:
                href = urllib.parse.unquote(m.group(1))
        if "shop.tiktok.com" in href.lower() and href not in urls:
            urls.append(href)
    return urls


def ddg_search(br: Browser, query: str, region: str) -> List[str]:
    urls = ddg_search_library(query, region)
    if len(urls) >= 3:
        time.sleep(random.uniform(*DDG_DELAY))
        return urls
    log.info(f"  ddg library: {len(urls)} URLs — falling back to Playwright DDG")
    pw_urls = ddg_search_playwright(br, query)
    merged = list(urls)
    for u in pw_urls:
        if u not in merged:
            merged.append(u)
    time.sleep(random.uniform(*DDG_DELAY))
    return merged


# ============================================================
# HELPERS
# ============================================================

def find_latest_run() -> Optional[Path]:
    if not BASE_OUTPUT.exists():
        return None
    dates = [p for p in BASE_OUTPUT.iterdir()
             if p.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}$", p.name)]
    return max(dates, key=lambda p: p.name) if dates else None


def short_search_terms(name: str, brand: str = "") -> str:
    n = name
    n = re.sub(r"\([^)]*\)", "", n)
    n = re.sub(r"\[[^\]]*\]", "", n)
    n = re.sub(
        r"\b\d+\s*(count|ct|pack|bars|bottles|capsules|softgels|tablets|"
        r"gummies|servings|servs|lb|lbs|oz|fl\s*oz|ml|l|g|kg|grams|gram|mg|mcg)\b",
        "", n, flags=re.IGNORECASE,
    )
    n = re.sub(
        r"\b(high|low|sugar|carb|gluten|free|keto|friendly|gmo|non-?gmo|"
        r"organic|natural|premium|improved|original|variety|dietary|new|"
        r"by|with|and|for|the|of|in|a)\b",
        "", n, flags=re.IGNORECASE,
    )
    n = re.sub(r"[^\w\s\-]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    words = n.split()[:5]
    short = " ".join(words)
    if brand and brand.lower() not in short.lower():
        short = f"{brand} {short}".strip()
    return short[:80]


def classify_url(url: str) -> str:
    """Classify a shop.tiktok.com URL by structure."""
    u = url.lower()
    if "/pdp/" in u or "/products/" in u:
        return "product_page"
    if re.search(r"shop\.tiktok\.com/@", u):
        return "shop_storefront"
    if "/view/shop/" in u or "/shop/" in u:
        return "shop_storefront"
    if "/search" in u or "/k/" in u:
        return "search_page"
    return "other"


def parse_tiktok_shop_page(html: str, url: str) -> Dict:
    soup = BeautifulSoup(html, "lxml")
    out = {
        "url": url, "url_type": classify_url(url),
        "page_title": "", "product_name": "",
        "seller": "", "price": "", "rating": "", "sold": "",
    }
    t = soup.find("title")
    if t: out["page_title"] = t.get_text(strip=True)[:200]

    for sel in [
        "h1[class*='title']", "h1[class*='Title']",
        "[class*='product-title']", "[class*='ProductTitle']",
        "[class*='goods-name']", "[class*='product-name']",
        "meta[property='og:title']",
    ]:
        el = soup.select_one(sel)
        if el:
            txt = el.get("content") if el.name == "meta" else el.get_text(strip=True)
            if txt and len(txt) > 4:
                out["product_name"] = txt[:250]; break

    for sel in [
        "[class*='shop-name']", "[class*='ShopName']",
        "[class*='seller-name']", "[class*='SellerName']",
        "a[class*='shop'] span",
    ]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            out["seller"] = el.get_text(strip=True)[:120]; break

    page_text = soup.get_text(" ", strip=True)
    m = re.search(r"(?:US\s*)?[\$£€]\s*\d+(?:\.\d{1,2})?(?:\s*-\s*[\$£€]\s*\d+(?:\.\d{1,2})?)?", page_text)
    if m: out["price"] = m.group(0)[:60]
    m = re.search(r"\b([0-4]\.\d|5\.0)\s*(?:out of 5|/5|★|stars)\b", page_text, re.IGNORECASE)
    if m: out["rating"] = m.group(1)
    m = re.search(r"(\d[\d,]*\.?\d*\s*[kKmM]?\+?)\s*(?:sold|orders?)", page_text)
    if m: out["sold"] = m.group(1)
    return out


def score_confidence(details: List[Dict], urls: List[str]) -> str:
    if not urls:
        return "NONE"
    url_types = [classify_url(u) for u in urls]
    has_pdp = "product_page" in url_types
    has_shop = "shop_storefront" in url_types
    has_full = any(d.get("price") and d.get("product_name") and d.get("seller")
                   for d in details)
    has_partial_parsed = any(d.get("product_name") or d.get("price") for d in details)
    if has_full:
        return "HIGH"
    if has_pdp and has_partial_parsed:
        return "HIGH"
    if has_pdp:                # real product URL even if page didn't parse
        return "MEDIUM"
    if has_partial_parsed:
        return "MEDIUM"
    if has_shop:
        return "MEDIUM"
    return "LOW"


# ============================================================
# PER-PRODUCT VERIFICATION
# ============================================================

def verify_product(br: Browser, product: Dict, region: str) -> Dict:
    region_cfg = REGION_INFO[region]
    name = str(product.get("product_name") or "").strip()
    brand = str(product.get("brand") or "").strip()
    short_q = short_search_terms(name, brand)

    queries = [
        f'site:shop.tiktok.com "{short_q}"',
        f'site:shop.tiktok.com {short_q}',
    ]
    if brand:
        queries.append(f'site:shop.tiktok.com {brand}')

    found_urls: List[str] = []
    for q in queries:
        urls = ddg_search(br, q, region_cfg["ddg_region"])
        for u in urls:
            if u not in found_urls:
                found_urls.append(u)
        if len(found_urls) >= URLS_TO_VISIT_PER_PRODUCT:
            break

    log.info(f"  DDG returned {len(found_urls)} shop.tiktok.com URLs")

    # Prioritise pdp URLs first
    found_urls.sort(key=lambda u: 0 if "/pdp/" in u.lower() else
                                   (1 if "/@" in u.lower() else 2))

    details = []
    for u in found_urls[:URLS_TO_VISIT_PER_PRODUCT]:
        log.info(f"  fetch headless: {u[:90]}")
        html = br.fetch_headless(u, scroll=2)
        if not html and ALLOW_VISIBLE_FALLBACK:
            log.warning(f"  headless blocked — trying visible browser")
            html = br.fetch_visible_with_pause(u, scroll=2)
        if html:
            details.append(parse_tiktok_shop_page(html, u))
        time.sleep(random.uniform(1.5, 3.0))

    conf = score_confidence(details, found_urls)
    log.info(f"  confidence: {conf}")
    return {
        "search_query": short_q,
        "shop_urls": found_urls[:5],
        "details": details,
        "confidence": conf,
    }


# ============================================================
# OUTPUT
# ============================================================

def write_confirmation(region: str, products: List[Dict], date_str: str,
                       run_folder: Path) -> Path:
    region_folder = run_folder / region
    region_folder.mkdir(parents=True, exist_ok=True)

    rows = []
    for rank, p in enumerate(products, 1):
        v = p["verification"]
        first = v["details"][0] if v["details"] else {}
        rows.append({
            "rank": rank,
            "product_name": p.get("product_name", ""),
            "brand": p.get("brand", ""),
            "amazon_total_score": p.get("total_score", ""),
            "confidence": v["confidence"],
            "search_query_used": v["search_query"],
            "shop_urls_count": len(v["shop_urls"]),
            "first_shop_url": v["shop_urls"][0] if v["shop_urls"] else "",
            "first_url_type": classify_url(v["shop_urls"][0]) if v["shop_urls"] else "",
            "tt_product_name": first.get("product_name", ""),
            "tt_seller": first.get("seller", ""),
            "tt_price": first.get("price", ""),
            "tt_rating": first.get("rating", ""),
            "tt_sold": first.get("sold", ""),
            "all_shop_urls": " | ".join(v["shop_urls"]),
        })
    df = pd.DataFrame(rows)
    csv_path = region_folder / f"tiktok_shop_confirmation_{region}_{date_str}.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    high  = sum(1 for r in rows if r["confidence"] == "HIGH")
    med   = sum(1 for r in rows if r["confidence"] == "MEDIUM")
    low   = sum(1 for r in rows if r["confidence"] == "LOW")
    none_ = sum(1 for r in rows if r["confidence"] == "NONE")

    lines = [
        f"# TikTok Shop confirmation — {region} — {date_str}", "",
        "Verifies top-5 products against public shop.tiktok.com listings.",
        "Tiered DDG (library → Playwright). Tiered page fetch (headless → visible).",
        "",
        "## Confidence summary",
        f"- HIGH: **{high} / {len(rows)}**",
        f"- MEDIUM: **{med} / {len(rows)}**",
        f"- LOW: **{low} / {len(rows)}**",
        f"- NONE: **{none_} / {len(rows)}**",
        "",
        "## Per-product detail", "",
    ]
    for r in rows:
        v_urls = r["all_shop_urls"].split(" | ") if r["all_shop_urls"] else []
        lines.append(f"### {r['rank']}. {r['product_name'][:160]}"); lines.append("")
        lines.append(f"- **Confidence:** {r['confidence']}")
        lines.append(f"- **Brand:** {r['brand'] or 'not detected'}")
        lines.append(f"- **Search query used:** `{r['search_query_used']}`")
        if r["first_shop_url"]:
            lines.append(f"- **First shop URL:** {r['first_shop_url']}")
            lines.append(f"    - url type: `{r['first_url_type']}`")
            if r["tt_product_name"]: lines.append(f"    - product name on TikTok: {r['tt_product_name']}")
            if r["tt_seller"]:       lines.append(f"    - seller: {r['tt_seller']}")
            if r["tt_price"]:        lines.append(f"    - price: {r['tt_price']}")
            if r["tt_rating"]:       lines.append(f"    - rating: {r['tt_rating']}")
            if r["tt_sold"]:         lines.append(f"    - sold: {r['tt_sold']}")
            if len(v_urls) > 1:
                lines.append(f"- **Other URLs:**")
                for u in v_urls[1:]:
                    lines.append(f"    - {u}")
        else:
            lines.append("- **No shop.tiktok.com listings found.**")
        lines.append("")

    lines += [
        "## How to read this",
        "- **HIGH** — clear listing with price+seller, or a `/pdp/` URL with partial parse. Real candidate.",
        "- **MEDIUM** — product page URL exists but couldn't parse details, "
        "or only a brand storefront was found. Worth manual check.",
        "- **LOW** — only a search/topic URL was found; product itself is unclear.",
        "- **NONE** — no shop.tiktok.com indexed URL. Affiliate path unlikely.",
        "",
        "## Limits",
        "- This script confirms public listing only.",
        "- 'Open collaboration' status and commission rate are visible only inside the affiliate dashboard, after approval.",
        "- A HIGH or MEDIUM here is a strong signal you'll find the product when you log in. LOW/NONE means don't bother.",
    ]
    md_path = region_folder / f"tiktok_shop_confirmation_{region}_{date_str}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return csv_path


# ============================================================
# MAIN
# ============================================================

def main():
    run_folder = find_latest_run()
    if not run_folder:
        log.error(f"No dated run folder under {BASE_OUTPUT}. Run the hunter first.")
        return
    date_str = run_folder.name
    log.info(f"=== TikTok Shop confirmation v2 — run folder: {date_str} ===")
    if ALLOW_VISIBLE_FALLBACK:
        log.info(C_PROMPT + f"Visible-browser fallback enabled "
                 f"(max {MAX_VISIBLE_PAUSES} pauses)." + C_RST)

    with Browser() as br:
        for region in ("US", "UK"):
            region_folder = run_folder / region
            csv_path = region_folder / f"top5_{region}_{date_str}.csv"
            if not csv_path.exists():
                log.warning(f"  no top5 CSV for {region}: {csv_path}")
                continue
            log.info(f"--- {region} ---")
            df = pd.read_csv(csv_path)
            if df.empty:
                log.warning(f"  empty top5 for {region}")
                continue
            products = df.to_dict(orient="records")
            for i, p in enumerate(products, 1):
                log.info(f"Product {i}/{len(products)}: "
                         f"{str(p.get('product_name',''))[:80]}")
                p["verification"] = verify_product(br, p, region)
            out_path = write_confirmation(region, products, date_str, run_folder)
            log.info(f"  ✓ saved → {out_path}")

    log.info("=== Done ===")


if __name__ == "__main__":
    main()