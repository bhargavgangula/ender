"""Deep email extraction from websites, Facebook, Instagram — rebuilt with proven techniques."""

import asyncio
import re
import logging
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from app.config import REQUEST_TIMEOUT, MAX_CONCURRENT_REQUESTS

logger = logging.getLogger(__name__)

# Check if Playwright + Chromium is available at module level
_HAS_PLAYWRIGHT = False
try:
    import os as _os
    from playwright.sync_api import sync_playwright as _sync_pw
    with _sync_pw() as _p:
        if _os.path.exists(_p.chromium.executable_path):
            _HAS_PLAYWRIGHT = True
except Exception:
    pass

EMAIL_REGEX = re.compile(
    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
)

# Invalid extensions that appear in false-positive emails
INVALID_EXTENSIONS = [
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.pdf',
    '.css', '.js', '.ico', '.txt', '.xml', '.woff', '.ttf',
]

# Domains to exclude from email results
INVALID_DOMAINS = [
    'sentry.io', 'example.com', 'test.com', 'localhost', 'w3.org',
    'schema.org', 'google.com', 'gstatic.com', 'facebook.com', 'fb.com',
    'instagram.com', 'twitter.com', 'x.com', 'linkedin.com', 'youtube.com',
    'maps.google.com', 'purl.org', 'googletagmanager.com', 'googleapis.com',
    'cloudflare.com', 'jquery.com', 'wordpress.org', 'gravatar.com',
    'wixpress.com', 'android.com', 'apple.com', 'microsoft.com',
]

# Keywords that indicate junk/system emails
INVALID_KEYWORDS = [
    'noreply', 'no-reply', 'donotreply', 'mailer-daemon', 'postmaster',
    'webmaster', 'abuse', 'spam', 'privacy', 'domain.com', 'email.com',
    'support@example', 'info@example', 'contact@example',
]

# Contact page keywords to look for
CONTACT_PAGE_KEYWORDS = [
    "contact", "about", "about-us", "contact-us", "get-in-touch",
    "reach-us", "info", "support", "help",
]


def find_emails(html: str) -> list[str]:
    """
    Advanced email extraction with obfuscation handling.
    Handles [at], (at), HTML entities, and other common obfuscation patterns.
    """
    if not html:
        return []

    # Normalize obfuscated patterns
    html_lower = html.lower()
    html_lower = html_lower.replace('[at]', '@').replace('(at)', '@')
    html_lower = html_lower.replace('[dot]', '.').replace('(dot)', '.')
    html_lower = html_lower.replace(' at ', '@').replace(' dot ', '.')

    # Extract emails using regex
    emails = EMAIL_REGEX.findall(html_lower)

    # Handle HTML entity obfuscation (e.g., &#105;&#110;&#102;&#111;)
    html_entities = re.findall(r'&#(\d+);', html)
    if html_entities:
        try:
            decoded = "".join([chr(int(e)) for e in html_entities])
            emails.extend(EMAIL_REGEX.findall(decoded))
        except (ValueError, OverflowError):
            pass

    # Also check mailto links in original HTML
    mailto_matches = re.findall(r'mailto:([^"\'?\s<>]+)', html, re.IGNORECASE)
    for m in mailto_matches:
        email = m.split('?')[0].strip()
        if EMAIL_REGEX.match(email.lower()):
            emails.append(email.lower())

    # Filter and validate
    valid_emails = []
    seen = set()
    for e in emails:
        e = e.strip().lower().rstrip('.')
        if e in seen:
            continue
        if e.count('@') != 1:
            continue
        if any(ext in e for ext in INVALID_EXTENSIONS):
            continue

        try:
            local, domain = e.split('@')
            if len(local) < 2 or len(domain) < 4:
                continue
            if any(kw in local for kw in INVALID_KEYWORDS):
                continue
            if any(d in domain for d in INVALID_DOMAINS):
                continue
            if '.' not in domain:
                continue
            seen.add(e)
            valid_emails.append(e)
        except Exception:
            continue

    return sorted(set(valid_emails))


