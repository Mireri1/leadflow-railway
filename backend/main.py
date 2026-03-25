"""
LeadFlow Railway Backend — Google Places scraper
"""

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt, os, re, time, requests as req_lib
from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel

SECRET_KEY      = os.getenv("SECRET_KEY",      "leadflow-secret")
TEAM_PASSWORD   = os.getenv("TEAM_PASSWORD",   "LeadFlow2024")
ADMIN_PASSWORD  = os.getenv("ADMIN_PASSWORD",  "LeadFlowAdmin2024!")
ADMIN_USERS     = set(u.strip().lower() for u in os.getenv("ADMIN_USERS", "eric").split(",") if u.strip())
ALGORITHM       = "HS256"

SUPABASE_URL  = os.getenv("SUPABASE_URL",  "https://ucpwpjokyconwzwqvdad.supabase.co")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY",  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVjcHdwam9reWNvbnd6d3F2ZGFkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzE5NjMxMjksImV4cCI6MjA4NzUzOTEyOX0.j1Ibnm3rhOnvdnfS3WPf2RLDH91wopuJbTByQmwVZ7w")
GOOGLE_KEY    = os.getenv("GOOGLE_API_KEY", "AIzaSyAqWVfEpEgbtyraNvE-MR_FEG_qPqyMHWU")

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=ignore-duplicates,return=representation"
}

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
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
    except:
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

@app.post("/api/auth/login")
def login(req: LoginRequest):
    is_admin = req.username.strip().lower() in ADMIN_USERS
    # Admins can use either the admin password or team password
    if is_admin:
        if req.password not in (ADMIN_PASSWORD, TEAM_PASSWORD):
            raise HTTPException(status_code=401, detail="Invalid password")
        role = "admin"
    else:
        if req.password != TEAM_PASSWORD:
            raise HTTPException(status_code=401, detail="Invalid password")
        role = "caller"
    token = create_token(req.username, role)
    return {"token": token, "username": req.username, "role": role}

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

