"""Lightweight HTTP-based business discovery — no Playwright/Chromium needed.

Used as a fallback when Playwright is not available (e.g., cloud deployments
on platforms without Chromium). Discovers businesses via web search (Brave Search
primary, DuckDuckGo fallback) and extracts contact info from business websites.
"""

import asyncio
import json
import re
import logging
from datetime import datetime
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

import aiohttp
from bs4 import BeautifulSoup

from app.config import REQUEST_TIMEOUT
from app.models import LeadResult

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

# Domains to skip — aggregators, directories, review sites, social media, etc.
_SKIP_DOMAINS = {
    # Search engines & big tech
    "google.com", "google.co", "gstatic.com", "googleapis.com",
    "bing.com", "yahoo.com", "duckduckgo.com", "search.brave.com",
    "youtube.com", "apple.com", "amazon.com", "microsoft.com",
    # Social media
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "linkedin.com", "reddit.com", "pinterest.com", "tiktok.com",
    "threads.net", "snapchat.com",
    # Review & directory sites
    "yelp.com", "tripadvisor.com", "mapquest.com", "foursquare.com",
    "yellowpages.com", "bbb.org", "loc8nearme.com", "chamberofcommerce.com",
    "manta.com", "angi.com", "thumbtack.com", "homeadvisor.com",
    "trustpilot.com", "glassdoor.com", "indeed.com",
    # Food & restaurant aggregators
    "doordash.com", "ubereats.com", "grubhub.com", "seamless.com",
    "postmates.com", "opentable.com", "resy.com", "tock.com",
    "toast.com", "toasttab.com",
    # Media / listicle / guide sites
    "eater.com", "infatuation.com", "gayot.com", "zagat.com",
    "timeout.com", "thrillist.com", "foodandwine.com", "bonappetit.com",
    "nytimes.com", "wsj.com", "newyorker.com", "cntraveler.com",
    "travelandleisure.com", "usatoday.com", "forbes.com",
    "michelin.com", "starchefs.com", "jamesbeard.org",
    "ny.eater.com", "sf.eater.com", "la.eater.com",
    "patch.com", "nextdoor.com", "buzzfeed.com", "tastingtable.com",
    # Wikipedia & reference
    "wikipedia.org", "wikimedia.org", "fandom.com",
    # Booking / travel
    "booking.com", "expedia.com", "hotels.com", "airbnb.com",
    # Generic platforms
    "blogspot.com", "wordpress.com", "medium.com", "substack.com",
    "tumblr.com",
    # Wine/alcohol specific aggregators
    "wine.com", "totalwine.com", "drizly.com", "minibar.com",
    "sanfranciscodrinksguide.com", "vivino.com", "wine-searcher.com",
    # Zip code / map / guide data sites
    "unitedstateszipcodes.org", "zip-codes.com", "city-data.com",
    "niche.com", "areavibes.com", "bestplaces.net",
    "whereyoueat.com", "nyc.com", "menuism.com",
}

# Patterns in titles that indicate listicle/review articles, NOT actual businesses
_LISTICLE_PATTERNS = [
    r"\b\d+\s+best\b",          # "10 Best Restaurants"
    r"\bbest\s+\d+\b",          # "Best 10 Restaurants"
    r"\btop\s+\d+\b",           # "Top 10 Restaurants"
    r"\bbest\s+.*\s+in\b",      # "Best Restaurants in NYC"
    r"\btop\s+.*\s+in\b",       # "Top Restaurants in NYC"
    r"\bnear\s+me\b",           # "Restaurants Near Me"
    r"\bnear\s+you\b",          # "Near You"
    r"\bzip\s*code\b",          # "Restaurants in 10001 zip code"
    r"\b\d{5}\s+[A-Z][a-z]+",   # "10001 Manhattan" (zip code + area name)
    r"\bguide\s+to\b",          # "Guide to..."
    r"\bultimate\s+guide\b",    # "Ultimate Guide"
    r"\bwhere\s+to\s+eat\b",    # "Where to Eat"
    r"\bwhere\s+to\s+drink\b",  # "Where to Drink"
    r"\bmust[\s-]visit\b",      # "Must-visit"
    r"\bmust[\s-]try\b",        # "Must-try"
    r"\branking[s]?\b",         # "Rankings"
    r"\bdelivery\s*&?\s*takeout\b",  # "Food Delivery & Takeout"
    r"\brestaurant\s+guide\b",  # "Restaurant Guide"
]
_LISTICLE_RE = re.compile("|".join(_LISTICLE_PATTERNS), re.IGNORECASE)


