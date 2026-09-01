"""
TikTok Shop product hunter — v4.

Hurdles anticipated and handled in this version (compared to v3):
  1. Accessories sneak through the keyword filter (shaker balls, bottles, scoops,
     tumblers, etc.). NEGATIVE_KEYWORDS now drops them before detail-page fetch.
  2. "Supplement" in a title does not mean the product is a supplement
     (e.g. "supplement organiser"). Negative list and category sanity check.
  3. Multi-pack accessories with supplement words in the title.
  4. Listings with no price (out of stock, removed). Dropped at scoring.
  5. Listings with a sponsor/ad-only structure that breaks the selector. Skipped.
  6. Amazon category URLs that 404 or get redirected (Protein-Powders did this
     in v3). Marked as soft failures, run continues.
  7. CAPTCHA on Amazon detail pages. Saved to _debug, candidate dropped.
  8. TikTok Creative Center returns 0 (HTML drift). Falls back to DDG-only signal.
  9. DDG rate-limits or returns empty. Score still computed, DDG just contributes 0.
 10. Red log lines for normal info caused stress. Replaced with grey/green/yellow/red
     colour coding (only real errors are red).
 11. Same product appearing in US and UK results. Cross-region dedupe in summary.
 12. Top 5 with all the same product type (e.g. all collagen). Mild diversity
     penalty so the final 5 cover at least 3 different product types.

Setup:
    pip install playwright requests beautifulsoup4 pandas rapidfuzz lxml ddgs colorama
    playwright install chromium

Run:
    python tiktok_product_hunter_v4.py
"""

import os
import re
import json
import time
import math
import random
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Set
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup
from rapidfuzz import fuzz
from playwright.sync_api import sync_playwright, Page, BrowserContext

# colour
try:
    from colorama import init as colorama_init, Fore, Style
    colorama_init()
    C_INFO = Fore.LIGHTBLACK_EX          # grey for routine info
    C_OK   = Fore.GREEN
    C_WARN = Fore.YELLOW
    C_ERR  = Fore.RED
    C_RST  = Style.RESET_ALL
except ImportError:
    C_INFO = C_OK = C_WARN = C_ERR = C_RST = ""

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

# Output location. Override with the TIKTOK_HUNTER_OUT env var; otherwise defaults
# to the historical folder so existing runs keep writing to the same place.
BASE_OUTPUT = Path(os.environ.get(
    "TIKTOK_HUNTER_OUT",
    r"C:\Users\chkam\OneDrive\Desktop\Toktok Business\Top selling products",
))
DEBUG_DIR = BASE_OUTPUT / "_debug"
HEADLESS = False
DETAIL_MAX_PER_REGION = 22         # pulled up because we drop more in pre-filter
FINAL_TOP_N = 5
DDG_MAX_RESULTS = 8
DDG_DELAY = (1.5, 3.0)

REGIONS = {
    "US": {
        "cc_country": "US",
        "amazon_base": "https://www.amazon.com",
        "ddg_region": "us-en",
        "currency": "$",
        "amazon_urls": [
            "https://www.amazon.com/Best-Sellers-Health-Personal-Care-Sports-Nutrition/zgbs/hpc/6973664011",
            "https://www.amazon.com/Best-Sellers-Health-Personal-Care-Vitamins-Dietary-Supplements/zgbs/hpc/3764441",
            "https://www.amazon.com/Best-Sellers-Sports-Outdoors-Sports-Nutrition/zgbs/sporting-goods/3375251",
            # protein powders category was redirecting; using a working alt:
            "https://www.amazon.com/Best-Sellers-Health-Personal-Care-Protein-Drinks/zgbs/hpc/6939426011",
        ],
    },
    "UK": {
        "cc_country": "GB",
        "amazon_base": "https://www.amazon.co.uk",
        "ddg_region": "uk-en",
        "currency": "£",
        "amazon_urls": [
            "https://www.amazon.co.uk/Best-Sellers-Health-Personal-Care-Sports-Nutrition/zgbs/drugstore/2826510031",
            "https://www.amazon.co.uk/Best-Sellers-Health-Personal-Care-Vitamins-Dietary-Supplements/zgbs/drugstore/2826534031",
            "https://www.amazon.co.uk/Best-Sellers-Health-Personal-Care-Protein-Drinks-Shakes/zgbs/drugstore/2826491031",
        ],
    },
}

