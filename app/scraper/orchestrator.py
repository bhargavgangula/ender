"""Orchestrator for running scrape jobs with parallel processing."""

import asyncio
import logging
import uuid
from datetime import datetime

from app.models import ScrapeRequest, ScrapeJob, LeadResult
from app.scraper.email_extractor import extract_all_emails
from app.scraper.pos_detector import detect_pos_system
from app.config import MAX_CONCURRENT_BROWSERS
from app import database as db

logger = logging.getLogger(__name__)

# Auto-detect Playwright/Chromium availability
_USE_PLAYWRIGHT = False
try:
    import os
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        if os.path.exists(p.chromium.executable_path):
            _USE_PLAYWRIGHT = True
            logger.info("Playwright + Chromium available — using full browser scraping.")
except Exception:
    pass

if _USE_PLAYWRIGHT:
    from app.scraper.google_maps import scrape_google_maps
else:
    from app.scraper.google_maps_http import scrape_google_maps
    logger.info("Playwright not available — using lightweight HTTP scraping.")

# In-memory job storage (for real-time progress tracking)
_jobs: dict[str, ScrapeJob] = {}


def get_job(job_id: str) -> ScrapeJob | None:
    """Get a job by ID."""
    return _jobs.get(job_id)


def get_all_jobs() -> list[ScrapeJob]:
    """Get all jobs."""
    return list(_jobs.values())


async def _enrich_lead(lead: LeadResult) -> LeadResult:
    """Enrich a lead with email extraction and POS detection."""
    try:
        # Extract emails from all sources in parallel
        email_result = await extract_all_emails(
            website_url=lead.website,
            facebook_url=lead.facebook_link,
            instagram_url=lead.instagram_link,
            google_maps_email=lead.google_maps_email,
        )

        # Update lead with email data
        lead.website_email = email_result.get("website_email", "")
        lead.all_website_emails = email_result.get("all_website_emails", "")
        lead.facebook_email = email_result.get("facebook_email", "")
        lead.instagram_email = email_result.get("instagram_email", "")
        lead.final_email = email_result.get("final_email", "")
        lead.comparing_emails = email_result.get("comparing_emails", "")
        lead.email_source = email_result.get("email_source", "")

        # Update social links if found on website
        social = email_result.get("social_links", {})
        if social.get("facebook") and not lead.facebook_link:
            lead.facebook_link = social["facebook"]
        if social.get("instagram") and not lead.instagram_link:
            lead.instagram_link = social["instagram"]
        if social.get("twitter") and not lead.twitter_link:
            lead.twitter_link = social["twitter"]
        if social.get("linkedin") and not lead.linkedin_link:
            lead.linkedin_link = social["linkedin"]

        # If we found Facebook/Instagram from website, try extracting emails again
        if social.get("facebook") and not lead.facebook_email:
            from app.scraper.email_extractor import extract_facebook_email
            fb_emails = await extract_facebook_email(social["facebook"])
            if fb_emails:
                lead.facebook_email = ", ".join(fb_emails)
                if not lead.final_email:
                    lead.final_email = fb_emails[0]
                    lead.email_source = "Facebook"

        # Detect POS system, delivery services, website type, storefront
        pos_result = await detect_pos_system(lead.website)
        lead.has_pos = "Yes" if pos_result["has_pos"] else "No"
        lead.pos_system = pos_result["pos_system"]
        lead.pos_details = pos_result["pos_details"]
        lead.delivery_services = pos_result.get("delivery_services", "")
        lead.website_type = pos_result.get("website_type", "Unknown")
        lead.storefront = pos_result.get("storefront", "No")

    except Exception as e:
        logger.error(f"Error enriching lead {lead.name}: {e}")

    return lead


def _parse_zip_code(zip_line: str) -> dict:
    """Parse a zip code line like '10001 New York NY USA'."""
    parts = zip_line.strip().split()
    result = {"zipcode": "", "city": "", "state": "", "country": "", "location": zip_line.strip()}
    if not parts:
        return result

    result["zipcode"] = parts[0]

    if len(parts) >= 4:
        result["country"] = parts[-1]
        result["state"] = parts[-2]
        result["city"] = " ".join(parts[1:-2])
    elif len(parts) >= 3:
        result["state"] = parts[-1]
        result["city"] = " ".join(parts[1:-1])
    elif len(parts) >= 2:
        result["city"] = " ".join(parts[1:])

    return result


