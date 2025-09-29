#!/usr/bin/env python3
"""
car_finder.py
Simple modular crawler to find car listings across multiple public listing sites.
Designed for personal use and demos. Respects robots.txt and avoids heavy scraping.
"""

import time
import argparse
import re
from urllib.parse import urlencode, urljoin, urlparse, parse_qs
import requests
from bs4 import BeautifulSoup
import pandas as pd
from tqdm import tqdm
import robotsparser as rp  # pip package name: python-robots-parser
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")
USER_AGENT = "CarFinderBot/1.0 (+https://example.com/contact) - personal use - polite"

# ---------------------------
# Utility functions
# ---------------------------
def polite_get(url, session=None, delay=1.0, timeout=12):
    """GET respecting a short delay and user-agent."""
    if session is None:
        session = requests.Session()
    headers = {"User-Agent": USER_AGENT}
    time.sleep(delay)
    resp = session.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp

def check_allowed(url):
    """Check robots.txt for the site root."""
    parsed = urlparse(url)
    root = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        rp_parser = rp.Robots.fetch(root, timeout=5)
        return rp_parser.allowed(url, USER_AGENT)
    except Exception:
        # If robots unreachable, be conservative and allow but log
        logging.warning(f"Could not fetch robots.txt for {root} — proceeding carefully.")
        return True

# ---------------------------
# Base scraper
# ---------------------------
class BaseScraper:
    def __init__(self, delay=1.2, session=None):
        self.delay = delay
        self.session = session or requests.Session()

    def allowed(self, url):
        return check_allowed(url)

    def search(self, **params):
        """Return iterator/list of listing dicts: {'title','price','mileage','location','url','source'}"""
        raise NotImplementedError

# ---------------------------
# eBay Motors scraper
# ---------------------------
class EBayMotorsScraper(BaseScraper):
    """Scrape eBay Motors search results (public search pages).
    Good quick source for many used cars. Parsing depends on page structure.
    """
    BASE = "https://www.ebay.com/sch/i.html"

    def _build_query_url(self, q, min_price=None, max_price=None, location=None, radius=None, page=1):
        params = {"_nkw": q, "LH_ItemCondition": "3000"}  # used items condition filter optional
        if min_price:
            params["_udlo"] = min_price
        if max_price:
            params["_udhi"] = max_price
        # pagination
        if page and page > 1:
            params["_pgn"] = page
        return f"{self.BASE}?{urlencode(params)}"

    def parse_listing_card(self, card):
        # eBay search result card parsing
        title_el = card.select_one(".s-item__title")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        url_el = card.select_one(".s-item__link")
        url = url_el.get("href") if url_el else None
        price_el = card.select_one(".s-item__price")
        price = price_el.get_text(strip=True) if price_el else None
        # mileage and location sometimes in subtitle
        subtitle = card.select_one(".s-item__subtitle")
        mileage = None
        location = None
        if subtitle:
            txt = subtitle.get_text(" ", strip=True)
            m = re.search(r"([\d,]+)\s*miles", txt)
            if m:
                mileage = m.group(1).replace(",", "")
            # sometimes location appears
            loc = re.search(r"from\s+([A-Za-z ,]+)", txt)
            if loc:
                location = loc.group(1).strip()
        return {"title": title, "price": price, "mileage": mileage, "location": location, "url": url, "source": "ebay"}

    def search(self, q, min_price=None, max_price=None, pages=2, **kwargs):
        results = []
        for p in range(1, pages + 1):
            url = self._build_query_url(q, min_price, max_price, page=p)
            logging.info(f"[eBay] GET {url}")
            if not self.allowed(url):
                logging.warning("robots.txt disallows scraping this URL. Skipping.")
                break
            resp = polite_get(url, session=self.session, delay=self.delay)
            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select(".s-item")
            for c in cards:
                item = self.parse_listing_card(c)
                if item:
                    results.append(item)
        return results