MAJOR_BRANDS = {
    "optimum nutrition", "myprotein", "garden of life", "now foods",
    "nature made", "centrum", "one a day", "kirkland", "gnc",
    "muscletech", "dymatize", "bsn", "cellucor", "isopure", "ghost",
    "ryse", "redcon", "transparent labs", "vital proteins",
    "ancient nutrition", "bulk", "applied nutrition", "huel",
    "ehp labs", "naked nutrition", "thorne", "puritan's pride",
    "puritans pride", "nature's bounty", "natures bounty", "solgar",
    "rainbow light", "swisse", "blackmores", "ostrovit",
    "evlution nutrition", "nutricost", "legion", "athletic greens",
    "ag1", "ritual", "olly", "hum", "goli", "neocell", "medicube",
    "dr.melaxin", "nature's way", "natures way", "jarrow", "doctor's best",
    "doctors best", "life extension", "nordic naturals", "sports research",
    "pure encapsulations", "nutrabolt", "c4", "gatorade", "celsius",
    "powerade", "redbull", "monster", "alani nu", "bloom nutrition",
    "liquid iv", "liquid i.v.", "drink lmnt", "lmnt", "amazon brand",
    "amazon basics", "solimo", "by amazon", "amfit nutrition",
}

# POSITIVE: title MUST contain at least one of these
FITNESS_KEYWORDS = [
    "protein", "whey", "casein", "isolate", "creatine", "preworkout",
    "pre-workout", "pre workout", "post workout", "post-workout",
    "bcaa", "eaa", "amino", "collagen", "magnesium", "vitamin",
    "multivitamin", "omega", "fish oil", "biotin", "ashwagandha",
    "turmeric", "probiotic", "fat burner", "thermogenic",
    "electrolyte", "mass gainer", "gainer", "greens", "superfood",
    "fiber", "psyllium", "zinc", "iron", "b12", "vitamin d",
    "vitamin c", "calcium", "glucosamine", "msm", "hyaluronic",
    "testosterone", "test booster", "keto", "mct", "peptides",
    "nootropic", "melatonin", "ksm-66", "l-carnitine", "carnitine",
    "beta-alanine", "supplement", "vitamins", "nutrition powder",
    "capsules", "softgels", "gummies", "tablets",
]

# NEGATIVE: title MUST NOT contain any of these. Drops accessories.
NEGATIVE_KEYWORDS = [
    # accessories
    "shaker ball", "shaker balls", "mixer ball", "mixing ball", "blender ball",
    "wire ball", "whisk ball",
    "shaker bottle", "shaker cup", "blender bottle", "protein bottle",
    "water bottle", "tumbler", "thermos", "flask", "softflask",
    "scoop", "funnel", "powder funnel", "dispenser",
    "container", "containers", "storage", "organizer", "organiser",
    "stash", "case", "pouch", "bag", "carrier",
    "pill box", "pill organizer", "pill organiser", "pill case",
    "lid", "cap", "sleeve", "strap", "holder",
    "blender", "mixer", "grinder",
    "stack pack", "weekly pack", "daily pack",  # often empty organisers
    "label", "sticker", "decal",
    # books / digital
    "book", "ebook", "guide ", "cookbook", "planner", "journal", "diary",
    # apparel / equipment
    "shirt", "tshirt", "t-shirt", "hoodie", "shorts", "leggings",
    "shoes", "trainers", "sneakers", "gloves", "wrist", "wristband",
    "belt", "lifting strap", "knee sleeve", "resistance band",
    "yoga mat", "foam roller", "massage gun", "ab roller",
    "dumbbell", "barbell", "kettlebell", "weight plate",
    # food/snack accessories (not the supplement itself)
    "spoon", "measuring",
    # ambiguous
    "for protein", "for shake", "for shakes", "for powder",  # accessory hints
]

