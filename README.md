# 🛍️ TikTok Product Hunter

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://github.com/Kamran5H/TikTokProductHunter)
[![Playwright](https://img.shields.io/badge/Playwright-Automated%20Browser-2EAD33?style=for-the-badge&logo=playwright&logoColor=white)](https://playwright.dev/)
[![Anti-Bot](https://img.shields.io/badge/Stealth-Anti--Detection%20Patched-FF4500?style=for-the-badge)](https://github.com/Kamran5H/TikTokProductHunter)
[![E-Commerce](https://img.shields.io/badge/Target-Amazon%20%E2%86%92%20TikTok%20Shop-000000?style=for-the-badge&logo=tiktok&logoColor=white)](https://github.com/Kamran5H/TikTokProductHunter)

**High-velocity automated product discovery engine bridging Amazon bestsellers with TikTok Shop viral traction.**

[Key Features](#-key-features) • [Architecture](#-system-architecture) • [Installation](#-installation) • [Usage](#-usage-guide) • [License](#-license)

</div>

---

## 🌟 Executive Overview

**TikTok Product Hunter** is an enterprise-grade product research automation suite engineered in Python. It systematically scrapes trending and top-selling catalogs on Amazon, extracts verified product titles, brand entities, and ASINs, and cross-references them in real time across **TikTok Shop** to detect viral momentum, pricing spreads, and arbitrage opportunities.

Powered by **Playwright headless/headed automation**, the suite implements browser-fingerprint evasion, realistic human scroll jitter, random delay distributions, and automated session caching to bypass aggressive bot mitigations.

---

## 🚀 Key Features

- **🔄 Cross-Platform Pipeline**: Automatically queries Amazon bestsellers and checks viral velocity across TikTok Shop trending creators and storefronts.
- **🛡️ Advanced Anti-Detection Protocols**: Patched navigator flags (`navigator.webdriver`), randomized viewport dimensions, and human-like cursor movements.
- **📊 Granular Product Extraction**:
  - Amazon Title, ASIN, Price, Rating, and Review Velocity
  - TikTok Shop matching listings, sold units count, and seller ratings
  - Arbitrage spread and estimated gross margin calculation
- **⚡ One-Click Windows Launcher**: Pre-configured `run_tiktok_hunter.bat` for turnkey execution without touching the command line.
- **💾 Structured Data Output**: Exports clean, deduplicated datasets directly into structured JSON and CSV formats for immediate spreadsheet analysis.

---

## 🏗️ System Architecture

```mermaid
flowchart TD
    A[Amazon Best Sellers / Trending Categories] -->|Playwright Headless Extractor| B(Product Normalizer & ASIN Resolver)
    B --> C{Matching Engine}
    C -->|Search Query Synthesis| D[TikTok Shop Live Search API / DOM Reader]
    D -->|Extract Sold Counts & Viral Signals| E[Arbitrage Scoring Filter]
    E --> F[(Output: CSV / JSON Reports)]
    E --> G[Interactive Terminal & Windows GUI]
```

---

## 📁 Repository Structure

```text
TikTokProductHunter/
├── tiktok_product_hunter.py    # Primary scraper engine & cross-platform matching pipeline
├── TikTok Shop confirm.py      # Confirmation verifier & detail extraction script
├── run_tiktok_hunter.bat       # Windows turnkey launcher script
├── tiktok_hunter.ico           # Application icon
├── .gitignore                  # Python & Playwright artifacts exclusion rules
└── LICENSE                     # Open-source MIT License
```

---

## ⚡ Installation

### Prerequisites
- Python 3.10 or higher
- Windows 10/11 or modern Linux/macOS

### Setup
```bash
# Clone the repository
git clone https://github.com/Kamran5H/TikTokProductHunter.git
cd TikTokProductHunter

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install playwright pandas requests
playwright install chromium
```

---

## 🖥️ Usage Guide

### 1. Interactive Windows Run
Simply double-click [`run_tiktok_hunter.bat`](run_tiktok_hunter.bat) to launch the hunter with default parameters.

### 2. Command-Line Execution
```bash
# Run full discovery pipeline
python tiktok_product_hunter.py

# Verify and validate specific TikTok Shop matches
python "TikTok Shop confirm.py"
```

---

## 📜 License

This project is open-source and released under the [MIT License](LICENSE).  
Copyright (c) 2024-2026 **Kamran Ashraf**.

---

<div align="center">

<a href="https://github.com/Kamran5H">
  <img src="https://raw.githubusercontent.com/Kamran5H/Kamran5H/main/assets/kamran-developer-badge.png" width="85" height="85" style="border-radius: 50%; border: 2px solid #00F2FE; box-shadow: 0 4px 14px rgba(0,0,0,0.18);" alt="Developer: Kamran Ashraf" />
</a>

<br/>

### 👨‍💻 **Developer:** [Kamran Ashraf](https://github.com/Kamran5H)
*AI Systems Architect • Computational Physical Chemist • Automation Pioneer*

[![Developer Profile](https://img.shields.io/badge/GitHub-Kamran5H-181717?style=flat-square&logo=github&logoColor=white)](https://github.com/Kamran5H)
[![Developer Portfolio](https://img.shields.io/badge/Portfolio-Visit%20Website-00F2FE?style=flat-square&logo=vercel&logoColor=black)](https://kamranashraf.is-a.dev)
[![Contact Email](https://img.shields.io/badge/Email-chkamran32b%40gmail.com-EA4335?style=flat-square&logo=gmail&logoColor=white)](mailto:chkamran32b@gmail.com)

</div>

