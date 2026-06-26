"""
LeadFlow Railway Backend — Google Places scraper
"""

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt, os, re, time, json as json_lib, requests as req_lib
import imaplib, email as email_lib, threading
from email.header import decode_header
from datetime import datetime, timedelta, timezone
from typing import Optional
from pydantic import BaseModel
from urllib.parse import quote as url_quote

SECRET_KEY      = os.getenv("SECRET_KEY",      "leadflow-secret")
TEAM_PASSWORD   = os.getenv("TEAM_PASSWORD",   "LeadFlow2024")
ADMIN_PASSWORD  = os.getenv("ADMIN_PASSWORD",  "LeadFlowAdmin2024!")
ADMIN_USERS     = set(u.strip().lower() for u in os.getenv("ADMIN_USERS", "eric").split(",") if u.strip())
BLOCKED_USERS   = set(u.strip().lower() for u in os.getenv("BLOCKED_USERS", "").split(",") if u.strip())
ALGORITHM       = "HS256"

# Pipeline / MCP service-to-service auth. When set, requests presenting this
# value as a Bearer token (or X-API-Key header) are treated as an admin-level
# system principal — used by the leadflow-mcp wrapper and other Vision MCP
# ecosystem services that need read-only programmatic access to LeadFlow data.
# Empty/unset → static-key auth disabled (UI JWT auth still works).
PIPELINE_API_KEY = os.getenv("PIPELINE_API_KEY", "").strip()
PIPELINE_PRINCIPAL = "mcp-pipeline"  # username recorded in audit/logs for key-auth callers

SUPABASE_URL  = os.getenv("SUPABASE_URL", os.getenv("VITE_SUPABASE_URL", ""))
SUPABASE_KEY  = os.getenv("SUPABASE_KEY", os.getenv("VITE_SUPABASE_KEY", ""))
# Service role key bypasses RLS — needed for login_log, audit_log, user_sessions
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", SUPABASE_KEY)
GOOGLE_KEY    = os.getenv("GOOGLE_API_KEY", "")
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY", "")
APOLLO_SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
APOLLO_MATCH_URL  = "https://api.apollo.io/api/v1/people/match"
# Random shared secret in the webhook URL path — Apollo's "people" webhook
# doesn't HMAC-sign requests, so URL-as-bearer is the standard auth model.
# Generate with: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# Strip whitespace + trailing '%' (zsh's no-newline indicator) — both are
# common copy-paste artifacts that Apollo's URL parser rejects.
APOLLO_WEBHOOK_SECRET = os.getenv("APOLLO_WEBHOOK_SECRET", "").strip().rstrip("%").strip()
# Phone reveals are 8 credits each — restrict to highest-value verticals only,
# fall through to named-ask + email workflow for everyone else. Monthly cap
# enforces a hard cutoff so we can't blow the 4,000-credit Pro budget.
APOLLO_PHONE_REVEAL_INDUSTRIES = set(
    i.strip().lower() for i in
    os.getenv("APOLLO_PHONE_REVEAL_INDUSTRIES", "healthcare,education").split(",")
    if i.strip()
)
APOLLO_PHONE_REVEAL_MONTHLY_CAP = int(os.getenv("APOLLO_PHONE_REVEAL_MONTHLY_CAP", "400"))

# ── Email campaign integration ────────────────────────────────────────────
# After a caller can't reach a lead by phone, LeadFlow sends a "tried to
# call you" follow-up directly via Resend (same API key + from-address that
# already powers other Vision Cleaning sends, so deliverability matches).
AUTO_CAMPAIGN_FAILED_CALL_THRESHOLD = int(os.getenv("AUTO_CAMPAIGN_FAILED_CALL_THRESHOLD", "2"))
AUTO_CAMPAIGN_AFTER_FAILED_CALL = os.getenv("AUTO_CAMPAIGN_AFTER_FAILED_CALL", "0") == "1"
CAMPAIGN_SUPPRESSION_DAYS = int(os.getenv("CAMPAIGN_SUPPRESSION_DAYS", "7"))
# Resend webhook secret — random token in URL path. Configure the URL
# (with this secret) in Resend dashboard → Webhooks. Auth is URL-as-bearer.
RESEND_WEBHOOK_SECRET = os.getenv("RESEND_WEBHOOK_SECRET", "").strip().rstrip("%").strip()
# IMAP reply poller — watches the inbox for replies to campaign sends and
# flips the lead status to 'interested' automatically so callers don't need
# to manually mark anything.
IMAP_SERVER     = os.getenv("IMAP_SERVER", "").strip()        # e.g. imap.gmail.com
IMAP_PORT       = int(os.getenv("IMAP_PORT", "993"))
IMAP_USERNAME   = os.getenv("IMAP_USERNAME", "").strip()      # full email
IMAP_PASSWORD   = os.getenv("IMAP_PASSWORD", "").strip()      # app password if Gmail+2FA
IMAP_POLL_INTERVAL_MINUTES = int(os.getenv("IMAP_POLL_INTERVAL_MINUTES", "10"))
# Self-healing Apollo dedupe sweep. Runs inside the bg-maintenance loop with
# a DB-tracked cooldown (app_settings.last_dedupe_sweep) so it fires once per
# week regardless of restarts. Auto-deletes truly-safe Apollo dupes; Slack-
# pings borderline cases that need a human eye.
DEDUPE_SWEEP_INTERVAL_DAYS = int(os.getenv("DEDUPE_SWEEP_INTERVAL_DAYS", "7"))
IMAP_FOLDER     = os.getenv("IMAP_FOLDER", "INBOX")
# Phrases that mark a message as auto-reply (case-insensitive). Skip these
# so a vacation responder doesn't false-flip a lead to 'interested'.
AUTO_REPLY_MARKERS = (
    "out of office", "out-of-office", "ooo:", "automatic reply",
    "auto-reply", "auto reply", "vacation reply", "i am away",
    "i'm away", "i'm currently out", "currently out of",
)
# Halts every Apollo enrichment call instantly. Same shape as PLACES_KILL_SWITCH.
APOLLO_KILL_SWITCH = os.getenv("APOLLO_KILL_SWITCH", "0") == "1"
# Titles tried (in priority order) when auto-enriching a scraped company.
APOLLO_ENRICH_TITLES = [t.strip() for t in os.getenv("APOLLO_ENRICH_TITLES",
    "Facility Manager,Director of Operations,Operations Manager,Property Manager,General Manager,Owner,Principal").split(",") if t.strip()]
# At a nursing home / hospital / dialysis center, the cleaning buyer is the EVS /
# facilities / admin chief — NOT an Owner/CEO. Used to enrich health-source leads.
APOLLO_HEALTHCARE_TITLES = [t.strip() for t in os.getenv("APOLLO_HEALTHCARE_TITLES",
    "Administrator,Executive Director,Director of Environmental Services,Environmental Services Director,"
    "Director of Facilities,Facilities Director,Director of Plant Operations,Director of Operations,"
    "Director of Nursing").split(",") if t.strip()]
# How long to remember "we already searched Apollo for this company" so we don't
# re-spend credits on the same company. In-memory only — restarts wipe it,
# which is fine; the worst case is paying for a few duplicate lookups.
APOLLO_ENRICH_CACHE_TTL_HOURS = int(os.getenv("APOLLO_ENRICH_CACHE_TTL_HOURS", "720"))  # 30 days

# Decision-maker title rotation. When a bulk pull turns up "all already in DB"
# it's because Apollo returns the SAME top-ranked people for the same titles
# every run — once you've pulled the Owners in a city, re-pulling Owners just
# re-hits them. Rotating through *different* decision-maker title groups surfaces
# fresh people (the GM, the Ops Director, the Facilities Manager at companies
# whose Owner you already have). Every group below is a budget-holder / buyer
# for Vision's services — no gatekeepers, no ICs. Ordered most-likely-buyer
# first. Override via APOLLO_DM_TITLE_GROUPS as "Owner|Founder;CEO|President;..."
# (groups separated by ';', titles within a group by '|').
def _parse_dm_title_groups(raw: str):
    groups = []
    for grp in raw.split(";"):
        titles = [t.strip() for t in grp.split("|") if t.strip()]
        if titles:
            groups.append(titles)
    return groups

APOLLO_DM_TITLE_GROUPS = _parse_dm_title_groups(os.getenv("APOLLO_DM_TITLE_GROUPS", "")) or [
    ["Owner", "Founder", "Co-Founder", "Proprietor"],
    ["CEO", "Chief Executive Officer", "President"],
    ["COO", "Chief Operating Officer", "VP of Operations", "Vice President of Operations"],
    ["General Manager", "Managing Director", "Managing Partner", "Managing Member"],
    ["Director of Operations", "Operations Manager", "Head of Operations"],
    ["Facilities Manager", "Facility Manager", "Director of Facilities", "Facilities Director"],
    ["Office Manager", "Business Manager", "Administrator", "Practice Manager"],
    ["Property Manager", "Regional Manager", "Site Manager"],
]

# ── Google Places cost controls ─────────────────────────────────────────────
# PLACES_KILL_SWITCH=1 halts every Places call instantly (same env name as
# vlm/recruitnil scrapers so one flip stops the bleeding across all repos).
PLACES_KILL_SWITCH = os.getenv("PLACES_KILL_SWITCH", "0") == "1"
# Non-admin daily scrape cap. UTC midnight reset. Eric (in ADMIN_USERS) is
# unlimited. Silently no-ops if usage_events table isn't migrated yet.
NON_ADMIN_DAILY_SCRAPE_CAP = int(os.getenv("NON_ADMIN_DAILY_SCRAPE_CAP", "3"))
# Non-admin daily Apollo-pull cap. Apollo burns ~1 credit per qualified contact
# (the /people/match enrichment), so callers get a tighter leash than Eric, who
# is unlimited. Phone reveals (5-8 credits each) are force-disabled for callers
# regardless. UTC midnight reset; silently no-ops until usage_events exists.
NON_ADMIN_APOLLO_PULL_CAP = int(os.getenv("NON_ADMIN_APOLLO_PULL_CAP", "5"))
# Per-pull page/size ceilings applied to non-admins to bound credit spend.
NON_ADMIN_APOLLO_MAX_PAGES = int(os.getenv("NON_ADMIN_APOLLO_MAX_PAGES", "5"))
NON_ADMIN_APOLLO_MAX_PER_PAGE = int(os.getenv("NON_ADMIN_APOLLO_MAX_PER_PAGE", "25"))
# Hard cap per scrape in dollars. Refuses the run if predicted spend exceeds.
PLACES_MAX_SPEND_PER_RUN = float(os.getenv("PLACES_MAX_SPEND_PER_RUN", "2.0"))
# Text Search cache TTL, days. Keyed on (pipeline='leadflow', city, keyword).
PLACES_CACHE_TTL_DAYS = int(os.getenv("PLACES_CACHE_TTL_DAYS", "14"))
# Autocomplete in-memory cache TTL, seconds.
AUTOCOMPLETE_CACHE_TTL_SECONDS = int(os.getenv("AUTOCOMPLETE_CACHE_TTL_SECONDS", "3600"))

# ── Free lead sources (OpenStreetMap, NPI, USASpending) ─────────────────────
# These cost $0 — no per-lead spend cap needed, just a kill switch + a sane
# fetch ceiling so a runaway query can't hammer a public API or flood the DB.
# One switch halts all free sources; mirrors PLACES_KILL_SWITCH ergonomics.
FREE_SOURCES_KILL_SWITCH = os.getenv("FREE_SOURCES_KILL_SWITCH", "0") == "1"
# Overpass mirrors, tried in order — public instances 504 under load, so we
# fall through to the next. kumi is usually fastest; .de is the canonical one.
OVERPASS_MIRRORS = [m.strip() for m in os.getenv("OVERPASS_MIRRORS",
    "https://overpass.kumi.systems/api/interpreter,https://overpass-api.de/api/interpreter").split(",") if m.strip()]
# Public Overpass blocks requests with no User-Agent (returns 406). Identify us.
OVERPASS_HEADERS = {"User-Agent": "LeadFlow/1.0 (Vision Cleaning lead sourcing)"}
# NPPES / NPI registry — free US healthcare-provider directory.
NPI_API_URL = os.getenv("NPI_API_URL", "https://npiregistry.cms.hhs.gov/api/")
# Max rows any single free-source pull will save (backstop, not a money cap).
FREE_SOURCE_MAX_ROWS = int(os.getenv("FREE_SOURCE_MAX_ROWS", "200"))
# Non-admin daily free-source pull cap (free, so looser than the paid caps).
NON_ADMIN_FREE_SOURCE_CAP = int(os.getenv("NON_ADMIN_FREE_SOURCE_CAP", "25"))
# Max phoneless OSM leads to Apollo-enrich per run when enrich=true. OSM is
# phone-sparse; this converts the wide net into callable leads without letting
# one pull burn unlimited Apollo credits. Admin-only (it costs ~1 credit each).
OSM_ENRICH_CAP = int(os.getenv("OSM_ENRICH_CAP", "50"))
# Wall-clock budgets so a big-state pull (6 Overpass queries + 50 sequential
# Apollo calls) can't blow the HTTP gateway timeout and hang. Each phase returns
# whatever it gathered so far — partial coverage beats a dead request.
OSM_FETCH_BUDGET_SEC  = int(os.getenv("OSM_FETCH_BUDGET_SEC", "35"))
OSM_ENRICH_BUDGET_SEC = int(os.getenv("OSM_ENRICH_BUDGET_SEC", "40"))

# Google Places pricing (May 2025) — used for usage_events cost tracking
# AND run-level spend prediction.
GOOGLE_COSTS_CENTS = {
    "google_text_search":  3.2,    # $0.032 per call
    "google_details":      1.7,    # $0.017 per call
    "google_autocomplete": 0.283,  # $2.83 / 1000 requests (no session token)
    "scrape_call":         0.0,    # aggregate row; children carry the cost
}

# Kill switch state cache. Refreshed from app_settings every KILL_SWITCH_CACHE_SECONDS.
# Env PLACES_KILL_SWITCH=1 is an absolute override — always wins, for those
# "something is horribly wrong and I cannot get into the UI" moments.
KILL_SWITCH_CACHE_SECONDS = 30
_kill_switch_cache = {"value": False, "source": "off", "expires": 0.0}

# ── Email config (Resend HTTP API for outreach) ─────────────────────────────────
# Railway blocks outbound SMTP, so we use Resend's HTTP API instead.
OUTREACH_EMAIL    = os.getenv("OUTREACH_EMAIL", "connect@visioncleaningcompany.com")
OUTREACH_NAME     = os.getenv("OUTREACH_NAME", "Vision Cleaning Company")
OUTREACH_REPLY_TO = os.getenv("OUTREACH_REPLY_TO", "")
RESEND_API_KEY    = os.getenv("RESEND_API_KEY", "")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

def send_slack(title, summary, fields=None, actions=None):
    """Fire-and-forget Slack notification."""
    if not SLACK_WEBHOOK_URL:
        return
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": title, "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": summary}},
    ]
    if fields:
        blocks.append({"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*{f['label']}*\n{f['value']}"} for f in fields
        ]})
    if actions:
        blocks.append({"type": "divider"})
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": a["label"], "emoji": True},
             "url": a["url"], **({"style": a["style"]} if "style" in a else {})}
            for a in actions
        ]})
    try:
        req_lib.post(SLACK_WEBHOOK_URL, json={"blocks": blocks}, timeout=5)
    except Exception as e:
        print(f"[slack] notification failed: {e}")

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=ignore-duplicates,return=representation"
}
# Admin headers use service role key to bypass RLS for login_log, sessions, etc.
SB_ADMIN_HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=ignore-duplicates,return=representation"
}

app = FastAPI()
ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "https://leadflow-railway-production.up.railway.app,http://localhost:5173,http://localhost:3000").split(",")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["*"], allow_headers=["*"])
security = HTTPBearer(auto_error=False)

def create_token(username, role="caller"):
    return jwt.encode(
        {"sub": username, "role": role, "exp": datetime.utcnow() + timedelta(hours=24)},
        SECRET_KEY, algorithm=ALGORITHM
    )

def _check_pipeline_key(request: Request, credentials: Optional[HTTPAuthorizationCredentials]) -> bool:
    """Return True if the request presents the static PIPELINE_API_KEY via either
    `Authorization: Bearer <key>` or `X-API-Key: <key>`. Disabled (returns False)
    when PIPELINE_API_KEY is unset, so we never accept an empty token as auth."""
    if not PIPELINE_API_KEY:
        return False
    if credentials and credentials.credentials == PIPELINE_API_KEY:
        return True
    header_key = request.headers.get("x-api-key") if request else None
    if header_key and header_key == PIPELINE_API_KEY:
        return True
    return False

def verify_token(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)):
    # Static key path first — service-to-service callers (leadflow-mcp) don't
    # have a UI session, just the shared PIPELINE_API_KEY.
    if _check_pipeline_key(request, credentials):
        return PIPELINE_PRINCIPAL
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return payload["sub"]
    except (jwt.exceptions.InvalidTokenError, KeyError, Exception):
        raise HTTPException(status_code=401, detail="Invalid token")

def verify_admin(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)):
    # Static-key callers are treated as admin — the key only ships to trusted
    # backend services (MCPs, internal pipelines), never to end-user UIs.
    if _check_pipeline_key(request, credentials):
        return PIPELINE_PRINCIPAL
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")
        return payload["sub"]
    except jwt.exceptions.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

class LoginRequest(BaseModel):
    username: str
    password: str

def log_login(username, status, role=None, detail=None):
    """Fire-and-forget login audit to Supabase (uses service role key to bypass RLS)"""
    try:
        req_lib.post(f"{SUPABASE_URL}/rest/v1/login_log",
            headers=SB_ADMIN_HEADERS,
            json={"username": username, "status": status, "role": role, "detail": detail,
                  "logged_at": datetime.utcnow().isoformat()},
            timeout=5)
    except:
        pass

def audit_log(username, action, resource_type=None, resource_id=None, details=None):
    """Fire-and-forget action audit to Supabase audit_log table"""
    try:
        req_lib.post(f"{SUPABASE_URL}/rest/v1/audit_log",
            headers=SB_ADMIN_HEADERS,
            json={"username": username, "action": action,
                  "resource_type": resource_type, "resource_id": str(resource_id) if resource_id else None,
                  "details": json_lib.dumps(details) if details else None,
                  "created_at": datetime.utcnow().isoformat()},
            timeout=5)
    except:
        pass

@app.post("/api/auth/login")
def login(req: LoginRequest):
    name_lower = req.username.strip().lower()
    # Block fired callers
    if name_lower in BLOCKED_USERS:
        log_login(req.username, "blocked")
        raise HTTPException(status_code=403, detail="Access revoked. Contact your manager.")
    is_admin = name_lower in ADMIN_USERS
    if is_admin:
        if req.password not in (ADMIN_PASSWORD, TEAM_PASSWORD):
            log_login(req.username, "failed", detail="wrong password (admin)")
            raise HTTPException(status_code=401, detail="Invalid password")
        role = "admin"
    else:
        if req.password != TEAM_PASSWORD:
            log_login(req.username, "failed", detail="wrong password")
            raise HTTPException(status_code=401, detail="Invalid password")
        role = "caller"
    log_login(req.username, "success", role=role)
    # Record session for sign-in tracking
    session_id = None
    try:
        sess_r = req_lib.post(f"{SUPABASE_URL}/rest/v1/user_sessions",
            headers=SB_ADMIN_HEADERS,
            json={"username": req.username.strip(), "signed_in": datetime.utcnow().isoformat()},
            timeout=5)
        print(f"[SESSION] POST user_sessions: HTTP {sess_r.status_code}")
        if sess_r.status_code not in (200, 201):
            print(f"[SESSION] Error body: {sess_r.text[:300]}")
        sess_data = sess_r.json() if sess_r.status_code in (200, 201) else []
        if isinstance(sess_data, list) and sess_data:
            session_id = sess_data[0].get("id")
            print(f"[SESSION] Created session {session_id} for {req.username}")
        else:
            print(f"[SESSION] No session ID returned for {req.username}")
    except Exception as e:
        print(f"[SESSION] Exception creating session: {e}")
    token = create_token(req.username, role)
    return {"token": token, "username": req.username, "role": role, "session_id": session_id}

@app.post("/api/auth/logout-beacon")
async def logout_beacon(request: Request):
    """Browser beacon for tab/window close — no auth header available"""
    body = await request.json()
    session_id = body.get("session_id")
    token = body.get("token")
    if not session_id or not token:
        return {"ok": False}
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        # Token is valid — record sign-out
        req_lib.patch(
            f"{SUPABASE_URL}/rest/v1/user_sessions?id=eq.{session_id}",
            headers=SB_ADMIN_HEADERS,
            json={"signed_out": datetime.utcnow().isoformat()},
            timeout=5)
        return {"ok": True}
    except:
        return {"ok": False}

@app.post("/api/auth/block")
def block_user(body: dict, user: str = Depends(verify_admin)):
    """Admin-only: add a username to the blocklist at runtime"""
    name = body.get("username", "").strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="Username required")
    BLOCKED_USERS.add(name)
    return {"blocked": name, "total_blocked": list(BLOCKED_USERS)}

@app.post("/api/auth/unblock")
def unblock_user(body: dict, user: str = Depends(verify_admin)):
    """Admin-only: remove a username from the blocklist"""
    name = body.get("username", "").strip().lower()
    BLOCKED_USERS.discard(name)
    return {"unblocked": name, "total_blocked": list(BLOCKED_USERS)}

@app.get("/api/auth/blocked")
def get_blocked(user: str = Depends(verify_admin)):
    return {"blocked": list(BLOCKED_USERS)}

@app.get("/api/auth/login-log")
def get_login_log(user: str = Depends(verify_admin)):
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/login_log?select=*&order=logged_at.desc&limit=100",
            headers=SB_ADMIN_HEADERS, timeout=30)
        logs = r.json() if r.status_code == 200 else []
        return logs if isinstance(logs, list) else []
    except:
        return []

@app.post("/api/auth/logout")
def logout_session(body: dict, user: str = Depends(verify_token)):
    """Record sign-out timestamp for the session"""
    session_id = body.get("session_id")
    if session_id:
        try:
            req_lib.patch(
                f"{SUPABASE_URL}/rest/v1/user_sessions?id=eq.{session_id}",
                headers=SB_ADMIN_HEADERS,
                json={"signed_out": datetime.utcnow().isoformat()},
                timeout=5)
        except:
            pass
    return {"ok": True}

@app.get("/api/auth/sessions")
def get_sessions(days: int = 0, user: str = Depends(verify_admin)):
    """Get sign-in sessions. days=0 means today only.
    Includes sessions that started OR were active during the window
    (e.g. signed in yesterday but still online today)."""
    try:
        if days > 0:
            since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        else:
            since = datetime.utcnow().strftime("%Y-%m-%d")
        # Get sessions that started in the window
        r1 = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/user_sessions?select=*&signed_in=gte.{since}T00:00:00&order=signed_in.desc&limit=500",
            headers=SB_ADMIN_HEADERS, timeout=30)
        started = r1.json() if r1.status_code == 200 else []
        if not isinstance(started, list):
            started = []
        # Also get sessions still active (no sign_out) that started before the window
        r2 = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/user_sessions?select=*&signed_in=lt.{since}T00:00:00&signed_out=is.null&order=signed_in.desc&limit=100",
            headers=SB_ADMIN_HEADERS, timeout=30)
        still_active = r2.json() if r2.status_code == 200 else []
        if not isinstance(still_active, list):
            still_active = []
        # Also get sessions that signed out during the window but started before
        r3 = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/user_sessions?select=*&signed_in=lt.{since}T00:00:00&signed_out=gte.{since}T00:00:00&order=signed_in.desc&limit=100",
            headers=SB_ADMIN_HEADERS, timeout=30)
        signed_out_during = r3.json() if r3.status_code == 200 else []
        if not isinstance(signed_out_during, list):
            signed_out_during = []
        # Merge and deduplicate by id
        seen = set()
        merged = []
        for s in started + still_active + signed_out_during:
            sid = s.get("id")
            if sid not in seen:
                seen.add(sid)
                merged.append(s)
        merged.sort(key=lambda x: x.get("signed_in", ""), reverse=True)
        return merged
    except:
        return []

@app.get("/api/auth/me")
def me(user: str = Depends(verify_token)):
    admin = is_admin(user)
    return {
        "username":       user,
        "isAdmin":        admin,
        # null = unlimited for admins; number = caller's daily cap for UI display
        "dailyScrapeCap": None if admin else NON_ADMIN_DAILY_SCRAPE_CAP,
    }

# ── Cost-control helpers ────────────────────────────────────────────────────
def is_admin(username: str) -> bool:
    return (username or "").strip().lower() in ADMIN_USERS

def is_kill_switch_on():
    """Two-layer check. Env var PLACES_KILL_SWITCH=1 always wins (absolute
    override). Otherwise reads app_settings.places_kill_switch with a 30s
    cache so per-request overhead is near-zero. Returns (bool, source)."""
    if PLACES_KILL_SWITCH:
        return True, "env"
    now = time.time()
    if _kill_switch_cache["expires"] > now:
        return _kill_switch_cache["value"], _kill_switch_cache["source"]
    # Cache miss — refresh from DB. On any error, fail OPEN (scraping allowed)
    # so a Supabase outage doesn't kill outreach; the env var is the reliable
    # override for true emergencies.
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/app_settings?key=eq.places_kill_switch&select=value",
            headers=SB_HEADERS, timeout=3,
        )
        rows = r.json() if r.status_code == 200 else []
        val = (rows[0]["value"] if isinstance(rows, list) and rows else "0")
        on  = str(val).strip().lower() in ("1", "true", "yes", "on")
        _kill_switch_cache["value"]   = on
        _kill_switch_cache["source"]  = "db" if on else "off"
        _kill_switch_cache["expires"] = now + KILL_SWITCH_CACHE_SECONDS
        return on, _kill_switch_cache["source"]
    except Exception as e:
        print(f"[KILL-SWITCH] refresh failed, failing open: {e}")
        _kill_switch_cache["expires"] = now + KILL_SWITCH_CACHE_SECONDS
        return False, "off"

def set_kill_switch(on: bool):
    """Admin-write. Upserts app_settings and invalidates the cache."""
    req_lib.post(
        f"{SUPABASE_URL}/rest/v1/app_settings",
        headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"},
        json={"key": "places_kill_switch", "value": "1" if on else "0"},
        timeout=10,
    )
    # Force next caller to refetch (don't set cached value from this side —
    # keeps the DB as the single source of truth across multiple workers).
    _kill_switch_cache["expires"] = 0.0

def events_today(username: str, event_type: str) -> int:
    """Count a user's events of one type since UTC midnight. Returns 0 if
    usage_events isn't set up yet (so any cap built on it silently no-ops)."""
    try:
        midnight = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        url = (
            f"{SUPABASE_URL}/rest/v1/usage_events"
            f"?select=id&username=eq.{url_quote(username)}"
            f"&event_type=eq.{url_quote(event_type)}&created_at=gte.{midnight}"
        )
        r = req_lib.get(url, headers=SB_HEADERS, timeout=5)
        if r.status_code == 200:
            data = r.json()
            return len(data) if isinstance(data, list) else 0
        return 0
    except Exception as e:
        print(f"[RATE-LIMIT] count failed: {e}")
        return 0

def scrapes_today(username: str) -> int:
    """Count a user's scrape_call events since UTC midnight."""
    return events_today(username, "scrape_call")

def log_usage(username: str, event_type: str, metadata: Optional[dict] = None):
    """Fire-and-forget usage logger. Never raises — never blocks a scrape."""
    try:
        cost = GOOGLE_COSTS_CENTS.get(event_type, 0)
        r = req_lib.post(
            f"{SUPABASE_URL}/rest/v1/usage_events",
            headers=SB_HEADERS,
            json={
                "username":   username or "unknown",
                "event_type": event_type,
                "cost_cents": cost,
                "metadata":   metadata or {},
            },
            timeout=5,
        )
        if r.status_code not in (200, 201):
            # Usually means the table isn't migrated yet — we don't block.
            print(f"[USAGE] Supabase {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[USAGE] Exception: {e}")

# ── Places Text Search cache ────────────────────────────────────────────────
# Shared pattern with vlm/recruitnil scrapers: cache (pipeline='leadflow',
# city, keyword) -> place_ids with a TTL. A fresh cache row means we already
# made that Text Search call within PLACES_CACHE_TTL_DAYS, so we skip it.
# DB errors are best-effort — a cache outage falls through to a live API
# call, never blocks a scrape.
def places_cache_load(combos):
    """combos: list[(city, keyword)]. Returns set of 'city||keyword' cache hits."""
    if not combos:
        return set()
    try:
        cutoff = (datetime.utcnow() - timedelta(days=PLACES_CACHE_TTL_DAYS)).isoformat()
        url = (
            f"{SUPABASE_URL}/rest/v1/places_search_cache"
            f"?select=city,keyword"
            f"&pipeline=eq.leadflow"
            f"&last_searched_at=gte.{cutoff}"
            f"&limit=5000"
        )
        r = req_lib.get(url, headers=SB_HEADERS, timeout=5)
        if r.status_code != 200:
            return set()
        rows = r.json() if isinstance(r.json(), list) else []
        want = {f"{c}||{k}" for c, k in combos}
        got  = {f"{row.get('city','')}||{row.get('keyword','')}" for row in rows}
        return want & got
    except Exception as e:
        print(f"[PLACES-CACHE] load failed: {e}")
        return set()

def places_cache_write(entries):
    """entries: list[dict(city, keyword, place_ids)]. Upserts pipeline='leadflow'."""
    if not entries:
        return
    try:
        now_iso = datetime.utcnow().isoformat()
        payload = [{
            "pipeline":         "leadflow",
            "city":             e["city"],
            "keyword":          e["keyword"],
            "last_searched_at": now_iso,
            "place_ids":        e.get("place_ids") or [],
            "result_count":     len(e.get("place_ids") or []),
        } for e in entries]
        r = req_lib.post(
            f"{SUPABASE_URL}/rest/v1/places_search_cache"
            f"?on_conflict=pipeline,city,keyword",
            headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json=payload, timeout=10,
        )
        if r.status_code not in (200, 201, 204):
            print(f"[PLACES-CACHE] upsert {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[PLACES-CACHE] write failed: {e}")

# ── Autocomplete in-memory cache ────────────────────────────────────────────
# Every keystroke hits /api/cities/autocomplete. Places Autocomplete is
# pay-per-request without session tokens. Process-wide TTL cache dedups the
# "user backspaces and retypes the same 4 chars" pattern that dominates
# real typing traffic. Cache key is (q.lower(), state.lower()).
_autocomplete_cache = {}  # { key: (expires_ts, suggestions_list) }

def autocomplete_cache_get(key):
    hit = _autocomplete_cache.get(key)
    if not hit:
        return None
    expires, suggestions = hit
    if expires < time.time():
        _autocomplete_cache.pop(key, None)
        return None
    return suggestions

def autocomplete_cache_set(key, suggestions):
    # Soft cap — prevent unbounded memory on a long-lived process.
    if len(_autocomplete_cache) > 5000:
        _autocomplete_cache.clear()
    _autocomplete_cache[key] = (time.time() + AUTOCOMPLETE_CACHE_TTL_SECONDS, suggestions)

INDUSTRY_MAP = {
    "Healthcare":         "health clinic",
    "Home Health Care":   "home health care agency",
    "Hospitals":          "hospital",
    "Nursing Facilities": "nursing home",
    "Medical Equipment":  "medical equipment supplier",
    "Software":           "software company",
    "IT Services":        "IT services company",
    "Consulting":         "business consulting firm",
    "Accounting / CPA":   "accounting firm CPA",
    "Legal Services":     "law firm",
    "Marketing":          "marketing agency",
    "Staffing / HR":      "staffing agency",
    "Engineering":        "engineering firm",
    "Insurance":          "insurance agency",
    "Real Estate":        "real estate agency",
    "Logistics":          "logistics company",
    "Construction":       "construction company",
    "Manufacturing":      "manufacturing company",
    "Finance":            "financial services",
    "Education":          "private school",
    # Cleaning-company verticals. Concrete Google Places terms that surface
    # facilities with heavy recurring janitorial needs.
    "Industrial":         "warehouse",
    "Entertainment":      "event venue",
}

def clean(v): return str(v).strip() if v else ""

# ── Lead quality model ───────────────────────────────────────────────────────
# Cleaning-fit tiers. For a commercial janitorial company, value tracks how big,
# how frequently-cleaned, and how outsourcing-prone the facility is. Matched
# against industry + company name (lowercased). First tier that matches wins.
CLEANING_FIT_TIERS = [
    # (points, [keywords]) — high to low. Keywords are substrings, matched
    # against industry + company + title, so they must catch NPI taxonomy
    # strings ("Family Medicine", "Skilled Nursing Facility, Internal Medicine")
    # and Apollo/OSM labels alike.
    (40, ["hospital", "surgery", "surgical", "surgeon", "nursing home", "nursing facility",
          "skilled nursing", "assisted living", "senior living", "long term care",
          "dialysis", "rehabilitation", "rehab", "medical center", "health system",
          "imaging center", "radiology", "urgent care", "emergency", "trauma",
          "behavioral health", "psychiatric", "hospice", "ambulatory"]),
    (36, ["clinic", "medical", "medicine", "physician", "practice", "dental", "dentist",
          "orthodont", "veterinary", "veterinarian", "physical therapy", "therapy",
          "health", "healthcare", "pediatric", "dermatology", "cardiology", "oncology",
          "orthopedic", "ophthalmology", "optometr", "chiropract", "pharmacy",
          "family medicine", "internal medicine", "primary care", "outpatient", "wellness"]),
    (38, ["manufacturing", "manufacturer", "warehouse", "distribution", "factory", "plant",
          "industrial", "fabrication", "machining", "processing", "logistics", "freight",
          "fulfillment", "storage", "cold storage", "assembly", "foundry", "refinery"]),
    (36, ["school", "university", "college", "campus", "academy", "daycare", "day care",
          "preschool", "pre-school", "childcare", "child care", "education", "educational",
          "montessori", "charter", "learning center", "institute", "seminary", "head start"]),
    (34, ["casino", "arena", "stadium", "convention", "event venue", "events venue", "banquet",
          "theater", "theatre", "cinema", "fitness", "gym", "athletic", "recreation",
          "bowling", "hotel", "motel", "resort", "conference center", "nightclub", "country club"]),
    (24, ["office", "corporate", "headquarters", "bank", "credit union", "financial",
          "dealership", "property management", "law firm", "attorney", "legal", "accounting",
          "cpa", "insurance", "real estate", "realty", "church", "worship", "ministry",
          "clubhouse", "municipal", "city hall", "government", "library"]),
    (12, ["retail", "store", "shop", "salon", "spa", "restaurant", "cafe", "coffee",
          "boutique", "barber", "grocery", "market", "bar ", "diner"]),
]
# National chains decide cleaning at HQ — a local rep can't close them. Demote.
CHAIN_MARKERS = ["mcdonald", "starbucks", "walmart", "target", "cvs", "walgreens",
                 "subway", "burger king", "wendy", "taco bell", "dunkin", "kfc",
                 "home depot", "lowe's", "costco", "kroger", "7-eleven", "dollar general"]

# Intent markers a source can stamp into a lead's notes via add_intent_marker().
# Each maps to a score boost — intent (active need) outranks raw fit.
INTENT_BOOSTS = {
    "health_violation": 42,  # FAILED a govt health inspection = authoritative, urgent, call-now
    "cleanliness": 34,   # recent reviews complain about dirtiness = urgent pain
    "rfp":         32,   # active solicitation for janitorial services
    "contract":    28,   # holds an expiring janitorial contract (recompete soon)
    "newbuild":    22,   # new construction / just opened = need being born
    "competitor":  26,   # unhappy with their current cleaner (poach)
    "lookalike":   12,   # resembles a lead we've already converted
}

def _fit_points(text: str) -> int:
    t = (text or "").lower()
    for pts, kws in CLEANING_FIT_TIERS:
        if any(k in t for k in kws):
            return pts
    return 15  # unknown vertical — neutral

def add_intent_marker(lead: dict, kind: str, label: str):
    """Stamp a machine-readable intent tag + human label into notes so the
    score model picks it up and reps see WHY a lead is hot. Idempotent-ish."""
    tag = f"[INTENT:{kind}]"
    notes = lead.get("notes") or ""
    if tag in notes:
        return
    lead["notes"] = (f"{tag} {label} | {notes}").strip(" |") if notes else f"{tag} {label}"

def lead_intent_kinds(lead: dict) -> list:
    notes = (lead.get("notes") or "").lower()
    return [k for k in INTENT_BOOSTS if f"[intent:{k}]" in notes]

def score_lead(lead):
    """Quality = contactability + cleaning-fit + active intent. 0-100.
    Replaces the old 'sum of filled fields' score so the dialer surfaces who's
    actually worth calling, not just who has the most complete record."""
    score = 0

    # ── Contactability (can a rep actually reach a buyer?) — up to ~33 ──
    if is_valid_us_phone((lead.get("phone") or "").strip()):
        score += 18
    if (lead.get("firstName") or "").strip():        # named decision-maker (Apollo)
        score += 11
    em = (lead.get("email") or "").strip()
    if "@" in em and "." in em.split("@")[-1]:
        score += 4

    # ── Cleaning-fit (is this the kind of facility we want?) — up to ~40 ──
    fit_text = f"{lead.get('industry','')} {lead.get('company','')} {lead.get('title','')}"
    score += _fit_points(fit_text)

    # Size/establishment proxy: review volume parsed from notes ("123 reviews").
    m = re.search(r"(\d+)\s+reviews", (lead.get("notes") or "").lower())
    if m:
        n = int(m.group(1))
        score += 8 if n >= 200 else 5 if n >= 50 else 2

    # ── Active intent (someone with a need RIGHT NOW) — can dominate ──
    notes_l = (lead.get("notes") or "").lower()
    for kind in lead_intent_kinds(lead):
        boost = INTENT_BOOSTS.get(kind, 0)
        # Cleanliness complaints decay with age — a review from last week is a
        # red-hot lead; one from 2 years ago is stale. The [clnage:<days>] token
        # is stamped by the review scanner. No token (older runs) → neutral.
        if kind == "cleanliness":
            am = re.search(r"\[clnage:(\d+)\]", notes_l)
            if am:
                d = int(am.group(1))
                mult = 1.45 if d <= 14 else 1.25 if d <= 30 else 1.0 if d <= 90 \
                       else 0.7 if d <= 365 else 0.45
                boost = round(boost * mult)
        # Failed inspection: a re-inspection clock is ticking, so freshness is
        # everything — a last-week failure is a call-today lead.
        if kind == "health_violation":
            am = re.search(r"\[hvage:(\d+)\]", notes_l)
            if am:
                d = int(am.group(1))
                # Gentle decay — CMS nursing-home surveys run ~annually, so a
                # 1-2yr-old infection-control deficiency is still a live signal
                # (and the facility's top-tier cleaning fit carries it anyway).
                mult = 1.5 if d <= 30 else 1.35 if d <= 90 else 1.1 if d <= 365 \
                       else 0.85 if d <= 730 else 0.6
                boost = round(boost * mult)
        score += boost

    # CMS Special Focus Facility = actively watched worst-performer (updated
    # monthly) → freshest, hottest signal. Complaint-triggered = year-round, off
    # the annual cycle = fresher than a routine survey.
    if "special focus" in notes_l:
        score += 12
    elif "complaint-triggered" in notes_l:
        score += 5

    # ── Penalties ──
    if any(c in (lead.get("company") or "").lower() for c in CHAIN_MARKERS):
        score -= 25  # HQ-decided, local rep can't close

    return min(100, max(0, score))

US_STATES_FULL = {
    "AL":"Alabama","AK":"Alaska","AZ":"Arizona","AR":"Arkansas","CA":"California",
    "CO":"Colorado","CT":"Connecticut","DE":"Delaware","FL":"Florida","GA":"Georgia",
    "HI":"Hawaii","ID":"Idaho","IL":"Illinois","IN":"Indiana","IA":"Iowa",
    "KS":"Kansas","KY":"Kentucky","LA":"Louisiana","ME":"Maine","MD":"Maryland",
    "MA":"Massachusetts","MI":"Michigan","MN":"Minnesota","MS":"Mississippi","MO":"Missouri",
    "MT":"Montana","NE":"Nebraska","NV":"Nevada","NH":"New Hampshire","NJ":"New Jersey",
    "NM":"New Mexico","NY":"New York","NC":"North Carolina","ND":"North Dakota","OH":"Ohio",
    "OK":"Oklahoma","OR":"Oregon","PA":"Pennsylvania","RI":"Rhode Island","SC":"South Carolina",
    "SD":"South Dakota","TN":"Tennessee","TX":"Texas","UT":"Utah","VT":"Vermont",
    "VA":"Virginia","WA":"Washington","WV":"West Virginia","WI":"Wisconsin","WY":"Wyoming"
}
US_STATE_ABBREVS = set(US_STATES_FULL.keys())
US_STATE_NAMES = set(v.lower() for v in US_STATES_FULL.values())
US_STATES_REVERSE = {v.lower(): k for k, v in US_STATES_FULL.items()}

def normalize_state_abbrev(state) -> str:
    """Normalize any state value (full name or 2-letter code) to the abbrev the
    rest of the app keys on. Apollo returns full names ("Nevada"); Google Places
    returns abbrevs ("NV"). Storing both broke the Leads state grouping + filter
    — a "Nevada" lead never matched a state=NV filter and sat in its own
    'NEVADA' group, so callers thought freshly-pulled leads vanished. Returns
    the input unchanged if unrecognized."""
    if not state:
        return ""
    s = str(state).strip()
    if s.upper() in US_STATE_ABBREVS:
        return s.upper()
    return US_STATES_REVERSE.get(s.lower(), s)

def clean_company_name(name) -> str:
    """Strip Apollo marketing taglines from an org name so the caller's script
    reads cleanly ("bServed: Powering Real-Time Hospital Revenue" -> "bServed").
    Conservative on purpose:
      - splits on ':' (colons in org names are essentially always taglines)
      - splits on '|' ONLY when it's space-adjacent ("Group | AMO® | CORFAC");
        a bare pipe is part of the real name ("Jacobsen|Daniels") and is kept
      - never splits on ' - ', because dashed Apollo names are often
        acronym-first ("SFIA - Sports & Fitness Industry Association") where the
        real name is AFTER the dash and truncating would make it worse.
    Returns the input unchanged if no separator (or if stripping would empty it)."""
    if not name:
        return name
    n = str(name)
    if ":" in n:
        n = n.split(":")[0]
    n = re.split(r"\s+\|\s*|\|\s+", n, maxsplit=1)[0]
    n = n.strip()
    return n or str(name).strip()

# State → IANA timezone mapping for connectivity-rate bucketing. We bucket
# calls by the *prospect's* local time, not the caller's, because pickup rate
# is driven by what time it is at the office being dialed (a 9am AZ call to
# NY = noon Eastern at the receptionist's desk). Multi-zone states use the
# dominant zone for simplicity — TN/KY/IN/FL/etc. have edge regions in a
# different zone but the bulk of leads land in the listed zone.
US_STATE_TIMEZONES = {
    "AL":"America/Chicago",     "AK":"America/Anchorage",
    "AZ":"America/Phoenix",     "AR":"America/Chicago",
    "CA":"America/Los_Angeles", "CO":"America/Denver",
    "CT":"America/New_York",    "DE":"America/New_York",
    "FL":"America/New_York",    "GA":"America/New_York",
    "HI":"Pacific/Honolulu",    "ID":"America/Boise",
    "IL":"America/Chicago",     "IN":"America/Indiana/Indianapolis",
    "IA":"America/Chicago",     "KS":"America/Chicago",
    "KY":"America/New_York",    "LA":"America/Chicago",
    "ME":"America/New_York",    "MD":"America/New_York",
    "MA":"America/New_York",    "MI":"America/Detroit",
    "MN":"America/Chicago",     "MS":"America/Chicago",
    "MO":"America/Chicago",     "MT":"America/Denver",
    "NE":"America/Chicago",     "NV":"America/Los_Angeles",
    "NH":"America/New_York",    "NJ":"America/New_York",
    "NM":"America/Denver",      "NY":"America/New_York",
    "NC":"America/New_York",    "ND":"America/Chicago",
    "OH":"America/New_York",    "OK":"America/Chicago",
    "OR":"America/Los_Angeles", "PA":"America/New_York",
    "RI":"America/New_York",    "SC":"America/New_York",
    "SD":"America/Chicago",     "TN":"America/Chicago",
    "TX":"America/Chicago",     "UT":"America/Denver",
    "VT":"America/New_York",    "VA":"America/New_York",
    "WA":"America/Los_Angeles", "WV":"America/New_York",
    "WI":"America/Chicago",     "WY":"America/Denver",
    "DC":"America/New_York",
    "PR":"America/Puerto_Rico", "VI":"America/Puerto_Rico",
    "GU":"Pacific/Guam",        "MP":"Pacific/Guam",
}

def state_to_iana_tz(state) -> str:
    """Resolve a state value (abbrev or full name) to IANA tz string. Returns
    empty string if unknown — caller falls back to LEADFLOW_TZ_OFFSET_HOURS."""
    if not state:
        return ""
    s = str(state).strip()
    if s.upper() in US_STATE_TIMEZONES:
        return US_STATE_TIMEZONES[s.upper()]
    # Full-name fallback: "California" → "CA" → tz
    s_lower = s.lower()
    for abbrev, full in US_STATES_FULL.items():
        if full.lower() == s_lower:
            return US_STATE_TIMEZONES.get(abbrev, "")
    return ""

def is_us_address(addr):
    """Check if a formatted address looks like it's in the USA"""
    if not addr:
        return False
    addr_lower = addr.lower().strip()
    # Check if it ends with "USA", "US", "United States"
    if any(addr_lower.endswith(s) for s in ("usa", "us", "united states", "united states of america")):
        return True
    # Check ALL comma segments for a US state abbreviation (with or without zip)
    # Google Places US format: "123 Main St, City, ST 06457" or "123 Main St, City, ST 06457, USA"
    parts = [p.strip() for p in addr.split(",")]
    for part in parts:
        tokens = part.split()
        if not tokens:
            continue
        # Match "CT 06457" or "CT" or "Connecticut"
        if tokens[0].upper() in US_STATE_ABBREVS and (len(tokens) == 1 or (len(tokens) == 2 and tokens[1][:1].isdigit())):
            return True
        if part.lower() in US_STATE_NAMES:
            return True
    return False

def normalize_us_phone(phone):
    """Canonical `(NNN) NNN-NNNN` for any US phone string with 10 digits, or
    11 digits starting with 1. Returns None for unparseable input.

    Stops the dedupe gap where `(203) 863-3000`, `203-863-3000`, `203.863.3000`
    and `+1 203 863 3000` were all stored as different strings and slipped
    past the cross-run phone check. Run on every lead write so the leads
    table only ever holds canonical strings."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"

def is_valid_us_phone(phone) -> bool:
    """NANP-format check for US phones. Connectivity-rate complaints from
    callers traced back to Google Places returning rows with empty / 7-digit /
    all-same-digit / 555-exchange phones. We skip those at capture so the
    caller never sees a guaranteed-fail dial.

      - 10 digits, or 11 digits starting with 1
      - Area code first digit must be 2-9 (NANP)
      - Exchange (4th digit overall) must be 2-9 (NANP)
      - Rejects all-same-digit (1111111111)
      - Rejects 555 exchange (fictional/test reservation, not dialable)
    """
    if not phone:
        return False
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return False
    if digits[0] in ("0", "1"):
        return False
    if digits[3] in ("0", "1"):
        return False
    if len(set(digits)) == 1:
        return False
    if digits[3:6] == "555":
        return False
    return True

def scrape_google_places(keyword="health clinic", state="", limit=25, username="unknown"):
    # Emergency brake. Env PLACES_KILL_SWITCH=1 is an absolute override;
    # app_settings.places_kill_switch lets Eric flip it from the admin UI
    # without a Railway redeploy.
    on, _src = is_kill_switch_on()
    if on:
        print("[PLACES] kill switch active — returning [] without calling API")
        return []

    leads = []
    place_ids_found = []  # Collected for cache write below
    # Force US context in query
    location_part = state if state else "USA"
    query = f"{keyword} {location_part}".strip()
    print(f"[PLACES] query: '{query}' limit: {limit} user: {username}")

    params = {
        "query": query,
        "key": GOOGLE_KEY,
        "type": "establishment",
        "region": "us",
    }

    fetched = 0
    next_page_token = None

    while fetched < limit:
        if next_page_token:
            params = {"pagetoken": next_page_token, "key": GOOGLE_KEY}
            time.sleep(2)  # Google requires delay before using next page token

        try:
            r = req_lib.get(
                "https://maps.googleapis.com/maps/api/place/textsearch/json",
                params=params, timeout=30
            )
            # Every text-search call = one billable unit. Log who triggered it.
            log_usage(username, "google_text_search", {"query": query, "state": state})
            print(f"[PLACES] HTTP {r.status_code}")
            data = r.json()
            status = data.get("status")
            print(f"[PLACES] status: {status}, results: {len(data.get('results',[]))}")

            if status == "REQUEST_DENIED":
                print(f"[PLACES] denied: {data.get('error_message')}")
                break
            if status not in ("OK", "ZERO_RESULTS"):
                break

            results = data.get("results", [])
            for place in results:
                if fetched >= limit:
                    break
                addr = place.get("formatted_address", "")

                # Skip non-US results
                if not is_us_address(addr):
                    continue

                parts = [p.strip() for p in addr.split(",")]
                # Parse US address: "123 Main St, City, ST ZIP, USA" or "123 Main St, City, ST ZIP"
                # Remove trailing "USA"/"US" part if present
                if parts and parts[-1].strip().lower() in ("usa", "us", "united states"):
                    parts = parts[:-1]
                city  = parts[-2].strip() if len(parts) >= 2 else ""
                st_part = parts[-1].strip() if parts else state
                st    = st_part.split()[0] if st_part.split() else state

                lead = {
                    "company":     clean(place.get("name", "")),
                    "industry":    keyword,
                    "phone":       clean(place.get("formatted_phone_number", "")),
                    "address":     parts[0].strip() if parts else "",
                    "city":        city,
                    "state":       st,
                    "website":     clean(place.get("website", "")),
                    "notes":       f"Google rating: {place.get('rating','N/A')} | {place.get('user_ratings_total',0)} reviews",
                    "source":      "Google Places",
                    "firstName":   "",
                    "lastName":    "",
                    "title":       "",
                    "email":       "",
                    "assignedTo":  "",
                    "callbackDate":"",
                    "status":      "new",
                    "createdAt":   datetime.utcnow().isoformat(),
                    "updatedAt":   datetime.utcnow().isoformat(),
                    "createdBy":   "system",
                }
                lead["score"] = score_lead(lead)

                # Track place_id for the cache write regardless of phone enrichment.
                pid = place.get("place_id")
                if pid:
                    place_ids_found.append(pid)

                # Get phone via place details if missing
                if not lead["phone"]:
                    place_id = place.get("place_id")
                    if place_id:
                        try:
                            det = req_lib.get(
                                "https://maps.googleapis.com/maps/api/place/details/json",
                                params={"place_id": place_id, "fields": "formatted_phone_number,website", "key": GOOGLE_KEY},
                                timeout=10
                            ).json()
                            # Details call = one billable unit. Log it.
                            log_usage(username, "google_details", {"place_id": place_id})
                            result = det.get("result", {})
                            lead["phone"]   = clean(result.get("formatted_phone_number", ""))
                            lead["website"] = clean(result.get("website", "")) or lead["website"]
                            lead["score"]   = score_lead(lead)
                        except:
                            pass

                # Final validation: company + US state. Phone validity is
                # NOT checked here because Apollo enrichment (downstream in
                # run_scrape) may rescue this row with a DM phone or
                # actionable name+email even if Google's phone is junk. The
                # final callable filter runs in run_scrape AFTER enrichment.
                if lead["company"] and (st.upper() in US_STATE_ABBREVS or not st):
                    leads.append(lead)
                    fetched += 1

            next_page_token = data.get("next_page_token")
            if not next_page_token or fetched >= limit:
                break

        except Exception as e:
            print(f"[PLACES] Exception: {e}")
            break

    print(f"[PLACES] Returning {len(leads)} leads")
    # Attach place_ids to the return so run_scrape can feed the cache write.
    # Using an attribute on the list would be weird; instead, return a dict-ish
    # wrapper via a tuple is too invasive — leads[0]._place_ids etc even worse.
    # Simplest: stash on a module-level dict keyed by (keyword, state), read
    # once by the caller. Keeps the signature backward-compatible.
    _LAST_SCRAPE_PLACE_IDS[(keyword, state)] = place_ids_found
    return leads[:limit]

# Module-level hand-off for cache writes. scrape_google_places stashes the
# list of place_ids it saw for (keyword, state); run_scrape reads then clears.
_LAST_SCRAPE_PLACE_IDS = {}

def save_to_supabase(leads):
    """Insert leads, deduping by phone against rows already in the table.
    Returns (saved_count, already_in_db_count) so callers can show a real
    breakdown ('13 already in your DB · 2 new') instead of a bare 'saved=2'
    that looks like a failure."""
    if not leads:
        return 0, 0
    already_in_db = 0
    try:
        # Canonicalize phones BEFORE dedup. Otherwise `(203) 863-3000`,
        # `203-863-3000`, and `+1 203 863 3000` would each look unique to the
        # cross-run check below and create three rows for the same business.
        for l in leads:
            np = normalize_us_phone(l.get("phone"))
            if np:
                l["phone"] = np

        # Cross-run dedup: the per-scrape `seen_phones` set in run_scrape only
        # dedupes within one Places run. Two runs over the same city/keyword
        # (e.g. caller searched "schools, CT" Monday and "private schools, CT"
        # Wednesday) bypass the (city,keyword) Places cache and re-insert the
        # same business as a fresh row, giving the dialer "repeating leads."
        # Filter incoming rows whose phone already exists in the leads table.
        incoming_phones = list({(l.get("phone") or "").strip() for l in leads if (l.get("phone") or "").strip()})
        existing_phones = set()
        if incoming_phones:
            # Chunk to avoid URL-length cap (~2KB). 100 phones * ~20 chars each = ~2KB.
            CHUNK = 100
            for i in range(0, len(incoming_phones), CHUNK):
                chunk = incoming_phones[i:i+CHUNK]
                # Each phone is quoted to keep commas-in-format safe.
                in_list = ",".join(f'"{p}"' for p in chunk)
                try:
                    r = req_lib.get(
                        f"{SUPABASE_URL}/rest/v1/leads?select=phone&phone=in.({in_list})",
                        headers=SB_HEADERS, timeout=20,
                    )
                    if r.status_code == 200:
                        for row in r.json():
                            ph = (row.get("phone") or "").strip()
                            if ph:
                                existing_phones.add(ph)
                except Exception as e:
                    print(f"[SUPABASE] dedup pre-check failed: {e}")
                    # Fail open — better to risk a dup than drop the whole batch.

        if existing_phones:
            before = len(leads)
            leads = [l for l in leads if (l.get("phone") or "").strip() not in existing_phones]
            already_in_db = before - len(leads)
            print(f"[SUPABASE] cross-run phone dedup: {already_in_db} of {before} dropped (already in DB)")

        if not leads:
            return 0, already_in_db

        r = req_lib.post(
            f"{SUPABASE_URL}/rest/v1/leads",
            headers=SB_HEADERS, json=leads, timeout=30
        )
        print(f"[SUPABASE] POST {r.status_code}")
        if r.status_code not in (200, 201):
            print(f"[SUPABASE] Error: {r.text[:300]}")
            return 0, already_in_db
        saved = r.json()
        return (len(saved) if isinstance(saved, list) else 1), already_in_db
    except Exception as e:
        print(f"[SUPABASE] Exception: {e}")
        return 0, already_in_db

# ── Unified free-source ingestion ───────────────────────────────────────────
# Every free source (OpenStreetMap, NPI, USASpending, …) produces partial lead
# dicts and funnels them through here: stamp defaults → in-batch phone dedup →
# score → callable filter → cross-run dedup + insert (reusing save_to_supabase).
# Keeps each fetcher tiny and guarantees they all inherit the same dedup + the
# phone-unique safety. Returns a breakdown dict shaped like run_scrape's.
def ingest_leads(raw_leads, source: str, user: str = "system", callable_filter: bool = True):
    now = datetime.utcnow().isoformat()
    staged, seen_batch_phones = [], set()
    dropped_uncallable = 0
    for r in (raw_leads or []):
        lead = {
            "company": clean(r.get("company", "")), "industry": r.get("industry", "") or "",
            "phone": normalize_us_phone(r.get("phone")) or "",
            "address": clean(r.get("address", "")), "city": clean(r.get("city", "")),
            "state": normalize_state_abbrev(clean(r.get("state", ""))),
            "website": clean(r.get("website", "")), "notes": r.get("notes", "") or "",
            "source": source, "firstName": clean(r.get("firstName", "")),
            "lastName": clean(r.get("lastName", "")), "title": clean(r.get("title", "")),
            "email": clean(r.get("email", "")), "assignedTo": "", "callbackDate": "",
            "status": "new", "createdAt": now, "updatedAt": now, "createdBy": user,
        }
        if not lead["company"]:
            continue
        # In-batch dedup on real phones (the partial unique index lets multiple
        # phoneless rows coexist, so only collapse meaningfully-set phones).
        ph = lead["phone"]
        if ph:
            if ph in seen_batch_phones:
                continue
            seen_batch_phones.add(ph)
        lead["score"] = score_lead(lead)
        # Callable: dialable phone OR a named DM with a deliverable email.
        if callable_filter:
            has_phone = is_valid_us_phone(ph)
            em = lead["email"]
            has_dm_email = bool(lead["firstName"]) and "@" in em and "." in em.split("@")[-1]
            if not (has_phone or has_dm_email):
                dropped_uncallable += 1
                continue
        staged.append(lead)
        if len(staged) >= FREE_SOURCE_MAX_ROWS:
            break
    saved, already_in_db = save_to_supabase(staged)
    if saved:
        audit_log(user, "ingest_leads", "lead", None,
                  {"source": source, "saved": saved, "found": len(raw_leads or [])})
    print(f"[INGEST:{source}] found={len(raw_leads or [])} staged={len(staged)} "
          f"saved={saved} dupes={already_in_db} dropped={dropped_uncallable}")
    return {"found": len(raw_leads or []), "saved": saved,
            "alreadyInDb": already_in_db, "droppedUncallable": dropped_uncallable}

# ── VCC campaign helpers ───────────────────────────────────────────────────

# Three-touch email sequence. Touch 1 fires when a caller marks "no answer
# / voicemail" with the email-followup box checked (or via the EOD batch).
# Touches 2 and 3 are auto-fired by process_email_sequences in the bg loop
# at +3 days and +7 days. After Touch 3, a 5-day reply window passes, then
# the lead is released back to the dialer pool with an audit note in the
# notes field showing the sequence ran.
DEFAULT_EMAIL_TEMPLATES = {
    "tried-to-call": {  # Touch 1, Day 0 — initial outreach
        "subject": "Quick question, {first_name}",
        "body":
            "Hi {first_name},\n\n"
            "Tried reaching you today about cleaning at {company} — didn't catch you. "
            "Vision Cleaning specializes in {industry_phrase}{state_phrase}, and I wanted to get you a quote either way.\n\n"
            "If a call is hard to schedule, just hit reply with:\n"
            "  • Square footage at {company}\n"
            "  • Cleaning frequency you'd want (daily / weekly / 2x weekly)\n"
            "  • Rough monthly budget\n\n"
            "I'll come back inside 24 hours with a tailored quote and next steps. "
            "If cleaning isn't on your radar right now, just reply \"not now\" and I'll close the loop on my end.\n\n"
            "Thanks,\n"
            "{sender_name}\n"
            "Vision Cleaning Company\n"
            "{sender_line}\n\n"
            "—\nReply STOP to opt out. Vision Cleaning Company.",
    },
    "tried-to-call-2": {  # Touch 2, Day 3 — softer nudge, lower friction
        "subject": "Re: cleaning at {company}",
        "body":
            "Hi {first_name},\n\n"
            "Quick follow-up — I sent you a note a few days back about cleaning service at {company}. "
            "I know inboxes pile up, so I wanted to surface it once more.\n\n"
            "Most {industry_phrase} we work with end up saving 15-25% versus their current vendor, "
            "and the switch takes about a week. If you want me to put a quote together, just hit reply "
            "with your square footage and cleaning frequency. 30 seconds on your end.\n\n"
            "If now isn't the right time, no problem — a one-line \"not now\" closes the loop.\n\n"
            "Thanks,\n"
            "{sender_name}\n"
            "Vision Cleaning Company\n"
            "{sender_line}\n\n"
            "—\nReply STOP to opt out. Vision Cleaning Company.",
    },
    "tried-to-call-3": {  # Touch 3, Day 7 — final, "closing your file" frame
        "subject": "Closing your file at Vision Cleaning",
        "body":
            "Hi {first_name},\n\n"
            "Last note from me — I've reached out a couple of times about cleaning service at {company} "
            "and haven't heard back, so I'm closing your file on my end.\n\n"
            "If timing changes — new building, change of janitorial vendor, budget review — "
            "just reply to this email and I'll pick it back up. No pressure either way.\n\n"
            "Best of luck with the rest of the year.\n\n"
            "Thanks,\n"
            "{sender_name}\n"
            "Vision Cleaning Company\n"
            "{sender_line}\n\n"
            "—\nReply STOP to opt out. Vision Cleaning Company.",
    },
}
# Backward compat — older code paths reference the singular constant.
DEFAULT_EMAIL_TEMPLATE = DEFAULT_EMAIL_TEMPLATES["tried-to-call"]

def _email_template_vars(lead: dict) -> dict:
    """Build the placeholder dict used to render an email template against a lead."""
    first   = (lead.get("firstName") or "").strip() or "there"
    last    = (lead.get("lastName")  or "").strip()
    industry = (lead.get("industry") or "").strip()
    state    = (lead.get("state")    or "").strip()
    company  = (lead.get("company")  or "").strip() or "your facility"
    sender_name  = os.getenv("OUTREACH_SENDER_NAME", "Eric")
    sender_email = OUTREACH_EMAIL
    sender_phone = os.getenv("OUTREACH_SENDER_PHONE", "")
    sender_line  = f"{sender_phone}  •  {sender_email}" if sender_phone else sender_email
    return {
        "first_name":      first,
        "last_name":       last,
        "full_name":       (first + " " + last).strip() or "there",
        "company":         company,
        "industry":        industry,
        "industry_phrase": (industry.lower() + " facilities") if industry and industry.lower() != "unknown" else "commercial facilities",
        "state":           state,
        "state_phrase":    (" across " + state) if state else "",
        "city":            (lead.get("city")  or "").strip(),
        "title":           (lead.get("title") or "").strip(),
        "sender_name":     sender_name,
        "sender_email":    sender_email,
        "sender_phone":    sender_phone,
        "sender_line":     sender_line,
    }

def _render_email_template(template_str: str, vars_: dict) -> str:
    """Replace {placeholder} tokens with values. Unknown {tokens} are left as-is
    rather than crashed on so a typo doesn't break a send."""
    out = template_str or ""
    for k, v in vars_.items():
        out = out.replace("{" + k + "}", str(v))
    return out

def load_email_template(name: str = "tried-to-call") -> dict:
    """Load admin-edited template from app_settings, or return the default.
    Uses service-role headers to match the writer (RLS may block anon reads
    of rows written with service-role)."""
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/app_settings?key=eq.email_template_{name}&select=value",
            headers=SB_ADMIN_HEADERS, timeout=5,
        )
        rows = r.json() if r.status_code == 200 else []
        if isinstance(rows, list) and rows and rows[0].get("value"):
            data = json_lib.loads(rows[0]["value"])
            if isinstance(data, dict) and data.get("subject") and data.get("body"):
                return data
    except Exception as e:
        print(f"[EMAIL-TEMPLATE] load failed for '{name}': {e}")
    return dict(DEFAULT_EMAIL_TEMPLATES.get(name, DEFAULT_EMAIL_TEMPLATE))

# Cross-system suppression sync — when LeadFlow sees a "no/stop/not now"
# reply, push that signal to vlm-scraper so the email scraper also adds
# the address to its own suppression_list. Fire-and-forget; remote
# unavailability must not stall the reply-poll loop.
VLM_SCRAPER_URL     = os.getenv("VLM_SCRAPER_URL", "").rstrip("/")
VLM_SCRAPER_API_KEY = os.getenv("VLM_SCRAPER_API_KEY", "").strip()

# Soft-negative phrases (e.g. "not now", "maybe later") classify as
# negative for suppression purposes. Erring toward over-suppression is
# cheaper than the complaint cost of a second cold email after a soft no.
_NEGATIVE_PHRASES = (
    "not interested", "no thanks", "no thank you", "no, thanks",
    "please remove", "unsubscribe", "stop emailing", "stop contacting",
    "do not contact", "do not email", "take me off", "remove me",
    "already have", "have a vendor", "under contract", "go away",
    "leave me alone", "this is spam", "cease",
    "not now", "maybe later", "not at this time", "no interest",
)
_POSITIVE_PHRASES = (
    "interested", "tell me more", "how much", "pricing", "quote",
    "sounds good", "let's talk", "lets talk", "call me", "schedule",
    "available", "when can", "set up a", "would like", "send me",
    "more info", "more information",
)

def classify_reply_sentiment(text: str) -> str:
    """Cheap keyword classifier matching vlm-scraper's
    classifyReplySentiment so the two systems agree on what counts as a
    negative reply. Bare 'stop' / 'no' on its own line counts as negative
    — those are the bulk of soft-opt-outs."""
    t = (text or "").lower().strip()
    if not t:
        return "neutral"
    # Single-word "stop" or "no" replies (very common opt-out style).
    first_line = t.splitlines()[0].strip(" .!?")
    if first_line in ("stop", "no", "remove", "unsubscribe"):
        return "negative"
    for phrase in _NEGATIVE_PHRASES:
        if phrase in t:
            return "negative"
    for phrase in _POSITIVE_PHRASES:
        if phrase in t:
            return "positive"
    return "neutral"

def email_is_suppressed(email: str) -> bool:
    """Is this email on the global lead_suppressions table? Used as the
    last-line gate inside send_lead_to_campaign. Fail-open on error: a
    transient Supabase blip should not silently block legitimate sends.
    A duplicate email is much cheaper than the suppression table being
    unreachable during a cron tick."""
    if not email:
        return False
    addr = email.strip().lower()
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/lead_suppressions"
            f"?email=eq.{url_quote(addr)}&select=email&limit=1",
            headers=SB_ADMIN_HEADERS, timeout=5,
        )
        if r.status_code != 200:
            return False
        rows = r.json() if r.content else []
        return isinstance(rows, list) and len(rows) > 0
    except Exception as e:
        print(f"[SUPPRESSION] check failed for {addr}: {e}")
        return False

def add_to_suppression(email: str, reason: str = "negative_reply",
                       source: str = "imap_poller", notes: str = "") -> bool:
    """Upsert (email is PK) into lead_suppressions. Idempotent — calling
    twice with the same email is a no-op on the second call."""
    if not email:
        return False
    addr = email.strip().lower()
    try:
        r = req_lib.post(
            f"{SUPABASE_URL}/rest/v1/lead_suppressions",
            headers={**SB_ADMIN_HEADERS,
                     "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"email": addr, "reason": reason, "source": source,
                  "notes": (notes or None)},
            timeout=10,
        )
        return r.status_code in (200, 201, 204)
    except Exception as e:
        print(f"[SUPPRESSION] add failed for {addr}: {e}")
        return False

def _push_vlm_suppression(email: str, snippet: str = "") -> None:
    """Tell vlm-scraper to add this email to its own suppression_list for
    both pipelines. Fire-and-forget — vlm-scraper unavailability must
    not block our reply processing."""
    if not (VLM_SCRAPER_URL and VLM_SCRAPER_API_KEY and email):
        return
    try:
        req_lib.post(
            f"{VLM_SCRAPER_URL}/api/suppression/cross-sync",
            headers={"x-api-key": VLM_SCRAPER_API_KEY,
                     "Content-Type": "application/json"},
            json={"email": email.strip().lower(),
                  "reason": "negative_reply",
                  "source": "leadflow_imap",
                  "notes": (snippet or "")[:200]},
            timeout=5,
        )
    except Exception as e:
        print(f"[SUPPRESSION] vlm cross-sync failed for {email}: {e}")

def lead_already_in_campaign(lead_id: str, campaign: str = "tried-to-call") -> bool:
    """Suppression: don't re-send the same lead to the same campaign within
    CAMPAIGN_SUPPRESSION_DAYS. Returns True if a recent send is on file."""
    try:
        cutoff = (datetime.utcnow() - timedelta(days=CAMPAIGN_SUPPRESSION_DAYS)).isoformat()
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/audit_log"
            f"?action=eq.campaign_sent&resource_id=eq.{url_quote(str(lead_id))}"
            f"&created_at=gte.{cutoff}&select=id&limit=1",
            headers=SB_ADMIN_HEADERS, timeout=5,
        )
        rows = r.json() if r.status_code == 200 else []
        return bool(isinstance(rows, list) and rows)
    except Exception as e:
        print(f"[CAMPAIGN] suppression check failed: {e}")
        return False  # fail-open — better to risk a duplicate than miss a send

def send_lead_to_campaign(lead: dict, trigger: str, user: str,
                          campaign: str = "tried-to-call", step: int = 1,
                          bypass_suppression: bool = False):
    """Send a campaign email via Resend. step=1 is the initial caller-triggered
    touch; step=2 and step=3 are auto-fired by process_email_sequences using
    different templates. bypass_suppression skips lead_already_in_campaign so
    the auto-sequence isn't blocked by its own initial send. Returns
    (ok: bool, detail: str)."""
    if not RESEND_API_KEY:
        return (False, "RESEND_API_KEY not configured")
    if not lead.get("email"):
        return (False, "lead has no email")
    if not lead.get("firstName"):
        return (False, "lead has no firstName (run Find Decision Maker first)")
    if step not in (1, 2, 3):
        return (False, f"invalid step {step}")
    if step == 1 and not bypass_suppression and lead_already_in_campaign(lead.get("id"), campaign):
        return (False, f"already sent to '{campaign}' within last {CAMPAIGN_SUPPRESSION_DAYS} days")

    # Global do-not-contact gate. Applies to ALL steps (including
    # auto-fired follow-ups that pass bypass_suppression=True for the
    # campaign-dedup check). A negative reply earlier in the sequence
    # adds the email here, so step 2/3 won't fire to someone who said
    # "stop" after step 1.
    if email_is_suppressed(lead.get("email")):
        return (False, "lead is on global suppression list")

    # Belt-and-suspenders: never email a lead whose status was flipped
    # to do_not_contact (manual flag, or set by IMAP poller before this
    # tick's suppression_list write committed).
    if (lead.get("status") or "").lower() == "do_not_contact":
        return (False, "lead.status is do_not_contact")

    template_name = "tried-to-call" if step == 1 else f"tried-to-call-{step}"
    tpl   = load_email_template(template_name)
    vars_ = _email_template_vars(lead)
    subject   = _render_email_template(tpl.get("subject"), vars_)
    body_text = _render_email_template(tpl.get("body"),    vars_)

    from_name  = os.getenv("OUTREACH_SENDER_NAME", OUTREACH_NAME)
    from_email = OUTREACH_EMAIL
    reply_to   = OUTREACH_REPLY_TO or from_email  # replies hit this inbox; IMAP poller watches it
    payload = {
        "from":    f"{from_name} <{from_email}>",
        "to":      [lead["email"]],
        "subject": subject,
        "text":    body_text,
        "reply_to": reply_to,
        # Tags survive in Resend's webhook payload — we use them to map events
        # back to lead_id without keeping a separate mapping table.
        "tags": [
            {"name": "campaign", "value": campaign},
            {"name": "step",     "value": str(step)},
            {"name": "lead_id",  "value": str(lead.get("id"))},
            {"name": "trigger",  "value": trigger.replace(" ", "_")[:40]},
        ],
    }
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type":  "application/json",
    }
    try:
        r = req_lib.post("https://api.resend.com/emails", headers=headers, json=payload, timeout=15)
        if r.status_code not in (200, 201, 202):
            err = r.text[:300]
            print(f"[CAMPAIGN] Resend rejected HTTP {r.status_code}: {err}")
            return (False, f"Resend {r.status_code}: {err}")
        resend_id = ""
        try:
            resend_id = (r.json() or {}).get("id", "")
        except Exception:
            pass
    except Exception as e:
        print(f"[CAMPAIGN] network error posting to Resend: {e}")
        return (False, f"network error: {e}")

    audit_log(user, "campaign_sent", "lead", lead.get("id"), {
        "campaign": campaign, "trigger": trigger, "step": step,
        "to_email": lead.get("email"), "company": lead.get("company"),
        "resend_id": resend_id,
    })

    # Schedule the next sequence event:
    #   step 1 → fire Touch 2 in 3 days
    #   step 2 → fire Touch 3 in 4 days  (i.e. day 7 from initial)
    #   step 3 → release to dialer pool in 5 days (final reply window)
    next_offset_days = {1: 3, 2: 4, 3: 5}[step]
    next_followup = (datetime.utcnow() + timedelta(days=next_offset_days)).date().isoformat()
    try:
        req_lib.patch(
            f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(str(lead.get('id')))}",
            headers=SB_HEADERS,
            json={
                "status":           "awaiting_email_reply",
                "nextfollowup":     next_followup,
                "followupsequence": "email_followup",
                "followupstep":     step,
                "updatedAt":        datetime.utcnow().isoformat(),
            },
            timeout=10,
        )
    except Exception as e:
        # Don't fail the send if the status patch fails — Resend already has the email
        print(f"[CAMPAIGN] post-send status patch failed for lead {lead.get('id')}: {e}")

    print(f"[CAMPAIGN] sent step {step} to lead {lead.get('id')} via {trigger}")
    return (True, f"sent step {step}")

# ── IMAP reply poller ─────────────────────────────────────────────────────
# Watches the campaign sender's inbox for replies to "tried to call you"
# emails. When a real reply arrives, looks up the lead by sender email,
# flips status from awaiting_email_reply → interested, prepends the reply
# snippet to the lead's notes, fires a Slack ping, and marks the email read.
# Auto-replies (out-of-office) are suppressed so they don't false-flip leads.

_imap_poll_lock = threading.Lock()  # don't run two polls concurrently
_imap_poll_state = {"last_run": None, "last_result": None}

def _decode_header_str(raw):
    """IMAP header values can be MIME-encoded (=?utf-8?B?...?=). Decode safely."""
    if not raw:
        return ""
    try:
        parts = decode_header(raw)
        return "".join(
            (p.decode(enc or "utf-8", errors="replace") if isinstance(p, bytes) else p)
            for p, enc in parts
        )
    except Exception:
        return str(raw)

def _extract_from_email(from_header: str) -> str:
    """Pull the bare email out of 'Sarah Chen <sarah@example.com>'."""
    if not from_header:
        return ""
    m = re.search(r"<([^>]+)>", from_header)
    addr = m.group(1) if m else from_header
    return addr.strip().lower()

def _is_auto_reply(subject: str, body: str, headers: dict) -> bool:
    """Catch the common auto-reply patterns so we don't false-flip leads."""
    if headers.get("auto-submitted") and headers.get("auto-submitted").lower() != "no":
        return True
    if headers.get("x-auto-response-suppress"):
        return True
    if headers.get("x-autoreply") or headers.get("x-autorespond"):
        return True
    blob = f"{subject or ''} {body or ''}".lower()
    return any(m in blob for m in AUTO_REPLY_MARKERS)

def _extract_body_snippet(msg, max_len: int = 400) -> str:
    """Pull a short text preview from a multipart email."""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                disp  = str(part.get("Content-Disposition") or "")
                if ctype == "text/plain" and "attachment" not in disp:
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode(part.get_content_charset() or "utf-8", errors="replace").strip()[:max_len]
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                return payload.decode(msg.get_content_charset() or "utf-8", errors="replace").strip()[:max_len]
    except Exception as e:
        print(f"[IMAP-POLL] body extract failed: {e}")
    return ""

def _release_lead_from_email_seq(lead: dict, sent_count: int):
    """Hand a lead back to the dialer pool with an audit trail in the notes
    field showing how many emails fired before release. The caller seeing
    this lead in the dialer will know exactly where it came from."""
    lead_id = lead.get("id")
    if not lead_id:
        return False
    today = datetime.utcnow().date().isoformat()
    plural = "s" if sent_count != 1 else ""
    summary = f"📧 Email sequence complete — {sent_count} email{plural} sent, no reply. Returned to dialer pool {today}."
    existing_notes = (lead.get("notes") or "").strip()
    new_notes = (summary + "\n\n" + existing_notes) if existing_notes else summary
    try:
        pr = req_lib.patch(
            f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(str(lead_id))}",
            headers={**SB_HEADERS, "Prefer": "return=minimal"},
            json={
                "status":           "new",
                "followupsequence": None,
                "followupstep":     None,
                "nextfollowup":     None,
                "notes":            new_notes,
                "updatedAt":        datetime.utcnow().isoformat(),
            },
            timeout=10,
        )
        return pr.status_code in (200, 204)
    except Exception as e:
        print(f"[EMAIL-SEQ] release failed for {lead_id}: {e}")
        return False

def process_email_sequences():
    """Drive the 3-touch email follow-up sequence (replaces the old single-
    touch release_stale_email_leads). Runs every bg-loop cycle.

      Touch 1 (Day 0)  — caller-triggered via send_lead_to_campaign(step=1)
      Touch 2 (Day 3)  — auto-fired here when followupstep=1 and due
      Touch 3 (Day 7)  — auto-fired here when followupstep=2 and due
      Release (Day 12) — hand back to dialer when followupstep=3 and due

    Legacy leads (followupsequence='email_followup' but followupstep null,
    pre-sequence-feature data) fall back to the original 7-day release
    behavior so we don't strand them."""
    today_iso = datetime.utcnow().date().isoformat()
    legacy_cutoff = (datetime.utcnow() - timedelta(days=CAMPAIGN_SUPPRESSION_DAYS)).isoformat()
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads"
            f"?status=eq.awaiting_email_reply"
            f"&select=id,email,firstName,lastName,company,industry,state,city,title,notes,"
            f"followupsequence,followupstep,nextfollowup,updatedAt"
            f"&limit=500",
            headers=SB_HEADERS, timeout=15,
        )
        leads = r.json() if r.status_code == 200 else []
        if not isinstance(leads, list) or len(leads) == 0:
            return {"sent_2": 0, "sent_3": 0, "released": 0}
    except Exception as e:
        print(f"[EMAIL-SEQ] query failed: {e}")
        return {"sent_2": 0, "sent_3": 0, "released": 0}

    sent_2 = sent_3 = released = 0
    for lead in leads:
        step = lead.get("followupstep")
        nf   = lead.get("nextfollowup") or ""
        seq  = lead.get("followupsequence") or ""

        # Legacy path — no step tracked. Release by old 7-day-since-update rule.
        if step is None or seq != "email_followup":
            updated = lead.get("updatedAt") or ""
            if updated and updated < legacy_cutoff:
                if _release_lead_from_email_seq(lead, sent_count=1):
                    released += 1
            continue

        # Modern path — only act on entries whose nextfollowup has come due.
        if not nf or nf > today_iso:
            continue

        if step == 1:
            ok, _ = send_lead_to_campaign(lead, trigger="auto_followup_2",
                                          user="auto_sequencer",
                                          step=2, bypass_suppression=True)
            if ok: sent_2 += 1
        elif step == 2:
            ok, _ = send_lead_to_campaign(lead, trigger="auto_followup_3",
                                          user="auto_sequencer",
                                          step=3, bypass_suppression=True)
            if ok: sent_3 += 1
        elif step == 3:
            if _release_lead_from_email_seq(lead, sent_count=3):
                released += 1

    if sent_2 or sent_3 or released:
        audit_log("auto_sequencer", "email_sequence_tick", "lead", None, {
            "sent_step_2": sent_2, "sent_step_3": sent_3, "released": released,
        })
        print(f"[EMAIL-SEQ] cycle: sent_2={sent_2} sent_3={sent_3} released={released}")
    return {"sent_2": sent_2, "sent_3": sent_3, "released": released}

# Backward-compat alias — the manual admin endpoint and the bg loop both
# expect this name. The new function name is more accurate (it now drives
# the sequence, not just stale releases) but we keep the alias to avoid
# breaking callers.
def release_stale_email_leads():
    res = process_email_sequences()
    return res.get("released", 0)

def imap_poll_replies():
    """Connect to IMAP, scan unread messages, match to awaiting-email leads.
    Returns a stats dict for the manual endpoint + activity panel."""
    if not (IMAP_SERVER and IMAP_USERNAME and IMAP_PASSWORD):
        return {"ok": False, "error": "IMAP not configured"}
    if not _imap_poll_lock.acquire(blocking=False):
        return {"ok": False, "error": "another poll is running"}

    stats = {"checked": 0, "matched": 0, "auto_replies_skipped": 0, "no_match": 0, "errors": 0}
    started = datetime.utcnow()
    try:
        try:
            # 30s timeout — without it, a slow Gmail handshake hangs forever
            # and never releases the poll lock. Python 3.9+ supports timeout=
            # on IMAP4_SSL directly; fall back to socket.setdefaulttimeout for
            # older runtimes (Railway runs 3.11+ so this is belt + suspenders).
            try:
                mbox = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT, timeout=30)
            except TypeError:
                import socket as _socket
                _socket.setdefaulttimeout(30)
                mbox = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
            mbox.login(IMAP_USERNAME, IMAP_PASSWORD)
            mbox.select(IMAP_FOLDER)
        except Exception as e:
            stats["errors"] += 1
            return {"ok": False, "error": f"IMAP connect failed: {type(e).__name__}: {e}", "stats": stats}

        try:
            # Only search the suppression window — older unread emails in a
            # busy info@ inbox shouldn't be re-processed every cycle. IMAP
            # SINCE format: "01-Jan-2026" (3-letter month, no leading 0 fine).
            since_dt = datetime.utcnow() - timedelta(days=CAMPAIGN_SUPPRESSION_DAYS)
            since_str = since_dt.strftime("%d-%b-%Y")
            typ, data = mbox.search(None, f'(UNSEEN SINCE {since_str})')
            if typ != "OK":
                return {"ok": False, "error": f"IMAP search failed: {typ}", "stats": stats}
            ids = data[0].split() if data and data[0] else []
            # Cap per-poll work — if 200 messages match, process the newest 100
            # and let the next poll catch the rest.
            MAX_PER_POLL = 100
            if len(ids) > MAX_PER_POLL:
                stats["truncated_from"] = len(ids)
                ids = ids[-MAX_PER_POLL:]  # IMAP returns oldest-first; tail = newest
        except Exception as e:
            return {"ok": False, "error": f"IMAP search exception: {e}", "stats": stats}

        for msg_id in ids:
            stats["checked"] += 1
            try:
                typ, msg_data = mbox.fetch(msg_id, "(RFC822)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    stats["errors"] += 1
                    continue
                raw = msg_data[0][1]
                msg = email_lib.message_from_bytes(raw)

                from_addr = _extract_from_email(_decode_header_str(msg.get("From", "")))
                subject   = _decode_header_str(msg.get("Subject", ""))
                headers   = {k.lower(): str(v) for k, v in msg.items()}
                body      = _extract_body_snippet(msg)
                to_addr   = _decode_header_str(msg.get("To", "")).lower()

                if _is_auto_reply(subject, body, headers):
                    stats["auto_replies_skipped"] += 1
                    try: mbox.store(msg_id, "+FLAGS", "\\Seen")
                    except: pass
                    continue

                if not from_addr:
                    stats["no_match"] += 1
                    continue

                # Tight match: this must look like a genuine reply to one of
                # OUR sends, not just any email from a known lead. Require
                # ANY of: (a) subject prefixed Re:/RE:/etc., (b) In-Reply-To
                # header set, (c) addressed to our outreach address. If none
                # match, this is some other inbound email we shouldn't react to.
                is_re      = bool(re.match(r"^\s*(re|fw|fwd)\s*:", subject, re.IGNORECASE))
                has_in_reply = bool(headers.get("in-reply-to") or headers.get("references"))
                outreach_lower = (OUTREACH_EMAIL or "").lower()
                reply_to_lower = (OUTREACH_REPLY_TO or "").lower()
                addressed_to_us = bool(
                    (outreach_lower and outreach_lower in to_addr) or
                    (reply_to_lower and reply_to_lower in to_addr)
                )
                if not (is_re or has_in_reply or addressed_to_us):
                    stats.setdefault("not_a_reply", 0)
                    stats["not_a_reply"] += 1
                    continue

                # Find a lead awaiting email reply with this email address.
                # Prefer awaiting_email_reply; fall back to any lead with that email.
                try:
                    lr = req_lib.get(
                        f"{SUPABASE_URL}/rest/v1/leads"
                        f"?email=ilike.{url_quote(from_addr)}"
                        f"&order=updatedAt.desc&limit=5&select=id,company,firstName,lastName,status,assignedTo,notes",
                        headers=SB_HEADERS, timeout=10,
                    )
                    leads = lr.json() if lr.status_code == 200 else []
                except Exception as e:
                    print(f"[IMAP-POLL] lead lookup failed for {from_addr}: {e}")
                    stats["errors"] += 1
                    continue

                if not isinstance(leads, list) or not leads:
                    stats["no_match"] += 1
                    continue

                # Prefer awaiting_email_reply lead; fall back to most recent
                target = next((l for l in leads if l.get("status") == "awaiting_email_reply"), leads[0])
                lead_id = target.get("id")
                snippet = (body or "")[:300].replace("\n", " ").strip()
                sentiment = classify_reply_sentiment(body or "")
                is_negative = (sentiment == "negative")

                # Negative replies → status=do_not_contact + suppression
                # row so future sequence steps (and any other campaign)
                # won't re-touch this address. Positive/neutral keep the
                # legacy "interested" handoff so reps can follow up.
                new_status = "do_not_contact" if is_negative else "interested"
                note_prefix = "🛑 Negative reply" if is_negative else "📧 Reply"
                reply_note = (f"{note_prefix} ({datetime.utcnow().date().isoformat()}, "
                              f"sentiment={sentiment}): {snippet}")

                try:
                    req_lib.patch(
                        f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(str(lead_id))}",
                        headers=SB_HEADERS,
                        json={
                            "status":    new_status,
                            "notes":     (reply_note + "\n\n" + (target.get("notes") or ""))[:4000],
                            "updatedAt": datetime.utcnow().isoformat(),
                        },
                        timeout=10,
                    )
                except Exception as e:
                    print(f"[IMAP-POLL] lead update failed: {e}")
                    stats["errors"] += 1
                    continue

                # Suppress + cross-sync to vlm-scraper. Both are fire-and-
                # forget enough that a failure here doesn't block lead
                # status patching above.
                if is_negative:
                    add_to_suppression(
                        from_addr,
                        reason="negative_reply",
                        source="imap_poller",
                        notes=f"reply: {snippet[:200]}",
                    )
                    _push_vlm_suppression(from_addr, snippet)
                    stats["negative"] = stats.get("negative", 0) + 1

                audit_log("imap_poller", "campaign_event", "lead", lead_id, {
                    "event": "negative_reply" if is_negative else "reply",
                    "from": from_addr, "subject": subject[:200],
                    "snippet": snippet, "sentiment": sentiment,
                })
                send_slack(
                    ":no_entry_sign: Negative Reply" if is_negative else ":incoming_envelope: Email Reply",
                    (f"*{target.get('firstName','')} {target.get('lastName','')}* at "
                     f"*{target.get('company','')}* "
                     + ("said NO — added to suppression list. "
                        "Do NOT call." if is_negative else "replied.")),
                    fields=[
                        {"label": "From",      "value": from_addr},
                        {"label": "Sentiment", "value": sentiment},
                        {"label": "Assigned",  "value": target.get("assignedTo") or "unassigned"},
                        {"label": "Snippet",   "value": snippet[:200] or "(empty)"},
                    ],
                    actions=[{"label": "📋 Open LeadFlow",
                              "url": os.getenv("APP_URL", "https://leadflow-railway-production.up.railway.app"),
                              "style": "primary"}],
                )
                stats["matched"] += 1

                # Mark seen so we don't re-process
                try: mbox.store(msg_id, "+FLAGS", "\\Seen")
                except: pass

            except Exception as e:
                print(f"[IMAP-POLL] error processing msg {msg_id}: {e}")
                stats["errors"] += 1

        try: mbox.logout()
        except: pass

        return {"ok": True, "stats": stats, "took_ms": int((datetime.utcnow()-started).total_seconds()*1000)}
    finally:
        _imap_poll_state["last_run"] = datetime.utcnow().isoformat()
        _imap_poll_lock.release()

def _bg_maintenance_loop():
    """Background thread — two independent jobs every cycle:
       1. IMAP reply poll (skipped silently if IMAP not configured)
       2. Stale-email release (always runs — pulls leads out of limbo)
    Both are idempotent and cheap. Bundling keeps deployment simple."""
    imap_on = bool(IMAP_SERVER and IMAP_USERNAME and IMAP_PASSWORD)
    print(f"[BG] maintenance loop starting (every {IMAP_POLL_INTERVAL_MINUTES} min)"
          f" — IMAP={'on' if imap_on else 'off'}, stale-release=on")
    time.sleep(60)  # let app finish startup before first run
    while True:
        if imap_on:
            try:
                res = imap_poll_replies()
                _imap_poll_state["last_result"] = res
                if res.get("ok") and res.get("stats", {}).get("matched", 0) > 0:
                    print(f"[IMAP-POLL] matched {res['stats']['matched']} replies this cycle")
            except Exception as e:
                print(f"[IMAP-POLL] loop exception: {e}")
        # Drive the 3-touch email sequence: fire Touch 2/3 when due, release
        # to dialer pool after the final reply window. Legacy single-touch
        # leads fall back to the old 7-day release rule. Always runs so
        # leads never get permanently stuck in awaiting_email_reply.
        try:
            process_email_sequences()
        except Exception as e:
            print(f"[EMAIL-SEQ] auto-process exception: {e}")
        # Self-healing weekly dedupe — internal cooldown, no-ops most ticks.
        # Apollo (name+company) and phone-based sweeps share the same cooldown
        # row, so they run together; the cooldown is updated once at the end.
        try:
            run_phone_dedupe_sweep_if_due()
        except Exception as e:
            print(f"[PHONE-DEDUPE] loop exception: {e}")
        try:
            run_dedupe_sweep_if_due()
        except Exception as e:
            print(f"[DEDUPE-SWEEP] loop exception: {e}")
        # Daily-cadence hot-lead-at-risk digest. Internal cooldown so this is
        # a no-op until ≥20h since the last run; bundling the check here
        # keeps the bg-loop scheduling simple (one thread, one loop).
        try:
            run_hot_lead_digest_if_due()
        except Exception as e:
            print(f"[HOT-DIGEST] loop exception: {e}")
        # Daily once-called recycle — internal cooldown, no-ops most ticks.
        try:
            run_once_called_recycle_if_due()
        except Exception as e:
            print(f"[ONCE-RECYCLE] loop exception: {e}")
        time.sleep(IMAP_POLL_INTERVAL_MINUTES * 60)

# ── Apollo dedupe — shared detection + self-healing weekly sweep ─────────
def find_apollo_dupe_groups(leads: list) -> list:
    """Pure helper: group leads by (firstName, lastName, company) lowercase
    and return one entry per multi-row group. Each entry distinguishes the
    'keep' row from extras, and splits extras into safe-to-auto-delete vs
    borderline (has activity / assigned / status moved past 'new').

    Caller decides what to do with each group:
      - cleanup-preview reports both safe + borderline
      - weekly auto-sweep deletes safe extras, alerts on borderline groups

    Returns list of dicts:
      {'person', 'kept', 'safe_extras', 'risky_extras'}"""
    groups = {}
    for l in leads:
        first = (l.get("firstName") or "").strip().lower()
        last  = (l.get("lastName")  or "").strip().lower()
        comp  = (l.get("company")   or "").strip().lower()
        if not (first and last and comp):
            continue
        groups.setdefault((first, last, comp), []).append(l)

    out = []
    for key, group in groups.items():
        if len(group) < 2:
            continue
        # Most call activity wins; ties → oldest createdAt (preserves the
        # original pull). Anything else in the group is an extra.
        sorted_group = sorted(
            group,
            key=lambda x: (-(x.get("total_calls") or 0), x.get("createdAt") or ""),
        )
        keep = sorted_group[0]
        safe_extras  = []
        risky_extras = []
        for e in sorted_group[1:]:
            unworked = (
                not e.get("assignedTo") and
                (e.get("total_calls") or 0) == 0 and
                (e.get("status") or "new") == "new"
            )
            (safe_extras if unworked else risky_extras).append(e)
        out.append({
            "person":        f"{key[0].title()} {key[1].title()} @ {keep.get('company') or '?'}",
            "kept":          keep,
            "safe_extras":   safe_extras,
            "risky_extras":  risky_extras,
        })
    return out

def _fetch_all_leads_for_dedupe() -> list:
    """Paginated full-table read for the dedupe sweep. Mirrors the loop
    pattern in cleanup_preview but returns only the columns dedupe needs.
    Includes `phone` so the same fetch powers both Apollo (name+company) and
    phone-based dedupe."""
    leads = []
    PAGE = 1000
    MAX_PAGES = 50
    for page in range(MAX_PAGES):
        start = page * PAGE
        end   = start + PAGE - 1
        try:
            r = req_lib.get(
                f"{SUPABASE_URL}/rest/v1/leads"
                f"?select=id,company,firstName,lastName,phone,createdAt,assignedTo,status,total_calls"
                f"&order=id.asc",
                headers={**SB_HEADERS, "Range-Unit": "items", "Range": f"{start}-{end}"},
                timeout=30,
            )
            batch = r.json() if r.status_code in (200, 206) else []
            if not isinstance(batch, list) or len(batch) == 0:
                break
            leads.extend(batch)
            if len(batch) < PAGE:
                break
        except Exception as e:
            print(f"[DEDUPE-SWEEP] fetch page {page} failed: {e}")
            break
    return leads

# Statuses we never auto-delete a duplicate of — the row carries real
# engagement value (or compliance signal) that mustn't be discarded.
ENGAGED_STATUSES = {"interested", "interested_no_dm", "converted", "callback", "awaiting_email_reply", "do_not_contact"}

def find_phone_dupe_groups(leads: list) -> list:
    """Group by exact phone match. For each group, pick the row to keep
    (engaged status wins → highest total_calls → newest) and split the
    extras into safe-to-delete (non-engaged) vs risky (engaged — preserve).

    Returns list of {phone, kept, safe_extras, risky_extras}."""
    by_phone = {}
    for l in leads:
        ph = (l.get("phone") or "").strip()
        if ph:
            by_phone.setdefault(ph, []).append(l)
    out = []
    for ph, group in by_phone.items():
        if len(group) < 2:
            continue
        def _priority(l):
            engaged = (l.get("status") or "") in ENGAGED_STATUSES
            return (engaged, l.get("total_calls") or 0, l.get("createdAt") or "")
        sorted_group = sorted(group, key=_priority, reverse=True)
        keep = sorted_group[0]
        safe_extras  = []
        risky_extras = []
        for e in sorted_group[1:]:
            if (e.get("status") or "") in ENGAGED_STATUSES:
                risky_extras.append(e)
            else:
                safe_extras.append(e)
        out.append({
            "phone":         ph,
            "kept":          keep,
            "safe_extras":   safe_extras,
            "risky_extras":  risky_extras,
        })
    return out

def _reparent_call_outcomes(keep_to_extras: dict) -> int:
    """Move call_outcomes rows from extra leadIds → keep leadId, in chunks of 50
    per kept lead. Returns total rows re-parented. Shared by manual cleanup
    and the auto sweep so dial history never orphans on dedupe.

    After re-parenting, the kept lead's total_calls + last_called_at on the
    leads table are now stale (history inherited but counters didn't move).
    Refresh them from the new call_outcomes count so the next time the lead
    surfaces in the dialer, the card shows the correct '4 total · last call
    Mar 12' instead of the pre-merge numbers."""
    reparented = 0
    for keep_id, extra_ids in keep_to_extras.items():
        for i in range(0, len(extra_ids), 50):
            chunk = [str(x) for x in extra_ids[i:i+50]]
            try:
                rp = req_lib.patch(
                    f"{SUPABASE_URL}/rest/v1/call_outcomes?leadId=in.({','.join(chunk)})",
                    headers={**SB_HEADERS, "Prefer": "return=minimal"},
                    json={"leadId": keep_id},
                    timeout=30,
                )
                if rp.status_code in (200, 204):
                    reparented += len(chunk)
                else:
                    print(f"[DEDUPE-SWEEP] reparent HTTP {rp.status_code}: {rp.text[:200]}")
            except Exception as e:
                print(f"[DEDUPE-SWEEP] reparent exception: {e}")
        # Recompute the keeper's counters now that history has moved over.
        try:
            cr = req_lib.get(
                f"{SUPABASE_URL}/rest/v1/call_outcomes?leadId=eq.{keep_id}&select=calledAt"
                f"&order=calledAt.desc&limit=1",
                headers={**SB_HEADERS, "Prefer": "count=exact"}, timeout=15,
            )
            cr_range = cr.headers.get("content-range", "*/0")
            new_count = int(cr_range.split("/")[-1]) if "/" in cr_range else None
            last_at_rows = cr.json() if cr.status_code in (200, 206) else []
            new_last = (last_at_rows[0].get("calledAt") if last_at_rows else None)
            if new_count is not None:
                patch = {"total_calls": new_count}
                if new_last:
                    patch["last_called_at"] = new_last
                req_lib.patch(
                    f"{SUPABASE_URL}/rest/v1/leads?id=eq.{keep_id}",
                    headers={**SB_HEADERS, "Prefer": "return=minimal"},
                    json=patch, timeout=15,
                )
        except Exception as e:
            print(f"[DEDUPE-SWEEP] counter refresh failed for keep_id={keep_id}: {e}")
    return reparented

# Hard cap per sweep so a runaway can't wipe the table. Configurable.
PHONE_DEDUPE_MAX_PER_RUN = int(os.getenv("PHONE_DEDUPE_MAX_PER_RUN", "300"))

def run_phone_dedupe_sweep_if_due():
    """Weekly self-healing phone dedupe. Same cooldown row as Apollo dedupe
    (`last_dedupe_sweep`) so the two stay in lock-step — one fetch, two
    sweeps per cycle. Skips silently if not due."""
    # Re-uses the Apollo cooldown row so the two sweeps share a tick.
    # If Apollo just ran, this is the same cycle and we run too.
    if not _dedupe_sweep_due():
        return
    print("[PHONE-DEDUPE] starting weekly phone-dedup sweep")
    leads = _fetch_all_leads_for_dedupe()
    if not leads:
        print("[PHONE-DEDUPE] empty fetch — aborting")
        return

    groups = find_phone_dupe_groups(leads)
    auto_ids   = []
    reparent   = {}  # keep_id -> [extra_id, ...]
    borderline = [g for g in groups if g["risky_extras"]]
    for g in groups:
        if not g["safe_extras"]:
            continue
        keep_id = g["kept"]["id"]
        for e in g["safe_extras"]:
            auto_ids.append(e["id"])
            reparent.setdefault(keep_id, []).append(e["id"])
            if len(auto_ids) >= PHONE_DEDUPE_MAX_PER_RUN:
                break
        if len(auto_ids) >= PHONE_DEDUPE_MAX_PER_RUN:
            print(f"[PHONE-DEDUPE] hit per-run cap ({PHONE_DEDUPE_MAX_PER_RUN}) — stopping")
            break

    reparented = 0
    deleted = 0
    if auto_ids:
        # Re-parent call history before delete so we don't orphan call_outcomes.
        reparented = _reparent_call_outcomes(reparent)
        BATCH = 50
        for i in range(0, len(auto_ids), BATCH):
            batch = [str(x) for x in auto_ids[i:i+BATCH]]
            try:
                r = req_lib.delete(
                    f"{SUPABASE_URL}/rest/v1/leads?id=in.({','.join(batch)})",
                    headers={**SB_HEADERS, "Prefer": "return=minimal"},
                    timeout=30,
                )
                if r.status_code in (200, 204):
                    deleted += len(batch)
                else:
                    print(f"[PHONE-DEDUPE] batch delete HTTP {r.status_code}: {r.text[:200]}")
            except Exception as e:
                print(f"[PHONE-DEDUPE] batch delete exception: {e}")
        audit_log("system", "leads_cleanup", "lead", None, {
            "reason":   "weekly_phone_dedupe_sweep",
            "deleted":  deleted,
            "reparented_calls": reparented,
            "borderline_groups": len(borderline),
        })

    if deleted > 0 or borderline:
        fields = [{"label": "Auto-cleaned",
                   "value": f":white_check_mark: {deleted} duplicate phones "
                            f"(reparented {reparented} call history rows)"}]
        if borderline:
            sample_lines = []
            for b in borderline[:5]:
                others = ", ".join(str(o["id"])[:8] for o in b["risky_extras"])
                sample_lines.append(f"• `{b['phone']}` (kept `{str(b['kept']['id'])[:8]}`, engaged extras: `{others}`)")
            more = f"\n…and {len(borderline) - 5} more" if len(borderline) > 5 else ""
            fields.append({
                "label": f"Need review ({len(borderline)})",
                "value": "\n".join(sample_lines) + more,
            })
        summary = f"Auto-cleaned {deleted} duplicate-phone rows"
        if borderline:
            summary += f" — {len(borderline)} group(s) need review (engaged duplicates preserved)"
        send_slack(":broom: Weekly phone-dedupe sweep", summary, fields=fields)

    # Defense-in-depth: record cooldown here too. The Apollo sweep also
    # records at its end, but if Apollo errors out, phone sweep alone has
    # already paid for the dedupe + Slack noise — don't let it re-fire next
    # tick. Shared cooldown row.
    _record_dedupe_sweep_run()
    print(f"[PHONE-DEDUPE] done — deleted={deleted} reparented={reparented} borderline={len(borderline)}")

def _dedupe_sweep_due() -> bool:
    """Read app_settings.last_dedupe_sweep; True if ≥ INTERVAL_DAYS have passed
    (or no row yet). Conservative on errors — returns False to avoid
    double-firing if the cooldown row is unreadable."""
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/app_settings?key=eq.last_dedupe_sweep&select=value",
            headers=SB_HEADERS, timeout=10,
        )
        if r.status_code != 200:
            return False
        rows = r.json()
        if not rows:
            return True  # never run → due
        last_iso = (rows[0].get("value") or "").replace("Z", "")
        try:
            last_dt = datetime.fromisoformat(last_iso)
        except Exception:
            return True  # corrupted timestamp → re-run
        return (datetime.utcnow() - last_dt).total_seconds() >= DEDUPE_SWEEP_INTERVAL_DAYS * 86400
    except Exception as e:
        print(f"[DEDUPE-SWEEP] cooldown check failed: {e}")
        return False

HOT_DIGEST_INTERVAL_HOURS = int(os.getenv("HOT_DIGEST_INTERVAL_HOURS", "24"))
HOT_LEAD_STALE_DAYS       = int(os.getenv("HOT_LEAD_STALE_DAYS", "5"))
# Off by default — the digest is Slack-spammy at 1-caller scale and the
# admin can already see overdue callbacks on the dashboard. Set
# HOT_DIGEST_ENABLED=1 in Railway env vars to turn it back on.
HOT_DIGEST_ENABLED        = os.getenv("HOT_DIGEST_ENABLED", "0") == "1"

def _hot_digest_due() -> bool:
    """Returns True if HOT_DIGEST_INTERVAL_HOURS have elapsed since the last
    digest (or never run). Same cooldown pattern as the dedup sweep — read
    from app_settings, fail closed on errors so we don't double-post."""
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/app_settings?key=eq.last_hot_digest&select=value",
            headers=SB_HEADERS, timeout=10,
        )
        if r.status_code != 200: return False
        rows = r.json()
        if not rows: return True
        last_iso = (rows[0].get("value") or "").replace("Z", "")
        try:
            last_dt = datetime.fromisoformat(last_iso)
        except Exception:
            return True
        return (datetime.utcnow() - last_dt).total_seconds() >= HOT_DIGEST_INTERVAL_HOURS * 3600
    except Exception as e:
        print(f"[HOT-DIGEST] cooldown check failed: {e}")
        return False

def _record_hot_digest_run():
    """Upsert cooldown row. Logs the status code on non-2xx so we can spot
    silent failures (which were the original cause of dedupe firing every
    10 min). req_lib.post doesn't raise on HTTP 4xx/5xx — must check r.status_code."""
    try:
        r = req_lib.post(
            f"{SUPABASE_URL}/rest/v1/app_settings?on_conflict=key",
            headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"key": "last_hot_digest", "value": datetime.utcnow().isoformat() + "Z"},
            timeout=10,
        )
        if r.status_code not in (200, 201, 204):
            print(f"[HOT-DIGEST] cooldown upsert HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[HOT-DIGEST] failed to record last-run: {e}")

def run_hot_lead_digest_if_due():
    """Surface engaged leads (interested / callback) that have gone quiet —
    last_called_at older than HOT_LEAD_STALE_DAYS — and post them to Slack so
    the admin can re-prioritize. Quiet by default: if there's nothing to flag,
    skip Slack entirely. Also catches overdue callbacks (callbackDate < today).

    OFF by default (HOT_DIGEST_ENABLED env var). At 1-caller scale the admin
    already sees this on the dashboard; turning it on means daily Slack noise."""
    if not HOT_DIGEST_ENABLED:
        return
    if not _hot_digest_due():
        return
    cutoff_dt   = datetime.utcnow() - timedelta(days=HOT_LEAD_STALE_DAYS)
    cutoff_iso  = cutoff_dt.isoformat()
    today_str   = datetime.utcnow().strftime("%Y-%m-%d")
    try:
        # Engaged + stale (not contacted within window). nullsfirst on
        # last_called_at would also catch "never called engaged leads."
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads?select=id,company,phone,status,assignedTo,"
            f"last_called_at,callbackDate,score,total_calls"
            f"&status=in.(interested,callback)"
            f"&or=(last_called_at.is.null,last_called_at.lt.{cutoff_iso})",
            headers=SB_HEADERS, timeout=30,
        )
        stale = r.json() if r.status_code == 200 else []

        # Overdue callbacks (callbackDate < today and status not converted)
        r2 = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads?select=id,company,phone,status,assignedTo,"
            f"callbackDate,last_called_at,score"
            f"&callbackDate=lt.{today_str}&callbackDate=neq.&status=neq.converted",
            headers=SB_HEADERS, timeout=30,
        )
        overdue_cb = r2.json() if r2.status_code == 200 else []
    except Exception as e:
        print(f"[HOT-DIGEST] fetch exception: {e}")
        return

    if not stale and not overdue_cb:
        # Nothing to say. Still record so we don't re-check every 10 min.
        _record_hot_digest_run()
        return

    fields = []
    if stale:
        stale_sorted = sorted(stale, key=lambda l: -(l.get("score") or 0))[:8]
        lines = []
        for l in stale_sorted:
            when = (l.get("last_called_at") or "never")[:10] if l.get("last_called_at") else "never"
            lines.append(f"• `{l.get('id')}` {l.get('company') or '?'} — {l.get('status')} · last contact {when}"
                         f"{' · ' + l['assignedTo'] if l.get('assignedTo') else ''}")
        if len(stale) > 8:
            lines.append(f"…and {len(stale) - 8} more")
        fields.append({"label": f"💤 Interested/callback gone quiet ({len(stale)})",
                       "value": "\n".join(lines)})
    if overdue_cb:
        cb_sorted = sorted(overdue_cb, key=lambda l: l.get("callbackDate") or "")[:8]
        lines = []
        for l in cb_sorted:
            lines.append(f"• `{l.get('id')}` {l.get('company') or '?'} — callback {l.get('callbackDate')}"
                         f"{' · ' + l['assignedTo'] if l.get('assignedTo') else ''}")
        if len(overdue_cb) > 8:
            lines.append(f"…and {len(overdue_cb) - 8} more")
        fields.append({"label": f"⏰ Overdue callbacks ({len(overdue_cb)})",
                       "value": "\n".join(lines)})

    send_slack(
        ":fire: Hot leads at risk",
        f"{len(stale)} engaged lead(s) gone quiet + {len(overdue_cb)} overdue callback(s)",
        fields=fields,
    )
    _record_hot_digest_run()
    print(f"[HOT-DIGEST] posted: {len(stale)} stale + {len(overdue_cb)} overdue")

# ── Nightly once-called recycle ──────────────────────────────────────────────
# Rotate leads that were dialed exactly once with NO contact (no-answer /
# voicemail) back into the shared pool after a grace window, so a single missed
# dial doesn't park a lead under one caller forever. Engaged outcomes
# (interested / interested_no_dm / callback / converted) are never touched —
# only status="no_answer" (voicemail maps to no_answer too). Runs on a daily
# cadence from the bg-maintenance loop with a DB-tracked cooldown.
ONCE_CALLED_RECYCLE_ENABLED        = os.getenv("ONCE_CALLED_RECYCLE_ENABLED", "1") == "1"
# Don't yank a lead the instant the caller logs one no-answer — give them a
# window to follow up themselves first. Default 48h.
ONCE_CALLED_RECYCLE_GRACE_HOURS    = int(os.getenv("ONCE_CALLED_RECYCLE_GRACE_HOURS", "48"))
ONCE_CALLED_RECYCLE_INTERVAL_HOURS = int(os.getenv("ONCE_CALLED_RECYCLE_INTERVAL_HOURS", "20"))

def _once_called_recycle_due() -> bool:
    """True if INTERVAL_HOURS elapsed since last run (or never). Same cooldown
    pattern as the hot digest — fail closed so we don't double-run."""
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/app_settings?key=eq.last_once_called_recycle&select=value",
            headers=SB_HEADERS, timeout=10,
        )
        if r.status_code != 200: return False
        rows = r.json()
        if not rows: return True
        last_iso = (rows[0].get("value") or "").replace("Z", "")
        try:
            last_dt = datetime.fromisoformat(last_iso)
        except Exception:
            return True
        return (datetime.utcnow() - last_dt).total_seconds() >= ONCE_CALLED_RECYCLE_INTERVAL_HOURS * 3600
    except Exception as e:
        print(f"[ONCE-RECYCLE] cooldown check failed: {e}")
        return False

def _record_once_called_recycle_run():
    try:
        r = req_lib.post(
            f"{SUPABASE_URL}/rest/v1/app_settings?on_conflict=key",
            headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"key": "last_once_called_recycle", "value": datetime.utcnow().isoformat() + "Z"},
            timeout=10,
        )
        if r.status_code not in (200, 201, 204):
            print(f"[ONCE-RECYCLE] cooldown upsert HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[ONCE-RECYCLE] failed to record last-run: {e}")

def run_once_called_recycle_if_due():
    """Unassign once-called, never-contacted leads back to the pool. No-op
    unless enabled + due. Quiet (no Slack) when nothing qualifies."""
    if not ONCE_CALLED_RECYCLE_ENABLED:
        return
    if not _once_called_recycle_due():
        return
    cutoff_iso = (datetime.utcnow() - timedelta(hours=ONCE_CALLED_RECYCLE_GRACE_HOURS)).isoformat()
    try:
        # status=no_answer (covers voicemail too — both map to no_answer),
        # exactly 1 call, currently assigned, last dial older than the grace
        # window. last_called_at=lt also excludes nulls.
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads"
            f"?select=id,company,assignedTo,total_calls,last_called_at,status"
            f"&status=eq.no_answer&total_calls=eq.1&assignedTo=neq."
            f"&last_called_at=lt.{cutoff_iso}",
            headers=SB_HEADERS, timeout=30,
        )
        leads = r.json() if r.status_code == 200 else []
        if not isinstance(leads, list):
            leads = []
        recycled = [{"id": l["id"], "company": l.get("company"),
                     "was_assigned_to": l.get("assignedTo")} for l in leads if l.get("id")]
        ids = [item["id"] for item in recycled]
        if not ids:
            _record_once_called_recycle_run()
            return
        # Bulk unassign in chunks (keep the in.() URL well under limits).
        now_iso = datetime.utcnow().isoformat()
        done = 0
        for i in range(0, len(ids), 200):
            chunk = ids[i:i+200]
            ids_filter = ",".join(str(x) for x in chunk)
            pr = req_lib.patch(
                f"{SUPABASE_URL}/rest/v1/leads?id=in.({ids_filter})",
                headers=SB_HEADERS,
                json={"assignedTo": "", "updatedAt": now_iso},
                timeout=30,
            )
            if pr.status_code in (200, 204):
                done += len(chunk)
            else:
                print(f"[ONCE-RECYCLE] patch HTTP {pr.status_code}: {pr.text[:200]}")
        audit_log("system", "recycle_once_called", "lead", None,
                  {"recycled": done, "grace_hours": ONCE_CALLED_RECYCLE_GRACE_HOURS,
                   "leads": recycled[:20]})
        print(f"[ONCE-RECYCLE] unassigned {done} once-called no-contact lead(s)")
        if done:
            try:
                send_slack(
                    ":recycle: Once-called leads recycled",
                    f"Rotated *{done}* lead(s) dialed once with no contact "
                    f"(>{ONCE_CALLED_RECYCLE_GRACE_HOURS}h ago) back into the shared pool.",
                )
            except Exception as e:
                print(f"[ONCE-RECYCLE] slack failed: {e}")
        _record_once_called_recycle_run()
    except Exception as e:
        print(f"[ONCE-RECYCLE] run failed: {e}")

def _record_dedupe_sweep_run():
    """Upsert the cooldown row. Without `?on_conflict=key` the merge-duplicates
    Prefer header has no idea what to merge on, so the POST hits a primary-key
    violation and the cooldown is never recorded — which caused the dedupe
    sweeps to silently re-fire every 10 minutes (the bg-loop interval) instead
    of weekly. Confirmed by audit on 2026-05-14.

    req_lib.post doesn't raise on 4xx/5xx, so we log non-2xx explicitly —
    otherwise the silent-fail mode is invisible from outside Supabase."""
    try:
        r = req_lib.post(
            f"{SUPABASE_URL}/rest/v1/app_settings?on_conflict=key",
            headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"key": "last_dedupe_sweep", "value": datetime.utcnow().isoformat() + "Z"},
            timeout=10,
        )
        if r.status_code not in (200, 201, 204):
            print(f"[DEDUPE-SWEEP] cooldown upsert HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[DEDUPE-SWEEP] failed to record last-run: {e}")

def run_dedupe_sweep_if_due():
    """Weekly self-healing dedupe. Skips silently if not due."""
    if not _dedupe_sweep_due():
        return
    print("[DEDUPE-SWEEP] starting weekly sweep")
    leads = _fetch_all_leads_for_dedupe()
    if not leads:
        print("[DEDUPE-SWEEP] empty fetch — aborting (will retry next cycle)")
        return  # don't record run, retry on next loop tick

    groups = find_apollo_dupe_groups(leads)
    auto_ids   = [e["id"] for g in groups for e in g["safe_extras"]]
    borderline = [g for g in groups if g["risky_extras"]]

    deleted = 0
    if auto_ids:
        BATCH = 50
        for i in range(0, len(auto_ids), BATCH):
            batch = [str(x) for x in auto_ids[i:i+BATCH]]
            try:
                r = req_lib.delete(
                    f"{SUPABASE_URL}/rest/v1/leads?id=in.({','.join(batch)})",
                    headers={**SB_HEADERS, "Prefer": "return=minimal"},
                    timeout=30,
                )
                if r.status_code in (200, 204):
                    deleted += len(batch)
                else:
                    print(f"[DEDUPE-SWEEP] batch delete HTTP {r.status_code}: {r.text[:200]}")
            except Exception as e:
                print(f"[DEDUPE-SWEEP] batch delete exception: {e}")
        audit_log("system", "leads_cleanup", "lead", None, {
            "reason":   "weekly_dedupe_sweep",
            "deleted":  deleted,
            "borderline_groups": len(borderline),
        })

    # Slack only when there's something to say. Quiet weeks stay quiet.
    if deleted > 0 or borderline:
        fields = [{"label": "Auto-cleaned",
                   "value": f":white_check_mark: {deleted} duplicate Apollo contacts"}]
        if borderline:
            sample_lines = []
            for b in borderline[:5]:
                others = ", ".join(o["id"][:8] for o in b["risky_extras"])
                sample_lines.append(f"• {b['person']} (kept `{b['kept']['id'][:8]}`, others: `{others}`)")
            more = f"\n…and {len(borderline) - 5} more" if len(borderline) > 5 else ""
            fields.append({
                "label": f"Need review ({len(borderline)})",
                "value": "\n".join(sample_lines) + more,
            })
        summary = f"Auto-cleaned {deleted} duplicates"
        if borderline:
            summary += f" — {len(borderline)} group(s) need review (call history at risk)"
        send_slack(":broom: Weekly Apollo dedupe sweep", summary, fields=fields)

    _record_dedupe_sweep_run()
    print(f"[DEDUPE-SWEEP] done — deleted={deleted} borderline={len(borderline)}")

# Start the background thread once at module load. Runs regardless of IMAP
# config — stale-release is the bigger guarantee. Daemon=True dies cleanly
# with the process. Uvicorn's single-worker default on Railway = one runner.
threading.Thread(target=_bg_maintenance_loop, daemon=True, name="bg-maintenance").start()

class ScrapeRequest(BaseModel):
    industry:  str
    industries: Optional[str] = ""   # comma-separated list for multi-industry
    state:     Optional[str] = ""
    cities:    Optional[str] = ""
    # Bulk multi-area search. A list of free-form location strings, each either
    # a state ("TX", "Georgia") or a "City, ST" pair ("Tifton, GA", "San Diego,
    # CA"). When present, this OVERRIDES state/cities and the scrape walks every
    # location in the list. Lets a rep queue a whole territory in one run.
    locations: Optional[object] = None
    limit:     Optional[int] = 25
    source:    Optional[str] = "places"

# ── Apollo.io integration ───────────────────────────────────────────────────
# Two flows:
#   1. Auto-enrichment: when Google Places scrape returns a company, look up
#      the most-likely decision maker at that company and merge name + direct
#      contact info into the lead before insert. Caller never sees Apollo —
#      they just see leads with real names attached.
#   2. Direct DM pull: admin-only "Pull from Apollo" finder (ApolloFinder UI)
#      generates fresh DM leads not tied to existing companies.
# Credit cost: ~1 credit per lookup on Pro tier (4,000/month).

# Process-wide cache: company_name_lower -> (expires_ts, person_dict_or_None).
# Backed by the apollo_enrich_cache Supabase table (migration 004) so it
# survives Railway redeploys. The in-memory dict is a hot cache for the
# current process — Supabase is consulted on miss.
_apollo_enrich_cache = {}

def apollo_cache_get(company: str):
    """Returns ('HIT', person_or_None) or ('MISS', None). Checks the
    in-process dict first, falls back to the Supabase persistence table."""
    if not company:
        return ("MISS", None)
    key = company.lower().strip()
    hit = _apollo_enrich_cache.get(key)
    if hit:
        expires, person = hit
        if expires >= time.time():
            return ("HIT", person)
        _apollo_enrich_cache.pop(key, None)

    # Persistence-layer lookup. Fail-open: any error → treat as MISS, the
    # caller will re-query Apollo (worst case: one extra credit).
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/apollo_enrich_cache",
            headers=SB_HEADERS,
            params={"company_key": f"eq.{key}",
                    "select":      "person,expires_at",
                    "limit":       "1"},
            timeout=10,
        )
        if r.status_code != 200:
            return ("MISS", None)
        rows = r.json()
        if not rows:
            return ("MISS", None)
        row = rows[0]
        try:
            expires_dt = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
            expires_ts = expires_dt.timestamp()
        except Exception:
            return ("MISS", None)
        if expires_ts < time.time():
            return ("MISS", None)
        person = row.get("person")  # may be None — that's a cached miss, still HIT
        _apollo_enrich_cache[key] = (expires_ts, person)
        return ("HIT", person)
    except Exception as e:
        print(f"[APOLLO-CACHE] Supabase lookup failed for '{company}': {e}")
        return ("MISS", None)

def apollo_cache_set(company: str, person):
    """Write through to both the in-memory dict and the Supabase table.
    Fail-open on the Supabase write — losing persistence is better than
    blocking the enrichment flow."""
    if not company:
        return
    if len(_apollo_enrich_cache) > 10000:
        _apollo_enrich_cache.clear()
    key = company.lower().strip()
    expires_ts = time.time() + APOLLO_ENRICH_CACHE_TTL_HOURS * 3600
    _apollo_enrich_cache[key] = (expires_ts, person)
    try:
        req_lib.post(
            f"{SUPABASE_URL}/rest/v1/apollo_enrich_cache",
            headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={
                "company_key": key,
                "person":      person,
                "expires_at":  datetime.utcfromtimestamp(expires_ts).isoformat() + "Z",
                "updated_at":  datetime.utcnow().isoformat() + "Z",
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[APOLLO-CACHE] Supabase upsert failed for '{company}': {e}")

def phone_reveal_count_this_month() -> int:
    """Count successful 'reveal_phone_requested' audit_log entries since the
    first of this UTC month. Used for the monthly budget cap. Fail-open on
    DB errors (better to allow occasional over-spend than block the caller)."""
    try:
        first_of_month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/audit_log"
            f"?action=eq.reveal_phone_requested&created_at=gte.{first_of_month}"
            f"&select=id",
            headers={**SB_ADMIN_HEADERS, "Prefer": "count=exact"}, timeout=5,
        )
        cr = r.headers.get("content-range", "*/0")
        return int(cr.split("/")[-1]) if "/" in cr else 0
    except Exception as e:
        print(f"[APOLLO-BUDGET] count query failed: {e}")
        return 0  # fail-open

def _check_apollo_budget_alert():
    """Fire a Slack alert when phone-reveal usage crosses 80% or 95% of the
    monthly cap. Idempotent per-threshold-per-month — uses app_settings to
    track which thresholds we've already alerted on, so the same threshold
    doesn't spam Slack on every reveal in the danger zone. Resets monthly."""
    cap = APOLLO_PHONE_REVEAL_MONTHLY_CAP
    if not cap or cap <= 0:
        return
    used = phone_reveal_count_this_month()
    pct = (used / cap) * 100
    THRESHOLDS = [80, 95]
    crossed = [t for t in THRESHOLDS if pct >= t]
    if not crossed:
        return

    month_key = datetime.utcnow().strftime("%Y-%m")
    state_key = "apollo_budget_alert_state"
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/app_settings?key=eq.{state_key}&select=value",
            headers=SB_HEADERS, timeout=5,
        )
        rows = r.json() if r.status_code == 200 else []
        cur = json_lib.loads(rows[0]["value"]) if rows and rows[0].get("value") else {}
    except Exception:
        cur = {}

    if cur.get("month") != month_key:
        cur = {"month": month_key, "alerted_at": []}

    new_thresholds = [t for t in crossed if t not in cur.get("alerted_at", [])]
    if not new_thresholds:
        return
    fire_threshold = max(new_thresholds)

    icon = ":rotating_light:" if fire_threshold >= 95 else ":warning:"
    next_step = ("Hard cap nearly reached — reveals will start returning "
                 "'budget exhausted' to the caller. Raise APOLLO_PHONE_REVEAL_MONTHLY_CAP "
                 "or pause bulk pulls until next month.") \
                if fire_threshold >= 95 else \
                ("Approaching cap — consider pausing bulk Apollo pulls or "
                 "raising APOLLO_PHONE_REVEAL_MONTHLY_CAP if the spend pencils out.")
    try:
        send_slack(
            f"{icon} Apollo phone-reveal budget at {fire_threshold}%",
            f"Used *{used}/{cap}* phone reveals this month ({pct:.0f}%). {next_step}",
        )
    except Exception as e:
        print(f"[APOLLO-BUDGET-ALERT] Slack send failed: {e}")
        return

    cur.setdefault("alerted_at", []).extend(new_thresholds)
    try:
        req_lib.post(
            f"{SUPABASE_URL}/rest/v1/app_settings",
            headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"key": state_key, "value": json_lib.dumps(cur)},
            timeout=5,
        )
    except Exception as e:
        print(f"[APOLLO-BUDGET-ALERT] state persist failed: {e}")

def can_reveal_phone(lead: dict):
    """Returns (allowed: bool, reason: str). Enforces industry allowlist + monthly cap."""
    industry = (lead.get("industry") or "").strip().lower()
    if APOLLO_PHONE_REVEAL_INDUSTRIES:
        ok = any(allowed in industry for allowed in APOLLO_PHONE_REVEAL_INDUSTRIES)
        if not ok:
            allowed_list = ", ".join(sorted(APOLLO_PHONE_REVEAL_INDUSTRIES))
            return (False,
                f"Phone reveals restricted to {allowed_list} verticals. "
                f"This lead's industry is '{industry or 'unset'}' — use the switchboard "
                f"and ask for {lead.get('firstName','the contact')} by name.")
    used = phone_reveal_count_this_month()
    if used >= APOLLO_PHONE_REVEAL_MONTHLY_CAP:
        return (False,
            f"Monthly phone reveal budget exhausted ({used}/{APOLLO_PHONE_REVEAL_MONTHLY_CAP}). "
            f"Resets first of next month — use switchboard + named-ask until then.")
    return (True, "")

def apollo_request_phone_reveal(person: dict, lead_id: str):
    """Fire-and-forget request for Apollo to async-reveal direct phone numbers.
    Apollo will POST the unlocked phone(s) to our webhook within ~5-30s.
    Costs 5-8 credits per successful reveal (charged when Apollo posts back),
    NOT when this function is called.
    Returns (ok: bool, detail: str) — detail is for surfacing Apollo's error."""
    if not APOLLO_API_KEY or APOLLO_KILL_SWITCH:
        return (False, "APOLLO_API_KEY missing or kill switch on")
    if not person or not lead_id:
        return (False, "missing person or lead_id")
    if not APOLLO_WEBHOOK_SECRET:
        return (False, "APOLLO_WEBHOOK_SECRET not set")

    app_url = os.getenv("APP_URL", "https://leadflow-railway-production.up.railway.app").rstrip("/")
    # URL-encode the secret in case it contains chars Apollo's parser rejects.
    safe_secret = url_quote(APOLLO_WEBHOOK_SECRET, safe="")
    webhook_url = f"{app_url}/api/webhooks/apollo/{safe_secret}/{lead_id}"
    print(f"[APOLLO-PHONE] webhook_url={webhook_url}")

    payload = {"reveal_phone_number": True, "webhook_url": webhook_url}
    # Pass through every identifying field Apollo accepts. Caller may have
    # filled any subset (linkedin_url > id > email > first+last+domain).
    for key in ("linkedin_url", "id", "email", "first_name", "last_name", "domain"):
        if person.get(key):
            payload[key] = person[key]
    has_strong_id = any(payload.get(k) for k in ("linkedin_url", "id", "email"))
    has_full_name_domain = payload.get("first_name") and payload.get("last_name") and payload.get("domain")
    if not has_strong_id and not has_full_name_domain:
        return (False, "no usable identifier (need linkedin_url, id, email, or first+last+domain)")

    headers = {
        "X-Api-Key":     APOLLO_API_KEY,
        "Cache-Control": "no-cache",
        "Content-Type":  "application/json",
    }
    try:
        r = req_lib.post(APOLLO_MATCH_URL, headers=headers, json=payload, timeout=15)
        if r.status_code != 200:
            err = r.text[:300]
            try:
                err = r.json().get("error") or err
            except Exception:
                pass
            print(f"[APOLLO-PHONE] reveal request HTTP {r.status_code}: {err}")
            return (False, f"Apollo {r.status_code}: {err}")
        return (True, "request accepted")
    except Exception as e:
        print(f"[APOLLO-PHONE] exception: {e}")
        return (False, f"network error: {e}")

def apollo_enrich_person(person: dict):
    """Unlock email + last name via Apollo /people/match. Apollo's api_search
    returns masked fields ('Robert' with no last name, no email) on Pro tier;
    /people/match unlocks them for ~1 credit per contact. Identifier priority:
    LinkedIn URL > Apollo person id > first+last+domain."""
    if not APOLLO_API_KEY or APOLLO_KILL_SWITCH or not person:
        return None

    payload = {"reveal_personal_emails": True}
    if person.get("linkedin_url"):
        payload["linkedin_url"] = person["linkedin_url"]
    elif person.get("id"):
        payload["id"] = person["id"]
    else:
        org = person.get("organization") or {}
        domain = org.get("primary_domain") or org.get("website_url", "").replace("https://", "").replace("http://", "").split("/")[0]
        if person.get("first_name") and person.get("last_name") and domain:
            payload["first_name"] = person["first_name"]
            payload["last_name"]  = person["last_name"]
            payload["domain"]     = domain
        else:
            return None  # Not enough identifying info to call /match

    headers = {
        "X-Api-Key":     APOLLO_API_KEY,
        "Cache-Control": "no-cache",
        "Content-Type":  "application/json",
    }
    try:
        r = req_lib.post(APOLLO_MATCH_URL, headers=headers, json=payload, timeout=15)
        if r.status_code != 200:
            print(f"[APOLLO-MATCH] HTTP {r.status_code}: {r.text[:200]}")
            return None
        data = r.json()
        return data.get("person") or None
    except Exception as e:
        print(f"[APOLLO-MATCH] exception: {e}")
        return None

def apollo_find_dm_at_company(company_name: str, titles=None):
    """Search Apollo for the top-ranked DM at a given company.
    Returns the Apollo person dict, or None if no match / API error / kill switch on."""
    if not APOLLO_API_KEY or APOLLO_KILL_SWITCH or not company_name:
        return None
    payload = {
        "q_organization_name": company_name,
        "person_titles":       titles or APOLLO_ENRICH_TITLES,
        "per_page":            1,
        "page":                1,
    }
    headers = {
        "X-Api-Key":     APOLLO_API_KEY,
        "Cache-Control": "no-cache",
        "Content-Type":  "application/json",
    }
    try:
        r = req_lib.post(APOLLO_SEARCH_URL, headers=headers, json=payload, timeout=15)
        if r.status_code != 200:
            print(f"[APOLLO-ENRICH] HTTP {r.status_code} for '{company_name}': {r.text[:150]}")
            return None
        data = r.json()
        people = data.get("people") or data.get("contacts") or []
        return people[0] if people else None
    except Exception as e:
        print(f"[APOLLO-ENRICH] exception for '{company_name}': {e}")
        return None

def apollo_enrich_lead_in_place(lead: dict, titles=None) -> bool:
    """Mutate `lead` to merge Apollo DM info on top of whatever Google Places
    found. Returns True if real enrichment happened (i.e. we got a person).
    Cache-aware: a previously-MISSed company in the cache window is skipped.
    `titles` overrides the DM titles searched (e.g. healthcare facility roles)."""
    if not lead.get("company"):
        return False
    if lead.get("firstName"):  # Already has a DM — don't re-spend credits
        return False

    cache_state, cached = apollo_cache_get(lead["company"])
    if cache_state == "HIT":
        person = cached  # may be None — that's a cached miss, still skip
    else:
        person = apollo_find_dm_at_company(lead["company"], titles=titles)
        # Unlock email + last name via /match before caching (~1 extra credit).
        # Without this the cached person has only first_name and is unusable.
        if person:
            enriched = apollo_enrich_person(person)
            if enriched:
                person = {**person, **enriched}
        apollo_cache_set(lead["company"], person)

    if not person:
        return False

    lead["firstName"] = clean(person.get("first_name", "")) or lead.get("firstName", "")
    lead["lastName"]  = clean(person.get("last_name", ""))  or lead.get("lastName", "")
    lead["title"]     = clean(person.get("title", ""))      or lead.get("title", "")

    em = person.get("email") or ""
    if em and "email_not_unlocked" not in em:
        lead["email"] = em

    # Direct line beats switchboard — overwrite Google's main number if Apollo has personal
    for p in (person.get("phone_numbers") or []):
        cand = p.get("sanitized_number") or p.get("raw_number") or ""
        if cand:
            lead["phone"] = cand
            break

    li = person.get("linkedin_url")
    if li and "linkedin" not in (lead.get("notes") or "").lower():
        lead["notes"] = ((lead.get("notes") or "") + f" | LinkedIn: {li}").strip(" |")

    lead["score"] = score_lead(lead)
    return True

def apollo_person_to_lead(person: dict, user: str) -> dict:
    """Map Apollo person object → LeadFlow lead schema."""
    org = person.get("organization") or {}

    # Phone: prefer person mobile, fall back to org main line
    phone = ""
    for p in (person.get("phone_numbers") or []):
        cand = p.get("sanitized_number") or p.get("raw_number") or ""
        if cand:
            phone = cand
            break
    if not phone:
        phone = org.get("phone") or org.get("primary_phone", {}).get("sanitized_number", "") or ""

    # Email: Apollo masks unrevealed emails as "email_not_unlocked@domain.com"
    email = person.get("email") or ""
    if "email_not_unlocked" in email:
        email = ""

    city  = person.get("city") or org.get("city") or ""
    state = person.get("state") or org.get("state") or ""
    addr  = org.get("street_address") or ""
    title = person.get("title") or ""
    linkedin = person.get("linkedin_url") or ""

    # `company` stores the trimmed name (tagline stripped) for clean display;
    # keep the FULL original in notes so search finds the lead by either form.
    full_company = clean(org.get("name", ""))
    company = clean(clean_company_name(org.get("name", "")))

    notes_parts = [f"Apollo: {title}".strip(": ")]
    if linkedin: notes_parts.append(f"LinkedIn: {linkedin}")
    if person.get("seniority"): notes_parts.append(f"Seniority: {person['seniority']}")
    if full_company and full_company != company:
        notes_parts.append(f"Full name: {full_company}")

    now = datetime.utcnow().isoformat()
    lead = {
        "company":     company,
        "industry":    clean(org.get("industry") or ""),
        "phone":       clean(phone),
        "address":     clean(addr),
        "city":        clean(city),
        "state":       normalize_state_abbrev(clean(state)),
        "website":     clean(org.get("website_url") or ""),
        "notes":       " | ".join(notes_parts),
        "source":      "Apollo",
        "firstName":   clean(person.get("first_name", "")),
        "lastName":    clean(person.get("last_name", "")),
        "title":       clean(title),
        "email":       clean(email),
        "assignedTo":  "",
        "callbackDate":"",
        "status":      "new",
        "createdAt":   now,
        "updatedAt":   now,
        "createdBy":   user,
    }
    lead["score"] = score_lead(lead)
    return lead

def _apollo_person_is_us(person: dict) -> bool:
    """Belt-and-suspenders US filter for Apollo /mixed_people results.

    Even with person_locations='United States', Apollo's matcher is loose —
    Filipino call-center managers, Indian ops directors, Canadian property
    managers slip through. We accept rows where ANY of these resolve to US:
      - person.country / org.country in US aliases
      - person.state / org.state is a US state abbrev or name
      - city + state combo that includes a US state token (e.g. "Boston, MA")
      - present_raw_address ends in a US zip pattern (5 digits, optional +4)

    Previous version rejected anything that didn't have explicit country=US
    AND was rejecting ~60% of valid US contacts because Apollo's data is
    sparse on the person record (often only org.country is populated, and
    sometimes nothing is populated at all even though person_locations
    anchored the search to the US). Now we also trust the search-anchor
    when both person.country and org.country are blank — the caller explicitly
    asked for US-only via locations[], so if Apollo found nothing to
    contradict that, accept."""
    org = person.get("organization") or {}
    p_country = (person.get("country") or "").strip().lower()
    o_country = (org.get("country") or "").strip().lower()
    p_state   = (person.get("state")   or "").strip()
    o_state   = (org.get("state")   or "").strip()
    p_city    = (person.get("city")  or "").strip()
    o_city    = (org.get("city")  or "").strip()
    raw_addr  = (person.get("present_raw_address") or org.get("present_raw_address") or "").strip()

    us_country_aliases = {"united states", "usa", "us", "u.s.", "u.s.a.", "united states of america"}
    foreign_signals = {"philippines","india","canada","united kingdom","uk","mexico","australia",
                       "germany","france","brazil","argentina","colombia","spain","italy",
                       "netherlands","sweden","norway","denmark","ireland","singapore","pakistan",
                       "bangladesh","indonesia","malaysia","thailand","vietnam","south africa",
                       "egypt","nigeria","kenya","ghana","japan","china","south korea","taiwan",
                       "hong kong","new zealand","russia","poland","ukraine","turkey","greece",
                       "portugal","belgium","switzerland","austria","czech republic","hungary",
                       "romania","saudi arabia","uae","united arab emirates","israel","jordan",
                       "lebanon","peru","chile","venezuela","ecuador","cuba","dominican republic",
                       "puerto rico"}  # PR is technically US territory but Apollo lists it separately

    def _is_us_state(s):
        return bool(s) and ((s.upper() in US_STATE_ABBREVS) or (s.lower() in US_STATE_NAMES))

    # Strong positive: explicit US country
    if p_country in us_country_aliases or o_country in us_country_aliases:
        return True
    # Strong positive: state is a US state name/abbrev
    if _is_us_state(p_state) or _is_us_state(o_state):
        return True
    # Strong positive: raw address contains a 5-digit zip
    if raw_addr and re.search(r"\b\d{5}(-\d{4})?\b", raw_addr):
        return True

    # Strong negative: explicit non-US country
    if p_country in foreign_signals or o_country in foreign_signals:
        return False

    # Neutral case: no country, no state, no zip. The search was anchored to
    # US locations via person_locations[], so trust that — accept rather than
    # reject. Net effect: fewer false-negative US filters, same protection
    # against the explicit Filipino/Indian contacts that triggered the
    # original filter.
    if not p_country and not o_country and not p_state and not o_state:
        return True
    return False

def filter_apollo_dupes(people: list) -> tuple:
    """Pre-enrichment dedupe: drop Apollo search results that already exist
    in our leads table. Apollo's /people/match call costs ~1 credit per
    contact; running it on a lead we already own is paying twice. Match on:
      1. linkedin_url present in any existing lead's notes column
      2. (first_name, company) fingerprint, case-insensitive
    Returns (filtered_people, skipped_count). Fail-open: if the lookup
    errors, we return the input unchanged rather than block the pull."""
    if not people:
        return people, 0

    orgs = set()
    for p in people:
        org = ((p.get("organization") or {}).get("name") or "").strip()
        if org:
            orgs.add(org)
    if not orgs:
        return people, 0

    # PostgREST in.() — wrap each value in double quotes so commas inside
    # company names ("Acme, Inc.") don't split the list. requests will
    # URL-encode the quotes for us.
    org_filter = ",".join('"' + o.replace('"', '\\"') + '"' for o in orgs)
    # Fetch ALL existing leads for these companies — paginate so a company with
    # hundreds of contacts can't silently overflow a single-page cap and let
    # dupes through (the old code stopped at 5000 rows). PostgREST offset/limit;
    # stop when a short page comes back. Fail-open on the first page, use
    # partial data on a later-page error, 50k hard backstop against runaway loops.
    existing = []
    try:
        PAGE = 1000
        offset = 0
        while True:
            r = req_lib.get(
                f"{SUPABASE_URL}/rest/v1/leads",
                headers=SB_HEADERS,
                params={
                    "select": "firstName,company,notes",
                    "company": f"in.({org_filter})",
                    "limit":   str(PAGE),
                    "offset":  str(offset),
                },
                timeout=15,
            )
            if r.status_code != 200:
                if offset == 0:
                    return people, 0
                break
            batch = r.json() or []
            existing.extend(batch)
            if len(batch) < PAGE:
                break
            offset += PAGE
            if offset >= 50000:
                print("[APOLLO-DEDUPE] existing-lead lookup hit 50k backstop")
                break
    except Exception as e:
        print(f"[APOLLO-DEDUPE] existing-lead lookup failed: {e}")
        return people, 0

    fingerprints = set()
    linkedins = set()
    for lead in existing:
        company = (lead.get("company") or "").strip().lower()
        first   = (lead.get("firstName") or "").strip().lower()
        if company and first:
            fingerprints.add((first, company))
        m = re.search(r'LinkedIn:\s*(https?://[^\s|]+)', lead.get("notes") or "", re.IGNORECASE)
        if m:
            linkedins.add(m.group(1).rstrip('/').lower())

    filtered = []
    skipped = 0
    for p in people:
        first = (p.get("first_name") or "").strip().lower()
        org   = ((p.get("organization") or {}).get("name") or "").strip().lower()
        li    = (p.get("linkedin_url") or "").rstrip('/').lower()
        if (li and li in linkedins) or (first and org and (first, org) in fingerprints):
            skipped += 1
            continue
        filtered.append(p)
    return filtered, skipped

class ApolloPullRequest(BaseModel):
    # Accept either comma-sep string (legacy) OR list (preferred — avoids the
    # "Phoenix, AZ" string-split problem where embedded commas break parsing).
    titles:        Optional[object] = ""
    industries:    Optional[object] = ""
    locations:     Optional[object] = ""
    employee_min:  Optional[int] = 50
    employee_max:  Optional[int] = 500
    per_page:      Optional[int] = 25   # Apollo caps at 100
    page:          Optional[int] = 1
    reveal_phones: Optional[bool] = False  # Costs +5-8 credits per lead via async webhook
    # Auto-paginate: keep pulling additional pages until we've saved at least
    # `min_new_leads` rows or burned `max_pages`. Solves "all 25 already in DB"
    # by walking through page 2, 3, ... until new contacts appear.
    auto_paginate: Optional[bool] = True
    min_new_leads: Optional[int]  = 10
    max_pages:     Optional[int]  = 5
    # Title rotation: when True, cycle through APOLLO_DM_TITLE_GROUPS (different
    # decision-maker title slices) instead of hammering one title set. Solves
    # "all already in DB" on repeat pulls of the same city — each group surfaces
    # fresh budget-holders. The user's `titles` (if any) are tried first, then
    # the rotation groups. `max_pages` becomes the TOTAL Apollo-call budget
    # spread across groups (round-robin), not per-group, so credit spend is
    # bounded exactly as before.
    rotate_titles: Optional[bool] = False

def _to_str_list(v):
    """Coerce a request field to a list of trimmed non-empty strings.
    Accepts list (used directly) or string (split on commas)."""
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [t.strip() for t in str(v or "").split(",") if t.strip()]

@app.post("/api/scrape")
def run_scrape(body: ScrapeRequest, user: str = Depends(verify_token)):
    # Kill switch first — reject cheap, reject early. Checks env var first,
    # then DB flag via is_kill_switch_on().
    on, src = is_kill_switch_on()
    if on:
        detail = ("Google Places scraping is disabled from the admin dashboard."
                  if src == "db" else
                  "Google Places scraping is disabled (PLACES_KILL_SWITCH env).")
        raise HTTPException(status_code=503, detail=detail)

    # Non-admin daily cap. UTC midnight reset. Eric (in ADMIN_USERS) is
    # unlimited. Silently no-ops if usage_events table isn't migrated yet
    # (scrapes_today returns 0 on any DB error).
    if not is_admin(user):
        used = scrapes_today(user)
        if used >= NON_ADMIN_DAILY_SCRAPE_CAP:
            raise HTTPException(
                status_code=429,
                detail=f"Daily scrape limit reached ({used}/{NON_ADMIN_DAILY_SCRAPE_CAP}). Resets at UTC midnight.",
            )

    # "How many" is per-location. For a single area this is the total (legacy
    # behavior). For a bulk multi-area run the total scales with the number of
    # locations so each area gets a fair allocation instead of 60 split 9 ways.
    per_location_limit = min(max(body.limit or 25, 5), 60)

    # Build list of keywords to search
    if body.industries:
        ind_list = [i.strip() for i in body.industries.split(",") if i.strip()]
        keywords = [(ind, INDUSTRY_MAP.get(ind, ind.lower())) for ind in ind_list]
    elif body.industry and body.industry != "_all_":
        keywords = [(body.industry, INDUSTRY_MAP.get(body.industry, body.industry.lower()))]
    else:
        # All industries — use "business" as a broad Google Places term
        keywords = [("All", "business")]

    # Build list of locations to search. Precedence:
    #   1. Explicit bulk `locations` list (multi-state/multi-city territory)
    #   2. Single state + comma-sep cities (legacy single-area form)
    #   3. Single state (or nationwide if blank)
    bulk = body.locations
    if isinstance(bulk, str):  # tolerate a comma-sep string too
        bulk = [s.strip() for s in bulk.split(",") if s.strip()]
    if isinstance(bulk, list) and any(str(x).strip() for x in bulk):
        # Dedupe case-insensitively while preserving order.
        seen_loc, locations = set(), []
        for x in bulk:
            loc = str(x).strip()
            if loc and loc.lower() not in seen_loc:
                seen_loc.add(loc.lower())
                locations.append(loc)
    elif body.cities:
        city_list = [c.strip() for c in body.cities.split(",") if c.strip()]
        locations = [f"{city}, {body.state}".strip(", ") for city in city_list]
    else:
        locations = [body.state or ""]

    # Total target scales with location count (per-location semantics above).
    # Hard ceiling of 300 backstops a huge bulk list; the per-run $ spend cap
    # below is the real guard and will reject anything too broad.
    limit = min(per_location_limit * max(1, len(locations)), 300)

    # Calculate per-combo limit
    combos = len(keywords) * len(locations)
    per_combo = max(limit // combos, 3) if combos else limit

    # Cache check — skip (location, keyword) pairs we already fetched fresh.
    # Cache key matches what scrape_google_places actually queries: the full
    # location string (e.g. "Phoenix, AZ" or just "AZ").
    all_combos = [(loc, kw) for _, kw in keywords for loc in locations]
    cache_hits = places_cache_load(all_combos)
    if cache_hits:
        print(f"[SCRAPE] cache hits: {len(cache_hits)}/{len(all_combos)} combos — skipping Text Search for those")

    # Cost prediction + hard cap. Text Search $0.032/call, Details $0.017/call.
    # Assume 1 Text Search per uncached combo (text search pagination is rare
    # at default per_combo=3), plus Details calls for ~60% of results (rough
    # hit rate when phone missing). Err generous.
    uncached = len(all_combos) - len(cache_hits)
    predicted_cents = uncached * GOOGLE_COSTS_CENTS["google_text_search"] + \
                      uncached * per_combo * 0.6 * GOOGLE_COSTS_CENTS["google_details"]
    max_spend_cents = PLACES_MAX_SPEND_PER_RUN * 100
    print(f"[SCRAPE] user={user} industries={[k[0] for k in keywords]} locations={locations} "
          f"limit={limit} ({per_combo}/combo, {combos} combos, {uncached} uncached) "
          f"predicted ${predicted_cents/100:.2f} (cap ${PLACES_MAX_SPEND_PER_RUN})")
    if predicted_cents > max_spend_cents:
        raise HTTPException(
            status_code=400,
            detail=f"Predicted spend ${predicted_cents/100:.2f} exceeds cap ${PLACES_MAX_SPEND_PER_RUN}. "
                   f"Narrow industries/cities or raise PLACES_MAX_SPEND_PER_RUN.",
        )

    # Aggregate "scrape_call" row — makes per-scrape rollups easy in /api/usage.
    log_usage(user, "scrape_call", {
        "industries": [k[0] for k in keywords],
        "state":      body.state or "",
        "cities":     body.cities or "",
        "limit":      limit,
        "combos":     combos,
        "uncached":   uncached,
    })

    all_leads = []
    seen_phones = set()
    cache_entries_to_write = []
    for ind_name, keyword in keywords:
        for location in locations:
            combo_key = f"{location}||{keyword}"
            if combo_key in cache_hits:
                # Fresh cache hit — already searched this within TTL. Same
                # Google result set is expected, and any new leads would have
                # been saved then. Skip the paid call.
                continue
            batch = scrape_google_places(
                keyword=keyword, state=location, limit=per_combo, username=user,
            )
            # Capture place_ids for the cache write (stashed by scrape_google_places).
            pids = _LAST_SCRAPE_PLACE_IDS.pop((keyword, location), [])
            cache_entries_to_write.append({
                "city": location, "keyword": keyword, "place_ids": pids,
            })
            for lead in batch:
                # Tag each lead with the industry it was scraped for
                if ind_name != "All" and not lead.get("industry"):
                    lead["industry"] = ind_name
                if lead.get("phone") and lead["phone"] not in seen_phones:
                    seen_phones.add(lead["phone"])
                    all_leads.append(lead)
                elif not lead.get("phone"):
                    all_leads.append(lead)
            if len(all_leads) >= limit:
                break
        if len(all_leads) >= limit:
            break
    leads = all_leads[:limit]

    # Write every combo we actually queried back to the cache (even empty
    # ones — a ZERO_RESULTS hit is worth caching so we don't retry it).
    places_cache_write(cache_entries_to_write)

    # Tag all scraped leads with the user who ran the search
    for lead in leads:
        lead["createdBy"] = user

    # Auto-enrich with Apollo: replace switchboard contact info with the actual
    # decision maker's name + direct line/email. Silent no-op if APOLLO_API_KEY
    # is missing or APOLLO_KILL_SWITCH=1. Per-company cache prevents re-spending
    # credits on the same company within APOLLO_ENRICH_CACHE_TTL_HOURS.
    apollo_enriched = 0
    if APOLLO_API_KEY and not APOLLO_KILL_SWITCH:
        for lead in leads:
            if apollo_enrich_lead_in_place(lead):
                apollo_enriched += 1
        print(f"[SCRAPE] Apollo enriched {apollo_enriched}/{len(leads)} leads with DM info")

    # Callable-lead filter, runs AFTER Apollo enrichment so a bad-Google-phone
    # row gets rescued by Apollo's DM phone (when available) or kept for email
    # outreach when Apollo gave us a name+email. Drop only the rows that have
    # neither a dialable number nor a usable DM contact — those are guaranteed
    # disconnects + dead emails, the caller's connectivity-rate killers.
    pre_filter_count = len(leads)
    callable_leads = []
    for lead in leads:
        ph = (lead.get("phone") or "").strip()
        em = (lead.get("email") or "").strip()
        has_phone = is_valid_us_phone(ph)
        has_email = "@" in em and "." in em.split("@")[-1]
        has_dm    = bool(lead.get("firstName"))
        if has_phone or (has_dm and has_email):
            callable_leads.append(lead)
        # else: drop — no dialable phone AND no DM email = uncallable
    dropped_uncallable = pre_filter_count - len(callable_leads)
    leads = callable_leads
    print(f"[SCRAPE] Callable filter: {len(leads)} kept, {dropped_uncallable} dropped (no phone + no DM email)")

    saved, already_in_db = save_to_supabase(leads)

    # Plain-English breakdown so a "0 saved" result reads as "you already own
    # these" rather than "the scraper is broken." Mirrors the Apollo pull
    # summary. Built from the funnel: Google returned N businesses → some
    # uncallable → some already in your DB → the rest saved.
    cached_combos = len(cache_hits)
    if cached_combos and cached_combos == len(all_combos):
        summary = (f"All {cached_combos} search area(s) were already pulled in the last "
                   f"{PLACES_CACHE_TTL_DAYS} days (cached) — no fresh Google call made. "
                   f"Try new cities/industries, or widen the area.")
    else:
        parts = [f"Google returned {pre_filter_count} business(es)"]
        if dropped_uncallable:
            parts.append(f"{dropped_uncallable} uncallable (no phone or DM email)")
        if already_in_db:
            parts.append(f"{already_in_db} already in your DB")
        if apollo_enriched:
            parts.append(f"{apollo_enriched} enriched with DM info")
        parts.append(f"{saved} new saved")
        if cached_combos:
            parts.append(f"{cached_combos}/{len(all_combos)} area(s) skipped (recently searched)")
        summary = " · ".join(parts)

    audit_log(user, "scrape_leads", "lead", None, {
        "industries": [k[0] for k in keywords], "state": body.state,
        "cities": body.cities, "found": pre_filter_count, "saved": saved,
        "already_in_db": already_in_db,
        "cache_hits": len(cache_hits), "uncached_combos": uncached,
        "apollo_enriched": apollo_enriched,
        "dropped_uncallable": dropped_uncallable})
    print(f"[SCRAPE] Saved {saved} leads (already_in_db={already_in_db}, cache_hits={len(cache_hits)}/{len(all_combos)}, apollo_enriched={apollo_enriched}, dropped={dropped_uncallable})")

    # Slack notification
    if saved > 0:
        app_url = os.getenv("APP_URL", "https://leadflow-railway-production.up.railway.app")
        fields = [
            {"label": "Industries", "value": ", ".join(k[0] for k in keywords)},
            {"label": "Location", "value": body.state or "All"},
            {"label": "Leads Found", "value": f":busts_in_silhouette: {len(leads)}"},
            {"label": "New Saved", "value": f":white_check_mark: {saved}"},
        ]
        if apollo_enriched > 0:
            fields.append({"label": "DM Enriched (Apollo)", "value": f":telephone_receiver: {apollo_enriched}"})
        if dropped_uncallable > 0:
            fields.append({"label": "Dropped (uncallable)", "value": f":no_entry: {dropped_uncallable}"})
        send_slack(
            "🔍 LeadFlow Scrape Complete",
            f"*{user}* scraped *{saved}* new leads.",
            fields=fields,
            actions=[{"label": "📋 View Leads", "url": app_url, "style": "primary"}],
        )

    return {
        "leads": leads,
        "count": len(leads),
        "saved": saved,
        "alreadyInDb":       already_in_db,
        "found":             pre_filter_count,
        "summary":           summary,
        "cacheHits": len(cache_hits),
        "combos":    len(all_combos),
        "apolloEnriched":    apollo_enriched,
        "droppedUncallable": dropped_uncallable,
    }

# ════════════════════════════════════════════════════════════════════════════
# FREE LEAD SOURCES — OpenStreetMap (Overpass) + NPI registry. $0, no per-lead
# cost, funneled through ingest_leads() for dedup + scoring + phone-unique safety.
# ════════════════════════════════════════════════════════════════════════════

# Cleaning-vertical → Overpass tag selectors. `nwr` = node|way|relation.
OSM_CATEGORY_SELECTORS = {
    "Healthcare":    ['["amenity"="hospital"]', '["amenity"="clinic"]', '["amenity"="doctors"]',
                      '["amenity"="dentist"]', '["amenity"="nursing_home"]', '["healthcare"="centre"]',
                      '["healthcare"="hospital"]', '["healthcare"="clinic"]'],
    "Education":     ['["amenity"="school"]', '["amenity"="college"]', '["amenity"="university"]',
                      '["amenity"="kindergarten"]'],
    "Industrial":    ['["building"="industrial"]', '["building"="warehouse"]', '["landuse"="industrial"]',
                      '["man_made"="works"]', '["industrial"]'],
    "Entertainment": ['["amenity"="theatre"]', '["amenity"="cinema"]', '["amenity"="conference_centre"]',
                      '["amenity"="events_venue"]', '["leisure"="sports_centre"]', '["leisure"="fitness_centre"]',
                      '["leisure"="bowling_alley"]'],
    "Offices":       ['["office"]', '["amenity"="bank"]'],
    "Hospitality":   ['["tourism"="hotel"]', '["amenity"="events_venue"]', '["leisure"="resort"]'],
}
# Map a category to the industry label stored on the lead (drives fit scoring).
OSM_CATEGORY_INDUSTRY = {
    "Healthcare": "Healthcare", "Education": "Education", "Industrial": "Industrial",
    "Entertainment": "Entertainment", "Offices": "Office", "Hospitality": "Hotel",
}

def fetch_osm(state_abbrev: str, categories: list, city: str = "") -> list:
    """Query Overpass for businesses by cleaning-vertical. Free, no key. When a
    city is given, scopes to that CITY's boundary (small area = fast, reliable on
    big states like CA); otherwise the whole state. Returns raw lead dicts."""
    iso = f"US-{state_abbrev.upper()}"
    city = (city or "").strip()
    # City-scoped area is tiny → fast, dodges the big-state Overpass timeout.
    # Escape quotes; admin_level 7|8 covers US municipalities. Name collisions
    # (a same-named city in another state) are dropped by the state post-filter.
    if city:
        c_esc = city.replace('"', '\\"')
        area_def = f'area["name"="{c_esc}"]["admin_level"~"^(7|8)$"]["boundary"="administrative"]->.a;'
    else:
        area_def = f'area["ISO3166-2"="{iso}"]->.a;'
    # Parse each selector once into (key, value|None) and remember its category.
    # '["amenity"="hospital"]' -> ("amenity","hospital"); '["office"]' -> ("office",None).
    def _parse_sel(s):
        inner = s.strip("[]")
        if '"="' in inner:
            k, v = inner.split('"="', 1)
            return (k.strip('"'), v.strip('"'))
        return (inner.strip('"'), None)
    cat_parsed = []  # [(category, key, value|None)] in priority order
    for cat in categories:
        for sel in OSM_CATEGORY_SELECTORS.get(cat, []):
            k, v = _parse_sel(sel)
            cat_parsed.append((cat, k, v))
    if not cat_parsed:
        return []

    # One Overpass call PER category — a whole-state query across all 6 verticals
    # 504s on big states (TX/CA/GA). Per-category queries are small + reliable;
    # we merge + dedup the elements. Free, so the extra calls cost nothing.
    def _overpass(ql):
        # Tight HTTP timeout so a single slow query can't blow past the fetch
        # budget (which is only checked between categories). A slow mirror is
        # abandoned fast and we move on — partial coverage beats a hung request.
        for ep in OVERPASS_MIRRORS:
            try:
                r = req_lib.post(ep, data=ql.encode("utf-8"), headers=OVERPASS_HEADERS, timeout=40)
                if r.status_code == 200:
                    return r.json().get("elements", [])
                print(f"[OSM] {ep.split('/')[2]} HTTP {r.status_code} — next mirror")
            except Exception as e:
                print(f"[OSM] {ep.split('/')[2]} failed ({e}) — next mirror")
        return None

    seen_ids, elements = set(), []
    fetch_deadline = time.time() + OSM_FETCH_BUDGET_SEC
    for cat in categories:
        sels = OSM_CATEGORY_SELECTORS.get(cat, [])
        if not sels:
            continue
        if time.time() > fetch_deadline:
            print(f"[OSM] fetch budget ({OSM_FETCH_BUDGET_SEC}s) hit — stopping at {cat}, partial result")
            break
        body = "\n".join(f"  nwr{sel}(area.a);" for sel in sels)
        ql = (f"[out:json][timeout:40];\n" + area_def + "\n(" + body + "\n);\nout center 250;")
        els = _overpass(ql)
        if els is None:
            print(f"[OSM] {cat}: all mirrors failed — skipping")
            continue
        for el in els:
            key = (el.get("type"), el.get("id"))
            if key not in seen_ids:
                seen_ids.add(key)
                elements.append(el)

    st_up = state_abbrev.upper()
    leads = []
    for el in elements:
        t = el.get("tags", {})
        name = t.get("name", "")
        if not name:
            continue
        el_city = t.get("addr:city", "")
        # City-scoped query already limits to the city's boundary, so we DON'T
        # drop rows missing addr:city. Guard only against a same-named city in
        # another state: if addr:state is present and wrong, skip.
        if city:
            est = (t.get("addr:state", "") or "").upper()
            if est and est not in (st_up, US_STATES_FULL.get(st_up, "").upper()):
                continue
        # Label by the first selector that actually matches key=value (a school
        # has amenity=school, which must NOT match the amenity=hospital selector).
        industry = ""
        for cat, k, v in cat_parsed:
            if k in t and (v is None or t.get(k) == v):
                industry = OSM_CATEGORY_INDUSTRY.get(cat, cat); break
        street = " ".join(x for x in [t.get("addr:housenumber",""), t.get("addr:street","")] if x).strip()
        leads.append({
            "company": name,
            "industry": industry,
            "phone": t.get("phone") or t.get("contact:phone") or "",
            "website": t.get("website") or t.get("contact:website") or "",
            "address": street,
            "city": el_city,
            "state": t.get("addr:state") or state_abbrev.upper(),
            "notes": "Source: OpenStreetMap",
        })
    return leads

# NPPES rejects a state-only query ("requires additional search criteria"), so a
# statewide pull (no city, no taxonomy) fans out across these facility types —
# the high-cleaning-value healthcare verticals — to cover the whole state.
NPI_STATEWIDE_TAXONOMIES = ["Clinic/Center", "Nursing", "Skilled Nursing", "Hospital",
    "General Acute Care", "Assisted Living", "Home Health", "Dental", "Rehabilitation",
    "Pharmacy", "Laboratory", "Urgent Care", "Surgical", "Dialysis"]

def _npi_query(state_abbrev: str, city: str, taxonomy: str, limit: int) -> list:
    params = {"version": "2.1", "enumeration_type": "NPI-2",
              "state": state_abbrev.upper(), "limit": min(max(limit, 1), 200)}
    if city:     params["city"] = city
    if taxonomy: params["taxonomy_description"] = taxonomy
    try:
        r = req_lib.get(NPI_API_URL, params=params, timeout=30)
        if r.status_code != 200:
            print(f"[NPI] HTTP {r.status_code}: {r.text[:160]}")
            return []
        return r.json().get("results", []) or []
    except Exception as e:
        print(f"[NPI] fetch failed: {e}")
        return []

def fetch_npi(state_abbrev: str, city: str = "", taxonomy: str = "", limit: int = 200) -> list:
    """Query NPPES/NPI for healthcare ORGANIZATIONS (facilities). Free, no key.
    A statewide pull (no city/taxonomy) fans out across facility taxonomies
    because NPPES won't accept a state-only query. Returns raw lead dicts."""
    if city or taxonomy:
        results = _npi_query(state_abbrev, city, taxonomy, limit)
    else:
        results, seen = [], set()
        for tx in NPI_STATEWIDE_TAXONOMIES:
            for res in _npi_query(state_abbrev, "", tx, 200):
                npi = res.get("number")
                if npi and npi not in seen:
                    seen.add(npi)
                    results.append(res)
            if len(results) >= FREE_SOURCE_MAX_ROWS:
                break

    leads = []
    for res in results:
        basic = res.get("basic", {})
        name = basic.get("organization_name") or basic.get("name") or ""
        if not name:
            continue
        # Prefer the LOCATION address (not mailing). NPPES marks it purpose=LOCATION.
        addrs = res.get("addresses", []) or []
        loc = next((a for a in addrs if a.get("address_purpose") == "LOCATION"), addrs[0] if addrs else {})
        taxos = res.get("taxonomies", []) or []
        primary_taxo = next((x.get("desc","") for x in taxos if x.get("primary")), taxos[0].get("desc","") if taxos else "")
        leads.append({
            "company": name,
            "industry": primary_taxo or "Healthcare",
            "phone": loc.get("telephone_number") or "",
            "address": " ".join(x for x in [loc.get("address_1",""), loc.get("address_2","")] if x).strip(),
            "city": loc.get("city", ""),
            "state": loc.get("state", state_abbrev.upper()),
            "notes": f"Source: NPI registry | {primary_taxo}".strip(" |"),
        })
    return leads

class FreeSourceRequest(BaseModel):
    state:      Optional[str] = ""
    cities:     Optional[str] = ""        # comma-sep; each pulled separately
    categories: Optional[object] = None   # OSM only: list of vertical names
    taxonomy:   Optional[str] = ""        # NPI only: e.g. "Nursing", "Dentist"
    limit:      Optional[int] = 200
    enrich:     Optional[bool] = False    # OSM only: Apollo-enrich phoneless leads (admin)
    restaurants: Optional[bool] = False   # health only: ALSO pull restaurant inspections (low-budget)

def _free_source_guard(user: str):
    """Shared preflight: kill switch + non-admin daily cap. Raises HTTPException."""
    if FREE_SOURCES_KILL_SWITCH:
        raise HTTPException(status_code=503, detail="Free lead sources are disabled (FREE_SOURCES_KILL_SWITCH).")
    if not is_admin(user):
        used = events_today(user, "free_source_pull")
        if used >= NON_ADMIN_FREE_SOURCE_CAP:
            raise HTTPException(status_code=429,
                detail=f"Daily free-source limit reached ({used}/{NON_ADMIN_FREE_SOURCE_CAP}). Resets at UTC midnight.")
    log_usage(user, "free_source_pull", {})

@app.post("/api/sources/osm")
def source_osm(body: FreeSourceRequest, user: str = Depends(verify_token)):
    """Pull businesses from OpenStreetMap by US state + cleaning vertical. Free."""
    _free_source_guard(user)
    if not body.state:
        raise HTTPException(status_code=400, detail="Pick a state.")
    cats = body.categories if isinstance(body.categories, list) and body.categories \
           else list(OSM_CATEGORY_SELECTORS.keys())
    cities = [c.strip() for c in (body.cities or "").split(",") if c.strip()] or [""]
    all_raw = []
    for city in cities:
        all_raw += fetch_osm(body.state, cats, city)

    # OSM is phone-sparse. Optionally Apollo-enrich the phoneless rows (up to a
    # cap) so the wide net becomes callable instead of getting dropped. Admin
    # only — it spends ~1 Apollo credit per company looked up.
    enriched = 0
    if body.enrich and is_admin(user) and APOLLO_API_KEY and not APOLLO_KILL_SWITCH:
        enrich_deadline = time.time() + OSM_ENRICH_BUDGET_SEC
        for lead in all_raw:
            if enriched >= OSM_ENRICH_CAP or time.time() > enrich_deadline:
                break  # bounded so a big-state pull can't hang the request
            if not is_valid_us_phone(lead.get("phone")):
                if apollo_enrich_lead_in_place(lead):
                    enriched += 1

    res = ingest_leads(all_raw, "OpenStreetMap", user)
    res["apolloEnriched"] = enriched
    audit_log(user, "source_osm", "lead", None,
              {"state": body.state, "categories": cats, "enriched": enriched, **res})
    enrich_note = f" · {enriched} Apollo-enriched" if enriched else ""
    res["summary"] = (f"OSM: {res['found']} found · {res['alreadyInDb']} already in DB · "
                      f"{res['droppedUncallable']} uncallable{enrich_note} · {res['saved']} new saved")
    return res

@app.post("/api/sources/npi")
def source_npi(body: FreeSourceRequest, user: str = Depends(verify_token)):
    """Pull healthcare facilities from the NPI registry by state/city. Free."""
    _free_source_guard(user)
    if not body.state:
        raise HTTPException(status_code=400, detail="Pick a state.")
    cities = [c.strip() for c in (body.cities or "").split(",") if c.strip()] or [""]
    all_raw = []
    for city in cities:
        all_raw += fetch_npi(body.state, city, body.taxonomy or "", body.limit or 200)
    res = ingest_leads(all_raw, "NPI registry", user)
    audit_log(user, "source_npi", "lead", None, {"state": body.state, "taxonomy": body.taxonomy, **res})
    res["summary"] = (f"NPI: {res['found']} found · {res['alreadyInDb']} already in DB · "
                      f"{res['droppedUncallable']} uncallable · {res['saved']} new saved")
    return res

# ════════════════════════════════════════════════════════════════════════════
# PERMIT / NEW-CONSTRUCTION TRIGGER FEED — a commercial build permit is a future
# cleaning need being born. Open-data permit portals vary per jurisdiction, so
# this is a pluggable registry: each entry knows its Socrata dataset + field
# map. Seeded with Austin TX (rich — includes contractor/applicant phones, so
# the leads are actually callable). Add metros over time; many publish nothing
# usable (address only), which is why this is curated, not blanket.
# ════════════════════════════════════════════════════════════════════════════
# Curated Socrata permit datasets — (state, metro, domain, dataset_id). Just an
# address; a heuristic field-detector maps each dataset's columns, so adding a
# metro is one line, not a custom schema. Datasets that change/vanish yield 0
# (engine fails soft). Coverage ≠ every state — many publish no usable permit
# data; this is the metros that do, expandable on request.
SOCRATA_PERMIT_SOURCES = [
    ("TX", "Austin",       "data.austintexas.gov",       "3syk-w9eu"),
    ("TX", "Dallas",       "www.dallasopendata.com",     "e7gq-4sah"),
    ("CA", "Los Angeles",  "data.lacity.org",            "pi9x-tg5x"),
    ("CA", "Roseville",    "data.roseville.ca.us",       "buxi-gsvq"),
    ("FL", "Gainesville",  "data.cityofgainesville.org", "p798-x3nx"),
    ("LA", "Baton Rouge",  "data.brla.gov",              "7fq7-8j7r"),
    ("LA", "New Orleans",  "data.nola.gov",              "nbcf-m6c2"),
    ("OH", "Cincinnati",   "data.cincinnati-oh.gov",     "uhjb-xac9"),
    ("IL", "Chicago",      "data.cityofchicago.org",     "ydr8-5enu"),
    ("CO", "Fort Collins", "opendata.fcgov.com",         "fvgz-viez"),
    ("AZ", "Mesa",         "citydata.mesaaz.gov",        "dzpk-hxfb"),
    ("HI", "Honolulu",     "data.honolulu.gov",          "4vab-c87q"),
    ("WI", "Janesville",   "janesville.data.socrata.com","qskr-bfe6"),
    ("NY", "New York City","data.cityofnewyork.us",      "ipu4-2q9a"),
    ("MA", "Cambridge",    "data.cambridgema.gov",       "9qm7-wbdc"),
]

def _permit_industry(desc: str) -> str:
    d = (desc or "").lower()
    for label, kws in [("Medical", ["medical", "clinic", "hospital", "dental", "health"]),
                       ("Office", ["office"]),
                       ("Education", ["school", "education", "university", "college"]),
                       ("Industrial", ["warehouse", "industrial", "manufactur", "distribution"]),
                       ("Hotel", ["hotel", "motel", "hospitality"]),
                       ("Retail", ["retail", "restaurant", "store", "shopping"])]:
        if any(k in d for k in kws):
            return label
    return "Commercial Construction"

# Keep commercial, drop residential — these run against the description + permit
# type since most datasets lack a clean class flag.
_PERMIT_COMMERCIAL_HINTS = ["commercial", "office", "retail", "tenant", "restaurant", "medical",
    "clinic", "hospital", "school", "university", "warehouse", "industrial", "hotel", "motel",
    "mixed use", "business", "store", "church", "worship", "institutional", "assembly",
    "mercantile", "bank", "daycare", "gym", "fitness", "shopping"]
_PERMIT_RESIDENTIAL_HINTS = ["single family", "single-family", "sfr", " sfd", "1 & 2 family",
    "one and two family", "dwelling", "residential", "townhome", "townhouse", "duplex", "reroof",
    "re-roof", "roof replace", "water heater", "solar", "swimming pool", "fence", "deck", "shed",
    "accessory dwelling", " adu", "mobile home"]

def _permit_is_commercial(klass: str, text: str) -> bool:
    if "commercial" in (klass or "").lower():
        return True
    t = f"{klass} {text}".lower()
    if any(h in t for h in _PERMIT_COMMERCIAL_HINTS):
        return True
    if any(h in t for h in _PERMIT_RESIDENTIAL_HINTS):
        return False
    return False  # unknown → drop, to keep the feed free of residential noise

def _pick_field(keys, priorities, exclude=()):
    """Return ALL original-cased keys matching the priority substrings (none of
    `exclude`), ordered by priority. Socrata omits null fields per-row, so we
    sample many rows AND keep every candidate — the row mapper picks the first
    non-empty (e.g. applicant_phone on one row, contractor_phone on the next)."""
    low = [(k.lower(), k) for k in keys]
    out = []
    for p in priorities:
        for lk, orig in low:
            if p in lk and not any(x in lk for x in exclude) and orig not in out:
                out.append(orig)
    return out

def _permit_val(row, candidates):
    """First non-empty value among candidate field names."""
    for c in candidates:
        v = row.get(c)
        if v not in (None, "", "N/A"):
            return v
    return ""

def _detect_permit_fields(k: list) -> dict:
    return {
        "addr":   _pick_field(k, ["original_address1", "street_address", "streetaddress",
                  "primary_address", "property_address", "project_address", "site_address", "address"],
                  exclude=["contact", "contractor", "applicant", "owner", "mail"]),
        "house":  _pick_field(k, ["street_number", "streetnumber", "house_number"]),
        "street": _pick_field(k, ["street_name", "streetname"]),
        "city":   _pick_field(k, ["original_city", "project_city", "site_city", "city"],
                  exclude=["contact", "contractor", "applicant", "owner", "capacity"]),
        "state":  _pick_field(k, ["original_state", "project_state", "site_state", "state"],
                  exclude=["contact", "contractor", "applicant", "owner", "real_estate", "statu"]),
        "date":   _pick_field(k, ["issue_date", "issued_date", "issueddate", "permit_issue", "issue",
                  "applieddate", "application_start", "permit_date", "file_date"]),
        "desc":   _pick_field(k, ["work_description", "description_of_work", "projectdescription",
                  "b1_work_desc", "scope", "description"],
                  exclude=["contact", "contractor", "applicant", "owner"]),
        "ptype":  _pick_field(k, ["permit_type_desc", "permittypemapped", "permit_type", "permittype",
                  "work_type", "worktype", "subtype", "type"]),
        "company": _pick_field(k, ["applicant_org", "company_name", "companyname", "business_name",
                  "contractor_company", "contractor_name", "contractorname", "contractor",
                  "applicant_name", "applicantname", "owner_name", "ownername", "contact_1_name", "owner"],
                  exclude=["address", "city", "state", "zip", "phone", "trade", "type"]),
        "phone":  _pick_field(k, ["applicant_phone", "contractor_phone", "phone"]),
        "name":   _pick_field(k, ["applicant_full_name", "contractor_full_name", "applicantname",
                  "contact_1_name"]),
        "klass":  _pick_field(k, ["permit_class_mapped", "permitclassmapped", "permit_class",
                  "permitclass", "class"]),
    }

def fetch_permits(state_abbrev: str, days: int = 30) -> list:
    """Pull recent commercial permits for every configured metro in a state.
    Heuristically maps each dataset's schema. Returns raw lead dicts tagged
    [INTENT:newbuild]. Free, no key. (days kept for signature compat.)"""
    sources = [s for s in SOCRATA_PERMIT_SOURCES if s[0] == state_abbrev.upper()]
    leads = []
    for st, name, domain, rid in sources:
        base = f"https://{domain}/resource/{rid}.json"
        hdr = {"User-Agent": "LeadFlow/1.0 (Vision Cleaning)"}
        try:  # sample many rows (Socrata drops null fields per-row) → union of keys
            sr = req_lib.get(base, params={"$limit": 50}, headers=hdr, timeout=25)
            sample = sr.json() if sr.status_code == 200 else []
            if not sample:
                print(f"[PERMITS] {name}: sample HTTP {sr.status_code}")
                continue
            union_keys = set()
            for row in sample:
                union_keys.update(row.keys())
            f = _detect_permit_fields(sorted(union_keys))
        except Exception as e:
            print(f"[PERMITS] {name}: sample failed {e}")
            continue
        params = {"$limit": FREE_SOURCE_MAX_ROWS}
        if f["date"]:
            params["$order"] = f"{f['date']} DESC"   # newest first
        try:
            r = req_lib.get(base, params=params, headers=hdr, timeout=45)
            if r.status_code != 200 and "$order" in params:  # bad date type → retry unordered
                r = req_lib.get(base, params={"$limit": FREE_SOURCE_MAX_ROWS}, headers=hdr, timeout=45)
            rows = r.json() if r.status_code == 200 else []
        except Exception as e:
            print(f"[PERMITS] {name}: fetch failed {e}")
            continue
        for row in rows:
            desc  = _permit_val(row, f["desc"])
            ptype = _permit_val(row, f["ptype"])
            klass = _permit_val(row, f["klass"])
            if not _permit_is_commercial(klass, f"{desc} {ptype}"):
                continue
            company = _permit_val(row, f["company"])
            addr = _permit_val(row, f["addr"])
            if not addr and f["house"] and f["street"]:
                addr = " ".join(x for x in [_permit_val(row, f["house"]), _permit_val(row, f["street"])] if x).strip()
            full = _permit_val(row, f["name"])
            fn, ln = "", ""
            if full:
                parts = str(full).split()
                fn, ln = parts[0], " ".join(parts[1:]) if len(parts) > 1 else ""
            if not company:
                company = full or (f"New commercial build — {addr}".strip(" —")) or desc[:50]
            leads.append({
                "company": company, "industry": _permit_industry(f"{desc} {ptype}"),
                "phone": _permit_val(row, f["phone"]), "firstName": fn, "lastName": ln,
                "address": addr, "city": _permit_val(row, f["city"]),
                "state": _permit_val(row, f["state"]) or state_abbrev.upper(),
                "notes": f"[INTENT:newbuild] Commercial permit ({name}): "
                         f"{(desc or ptype)[:120]} @ {addr}".strip(),
            })
    return leads

@app.post("/api/sources/permits")
def source_permits(body: FreeSourceRequest, user: str = Depends(verify_token)):
    """Pull recent commercial new-construction permits (trigger leads). Free.
    Coverage is metro-seeded — returns a clear note for unconfigured states."""
    _free_source_guard(user)
    if not body.state:
        raise HTTPException(status_code=400, detail="Pick a state.")
    configured = {s[0] for s in SOCRATA_PERMIT_SOURCES}
    if body.state.upper() not in configured:
        return {"found": 0, "saved": 0, "alreadyInDb": 0, "droppedUncallable": 0,
                "summary": f"No permit feed configured for {body.state} yet. Covered states: "
                           f"{', '.join(sorted(configured))}. (Permit portals are per-metro; ask to add yours.)"}
    raw = fetch_permits(body.state, body.limit if (body.limit and body.limit < 121) else 30)
    res = ingest_leads(raw, "Permit (new construction)", user)
    audit_log(user, "source_permits", "lead", None, {"state": body.state, **res})
    res["summary"] = (f"Permits: {res['found']} commercial builds found · {res['alreadyInDb']} already in DB · "
                      f"{res['droppedUncallable']} no contact · {res['saved']} new saved")
    return res

# ════════════════════════════════════════════════════════════════════════════
# HEALTH-INSPECTION FEED — a FAILED government health inspection is the most
# urgent cleaning signal there is: an inspector documented the sanitation
# problem, there's a re-inspection clock ticking, and the violation text is the
# pitch. These are "call today" leads. Per-metro (county/city) open data on
# Socrata; explicit per-source field map since grading systems vary wildly.
# ════════════════════════════════════════════════════════════════════════════
HEALTH_INSPECTION_SOURCES = [
    {"state": "IL", "metro": "Chicago", "domain": "data.cityofchicago.org", "id": "4ijn-s7e5",
     "name": "dba_name", "addr": "address", "city": "city", "date": "inspection_date",
     "viol": "violations", "result": "results", "ftype": "facility_type",
     "where": "results='Fail'"},
    {"state": "NY", "metro": "New York City", "domain": "data.cityofnewyork.us", "id": "43nn-pn8j",
     "name": "dba", "addr_parts": ["building", "street"], "city": "boro", "date": "inspection_date",
     "viol": "violation_description", "result": "grade", "ftype": "cuisine_description",
     "where": "grade in('B','C')"},
    # (King County/WA dropped — its open data lags ~7 months, too stale for an
    #  "urgent" signal. Add metros only where inspections post within weeks.)
    {"state": "TX", "metro": "Austin", "domain": "data.austintexas.gov", "id": "ecmv-9xxi",
     "name": "restaurant_name", "addr": "address", "city_const": "Austin", "date": "inspection_date",
     "viol": "process_description", "result": "score", "ftype": None,
     "where": "score < 70"},  # Austin posts 0-100; <70 = serious problems
]

def google_place_phone(name: str, city: str, state: str) -> str:
    """Look up a business's public phone via Google Places (Find Place →
    Details). Best phone source for food-service facilities (restaurants list a
    public number; Apollo targets office DMs, not diners). ~$0.035/lead."""
    pid = google_find_place_id(name, city, state)
    if not pid:
        return ""
    try:
        det = req_lib.get("https://maps.googleapis.com/maps/api/place/details/json",
                          params={"place_id": pid, "fields": "formatted_phone_number", "key": GOOGLE_KEY},
                          timeout=10).json().get("result", {})
        return clean(det.get("formatted_phone_number", ""))
    except Exception:
        return ""

# CMS (Medicare) nursing-home data — nationwide (all 50 states), free, no key,
# and the Provider table includes phone numbers. Health Deficiencies has 418k
# infection-control / sanitation citations; we surface the facilities flagged
# for infection control (the cleaning-relevant ones) and join their phone.
CMS_BASE = "https://data.cms.gov/provider-data/api/1/datastore/query"
CMS_DEFICIENCIES_ID = "327a1777-6c39-5872-9874-c18728c3c104"
CMS_PROVIDER_ID     = "c977e9dc-c100-5531-aeb5-80cdff2eacc5"

# Cleanliness/sanitation-relevant deficiency descriptions (beyond the explicit
# infection-control flag) — keeps the pitch on-message while letting us pull the
# FRESHEST surveys regardless of how a given citation was coded.
CMS_CLEAN_KEYWORDS = ["infection", "clean", "sanitat", "sanitary", "environment", "housekeep",
    "garbage", "pest", "vermin", "soiled", "dirty", "linen", "laundry", "disinfect",
    "hand hygiene", "odor", "mold", "sewage", "waste", "food storage", "hazardous", "ppe"]

def fetch_cms_nursing(state_abbrev: str) -> list:
    """All-50-states nursing facilities with recent cleanliness/sanitation
    deficiencies, FRESHEST survey first, deduped to one lead/facility, phone
    joined from the CMS Provider table. Boosts Special Focus Facilities (CMS's
    actively-watched worst performers) + complaint-triggered surveys."""
    st = state_abbrev.upper()
    hdr = {"User-Agent": "LeadFlow/1.0 (Vision Cleaning)"}
    # 1) Recent deficiencies in this state, NEWEST first (no infection-only
    #    server filter — that skewed old; we filter for relevance client-side).
    #    CMS caps limit at 500, so paginate the newest several pages so we get
    #    enough unique facilities without reaching back into stale years.
    defs = []
    try:
        offset = 0
        while offset < 3000:
            dj = req_lib.get(f"{CMS_BASE}/{CMS_DEFICIENCIES_ID}", headers=hdr, timeout=45, params={
                "limit": 500, "offset": offset,
                "conditions[0][property]": "state", "conditions[0][value]": st, "conditions[0][operator]": "=",
                "sort[0][property]": "survey_date", "sort[0][order]": "desc"})
            page = dj.json().get("results", []) if dj.status_code == 200 else []
            defs.extend(page)
            if len(page) < 500:
                break
            offset += 500
    except Exception as e:
        print(f"[CMS] deficiency fetch failed: {e}")
        if not defs:
            return []
    # 2) Provider phones/ratings for this state → CCN map. CMS caps limit at
    #    500, so paginate (a big state has ~1200 facilities = ~3 pages).
    prov = {}
    try:
        offset = 0
        while offset < 4000:
            pj = req_lib.get(f"{CMS_BASE}/{CMS_PROVIDER_ID}", headers=hdr, timeout=45, params={
                "limit": 500, "offset": offset,
                "conditions[0][property]": "state", "conditions[0][value]": st, "conditions[0][operator]": "="})
            page = pj.json().get("results", []) if pj.status_code == 200 else []
            for p in page:
                prov[p.get("cms_certification_number_ccn")] = p
            if len(page) < 500:
                break
            offset += 500
    except Exception as e:
        print(f"[CMS] provider fetch failed: {e}")

    leads, seen = [], set()
    for d in defs:
        ccn = d.get("cms_certification_number_ccn")
        if not ccn or ccn in seen:        # one lead per facility (freshest kept)
            continue
        desc = clean(d.get("deficiency_description", ""))
        desc_l = desc.lower()
        # Relevance: infection-control flag OR a cleanliness/sanitation keyword.
        if d.get("infection_control_inspection_deficiency") != "Y" and \
           not any(k in desc_l for k in CMS_CLEAN_KEYWORDS):
            continue
        seen.add(ccn)
        p = prov.get(ccn, {})
        name = clean(d.get("provider_name") or p.get("provider_name") or "")
        if not name:
            continue
        survey = str(d.get("survey_date", ""))[:10]
        age_days = None
        try:
            age_days = max(0, (datetime.utcnow() - datetime.strptime(survey, "%Y-%m-%d")).days)
        except Exception:
            pass
        sev = clean(d.get("scope_severity_code", ""))
        sff = clean(p.get("special_focus_status", ""))        # "SFF" / "SFF Candidate" / ""
        complaint = d.get("complaint_deficiency") == "Y"
        tags = []
        if sff:        tags.append(f"⚠ CMS Special Focus ({sff})")
        if complaint:  tags.append("complaint-triggered")
        rating = p.get("overall_rating")
        if rating:     tags.append(f"{rating}★ overall")
        age_tag = f" [hvage:{age_days}]" if age_days is not None else ""
        leads.append({
            "company": name, "industry": "Nursing Facility",
            "phone": clean(p.get("telephone_number", "")),
            "address": clean(d.get("provider_address") or p.get("provider_address") or ""),
            "city": clean(d.get("citytown") or p.get("citytown") or ""), "state": st,
            "notes": f"[INTENT:health_violation] CMS deficiency (survey {survey}, severity {sev}): "
                     f"{desc[:150]}{(' | ' + ' · '.join(tags)) if tags else ''}{age_tag}".strip(),
        })
        if len(leads) >= FREE_SOURCE_MAX_ROWS:
            break
    return leads

# Other CMS facility types. CMS only publishes structured DEFICIENCIES for
# nursing homes; for hospitals the equivalent "sanitation problem" signal is a
# worse-than-national Healthcare-Associated-Infection (HAI) rate, and for
# dialysis it's a low CMS star rating. Framework so more types drop in cleanly.
CMS_HOSPITAL_GENERAL_ID = "b0a92ff7-a457-54f9-b247-20022db14590"
CMS_HAI_ID              = "76ff056f-2b95-5594-ab53-c9b5a1e4db43"
CMS_DIALYSIS_ID         = "bd6d011d-7e7e-5964-be9a-a0bbf4010e6c"

def _cms_paginate(did, params, cap=4000):
    """Page through a CMS datastore query (500-row cap) up to `cap` rows."""
    hdr = {"User-Agent": "LeadFlow/1.0 (Vision Cleaning)"}
    out, offset = [], 0
    while offset < cap:
        try:
            r = req_lib.get(f"{CMS_BASE}/{did}", headers=hdr, timeout=45,
                            params={**params, "limit": 500, "offset": offset})
            page = r.json().get("results", []) if r.status_code == 200 else []
        except Exception as e:
            print(f"[CMS] paginate {did[:8]} failed: {e}"); break
        out.extend(page)
        if len(page) < 500:
            break
        offset += 500
    return out

def fetch_cms_hospitals(state_abbrev: str) -> list:
    """Hospitals with a WORSE-than-national infection (HAI) rate — the hospital
    equivalent of a sanitation problem. Phone/address from Hospital General
    Information. All 50 states."""
    st = state_abbrev.upper()
    hai = _cms_paginate(CMS_HAI_ID, {
        "conditions[0][property]": "state", "conditions[0][value]": st, "conditions[0][operator]": "=",
        "conditions[1][property]": "compared_to_national",
        "conditions[1][value]": "Worse than the National Benchmark", "conditions[1][operator]": "="})
    worst = {}
    for r in hai:
        fid = r.get("facility_id")
        if not fid:
            continue
        w = worst.setdefault(fid, {"name": r.get("facility_name", ""), "measures": []})
        m = clean(r.get("measure_name", "")).replace(" (ICU + select Wards)", "")
        if m and m not in w["measures"] and len(w["measures"]) < 3:
            w["measures"].append(m)
    if not worst:
        return []
    # Phone/address/rating by facility_id.
    gmap = {}
    for g in _cms_paginate(CMS_HOSPITAL_GENERAL_ID, {
            "conditions[0][property]": "state", "conditions[0][value]": st, "conditions[0][operator]": "="}):
        gmap[g.get("facility_id")] = g
    leads = []
    for fid, w in worst.items():
        g = gmap.get(fid, {})
        name = clean(w["name"] or g.get("facility_name", ""))
        if not name:
            continue
        measures = ", ".join(w["measures"]) or "infection rates"
        leads.append({
            "company": name, "industry": "Hospital",
            "phone": clean(g.get("telephone_number", "")),
            "address": clean(g.get("address", "")), "city": clean(g.get("citytown", "")), "state": st,
            "notes": f"[INTENT:health_violation] CMS: worse-than-national infection rate ({measures})"
                     f"{(' | ' + str(g.get('hospital_overall_rating')) + '★ overall') if g.get('hospital_overall_rating') else ''}".strip(),
        })
        if len(leads) >= FREE_SOURCE_MAX_ROWS:
            break
    return leads

def fetch_cms_dialysis(state_abbrev: str) -> list:
    """Dialysis centers (infection-critical, real cleaning need). CMS listing has
    name/address/phone + a 5-star rating; we surface the BELOW-AVERAGE (1-2★)
    ones as the 'needs help' signal."""
    st = state_abbrev.upper()
    rows = _cms_paginate(CMS_DIALYSIS_ID, {
        "conditions[0][property]": "state", "conditions[0][value]": st, "conditions[0][operator]": "="})
    leads = []
    for r in rows:
        star_raw = clean(r.get("five_star", ""))
        try:
            star = float(star_raw)
        except ValueError:
            continue
        if star > 2:                      # only the below-average (1-2★) ones signal a need
            continue
        name = clean(r.get("facility_name", ""))
        if not name:
            continue
        addr = " ".join(clean(r.get(p, "")) for p in ["address_line_1", "address_line_2"]).strip()
        leads.append({
            "company": name, "industry": "Dialysis Center",
            "phone": clean(r.get("telephone_number", "")),
            "address": addr, "city": clean(r.get("citytown", "")), "state": st,
            "notes": f"[INTENT:health_violation] CMS {int(star)}★ dialysis facility (below average) — "
                     f"infection control is critical here",
        })
        if len(leads) >= FREE_SOURCE_MAX_ROWS:
            break
    return leads

# Registry — add a facility type by dropping in (label, fetcher). The endpoint
# pulls every type for a state. Nursing homes carry the deepest signal.
CMS_HEALTHCARE_FETCHERS = [
    ("nursing",  fetch_cms_nursing),
    ("hospital", fetch_cms_hospitals),
    ("dialysis", fetch_cms_dialysis),
]

def fetch_health_inspections(state_abbrev: str, days: int = 90) -> list:
    """Pull recent FAILED health inspections for configured metros in a state.
    Dedups multi-violation rows to one lead per facility, tagged
    [INTENT:health_violation] with the violation as the pitch. Free, no key."""
    sources = [s for s in HEALTH_INSPECTION_SOURCES if s["state"] == state_abbrev.upper()]
    cutoff = (datetime.utcnow() - timedelta(days=max(1, min(days, 180)))).strftime("%Y-%m-%dT00:00:00")
    leads, seen = [], set()
    for s in sources:
        where = f"{s['where']} and {s['date']} > '{cutoff}'"
        try:
            r = req_lib.get(f"https://{s['domain']}/resource/{s['id']}.json",
                            params={"$where": where, "$order": f"{s['date']} DESC",
                                    "$limit": FREE_SOURCE_MAX_ROWS * 2},
                            headers={"User-Agent": "LeadFlow/1.0 (Vision Cleaning)"}, timeout=45)
            rows = r.json() if r.status_code == 200 else []
            if r.status_code != 200:
                print(f"[HEALTH] {s['metro']} HTTP {r.status_code}: {str(rows)[:150]}")
        except Exception as e:
            print(f"[HEALTH] {s['metro']} failed: {e}")
            continue
        for row in rows:
            name = clean(row.get(s["name"], ""))
            if not name:
                continue
            addr = clean(row.get(s["addr"], "")) if s.get("addr") else \
                   " ".join(clean(row.get(p, "")) for p in s.get("addr_parts", [])).strip()
            key = (name.lower(), addr.lower())
            if key in seen:           # one lead per facility (rows repeat per violation)
                continue
            seen.add(key)
            date = str(row.get(s["date"], ""))[:10]
            age_days = None
            try:
                age_days = max(0, (datetime.utcnow() - datetime.strptime(date, "%Y-%m-%d")).days)
            except Exception:
                pass
            viol = clean(row.get(s["viol"], ""))[:160]
            result = clean(row.get(s["result"], ""))
            ftype = clean(row.get(s["ftype"], "")) if s.get("ftype") else ""
            age_tag = f" [hvage:{age_days}]" if age_days is not None else ""
            lead_city = clean(row.get(s["city"], "")) if s.get("city") else s.get("city_const", "")
            leads.append({
                "company": name, "industry": ftype or "Food Service", "phone": "",
                "address": addr, "city": lead_city, "state": state_abbrev.upper(),
                "notes": f"[INTENT:health_violation] Failed health inspection ({s['metro']}, {date}) — "
                         f"{result}: {viol}{age_tag}".strip(),
            })
            if len(leads) >= FREE_SOURCE_MAX_ROWS:
                break
    return leads

@app.post("/api/sources/health")
def source_health(body: FreeSourceRequest, user: str = Depends(verify_token)):
    """Failed health inspections (urgent, call-today leads). PRIMARY source is
    CMS nursing-home infection-control deficiencies — nationwide (all 50 states),
    healthcare facilities with real cleaning budgets, phones included. Restaurant
    inspections (city-run, low-budget) are opt-in via restaurants=true."""
    _free_source_guard(user)
    if not body.state:
        raise HTTPException(status_code=400, detail="Pick a state.")

    # PRIMARY: CMS healthcare facilities (nursing homes + hospitals + dialysis),
    # all 50 states, phones included. Each type carries its own sanitation signal.
    raw, by_type = [], {}
    for label, fetcher in CMS_HEALTHCARE_FETCHERS:
        try:
            part = fetcher(body.state)
        except Exception as e:
            print(f"[CMS] {label} fetch error: {e}"); part = []
        by_type[label] = len(part)
        raw += part
    cms_n = len(raw)

    # Apollo-enrich the HIGHEST-VALUE facilities with the actual cleaning buyer
    # (EVS / facilities / administrator) — the CMS phone is the main line, not
    # the decision-maker. Enrich top-scored first (the ones called first), capped
    # + time-budgeted. On by default for health (enrich!=false); admin only.
    dm_enriched = 0
    if body.enrich is not False and is_admin(user) and APOLLO_API_KEY and not APOLLO_KILL_SWITCH and raw:
        raw.sort(key=lambda l: score_lead(l), reverse=True)   # call-order first
        deadline = time.time() + OSM_ENRICH_BUDGET_SEC
        for lead in raw:
            if dm_enriched >= OSM_ENRICH_CAP or time.time() > deadline:
                break
            if apollo_enrich_lead_in_place(lead, titles=APOLLO_HEALTHCARE_TITLES):
                dm_enriched += 1

    # OPT-IN: restaurant inspections (3 metros) — Google-phone-matched. Off by
    # default because restaurants rarely have a cleaning budget.
    restaurant_n = 0
    if body.restaurants:
        rest = fetch_health_inspections(body.state, body.limit if (body.limit and body.limit < 181) else 90)
        restaurant_n = len(rest)
        on, _src = is_kill_switch_on()
        if GOOGLE_KEY and not on and rest:
            deadline = time.time() + OSM_ENRICH_BUDGET_SEC
            phoned = 0
            for lead in rest:
                if phoned >= OSM_ENRICH_CAP or time.time() > deadline:
                    break
                ph = google_place_phone(lead["company"], lead.get("city", ""), lead.get("state", ""))
                if ph:
                    lead["phone"] = ph; phoned += 1
                    log_usage(user, "google_find_place", {"company": lead["company"]})
                    log_usage(user, "google_details", {"company": lead["company"]})
        raw = raw + rest

    res = ingest_leads(raw, "Health Inspection", user)
    res["dmEnriched"] = dm_enriched
    audit_log(user, "source_health", "lead", None,
              {"state": body.state, "by_type": by_type, "restaurants": restaurant_n,
               "dm_enriched": dm_enriched, **res})
    breakdown = ", ".join(f"{n} {lbl}" for lbl, n in by_type.items() if n)
    res["summary"] = (f"Health: {cms_n} healthcare facilities ({breakdown})"
                      f"{f' + {restaurant_n} restaurants' if body.restaurants else ''} · "
                      f"{dm_enriched} decision-makers found (Apollo) · "
                      f"{res['alreadyInDb']} already in DB · {res['saved']} new urgent leads saved")
    return res

# ════════════════════════════════════════════════════════════════════════════
# GOOGLE-REVIEW CLEANLINESS INTENT — the out-of-the-box play. A business with a
# recent review complaining about dirtiness has an ACUTE need and a built-in
# pitch. We scan existing leads' Google reviews, stamp [INTENT:cleanliness] +
# a quotable snippet, and re-score so they rocket up the dialer.
# ════════════════════════════════════════════════════════════════════════════
# STRONG terms fire on their own — high-confidence facility-cleanliness signals.
CLEANLINESS_STRONG_TERMS = [
    "filthy", "unsanitary", "disgusting", "mold", "mildew", "cockroach", "roaches",
    "rodent", "mice", "feces", "urine", "vomit", "biohazard", "grimy", "grime",
    "dirty bathroom", "dirty restroom", "dirty toilet", "filthy bathroom",
    "bathroom was disgusting", "restroom was disgusting", "needs a deep clean",
    "never cleaned", "needs cleaning", "trash everywhere", "covered in dust",
]
# CONTEXT terms (dirty/smell/etc.) only count when a FACILITY word is nearby —
# kills false positives like a scrap yard making "clean metal dirty".
CLEANLINESS_CONTEXT_TERMS = ["dirty", "unclean", "not clean", "wasn't clean",
    "stained", "sticky", "dusty", "smelled", "smelly", "foul smell", "bad smell", "gross"]
CLEANLINESS_FACILITY_WORDS = ["bathroom", "restroom", "toilet", "floor", "floors",
    "table", "tables", "seat", "chair", "room", "rooms", "kitchen", "counter",
    "locker", "shower", "gym", "lobby", "entrance", "carpet", "wall", "window",
    "facility", "place was", "store was", "everything was"]

def _is_cleanliness_complaint(text_lower: str) -> bool:
    if any(t in text_lower for t in CLEANLINESS_STRONG_TERMS):
        return True
    if any(t in text_lower for t in CLEANLINESS_CONTEXT_TERMS) and \
       any(f in text_lower for f in CLEANLINESS_FACILITY_WORDS):
        return True
    return False

def google_find_place_id(name: str, city: str, state: str) -> str:
    """Resolve a business to a Google place_id via Find Place From Text."""
    q = " ".join(x for x in [name, city, state] if x).strip()
    try:
        r = req_lib.get(
            "https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
            params={"input": q, "inputtype": "textquery", "fields": "place_id", "key": GOOGLE_KEY},
            timeout=12)
        cands = r.json().get("candidates", [])
        return cands[0].get("place_id", "") if cands else ""
    except Exception as e:
        print(f"[REVIEWS] find_place failed: {e}")
        return ""

def google_place_reviews(place_id: str):
    """Fetch reviews + rating for a place. Google caps Details at 5 reviews per
    call and has no 'lowest rating' sort, so we STACK the two available sorts —
    `newest` (recent complaints) + `most_relevant` (which surfaces the high-
    engagement, often-negative reviews) — and union them. Up to ~10 distinct
    reviews instead of 5, then the rating<=3 filter picks the lowest among them.
    Costs 2 Details calls per business (~$0.034)."""
    def _details(sort):
        params = {"place_id": place_id, "fields": "reviews,rating,user_ratings_total",
                  "key": GOOGLE_KEY}
        if sort:
            params["reviews_sort"] = sort
        try:
            res = req_lib.get("https://maps.googleapis.com/maps/api/place/details/json",
                              params=params, timeout=12).json().get("result", {})
            return res.get("reviews", []) or [], res.get("rating"), res.get("user_ratings_total")
        except Exception as e:
            print(f"[REVIEWS] details({sort}) failed: {e}")
            return [], None, None

    newest, rating, total = _details("newest")
    relevant, r2, t2 = _details("most_relevant")
    rating = rating or r2
    total = total or t2
    merged, seen = [], set()
    for rev in (newest + relevant):
        key = (rev.get("author_name", ""), rev.get("time", 0))
        if key not in seen:
            seen.add(key)
            merged.append(rev)
    return merged, rating, total

def scan_cleanliness(reviews: list):
    """Find the MOST RECENT low-rated review complaining about cleanliness. A
    fresh complaint = an urgent, acute need, so recency matters as much as the
    hit itself. Returns (hit, snippet, age_days, relative_desc).
    age_days is None when Google doesn't give a timestamp."""
    matches = []
    for rev in reviews:
        text = (rev.get("text") or "").lower()
        rating = rev.get("rating", 5)
        if rating and rating <= 3 and _is_cleanliness_complaint(text):
            matches.append(rev)
    if not matches:
        return False, "", None, ""
    # Newest first — Google gives `time` (unix seconds) + relative_time_description.
    matches.sort(key=lambda r: r.get("time", 0), reverse=True)
    best = matches[0]
    snippet = (best.get("text") or "").strip().replace("\n", " ")
    if len(snippet) > 140:
        snippet = snippet[:137] + "…"
    age_days = None
    if best.get("time"):
        age_days = max(0, int((time.time() - best["time"]) / 86400))
    rel = best.get("relative_time_description", "")
    label = f' ({rel})' if rel else ""
    return True, f'"{snippet}" ({best.get("rating", "?")}★{label})', age_days, rel

class ReviewScanRequest(BaseModel):
    state:      Optional[str] = ""
    cities:     Optional[str] = ""
    industries: Optional[object] = None  # restrict to these verticals (e.g. gyms/venues)
    limit:      Optional[int] = 25        # max leads to scan this run (Google calls cost)

@app.post("/api/enrich/reviews")
def enrich_reviews(body: ReviewScanRequest, user: str = Depends(verify_admin)):
    """Scan existing leads' Google reviews for cleanliness complaints and flag
    the hits as hot intent. Admin-only (burns Google Places calls). Each lead =
    1 Find Place + 1 Details call (~$0.05). Capped per run."""
    on, _ = is_kill_switch_on()
    if on:
        raise HTTPException(status_code=503, detail="Google Places is disabled (kill switch).")
    if not GOOGLE_KEY:
        raise HTTPException(status_code=400, detail="GOOGLE_API_KEY not configured.")
    scan_n = min(max(int(body.limit or 25), 1), 100)

    # Vertical filter (case-insensitive substring match against lead.industry).
    want_inds = [str(x).strip().lower() for x in body.industries] if isinstance(body.industries, list) \
                else [s.strip().lower() for s in str(body.industries or "").split(",") if s.strip()]
    # Build the query via params so PostgREST encodes correctly. Crucially, the
    # vertical filter is SERVER-SIDE (or= of ilike patterns) — PostgREST caps a
    # response near 1000 rows, so a client-side filter over "newest 1000" would
    # miss older target leads (e.g. gyms pulled before today's healthcare sweep).
    params = {
        "select": "id,company,city,state,notes,score,phone,firstName,email,industry,title",
        "company": "not.is.null",
        "order": "createdAt.desc",
        "limit": str(scan_n * (40 if want_inds else 3)),
    }
    if body.state:
        params["state"] = f"eq.{body.state}"
    if want_inds:
        params["or"] = "(" + ",".join(f"industry.ilike.*{w}*" for w in want_inds) + ")"
    try:
        r = req_lib.get(f"{SUPABASE_URL}/rest/v1/leads", params=params, headers=SB_HEADERS, timeout=25)
        candidates = r.json() if r.status_code == 200 else []
        if r.status_code != 200:
            print(f"[REVIEWS] candidate fetch {r.status_code}: {r.text[:160]}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Lead fetch failed: {e}")

    flagged, scanned, samples = 0, 0, []
    for lead in candidates:
        if scanned >= scan_n:
            break
        if "[INTENT:cleanliness]" in (lead.get("notes") or ""):
            continue
        if want_inds:
            ind = (lead.get("industry") or "").lower()
            if not any(w in ind for w in want_inds):
                continue
        city = lead.get("city") or ""
        if body.cities and city.lower() not in {c.strip().lower() for c in body.cities.split(",")}:
            continue
        scanned += 1
        log_usage(user, "google_find_place", {"company": lead.get("company")})
        pid = google_find_place_id(lead.get("company",""), city, lead.get("state",""))
        if not pid:
            continue
        log_usage(user, "google_details", {"place_id": pid})
        reviews, rating, _ = google_place_reviews(pid)
        hit, snippet, age_days, rel = scan_cleanliness(reviews)
        if not hit:
            continue
        # Stamp a parseable age token so score_lead can decay older complaints.
        age_tag = f" [clnage:{age_days}]" if age_days is not None else ""
        add_intent_marker(lead, "cleanliness", f"Review complaint: {snippet}{age_tag}")
        new_score = score_lead(lead)
        try:
            req_lib.patch(
                f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(str(lead['id']))}",
                headers={**SB_HEADERS, "Prefer": "return=minimal"},
                json={"notes": lead["notes"], "score": new_score}, timeout=15)
            flagged += 1
            if len(samples) < 8:
                samples.append({"company": lead.get("company"), "city": city,
                                "score": new_score, "snippet": snippet,
                                "posted": rel or "?", "age_days": age_days})
        except Exception as e:
            print(f"[REVIEWS] patch failed for {lead.get('id')}: {e}")

    # Freshest complaint first — that's the order a caller should work them.
    samples.sort(key=lambda s: (s.get("age_days") if s.get("age_days") is not None else 99999))
    audit_log(user, "enrich_reviews", "lead", None,
              {"scanned": scanned, "flagged": flagged, "state": body.state})
    if flagged:
        send_slack("🔥 Cleanliness-complaint leads found",
                   f"*{user}* scanned {scanned} leads → *{flagged}* have Google reviews complaining "
                   f"about cleanliness. Freshest complaints are now top-scored in the dialer.",
                   fields=[{"label": f"{s['company']} · {s['posted']}", "value": s["snippet"]} for s in samples[:5]])
    return {"scanned": scanned, "flagged": flagged,
            "summary": f"Scanned {scanned} leads · {flagged} have cleanliness complaints (freshest = hottest)",
            "samples": samples}

# ════════════════════════════════════════════════════════════════════════════
# LOOKALIKE SCORING — your own wins become a free targeting model. Learn which
# verticals actually convert, then boost matching un-worked leads. No API cost.
# ════════════════════════════════════════════════════════════════════════════
def _fit_tier_index(lead: dict) -> int:
    """Which cleaning-fit tier a lead falls in (index into CLEANING_FIT_TIERS),
    or -1 if none. Used as the 'vertical bucket' for lookalike matching."""
    t = f"{lead.get('industry','')} {lead.get('company','')} {lead.get('title','')}".lower()
    for i, (_pts, kws) in enumerate(CLEANING_FIT_TIERS):
        if any(k in t for k in kws):
            return i
    return -1

@app.post("/api/enrich/lookalike")
def enrich_lookalike(user: str = Depends(verify_admin)):
    """Profile the verticals + states that actually CONVERT, then stamp
    [INTENT:lookalike] on un-worked leads that match — so your dialer favors
    the kinds of accounts you've already won. Free; learns from your own data."""
    MIN_WINS = 5
    try:
        rc = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads?select=industry,company,title,state"
            f"&status=eq.converted&limit=2000", headers=SB_HEADERS, timeout=20)
        wins = rc.json() if rc.status_code == 200 else []
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Win fetch failed: {e}")
    if len(wins) < MIN_WINS:
        return {"applied": 0, "wins": len(wins),
                "summary": f"Only {len(wins)} converted leads — need {MIN_WINS}+ before lookalike "
                           f"targeting is meaningful. Keep closing; this sharpens as you win."}

    from collections import Counter
    tier_ct, state_ct = Counter(), Counter()
    for w in wins:
        ti = _fit_tier_index(w)
        if ti >= 0:
            tier_ct[ti] += 1
        st = (w.get("state") or "").upper()
        if st:
            state_ct[st] += 1
    # Winning verticals = tiers covering the bulk of wins (top tiers up to ~75%).
    win_tiers = {ti for ti, _ in tier_ct.most_common(3)}
    win_states = {s for s, _ in state_ct.most_common(8)}

    # Scan un-worked leads (new, not already lookalike-tagged) and boost matches.
    try:
        rl = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads?select=id,industry,company,title,state,notes,score,phone,firstName,email"
            f"&status=eq.new&order=createdAt.desc&limit=3000", headers=SB_HEADERS, timeout=30)
        candidates = rl.json() if rl.status_code == 200 else []
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Lead fetch failed: {e}")

    applied, already = 0, 0
    for lead in candidates:
        if "[INTENT:lookalike]" in (lead.get("notes") or ""):
            already += 1   # already boosted on a prior run — idempotent
            continue
        ti = _fit_tier_index(lead)
        if ti not in win_tiers:
            continue
        # Vertical match required; same-state is a bonus reason, not a gate.
        why = "matches a vertical you convert"
        if (lead.get("state") or "").upper() in win_states:
            why += " in a state you win in"
        add_intent_marker(lead, "lookalike", why)
        try:
            pr = req_lib.patch(
                f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(str(lead['id']))}",
                headers={**SB_HEADERS, "Prefer": "return=minimal"},
                json={"notes": lead["notes"], "score": score_lead(lead)}, timeout=15)
            if pr.status_code in (200, 204):
                applied += 1
        except Exception as e:
            print(f"[LOOKALIKE] patch failed for {lead.get('id')}: {e}")

    top_labels = [", ".join(CLEANING_FIT_TIERS[ti][1][:2]) for ti, _ in tier_ct.most_common(3)]
    audit_log(user, "enrich_lookalike", "lead", None,
              {"wins": len(wins), "applied": applied, "win_states": list(win_states)})
    already_note = f" ({already} already boosted earlier)" if already else ""
    return {"applied": applied, "wins": len(wins), "alreadyBoosted": already,
            "winning_verticals": top_labels, "winning_states": sorted(win_states),
            "summary": f"Learned from {len(wins)} wins → boosted {applied} new matching leads"
                       f"{already_note} (verticals like {'; '.join(top_labels)})"}

@app.get("/api/usage")
def get_usage(days: int = 7, user: str = Depends(verify_token)):
    """Admin-only Places-API cost rollup. Shows who ran which queries and
    how much it cost. Reads from usage_events; returns a clear error if the
    table isn't migrated yet (so the admin knows to run the migration)."""
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")
    try:
        since = (datetime.utcnow() - timedelta(days=max(1, min(days, 90)))).isoformat()
        url = (
            f"{SUPABASE_URL}/rest/v1/usage_events"
            f"?select=created_at,username,event_type,cost_cents,metadata"
            f"&created_at=gte.{since}"
            f"&order=created_at.desc&limit=5000"
        )
        r = req_lib.get(url, headers=SB_HEADERS, timeout=15)
        if r.status_code != 200:
            return {
                "error":   "usage_events table missing — run backend/migrations/001_usage_events.sql in Supabase",
                "byUser":  [], "byDay": [], "recent": [],
                "totals":  {"cost_cents": 0, "events": 0, "days": days},
            }
        rows = r.json() if isinstance(r.json(), list) else []

        by_user, by_day = {}, {}
        total_cost = 0.0
        for row in rows:
            u  = row.get("username") or "unknown"
            et = row.get("event_type") or ""
            cost = float(row.get("cost_cents") or 0)
            total_cost += cost

            if u not in by_user:
                by_user[u] = {"username": u, "events": 0, "cost_cents": 0.0,
                              "text_searches": 0, "details": 0,
                              "autocompletes": 0, "scrapes": 0}
            by_user[u]["events"]     += 1
            by_user[u]["cost_cents"] += cost
            if   et == "google_text_search":  by_user[u]["text_searches"] += 1
            elif et == "google_details":      by_user[u]["details"]       += 1
            elif et == "google_autocomplete": by_user[u]["autocompletes"] += 1
            elif et == "scrape_call":         by_user[u]["scrapes"]       += 1

            day = (row.get("created_at") or "")[:10]
            if day:
                if day not in by_day:
                    by_day[day] = {"date": day, "cost_cents": 0.0, "events": 0}
                by_day[day]["cost_cents"] += cost
                by_day[day]["events"]     += 1

        window_days = max(1, days)
        daily_avg_cents = total_cost / window_days

        # ── Leads pulled per rep ─────────────────────────────────────────────
        # Queries the leads table directly (not usage_events) so it counts
        # actual rows created, not just scrape_call events. Today's counts
        # are the headline; the window total is in byRep for context.
        today_date = datetime.utcnow().strftime("%Y-%m-%d")
        leads_by_rep_today  = {}  # {username: int}
        leads_by_rep_window = {}  # {username: int}
        try:
            leads_url = (
                f"{SUPABASE_URL}/rest/v1/leads"
                f"?select=createdBy,createdAt"
                f"&createdAt=gte.{since}"
                f"&limit=10000"
            )
            lr = req_lib.get(leads_url, headers=SB_HEADERS, timeout=15)
            if lr.status_code == 200:
                lead_rows = lr.json() if isinstance(lr.json(), list) else []
                for lrow in lead_rows:
                    rep = lrow.get("createdBy") or "unknown"
                    leads_by_rep_window[rep] = leads_by_rep_window.get(rep, 0) + 1
                    if (lrow.get("createdAt") or "")[:10] == today_date:
                        leads_by_rep_today[rep] = leads_by_rep_today.get(rep, 0) + 1
        except Exception as e:
            print(f"[USAGE] leads aggregation failed: {e}")

        # Merge today + window counts onto one list, sorted by today desc
        # (so the admin sees who pulled most TODAY first), then window desc.
        all_reps = set(leads_by_rep_today) | set(leads_by_rep_window) | set(by_user)
        leads_by_rep = [
            {
                "username":    rep,
                "leadsToday":  leads_by_rep_today.get(rep, 0),
                "leadsWindow": leads_by_rep_window.get(rep, 0),
            }
            for rep in all_reps
        ]
        leads_by_rep.sort(key=lambda x: (x["leadsToday"], x["leadsWindow"]), reverse=True)

        return {
            "byUser":     sorted(by_user.values(), key=lambda x: x["cost_cents"], reverse=True),
            "byDay":      sorted(by_day.values(),  key=lambda x: x["date"]),
            "recent":     rows[:50],
            "leadsByRep": leads_by_rep,
            "totals":     {
                "cost_cents":  round(total_cost, 2),
                "events":      len(rows),
                "days":        days,
                "leadsToday":  sum(leads_by_rep_today.values()),
                "leadsWindow": sum(leads_by_rep_window.values()),
            },
            "projection": {
                "dailyAverage_cents":    round(daily_avg_cents, 2),
                "weeklyEstimate_cents":  round(daily_avg_cents * 7, 2),
                "monthlyEstimate_cents": round(daily_avg_cents * 30, 2),
            },
            "limits": {
                "nonAdminDailyScrapeCap": NON_ADMIN_DAILY_SCRAPE_CAP,
                "maxSpendPerRun":         PLACES_MAX_SPEND_PER_RUN,
                "cacheTtlDays":           PLACES_CACHE_TTL_DAYS,
                "killSwitch":             is_kill_switch_on()[0],
                "killSwitchSource":       is_kill_switch_on()[1],
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Admin kill-switch toggle ────────────────────────────────────────────────
# Big red button for the admin UI. Writes app_settings.places_kill_switch
# and busts the 30-second cache. Env PLACES_KILL_SWITCH=1 still wins, so if
# Eric has flipped the env var the UI will say "source: env" and show the
# toggle as uncontrollable (the endpoint refuses to write "off" if env is on).
@app.get("/api/admin/kill-switch")
def get_kill_switch(user: str = Depends(verify_token)):
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")
    on, src = is_kill_switch_on()
    return {"on": on, "source": src, "envLocked": PLACES_KILL_SWITCH}

@app.post("/api/admin/kill-switch")
def post_kill_switch(body: dict, user: str = Depends(verify_token)):
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")
    want_on = bool(body.get("on"))
    # If the env var is locking it ON, refuse to flip OFF from the DB —
    # the UI would lie about the state otherwise.
    if PLACES_KILL_SWITCH and not want_on:
        raise HTTPException(
            status_code=409,
            detail="PLACES_KILL_SWITCH=1 env var is active — unset it in Railway to re-enable scraping.",
        )
    try:
        set_kill_switch(want_on)
        audit_log(user, "kill_switch_toggle", "config", "places_kill_switch", {"on": want_on})
        on, src = is_kill_switch_on()
        return {"on": on, "source": src, "envLocked": PLACES_KILL_SWITCH}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/cities/autocomplete")
def city_autocomplete(q: str = "", state: str = "", user: str = Depends(verify_token)):
    """Return city suggestions from Google Places Autocomplete.

    Process-wide TTL cache (AUTOCOMPLETE_CACHE_TTL_SECONDS, default 1h) keyed
    on (q.lower(), state.lower()). Real typing traffic repeats prefixes
    constantly — backspacing, retyping, multiple callers typing the same
    metros — so this kills 80%+ of paid calls with no UX change."""
    if not q or len(q) < 2:
        return {"suggestions": []}
    # Kill switch respects both env var and admin-toggled DB flag.
    on, _src = is_kill_switch_on()
    if on:
        return {"suggestions": []}

    cache_key = (q.strip().lower(), (state or "").strip().lower())
    cached = autocomplete_cache_get(cache_key)
    if cached is not None:
        return {"suggestions": cached, "cached": True}

    try:
        input_text = f"{q}, {state}" if state else q
        r = req_lib.get(
            "https://maps.googleapis.com/maps/api/place/autocomplete/json",
            params={
                "input": input_text,
                "types": "(cities)",
                "components": "country:us",
                "key": GOOGLE_KEY,
            },
            timeout=5)
        # Live call — log + increment usage. Cached calls are free.
        log_usage(user, "google_autocomplete", {"q": q, "state": state})
        data = r.json()
        if data.get("status") != "OK":
            # Cache empty result too — user's next keystroke shouldn't re-hit.
            autocomplete_cache_set(cache_key, [])
            return {"suggestions": []}
        cities = []
        for pred in data.get("predictions", [])[:8]:
            terms = pred.get("terms", [])
            city_name = terms[0]["value"] if terms else pred.get("structured_formatting", {}).get("main_text", "")
            if city_name and city_name not in cities:
                cities.append(city_name)
        autocomplete_cache_set(cache_key, cities)
        return {"suggestions": cities}
    except:
        return {"suggestions": []}

@app.get("/api/industries")
def get_industries():
    return {"industries": list(INDUSTRY_MAP.keys())}

def _paginated_get(url: str, headers: dict = None, page_size: int = 1000, max_pages: int = 50) -> list:
    """Paginated PostgREST GET. Supabase silently caps single requests at
    1000 rows; without this, /api/leads and /api/stats only see the first
    1000 leads no matter how big the table actually is. Loops via Range
    header until a short page lands or we hit the safety ceiling."""
    rows = []
    base_headers = {**(headers or SB_HEADERS), "Range-Unit": "items"}
    for page in range(max_pages):
        start = page * page_size
        end   = start + page_size - 1
        try:
            r = req_lib.get(url, headers={**base_headers, "Range": f"{start}-{end}"}, timeout=30)
            batch = r.json() if r.status_code in (200, 206) else []
            if not isinstance(batch, list) or len(batch) == 0:
                break
            rows.extend(batch)
            if len(batch) < page_size:
                break
        except Exception as e:
            print(f"[PAGINATED-GET] page {page} failed: {e}")
            break
    return rows

@app.get("/api/leads/lookup")
def lookup_by_phone(phone: str = "", user: str = Depends(verify_token)):
    """Reverse phone lookup for inbound calls: paste a number, get the lead(s).
    Accuracy is exact, not fuzzy — the wildcard ilike only narrows candidates;
    we then keep only rows whose normalized digits EXACTLY equal the query's
    last-N digits, so the result is never a wrong number. Returns ALL exact
    matches (a company may have several contacts on the same main line)."""
    try:
        digits = re.sub(r"\D", "", phone or "")
        if len(digits) < 7:
            return {"query": phone, "digits": digits, "count": 0, "candidates": 0, "matchedOn": None, "matches": []}
        n = 10 if len(digits) >= 10 else 7
        t = digits[-n:]
        pat = (f"%25{t[0:3]}%25{t[3:6]}%25{t[6:10]}%25" if n == 10
               else f"%25{t[0:3]}%25{t[3:7]}%25")
        rows = _paginated_get(f"{SUPABASE_URL}/rest/v1/leads?select=*&phone=ilike.{pat}")
        matches = [r for r in rows if re.sub(r"\D", "", r.get("phone") or "")[-n:] == t]
        # Most-recently-touched first so the live record surfaces on top.
        matches.sort(key=lambda r: (r.get("last_called_at") or r.get("updatedAt") or ""), reverse=True)
        # Attach each match's most recent call (outcome + notes) so the caller
        # sees the last interaction without leaving the lookup.
        ids = [str(m["id"]) for m in matches if m.get("id") is not None]
        if ids:
            try:
                cr = req_lib.get(
                    f"{SUPABASE_URL}/rest/v1/call_outcomes"
                    f"?leadId=in.({','.join(ids)})"
                    f"&select=leadId,outcome,notes,calledBy,calledAt&order=calledAt.desc",
                    headers=SB_HEADERS, timeout=20)
                calls = cr.json() if cr.status_code == 200 else []
                latest = {}
                for c in (calls if isinstance(calls, list) else []):
                    lid = c.get("leadId")
                    if lid not in latest:   # ordered desc → first seen is newest
                        latest[lid] = c
                for m in matches:
                    m["last_call"] = latest.get(m.get("id"))
            except Exception as e:
                print(f"[LOOKUP] last-call enrich failed: {e}")
        return {"query": phone, "digits": digits, "count": len(matches),
                "candidates": len(rows), "matchedOn": f"last {n} digits", "matches": matches}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/leads")
def list_leads(status: str = "", search: str = "", sort: str = "smart",
               callbacks: str = "", source: str = "", user: str = Depends(verify_token)):
    try:
        # "smart" is the new default: fresh (never-dialed) leads first, then
        # leads not dialed in the longest time, then highest-score within ties.
        # Stops the caller from re-spinning through the same recently-dialed
        # no_answer rows over and over.
        order_map = {
            "smart":     "total_calls.asc.nullsfirst,last_called_at.asc.nullsfirst,score.desc",
            "score":     "score.desc",
            "newest":    "createdAt.desc",
            "oldest_contact": "last_called_at.asc.nullsfirst",
            "company":   "company.asc",
            "callbacks": "callbackDate.asc",
        }
        order = order_map.get(sort, order_map["smart"])

        # Build base URL (filters that aren't search)
        base_url = f"{SUPABASE_URL}/rest/v1/leads?select=*"
        if status:   base_url += f"&status=eq.{status}"
        if source:   base_url += f"&source=eq.{url_quote(source)}"
        if callbacks == "true":
            today = datetime.utcnow().strftime("%Y-%m-%d")
            base_url += f"&callbackDate=lte.{today}&callbackDate=neq.&status=neq.converted"

        if not search:
            return _paginated_get(f"{base_url}&order={order}")

        # ── Reverse phone lookup ────────────────────────────────────────
        # If the query is phone-like (only digits + phone punctuation), match
        # by digit groups joined with wildcards so it resolves no matter how
        # the number is stored — "(702) 485-3232", "702-485-3232", "7024853232",
        # "+1 702 485 3232" all find the same lead. This is the inbound-call
        # "who is this number?" case, and it also fixes pasting the full
        # "(702) 485-3232" format (the parens used to break the OR filter → 0 hits).
        _digits = re.sub(r"\D", "", search)
        if len(_digits) >= 7 and re.fullmatch(r"[\d\s().+\-.]+", search.strip()):
            n = 10 if len(_digits) >= 10 else 7
            t = _digits[-n:]
            pat = (f"%25{t[0:3]}%25{t[3:6]}%25{t[6:10]}%25" if n == 10   # %AAA%BBB%CCCC%
                   else f"%25{t[0:3]}%25{t[3:7]}%25")                    # %BBB%CCCC%
            rows = _paginated_get(f"{base_url}&phone=ilike.{pat}&order={order}")
            # Accuracy: the wildcard is only a pre-filter — keep rows whose
            # normalized digits EXACTLY equal the query's last-N, so a near-miss
            # can't slip through.
            return [r for r in rows if re.sub(r"\D", "", r.get("phone") or "")[-n:] == t]

        # ── Two-phase search ───────────────────────────────────────────
        # Old search only matched company/firstName/lastName/phone — missed
        # notes, email, assignedTo, and call_outcomes entirely. So "Cristine"
        # (a former caller's name living in call_outcomes.calledBy) and
        # "contract" (a phrase typed into a per-call note) returned zero hits
        # even though the lead existed. Phase 1 widens the leads-table OR;
        # phase 2 looks at call_outcomes and pulls in any leadIds whose
        # calls match — handles "find the lead my ex-rep worked" and
        # "find the lead with that note in call history".
        s = search.replace(" ", "%20")
        leads_or = (
            f"company.ilike.%25{s}%25,"
            f"firstName.ilike.%25{s}%25,"
            f"lastName.ilike.%25{s}%25,"
            f"phone.ilike.%25{s}%25,"
            f"email.ilike.%25{s}%25,"
            f"notes.ilike.%25{s}%25,"
            f"city.ilike.%25{s}%25,"
            f"address.ilike.%25{s}%25,"
            f"assignedTo.ilike.%25{s}%25,"
            f"createdBy.ilike.%25{s}%25"
        )
        leads = _paginated_get(f"{base_url}&or=({leads_or})&order={order}")

        # Phase 2: search call_outcomes for matching calledBy or notes,
        # collect those leadIds, and merge in any leads we didn't catch.
        try:
            call_url = (
                f"{SUPABASE_URL}/rest/v1/call_outcomes?select=leadId"
                f"&or=(calledBy.ilike.%25{s}%25,notes.ilike.%25{s}%25)"
            )
            calls = _paginated_get(call_url)
            extra_ids = list({c.get("leadId") for c in calls if c.get("leadId")})
            existing = {l.get("id") for l in leads}
            missing  = [lid for lid in extra_ids if lid not in existing]
            if missing:
                # Re-apply non-search filters on the lookup so a user filtering
                # status=interested doesn't get callbacks pulled in via Phase 2.
                BATCH = 100
                for i in range(0, len(missing), BATCH):
                    chunk = missing[i:i+BATCH]
                    ids_filter = ",".join(str(x) for x in chunk)
                    extra_url = f"{base_url}&id=in.({ids_filter})&order={order}"
                    leads.extend(_paginated_get(extra_url))
        except Exception as e:
            print(f"[SEARCH] call_outcomes phase failed: {e}")

        # Sort the merged result so phase-2 inserts land in the right spot.
        # Match the multi-column smart sort (fresh first → oldest contact → score).
        def _smart_key(l):
            return (
                l.get("total_calls") or 0,
                l.get("last_called_at") or "",  # empty string sorts first → fresh top
                -(l.get("score") or 0),         # negate so desc-by-score via asc sort
            )
        first_col = order.split(",")[0]
        sort_key, _, dir_ = first_col.partition(".")
        if sort == "smart" or order.startswith("total_calls"):
            leads.sort(key=_smart_key)
        elif sort_key in ("score", "createdAt", "company", "callbackDate", "last_called_at"):
            leads.sort(
                key=lambda x: (x.get(sort_key) or 0) if sort_key == "score" else (x.get(sort_key) or ""),
                reverse=(dir_ == "desc"),
            )
        return leads
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/dialer/queue")
def dialer_queue(limit: int = 50, snooze_hours: int = 4,
                 user: str = Depends(verify_token)):
    """Server-side dialer queue. Returns at most `limit` leads, already
    smart-sorted and filtered against:
      - assigned to me OR unassigned
      - status not in NO_DIAL set (awaiting_email_reply, do_not_contact)
      - valid US/NANP phone (caller never sees a guaranteed-fail dial)
      - not snoozed (no_answer/voicemail within last `snooze_hours`)

    Replaces the old pattern of fetching every lead and filtering client-side
    (a 1MB+ payload on every dialer-tab open). 50 leads is more than one
    caller can churn through in a shift, with room to skip."""
    limit = max(1, min(int(limit or 50), 200))
    snooze_hours = max(0, min(int(snooze_hours or 0), 168))

    # Filters at the PostgREST level; one paginated GET covers worst case.
    # NOTE: leads.assignedTo stores unassigned as empty string "", not NULL —
    # legacy data convention. So the "mine or unassigned" filter needs the
    # eq.<empty> branch as well as is.null. Tested both branches in prod.
    base = (f"{SUPABASE_URL}/rest/v1/leads?select=*"
            f"&or=(assignedTo.eq.{url_quote(user)},assignedTo.is.null,assignedTo.eq.)"
            f"&status=not.in.(awaiting_email_reply,do_not_contact)"
            f"&phone=neq."  # empty-string check matches the legacy convention
            f"&order=total_calls.asc.nullsfirst,last_called_at.asc.nullsfirst,score.desc")
    rows = _paginated_get(base, page_size=1000, max_pages=5)
    if not isinstance(rows, list):
        rows = []

    # NANP guard (PostgREST regex would need a function; keep in Python).
    NO_DIAL = {"awaiting_email_reply", "do_not_contact"}
    SNOOZED_OUTCOMES = {"no_answer", "voicemail", "called", "not_interested"}
    cutoff_iso = None
    if snooze_hours > 0:
        cutoff_iso = (datetime.utcnow() - timedelta(hours=snooze_hours)).isoformat()

    out = []
    for l in rows:
        if (l.get("status") or "") in NO_DIAL: continue
        if not is_valid_us_phone(l.get("phone")): continue
        if cutoff_iso:
            lca = l.get("last_called_at") or ""
            if lca and lca >= cutoff_iso and (l.get("status") or "") in SNOOZED_OUTCOMES:
                continue
        out.append(l)
        if len(out) >= limit:
            break
    return {"queue": out, "fetched": len(rows), "returned": len(out),
            "user": user, "snooze_hours": snooze_hours}

@app.post("/api/leads")
def create_lead(lead: dict, user: str = Depends(verify_token)):
    try:
        np = normalize_us_phone(lead.get("phone"))
        if np:
            lead["phone"] = np
        lead["score"] = score_lead(lead)
        lead["createdBy"] = user
        r = req_lib.post(f"{SUPABASE_URL}/rest/v1/leads", headers=SB_HEADERS, json=lead, timeout=30)
        result = r.json()
        lead_id = result[0].get("id") if isinstance(result, list) and result else None
        audit_log(user, "create_lead", "lead", lead_id, {"company": lead.get("company"), "source": "manual"})
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/leads/import")
async def import_leads(request: Request, user: str = Depends(verify_token)):
    """CSV / prospect-sheet import. Canonicalizes phones and dedups against
    existing DB phones BEFORE insert — same cross-run guard `save_to_supabase`
    uses for scrape output. Stops a re-imported sheet from doubling up leads.

    Reads the JSON body directly so a bare array (`[...]`, what the frontend
    sends) and a wrapped object (`{"leads": [...]}`) both work. A plain
    `leads: list` signature made FastAPI expect multipart/form-data, which
    silently broke every JSON import."""
    try:
        body = await request.json()
        leads = body.get("leads") if isinstance(body, dict) else body
        if not isinstance(leads, list):
            raise HTTPException(status_code=400, detail="expected a JSON array of leads")
        for lead in leads:
            np = normalize_us_phone(lead.get("phone"))
            if np:
                lead["phone"] = np
            lead["score"] = score_lead(lead)
            lead["createdBy"] = user

        # Cross-run dedup against existing DB phones.
        incoming_phones = list({(l.get("phone") or "").strip() for l in leads if (l.get("phone") or "").strip()})
        existing = set()
        if incoming_phones:
            CHUNK = 100
            for i in range(0, len(incoming_phones), CHUNK):
                chunk = incoming_phones[i:i+CHUNK]
                in_list = ",".join(f'"{p}"' for p in chunk)
                try:
                    rq = req_lib.get(
                        f"{SUPABASE_URL}/rest/v1/leads?select=phone&phone=in.({in_list})",
                        headers=SB_HEADERS, timeout=20,
                    )
                    if rq.status_code == 200:
                        for row in rq.json():
                            ph = (row.get("phone") or "").strip()
                            if ph: existing.add(ph)
                except Exception as e:
                    print(f"[IMPORT] dedup pre-check failed: {e}")
        skipped = 0
        if existing:
            before = len(leads)
            leads = [l for l in leads if (l.get("phone") or "").strip() not in existing]
            skipped = before - len(leads)
        if not leads:
            return {"count": 0, "skipped": skipped, "reason": "all rows already in DB"}
        r = req_lib.post(f"{SUPABASE_URL}/rest/v1/leads", headers=SB_HEADERS, json=leads, timeout=30)
        saved = r.json()
        count = len(saved) if isinstance(saved, list) else 0
        audit_log(user, "import_leads", "lead", None, {"count": count, "skipped": skipped, "source": "csv"})
        return {"count": count, "skipped": skipped}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/leads/{lead_id}")
def update_lead(lead_id: str, data: dict, user: str = Depends(verify_token)):
    try:
        if "phone" in data:
            np = normalize_us_phone(data.get("phone"))
            if np:
                data["phone"] = np
        r = req_lib.patch(f"{SUPABASE_URL}/rest/v1/leads?id=eq.{lead_id}",
                         headers=SB_HEADERS, json=data, timeout=30)
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/leads/{lead_id}")
def delete_lead(lead_id: str, user: str = Depends(verify_token)):
    try:
        # Fetch lead details before deleting for audit trail
        lr = req_lib.get(f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(lead_id)}&select=company,assignedTo,status",
                        headers=SB_HEADERS, timeout=10)
        lead_info = lr.json() if lr.status_code == 200 else []
        lead_detail = lead_info[0] if isinstance(lead_info, list) and lead_info else {}

        req_lib.delete(f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(lead_id)}",
                      headers={**SB_HEADERS, "Prefer":""}, timeout=30)
        audit_log(user, "delete_lead", "lead", lead_id, {
            "company": lead_detail.get("company"), "assignedTo": lead_detail.get("assignedTo"),
            "status": lead_detail.get("status")})
        return {"deleted": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/calls")
def log_call(call: dict, user: str = Depends(verify_token)):
    try:
        lead_id = call.get("leadId")
        caller  = call.get("calledBy") or user
        outcome = (call.get("outcome") or "").strip().lower()
        # Pop the email-followup flag — Supabase's call_outcomes table doesn't
        # have a column for it; we use the local var only for the trigger logic.
        send_email_followup = bool(call.pop("send_email_followup", False))
        flags = []

        # Anti-gaming: empty form — no notes and no qual data filled out
        has_notes = bool((call.get("notes") or "").strip())
        has_qual = any(call.get(f) for f in ["budgetfocus", "vendorstatus", "decisionmaker", "timeline", "qualified"])
        if not has_notes and not has_qual:
            flags.append("empty_form")

        # Anti-gaming: duplicate cooldown — same lead within 5 minutes
        if lead_id:
            five_min_ago = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
            dup_r = req_lib.get(
                f"{SUPABASE_URL}/rest/v1/call_outcomes?leadId=eq.{lead_id}&calledBy=eq.{caller}"
                f"&calledAt=gte.{five_min_ago}&select=id",
                headers=SB_HEADERS, timeout=10)
            dups = dup_r.json() if dup_r.status_code == 200 else []
            if isinstance(dups, list) and len(dups) > 0:
                flags.append("duplicate_cooldown")

        # Anti-gaming: cadence — more than 5 calls in last 5 minutes
        five_min_ago = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
        cad_r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/call_outcomes?calledBy=eq.{caller}"
            f"&calledAt=gte.{five_min_ago}&select=id",
            headers={**SB_HEADERS, "Prefer": ""}, timeout=10)
        recent = cad_r.json() if cad_r.status_code == 200 else []
        if isinstance(recent, list) and len(recent) >= 5:
            flags.append("rapid_cadence")

        # Store flags on the call record
        if flags:
            call["follow_up_outcome"] = ",".join(flags)  # repurpose unused field for flags

        r = req_lib.post(f"{SUPABASE_URL}/rest/v1/call_outcomes",
                        headers=SB_HEADERS, json=call, timeout=30)
        if lead_id:
            lr = req_lib.get(
                f"{SUPABASE_URL}/rest/v1/leads?id=eq.{lead_id}&select=*",
                headers=SB_HEADERS, timeout=30)
            rows = lr.json() if lr.status_code == 200 else []
            lead_full = rows[0] if rows else {}

            # total_calls comes from a COUNT on call_outcomes — the ground
            # truth. Avoids three previous-design failures:
            #   1. Read-modify-write race: two concurrent log_call hits would
            #      both read N and both write N+1, losing one increment.
            #   2. GET-on-leads failure silently overwriting total_calls with
            #      1 (`(lead_full.get('total_calls') or 0) + 1` collapses to 1
            #      when lead_full = {}, wiping a 50-call history).
            #   3. Drift from the dedup-reparent path — when call_outcomes get
            #      moved from a duplicate row to the kept row, the count is
            #      automatically correct on the next log_call.
            new_count = None
            try:
                cr = req_lib.get(
                    f"{SUPABASE_URL}/rest/v1/call_outcomes?leadId=eq.{lead_id}&select=id",
                    headers={**SB_HEADERS, "Prefer": "count=exact"}, timeout=10)
                cr_range = cr.headers.get("content-range", "*/0")
                if "/" in cr_range:
                    new_count = int(cr_range.split("/")[-1])
            except Exception as e:
                print(f"[LOG-CALL] count query failed for lead {lead_id}: {e}")

            # Frontend captures call start in call.calledAt — prefer it over
            # server PATCH time so a back-dated log (call earlier in shift,
            # logged at EOD) records the right moment instead of "now."
            now_iso = datetime.utcnow().isoformat()
            called_at = (call.get("calledAt") or "").strip() or now_iso
            patch_payload = {
                "updatedAt":      now_iso,
                "last_called_at": called_at,
            }
            # Only update total_calls when we have a trustworthy count.
            # Skipping (vs. writing a wrong value) is the safe failure mode —
            # the next successful log_call self-heals the counter.
            if new_count is not None:
                patch_payload["total_calls"] = new_count
            if lead_full and not lead_full.get("assignedTo"):
                patch_payload["assignedTo"] = caller
            req_lib.patch(
                f"{SUPABASE_URL}/rest/v1/leads?id=eq.{lead_id}",
                headers=SB_HEADERS,
                json=patch_payload,
                timeout=30)

            # Email follow-up trigger. Two paths:
            #   (a) Caller checked "Send follow-up email" in the modal → fire now,
            #       regardless of attempt count. Per-call override.
            #   (b) AUTO_CAMPAIGN_AFTER_FAILED_CALL=1 + outcome is failed +
            #       attempt count >= threshold → background auto-fire.
            # Both paths share send_lead_to_campaign which handles the
            # suppression window (CAMPAIGN_SUPPRESSION_DAYS) so a manual +
            # auto can't double-send.
            should_email = False
            email_trigger = ""
            if (RESEND_API_KEY and outcome in ("no_answer", "voicemail")
                    and lead_full and lead_full.get("email") and lead_full.get("firstName")):
                if send_email_followup:
                    should_email = True
                    email_trigger = f"{outcome}_caller_chose"
                elif AUTO_CAMPAIGN_AFTER_FAILED_CALL:
                    try:
                        fr = req_lib.get(
                            f"{SUPABASE_URL}/rest/v1/call_outcomes"
                            f"?leadId=eq.{lead_id}&outcome=in.(no_answer,voicemail)&select=id"
                            f"&limit={AUTO_CAMPAIGN_FAILED_CALL_THRESHOLD + 5}",
                            headers={**SB_HEADERS, "Prefer": "count=exact"}, timeout=10)
                        cr = fr.headers.get("content-range", "*/0")
                        failed_count = int(cr.split("/")[-1]) if "/" in cr else 0
                        if failed_count >= AUTO_CAMPAIGN_FAILED_CALL_THRESHOLD:
                            should_email = True
                            email_trigger = f"{outcome}_attempt_{failed_count}_auto"
                    except Exception as e:
                        print(f"[AUTO-CAMPAIGN] threshold check failed for lead {lead_id}: {e}")
            if should_email:
                try:
                    send_lead_to_campaign(lead_full, trigger=email_trigger, user=caller)
                except Exception as e:
                    print(f"[AUTO-CAMPAIGN] send failed for lead {lead_id}: {e}")

        return r.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/calls/today")
def get_calls_today(user: str = Depends(verify_token)):
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        r = req_lib.get(f"{SUPABASE_URL}/rest/v1/call_outcomes?select=outcome,calledBy&calledAt=gte.{today}T00:00:00",
                       headers=SB_HEADERS, timeout=30)
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Quota endpoints ────────────────────────────────────────────────────────────

# Default quota — used if no Supabase row exists yet
DEFAULT_QUOTA = int(os.getenv("DAILY_CALL_QUOTA", "60"))

@app.get("/api/quota")
def get_quota(user: str = Depends(verify_token)):
    try:
        # Check for per-user quota first, then fall back to team default
        r_user = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/app_settings?key=eq.quota_{user.lower()}&select=value",
            headers=SB_HEADERS, timeout=10)
        user_rows = r_user.json() if r_user.status_code == 200 else []

        if isinstance(user_rows, list) and user_rows:
            quota = int(user_rows[0]["value"])
        else:
            r_default = req_lib.get(
                f"{SUPABASE_URL}/rest/v1/app_settings?key=eq.daily_quota&select=value",
                headers=SB_HEADERS, timeout=10)
            default_rows = r_default.json() if r_default.status_code == 200 else []
            quota = int(default_rows[0]["value"]) if isinstance(default_rows, list) and default_rows else DEFAULT_QUOTA

        # Get this user's calls today
        today = datetime.utcnow().strftime("%Y-%m-%d")
        r2 = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/call_outcomes?select=id&calledBy=eq.{user}"
            f"&calledAt=gte.{today}T00:00:00",
            headers={**SB_HEADERS, "Prefer": ""}, timeout=10)
        my_calls = r2.json() if r2.status_code == 200 else []
        my_count = len(my_calls) if isinstance(my_calls, list) else 0

        return {"quota": quota, "my_calls_today": my_count}
    except:
        return {"quota": DEFAULT_QUOTA, "my_calls_today": 0}

@app.put("/api/quota")
def set_quota(body: dict, user: str = Depends(verify_admin)):
    """Set quota — per-user if 'caller' specified, team default otherwise"""
    try:
        new_quota = int(body.get("quota", DEFAULT_QUOTA))
        caller = body.get("caller", "").strip()
        if new_quota < 1 or new_quota > 500:
            raise HTTPException(status_code=400, detail="Quota must be 1-500")
        key = f"quota_{caller.lower()}" if caller else "daily_quota"
        req_lib.post(
            f"{SUPABASE_URL}/rest/v1/app_settings",
            headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"},
            json={"key": key, "value": str(new_quota)},
            timeout=10)
        return {"quota": new_quota, "caller": caller or "all"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/quota/all")
def get_all_quotas(user: str = Depends(verify_admin)):
    """Admin-only: get all quota settings"""
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/app_settings?key=like.quota_*&select=key,value",
            headers=SB_HEADERS, timeout=10)
        per_user = r.json() if r.status_code == 200 else []
        r2 = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/app_settings?key=eq.daily_quota&select=value",
            headers=SB_HEADERS, timeout=10)
        default_rows = r2.json() if r2.status_code == 200 else []
        team_default = int(default_rows[0]["value"]) if isinstance(default_rows, list) and default_rows else DEFAULT_QUOTA
        quotas = {}
        if isinstance(per_user, list):
            for row in per_user:
                name = row["key"].replace("quota_", "")
                quotas[name] = int(row["value"])
        return {"team_default": team_default, "per_user": quotas}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/calls/qualified")
def get_qualified_calls(user: str = Depends(verify_token)):
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/call_outcomes"
            f"?select=*"
            f"&order=calledAt.desc&limit=500",
            headers=SB_HEADERS, timeout=30)
        all_calls = r.json() if r.status_code == 200 else []
        if not isinstance(all_calls, list):
            return []
        qual_fields = ["budgetfocus", "vendorstatus", "decisionmaker", "timeline", "qualified"]
        qualified = [c for c in all_calls if any(c.get(f) for f in qual_fields)]
        # Batch-fetch all lead data in one request instead of N+1
        lead_ids = list(set(c.get("leadId") for c in qualified if c.get("leadId")))
        leads_map = {}
        if lead_ids:
            ids_filter = ",".join(str(lid) for lid in lead_ids)
            lr = req_lib.get(
                f"{SUPABASE_URL}/rest/v1/leads?id=in.({ids_filter})"
                f"&select=id,company,firstName,lastName,phone,industry,state,score,status,assignedTo",
                headers=SB_HEADERS, timeout=30)
            leads_data = lr.json() if lr.status_code == 200 else []
            if isinstance(leads_data, list):
                leads_map = {l["id"]: l for l in leads_data}
        for c in qualified:
            c["leads"] = leads_map.get(c.get("leadId"))
        return qualified
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/calls/{call_id}/review")
def review_call(call_id: str, body: dict, user: str = Depends(verify_admin)):
    review = body.get("review")
    if review not in ("approved", "rejected", None, ""):
        raise HTTPException(status_code=400, detail="review must be approved, rejected, or null")
    try:
        payload = {
            "admin_review": review or None,
            "admin_reviewed_by": user if review else None,
            "admin_reviewed_at": datetime.utcnow().isoformat() if review else None,
        }
        r = req_lib.patch(
            f"{SUPABASE_URL}/rest/v1/call_outcomes?id=eq.{call_id}",
            headers=SB_HEADERS, json=payload, timeout=30)
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return {"ok": True, "review": review or None}
    except HTTPException: raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/calls/{call_id}")
def delete_call(call_id: str, user: str = Depends(verify_admin)):
    try:
        r = req_lib.delete(
            f"{SUPABASE_URL}/rest/v1/call_outcomes?id=eq.{call_id}",
            headers=SB_HEADERS, timeout=30)
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return {"deleted": True}
    except HTTPException: raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/calls/{call_id}/followup")
def followup_call(call_id: str, body: dict, user: str = Depends(verify_admin)):
    date = (body.get("date") or "").strip()
    if not date:
        raise HTTPException(status_code=400, detail="date required (YYYY-MM-DD)")
    try:
        cr = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/call_outcomes?id=eq.{call_id}&select=leadId",
            headers=SB_HEADERS, timeout=30)
        rows = cr.json() if cr.status_code == 200 else []
        if not rows or not rows[0].get("leadId"):
            raise HTTPException(status_code=404, detail="call or lead not found")
        lead_id = rows[0]["leadId"]
        lr = req_lib.patch(
            f"{SUPABASE_URL}/rest/v1/leads?id=eq.{lead_id}",
            headers=SB_HEADERS,
            json={"status": "callback", "callbackDate": date,
                  "nextfollowup": date, "updatedAt": datetime.utcnow().isoformat()},
            timeout=30)
        if lr.status_code >= 400:
            raise HTTPException(status_code=lr.status_code, detail=lr.text)
        return {"ok": True, "leadId": lead_id, "callbackDate": date}
    except HTTPException: raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/calls/history")
def get_call_history(date_from: str = "", date_to: str = "", caller: str = "",
                     user: str = Depends(verify_token)):
    try:
        # Paginate via _paginated_get — Supabase caps single-request returns
        # at 1000 rows, so the analytics tab's "Calls Made" was stuck at 1000
        # the moment lifetime calls crossed that threshold.
        url = f"{SUPABASE_URL}/rest/v1/call_outcomes?select=*&order=calledAt.desc"
        if date_from:
            url += f"&calledAt=gte.{date_from}T00:00:00"
        if date_to:
            url += f"&calledAt=lte.{date_to}T23:59:59"
        if caller:
            url += f"&calledBy=eq.{caller}"
        calls = _paginated_get(url)
        if not isinstance(calls, list):
            return {"calls": [], "summary": {}, "callers": []}

        # Track which leads have been called before for first-call detection
        lead_first_call = {}  # leadId -> earliest calledAt

        # Build summary stats. "not_interested" counts as contacted: it's only
        # reachable after "Answered" in the two-step flow, so the rep DID reach a
        # person — excluding it undercounted contact rate.
        contacted = ["answered", "interested", "interested_no_dm", "converted", "callback", "not_interested"]
        summary = {"total": len(calls), "converted": 0, "interested": 0,
                   "no_answer": 0, "callback": 0, "voicemail": 0, "answered": 0,
                   "total_talk_time": 0, "first_calls": 0, "follow_ups": 0}
        by_caller = {}
        by_date = {}

        # First pass: find earliest call per lead for first-call detection
        for c in calls:
            lid = c.get("leadId")
            cat = c.get("calledAt", "")
            if lid:
                if lid not in lead_first_call or cat < lead_first_call[lid]:
                    lead_first_call[lid] = cat

        for c in calls:
            o = c.get("outcome", "")
            dur = c.get("duration") or 0
            if o in summary: summary[o] += 1
            summary["total_talk_time"] += dur

            # First call vs follow-up
            lid = c.get("leadId")
            cat = c.get("calledAt", "")
            is_first = lid and lead_first_call.get(lid) == cat
            if is_first: summary["first_calls"] += 1
            else: summary["follow_ups"] += 1

            name = c.get("calledBy", "Unknown")
            if name not in by_caller:
                by_caller[name] = {"name": name, "total": 0, "converted": 0,
                                   "interested": 0, "no_answer": 0, "callback": 0,
                                   "voicemail": 0, "talk_time": 0,
                                   "first_calls": 0, "follow_ups": 0, "contacted": 0}
            u = by_caller[name]
            u["total"] += 1
            u["talk_time"] += dur
            if o in u: u[o] += 1
            if o in contacted: u["contacted"] += 1
            if is_first: u["first_calls"] += 1
            else: u["follow_ups"] += 1

            day = (cat)[:10]
            if day:
                if day not in by_date:
                    by_date[day] = {"date": day, "total": 0, "converted": 0, "interested": 0}
                by_date[day]["total"] += 1
                if o in ("converted", "interested"):
                    by_date[day][o] += 1

        # Contact rate = % of calls that reached a person
        total_contacted = sum(1 for c in calls if c.get("outcome") in contacted)
        summary["contact_rate"] = f"{(total_contacted/len(calls)*100):.1f}" if calls else "0.0"
        summary["avg_talk_time"] = round(summary["total_talk_time"] / len(calls)) if calls else 0

        # Per-caller rates
        caller_list = sorted(by_caller.values(), key=lambda x: -x["total"])
        for cl in caller_list:
            tc = cl["total"]
            cl["conv_rate"] = f"{(cl['converted']/tc*100):.1f}" if tc else "0.0"
            cl["contact_rate"] = f"{(cl['contacted']/tc*100):.1f}" if tc else "0.0"
            cl["avg_talk_time"] = round(cl["talk_time"] / tc) if tc else 0

        date_list = sorted(by_date.values(), key=lambda x: x["date"], reverse=True)

        # Unique callers for dropdown
        all_callers = sorted(set(c.get("calledBy", "") for c in calls if c.get("calledBy")))

        return {
            "calls": calls,
            "summary": summary,
            "by_caller": caller_list,
            "by_date": date_list,
            "callers": all_callers,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def _is_us_state_value(s) -> bool:
    """Accept either a 2-letter code or a full state name. Empty → False
    (treats no-state as suspicious for cleanup)."""
    s = (s or "").strip()
    if not s:
        return False
    if s.upper() in US_STATE_ABBREVS:
        return True
    if s.lower() in US_STATE_NAMES:
        return True
    return False

@app.get("/api/admin/leads/cleanup-preview")
def cleanup_preview(user: str = Depends(verify_admin)):
    """Categorize likely-irrelevant leads. NON-DESTRUCTIVE — returns IDs
    + samples grouped by reason. Caller picks which categories to act on,
    then sends those IDs back to /cleanup-execute."""
    # Supabase caps result size at 1000/page regardless of limit param. Paginate
    # via Range header so we see the full DB, not just the first thousand.
    leads = []
    PAGE = 1000
    MAX_PAGES = 50  # 50,000-lead ceiling — fail-safe so a runaway can't OOM
    try:
        for page in range(MAX_PAGES):
            start = page * PAGE
            end   = start + PAGE - 1
            r = req_lib.get(
                f"{SUPABASE_URL}/rest/v1/leads"
                f"?select=id,company,firstName,lastName,phone,email,state,city,source,createdAt,assignedTo,status,total_calls"
                f"&order=id.asc",
                headers={**SB_HEADERS, "Range-Unit": "items", "Range": f"{start}-{end}"},
                timeout=30,
            )
            batch = r.json() if r.status_code in (200, 206) else []
            if not isinstance(batch, list) or len(batch) == 0:
                break
            leads.extend(batch)
            if len(batch) < PAGE:
                break  # last page
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch leads: {e}")

    # ── Foreign / no-state leads ──────────────────────────────────────────
    foreign_ids = []
    foreign_samples = []
    for l in leads:
        if not _is_us_state_value(l.get("state")):
            foreign_ids.append(l["id"])
            if len(foreign_samples) < 12:
                foreign_samples.append({
                    "id": l["id"], "company": l.get("company"),
                    "state": l.get("state"), "city": l.get("city"),
                    "source": l.get("source"),
                })

    # ── Duplicate by phone ──────────────────────────────────────────────
    # Group by exact phone match. The dialer queue surfaces every row
    # individually, so duplicate-phone rows make the caller dial the same
    # business N times and feel like "the leads are repeating."
    #
    # Keep rule: prefer the row with an engaged status (interested / converted /
    # callback / awaiting_email_reply / do_not_contact), then highest total_calls,
    # then newest createdAt. Extras are safe to delete UNLESS they themselves
    # carry an engaged status (don't drop a separate "interested" row just
    # because the keeper is "converted" — both records are meaningful).
    #
    # cleanup-execute will re-parent call_outcomes from delete→keep before
    # deleting, so dial history doesn't get orphaned.
    # ENGAGED_STATUSES defined at module level — same set as the auto sweep.
    phone_groups = {}
    for l in leads:
        ph = (l.get("phone") or "").strip()
        if ph:
            phone_groups.setdefault(ph, []).append(l)
    dup_phone_ids = []
    dup_phone_samples = []
    dup_phone_reparent = {}  # extra_id -> keep_id, used by cleanup-execute
    for ph, group in phone_groups.items():
        if len(group) < 2:
            continue
        def _keep_priority(l):
            engaged = (l.get("status") or "") in ENGAGED_STATUSES
            return (engaged, l.get("total_calls") or 0, l.get("createdAt") or "")
        group_sorted = sorted(group, key=_keep_priority, reverse=True)
        keep = group_sorted[0]
        extras = group_sorted[1:]
        worth_deleting = [e for e in extras if (e.get("status") or "") not in ENGAGED_STATUSES]
        if not worth_deleting:
            continue
        for e in worth_deleting:
            dup_phone_ids.append(e["id"])
            dup_phone_reparent[str(e["id"])] = keep["id"]
        if len(dup_phone_samples) < 8:
            dup_phone_samples.append({
                "phone": ph,
                "kept":  {"id": keep["id"], "company": keep.get("company"),
                          "createdAt": (keep.get("createdAt") or "")[:10],
                          "status": keep.get("status"),
                          "total_calls": keep.get("total_calls") or 0,
                          "source": keep.get("source")},
                "delete":[{"id": e["id"], "company": e.get("company"),
                           "createdAt": (e.get("createdAt") or "")[:10],
                           "status": e.get("status"),
                           "total_calls": e.get("total_calls") or 0,
                           "source": e.get("source")} for e in worth_deleting],
            })

    # ── Non-NANP phone numbers (caller can't dial these) ─────────────────
    # Connectivity-rate complaints traced to Google-Places rows whose phones
    # don't match NANP (wrong digit count, 0/1 area code, all-same-digit, 555
    # exchange). Only flag rows with no email backup so deletion can't strand
    # an active email outreach. Skip anything assigned/worked.
    bad_phone_ids = []
    bad_phone_samples = []
    for l in leads:
        ph = (l.get("phone") or "").strip()
        if not ph or is_valid_us_phone(ph):
            continue
        em = (l.get("email") or "").strip()
        has_email = "@" in em and "." in em.split("@")[-1]
        if has_email:
            continue
        if l.get("assignedTo") or (l.get("total_calls") or 0) > 0:
            continue
        if l.get("status") and l.get("status") != "new":
            continue
        bad_phone_ids.append(l["id"])
        if len(bad_phone_samples) < 12:
            bad_phone_samples.append({
                "id": l["id"], "company": l.get("company"),
                "phone": ph, "source": l.get("source"),
            })

    # ── No phone AND no email = totally unreachable ──────────────────────
    no_contact_ids = []
    no_contact_samples = []
    for l in leads:
        ph = (l.get("phone") or "").strip()
        em = (l.get("email") or "").strip()
        has_phone = bool(ph)
        has_email = "@" in em and "." in em.split("@")[-1]
        if has_phone or has_email:
            continue
        if l.get("assignedTo") or (l.get("total_calls") or 0) > 0:
            continue
        if l.get("status") and l.get("status") != "new":
            continue
        no_contact_ids.append(l["id"])
        if len(no_contact_samples) < 12:
            no_contact_samples.append({
                "id": l["id"], "company": l.get("company"),
                "city": l.get("city"), "source": l.get("source"),
            })

    # ── Duplicate Apollo contacts ─────────────────────────────────────────
    # Same person at same company appearing in multiple rows — typically
    # caused by repeat Apollo bulk pulls before the pre-enrichment dedupe
    # was in place. Shared detection logic with the weekly auto-sweep.
    dupe_groups = find_apollo_dupe_groups(leads)
    dup_apollo_ids = [e["id"] for g in dupe_groups for e in g["safe_extras"]]
    dup_apollo_samples = []
    for g in dupe_groups:
        if not g["safe_extras"]:
            continue
        if len(dup_apollo_samples) >= 8:
            break
        keep = g["kept"]
        dup_apollo_samples.append({
            "person": g["person"],
            "kept":   {"id": keep["id"],
                       "createdAt": (keep.get("createdAt") or "")[:10],
                       "total_calls": keep.get("total_calls") or 0,
                       "source": keep.get("source")},
            "delete": [{"id": e["id"],
                        "createdAt": (e.get("createdAt") or "")[:10],
                        "source": e.get("source")} for e in g["safe_extras"]],
        })

    return {
        "total_leads":   len(leads),
        "categories": {
            "foreign": {
                "label":   "Foreign / non-US (Apollo's loose location filter before fix)",
                "count":   len(foreign_ids),
                "ids":     foreign_ids,
                "samples": foreign_samples,
            },
            "duplicate_phones": {
                "label":   "Duplicate phone numbers (best copy kept; call history re-parented before delete)",
                "count":   len(dup_phone_ids),
                "ids":     dup_phone_ids,
                "samples": dup_phone_samples,
                # Map of {extra_id -> keep_id} so cleanup-execute can re-parent
                # call_outcomes before deleting the extra rows.
                "reparent_map": dup_phone_reparent,
            },
            "duplicate_apollo_contacts": {
                "label":   "Same person (firstName+lastName) at same company across multiple rows — keeps most-worked copy, deletes unworked extras",
                "count":   len(dup_apollo_ids),
                "ids":     dup_apollo_ids,
                "samples": dup_apollo_samples,
            },
            "bad_phone": {
                "label":   "Bad / non-dialable phone (fails NANP) and no email backup — pure connectivity-rate drag",
                "count":   len(bad_phone_ids),
                "ids":     bad_phone_ids,
                "samples": bad_phone_samples,
            },
            "no_contact": {
                "label":   "No phone AND no email — totally unreachable (unworked rows only)",
                "count":   len(no_contact_ids),
                "ids":     no_contact_ids,
                "samples": no_contact_samples,
            },
        },
    }

@app.post("/api/admin/leads/cleanup-execute")
def cleanup_execute(body: dict, user: str = Depends(verify_admin)):
    """Delete leads by ID list. Body: {ids: [...], reason: 'foreign'|'duplicate_phones'|...,
    reparent_map?: {extra_id: keep_id}}.

    For reason='duplicate_phones', the optional reparent_map tells us where to
    move each extra row's call_outcomes BEFORE we delete the extra row — so
    dial history follows the surviving lead instead of orphaning. Batched by
    50 because Supabase URL length limits."""
    ids = body.get("ids") or []
    reason = (body.get("reason") or "manual").strip()
    reparent_map = body.get("reparent_map") or {}
    if not ids:
        return {"deleted": 0, "requested": 0}

    # Re-parent call_outcomes from extras to the kept row before deleting.
    # Group by keep_id so we issue one PATCH per kept row instead of one per
    # extra (cuts request count when a lead has 10+ duplicates).
    reparented = 0
    if reparent_map and reason == "duplicate_phones":
        keep_to_extras = {}
        for extra_id, keep_id in reparent_map.items():
            keep_to_extras.setdefault(keep_id, []).append(extra_id)
        for keep_id, extra_ids in keep_to_extras.items():
            for i in range(0, len(extra_ids), 50):
                chunk = [str(x) for x in extra_ids[i:i+50]]
                try:
                    rp = req_lib.patch(
                        f"{SUPABASE_URL}/rest/v1/call_outcomes?leadId=in.({','.join(chunk)})",
                        headers={**SB_HEADERS, "Prefer": "return=minimal"},
                        json={"leadId": keep_id},
                        timeout=30,
                    )
                    if rp.status_code in (200, 204):
                        reparented += len(chunk)
                    else:
                        print(f"[CLEANUP] reparent HTTP {rp.status_code}: {rp.text[:200]}")
                except Exception as e:
                    print(f"[CLEANUP] reparent exception: {e}")

    deleted = 0
    failed_batches = 0
    BATCH = 50
    for i in range(0, len(ids), BATCH):
        batch = [str(x) for x in ids[i:i+BATCH]]
        try:
            r = req_lib.delete(
                f"{SUPABASE_URL}/rest/v1/leads?id=in.({','.join(batch)})",
                headers={**SB_HEADERS, "Prefer": "return=minimal"},
                timeout=30,
            )
            if r.status_code in (200, 204):
                deleted += len(batch)
            else:
                failed_batches += 1
                print(f"[CLEANUP] batch delete HTTP {r.status_code}: {r.text[:200]}")
        except Exception as e:
            failed_batches += 1
            print(f"[CLEANUP] batch delete exception: {e}")

    audit_log(user, "leads_cleanup", "lead", None, {
        "reason": reason, "requested": len(ids),
        "deleted": deleted, "failed_batches": failed_batches,
        "reparented_calls": reparented,
    })
    return {"deleted": deleted, "requested": len(ids), "failed_batches": failed_batches, "reparented_calls": reparented}

@app.post("/api/admin/leads/backfill-call-stats")
def backfill_call_stats(user: str = Depends(verify_admin)):
    """One-shot: aggregate call_outcomes per leadId and write the totals back
    to the leads table. Until `log_call` was patched to bump total_calls /
    last_called_at, every lead had `total_calls=0` and `last_called_at=NULL`
    regardless of how many times it had actually been dialed — which broke
    the smart sort (every lead looked 'fresh') and hid timestamps on cards.

    This rebuilds those columns from call_outcomes, the ground-truth log."""
    print(f"[BACKFILL] starting call-stats backfill (triggered by {user})")
    # Pull all call_outcomes — id+leadId+calledAt is enough. Paginate via Range
    # because the table is well past 1000 rows.
    rows = _paginated_get(
        f"{SUPABASE_URL}/rest/v1/call_outcomes?select=leadId,calledAt&order=calledAt.asc",
        page_size=1000, max_pages=100,
    )
    if not rows:
        return {"updated": 0, "scanned_calls": 0, "note": "no call_outcomes found"}

    # Aggregate in Python: per leadId, count + most-recent calledAt.
    agg = {}
    for c in rows:
        lid = c.get("leadId")
        if not lid:
            continue
        called_at = c.get("calledAt") or ""
        cur = agg.get(lid)
        if not cur:
            agg[lid] = {"total_calls": 1, "last_called_at": called_at}
        else:
            cur["total_calls"] += 1
            if called_at and called_at > cur["last_called_at"]:
                cur["last_called_at"] = called_at

    # Bulk upsert on `id` — PostgREST merges only the fields we send when
    # Prefer: resolution=merge-duplicates is set. 500/batch keeps payload size
    # under Supabase's request limit comfortably.
    payload = [{"id": lid, "total_calls": v["total_calls"], "last_called_at": v["last_called_at"]}
               for lid, v in agg.items()]
    BATCH = 500
    updated = 0
    failed_batches = 0
    for i in range(0, len(payload), BATCH):
        batch = payload[i:i+BATCH]
        try:
            r = req_lib.post(
                f"{SUPABASE_URL}/rest/v1/leads?on_conflict=id",
                headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
                json=batch, timeout=60,
            )
            if r.status_code in (200, 201, 204):
                updated += len(batch)
            else:
                failed_batches += 1
                print(f"[BACKFILL] batch {i//BATCH} HTTP {r.status_code}: {r.text[:300]}")
        except Exception as e:
            failed_batches += 1
            print(f"[BACKFILL] batch {i//BATCH} exception: {e}")

    audit_log(user, "backfill_call_stats", "lead", None, {
        "scanned_calls": len(rows), "leads_updated": updated,
        "failed_batches": failed_batches,
    })
    print(f"[BACKFILL] done — scanned {len(rows)} calls across {len(agg)} leads, "
          f"updated {updated}, failed_batches={failed_batches}")
    return {
        "scanned_calls": len(rows),
        "leads_with_calls": len(agg),
        "updated": updated,
        "failed_batches": failed_batches,
    }

@app.get("/api/admin/audit-log")
def admin_audit_log(limit: int = 200, action: str = "", username: str = "",
                    user: str = Depends(verify_admin)):
    """Browse recent audit_log rows. Optional filters: action (e.g.
    'leads_cleanup', 'login_success', 'scrape_leads') and username. Default
    200 newest rows. Used by the admin Audit Log panel for post-mortems —
    every cleanup, scrape, login, kill-switch toggle, etc. lands here."""
    limit = max(1, min(int(limit or 200), 1000))
    url = (f"{SUPABASE_URL}/rest/v1/audit_log?select=*"
           f"&order=created_at.desc&limit={limit}")
    if action:   url += f"&action=eq.{url_quote(action)}"
    if username: url += f"&username=eq.{url_quote(username)}"
    try:
        r = req_lib.get(url, headers=SB_HEADERS, timeout=20)
        return {"entries": r.json() if r.status_code == 200 else [], "limit": limit}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/insights/best-call-time")
def insights_best_call_time(industry: str = "", days: int = 60,
                            user: str = Depends(verify_token)):
    """Hour-of-day contact-rate heuristic. Buckets call_outcomes from the
    last `days` days by hour-of-day (caller's local clock, UTC for now —
    good enough for relative-best, not absolute legal). Returns a 24-bucket
    array of (calls, contacts, rate%) so the dialer card can hint "best hour
    for healthcare is 10-11am."

    Without an industry filter, returns a global ranking across all calls."""
    days = max(1, min(int(days or 60), 365))
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    contacted_set = {"answered", "interested", "interested_no_dm", "converted", "callback", "not_interested"}

    # If filtering by industry, first resolve leadIds matching that industry,
    # then pull their calls. PostgREST can't join directly.
    lead_filter = ""
    if industry:
        rl = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads?select=id&industry=eq.{url_quote(industry)}&limit=10000",
            headers=SB_HEADERS, timeout=30,
        )
        ids = [str(x.get("id")) for x in (rl.json() if rl.status_code == 200 else []) if x.get("id")]
        if not ids:
            return {"industry": industry, "days": days, "hours": [], "total_calls": 0}
        # Chunk to keep the URL length reasonable
        chunks = [ids[i:i+200] for i in range(0, len(ids), 200)]
    else:
        chunks = [None]

    by_hour = [{"hour": h, "calls": 0, "contacts": 0} for h in range(24)]
    total_calls = 0
    for chunk in chunks:
        url = (f"{SUPABASE_URL}/rest/v1/call_outcomes?select=calledAt,outcome"
               f"&calledAt=gte.{cutoff}")
        if chunk is not None:
            url += f"&leadId=in.({','.join(chunk)})"
        rows = _paginated_get(url, page_size=1000, max_pages=20)
        for c in rows:
            cat = c.get("calledAt") or ""
            if len(cat) < 13: continue
            try:
                hr = int(cat[11:13])
            except Exception:
                continue
            by_hour[hr]["calls"] += 1
            if (c.get("outcome") or "") in contacted_set:
                by_hour[hr]["contacts"] += 1
            total_calls += 1

    for b in by_hour:
        b["rate"] = round((b["contacts"] / b["calls"] * 100), 1) if b["calls"] else 0.0
    # Best hour = highest rate with at least 5 calls' worth of signal.
    eligible = [b for b in by_hour if b["calls"] >= 5]
    best = max(eligible, key=lambda b: b["rate"]) if eligible else None
    return {"industry": industry or "all", "days": days, "total_calls": total_calls,
            "best_hour": best, "hours": by_hour}

# Soft guidance: tells a caller which state is in its peak-pickup hour right
# now, based on last `days` of call_outcomes bucketed by lead-state local hour.
# Returns null when no state has a meaningfully-better-than-average bucket at
# its current local hour — UI then hides the banner and the caller just keeps
# working her existing queue.
GUIDANCE_PULL_COOLDOWN_HOURS = 4    # per caller × state
GUIDANCE_PULL_QUEUE_THRESHOLD = 30  # only pull Apollo when queue is below this

@app.get("/api/guidance/best-region")
def guidance_best_region(days: int = 30, min_sample: int = 20,
                         user: str = Depends(verify_token)):
    """Returns the state whose *current local hour* has the best historical
    answer rate AND beats that state's own daily average by ≥5pp. Null when
    no state qualifies. Caller frontend uses this to show a soft 'switch to
    X' banner; absence of recommendation = silent."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        return None
    days = max(7, min(int(days or 30), 90))
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()

    calls = _paginated_get(
        f"{SUPABASE_URL}/rest/v1/call_outcomes?select=outcome,calledAt,leadId"
        f"&calledAt=gte.{since}&order=calledAt.desc"
    )
    if not calls:
        return None

    lead_ids = list({c.get("leadId") for c in calls if c.get("leadId")})
    lead_state = {}
    for i in range(0, len(lead_ids), 200):
        chunk = lead_ids[i:i+200]
        try:
            ids_f = ",".join(str(x) for x in chunk)
            r = req_lib.get(
                f"{SUPABASE_URL}/rest/v1/leads?id=in.({ids_f})&select=id,state",
                headers=SB_HEADERS, timeout=30,
            )
            rows = r.json() if r.status_code == 200 else []
            if isinstance(rows, list):
                for row in rows:
                    s = (row.get("state") or "").strip().upper()
                    if row.get("id") and s in US_STATE_TIMEZONES:
                        lead_state[row["id"]] = s
        except Exception:
            pass

    PICKUP = {"answered", "interested", "not_interested", "callback", "converted"}
    tz_cache = {}
    def gtz(tz_str):
        if tz_str not in tz_cache:
            try: tz_cache[tz_str] = ZoneInfo(tz_str)
            except Exception: tz_cache[tz_str] = None
        return tz_cache[tz_str]

    # buckets[state][local_hour] -> {"calls", "pickups"}
    buckets = {}
    for c in calls:
        st = lead_state.get(c.get("leadId"))
        if not st: continue
        cat = c.get("calledAt") or ""
        if len(cat) < 19: continue
        tz = gtz(US_STATE_TIMEZONES[st])
        if not tz: continue
        try:
            ts = datetime.fromisoformat(cat.replace("Z","+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=ZoneInfo("UTC"))
            local_hr = ts.astimezone(tz).hour
        except Exception:
            continue
        b = buckets.setdefault(st, {}).setdefault(local_hr,
                                                  {"calls": 0, "pickups": 0})
        b["calls"] += 1
        if (c.get("outcome") or "") in PICKUP:
            b["pickups"] += 1

    candidates = []
    for st, hours in buckets.items():
        tz = gtz(US_STATE_TIMEZONES[st])
        if not tz: continue
        now_hr = datetime.now(tz).hour
        cur = hours.get(now_hr)
        if not cur or cur["calls"] < min_sample: continue
        rate = cur["pickups"] / cur["calls"]
        total_c = sum(b["calls"] for b in hours.values())
        total_p = sum(b["pickups"] for b in hours.values())
        avg = (total_p / total_c) if total_c else 0
        # Require ≥25% absolute and ≥5pp lift over state avg — keeps signal
        # high so the banner doesn't nag with marginal recommendations.
        if rate < 0.25 or (rate - avg) < 0.05: continue
        candidates.append({
            "state": st,
            "state_name": US_STATES_FULL.get(st, st),
            "current_local_hour": now_hr,
            "answer_rate": round(rate * 100, 1),
            "state_avg_rate": round(avg * 100, 1),
            "sample_size": cur["calls"],
        })

    if not candidates:
        return None
    candidates.sort(key=lambda x: x["answer_rate"], reverse=True)
    top = candidates[0]

    # Available (unclaimed) leads with phone in that state, excluding closed.
    try:
        ql = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads"
            f"?select=id&state=eq.{top['state']}"
            f"&or=(assignedTo.is.null,assignedTo.eq.)"
            f"&phone=not.is.null"
            f"&status=not.in.(converted,interested)"
            "&limit=1000",
            headers=SB_HEADERS, timeout=20,
        )
        top["queued_leads"] = len(ql.json()) if ql.status_code == 200 else 0
    except Exception:
        top["queued_leads"] = 0
    return top

@app.post("/api/guidance/switch")
def guidance_switch(body: dict, user: str = Depends(verify_token)):
    """Caller clicked 'Switch to <state>' on the guidance banner. If the
    unclaimed queue for that state is below threshold AND this caller hasn't
    pulled it in the cooldown window, triggers a strict-capped Apollo pull
    (1 page × 25 contacts, no phone reveal). Otherwise just confirms the
    filter switch."""
    state = (body.get("state") or "").strip().upper()
    if state not in US_STATE_TIMEZONES:
        raise HTTPException(status_code=400, detail="Unknown state")

    try:
        ql = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads"
            f"?select=id&state=eq.{state}"
            f"&or=(assignedTo.is.null,assignedTo.eq.)"
            f"&phone=not.is.null"
            f"&status=not.in.(converted,interested)"
            "&limit=1000",
            headers=SB_HEADERS, timeout=20,
        )
        queued = len(ql.json()) if ql.status_code == 200 else 0
    except Exception:
        queued = 0

    resp = {"state": state, "queued": queued,
            "apollo_pulled": False, "apollo_new_leads": 0, "message": ""}

    if queued >= GUIDANCE_PULL_QUEUE_THRESHOLD:
        resp["message"] = f"{queued} {state} leads already in queue — switched."
        return resp

    ck = f"guidance_pull_{user}_{state}".lower()
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/app_settings?key=eq.{ck}&select=value",
            headers=SB_HEADERS, timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
        if rows:
            try:
                last_ts = datetime.fromisoformat(
                    rows[0].get("value","").replace("Z","+00:00"))
                if last_ts.tzinfo: last_ts = last_ts.replace(tzinfo=None)
                elapsed_h = (datetime.utcnow() - last_ts).total_seconds() / 3600
                if elapsed_h < GUIDANCE_PULL_COOLDOWN_HOURS:
                    resp["message"] = (f"Switched to {state}. Last pull "
                                       f"{round(elapsed_h,1)}h ago — cooldown active.")
                    return resp
            except Exception:
                pass
    except Exception:
        pass

    if not APOLLO_API_KEY:
        resp["message"] = f"Switched to {state}. (Apollo not configured.)"
        return resp

    pull_body = ApolloPullRequest(
        titles=["Facility Manager","Director of Operations",
                "Operations Manager","Property Manager"],
        industries=[],
        locations=[US_STATES_FULL.get(state, state)],
        per_page=25, page=1, reveal_phones=False,
        auto_paginate=False, min_new_leads=10, max_pages=1,
    )
    try:
        pull_result = apollo_pull(pull_body, user)
        resp["apollo_pulled"] = True
        resp["apollo_new_leads"] = int(pull_result.get("saved", 0))
        resp["message"] = (f"Switched to {state}. Pulled "
                           f"{resp['apollo_new_leads']} fresh contacts.")
        try:
            req_lib.post(
                f"{SUPABASE_URL}/rest/v1/app_settings?on_conflict=key",
                headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates"},
                json={"key": ck, "value": datetime.utcnow().isoformat()},
                timeout=10,
            )
        except Exception:
            pass
    except HTTPException as e:
        resp["message"] = f"Switched to {state}. Apollo pull failed: {e.detail}"
    except Exception as e:
        resp["message"] = f"Switched to {state}. Apollo pull error: {e}"
    return resp

@app.post("/api/leads/recycle-stale")
def recycle_stale_leads(user: str = Depends(verify_admin)):
    """Unassign leads that haven't been touched in 7+ days"""
    try:
        cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads?select=id,assignedTo,updatedAt,status"
            f"&assignedTo=neq.&status=not.in.(converted,interested)"
            f"&updatedAt=lt.{cutoff}",
            headers=SB_HEADERS, timeout=30)
        stale = r.json() if r.status_code == 200 else []
        if not isinstance(stale, list):
            return {"recycled": 0}
        recycled_ids = [{"id": l["id"], "was_assigned_to": l.get("assignedTo")} for l in stale if l.get("assignedTo")]
        stale_ids = [item["id"] for item in recycled_ids]
        recycled = 0
        if stale_ids:
            # Bulk update in one request
            ids_filter = ",".join(str(i) for i in stale_ids)
            req_lib.patch(
                f"{SUPABASE_URL}/rest/v1/leads?id=in.({ids_filter})",
                headers=SB_HEADERS,
                json={"assignedTo": "", "updatedAt": datetime.utcnow().isoformat()},
                timeout=30)
            recycled = len(stale_ids)
        audit_log(user, "recycle_stale", "lead", None, {"recycled": recycled, "leads": recycled_ids[:20]})
        return {"recycled": recycled, "total_checked": len(stale)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Last-contacted backfill + connectivity-rate analytics ──────────────────
# log_call now bumps lead.updatedAt on every call, but that's only forward-
# looking — the backfill walks call_outcomes once to retroactively pin
# updatedAt to the most recent calledAt for each lead, so the cards show
# accurate "last contacted" timestamps for historical data.

# Local timezone for connectivity bucketing. Defaults to Phoenix (MST, no
# DST) since Vision is in AZ; override with LEADFLOW_TZ_OFFSET_HOURS for
# other deployments. UTC offset in hours, can be negative.
LEADFLOW_TZ_OFFSET_HOURS = int(os.getenv("LEADFLOW_TZ_OFFSET_HOURS", "-7"))

@app.post("/api/admin/leads/backfill-last-contacted")
def backfill_last_contacted(user: str = Depends(verify_admin)):
    """One-shot: walk call_outcomes, pin each lead's updatedAt to the most
    recent calledAt. Idempotent (only updates when calledAt > current
    updatedAt). Surfaces accurate 'last contacted' timestamps on lead cards
    for leads that were called before the log_call updatedAt-bump fix."""
    PAGE = 1000
    last_call_per_lead = {}  # leadId → max calledAt ISO string

    # Walk every call_outcome row in pages, build {leadId: max calledAt}
    for page in range(100):  # 100K-call ceiling fail-safe
        start = page * PAGE
        end   = start + PAGE - 1
        try:
            r = req_lib.get(
                f"{SUPABASE_URL}/rest/v1/call_outcomes"
                f"?select=leadId,calledAt&order=calledAt.desc",
                headers={**SB_HEADERS, "Range-Unit": "items", "Range": f"{start}-{end}"},
                timeout=30,
            )
            batch = r.json() if r.status_code in (200, 206) else []
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"call_outcomes page {page} fetch failed: {e}")
        if not isinstance(batch, list) or not batch:
            break
        for c in batch:
            lid = c.get("leadId")
            ts  = c.get("calledAt")
            if not lid or not ts:
                continue
            cur = last_call_per_lead.get(lid)
            if not cur or ts > cur:
                last_call_per_lead[lid] = ts
        if len(batch) < PAGE:
            break

    if not last_call_per_lead:
        return {"calls_processed": 0, "leads_updated": 0,
                "leads_with_calls": 0, "skipped_already_current": 0}

    # Fetch current updatedAt for every affected lead in batches, then patch
    # only the rows where the call timestamp is newer (idempotent re-runs).
    BATCH = 50
    lead_ids = list(last_call_per_lead.keys())
    updated = 0
    skipped_current = 0
    failed = 0
    for i in range(0, len(lead_ids), BATCH):
        batch_ids = lead_ids[i:i+BATCH]
        ids_filter = ",".join(str(x) for x in batch_ids)
        try:
            cr = req_lib.get(
                f"{SUPABASE_URL}/rest/v1/leads?id=in.({ids_filter})&select=id,updatedAt",
                headers=SB_HEADERS, timeout=30,
            )
            current = cr.json() if cr.status_code == 200 else []
        except Exception as e:
            print(f"[BACKFILL-LC] fetch batch {i} failed: {e}")
            failed += len(batch_ids)
            continue
        if not isinstance(current, list):
            continue
        for lead in current:
            lid = lead.get("id")
            current_ts = lead.get("updatedAt") or ""
            new_ts = last_call_per_lead.get(lid)
            if not new_ts:
                continue
            if new_ts <= current_ts:
                skipped_current += 1
                continue
            try:
                pr = req_lib.patch(
                    f"{SUPABASE_URL}/rest/v1/leads?id=eq.{lid}",
                    headers=SB_HEADERS,
                    json={"updatedAt": new_ts},
                    timeout=10,
                )
                if pr.status_code in (200, 204):
                    updated += 1
                else:
                    failed += 1
            except Exception as e:
                print(f"[BACKFILL-LC] patch lead {lid} failed: {e}")
                failed += 1

    audit_log(user, "backfill_last_contacted", "lead", None, {
        "leads_with_calls": len(last_call_per_lead),
        "leads_updated":    updated,
        "skipped_current":  skipped_current,
        "failed":           failed,
    })
    return {
        "leads_with_calls":         len(last_call_per_lead),
        "leads_updated":            updated,
        "skipped_already_current":  skipped_current,
        "failed":                   failed,
    }

@app.get("/api/analytics/connectivity")
def get_connectivity_analytics(days: int = 90, user: str = Depends(verify_token)):
    """Day-of-week × hour-of-day connectivity heatmap from call_outcomes.

    Bucketed by the *prospect's* local time (lead.state → IANA tz, DST-aware
    via zoneinfo). Pickup rate is driven by what hour it is at the office
    being dialed, not the caller's clock — a 9am AZ call to NY = noon
    Eastern at the receptionist's desk, and that's the bucket that matters.

    Reports total/pickup/voicemail/no_answer per bucket plus pickup_rate.
    'Pickup' = real human (answered, interested, not_interested, callback,
    converted); voicemail + no_answer split out so 'no one home' vs 'human
    there but won't engage' are visible separately.

    For calls whose lead has no state or an unknown state, falls back to
    LEADFLOW_TZ_OFFSET_HOURS (default Phoenix). Counted in fallback_calls
    so admins can see if the heatmap is being watered down.
    """
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        ZoneInfo = None  # type: ignore  # Python <3.9 fallback to fixed offset
    days = max(1, min(int(days or 90), 365))
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()

    # Fetch calls (with leadId so we can join lead.state) and the leads they
    # touched. Both paginated to dodge the 1000-row Supabase cap.
    calls = _paginated_get(
        f"{SUPABASE_URL}/rest/v1/call_outcomes"
        f"?select=outcome,calledAt,leadId&calledAt=gte.{since}&order=calledAt.desc"
    )

    # Lead-state lookup: only the leadIds we actually saw, batched to keep
    # the in.() filter URL under ~8KB. Builds {leadId: state} for tz lookup.
    lead_ids = list({c.get("leadId") for c in calls if c.get("leadId")})
    lead_state = {}
    BATCH = 200
    for i in range(0, len(lead_ids), BATCH):
        chunk = lead_ids[i:i+BATCH]
        try:
            ids_filter = ",".join(str(x) for x in chunk)
            lr = req_lib.get(
                f"{SUPABASE_URL}/rest/v1/leads?id=in.({ids_filter})&select=id,state",
                headers=SB_HEADERS, timeout=30,
            )
            rows = lr.json() if lr.status_code == 200 else []
            if isinstance(rows, list):
                for row in rows:
                    if row.get("id"):
                        lead_state[row["id"]] = (row.get("state") or "").strip()
        except Exception as e:
            print(f"[CONNECTIVITY] lead-state batch {i} fetch failed: {e}")

    DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    PICKUP_OUTCOMES    = {"answered", "interested", "not_interested", "callback", "converted"}
    VOICEMAIL_OUTCOMES = {"voicemail"}

    # Cache ZoneInfo objects per tz string (cheap, but no need to re-build per call)
    tz_cache = {}
    def get_tz(tz_str):
        if not ZoneInfo or not tz_str:
            return None
        z = tz_cache.get(tz_str)
        if z is None:
            try:
                z = ZoneInfo(tz_str)
            except Exception:
                z = None
            tz_cache[tz_str] = z
        return z

    buckets = {}
    parse_failures = 0
    fallback_calls = 0  # calls bucketed via fixed offset (state unknown / pre-3.9 Python)
    state_counts = {}   # {state: bucketed_count} — observability
    state_buckets = {}  # {state: {total, pickup, voicemail, no_answer}} — per-state pickup rate
    for c in calls:
        ts = c.get("calledAt") or ""
        try:
            clean_ts = ts.replace("Z", "").split("+")[0].split(".")[0]
            dt_utc = datetime.fromisoformat(clean_ts).replace(tzinfo=timezone.utc)
        except Exception:
            parse_failures += 1
            continue

        lid = c.get("leadId")
        state = lead_state.get(lid, "") if lid else ""
        state_key = (state.upper() if len(state) == 2 else state) or "(unset)"
        iana = state_to_iana_tz(state)
        tz = get_tz(iana) if iana else None
        if tz is not None:
            dt_local = dt_utc.astimezone(tz)
            state_counts[state_key] = state_counts.get(state_key, 0) + 1
        else:
            # Fallback: fixed offset (caller's tz default). Used when lead has
            # no state, an unrecognized state, or zoneinfo is unavailable.
            dt_local = (dt_utc + timedelta(hours=LEADFLOW_TZ_OFFSET_HOURS)).replace(tzinfo=None)
            fallback_calls += 1

        day_idx = dt_local.weekday()
        hour    = dt_local.hour
        key     = (day_idx, hour)
        if key not in buckets:
            buckets[key] = {"total": 0, "pickup": 0, "voicemail": 0, "no_answer": 0}
        buckets[key]["total"] += 1
        if state_key not in state_buckets:
            state_buckets[state_key] = {"total": 0, "pickup": 0, "voicemail": 0, "no_answer": 0}
        state_buckets[state_key]["total"] += 1
        outcome = (c.get("outcome") or "").lower()
        if outcome in PICKUP_OUTCOMES:
            buckets[key]["pickup"] += 1
            state_buckets[state_key]["pickup"] += 1
        elif outcome in VOICEMAIL_OUTCOMES:
            buckets[key]["voicemail"] += 1
            state_buckets[state_key]["voicemail"] += 1
        else:
            buckets[key]["no_answer"] += 1
            state_buckets[state_key]["no_answer"] += 1

    windows = []
    for (day_idx, hour), counts in sorted(buckets.items()):
        rate = (counts["pickup"] / counts["total"] * 100) if counts["total"] else 0
        windows.append({
            "day":          DAYS[day_idx],
            "day_idx":      day_idx,
            "hour":         hour,
            "total":        counts["total"],
            "pickup":       counts["pickup"],
            "voicemail":    counts["voicemail"],
            "no_answer":    counts["no_answer"],
            "pickup_rate":  round(rate, 1),
        })

    MIN_SAMPLE = 5
    significant = [w for w in windows if w["total"] >= MIN_SAMPLE]
    best  = sorted(significant, key=lambda w: -w["pickup_rate"])[:5]
    worst = sorted(significant, key=lambda w:  w["pickup_rate"])[:5]

    overall_total  = sum(w["total"]  for w in windows)
    overall_pickup = sum(w["pickup"] for w in windows)
    overall_rate   = (overall_pickup / overall_total * 100) if overall_total else 0

    # Per-state rollup — sorted by volume so highest-impact states appear first.
    # Pickup rate is meaningful at ≥10 dials per state to avoid noisy ranks.
    state_rows = []
    for st, b in state_buckets.items():
        rate = (b["pickup"] / b["total"] * 100) if b["total"] else 0
        state_rows.append({
            "state":       st,
            "total":       b["total"],
            "pickup":      b["pickup"],
            "voicemail":   b["voicemail"],
            "no_answer":   b["no_answer"],
            "pickup_rate": round(rate, 1),
        })
    state_rows.sort(key=lambda r: -r["total"])

    return {
        "since":           since,
        "days_analyzed":   days,
        "tz_strategy":     "prospect_local" if ZoneInfo else "fixed_offset_only",
        "tz_offset_hours_fallback": LEADFLOW_TZ_OFFSET_HOURS,
        "windows":         windows,
        "best_windows":    best,
        "worst_windows":   worst,
        "state_counts":    state_counts,
        "state_breakdown": state_rows,
        "summary": {
            "total_calls":          overall_total,
            "overall_pickup_rate":  round(overall_rate, 1),
            "min_sample_for_rank":  MIN_SAMPLE,
            "parse_failures":       parse_failures,
            "fallback_calls":       fallback_calls,
        },
    }

@app.get("/api/analytics/insights")
def get_insights(days: int = 90, user: str = Depends(verify_token)):
    """Three insights bundled in one call (one paginated leads + calls fetch
    feeds all three — cheaper than three separate endpoints):

      1. Apollo ROI — conversion + engagement + contact rate, broken down by
         lead source bucket (Apollo direct / Apollo-enriched Google Places /
         Google-only / other). Tells you whether Apollo credits are paying off.

      2. Touch-count to conversion — for every status=converted lead,
         distribution of how many calls it took to convert. Median + mean +
         cumulative %. Tells you when to give up vs keep dialing.

      3. Stale callbacks — leads with status=callback whose callbackDate is in
         the past and have no call logged since the callback was scheduled.
         Direct leakage: scheduled commitments your caller forgot.
    """
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")
    days = max(1, min(int(days or 90), 365))
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    today_iso = datetime.utcnow().strftime("%Y-%m-%d")

    leads = _paginated_get(
        f"{SUPABASE_URL}/rest/v1/leads"
        f"?select=id,company,source,status,firstName,assignedTo,callbackDate,total_calls,createdAt"
    )
    calls = _paginated_get(
        f"{SUPABASE_URL}/rest/v1/call_outcomes"
        f"?select=leadId,outcome,calledAt&calledAt=gte.{since}"
    )

    # ── Apollo ROI ───────────────────────────────────────────────────────
    # Source-bucket inference:
    #   apollo_direct   = source="Apollo" (admin bulk pull of DM contacts)
    #   apollo_enriched = source="Google Places" + has firstName (Apollo's
    #                     auto-enrichment found a DM at the company)
    #   gp_only         = source="Google Places" + no firstName (Apollo
    #                     missed or wasn't run — switchboard-only contact)
    #   other           = CSV imports, manual creates, anything else
    bucket_defs = [
        ("apollo_direct",   "Apollo direct (bulk DM pull)"),
        ("apollo_enriched", "Apollo-enriched (Google Places + DM)"),
        ("gp_only",         "Google Places only (no DM)"),
        ("other",           "Manual / CSV / other"),
    ]
    buckets = {k: {"name": n, "total": 0, "converted": 0, "interested": 0,
                   "callback": 0, "contacted": 0}
               for k, n in bucket_defs}
    for l in leads:
        src     = (l.get("source") or "").strip()
        has_dm  = bool((l.get("firstName") or "").strip())
        if src == "Apollo":
            key = "apollo_direct"
        elif src == "Google Places":
            key = "apollo_enriched" if has_dm else "gp_only"
        else:
            key = "other"
        b = buckets[key]
        b["total"] += 1
        status = (l.get("status") or "").strip()
        if status == "converted":   b["converted"]  += 1
        elif status == "interested":b["interested"] += 1
        elif status == "callback":  b["callback"]   += 1
        if (l.get("total_calls") or 0) > 0:
            b["contacted"] += 1

    apollo_roi = []
    for k, _name in bucket_defs:
        b = buckets[k]
        t = b["total"]
        engaged = b["converted"] + b["interested"] + b["callback"]
        apollo_roi.append({
            "key":             k,
            "name":            b["name"],
            "total":           t,
            "converted":       b["converted"],
            "interested":      b["interested"],
            "callback":        b["callback"],
            "contacted":       b["contacted"],
            "conv_rate":       round((b["converted"] / t * 100), 1) if t else 0.0,
            "engagement_rate": round((engaged       / t * 100), 1) if t else 0.0,
            "contact_rate":    round((b["contacted"]/ t * 100), 1) if t else 0.0,
        })

    # ── Touch-count to conversion ────────────────────────────────────────
    calls_by_lead = {}
    for c in calls:
        lid = c.get("leadId")
        if lid:
            calls_by_lead.setdefault(lid, []).append(c)

    converted_lead_ids = [l["id"] for l in leads
                          if l.get("status") == "converted" and l.get("id")]
    touch_counts = []
    touch_dist = {}
    for lid in converted_lead_ids:
        n = len(calls_by_lead.get(lid, []))
        if n == 0:
            continue  # converted with no calls in the window — skip (probably outside our window)
        touch_counts.append(n)
        touch_dist[n] = touch_dist.get(n, 0) + 1

    distribution = [{"touches": k, "count": v} for k, v in sorted(touch_dist.items())]
    median_touches = 0
    mean_touches   = 0.0
    p75_touches    = 0
    if touch_counts:
        sorted_tc = sorted(touch_counts)
        n = len(sorted_tc)
        median_touches = sorted_tc[n//2] if n % 2 else (sorted_tc[n//2-1] + sorted_tc[n//2]) / 2
        mean_touches   = round(sum(sorted_tc) / n, 1)
        p75_idx        = max(0, int(n * 0.75) - 1)
        p75_touches    = sorted_tc[p75_idx]

    cumulative = []
    if touch_counts:
        running = 0
        total_conv = len(touch_counts)
        for k, v in sorted(touch_dist.items()):
            running += v
            cumulative.append({"by_touch": k,
                              "pct": round(running / total_conv * 100, 1)})

    # ── Stale callbacks ──────────────────────────────────────────────────
    stale = []
    for l in leads:
        if l.get("status") != "callback":
            continue
        cb_date = (l.get("callbackDate") or "").strip()
        if not cb_date or cb_date >= today_iso:
            continue
        # No call logged since the callback was due?
        recent = [c for c in calls_by_lead.get(l.get("id"), [])
                  if (c.get("calledAt") or "") >= cb_date + "T00:00:00"]
        if recent:
            continue
        stale.append(l)
    stale.sort(key=lambda x: x.get("callbackDate") or "")
    stale_samples = [{
        "id":           s["id"],
        "company":      s.get("company"),
        "callbackDate": s.get("callbackDate"),
        "assignedTo":   s.get("assignedTo"),
        "days_overdue": (datetime.strptime(today_iso, "%Y-%m-%d")
                          - datetime.strptime(s.get("callbackDate"), "%Y-%m-%d")).days
                          if s.get("callbackDate") else 0,
    } for s in stale[:20]]

    return {
        "since":         since,
        "days_analyzed": days,
        "apollo_roi":    apollo_roi,
        "touch_count": {
            "distribution":    distribution,
            "cumulative_pct":  cumulative,
            "median":          median_touches,
            "mean":            mean_touches,
            "p75":             p75_touches,
            "converted_count": len(touch_counts),
        },
        "stale_callbacks": {
            "count":   len(stale),
            "samples": stale_samples,
        },
    }

def _apollo_pull_one_page(payload: dict, headers: dict, body: "ApolloPullRequest", user: str) -> dict:
    """Run one Apollo search page end-to-end: query → US filter → enrich →
    save → request phone reveals. Returns a dict with per-page counters that
    `apollo_pull` aggregates across pages.

    Pure inner helper. The outer endpoint handles auto-pagination so the
    caller can pull until they get N new leads instead of "0 saved, ¯\_(ツ)_/¯".
    """
    print(f"[APOLLO] user={user} payload={payload}")
    try:
        r = req_lib.post(APOLLO_SEARCH_URL, headers=headers, json=payload, timeout=30)
        print(f"[APOLLO] HTTP {r.status_code}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Apollo API call failed: {e}")

    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Apollo rejected the API key. Verify APOLLO_API_KEY in Railway.")
    if r.status_code == 422:
        raise HTTPException(status_code=400, detail=f"Apollo rejected the search params: {r.text[:300]}")
    if r.status_code != 200:
        raise HTTPException(status_code=502,
            detail=f"Apollo returned {r.status_code}: {r.text[:300]}")

    try:
        data = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Apollo returned non-JSON response")

    people = data.get("people") or data.get("contacts") or []
    pagination = data.get("pagination") or {}
    print(f"[APOLLO] page {payload.get('page')} returned {len(people)} people "
          f"(total available: {pagination.get('total_entries')})")

    return _apollo_process_page(people, pagination, body, user)

def _apollo_process_page(people, pagination, body: "ApolloPullRequest", user: str) -> dict:
    """Filter, enrich, dedup, save the people returned for one page. Separated
    from the HTTP call so the auto-paginate loop can rate-limit cleanly and
    the unit-test path can inject fixtures without hitting Apollo."""

    # Pre-enrichment dedupe — drop people we already have in leads before we
    # spend ~1 credit/each on /people/match. Saves money on repeat searches.
    people, skipped_already_in_db = filter_apollo_dupes(people)
    if skipped_already_in_db:
        print(f"[APOLLO] dedupe: skipped {skipped_already_in_db} already-known contacts before enrichment")

    qualified_pairs = []  # list of (lead, original_person) — kept aligned for phone reveal step
    skipped_no_company = 0
    skipped_no_actionable = 0
    skipped_non_us = 0
    for person in people:
        # Drop non-US results BEFORE the /people/match credit spend. Apollo's
        # location filter is permissive (matches "California" against e.g.
        # "California, Wales"); the api_search response has `country` and
        # `state` per person, so we double-check here.
        if not _apollo_person_is_us(person):
            skipped_non_us += 1
            continue

        # Unlock email + last name via /people/match (~1 credit). api_search alone
        # masks these on Pro tier — match is the only way to make leads dialable.
        enriched = apollo_enrich_person(person)
        if enriched:
            person = {**person, **enriched}

        lead = apollo_person_to_lead(person, user)
        if not lead.get("company"):
            skipped_no_company += 1
            continue
        has_contact = bool(lead.get("phone") or lead.get("email"))
        has_named_ask = bool(lead.get("firstName") and lead.get("lastName") and lead.get("title"))
        if not has_contact and not has_named_ask:
            skipped_no_actionable += 1
            continue
        qualified_pairs.append((lead, person))

    leads = [l for l, _ in qualified_pairs]

    # Phone normalization — same canonical (NNN) NNN-NNNN format used by
    # save_to_supabase. Without this an Apollo phone like "+1 203 863 3000"
    # gets stored differently than a Google Places phone "(203) 863-3000"
    # for the same business, and the cross-run dedup misses the collision.
    for l in leads:
        np = normalize_us_phone(l.get("phone"))
        if np:
            l["phone"] = np

    # Cross-run dedup against existing DB phones. Apollo's pre-enrichment
    # filter_apollo_dupes already checks (name, company), but two scrapes a
    # week apart can pull the same person under slightly different name
    # spellings — and Apollo's `q_organization_industries` returns the same
    # 25 contacts repeatedly across page=1 calls. This catches both.
    skipped_phone_dup_in_db = 0
    if leads:
        incoming_phones = list({(l.get("phone") or "").strip() for l in leads if (l.get("phone") or "").strip()})
        existing_phones = set()
        if incoming_phones:
            CHUNK = 100
            for i in range(0, len(incoming_phones), CHUNK):
                chunk = incoming_phones[i:i+CHUNK]
                in_list = ",".join(f'"{p}"' for p in chunk)
                try:
                    dq = req_lib.get(
                        f"{SUPABASE_URL}/rest/v1/leads?select=phone&phone=in.({in_list})",
                        headers=SB_HEADERS, timeout=20,
                    )
                    if dq.status_code == 200:
                        for row in dq.json():
                            ph = (row.get("phone") or "").strip()
                            if ph: existing_phones.add(ph)
                except Exception as e:
                    print(f"[APOLLO-PULL] phone dedup pre-check failed: {e}")
        if existing_phones:
            before = len(leads)
            # Realign qualified_pairs with the filtered leads so the reveal-
            # phone webhook step still pairs each saved row with the right
            # Apollo person record.
            kept_pairs = [(l, p) for l, p in qualified_pairs
                          if (l.get("phone") or "").strip() not in existing_phones]
            skipped_phone_dup_in_db = before - len(kept_pairs)
            qualified_pairs = kept_pairs
            leads = [l for l, _ in qualified_pairs]
            if skipped_phone_dup_in_db:
                print(f"[APOLLO-PULL] phone dedup dropped {skipped_phone_dup_in_db} rows already in DB")

    # Save and capture row IDs for downstream phone-reveal webhook routing.
    saved_rows = []
    if leads:
        try:
            sr = req_lib.post(
                f"{SUPABASE_URL}/rest/v1/leads",
                headers=SB_HEADERS, json=leads, timeout=30,
            )
            if sr.status_code in (200, 201):
                body_json = sr.json()
                saved_rows = body_json if isinstance(body_json, list) else []
            else:
                print(f"[APOLLO-PULL] supabase insert HTTP {sr.status_code}: {sr.text[:200]}")
        except Exception as e:
            print(f"[APOLLO-PULL] supabase insert failed: {e}")
    saved = len(saved_rows)

    # Fire phone reveals (async via webhook) if requested. Apollo posts back to
    # /api/webhooks/apollo/{secret}/{lead_id} when ready.
    phone_reveals_requested = 0
    phone_reveals_blocked_by_policy = 0
    if body.reveal_phones and saved_rows and APOLLO_WEBHOOK_SECRET:
        # Same industry + budget gate the per-lead endpoint uses, applied per row.
        for (lead, person), saved_row in zip(qualified_pairs, saved_rows):
            if not saved_row.get("id"):
                continue
            policy_ok, _ = can_reveal_phone(lead)
            if not policy_ok:
                phone_reveals_blocked_by_policy += 1
                continue
            ok, _detail = apollo_request_phone_reveal(person, saved_row["id"])
            if ok:
                phone_reveals_requested += 1
                # Audit-log per reveal so the budget counter stays accurate
                audit_log(user, "reveal_phone_requested", "lead", saved_row["id"],
                          {"company": lead.get("company"), "via": "bulk_pull"})
        # Fire a single budget-threshold check after the whole batch (vs once
        # per reveal) — minimizes Slack spam during a 25-lead bulk pull.
        if phone_reveals_requested:
            _check_apollo_budget_alert()

    # Helper returns only per-page counters + saved_rows. The wrapper
    # aggregates across pages and emits audit_log / Slack / summary once.
    return {
        "people_returned":         len(people),
        "saved":                   saved,
        "saved_rows":              saved_rows,
        "skipped_already_in_db":   skipped_already_in_db,
        "skipped_phone_dup_in_db": skipped_phone_dup_in_db,
        "skipped_non_us":          skipped_non_us,
        "skipped_no_company":      skipped_no_company,
        "skipped_no_actionable":   skipped_no_actionable,
        "phone_reveals_requested": phone_reveals_requested,
        "leads":                   leads,
        "total_available":         pagination.get("total_entries"),
        "page":                    pagination.get("page"),
        "total_pages":             pagination.get("total_pages"),
    }

@app.post("/api/admin/apollo/pull")
def apollo_pull(body: ApolloPullRequest, user: str = Depends(verify_token)):
    """Pull decision-maker contacts from Apollo. Auto-paginates by default —
    keeps walking through pages until either `min_new_leads` rows are saved
    or `max_pages` are exhausted. Old behavior (single page, "0 saved" if
    everything was a dupe) is recoverable by passing auto_paginate=false.

    Open to callers, not just admins, so the floor can self-serve fresh
    decision-maker leads. Non-admins are leashed: a daily pull cap, lower
    page/size ceilings, and phone reveals (5-8 credits each) force-disabled.
    Eric stays unlimited."""
    if not APOLLO_API_KEY:
        raise HTTPException(status_code=400,
            detail="APOLLO_API_KEY not configured. Set it in Railway env vars.")

    # Non-admin guardrails. Bound credit spend without blocking self-service.
    caller_is_admin = is_admin(user)
    if not caller_is_admin:
        used = events_today(user, "apollo_pull")
        if used >= NON_ADMIN_APOLLO_PULL_CAP:
            raise HTTPException(
                status_code=429,
                detail=f"Daily Apollo pull limit reached ({used}/{NON_ADMIN_APOLLO_PULL_CAP}). "
                       f"Resets at UTC midnight. Ask Eric if you need more.",
            )
        # Callers never trigger paid phone reveals — those are an admin spend.
        body.reveal_phones = False
        body.per_page = min(int(body.per_page or 25), NON_ADMIN_APOLLO_MAX_PER_PAGE)
        body.max_pages = min(int(body.max_pages or 5), NON_ADMIN_APOLLO_MAX_PAGES)

    # Log the pull so events_today() can enforce the daily cap. Fire-and-forget.
    log_usage(user, "apollo_pull", {"locations": _to_str_list(body.locations),
                                    "industries": _to_str_list(body.industries)})

    titles     = _to_str_list(body.titles)
    industries = _to_str_list(body.industries)
    locations  = _to_str_list(body.locations)
    per_page   = max(1, min(body.per_page or 25, 100))

    # US-only enforcement at the API layer. If the caller didn't pass any
    # locations, Apollo returns global results — that's how Filipino /
    # Indian / Canadian contacts were leaking in. Always anchor to US.
    if not locations:
        locations = ["United States"]

    base_payload = {"per_page": per_page}
    if industries: base_payload["q_organization_industries"] = industries
    base_payload["person_locations"] = locations
    if body.employee_min and body.employee_max:
        base_payload["organization_num_employees_ranges"] = [f"{body.employee_min},{body.employee_max}"]

    headers = {
        "X-Api-Key":     APOLLO_API_KEY,
        "Cache-Control": "no-cache",
        "Content-Type":  "application/json",
    }

    # Build the title groups to walk. Without rotation it's a single group (the
    # user's titles, or [] = "any title"). With rotation we append the
    # decision-maker roster after the user's titles, so repeat pulls of the same
    # city surface *different* budget-holders (the GM, the Ops Director, the
    # Facilities Manager) instead of re-hitting the same Owners that are already
    # in the DB. Exact-duplicate groups are dropped so we don't pay twice.
    def _norm_group(g):
        return tuple(sorted(t.lower() for t in g))
    if body.rotate_titles:
        title_groups, seen_groups = [], set()
        for g in (([titles] if titles else []) + APOLLO_DM_TITLE_GROUPS):
            if not g:
                continue
            k = _norm_group(g)
            if k in seen_groups:
                continue
            seen_groups.add(k)
            title_groups.append(g)
        if not title_groups:
            title_groups = [titles]
    else:
        title_groups = [titles]

    # Aggregates across all pages walked
    agg = {
        "people_returned":         0,
        "saved":                   0,
        "skipped_already_in_db":   0,
        "skipped_phone_dup_in_db": 0,
        "skipped_non_us":          0,
        "skipped_no_company":      0,
        "skipped_no_actionable":   0,
        "phone_reveals_requested": 0,
        "all_leads":               [],
        "all_saved_rows":          [],
    }
    pages_walked = 0
    start_page  = max(1, body.page or 1)
    max_pages   = max(1, min(int(body.max_pages or 5), 20)) if body.auto_paginate else 1
    min_new     = max(1, int(body.min_new_leads or 10))
    total_avail = None
    last_page_meta_pages = None

    # Round-robin across title groups, each with its own page cursor, sharing a
    # single `max_pages` Apollo-call budget. Non-rotation (1 group) degenerates
    # to the old sequential page walk — identical behavior + credit spend.
    n_groups     = len(title_groups)
    group_cursor = [start_page] * n_groups
    exhausted    = [False] * n_groups
    titles_tried = []   # human-readable groups actually queried, in order
    gi = 0
    while pages_walked < max_pages and not all(exhausted):
        if exhausted[gi]:
            gi = (gi + 1) % n_groups
            continue
        grp  = title_groups[gi]
        page = group_cursor[gi]
        payload = {**base_payload, "page": page}
        if grp:
            payload["person_titles"] = grp
        result = _apollo_pull_one_page(payload, headers, body, user)
        pages_walked += 1
        group_cursor[gi] += 1
        label = ", ".join(grp) if grp else "(any title)"
        if label not in titles_tried:
            titles_tried.append(label)

        agg["people_returned"]         += result["people_returned"]
        agg["saved"]                   += result["saved"]
        agg["skipped_already_in_db"]   += result["skipped_already_in_db"]
        agg["skipped_phone_dup_in_db"] += result["skipped_phone_dup_in_db"]
        agg["skipped_non_us"]          += result["skipped_non_us"]
        agg["skipped_no_company"]      += result["skipped_no_company"]
        agg["skipped_no_actionable"]   += result["skipped_no_actionable"]
        agg["phone_reveals_requested"] += result["phone_reveals_requested"]
        agg["all_leads"].extend(result["leads"])
        agg["all_saved_rows"].extend(result["saved_rows"])
        if total_avail is None and result.get("total_available") is not None:
            total_avail = result["total_available"]
        if result.get("total_pages") is not None:
            last_page_meta_pages = result["total_pages"]

        # Per-group exhaustion: Apollo returned 0, or we hit this group's last
        # page. A group going dry just drops out of the rotation; others go on.
        if result["people_returned"] == 0:
            exhausted[gi] = True
        elif result.get("total_pages") is not None and page >= result["total_pages"]:
            exhausted[gi] = True

        # Global stop: enough fresh leads in the bag.
        if agg["saved"] >= min_new:
            break
        gi = (gi + 1) % n_groups

    # Resume cursor: lowest page reached across groups so the next click doesn't
    # skip pages we never fetched (round-robin keeps cursors within 1 of each
    # other). For a single group this is just start_page + pages_walked.
    next_page_cursor = min(group_cursor) if group_cursor else start_page

    # Audit + Slack once at the end, summarized across all pages walked.
    audit_log(user, "apollo_pull", "lead", None, {
        "titles": titles, "industries": industries, "locations": locations,
        "employee_range":           f"{body.employee_min}-{body.employee_max}",
        "start_page":               start_page,
        "pages_walked":             pages_walked,
        "per_page":                 per_page,
        "auto_paginate":            bool(body.auto_paginate),
        "returned":                 agg["people_returned"] + agg["skipped_already_in_db"],
        "qualified":                len(agg["all_leads"]),
        "saved":                    agg["saved"],
        "skipped_already_in_db":    agg["skipped_already_in_db"],
        "skipped_non_us":           agg["skipped_non_us"],
        "skipped_no_company":       agg["skipped_no_company"],
        "skipped_no_actionable":    agg["skipped_no_actionable"],
        "skipped_phone_dup_in_db":  agg["skipped_phone_dup_in_db"],
        "phone_reveals_requested":  agg["phone_reveals_requested"],
        "total_entries":            total_avail,
        "total_pages":              last_page_meta_pages,
        "rotate_titles":            bool(body.rotate_titles),
        "titles_tried":             titles_tried,
    })

    if agg["saved"] > 0:
        app_url = os.getenv("APP_URL", "https://leadflow-railway-production.up.railway.app")
        send_slack(
            ":telephone_receiver: Apollo Pull Complete",
            f"*{user}* pulled *{agg['saved']}* new decision-maker leads from Apollo "
            f"({pages_walked} page{'s' if pages_walked!=1 else ''} walked).",
            fields=[
                {"label": "Titles",       "value": (" → ".join(titles_tried) if body.rotate_titles else (", ".join(titles) or "Any"))},
                {"label": "Locations",    "value": ", ".join(locations) or "Any"},
                {"label": "Returned",     "value": str(agg["people_returned"] + agg["skipped_already_in_db"])},
                {"label": "Saved",        "value": f":white_check_mark: {agg['saved']}"},
                {"label": "Pages",        "value": f"{pages_walked} ({'rotated' if body.rotate_titles else f'{start_page}-{start_page + pages_walked - 1}'})"},
            ],
            actions=[{"label": "📋 View Leads", "url": app_url, "style": "primary"}],
        )

    total_in = agg["people_returned"] + agg["skipped_already_in_db"]
    summary_parts = []
    if agg["saved"]:                    summary_parts.append(f"{agg['saved']} saved")
    if agg["skipped_already_in_db"]:    summary_parts.append(f"{agg['skipped_already_in_db']} already in DB (pre-enrich)")
    if agg["skipped_phone_dup_in_db"]:  summary_parts.append(f"{agg['skipped_phone_dup_in_db']} duplicate phone")
    if agg["skipped_non_us"]:           summary_parts.append(f"{agg['skipped_non_us']} non-US")
    if agg["skipped_no_company"]:       summary_parts.append(f"{agg['skipped_no_company']} missing company")
    if agg["skipped_no_actionable"]:    summary_parts.append(f"{agg['skipped_no_actionable']} no contact info")
    walk_desc = (f"{pages_walked} page(s) across {len(titles_tried)} title group(s)"
                 if body.rotate_titles else f"{pages_walked} page(s)")
    summary = (f"Apollo returned {total_in} across {walk_desc} · " + " · ".join(summary_parts)) \
              if summary_parts else f"Apollo returned {total_in} across {walk_desc} · all filtered out"

    return {
        "returned":        total_in,
        "qualified":       len(agg["all_leads"]),
        "saved":           agg["saved"],
        "summary":         summary,
        "pages_walked":    pages_walked,
        "next_page":       next_page_cursor,
        "rotated":         bool(body.rotate_titles),
        "titles_tried":    titles_tried,
        "skipped":         {"already_in_db":   agg["skipped_already_in_db"],
                            "phone_dup_in_db": agg["skipped_phone_dup_in_db"],
                            "non_us":          agg["skipped_non_us"],
                            "no_company":      agg["skipped_no_company"],
                            "no_actionable":   agg["skipped_no_actionable"]},
        "phone_reveals":   {"requested": agg["phone_reveals_requested"],
                            "note": "phones arrive async via webhook within 30s"} if body.reveal_phones else None,
        "total_available": total_avail,
        "total_pages":     last_page_meta_pages,
        "sample":          [{"company": l.get("company"),
                             "name": f"{l.get('firstName','')} {l.get('lastName','')}".strip(),
                             "title": l.get("title"), "phone": l.get("phone"), "email": l.get("email")}
                            for l in (agg["all_saved_rows"] or agg["all_leads"])[:5]],
    }

@app.get("/api/admin/apollo/webhook-url")
def show_webhook_url(user: str = Depends(verify_admin)):
    """Diagnostic — show exactly what webhook_url we'd send to Apollo,
    so we can debug 'invalid HTTPS URL' rejections."""
    app_url = os.getenv("APP_URL", "https://leadflow-railway-production.up.railway.app").rstrip("/")
    secret = APOLLO_WEBHOOK_SECRET or ""
    safe_secret = url_quote(secret, safe="") if secret else ""
    return {
        "app_url_env":         os.getenv("APP_URL", "(unset, using default)"),
        "app_url_used":        app_url,
        "webhook_secret_set":  bool(secret),
        "webhook_secret_len":  len(secret),
        "webhook_secret_preview": (secret[:4] + "..." + secret[-4:]) if len(secret) >= 8 else "<too short>",
        "webhook_secret_url_encoded_changed": secret != safe_secret,
        "example_webhook_url": f"{app_url}/api/webhooks/apollo/{safe_secret}/EXAMPLE_LEAD_ID" if safe_secret else "(secret missing)",
        "url_starts_with_https": app_url.startswith("https://"),
    }

@app.post("/api/admin/apollo/test-phone-reveal")
def test_phone_reveal(body: dict, user: str = Depends(verify_admin)):
    """Diagnostic — call Apollo /people/match with reveal_phone_number=true
    and NO webhook_url to find out if Pro returns phones synchronously,
    requires async webhook, or blocks the request entirely.
    Body: {linkedin_url} OR {email} OR {first_name, last_name, domain}.
    Costs whatever Apollo charges for a phone reveal (~5-8 credits if it works)."""
    if not APOLLO_API_KEY:
        raise HTTPException(status_code=400, detail="APOLLO_API_KEY not configured")

    payload = {"reveal_phone_number": True, "reveal_personal_emails": True}
    if body.get("linkedin_url"):
        payload["linkedin_url"] = body["linkedin_url"]
    elif body.get("email"):
        payload["email"] = body["email"]
    elif body.get("first_name") and body.get("last_name") and body.get("domain"):
        payload["first_name"] = body["first_name"]
        payload["last_name"]  = body["last_name"]
        payload["domain"]     = body["domain"]
    else:
        raise HTTPException(status_code=400,
            detail="Provide one of: linkedin_url, email, or first_name+last_name+domain")

    headers = {
        "X-Api-Key":     APOLLO_API_KEY,
        "Cache-Control": "no-cache",
        "Content-Type":  "application/json",
    }
    try:
        r = req_lib.post(APOLLO_MATCH_URL, headers=headers, json=payload, timeout=30)
        try:
            body_data = r.json()
        except Exception:
            body_data = r.text[:2000]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Apollo request failed: {e}")

    # Surface the parts we care about for diagnosis
    person = (body_data or {}).get("person") if isinstance(body_data, dict) else None
    phones = (person or {}).get("phone_numbers", []) if person else []
    return {
        "apollo_status_code": r.status_code,
        "phone_returned_sync": bool(phones and any(p.get("sanitized_number") or p.get("raw_number") for p in phones)),
        "phones": phones,
        "rate_limit_headers": {k: v for k, v in r.headers.items() if "rate" in k.lower() or "credit" in k.lower()},
        "person_summary": {
            "name": f"{(person or {}).get('first_name','')} {(person or {}).get('last_name','')}".strip(),
            "title": (person or {}).get("title"),
            "email": (person or {}).get("email"),
            "linkedin_url": (person or {}).get("linkedin_url"),
        } if person else None,
        "raw_top_keys": list(body_data.keys()) if isinstance(body_data, dict) else None,
        "error_message": body_data.get("error") if isinstance(body_data, dict) and body_data.get("error") else None,
    }

@app.post("/api/admin/apollo/backfill")
def apollo_backfill(body: dict, user: str = Depends(verify_admin)):
    """Enrich existing unassigned leads that have no DM info. Body: {limit: int}.
    Walks current 'new' + unassigned leads with empty firstName, runs Apollo
    enrichment, and updates Supabase in place. Burns ~1 credit per lead."""
    if not APOLLO_API_KEY:
        raise HTTPException(status_code=400, detail="APOLLO_API_KEY not configured")
    if APOLLO_KILL_SWITCH:
        raise HTTPException(status_code=503, detail="Apollo kill switch is on (APOLLO_KILL_SWITCH=1)")

    limit = max(1, min(int(body.get("limit", 25)), 200))
    # Optional: restrict to one source (e.g. "Health Inspection") so a backfill
    # can target the high-value health leads + use the right DM titles for them.
    src = (body.get("source") or "").strip()
    src_filter = f"&source=eq.{url_quote(src)}" if src else ""
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads"
            f"?firstName=eq.&assignedTo=eq.&status=eq.new{src_filter}"
            f"&select=id,company,firstName,lastName,title,email,phone,notes,score,source"
            f"&order=score.desc&limit={limit}",
            headers=SB_HEADERS, timeout=30,
        )
        rows = r.json() if r.status_code == 200 else []
        if not isinstance(rows, list):
            rows = []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch leads: {e}")

    enriched = 0
    updated_ids = []
    for lead in rows:
        # Healthcare facilities need EVS/admin titles, not Owner/CEO.
        titles = APOLLO_HEALTHCARE_TITLES if lead.get("source") == "Health Inspection" else None
        if not apollo_enrich_lead_in_place(lead, titles=titles):
            continue
        update_payload = {
            "firstName": lead.get("firstName", ""),
            "lastName":  lead.get("lastName", ""),
            "title":     lead.get("title", ""),
            "email":     lead.get("email", ""),
            "phone":     lead.get("phone", ""),
            "notes":     lead.get("notes", ""),
            "score":     lead.get("score"),
            "updatedAt": datetime.utcnow().isoformat(),
        }
        try:
            req_lib.patch(
                f"{SUPABASE_URL}/rest/v1/leads?id=eq.{lead['id']}",
                headers=SB_HEADERS, json=update_payload, timeout=10,
            )
            enriched += 1
            updated_ids.append(lead["id"])
        except Exception as e:
            print(f"[APOLLO-BACKFILL] update failed for {lead.get('id')}: {e}")

    audit_log(user, "apollo_backfill", "lead", None, {
        "checked": len(rows), "enriched": enriched, "limit": limit,
    })
    print(f"[APOLLO-BACKFILL] checked={len(rows)} enriched={enriched}")
    return {"checked": len(rows), "enriched": enriched, "updated_ids": updated_ids[:20]}

def lead_to_apollo_query(lead: dict) -> dict:
    """Build the identifying fields /people/match needs from a stored lead row.
    Priority: email > LinkedIn URL (in notes) > first+last+domain."""
    q = {}
    if lead.get("email"):
        q["email"] = lead["email"]
        return q
    notes = lead.get("notes") or ""
    m = re.search(r"(https?://(?:www\.)?linkedin\.com/in/[^\s|]+)", notes)
    if m:
        q["linkedin_url"] = m.group(1)
        return q
    if lead.get("firstName") and lead.get("lastName"):
        domain = ""
        if lead.get("email") and "@" in lead["email"]:
            domain = lead["email"].split("@")[1]
        elif lead.get("website"):
            domain = lead["website"].replace("https://", "").replace("http://", "").split("/")[0]
        if domain:
            q["first_name"] = lead["firstName"]
            q["last_name"]  = lead["lastName"]
            q["domain"]     = domain
    return q

@app.post("/api/leads/{lead_id}/reveal-phone")
def reveal_phone(lead_id: str, user: str = Depends(verify_token)):
    """Caller-triggered direct phone reveal. Fires async — Apollo posts the
    unlocked phone(s) to /api/webhooks/apollo/{secret}/{lead_id} within ~30s.
    Costs 5-8 credits when Apollo successfully reveals (charged on webhook,
    not on this request)."""
    if not APOLLO_API_KEY or APOLLO_KILL_SWITCH:
        raise HTTPException(status_code=503, detail="Apollo phone reveal unavailable")
    if not APOLLO_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="APOLLO_WEBHOOK_SECRET not configured — phone reveal disabled")

    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(lead_id)}&select=*",
            headers=SB_HEADERS, timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch lead: {e}")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead = rows[0]

    # Policy gate: industry allowlist + monthly budget cap
    policy_ok, policy_reason = can_reveal_phone(lead)
    if not policy_ok:
        return {"requested": False, "blocked_by_policy": True, "message": policy_reason}

    person = lead_to_apollo_query(lead)
    if not person:
        return {"requested": False,
                "message": "Not enough info to identify this person on Apollo (need email or LinkedIn URL — try Find Decision Maker first)"}

    ok, detail = apollo_request_phone_reveal(person, lead_id)
    if not ok:
        return {"requested": False, "message": f"Apollo rejected the request: {detail}"}

    audit_log(user, "reveal_phone_requested", "lead", lead_id, {"company": lead.get("company")})
    _check_apollo_budget_alert()
    return {"requested": True,
            "message": "Apollo is searching — phone will appear within 30 seconds. Refresh the lead to see it."}

@app.post("/api/webhooks/apollo/{secret}/{lead_id}")
@app.get("/api/webhooks/apollo/{secret}/{lead_id}")  # in case Apollo health-checks
async def apollo_phone_webhook(secret: str, lead_id: str, request: Request):
    """Receives async phone reveals from Apollo. Auth via random secret in URL
    path. Audit-logs every hit so we can prove from outside Railway whether
    Apollo ever posted at all."""
    raw_body = ""
    try:
        raw_body = (await request.body()).decode("utf-8", errors="replace")[:2000]
    except Exception:
        pass

    # Always audit, even on auth failure / bad payload — so we can prove receipt.
    audit_log("apollo_webhook", "received", "lead", lead_id, {
        "method":      request.method,
        "secret_ok":   bool(APOLLO_WEBHOOK_SECRET) and secret == APOLLO_WEBHOOK_SECRET,
        "body_len":    len(raw_body),
        "body_sample": raw_body[:500],
    })
    print(f"[APOLLO-WEBHOOK] {request.method} hit for lead={lead_id} body_len={len(raw_body)}")

    if not APOLLO_WEBHOOK_SECRET or secret != APOLLO_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    if request.method == "GET":
        return {"ok": True, "method": "GET (health check)"}

    try:
        data = json_lib.loads(raw_body) if raw_body else {}
    except Exception:
        return {"ok": False, "error": "invalid JSON"}

    # Apollo's actual payload shape (verified from real webhook hit):
    #   {"status":"success", "credits_consumed":8, "people":[{"phone_numbers":[...]}]}
    # Older/other endpoints may send {"person": {...}} or just the person dict.
    person = None
    if isinstance(data.get("people"), list) and data["people"]:
        person = data["people"][0]
    elif isinstance(data.get("person"), dict):
        person = data["person"]
    elif isinstance(data.get("contact"), dict):
        person = data["contact"]
    elif isinstance(data.get("phone_numbers"), list):
        person = data
    if not isinstance(person, dict):
        return {"ok": False, "error": "no person in payload", "raw_keys": list(data.keys()) if isinstance(data, dict) else None}

    # Prefer mobile numbers over other types when multiple are returned
    phone = ""
    phones = person.get("phone_numbers") or []
    mobile = next((p for p in phones if p.get("type_cd") == "mobile"), None)
    chosen = mobile or (phones[0] if phones else None)
    if chosen:
        phone = chosen.get("sanitized_number") or chosen.get("raw_number") or ""

    if not phone:
        print(f"[APOLLO-WEBHOOK] no phone in payload for lead {lead_id}")
        return {"ok": True, "phone": None}

    try:
        req_lib.patch(
            f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(lead_id)}",
            headers=SB_HEADERS,
            json={"phone": phone, "updatedAt": datetime.utcnow().isoformat()},
            timeout=10,
        )
        print(f"[APOLLO-WEBHOOK] updated lead {lead_id} with phone {phone}")
    except Exception as e:
        print(f"[APOLLO-WEBHOOK] update failed for {lead_id}: {e}")

    return {"ok": True, "phone": phone}

@app.get("/api/admin/apollo/budget")
def apollo_budget(user: str = Depends(verify_token)):
    """Surface the phone-reveal policy + current usage so the UI can show
    a budget meter and decide whether to display the Reveal Mobile button.
    Caller-readable so the call modal can hide the button when blocked."""
    used = phone_reveal_count_this_month()
    caller_is_admin = is_admin(user)
    return {
        "monthly_cap":            APOLLO_PHONE_REVEAL_MONTHLY_CAP,
        "used_this_month":        used,
        "remaining":              max(0, APOLLO_PHONE_REVEAL_MONTHLY_CAP - used),
        "allowed_industries":     sorted(APOLLO_PHONE_REVEAL_INDUSTRIES),
        "credits_per_reveal_est": 8,
        # Caller-facing pull policy so the UI can show "N pulls left today"
        # and hide admin-only controls (phone reveal, backfill).
        "is_admin":               caller_is_admin,
        "daily_pull_cap":         None if caller_is_admin else NON_ADMIN_APOLLO_PULL_CAP,
        "pulls_used_today":       0 if caller_is_admin else events_today(user, "apollo_pull"),
    }

@app.get("/api/admin/apollo/webhook-hits")
def webhook_hits(user: str = Depends(verify_admin)):
    """Diagnostic — returns the last 20 audit_log entries from the webhook
    receiver, so we can see if Apollo has actually hit us."""
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/audit_log"
            f"?username=eq.apollo_webhook&select=created_at,resource_id,details"
            f"&order=created_at.desc&limit=20",
            headers=SB_ADMIN_HEADERS, timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
        return {"count": len(rows) if isinstance(rows, list) else 0, "hits": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/leads/{lead_id}/send-to-campaign")
def send_to_campaign(lead_id: str, body: dict, user: str = Depends(verify_token)):
    """Caller-triggered manual send of a lead to VCC's email campaign.
    Body (optional): {campaign: str, trigger: str}. Defaults to tried-to-call/manual."""
    if not RESEND_API_KEY:
        raise HTTPException(status_code=503,
            detail="RESEND_API_KEY not configured — email sending disabled")
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(lead_id)}&select=*",
            headers=SB_HEADERS, timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch lead: {e}")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead = rows[0]

    campaign = (body or {}).get("campaign", "tried-to-call")
    trigger  = (body or {}).get("trigger", "manual")
    ok, detail = send_lead_to_campaign(lead, trigger=trigger, user=user, campaign=campaign)
    if not ok:
        return {"sent": False, "message": detail}
    return {"sent": True, "message": "Sent to VCC", "campaign": campaign, "trigger": trigger}

@app.get("/api/admin/email-template")
def get_email_template(name: str = "tried-to-call", user: str = Depends(verify_admin)):
    """Returns the current saved template + the default + the placeholder
    list so the editor UI can show everything it needs to render."""
    saved = load_email_template(name)
    return {
        "name":     name,
        "is_default": saved == DEFAULT_EMAIL_TEMPLATE,
        "subject":  saved.get("subject"),
        "body":     saved.get("body"),
        "default": {
            "subject": DEFAULT_EMAIL_TEMPLATE["subject"],
            "body":    DEFAULT_EMAIL_TEMPLATE["body"],
        },
        # Documented placeholder list shown in the editor's reference panel
        "placeholders": [
            {"key": "first_name",      "desc": "Lead's first name (falls back to 'there')"},
            {"key": "last_name",       "desc": "Lead's last name"},
            {"key": "full_name",       "desc": "First + last (falls back to 'there')"},
            {"key": "company",         "desc": "Company name (falls back to 'your facility')"},
            {"key": "title",           "desc": "Job title — Facility Manager, etc."},
            {"key": "industry",        "desc": "Raw industry string"},
            {"key": "industry_phrase", "desc": "'<industry> facilities' or 'commercial facilities'"},
            {"key": "state",           "desc": "Two-letter state code or full name"},
            {"key": "state_phrase",    "desc": "' across <state>' or empty string"},
            {"key": "city",            "desc": "City"},
            {"key": "sender_name",     "desc": "From OUTREACH_SENDER_NAME env var"},
            {"key": "sender_email",    "desc": "From OUTREACH_EMAIL env var"},
            {"key": "sender_phone",    "desc": "From OUTREACH_SENDER_PHONE env var"},
            {"key": "sender_line",     "desc": "Pre-formatted phone + email signature line"},
        ],
    }

@app.put("/api/admin/email-template")
def save_email_template(body: dict, user: str = Depends(verify_admin)):
    """Save an edited template. Body: {name?, subject, body}.
    Empty subject/body strings are rejected so a fat-finger save doesn't
    silently send blank emails."""
    name    = (body.get("name") or "tried-to-call").strip()
    subject = (body.get("subject") or "").strip()
    text    = (body.get("body") or "").strip()
    if not subject or not text:
        raise HTTPException(status_code=400, detail="Both subject and body are required")
    if len(subject) > 200:
        raise HTTPException(status_code=400, detail="Subject too long (max 200 chars)")

    payload = {
        "key":   f"email_template_{name}",
        "value": json_lib.dumps({"subject": subject, "body": text}),
    }
    # Service-role headers bypass RLS (the anon key has no INSERT/UPDATE
    # policy for this key prefix). on_conflict=key tells Supabase to merge
    # on the unique 'key' column instead of inserting a duplicate row.
    try:
        r = req_lib.post(
            f"{SUPABASE_URL}/rest/v1/app_settings?on_conflict=key",
            headers={**SB_ADMIN_HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"},
            json=payload, timeout=10,
        )
        if r.status_code not in (200, 201, 204):
            raise HTTPException(status_code=500, detail=f"Save failed: {r.text[:200]}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Save failed: {e}")

    audit_log(user, "email_template_saved", "template", name, {
        "subject_len": len(subject), "body_len": len(text),
    })
    return {"ok": True, "name": name}

@app.delete("/api/admin/email-template")
def reset_email_template(name: str = "tried-to-call", user: str = Depends(verify_admin)):
    """Reset to default by deleting the override row in app_settings.
    Next send falls back to DEFAULT_EMAIL_TEMPLATE."""
    try:
        req_lib.delete(
            f"{SUPABASE_URL}/rest/v1/app_settings?key=eq.email_template_{name}",
            headers=SB_ADMIN_HEADERS, timeout=10,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reset failed: {e}")
    audit_log(user, "email_template_reset", "template", name, {})
    return {"ok": True, "reset_to": "default"}

@app.post("/api/admin/email-template/preview")
def preview_email_template(body: dict, user: str = Depends(verify_admin)):
    """Render a draft template against either a real lead or a sample.
    Body: {subject, body, lead_id?}. If lead_id given, pulls that lead;
    otherwise uses a built-in sample so the editor can show a live preview
    even without a lead handy."""
    subject = body.get("subject") or ""
    text    = body.get("body") or ""
    lead_id = body.get("lead_id")

    lead = None
    if lead_id:
        try:
            r = req_lib.get(
                f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(str(lead_id))}&select=*",
                headers=SB_HEADERS, timeout=10,
            )
            rows = r.json() if r.status_code == 200 else []
            lead = rows[0] if isinstance(rows, list) and rows else None
        except Exception:
            lead = None
    if not lead:
        lead = {
            "firstName": "Sarah", "lastName": "Chen",
            "company":   "Atlas Healthcare", "title": "Facility Manager",
            "industry":  "Healthcare", "state": "AZ", "city": "Phoenix",
        }

    vars_ = _email_template_vars(lead)
    return {
        "subject":   _render_email_template(subject, vars_),
        "body_text": _render_email_template(text, vars_),
        "vars_used": vars_,
        "lead_used": {
            "id":        lead.get("id"),
            "name":      f"{lead.get('firstName','')} {lead.get('lastName','')}".strip(),
            "company":   lead.get("company"),
            "title":     lead.get("title"),
            "is_sample": lead.get("id") is None,
        },
    }

@app.get("/api/admin/email/setup-status")
def email_setup_status(user: str = Depends(verify_admin)):
    """One-stop diagnostic for the email + reply pipeline. Shows what's
    configured, what's missing, and the exact webhook URL to paste into
    Resend dashboard. Designed to be the only page admin needs while wiring."""
    app_url = os.getenv("APP_URL", "https://leadflow-railway-production.up.railway.app").rstrip("/")
    secret  = RESEND_WEBHOOK_SECRET or ""
    safe_secret = url_quote(secret, safe="") if secret else ""
    webhook_url = f"{app_url}/api/webhooks/resend/{safe_secret}" if secret else None

    last = _imap_poll_state.get("last_run")
    last_result = _imap_poll_state.get("last_result")

    return {
        "send_path": {
            "resend_api_key_set":      bool(RESEND_API_KEY),
            "from_address":            f"{os.getenv('OUTREACH_SENDER_NAME', OUTREACH_NAME)} <{OUTREACH_EMAIL}>",
            "reply_to":                OUTREACH_REPLY_TO or OUTREACH_EMAIL,
            "ready":                   bool(RESEND_API_KEY),
        },
        "webhook_path": {
            "resend_webhook_secret_set": bool(secret),
            "url_for_resend_dashboard":  webhook_url,
            "url_starts_with_https":     bool(webhook_url and webhook_url.startswith("https://")),
            "ready":                     bool(secret),
        },
        "reply_path": {
            "imap_configured":           bool(IMAP_SERVER and IMAP_USERNAME and IMAP_PASSWORD),
            "imap_server":               IMAP_SERVER or None,
            "imap_username":             IMAP_USERNAME or None,
            "imap_folder":               IMAP_FOLDER,
            "poll_interval_minutes":     IMAP_POLL_INTERVAL_MINUTES,
            "last_poll_run_at":          last,
            "last_poll_result":          last_result,
            "ready":                     bool(IMAP_SERVER and IMAP_USERNAME and IMAP_PASSWORD),
        },
        "all_ready": bool(RESEND_API_KEY and secret and IMAP_SERVER and IMAP_USERNAME and IMAP_PASSWORD),
    }

@app.get("/api/leads/emailed-flags")
def emailed_flags(days: int = 90, user: str = Depends(verify_token)):
    """Returns a per-lead-id map of email history so the leads list can
    show a persistent '📧 emailed' badge that survives status changes.
    Caller-readable so reps see the badge in the Leads tab too. Pulled
    from audit_log (action=campaign_sent) — no schema migration needed."""
    days = max(1, min(int(days), 365))
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/audit_log"
            f"?action=eq.campaign_sent&created_at=gte.{since}"
            f"&select=resource_id,created_at"
            f"&order=created_at.desc&limit=10000",
            headers=SB_ADMIN_HEADERS, timeout=15,
        )
        rows = r.json() if r.status_code == 200 else []
        if not isinstance(rows, list):
            rows = []
    except Exception as e:
        print(f"[EMAILED-FLAGS] query failed: {e}")
        return {"flags": {}, "window_days": days}

    # Aggregate: most recent send (rows are desc) + count per lead.
    flags = {}
    for row in rows:
        lid = row.get("resource_id")
        if not lid:
            continue
        key = str(lid)
        if key not in flags:
            flags[key] = {"last_emailed_at": row.get("created_at"), "email_count": 1}
        else:
            flags[key]["email_count"] += 1
    return {"flags": flags, "window_days": days, "total_leads_emailed": len(flags)}

@app.get("/api/admin/campaigns/eligible")
def campaigns_eligible(window_days: int = 7, user: str = Depends(verify_admin)):
    """Returns leads with recent no-answer/voicemail outcomes that haven't
    yet been emailed and meet the prerequisites: email + firstName populated,
    not currently awaiting a reply, not within the suppression window.
    Designed for the EOD batch-send queue — admin reviews + bulk-fires
    instead of relying on callers to check the per-call box."""
    if not RESEND_API_KEY:
        return {"eligible": [], "count": 0, "window_days": window_days,
                "error": "RESEND_API_KEY not configured"}

    window_days = max(1, min(int(window_days), 30))
    since_dt = datetime.utcnow() - timedelta(days=window_days)
    since = since_dt.isoformat()

    # 1. Recent failed-call records in window
    try:
        cr = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/call_outcomes"
            f"?outcome=in.(no_answer,voicemail)"
            f"&calledAt=gte.{since}"
            f"&select=id,leadId,outcome,calledAt,calledBy,notes"
            f"&order=calledAt.desc&limit=500",
            headers=SB_HEADERS, timeout=30,
        )
        calls = cr.json() if cr.status_code == 200 else []
        if not isinstance(calls, list):
            calls = []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch calls: {e}")

    # Group by lead — we want the MOST RECENT failed call per lead, not every
    # past attempt. The order=desc above means the first hit per leadId wins.
    by_lead = {}
    for c in calls:
        lid = c.get("leadId")
        if lid and lid not in by_lead:
            by_lead[lid] = c
    if not by_lead:
        return {"eligible": [], "count": 0, "window_days": window_days}

    # 2. Suppression list — leads already campaign_sent within
    # CAMPAIGN_SUPPRESSION_DAYS. We pull this once instead of per-lead so the
    # eligibility check stays a single batch.
    sup_cutoff = (datetime.utcnow() - timedelta(days=CAMPAIGN_SUPPRESSION_DAYS)).isoformat()
    try:
        sr = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/audit_log"
            f"?action=eq.campaign_sent&created_at=gte.{sup_cutoff}"
            f"&select=resource_id&limit=10000",
            headers=SB_ADMIN_HEADERS, timeout=10,
        )
        rows = sr.json() if sr.status_code == 200 else []
        suppressed = {str(r_.get("resource_id")) for r_ in (rows if isinstance(rows, list) else []) if r_.get("resource_id")}
    except Exception as e:
        print(f"[ELIGIBLE] suppression query failed: {e}")
        suppressed = set()

    # 3. Fetch each lead — batched in chunks of 100 since id=in.() URL has length limits
    lead_ids = [lid for lid in by_lead.keys() if str(lid) not in suppressed]
    leads_by_id = {}
    BATCH = 100
    for i in range(0, len(lead_ids), BATCH):
        batch = lead_ids[i:i+BATCH]
        ids_csv = ",".join(str(x) for x in batch)
        try:
            r = req_lib.get(
                f"{SUPABASE_URL}/rest/v1/leads?id=in.({ids_csv})"
                f"&select=id,company,firstName,lastName,title,email,phone,industry,state,city,status,assignedTo",
                headers=SB_HEADERS, timeout=30,
            )
            for l in (r.json() if r.status_code == 200 else []):
                if isinstance(l, dict):
                    leads_by_id[l["id"]] = l
        except Exception as e:
            print(f"[ELIGIBLE] batch lead fetch failed: {e}")

    # 4. Filter to email-eligible
    eligible = []
    for lead_id, call in by_lead.items():
        if str(lead_id) in suppressed:
            continue
        lead = leads_by_id.get(lead_id)
        if not lead:
            continue
        if not (lead.get("email") or "").strip():
            continue
        if not (lead.get("firstName") or "").strip():
            continue
        if lead.get("status") in ("awaiting_email_reply", "do_not_contact", "converted"):
            continue
        eligible.append({
            "lead_id":      lead["id"],
            "company":      lead.get("company"),
            "first_name":   lead.get("firstName"),
            "last_name":    lead.get("lastName") or "",
            "title":        lead.get("title") or "",
            "email":        lead.get("email"),
            "phone":        lead.get("phone") or "",
            "industry":     lead.get("industry") or "",
            "state":        lead.get("state") or "",
            "city":         lead.get("city") or "",
            "status":       lead.get("status") or "new",
            "assigned_to":  lead.get("assignedTo") or "",
            "last_outcome": call.get("outcome"),
            "last_call_at": call.get("calledAt"),
            "last_call_by": call.get("calledBy"),
        })

    eligible.sort(key=lambda x: x.get("last_call_at") or "", reverse=True)
    return {"eligible": eligible, "count": len(eligible), "window_days": window_days}

@app.post("/api/admin/campaigns/batch-send")
def campaigns_batch_send(body: dict, user: str = Depends(verify_admin)):
    """Send the campaign email to a list of leads in one shot. Body:
       {lead_ids: [int, ...], trigger?: str}
    Each lead is processed via send_lead_to_campaign which handles its own
    suppression + status flip — so calling this twice with the same list
    is idempotent within the suppression window."""
    if not RESEND_API_KEY:
        raise HTTPException(status_code=400, detail="RESEND_API_KEY not configured")

    lead_ids = body.get("lead_ids") or []
    if not isinstance(lead_ids, list) or len(lead_ids) == 0:
        return {"sent": 0, "failed": 0, "skipped": 0, "details": []}
    trigger = (body.get("trigger") or "batch_eod").strip() or "batch_eod"

    sent = 0
    failed = []
    skipped = []
    for lid in lead_ids[:500]:  # hard cap so a fat-fingered request can't fan out forever
        try:
            r = req_lib.get(
                f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(str(lid))}&select=*",
                headers=SB_HEADERS, timeout=10,
            )
            rows = r.json() if r.status_code == 200 else []
            lead = rows[0] if isinstance(rows, list) and rows else None
        except Exception as e:
            failed.append({"lead_id": lid, "reason": f"fetch error: {e}"})
            continue
        if not lead:
            failed.append({"lead_id": lid, "reason": "lead not found"})
            continue

        ok, detail = send_lead_to_campaign(lead, trigger=trigger, user=user)
        if ok:
            sent += 1
        elif "already sent" in (detail or "").lower():
            skipped.append({"lead_id": lid, "company": lead.get("company"), "reason": detail})
        else:
            failed.append({"lead_id": lid, "company": lead.get("company"), "reason": detail})

    audit_log(user, "campaigns_batch_sent", "campaign", None, {
        "trigger":  trigger,
        "requested": len(lead_ids),
        "sent":     sent,
        "skipped":  len(skipped),
        "failed":   len(failed),
    })
    return {
        "sent":     sent,
        "skipped":  len(skipped),
        "failed":   len(failed),
        "skips":    skipped[:30],
        "failures": failed[:30],
    }

@app.post("/api/admin/leads/release-stale-emails")
def manual_release_stale_emails(user: str = Depends(verify_admin)):
    """Manual trigger for stale-email release. Same logic the bg loop runs
    every 10 min — useful for an immediate "unblock everyone" action."""
    released = release_stale_email_leads()
    return {"released": released, "cutoff_days": CAMPAIGN_SUPPRESSION_DAYS}

@app.get("/api/admin/email-sequence/status")
def email_sequence_status(user: str = Depends(verify_admin)):
    """Live counts of leads at each step of the 3-touch email sequence so
    the admin UI can show 'drip is queued' state. Counts only leads in
    awaiting_email_reply with followupsequence='email_followup'.

      step_1 — Touch 1 sent, Touch 2 next
      step_2 — Touch 2 sent, Touch 3 next
      step_3 — Touch 3 sent, release next
      legacy — followupstep IS NULL (pre-sequence-feature data)"""
    rows = _paginated_get(
        f"{SUPABASE_URL}/rest/v1/leads"
        f"?status=eq.awaiting_email_reply"
        f"&select=followupsequence,followupstep,nextfollowup"
    )
    buckets = {1: [], 2: [], 3: [], 0: []}  # 0 = legacy
    for l in rows:
        if (l.get("followupsequence") or "") != "email_followup":
            continue
        step = l.get("followupstep")
        if step in (1, 2, 3):
            buckets[step].append(l)
        else:
            buckets[0].append(l)

    def summarize(rs):
        dates = sorted([r.get("nextfollowup") for r in rs if r.get("nextfollowup")])
        return {"count": len(rs), "next_due": dates[0] if dates else None}

    return {
        "step_1": summarize(buckets[1]),  # at step 1 → next event is Touch 2
        "step_2": summarize(buckets[2]),  # at step 2 → next event is Touch 3
        "step_3": summarize(buckets[3]),  # at step 3 → next event is release
        "legacy": {"count": len(buckets[0])},
        "total":  sum(len(v) for v in buckets.values()),
    }

@app.post("/api/admin/email-sequence/backfill")
def email_sequence_backfill(execute: bool = False, user: str = Depends(verify_admin)):
    """Retroactively put legacy leads (followupsequence='email_followup',
    followupstep IS NULL) onto the new 3-touch pipeline. Sets followupstep=1
    and aligns nextfollowup to (Touch1 date + 3 days), or tomorrow if that's
    already past. The bg-loop sequencer then fires Touch 2 and Touch 3 on
    the standard cadence and releases at the end.

    Dry-run by default — pass ?execute=true to apply. Idempotent (only
    targets rows where followupstep IS NULL, so re-running is safe)."""
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads"
            f"?status=eq.awaiting_email_reply"
            f"&followupsequence=eq.email_followup"
            f"&followupstep=is.null"
            f"&select=id,company,firstName,lastName,email,updatedAt,nextfollowup"
            f"&limit=1000",
            headers=SB_HEADERS, timeout=15,
        )
        candidates = r.json() if r.status_code == 200 else []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not isinstance(candidates, list) or not candidates:
        return {"candidates": 0, "message": "No legacy leads need backfill"}

    today = datetime.utcnow().date()
    tomorrow = today + timedelta(days=1)

    plan = []  # one entry per lead
    for l in candidates:
        # updatedAt was set by send_lead_to_campaign on the original Touch 1
        # send. Use it as Touch 1's date; if it's been edited since, the
        # delta will be smaller and Touch 2 fires sooner — acceptable.
        try:
            updated_iso = (l.get("updatedAt") or "").replace("Z", "").split("+")[0]
            touch1_date = datetime.fromisoformat(updated_iso).date() if updated_iso else today
        except Exception:
            touch1_date = today
        ideal_t2 = touch1_date + timedelta(days=3)
        next_fu  = max(ideal_t2, tomorrow)
        plan.append({
            "id":               l.get("id"),
            "company":          l.get("company"),
            "firstName":        l.get("firstName"),
            "touch1_date":      touch1_date.isoformat(),
            "touch1_age_days":  (today - touch1_date).days,
            "next_touch":       next_fu.isoformat(),
        })

    if not execute:
        return {
            "candidates": len(plan),
            "rule":       "set followupstep=1, nextfollowup=max(touch1+3d, tomorrow)",
            "plan":       plan[:30],
            "execute_with": "POST /api/admin/email-sequence/backfill?execute=true",
        }

    # Execute — patch leads individually since each gets a different nextfollowup.
    now_iso = datetime.utcnow().isoformat()
    updated = 0
    failed  = 0
    for entry in plan:
        try:
            pr = req_lib.patch(
                f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(str(entry['id']))}",
                headers={**SB_HEADERS, "Prefer": "return=minimal"},
                json={
                    "followupstep": 1,
                    "nextfollowup": entry["next_touch"],
                    "updatedAt":    now_iso,
                },
                timeout=10,
            )
            if pr.status_code in (200, 204):
                updated += 1
            else:
                failed += 1
                print(f"[BACKFILL] patch {entry['id']} HTTP {pr.status_code}: {pr.text[:200]}")
        except Exception as e:
            failed += 1
            print(f"[BACKFILL] patch {entry['id']} exception: {e}")

    audit_log(user, "email_sequence_backfill", "lead", None, {
        "candidates": len(plan), "updated": updated, "failed": failed,
    })
    return {"candidates": len(plan), "updated": updated, "failed": failed,
            "next_sequencer_tick": "within 10 min"}

@app.post("/api/admin/poll-replies")
def poll_replies_now(user: str = Depends(verify_admin)):
    """Manual trigger for the IMAP reply poller — useful when you want an
    immediate check rather than waiting for the next 10-min background pass."""
    if not (IMAP_SERVER and IMAP_USERNAME and IMAP_PASSWORD):
        raise HTTPException(status_code=503,
            detail="IMAP not configured. Set IMAP_SERVER, IMAP_USERNAME, IMAP_PASSWORD in Railway.")
    return imap_poll_replies()

@app.get("/api/admin/campaigns/recent")
def campaigns_recent(days: int = 14, user: str = Depends(verify_admin)):
    """Returns recent campaign activity for the admin panel — sends and events
    grouped, with totals so you can verify the flow is working."""
    days = max(1, min(int(days), 90))
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    try:
        # Sends
        sr = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/audit_log"
            f"?action=eq.campaign_sent&created_at=gte.{since}"
            f"&select=resource_id,details,created_at,username"
            f"&order=created_at.desc&limit=200",
            headers=SB_ADMIN_HEADERS, timeout=15,
        )
        sends = sr.json() if sr.status_code == 200 else []
        if not isinstance(sends, list): sends = []

        # Events (replies/bounces/opens/clicks/unsubs from VCC callbacks)
        er = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/audit_log"
            f"?action=eq.campaign_event&created_at=gte.{since}"
            f"&select=resource_id,details,created_at"
            f"&order=created_at.desc&limit=200",
            headers=SB_ADMIN_HEADERS, timeout=15,
        )
        events = er.json() if er.status_code == 200 else []
        if not isinstance(events, list): events = []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Activity query failed: {e}")

    # Tally event counts by type for the headline numbers
    event_counts = {"reply": 0, "open": 0, "click": 0, "bounce": 0, "unsubscribe": 0, "other": 0}
    for ev in events:
        try:
            d = json_lib.loads(ev.get("details") or "{}") if isinstance(ev.get("details"), str) else (ev.get("details") or {})
            etype = (d.get("event") or "other").lower()
            event_counts[etype if etype in event_counts else "other"] += 1
        except Exception:
            event_counts["other"] += 1

    return {
        "window_days":  days,
        "email_configured": bool(RESEND_API_KEY),
        "imap_configured":  bool(IMAP_SERVER and IMAP_USERNAME and IMAP_PASSWORD),
        "totals": {
            "sends":   len(sends),
            "replies": event_counts["reply"],
            "opens":   event_counts["open"],
            "clicks":  event_counts["click"],
            "bounces": event_counts["bounce"],
            "unsubs":  event_counts["unsubscribe"],
        },
        "reply_rate_pct": round((event_counts["reply"] / len(sends)) * 100, 1) if sends else 0.0,
        "recent_sends":   sends[:20],
        "recent_events":  events[:20],
    }

@app.get("/api/leads/{lead_id}/campaign-status")
def campaign_status(lead_id: str, user: str = Depends(verify_token)):
    """Return the most recent campaign sends + replies/events for a lead so
    the call modal can show a 'in campaign' badge or 'replied' status."""
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/audit_log"
            f"?resource_id=eq.{url_quote(str(lead_id))}"
            f"&action=in.(campaign_sent,campaign_event)"
            f"&select=action,details,created_at"
            f"&order=created_at.desc&limit=10",
            headers=SB_ADMIN_HEADERS, timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
        if not isinstance(rows, list):
            rows = []
        sent_rows  = [r_ for r_ in rows if r_.get("action") == "campaign_sent"]
        event_rows = [r_ for r_ in rows if r_.get("action") == "campaign_event"]
        return {
            "in_campaign":   bool(sent_rows),
            "last_sent_at":  sent_rows[0]["created_at"] if sent_rows else None,
            "send_count":    len(sent_rows),
            "events":        event_rows[:5],
            "email_configured": bool(RESEND_API_KEY),
        "imap_configured":  bool(IMAP_SERVER and IMAP_USERNAME and IMAP_PASSWORD),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/webhooks/resend/{secret}")
async def resend_webhook(secret: str, request: Request):
    """Receives email events from Resend (delivered/opened/clicked/bounced/
    complained). lead_id is in the email's `tags` we set on send. Auth via
    secret in URL path. Configure URL + this secret in Resend dashboard."""
    if not RESEND_WEBHOOK_SECRET or secret != RESEND_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    raw_body = ""
    try:
        raw_body = (await request.body()).decode("utf-8", errors="replace")[:3000]
        data = json_lib.loads(raw_body) if raw_body else {}
    except Exception:
        data = {}

    # Resend payload shape: {type: "email.opened", created_at, data: {email_id, to, tags: [...], ...}}
    etype  = (data.get("type") or "").lower()
    payload = data.get("data") or {}
    tags = {t.get("name"): t.get("value") for t in (payload.get("tags") or []) if isinstance(t, dict)}
    lead_id = tags.get("lead_id")

    # Map Resend event types to our canonical event names so the activity panel
    # can keep its existing category tallies.
    event_map = {
        "email.delivered":      "delivered",
        "email.opened":         "open",
        "email.clicked":        "click",
        "email.bounced":        "bounce",
        "email.complained":     "complaint",
        "email.unsubscribed":   "unsubscribe",
        "email.delivery_delayed":"delayed",
        "email.failed":         "failed",
    }
    event = event_map.get(etype, etype.replace("email.", "") or "other")

    audit_log("resend_webhook", "campaign_event", "lead", lead_id, {
        "event": event, "resend_type": etype, "email_id": payload.get("email_id"),
    })
    print(f"[RESEND-WEBHOOK] lead={lead_id} event={event} ({etype})")

    if not lead_id:
        return {"ok": True, "no_lead_id": True}

    # Bounce → clear email so we don't keep emailing into the void
    if event == "bounce":
        try:
            r = req_lib.get(
                f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(lead_id)}&select=notes",
                headers=SB_HEADERS, timeout=10,
            )
            existing = (r.json() or [{}])[0] if r.status_code == 200 else {}
            req_lib.patch(
                f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(lead_id)}",
                headers=SB_HEADERS,
                json={"email": "", "updatedAt": datetime.utcnow().isoformat(),
                      "notes": (existing.get("notes") or "") + f" | Email bounced {datetime.utcnow().date().isoformat()}"},
                timeout=10,
            )
        except Exception as e:
            print(f"[RESEND-WEBHOOK] bounce update failed: {e}")

    elif event in ("complaint", "unsubscribe"):
        try:
            req_lib.patch(
                f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(lead_id)}",
                headers=SB_HEADERS,
                json={"status": "do_not_contact", "updatedAt": datetime.utcnow().isoformat()},
                timeout=10,
            )
        except Exception as e:
            print(f"[RESEND-WEBHOOK] do_not_contact update failed: {e}")

    # opens + clicks + delivered: audit-log only (already done above)

    return {"ok": True, "event": event}

@app.post("/api/leads/{lead_id}/find-dm")
def find_dm(lead_id: str, user: str = Depends(verify_token)):
    """Caller-triggered Apollo enrichment for a single lead. Returns the
    updated lead so the call modal can refresh inline."""
    if not APOLLO_API_KEY or APOLLO_KILL_SWITCH:
        raise HTTPException(status_code=503, detail="Apollo enrichment unavailable")
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(lead_id)}&select=*",
            headers=SB_HEADERS, timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch lead: {e}")

    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead = rows[0]

    if not apollo_enrich_lead_in_place(lead):
        return {"enriched": False, "lead": lead, "message": "No Apollo match found for this company"}

    update_payload = {
        "firstName": lead.get("firstName", ""),
        "lastName":  lead.get("lastName", ""),
        "title":     lead.get("title", ""),
        "email":     lead.get("email", ""),
        "phone":     lead.get("phone", ""),
        "notes":     lead.get("notes", ""),
        "score":     lead.get("score"),
        "updatedAt": datetime.utcnow().isoformat(),
    }
    try:
        req_lib.patch(
            f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(lead_id)}",
            headers=SB_HEADERS, json=update_payload, timeout=10,
        )
    except Exception as e:
        print(f"[APOLLO-FIND-DM] update failed for {lead_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Update failed: {e}")

    audit_log(user, "find_dm", "lead", lead_id, {
        "company": lead.get("company"), "enriched": True,
    })
    return {"enriched": True, "lead": lead}

@app.post("/api/leads/reassign")
def reassign_leads(body: dict, user: str = Depends(verify_admin)):
    """Bulk reassign leads from one rep to another (or unassign to pool)"""
    try:
        from_rep = body.get("from", "")
        to_rep = body.get("to", "")  # empty string = back to pool
        if not from_rep:
            raise HTTPException(status_code=400, detail="'from' rep is required")
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads?select=id&assignedTo=eq.{from_rep}",
            headers=SB_HEADERS, timeout=30)
        leads = r.json() if r.status_code == 200 else []
        if not isinstance(leads, list) or not leads:
            return {"reassigned": 0, "message": f"No leads assigned to {from_rep}"}
        # Bulk reassign in one request
        ids_filter = ",".join(str(l["id"]) for l in leads)
        req_lib.patch(
            f"{SUPABASE_URL}/rest/v1/leads?id=in.({ids_filter})",
            headers=SB_HEADERS,
            json={"assignedTo": to_rep, "updatedAt": datetime.utcnow().isoformat()},
            timeout=30)
        count = len(leads)
        dest = to_rep if to_rep else "unassigned pool"
        audit_log(user, "reassign_leads", "lead", None, {"from": from_rep, "to": dest, "count": count})
        return {"reassigned": count, "from": from_rep, "to": dest}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/leads/assign-ids")
def assign_leads_by_ids(body: dict, user: str = Depends(verify_admin)):
    """Bulk-assign a specific set of leads (by id) to a rep, or to the pool if
    to='' . Powers the 'Assign these to…' action on a filtered Leads view —
    lets the admin hand a whole segment (e.g. the new Las Vegas pull) to a
    caller in one click."""
    try:
        ids = [str(i) for i in (body.get("ids") or []) if str(i).strip()]
        to_rep = (body.get("to") or "").strip()
        if not ids:
            return {"assigned": 0, "message": "No leads selected"}
        now = datetime.utcnow().isoformat()
        assigned = 0
        CHUNK = 100
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i:i+CHUNK]
            ids_filter = ",".join(chunk)
            rr = req_lib.patch(
                f"{SUPABASE_URL}/rest/v1/leads?id=in.({ids_filter})",
                headers=SB_HEADERS,
                json={"assignedTo": to_rep, "updatedAt": now},
                timeout=30)
            if rr.status_code in (200, 204):
                assigned += len(chunk)
        audit_log(user, "assign_leads_by_ids", "lead", None, {"to": to_rep or "pool", "count": assigned})
        return {"assigned": assigned, "to": to_rep or "unassigned pool"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/calls/flagged")
def get_flagged_calls(user: str = Depends(verify_admin)):
    """Admin-only: get calls with anti-gaming flags"""
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/call_outcomes"
            f"?select=*&follow_up_outcome=neq.&follow_up_outcome=not.is.null"
            f"&order=calledAt.desc&limit=200",
            headers=SB_HEADERS, timeout=30)
        calls = r.json() if r.status_code == 200 else []
        if not isinstance(calls, list):
            return []
        # Only return calls that have our gaming flags
        gaming_flags = {"empty_form", "duplicate_cooldown", "rapid_cadence"}
        flagged = [c for c in calls if any(f in (c.get("follow_up_outcome") or "") for f in gaming_flags)]
        return flagged
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/reps")
def get_reps(user: str = Depends(verify_admin)):
    """Get all reps with their lead counts and last activity"""
    try:
        # All assigned leads
        r1 = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads?select=assignedTo",
            headers=SB_HEADERS, timeout=30)
        leads = r1.json() if r1.status_code == 200 else []
        # All calls for last activity
        r2 = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/call_outcomes?select=calledBy,calledAt&order=calledAt.desc",
            headers=SB_HEADERS, timeout=30)
        calls = r2.json() if r2.status_code == 200 else []

        reps = {}
        for l in (leads if isinstance(leads, list) else []):
            name = l.get("assignedTo") or ""
            if name:
                reps.setdefault(name, {"name": name, "leads": 0, "last_call": None})
                reps[name]["leads"] += 1
        for c in (calls if isinstance(calls, list) else []):
            name = c.get("calledBy") or ""
            if name:
                reps.setdefault(name, {"name": name, "leads": 0, "last_call": None})
                if not reps[name]["last_call"]:
                    reps[name]["last_call"] = c.get("calledAt")

        now = datetime.utcnow()
        result = []
        for rep in reps.values():
            lc = rep["last_call"]
            if lc:
                try:
                    last_dt = datetime.fromisoformat(lc.replace("+00:00", "").replace("Z", ""))
                    days_inactive = (now - last_dt).days
                except:
                    days_inactive = 999
            else:
                days_inactive = 999
            rep["days_inactive"] = days_inactive
            rep["status"] = "active" if days_inactive <= 3 else "idle" if days_inactive <= 7 else "inactive"
            result.append(rep)

        result.sort(key=lambda x: (-x["leads"], x["name"]))
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/calls/{lead_id}")
def get_calls(lead_id: str, user: str = Depends(verify_token)):
    try:
        r = req_lib.get(f"{SUPABASE_URL}/rest/v1/call_outcomes?leadId=eq.{lead_id}&order=calledAt.desc",
                       headers=SB_HEADERS, timeout=30)
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stats")
def get_stats(user: str = Depends(verify_token)):
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        # Paginate — Supabase caps single requests at 1000 rows, so without
        # this the 'total' field undercounts once the DB grows past 1000.
        sl = _paginated_get(f"{SUPABASE_URL}/rest/v1/leads?select=status,score,callbackDate,createdAt")
        r2 = req_lib.get(f"{SUPABASE_URL}/rest/v1/call_outcomes?select=outcome,calledBy&calledAt=gte.{today}T00:00:00",
                        headers=SB_HEADERS, timeout=30)
        sc = r2.json() if r2.status_code == 200 else []
        total     = len(sl)
        converted = len([l for l in sl if l.get("status")=="converted"])
        return {
            "total": total,
            "newToday": len([l for l in sl if (l.get("createdAt","")).startswith(today)]),
            "interested": len([l for l in sl if l.get("status")=="interested"]),
            "converted": converted,
            "callbacksDue": len([l for l in sl if l.get("callbackDate","")<=today and l.get("callbackDate") and l.get("status")!="converted"]),
            "callsToday": len(sc),
            "conversionRate": f"{(converted/total*100):.1f}" if total else "0.0",
            "contactRate": f"{(len([c for c in sc if c.get('outcome') in ('answered','interested','interested_no_dm','converted','callback','not_interested')])/len(sc)*100):.1f}" if sc else "0.0",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def _theme_tally(calls):
    """Count CALL_NOTE_THEMES across a list of calls (by their notes)."""
    from collections import Counter
    t = Counter()
    for c in (calls or []):
        nl = (c.get("notes") or "").lower()
        if not nl:
            continue
        for theme, kws in CALL_NOTE_THEMES.items():
            if any(k in nl for k in kws):
                t[theme] += 1
    return t

def _interest_rate(calls):
    """% of calls that reached interest (interested/converted/callback)."""
    calls = calls or []
    if not calls:
        return 0.0
    inter = sum(1 for c in calls if c.get("outcome") in
                ("interested", "interested_no_dm", "converted", "callback"))
    return round(inter / len(calls) * 100, 1)

def _trend_arrow(now, prev):
    if prev == 0:
        return "🆕" if now else "→"
    d = now - prev
    return "↑" if d > 0 else "↓" if d < 0 else "→"

@app.get("/api/daily-summary")
def daily_summary():
    """Send end-of-day summary to Slack. Triggered by Railway cron or manually.
    Includes objection patterns + week-over-week trend (the seasonality base)."""
    try:
        now = datetime.utcnow()
        today = now.strftime("%Y-%m-%d")
        wk_start = (now - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
        prior_start = (now - timedelta(days=14)).strftime("%Y-%m-%dT00:00:00")

        # Calls today (with notes for theme tally)
        r_calls = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/call_outcomes?select=outcome,calledBy,notes,vendorstatus&calledAt=gte.{today}T00:00:00",
            headers=SB_HEADERS, timeout=30)
        calls = r_calls.json() if r_calls.status_code == 200 else []

        # This week + prior week (for week-over-week trend → seasonality over time)
        wk = _paginated_get(f"{SUPABASE_URL}/rest/v1/call_outcomes?select=outcome,notes,calledAt&calledAt=gte.{wk_start}")
        prior = _paginated_get(
            f"{SUPABASE_URL}/rest/v1/call_outcomes?select=outcome,notes,calledAt"
            f"&calledAt=gte.{prior_start}&calledAt=lt.{wk_start}")

        # Leads created today
        r_leads = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads?select=id,createdBy&createdAt=gte.{today}T00:00:00",
            headers=SB_HEADERS, timeout=30)
        new_leads = r_leads.json() if r_leads.status_code == 200 else []

        # Emails sent today
        r_emails = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/email_log?select=id,sent_by&sent_at=gte.{today}T00:00:00",
            headers=SB_HEADERS, timeout=30)
        emails = r_emails.json() if r_emails.status_code == 200 else []

        # Callbacks due
        r_cb = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads?select=id&callbackDate=lte.{today}&status=not.in.(converted)",
            headers=SB_HEADERS, timeout=30)
        callbacks = r_cb.json() if r_cb.status_code == 200 else []

        total_calls = len(calls) if isinstance(calls, list) else 0
        total_leads = len(new_leads) if isinstance(new_leads, list) else 0
        total_emails = len(emails) if isinstance(emails, list) else 0
        total_callbacks = len(callbacks) if isinstance(callbacks, list) else 0

        # Per-caller breakdown
        caller_calls = {}
        for c in (calls if isinstance(calls, list) else []):
            name = c.get("calledBy", "Unknown")
            caller_calls[name] = caller_calls.get(name, 0) + 1
        leaderboard = sorted(caller_calls.items(), key=lambda x: -x[1])
        lb_text = "\n".join(f"  {name}: *{count}* calls" for name, count in leaderboard[:5]) if leaderboard else "  No calls logged"

        interested = len([c for c in (calls if isinstance(calls, list) else []) if c.get("outcome") in ("interested", "converted", "callback")])

        # ── Patterns + week-over-week trend (the seasonality base) ──
        themes_today = _theme_tally(calls).most_common(3)
        themes_txt = "\n".join(f"  • {t}: *{n}*" for t, n in themes_today) or "  No notes logged"
        wk_n, prior_n = len(wk), len(prior)
        wk_rate, prior_rate = _interest_rate(wk), _interest_rate(prior)
        wk_themes = _theme_tally(wk).most_common(1)
        top_obj = f"{wk_themes[0][0]} ({wk_themes[0][1]})" if wk_themes else "—"
        trend_txt = (f"  Calls: *{wk_n}* {_trend_arrow(wk_n, prior_n)} (prev {prior_n})\n"
                     f"  Interest rate: *{wk_rate}%* {_trend_arrow(wk_rate, prior_rate)} (prev {prior_rate}%)\n"
                     f"  Top objection: {top_obj}")

        app_url = os.getenv("APP_URL", "https://leadflow-railway-production.up.railway.app")

        send_slack(
            "📊 LeadFlow Daily Summary",
            f"Here's what your team did today ({today}):",
            fields=[
                {"label": "Calls Made", "value": f":telephone_receiver: *{total_calls}*"},
                {"label": "Interested/Callback", "value": f":fire: *{interested}*"},
                {"label": "Leads Scraped", "value": f":busts_in_silhouette: *{total_leads}*"},
                {"label": "Emails Sent", "value": f":email: *{total_emails}*"},
                {"label": "Callbacks Due", "value": f":calendar: *{total_callbacks}*"},
                {"label": "Top Callers", "value": lb_text},
                {"label": "Today's objections / themes", "value": themes_txt},
                {"label": "📈 This week vs last (trend)", "value": trend_txt},
            ],
            actions=[
                {"label": "Open LeadFlow", "url": app_url, "style": "primary"},
            ],
        )

        return {"sent": True, "calls": total_calls, "leads": total_leads, "emails": total_emails}
    except Exception as e:
        print(f"[daily-summary] error: {e}")
        return {"error": str(e)}

# Objection/theme buckets tallied from free-text call notes (no LLM needed).
CALL_NOTE_THEMES = {
    "Happy w/ current / in-house": ["happy with", "in-house", "in house", "already have", "have someone",
                                    "have a company", "under contract", "already using", "got a guy"],
    "Price / no budget":           ["no budget", "too expensive", "expensive", "price", "pricing", "cost",
                                    "can't afford", "cant afford", "budget"],
    "Gatekeeper / wrong person":   ["gatekeeper", "front desk", "wrong person", "not the right", "transferred",
                                    "wouldn't transfer", "no name"],
    "Voicemail / no answer":       ["voicemail", "vm", "no answer", "left message", "left a message", "didn't pick"],
    "Callback / timing":           ["call back", "callback", "follow up", "next month", "next week", "busy",
                                    "not a good time", "reach out later", "circle back"],
    "Interested / wants quote":    ["interested", "wants a quote", "quote", "walkthrough", "walk through",
                                    "send info", "schedule", "set up", "estimate"],
    "Not interested":              ["not interested", "no thanks", "no need", "all set", "we're good"],
}

def _parse_iso(s):
    """Parse a stored ISO timestamp → NAIVE UTC datetime. PostgREST returns
    timestamptz with a +00:00 offset, so we strip tzinfo to stay comparable with
    datetime.utcnow() (mixing aware+naive raises)."""
    if not s:
        return None
    s = str(s).strip().replace("Z", "")
    for fmt in (None, "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            dt = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except Exception:
            continue
    return None

# A session longer than this = a forgotten logout, not a 16-hour shift. Capped.
MAX_SESSION_HOURS = float(os.getenv("MAX_SESSION_HOURS", "12"))

@app.get("/api/analytics/hours")
def hours_worked(start: str = "", end: str = "", user: str = Depends(verify_token)):
    """Consolidated hours-worked per caller for payroll. Built from user_sessions
    (sign-in → sign-out); a missing sign-out (left-open tab) is capped at the
    caller's LAST CALL that session, then at MAX_SESSION_HOURS — so you don't pay
    for an idle overnight tab. Defaults to the current week (Mon → now). Admin."""
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")
    now = datetime.utcnow()
    start_dt = _parse_iso(start + "T00:00:00") if start else (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = _parse_iso(end + "T23:59:59") if end else now
    s_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%S")

    sessions = _paginated_get(
        f"{SUPABASE_URL}/rest/v1/user_sessions?select=username,signed_in,signed_out"
        f"&signed_in=gte.{s_iso}&order=signed_in.asc")
    calls = _paginated_get(
        f"{SUPABASE_URL}/rest/v1/call_outcomes?select=calledBy,calledAt,duration"
        f"&calledAt=gte.{s_iso}")

    from collections import defaultdict
    calls_by = defaultdict(list)          # caller → [(dt, duration)]
    for c in calls:
        dt = _parse_iso(c.get("calledAt"))
        if dt:
            calls_by[c.get("calledBy") or "Unknown"].append((dt, c.get("duration") or 0))
    for k in calls_by:
        calls_by[k].sort()

    per = defaultdict(lambda: {"name": "", "total_hours": 0.0, "days": {}})
    for s in sessions:
        name = s.get("username") or ""
        si = _parse_iso(s.get("signed_in"))
        if not name or not si or si < start_dt or si > end_dt:
            continue
        so = _parse_iso(s.get("signed_out"))
        capped = False
        if not so:
            # No sign-out → end at the last call after sign-in (+5m), else minimal.
            after = [dt for dt, _ in calls_by.get(name, []) if dt >= si]
            so = (max(after) + timedelta(minutes=5)) if after else (si + timedelta(minutes=15))
            capped = True
        hrs = (so - si).total_seconds() / 3600
        if hrs > MAX_SESSION_HOURS:
            hrs, capped = MAX_SESSION_HOURS, True
        hrs = max(0.0, hrs)
        d_key = si.strftime("%Y-%m-%d")
        bucket = per[name]
        bucket["name"] = name
        day = bucket["days"].setdefault(d_key, {"date": d_key, "hours": 0.0, "sessions": 0,
                                                "first_in": None, "last_out": None, "capped": False})
        day["hours"] += hrs
        day["sessions"] += 1
        day["first_in"] = si.strftime("%H:%M") if not day["first_in"] else min(day["first_in"], si.strftime("%H:%M"))
        day["last_out"] = max(day["last_out"] or "", so.strftime("%H:%M"))
        day["capped"] = day["capped"] or capped
        bucket["total_hours"] += hrs

    # Attach per-day call counts + talk time.
    for name, (lst) in calls_by.items():
        if name not in per:
            continue
        for dt, dur in lst:
            dk = dt.strftime("%Y-%m-%d")
            if dk in per[name]["days"]:
                per[name]["days"][dk].setdefault("calls", 0)
                per[name]["days"][dk]["calls"] += 1
                per[name]["days"][dk].setdefault("talk_min", 0)
                per[name]["days"][dk]["talk_min"] += round((dur or 0) / 60)

    callers = []
    for name, b in per.items():
        if is_admin(name):        # payroll is for hourly callers, not admins
            continue
        days = sorted(b["days"].values(), key=lambda x: x["date"])
        for d in days:
            d["hours"] = round(d["hours"], 2)
            d.setdefault("calls", 0); d.setdefault("talk_min", 0)
        callers.append({"name": name, "total_hours": round(b["total_hours"], 2),
                        "total_calls": sum(d["calls"] for d in days), "days": days})
    callers.sort(key=lambda x: -x["total_hours"])
    return {"start": start_dt.strftime("%Y-%m-%d"), "end": end_dt.strftime("%Y-%m-%d"),
            "callers": callers, "max_session_hours": MAX_SESSION_HOURS}

@app.get("/api/analytics/call-notes")
def call_notes_digest(days: int = 7, user: str = Depends(verify_token)):
    """Daily digest of the caller's notes + objection/theme patterns over the
    window — so the admin can read what's being said and spot what's recurring.
    Admin only. No LLM: patterns come from the qual chips + note keyword tallies."""
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")
    days = max(1, min(int(days), 30))
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    calls = _paginated_get(
        f"{SUPABASE_URL}/rest/v1/call_outcomes?select=notes,outcome,calledBy,calledAt,leadId,"
        f"vendorstatus,budgetfocus,timeline,decisionmaker,qualified"
        f"&calledAt=gte.{since}T00:00:00&order=calledAt.desc")
    # Only resolve the leads actually referenced (not all ~9k) — chunked in.()
    lead_ids = list({c.get("leadId") for c in calls if c.get("leadId")})
    leadmap = {}
    for i in range(0, len(lead_ids), 100):
        chunk = ",".join(f'"{x}"' for x in lead_ids[i:i+100])
        try:
            r = req_lib.get(f"{SUPABASE_URL}/rest/v1/leads?select=id,company,state&id=in.({chunk})",
                            headers=SB_HEADERS, timeout=20)
            for l in (r.json() if r.status_code == 200 else []):
                leadmap[l.get("id")] = l
        except Exception as e:
            print(f"[CALL-NOTES] lead chunk failed: {e}")

    from collections import defaultdict, Counter
    days_map = defaultdict(lambda: {"date": "", "calls": 0, "outcomes": Counter(), "notes": []})
    vendor, focus, timeline, themes = Counter(), Counter(), Counter(), Counter()
    for c in calls:
        d = (c.get("calledAt") or "")[:10]
        if not d:
            continue
        day = days_map[d]; day["date"] = d; day["calls"] += 1
        day["outcomes"][c.get("outcome", "") or "?"] += 1
        if c.get("vendorstatus"): vendor[c["vendorstatus"]] += 1
        if c.get("budgetfocus"):  focus[c["budgetfocus"]] += 1
        if c.get("timeline"):     timeline[c["timeline"]] += 1
        note = (c.get("notes") or "").strip()
        if note:
            nl = note.lower()
            for theme, kws in CALL_NOTE_THEMES.items():
                if any(k in nl for k in kws):
                    themes[theme] += 1
            if len(day["notes"]) < 120:
                lead = leadmap.get(c.get("leadId"), {})
                day["notes"].append({"company": lead.get("company", "—"), "state": lead.get("state", ""),
                                     "outcome": c.get("outcome", ""), "caller": c.get("calledBy", ""),
                                     "note": note[:400],
                                     "qual": [x for x in [c.get("vendorstatus"), c.get("budgetfocus"),
                                              c.get("timeline"), c.get("qualified")] if x]})
    daily = sorted(days_map.values(), key=lambda x: x["date"], reverse=True)
    for x in daily:
        x["outcomes"] = dict(x["outcomes"])
    return {
        "days": days,
        "daily": daily,
        "patterns": {
            "themes":        themes.most_common(),
            "vendor_status": vendor.most_common(),
            "focus":         focus.most_common(),
            "timeline":      timeline.most_common(),
        },
        "totals": {"calls": sum(x["calls"] for x in daily), "notes_written": sum(len(x["notes"]) for x in daily)},
    }

@app.get("/api/analytics/conversions")
def conversion_analytics(user: str = Depends(verify_token)):
    """Conversion funnel BY SOURCE and BY INTENT — closes the learning loop:
    which lead sources / hot-intent signals actually convert, so you double down
    on what works (and feed the lookalike model). conv_rate = converted / called,
    so uncalled fresh leads don't dilute it. Admin only."""
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")
    rows = _paginated_get(
        f"{SUPABASE_URL}/rest/v1/leads?select=source,status,total_calls,notes")
    from collections import defaultdict
    def bucket():
        return {"leads": 0, "called": 0, "interested": 0, "converted": 0}
    by_source, by_intent, overall = defaultdict(bucket), defaultdict(bucket), bucket()
    for l in rows:
        src = (l.get("source") or "—").strip() or "—"
        called = (l.get("total_calls") or 0) > 0
        st = l.get("status")
        conv = st == "converted"
        inter = st in ("interested", "interested_no_dm")
        targets = [by_source[src], overall] + [by_intent[k] for k in lead_intent_kinds(l)]
        for d in targets:
            d["leads"] += 1
            d["called"] += 1 if called else 0
            d["interested"] += 1 if inter else 0
            d["converted"] += 1 if conv else 0
    def fmt(name, d):
        return {"name": name, **d,
                "conv_rate": round(d["converted"] / d["called"] * 100, 1) if d["called"] else 0.0,
                "contact_rate": round(d["called"] / d["leads"] * 100, 1) if d["leads"] else 0.0}
    return {
        "by_source": sorted((fmt(k, v) for k, v in by_source.items()),
                            key=lambda x: (-x["converted"], -x["called"], -x["leads"])),
        "by_intent": sorted((fmt(k, v) for k, v in by_intent.items()),
                            key=lambda x: (-x["converted"], -x["called"])),
        "overall": fmt("All leads", overall),
        "note": "conv_rate = converted ÷ called. New sources read ~0 until the caller works them.",
    }

@app.get("/api/leaderboard")
def get_leaderboard(range: str = "today", user: str = Depends(verify_token)):
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        # Calculate date filter based on range
        if range == "7d":
            since = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
        elif range == "30d":
            since = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
        elif range == "all":
            since = ""
        else:
            since = today

        # Paginate every Supabase fetch — single requests cap at 1000 rows,
        # which silently truncated leaderboard totals once the team crossed
        # 1000 lifetime calls (or 1000 in any window). Same root cause as
        # the /api/leads + /api/stats fix in commit 6142f2c.
        calls_url = f"{SUPABASE_URL}/rest/v1/call_outcomes?select=outcome,calledBy,calledAt,duration"
        if since:
            calls_url += f"&calledAt=gte.{since}T00:00:00"
        calls = _paginated_get(calls_url)
        leads = _paginated_get(
            f"{SUPABASE_URL}/rest/v1/leads?select=assignedTo,status,score,createdBy,createdAt"
        )
        sess_url = f"{SUPABASE_URL}/rest/v1/user_sessions?select=username,signed_in,signed_out"
        if since:
            sess_url += f"&signed_in=gte.{since}T00:00:00"
        sess_url += "&order=signed_in.desc"
        sessions = _paginated_get(sess_url)
        if not isinstance(sessions, list):
            sessions = []

        # Build per-user call stats
        users = {}
        # Add signed-in users first so they appear even with 0 calls
        for s in sessions:
            name = s.get("username") or ""
            if not name:
                continue
            if name not in users:
                users[name] = {"name": name, "total_calls": 0, "calls_today": 0,
                               "conversions": 0, "interested": 0, "no_answer": 0,
                               "voicemail": 0, "callbacks": 0, "contacted": 0,
                               "talk_time": 0, "revenue": 0,
                               "signed_in_at": s.get("signed_in"),
                               "signed_out_at": s.get("signed_out"),
                               "sessions": 0}
            users[name]["sessions"] = users[name].get("sessions", 0) + 1

        for c in calls:
            name = c.get("calledBy") or "Unknown"
            if name not in users:
                users[name] = {"name": name, "total_calls": 0, "calls_today": 0,
                               "conversions": 0, "interested": 0, "no_answer": 0,
                               "voicemail": 0, "callbacks": 0, "contacted": 0,
                               "talk_time": 0, "revenue": 0,
                               "signed_in_at": None, "signed_out_at": None, "sessions": 0}
            u = users[name]
            u["total_calls"] += 1
            u["talk_time"] += c.get("duration") or 0
            if (c.get("calledAt") or "").startswith(today):
                u["calls_today"] += 1
            outcome = c.get("outcome", "")
            if outcome in ("answered", "interested", "interested_no_dm", "converted", "callback", "not_interested"):
                u["contacted"] += 1
            if outcome == "converted":
                u["conversions"] += 1
            elif outcome == "interested": u["interested"]  += 1
            elif outcome == "no_answer":  u["no_answer"]   += 1
            elif outcome == "voicemail":  u["voicemail"]   += 1
            elif outcome == "callback":   u["callbacks"]   += 1

        # Add lead assignment counts + leads populated (created/scraped)
        for l in leads:
            name = l.get("assignedTo") or ""
            if name and name in users:
                users[name].setdefault("leads_assigned", 0)
                users[name]["leads_assigned"] = users[name].get("leads_assigned", 0) + 1
            # Count leads populated by this user in the date range
            creator = l.get("createdBy") or ""
            created_at = l.get("createdAt") or ""
            if creator and creator not in ("system",) and (not since or created_at >= f"{since}T00:00:00"):
                if creator not in users:
                    users[creator] = {"name": creator, "total_calls": 0, "calls_today": 0,
                                      "conversions": 0, "interested": 0, "no_answer": 0,
                                      "voicemail": 0, "callbacks": 0, "contacted": 0,
                                      "talk_time": 0, "revenue": 0,
                                      "signed_in_at": None, "signed_out_at": None, "sessions": 0}
                users[creator].setdefault("leads_populated", 0)
                users[creator]["leads_populated"] = users[creator].get("leads_populated", 0) + 1

        # Compute rates per user
        result = []
        for u in users.values():
            tc = u["total_calls"]
            u["conv_rate"] = f"{(u['conversions']/tc*100):.1f}" if tc else "0.0"
            u["contact_rate"] = f"{(u['contacted']/tc*100):.1f}" if tc else "0.0"
            u["avg_talk_time"] = round(u["talk_time"] / tc) if tc else 0
            u["leads_assigned"] = u.get("leads_assigned", 0)
            u["leads_populated"] = u.get("leads_populated", 0)
            result.append(u)

        # Flag suspicious stats
        for u in result:
            u["flags"] = []
            conv = float(u["conv_rate"]) if u["total_calls"] >= 10 else 0
            contact = float(u["contact_rate"]) if u["total_calls"] >= 10 else 0
            if conv > 50: u["flags"].append("high_conv_rate")
            if contact > 95 and u["total_calls"] >= 20: u["flags"].append("perfect_contact")

        # Sort by calls today desc, then total calls
        result.sort(key=lambda x: (-x["calls_today"], -x["total_calls"]))
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Audit log endpoint ────────────────────────────────────────────────────────

@app.get("/api/audit-log")
def get_audit_log(days: int = 7, user: str = Depends(verify_admin)):
    """Admin-only: fetch recent audit log entries"""
    try:
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/audit_log?select=*&created_at=gte.{since}T00:00:00&order=created_at.desc&limit=500",
            headers=SB_HEADERS, timeout=30)
        logs = r.json() if r.status_code == 200 else []
        return logs if isinstance(logs, list) else []
    except:
        return []

# ── Scripts endpoints ──────────────────────────────────────────────────────────

@app.get("/api/scripts")
def get_scripts(industry: str = "", user: str = Depends(verify_token)):
    try:
        url = f"{SUPABASE_URL}/rest/v1/scripts?is_active=eq.true&order=usage_count.desc"
        if industry:
            url += f"&industry=eq.{industry}"
        r = req_lib.get(url, headers=SB_HEADERS, timeout=30)
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/scripts")
def create_script(script: dict, user: str = Depends(verify_token)):
    try:
        script["is_active"] = True
        script["usage_count"] = 0
        script["created_by"] = user
        script["created_at"] = datetime.utcnow().isoformat()
        script["updated_at"] = datetime.utcnow().isoformat()
        r = req_lib.post(f"{SUPABASE_URL}/rest/v1/scripts", headers=SB_HEADERS, json=script, timeout=30)
        result = r.json()
        audit_log(user, "create_script", "script", None, {"title": script.get("title"), "industry": script.get("industry")})
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/scripts/{script_id}")
def update_script(script_id: str, data: dict, user: str = Depends(verify_token)):
    try:
        data["updated_at"] = datetime.utcnow().isoformat()
        r = req_lib.patch(f"{SUPABASE_URL}/rest/v1/scripts?id=eq.{url_quote(script_id)}",
                         headers=SB_HEADERS, json=data, timeout=30)
        audit_log(user, "update_script", "script", script_id, {"fields_changed": list(data.keys())})
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/scripts/{script_id}")
def delete_script(script_id: str, user: str = Depends(verify_token)):
    try:
        req_lib.patch(f"{SUPABASE_URL}/rest/v1/scripts?id=eq.{url_quote(script_id)}",
                     headers=SB_HEADERS, json={"is_active": False}, timeout=30)
        audit_log(user, "delete_script", "script", script_id)
        return {"deleted": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/scripts/{script_id}/use")
def increment_script_usage(script_id: str, user: str = Depends(verify_token)):
    try:
        r = req_lib.get(f"{SUPABASE_URL}/rest/v1/scripts?id=eq.{script_id}&select=usage_count",
                       headers=SB_HEADERS, timeout=30)
        scripts = r.json()
        count = scripts[0].get("usage_count", 0) + 1 if scripts else 1
        req_lib.patch(f"{SUPABASE_URL}/rest/v1/scripts?id=eq.{script_id}",
                     headers=SB_HEADERS, json={"usage_count": count, "last_used": datetime.utcnow().isoformat()}, timeout=30)
        return {"usage_count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Caller detail endpoint (admin only) ───────────────────────────────────────

@app.get("/api/caller/{username}/detail")
def get_caller_detail(username: str, date: str = "", date_to: str = "", user: str = Depends(verify_admin)):
    """Admin: get detailed breakdown of a caller's activity for a date or range"""
    try:
        today = date if date else datetime.utcnow().strftime("%Y-%m-%d")
        end_date = date_to if date_to else today
        # Get calls for this caller in the date range
        r_calls = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/call_outcomes?select=*&calledBy=eq.{username}"
            f"&calledAt=gte.{today}T00:00:00&calledAt=lte.{end_date}T23:59:59&order=calledAt.desc",
            headers=SB_HEADERS, timeout=30)
        calls = r_calls.json() if r_calls.status_code == 200 else []
        if not isinstance(calls, list):
            calls = []

        # Breakdown by outcome
        breakdown = {"answered": 0, "no_answer": 0, "voicemail": 0,
                     "interested": 0, "callback": 0, "converted": 0, "not_interested": 0}
        total_talk_time = 0
        for c in calls:
            o = c.get("outcome", "")
            if o in breakdown:
                breakdown[o] += 1
            total_talk_time += c.get("duration") or 0

        # Get qualified calls (with qual data)
        qual_fields = ["budgetfocus", "vendorstatus", "decisionmaker", "timeline", "qualified"]
        qualified = [c for c in calls if any(c.get(f) for f in qual_fields)]

        # Enrich calls with lead info
        lead_ids = list(set(c.get("leadId") for c in calls if c.get("leadId")))
        lead_map = {}
        if lead_ids:
            # Batch fetch lead info — all of them, 50 at a time
            for batch_start in range(0, len(lead_ids), 50):
                batch = lead_ids[batch_start:batch_start+50]
                ids_filter = ",".join(str(x) for x in batch)
                lr = req_lib.get(
                    f"{SUPABASE_URL}/rest/v1/leads?id=in.({ids_filter})"
                    f"&select=id,company,firstName,lastName,phone,industry,city,state,status",
                    headers=SB_HEADERS, timeout=10)
                ld = lr.json() if lr.status_code == 200 else []
                if isinstance(ld, list):
                    for l in ld:
                        lead_map[l["id"]] = l

        # Attach lead info to each call
        call_list = []
        for c in calls:
            lid = c.get("leadId")
            lead_info = lead_map.get(lid) if lid else None
            call_list.append({
                "id": c.get("id"),
                "outcome": c.get("outcome"),
                "duration": c.get("duration"),
                "calledAt": c.get("calledAt"),
                "notes": c.get("notes"),
                "leadId": lid,
                "lead_company": lead_info.get("company") if lead_info else None,
                "lead_name": f"{lead_info.get('firstName','')} {lead_info.get('lastName','')}".strip() if lead_info else None,
                "lead_phone": lead_info.get("phone") if lead_info else None,
                "lead_status": lead_info.get("status") if lead_info else None,
            })

        # Get leads populated in the date range
        lr_pop = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads?select=id,company,industry,city,state"
            f"&createdBy=eq.{username}&createdAt=gte.{today}T00:00:00&createdAt=lte.{end_date}T23:59:59&order=createdAt.desc&limit=50",
            headers=SB_HEADERS, timeout=10)
        leads_populated = lr_pop.json() if lr_pop.status_code == 200 else []
        if not isinstance(leads_populated, list):
            leads_populated = []

        return {
            "username": username,
            "date": today,
            "total_calls": len(calls),
            "total_talk_time": total_talk_time,
            "avg_talk_time": round(total_talk_time / len(calls)) if calls else 0,
            "breakdown": breakdown,
            "qualified_count": len(qualified),
            "calls": call_list,
            "leads_populated": len(leads_populated),
            "leads_populated_list": leads_populated[:20],
        }
    except Exception as e:
        print(f"[CALLER_DETAIL] Error for {username}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Email Sending ────────────────────────────────────────────────────────────────

class SendEmailRequest(BaseModel):
    lead_id: Optional[int] = None
    to_email: str
    to_name: Optional[str] = ""
    subject: str
    body: str
    company: Optional[str] = ""

def send_smtp_email(to_email: str, to_name: str, subject: str, body_html: str, reply_to: str = ""):
    """Send an email via Resend HTTP API (function name kept for backwards compat).
    Railway blocks outbound SMTP, so we use Resend's HTTP API instead.
    Returns (success, error_message)."""
    if not RESEND_API_KEY:
        return False, "Email not configured. Set RESEND_API_KEY env var."
    # Plain text fallback derived from HTML
    plain = re.sub(r"<[^>]+>", "", body_html).strip()
    payload = {
        "from": f"{OUTREACH_NAME} <{OUTREACH_EMAIL}>",
        "to": [f"{to_name} <{to_email}>"] if to_name else [to_email],
        "subject": subject,
        "html": body_html,
        "text": plain,
    }
    if reply_to:
        payload["reply_to"] = reply_to
    try:
        r = req_lib.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        if r.status_code in (200, 201, 202):
            return True, None
        try:
            err = r.json().get("message") or r.text
        except Exception:
            err = r.text
        return False, f"Resend {r.status_code}: {err}"
    except Exception as e:
        return False, str(e)

@app.post("/api/email/send")
def send_email(req: SendEmailRequest, user: str = Depends(verify_token)):
    """Send a follow-up email to a prospect and log it."""
    if not req.to_email or "@" not in req.to_email:
        raise HTTPException(status_code=400, detail="Valid email address required")
    if not req.subject or not req.body:
        raise HTTPException(status_code=400, detail="Subject and body required")

    # Send the email
    success, err = send_smtp_email(req.to_email, req.to_name, req.subject, req.body, reply_to=OUTREACH_REPLY_TO)
    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to send: {err}")

    # Log to email_log table
    log_entry = {
        "lead_id": req.lead_id,
        "sent_by": user,
        "to_email": req.to_email,
        "to_name": req.to_name or "",
        "subject": req.subject,
        "body": req.body,
        "company": req.company or "",
        "status": "sent",
        "sent_at": datetime.utcnow().isoformat(),
    }
    try:
        r = req_lib.post(f"{SUPABASE_URL}/rest/v1/email_log",
            headers=SB_ADMIN_HEADERS, json=log_entry, timeout=10)
        log_data = r.json() if r.status_code in (200, 201) else []
        log_id = log_data[0]["id"] if isinstance(log_data, list) and log_data else None
    except:
        log_id = None

    audit_log(user, "send_email", "lead", req.lead_id, {
        "to": req.to_email, "subject": req.subject, "company": req.company})

    # Slack notification
    send_slack(
        "📧 LeadFlow Email Sent",
        f"*{user}* sent outreach to *{req.to_name or req.to_email}*",
        fields=[
            {"label": "To", "value": req.to_email},
            {"label": "Company", "value": req.company or "—"},
            {"label": "Subject", "value": req.subject[:50]},
        ],
    )

    return {"sent": True, "log_id": log_id}

@app.get("/api/email/history")
def get_email_history(lead_id: int = 0, user: str = Depends(verify_token)):
    """Get email history for a specific lead or all recent emails."""
    try:
        url = f"{SUPABASE_URL}/rest/v1/email_log?select=*&order=sent_at.desc"
        if lead_id:
            url += f"&lead_id=eq.{lead_id}"
        url += "&limit=100"
        r = req_lib.get(url, headers=SB_ADMIN_HEADERS, timeout=30)
        emails = r.json() if r.status_code == 200 else []
        return emails if isinstance(emails, list) else []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/email/stats")
def get_email_stats(user: str = Depends(verify_admin)):
    """Admin: email sending stats."""
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/email_log?select=id,sent_by,sent_at,status&order=sent_at.desc&limit=500",
            headers=SB_ADMIN_HEADERS, timeout=30)
        emails = r.json() if r.status_code == 200 else []
        if not isinstance(emails, list):
            emails = []
        today_count = len([e for e in emails if (e.get("sent_at") or "").startswith(today)])
        by_user = {}
        for e in emails:
            u = e.get("sent_by", "unknown")
            by_user[u] = by_user.get(u, 0) + 1
        return {"total": len(emails), "today": today_count, "by_user": by_user}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Email Templates (CRUD — same pattern as call scripts) ────────────────────────

@app.get("/api/email-templates")
def get_email_templates(industry: str = "", user: str = Depends(verify_token)):
    try:
        url = f"{SUPABASE_URL}/rest/v1/email_templates?is_active=eq.true&order=usage_count.desc"
        if industry:
            url += f"&industry=eq.{industry}"
        r = req_lib.get(url, headers=SB_HEADERS, timeout=30)
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/email-templates")
def create_email_template(template: dict, user: str = Depends(verify_token)):
    try:
        template["is_active"] = True
        template["usage_count"] = 0
        template["created_by"] = user
        template["created_at"] = datetime.utcnow().isoformat()
        template["updated_at"] = datetime.utcnow().isoformat()
        r = req_lib.post(f"{SUPABASE_URL}/rest/v1/email_templates", headers=SB_HEADERS, json=template, timeout=30)
        audit_log(user, "create_email_template", "email_template", None, {"name": template.get("name")})
        return r.json() if r.status_code in (200, 201) else []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/email-templates/{template_id}")
def update_email_template(template_id: str, data: dict, user: str = Depends(verify_token)):
    try:
        data["updated_at"] = datetime.utcnow().isoformat()
        r = req_lib.patch(f"{SUPABASE_URL}/rest/v1/email_templates?id=eq.{url_quote(template_id)}",
                         headers=SB_HEADERS, json=data, timeout=30)
        audit_log(user, "update_email_template", "email_template", template_id)
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/email-templates/{template_id}")
def delete_email_template(template_id: str, user: str = Depends(verify_token)):
    try:
        req_lib.patch(f"{SUPABASE_URL}/rest/v1/email_templates?id=eq.{url_quote(template_id)}",
                     headers=SB_HEADERS, json={"is_active": False}, timeout=30)
        audit_log(user, "delete_email_template", "email_template", template_id)
        return {"deleted": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/email-templates/{template_id}/use")
def increment_template_usage(template_id: str, user: str = Depends(verify_token)):
    try:
        r = req_lib.get(f"{SUPABASE_URL}/rest/v1/email_templates?id=eq.{template_id}&select=usage_count",
                       headers=SB_HEADERS, timeout=30)
        templates = r.json() if r.status_code == 200 else []
        count = templates[0].get("usage_count", 0) + 1 if templates else 1
        req_lib.patch(f"{SUPABASE_URL}/rest/v1/email_templates?id=eq.{template_id}",
                     headers=SB_HEADERS, json={"usage_count": count, "last_used": datetime.utcnow().isoformat()}, timeout=30)
        return {"usage_count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── End Email ────────────────────────────────────────────────────────────────────

frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.exists(frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str):
        return FileResponse(os.path.join(frontend_dist, "index.html"))