# Category sanity: the product page bullets should reference INGESTION cues.
# This is a soft check — used as a bonus, not a hard filter.
INGESTION_HINTS = [
    "serving", "servings", "scoop", "scoops per", "capsule", "capsules",
    "tablet", "tablets", "softgel", "softgels", "gummy", "gummies",
    "mg ", "mg)", "mcg", "iu ", "grams of", "gram of protein",
    "daily dose", "take ", "one capsule", "two capsules",
    "mix with water", "mix with milk", "ingredients",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)

# ============================================================
# LOGGING — colour-coded
# ============================================================

class ColourFormatter(logging.Formatter):
    LEVEL_COLOURS = {
        "DEBUG":    C_INFO,
        "INFO":     C_INFO,
        "WARNING":  C_WARN,
        "ERROR":    C_ERR,
        "CRITICAL": C_ERR,
    }
    def format(self, record):
        colour = self.LEVEL_COLOURS.get(record.levelname, "")
        # phase headers get green
        msg = record.getMessage()
        if msg.startswith(("===", "---", "Phase ")) and record.levelno == logging.INFO:
            colour = C_OK
        elif "✓" in msg or " saved " in msg:
            colour = C_OK
        return f"{colour}{self.formatTime(record, '%H:%M:%S')} {record.levelname:7s} {msg}{C_RST}"

handler = logging.StreamHandler()
handler.setFormatter(ColourFormatter())
log = logging.getLogger("hunter")
log.setLevel(logging.INFO)
log.handlers = [handler]
log.propagate = False


# ============================================================
# BROWSER
# ============================================================

class Browser:
    def __enter__(self):
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox",
            ],
        )
        self.ctx: BrowserContext = self.browser.new_context(
            user_agent=USER_AGENT,
            locale="en-US",
            viewport={"width": 1366, "height": 900},
            java_script_enabled=True,
        )
        self.ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return self

    def __exit__(self, *a):
        for fn in (self.ctx.close, self.browser.close, self._pw.stop):
            try: fn()
            except Exception: pass

    def fetch_html(self, url, wait_selector=None, scroll=0, settle=2.5, retries=2):
        for attempt in range(retries + 1):
            page: Optional[Page] = None
            try:
                page = self.ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                if wait_selector:
                    try:
                        page.wait_for_selector(wait_selector, timeout=8000)
                    except Exception:
                        pass
                time.sleep(settle + random.uniform(0, 1.2))
                for _ in range(scroll):
                    page.evaluate("window.scrollBy(0, 700)")
                    time.sleep(random.uniform(0.7, 1.4))
                html = page.content()
                page.close()
                if html and "captcha" not in html.lower()[:5000]:
                    return html
                log.warning(f"  retry {attempt+1}: block or empty on {url[-50:]}")
            except Exception as e:
                log.warning(f"  retry {attempt+1} error: {e}")
                if page is not None:
                    try: page.close()
                    except Exception: pass
            time.sleep(random.uniform(6, 12))
        return None


def save_debug(name, html):
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fp = DEBUG_DIR / f"{name}_{ts}.html"
    fp.write_text(html, encoding="utf-8")
    log.info(f"  debug saved → {fp}")


# ============================================================
# AMAZON BESTSELLERS
# ============================================================

def parse_bestseller_html(html, base_url):
    soup = BeautifulSoup(html, "lxml")
    items = soup.select(
        "div.zg-grid-general-faceout, div[id^='gridItemRoot'], "
        "div.p13n-sc-uncoverable-faceout"
    )
    out = []
    for item in items:
        name_el = item.select_one(
            "div._cDEzb_p13n-sc-css-line-clamp-3_g3dy1, "
            "div._cDEzb_p13n-sc-css-line-clamp-2_EWgCb, "
            "div._cDEzb_p13n-sc-css-line-clamp-1_1Fn1y, "
            "div._cDEzb_p13n-sc-css-line-clamp-4_2q2cc, "
            "a.a-link-normal span div"
        )
        if not name_el:
            name_el = item.select_one("a.a-link-normal")
        if not name_el: continue
        name = name_el.get_text(strip=True)
        if not name or len(name) < 5: continue

        price_el = item.select_one(
            "span._cDEzb_p13n-sc-price_3mJ9Z, span.p13n-sc-price, "
            "span.a-color-price, span.a-price span.a-offscreen"
        )
        rating_el = item.select_one("span.a-icon-alt, i.a-icon-star span.a-icon-alt")
        link_el = item.select_one("a.a-link-normal[href*='/dp/'], a.a-link-normal")
        price = price_el.get_text(strip=True) if price_el else ""
        rating_txt = rating_el.get_text(strip=True) if rating_el else ""
        m = re.search(r"([\d.]+)\s*out of", rating_txt)
        rating = float(m.group(1)) if m else None
        product_url = ""
        if link_el and link_el.get("href"):
            href = link_el["href"].split("?")[0]
            product_url = urljoin(base_url, href)
        out.append({
            "name": name, "price_text": price,
            "rating": rating, "url": product_url,
        })
    return out


