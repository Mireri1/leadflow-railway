"""
LeadFlow Railway Backend — Google Places scraper
"""

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt, os, re, time, json as json_lib, requests as req_lib
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
APOLLO_SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/search"

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

class ScrapeRequest(BaseModel):
    industry:  str
    industries: Optional[str] = ""   # comma-separated list for multi-industry
    state:     Optional[str] = ""
    cities:    Optional[str] = ""
    limit:     Optional[int] = 25
    source:    Optional[str] = "places"

# ── Apollo.io integration ───────────────────────────────────────────────────
# Pulls decision-maker contacts (name + title + direct email/phone) from
# Apollo's People Search API. Uses search-only flow — no separate enrich
# call — so credit cost is whatever Apollo charges for a search hit on
# your plan tier (Pro = 4,000 credits/seat/month).

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
    titles:        Optional[str] = ""   # comma-sep e.g. "Facility Manager,Director of Operations"
    industries:    Optional[str] = ""   # comma-sep keywords e.g. "Hospital,Education"
    locations:     Optional[str] = ""   # comma-sep e.g. "California, US" or "Phoenix, AZ"
    employee_min:  Optional[int] = 50
    employee_max:  Optional[int] = 500
    per_page:      Optional[int] = 25   # Apollo caps at 100
    page:          Optional[int] = 1

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

    saved = save_to_supabase(leads)
    audit_log(user, "scrape_leads", "lead", None, {
        "industries": [k[0] for k in keywords], "state": body.state,
        "cities": body.cities, "found": len(leads), "saved": saved,
        "cache_hits": len(cache_hits), "uncached_combos": uncached})
    print(f"[SCRAPE] Saved {saved} leads (cache_hits={len(cache_hits)}/{len(all_combos)})")

    # Slack notification
    if saved > 0:
        app_url = os.getenv("APP_URL", "https://leadflow-railway-production.up.railway.app")
        send_slack(
            "🔍 LeadFlow Scrape Complete",
            f"*{user}* scraped *{saved}* new leads.",
            fields=[
                {"label": "Industries", "value": ", ".join(k[0] for k in keywords)},
                {"label": "Location", "value": body.state or "All"},
                {"label": "Leads Found", "value": f":busts_in_silhouette: {len(leads)}"},
                {"label": "New Saved", "value": f":white_check_mark: {saved}"},
            ],
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
                f"{SUPABASE_URL}/rest/v1/leads?id=eq.{lead_id}&select=assignedTo",
                headers=SB_HEADERS, timeout=30)
            rows = lr.json() if lr.status_code == 200 else []
            if rows and not rows[0].get("assignedTo"):
                req_lib.patch(
                    f"{SUPABASE_URL}/rest/v1/leads?id=eq.{lead_id}",
                    headers=SB_HEADERS,
                    json={"assignedTo": caller},
                    timeout=30)
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

    titles     = [t.strip() for t in (body.titles or "").split(",") if t.strip()]
    industries = [i.strip() for i in (body.industries or "").split(",") if i.strip()]
    locations  = [l.strip() for l in (body.locations or "").split(",") if l.strip()]
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

    leads = []
    skipped_no_company = 0
    skipped_no_phone   = 0
    for person in people:
        lead = apollo_person_to_lead(person, user)
        if not lead.get("company"):
            skipped_no_company += 1
            continue
        # Phone is the whole point — skip contacts with neither phone nor email
        if not lead.get("phone") and not lead.get("email"):
            skipped_no_phone += 1
            continue
        leads.append(lead)

    saved = save_to_supabase(leads) if leads else 0

    audit_log(user, "apollo_pull", "lead", None, {
        "titles": titles, "industries": industries, "locations": locations,
        "employee_range": f"{body.employee_min}-{body.employee_max}",
        "page": payload["page"], "per_page": per_page,
        "returned": len(people), "qualified": len(leads), "saved": saved,
        "skipped_no_company": skipped_no_company,
        "skipped_no_contact": skipped_no_phone,
        "total_entries":      pagination.get("total_entries"),
        "total_pages":        pagination.get("total_pages"),
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
        "skipped":         {"no_company": skipped_no_company, "no_contact": skipped_no_phone},
        "total_available": pagination.get("total_entries"),
        "page":            pagination.get("page"),
        "total_pages":     pagination.get("total_pages"),
    }

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