def _is_business_url(url: str) -> bool:
    """Check if a URL is likely a direct business website."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        if any(domain == skip or domain.endswith("." + skip) for skip in _SKIP_DOMAINS):
            return False
        # Skip URLs with long paths that look like articles
        path = parsed.path.lower()
        if re.search(r"best[-_]|top[-_]\d|ranking|review|guide|listicle", path):
            return False
        return True
    except Exception:
        return False


def _is_listicle_title(title: str) -> bool:
    """Check if a search result title looks like a listicle/review article."""
    return bool(_LISTICLE_RE.search(title))


def _extract_ddg_url(href: str) -> str:
    """Extract the actual URL from DuckDuckGo's redirect link."""
    if "uddg=" in href:
        parsed = parse_qs(urlparse(href).query)
        if "uddg" in parsed:
            return unquote(parsed["uddg"][0])
    return href


def _clean_business_name(name: str) -> str:
    """Clean up a business name from search result title."""
    suffixes = [
        " - Home", " | Home", " - Official", " | Official",
        " - Yelp", " - TripAdvisor", " - MapQuest",
        " - Updated", " - Last Updated",
        " - Order Online", " | Order Online",
        " - Menu", " | Menu", " - Reservations",
        " - Google Maps", " | Google Maps",
        " - DoorDash", " | DoorDash",
        " - OpenTable", " | OpenTable",
    ]
    for s in suffixes:
        if s.lower() in name.lower():
            idx = name.lower().index(s.lower())
            name = name[:idx]
    # Truncate at common separators if the name is too long
    for sep in [" | ", " - ", " — "]:
        if sep in name and len(name) > 50:
            name = name.split(sep)[0]
    return name.strip()


# ---------------------------------------------------------------------------
# Search Providers
# ---------------------------------------------------------------------------