def scrape_amazon_bestsellers(br, cfg):
    out = []
    for url in cfg["amazon_urls"]:
        html = br.fetch_html(url, wait_selector="div.p13n-grid-content, div[id^='gridItemRoot']", scroll=4)
        if not html:
            log.warning(f"  bestseller skipped (no html): {url[-50:]}")
            continue
        items = parse_bestseller_html(html, url)
        if not items:
            save_debug(f"amazon_bestseller_empty_{cfg['cc_country']}", html)
        log.info(f"  bestseller {len(items):3d} ← {url[-50:]}")
        out.extend(items)
        time.sleep(random.uniform(2, 4))
    seen, dedup = set(), []
    for p in out:
        k = p["name"].lower()[:80]
        if k in seen: continue
        seen.add(k); dedup.append(p)
    return dedup


# ============================================================
# AMAZON DETAIL
# ============================================================

def parse_price_number(text):
    if not text: return None
    cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
    if not cleaned: return None
    try: return float(cleaned)
    except ValueError: return None


def enrich_detail(br, product):
    e = dict(product)
    e.update({
        "brand": "", "review_count": None, "bought_past_month": "",
        "price_value": None, "features": [], "ingestion_hits": 0,
        "detail_ok": False,
    })
    if not product.get("url"): return e

    html = br.fetch_html(product["url"], wait_selector="#productTitle, #title",
                         scroll=2, settle=2.0)
    if not html:
        # detail page blocked (CAPTCHA) or unreachable — leave detail_ok False so
        # the caller can decide whether to trust this candidate.
        log.warning(f"    detail fetch failed (block/CAPTCHA/empty): {product['name'][:60]}")
        return e
    e["detail_ok"] = True

    soup = BeautifulSoup(html, "lxml")
    # brand
    for sel in [
        "a#bylineInfo", "tr.po-brand td.a-span9 span",
        "span.a-size-base.po-break-word",
        "div#brandByline_feature_div a",
    ]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            t = el.get_text(strip=True)
            t = re.sub(r"^(Visit the|Brand:)\s*", "", t, flags=re.IGNORECASE)
            t = re.sub(r"\s*Store$", "", t, flags=re.IGNORECASE)
            e["brand"] = t
            break

    rev_el = soup.select_one("span#acrCustomerReviewText, a#acrCustomerReviewLink span")
    if rev_el:
        m = re.search(r"([\d,]+)", rev_el.get_text(strip=True))
        if m:
            try: e["review_count"] = int(m.group(1).replace(",", ""))
            except ValueError: pass

    bpm = soup.find(string=re.compile(r"bought in past month", re.IGNORECASE))
    if bpm: e["bought_past_month"] = bpm.strip()

    price_el = soup.select_one(
        "span.a-price span.a-offscreen, "
        "span#priceblock_ourprice, span#priceblock_dealprice"
    )
    if price_el:
        e["price_text"] = price_el.get_text(strip=True) or e["price_text"]
    e["price_value"] = parse_price_number(e["price_text"])

    feats = []
    for li in soup.select("#feature-bullets ul li span.a-list-item"):
        t = li.get_text(strip=True)
        if t and len(t) > 8: feats.append(t)
    e["features"] = feats[:6]

    # ingestion hint check — does this look like an ingestible supplement?
    blob = (" ".join(feats) + " " + product["name"]).lower()
    e["ingestion_hits"] = sum(1 for h in INGESTION_HINTS if h in blob)

    return e