# ---------------------------
# SerpAPI / search-engine connector
# ---------------------------
class SerpAPIScraper(BaseScraper):
    """
    Uses SerpAPI (google-search-results) to get result links for queries, then tries to fetch each link and extract meta info.
    You will need SERPAPI_API_KEY environment var or pass key.
    """
    def __init__(self, api_key, delay=1.0, session=None):
        super().__init__(delay, session=session)
        self.api_key = api_key

    def search(self, q, num=20):
        from google_search_results import GoogleSearch
        params = {"q": q, "num": num, "api_key": self.api_key}
        gs = GoogleSearch(params)
        logging.info("[SerpAPI] querying search engine...")
        res = gs.get_dict()
        results = []
        for r in res.get("organic_results", []):
            link = r.get("link")
            title = r.get("title")
            snippet = r.get("snippet")
            if not link:
                continue
            if not self.allowed(link):
                continue
            try:
                resp = polite_get(link, session=self.session, delay=self.delay)
                page = BeautifulSoup(resp.text, "html.parser")
                # simple metadata extraction
                price = None
                # many sites include og:price:amount etc
                og_price = page.select_one('meta[property="product:price:amount"], meta[property="og:price:amount"]')
                if og_price:
                    price = og_price.get("content")
                results.append({"title": title, "price": price, "mileage": None, "location": None, "url": link, "source": "serp"})
            except Exception as e:
                logging.warning(f"Couldn't fetch {link}: {e}")
        return results

# ---------------------------
# Generic function to run multiple scrapers
# ---------------------------
def find_cars(params, max_results=200):
    q_parts = []
    if params.get("make"):
        q_parts.append(params["make"])
    if params.get("model"):
        q_parts.append(params["model"])
    if params.get("keywords"):
        q_parts.append(params["keywords"])
    q = " ".join(q_parts) if q_parts else "used car"
    if params.get("zip"):
        q = f"{q} {params['zip']}"
    logging.info(f"Search query: {q}")

    session = requests.Session()
    scrapers = []

    # add eBay scraper by default
    scrapers.append(EBayMotorsScraper(delay=1.0, session=session))

    # optionally add serpapi if key provided
    if params.get("serpapi_key"):
        scrapers.append(SerpAPIScraper(api_key=params["serpapi_key"], delay=1.0, session=session))

    all_results = []
    for s in scrapers:
        try:
            found = s.search(q=q, min_price=params.get("min_price"), max_price=params.get("max_price"), pages=2)
            all_results.extend(found)
            if len(all_results) >= max_results:
                break
        except Exception as e:
            logging.warning(f"Scraper {s.__class__.__name__} failed: {e}")

    # simple normalization
    df = pd.DataFrame(all_results)
    # try to parse price numeric
    def parse_price(p):
        if p is None: return None
        s = re.sub(r"[^\d.]", "", str(p))
        try:
            return float(s) if s else None
        except:
            return None
    if not df.empty:
        df["price_num"] = df["price"].apply(parse_price)
        df = df.drop_duplicates(subset=["url"]).reset_index(drop=True)
    return df

# ---------------------------
# CLI
# ---------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", help="Zip or city to include in the query (e.g. Dallas, TX or 75201)", default="Dallas TX")
    parser.add_argument("--make", help="Make (Toyota, Honda, etc.)", default=None)
    parser.add_argument("--model", help="Model (Camry, Civic, etc.)", default=None)
    parser.add_argument("--min_price", type=int, default=None)
    parser.add_argument("--max_price", type=int, default=None)
    parser.add_argument("--keywords", default="")
    parser.add_argument("--max_results", type=int, default=200)
    parser.add_argument("--serpapi_key", default=None, help="Optional SerpAPI key for broader web search results")
    args = parser.parse_args()

    params = vars(args)
    df = find_cars(params, max_results=args.max_results)

    if df.empty:
        print("No results found. Try broader keywords or add additional source scrapers.")
    else:
        print(df[["title","price","mileage","location","url","source"]].to_string(index=False))
        out_csv = f"car_results_{int(time.time())}.csv"
        df.to_csv(out_csv, index=False)
        print(f"\nSaved {len(df)} rows to {out_csv}")

if __name__ == "__main__":
    main()
