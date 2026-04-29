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
from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel
from urllib.parse import quote as url_quote

SECRET_KEY      = os.getenv("SECRET_KEY",      "leadflow-secret")
TEAM_PASSWORD   = os.getenv("TEAM_PASSWORD",   "LeadFlow2024")
ADMIN_PASSWORD  = os.getenv("ADMIN_PASSWORD",  "LeadFlowAdmin2024!")
ADMIN_USERS     = set(u.strip().lower() for u in os.getenv("ADMIN_USERS", "eric").split(",") if u.strip())
BLOCKED_USERS   = set(u.strip().lower() for u in os.getenv("BLOCKED_USERS", "").split(",") if u.strip())
ALGORITHM       = "HS256"

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
CAMPAIGN_SUPPRESSION_DAYS = int(os.getenv("CAMPAIGN_SUPPRESSION_DAYS", "14"))
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
# How long to remember "we already searched Apollo for this company" so we don't
# re-spend credits on the same company. In-memory only — restarts wipe it,
# which is fine; the worst case is paying for a few duplicate lookups.
APOLLO_ENRICH_CACHE_TTL_HOURS = int(os.getenv("APOLLO_ENRICH_CACHE_TTL_HOURS", "720"))  # 30 days

# ── Google Places cost controls ─────────────────────────────────────────────
# PLACES_KILL_SWITCH=1 halts every Places call instantly (same env name as
# vlm/recruitnil scrapers so one flip stops the bleeding across all repos).
PLACES_KILL_SWITCH = os.getenv("PLACES_KILL_SWITCH", "0") == "1"
# Non-admin daily scrape cap. UTC midnight reset. Eric (in ADMIN_USERS) is
# unlimited. Silently no-ops if usage_events table isn't migrated yet.
NON_ADMIN_DAILY_SCRAPE_CAP = int(os.getenv("NON_ADMIN_DAILY_SCRAPE_CAP", "3"))
# Hard cap per scrape in dollars. Refuses the run if predicted spend exceeds.
PLACES_MAX_SPEND_PER_RUN = float(os.getenv("PLACES_MAX_SPEND_PER_RUN", "2.0"))
# Text Search cache TTL, days. Keyed on (pipeline='leadflow', city, keyword).
PLACES_CACHE_TTL_DAYS = int(os.getenv("PLACES_CACHE_TTL_DAYS", "14"))
# Autocomplete in-memory cache TTL, seconds.
AUTOCOMPLETE_CACHE_TTL_SECONDS = int(os.getenv("AUTOCOMPLETE_CACHE_TTL_SECONDS", "3600"))

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

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return payload["sub"]
    except (jwt.exceptions.InvalidTokenError, KeyError, Exception):
        raise HTTPException(status_code=401, detail="Invalid token")

def verify_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
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

def scrapes_today(username: str) -> int:
    """Count a user's scrape_call events since UTC midnight. Returns 0 if
    usage_events isn't set up yet (so the cap silently doesn't apply)."""
    try:
        midnight = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        url = (
            f"{SUPABASE_URL}/rest/v1/usage_events"
            f"?select=id&username=eq.{url_quote(username)}"
            f"&event_type=eq.scrape_call&created_at=gte.{midnight}"
        )
        r = req_lib.get(url, headers=SB_HEADERS, timeout=5)
        if r.status_code == 200:
            data = r.json()
            return len(data) if isinstance(data, list) else 0
        return 0
    except Exception as e:
        print(f"[RATE-LIMIT] count failed: {e}")
        return 0

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
}

def clean(v): return str(v).strip() if v else ""

def score_lead(lead):
    s = 5
    if lead.get("company","").strip():  s += 8
    if lead.get("phone","").strip():    s += 15
    if lead.get("email","").strip():    s += 6
    if lead.get("website","").strip():  s += 5
    if lead.get("address","").strip():  s += 3
    return min(100, max(0, s))

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

                # Final validation: must have a company name and valid US state
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
    if not leads:
        return 0
    try:
        r = req_lib.post(
            f"{SUPABASE_URL}/rest/v1/leads",
            headers=SB_HEADERS, json=leads, timeout=30
        )
        print(f"[SUPABASE] POST {r.status_code}")
        if r.status_code not in (200, 201):
            print(f"[SUPABASE] Error: {r.text[:300]}")
            return 0
        saved = r.json()
        return len(saved) if isinstance(saved, list) else 1
    except Exception as e:
        print(f"[SUPABASE] Exception: {e}")
        return 0