async def run_scrape_job(request: ScrapeRequest) -> str:
    """
    Start a scraping job. Returns the job ID.
    The job runs in the background (Playwright mode) or inline (HTTP mode).
    """
    job_id = str(uuid.uuid4())[:8]
    job = ScrapeJob(
        job_id=job_id,
        status="running",
        total=len(request.search_terms) * len(request.zip_codes),
    )
    _jobs[job_id] = job

    # Save task to Supabase
    search_terms_str = ", ".join(request.search_terms)
    zip_codes_str = ", ".join(request.zip_codes)
    await db.create_task(
        job_id=job_id,
        search_term=search_terms_str,
        zip_codes=zip_codes_str,
        industry=request.search_terms[0] if request.search_terms else "",
    )

    if _USE_PLAYWRIGHT:
        # Playwright is slow (minutes) — run in background
        asyncio.create_task(_execute_job(job, request))
    else:
        # HTTP mode is fast (~5-10s) — run inline so the response
        # contains results (avoids multi-machine routing issues)
        await _execute_job(job, request)

    return job_id


async def _execute_job(job: ScrapeJob, request: ScrapeRequest):
    """Execute the scraping job."""
    try:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_BROWSERS)

        async def _scrape_combination(search_term: str, zip_line: str):
            async with semaphore:
                zip_data = _parse_zip_code(zip_line)

                async def progress_cb(current, total):
                    pass  # Progress tracked at job level

                leads = await scrape_google_maps(
                    search_term=search_term,
                    location=zip_data["location"],
                    zipcode=zip_data["zipcode"],
                    city=zip_data["city"],
                    state=zip_data["state"],
                    country=zip_data["country"],
                    max_results=request.max_results_per_search,
                    progress_callback=progress_cb,
                )

                # Check for duplicates against DB before enriching
                new_leads = []
                for lead in leads:
                    if lead.name:
                        is_dup = await db.is_duplicate(lead.name, lead.address)
                        if is_dup:
                            logger.info(f"  SKIP (duplicate): {lead.name}")
                        else:
                            new_leads.append(lead)

                logger.info(
                    f"New leads: {len(new_leads)} / {len(leads)} "
                    f"(skipped {len(leads) - len(new_leads)} duplicates)"
                )

                # Enrich each NEW lead with emails and POS detection
                enriched_leads = []
                enrich_tasks = [_enrich_lead(lead) for lead in new_leads]
                enriched = await asyncio.gather(*enrich_tasks, return_exceptions=True)
                for result in enriched:
                    if isinstance(result, LeadResult):
                        enriched_leads.append(result)

                # Save to Supabase
                saved = await db.save_leads_batch(enriched_leads, job.job_id)
                logger.info(f"Saved {saved} leads to database for job {job.job_id}")

                job.results.extend(enriched_leads)
                job.completed += 1

                # Update task progress in DB
                await db.update_task_status(
                    job.job_id, "Running",
                    scraped_count=len(job.results),
                    total_results=len(job.results),
                )

        # Create tasks for all combinations
        tasks = []
        for search_term in request.search_terms:
            for zip_code in request.zip_codes:
                if search_term.strip() and zip_code.strip():
                    tasks.append(_scrape_combination(search_term.strip(), zip_code.strip()))

        job.total = len(tasks)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        job.status = "completed"
        await db.update_task_status(
            job.job_id, "Completed",
            scraped_count=len(job.results),
            total_results=len(job.results),
        )
        logger.info(f"Job {job.job_id} completed. Found {len(job.results)} leads.")

    except Exception as e:
        job.status = "failed"
        job.errors.append(str(e))
        await db.update_task_status(job.job_id, "Failed")
        logger.error(f"Job {job.job_id} failed: {e}")