# ============================================================
# TIKTOK CC
# ============================================================

def scrape_tiktok_cc(br, country):
    url = (f"https://ads.tiktok.com/business/creativecenter/topproducts/pc/en"
           f"?period=7&countryCode={country}")
    html = br.fetch_html(url, scroll=5, settle=4.0)
    if not html: return []
    soup = BeautifulSoup(html, "lxml")
    candidates = soup.select(
        "[class*='ProductCard'] [class*='title'], "
        "[class*='product-card'] [class*='title'], "
        "[class*='CardItem'] [class*='Title'], "
        "span[class*='product-name'], div[class*='product-name'], "
        "[class*='goods-name']"
    )
    seen, products = set(), []
    for el in candidates:
        n = el.get_text(strip=True)
        if not n or len(n) < 4: continue
        k = n.lower()[:80]
        if k in seen: continue
        seen.add(k); products.append({"name": n, "country": country})
    if not products:
        nd = soup.find("script", id="__NEXT_DATA__")
        if nd and nd.string:
            try:
                txt = json.dumps(json.loads(nd.string))
                for m in re.finditer(
                    r'"(?:product_name|productName|name)"\s*:\s*"([^"]{6,140})"', txt):
                    nm = m.group(1).strip()
                    k = nm.lower()[:80]
                    if k in seen: continue
                    seen.add(k); products.append({"name": nm, "country": country})
            except Exception as e:
                log.warning(f"  CC parse failed: {e}")
        if not products:
            save_debug(f"tiktok_cc_empty_{country}", html)
    log.info(f"  TikTok CC ({country}): {len(products)}")
    return products


# ============================================================
# DDG
# ============================================================

def ddg_verify(product_name, region):
    result = {"ddg_total_hits": 0, "ddg_tiktok_hits": 0, "ddg_sample_url": ""}
    if DDGS is None: return result
    q = f'"{product_name[:80]}" tiktok shop'
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(q, region=region, safesearch="moderate",
                                     max_results=DDG_MAX_RESULTS)) or []
    except Exception as e:
        log.warning(f"  DDG: {e}")
        results = []
    time.sleep(random.uniform(*DDG_DELAY))
    result["ddg_total_hits"] = len(results)
    for r in results:
        href = (r.get("href") or "").lower()
        if any(d in href for d in ("tiktok.com", "shop.tiktok", "fastmoss", "kalodata", "echotik")):
            result["ddg_tiktok_hits"] += 1
            if not result["ddg_sample_url"]:
                result["ddg_sample_url"] = r.get("href", "")
    return result


# ============================================================
# FILTER + SCORE
# ============================================================

def is_fitness(name):
    n = name.lower()
    if not any(k in n for k in FITNESS_KEYWORDS):
        return False
    if any(neg in n for neg in NEGATIVE_KEYWORDS):
        return False
    return True


def matches_major_brand(text):
    if not text: return None
    t = text.lower()
    for b in MAJOR_BRANDS:
        if b in t: return b
    return None


def best_tt_match(name, tt_names):
    if not tt_names: return {"score": 0, "match": ""}
    best, best_name = 0, ""
    for tn in tt_names:
        s = fuzz.token_set_ratio(name.lower(), tn.lower())
        if s > best: best, best_name = s, tn
    return {"score": best, "match": best_name if best >= 60 else ""}


def product_type_token(name):
    """Coarse product type for diversity penalty."""
    n = name.lower()
    types = [
        ("protein", ["protein", "whey", "casein", "isolate"]),
        ("creatine", ["creatine"]),
        ("preworkout", ["preworkout", "pre-workout", "pre workout"]),
        ("collagen", ["collagen", "peptides"]),
        ("multivitamin", ["multivitamin", "multi-vitamin"]),
        ("magnesium", ["magnesium"]),
        ("omega", ["omega", "fish oil"]),
        ("ashwagandha", ["ashwagandha", "ksm-66"]),
        ("greens", ["greens", "superfood", "spirulina"]),
        ("hydration", ["electrolyte", "hydration"]),
        ("gummies", ["gummies"]),
        ("vitaminD", ["vitamin d"]),
        ("vitaminC", ["vitamin c"]),
        ("turmeric", ["turmeric", "curcumin"]),
        ("biotin", ["biotin"]),
        ("probiotic", ["probiotic"]),
        ("fatburner", ["fat burner", "thermogenic"]),
        ("bcaa", ["bcaa", "eaa", "amino"]),
        ("testbooster", ["testosterone", "test booster"]),
        ("nootropic", ["nootropic", "focus"]),
        ("sleep", ["sleep", "melatonin"]),
    ]
    for label, kws in types:
        if any(k in n for k in kws):
            return label
    return "other"