async def _brave_search(session: aiohttp.ClientSession, query: str) -> list[dict]:
    """Search Brave and return list of {name, url, snippet} dicts."""
    url = f"https://search.brave.com/search?q={quote_plus(query)}"
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    results = []
    try:
        async with session.get(url, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as resp:
            if resp.status != 200:
                logger.warning(f"Brave Search returned status {resp.status}")
                return results
            html = await resp.text()

        soup = BeautifulSoup(html, "lxml")

        for div in soup.select("div.snippet"):
            # Skip FAQ/People Also Ask sections
            if div.get("id") == "faq":
                continue

            title_el = div.select_one(".title")
            url_el = div.select_one("a[href^='http']")
            desc_el = div.select_one(".snippet-description")

            if not title_el or not url_el:
                continue

            name = title_el.get_text(strip=True)
            href = url_el.get("href", "")
            snippet = desc_el.get_text(strip=True) if desc_el else ""

            if name and href.startswith("http"):
                results.append({
                    "name": name,
                    "url": href,
                    "snippet": snippet,
                })

    except Exception as e:
        logger.error(f"Brave Search failed: {e}")

    return results


async def _ddg_search(session: aiohttp.ClientSession, query: str) -> list[dict]:
    """Search DuckDuckGo HTML and return list of {name, url, snippet} dicts."""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    results = []
    try:
        async with session.get(url, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as resp:
            if resp.status != 200:
                logger.debug(f"DuckDuckGo returned status {resp.status}")
                return results
            html = await resp.text()

        soup = BeautifulSoup(html, "lxml")
        for div in soup.select("div.result"):
            title_el = div.select_one("a.result__a")
            snippet_el = div.select_one("a.result__snippet")
            if not title_el:
                continue

            name = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            actual_url = _extract_ddg_url(href)
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""

            if name and actual_url.startswith("http"):
                results.append({
                    "name": name,
                    "url": actual_url,
                    "snippet": snippet,
                })

    except Exception as e:
        logger.error(f"DuckDuckGo search failed: {e}")

    return results


async def _web_search(session: aiohttp.ClientSession, query: str) -> list[dict]:
    """Search the web using Brave (primary) with DuckDuckGo fallback."""
    results = await _brave_search(session, query)
    if results:
        return results
    logger.info("Brave Search returned no results, falling back to DuckDuckGo")
    return await _ddg_search(session, query)


# ---------------------------------------------------------------------------
# Website Scraping
# ---------------------------------------------------------------------------

async def _scrape_business_website(session: aiohttp.ClientSession, url: str) -> dict:
    """Fetch a business website and extract contact info + structured data."""
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    info = {}
    try:
        async with session.get(url, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                               allow_redirects=True) as resp:
            if resp.status != 200:
                return info
            html = await resp.text()

        soup = BeautifulSoup(html, "lxml")

        # Extract from JSON-LD structured data (most reliable)
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("telephone") and not info.get("phone"):
                        info["phone"] = item["telephone"]
                    if item.get("name") and not info.get("name"):
                        info["name"] = item["name"]

                    addr = item.get("address", {})
                    if isinstance(addr, dict) and addr.get("streetAddress") and not info.get("address"):
                        parts = [addr.get("streetAddress", "")]
                        if addr.get("addressLocality"):
                            parts.append(addr["addressLocality"])
                        if addr.get("addressRegion"):
                            parts.append(addr["addressRegion"])
                        if addr.get("postalCode"):
                            parts.append(addr["postalCode"])
                        info["address"] = ", ".join(p for p in parts if p)

                    if item.get("aggregateRating"):
                        rating = item["aggregateRating"]
                        if rating.get("ratingValue"):
                            info["rating"] = str(rating["ratingValue"])
                        if rating.get("reviewCount"):
                            info["reviews_count"] = str(rating["reviewCount"])
            except Exception:
                pass

        # Fallback: extract phone from raw HTML
        if not info.get("phone"):
            phone_match = re.search(r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', html)
            if phone_match:
                info["phone"] = phone_match.group(0)

        # Extract social links
        social_patterns = {
            "facebook": r'https?://(?:www\.)?facebook\.com/[^\s"\'<>]+',
            "instagram": r'https?://(?:www\.)?instagram\.com/[^\s"\'<>]+',
        }
        for key, pattern in social_patterns.items():
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                link = match.group(0).rstrip('/"')
                if "/tr?" not in link and "/sharer" not in link:
                    info[key] = link

    except Exception as e:
        logger.debug(f"Failed to scrape {url}: {e}")

    return info


# ---------------------------------------------------------------------------
# Main Scraping Function
# ---------------------------------------------------------------------------

async def scrape_google_maps(
    search_term: str,
    location: str,
    zipcode: str = "",
    city: str = "",
    state: str = "",
    country: str = "",
    max_results: int = 20,
    progress_callback=None,
) -> list[LeadResult]:
    """
    HTTP-based business discovery — fallback when Playwright is unavailable.

    Strategy:
    1. Search for businesses using Brave Search (primary) / DuckDuckGo (fallback)
    2. Filter out listicle articles, review sites, and aggregators
    3. Enrich remaining business websites with contact info via scraping
    """
    full_location = f"{city} {state} {zipcode}".strip() or location
    logger.info(f"[HTTP mode] Searching: {search_term} in {full_location}")

    connector = aiohttp.TCPConnector(limit=5, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        all_leads: dict[str, LeadResult] = {}

        # Multiple search queries to maximize real business results
        search_queries = [
            f"{search_term} {city} {state} {zipcode}",
            f"{search_term} near {full_location}",
            f"{search_term} {city} {state} menu phone",
        ]

        for sq in search_queries:
            if len(all_leads) >= max_results:
                break

            search_results = await _web_search(session, sq)
            logger.info(f"[HTTP mode] Query '{sq}' returned {len(search_results)} raw results")

            for item in search_results:
                if len(all_leads) >= max_results:
                    break

                url = item["url"]
                title = item["name"]

                # Filter out non-business URLs
                if not _is_business_url(url):
                    logger.debug(f"  SKIP (aggregator): {title[:50]}")
                    continue

                # Filter out listicle/review titles
                if _is_listicle_title(title):
                    logger.debug(f"  SKIP (listicle): {title[:50]}")
                    continue

                name = _clean_business_name(title)

                # Skip if name is too generic or too short
                if len(name) < 3:
                    continue

                if name in all_leads:
                    continue

                lead = LeadResult(
                    date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    search_query=search_term,
                    zipcode=zipcode,
                    city=city,
                    state=state,
                    country=country,
                    name=name,
                    website=url,
                )

                # Extract info from snippet
                snippet = item.get("snippet", "")
                phone_match = re.search(r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', snippet)
                if phone_match:
                    lead.phone = phone_match.group(0)

                all_leads[name] = lead

            await asyncio.sleep(1)  # Rate limit between searches

        # Enrich leads by scraping their websites for contact info
        leads_list = list(all_leads.values())
        logger.info(f"[HTTP mode] Found {len(leads_list)} business websites, enriching...")

        sem = asyncio.Semaphore(5)

        async def _enrich(lead: LeadResult) -> LeadResult:
            async with sem:
                if not lead.website:
                    return lead
                info = await _scrape_business_website(session, lead.website)
                if info.get("phone") and not lead.phone:
                    lead.phone = info["phone"]
                if info.get("address") and not lead.address:
                    lead.address = info["address"]
                # Use JSON-LD name if it's cleaner than the search title
                if info.get("name") and (not lead.name or len(info["name"]) < len(lead.name)):
                    lead.name = info["name"]
                if info.get("rating"):
                    lead.rating = info["rating"]
                if info.get("reviews_count"):
                    lead.reviews_count = info["reviews_count"]
                if info.get("facebook"):
                    lead.facebook_link = info["facebook"]
                if info.get("instagram"):
                    lead.instagram_link = info["instagram"]
                return lead

        enriched = await asyncio.gather(
            *[_enrich(lead) for lead in leads_list],
            return_exceptions=True,
        )

        results = []
        for i, result in enumerate(enriched):
            if isinstance(result, LeadResult):
                results.append(result)
                if progress_callback:
                    await progress_callback(i + 1, len(leads_list))
            elif isinstance(result, Exception):
                logger.error(f"Error enriching lead: {result}")

    logger.info(f"[HTTP mode] Completed. Found {len(results)} businesses for '{search_term}' in {full_location}")
    return results
