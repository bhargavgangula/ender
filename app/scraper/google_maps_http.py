"""Lightweight HTTP-based business discovery — no Playwright/Chromium needed.

Used as a fallback when Playwright is not available (e.g., cloud deployments
on platforms without Chromium). Discovers businesses via DuckDuckGo search
and extracts basic info (name, website, address, phone) from business websites.
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

# Domains to skip when looking for business websites
_SKIP_DOMAINS = {
    "google.com", "google.co", "gstatic.com", "googleapis.com",
    "youtube.com", "facebook.com", "instagram.com", "twitter.com",
    "linkedin.com", "yelp.com", "tripadvisor.com", "wikipedia.org",
    "reddit.com", "pinterest.com", "tiktok.com", "x.com",
    "apple.com", "amazon.com", "doordash.com", "ubereats.com",
    "grubhub.com", "seamless.com", "postmates.com", "duckduckgo.com",
    "mapquest.com", "loc8nearme.com", "yellowpages.com", "bbb.org",
    "sanfranciscodrinksguide.com", "wine.com",
}


def _is_business_url(url: str) -> bool:
    """Check if a URL is likely a direct business website."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        return not any(skip in domain for skip in _SKIP_DOMAINS)
    except Exception:
        return False


def _extract_ddg_url(href: str) -> str:
    """Extract the actual URL from DuckDuckGo's redirect link."""
    if "uddg=" in href:
        parsed = parse_qs(urlparse(href).query)
        if "uddg" in parsed:
            return unquote(parsed["uddg"][0])
    return href


def _clean_business_name(name: str) -> str:
    """Clean up a business name from search result title."""
    # Remove common suffixes
    suffixes = [
        " - Home", " | Home", " - Official", " | Official",
        " - Yelp", " - TripAdvisor", " - MapQuest",
        " - Updated", " - Last Updated",
    ]
    for s in suffixes:
        if s in name:
            name = name[:name.index(s)]
    # Truncate at common separators if the name is too long
    for sep in [" | ", " - ", " — "]:
        if sep in name and len(name) > 50:
            name = name.split(sep)[0]
    return name.strip()


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
                logger.warning(f"DuckDuckGo returned status {resp.status}")
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
                # Skip generic/tracking links
                if "/tr?" not in link and "/sharer" not in link:
                    info[key] = link

    except Exception as e:
        logger.debug(f"Failed to scrape {url}: {e}")

    return info


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

    Uses DuckDuckGo search to find businesses, then scrapes their websites
    for contact information. Same interface as google_maps.scrape_google_maps.
    """
    query = f"{search_term} {location}".strip()
    logger.info(f"[HTTP mode] Searching: {query}")

    connector = aiohttp.TCPConnector(limit=5, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        # Search with different query variations to find business websites
        all_leads: dict[str, LeadResult] = {}

        search_queries = [
            f"{search_term} {city} {state} {zipcode}",
            f"{search_term} near {location}",
        ]

        for sq in search_queries:
            if len(all_leads) >= max_results:
                break

            ddg_results = await _ddg_search(session, sq)

            for item in ddg_results:
                if len(all_leads) >= max_results:
                    break

                url = item["url"]
                name = _clean_business_name(item["name"])

                # Only keep direct business websites
                if not _is_business_url(url):
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

                # Extract info from snippet (phone, address)
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
                if info.get("name") and not lead.name:
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

    logger.info(f"[HTTP mode] Completed. Found {len(results)} businesses for '{query}'")
    return results