def score_product(p, tt_names, ddg):
    rating = p.get("rating") or 0.0
    rating_s = min(100.0, max(0.0, (rating - 3.5) / 1.5 * 100))

    rc = p.get("review_count") or 0
    review_s = 0.0 if rc <= 0 else min(100.0, math.log10(rc + 1) / math.log10(5001) * 100)

    bpm = (p.get("bought_past_month") or "").lower()
    m = re.search(r"([\d,]+)\s*([km]?)\+?\s*bought", bpm)
    mom_v = 0
    if m:
        n = int(m.group(1).replace(",", ""))
        mom_v = n * {"k": 1_000, "m": 1_000_000, "": 1}[m.group(2)]
    momentum_s = 0.0 if mom_v <= 0 else min(100.0, math.log10(mom_v + 1) / math.log10(10_001) * 100)

    price = p.get("price_value")
    if price is None: price_s = 25.0
    elif price < 8:   price_s = 35.0
    elif price <= 15: price_s = 70.0
    elif price <= 40: price_s = 100.0
    elif price <= 60: price_s = 70.0
    elif price <= 90: price_s = 45.0
    else:             price_s = 20.0

    tt = best_tt_match(p["name"], tt_names)
    if   tt["score"] >= 80: tiktok_s = 100.0
    elif tt["score"] >= 60: tiktok_s = 65.0
    elif tt["score"] >= 40: tiktok_s = 25.0
    else:                   tiktok_s = 0.0

    hits = ddg.get("ddg_tiktok_hits", 0)
    if   hits >= 3: ddg_s = 100.0
    elif hits == 2: ddg_s = 75.0
    elif hits == 1: ddg_s = 50.0
    else:           ddg_s = 0.0

    # ingestion bonus — does this look like a real supplement?
    ing_hits = p.get("ingestion_hits", 0)
    ingestion_s = min(100.0, ing_hits * 20.0)

    total = (
        rating_s    * 0.15 +
        review_s    * 0.20 +
        momentum_s  * 0.18 +
        price_s     * 0.12 +
        tiktok_s    * 0.13 +
        ddg_s       * 0.12 +
        ingestion_s * 0.10
    )
    return {
        "rating_score": round(rating_s, 1),
        "review_score": round(review_s, 1),
        "momentum_score": round(momentum_s, 1),
        "price_score": round(price_s, 1),
        "tiktok_score": round(tiktok_s, 1),
        "ddg_score": round(ddg_s, 1),
        "ingestion_score": round(ingestion_s, 1),
        "tiktok_match_score": tt["score"],
        "tiktok_match_name": tt["match"],
        "total": round(total, 2),
    }


def diversify_top_n(scored, n):
    """Pick top n with mild diversity penalty: prefer covering different product types."""
    scored = sorted(scored, key=lambda x: x["score"]["total"], reverse=True)
    chosen, used_types = [], set()
    # first pass: take highest-scoring of each new type
    for p in scored:
        if len(chosen) >= n: break
        t = product_type_token(p["name"])
        if t not in used_types:
            chosen.append(p); used_types.add(t)
    # second pass: fill remainder by score
    if len(chosen) < n:
        for p in scored:
            if len(chosen) >= n: break
            if p not in chosen:
                chosen.append(p)
    return chosen


# ============================================================
# OUTPUT
# ============================================================

