"""
LeadFlow Railway Backend — Google Places scraper
"""

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt, os, re, time, json as json_lib, requests as req_lib, smtplib
from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel
from urllib.parse import quote as url_quote
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

SECRET_KEY      = os.getenv("SECRET_KEY",      "leadflow-secret")
TEAM_PASSWORD   = os.getenv("TEAM_PASSWORD",   "LeadFlow2024")
ADMIN_PASSWORD  = os.getenv("ADMIN_PASSWORD",  "LeadFlowAdmin2024!")
ADMIN_USERS     = set(u.strip().lower() for u in os.getenv("ADMIN_USERS", "eric").split(",") if u.strip())
BLOCKED_USERS   = set(u.strip().lower() for u in os.getenv("BLOCKED_USERS", "").split(",") if u.strip())
ALGORITHM       = "HS256"

SUPABASE_URL  = os.getenv("SUPABASE_URL",  "")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY",  "")
# Service role key bypasses RLS — needed for login_log, audit_log, user_sessions
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", SUPABASE_KEY)
GOOGLE_KEY    = os.getenv("GOOGLE_API_KEY", "")

# ── Email config (Gmail SMTP for outreach) ──────────────────────────────────────
OUTREACH_EMAIL     = os.getenv("OUTREACH_EMAIL", "connect@visioncleaningcompanyllc.com")
OUTREACH_EMAIL_PWD = os.getenv("OUTREACH_EMAIL_PASSWORD", "")  # Gmail app password
SMTP_HOST          = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT          = int(os.getenv("SMTP_PORT", "587"))

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
    return {"username": user}

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

def scrape_google_places(keyword="health clinic", state="", limit=25):
    leads = []
    # Force US context in query
    location_part = state if state else "USA"
    query = f"{keyword} {location_part}".strip()
    print(f"[PLACES] query: '{query}' limit: {limit}")

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
    return leads[:limit]

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

@app.post("/api/scrape")
def run_scrape(body: ScrapeRequest, user: str = Depends(verify_token)):
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

    print(f"[SCRAPE] industries: {[k[0] for k in keywords]}, locations: {locations}, limit: {limit} ({per_combo}/combo, {combos} combos)")

    all_leads = []
    seen_phones = set()
    for ind_name, keyword in keywords:
        for location in locations:
            batch = scrape_google_places(keyword=keyword, state=location, limit=per_combo)
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

    # Tag all scraped leads with the user who ran the search
    for lead in leads:
        lead["createdBy"] = user

    saved = save_to_supabase(leads)
    audit_log(user, "scrape_leads", "lead", None, {
        "industries": [k[0] for k in keywords], "state": body.state,
        "cities": body.cities, "found": len(leads), "saved": saved})
    print(f"[SCRAPE] Saved {saved} leads")
    return {"leads": leads, "count": len(leads), "saved": saved}

@app.get("/api/cities/autocomplete")
def city_autocomplete(q: str = "", state: str = "", user: str = Depends(verify_token)):
    """Return city suggestions from Google Places Autocomplete"""
    if not q or len(q) < 2:
        return {"suggestions": []}
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
        data = r.json()
        if data.get("status") != "OK":
            return {"suggestions": []}
        cities = []
        for pred in data.get("predictions", [])[:8]:
            terms = pred.get("terms", [])
            city_name = terms[0]["value"] if terms else pred.get("structured_formatting", {}).get("main_text", "")
            if city_name and city_name not in cities:
                cities.append(city_name)
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
            # Batch fetch lead info (up to 50)
            for batch_start in range(0, min(len(lead_ids), 50), 10):
                batch = lead_ids[batch_start:batch_start+10]
                ids_filter = ",".join(batch)
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
    """Send an email via SMTP (Gmail). Returns (success, error_message)."""
    if not OUTREACH_EMAIL_PWD:
        return False, "Email not configured. Set OUTREACH_EMAIL_PASSWORD env var."
    msg = MIMEMultipart("alternative")
    msg["From"] = f"Vision Cleaning Company <{OUTREACH_EMAIL}>"
    msg["To"] = f"{to_name} <{to_email}>" if to_name else to_email
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    # Plain text fallback
    plain = re.sub(r"<[^>]+>", "", body_html).strip()
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(body_html, "html"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(OUTREACH_EMAIL, OUTREACH_EMAIL_PWD)
            server.send_message(msg)
        return True, None
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
    success, err = send_smtp_email(req.to_email, req.to_name, req.subject, req.body)
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

# ── End Email ────────────────────────────────────────────────────────────────────

frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.exists(frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str):
        return FileResponse(os.path.join(frontend_dist, "index.html"))