def scrape_google_places(keyword="health clinic", state="", limit=25):
    leads = []
    query = f"{keyword} {state}".strip() if state else keyword
    print(f"[PLACES] query: '{query}' limit: {limit}")

    params = {
        "query": query,
        "key": GOOGLE_KEY,
        "type": "establishment",
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
                parts = addr.split(",")
                city  = parts[-3].strip() if len(parts) >= 3 else ""
                st    = parts[-2].strip().split(" ")[0] if len(parts) >= 2 else state

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

                if lead["company"]:
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
    state:     Optional[str] = ""
    limit:     Optional[int] = 25
    source:    Optional[str] = "places"

@app.post("/api/scrape")
def run_scrape(body: ScrapeRequest, user: str = Depends(verify_token)):
    keyword = INDUSTRY_MAP.get(body.industry, body.industry.lower())
    limit   = min(max(body.limit or 25, 5), 60)
    print(f"[SCRAPE] {body.industry} → '{keyword}', state: {body.state}, limit: {limit}")
    leads = scrape_google_places(keyword=keyword, state=body.state or "", limit=limit)
    saved = save_to_supabase(leads)
    print(f"[SCRAPE] Saved {saved} leads")
    return {"leads": leads, "count": len(leads), "saved": saved}

@app.get("/api/industries")
def get_industries():
    return {"industries": list(INDUSTRY_MAP.keys())}

@app.get("/api/leads")
def list_leads(status: str = "", search: str = "", sort: str = "score",
               callbacks: str = "", user: str = Depends(verify_token)):
    try:
        url = f"{SUPABASE_URL}/rest/v1/leads?select=*"
        if status:   url += f"&status=eq.{status}"
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
        r = req_lib.post(f"{SUPABASE_URL}/rest/v1/leads", headers=SB_HEADERS, json=lead, timeout=30)
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/leads/import")
def import_leads(leads: list, user: str = Depends(verify_token)):
    try:
        for lead in leads:
            lead["score"] = score_lead(lead)
        r = req_lib.post(f"{SUPABASE_URL}/rest/v1/leads", headers=SB_HEADERS, json=leads, timeout=30)
        saved = r.json()
        return {"count": len(saved) if isinstance(saved, list) else 0}
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
        req_lib.delete(f"{SUPABASE_URL}/rest/v1/leads?id=eq.{lead_id}",
                      headers={**SB_HEADERS, "Prefer":""}, timeout=30)
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
        r = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/app_settings?key=eq.daily_quota&select=value",
            headers=SB_HEADERS, timeout=10)
        rows = r.json() if r.status_code == 200 else []
        quota = int(rows[0]["value"]) if isinstance(rows, list) and rows else DEFAULT_QUOTA

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
    try:
        new_quota = int(body.get("quota", DEFAULT_QUOTA))
        if new_quota < 1 or new_quota > 500:
            raise HTTPException(status_code=400, detail="Quota must be 1-500")
        # Upsert into app_settings
        req_lib.post(
            f"{SUPABASE_URL}/rest/v1/app_settings",
            headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"},
            json={"key": "daily_quota", "value": str(new_quota)},
            timeout=10)
        return {"quota": new_quota}
    except HTTPException:
        raise
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
        for c in qualified:
            lid = c.get("leadId")
            if lid:
                lr = req_lib.get(
                    f"{SUPABASE_URL}/rest/v1/leads?id=eq.{lid}&select=company,firstName,lastName,phone,industry,state,score,status,assignedTo",
                    headers=SB_HEADERS, timeout=10)
                leads_data = lr.json() if lr.status_code == 200 else []
                c["leads"] = leads_data[0] if leads_data else None
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
        recycled = 0
        for lead in stale:
            if lead.get("assignedTo"):
                req_lib.patch(
                    f"{SUPABASE_URL}/rest/v1/leads?id=eq.{lead['id']}",
                    headers=SB_HEADERS,
                    json={"assignedTo": "", "updatedAt": datetime.utcnow().isoformat()},
                    timeout=10)
                recycled += 1
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
        count = 0
        for lead in leads:
            req_lib.patch(
                f"{SUPABASE_URL}/rest/v1/leads?id=eq.{lead['id']}",
                headers=SB_HEADERS,
                json={"assignedTo": to_rep, "updatedAt": datetime.utcnow().isoformat()},
                timeout=10)
            count += 1
        dest = to_rep if to_rep else "unassigned pool"
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
def get_leaderboard(user: str = Depends(verify_token)):
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        # All-time calls
        r1 = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/call_outcomes?select=outcome,calledBy,calledAt,duration",
            headers=SB_HEADERS, timeout=30)
        # Leads for assignment tracking
        r2 = req_lib.get(
            f"{SUPABASE_URL}/rest/v1/leads?select=assignedTo,status,score",
            headers=SB_HEADERS, timeout=30)
        calls = r1.json() if r1.status_code == 200 else []
        leads = r2.json() if r2.status_code == 200 else []

        # Build per-user call stats
        users = {}
        for c in calls:
            name = c.get("calledBy") or "Unknown"
            if name not in users:
                users[name] = {"name": name, "total_calls": 0, "calls_today": 0,
                               "conversions": 0, "interested": 0, "no_answer": 0,
                               "voicemail": 0, "callbacks": 0, "contacted": 0,
                               "talk_time": 0}
            u = users[name]
            u["total_calls"] += 1
            u["talk_time"] += c.get("duration") or 0
            if (c.get("calledAt") or "").startswith(today):
                u["calls_today"] += 1
            outcome = c.get("outcome", "")
            if outcome in ("answered", "interested", "converted", "callback"):
                u["contacted"] += 1
            if outcome == "converted":    u["conversions"] += 1
            elif outcome == "interested": u["interested"]  += 1
            elif outcome == "no_answer":  u["no_answer"]   += 1
            elif outcome == "voicemail":  u["voicemail"]   += 1
            elif outcome == "callback":   u["callbacks"]   += 1

        # Add lead assignment counts
        for l in leads:
            name = l.get("assignedTo") or ""
            if name and name in users:
                users[name].setdefault("leads_assigned", 0)
                users[name]["leads_assigned"] = users[name].get("leads_assigned", 0) + 1

        # Compute rates per user
        result = []
        for u in users.values():
            tc = u["total_calls"]
            u["conv_rate"] = f"{(u['conversions']/tc*100):.1f}" if tc else "0.0"
            u["contact_rate"] = f"{(u['contacted']/tc*100):.1f}" if tc else "0.0"
            u["avg_talk_time"] = round(u["talk_time"] / tc) if tc else 0
            u["leads_assigned"] = u.get("leads_assigned", 0)
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
        script["created_at"] = datetime.utcnow().isoformat()
        script["updated_at"] = datetime.utcnow().isoformat()
        r = req_lib.post(f"{SUPABASE_URL}/rest/v1/scripts", headers=SB_HEADERS, json=script, timeout=30)
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/scripts/{script_id}")
def update_script(script_id: str, data: dict, user: str = Depends(verify_token)):
    try:
        data["updated_at"] = datetime.utcnow().isoformat()
        r = req_lib.patch(f"{SUPABASE_URL}/rest/v1/scripts?id=eq.{script_id}",
                         headers=SB_HEADERS, json=data, timeout=30)
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/scripts/{script_id}")
def delete_script(script_id: str, user: str = Depends(verify_token)):
    try:
        req_lib.patch(f"{SUPABASE_URL}/rest/v1/scripts?id=eq.{script_id}",
                     headers=SB_HEADERS, json={"is_active": False}, timeout=30)
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

frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.exists(frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str):
        return FileResponse(os.path.join(frontend_dist, "index.html"))