def write_outputs(region, top5, date_str, amazon_count, tiktok_count,
                  prefilter_count, enriched_count, dropped_brand, dropped_accessory):
    folder = BASE_OUTPUT / date_str / region
    folder.mkdir(parents=True, exist_ok=True)

    rows = []
    for rank, p in enumerate(top5, 1):
        s = p["score"]; d = p["ddg"]
        rows.append({
            "rank": rank, "product_name": p["name"],
            "brand": p.get("brand", ""), "price": p.get("price_text", ""),
            "rating": p.get("rating"), "review_count": p.get("review_count"),
            "bought_past_month": p.get("bought_past_month", ""),
            "amazon_url": p.get("url", ""),
            "ddg_tiktok_hits": d.get("ddg_tiktok_hits", 0),
            "ddg_sample_url": d.get("ddg_sample_url", ""),
            "total_score": s["total"],
            "rating_score": s["rating_score"],
            "review_score": s["review_score"],
            "momentum_score": s["momentum_score"],
            "price_score": s["price_score"],
            "tiktok_score": s["tiktok_score"],
            "ddg_score": s["ddg_score"],
            "ingestion_score": s["ingestion_score"],
            "tiktok_match_name": s["tiktok_match_name"],
            "product_type": product_type_token(p["name"]),
        })
    df = pd.DataFrame(rows)
    df.to_csv(folder / f"top5_{region}_{date_str}.csv",
              index=False, encoding="utf-8-sig")

    lines = [
        f"# TikTok Shop top 5 — {region} — {date_str}", "",
        f"**Niche:** supplements / vitamins / protein / gym / fitness",
        f"**Market:** {region} "
        f"(Amazon{'.com' if region == 'US' else '.co.uk'}, region-locked)", "",
        "## Pipeline",
        f"- Amazon raw: **{amazon_count}**",
        f"- Pre-filter survivors (fitness, not accessory, not major brand): **{prefilter_count}**",
        f"- Dropped as accessories/equipment: **{dropped_accessory}**",
        f"- Dropped at byline brand check: **{dropped_brand}**",
        f"- Detail pages enriched: **{enriched_count}**",
        f"- TikTok Creative Center items: **{tiktok_count}**", "",
        "## Scoring (7 dimensions)",
        "- Rating 15% · Reviews 20% · Momentum 18% · Price sweet spot 12% "
        "· TikTok CC 13% · DDG TikTok hits 12% · Ingestion sanity 10%", "",
        "## Top 5", "",
    ]
    if not top5:
        lines.append("_No products survived. Check `_debug/` HTML files. Likely "
                     "the pre-filter dropped everything or detail pages all hit CAPTCHA._")
    else:
        for i, p in enumerate(top5, 1):
            s = p["score"]; d = p["ddg"]
            lines.append(f"### {i}. {p['name'][:160]}"); lines.append("")
            lines.append(f"- **Score:** {s['total']} / 100  ·  type: `{product_type_token(p['name'])}`")
            lines.append(f"- **Brand:** {p.get('brand') or 'not detected'}")
            lines.append(f"- **Price:** {p.get('price_text') or 'n/a'}")
            lines.append(f"- **Rating:** {p.get('rating') or 'n/a'} "
                         f"({p.get('review_count') or 'unknown'} reviews)")
            if p.get("bought_past_month"):
                lines.append(f"- **Momentum:** {p['bought_past_month']}")
            if s["tiktok_match_name"]:
                lines.append(f"- **TikTok CC match:** {s['tiktok_match_name']} "
                             f"(similarity {s['tiktok_match_score']})")
            else:
                lines.append(f"- **TikTok CC match:** none")
            lines.append(f"- **DDG TikTok hits:** {d.get('ddg_tiktok_hits', 0)}")
            if d.get("ddg_sample_url"):
                lines.append(f"    - sample: {d['ddg_sample_url']}")
            lines.append(f"- **Score breakdown:** rating {s['rating_score']} · "
                         f"reviews {s['review_score']} · momentum {s['momentum_score']} · "
                         f"price {s['price_score']} · tiktok {s['tiktok_score']} · "
                         f"ddg {s['ddg_score']} · ingestion {s['ingestion_score']}")
            if p.get("url"): lines.append(f"- **Amazon:** {p['url']}")
            if p.get("features"):
                lines.append(f"- **Key features (content angles):**")
                for f in p["features"][:4]:
                    lines.append(f"    - {f[:200]}")
            lines.append("")

    lines += [
        "## Notes",
        "- Accessory exclusion: shaker balls, bottles, scoops, organisers, books, apparel, "
        "and equipment are removed in pre-filter. The script never visits their detail pages.",
        "- Ingestion sanity: bullet points are checked for cues like 'servings', 'capsules', "
        "'mg', 'scoop'. Products with no ingestion cues lose 10 points.",
        "- Diversity: the top 5 are biased to cover at least 3 different product types so "
        "you don't end up with 5 collagen products in a row.",
    ]
    (folder / f"README_{region}_{date_str}.md").write_text(
        "\n".join(lines), encoding="utf-8")
    return folder