# ── VCC campaign helpers ───────────────────────────────────────────────────

DEFAULT_EMAIL_TEMPLATE = {
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
}

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
    return dict(DEFAULT_EMAIL_TEMPLATE)

def build_tried_to_call_email(lead: dict) -> dict:
    """Render the 'tried to call you' email subject + body for a lead.
    Pulls the (admin-editable) template from app_settings, falls back to
    DEFAULT_EMAIL_TEMPLATE if nothing's been saved."""
    tpl  = load_email_template("tried-to-call")
    vars_= _email_template_vars(lead)
    return {
        "subject":   _render_email_template(tpl.get("subject"), vars_),
        "body_text": _render_email_template(tpl.get("body"),    vars_),
    }

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

def send_lead_to_campaign(lead: dict, trigger: str, user: str, campaign: str = "tried-to-call"):
    """Send the 'tried to call you' follow-up directly via Resend. Same API key
    + from-address as the rest of Vision Cleaning's outbound, so SPF/DKIM/sender
    reputation match. Returns (ok: bool, detail: str)."""
    if not RESEND_API_KEY:
        return (False, "RESEND_API_KEY not configured")
    if not lead.get("email"):
        return (False, "lead has no email")
    if not lead.get("firstName"):
        return (False, "lead has no firstName (run Find Decision Maker first)")
    if lead_already_in_campaign(lead.get("id"), campaign):
        return (False, f"already sent to '{campaign}' within last {CAMPAIGN_SUPPRESSION_DAYS} days")

    template = build_tried_to_call_email(lead)
    from_name  = os.getenv("OUTREACH_SENDER_NAME", OUTREACH_NAME)
    from_email = OUTREACH_EMAIL
    reply_to   = OUTREACH_REPLY_TO or from_email  # replies hit this inbox; IMAP poller watches it
    payload = {
        "from":    f"{from_name} <{from_email}>",
        "to":      [lead["email"]],
        "subject": template["subject"],
        "text":    template["body_text"],
        "reply_to": reply_to,
        # Tags survive in Resend's webhook payload — we use them to map events
        # back to lead_id without keeping a separate mapping table.
        "tags": [
            {"name": "campaign", "value": campaign},
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
        "campaign": campaign, "trigger": trigger,
        "to_email": lead.get("email"), "company": lead.get("company"),
        "resend_id": resend_id,
    })

    # Mark the lead as awaiting an email reply so callers don't re-dial. The
    # dialer queue + auto-suggest filters exclude this status. When VCC posts
    # back with a 'reply' event, the callback flips it to 'interested' so it
    # naturally returns to the queue. After 7 days with no reply, the
    # nextfollowup nudge surfaces it again for a follow-up touch.
    try:
        next_followup = (datetime.utcnow() + timedelta(days=7)).date().isoformat()
        req_lib.patch(
            f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(str(lead.get('id')))}",
            headers=SB_HEADERS,
            json={
                "status":           "awaiting_email_reply",
                "nextfollowup":     next_followup,
                "followupsequence": "email_followup",
                "updatedAt":        datetime.utcnow().isoformat(),
            },
            timeout=10,
        )
    except Exception as e:
        # Don't fail the send if the status patch fails — VCC already has the lead
        print(f"[CAMPAIGN] post-send status patch failed for lead {lead.get('id')}: {e}")

    print(f"[CAMPAIGN] sent lead {lead.get('id')} to '{campaign}' via {trigger} → status=awaiting_email_reply")
    return (True, "queued at VCC")

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
                reply_note = f"📧 Reply ({datetime.utcnow().date().isoformat()}): {snippet}"

                try:
                    req_lib.patch(
                        f"{SUPABASE_URL}/rest/v1/leads?id=eq.{url_quote(str(lead_id))}",
                        headers=SB_HEADERS,
                        json={
                            "status":    "interested",
                            "notes":     (reply_note + "\n\n" + (target.get("notes") or ""))[:4000],
                            "updatedAt": datetime.utcnow().isoformat(),
                        },
                        timeout=10,
                    )
                except Exception as e:
                    print(f"[IMAP-POLL] lead update failed: {e}")
                    stats["errors"] += 1
                    continue

                audit_log("imap_poller", "campaign_event", "lead", lead_id, {
                    "event": "reply", "from": from_addr, "subject": subject[:200], "snippet": snippet,
                })
                send_slack(
                    ":incoming_envelope: Email Reply",
                    f"*{target.get('firstName','')} {target.get('lastName','')}* at *{target.get('company','')}* replied.",
                    fields=[
                        {"label": "From",     "value": from_addr},
                        {"label": "Assigned", "value": target.get("assignedTo") or "unassigned"},
                        {"label": "Snippet",  "value": snippet[:200] or "(empty)"},
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

def _imap_poll_loop():
    """Background thread loop. Sleeps between polls so we're not hammering."""
    print(f"[IMAP-POLL] background poller starting (every {IMAP_POLL_INTERVAL_MINUTES} min)")
    # Initial delay so we don't poll during startup before everything is ready
    time.sleep(60)
    while True:
        try:
            res = imap_poll_replies()
            _imap_poll_state["last_result"] = res
            if res.get("ok") and res.get("stats", {}).get("matched", 0) > 0:
                print(f"[IMAP-POLL] matched {res['stats']['matched']} replies this cycle")
        except Exception as e:
            print(f"[IMAP-POLL] loop exception: {e}")
        time.sleep(IMAP_POLL_INTERVAL_MINUTES * 60)

# Start the background thread once at module load if IMAP is configured.
# Daemon=True so it dies cleanly with the process. Uvicorn's single-worker
# default on Railway means exactly one poller runs.
if IMAP_SERVER and IMAP_USERNAME and IMAP_PASSWORD:
    threading.Thread(target=_imap_poll_loop, daemon=True, name="imap-reply-poller").start()
else:
    print("[IMAP-POLL] not started — set IMAP_SERVER, IMAP_USERNAME, IMAP_PASSWORD to enable")

class ScrapeRequest(BaseModel):
    industry:  str
    industries: Optional[str] = ""   # comma-separated list for multi-industry
    state:     Optional[str] = ""
    cities:    Optional[str] = ""
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
# We cache MISS results too — no point re-paying when Apollo had nothing.
_apollo_enrich_cache = {}

def apollo_cache_get(company: str):
    """Returns ('HIT', person_or_None) or ('MISS', None)."""
    if not company:
        return ("MISS", None)
    key = company.lower().strip()
    hit = _apollo_enrich_cache.get(key)
    if not hit:
        return ("MISS", None)
    expires, person = hit
    if expires < time.time():
        _apollo_enrich_cache.pop(key, None)
        return ("MISS", None)
    return ("HIT", person)

def apollo_cache_set(company: str, person):
    if not company:
        return
    if len(_apollo_enrich_cache) > 10000:
        _apollo_enrich_cache.clear()
    key = company.lower().strip()
    _apollo_enrich_cache[key] = (time.time() + APOLLO_ENRICH_CACHE_TTL_HOURS * 3600, person)

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

def apollo_enrich_lead_in_place(lead: dict) -> bool:
    """Mutate `lead` to merge Apollo DM info on top of whatever Google Places
    found. Returns True if real enrichment happened (i.e. we got a person).
    Cache-aware: a previously-MISSed company in the cache window is skipped."""
    if not lead.get("company"):
        return False
    if lead.get("firstName"):  # Already has a DM — don't re-spend credits
        return False

    cache_state, cached = apollo_cache_get(lead["company"])
    if cache_state == "HIT":
        person = cached  # may be None — that's a cached miss, still skip
    else:
        person = apollo_find_dm_at_company(lead["company"])
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

    notes_parts = [f"Apollo: {title}".strip(": ")]
    if linkedin: notes_parts.append(f"LinkedIn: {linkedin}")
    if person.get("seniority"): notes_parts.append(f"Seniority: {person['seniority']}")

    now = datetime.utcnow().isoformat()
    lead = {
        "company":     clean(org.get("name", "")),
        "industry":    clean(org.get("industry") or ""),
        "phone":       clean(phone),
        "address":     clean(addr),
        "city":        clean(city),
        "state":       clean(state),
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

    limit = min(max(body.limit or 25, 5), 60)

    # Build list of keywords to search
    if body.industries:
        ind_list = [i.strip() for i in body.industries.split(",") if i.strip()]
        keywords = [(ind, INDUSTRY_MAP.get(ind, ind.lower())) for ind in ind_list]
    elif body.industry and body.industry != "_all_":
        keywords = [(body.industry, INDUSTRY_MAP.get(body.industry, body.industry.lower()))]
    else:
        # All industries — use "business" as a broad Google Places term
        keywords = [("All", "business")]

    # Build list of locations to search
    if body.cities:
        city_list = [c.strip() for c in body.cities.split(",") if c.strip()]
        locations = [f"{city}, {body.state}".strip(", ") for city in city_list]
    else:
        locations = [body.state or ""]

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

    saved = save_to_supabase(leads)
    audit_log(user, "scrape_leads", "lead", None, {
        "industries": [k[0] for k in keywords], "state": body.state,
        "cities": body.cities, "found": len(leads), "saved": saved,
        "cache_hits": len(cache_hits), "uncached_combos": uncached,
        "apollo_enriched": apollo_enriched})
    print(f"[SCRAPE] Saved {saved} leads (cache_hits={len(cache_hits)}/{len(all_combos)}, apollo_enriched={apollo_enriched})")

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
        "cacheHits": len(cache_hits),
        "combos":    len(all_combos),
    }

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

@app.get("/api/leads")
def list_leads(status: str = "", search: str = "", sort: str = "score",
               callbacks: str = "", source: str = "", user: str = Depends(verify_token)):
    try:
        url = f"{SUPABASE_URL}/rest/v1/leads?select=*"
        if status:   url += f"&status=eq.{status}"
        if source:   url += f"&source=eq.{url_quote(source)}"
        if callbacks == "true":
            today = datetime.utcnow().strftime("%Y-%m-%d")
            url += f"&callbackDate=lte.{today}&callbackDate=neq.&status=neq.converted"
        if search:
            s = search.replace(" ", "%20")
            url += f"&or=(company.ilike.%25{s}%25,firstName.ilike.%25{s}%25,lastName.ilike.%25{s}%25,phone.ilike.%25{s}%25)"
        order_map = {"score":"score.desc","newest":"createdAt.desc","company":"company.asc","callbacks":"callbackDate.asc"}
        url += f"&order={order_map.get(sort,'score.desc')}"
        r = req_lib.get(url, headers=SB_HEADERS, timeout=30)
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/leads")
def create_lead(lead: dict, user: str = Depends(verify_token)):
    try:
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
def import_leads(leads: list, user: str = Depends(verify_token)):
    try:
        for lead in leads:
            lead["score"] = score_lead(lead)
            lead["createdBy"] = user
        r = req_lib.post(f"{SUPABASE_URL}/rest/v1/leads", headers=SB_HEADERS, json=leads, timeout=30)
        saved = r.json()
        count = len(saved) if isinstance(saved, list) else 0
        audit_log(user, "import_leads", "lead", None, {"count": count, "source": "csv"})
        return {"count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/leads/{lead_id}")
def update_lead(lead_id: str, data: dict, user: str = Depends(verify_token)):
    try:
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
            if lead_full and not lead_full.get("assignedTo"):
                req_lib.patch(
                    f"{SUPABASE_URL}/rest/v1/leads?id=eq.{lead_id}",
                    headers=SB_HEADERS,
                    json={"assignedTo": caller},
                    timeout=30)

            # Email follow-up trigger. Two paths:
            #   (a) Caller checked "Send follow-up email" in the modal → fire now,
            #       regardless of attempt count. Per-call override.
            #   (b) AUTO_CAMPAIGN_AFTER_FAILED_CALL=1 + outcome is failed +
            #       attempt count >= threshold → background auto-fire.
            # Both paths share send_lead_to_campaign which handles 14-day
            # suppression so a manual + auto can't double-send.
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
        url = f"{SUPABASE_URL}/rest/v1/call_outcomes?select=*&order=calledAt.desc&limit=1000"
        if date_from:
            url += f"&calledAt=gte.{date_from}T00:00:00"
        if date_to:
            url += f"&calledAt=lte.{date_to}T23:59:59"
        if caller:
            url += f"&calledBy=eq.{caller}"
        r = req_lib.get(url, headers=SB_HEADERS, timeout=30)
        calls = r.json() if r.status_code == 200 else []
        if not isinstance(calls, list):
            return {"calls": [], "summary": {}, "callers": []}

        # Track which leads have been called before for first-call detection
        lead_first_call = {}  # leadId -> earliest calledAt

        # Build summary stats
        contacted = ["answered", "interested", "converted", "callback"]
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
                f"?select=id,company,phone,email,state,city,source,createdAt,assignedTo,status,total_calls"
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
    # Group by exact phone match. Keep the newest createdAt; mark older
    # extras for deletion ONLY if they have no caller activity (otherwise
    # we'd lose call-history attached to a worked lead).
    phone_groups = {}
    for l in leads:
        ph = (l.get("phone") or "").strip()
        if ph:
            phone_groups.setdefault(ph, []).append(l)
    dup_phone_ids = []
    dup_phone_samples = []
    for ph, group in phone_groups.items():
        if len(group) < 2:
            continue
        group_sorted = sorted(group, key=lambda x: x.get("createdAt") or "", reverse=True)
        keep = group_sorted[0]
        extras = group_sorted[1:]
        worth_deleting = []
        for e in extras:
            # Skip if worked: assigned, calls logged, or status moved past 'new'
            if e.get("assignedTo"):
                continue
            if (e.get("total_calls") or 0) > 0:
                continue
            if e.get("status") and e.get("status") != "new":
                continue
            worth_deleting.append(e)
        if not worth_deleting:
            continue
        for e in worth_deleting:
            dup_phone_ids.append(e["id"])
        if len(dup_phone_samples) < 8:
            dup_phone_samples.append({
                "phone": ph,
                "kept":  {"id": keep["id"], "company": keep.get("company"),
                          "createdAt": (keep.get("createdAt") or "")[:10],
                          "source": keep.get("source")},
                "delete":[{"id": e["id"], "company": e.get("company"),
                           "createdAt": (e.get("createdAt") or "")[:10],
                           "source": e.get("source")} for e in worth_deleting],
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
                "label":   "Duplicate phone numbers (newer copy kept; only deletes unworked extras)",
                "count":   len(dup_phone_ids),
                "ids":     dup_phone_ids,
                "samples": dup_phone_samples,
            },
        },
    }

@app.post("/api/admin/leads/cleanup-execute")
def cleanup_execute(body: dict, user: str = Depends(verify_admin)):
    """Delete leads by ID list. Body: {ids: [...], reason: 'foreign'|'duplicate_phones'|...}.
    Batched by 50 because Supabase URL length limits."""
    ids = body.get("ids") or []
    reason = (body.get("reason") or "manual").strip()
    if not ids:
        return {"deleted": 0, "requested": 0}

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
    })
    return {"deleted": deleted, "requested": len(ids), "failed_batches": failed_batches}

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

