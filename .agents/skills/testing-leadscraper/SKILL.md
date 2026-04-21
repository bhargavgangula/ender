# Testing LeadScraper Pro

## Overview
LeadScraper Pro is a FastAPI + Playwright app that scrapes Google Maps for alcohol-related business leads, extracts emails from multiple sources, detects POS systems, and stores results in Supabase. It has two scraping modes: full Playwright (local) and lightweight HTTP fallback (cloud/Fly.io).

## Devin Secrets Needed
- `SUPABASE_URL` — Must be the **API URL** format: `https://<project-id>.supabase.co` (NOT the dashboard URL `https://supabase.com/dashboard/project/...`)
- `SUPABASE_KEY` — Supabase anon or service_role key
- `FLY_API_TOKEN` — (optional) For deploying to Fly.io via `fly deploy`

## Environment Setup

### Local Development
```bash
cd /home/ubuntu/repos/ender
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Starting the Server
```bash
SUPABASE_URL="https://<project-id>.supabase.co" uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Deployed App
The app may be deployed to Fly.io at a URL like `https://leadscraper-pro-<hash>.fly.dev/`.
Check the `fly.toml` file or run `fly status` to find the current deployment URL.

### Supabase Tables
The app requires two tables: `scraping_tasks` and `business_data`. If they don't exist, the app still works but DB features (task history, database explorer, duplicate prevention) will show empty/gracefully degrade. The schema is in `supabase_schema.sql` — the user must run it manually in Supabase SQL Editor.

## Testing Procedure

### 1. Verify App Loads
- Navigate to the app URL (localhost:8000 or Fly.io URL)
- Check all 4 sidebar tabs render: Dashboard, Scraper, Task History, Database
- Verify "Supabase Connected" indicator at bottom of sidebar

### 2. Test Scraping Flow
Use small test parameters to keep scraping fast:
- **Search terms:** `wine stores` (single term)
- **Zip codes:** `94102 San Francisco CA USA` (single zip)
- **Max results:** `3` (local) or `3` (deployed)

**Local (Playwright mode):** ~30-60 seconds. Results appear via polling (async).
**Deployed (HTTP mode):** ~30-50 seconds. Results appear inline after POST completes (sync, no polling).

Expected: 3 results with 2-3 having emails, 1-2 with POS detected.

Good test stores that reliably return emails:
- SF Wine Trading Company → `cameron.anderson@sfwtc.com` (+ multiple team emails)
- San Francisco Wine & Cheese → `sfwineandcheese@gmail.com`
- Flatiron Wines → `help@flatiron-wines.com` (Shopify POS detected)

### 3. Test Export
- Click CSV button after scraping completes
- Verify file downloads with correct name pattern: `leads_{job_id}.csv`
- Toast should say: "Exported N leads as CSV"

### 4. Test Dashboard
- Click Dashboard tab after scraping
- Verify it loads without errors/crashes
- Stats may show 0 if Supabase is not configured (expected graceful degradation)

### 5. Test DB Features (requires Supabase tables)
- After scraping: Task History tab should show the completed task
- Database Explorer should show saved leads with industry filter
- Run same scrape again to test duplicate prevention

## Two Scraping Modes

### Playwright Mode (Local)
- Auto-detected when Chromium binary exists on the system
- Uses Google Maps for business discovery
- Full browser-based email extraction from websites, Facebook, Instagram
- Async: POST /api/scrape returns job_id immediately, poll GET /api/job/{id} for results

### HTTP Mode (Cloud/Fly.io)
- Auto-detected when Chromium is NOT available
- Uses DuckDuckGo HTML search for business discovery (Google blocks non-JS requests)
- aiohttp + BeautifulSoup for website scraping, JSON-LD extraction
- **Sync**: POST /api/scrape runs inline and returns results directly in the response
- UI detects sync completion via `data.status === 'completed'` in response
- No polling needed — results appear immediately when POST completes

## Known Issues & Gotchas

1. **SUPABASE_URL format** — The secret might be stored as the dashboard URL instead of the API URL. Always verify and fix before testing.

2. **Scraping takes time** — Don't panic if the button stays on "Scraping..." for 30-50s. The HTTP scraper enriches each business website which takes time.

3. **Email yield varies** — Not all stores have public emails. 60-100% email yield is typical for wine stores. POS detection count may vary between runs (1-2 out of 3).

4. **Facebook email scraping** — Facebook aggressively blocks non-logged-in scraping. Facebook emails will not be extracted in HTTP mode (or even Playwright mode without login).

5. **Port conflicts** — If port 8000 is already in use, kill the old process with `fuser -k 8000/tcp` before restarting.

6. **Fly.io free tier RAM** — 256MB limit. Chromium (~200MB) cannot fit alongside the app. This is why the HTTP fallback scraper exists. Do NOT try to install/run Chromium on Fly.io free tier.

7. **DuckDuckGo vs Google** — HTTP scraper uses DuckDuckGo because Google heavily blocks non-JS requests (returns CAPTCHAs). DuckDuckGo serves server-rendered HTML.

8. **Multi-machine routing on Fly.io** — Fly.io may route requests to different machines. This is why HTTP mode runs scraping synchronously (inline) instead of async+polling — the GET poll might hit a different machine that doesn't have the job.

9. **Playwright executable_path detection** — `p.chromium.executable_path` returns a path string even if the binary doesn't exist. Must use `os.path.exists()` to verify.

10. **Graceful degradation** — The app works without Supabase, without Chromium, and with partial email extraction. Dashboard stats show 0 when DB is unavailable — this is expected.

## Deploying to Fly.io
```bash
cd /home/ubuntu/repos/ender
fly deploy
```
The `fly.toml` and `Dockerfile` are already configured. After deploy, verify with `fly status` and test the public URL.

## App Architecture
- **Backend:** FastAPI (`app/main.py`) with 14+ API endpoints
- **Frontend:** Single-page HTML (`app/templates/index.html`) with vanilla JS (`app/static/js/app.js`)
- **Scraper (Playwright):** Google Maps scraper (`app/scraper/google_maps.py`)
- **Scraper (HTTP):** DuckDuckGo + aiohttp scraper (`app/scraper/google_maps_http.py`)
- **Orchestrator:** Auto-detects scraper mode (`app/scraper/orchestrator.py`)
- **Email extraction:** aiohttp + Playwright fallback (`app/scraper/email_extractor.py`)
- **POS detection:** Website scanning (`app/scraper/pos_detector.py`)
- **Database:** Supabase client (`app/database.py`) — graceful degradation when unavailable
- **Sync mode:** HTTP scraper returns results directly in POST response
- **Async mode:** Playwright scraper uses background task + polling