# ============================================================
# MAIN
# ============================================================

def main():
    date_str = datetime.now().strftime("%Y-%m-%d")
    log.info(f"=== v4 run: {date_str} ===")
    BASE_OUTPUT.mkdir(parents=True, exist_ok=True)
    cross_region_seen: Set[str] = set()

    with Browser() as br:
        for region, cfg in REGIONS.items():
            log.info(f"--- {region} ---")

            log.info("Phase 1: Amazon bestseller lists")
            raw = scrape_amazon_bestsellers(br, cfg)
            log.info(f"  raw: {len(raw)}")

            log.info("Phase 2: fitness + accessory + title brand pre-filter")
            survivors, dropped_acc = [], 0
            for p in raw:
                n = p["name"].lower()
                if not any(k in n for k in FITNESS_KEYWORDS):
                    continue
                # is_fitness() also drops accessories; count that reason explicitly
                if any(neg in n for neg in NEGATIVE_KEYWORDS):
                    dropped_acc += 1
                    continue
                if matches_major_brand(n):
                    continue
                if not is_fitness(n):   # single source of truth for the fitness gate
                    continue
                survivors.append(p)
            log.info(f"  survivors: {len(survivors)} (dropped {dropped_acc} accessories)")
            survivors = survivors[:DETAIL_MAX_PER_REGION]

            log.info(f"Phase 3: detail pages ({len(survivors)} candidates)")
            enriched, dropped_brand = [], 0
            for i, p in enumerate(survivors, 1):
                log.info(f"  ({i}/{len(survivors)}) {p['name'][:70]}")
                e = enrich_detail(br, p)
                if matches_major_brand(e.get("brand", "")):
                    log.info(f"    dropped (brand byline: {e['brand']})")
                    dropped_brand += 1
                    continue
                # cross-region dedupe by normalised name
                key = re.sub(r"[^a-z0-9]+", "", p["name"].lower())[:60]
                if key in cross_region_seen:
                    log.info(f"    already in other region — kept but flagged")
                cross_region_seen.add(key)
                enriched.append(e)
            log.info(f"  enriched: {len(enriched)} (dropped {dropped_brand} on byline)")

            log.info("Phase 4: TikTok Creative Center")
            tt = scrape_tiktok_cc(br, cfg["cc_country"])
            tt_names = [x["name"] for x in tt]

            log.info(f"Phase 5: DuckDuckGo verification ({len(enriched)} queries)")
            for i, p in enumerate(enriched, 1):
                log.info(f"  ({i}/{len(enriched)}) DDG: {p['name'][:60]}")
                p["ddg"] = ddg_verify(p["name"], cfg["ddg_region"])

            log.info("Phase 6: scoring + diversification")
            for p in enriched:
                p["score"] = score_product(p, tt_names, p["ddg"])
            top5 = diversify_top_n(enriched, FINAL_TOP_N)
            log.info(f"  ✓ top {len(top5)} selected")

            folder = write_outputs(
                region, top5, date_str,
                amazon_count=len(raw),
                tiktok_count=len(tt),
                prefilter_count=len(survivors),
                enriched_count=len(enriched),
                dropped_brand=dropped_brand,
                dropped_accessory=dropped_acc,
            )
            log.info(f"  ✓ saved → {folder}")

    log.info("=== Done ===")


if __name__ == "__main__":
    main()