@app.post("/api/admin/apollo/pull")
def apollo_pull(body: ApolloPullRequest, user: str = Depends(verify_admin)):
    """Pull decision-maker contacts from Apollo and insert into leads.
    Admin-only. Dedupes via Supabase unique constraints (same as Google Places flow)."""
    if not APOLLO_API_KEY:
        raise HTTPException(status_code=400,
            detail="APOLLO_API_KEY not configured. Set it in Railway env vars.")

    titles     = _to_str_list(body.titles)
    industries = _to_str_list(body.industries)
    locations  = _to_str_list(body.locations)
    per_page   = max(1, min(body.per_page or 25, 100))

    payload = {"page": body.page or 1, "per_page": per_page}
    if titles:     payload["person_titles"] = titles
    if industries: payload["q_organization_industries"] = industries
    if locations:  payload["person_locations"] = locations
    if body.employee_min and body.employee_max:
        payload["organization_num_employees_ranges"] = [f"{body.employee_min},{body.employee_max}"]

    headers = {
        "X-Api-Key":     APOLLO_API_KEY,
        "Cache-Control": "no-cache",
        "Content-Type":  "application/json",
    }

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
    print(f"[APOLLO] returned {len(people)} people (total available: {pagination.get('total_entries')})")

    qualified_pairs = []  # list of (lead, original_person) — kept aligned for phone reveal step
    skipped_no_company = 0
    skipped_no_actionable = 0
    for person in people:
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

    audit_log(user, "apollo_pull", "lead", None, {
        "titles": titles, "industries": industries, "locations": locations,
        "employee_range": f"{body.employee_min}-{body.employee_max}",
        "page": payload["page"], "per_page": per_page,
        "returned": len(people), "qualified": len(leads), "saved": saved,
        "skipped_no_company":      skipped_no_company,
        "skipped_no_actionable":   skipped_no_actionable,
        "phone_reveals_requested": phone_reveals_requested,
        "total_entries":           pagination.get("total_entries"),
        "total_pages":             pagination.get("total_pages"),
    })

    if saved > 0:
        app_url = os.getenv("APP_URL", "https://leadflow-railway-production.up.railway.app")
        send_slack(
            ":telephone_receiver: Apollo Pull Complete",
            f"*{user}* pulled *{saved}* new decision-maker leads from Apollo.",
            fields=[
                {"label": "Titles",    "value": ", ".join(titles) or "Any"},
                {"label": "Locations", "value": ", ".join(locations) or "Any"},
                {"label": "Returned",  "value": str(len(people))},
                {"label": "Saved",     "value": f":white_check_mark: {saved}"},
            ],
            actions=[{"label": "📋 View Leads", "url": app_url, "style": "primary"}],
        )

    return {
        "returned":        len(people),
        "qualified":       len(leads),
        "saved":           saved,
        "skipped":         {"no_company": skipped_no_company, "no_actionable": skipped_no_actionable},
        "phone_reveals":   {"requested": phone_reveals_requested,
                            "note": "phones arrive async via webhook within 30s"} if body.reveal_phones else None,
        "total_available": pagination.get("total_entries"),
        "page":            pagination.get("page"),
        "total_pages":     pagination.get("total_pages"),
        "sample":          [{"company": l.get("company"), "name": f"{l.get('firstName','')} {l.get('lastName','')}".strip(),
                             "title": l.get("title"), "phone": l.get("phone"), "email": l.get("email")}
                            for l in leads[:3]],
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
    try:
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads"
            f"?firstName=eq.&assignedTo=eq.&status=eq.new"
            f"&select=id,company,firstName,lastName,title,email,phone,notes,score"
            f"&limit={limit}",
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
        if not apollo_enrich_lead_in_place(lead):
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
    return {
        "monthly_cap":            APOLLO_PHONE_REVEAL_MONTHLY_CAP,
        "used_this_month":        used,
        "remaining":              max(0, APOLLO_PHONE_REVEAL_MONTHLY_CAP - used),
        "allowed_industries":     sorted(APOLLO_PHONE_REVEAL_INDUSTRIES),
        "credits_per_reveal_est": 8,
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
        r1 = req_lib.get(f"{SUPABASE_URL}/rest/v1/leads?select=status,score,callbackDate,createdAt",
                        headers=SB_HEADERS, timeout=30)
        r2 = req_lib.get(f"{SUPABASE_URL}/rest/v1/call_outcomes?select=outcome,calledBy&calledAt=gte.{today}T00:00:00",
                        headers=SB_HEADERS, timeout=30)
        sl = r1.json() if r1.status_code == 200 else []
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
            "contactRate": f"{(len([c for c in sc if c.get('outcome') in ('answered','interested','converted','callback')])/len(sc)*100):.1f}" if sc else "0.0",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/daily-summary")
def daily_summary():
    """Send end-of-day summary to Slack. Triggered by Railway cron or manually."""
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")

        # Calls today
        r_calls = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/call_outcomes?select=outcome,calledBy&calledAt=gte.{today}T00:00:00",
            headers=SB_HEADERS, timeout=30)
        calls = r_calls.json() if r_calls.status_code == 200 else []

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
            ],
            actions=[
                {"label": "Open LeadFlow", "url": app_url, "style": "primary"},
            ],
        )

        return {"sent": True, "calls": total_calls, "leads": total_leads, "emails": total_emails}
    except Exception as e:
        print(f"[daily-summary] error: {e}")
        return {"error": str(e)}

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

        # Calls (filtered by range or all-time)
        calls_url = f"{SUPABASE_URL}/rest/v1/call_outcomes?select=outcome,calledBy,calledAt,duration"
        if since:
            calls_url += f"&calledAt=gte.{since}T00:00:00"
        r1 = req_lib.get(calls_url, headers=SB_HEADERS, timeout=30)
        # Leads for assignment + population tracking
        r2 = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads?select=assignedTo,status,score,createdBy,createdAt",
            headers=SB_HEADERS, timeout=30)
        # Sessions for sign-in tracking
        sess_url = f"{SUPABASE_URL}/rest/v1/user_sessions?select=username,signed_in,signed_out"
        if since:
            sess_url += f"&signed_in=gte.{since}T00:00:00"
        sess_url += "&order=signed_in.desc"
        r3 = req_lib.get(sess_url, headers=SB_HEADERS, timeout=30)

        calls = r1.json() if r1.status_code == 200 else []
        leads = r2.json() if r2.status_code == 200 else []
        sessions = r3.json() if r3.status_code == 200 else []
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
            if outcome in ("answered", "interested", "converted", "callback"):
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