def _extract_social_links(html: str) -> dict[str, str]:
    """Extract social media links using advanced regex patterns."""
    socials = {"facebook": "", "instagram": "", "twitter": "", "linkedin": ""}
    if not html:
        return socials

    patterns = {
        "facebook": r'https?://(?:[a-z]{2,3}\.)?facebook\.com/(?:pages/|people/|groups/|profile\.php\?id=)?([^\s"\'<>?&/]+)',
        "instagram": r'https?://(?:www\.)?instagram\.com/([^\s"\'<>?&/]+)',
        "twitter": r'https?://(?:www\.)?(?:twitter|x)\.com/([^\s"\'<>?&/]+)',
        "linkedin": r'https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in|school)/([^\s"\'<>?&/]+)',
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            username = match.group(1)
            if key == "facebook":
                socials[key] = f"https://www.facebook.com/{username}"
            elif key == "instagram":
                socials[key] = f"https://www.instagram.com/{username}"
            elif key == "twitter":
                socials[key] = f"https://twitter.com/{username}"
            elif key == "linkedin":
                socials[key] = f"https://www.linkedin.com/company/{username}"

    return socials


async def _fetch_page(session: aiohttp.ClientSession, url: str) -> str:
    """Fetch a web page and return its HTML content."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ssl=False, allow_redirects=True
        ) as response:
            if response.status == 200:
                return await response.text(errors="replace")
    except Exception as e:
        logger.debug(f"Failed to fetch {url}: {e}")
    return ""


def _find_contact_pages(html: str, base_url: str) -> list[str]:
    """Find links to contact/about pages on the website."""
    pages = []
    if not html:
        return pages

    soup = BeautifulSoup(html, "lxml")
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        text = link.get_text().strip().lower()
        href_lower = href.lower()

        is_contact_page = any(kw in href_lower for kw in CONTACT_PAGE_KEYWORDS) or \
                          any(kw in text for kw in CONTACT_PAGE_KEYWORDS)

        if is_contact_page:
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            base_parsed = urlparse(base_url)
            if parsed.netloc == base_parsed.netloc or not parsed.netloc:
                pages.append(full_url)

    return list(set(pages))[:5]


async def _playwright_scrape_website(url: str) -> tuple[list[str], dict[str, str]]:
    """
    Use Playwright to scrape emails from JS-rendered websites.
    Handles Shopify, React, Angular, and other SPA sites.
    Visits homepage + contact/about pages.
    """
    emails = []
    social_links = {"facebook": "", "instagram": "", "twitter": "", "linkedin": ""}

    if not _HAS_PLAYWRIGHT:
        return emails, social_links

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = await browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36"
                )
            )

            page.set_default_timeout(12000)

            # Visit homepage
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)

            # Scroll to load lazy content
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight/2)")
            await asyncio.sleep(0.5)

            # Extract all data via JS
            data = await page.evaluate("""() => {
                const body = document.body.innerHTML.toLowerCase()
                    .replace(/\\[at\\]/g, '@').replace(/\\(at\\)/g, '@')
                    .replace(/\\[dot\\]/g, '.').replace(/\\(dot\\)/g, '.');

                const emailRegex = /[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}/g;
                const emails = [...new Set(body.match(emailRegex) || [])];

                // Also check mailto links
                document.querySelectorAll('a[href*="mailto:"]').forEach(a => {
                    const email = a.href.replace('mailto:', '').split('?')[0].trim().toLowerCase();
                    if (email && email.includes('@') && !emails.includes(email)) emails.push(email);
                });

                // Social links
                const social = {facebook: '', instagram: '', twitter: '', linkedin: ''};
                document.querySelectorAll('a[href]').forEach(a => {
                    const h = a.href.toLowerCase();
                    if (h.includes('facebook.com/') && !social.facebook) social.facebook = a.href;
                    if (h.includes('instagram.com/') && !social.instagram) social.instagram = a.href;
                    if ((h.includes('twitter.com/') || h.includes('x.com/')) && !social.twitter) social.twitter = a.href;
                    if (h.includes('linkedin.com/') && !social.linkedin) social.linkedin = a.href;
                });

                // Find contact page URLs
                const contactPages = [];
                document.querySelectorAll('a[href]').forEach(a => {
                    const h = (a.href || '').toLowerCase();
                    const t = (a.textContent || '').toLowerCase();
                    if (['contact', 'about'].some(kw => h.includes(kw) || t.includes(kw))) {
                        try {
                            if (new URL(a.href).origin === window.location.origin) {
                                contactPages.push(a.href);
                            }
                        } catch(e) {}
                    }
                });

                return {
                    emails: emails,
                    social: social,
                    contactPages: [...new Set(contactPages)].slice(0, 3)
                };
            }""")

            emails = data.get("emails", [])
            social_links = data.get("social", social_links)

            # Visit contact pages for more emails
            for contact_url in data.get("contactPages", []):
                try:
                    await page.goto(contact_url, wait_until="domcontentloaded", timeout=10000)
                    await asyncio.sleep(1.5)
                    page_emails = await page.evaluate("""() => {
                        const body = document.body.innerHTML.toLowerCase()
                            .replace(/\\[at\\]/g, '@').replace(/\\(at\\)/g, '@')
                            .replace(/\\[dot\\]/g, '.').replace(/\\(dot\\)/g, '.');
                        const emailRegex = /[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}/g;
                        const emails = [...new Set(body.match(emailRegex) || [])];
                        document.querySelectorAll('a[href*="mailto:"]').forEach(a => {
                            const email = a.href.replace('mailto:', '').split('?')[0].trim().toLowerCase();
                            if (email && email.includes('@') && !emails.includes(email)) emails.push(email);
                        });
                        return emails;
                    }""")
                    emails.extend(page_emails)
                except Exception:
                    pass

            await browser.close()
    except Exception as e:
        logger.debug(f"Playwright email extraction failed for {url}: {e}")

    return find_emails(" ".join(emails)), social_links


def get_domain_matched_email(emails: list[str], website_url: str) -> str:
    """Pick the best email — prefer domain-matched, then business email."""
    if not emails:
        return ""
    if not website_url:
        return emails[0]

    try:
        domain = urlparse(website_url).netloc.replace('www.', '').split(':')[0]
        for email in emails:
            if domain in email:
                return email
    except Exception:
        pass

    # Prefer business emails over generic (gmail, yahoo, etc.)
    priority_domains = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com']
    business_emails = [e for e in emails if not any(d in e for d in priority_domains)]
    return business_emails[0] if business_emails else emails[0]


async def extract_website_emails(website_url: str) -> dict:
    """
    Deep email extraction from a business website.
    Phase 1: Fast aiohttp for static HTML.
    Phase 2: Playwright fallback for JS-rendered sites.
    """
    if not website_url:
        return {"emails": [], "social_links": {}}

    if not website_url.startswith("http"):
        website_url = "https://" + website_url

    all_emails = []
    social_links = {}

    # Phase 1: Fast aiohttp extraction
    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT_REQUESTS, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        homepage_html = await _fetch_page(session, website_url)
        if homepage_html:
            all_emails.extend(find_emails(homepage_html))
            social_links = _extract_social_links(homepage_html)

            contact_pages = _find_contact_pages(homepage_html, website_url)
            if contact_pages:
                tasks = [_fetch_page(session, url) for url in contact_pages]
                pages = await asyncio.gather(*tasks, return_exceptions=True)
                for page_html in pages:
                    if isinstance(page_html, str) and page_html:
                        all_emails.extend(find_emails(page_html))
                        page_social = _extract_social_links(page_html)
                        for key, val in page_social.items():
                            if val and not social_links.get(key):
                                social_links[key] = val

    filtered = find_emails(" ".join(all_emails)) if all_emails else []

    # Phase 2: Playwright fallback for JS-rendered sites
    if not filtered:
        logger.info(f"No emails via aiohttp for {website_url}, trying Playwright...")
        pw_emails, pw_social = await _playwright_scrape_website(website_url)
        filtered.extend(pw_emails)
        for key, val in pw_social.items():
            if val and not social_links.get(key):
                social_links[key] = val

    return {
        "emails": find_emails(" ".join(filtered)) if filtered else [],
        "social_links": social_links,
    }


async def extract_facebook_email(facebook_url: str) -> list[str]:
    """
    Extract email from a Facebook business page using Playwright.
    Facebook blocks plain HTTP requests, so we need a real browser.
    Strategy: Use desktop browser → close login popup → scrape page content.
    Tries the main page first (has most data in HTML), then /about variants.
    """
    if not facebook_url:
        return []

    if not _HAS_PLAYWRIGHT:
        return []

    emails = []
    base = facebook_url.rstrip("/")

    # Normalize URL to www.facebook.com (desktop version shows more data)
    parsed_fb = urlparse(base)
    if parsed_fb.netloc and "facebook.com" in parsed_fb.netloc:
        desktop_base = parsed_fb._replace(netloc="www.facebook.com").geturl()
    else:
        desktop_base = base

    # Main page first (contains email in page data), then /about variants
    fb_urls = [
        desktop_base,
        desktop_base + "/about",
        desktop_base + "/about_contact_and_basic_info",
    ]

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            page = await context.new_page()
            page.set_default_timeout(12000)

            for url in fb_urls:
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(3)

                    # Close the login popup that Facebook shows to non-logged-in users
                    try:
                        close_btn = await page.query_selector('[aria-label="Close"]')
                        if close_btn:
                            await close_btn.click()
                            await asyncio.sleep(1)
                            logger.debug(f"Closed Facebook login popup on {url}")
                    except Exception:
                        pass

                    # Scroll down to load more content
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3)")
                    await asyncio.sleep(1)

                    # Extract emails from full rendered page via JS
                    page_data = await page.evaluate("""() => {
                        // Get the full page HTML (includes data in scripts, meta tags, etc.)
                        const fullHtml = document.documentElement.innerHTML.toLowerCase()
                            .replace(/\\[at\\]/g, '@').replace(/\\(at\\)/g, '@')
                            .replace(/\\[dot\\]/g, '.').replace(/\\(dot\\)/g, '.');

                        const emailRegex = /[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}/g;
                        const emails = [...new Set(fullHtml.match(emailRegex) || [])];

                        // Check mailto links
                        document.querySelectorAll('a[href*="mailto:"]').forEach(a => {
                            const email = a.href.replace('mailto:', '').split('?')[0].trim().toLowerCase();
                            if (email && email.includes('@') && !emails.includes(email)) emails.push(email);
                        });

                        // Check text nodes for email patterns
                        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                        while (walker.nextNode()) {
                            const text = walker.currentNode.textContent.toLowerCase()
                                .replace(/\\[at\\]/g, '@').replace(/\\(at\\)/g, '@')
                                .replace(/\\[dot\\]/g, '.').replace(/\\(dot\\)/g, '.');
                            const found = text.match(emailRegex);
                            if (found) found.forEach(e => { if (!emails.includes(e)) emails.push(e); });
                        }

                        return emails;
                    }""")

                    if page_data:
                        found = find_emails(" ".join(page_data))
                        emails.extend(found)
                        if found:
                            logger.info(f"Found Facebook emails from {url}: {found}")
                            break

                except Exception as e:
                    logger.debug(f"Facebook page fetch failed for {url}: {e}")
                    continue

            await browser.close()

    except Exception as e:
        logger.warning(f"Playwright Facebook extraction failed for {facebook_url}: {e}")

    return find_emails(" ".join(emails)) if emails else []


async def extract_instagram_email(instagram_url: str) -> list[str]:
    """
    Extract email from an Instagram profile bio using Playwright.
    Instagram also blocks plain HTTP requests, needs a real browser.
    """
    if not instagram_url:
        return []

    if not _HAS_PLAYWRIGHT:
        return []

    emails = []

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = await browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128.0.0.0 Mobile Safari/537.36"
                ),
            )
            page.set_default_timeout(12000)

            await page.goto(instagram_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)

            # Extract emails from rendered page
            page_emails = await page.evaluate("""() => {
                const body = document.body.innerHTML.toLowerCase()
                    .replace(/\\[at\\]/g, '@').replace(/\\(at\\)/g, '@')
                    .replace(/\\[dot\\]/g, '.').replace(/\\(dot\\)/g, '.');
                const emailRegex = /[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}/g;
                const emails = [...new Set(body.match(emailRegex) || [])];
                document.querySelectorAll('a[href*="mailto:"]').forEach(a => {
                    const email = a.href.replace('mailto:', '').split('?')[0].trim().toLowerCase();
                    if (email && email.includes('@') && !emails.includes(email)) emails.push(email);
                });
                return emails;
            }""")

            if page_emails:
                emails = find_emails(" ".join(page_emails))

            await browser.close()

    except Exception as e:
        logger.debug(f"Playwright Instagram extraction failed for {instagram_url}: {e}")
        # Fallback to aiohttp
        connector = aiohttp.TCPConnector(limit=5, ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            html = await _fetch_page(session, instagram_url)
            if html:
                emails = find_emails(html)

    return emails


async def extract_all_emails(
    website_url: str = "",
    facebook_url: str = "",
    instagram_url: str = "",
    google_maps_email: str = "",
) -> dict:
    """
    Extract emails from all available sources and consolidate.
    Uses domain-matched email selection for best results.
    """
    tasks = [
        extract_website_emails(website_url),
        extract_facebook_email(facebook_url),
        extract_instagram_email(instagram_url),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    website_result = results[0] if not isinstance(results[0], Exception) else {"emails": [], "social_links": {}}
    fb_emails = results[1] if not isinstance(results[1], Exception) else []
    ig_emails = results[2] if not isinstance(results[2], Exception) else []

    website_emails = website_result.get("emails", [])
    social_links = website_result.get("social_links", {})

    # Collect all unique emails
    all_emails = set()
    if google_maps_email:
        all_emails.add(google_maps_email.lower())
    for e in website_emails:
        all_emails.add(e.lower())
    for e in fb_emails:
        all_emails.add(e.lower())
    for e in ig_emails:
        all_emails.add(e.lower())

    all_emails_list = sorted(all_emails)

    # Determine final email using domain-matched selection
    final_email = ""
    source = ""
    if website_emails:
        final_email = get_domain_matched_email(website_emails, website_url)
        source = "Website"
    elif google_maps_email:
        final_email = google_maps_email
        source = "Google Maps"
    elif fb_emails:
        final_email = fb_emails[0]
        source = "Facebook"
    elif ig_emails:
        final_email = ig_emails[0]
        source = "Instagram"

    return {
        "website_email": website_emails[0] if website_emails else "",
        "all_website_emails": ", ".join(website_emails),
        "facebook_email": ", ".join(fb_emails) if fb_emails else "",
        "instagram_email": ", ".join(ig_emails) if ig_emails else "",
        "final_email": final_email,
        "comparing_emails": ", ".join(all_emails_list),
        "email_source": source,
        "social_links": social_links,
    }
