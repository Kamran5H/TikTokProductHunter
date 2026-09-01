# TikTok Product Hunter

Discovers trending products by bridging **Amazon and TikTok Shop** — find what's selling on Amazon, then check its traction on TikTok for sourcing and trend research.

## Features

- Playwright-driven scraper (real browser, resilient to basic bot checks)
- Amazon → TikTok Shop product matching
- One-click launcher for Windows (`run_tiktok_hunter.bat`)

## Run

```bash
pip install playwright
playwright install chromium
python tiktok_product_hunter.py
```

## Files

- `tiktok_product_hunter.py` — main scraper / hunter
- `TikTok Shop confirm.py` — TikTok Shop confirmation step
- `run_tiktok_hunter.bat` — Windows launcher
