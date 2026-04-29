"""Supabase database integration for persistent storage."""

import os
import logging
from datetime import datetime

try:
    from supabase import create_client, Client
except ImportError:
    create_client = None
    Client = None

from app.models import LeadResult

logger = logging.getLogger(__name__)

_client = None
_init_attempted = False


def get_client():
    """Get or create a Supabase client."""
    global _client, _init_attempted

    if _init_attempted:
        return _client

    if create_client is None:
        _init_attempted = True
        logger.warning("supabase package not installed. Database disabled.")
        return None

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")

    if not url or not key:
        _init_attempted = True
        logger.warning("SUPABASE_URL or SUPABASE_KEY not set. Database disabled.")
        return None

    try:
        _client = create_client(url, key)
        _init_attempted = True
        logger.info("Supabase client connected.")
        return _client
    except Exception as e:
        # Don't set _init_attempted — allow retry on transient failures
        logger.error(f"Failed to connect to Supabase: {e}")
        return None


# ─── Task Management ────────────────────────────────────────────────

async def create_task(job_id: str, search_term: str, zip_codes: str, industry: str = "") -> bool:
    """Create a new scraping task in the database."""
    client = get_client()
    if not client:
        return False

    try:
        client.table("scraping_tasks").insert({
            "job_id": job_id,
            "search_term": search_term,
            "zip_codes": zip_codes,
            "status": "Running",
            "industry": industry or search_term,
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Failed to create task {job_id}: {e}")
        return False


async def update_task_status(job_id: str, status: str, scraped_count: int = 0, total_results: int = 0) -> bool:
    """Update a task's status and counts."""
    client = get_client()
    if not client:
        return False

    try:
        update_data = {
            "status": status,
            "scraped_count": scraped_count,
            "total_results": total_results,
        }
        if status in ("Completed", "Failed", "Stopped"):
            update_data["completed_at"] = datetime.utcnow().isoformat()

        client.table("scraping_tasks").update(update_data).eq("job_id", job_id).execute()
        return True
    except Exception as e:
        logger.error(f"Failed to update task {job_id}: {e}")
        return False


async def get_all_tasks() -> list[dict]:
    """Get all scraping tasks, newest first."""
    client = get_client()
    if not client:
        return []

    try:
        result = client.table("scraping_tasks").select("*").order("created_at", desc=True).execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Failed to fetch tasks: {e}")
        return []


async def delete_task(job_id: str) -> bool:
    """Delete a task and all its associated business data (CASCADE)."""
    client = get_client()
    if not client:
        return False

    try:
        client.table("scraping_tasks").delete().eq("job_id", job_id).execute()
        return True
    except Exception as e:
        logger.error(f"Failed to delete task {job_id}: {e}")
        return False


# ─── Business Data Management ───────────────────────────────────────

async def save_lead(lead: LeadResult, task_id: str) -> bool:
    """Save a lead to the database. Skips duplicates (name+address)."""
    client = get_client()
    if not client:
        return False

    try:
        data = {
            "task_id": task_id,
            "name": lead.name,
            "address": lead.address,
            "phone": lead.phone,
            "website": lead.website,
            "final_email": lead.final_email,
            "all_website_emails": lead.all_website_emails,
            "website_email": lead.website_email,
            "facebook_email": lead.facebook_email,
            "instagram_email": lead.instagram_email,
            "google_maps_email": lead.google_maps_email,
            "comparing_emails": lead.comparing_emails,
            "email_source": lead.email_source,
            "facebook_link": lead.facebook_link,
            "instagram_link": lead.instagram_link,
            "twitter_link": lead.twitter_link,
            "linkedin_link": lead.linkedin_link,
            "maps_url": lead.maps_url,
            "place_id": lead.place_id,
            "closure_status": lead.closure_status,
            "status": lead.status,
            "rating": lead.rating,
            "reviews_count": lead.reviews_count,
            "price_range": lead.price_range,
            "category": lead.category,
            "cuisine_types": lead.cuisine_types,
            "opening_hours": lead.opening_hours,
            "has_pos": lead.has_pos,
            "pos_system": lead.pos_system,
            "pos_details": lead.pos_details,
            "delivery_services": lead.delivery_services,
            "website_type": lead.website_type,
            "storefront": lead.storefront,
            "search_query": lead.search_query,
            "zipcode": lead.zipcode,
            "city": lead.city,
            "state": lead.state,
            "country": lead.country,
            "date": lead.date,
        }

        # Use upsert to handle duplicates gracefully (name+address unique constraint)
        client.table("business_data").upsert(
            data, on_conflict="name,address"
        ).execute()
        return True
    except Exception as e:
        logger.error(f"Failed to save lead {lead.name}: {e}")
        return False


async def save_leads_batch(leads: list[LeadResult], task_id: str) -> int:
    """Save multiple leads to the database. Returns count of saved leads."""
    saved = 0
    for lead in leads:
        if lead.name:
            ok = await save_lead(lead, task_id)
            if ok:
                saved += 1
    return saved


async def is_duplicate(name: str, address: str) -> bool:
    """Check if a business already exists in the database."""
    client = get_client()
    if not client:
        return False

    if not name:
        return False

    try:
        query = client.table("business_data").select("id").eq("name", name)
        if address:
            query = query.eq("address", address)
        result = query.limit(1).execute()
        return len(result.data) > 0
    except Exception as e:
        logger.debug(f"Duplicate check failed for {name}: {e}")
        return False


async def get_existing_businesses(names_addresses: list[tuple[str, str]]) -> set[str]:
    """
    Batch check which businesses already exist in the DB.
    Returns set of "name|address" keys that already exist.
    """
    client = get_client()
    if not client:
        return set()

    existing = set()
    try:
        # Get all names we're checking
        names = [na[0] for na in names_addresses if na[0]]
        if not names:
            return existing

        result = client.table("business_data").select("name,address").in_("name", names).execute()
        for row in result.data:
            key = f"{row['name']}|{row.get('address', '')}"
            existing.add(key)
    except Exception as e:
        logger.debug(f"Batch duplicate check failed: {e}")

    return existing


# ─── Query & Filter ─────────────────────────────────────────────────

async def get_task_results(job_id: str) -> list[dict]:
    """Get all business data for a specific task."""
    client = get_client()
    if not client:
        return []

    try:
        result = client.table("business_data").select("*").eq("task_id", job_id).execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Failed to fetch results for task {job_id}: {e}")
        return []


async def get_all_business_data(industry: str = "", limit: int = 5000) -> list[dict]:
    """Get all business data, optionally filtered by industry/search_query."""
    client = get_client()
    if not client:
        return []

    try:
        query = client.table("business_data").select("*")
        if industry:
            query = query.ilike("search_query", f"%{industry}%")
        result = query.order("created_at", desc=True).limit(limit).execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Failed to fetch business data: {e}")
        return []


async def get_industries() -> list[str]:
    """Get a list of unique industries/search queries in the database."""
    client = get_client()
    if not client:
        return []

    try:
        result = client.table("business_data").select("search_query").execute()
        industries = set()
        for row in result.data:
            if row.get("search_query"):
                industries.add(row["search_query"])
        return sorted(industries)
    except Exception as e:
        logger.error(f"Failed to fetch industries: {e}")
        return []


async def get_stats() -> dict:
    """Get overall database statistics."""
    client = get_client()
    if not client:
        return {"total_businesses": 0, "total_emails": 0, "total_tasks": 0}

    try:
        biz = client.table("business_data").select("id,final_email", count="exact").execute()
        tasks = client.table("scraping_tasks").select("id", count="exact").execute()

        total_biz = biz.count if biz.count else len(biz.data)
        emails_count = sum(1 for row in biz.data if row.get("final_email"))
        total_tasks = tasks.count if tasks.count else len(tasks.data)

        return {
            "total_businesses": total_biz,
            "total_emails": emails_count,
            "total_tasks": total_tasks,
        }
    except Exception as e:
        logger.error(f"Failed to fetch stats: {e}")
        return {"total_businesses": 0, "total_emails": 0, "total_tasks": 0}


async def delete_all_data() -> bool:
    """Delete ALL business data and tasks. Use with caution."""
    client = get_client()
    if not client:
        return False

    try:
        client.table("business_data").delete().neq("id", 0).execute()
        client.table("scraping_tasks").delete().neq("id", 0).execute()
        return True
    except Exception as e:
        logger.error(f"Failed to delete all data: {e}")
        return False
