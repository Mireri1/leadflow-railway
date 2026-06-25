import React, { useState, useEffect, useRef, useCallback } from "react"

const API_BASE = ""

const STATES = [
  "","AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
  "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
  "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
  "VA","WA","WV","WI","WY"
]

// Used to convert state codes to Apollo's expected location format:
//   "California, US" / "Phoenix, AZ" — bare "California" matches UK/AU regions too.
const STATE_NAMES = {
  AL:"Alabama",AK:"Alaska",AZ:"Arizona",AR:"Arkansas",CA:"California",CO:"Colorado",
  CT:"Connecticut",DE:"Delaware",FL:"Florida",GA:"Georgia",HI:"Hawaii",ID:"Idaho",
  IL:"Illinois",IN:"Indiana",IA:"Iowa",KS:"Kansas",KY:"Kentucky",LA:"Louisiana",
  ME:"Maine",MD:"Maryland",MA:"Massachusetts",MI:"Michigan",MN:"Minnesota",MS:"Mississippi",
  MO:"Missouri",MT:"Montana",NE:"Nebraska",NV:"Nevada",NH:"New Hampshire",NJ:"New Jersey",
  NM:"New Mexico",NY:"New York",NC:"North Carolina",ND:"North Dakota",OH:"Ohio",OK:"Oklahoma",
  OR:"Oregon",PA:"Pennsylvania",RI:"Rhode Island",SC:"South Carolina",SD:"South Dakota",
  TN:"Tennessee",TX:"Texas",UT:"Utah",VT:"Vermont",VA:"Virginia",WA:"Washington",
  WV:"West Virginia",WI:"Wisconsin",WY:"Wyoming",
}

const STATUS_OPTIONS = [
  { value:"new",                   label:"New",                   color:"#a3a6ff" },
  { value:"called",                label:"Called",                color:"#ffe083" },
  { value:"no_answer",             label:"No Answer",             color:"#a3aac4" },
  { value:"interested",            label:"Interested",            color:"#69f6b8" },
  { value:"interested_no_dm",      label:"Interested · No DM",    color:"#5ec8ff" },
  { value:"not_interested",        label:"Not Interested",        color:"#ff6e84" },
  { value:"callback",              label:"Callback",              color:"#8b5cf6" },
  { value:"converted",             label:"Converted",             color:"#06d6a0" },
  { value:"awaiting_email_reply",  label:"Awaiting Email Reply",  color:"#a3a6ff" },
  { value:"do_not_contact",        label:"Do Not Contact",        color:"#ff6e84" },
]
// Hours to suppress a lead from the dialer after a no-answer/voicemail.
// Caller's "leads repeating" complaint partly came from re-dialing the same
// number within the same shift. Fresh + long-rested still come up; only
// recent dead-ends get snoozed. Configurable from the dialer UI.
const DIALER_SNOOZE_HOURS_DEFAULT = 4

// Statuses callers should NEVER auto-dial — used to filter the dialer queue
// and to show a "don't call" warning in the call modal.
const NO_DIAL_STATUSES = new Set(["awaiting_email_reply","do_not_contact"])

const CALL_OUTCOMES = [
  { value:"answered",       label:"Answered" },
  { value:"no_answer",      label:"No Answer" },
  { value:"voicemail",      label:"Left Voicemail" },
  { value:"callback",       label:"Requested Callback" },
  { value:"interested",     label:"Interested" },
  { value:"interested_no_dm", label:"Interested · No DM" },
  { value:"not_interested", label:"Not Interested" },
  { value:"converted",      label:"Converted!" },
]

const PRIMARY_OUTCOMES = [
  { value:"no_answer", label:"No Answer", color:"#40485d", icon:"📵" },
  { value:"voicemail", label:"Voicemail", color:"#a3aac4", icon:"📨" },
  { value:"answered",  label:"Answered",  color:"#69f6b8", icon:"📞" },
]

const SECONDARY_OUTCOMES = [
  { value:"not_interested", label:"Not Interested", color:"#40485d", icon:"👎", needsQual:false },
  { value:"interested",     label:"Interested",     color:"#ffe083", icon:"👍", needsQual:true },
  // Gatekeeper warm: prospect/gatekeeper seems interested but the caller never
  // reached the actual decision-maker. Tracked as engaged (surfaces for
  // follow-up, exempt from the once-called recycle) but no qual is forced
  // since the DM conversation hasn't happened yet.
  { value:"interested_no_dm", label:"Interested · No DM", color:"#5ec8ff", icon:"🚪", needsQual:false,
    hint:"Someone seemed interested, but you didn't reach the decision-maker (e.g. gatekeeper, assistant). We'll keep it warm for a follow-up." },
  { value:"callback",       label:"Callback",       color:"#8b5cf6", icon:"📅", needsQual:true },
  { value:"converted",      label:"Converted!",     color:"#69f6b8", icon:"🎉", needsQual:true },
]

const CALLBACK_REASONS = [
  "Decision Maker Unavailable",
  "Requested Call Back Later",
  "Needs Internal Approval",
  "Timing Not Right",
  "Gatekeeper — Need Direct Line",
  "Other",
]

const INDUSTRIES = [
  "Healthcare","Home Health Care","Hospitals","Nursing Facilities","Medical Equipment",
  "Software","IT Services","Consulting","Accounting / CPA","Legal Services",
  "Marketing","Staffing / HR","Engineering","Insurance","Real Estate",
  "Logistics","Construction","Manufacturing","Finance","Education","Other",
]

const emptyForm = {
  firstName:"",lastName:"",title:"",company:"",industry:"",phone:"",
  email:"",website:"",address:"",city:"",state:"",status:"new",
  assignedTo:"",notes:"",source:"",callbackDate:"",
}

// NANP-format check for US phones — mirrors backend is_valid_us_phone().
// Used to skip bad-phone leads from the dialer view so the caller never
// sees a guaranteed-fail dial. Same rules as backend (10/11 digits, area
// code + exchange first digit 2-9, no all-same, no 555 exchange).
function isValidUsPhone(phone){
  if(!phone) return false
  let d = String(phone).replace(/\D/g,"")
  if(d.length===11 && d[0]==="1") d = d.slice(1)
  if(d.length!==10) return false
  if(d[0]==="0"||d[0]==="1") return false
  if(d[3]==="0"||d[3]==="1") return false
  if(new Set(d).size===1) return false
  if(d.slice(3,6)==="555") return false
  return true
}

// Rough US-state → IANA timezone. Pacific time states use Los_Angeles, etc.
// Used by the dialer's "local time" badge so the caller doesn't dial MD at
// 5am Pacific. Some states span two zones — we pick the dominant one (e.g.
// FL→Eastern even though the panhandle is Central). Good enough for "is it
// safe to call?", not for legal compliance.
const STATE_TZ = {
  AL:"America/Chicago",AK:"America/Anchorage",AZ:"America/Phoenix",AR:"America/Chicago",
  CA:"America/Los_Angeles",CO:"America/Denver",CT:"America/New_York",DE:"America/New_York",
  FL:"America/New_York",GA:"America/New_York",HI:"Pacific/Honolulu",ID:"America/Boise",
  IL:"America/Chicago",IN:"America/Indiana/Indianapolis",IA:"America/Chicago",KS:"America/Chicago",
  KY:"America/New_York",LA:"America/Chicago",ME:"America/New_York",MD:"America/New_York",
  MA:"America/New_York",MI:"America/Detroit",MN:"America/Chicago",MS:"America/Chicago",
  MO:"America/Chicago",MT:"America/Denver",NE:"America/Chicago",NV:"America/Los_Angeles",
  NH:"America/New_York",NJ:"America/New_York",NM:"America/Denver",NY:"America/New_York",
  NC:"America/New_York",ND:"America/Chicago",OH:"America/New_York",OK:"America/Chicago",
  OR:"America/Los_Angeles",PA:"America/New_York",RI:"America/New_York",SC:"America/New_York",
  SD:"America/Chicago",TN:"America/Chicago",TX:"America/Chicago",UT:"America/Denver",
  VT:"America/New_York",VA:"America/New_York",WA:"America/Los_Angeles",WV:"America/New_York",
  WI:"America/Chicago",WY:"America/Denver",DC:"America/New_York",
}
function localTimeAt(state){
  const tz = STATE_TZ[(state||"").toUpperCase()]
  if(!tz) return null
  try{
    const now = new Date()
    const t = now.toLocaleTimeString("en-US",{timeZone:tz,hour:"numeric",minute:"2-digit",hour12:true})
    const hourNum = parseInt(now.toLocaleTimeString("en-US",{timeZone:tz,hour:"numeric",hour12:false}),10)
    const tzShort = now.toLocaleTimeString("en-US",{timeZone:tz,timeZoneName:"short"}).split(" ").pop()
    return {time:t, hour:hourNum, tzShort, tz}
  }catch{ return null }
}

// "3h", "2d", "5mo" — compact relative-past for last-contacted labels.
// Lead cards and the dialer use this so the caller knows whether a lead
// is freshly worked vs. been resting for a month.
function pastAgo(dateStr){
  if(!dateStr) return ""
  const ms = Date.now() - new Date(dateStr).getTime()
  if(!isFinite(ms) || ms < 0) return ""
  const mins = Math.floor(ms/60000)
  if(mins < 1)   return "just now"
  if(mins < 60)  return `${mins}m`
  const hrs = Math.floor(mins/60)
  if(hrs < 24)   return `${hrs}h`
  const days = Math.floor(hrs/24)
  if(days < 30)  return `${days}d`
  const mos = Math.floor(days/30)
  if(mos < 12)   return `${mos}mo`
  return `${Math.floor(mos/12)}y`
}

function scoreLead(lead) {
  let s = 5
  if ((lead.company||"").trim())   s+=8
  if ((lead.phone||"").trim())     s+=8
  if ((lead.email||"").trim())     s+=6
  if ((lead.firstName||"").trim()) s+=3
  if ((lead.lastName||"").trim())  s+=3
  if ((lead.industry||"").trim())  s+=2
  const sta = { called:5,no_answer:2,callback:15,interested:30,not_interested:-10,converted:40 }
  s+=sta[lead.status]||0
  return Math.max(0,Math.min(100,s))
}
function scoreColor(s){return s>=75?"#ff6e84":s>=50?"#ffe083":s>=25?"#a3a6ff":"#40485d"}
function scoreLabel(s){return s>=75?"Hot":s>=50?"Warm":s>=25?"Cool":"Cold"}

function getUser()  { return localStorage.getItem("lf_user") || "" }
function getToken() { return localStorage.getItem("lf_token") || "" }
function getRole()  { return localStorage.getItem("lf_role") || "caller" }
function isLoggedIn(){ return !!localStorage.getItem("lf_token") }
function isAdmin()  { return getRole() === "admin" }

async function api(path, opts={}) {
  const token = getToken()
  const res = await fetch(`${API_BASE}${path}`, {
    ...opts,
    headers: {
      "Content-Type":"application/json",
      ...(token ? {"Authorization":`Bearer ${token}`} : {}),
      ...(opts.headers||{})
    }
  })
  if (res.status===401) { localStorage.clear(); window.location.reload(); return }
  const body = await res.json()
  if (!res.ok) throw new Error(body.detail || JSON.stringify(body))
  return body
}

function parseCSV(text) {
  const lines = text.trim().split("\n"); if (lines.length<2) return []
  const header = lines[0].split(",").map(h=>h.replace(/^"|"$/g,"").trim()
    .replace(/\s+/g,"").replace("firstname","firstName").replace("lastname","lastName").replace("assignedto","assignedTo"))
  return lines.slice(1).filter(l=>l.trim()).map(line=>{
    const vals=[]; let cur="",inQ=false
    for(const ch of line){ if(ch==='"'){inQ=!inQ;continue} if(ch===','&&!inQ){vals.push(cur);cur="";continue} cur+=ch }
    vals.push(cur)
    const lead={...emptyForm}
    header.forEach((k,i)=>{ if(k in lead&&vals[i]!==undefined) lead[k]=vals[i].trim() })
    return lead
  }).filter(l=>l.company||l.phone||l.firstName)
}

const CSS = `
  @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=Inter:wght@300;400;500;600;700&display=swap');
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{background:#060e20;color:#dee5ff;font-family:'Inter',sans-serif;font-size:14px}
  ::-webkit-scrollbar{width:4px}::-webkit-scrollbar-track{background:#060e20}::-webkit-scrollbar-thumb{background:#40485d;border-radius:4px}
  input,select,textarea,button{font-family:inherit}
  .ff{display:flex;flex-direction:column;gap:4px}
  .ff label{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:#a3aac4}
  .ff input,.ff select,.ff textarea{background:#000011;border:1px solid #40485d50;color:#dee5ff;padding:9px 11px;border-radius:8px;font-size:12px;outline:none;transition:border-color .15s}
  .ff input:focus,.ff select:focus,.ff textarea:focus{border-color:#a3a6ff;box-shadow:0 0 0 3px #a3a6ff15}
  .ff select option{background:#0f1930}
  .btn{cursor:pointer;border:none;font-family:inherit;font-size:13px;font-weight:600;letter-spacing:.02em;transition:all .15s;border-radius:8px}
  .btn-p{background:linear-gradient(135deg,#a3a6ff 0%,#6063ee 100%);color:#000011;padding:9px 20px}.btn-p:hover{opacity:.88;transform:translateY(-1px)}
  .btn-g{background:transparent;color:#a3aac4;padding:8px 14px;border:1px solid #40485d50}.btn-g:hover{background:#192540;color:#dee5ff}
  .btn-r{background:transparent;color:#ff6e84;padding:5px 10px;border:1px solid #ff6e8425;font-size:11px}.btn-r:hover{background:#ff6e8412}
  .btn-gr{background:#006c49;color:#69f6b8;padding:9px 20px}.btn-gr:hover{background:#00805a}
  .btn-amber{background:#ffe08318;color:#ffe083;border:1px solid #ffe08330;padding:6px 14px;font-size:11px}.btn-amber:hover{background:#ffe08328}
  .card{background:#0f1930;border-radius:12px;padding:18px 22px}
  .pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:11px;letter-spacing:.04em;font-weight:600}
  .qs{background:transparent;border:1px solid #40485d40;font-size:10px;padding:3px 7px;border-radius:5px;cursor:pointer;font-family:inherit;transition:all .1s;color:#a3aac4}.qs:hover{background:#192540;color:#dee5ff}
  .modal-bg{position:fixed;inset:0;background:#00000095;backdrop-filter:blur(6px);z-index:200;display:flex;align-items:center;justify-content:center;padding:20px}
  .modal{background:#0f1930;border:1px solid #40485d40;border-radius:16px;padding:28px;width:100%;max-width:600px;max-height:90vh;overflow-y:auto}
  .toast{position:fixed;bottom:24px;right:24px;padding:11px 18px;background:#141f38;border:1px solid #40485d;border-radius:10px;font-size:13px;z-index:9999;animation:fadeUp .2s ease}
  @keyframes fadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
  .dropzone{background:#000011;border:2px dashed #40485d;border-radius:12px;padding:36px;text-align:center;cursor:pointer;transition:all .2s}.dropzone:hover{border-color:#a3a6ff;background:#a3a6ff08}
  .sel{background:#0f1930;border:1px solid #40485d40;color:#a3aac4;padding:8px 12px;border-radius:8px;font-size:13px;font-family:inherit;cursor:pointer;outline:none;transition:border-color .15s}.sel:focus,.sel:hover{border-color:#a3a6ff50;color:#dee5ff}
  .src-tag{font-size:9px;background:#192540;color:#a3aac4;padding:2px 6px;border-radius:4px;letter-spacing:.05em}
  .finder{background:linear-gradient(135deg,#0f1930 0%,#091328 100%);border:1px solid #a3a6ff25;border-radius:14px;padding:24px;margin-bottom:24px}
  .finder-title{font-family:'Space Grotesk',sans-serif;font-size:17px;font-weight:700;color:#dee5ff;margin-bottom:4px}
  .finder-sub{font-size:12px;color:#a3aac4;margin-bottom:20px}
  .range-wrap{display:flex;flex-direction:column;gap:6px}
  .range-wrap input[type=range]{-webkit-appearance:none;width:100%;height:4px;border-radius:2px;background:#40485d;outline:none}
  .range-wrap input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:16px;height:16px;border-radius:50%;background:#a3a6ff;cursor:pointer}
  .pulse{animation:pulse 1.5s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  .lrow-cb{background:#8b5cf608!important}
  @media(max-width:1023px){.lg-sidebar{display:none!important}.lg-main{margin-left:0!important}}
  @media(max-width:767px){.mobile-nav{display:flex!important}.lg-topnav-tabs{display:none!important}}
`

// ─── Icons ───────────────────────────────────────────────────────────────────

function IconDashboard(){return<svg width={18} height={18} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"/></svg>}
function IconPeople(){return<svg width={18} height={18} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/></svg>}
function IconPhone(){return<svg width={18} height={18} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"/></svg>}
function IconChart(){return<svg width={18} height={18} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>}
function IconHistory(){return<svg width={18} height={18} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>}
function IconHelp(){return<svg width={18} height={18} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>}
function IconPerson(){return<svg width={18} height={18} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>}
function IconUpload(){return<svg width={18} height={18} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/></svg>}
function IconSearch(){return<svg width={16} height={16} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>}
function IconBell(){return<svg width={20} height={20} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"/></svg>}
function IconSettings(){return<svg width={20} height={20} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>}
function IconEdit(){return<svg width={17} height={17} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>}
function IconTrash(){return<svg width={16} height={16} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>}
function IconMail(){return<svg width={16} height={16} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>}
function IconCallFwd(){return<svg width={16} height={16} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M17 8l4 4m0 0l-4 4m4-4H3"/></svg>}
function IconFilter(){return<svg width={14} height={14} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M3 4h18M7 8h10M11 12h2"/></svg>}
function IconChevLeft(){return<svg width={20} height={20} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7"/></svg>}
function IconChevRight(){return<svg width={20} height={20} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7"/></svg>}
function IconPlus(){return<svg width={20} height={20} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}><path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4"/></svg>}
function IconCalendarFar(){return<svg width={18} height={18} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>}
function IconClipCheck(){return<svg width={18} height={18} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}><path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"/></svg>}

// ─── ScoreRing (redesigned as dot + label) ───────────────────────────────────

function ScoreRing({score=0}){
  const c=scoreColor(score), l=scoreLabel(score)
  return(
    <div style={{display:"flex",alignItems:"center",gap:7}}>
      <span style={{width:10,height:10,borderRadius:"50%",background:c,
        boxShadow:`0 0 8px ${c}99`,flexShrink:0,display:"inline-block"}}/>
      <span style={{fontSize:13,fontWeight:600,color:c,fontFamily:"'Space Grotesk',sans-serif"}}>{l}</span>
    </div>
  )
}

// ─── Best-region nudge banner ────────────────────────────────────────────────
// Renders only when the server returned a state currently in its peak-pickup
// window and the caller hasn't dismissed that state's banner today.
function BestRegionBanner({region, dismissed, onSwitch, onDismiss, switching}){
  if(!region || !region.state) return null
  const today = new Date().toISOString().slice(0,10)
  if(dismissed && dismissed[region.state] === today) return null
  const hr = region.current_local_hour
  const hrLabel = hr==null ? "" :
    (hr===0?"12am":hr<12?`${hr}am`:hr===12?"12pm":`${hr-12}pm`)
  return (
    <div style={{
      background:"linear-gradient(135deg,#1a3a5c 0%,#2a4a6c 100%)",
      border:"1px solid #4a8ac440", borderRadius:14,
      padding:"14px 18px", marginBottom:20,
      display:"flex", alignItems:"center", gap:14, flexWrap:"wrap"}}>
      <span style={{fontSize:22}}>📞</span>
      <div style={{flex:"1 1 280px"}}>
        <div style={{color:"#dee5ff",fontWeight:600,fontSize:14,
                     fontFamily:"'Space Grotesk',sans-serif"}}>
          {region.state_name} is in its best connectivity window
          {hrLabel && ` (${hrLabel} local)`}
        </div>
        <div style={{color:"#a3aac4",fontSize:12,marginTop:3}}>
          {region.answer_rate}% answer rate this hour vs {region.state_avg_rate}% daily avg
          {" · "}{region.sample_size} calls of signal
          {region.queued_leads>0
            ? ` · ${region.queued_leads} leads ready`
            : " · queue empty — pull will run"}
        </div>
      </div>
      <div style={{display:"flex",gap:8,alignItems:"center"}}>
        <button className="btn btn-p" disabled={switching}
          onClick={()=>onSwitch(region.state)}>
          {switching ? "Switching…" : `Switch to ${region.state} →`}
        </button>
        <button onClick={()=>onDismiss(region.state)}
          style={{background:"transparent",border:"none",color:"#a3aac4",
                  fontSize:20,cursor:"pointer",padding:"0 6px"}}
          title="Hide for today">×</button>
      </div>
    </div>
  )
}

// ─── Login ───────────────────────────────────────────────────────────────────

function Login({onLogin}){
  const [name,setName]=useState("")
  const [pass,setPass]=useState("")
  const [err,setErr]=useState("")
  const [loading,setLoad]=useState(false)

  async function submit(e){
    e.preventDefault(); setErr(""); setLoad(true)
    try {
      const res = await api("/api/auth/login",{method:"POST",body:JSON.stringify({username:name,password:pass})})
      localStorage.setItem("lf_token", res.token)
      localStorage.setItem("lf_user",  res.username)
      localStorage.setItem("lf_role",  res.role || "caller")
      if(res.session_id) localStorage.setItem("lf_session_id", res.session_id)
      onLogin(res.username)
    } catch(ex){ setErr(ex.message) }
    finally{ setLoad(false) }
  }

  return(
    <div style={{minHeight:"100vh",display:"flex",alignItems:"center",justifyContent:"center",background:"#060e20"}}>
      <style>{CSS}</style>
      <div style={{width:340}}>
        <div style={{textAlign:"center",marginBottom:36}}>
          <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:32,fontWeight:700,color:"#a3a6ff",letterSpacing:"-.02em"}}>
            LeadFlow
          </div>
          <div style={{fontSize:11,color:"#40485d",letterSpacing:".12em",marginTop:6,textTransform:"uppercase"}}>B2B Cold Call Platform</div>
        </div>
        <div className="card" style={{border:"1px solid #a3a6ff25"}}>
          <form onSubmit={submit} style={{display:"flex",flexDirection:"column",gap:14}}>
            <div className="ff"><label>Your Name</label>
              <input value={name} onChange={e=>setName(e.target.value)} placeholder="e.g. Alice" autoFocus/>
            </div>
            <div className="ff"><label>Team Password</label>
              <input type="password" value={pass} onChange={e=>setPass(e.target.value)} placeholder="••••••••"/>
            </div>
            {err&&<div style={{color:"#ff6e84",fontSize:12}}>⚠ {err}</div>}
            <button type="submit" className="btn btn-p" style={{padding:"12px",fontSize:14,marginTop:4}} disabled={loading}>
              {loading?"Signing in…":"Sign In →"}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}

// ─── CityAutocomplete ───────────────────────────────────────────────────────

function CityAutocomplete({value, onChange, state, placeholder, style, disabled, multi}){
  const [suggestions, setSuggestions] = useState([])
  const [showDrop, setShowDrop] = useState(false)
  const [inputVal, setInputVal] = useState("")
  const debounceRef = useRef(null)
  const wrapRef = useRef(null)

  useEffect(()=>{
    function handleClick(e){ if(wrapRef.current && !wrapRef.current.contains(e.target)) setShowDrop(false) }
    document.addEventListener("mousedown", handleClick)
    return ()=>document.removeEventListener("mousedown", handleClick)
  },[])

  function handleInput(raw){
    if(multi){
      const parts = raw.split(",")
      const typing = parts.pop().trimStart()
      setInputVal(typing)
      onChange(raw)
      fetchSuggestions(typing)
    } else {
      onChange(raw)
      fetchSuggestions(raw)
    }
  }

  function fetchSuggestions(q){
    clearTimeout(debounceRef.current)
    if(!q || q.length < 2 || disabled){ setSuggestions([]); return }
    debounceRef.current = setTimeout(async()=>{
      try{
        const params = new URLSearchParams({q, ...(state?{state}:{})})
        const r = await api(`/api/cities/autocomplete?${params}`)
        setSuggestions(r.suggestions||[])
        setShowDrop((r.suggestions||[]).length > 0)
      }catch{ setSuggestions([]) }
    }, 250)
  }

  function pick(city){
    if(multi){
      const parts = value.split(",").map(s=>s.trim()).filter(Boolean)
      parts.pop()
      parts.push(city)
      onChange(parts.join(", ") + ", ")
      setInputVal("")
    } else {
      onChange(city)
    }
    setShowDrop(false)
    setSuggestions([])
  }

  return(
    <div ref={wrapRef} style={{position:"relative",...(style?.wrapper||{})}}>
      <input value={value} onChange={e=>handleInput(e.target.value)}
        onFocus={()=>suggestions.length>0&&setShowDrop(true)}
        placeholder={placeholder||"Type a city..."}
        disabled={disabled}
        style={{width:"100%",background:disabled?"#0a0f1a":"#000011",border:"1px solid #40485d30",
          borderRadius:8,padding:"8px 12px",color:disabled?"#40485d":"#dee5ff",
          fontSize:13,fontFamily:"'Inter',sans-serif",outline:"none",
          cursor:disabled?"not-allowed":"text",...(style?.input||{})}}/>
      {showDrop&&suggestions.length>0&&(
        <div style={{position:"absolute",top:"100%",left:0,right:0,zIndex:100,
          background:"#141f38",border:"1px solid #40485d40",borderRadius:8,
          marginTop:4,maxHeight:200,overflowY:"auto",boxShadow:"0 8px 32px rgba(0,0,0,.4)"}}>
          {suggestions.map(city=>(
            <div key={city} onClick={()=>pick(city)}
              style={{padding:"8px 14px",fontSize:13,color:"#dee5ff",cursor:"pointer",
                borderBottom:"1px solid #40485d15"}}
              onMouseEnter={e=>e.currentTarget.style.background="#192540"}
              onMouseLeave={e=>e.currentTarget.style.background="transparent"}>
              {city}{state?`, ${state}`:""}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── FreeSourceFinder ────────────────────────────────────────────────────────
// $0 lead sources: OpenStreetMap (any vertical, nationwide) + NPI registry
// (healthcare facilities). No per-lead cost, so no spend cap — pull freely.
const OSM_CATEGORIES = ["Healthcare","Education","Industrial","Entertainment","Offices","Hospitality"]

function FreeSourceFinder({onFound}){
  const [src,setSrc]       = useState("osm")          // "osm" | "npi"
  const [state,setState]   = useState("")
  const [cities,setCities] = useState("")
  const [cats,setCats]     = useState(["Healthcare","Education","Industrial","Entertainment"])
  const [taxonomy,setTax]  = useState("")
  const [loading,setLoad]  = useState(false)
  const [result,setResult] = useState(null)
  const [err,setErr]       = useState("")

  function toggleCat(c){ setCats(s=>s.includes(c)?s.filter(x=>x!==c):[...s,c]) }

  async function pull(){
    setErr("")
    if(!state){ setErr("Pick a state."); return }
    if(src==="osm" && cats.length===0){ setErr("Pick at least one category."); return }
    setLoad(true); setResult(null)
    try{
      const path = src==="osm" ? "/api/sources/osm" : "/api/sources/npi"
      const body = src==="osm" ? {state,cities,categories:cats}
                               : {state,cities,taxonomy,limit:200}
      const res = await api(path,{method:"POST",body:JSON.stringify(body)})
      setResult(res); if(res.saved>0) onFound&&onFound()
    }catch(ex){ setErr(ex.message||"Pull failed — try again or a different state.") }
    finally{ setLoad(false) }
  }

  return(
    <div className="finder" style={{borderTop:"3px solid #69f6b8"}}>
      <div className="finder-title">
        🌐 Free Lead Sources
        <span style={{color:"#69f6b8",fontSize:9,marginLeft:8,verticalAlign:"middle"}}>$0 · NO LIMIT</span>
      </div>
      <div className="finder-sub">
        OpenStreetMap (every vertical) + the national NPI healthcare registry. No API cost — pull as much as you want.
      </div>

      {/* Source toggle */}
      <div style={{display:"flex",gap:8,marginBottom:14}}>
        {[["osm","🗺️ OpenStreetMap"],["npi","🏥 NPI Healthcare"]].map(([v,label])=>(
          <button key={v} onClick={()=>setSrc(v)} type="button"
            style={{padding:"6px 14px",borderRadius:8,fontSize:12,fontFamily:"inherit",cursor:"pointer",
              background:src===v?"#69f6b8":"transparent",color:src===v?"#001b12":"#a3aac4",
              border:`1px solid ${src===v?"#69f6b8":"#40485d40"}`,transition:"all .1s"}}>
            {label}
          </button>
        ))}
      </div>

      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12,alignItems:"flex-end"}}>
        <div className="ff">
          <label>State (required)</label>
          <select value={state} onChange={e=>setState(e.target.value)} className="sel"
            style={{color:state?"#dee5ff":"#a3aac4",border:!state?"1px solid #40485d40":""}}>
            <option value="">Pick a state</option>
            {STATES.filter(s=>s).map(s=><option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="ff">
          <label>Cities (optional, comma-sep)</label>
          <CityAutocomplete value={cities} onChange={setCities} state={state} multi={true}
            placeholder={state?"e.g. Atlanta, Savannah — blank = whole state":"Pick a state first"}/>
        </div>

        {src==="osm"&&(
          <div className="ff" style={{gridColumn:"1 / -1"}}>
            <label>Categories ({cats.length} selected)</label>
            <div style={{display:"flex",gap:5,flexWrap:"wrap",marginTop:4}}>
              {OSM_CATEGORIES.map(c=>(
                <button key={c} type="button" onClick={()=>toggleCat(c)}
                  style={{padding:"4px 10px",borderRadius:6,fontSize:11,fontFamily:"inherit",cursor:"pointer",
                    background:cats.includes(c)?"#69f6b8":"transparent",
                    color:cats.includes(c)?"#001b12":"#a3aac4",
                    border:`1px solid ${cats.includes(c)?"#69f6b8":"#40485d40"}`}}>
                  {c}
                </button>
              ))}
            </div>
          </div>
        )}
        {src==="npi"&&(
          <div className="ff" style={{gridColumn:"1 / -1"}}>
            <label>Facility type (optional)</label>
            <input className="sel" value={taxonomy} onChange={e=>setTax(e.target.value)}
              placeholder="e.g. Nursing, Dentist, Hospital — blank = all healthcare orgs"/>
          </div>
        )}

        <button className="btn btn-p" onClick={pull} disabled={loading}
          style={{gridColumn:"1 / -1",padding:"10px 22px",background:"#69f6b8",color:"#001b12",fontWeight:600}}>
          {loading?"Pulling…":(src==="osm"?"Pull from OpenStreetMap →":"Pull from NPI Registry →")}
        </button>
      </div>

      {err&&<div style={{marginTop:14,padding:"10px 14px",background:"#ff6e8418",border:"1px solid #ff6e8440",
        borderRadius:8,fontSize:12,color:"#ff6e84"}}>{err}</div>}
      {loading&&<div style={{marginTop:14,display:"flex",alignItems:"center",gap:8,fontSize:12,color:"#a3aac4"}}>
        <div className="pulse" style={{width:6,height:6,borderRadius:"50%",background:"#69f6b8"}}/>
        {src==="osm"?"Querying OpenStreetMap (can take 20-40s for a whole state)…":"Querying NPI registry…"}
      </div>}
      {result&&!loading&&(
        <div style={{marginTop:14,padding:"10px 14px",
          background:result.saved>0?"#69f6b815":"#ffe08315",
          border:`1px solid ${result.saved>0?"#69f6b830":"#ffe08340"}`,
          borderRadius:8,fontSize:12,color:result.saved>0?"#69f6b8":"#ffe083",lineHeight:1.5}}>
          {result.saved>0
            ? <>✓ <b>{result.saved}</b> new lead{result.saved===1?"":"s"} saved</>
            : <>ⓘ <b>0 new</b> — see breakdown below</>}
          {result.summary&&<div style={{marginTop:4,fontSize:10,color:"#a3aac4"}}>{result.summary}</div>}
          {src==="osm"&&result.found>0&&result.saved<result.found*0.3&&(
            <div style={{marginTop:6,fontSize:10,color:"#a3aac4"}}>
              OSM is phone-sparse — many results had no phone and were skipped. NPI and the Apollo
              enrichment fill those gaps; OSM is best for the wide net across industrial/education/venues.
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ─── LeadFinder ──────────────────────────────────────────────────────────────

function LeadFinder({onFound, industries}){
  const [selIndustries,setSelIndustries] = useState([industries[0]||"Healthcare"])
  const [state,setState]       = useState("")
  const [cities,setCities]     = useState("")
  const [limit,setLimit]       = useState(25)
  const [loading,setLoad]      = useState(false)
  const [lastResult,setLast]   = useState(null)
  const [findError,setFindError] = useState("")
  // Bulk multi-area list. Each entry is "ST" or "City, ST". Built from the
  // state/cities inputs below via "Add area", then sent as `locations` so one
  // run walks a whole territory.
  const [bulkLocations,setBulkLocations] = useState([])

  function addArea(){
    setFindError("")
    if(!state){ setFindError("Pick a state to add an area."); return }
    const cityList = cities.split(",").map(c=>c.trim()).filter(Boolean)
    const toAdd = cityList.length ? cityList.map(c=>`${c}, ${state}`) : [state]
    setBulkLocations(prev=>{
      const seen = new Set(prev.map(x=>x.toLowerCase()))
      return [...prev, ...toAdd.filter(x=>!seen.has(x.toLowerCase()))]
    })
    setCities("")  // clear so the next area starts fresh; keep state selected
  }
  function removeArea(loc){ setBulkLocations(prev=>prev.filter(x=>x!==loc)) }

  function toggleIndustry(ind){
    if(ind==="_all_"){
      setSelIndustries(s=>s.length===industries.length?[]:[...industries])
      return
    }
    setSelIndustries(s=>s.includes(ind)?s.filter(i=>i!==ind):[...s,ind])
  }

  async function find(){
    setFindError("")
    if(cities.trim() && !state && bulkLocations.length===0){ setFindError("Please select a state when targeting specific cities."); return }
    if(selIndustries.length===0){ setFindError("Select at least one industry."); return }
    setLoad(true); setLast(null)
    try {
      const isAll = selIndustries.length===industries.length
      // If the user staged a bulk area list, send it (overrides state/cities).
      // Also fold in the currently-typed-but-not-yet-added area so a lone
      // selection isn't silently ignored.
      const pendingArea = !state ? []
        : cities.trim() ? cities.split(",").map(c=>c.trim()).filter(Boolean).map(c=>`${c}, ${state}`)
        : [state]
      const allLocations = [...new Set([...bulkLocations, ...pendingArea])]
      const body = {
        industry: isAll?"_all_":selIndustries[0],
        industries: selIndustries.length>1&&!isAll?selIndustries.join(","):"",
        state,cities,limit,source:"sam",
        ...(bulkLocations.length>0 ? {locations: allLocations} : {})
      }
      const res = await api("/api/scrape",{method:"POST",body:JSON.stringify(body)})
      setLast(res)
      if(res.saved > 0) onFound()
    } catch(ex){ setFindError("Search failed — check your internet and try again.") }
    finally { setLoad(false) }
  }

  return(
    <div className="finder">
      <div className="finder-title">🔍 Find Leads</div>
      <div className="finder-sub">Pull fresh leads from government databases — no CSV needed</div>
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12,alignItems:"flex-end"}}>
        <div className="ff" style={{gridColumn:"1 / -1"}}>
          <label style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
            <span>Industries ({selIndustries.length===industries.length?"All":selIndustries.length} selected)</span>
            <button onClick={()=>toggleIndustry("_all_")} style={{background:"none",border:"none",
              color:"#a3a6ff",fontSize:11,cursor:"pointer",padding:0}}>
              {selIndustries.length===industries.length?"Clear all":"Select all"}
            </button>
          </label>
          <div style={{display:"flex",gap:5,flexWrap:"wrap",marginTop:4}}>
            {industries.map(ind=>(
              <button key={ind} onClick={()=>toggleIndustry(ind)}
                style={{padding:"4px 10px",borderRadius:6,fontSize:11,fontFamily:"inherit",cursor:"pointer",
                  background:selIndustries.includes(ind)?"#a3a6ff":"transparent",
                  color:selIndustries.includes(ind)?"#000011":"#a3aac4",
                  border:`1px solid ${selIndustries.includes(ind)?"#a3a6ff":"#40485d40"}`,
                  transition:"all .1s"}}>
                {ind}
              </button>
            ))}
          </div>
        </div>
        <div className="ff">
          <label>{cities.trim()?"State (required for city search)":"State (optional)"}</label>
          <select value={state} onChange={e=>setState(e.target.value)} className="sel"
            style={{color:state?"#dee5ff":"#a3aac4",border:cities.trim()&&!state?"1px solid #ff6e84":""}}>
            <option value="">All States</option>
            {STATES.filter(s=>s).map(s=><option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="ff" style={{gridColumn:"1 / -1"}}>
          <label>Cities / Towns (optional)</label>
          <CityAutocomplete value={cities} onChange={setCities} state={state} multi={true}
            placeholder="e.g. Stratford, Norwalk, Bridgeport — leave blank for entire state"/>
        </div>

        {/* ── Bulk areas: queue several states / cities into one run ─────── */}
        <div className="ff" style={{gridColumn:"1 / -1"}}>
          <label style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
            <span>Bulk Areas {bulkLocations.length>0&&<span style={{color:"#a3a6ff"}}>({bulkLocations.length} queued)</span>}</span>
            <button type="button" onClick={addArea}
              style={{background:"#a3a6ff18",border:"1px solid #a3a6ff40",color:"#a3a6ff",
                fontSize:11,cursor:"pointer",padding:"3px 10px",borderRadius:6,fontFamily:"inherit"}}>
              ＋ Add area from State/Cities above
            </button>
          </label>
          <div style={{fontSize:10,color:"#40485d",marginTop:3}}>
            Pick a state (and optionally cities) above, click Add. Repeat to stack a whole
            territory — each area pulls ~{limit} leads. Leave empty to search just the single area above.
          </div>
          {bulkLocations.length>0&&(
            <div style={{display:"flex",gap:6,flexWrap:"wrap",marginTop:8}}>
              {bulkLocations.map(loc=>(
                <span key={loc} style={{display:"inline-flex",alignItems:"center",gap:6,padding:"4px 8px",
                  borderRadius:6,fontSize:11,background:"#a3a6ff18",border:"1px solid #a3a6ff40",color:"#dee5ff"}}>
                  📍 {loc}
                  <button type="button" onClick={()=>removeArea(loc)}
                    style={{background:"none",border:"none",color:"#a3aac4",cursor:"pointer",padding:0,fontSize:13,lineHeight:1}}
                    title="Remove">×</button>
                </span>
              ))}
              <button type="button" onClick={()=>setBulkLocations([])}
                style={{background:"none",border:"none",color:"#ff6e84",fontSize:10,cursor:"pointer",
                  padding:"4px 6px",textDecoration:"underline"}}>Clear all</button>
            </div>
          )}
        </div>

        <div className="range-wrap">
          <label style={{fontSize:10,letterSpacing:".1em",textTransform:"uppercase",color:"#a3aac4",display:"flex",justifyContent:"space-between"}}>
            <span>How Many</span><span style={{color:"#a3a6ff"}}>{limit}</span>
          </label>
          <input type="range" min={25} max={200} step={25} value={limit} onChange={e=>setLimit(Number(e.target.value))}/>
          <div style={{display:"flex",justifyContent:"space-between",fontSize:9,color:"#40485d"}}><span>25</span><span>200</span></div>
        </div>
        <button className="btn btn-p" onClick={find} disabled={loading} style={{padding:"10px 22px",whiteSpace:"nowrap",alignSelf:"flex-end"}}>
          {loading?"Searching\u2026":(bulkLocations.length>0?`Find across ${bulkLocations.length} areas \u2192`:"Find Leads \u2192")}
        </button>
      </div>
      {findError&&<div style={{marginTop:14,padding:"10px 14px",background:"#ff6e8418",border:"1px solid #ff6e8440",
        borderRadius:8,fontSize:12,color:"#ff6e84"}}>{findError}</div>}
      {loading&&<div style={{marginTop:14,display:"flex",alignItems:"center",gap:8,fontSize:12,color:"#a3aac4"}}>
        <div className="pulse" style={{width:6,height:6,borderRadius:"50%",background:"#a3a6ff"}}/>Pulling records…
      </div>}
      {lastResult&&!loading&&(()=>{
        const saved=lastResult.saved||0
        const dupes=lastResult.alreadyInDb||0
        // "0 saved" reads as broken unless we explain why. When the area is
        // tapped out (everything already owned, or all combos cached), say so.
        const tappedOut = saved===0 && (dupes>0 || lastResult.cacheHits>=lastResult.combos)
        return (
        <div style={{marginTop:14,padding:"10px 14px",
          background: saved>0?"#69f6b815":"#ffe08315",
          border:`1px solid ${saved>0?"#69f6b830":"#ffe08340"}`,
          borderRadius:8,fontSize:12,color: saved>0?"#69f6b8":"#ffe083",lineHeight:1.5}}>
          {saved>0
            ? <>✓ <b>{saved}</b> new lead{saved===1?"":"s"} saved — ready to call</>
            : tappedOut
              ? <>ⓘ <b>0 new leads</b> — you've already pulled this area. Not a bug; the dedup is working.</>
              : <>ⓘ <b>0 new leads</b> from this search.</>}
          {/* Full funnel breakdown straight from the backend summary. */}
          {lastResult.summary&&(
            <div style={{marginTop:4,fontSize:10,color:"#a3aac4"}}>{lastResult.summary}</div>
          )}
          {tappedOut&&(
            <div style={{marginTop:6,padding:"8px 10px",background:"#a3a6ff12",border:"1px solid #a3a6ff30",
              borderRadius:6,fontSize:10,color:"#dee5ff"}}>
              💡 Try new cities or a different industry to reach fresh businesses — or use
              <b style={{color:"#8b5cf6"}}> 📞 Pull from Apollo</b> below for decision-maker contacts.
            </div>
          )}
        </div>
        )
      })()}
    </div>
  )
}

// ─── ApolloFinder (admin only) ───────────────────────────────────────────────
// Pulls decision-maker contacts from Apollo.io: real names, titles, direct
// emails/phones — built to bypass gatekeepers. Admin-only because it burns
// paid Apollo credits (4,000/month on Pro, ~$0.025/contact at typical rates).

function ApolloFinder({onFound, industries: industryOptions = []}){
  const [titles,setTitles]         = useState("Facility Manager,Director of Operations,Operations Manager,Property Manager")
  // Structured location: state code + comma-sep cities. Replaces the loose
  // free-text input that was matching UK/AU "California, Wales"-style results.
  const [state,setState]           = useState("")
  const [cities,setCities]         = useState("")
  // Industry chips (multi-select). Uses the same /api/industries list as
  // Find Leads so callers see one consistent vocabulary.
  const [selIndustries,setSelInds] = useState([])
  const [empMin,setEmpMin]         = useState(50)
  const [empMax,setEmpMax]         = useState(500)
  const [perPage,setPerPage]       = useState(25)
  const [revealPhones,setRevealPh] = useState(false)
  // Rotate through decision-maker title groups (Owner → CEO → GM → Ops → …) so
  // repeat pulls of the same city surface fresh budget-holders instead of
  // re-hitting the same Owners that are already in the DB.
  const [rotateTitles,setRotateTitles] = useState(false)
  const [loading,setLoad]          = useState(false)
  const [result,setResult]         = useState(null)
  const [error,setError]           = useState("")
  // Persistent page cursor — successive "Pull from Apollo" clicks advance
  // through Apollo's catalog instead of always restarting at page 1. Keyed
  // per search-criteria in localStorage so the cursor survives page reloads /
  // navigating away AND resumes per-city. This is the fix for "no new leads
  // even for major cities": the cursor used to be ephemeral React state that
  // snapped back to page 1 on every remount or field tweak, so each re-pull
  // re-fetched page 1 and the backend deduped every contact as already-in-DB.
  const cursorKey = ()=>"apolloPg:"+JSON.stringify({
    s:state, c:(cities||"").trim().toLowerCase(), t:(titles||"").trim().toLowerCase(),
    i:[...selIndustries].sort(), r:rotateTitles
  })
  const readCursor = ()=>{ try{ const v=Number(localStorage.getItem(cursorKey())); return v>0?v:1 }catch{ return 1 } }
  const [pullStartPage,setPullStartPage] = useState(readCursor)
  // On mount and whenever the search criteria change, load THAT criteria's
  // saved cursor (default 1) instead of blindly resetting — so switching
  // city A→B→A resumes A's page rather than starting over at 1.
  useEffect(()=>{ setPullStartPage(readCursor()) },[titles,selIndustries,state,cities,rotateTitles])  // eslint-disable-line react-hooks/exhaustive-deps
  const [budget,setBudget]         = useState(null)
  useEffect(()=>{
    api("/api/admin/apollo/budget").then(setBudget).catch(()=>{})
  },[])
  // Backfill: enrich existing unassigned leads with no DM info
  const [backfillN,setBackfillN]      = useState(25)
  const [backfilling,setBackfilling]  = useState(false)
  const [backfillResult,setBfResult]  = useState(null)
  const [backfillError,setBfError]    = useState("")

  function toggleIndustry(ind){
    if(ind==="_all_"){
      setSelInds(s=>s.length===industryOptions.length?[]:[...industryOptions])
      return
    }
    setSelInds(s=>s.includes(ind)?s.filter(i=>i!==ind):[...s,ind])
  }

  // Build Apollo's person_locations array from structured state+cities. This
  // is the whole point of the refactor — avoids matching "California" against
  // South Wales etc. Always anchors to US (last week empty-state was sending
  // [] which Apollo treated as "any country" → Filipino/Indian leaks).
  function buildLocations(){
    if(!state) return ["United States"]
    const fullState = STATE_NAMES[state] || state
    const cityList = cities.split(",").map(c=>c.trim()).filter(Boolean)
    if(cityList.length===0) return [`${fullState}, US`]
    return cityList.map(c=>`${c}, ${state}`)
  }

  async function pull(){
    setError(""); setResult(null)
    if(!titles.trim()&&!rotateTitles){ setError("Enter at least one job title (or turn on title rotation)."); return }
    if(cities.trim()&&!state){ setError("Pick a state when targeting specific cities."); return }
    setLoad(true)
    try{
      const locArr = buildLocations()
      const res = await api("/api/admin/apollo/pull",{method:"POST",body:JSON.stringify({
        titles,
        industries:    selIndustries,  // backend accepts list now
        locations:     locArr,
        employee_min:  Number(empMin)||50,
        employee_max:  Number(empMax)||500,
        per_page:      Number(perPage)||25,
        page:          pullStartPage,    // resumes from where last pull left off
        reveal_phones: revealPhones,
        // Auto-paginate so the user sees real new leads rather than "0 saved"
        // when every contact on page 1 is already in the DB. Walks up to 5
        // pages (8 when rotating, to sample more title groups) or until 10
        // new are saved, whichever first.
        auto_paginate: true,
        min_new_leads: 10,
        max_pages:     rotateTitles?8:5,
        // Cycle decision-maker title groups to keep repeat pulls fresh.
        rotate_titles: rotateTitles,
      })})
      // Persist the next-page cursor (in state AND localStorage) so the next
      // "Pull from Apollo" click — even after a reload or navigating away —
      // advances through Apollo's catalog instead of re-hitting page 1.
      if(res.next_page){
        setPullStartPage(res.next_page)
        try{ localStorage.setItem(cursorKey(),String(res.next_page)) }catch{}
      }
      setResult(res)
      if(res.saved>0) onFound&&onFound()
    }catch(ex){
      setError(ex.message||"Apollo pull failed — check API key in Railway.")
    }finally{ setLoad(false) }
  }

  async function backfill(){
    setBfError(""); setBfResult(null)
    const n = Math.max(1,Math.min(Number(backfillN)||25,200))
    if(!window.confirm(`Spend up to ${n} Apollo credits to enrich existing unassigned leads with decision-maker info?`)) return
    setBackfilling(true)
    try{
      const res = await api("/api/admin/apollo/backfill",{method:"POST",body:JSON.stringify({limit:n})})
      setBfResult(res)
      if(res.enriched>0) onFound&&onFound()
    }catch(ex){
      setBfError(ex.message||"Backfill failed — check Apollo key + kill switch.")
    }finally{ setBackfilling(false) }
  }

  return(
    <div className="finder" style={{borderTop:"3px solid #8b5cf6"}}>
      <div className="finder-title">
        📞 Pull from Apollo
        {!isAdmin()&&(
          <span style={{color:"#8b5cf6",fontSize:9,marginLeft:8,verticalAlign:"middle"}}>NEW</span>
        )}
      </div>
      <div className="finder-sub">
        Decision-maker contacts: real names + titles + direct emails — built to bypass gatekeepers.
        {!isAdmin()&&budget?.daily_pull_cap&&(
          ` ${Math.max(0,budget.daily_pull_cap-(budget.pulls_used_today||0))}/${budget.daily_pull_cap} pulls left today.`
        )}
      </div>
      {isAdmin()&&budget&&(
        <div style={{marginBottom:14,padding:"8px 12px",background:"#0f1930",border:"1px solid #40485d40",
          borderRadius:8,fontSize:11,color:"#a3aac4",display:"flex",justifyContent:"space-between",alignItems:"center",gap:12}}>
          <span>📱 Phone reveals this month: <b style={{color: budget.remaining<=0?"#ff6e84":"#8b5cf6"}}>{budget.used_this_month}/{budget.monthly_cap}</b>
            <span style={{color:"#40485d"}}> · ~{budget.credits_per_reveal_est} cr each</span></span>
          <span style={{color:"#40485d",fontSize:10}}>
            Allowed: {budget.allowed_industries?.join(", ")||"any"}
          </span>
        </div>
      )}
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12,alignItems:"flex-end"}}>
        <div className="ff" style={{gridColumn:"1 / -1"}}>
          <label>Job Titles (comma-separated)</label>
          <input className="sel" value={titles} onChange={e=>setTitles(e.target.value)}
            placeholder="Facility Manager, Director of Operations, Property Manager"/>
        </div>
        {industryOptions.length>0 && (
          <div className="ff" style={{gridColumn:"1 / -1"}}>
            <label style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
              <span>Industries ({selIndustries.length===industryOptions.length?"All":selIndustries.length} selected)</span>
              <button onClick={()=>toggleIndustry("_all_")} style={{background:"none",border:"none",
                color:"#8b5cf6",fontSize:11,cursor:"pointer",padding:0}}>
                {selIndustries.length===industryOptions.length?"Clear all":"Select all"}
              </button>
            </label>
            <div style={{display:"flex",gap:5,flexWrap:"wrap",marginTop:4}}>
              {industryOptions.map(ind=>(
                <button key={ind} onClick={()=>toggleIndustry(ind)}
                  style={{padding:"4px 10px",borderRadius:6,fontSize:11,fontFamily:"inherit",cursor:"pointer",
                    background:selIndustries.includes(ind)?"#8b5cf6":"transparent",
                    color:selIndustries.includes(ind)?"#000011":"#a3aac4",
                    border:`1px solid ${selIndustries.includes(ind)?"#8b5cf6":"#40485d40"}`,
                    transition:"all .1s"}}>
                  {ind}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="ff">
          <label>{cities.trim()?"State (required for city search)":"State (optional — empty = nationwide)"}</label>
          <select value={state} onChange={e=>setState(e.target.value)} className="sel"
            style={{color:state?"#dee5ff":"#a3aac4",border:cities.trim()&&!state?"1px solid #ff6e84":""}}>
            <option value="">All States (US-only)</option>
            {STATES.filter(s=>s).map(s=><option key={s} value={s}>{s} — {STATE_NAMES[s]||s}</option>)}
          </select>
        </div>
        <div className="ff">
          <label>Cities (optional, multi)</label>
          <CityAutocomplete value={cities} onChange={setCities} state={state} multi={true}
            placeholder={state?"e.g. Phoenix, Tucson, Tempe":"Pick a state first"}/>
        </div>
        <div className="ff">
          <label>Company Size (employees)</label>
          <div style={{display:"flex",gap:6,alignItems:"center"}}>
            <input className="sel" type="number" min={1} value={empMin}
              onChange={e=>setEmpMin(e.target.value)} style={{width:"100%"}}/>
            <span style={{color:"#40485d"}}>to</span>
            <input className="sel" type="number" min={1} value={empMax}
              onChange={e=>setEmpMax(e.target.value)} style={{width:"100%"}}/>
          </div>
        </div>
        <div className="range-wrap">
          <label style={{fontSize:10,letterSpacing:".1em",textTransform:"uppercase",color:"#a3aac4",display:"flex",justifyContent:"space-between"}}>
            <span>How Many</span><span style={{color:"#8b5cf6"}}>{perPage}</span>
          </label>
          <input type="range" min={5} max={100} step={5} value={perPage} onChange={e=>setPerPage(Number(e.target.value))}/>
          <div style={{display:"flex",justifyContent:"space-between",fontSize:9,color:"#40485d"}}><span>5</span><span>100</span></div>
        </div>
        <div style={{display:"flex",flexDirection:"column",alignItems:"flex-end",gap:4,alignSelf:"flex-end"}}>
          <button className="btn btn-p" onClick={pull} disabled={loading}
            style={{padding:"10px 22px",whiteSpace:"nowrap",background:"#8b5cf6"}}>
            {loading?"Pulling…":(pullStartPage>1?`Pull next 5 pages (from p${pullStartPage}) →`:"Pull from Apollo →")}
          </button>
          {pullStartPage>1&&(
            <button onClick={()=>{ setPullStartPage(1); try{ localStorage.removeItem(cursorKey()) }catch{} }}
              style={{background:"none",border:"none",color:"#a3aac4",fontSize:10,cursor:"pointer",padding:0,textDecoration:"underline"}}
              title="Reset to Apollo page 1">↺ Reset to page 1</button>
          )}
        </div>
        {isAdmin()&&(
          <label style={{gridColumn:"1 / -1",display:"flex",alignItems:"center",gap:8,fontSize:11,color:"#a3aac4",cursor:"pointer"}}>
            <input type="checkbox" checked={revealPhones} onChange={e=>setRevealPh(e.target.checked)}
              style={{cursor:"pointer"}}/>
            <span>Also reveal direct mobile phones <span style={{color:"#ffe083"}}>(+5-8 credits per lead, async via webhook ~30s)</span></span>
          </label>
        )}
        <label style={{gridColumn:"1 / -1",display:"flex",alignItems:"flex-start",gap:8,fontSize:11,color:"#a3aac4",cursor:"pointer"}}>
          <input type="checkbox" checked={rotateTitles} onChange={e=>setRotateTitles(e.target.checked)}
            style={{cursor:"pointer",marginTop:2}}/>
          <span>🔄 Rotate decision-maker titles <span style={{color:"#69f6b8"}}>(keeps leads fresh)</span>
            <span style={{display:"block",color:"#40485d",fontSize:10,marginTop:1}}>
              Cycles Owner → CEO/President → COO/Ops → GM → Facilities → Office Mgr → Property Mgr.
              Surfaces new buyers in cities you've already pulled, instead of re-hitting the same people.
              {titles.trim()?" Your titles above are tried first.":" (your title box can be left blank)"}
            </span>
          </span>
        </label>
      </div>
      {error&&<div style={{marginTop:14,padding:"10px 14px",background:"#ff6e8418",border:"1px solid #ff6e8440",
        borderRadius:8,fontSize:12,color:"#ff6e84"}}>{error}</div>}
      {loading&&(
        <div style={{marginTop:14,padding:"12px 14px",background:"#8b5cf612",border:"1px solid #8b5cf625",
          borderRadius:8,fontSize:12,color:"#a3aac4",lineHeight:1.6}}>
          <div style={{display:"flex",alignItems:"center",gap:8}}>
            <div className="pulse" style={{width:6,height:6,borderRadius:"50%",background:"#8b5cf6"}}/>
            <b style={{color:"#dee5ff"}}>Searching Apollo…</b>
          </div>
          <div style={{fontSize:11,marginTop:6,color:"#40485d"}}>
            1. Query Apollo · 2. Filter US-only · 3. Enrich with /people/match (~1 credit each)
            · 4. Dedupe against your DB · 5. Save remainder. Usually 20–45s for 25 contacts.
          </div>
        </div>
      )}
      {result&&!loading&&(
        <div style={{marginTop:14,padding:"12px 14px",
          background:result.saved>0?"#8b5cf615":"#ffe08315",
          border:`1px solid ${result.saved>0?"#8b5cf630":"#ffe08340"}`,
          borderRadius:8,fontSize:12,color:"#dee5ff",lineHeight:1.6}}>
          {/* Headline — varies by outcome so "0 saved" doesn't look like a
              failure when it's just "all already in DB". */}
          {result.saved>0?(
            <div>
              ✓ <b style={{color:"#8b5cf6",fontSize:14}}>{result.saved}</b> new lead{result.saved===1?"":"s"} saved
              {" "}<span style={{color:"#a3aac4"}}>· {result.returned} contacts checked across {result.pages_walked||1} page{(result.pages_walked||1)===1?"":"s"}</span>
              {result.next_page&&result.total_pages&&result.next_page<=result.total_pages&&(
                <span style={{color:"#40485d",fontSize:11,marginLeft:6}}>
                  · next click pulls page {result.next_page}/{result.total_pages}
                </span>
              )}
            </div>
          ):result.returned===0?(
            <div style={{color:"#ffe083"}}>
              ⚠ Apollo returned <b>0 contacts</b> across {result.pages_walked||1} page(s) for this search.
              Try widening titles, removing the state filter, or bumping employee range.
            </div>
          ):(()=>{
            const dupes=(result.skipped?.already_in_db||0)+(result.skipped?.phone_dup_in_db||0)
            const dupeDominant=dupes>0&&dupes>=result.returned*0.6
            return (
            <div style={{color:"#ffe083"}}>
              ⓘ <b>0 new leads</b> — all {result.returned} contacts across {result.pages_walked||1} page{(result.pages_walked||1)===1?"":"s"} were filtered out.
              {dupeDominant&&(
                <div style={{marginTop:6,fontSize:11,color:"#a3aac4"}}>
                  Most ({dupes}) were <b style={{color:"#dee5ff"}}>already in your pipeline</b> — you've pulled
                  these people before, so the dedup is doing its job (not a bug).
                  {!rotateTitles&&(
                    <div style={{marginTop:6,padding:"8px 10px",background:"#69f6b812",border:"1px solid #69f6b830",borderRadius:6,color:"#dee5ff"}}>
                      💡 You've tapped out these titles in this area. Turn on
                      <b style={{color:"#69f6b8"}}> 🔄 Rotate decision-maker titles</b> below and pull again —
                      it'll grab the GMs, Ops Directors, and Facilities Managers at companies whose
                      Owner you already have.
                    </div>
                  )}
                  {rotateTitles&&(
                    <div style={{marginTop:6,color:"#a3aac4"}}>
                      Even with rotation every title group here is exhausted. Try a nearby city,
                      a wider employee range, or a different industry.
                    </div>
                  )}
                </div>
              )}
              {!dupeDominant&&result.total_pages&&result.next_page&&result.next_page<=result.total_pages?(
                <div style={{marginTop:6,fontSize:11,color:"#a3aac4"}}>
                  {result.total_pages.toLocaleString()} total contacts match this search.
                  Click "Pull from Apollo" again to walk pages {result.next_page}–{Math.min(result.next_page+4, result.total_pages)} (next batch).
                </div>
              ):null}
            </div>
          )})()}

          {/* Title groups walked this pull (rotation only). */}
          {result.rotated&&result.titles_tried&&result.titles_tried.length>0&&(
            <div style={{marginTop:8,fontSize:11,color:"#a3aac4"}}>
              🔄 Rotated through: <span style={{color:"#69f6b8"}}>{result.titles_tried.join(" → ")}</span>
            </div>
          )}

          {/* Full skip-category breakdown. Each row only renders if non-zero. */}
          {result.skipped&&(()=>{
            const cats = [
              {k:"already_in_db",   label:"already in DB (caught pre-Apollo)",  color:"#a3aac4"},
              {k:"phone_dup_in_db", label:"duplicate phone (caught post-Apollo)", color:"#a3aac4"},
              {k:"non_us",          label:"non-US contacts",                     color:"#a3aac4"},
              {k:"no_company",      label:"missing company",                     color:"#a3aac4"},
              {k:"no_actionable",   label:"no name + phone + email combo",       color:"#a3aac4"},
            ].filter(c=>(result.skipped[c.k]||0)>0)
            if(cats.length===0) return null
            return (
              <div style={{marginTop:8,fontSize:11,color:"#a3aac4",lineHeight:1.7}}>
                <div style={{fontSize:10,letterSpacing:".08em",textTransform:"uppercase",
                  color:"#40485d",marginBottom:3}}>Filtered out:</div>
                {cats.map(c=>(
                  <div key={c.k}>· <b style={{color:c.color}}>{result.skipped[c.k]}</b> {c.label}</div>
                ))}
              </div>
            )
          })()}

          {/* Show a few sample saved leads so the caller can verify what
              actually landed. Without this, "5 saved" was abstract. */}
          {result.sample&&result.sample.length>0&&result.saved>0&&(
            <div style={{marginTop:10,paddingTop:8,borderTop:"1px solid #40485d20"}}>
              <div style={{fontSize:10,letterSpacing:".08em",textTransform:"uppercase",
                color:"#40485d",marginBottom:4}}>Sample saved:</div>
              {result.sample.slice(0,5).map((s,i)=>(
                <div key={i} style={{fontSize:11,color:"#dee5ff",lineHeight:1.5}}>
                  · <b>{s.name||"—"}</b>{s.title?` · ${s.title}`:""}
                  {s.company?` @ ${s.company}`:""}
                  {s.phone?<span style={{color:"#69f6b8"}}> · 📞 {s.phone}</span>:""}
                  {s.email?<span style={{color:"#a3a6ff"}}> · ✉ {s.email}</span>:""}
                </div>
              ))}
            </div>
          )}

          {result.phone_reveals&&(
            <div style={{marginTop:8,color:"#ffe083",fontSize:11}}>
              📱 Requested {result.phone_reveals.requested} phone reveals — arrive async within ~30s, refresh the leads list to see them
            </div>
          )}
          {result.total_available>0&&(
            <div style={{marginTop:8,color:"#40485d",fontSize:11}}>
              {result.total_available.toLocaleString()} total contacts match this search
              ({result.total_pages} pages of {perPage} each — bump page to pull more)
            </div>
          )}
        </div>
      )}

      {/* ── Backfill: enrich existing leads with no DM info (admin only —
          bulk credit spend) ────────────────────────────────────────────── */}
      {isAdmin()&&(
      <div style={{marginTop:18,paddingTop:18,borderTop:"1px solid #40485d30"}}>
        <div style={{fontSize:12,color:"#dee5ff",fontWeight:600,marginBottom:6}}>
          Enrich Existing Leads
        </div>
        <div style={{fontSize:11,color:"#a3aac4",marginBottom:10}}>
          Run Apollo on unassigned leads in your queue that have no decision-maker name yet.
          One credit per lead checked.
        </div>
        <div style={{display:"flex",gap:8,alignItems:"center"}}>
          <label style={{fontSize:11,color:"#a3aac4"}}>How many:</label>
          <input className="sel" type="number" min={1} max={200} value={backfillN}
            onChange={e=>setBackfillN(e.target.value)} style={{width:90,padding:"6px 10px"}}/>
          <button className="btn btn-p" onClick={backfill} disabled={backfilling}
            style={{fontSize:11,padding:"7px 14px",background:"#8b5cf6"}}>
            {backfilling?"Enriching…":"Backfill Now"}
          </button>
        </div>
        {backfillError&&<div style={{marginTop:10,padding:"8px 12px",background:"#ff6e8418",border:"1px solid #ff6e8440",
          borderRadius:8,fontSize:11,color:"#ff6e84"}}>{backfillError}</div>}
        {backfillResult&&(
          <div style={{marginTop:10,padding:"10px 12px",background:"#8b5cf615",border:"1px solid #8b5cf630",
            borderRadius:8,fontSize:11,color:"#dee5ff"}}>
            ✓ Checked <b>{backfillResult.checked}</b> leads, enriched <b style={{color:"#8b5cf6"}}>{backfillResult.enriched}</b> with decision-maker info
            {backfillResult.checked>backfillResult.enriched&&(
              <span style={{color:"#a3aac4"}}> ({backfillResult.checked-backfillResult.enriched} had no Apollo match)</span>
            )}
          </div>
        )}
      </div>
      )}
    </div>
  )
}

// ─── Follow-up sequences ─────────────────────────────────────────────────────

const FOLLOW_UP_SEQUENCES = [
  { value:"", label:"No Follow-up Sequence" },
  { value:"24h-48h-5d", label:"Hot Lead: 24h → 48h → 5 days" },
  { value:"48h-5d-7d", label:"Standard: 48h → 5 days → 7 days" },
  { value:"48h-7d-14d", label:"Slow Burn: 48h → 7 days → 14 days" },
  { value:"30d-60d-90d", label:"Long Nurture: 30 → 60 → 90 days" },
  { value:"90d-180d", label:"Future: 3 months → 6 months" },
  { value:"180d-365d", label:"Far Future: 6 months → 1 year" },
]

const FOLLOW_UP_DAYS = {
  "24h-48h-5d": [1, 2, 5],
  "48h-5d-7d":  [2, 5, 7],
  "48h-7d-14d": [2, 7, 14],
  "30d-60d-90d": [30, 60, 90],
  "90d-180d":   [90, 180],
  "180d-365d":  [180, 365],
}

function addDays(days){
  const d = new Date()
  d.setDate(d.getDate() + days)
  return d.toISOString().split("T")[0]
}

// ─── QualChip ────────────────────────────────────────────────────────────────

function QualChip({label, value, options, onChange}){
  return(
    <div style={{display:"flex",flexDirection:"column",gap:4}}>
      <div style={{fontSize:9,letterSpacing:".1em",textTransform:"uppercase",color:"#a3aac4"}}>{label}</div>
      <div style={{display:"flex",gap:4,flexWrap:"wrap"}}>
        {options.map(o=>(
          <button key={o} onClick={()=>onChange(value===o?"":o)}
            style={{padding:"4px 10px",borderRadius:5,fontSize:11,fontFamily:"inherit",cursor:"pointer",
              background:value===o?"#a3a6ff":"transparent",
              color:value===o?"#000011":"#a3aac4",
              border:`1px solid ${value===o?"#a3a6ff":"#40485d40"}`,
              transition:"all .1s"}}>
            {o}
          </button>
        ))}
      </div>
    </div>
  )
}

// ─── CallModal ───────────────────────────────────────────────────────────────

function CallModal({lead: leadProp,onClose,onSaved}){
  // Local mirror so the Find-DM button can update the displayed contact info
  // without closing the modal or refetching from the server. lead.id is
  // stable, so the existing useEffect on lead.id stays correct.
  const [lead,setLead]            = useState(leadProp)
  const [findingDM,setFindingDM]  = useState(false)
  const [dmStatus,setDmStatus]    = useState("")
  const [revealing,setRevealing]  = useState(false)
  const [revealStatus,setRevealStatus] = useState("")
  const [phoneBudget,setPhoneBudget] = useState(null)  // {monthly_cap, used_this_month, remaining, allowed_industries}
  const [sendingCampaign,setSendingCampaign] = useState(false)
  const [campaignStatus,setCampaignStatus] = useState("")
  const [campaignInfo,setCampaignInfo] = useState(null)  // {in_campaign, last_sent_at, send_count, email_configured}
  const [sendEmailFollowup,setSendEmailFollowup] = useState(true)  // default checked when applicable
  const [calls,setCalls]          = useState([])
  const [modalError,setModalError] = useState("")
  const [primary,setPrimary]      = useState("")       // no_answer, voicemail, answered
  const [secondary,setSecondary]  = useState("")       // interested, not_interested, callback, converted
  const [cbReason,setCbReason]    = useState("")       // callback reason
  const [notes,setNotes]          = useState("")
  const [cbDate,setCbDate]        = useState("")
  const [duration,setDur]         = useState("")
  const [saving,setSave]          = useState(false)
  const [followUpSeq,setFuSeq]    = useState("")
  // Auto-timer
  const [timerStart]              = useState(()=>Date.now())
  const [timerNow,setTimerNow]    = useState(Date.now())
  const [timerRunning,setTimerRunning] = useState(true)
  useEffect(()=>{
    if(!timerRunning) return
    const iv=setInterval(()=>setTimerNow(Date.now()),1000)
    return()=>clearInterval(iv)
  },[timerRunning])
  const timerSeconds=Math.floor((timerNow-timerStart)/1000)
  const timerDisplay=`${Math.floor(timerSeconds/60).toString().padStart(2,"0")}:${(timerSeconds%60).toString().padStart(2,"0")}`
  const [budgetFocus,setBudget]   = useState("")
  const [vendorStatus,setVendor]  = useState("")
  const [decisionMaker,setDM]     = useState("")
  const [timeline,setTimeline]    = useState("")
  const [qualified,setQualified]  = useState("")
  const [scripts,setScripts]      = useState([])
  const [scriptId,setScriptId]    = useState("")
  const [showScript,setShowScript]= useState(false)
  const [contractValue,setContractValue] = useState("")

  useEffect(()=>{
    api(`/api/calls/${lead.id}`).then(r=>setCalls(Array.isArray(r)?r:[])).catch(()=>setCalls([]))
    api("/api/scripts").then(r=>setScripts(Array.isArray(r)?r:[])).catch(()=>{})
    api("/api/admin/apollo/budget").then(setPhoneBudget).catch(()=>setPhoneBudget(null))
    api(`/api/leads/${lead.id}/campaign-status`).then(setCampaignInfo).catch(()=>setCampaignInfo(null))
  },[lead.id])

  // Phone reveal allowed when industry is in allowlist AND budget remaining
  const phoneRevealAllowed = (()=>{
    if(!phoneBudget) return false
    if(phoneBudget.remaining<=0) return false
    const inds = phoneBudget.allowed_industries||[]
    if(inds.length===0) return true
    const leadInd = (lead.industry||"").toLowerCase()
    return inds.some(i=>leadInd.includes(i.toLowerCase()))
  })()

  // Derive the flat outcome for storage (backward compatible)
  const outcome = primary==="answered" ? (secondary||"answered") : primary
  const secDef = SECONDARY_OUTCOMES.find(s=>s.value===secondary)
  const needsQual = secDef?.needsQual || false
  const hasQualData = budgetFocus || vendorStatus || decisionMaker || timeline || qualified

  // What step is the user on?
  const step = !primary ? 1 : primary!=="answered" ? 3 : !secondary ? 2 : needsQual&&!hasQualData ? 2.5 : 3

  async function log(){
    setModalError("")
    if(!primary){ setModalError("Select what happened on the call."); return }
    if(primary==="answered"&&!secondary){ setModalError("Select the call result."); return }
    if(needsQual&&!hasQualData){ setModalError("Fill out at least one qualification field below."); return }
    if(secondary==="callback"&&!cbDate){ setModalError("Please select a callback date."); return }

    setSave(true)
    try{
      setTimerRunning(false)
      const finalDuration = duration ? parseInt(duration)*60 : timerSeconds
      const fullNotes = cbReason ? `[${cbReason}] ${notes}`.trim() : notes
      // Per-call email follow-up: only meaningful when this was a failed dial
      // and the lead has the contact info we'd email + VCC is configured.
      const emailEligible = (primary==="no_answer"||primary==="voicemail")
        && lead.email && lead.firstName && campaignInfo?.email_configured
        && !campaignInfo?.in_campaign
      const callPayload = {
        leadId:lead.id, outcome, notes:fullNotes,
        duration:finalDuration,
        callbackDate:secondary==="callback"?cbDate:"",
        calledBy:getUser(), calledAt:new Date().toISOString(),
        budgetfocus: budgetFocus||null, vendorstatus: vendorStatus||null,
        decisionmaker: decisionMaker||null, timeline: timeline||null,
        qualified: qualified||null,
        followupsequence: followUpSeq||null,
        script_id: scriptId ? parseInt(scriptId) : null,
        converted: outcome === "converted",
        send_email_followup: emailEligible && sendEmailFollowup,
      }
      await api("/api/calls",{method:"POST",body:JSON.stringify(callPayload)})
      if(scriptId) {
        api(`/api/scripts/${scriptId}/use`,{method:"POST",body:JSON.stringify({})}).catch(()=>{})
      }
      const statusMap={answered:"called",no_answer:"no_answer",voicemail:"no_answer",
        callback:"callback",interested:"interested",interested_no_dm:"interested_no_dm",
        not_interested:"not_interested",converted:"converted"}
      const fuDays = FOLLOW_UP_DAYS[followUpSeq]
      const nextFollowUp = fuDays ? addDays(fuDays[0]) : (secondary==="callback"?cbDate:"")
      await api(`/api/leads/${lead.id}`,{method:"PATCH",body:JSON.stringify({
        status:statusMap[outcome]||"called",
        callbackDate:secondary==="callback"?cbDate:nextFollowUp||"",
        followupsequence: followUpSeq||null,
        nextfollowup: nextFollowUp||null,
        followupstep: fuDays ? 0 : null,
        ...(!lead.assignedTo ? {assignedTo: getUser()} : {}),
        updatedAt:new Date().toISOString()
      })})
      onSaved(); onClose()
    }catch(ex){setModalError("Couldn't save — check your internet and try again.")}
    finally{setSave(false)}
  }

  const selectedScript = scripts.find(s=>s.id===parseInt(scriptId))
  const si=v=>STATUS_OPTIONS.find(s=>s.value===v)||STATUS_OPTIONS[0]

  async function findDecisionMaker(){
    setDmStatus(""); setFindingDM(true)
    try{
      const r = await api(`/api/leads/${lead.id}/find-dm`,{method:"POST",body:"{}"})
      if(r.enriched && r.lead){
        setLead(r.lead)
        setDmStatus("✓ Found")
        onSaved&&onSaved()  // refresh the parent leads list in the background
      }else{
        setDmStatus(r.message||"No Apollo match found for this company")
      }
    }catch(ex){
      setDmStatus(ex.message||"Couldn't reach Apollo — try again later")
    }finally{ setFindingDM(false) }
  }

  async function sendToCampaign(){
    if(!window.confirm(`Send "${lead.firstName} ${lead.lastName||''}" to the tried-to-call email campaign via VCC?`)) return
    setCampaignStatus(""); setSendingCampaign(true)
    try{
      const r = await api(`/api/leads/${lead.id}/send-to-campaign`,
        {method:"POST",body:JSON.stringify({campaign:"tried-to-call",trigger:"manual"})})
      if(r.sent){
        setCampaignStatus("✓ Queued at VCC")
        // Refresh status so the badge appears
        api(`/api/leads/${lead.id}/campaign-status`).then(setCampaignInfo).catch(()=>{})
      }else{
        setCampaignStatus(r.message||"Send blocked")
      }
    }catch(ex){
      setCampaignStatus(ex.message||"Couldn't reach VCC")
    }finally{ setSendingCampaign(false) }
  }

  async function revealMobile(){
    if(!window.confirm("Spend ~5-8 Apollo credits to reveal this person's direct mobile?")) return
    setRevealStatus(""); setRevealing(true)
    try{
      const r = await api(`/api/leads/${lead.id}/reveal-phone`,{method:"POST",body:"{}"})
      if(r.requested){
        setRevealStatus("📱 Searching… phone arrives in ~30s, click here to refresh")
        // Poll once after 30s for the unlocked phone
        setTimeout(async()=>{
          try{
            const fresh = await api(`/api/leads?search=${encodeURIComponent(lead.company||"")}`)
            const updated = (fresh||[]).find(l=>l.id===lead.id)
            if(updated&&updated.phone&&updated.phone!==lead.phone){
              setLead(updated)
              setRevealStatus("✓ Phone revealed: "+updated.phone)
              onSaved&&onSaved()
            }else{
              setRevealStatus("Still waiting on Apollo — refresh the lead in a minute")
            }
          }catch{ setRevealStatus("Phone request sent — refresh the lead manually") }
        },30000)
      }else{
        setRevealStatus(r.message||"Reveal request failed")
      }
    }catch(ex){
      setRevealStatus(ex.message||"Couldn't request reveal")
    }finally{ setRevealing(false) }
  }

  return(
    <div className="modal-bg" onClick={e=>e.target===e.currentTarget&&onClose()}>
      <div className="modal" style={{maxWidth:640}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:16}}>
          <div>
            <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:16,fontWeight:700,color:"#dee5ff",marginBottom:4}}>
              {[lead.firstName,lead.lastName].filter(Boolean).join(" ")||lead.company}
            </div>
            {lead.firstName&&lead.title&&(
              <div style={{fontSize:11,color:"#a3aac4",marginBottom:4}}>{lead.title} · {lead.company}</div>
            )}
            <div style={{display:"flex",gap:12,fontSize:12,color:"#a3aac4",flexWrap:"wrap",alignItems:"center"}}>
              {lead.phone&&<span>📞 {lead.phone}</span>}
              {lead.email&&<span>✉ {lead.email}</span>}
              {!lead.firstName&&(
                <button onClick={findDecisionMaker} disabled={findingDM}
                  style={{fontSize:11,padding:"4px 10px",borderRadius:6,fontFamily:"inherit",cursor:findingDM?"wait":"pointer",
                    background:"#8b5cf6",color:"#fff",border:"none",opacity:findingDM?0.7:1}}
                  title="Look up the decision maker at this company via Apollo (~2 credits)">
                  {findingDM?"Searching Apollo…":"🔍 Find Decision Maker"}
                </button>
              )}
              {lead.firstName&&!lead.phone&&phoneRevealAllowed&&(
                <button onClick={revealMobile} disabled={revealing}
                  style={{fontSize:11,padding:"4px 10px",borderRadius:6,fontFamily:"inherit",cursor:revealing?"wait":"pointer",
                    background:"#ffe083",color:"#000011",border:"none",opacity:revealing?0.7:1,fontWeight:600}}
                  title={`Reveal direct mobile via Apollo (~8 credits). Budget: ${phoneBudget?.remaining??"?"}/${phoneBudget?.monthly_cap??"?"} this month.`}>
                  {revealing?"Requesting…":"📱 Reveal Direct Mobile"}
                </button>
              )}
              {lead.firstName&&!lead.phone&&!phoneRevealAllowed&&phoneBudget&&(
                <span style={{fontSize:10,color:"#a3aac4",fontStyle:"italic"}}
                  title={phoneBudget.remaining<=0
                    ? `Phone reveal budget exhausted (${phoneBudget.used_this_month}/${phoneBudget.monthly_cap}). Resets next month.`
                    : `Phone reveals only for ${(phoneBudget.allowed_industries||[]).join(", ")} verticals. Use switchboard + ask for ${lead.firstName} by name.`}>
                  {phoneBudget.remaining<=0 ? "📱 budget exhausted" : "📱 use named-ask"}
                </span>
              )}
              {/* Email campaign trigger — only when VCC is configured AND lead has email + name */}
              {campaignInfo?.email_configured && lead.email && lead.firstName && !campaignInfo.in_campaign && (
                <button onClick={sendToCampaign} disabled={sendingCampaign}
                  style={{fontSize:11,padding:"4px 10px",borderRadius:6,fontFamily:"inherit",cursor:sendingCampaign?"wait":"pointer",
                    background:"#a3a6ff",color:"#000011",border:"none",opacity:sendingCampaign?0.7:1,fontWeight:600}}
                  title="Send the 'tried to call you' email via VCC's campaign engine">
                  {sendingCampaign?"Sending…":"📧 Send to Campaign"}
                </button>
              )}
              {campaignInfo?.in_campaign && (
                <span style={{fontSize:10,padding:"3px 8px",borderRadius:6,background:"#a3a6ff20",
                  color:"#a3a6ff",fontWeight:600}}
                  title={`In campaign since ${campaignInfo.last_sent_at?.slice(0,10)} — ${campaignInfo.send_count} send${campaignInfo.send_count===1?"":"s"}`}>
                  📧 In campaign
                </span>
              )}
              {dmStatus&&(
                <span style={{fontSize:11,color:dmStatus.startsWith("✓")?"#69f6b8":"#ffe083"}}>{dmStatus}</span>
              )}
              {revealStatus&&(
                <span style={{fontSize:11,color:revealStatus.startsWith("✓")?"#69f6b8":"#ffe083"}}>{revealStatus}</span>
              )}
              {campaignStatus&&(
                <span style={{fontSize:11,color:campaignStatus.startsWith("✓")?"#69f6b8":"#ffe083"}}>{campaignStatus}</span>
              )}
            </div>
          </div>
          <button className="btn btn-g" style={{fontSize:12,padding:"5px 10px"}} onClick={onClose}>✕</button>
        </div>

        {NO_DIAL_STATUSES.has(lead.status)&&(
          <div style={{padding:"12px 14px",marginBottom:12,
            background:lead.status==="do_not_contact"?"#ff6e8418":"#a3a6ff18",
            border:`1px solid ${lead.status==="do_not_contact"?"#ff6e8440":"#a3a6ff40"}`,
            borderRadius:8,fontSize:13,
            color:lead.status==="do_not_contact"?"#ff6e84":"#a3a6ff",
            display:"flex",alignItems:"center",gap:8}}>
            <span>{lead.status==="do_not_contact"?"🚫":"📧"}</span>
            <div>
              <div style={{fontWeight:600,marginBottom:2}}>
                {lead.status==="do_not_contact"
                  ? "Do not contact — this lead has unsubscribed."
                  : "Awaiting email reply — don't dial right now."}
              </div>
              <div style={{fontSize:11,opacity:0.8}}>
                {lead.status==="do_not_contact"
                  ? "Marked do-not-contact via VCC unsubscribe. Logging a call here is not recommended."
                  : `Email sent ${lead.updatedAt?.slice(0,10)||"recently"}. They may reply via email — give it a few days before calling.`}
              </div>
            </div>
          </div>
        )}
        {/* Persistent "previously emailed" banner — shows even after status
            moves off awaiting_email_reply, so caller knows context when working
            a returning lead. Hidden when the no-dial banner above already
            covers the same info to avoid double-stacking. */}
        {!NO_DIAL_STATUSES.has(lead.status) && campaignInfo?.send_count>0 && (
          <div style={{padding:"10px 14px",marginBottom:12,
            background:"#a3a6ff10",border:"1px solid #a3a6ff30",borderRadius:8,
            fontSize:12,color:"#a3a6ff",display:"flex",alignItems:"center",gap:8}}>
            <span>📧</span>
            <div style={{lineHeight:1.4}}>
              <b>Previously emailed</b>
              {campaignInfo.send_count>1?` (${campaignInfo.send_count} sends)`:""}
              {campaignInfo.last_sent_at ? ` — last on ${campaignInfo.last_sent_at.slice(0,10)}` : ""}
              <span style={{color:"#a3aac4",marginLeft:6}}>
                · reference the email if they bring it up
              </span>
            </div>
          </div>
        )}
        {modalError&&<div style={{padding:"10px 14px",marginBottom:12,background:"#ff6e8418",border:"1px solid #ff6e8440",
          borderRadius:8,fontSize:13,color:"#ff6e84",display:"flex",alignItems:"center",gap:8}}>
          <span>⚠</span>{modalError}
        </div>}

        {scripts.length>0&&(
          <div style={{marginBottom:16,background:"#060e20",border:"1px solid #a3a6ff25",borderRadius:10,padding:14}}>
            <div style={{fontSize:10,color:"#a3a6ff",letterSpacing:".1em",marginBottom:10}}>CALL SCRIPT</div>
            <div style={{display:"flex",gap:8,marginBottom:scriptId?10:0}}>
              <select value={scriptId} onChange={e=>{setScriptId(e.target.value);setShowScript(false)}}
                style={{flex:1,background:"#0f1930",border:"1px solid #40485d40",color:"#dee5ff",padding:"8px 10px",borderRadius:7,fontSize:12,fontFamily:"inherit"}}>
                <option value="">No script selected</option>
                {scripts.map(s=><option key={s.id} value={s.id}>{s.name}{s.industry?` (${s.industry})`:""}</option>)}
              </select>
              {scriptId&&(
                <button className="btn btn-g" style={{fontSize:11,padding:"5px 12px"}} onClick={()=>setShowScript(p=>!p)}>
                  {showScript?"Hide":"View"}
                </button>
              )}
            </div>
            {showScript&&selectedScript&&(
              <div style={{background:"#060e20",border:"1px solid #40485d40",borderRadius:8,padding:14,fontSize:12,color:"#a3aac4",lineHeight:1.8,whiteSpace:"pre-wrap",maxHeight:200,overflowY:"auto"}}>
                {selectedScript.script_text}
                {selectedScript.objection_handlers?.length>0&&(
                  <div style={{marginTop:12,borderTop:"1px solid #40485d40",paddingTop:10}}>
                    <div style={{fontSize:10,color:"#a3a6ff",letterSpacing:".08em",marginBottom:8}}>OBJECTION HANDLERS</div>
                    {selectedScript.objection_handlers.map((obj,i)=>(
                      <div key={i} style={{marginBottom:8}}>
                        <div style={{color:"#ffe083",fontSize:11}}>"{obj.objection}"</div>
                        <div style={{color:"#a3aac4",fontSize:11,paddingLeft:10}}>→ {obj.response}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Timer bar */}
        <div style={{display:"flex",alignItems:"center",gap:12,marginBottom:16,
          background:"#060e20",borderRadius:8,padding:"10px 14px"}}>
          <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:24,fontWeight:700,
            color:timerRunning?"#69f6b8":"#a3a6ff",letterSpacing:".05em",minWidth:70}}>
            {timerDisplay}
          </div>
          <button type="button" onClick={()=>setTimerRunning(r=>!r)}
            style={{fontSize:11,padding:"5px 10px",borderRadius:6,border:"1px solid #40485d40",
              background:"transparent",color:"#a3aac4",cursor:"pointer",fontFamily:"inherit"}}>
            {timerRunning?"Pause":"Resume"}
          </button>
          <div style={{flex:1}}/>
          <span style={{fontSize:10,color:"#40485d"}}>Manual:</span>
          <input type="number" value={duration} onChange={e=>setDur(e.target.value)}
            placeholder="min" min="0" style={{width:50,fontSize:12}}/>
        </div>

        {/* Step 1: What happened? */}
        <div style={{marginBottom:16}}>
          <div style={{fontSize:10,color:"#a3aac4",letterSpacing:".1em",fontWeight:700,marginBottom:10}}>
            STEP 1 — WHAT HAPPENED?
          </div>
          <div style={{display:"flex",gap:8}}>
            {PRIMARY_OUTCOMES.map(p=>(
              <button key={p.value} onClick={()=>{setPrimary(p.value);if(p.value!=="answered"){setSecondary("");setCbReason("")}}}
                style={{flex:1,padding:"14px 12px",borderRadius:10,cursor:"pointer",fontFamily:"inherit",
                  textAlign:"center",fontSize:13,fontWeight:600,transition:"all .15s",
                  background:primary===p.value?p.color+"25":"#060e20",
                  color:primary===p.value?p.color:"#a3aac4",
                  border:`2px solid ${primary===p.value?p.color:"#40485d30"}`}}>
                <div style={{fontSize:20,marginBottom:4}}>{p.icon}</div>
                {p.label}
              </button>
            ))}
          </div>
        </div>

        {/* Step 2: If answered — what was the result? */}
        {primary==="answered"&&(
          <div style={{marginBottom:16}}>
            <div style={{fontSize:10,color:"#a3aac4",letterSpacing:".1em",fontWeight:700,marginBottom:10}}>
              STEP 2 — CALL RESULT
            </div>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8}}>
              {SECONDARY_OUTCOMES.map(s=>(
                <button key={s.value} onClick={()=>setSecondary(s.value)} title={s.hint||s.label}
                  style={{padding:"12px",borderRadius:10,cursor:"pointer",fontFamily:"inherit",
                    textAlign:"center",fontSize:13,fontWeight:600,transition:"all .15s",
                    background:secondary===s.value?s.color+"25":"#060e20",
                    color:secondary===s.value?s.color:"#a3aac4",
                    border:`2px solid ${secondary===s.value?s.color:"#40485d30"}`}}>
                  <span style={{marginRight:6}}>{s.icon}</span>{s.label}
                </button>
              ))}
            </div>
            {/* Plain-language explainer for the selected result so the caller
                is never guessing what an option means. */}
            {secDef?.hint&&(
              <div style={{marginTop:10,padding:"8px 12px",borderRadius:8,fontSize:12,
                lineHeight:1.5,color:"#dee5ff",background:secDef.color+"12",
                border:`1px solid ${secDef.color}30`}}>
                <span style={{marginRight:6}}>{secDef.icon}</span>{secDef.hint}
              </div>
            )}
          </div>
        )}

        {/* Callback reason */}
        {secondary==="callback"&&(
          <div style={{marginBottom:16,background:"#060e20",borderRadius:10,padding:14,
            border:"1px solid #8b5cf625"}}>
            <div style={{fontSize:10,color:"#8b5cf6",letterSpacing:".1em",fontWeight:700,marginBottom:10}}>
              CALLBACK REASON
            </div>
            <div style={{display:"flex",flexWrap:"wrap",gap:6,marginBottom:12}}>
              {CALLBACK_REASONS.map(r=>(
                <button key={r} onClick={()=>setCbReason(r)}
                  style={{padding:"6px 12px",borderRadius:6,fontSize:11,cursor:"pointer",fontFamily:"inherit",
                    background:cbReason===r?"#8b5cf625":"transparent",
                    color:cbReason===r?"#8b5cf6":"#a3aac4",
                    border:`1px solid ${cbReason===r?"#8b5cf6":"#40485d40"}`}}>
                  {r}
                </button>
              ))}
            </div>
            <div className="ff">
              <label>Callback Date</label>
              <input type="date" value={cbDate} onChange={e=>setCbDate(e.target.value)}/>
            </div>
          </div>
        )}

        {/* Qualification — shown when needed */}
        {needsQual&&(
          <div style={{marginBottom:16,background:"#060e20",borderRadius:10,padding:14,
            border:`1px solid ${hasQualData?"#69f6b825":"#92400e"}`}}>
            <div style={{fontSize:10,letterSpacing:".1em",fontWeight:700,marginBottom:12,
              color:hasQualData?"#69f6b8":"#fbbf24"}}>
              {hasQualData?"✓ QUALIFICATION DATA":"⚠ QUALIFICATION REQUIRED"}
            </div>
            <div style={{display:"flex",flexDirection:"column",gap:12}}>
              <QualChip label="Focus" value={budgetFocus} onChange={setBudget}
                options={["Budget Focused","Quality Focused","Value Balanced"]}/>
              <QualChip label="Vendor Status" value={vendorStatus} onChange={setVendor}
                options={["Happy with Current","Open to Options","Actively Shopping","No Vendor"]}/>
              <QualChip label="Contact Type" value={decisionMaker} onChange={setDM}
                options={["Decision Maker","Influencer","Gatekeeper","Unknown"]}/>
              <QualChip label="Timeline" value={timeline} onChange={setTimeline}
                options={["Ready Now","30 Days","90 Days","Just Browsing"]}/>
              <QualChip label="Qualified?" value={qualified} onChange={setQualified}
                options={["Hot","Warm","Not Yet","Disqualified"]}/>
            </div>
          </div>
        )}

        {/* Notes + Follow-up + Submit */}
        {primary&&(step>=2||primary!=="answered")&&(
          <div style={{background:"#060e20",borderRadius:10,padding:14,marginBottom:16,
            border:"1px solid #40485d20"}}>
            <div className="ff" style={{marginBottom:12}}>
              <label>Notes</label>
              <textarea value={notes} onChange={e=>setNotes(e.target.value)} rows={2} style={{resize:"vertical"}}
                placeholder={secondary==="callback"?"What did they say? When should you call back?":"Any details from the call..."}/>
            </div>
            <div className="ff" style={{marginBottom:12}}>
              <label>Follow-up Sequence</label>
              <select value={followUpSeq} onChange={e=>setFuSeq(e.target.value)}>
                {FOLLOW_UP_SEQUENCES.map(s=><option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
            {followUpSeq&&(
              <div style={{fontSize:11,color:"#ffe083",background:"#ffe08310",border:"1px solid #ffe08325",
                borderRadius:6,padding:"8px 12px",marginBottom:12}}>
                Next follow-up: <strong>{addDays(FOLLOW_UP_DAYS[followUpSeq][0])}</strong>
                {" \u00b7 then "}
                {FOLLOW_UP_DAYS[followUpSeq].slice(1).map(d=>"+"+d+"d").join(" \u00b7 ")}
              </div>
            )}
            {/* Per-call email follow-up — only show when this is a failed dial,
                lead has email + name, VCC is configured, not already in campaign */}
            {(primary==="no_answer"||primary==="voicemail")
              && lead.email && lead.firstName
              && campaignInfo?.email_configured
              && !campaignInfo?.in_campaign && (
              <label style={{display:"flex",alignItems:"center",gap:10,padding:"10px 14px",marginBottom:10,
                background:"#a3a6ff10",border:"1px solid #a3a6ff30",borderRadius:8,cursor:"pointer",fontSize:12,color:"#dee5ff"}}>
                <input type="checkbox" checked={sendEmailFollowup} onChange={e=>setSendEmailFollowup(e.target.checked)}
                  style={{cursor:"pointer",width:16,height:16}}/>
                <div>
                  <div style={{fontWeight:600}}>📧 Send "tried to call you" email after saving</div>
                  <div style={{fontSize:10,color:"#a3aac4",marginTop:2}}>
                    To {lead.email} via VCC. Lead will move to "Awaiting Reply" so you don't re-dial.
                  </div>
                </div>
              </label>
            )}
            <button className="btn btn-p" onClick={log} disabled={saving||
              (primary==="answered"&&!secondary)||
              (needsQual&&!hasQualData)||
              (secondary==="callback"&&!cbDate)}
              style={{width:"100%",padding:"14px",fontSize:14,fontFamily:"'Space Grotesk',sans-serif",fontWeight:700}}>
              {saving?"Saving...":"Log Call"}
            </button>
            {(needsQual&&!hasQualData)&&(
              <div style={{fontSize:11,color:"#fbbf24",textAlign:"center",marginTop:8}}>
                Fill out qualification above to enable logging
              </div>
            )}
          </div>
        )}
        {calls.length>0&&(
          <>
            <div style={{fontSize:10,color:"#a3aac4",letterSpacing:".08em",marginBottom:10}}>HISTORY ({calls.length})</div>
            <div style={{display:"flex",flexDirection:"column",gap:6,maxHeight:200,overflowY:"auto"}}>
              {calls.map(c=>{
                const info=si(c.outcome?.replace("voicemail","no_answer").replace("answered","called"))
                return(
                  <div key={c.id} style={{background:"#060e20",border:"1px solid #40485d30",borderRadius:8,padding:"10px 12px"}}>
                    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:4}}>
                      <span className="pill" style={{background:info.color+"25",color:info.color,border:`1px solid ${info.color}35`}}>
                        {(c.outcome||"").replace(/_/g," ")}
                      </span>
                      <div style={{fontSize:10,color:"#a3aac4"}}>
                        {new Date(c.calledAt).toLocaleDateString()}
                        {c.duration>0&&<span style={{marginLeft:8}}>{Math.round(c.duration/60)}m</span>}
                        <span style={{marginLeft:8,color:"#40485d"}}>· {c.calledBy}</span>
                      </div>
                    </div>
                    {c.notes&&<div style={{fontSize:11,color:"#a3aac4",marginBottom:4}}>{c.notes}</div>}
                    {(c.budgetfocus||c.vendorstatus||c.decisionmaker||c.timeline||c.qualified)&&(
                      <div style={{display:"flex",gap:4,flexWrap:"wrap"}}>
                        {[c.budgetfocus,c.vendorstatus,c.decisionmaker,c.timeline,c.qualified].filter(Boolean).map((t,i)=>(
                          <span key={i} style={{fontSize:9,background:"#192540",color:"#a3aac4",padding:"2px 6px",borderRadius:4}}>{t}</span>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ─── EmailModal ──────────────────────────────────────────────────────────────

function EmailModal({lead,onClose,onSent}){
  const defaultBody=(name)=>`Hi ${name},\n\nThank you for taking our call today. It was great connecting with you.\n\nIf you'd like a free, no obligation estimate, simply reply to this email with your approximate square footage and how often you'd like service (daily, weekly, bi weekly, monthly) and we'll send a custom quote your way as soon as possible.\n\nYou can also learn more about Vision Cleaning Company and the services we offer at https://visioncleaningcompanyllc.com. Feel free to request your quote directly through our site any time.\n\nWe appreciate the opportunity and look forward to the chance to work with you.\n\nBest regards,\nVision Cleaning Company\nconnect@visioncleaningcompanyllc.com\nhttps://visioncleaningcompanyllc.com`

  const [toEmail,setToEmail]=useState(lead?.email||"")
  const [toName,setToName]=useState(lead?[lead.firstName,lead.lastName].filter(Boolean).join(" "):"")
  const [subject,setSubject]=useState(lead?.company?`Following Up — ${lead.company}`:"Following Up")
  const [body,setBody]=useState(()=>defaultBody(lead?.firstName||lead?.company||"there"))
  const [sending,setSending]=useState(false)
  const [err,setErr]=useState("")
  const [sent,setSent]=useState(false)
  const [emailHistory,setEmailHistory]=useState([])
  const [showHistory,setShowHistory]=useState(false)
  const [templates,setTemplates]=useState([])
  const [selectedTpl,setSelectedTpl]=useState("")
  const [showManage,setShowManage]=useState(false)

  useEffect(()=>{
    api("/api/email-templates").then(r=>{if(Array.isArray(r))setTemplates(r)}).catch(()=>{})
    if(lead?.id){
      api(`/api/email/history?lead_id=${lead.id}`).then(r=>{
        if(Array.isArray(r)) setEmailHistory(r)
      }).catch(()=>{})
    }
  },[lead?.id])

  const applyTemplate=(tplId)=>{
    const tpl=templates.find(t=>t.id===parseInt(tplId))
    if(!tpl){setSelectedTpl("");return}
    setSelectedTpl(tplId)
    // Personalize: replace {{name}}, {{company}}, {{firstName}}
    const name=lead?.firstName||lead?.company||"there"
    const co=lead?.company||"your company"
    let s=tpl.subject.replace(/\{\{name\}\}/gi,name).replace(/\{\{company\}\}/gi,co).replace(/\{\{firstName\}\}/gi,lead?.firstName||"there")
    let b=tpl.body.replace(/\{\{name\}\}/gi,name).replace(/\{\{company\}\}/gi,co).replace(/\{\{firstName\}\}/gi,lead?.firstName||"there")
    setSubject(s)
    setBody(b)
    api(`/api/email-templates/${tpl.id}/use`,{method:"POST",body:JSON.stringify({})}).catch(()=>{})
  }

  const doSend=async()=>{
    if(!toEmail||!toEmail.includes("@")){setErr("Valid email address required");return}
    if(!subject.trim()){setErr("Subject is required");return}
    if(!body.trim()){setErr("Email body is required");return}
    setSending(true);setErr("")
    try{
      const htmlBody=body.split("\n").map(l=>`<p>${l}</p>`).join("")
      const r=await api("/api/email/send",{method:"POST",body:JSON.stringify({
        lead_id:lead?.id||null,to_email:toEmail.trim(),to_name:toName.trim(),
        subject:subject.trim(),body:htmlBody,company:lead?.company||""
      })})
      if(r.sent){setSent(true);if(onSent)onSent()}
      else setErr(r.detail||"Failed to send")
    }catch(e){setErr(e.message||"Failed to send")}
    finally{setSending(false)}
  }

  const inputStyle={width:"100%",padding:"10px 14px",background:"#0a1628",border:"1px solid #40485d30",
    borderRadius:10,color:"#dee5ff",fontSize:14,fontFamily:"inherit",outline:"none",boxSizing:"border-box"}

  if(showManage) return <EmailTemplatesModal onClose={()=>{
    setShowManage(false)
    api("/api/email-templates").then(r=>{if(Array.isArray(r))setTemplates(r)}).catch(()=>{})
  }}/>

  return(
    <div className="modal-bg" onClick={e=>e.target===e.currentTarget&&onClose()}>
      <div className="modal" style={{maxWidth:680,maxHeight:"90vh",display:"flex",flexDirection:"column"}}>
        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:18}}>
          <div>
            <h3 style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:22,fontWeight:700,color:"#dee5ff",margin:0}}>
              Send Email
            </h3>
            {lead?.company&&<div style={{fontSize:13,color:"#a3aac4",marginTop:2}}>{lead.company}</div>}
          </div>
          <button onClick={onClose} style={{background:"none",border:"none",color:"#40485d",fontSize:24,
            cursor:"pointer",padding:4}}>&times;</button>
        </div>

        {sent?(
          <div style={{textAlign:"center",padding:"40px 0"}}>
            <div style={{fontSize:48,marginBottom:12}}>&#9993;</div>
            <div style={{fontSize:18,fontWeight:700,color:"#69f6b8",marginBottom:8}}>Email Sent!</div>
            <div style={{fontSize:13,color:"#a3aac4",marginBottom:20}}>
              Sent to {toEmail}
            </div>
            <button className="btn btn-p" onClick={onClose} style={{padding:"10px 32px"}}>Close</button>
          </div>
        ):(
          <div style={{flex:1,overflowY:"auto"}}>
            {err&&<div style={{padding:"10px 14px",marginBottom:12,background:"#ff6e8418",
              border:"1px solid #ff6e8440",borderRadius:8,fontSize:13,color:"#ff6e84",
              display:"flex",alignItems:"center",gap:8}}>
              <span>&#9888;</span>{err}
            </div>}

            {/* Template selector */}
            <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:14}}>
              <select value={selectedTpl} onChange={e=>applyTemplate(e.target.value)}
                style={{...inputStyle,flex:1,cursor:"pointer"}}>
                <option value="">-- Select a template --</option>
                {templates.map(t=><option key={t.id} value={t.id}>{t.name}{t.industry?` (${t.industry})`:""}</option>)}
              </select>
              <button className="btn btn-g" style={{fontSize:11,padding:"10px 14px",whiteSpace:"nowrap"}}
                onClick={()=>setShowManage(true)}>Manage Templates</button>
            </div>

            <div style={{display:"grid",gap:12,marginBottom:16}}>
              <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12}}>
                <div>
                  <label style={{fontSize:11,color:"#a3aac4",fontWeight:600,marginBottom:4,display:"block"}}>To Email *</label>
                  <input value={toEmail} onChange={e=>setToEmail(e.target.value)} placeholder="prospect@company.com"
                    style={inputStyle}/>
                </div>
                <div>
                  <label style={{fontSize:11,color:"#a3aac4",fontWeight:600,marginBottom:4,display:"block"}}>Recipient Name</label>
                  <input value={toName} onChange={e=>setToName(e.target.value)} placeholder="John Smith"
                    style={inputStyle}/>
                </div>
              </div>
              <div>
                <label style={{fontSize:11,color:"#a3aac4",fontWeight:600,marginBottom:4,display:"block"}}>Subject *</label>
                <input value={subject} onChange={e=>setSubject(e.target.value)} placeholder="Following Up — Company"
                  style={inputStyle}/>
              </div>
              <div>
                <label style={{fontSize:11,color:"#a3aac4",fontWeight:600,marginBottom:4,display:"block"}}>Message *
                  <span style={{fontWeight:400,color:"#40485d",marginLeft:8}}>Use {"{{name}}"}, {"{{company}}"}, {"{{firstName}}"} for personalization</span>
                </label>
                <textarea value={body}
                  onChange={e=>setBody(e.target.value)}
                  placeholder="Hi {{name}}, thanks for speaking with us..."
                  style={{...inputStyle,minHeight:180,resize:"vertical",lineHeight:1.6}}/>
              </div>
            </div>

            <div style={{display:"flex",alignItems:"center",gap:12,marginBottom:16}}>
              <button className="btn btn-p" onClick={doSend} disabled={sending}
                style={{padding:"12px 32px",fontSize:14,fontWeight:700,opacity:sending?.6:1}}>
                {sending?"Sending...":"Send Email"}
              </button>
              <button className="btn btn-g" onClick={onClose} style={{padding:"12px 24px",fontSize:14}}>Cancel</button>
              <div style={{flex:1,textAlign:"right",fontSize:11,color:"#40485d"}}>
                From: connect@visioncleaningcompanyllc.com
              </div>
            </div>

            {emailHistory.length>0&&(
              <div style={{borderTop:"1px solid #40485d15",paddingTop:14}}>
                <div onClick={()=>setShowHistory(p=>!p)}
                  style={{fontSize:11,color:"#a3aac4",fontWeight:700,letterSpacing:".08em",
                    textTransform:"uppercase",cursor:"pointer",display:"flex",alignItems:"center",gap:6}}>
                  Previous Emails ({emailHistory.length})
                  <span style={{fontSize:10,transition:"transform .2s",
                    transform:showHistory?"rotate(180deg)":"rotate(0)"}}>&#9660;</span>
                </div>
                {showHistory&&(
                  <div style={{marginTop:10,maxHeight:200,overflowY:"auto"}}>
                    {emailHistory.map((em,i)=>(
                      <div key={em.id||i} style={{padding:"10px 0",borderBottom:"1px solid #40485d08",fontSize:12}}>
                        <div style={{display:"flex",justifyContent:"space-between",marginBottom:2}}>
                          <span style={{color:"#dee5ff",fontWeight:600}}>{em.subject}</span>
                          <span style={{color:"#40485d",fontSize:10}}>
                            {em.sent_at?new Date(em.sent_at).toLocaleDateString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}):""}
                          </span>
                        </div>
                        <div style={{color:"#40485d"}}>To: {em.to_email} &middot; By: {em.sent_by}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── EmailTemplatesModal (manage templates — same UX as ScriptsModal) ────────

function EmailTemplatesModal({onClose}){
  const [templates,setTemplates]=useState([])
  const [loading,setLoad]=useState(true)
  const [editing,setEditing]=useState(null)
  const [form,setForm]=useState({name:"",industry:"",subject:"",body:""})
  const [saving,setSaving]=useState(false)
  const [toast,setToast]=useState(null)

  useEffect(()=>{ fetchTemplates() },[])

  async function fetchTemplates(){
    setLoad(true)
    try{ const r=await api("/api/email-templates"); setTemplates(Array.isArray(r)?r:[]) }catch{}
    finally{ setLoad(false) }
  }

  function showToast(msg){ setToast(msg); setTimeout(()=>setToast(null),2500) }
  function openNew(){ setEditing("new"); setForm({name:"",industry:"",subject:"",body:""}) }
  function openEdit(t){ setEditing(t.id); setForm({name:t.name,industry:t.industry||"",subject:t.subject,body:t.body}) }

  async function save(){
    if(!form.name||!form.subject||!form.body){ showToast("Name, subject, and body required"); return }
    setSaving(true)
    try{
      if(editing==="new") await api("/api/email-templates",{method:"POST",body:JSON.stringify(form)})
      else await api(`/api/email-templates/${editing}`,{method:"PATCH",body:JSON.stringify(form)})
      showToast("Saved"); setEditing(null); fetchTemplates()
    }catch(ex){ showToast("Error: "+ex.message) }
    finally{ setSaving(false) }
  }

  async function del(id){
    if(!window.confirm("Delete this template?")) return
    await api(`/api/email-templates/${id}`,{method:"DELETE"})
    fetchTemplates()
  }

  return(
    <div className="modal-bg" onClick={e=>e.target===e.currentTarget&&onClose()}>
      <div className="modal" style={{maxWidth:680}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:20}}>
          <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:16,fontWeight:700,color:"#dee5ff"}}>EMAIL TEMPLATES</div>
          <div style={{display:"flex",gap:8}}>
            {!editing&&<button className="btn btn-p" style={{fontSize:12}} onClick={openNew}>+ New Template</button>}
            <button className="btn btn-g" onClick={onClose}>&times;</button>
          </div>
        </div>
        {editing?(
          <div>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:10,marginBottom:12}}>
              <div className="ff">
                <label>Template Name</label>
                <input value={form.name} onChange={e=>setForm(f=>({...f,name:e.target.value}))} placeholder="Post-call follow up"/>
              </div>
              <div className="ff">
                <label>Industry (optional)</label>
                <select value={form.industry} onChange={e=>setForm(f=>({...f,industry:e.target.value}))}>
                  <option value="">All Industries</option>
                  {INDUSTRIES.map(i=><option key={i} value={i}>{i}</option>)}
                </select>
              </div>
            </div>
            <div className="ff" style={{marginBottom:12}}>
              <label>Subject Line</label>
              <input value={form.subject} onChange={e=>setForm(f=>({...f,subject:e.target.value}))}
                placeholder="Following Up — {{company}}"/>
            </div>
            <div className="ff" style={{marginBottom:14}}>
              <label>Email Body</label>
              <textarea value={form.body} onChange={e=>setForm(f=>({...f,body:e.target.value}))}
                rows={10} style={{resize:"vertical"}}
                placeholder={"Hi {{name}},\n\nThank you for taking the time to speak with us...\n\nUse {{name}}, {{company}}, {{firstName}} for personalization."}/>
            </div>
            <div style={{display:"flex",gap:8,justifyContent:"flex-end"}}>
              <button className="btn btn-g" onClick={()=>setEditing(null)}>Cancel</button>
              <button className="btn btn-p" onClick={save} disabled={saving}>{saving?"Saving...":"Save Template"}</button>
            </div>
            {toast&&<div style={{marginTop:10,fontSize:12,color:"#69f6b8"}}>{toast}</div>}
          </div>
        ):loading?(
          <div style={{textAlign:"center",padding:40,color:"#a3aac4"}}>Loading...</div>
        ):templates.length===0?(
          <div style={{textAlign:"center",padding:40,color:"#a3aac4"}}>
            <div style={{fontSize:32,marginBottom:12}}>&#9993;</div>
            <div style={{fontSize:13,marginBottom:16}}>No email templates yet</div>
            <button className="btn btn-p" onClick={openNew}>Create your first template</button>
          </div>
        ):(
          <div style={{display:"flex",flexDirection:"column",gap:8}}>
            {templates.map(t=>(
              <div key={t.id} style={{background:"#060e20",border:"1px solid #40485d30",borderRadius:10,padding:14}}>
                <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:8}}>
                  <div>
                    <div style={{fontSize:13,fontWeight:600,color:"#dee5ff",marginBottom:3}}>{t.name}</div>
                    <div style={{display:"flex",gap:8,fontSize:10,color:"#a3aac4"}}>
                      {t.industry&&<span style={{background:"#a3a6ff18",color:"#a3a6ff",padding:"1px 6px",borderRadius:4}}>{t.industry}</span>}
                      <span>{t.usage_count||0} uses</span>
                    </div>
                  </div>
                  <div style={{display:"flex",gap:6}}>
                    <button className="btn btn-g" style={{fontSize:11,padding:"4px 10px"}} onClick={()=>openEdit(t)}>Edit</button>
                    <button className="btn btn-r" style={{fontSize:11,padding:"4px 10px"}} onClick={()=>del(t.id)}>Del</button>
                  </div>
                </div>
                <div style={{fontSize:11,color:"#a3aac4",marginBottom:4,fontWeight:600}}>Subject: {t.subject}</div>
                <div style={{fontSize:11,color:"#40485d",lineHeight:1.5,maxHeight:48,overflow:"hidden"}}>
                  {t.body.slice(0,120)}{t.body.length>120?"...":""}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── LeadModal ───────────────────────────────────────────────────────────────

function LeadModal({lead,onClose,onSaved}){
  const [form,setForm]=useState(lead?{...emptyForm,...lead}:emptyForm)
  const [saving,setSave]=useState(false)
  const isEdit=!!lead?.id
  const f=key=>({value:form[key]||"",onChange:e=>setForm(p=>({...p,[key]:e.target.value}))})

  async function save(){
    if(!form.company&&!form.firstName){alert("Company or name required");return}
    setSave(true)
    try{
      if(isEdit){
        await api(`/api/leads/${lead.id}`,{method:"PATCH",body:JSON.stringify({...form,updatedAt:new Date().toISOString()})})
      } else {
        await api("/api/leads",{method:"POST",body:JSON.stringify({...form,createdBy:getUser(),createdAt:new Date().toISOString()})})
      }
      onSaved(); onClose()
    }catch(ex){alert("Error: "+ex.message)}
    finally{setSave(false)}
  }

  return(
    <div className="modal-bg" onClick={e=>e.target===e.currentTarget&&onClose()}>
      <div className="modal" style={{maxWidth:660}}>
        <div style={{display:"flex",justifyContent:"space-between",marginBottom:20}}>
          <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:16,fontWeight:700,color:"#dee5ff"}}>{isEdit?"EDIT LEAD":"NEW LEAD"}</div>
          <button className="btn btn-g" onClick={onClose}>✕</button>
        </div>
        <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:10,marginBottom:10}}>
          {[["firstName","First Name","Jane"],["lastName","Last Name","Smith"],["title","Title","VP Sales"],
            ["company","Company *","Acme Corp"],["phone","Phone","(555) 000-0000"],["email","Email","jane@acme.com"],
            ["city","City","Chicago"],["state","State","IL"],["website","Website","https://..."]].map(([k,l,p])=>(
            <div key={k} className="ff"><label>{l}</label><input {...f(k)} placeholder={p}/></div>
          ))}
          <div className="ff"><label>Industry</label><input {...f("industry")} placeholder="Healthcare"/></div>
          <div className="ff"><label>Status</label>
            <select {...f("status")}>{STATUS_OPTIONS.map(s=><option key={s.value} value={s.value}>{s.label}</option>)}</select>
          </div>
          <div className="ff"><label>Assigned To</label><input {...f("assignedTo")} placeholder="Alice"/></div>
          <div className="ff"><label>Callback Date</label><input type="date" {...f("callbackDate")}/></div>
          <div className="ff"><label>Source</label><input {...f("source")} placeholder="SAM.gov…"/></div>
        </div>
        <div className="ff" style={{marginBottom:18}}>
          <label>Notes</label>
          <textarea {...f("notes")} rows={2} style={{resize:"vertical"}} placeholder="Notes…"/>
        </div>
        <div style={{display:"flex",gap:8}}>
          <button className="btn btn-p" onClick={save} disabled={saving}>{saving?"Saving…":isEdit?"Save Changes":"Add Lead"}</button>
          <button className="btn btn-g" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  )
}

// ─── ImportModal ─────────────────────────────────────────────────────────────

function ImportModal({onClose,onDone}){
  const [preview,setPrev]=useState(null)
  const [assignTo,setAT]=useState("")
  const [loading,setLoad]=useState(false)
  const fileRef=useRef()

  function handleFile(e){
    const file=e.target.files[0]; if(!file) return
    const r=new FileReader(); r.onload=ev=>setPrev(parseCSV(ev.target.result)); r.readAsText(file)
  }

  async function doImport(){
    if(!preview) return; setLoad(true)
    try{
      const rows=preview.map(l=>({...l,assignedTo:assignTo||l.assignedTo||"",
        createdBy:getUser(),status:l.status||"new",
        createdAt:new Date().toISOString(),updatedAt:new Date().toISOString()}))
      await api("/api/leads/import",{method:"POST",body:JSON.stringify(rows)})
      onDone(`✓ Imported ${rows.length} leads`); onClose()
    }catch(ex){alert("Error: "+ex.message)}
    finally{setLoad(false)}
  }

  return(
    <div className="modal-bg" onClick={e=>e.target===e.currentTarget&&onClose()}>
      <div className="modal">
        <div style={{display:"flex",justifyContent:"space-between",marginBottom:20}}>
          <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:16,fontWeight:700,color:"#dee5ff"}}>IMPORT CSV</div>
          <button className="btn btn-g" onClick={onClose}>✕</button>
        </div>
        {!preview?(
          <div className="dropzone" onClick={()=>fileRef.current?.click()}>
            <div style={{fontSize:28,marginBottom:8}}>↑</div>
            <div style={{color:"#a3aac4",fontSize:13}}>Click to upload CSV</div>
            <input ref={fileRef} type="file" accept=".csv" style={{display:"none"}} onChange={handleFile}/>
          </div>
        ):(
          <div>
            <div style={{padding:14,background:"#060e20",border:"1px solid #69f6b830",borderRadius:8,marginBottom:14}}>
              <div style={{color:"#69f6b8",fontSize:13,marginBottom:8}}>✓ {preview.length} leads parsed</div>
              {preview.slice(0,4).map((l,i)=>(
                <div key={i} style={{fontSize:11,color:"#a3aac4",borderLeft:"2px solid #a3a6ff",paddingLeft:8,marginBottom:3}}>
                  {[l.firstName,l.lastName,l.company,l.phone].filter(Boolean).join(" · ")}
                </div>
              ))}
              {preview.length>4&&<div style={{fontSize:11,color:"#40485d"}}>…+{preview.length-4} more</div>}
            </div>
            <div className="ff" style={{marginBottom:14,maxWidth:220}}>
              <label>Assign all to (optional)</label>
              <input value={assignTo} onChange={e=>setAT(e.target.value)} placeholder="Alice"/>
            </div>
            <div style={{display:"flex",gap:8}}>
              <button className="btn btn-gr" onClick={doImport} disabled={loading}>
                {loading?"Importing…":"Import "+preview.length+" Leads"}
              </button>
              <button className="btn btn-g" onClick={()=>{setPrev(null);if(fileRef.current)fileRef.current.value=""}}>Re-upload</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── ScriptsModal ─────────────────────────────────────────────────────────────

function ScriptsModal({onClose}){
  const [scripts,setScripts]   = useState([])
  const [loading,setLoad]      = useState(true)
  const [editing,setEditing]   = useState(null)
  const [form,setForm]         = useState({name:"",industry:"",script_text:"",objection_handlers:[]})
  const [saving,setSaving]     = useState(false)
  const [newObj,setNewObj]     = useState({objection:"",response:""})
  const [toast,setToast]       = useState(null)

  useEffect(()=>{ fetchScripts() },[])

  async function fetchScripts(){
    setLoad(true)
    try{ setScripts(await api("/api/scripts")||[]) }catch{}
    finally{ setLoad(false) }
  }

  function showToast(msg){ setToast(msg); setTimeout(()=>setToast(null),2500) }
  function openNew(){ setEditing("new"); setForm({name:"",industry:"",script_text:"",objection_handlers:[]}) }
  function openEdit(s){ setEditing(s.id); setForm({name:s.name,industry:s.industry||"",script_text:s.script_text,objection_handlers:s.objection_handlers||[]}) }
  function addObjection(){
    if(!newObj.objection||!newObj.response) return
    setForm(f=>({...f,objection_handlers:[...f.objection_handlers,{...newObj}]}))
    setNewObj({objection:"",response:""})
  }
  function removeObjection(i){ setForm(f=>({...f,objection_handlers:f.objection_handlers.filter((_,idx)=>idx!==i)})) }

  async function save(){
    if(!form.name||!form.script_text){ showToast("Name and script required"); return }
    setSaving(true)
    try{
      if(editing==="new") await api("/api/scripts",{method:"POST",body:JSON.stringify(form)})
      else await api(`/api/scripts/${editing}`,{method:"PATCH",body:JSON.stringify(form)})
      showToast("✓ Saved"); setEditing(null); fetchScripts()
    }catch(ex){ showToast("Error: "+ex.message) }
    finally{ setSaving(false) }
  }

  async function del(id){
    if(!window.confirm("Delete this script?")) return
    await api(`/api/scripts/${id}`,{method:"DELETE"})
    fetchScripts()
  }

  return(
    <div className="modal-bg" onClick={e=>e.target===e.currentTarget&&onClose()}>
      <div className="modal" style={{maxWidth:680}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:20}}>
          <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:16,fontWeight:700,color:"#dee5ff"}}>CALL SCRIPTS</div>
          <div style={{display:"flex",gap:8}}>
            {!editing&&<button className="btn btn-p" style={{fontSize:12}} onClick={openNew}>+ New Script</button>}
            <button className="btn btn-g" onClick={onClose}>✕</button>
          </div>
        </div>
        {editing?(
          <div>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:10,marginBottom:12}}>
              <div className="ff">
                <label>Script Name</label>
                <input value={form.name} onChange={e=>setForm(f=>({...f,name:e.target.value}))} placeholder="Cold intro script"/>
              </div>
              <div className="ff">
                <label>Industry (optional)</label>
                <select value={form.industry} onChange={e=>setForm(f=>({...f,industry:e.target.value}))}>
                  <option value="">All Industries</option>
                  {INDUSTRIES.map(i=><option key={i} value={i}>{i}</option>)}
                </select>
              </div>
            </div>
            <div className="ff" style={{marginBottom:14}}>
              <label>Script</label>
              <textarea value={form.script_text} onChange={e=>setForm(f=>({...f,script_text:e.target.value}))}
                rows={8} style={{resize:"vertical"}}
                placeholder={"Hi, this is [name] calling from Vision Cleaning Company...\n\nI'm reaching out because we specialize in commercial cleaning for facilities like yours..."}/>
            </div>
            <div style={{marginBottom:14}}>
              <div style={{fontSize:10,color:"#a3a6ff",letterSpacing:".1em",marginBottom:10}}>OBJECTION HANDLERS</div>
              {form.objection_handlers.map((obj,i)=>(
                <div key={i} style={{background:"#060e20",border:"1px solid #40485d40",borderRadius:8,padding:10,marginBottom:8}}>
                  <div style={{display:"flex",justifyContent:"space-between",marginBottom:4}}>
                    <div style={{fontSize:11,color:"#ffe083"}}>"{obj.objection}"</div>
                    <button onClick={()=>removeObjection(i)} style={{background:"none",border:"none",color:"#ff6e8060",cursor:"pointer",fontSize:12}}>✕</button>
                  </div>
                  <div style={{fontSize:11,color:"#a3aac4"}}>→ {obj.response}</div>
                </div>
              ))}
              <div style={{display:"grid",gridTemplateColumns:"1fr 1fr auto",gap:8,marginTop:8}}>
                <input value={newObj.objection} onChange={e=>setNewObj(o=>({...o,objection:e.target.value}))}
                  placeholder="We already have a cleaner..."
                  style={{background:"#060e20",border:"1px solid #40485d40",color:"#dee5ff",padding:"8px 10px",borderRadius:7,fontSize:11,fontFamily:"inherit",outline:"none"}}/>
                <input value={newObj.response} onChange={e=>setNewObj(o=>({...o,response:e.target.value}))}
                  placeholder="That's great — what I've found is..."
                  style={{background:"#060e20",border:"1px solid #40485d40",color:"#dee5ff",padding:"8px 10px",borderRadius:7,fontSize:11,fontFamily:"inherit",outline:"none"}}/>
                <button className="btn btn-g" style={{fontSize:11}} onClick={addObjection}>+ Add</button>
              </div>
            </div>
            <div style={{display:"flex",gap:8,justifyContent:"flex-end"}}>
              <button className="btn btn-g" onClick={()=>setEditing(null)}>Cancel</button>
              <button className="btn btn-p" onClick={save} disabled={saving}>{saving?"Saving…":"Save Script"}</button>
            </div>
            {toast&&<div style={{marginTop:10,fontSize:12,color:"#69f6b8"}}>{toast}</div>}
          </div>
        ):loading?(
          <div style={{textAlign:"center",padding:40,color:"#a3aac4"}}>Loading…</div>
        ):scripts.length===0?(
          <div style={{textAlign:"center",padding:40,color:"#a3aac4"}}>
            <div style={{fontSize:32,marginBottom:12}}>📋</div>
            <div style={{fontSize:13,marginBottom:16}}>No scripts yet</div>
            <button className="btn btn-p" onClick={openNew}>Create your first script</button>
          </div>
        ):(
          <div style={{display:"flex",flexDirection:"column",gap:8}}>
            {scripts.map(s=>(
              <div key={s.id} style={{background:"#060e20",border:"1px solid #40485d30",borderRadius:10,padding:14}}>
                <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:8}}>
                  <div>
                    <div style={{fontSize:13,fontWeight:600,color:"#dee5ff",marginBottom:3}}>{s.name}</div>
                    <div style={{display:"flex",gap:8,fontSize:10,color:"#a3aac4"}}>
                      {s.industry&&<span style={{background:"#a3a6ff18",color:"#a3a6ff",padding:"1px 6px",borderRadius:4}}>{s.industry}</span>}
                      <span>{s.usage_count||0} uses</span>
                      {s.objection_handlers?.length>0&&<span>{s.objection_handlers.length} objection{s.objection_handlers.length!==1?"s":""}</span>}
                    </div>
                  </div>
                  <div style={{display:"flex",gap:6}}>
                    <button className="btn btn-g" style={{fontSize:11,padding:"4px 10px"}} onClick={()=>openEdit(s)}>Edit</button>
                    <button className="btn btn-r" style={{fontSize:11,padding:"4px 10px"}} onClick={()=>del(s.id)}>Del</button>
                  </div>
                </div>
                <div style={{fontSize:11,color:"#a3aac4",lineHeight:1.6,maxHeight:60,overflow:"hidden"}}>
                  {s.script_text.slice(0,150)}{s.script_text.length>150?"…":""}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── StatsBar ─────────────────────────────────────────────────────────────────

function StatsBar({stats,onCallbacks}){
  if(!stats) return null
  const items=[
    {l:"Calls Today",  v:stats.callsToday||0,              c:"#a3a6ff", border:"#a3a6ff"},
    {l:"Conversions",  v:stats.converted||0,               c:"#69f6b8", border:"#69f6b8"},
    {l:"Follow Ups",   v:stats.callbacksDue||0,            c:"#ffe083", border:"#ffe083", onClick:onCallbacks},
    {l:"Contact Rate",  v:(stats.contactRate||"0.0")+"%",  c:"#8b5cf6", border:"#8b5cf6"},
    {l:"Conv. Rate",   v:(stats.conversionRate||0)+"%",    c:"#ff6e84", border:"#ff6e84"},
  ]
  return(
    <div style={{display:"grid",gridTemplateColumns:`repeat(${items.length},1fr)`,gap:12}}>
      {items.map(s=>(
        <div key={s.l}
          onClick={s.onClick}
          style={{background:"#0f1930",borderRadius:10,padding:"14px 18px",
            borderLeft:`4px solid ${s.border}`,cursor:s.onClick?"pointer":"default",
            transition:"background .15s"}}
          onMouseEnter={e=>{if(s.onClick)e.currentTarget.style.background="#141f38"}}
          onMouseLeave={e=>{if(s.onClick)e.currentTarget.style.background="#0f1930"}}>
          <div style={{fontSize:"0.6rem",color:"#a3aac4",fontWeight:700,letterSpacing:".1em",
            textTransform:"uppercase",marginBottom:6}}>{s.l}</div>
          <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:22,fontWeight:700,color:s.c}}>{s.v}</div>
        </div>
      ))}
    </div>
  )
}

// ─── Avatar helpers ───────────────────────────────────────────────────────────

const AVATAR_COLORS=["#a3a6ff","#69f6b8","#ffe083","#ff6e84","#8b5cf6","#06d6a0"]
function avatarColor(str){
  let h=0; for(const c of (str||"")) h=(h*31+c.charCodeAt(0))&0xffffffff
  return AVATAR_COLORS[Math.abs(h)%AVATAR_COLORS.length]
}
function getInitials(lead){
  if(lead.firstName&&lead.lastName) return(lead.firstName[0]+lead.lastName[0]).toUpperCase()
  if(lead.firstName) return lead.firstName.slice(0,2).toUpperCase()
  if(lead.company) return lead.company.slice(0,2).toUpperCase()
  return"??"
}

// ─── IconButton helper ────────────────────────────────────────────────────────

function IconBtn({onClick,children,title,hoverColor="#dee5ff",baseColor="#a3aac4"}){
  const [hov,setHov]=useState(false)
  return(
    <button onClick={onClick} title={title}
      onMouseEnter={()=>setHov(true)} onMouseLeave={()=>setHov(false)}
      style={{padding:8,background:hov?"#192540":"transparent",border:"none",
        color:hov?hoverColor:baseColor,cursor:"pointer",borderRadius:8,
        display:"flex",alignItems:"center",justifyContent:"center",transition:"all .15s"}}>
      {children}
    </button>
  )
}

// ─── App ──────────────────────────────────────────────────────────────────────

const SIDEBAR_NAV = [
  { key:"dashboard", label:"Dashboard",       Icon:IconDashboard },
  { key:"leads",     label:"Leads",           Icon:IconPeople },
  { key:"lookup",    label:"Phone Lookup",    Icon:IconSearch },
  { key:"warm",      label:"Warm Leads",      Icon:IconChart },
  { key:"dialer",    label:"Dialer",          Icon:IconPhone },
  { key:"followups", label:"Follow-Ups",      Icon:IconCalendarFar },
  { key:"qualified", label:"Qualified",        Icon:IconClipCheck },
  { key:"campaigns", label:"Email Campaigns",  Icon:IconMail },
  { key:"analytics", label:"Analytics",       Icon:IconChart },
  { key:"history",   label:"History",         Icon:IconHistory },
]

export default function App(){
  const [user,setUser]             = useState(()=>isLoggedIn()?localStorage.getItem("lf_user"):null)
  const [leads,setLeads]           = useState([])
  // Persistent "this lead was emailed" map. Survives status changes so the
  // badge stays even after a reply flips the lead back to 'interested'.
  // Shape: { [lead_id]: {last_emailed_at, email_count} }
  const [emailedFlags,setEmailedFlags] = useState({})
  const [stats,setStats]           = useState(null)
  const [loading,setLoad]          = useState(false)
  const [toast,setToast]           = useState(null)
  const [industries,setIndustries] = useState([])
  const [callModal,setCallModal]   = useState(null)
  const [editModal,setEditModal]   = useState(null)
  const [showImport,setImport]     = useState(false)
  const [showScripts,setShowScripts] = useState(false)
  const [search,setSearch]         = useState("")
  const [fStatus,setFStatus]       = useState("all")
  const [sortBy,setSort]           = useState("smart")
  const [cbOnly,setCbOnly]         = useState(false)
  const [fIndustry,setFIndustry]   = useState("")
  const [fState,setFState]         = useState("")
  const [fCity,setFCity]           = useState("")
  const [availableOnly,setAvailOnly] = useState(false)
  const [emailedOnly,setEmailedOnly] = useState(false)
  // "Needs Another Call" quick-filter: unanswered leads (no_answer) with under
  // 4 attempts — the pool to work when there's nothing fresh (4×-before-giveup).
  const [needsAttemptOnly,setNeedsAttemptOnly] = useState(false)
  // Per-state collapse for the state-grouped Leads view. Key = state code (or
  // "__none__" for leads with no state). Empty = all expanded.
  const [collapsedStates,setCollapsedStates] = useState({})
  const toggleState = (k)=>setCollapsedStates(p=>({...p,[k]:!p[k]}))
  const [activeNav,setNav]         = useState("dashboard")
  // Reverse phone lookup (inbound-call "who is this number?")
  const [lookupQuery,setLookupQuery]   = useState("")
  const [lookupResult,setLookupResult] = useState(null)
  const [lookupLoading,setLookupLoad]  = useState(false)
  const [callHistory,setCallHistory] = useState([])
  const [histData,setHistData]       = useState(null)
  const [histLoading,setHistLoading] = useState(false)
  const [histCaller,setHistCaller]   = useState("")
  const [histFrom,setHistFrom]       = useState("")
  const [histTo,setHistTo]           = useState("")
  const [histRange,setHistRange]     = useState("week")
  const [dialerIdx,setDialerIdx]   = useState(0)
  // Dialer scope: optionally restrict the queue to one state and/or to
  // unanswered (no_answer) leads — lets a caller "go back and call the CT
  // leads that never picked up" without leaving the dialer.
  const [dialerState,setDialerState] = useState("")
  const [dialerCity,setDialerCity] = useState("")
  const [dialerUnanswered,setDialerUnanswered] = useState(false)
  useEffect(()=>{ setDialerIdx(0) },[dialerState,dialerCity,dialerUnanswered])
  // Snooze: hide leads dialed within the last N hours where the outcome was
  // a non-engagement (no_answer / voicemail / called). Default 4h; caller
  // can override per-session via the dialer toolbar.
  const [snoozeHours,setSnoozeHours] = useState(DIALER_SNOOZE_HOURS_DEFAULT)
  const [showNotifs,setShowNotifs]  = useState(false)
  const [quota,setQuota]            = useState({quota:60,my_calls_today:0})
  const [leaderboard,setLeaderboard] = useState([])
  const [lbLoading,setLbLoading]   = useState(false)
  const [lbRange,setLbRange]       = useState("today")
  const [qualifiedCalls,setQualifiedCalls] = useState([])
  const [qualLoading,setQualLoading] = useState(false)
  const [expandedCaller,setExpandedCaller] = useState(null)
  const [callerDetail,setCallerDetail] = useState(null)
  const [callerDetailLoading,setCallerDetailLoading] = useState(false)
  const [callerDetailDate,setCallerDetailDate] = useState("")
  const [callerDetailDateTo,setCallerDetailDateTo] = useState("")
  const [callerSearch,setCallerSearch] = useState("")
  const [callerFilter,setCallerFilter] = useState("")
  const [warmLeads,setWarmLeads] = useState([])
  const [warmLoading,setWarmLoading] = useState(false)
  const [emailModal,setEmailModal] = useState(null)
  // Industry → top script lookup. Populated lazily when the dialer opens.
  // Cached per industry so we don't hammer /api/scripts on every navigation.
  const [scriptCache,setScriptCache] = useState({})  // {industry: script}
  // Best-call-time hint per industry. Computed server-side from the last
  // 60 days of call_outcomes — picks the hour-of-day with the highest
  // contact rate (≥5 calls of signal). Cached per industry.
  const [bestTimeCache,setBestTimeCache] = useState({})  // {industry: {best_hour, ...}}

  // Soft "where to call now" nudge. Server returns a state currently in its
  // peak-pickup window, or null. Refetched every 15 min. Dismissed banners
  // remember their dismissal for the rest of the calendar day per state.
  const [bestRegion,setBestRegion]       = useState(null)
  const [bestRegionDismissed,setBestRegionDismissed] = useState(()=>{
    try { return JSON.parse(localStorage.getItem("lf_region_dismissed")||"{}") }
    catch(_) { return {} }
  })
  const [switchingRegion,setSwitchingRegion] = useState(false)

  useEffect(()=>{
    if(!user) return
    let live = true
    const load = () => api("/api/guidance/best-region")
      .then(r => { if(live) setBestRegion(r && r.state ? r : null) })
      .catch(()=>{ if(live) setBestRegion(null) })
    load()
    const t = setInterval(load, 15*60*1000)
    return () => { live=false; clearInterval(t) }
  },[user])

  function dismissBestRegion(state){
    const today = new Date().toISOString().slice(0,10)
    const next = {...bestRegionDismissed, [state]: today}
    setBestRegionDismissed(next)
    try { localStorage.setItem("lf_region_dismissed", JSON.stringify(next)) }
    catch(_){}
  }

  async function switchToRegion(state){
    setSwitchingRegion(true)
    try {
      const r = await api("/api/guidance/switch",
        {method:"POST", body: JSON.stringify({state})})
      setFState(state)
      setFCity("")
      setNav("leads")
      notify(r?.message || `Switched to ${state}.`)
      // Refresh the lead list so the new Apollo pulls (if any) show up.
      if(r?.apollo_new_leads > 0) loadLeads()
    } catch(e) {
      notify(`Switch failed: ${e.message||e}`, "error")
    } finally {
      setSwitchingRegion(false)
    }
  }

  // Auto-load warm leads when switching to warm tab
  useEffect(()=>{
    if(activeNav==="warm"&&user&&warmLeads.length===0){
      setWarmLoading(true)
      api("/api/leads?source=VCC+Outreach").then(r=>{
        setWarmLeads(Array.isArray(r)?r:[])
      }).catch(()=>{}).finally(()=>setWarmLoading(false))
    }
  },[activeNav])

  // Lazy-load the top script per industry so the dialer card can show the
  // matching opener inline. We fetch only the industries the caller actually
  // sees on the active dialer lead, cache forever per session.
  function ensureScript(industry){
    if(!industry || scriptCache[industry] !== undefined) return
    api(`/api/scripts?industry=${encodeURIComponent(industry)}`)
      .then(rows => {
        const top = Array.isArray(rows) && rows.length ? rows[0] : null
        setScriptCache(c => ({...c, [industry]: top}))
      })
      .catch(()=>{
        setScriptCache(c => ({...c, [industry]: null}))
      })
  }

  function ensureBestTime(industry){
    if(!industry || bestTimeCache[industry] !== undefined) return
    api(`/api/insights/best-call-time?industry=${encodeURIComponent(industry)}&days=60`)
      .then(r => setBestTimeCache(c => ({...c, [industry]: r})))
      .catch(()=>setBestTimeCache(c => ({...c, [industry]: null})))
  }

  function notify(msg,type="success"){ setToast({msg,type}); setTimeout(()=>setToast(null),3200) }

  async function doLogout(){
    if(!window.confirm("Sign out?")) return
    const sessId=localStorage.getItem("lf_session_id")
    if(sessId){
      try{await api("/api/auth/logout",{method:"POST",body:JSON.stringify({session_id:sessId})})}catch(e){}
    }
    localStorage.clear(); setUser(null)
  }

  useEffect(()=>{
    if(user){
      api("/api/industries").then(r=>setIndustries(r.industries||[])).catch(()=>{})
      api("/api/quota").then(r=>setQuota(r)).catch(()=>{})
    }
  },[user])

  // Record sign-out on tab/browser close so sessions don't stay open forever
  useEffect(()=>{
    if(!user) return
    const handleUnload = () => {
      const sessId = localStorage.getItem("lf_session_id")
      const token = localStorage.getItem("lf_token")
      if(sessId && token){
        navigator.sendBeacon(`${API_BASE}/api/auth/logout-beacon`,
          JSON.stringify({session_id: sessId, token}))
      }
    }
    window.addEventListener("beforeunload", handleUnload)
    return () => window.removeEventListener("beforeunload", handleUnload)
  },[user])

  const loadLeads = useCallback(async()=>{
    setLoad(true)
    try{
      const params = new URLSearchParams()
      if(fStatus!=="all") params.set("status", fStatus)
      if(search) params.set("search", search)
      if(cbOnly) params.set("callbacks", "true")
      params.set("sort", sortBy)
      const leadsData = await api(`/api/leads?${params}`)
      setLeads(Array.isArray(leadsData)?leadsData:[])
      try{ const s=await api("/api/stats"); if(s) setStats(s) }catch{}
      try{ const q=await api("/api/quota"); if(q) setQuota(q) }catch{}
      // Persistent email-history map for the 📧 badges.
      // 90-day window covers most workflows; older history is fine to forget.
      try{
        const f=await api("/api/leads/emailed-flags?days=90")
        if(f?.flags) setEmailedFlags(f.flags)
      }catch{}
    }catch(ex){ notify("Error loading leads","error") }
    finally{ setLoad(false) }
  },[search,fStatus,sortBy,cbOnly])

  useEffect(()=>{
    if(!user) return
    const t=setTimeout(loadLeads,search?350:0)
    return()=>clearTimeout(t)
  },[user,loadLeads])

  useEffect(()=>{
    if(activeNav!=="analytics"||!user) return
    setLbLoading(true)
    let cancelled = false
    const fetchLb = ()=>{
      api(`/api/leaderboard?range=${lbRange}`)
        .then(r=>{ if(!cancelled) setLeaderboard(Array.isArray(r)?r:[]) })
        .catch(()=>{})
        .finally(()=>{ if(!cancelled) setLbLoading(false) })
    }
    fetchLb()
    // Live refresh on the analytics tab: poll every 30s while the tab is
    // focused, pause when the browser tab is hidden so we don't burn API
    // calls. Admin sees Kathrine's call count tick up in near-real-time
    // without a manual refresh. Stops on unmount / tab switch via cancelled.
    const POLL_MS = 30_000
    let timer = null
    const tick = ()=>{
      if(cancelled) return
      if(document.visibilityState === "visible") fetchLb()
      timer = setTimeout(tick, POLL_MS)
    }
    timer = setTimeout(tick, POLL_MS)
    const onVis = ()=>{ if(document.visibilityState === "visible") fetchLb() }
    document.addEventListener("visibilitychange", onVis)
    return ()=>{
      cancelled = true
      if(timer) clearTimeout(timer)
      document.removeEventListener("visibilitychange", onVis)
    }
  },[activeNav,user,lbRange])

  useEffect(()=>{
    if(activeNav!=="qualified"||!user) return
    setQualLoading(true)
    api("/api/calls/qualified").then(r=>setQualifiedCalls(Array.isArray(r)?r:[])).catch(()=>{}).finally(()=>setQualLoading(false))
  },[activeNav,user])

  const loadHistory = useCallback(async(range,caller)=>{
    setHistLoading(true)
    const params = new URLSearchParams()
    const now = new Date()
    let from = ""
    if(range==="today"){ from = now.toISOString().split("T")[0] }
    else if(range==="week"){ const d=new Date(now); d.setDate(d.getDate()-7); from=d.toISOString().split("T")[0] }
    else if(range==="month"){ const d=new Date(now); d.setMonth(d.getMonth()-1); from=d.toISOString().split("T")[0] }
    else if(range==="custom"){ if(histFrom) params.set("date_from",histFrom); if(histTo) params.set("date_to",histTo) }
    if(range!=="custom"&&from) params.set("date_from",from)
    if(caller) params.set("caller",caller)
    try{
      const r=await api(`/api/calls/history?${params}`)
      setHistData(r)
      setCallHistory(r.calls||[])
    }catch(e){ setHistData(null); setCallHistory([]) }
    setHistLoading(false)
  },[histFrom,histTo])

  useEffect(()=>{
    if(activeNav!=="history"||!user) return
    loadHistory(histRange,histCaller)
  },[activeNav,user])

  async function quickStatus(lead,status){
    // 4×-contact rule: don't let a lead that's never been reached get dumped to
    // "Not Interested" before 4 attempts. Scoped to never-answered leads only
    // (current status no_answer) — a lead where a human actually declined is a
    // legitimate Not Interested regardless of attempt count. Warn + allow override.
    if(status==="not_interested" && lead.status==="no_answer" && (lead.total_calls||0)<4){
      const n = lead.total_calls||0
      if(!window.confirm(`${lead.company||lead.firstName||"This lead"} hasn't answered yet — only ${n} of 4 attempts.\n\nTeam rule: try unanswered leads at least 4× before marking Not Interested.\n\nMark Not Interested anyway?`)) return
    }
    let cbDate=""
    if(status==="callback") cbDate=window.prompt("Callback date (YYYY-MM-DD):",new Date().toISOString().split("T")[0])||""
    try{
      await api(`/api/leads/${lead.id}`,{method:"PATCH",body:JSON.stringify({status,callbackDate:cbDate,updatedAt:new Date().toISOString()})})
      setLeads(p=>p.map(l=>l.id===lead.id?{...l,status,callbackDate:cbDate}:l))
      setTimeout(loadLeads,500)
    }catch(ex){ notify("Error updating","error") }
  }

  async function deleteL(id){
    if(!window.confirm("Delete this lead?")) return
    try{
      await api(`/api/leads/${id}`,{method:"DELETE"})
      setLeads(p=>p.filter(l=>l.id!==id)); notify("Deleted","error")
      loadLeads()
    }catch(ex){ notify("Error","error") }
  }

  async function doLookup(){
    const q=(lookupQuery||"").trim()
    const digits=q.replace(/\D/g,"")
    if(digits.length<7){ notify("Enter at least 7 digits of the phone number","error"); return }
    setLookupLoad(true); setLookupResult(null)
    try{
      const r=await api(`/api/leads/lookup?phone=${encodeURIComponent(q)}`)
      setLookupResult(r)
    }catch(ex){ notify("Lookup failed","error"); setLookupResult({count:0,matches:[]}) }
    finally{ setLookupLoad(false) }
  }

  if(!user) return <Login onLogin={u=>setUser(u)}/>

  const si=v=>STATUS_OPTIONS.find(s=>s.value===v)||STATUS_OPTIONS[0]
  const localDate=d=>{const y=d.getFullYear(),m=String(d.getMonth()+1).padStart(2,"0"),day=String(d.getDate()).padStart(2,"0");return`${y}-${m}-${day}`}
  const today=localDate(new Date())
  const tomorrow=(()=>{const d=new Date();d.setDate(d.getDate()+1);return localDate(d)})()
  const threeDays=(()=>{const d=new Date();d.setDate(d.getDate()+3);return localDate(d)})()

  // Notification items
  const notifItems=leads.filter(l=>l.callbackDate&&l.status!=="converted").map(l=>{
    const d=l.callbackDate
    if(d<today) return{...l,urgency:"overdue",label:"Overdue",color:"#ff6e84"}
    if(d===today) return{...l,urgency:"today",label:"Due today",color:"#ffe083"}
    if(d===tomorrow) return{...l,urgency:"tomorrow",label:"Tomorrow",color:"#a3a6ff"}
    if(d<=threeDays) return{...l,urgency:"soon",label:`Due ${d}`,color:"#8b5cf6"}
    return null
  }).filter(Boolean).sort((a,b)=>{
    const order={overdue:0,today:1,tomorrow:2,soon:3}
    return(order[a.urgency]||4)-(order[b.urgency]||4)
  })

  // Browser notification on load if overdue
  useEffect(()=>{
    if(!leads.length) return
    const overdue=leads.filter(l=>l.callbackDate&&l.callbackDate<today&&l.status!=="converted")
    if(overdue.length>0&&"Notification" in window){
      if(Notification.permission==="granted"){
        new Notification(`LeadFlow: ${overdue.length} overdue follow-up${overdue.length>1?"s":""}`,{
          body:overdue.slice(0,3).map(l=>l.company||l.firstName).join(", "),icon:"/favicon.ico"})
      }else if(Notification.permission!=="denied"){
        Notification.requestPermission()
      }
    }
  },[leads.length, today])

  const displayLeads=leads.filter(l=>{
    if(fIndustry&&l.industry!==fIndustry) return false
    if(fState&&l.state!==fState) return false
    if(fCity){
      const cityLower = l.city?.toLowerCase()||""
      const filterLower = fCity.toLowerCase().trim()
      if(!cityLower.startsWith(filterLower) && cityLower !== filterLower) return false
    }
    if(availableOnly&&l.assignedTo&&l.assignedTo!==user) return false
    if(emailedOnly&&!emailedFlags[l.id]) return false
    if(needsAttemptOnly&&!(l.status==="no_answer"&&(l.total_calls||0)<4)) return false
    return true
  })
  const newTodayLeads = displayLeads.filter(l=>(l.createdAt||"").startsWith(today))
  const olderLeads = displayLeads.filter(l=>!(l.createdAt||"").startsWith(today))

  // Source-based segmentation for the Leads tab — keeps Apollo-pulled
  // decision-maker contacts visually separate from Google Places / manual
  // / VCC warm leads so callers can see at a glance which list they're in.
  // Apollo-direct pulls have source="Apollo"; everything else (Google
  // Places + auto-enriched Google Places, manual imports, VCC warm) goes
  // under Warm Leads. The banner counts use the FILTERED set so they
  // reflect what the user is currently looking at.
  const isApolloLead = (l) => (l.source||"").toLowerCase() === "apollo"
  // Within each segment, surface "new today" first (preserves the prior
  // newest-first behavior — just nested inside each section).
  // Leads are grouped by state below; within each state the Apollo / Warm
  // split is applied with "new today" surfaced first inside each sub-section.
  const sortNewFirst = (arr) => {
    const newToday = arr.filter(l=>(l.createdAt||"").startsWith(today))
    const older    = arr.filter(l=>!(l.createdAt||"").startsWith(today))
    return [...newToday, ...older]
  }

  function reset(){setSearch("");setFIndustry("");setFState("");setFCity("");setFStatus("all");setCbOnly(false);setAvailOnly(false);setEmailedOnly(false);setNeedsAttemptOnly(false)}

  return(
    <div style={{minHeight:"100vh",background:"#060e20"}}>
      <style>{CSS}</style>

      {/* ── Top Nav ─────────────────────────────────────────────────────── */}
      <header style={{position:"fixed",top:0,left:0,right:0,height:64,background:"#060e20",
        borderBottom:"1px solid #40485d25",zIndex:50,display:"flex",alignItems:"center",
        padding:"0 24px",gap:28}}>

        <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:22,fontWeight:700,
          color:"#a3a6ff",letterSpacing:"-.01em",flexShrink:0}}>
          LeadFlow
        </div>

        <nav className="lg-topnav-tabs" style={{display:"flex",gap:24,alignItems:"center"}}>
          {["Dashboard","Leads","Analytics"].map(t=>{
            const key=t.toLowerCase()
            const isActive=activeNav===key
            return(
              <a key={t} href="#" onClick={e=>{e.preventDefault();setNav(key)}}
                style={{fontSize:14,fontWeight:isActive?700:500,
                  color:isActive?"#a3a6ff":"#a3aac4",
                  borderBottom:isActive?"2px solid #a3a6ff":"2px solid transparent",
                  paddingBottom:4,textDecoration:"none",transition:"color .2s",letterSpacing:"-.01em"}}>
                {t}
              </a>
            )
          })}
        </nav>

        <div style={{flex:1}}/>

        {/* Search */}
        <div style={{position:"relative",display:"flex",alignItems:"center"}}>
          <span style={{position:"absolute",left:12,color:"#40485d",display:"flex"}}>
            <IconSearch/>
          </span>
          <input value={search} onChange={e=>setSearch(e.target.value)}
            placeholder="Search name, phone, company, email…"
            title="Search by contact name, phone number, company, email, city, notes — even past call history"
            style={{background:"#000011",border:"none",borderRadius:12,
              padding:"8px 16px 8px 36px",color:"#dee5ff",fontSize:13,
              fontFamily:"'Inter',sans-serif",outline:"none",width:260}}/>
        </div>

        {/* Callbacks badge */}
        {stats?.callbacksDue>0&&(
          <button className="btn btn-amber" onClick={()=>setCbOnly(p=>!p)}>
            🔔 {stats.callbacksDue}
          </button>
        )}

        <div style={{position:"relative"}}>
          <button onClick={()=>setShowNotifs(p=>!p)} title="Notifications"
            style={{padding:8,background:showNotifs?"#192540":"transparent",border:"none",
              color:notifItems.length>0?"#ffe083":"#a3aac4",cursor:"pointer",borderRadius:8,
              display:"flex",alignItems:"center",justifyContent:"center",transition:"all .15s",position:"relative"}}>
            <IconBell/>
            {notifItems.length>0&&(
              <span style={{position:"absolute",top:2,right:2,width:18,height:18,borderRadius:"50%",
                background:"#ff6e84",color:"#fff",fontSize:10,fontWeight:700,
                display:"flex",alignItems:"center",justifyContent:"center",
                fontFamily:"'Space Grotesk',sans-serif"}}>{notifItems.length>9?"9+":notifItems.length}</span>
            )}
          </button>
          {showNotifs&&(
            <div style={{position:"absolute",top:"100%",right:0,marginTop:8,width:360,maxHeight:420,
              overflowY:"auto",background:"#0f1930",border:"1px solid #40485d40",borderRadius:12,
              boxShadow:"0 16px 48px rgba(0,0,0,.5)",zIndex:100}}>
              <div style={{padding:"14px 16px",borderBottom:"1px solid #40485d20",
                display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                <span style={{fontSize:12,fontWeight:700,color:"#dee5ff",letterSpacing:".05em",
                  textTransform:"uppercase"}}>Notifications</span>
                <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:13,fontWeight:700,
                  color:"#a3a6ff"}}>{notifItems.length}</span>
              </div>
              {notifItems.length===0?(
                <div style={{padding:32,textAlign:"center",color:"#40485d",fontSize:13}}>
                  No upcoming follow-ups
                </div>
              ):(
                notifItems.slice(0,20).map((item,i)=>(
                  <div key={item.id||i}
                    style={{padding:"12px 16px",borderBottom:"1px solid #40485d10",
                      display:"flex",alignItems:"center",gap:12,cursor:"pointer",
                      transition:"background .12s"}}
                    onMouseEnter={e=>e.currentTarget.style.background="#192540"}
                    onMouseLeave={e=>e.currentTarget.style.background="transparent"}
                    onClick={()=>{setCallModal(item);setShowNotifs(false)}}>
                    <div style={{width:8,height:8,borderRadius:"50%",background:item.color,flexShrink:0}}/>
                    <div style={{flex:1,minWidth:0}}>
                      <div style={{fontSize:13,fontWeight:600,color:"#dee5ff",
                        overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                        {[item.firstName,item.lastName].filter(Boolean).join(" ")||item.company}
                      </div>
                      <div style={{fontSize:11,color:"#a3aac4"}}>{item.company}</div>
                    </div>
                    <div style={{textAlign:"right",flexShrink:0}}>
                      <span style={{fontSize:11,fontWeight:700,color:item.color}}>{item.label}</span>
                      <div style={{fontSize:10,color:"#40485d",fontFamily:"'Space Grotesk',sans-serif"}}>{item.callbackDate}</div>
                    </div>
                  </div>
                ))
              )}
              {notifItems.length>0&&(
                <div style={{padding:"10px 16px",borderTop:"1px solid #40485d20"}}>
                  <button className="btn btn-p" style={{width:"100%",fontSize:12,padding:"8px"}}
                    onClick={()=>{setNav("leads");setCbOnly(true);setShowNotifs(false)}}>
                    View All Follow-Ups
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
        <IconBtn onClick={()=>setShowScripts(true)} title="Settings / Scripts"><IconSettings/></IconBtn>

        {/* User avatar */}
        <div
          onClick={doLogout}
          title={`${user} — click to sign out`}
          style={{width:34,height:34,borderRadius:"50%",background:"#a3a6ff25",
            border:"1px solid #40485d60",display:"flex",alignItems:"center",
            justifyContent:"center",fontSize:13,fontWeight:700,color:"#a3a6ff",
            cursor:"pointer",flexShrink:0,fontFamily:"'Space Grotesk',sans-serif"}}>
          {(user||"?")[0].toUpperCase()}
        </div>
      </header>

      {/* ── Sidebar ─────────────────────────────────────────────────────── */}
      <aside className="lg-sidebar" style={{position:"fixed",left:0,top:64,bottom:0,width:256,
        background:"#060e20",borderRight:"1px solid #40485d25",zIndex:40,
        display:"flex",flexDirection:"column",overflowY:"auto"}}>

        <nav style={{flex:1,padding:"8px 0",display:"flex",flexDirection:"column",gap:2}}>
          {SIDEBAR_NAV.map(({key,label,Icon})=>{
            const active=activeNav===key
            return(
              <a key={key} href="#" onClick={e=>{e.preventDefault();setNav(key)}}
                style={{display:"flex",alignItems:"center",gap:12,padding:"11px 16px",
                  fontSize:14,color:active?"#a3a6ff":"#a3aac4",
                  background:active?"#a3a6ff1a":"transparent",
                  borderRight:`3px solid ${active?"#a3a6ff":"transparent"}`,
                  textDecoration:"none",transition:"all .18s"}}>
                <Icon/>{label}
              </a>
            )
          })}
        </nav>

        {/* Start Dialing CTA */}
        <div style={{padding:"0 16px 20px"}}>
          <button className="btn btn-p"
            style={{width:"100%",padding:"13px",fontSize:14,
              fontFamily:"'Space Grotesk',sans-serif",fontWeight:700,
              boxShadow:"0 8px 32px rgba(163,166,255,.18)"}}
            onClick={()=>leads.length>0?setCallModal(leads[0]):setEditModal(true)}>
            Start Dialing
          </button>
        </div>

        {/* Bottom links */}
        <div style={{borderTop:"1px solid #40485d25",padding:"8px 0"}}>
          {[
            {label:"Import CSV", Icon:IconUpload, action:()=>setImport(true)},
            {label:"Account",    Icon:IconPerson, action:doLogout},
          ].map(({label,Icon,action})=>(
            <a key={label} href="#" onClick={e=>{e.preventDefault();action()}}
              style={{display:"flex",alignItems:"center",gap:12,padding:"9px 16px",
                fontSize:14,color:"#a3aac4",textDecoration:"none",transition:"background .15s"}}
              onMouseEnter={e=>e.currentTarget.style.background="#192540"}
              onMouseLeave={e=>e.currentTarget.style.background="transparent"}>
              <Icon/>{label}
            </a>
          ))}
        </div>
      </aside>

      {/* ── Main Content ─────────────────────────────────────────────────── */}
      <main className="lg-main" style={{marginLeft:256,paddingTop:64,minHeight:"100vh"}}>
        <div style={{padding:"32px 28px",maxWidth:1240}}>

          {/* ── DASHBOARD ───────────────────────────────────────────────── */}
          {activeNav==="dashboard"&&(
            <div>
              <div style={{marginBottom:32}}>
                <h1 style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:36,fontWeight:700,
                  color:"#dee5ff",letterSpacing:"-.02em",lineHeight:1.1}}>
                  Welcome back, {(user||"").split("@")[0]}
                </h1>
                <p style={{color:"#a3aac4",fontSize:14,marginTop:4}}>Here's what's happening today</p>
              </div>
              <BestRegionBanner region={bestRegion} dismissed={bestRegionDismissed}
                onSwitch={switchToRegion} onDismiss={dismissBestRegion}
                switching={switchingRegion}/>
              <StatsBar stats={stats} onCallbacks={()=>{setNav("leads");setCbOnly(p=>!p)}}/>

              {/* Daily Call Quota */}
              {(()=>{
                const target=quota.quota||60
                const done=quota.my_calls_today||0
                const pct=Math.min(Math.round(done/target*100),100)
                return(
                  <div style={{background:"#0f1930",borderRadius:12,padding:"16px 20px",marginTop:16}}>
                    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:8}}>
                      <span style={{fontSize:"0.6rem",color:"#a3aac4",fontWeight:700,letterSpacing:".1em",
                        textTransform:"uppercase"}}>Your Daily Quota</span>
                      <div style={{display:"flex",alignItems:"center",gap:10}}>
                        <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:14,fontWeight:700,
                          color:pct>=100?"#69f6b8":pct>=50?"#ffe083":"#a3a6ff"}}>{done} / {target}</span>
                        {isAdmin()&&(
                          <button className="btn btn-g" style={{fontSize:10,padding:"3px 8px"}}
                            onClick={async()=>{
                              const v=window.prompt("Set default daily quota for all callers:",target)
                              if(!v) return
                              const n=parseInt(v)
                              if(isNaN(n)||n<1||n>500){alert("Must be 1-500");return}
                              try{
                                await api("/api/quota",{method:"PUT",body:JSON.stringify({quota:n})})
                                setQuota(q=>({...q,quota:n}))
                                notify(`Team quota set to ${n} calls/day`)
                              }catch(e){notify("Error: "+e.message,"error")}
                            }}>Set Default</button>
                        )}
                      </div>
                    </div>
                    <div style={{height:8,background:"#141f38",borderRadius:4,overflow:"hidden"}}>
                      <div style={{height:"100%",width:`${pct}%`,borderRadius:4,transition:"width .3s",
                        background:pct>=100?"#69f6b8":pct>=50?"#ffe083":"#a3a6ff"}}/>
                    </div>
                    {pct>=100&&<div style={{fontSize:11,color:"#69f6b8",marginTop:6,fontWeight:600}}>Quota hit! Keep going.</div>}
                    {pct<50&&done>0&&<div style={{fontSize:11,color:"#a3aac4",marginTop:6}}>{target-done} calls to go</div>}
                  </div>
                )
              })()}

              {/* Email Campaigns summary — clickable, jumps to full campaigns tab */}
              <CampaignSummaryCard onClick={()=>setNav("campaigns")}/>

              {/* Overdue callbacks */}
              {leads.filter(l=>l.callbackDate&&l.callbackDate<today&&l.status!=="converted").length>0&&(
                <div style={{background:"#2d0a0a",border:"1px solid #92400e",borderRadius:12,padding:"14px 20px",
                  marginTop:16,display:"flex",alignItems:"center",justifyContent:"space-between"}}>
                  <div>
                    <span style={{color:"#fca5a5",fontWeight:700,fontSize:13}}>
                      {leads.filter(l=>l.callbackDate&&l.callbackDate<today&&l.status!=="converted").length} overdue callback{leads.filter(l=>l.callbackDate&&l.callbackDate<today&&l.status!=="converted").length!==1?"s":""}
                    </span>
                    <span style={{color:"#a3aac4",fontSize:12,marginLeft:8}}>— these leads expected a call back</span>
                  </div>
                  <button className="btn btn-p" style={{fontSize:12,padding:"7px 14px"}}
                    onClick={()=>{setNav("leads");setCbOnly(true)}}>View</button>
                </div>
              )}

              {leads.filter(l=>l.callbackDate&&l.callbackDate<=today&&l.status!=="converted").length>0&&(
                <div style={{marginTop:32}}>
                  <div style={{fontSize:"0.6rem",color:"#ffe083",fontWeight:700,letterSpacing:".1em",
                    textTransform:"uppercase",marginBottom:14}}>🔔 Callbacks Due</div>
                  <div style={{display:"flex",flexDirection:"column",gap:8}}>
                    {leads.filter(l=>l.callbackDate&&l.callbackDate<=today&&l.status!=="converted").slice(0,5).map(lead=>{
                      const ac=avatarColor(lead.company||lead.firstName||"?")
                      return(
                        <div key={lead.id} style={{background:"#1a1030",borderRadius:10,padding:"14px 18px",
                          display:"flex",alignItems:"center",gap:14,border:"1px solid #8b5cf620"}}>
                          <div style={{width:36,height:36,borderRadius:"50%",background:ac+"22",flexShrink:0,
                            display:"flex",alignItems:"center",justifyContent:"center",
                            fontSize:12,fontWeight:700,color:ac,fontFamily:"'Space Grotesk',sans-serif"}}>
                            {getInitials(lead)}</div>
                          <div style={{flex:1,minWidth:0}}>
                            <div style={{fontWeight:600,color:"#dee5ff",fontSize:14}}>
                              {[lead.firstName,lead.lastName].filter(Boolean).join(" ")||lead.company}</div>
                            <div style={{fontSize:12,color:"#a3aac4"}}>{lead.company}</div>
                          </div>
                          <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:12,color:"#ffe083"}}>{lead.callbackDate}</div>
                          <button className="btn btn-p" style={{fontSize:12,padding:"7px 14px"}}
                            onClick={()=>setCallModal(lead)}>Call Now</button>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}

              <div style={{marginTop:32}}>
                <div style={{fontSize:"0.6rem",color:"#ff6e84",fontWeight:700,letterSpacing:".1em",
                  textTransform:"uppercase",marginBottom:14}}>🔥 Hot Leads</div>
                {leads.filter(l=>{const s=l.score||scoreLead(l)||0;return s>=75&&l.status!=="converted"}).length===0?(
                  <div style={{background:"#0f1930",borderRadius:10,padding:24,textAlign:"center",color:"#40485d",fontSize:13}}>
                    No hot leads yet — keep prospecting!</div>
                ):(
                  <div style={{display:"flex",flexDirection:"column",gap:8}}>
                    {leads.filter(l=>{const s=l.score||scoreLead(l)||0;return s>=75&&l.status!=="converted"})
                      .sort((a,b)=>(b.score||scoreLead(b))-(a.score||scoreLead(a))).slice(0,6).map(lead=>{
                      const score=lead.score||scoreLead(lead)||0
                      const ac=avatarColor(lead.company||lead.firstName||"?")
                      const info=STATUS_OPTIONS.find(s=>s.value===lead.status)||STATUS_OPTIONS[0]
                      return(
                        <div key={lead.id} style={{background:"#0f1930",borderRadius:10,padding:"14px 18px",
                          display:"flex",alignItems:"center",gap:14}}>
                          <div style={{width:36,height:36,borderRadius:"50%",background:ac+"22",flexShrink:0,
                            display:"flex",alignItems:"center",justifyContent:"center",
                            fontSize:12,fontWeight:700,color:ac,fontFamily:"'Space Grotesk',sans-serif"}}>
                            {getInitials(lead)}</div>
                          <div style={{flex:1,minWidth:0}}>
                            <div style={{fontWeight:600,color:"#dee5ff",fontSize:14}}>
                              {[lead.firstName,lead.lastName].filter(Boolean).join(" ")||lead.company}</div>
                            <div style={{fontSize:12,color:"#a3aac4"}}>{lead.company}</div>
                          </div>
                          <ScoreRing score={score}/>
                          <span className="pill" style={{background:info.color+"20",color:info.color,border:`1px solid ${info.color}30`}}>{info.label}</span>
                          <button className="btn btn-p" style={{fontSize:12,padding:"7px 14px"}}
                            onClick={()=>setCallModal(lead)}>Call</button>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>

              {industries.length>0&&(
                <div style={{marginTop:32}}><LeadFinder onFound={loadLeads} industries={industries}/></div>
              )}
              <div style={{marginTop:24}}><FreeSourceFinder onFound={loadLeads}/></div>
              <div style={{marginTop:24}}><ApolloFinder onFound={loadLeads} industries={industries}/></div>
            </div>
          )}

          {/* ── LEADS ───────────────────────────────────────────────────────── */}
          {activeNav==="leads"&&(
            <div>
              <BestRegionBanner region={bestRegion} dismissed={bestRegionDismissed}
                onSwitch={switchToRegion} onDismiss={dismissBestRegion}
                switching={switchingRegion}/>
              <div style={{marginBottom:28}}>
                <h1 style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:36,fontWeight:700,
                  color:"#dee5ff",letterSpacing:"-.02em",lineHeight:1.1}}>Leads</h1>
                <p style={{color:"#a3aac4",fontSize:14,marginTop:4}}>
                  {leads.length} prospect{leads.length!==1?"s":""} in your pipeline
                  {(()=>{
                    const dmCount   = leads.filter(l=>(l.source||"").toLowerCase()==="apollo").length
                    const warmCount = leads.length - dmCount
                    if(leads.length===0) return null
                    return (
                      <span style={{marginLeft:10,fontSize:12,color:"#40485d"}}>
                        · <span style={{color:"#8b5cf6"}}>{dmCount}</span> decision maker{dmCount!==1?"s":""}
                        · <span style={{color:"#ffe083"}}>{warmCount}</span> warm
                      </span>
                    )
                  })()}
                </p>
              </div>

              <section style={{background:"#141f38",borderRadius:16,padding:"16px 20px",
                display:"flex",flexWrap:"wrap",alignItems:"center",gap:12,marginBottom:20}}>
                <div style={{flex:"1 1 200px",position:"relative",display:"flex",alignItems:"center"}}>
                  <span style={{position:"absolute",left:12,color:"#40485d",display:"flex"}}><IconFilter/></span>
                  <input value={search} onChange={e=>setSearch(e.target.value)}
                    placeholder="Search by name, phone, company, email, or notes…"
                    title="Look up a lead by contact name, phone number, company, email, city, notes — even past call history. Forgot the name? Paste the phone number."
                    style={{width:"100%",background:"#000011",border:"1px solid #40485d30",
                      borderRadius:8,padding:"8px 12px 8px 32px",color:"#dee5ff",
                      fontSize:13,fontFamily:"'Inter',sans-serif",outline:"none"}}/>
                </div>
                <div style={{display:"flex",gap:10,alignItems:"center",flexWrap:"wrap"}}>
                  <select className="sel" value={fIndustry} onChange={e=>setFIndustry(e.target.value)}>
                    <option value="">Industry: All</option>
                    {(industries.length>0?industries:INDUSTRIES).map(i=><option key={i} value={i}>{i}</option>)}
                  </select>
                  <select className="sel" value={fState} onChange={e=>{setFState(e.target.value);if(!e.target.value)setFCity("")}}>
                    <option value="">State: All States</option>
                    {STATES.filter(s=>s).map(s=><option key={s} value={s}>{s}</option>)}
                  </select>
                  <CityAutocomplete value={fCity} onChange={setFCity} state={fState}
                    disabled={!fState}
                    placeholder={fState?"City...":"Select state first"}
                    style={{wrapper:{width:140},input:{padding:"6px 10px"}}}/>
                  <select className="sel" value={fStatus} onChange={e=>setFStatus(e.target.value)}>
                    <option value="all">Status: All</option>
                    {STATUS_OPTIONS.map(s=><option key={s.value} value={s.value}>{s.label}</option>)}
                  </select>
                  <select className="sel" value={sortBy} onChange={e=>setSort(e.target.value)}>
                    <option value="smart">Smart (fresh + rested first)</option>
                    <option value="score">Highest Score</option>
                    <option value="newest">Newest First</option>
                    <option value="oldest_contact">Longest Since Last Call</option>
                    <option value="company">Company A–Z</option>
                    <option value="callbacks">Callbacks Due</option>
                  </select>
                  <button className="btn btn-g" style={{fontSize:12,padding:"8px 16px"}} onClick={reset}>Reset</button>
                  <button
                    onClick={()=>setAvailOnly(p=>!p)}
                    style={{fontSize:12,padding:"8px 14px",borderRadius:8,border:"none",cursor:"pointer",
                      fontFamily:"'Inter',sans-serif",fontWeight:600,transition:"all .15s",
                      background:availableOnly?"#69f6b8":"#192540",
                      color:availableOnly?"#003d26":"#a3aac4"}}>
                    {availableOnly?"✓ Available only":"Available only"}
                  </button>
                  {(()=>{
                    const emailedCount = leads.filter(l=>l.status==="awaiting_email_reply").length
                    const active = fStatus==="awaiting_email_reply"
                    return (
                      <button
                        onClick={()=>setFStatus(active?"all":"awaiting_email_reply")}
                        title="Show leads currently in the email follow-up flow (won't appear in dialer)"
                        style={{fontSize:12,padding:"8px 14px",borderRadius:8,border:"none",cursor:"pointer",
                          fontFamily:"'Inter',sans-serif",fontWeight:600,transition:"all .15s",
                          background:active?"#a3a6ff":"#192540",
                          color:active?"#000011":"#a3aac4"}}>
                        📧 Awaiting Reply{emailedCount>0?` (${emailedCount})`:""}
                      </button>
                    )
                  })()}
                  {(()=>{
                    const everEmailedCount = leads.filter(l=>emailedFlags[l.id]).length
                    return (
                      <button
                        onClick={()=>setEmailedOnly(p=>!p)}
                        title="Show only leads that have ever been sent a campaign email (any send in last 90 days). Persists across status changes."
                        style={{fontSize:12,padding:"8px 14px",borderRadius:8,border:"none",cursor:"pointer",
                          fontFamily:"'Inter',sans-serif",fontWeight:600,transition:"all .15s",
                          background:emailedOnly?"#a3a6ff":"#192540",
                          color:emailedOnly?"#000011":"#a3aac4"}}>
                        📧 Emailed Previously{everEmailedCount>0?` (${everEmailedCount})`:""}
                      </button>
                    )
                  })()}
                  {(()=>{
                    const needCount = leads.filter(l=>l.status==="no_answer"&&(l.total_calls||0)<4).length
                    return (
                      <button
                        onClick={()=>setNeedsAttemptOnly(p=>!p)}
                        title="Unanswered leads with fewer than 4 attempts. Team rule: try these at least 4× before giving up. Great pool to work when there are no fresh leads."
                        style={{fontSize:12,padding:"8px 14px",borderRadius:8,border:"none",cursor:"pointer",
                          fontFamily:"'Inter',sans-serif",fontWeight:600,transition:"all .15s",
                          background:needsAttemptOnly?"#ffe083":"#192540",
                          color:needsAttemptOnly?"#3a2e00":"#a3aac4"}}>
                        📞 Needs Another Call{needCount>0?` (${needCount})`:""}
                      </button>
                    )
                  })()}
                  {(()=>{
                    // Bridge: carry the current Leads view (state + city +
                    // unanswered intent) straight into the scoped Dialer so she
                    // can start calling exactly what she's filtered to. Count
                    // shows dial-eligible leads (assignable + valid phone).
                    const wantUnanswered = fStatus==="no_answer" || needsAttemptOnly
                    const eligible = displayLeads.filter(l=>
                      (!l.assignedTo||l.assignedTo===user)
                      && !NO_DIAL_STATUSES.has(l.status)
                      && isValidUsPhone(l.phone)
                      && (!wantUnanswered || l.status==="no_answer")
                    ).length
                    return (
                      <button
                        onClick={()=>{ setDialerState(fState||""); setDialerCity(fCity||""); setDialerUnanswered(wantUnanswered); setNav("dialer") }}
                        title="Open the Dialer scoped to this exact view (state + city + unanswered) and start calling"
                        style={{fontSize:12,padding:"8px 16px",borderRadius:8,border:"none",cursor:"pointer",
                          fontFamily:"'Inter',sans-serif",fontWeight:700,transition:"all .15s",
                          background:"#69f6b8",color:"#003d26"}}>
                        📞 Call these in Dialer{eligible>0?` (${eligible})`:""}
                      </button>
                    )
                  })()}
                  {isAdmin()&&(()=>{
                    // Admin: hand the whole filtered view to a caller in one
                    // click (e.g. the 99 new Las Vegas leads → cristine).
                    const ids = displayLeads.map(l=>l.id)
                    return (
                      <button
                        onClick={async()=>{
                          if(ids.length===0){ notify("No leads in this view","error"); return }
                          const raw = window.prompt(`Assign these ${ids.length} lead(s) to which caller?\n(leave blank = release to the unassigned pool)`)
                          if(raw===null) return
                          const to = raw.trim()
                          if(!window.confirm(`Assign ${ids.length} lead${ids.length!==1?"s":""} to ${to||"the unassigned pool"}?`)) return
                          try{
                            const r=await api("/api/leads/assign-ids",{method:"POST",body:JSON.stringify({ids,to})})
                            notify(`${r.assigned} lead${r.assigned!==1?"s":""} assigned to ${r.to}`)
                            loadLeads()
                          }catch(ex){ notify("Assign failed","error") }
                        }}
                        title="Assign every lead in this filtered view to a caller (admin only)"
                        style={{fontSize:12,padding:"8px 16px",borderRadius:8,border:"none",cursor:"pointer",
                          fontFamily:"'Inter',sans-serif",fontWeight:700,transition:"all .15s",
                          background:"#8b5cf6",color:"#fff"}}>
                        👤 Assign these to…{ids.length>0?` (${ids.length})`:""}
                      </button>
                    )
                  })()}
                </div>
              </section>

              <div style={{background:"#0f1930",borderRadius:12,overflow:"hidden"}}>
                <div style={{display:"grid",gridTemplateColumns:"3fr 2fr 2fr 2fr 3fr",
                  padding:"10px 24px",fontSize:"0.6rem",fontWeight:700,
                  color:"#a3aac4",textTransform:"uppercase",letterSpacing:".1em",
                  opacity:.65,borderBottom:"1px solid #40485d20"}}>
                  <div>Contact &amp; Company</div>
                  <div>Phone Number</div>
                  <div>Lead Score</div>
                  <div>Status</div>
                  <div style={{textAlign:"right"}}>Actions</div>
                </div>
                {loading?(
                  <div style={{padding:"64px 24px",textAlign:"center",color:"#40485d",fontSize:13}}>Loading leads…</div>
                ):displayLeads.length===0?(
                  <div style={{padding:"72px 24px",textAlign:"center"}}>
                    <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:52,fontWeight:700,
                      color:"#192540",marginBottom:10}}>0</div>
                    <div style={{fontSize:11,color:"#40485d",letterSpacing:".1em",textTransform:"uppercase"}}>
                      {cbOnly?"No callbacks due":"Import a CSV or use Find Leads to get started"}
                    </div>
                  </div>
                ):(()=>{
                  // Single per-row renderer reused for both segments below.
                  const renderRow = (lead) => {
                    const info=si(lead.status)
                    const isCb=lead.callbackDate&&lead.callbackDate<=today&&lead.status!=="converted"
                    const score=lead.score||scoreLead(lead)||0
                    const ac=avatarColor(lead.company||lead.firstName||"?")
                    const isMine=!lead.assignedTo||lead.assignedTo===user
                    const takenBy=!isMine?lead.assignedTo:null
                    const isNewToday=(lead.createdAt||"").startsWith(today)
                    return(
                      <div key={lead.id} className={isCb?"lrow-cb":""}
                        style={{display:"grid",gridTemplateColumns:"3fr 2fr 2fr 2fr 3fr",
                          padding:"16px 24px",alignItems:"center",gap:16,
                          borderBottom:"1px solid #40485d12",transition:"background .12s",cursor:"default",
                          opacity:takenBy?0.55:1}}
                        onMouseEnter={e=>e.currentTarget.style.background=isCb?"#8b5cf612":takenBy?"#0f1930":"#192540"}
                        onMouseLeave={e=>e.currentTarget.style.background=isCb?"#8b5cf608":"transparent"}>
                        <div style={{display:"flex",alignItems:"center",gap:14,minWidth:0}}>
                          <div style={{width:40,height:40,borderRadius:"50%",flexShrink:0,
                            background:ac+"22",display:"flex",alignItems:"center",justifyContent:"center",
                            fontSize:13,fontWeight:700,color:ac,fontFamily:"'Space Grotesk',sans-serif",letterSpacing:"-.01em"}}>
                            {getInitials(lead)}</div>
                          <div style={{minWidth:0}}>
                            <div style={{fontWeight:700,color:"#dee5ff",fontFamily:"'Space Grotesk',sans-serif",fontSize:14,
                              overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                              {[lead.firstName,lead.lastName].filter(Boolean).join(" ")||lead.company}</div>
                            {/* Show title only when we have a person (firstName) — for warm leads
                                where line 1 is already the company, repeating it would look weird. */}
                            {lead.firstName&&lead.title&&(
                              <div style={{fontSize:12,color:"#8b5cf6",marginTop:1,fontWeight:600,
                                overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                                {lead.title}
                              </div>
                            )}
                            <div style={{fontSize:13,color:"#a3aac4",marginTop:1}}>{lead.company||"—"}{lead.city&&lead.state?` · ${lead.city}, ${lead.state}`:lead.state?` · ${lead.state}`:lead.city?` · ${lead.city}`:""}</div>
                            <div style={{display:"flex",gap:5,marginTop:4,flexWrap:"wrap"}}>
                              {isNewToday&&<span style={{fontSize:9,background:"#69f6b818",color:"#69f6b8",padding:"2px 7px",borderRadius:4,border:"1px solid #69f6b830",fontWeight:700}}>NEW TODAY</span>}
                              {takenBy&&<span style={{fontSize:9,background:"#ff6e8420",color:"#ff6e84",padding:"2px 7px",borderRadius:4,border:"1px solid #ff6e8430"}}>🔒 {takenBy}</span>}
                              {!takenBy&&lead.assignedTo&&<span style={{fontSize:9,background:"#69f6b818",color:"#69f6b8",padding:"2px 7px",borderRadius:4}}>✓ mine</span>}
                              {lead.source&&<span className="src-tag">{lead.source}</span>}
                              {isCb&&<span style={{fontSize:9,background:"#8b5cf618",color:"#8b5cf6",padding:"2px 7px",borderRadius:4,border:"1px solid #8b5cf630"}}>🔔 {lead.callbackDate}</span>}
                              {/* 📧 Persistent email-history badge — survives status changes
                                  so caller knows "this lead got our email" even after a
                                  reply flips them back into the pool. */}
                              {emailedFlags[lead.id]&&(
                                <span style={{fontSize:9,background:"#a3a6ff20",color:"#a3a6ff",
                                  padding:"2px 7px",borderRadius:4,border:"1px solid #a3a6ff40",fontWeight:700}}
                                  title={`Emailed ${emailedFlags[lead.id].email_count}× — last on ${emailedFlags[lead.id].last_emailed_at?.slice(0,10)||"?"}`}>
                                  📧 EMAILED{emailedFlags[lead.id].email_count>1?` ${emailedFlags[lead.id].email_count}×`:""}
                                </span>
                              )}
                              {lead.contract_value>0&&<span style={{fontSize:9,background:"#69f6b818",color:"#69f6b8",padding:"2px 7px",borderRadius:4}}>${(lead.contract_value||0).toLocaleString()}</span>}
                              {lead.followupsequence&&<span style={{fontSize:9,background:"#ffe08312",color:"#ffe083",padding:"2px 7px",borderRadius:4,border:"1px solid #ffe08325"}}>⏱ fu</span>}
                            </div>
                          </div>
                        </div>
                        <div style={{display:"flex",alignItems:"center",gap:7,fontSize:13,color:"#dee5ff",opacity:.85}}>
                          {lead.phone?(
                            <><svg width={14} height={14} fill="none" viewBox="0 0 24 24" stroke="#a3a6ff" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"/>
                            </svg>{lead.phone}</>
                          ):<span style={{color:"#40485d"}}>—</span>}
                        </div>
                        <div>
                          <ScoreRing score={score}/>
                          {(lead.assignedTo||lead.total_calls>0)&&(
                            <div style={{marginTop:4,fontSize:11,color:"#40485d"}}>
                              {lead.assignedTo&&<span>{lead.assignedTo}</span>}
                              {lead.total_calls>0&&<span style={{marginLeft:4}}>· {lead.total_calls} call{lead.total_calls!==1?"s":""}</span>}
                            </div>
                          )}
                          {lead.last_called_at?(
                            <div style={{marginTop:2,fontSize:10,color:"#a3aac4",lineHeight:1.35}}
                              title={"Exact: "+new Date(lead.last_called_at).toLocaleString()+
                                     "\nVary timing for non-answers — don't keep retrying the same hour."}>
                              <div>📞 {pastAgo(lead.last_called_at)} ago</div>
                              <div style={{color:"#40485d",fontSize:9}}>
                                {new Date(lead.last_called_at).toLocaleDateString([],{month:"short",day:"numeric",year:"2-digit"})}
                                {" · "}
                                {new Date(lead.last_called_at).toLocaleTimeString([],{hour:"numeric",minute:"2-digit"})}
                              </div>
                            </div>
                          ):(lead.total_calls||0)===0?(
                            <div style={{marginTop:2,fontSize:10,color:"#69f6b8",fontWeight:600}}>
                              ✨ Never called
                            </div>
                          ):null}
                        </div>
                        <div>
                          <span className="pill" style={{background:info.color+"20",color:info.color,border:`1px solid ${info.color}30`}}>
                            {info.label}</span>
                          <div style={{display:"flex",gap:3,marginTop:6,flexWrap:"wrap"}}>
                            {STATUS_OPTIONS.filter(s=>s.value!==lead.status).slice(0,2).map(s=>(
                              <button key={s.value} className="qs"
                                onClick={e=>{e.stopPropagation();quickStatus(lead,s.value)}}
                                style={{color:s.color,borderColor:s.color+"30",fontSize:"10px"}}>{s.label}</button>
                            ))}
                          </div>
                        </div>
                        <div style={{display:"flex",alignItems:"center",justifyContent:"flex-end",gap:6}}>
                          <LogCallBtn onClick={e=>{e.stopPropagation();setCallModal(lead)}}/>
                          <IconBtn onClick={e=>{e.stopPropagation();setEmailModal(lead)}} title="Send Email"
                            hoverColor="#69f6b8" baseColor="#40485d"><IconMail/></IconBtn>
                          <IconBtn onClick={e=>{e.stopPropagation();setEditModal(lead)}} title="Edit lead"><IconEdit/></IconBtn>
                          <IconBtn onClick={e=>{e.stopPropagation();deleteL(lead.id)}} title="Delete"
                            hoverColor="#ff6e84" baseColor="#40485d"><IconTrash/></IconBtn>
                        </div>
                      </div>
                    )
                  }
                  // Section banner reused for both groups — keeps the visual
                  // pattern that matches the existing header strip on the page.
                  const Banner = ({color,icon,label,count,subtitle}) => (
                    <div style={{padding:"12px 24px",background:`${color}10`,borderBottom:`1px solid ${color}30`,
                      display:"flex",alignItems:"center",gap:10,flexWrap:"wrap"}}>
                      <span style={{fontSize:14}}>{icon}</span>
                      <span style={{fontSize:11,fontWeight:700,color,letterSpacing:".08em",textTransform:"uppercase"}}>
                        {label}
                      </span>
                      <span style={{fontSize:11,color,background:`${color}25`,padding:"2px 9px",borderRadius:10,fontWeight:700}}>
                        {count}
                      </span>
                      {subtitle&&<span style={{fontSize:11,color:"#a3aac4",marginLeft:4}}>{subtitle}</span>}
                    </div>
                  )
                  // ── State-grouped, collapsible sections ──────────────
                  // Primary grouping is by state so the caller can work one
                  // state at a time. The original Apollo / Warm split is kept
                  // nested inside each state section.
                  const stateOf = (l)=>(l.state||"").trim().toUpperCase()
                  const groups = {}
                  displayLeads.forEach(l=>{
                    const k = stateOf(l) || "__none__"
                    ;(groups[k] = groups[k] || []).push(l)
                  })
                  const stateKeys = Object.keys(groups).sort((a,b)=>{
                    if(a==="__none__") return 1
                    if(b==="__none__") return -1
                    return groups[b].length - groups[a].length   // busiest state first
                  })
                  return (
                    <div>
                      {stateKeys.map(sk=>{
                        const list = groups[sk]
                        const fullName = sk==="__none__" ? "No State / Unknown" : (STATE_NAMES[sk]||sk)
                        const collapsed = !!collapsedStates[sk]
                        const newToday = list.filter(l=>(l.createdAt||"").startsWith(today)).length
                        const stDm   = sortNewFirst(list.filter(isApolloLead))
                        const stWarm = sortNewFirst(list.filter(l=>!isApolloLead(l)))
                        return (
                          <div key={sk} style={{marginBottom:14,border:"1px solid #1c2740",
                            borderRadius:12,overflow:"hidden"}}>
                            <button onClick={()=>toggleState(sk)}
                              title={collapsed?`Click to show ${fullName} leads`:`Click to hide ${fullName} leads`}
                              style={{width:"100%",display:"flex",alignItems:"center",gap:12,
                                padding:"14px 20px",background:"#0f1930",border:"none",cursor:"pointer",
                                fontFamily:"inherit",textAlign:"left"}}>
                              <span style={{fontSize:13,transition:"transform .15s",width:14,flexShrink:0,
                                transform:collapsed?"rotate(-90deg)":"rotate(0)",color:"#a3a6ff"}}>▼</span>
                              <span style={{fontSize:16}}>🗺️</span>
                              <span style={{fontSize:16,fontWeight:700,color:"#dee5ff",
                                fontFamily:"'Space Grotesk',sans-serif"}}>{fullName}</span>
                              <span style={{fontSize:12,color:"#a3a6ff",background:"#a3a6ff20",
                                padding:"2px 10px",borderRadius:10,fontWeight:700}}>
                                {list.length.toLocaleString()} lead{list.length===1?"":"s"}</span>
                              {/* DM / warm split so the caller knows the mix at a glance */}
                              {stDm.length>0&&(
                                <span style={{fontSize:11,color:"#b69cff"}}>🎯 {stDm.length.toLocaleString()} decision-maker{stDm.length===1?"":"s"}</span>
                              )}
                              {stWarm.length>0&&(
                                <span style={{fontSize:11,color:"#ffe083"}}>🌡️ {stWarm.length.toLocaleString()} warm</span>
                              )}
                              {newToday>0&&(
                                <span style={{fontSize:11,color:"#69f6b8",fontWeight:600}}>+{newToday} new today</span>
                              )}
                              <span style={{marginLeft:"auto",fontSize:11,color:"#56607f"}}>
                                {collapsed?"Show ▾":"Hide ▴"}</span>
                            </button>
                            {!collapsed&&(
                              <div>
                                {stDm.length>0&&(
                                  <>
                                    <Banner color="#8b5cf6" icon="🎯"
                                      label="Decision Makers (Apollo)" count={stDm.length}
                                      subtitle="Direct contacts pulled from Apollo"/>
                                    {stDm.map(renderRow)}
                                  </>
                                )}
                                {stWarm.length>0&&(
                                  <>
                                    <Banner color="#ffe083" icon="🌡️"
                                      label="Warm Leads" count={stWarm.length}
                                      subtitle="Companies — call switchboard, ask for the DM by name if known"/>
                                    {stWarm.map(renderRow)}
                                  </>
                                )}
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  )
                })()}
              </div>

              <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginTop:24,padding:"0 4px"}}>
                <p style={{fontSize:14,color:"#a3aac4"}}>
                  {loading?"Loading…":`Showing ${displayLeads.length} of ${leads.length} lead${leads.length!==1?"s":""}`}
                </p>
                <div style={{display:"flex",alignItems:"center",gap:8}}>
                  <button style={{padding:8,borderRadius:8,background:"#141f38",color:"#dee5ff",
                    border:"none",cursor:"pointer",display:"flex",alignItems:"center"}}><IconChevLeft/></button>
                  <span style={{fontSize:14,fontWeight:700,color:"#a3a6ff",padding:"0 12px",
                    fontFamily:"'Space Grotesk',sans-serif"}}>1</span>
                  <button style={{padding:8,borderRadius:8,background:"#141f38",color:"#dee5ff",
                    border:"none",cursor:"pointer",display:"flex",alignItems:"center"}}><IconChevRight/></button>
                </div>
              </div>
            </div>
          )}

          {/* ── PHONE LOOKUP (inbound-call reverse lookup) ──────────────── */}
          {activeNav==="lookup"&&(
            <div style={{maxWidth:720,margin:"0 auto"}}>
              <div style={{marginBottom:24}}>
                <h1 style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:36,fontWeight:700,
                  color:"#dee5ff",letterSpacing:"-.02em"}}>Phone Lookup</h1>
                <p style={{color:"#a3aac4",fontSize:14,marginTop:4}}>
                  Inbound call? Paste the number to see who it is. Matches exactly on the digits —
                  any format works: <span style={{color:"#69f6b8"}}>(702) 485-3232</span>, 7024853232, +1 702-485-3232.
                </p>
              </div>
              <section style={{background:"#141f38",borderRadius:16,padding:"16px 20px",
                display:"flex",gap:12,alignItems:"center",marginBottom:20}}>
                <span style={{color:"#40485d",display:"flex"}}><IconSearch/></span>
                <input autoFocus value={lookupQuery}
                  onChange={e=>setLookupQuery(e.target.value)}
                  onKeyDown={e=>{ if(e.key==="Enter") doLookup() }}
                  placeholder="Paste or type a phone number…"
                  style={{flex:1,background:"#000011",border:"1px solid #40485d30",borderRadius:8,
                    padding:"10px 14px",color:"#dee5ff",fontSize:15,fontFamily:"'Inter',sans-serif",outline:"none"}}/>
                <button className="btn btn-p" onClick={doLookup} disabled={lookupLoading}
                  style={{background:"#8b5cf6",whiteSpace:"nowrap"}}>
                  {lookupLoading?"Looking…":"Look up →"}
                </button>
              </section>

              {lookupResult&&(
                lookupResult.count===0 ? (
                  <div style={{background:"#0f1930",borderRadius:16,padding:48,textAlign:"center"}}>
                    <div style={{fontSize:36,marginBottom:10}}>🆕</div>
                    <div style={{color:"#dee5ff",fontSize:15,marginBottom:6}}>No lead matches that number</div>
                    <div style={{color:"#a3aac4",fontSize:13,maxWidth:460,margin:"0 auto",lineHeight:1.5}}>
                      Looks like a brand-new inbound. Note: if they're calling from a different line
                      than the one on file, it won't match — try their company name in{" "}
                      <span style={{color:"#69f6b8",cursor:"pointer",textDecoration:"underline"}}
                        onClick={()=>setNav("leads")}>Leads</span>.
                    </div>
                  </div>
                ) : (
                  <div>
                    <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:12,
                      color:"#69f6b8",fontSize:13,fontWeight:600}}>
                      ✓ {lookupResult.count} exact match{lookupResult.count!==1?"es":""}
                      <span style={{color:"#40485d",fontWeight:400}}>· verified on {lookupResult.matchedOn}</span>
                    </div>
                    {lookupResult.matches.map(lead=>{
                      const info=si(lead.status)
                      return (
                        <div key={lead.id} style={{background:"#0f1930",borderRadius:12,padding:"16px 20px",
                          marginBottom:12,display:"flex",justifyContent:"space-between",gap:16,flexWrap:"wrap"}}>
                          <div style={{minWidth:0}}>
                            <div style={{color:"#dee5ff",fontSize:17,fontWeight:700}}>{lead.company||"—"}</div>
                            <div style={{color:"#a3aac4",fontSize:13,marginTop:2}}>
                              {[lead.firstName,lead.lastName].filter(Boolean).join(" ")}
                              {lead.title?<span style={{color:"#40485d"}}> · {lead.title}</span>:null}
                            </div>
                            <div style={{color:"#a3aac4",fontSize:13,marginTop:4}}>
                              📞 {lead.phone||"—"}
                              {lead.city?<span style={{color:"#40485d"}}> · {lead.city}{lead.state?`, ${lead.state}`:""}</span>:null}
                            </div>
                            <div style={{color:"#40485d",fontSize:12,marginTop:4}}>
                              {lead.total_calls>0?`${lead.total_calls} call${lead.total_calls!==1?"s":""}`:"never called"}
                              {lead.assignedTo?` · assigned to ${lead.assignedTo}`:" · unassigned"}
                            </div>
                            {lead.last_call&&(
                              <div style={{marginTop:8,paddingTop:8,borderTop:"1px solid #40485d20",
                                color:"#a3aac4",fontSize:12}}>
                                <span style={{color:"#69f6b8",fontWeight:600}}>Last call:</span>{" "}
                                {(si(lead.last_call.outcome).label)||lead.last_call.outcome}
                                {lead.last_call.calledBy?` by ${lead.last_call.calledBy}`:""}
                                {lead.last_call.calledAt?` · ${String(lead.last_call.calledAt).slice(0,10)}`:""}
                                {lead.last_call.notes?<div style={{color:"#8893ad",marginTop:2,fontStyle:"italic"}}>"{lead.last_call.notes}"</div>:null}
                              </div>
                            )}
                          </div>
                          <div style={{display:"flex",flexDirection:"column",alignItems:"flex-end",gap:8}}>
                            <span style={{background:info.color+"22",color:info.color,padding:"3px 10px",
                              borderRadius:20,fontSize:12,fontWeight:700}}>{info.label}</span>
                            <button className="btn btn-p" style={{fontSize:12,background:"#8b5cf6"}}
                              onClick={()=>setCallModal(lead)}>
                              📞 Log this call
                            </button>
                            <button className="btn btn-g" style={{fontSize:12}}
                              onClick={()=>{ setSearch(lead.phone||lookupQuery); setNav("leads") }}>
                              Open in Leads →
                            </button>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )
              )}
            </div>
          )}

          {/* ── WARM LEADS (from VCC Outreach) ──────────────────────────── */}
          {activeNav==="warm"&&(
            <div>
              <div style={{marginBottom:28,display:"flex",alignItems:"flex-end",justifyContent:"space-between",gap:20,flexWrap:"wrap"}}>
                <div>
                  <h1 style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:36,fontWeight:700,
                    color:"#dee5ff",letterSpacing:"-.02em",lineHeight:1.1}}>
                    Warm Leads <span style={{fontSize:16,color:"#ffe083",marginLeft:8}}>from VCC Outreach</span>
                  </h1>
                  <p style={{color:"#a3aac4",fontSize:14,marginTop:4}}>
                    These companies opened our outreach emails — they already know who we are
                  </p>
                </div>
                <div style={{display:"flex",gap:8,alignItems:"center"}}>
                  {warmLeads.length>0&&(
                    <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:28,fontWeight:700,
                      color:"#ffe083"}}>{warmLeads.length}</span>
                  )}
                  <button className="btn btn-g" style={{fontSize:11,padding:"5px 12px"}}
                    onClick={()=>{
                      setWarmLoading(true)
                      api("/api/leads?source=VCC+Outreach").then(r=>{
                        const list = Array.isArray(r)?r:[]
                        setWarmLeads(list)
                      }).catch(()=>{}).finally(()=>setWarmLoading(false))
                    }}>Refresh</button>
                </div>
              </div>

              {warmLoading?(
                <div style={{padding:60,textAlign:"center",color:"#40485d"}}>Loading...</div>
              ):warmLeads.length===0?(
                <div style={{background:"#0f1930",borderRadius:16,padding:72,textAlign:"center"}}>
                  <div style={{fontSize:40,marginBottom:12}}>📭</div>
                  <div style={{color:"#a3aac4",fontSize:14}}>No warm leads yet</div>
                  <div style={{color:"#40485d",fontSize:12,marginTop:6}}>
                    Engaged VCC companies will auto-populate here once they open outreach emails
                  </div>
                </div>
              ):(
                <div style={{background:"#0f1930",borderRadius:16,overflow:"hidden"}}>
                  {/* Header row */}
                  <div style={{display:"grid",gridTemplateColumns:"2.5fr 1.5fr 1.5fr 1fr 1fr 2fr 1.5fr",
                    padding:"12px 24px",fontSize:"0.5rem",fontWeight:700,color:"#a3aac4",
                    textTransform:"uppercase",letterSpacing:".08em",borderBottom:"1px solid #40485d15"}}>
                    <div>Company</div>
                    <div>Contact</div>
                    <div>Phone</div>
                    <div style={{textAlign:"center"}}>Score</div>
                    <div style={{textAlign:"center"}}>Status</div>
                    <div>Notes</div>
                    <div style={{textAlign:"right"}}>Actions</div>
                  </div>
                  {warmLeads.map(lead=>{
                    const info=si(lead.status)
                    const score=lead.score||scoreLead(lead)||0
                    const ac=avatarColor(lead.company||"?")
                    // Parse engagement info from notes
                    const engMatch=(lead.notes||"").match(/Engagement score: (\d+)/)
                    const engScore=engMatch?parseInt(engMatch[1]):0
                    const opensMatch=(lead.notes||"").match(/(\d+) email opens/)
                    const opens=opensMatch?parseInt(opensMatch[1]):0
                    return(
                      <div key={lead.id}
                        style={{display:"grid",gridTemplateColumns:"2.5fr 1.5fr 1.5fr 1fr 1fr 2fr 1.5fr",
                          padding:"14px 24px",alignItems:"center",gap:8,
                          borderBottom:"1px solid #40485d10",transition:"background .12s"}}
                        onMouseEnter={e=>e.currentTarget.style.background="#192540"}
                        onMouseLeave={e=>e.currentTarget.style.background="transparent"}>
                        {/* Company */}
                        <div style={{display:"flex",alignItems:"center",gap:12,minWidth:0}}>
                          <div style={{width:36,height:36,borderRadius:"50%",flexShrink:0,background:ac+"22",
                            display:"flex",alignItems:"center",justifyContent:"center",
                            fontSize:12,fontWeight:700,color:ac,fontFamily:"'Space Grotesk',sans-serif"}}>
                            {(lead.company||"?").slice(0,2).toUpperCase()}
                          </div>
                          <div style={{minWidth:0}}>
                            <div style={{fontWeight:700,color:"#dee5ff",fontSize:14,fontFamily:"'Space Grotesk',sans-serif",
                              overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{lead.company||"—"}</div>
                            <div style={{fontSize:11,color:"#a3aac4"}}>
                              {lead.city&&lead.state?`${lead.city}, ${lead.state}`:lead.state||lead.city||""}
                              {lead.industry?` · ${lead.industry}`:""}
                            </div>
                            <div style={{display:"flex",gap:4,marginTop:3}}>
                              <span style={{fontSize:9,background:"#ffe08318",color:"#ffe083",padding:"2px 7px",
                                borderRadius:4,border:"1px solid #ffe08330"}}>VCC Outreach</span>
                              {engScore>=70&&<span style={{fontSize:9,background:"#ff6e8418",color:"#ff6e84",
                                padding:"2px 7px",borderRadius:4,border:"1px solid #ff6e8430"}}>🔥 Hot</span>}
                              {engScore>=40&&engScore<70&&<span style={{fontSize:9,background:"#ffe08318",color:"#ffe083",
                                padding:"2px 7px",borderRadius:4,border:"1px solid #ffe08330"}}>Warm</span>}
                              {opens>0&&<span style={{fontSize:9,background:"#a3a6ff18",color:"#a3a6ff",
                                padding:"2px 7px",borderRadius:4}}>{opens} opens</span>}
                            </div>
                          </div>
                        </div>
                        {/* Contact */}
                        <div>
                          <div style={{fontSize:13,color:"#dee5ff"}}>
                            {[lead.firstName,lead.lastName].filter(Boolean).join(" ")||"—"}
                          </div>
                          {lead.email&&<div style={{fontSize:11,color:"#a3a6ff",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{lead.email}</div>}
                          {lead.title&&<div style={{fontSize:10,color:"#40485d"}}>{lead.title}</div>}
                        </div>
                        {/* Phone */}
                        <div style={{fontSize:13,color:lead.phone?"#dee5ff":"#40485d",fontFamily:"'Space Grotesk',sans-serif"}}>
                          {lead.phone||"No phone"}
                        </div>
                        {/* Score */}
                        <div style={{textAlign:"center"}}>
                          <ScoreRing score={score}/>
                        </div>
                        {/* Status */}
                        <div style={{textAlign:"center"}}>
                          <span className="pill" style={{background:info.color+"20",color:info.color,
                            border:`1px solid ${info.color}30`}}>{info.label}</span>
                        </div>
                        {/* Notes */}
                        <div style={{fontSize:11,color:"#a3aac4",overflow:"hidden",textOverflow:"ellipsis",
                          whiteSpace:"nowrap"}}>{lead.notes||"—"}</div>
                        {/* Actions */}
                        <div style={{display:"flex",alignItems:"center",justifyContent:"flex-end",gap:6}}>
                          <LogCallBtn onClick={e=>{e.stopPropagation();setCallModal(lead)}}/>
                          <IconBtn onClick={e=>{e.stopPropagation();setEmailModal(lead)}} title="Send Email"
                            hoverColor="#69f6b8" baseColor="#40485d"><IconMail/></IconBtn>
                          <IconBtn onClick={e=>{e.stopPropagation();setEditModal(lead)}} title="Edit"><IconEdit/></IconBtn>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {/* ── DIALER ──────────────────────────────────────────────────────── */}
          {activeNav==="dialer"&&(
            <div>
              <div style={{marginBottom:24,display:"flex",alignItems:"flex-end",justifyContent:"space-between",
                gap:16,flexWrap:"wrap"}}>
                <div>
                  <h1 style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:36,fontWeight:700,
                    color:"#dee5ff",letterSpacing:"-.02em"}}>Dialer</h1>
                  <p style={{color:"#a3aac4",fontSize:14,marginTop:4}}>
                  Focused calling mode — only showing unclaimed leads
                  {(()=>{
                    const overdue=leads.filter(l=>l.callbackDate&&l.callbackDate<today&&l.status!=="converted")
                    return overdue.length>0?(
                      <span style={{marginLeft:12,background:"#ff6e8430",color:"#ff6e84",padding:"3px 10px",
                        borderRadius:20,fontSize:12,fontWeight:700}}>
                        {overdue.length} overdue
                      </span>
                    ):null
                  })()}
                  </p>
                </div>
                {/* Scope + snooze controls. Scope lets the caller power through
                    one state's unanswered leads ("go back and call the CT leads
                    that never picked up"); snooze hides dead-ends dialed recently. */}
                <div style={{display:"flex",alignItems:"center",gap:14,flexWrap:"wrap"}}>
                  <div style={{display:"flex",alignItems:"center",gap:8,fontSize:12,color:"#a3aac4"}}>
                    <span>📍 State</span>
                    <select className="sel" value={dialerState} onChange={e=>setDialerState(e.target.value)}
                      title="Only dial leads in this state" style={{padding:"6px 10px",fontSize:12,minWidth:90}}>
                      <option value="">All states</option>
                      {STATES.filter(s=>s).map(s=><option key={s} value={s}>{s}</option>)}
                    </select>
                  </div>
                  {dialerCity&&(
                    <div style={{display:"flex",alignItems:"center",gap:6,fontSize:12,fontWeight:600,
                      color:"#003d26",background:"#69f6b8",padding:"6px 10px",borderRadius:8}}>
                      📍 {dialerCity}
                      <span onClick={()=>setDialerCity("")} style={{cursor:"pointer",fontWeight:700}}
                        title="Clear city scope">✕</span>
                    </div>
                  )}
                  <button
                    onClick={()=>setDialerUnanswered(p=>!p)}
                    title="Only dial leads that have never answered (No Answer / voicemail) — go back and re-try them"
                    style={{fontSize:12,padding:"6px 12px",borderRadius:8,border:"none",cursor:"pointer",
                      fontFamily:"'Inter',sans-serif",fontWeight:600,transition:"all .15s",
                      background:dialerUnanswered?"#ffe083":"#192540",
                      color:dialerUnanswered?"#3a2e00":"#a3aac4"}}>
                    📞 Unanswered only
                  </button>
                  <div style={{display:"flex",alignItems:"center",gap:8,fontSize:12,color:"#a3aac4"}}>
                    <span title="Hide no_answer/voicemail leads dialed in the last N hours">😴 Snooze recent</span>
                    <select className="sel" value={snoozeHours} onChange={e=>setSnoozeHours(+e.target.value)}
                      style={{padding:"6px 10px",fontSize:12,minWidth:70}}>
                      <option value={0}>Off</option>
                      <option value={2}>2h</option>
                      <option value={4}>4h</option>
                      <option value={8}>8h</option>
                      <option value={24}>24h</option>
                    </select>
                  </div>
                </div>
              </div>
              {(()=>{
                // Skip bad-phone leads — caller dialing a non-NANP number is
                // a guaranteed disconnect that drags down connectivity rate.
                // The lead still exists in the DB (admin can review/clean via
                // the Lead Cleanup panel) but won't surface in the dial queue.
                //
                // Always smart-sort the dialer pool regardless of the leads
                // view's sort dropdown: fresh (never dialed) first, then
                // longest-since-last-call, then highest score. This is the
                // fix for "leads feel like they're repeating" — the same
                // no_answer rows kept rising to the top under score.desc.
                // Snooze cutoff: dead-end outcomes (no_answer/voicemail/called)
                // dialed within `snoozeHours` are hidden from the queue. Engaged
                // statuses (interested/callback/converted) bypass snooze — those
                // we DO want to surface again.
                const snoozeCutoff = snoozeHours>0
                  ? new Date(Date.now()-snoozeHours*3600*1000).toISOString()
                  : null
                const SNOOZED_OUTCOMES = new Set(["no_answer","voicemail","called","not_interested"])
                const isSnoozed = (l)=>{
                  if(!snoozeCutoff) return false
                  if(!l.last_called_at) return false
                  if(!SNOOZED_OUTCOMES.has(l.status)) return false
                  return l.last_called_at >= snoozeCutoff
                }
                const dialerLeads=leads.filter(l=>
                  (!l.assignedTo||l.assignedTo===user)
                  && !NO_DIAL_STATUSES.has(l.status)
                  && isValidUsPhone(l.phone)
                  && !isSnoozed(l)
                  && (!dialerState || l.state===dialerState)
                  && (!dialerCity || (l.city||"").toLowerCase().startsWith(dialerCity.toLowerCase().trim()))
                  && (!dialerUnanswered || l.status==="no_answer")
                ).slice().sort((a,b)=>{
                  const ca=a.total_calls||0, cb=b.total_calls||0
                  if(ca!==cb) return ca-cb                              // fewest calls first
                  const la=a.last_called_at||"", lb=b.last_called_at||""
                  if(la!==lb) return la<lb?-1:1                         // oldest contact first ("" sorts before any date)
                  return (b.score||0)-(a.score||0)                      // tiebreak: highest score
                })
                if(dialerLeads.length===0){
                  // Fallback when nothing fresh is in the queue: surface
                  // unanswered leads that still need attempts (no_answer, <4
                  // calls). They may be hidden here by the snooze window, but
                  // the caller can still work them — the 4×-before-giveup rule.
                  const needAttempt = leads.filter(l=>(!l.assignedTo||l.assignedTo===user)&&l.status==="no_answer"&&(l.total_calls||0)<4)
                  const scoped = dialerState||dialerCity||dialerUnanswered
                  const scopeLabel = [dialerUnanswered?"unanswered":"",dialerCity||"",dialerState||""].filter(Boolean).join(" ")
                  return(
                  <div style={{background:"#0f1930",borderRadius:16,padding:72,textAlign:"center"}}>
                    <div style={{fontSize:40,marginBottom:12}}>{scoped?"🔍":(needAttempt.length>0?"📞":"✅")}</div>
                    <div style={{color:"#a3aac4",fontSize:14,marginBottom:8}}>
                      {scoped
                        ? `No ${scopeLabel} leads to dial right now`
                        : (leads.length>0?"No fresh leads in the dialer right now":"No leads to dial yet")}
                    </div>
                    <div style={{color:"#40485d",fontSize:12,marginBottom:20}}>
                      {scoped
                        ? (snoozeHours>0
                            ? `Nothing matches this scope right now — recently-dialed leads are hidden by the ${snoozeHours}h snooze. Turn off snooze to re-work them, or clear the filters.`
                            : "Nothing matches this scope (they may all be assigned to other reps). Clear the filters to see everything.")
                        : (needAttempt.length>0
                            ? `${needAttempt.length} unanswered lead${needAttempt.length!==1?"s":""} still need another attempt (under 4 tries)`
                            : (leads.length>0?`${leads.length} lead${leads.length!==1?"s":""} total, all assigned`:"Import a CSV or use Find Leads to get started"))}
                    </div>
                    {scoped&&snoozeHours>0&&(
                      <button className="btn btn-p" style={{marginRight:8}}
                        onClick={()=>setSnoozeHours(0)}>
                        😴 Turn off snooze
                      </button>
                    )}
                    {scoped&&(
                      <button className="btn btn-g" style={{marginRight:8}}
                        onClick={()=>{ setDialerState(""); setDialerCity(""); setDialerUnanswered(false) }}>
                        ✕ Clear dialer filters
                      </button>
                    )}
                    {!scoped&&needAttempt.length>0&&(
                      <button className="btn btn-p" style={{marginRight:8}}
                        onClick={()=>{ setNeedsAttemptOnly(true); setNav("leads") }}>
                        📞 Work {needAttempt.length} unanswered lead{needAttempt.length!==1?"s":""}
                      </button>
                    )}
                    <button className="btn btn-g" onClick={()=>setNav("leads")}>View All Leads</button>
                  </div>
                  )
                }
                const idx=Math.min(dialerIdx,dialerLeads.length-1)
                const lead=dialerLeads[idx]
                const score=lead.score||scoreLead(lead)||0
                const ac=avatarColor(lead.company||lead.firstName||"?")
                const info=STATUS_OPTIONS.find(s=>s.value===lead.status)||STATUS_OPTIONS[0]
                // Resolve the local time at the lead's location and the
                // industry-specific script for the call. Both are pure
                // lookups against state already loaded; the script cache
                // gets populated by the dialer-render effect below.
                const lt = localTimeAt(lead.state)
                const offHours = lt && (lt.hour < 8 || lt.hour >= 19)  // outside 8am-7pm local
                const industryScript = lead.industry ? scriptCache[lead.industry] : null
                return(
                  <div style={{maxWidth:520,margin:"0 auto"}}>
                    <div style={{textAlign:"center",color:"#a3aac4",fontSize:13,marginBottom:24,
                      fontFamily:"'Space Grotesk',sans-serif"}}>
                      {dialerLeads.length} unclaimed lead{dialerLeads.length!==1?"s":""} · showing {idx+1} of {dialerLeads.length}
                    </div>
                    <div style={{background:"#0f1930",borderRadius:20,padding:40,textAlign:"center",marginBottom:16}}>
                      <div style={{width:80,height:80,borderRadius:"50%",background:ac+"22",margin:"0 auto 20px",
                        display:"flex",alignItems:"center",justifyContent:"center",
                        fontSize:28,fontWeight:700,color:ac,fontFamily:"'Space Grotesk',sans-serif"}}>
                        {getInitials(lead)}</div>
                      <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:26,fontWeight:700,
                        color:"#dee5ff",marginBottom:4}}>
                        {[lead.firstName,lead.lastName].filter(Boolean).join(" ")||lead.company}</div>
                      <div style={{color:"#a3aac4",fontSize:15,marginBottom:16}}>{lead.company}</div>
                      <div style={{display:"flex",justifyContent:"center",gap:10,marginBottom:14,flexWrap:"wrap"}}>
                        <ScoreRing score={score}/>
                        <span className="pill" style={{background:info.color+"20",color:info.color,border:`1px solid ${info.color}30`}}>{info.label}</span>
                        {lead.industry&&<span className="pill" style={{background:"#a3a6ff18",color:"#a3a6ff"}}>{lead.industry}</span>}
                        {/* Local-time-at-lead chip — turns red outside business
                            hours (before 8am / after 7pm local) so the caller
                            doesn't ring MD businesses at 5am Pacific. */}
                        {lt&&(
                          <span className="pill"
                            title={`${lead.state} local time · ${lt.tz}`}
                            style={{background:offHours?"#ff6e8420":"#69f6b818",
                                    color:offHours?"#ff6e84":"#69f6b8",
                                    border:`1px solid ${offHours?"#ff6e8430":"#69f6b830"}`}}>
                            🕐 {lt.time} {lt.tzShort}{offHours?" · off-hours":""}
                          </span>
                        )}
                      </div>
                      {/* Contact history line — fresh leads get a green badge,
                          previously-worked leads show how long it's been so
                          the caller can vary timing instead of dialing the
                          same hour twice. */}
                      <div style={{marginBottom:18,fontSize:12,color:"#a3aac4",
                        fontFamily:"'Space Grotesk',sans-serif",lineHeight:1.5}}
                        title={lead.last_called_at?"Exact: "+new Date(lead.last_called_at).toLocaleString():""}>
                        {lead.last_called_at?(
                          <>
                            <div>📞 Last call {pastAgo(lead.last_called_at)} ago
                              {lead.total_calls>0&&<span style={{color:"#40485d"}}> · {lead.total_calls} total</span>}
                            </div>
                            <div style={{color:"#40485d",fontSize:11,marginTop:2}}>
                              {new Date(lead.last_called_at).toLocaleDateString([],{weekday:"short",month:"short",day:"numeric",year:"numeric"})}
                              {" at "}
                              {new Date(lead.last_called_at).toLocaleTimeString([],{hour:"numeric",minute:"2-digit"})}
                            </div>
                          </>
                        ):(
                          <span style={{color:"#69f6b8",fontWeight:600}}>✨ Never called — fresh lead</span>
                        )}
                      </div>
                      {lead.phone&&(
                        <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:32,fontWeight:700,
                          color:"#a3a6ff",letterSpacing:".04em",marginBottom:20}}>{lead.phone}</div>
                      )}
                      {/* Claim before calling */}
                      {!lead.assignedTo&&(
                        <button className="btn btn-g"
                          style={{width:"100%",padding:"11px",fontSize:13,marginBottom:10}}
                          onClick={async()=>{
                            await api(`/api/leads/${lead.id}`,{method:"PATCH",body:JSON.stringify({assignedTo:user,updatedAt:new Date().toISOString()})})
                            setLeads(p=>p.map(l=>l.id===lead.id?{...l,assignedTo:user}:l))
                          }}>
                          🔒 Claim Lead
                        </button>
                      )}
                      <button className="btn btn-p"
                        style={{width:"100%",padding:"16px",fontSize:16,fontFamily:"'Space Grotesk',sans-serif",
                          fontWeight:700,boxShadow:"0 8px 32px rgba(163,166,255,.25)"}}
                        onClick={()=>setCallModal(lead)}>
                        📞 Log Call
                      </button>
                      <button className="btn btn-g"
                        style={{width:"100%",padding:"13px",fontSize:14,fontFamily:"'Space Grotesk',sans-serif",
                          fontWeight:600,marginTop:8,display:"flex",alignItems:"center",justifyContent:"center",gap:8}}
                        onClick={()=>setEmailModal(lead)}>
                        <IconMail/> Send Email
                      </button>
                    </div>
                    {/* Trigger lazy script + best-time load for this industry */}
                    {lead.industry && (ensureScript(lead.industry), ensureBestTime(lead.industry), null)}
                    {/* Best-call-time hint — green if the local hour matches
                        the historical sweet spot, dim grey otherwise. Only
                        shows when there's enough signal (≥5 calls). */}
                    {(()=>{
                      const bt = lead.industry ? bestTimeCache[lead.industry] : null
                      const best = bt?.best_hour
                      if(!best || !lt) return null
                      const matches = best.hour === lt.hour
                      const fmtHr = (h)=>{
                        const ap = h>=12?"PM":"AM"; const h12 = ((h+11)%12)+1
                        return `${h12}${ap}`
                      }
                      return (
                        <div style={{marginBottom:14,fontSize:11,textAlign:"center",
                          color:matches?"#69f6b8":"#a3aac4"}}>
                          💡 Best hour for {lead.industry}: <b>{fmtHr(best.hour)}–{fmtHr((best.hour+1)%24)}</b>
                          {" "}({best.rate}% contact rate, n={best.calls})
                          {matches&&<span style={{marginLeft:6,fontWeight:700}}>· you're in the window now</span>}
                        </div>
                      )
                    })()}
                    <div style={{display:"flex",gap:12}}>
                      <button className="btn btn-g" style={{flex:1,padding:11}}
                        onClick={()=>setDialerIdx(i=>Math.max(0,i-1))}
                        disabled={idx===0}>← Previous</button>
                      <button className="btn btn-g" style={{flex:1,padding:11}}
                        onClick={()=>setDialerIdx(i=>Math.min(dialerLeads.length-1,i+1))}
                        disabled={idx>=dialerLeads.length-1}>Skip →</button>
                    </div>
                    {/* Bad-Number quick-action: caller heard "number not in
                        service" or got a fax tone. One click flips the status
                        and removes from dialer pool — different from
                        do_not_contact which implies the prospect asked us to
                        stop. Bad number is a data-quality problem, not consent. */}
                    <button className="btn btn-g" style={{width:"100%",padding:9,marginTop:8,
                      fontSize:11,color:"#ff6e84",borderColor:"#ff6e8430"}}
                      title="Mark this phone as out-of-service / wrong number — removes from dialer"
                      onClick={async()=>{
                        if(!window.confirm(`Mark ${lead.phone} as out-of-service for ${lead.company}?\n\nLead stays in DB (admin Lead Cleanup can purge later) but won't surface in the dialer.`)) return
                        try{
                          await api(`/api/leads/${lead.id}`,{method:"PATCH",body:JSON.stringify({
                            status:"do_not_contact",
                            notes:[(lead.notes||"").trim(),
                              `[${new Date().toISOString().slice(0,16)}] bad-phone flagged by ${user}: ${lead.phone}`]
                              .filter(Boolean).join("\n"),
                            updatedAt:new Date().toISOString(),
                          })})
                          setLeads(p=>p.map(l=>l.id===lead.id?{...l,status:"do_not_contact"}:l))
                          notify("Phone flagged as bad","success")
                        }catch(ex){ notify("Failed to flag: "+(ex.message||"error"),"error") }
                      }}>
                      📵 Bad Number / Not in Service
                    </button>
                    {/* Industry-script opener — top-used script for this
                        industry, lazy-loaded. Shown collapsed until clicked
                        so it doesn't crowd the call card on first glance. */}
                    {industryScript&&(
                      <details style={{marginTop:16,background:"#0f1930",borderRadius:12,padding:"12px 16px"}}>
                        <summary style={{cursor:"pointer",fontSize:"0.6rem",color:"#a3a6ff",fontWeight:700,
                          letterSpacing:".1em",textTransform:"uppercase"}}>
                          📜 Script: {industryScript.title || industryScript.industry} {industryScript.usage_count>0?`· used ${industryScript.usage_count}×`:""}
                        </summary>
                        <div style={{fontSize:13,color:"#dee5ff",lineHeight:1.6,marginTop:10,
                          whiteSpace:"pre-wrap",fontFamily:"'Inter',sans-serif"}}>
                          {industryScript.body || industryScript.content || industryScript.script || ""}
                        </div>
                      </details>
                    )}
                    {lead.notes&&(
                      <div style={{marginTop:16,background:"#0f1930",borderRadius:12,padding:16}}>
                        <div style={{fontSize:"0.6rem",color:"#a3aac4",fontWeight:700,letterSpacing:".1em",
                          textTransform:"uppercase",marginBottom:8}}>Notes</div>
                        <div style={{fontSize:13,color:"#a3aac4",lineHeight:1.6}}>{lead.notes}</div>
                      </div>
                    )}
                  </div>
                )
              })()}
            </div>
          )}

          {/* ── QUALIFIED LEADS ──────────────────────────────────────────────── */}
          {activeNav==="qualified"&&(
            <div>
              <div style={{marginBottom:28,display:"flex",alignItems:"flex-end",
                justifyContent:"space-between",gap:20,flexWrap:"wrap"}}>
                <div>
                  <h1 style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:36,fontWeight:700,
                    color:"#dee5ff",letterSpacing:"-.02em",lineHeight:1.1}}>Qualified Leads</h1>
                  <p style={{color:"#a3aac4",fontSize:14,marginTop:4}}>
                    Calls with qualification data filled out
                  </p>
                </div>
                <div style={{display:"flex",gap:8,alignItems:"center"}}>
                  {qualifiedCalls.length>0&&(
                    <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:28,fontWeight:700,
                      color:"#69f6b8"}}>{qualifiedCalls.length}</span>
                  )}
                  <button className="btn btn-g" style={{fontSize:11,padding:"5px 12px"}}
                    onClick={()=>{
                      setQualLoading(true)
                      api("/api/calls/qualified").then(r=>setQualifiedCalls(Array.isArray(r)?r:[])).catch(()=>{}).finally(()=>setQualLoading(false))
                    }}>Refresh</button>
                </div>
              </div>

              {qualLoading?(
                <div style={{padding:60,textAlign:"center",color:"#40485d"}}>Loading...</div>
              ):qualifiedCalls.length===0?(
                <div style={{background:"#0f1930",borderRadius:16,padding:72,textAlign:"center"}}>
                  <div style={{fontSize:40,marginBottom:12}}>&#x1f4cb;</div>
                  <div style={{color:"#a3aac4",fontSize:14}}>No qualified leads yet</div>
                  <div style={{color:"#40485d",fontSize:12,marginTop:6}}>
                    Use the Qualify tab in the call modal to fill out qualification data
                  </div>
                </div>
              ):(
                <div style={{display:"flex",flexDirection:"column",gap:16}}>
                  {qualifiedCalls.map((call,i)=>{
                    const lead=call.leads||{}
                    const ac=avatarColor(lead.company||lead.firstName||call.calledBy||"?")
                    const review=call.admin_review
                    const reviewColor=review==="approved"?"#69f6b8":review==="rejected"?"#ff6e84":null
                    const qualColor=reviewColor||(call.qualified==="Hot"?"#ff6e84":call.qualified==="Warm"?"#ffe083":
                      call.qualified==="Not Yet"?"#a3a6ff":"#40485d")
                    const qualChips=[
                      {label:"Focus",val:call.budgetfocus},
                      {label:"Vendor",val:call.vendorstatus},
                      {label:"Contact",val:call.decisionmaker},
                      {label:"Timeline",val:call.timeline},
                      {label:"Qualified",val:call.qualified},
                    ].filter(c=>c.val)
                    const refresh=()=>{
                      api("/api/calls/qualified").then(r=>setQualifiedCalls(Array.isArray(r)?r:[])).catch(()=>{})
                    }
                    const setReview=async v=>{
                      try{ await api(`/api/calls/${call.id}/review`,{method:"PATCH",body:JSON.stringify({review:v})}); refresh() }
                      catch{ alert("Couldn't update review") }
                    }
                    const moveToFollowUp=async()=>{
                      // Future Follow-Ups tab filters callbackDate >= today+180d.
                      // Default to 180d so the lead lands where the user expects;
                      // shorter dates still save fine but show under regular Leads
                      // with status=callback (and on the Dashboard's callbacks-due card).
                      const d=prompt(
                        "Follow-up date (YYYY-MM-DD)\nTip: 180+ days from today shows in the Future Follow-Ups tab.",
                        addDays(180)
                      )
                      if(!d) return
                      try{
                        await api(`/api/calls/${call.id}/followup`,{method:"POST",body:JSON.stringify({date:d})})
                        const isFuture = d >= addDays(180)
                        notify(`✓ Follow-up scheduled for ${d}${isFuture?" — see Future Follow-Ups tab":""}`)
                        refresh()
                        loadLeads&&loadLeads()
                      }catch(ex){
                        notify("Couldn't move to follow-up — "+(ex.message||"try again"),"error")
                      }
                    }
                    const deleteCall=async()=>{
                      if(!confirm("Delete this qualification entry? The lead is not affected.")) return
                      try{ await api(`/api/calls/${call.id}`,{method:"DELETE"}); refresh() }
                      catch{ alert("Couldn't delete") }
                    }
                    return(
                      <div key={call.id||i} style={{background:"#0f1930",borderRadius:12,
                        borderLeft:`4px solid ${qualColor}`,overflow:"hidden"}}>
                        {/* Header row */}
                        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",
                          padding:"16px 20px",gap:16,flexWrap:"wrap"}}>
                          <div style={{display:"flex",alignItems:"center",gap:12,minWidth:0}}>
                            <div style={{width:38,height:38,borderRadius:"50%",background:ac+"22",flexShrink:0,
                              display:"flex",alignItems:"center",justifyContent:"center",
                              fontSize:12,fontWeight:700,color:ac,fontFamily:"'Space Grotesk',sans-serif"}}>
                              {(lead.company||lead.firstName||call.calledBy||"?").slice(0,2).toUpperCase()}
                            </div>
                            <div style={{minWidth:0}}>
                              <div style={{fontWeight:600,color:"#dee5ff",fontSize:15,
                                overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                                {[lead.firstName,lead.lastName].filter(Boolean).join(" ")||lead.company||"Unknown"}
                              </div>
                              <div style={{fontSize:12,color:"#a3aac4"}}>
                                {lead.company}{lead.industry?` \u00b7 ${lead.industry}`:""}{lead.city&&lead.state?` \u00b7 ${lead.city}, ${lead.state}`:lead.state?` \u00b7 ${lead.state}`:lead.city?` \u00b7 ${lead.city}`:""}
                              </div>
                            </div>
                          </div>
                          <div style={{display:"flex",alignItems:"center",gap:12,flexShrink:0}}>
                            {review&&(
                              <span className="pill" style={{background:reviewColor+"20",color:reviewColor,
                                border:`1px solid ${reviewColor}30`,fontSize:11,fontWeight:700,textTransform:"uppercase",letterSpacing:".05em"}}>
                                {review==="approved"?"\u2713 Approved":"\u2717 Rejected"}
                              </span>
                            )}
                            <span className="pill" style={{background:qualColor+"20",color:qualColor,
                              border:`1px solid ${qualColor}30`,fontSize:12,fontWeight:700}}>
                              {call.qualified||"Pending"}
                            </span>
                            <div style={{textAlign:"right"}}>
                              <div style={{fontSize:11,color:"#40485d"}}>
                                {call.calledBy} \u00b7 {call.calledAt?new Date(call.calledAt).toLocaleDateString():""}
                              </div>
                              {lead.phone&&(
                                <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:12,color:"#a3aac4"}}>{lead.phone}</div>
                              )}
                            </div>
                          </div>
                        </div>

                        {/* Qual chips */}
                        <div style={{padding:"0 20px 16px",display:"flex",gap:8,flexWrap:"wrap"}}>
                          {qualChips.map((c,j)=>(
                            <div key={j} style={{background:"#141f38",borderRadius:8,padding:"8px 14px",
                              display:"flex",flexDirection:"column",gap:2}}>
                              <span style={{fontSize:9,color:"#40485d",textTransform:"uppercase",
                                letterSpacing:".06em",fontWeight:700}}>{c.label}</span>
                              <span style={{fontSize:13,color:"#dee5ff",fontWeight:600}}>{c.val}</span>
                            </div>
                          ))}
                        </div>

                        {/* Notes if any */}
                        {call.notes&&(
                          <div style={{padding:"0 20px 16px",fontSize:12,color:"#a3aac4",
                            borderTop:"1px solid #40485d15",paddingTop:12,marginTop:0}}>
                            {call.notes}
                          </div>
                        )}

                        {/* Admin actions */}
                        {isAdmin()&&(
                          <div style={{padding:"12px 20px",display:"flex",gap:8,flexWrap:"wrap",
                            borderTop:"1px solid #40485d20",background:"#0c1528"}}>
                            <button className="btn" style={{fontSize:11,padding:"5px 12px",
                              background:review==="approved"?"#69f6b820":"transparent",
                              color:review==="approved"?"#69f6b8":"#a3aac4",
                              border:"1px solid "+(review==="approved"?"#69f6b860":"#40485d40")}}
                              onClick={()=>setReview(review==="approved"?null:"approved")}>
                              {review==="approved"?"✓ Approved":"Approve"}
                            </button>
                            <button className="btn" style={{fontSize:11,padding:"5px 12px",
                              background:review==="rejected"?"#ff6e8420":"transparent",
                              color:review==="rejected"?"#ff6e84":"#a3aac4",
                              border:"1px solid "+(review==="rejected"?"#ff6e8460":"#40485d40")}}
                              onClick={()=>setReview(review==="rejected"?null:"rejected")}>
                              {review==="rejected"?"✗ Rejected":"Reject"}
                            </button>
                            {lead.id&&(
                              <button className="btn" style={{fontSize:11,padding:"5px 12px",
                                background:"transparent",color:"#a3a6ff",border:"1px solid #a3a6ff40"}}
                                onClick={moveToFollowUp}>
                                Move to Follow-up
                              </button>
                            )}
                            <button className="btn" style={{fontSize:11,padding:"5px 12px",
                              background:"transparent",color:"#ff6e84",border:"1px solid #ff6e8440",marginLeft:"auto"}}
                              onClick={deleteCall}>
                              Delete
                            </button>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {/* ── FOLLOW-UPS (overdue + today + week + month + later + future) ── */}
          {activeNav==="followups"&&(()=>{
            const allFollowups = leads
              .filter(l=>l.callbackDate&&l.status!=="converted")
              .sort((a,b)=>a.callbackDate.localeCompare(b.callbackDate))

            const d7  = addDays(7)
            const d30 = addDays(30)
            const d180= addDays(180)
            const buckets = [
              {key:"overdue", label:"Overdue", color:"#ff6e84", icon:"⚠",
                desc:"Past due — these callbacks didn't happen yet",
                leads: allFollowups.filter(l=>l.callbackDate<today)},
              {key:"today",   label:"Today",   color:"#ffe083", icon:"📞",
                desc:"Call back today",
                leads: allFollowups.filter(l=>l.callbackDate===today)},
              {key:"week",    label:"This Week", color:"#69f6b8", icon:"🗓",
                desc:"Next 7 days",
                leads: allFollowups.filter(l=>l.callbackDate>today&&l.callbackDate<=d7)},
              {key:"month",   label:"Next 30 Days", color:"#a3a6ff", icon:"📅",
                desc:"Coming up — schedule outreach",
                leads: allFollowups.filter(l=>l.callbackDate>d7&&l.callbackDate<=d30)},
              {key:"later",   label:"Later (1 to 6 months)", color:"#8b5cf6", icon:"⏳",
                desc:"30 days to 6 months out",
                leads: allFollowups.filter(l=>l.callbackDate>d30&&l.callbackDate<d180)},
              {key:"future",  label:"Future (6+ months)", color:"#06d6a0", icon:"🌱",
                desc:"Long-tail — quarterly check-in candidates",
                leads: allFollowups.filter(l=>l.callbackDate>=d180)},
            ]

            function relativeTime(dateStr){
              const days=Math.round((new Date(dateStr)-new Date())/(1000*60*60*24))
              if(days<0) return Math.abs(days)+"d overdue"
              if(days===0) return "today"
              if(days===1) return "tomorrow"
              if(days<7) return `in ${days}d`
              if(days<30) return `in ${Math.round(days/7)}w`
              if(days<365) return `in ${Math.round(days/30)}mo`
              return `in ${Math.round(days/365)}y`
            }

            return(
              <div>
                <div style={{marginBottom:28,display:"flex",alignItems:"flex-end",
                  justifyContent:"space-between",gap:20,flexWrap:"wrap"}}>
                  <div>
                    <h1 style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:36,fontWeight:700,
                      color:"#dee5ff",letterSpacing:"-.02em",lineHeight:1.1}}>Follow-Ups</h1>
                    <p style={{color:"#a3aac4",fontSize:14,marginTop:4}}>
                      Every scheduled callback in one place — overdue, this week, future
                    </p>
                  </div>
                  {allFollowups.length>0&&(
                    <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:28,fontWeight:700,
                      color:"#a3a6ff"}}>{allFollowups.length}</div>
                  )}
                </div>

                {allFollowups.length===0?(
                  <div style={{background:"#0f1930",borderRadius:16,padding:72,textAlign:"center"}}>
                    <div style={{fontSize:40,marginBottom:12}}>&#x1f4c5;</div>
                    <div style={{color:"#a3aac4",fontSize:14}}>No follow-ups scheduled</div>
                    <div style={{color:"#40485d",fontSize:12,marginTop:6}}>
                      Log a call with outcome "Callback" + a date and it appears here
                    </div>
                  </div>
                ):(
                  <div style={{display:"flex",flexDirection:"column",gap:18}}>
                    {buckets.filter(b=>b.leads.length>0).map(b=>(
                      <div key={b.key} style={{background:"#0f1930",borderRadius:12,overflow:"hidden",
                        borderTop:`3px solid ${b.color}`}}>
                        <div style={{padding:"14px 24px",display:"flex",alignItems:"center",gap:12,
                          borderBottom:"1px solid #40485d20",background:`${b.color}08`,flexWrap:"wrap"}}>
                          <span style={{fontSize:18}}>{b.icon}</span>
                          <span style={{fontSize:13,fontWeight:700,color:b.color,letterSpacing:".05em",textTransform:"uppercase"}}>
                            {b.label}
                          </span>
                          <span style={{fontSize:12,color:b.color,background:`${b.color}25`,
                            padding:"2px 10px",borderRadius:10,fontWeight:700}}>
                            {b.leads.length}
                          </span>
                          <span style={{fontSize:11,color:"#a3aac4",marginLeft:6}}>{b.desc}</span>
                        </div>
                        {b.leads.map(lead=>{
                          const ac=avatarColor(lead.company||lead.firstName||"?")
                          const info=STATUS_OPTIONS.find(s=>s.value===lead.status)||STATUS_OPTIONS[0]
                          return(
                            <div key={lead.id}
                              style={{display:"grid",gridTemplateColumns:"3fr 2fr 1fr 2fr 2fr",
                                padding:"14px 24px",alignItems:"center",gap:16,
                                borderBottom:"1px solid #40485d10",transition:"background .12s"}}
                              onMouseEnter={e=>e.currentTarget.style.background="#192540"}
                              onMouseLeave={e=>e.currentTarget.style.background="transparent"}>
                              <div style={{display:"flex",alignItems:"center",gap:12,minWidth:0}}>
                                <div style={{width:36,height:36,borderRadius:"50%",background:ac+"22",flexShrink:0,
                                  display:"flex",alignItems:"center",justifyContent:"center",
                                  fontSize:12,fontWeight:700,color:ac,fontFamily:"'Space Grotesk',sans-serif"}}>
                                  {getInitials(lead)}</div>
                                <div style={{minWidth:0}}>
                                  <div style={{fontWeight:600,color:"#dee5ff",fontSize:14,
                                    overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                                    {[lead.firstName,lead.lastName].filter(Boolean).join(" ")||lead.company}</div>
                                  <div style={{fontSize:12,color:"#a3aac4",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                                    {lead.title?`${lead.title} · `:""}{lead.company||""}
                                    {lead.city&&lead.state?` · ${lead.city}, ${lead.state}`:lead.state?` · ${lead.state}`:""}
                                  </div>
                                </div>
                              </div>
                              <div>
                                <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:13,fontWeight:700,color:b.color}}>
                                  {lead.callbackDate}
                                </div>
                                <div style={{fontSize:11,color:"#40485d",marginTop:2}}>
                                  {relativeTime(lead.callbackDate)}
                                </div>
                              </div>
                              <span className="pill" style={{background:info.color+"20",color:info.color,
                                border:`1px solid ${info.color}30`,width:"fit-content"}}>{info.label}</span>
                              <div style={{fontSize:12,color:"#a3aac4",overflow:"hidden",
                                display:"-webkit-box",WebkitLineClamp:2,WebkitBoxOrient:"vertical"}}>
                                {lead.phone?<><span style={{color:"#a3a6ff"}}>📞</span> {lead.phone}<br/></>:""}
                                {lead.notes||""}
                              </div>
                              <div style={{display:"flex",gap:6,justifyContent:"flex-end",flexWrap:"wrap"}}>
                                <button className="btn btn-g"
                                  style={{fontSize:11,padding:"6px 12px",whiteSpace:"nowrap"}}
                                  onClick={async()=>{
                                    const d=window.prompt(
                                      `Reschedule follow-up for ${lead.company||lead.firstName}? (YYYY-MM-DD)`,
                                      addDays(7)
                                    )
                                    if(d&&d.match(/^\d{4}-\d{2}-\d{2}$/)){
                                      await api(`/api/leads/${lead.id}`,{method:"PATCH",body:JSON.stringify({
                                        callbackDate:d,status:"callback",updatedAt:new Date().toISOString()})})
                                      setLeads(p=>p.map(l=>l.id===lead.id?{...l,callbackDate:d,status:"callback"}:l))
                                      notify("Follow-up moved to "+d)
                                    }
                                  }}>
                                  Reschedule
                                </button>
                                <IconBtn onClick={()=>setCallModal(lead)} title="Log call"><IconPhone/></IconBtn>
                                <IconBtn onClick={()=>setEditModal(lead)} title="Edit"><IconEdit/></IconBtn>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })()}

          {/* ── EMAIL CAMPAIGNS (full page, top-level nav) ─────────────────── */}
          {activeNav==="campaigns"&&<CampaignsPage/>}

          {/* ── ANALYTICS ───────────────────────────────────────────────────── */}
          {activeNav==="analytics"&&(
            <div>
              <div style={{marginBottom:32}}>
                <h1 style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:36,fontWeight:700,
                  color:"#dee5ff",letterSpacing:"-.02em"}}>Analytics</h1>
                <p style={{color:"#a3aac4",fontSize:14,marginTop:4}}>
                  Team performance · {leads.length} lead{leads.length!==1?"s":""} in pipeline
                </p>
              </div>
              <StatsBar stats={stats} onCallbacks={()=>{setNav("leads");setCbOnly(true)}}/>

              {/* ── Leaderboard ── */}
              <div style={{background:"#0f1930",borderRadius:16,overflow:"hidden",marginTop:28}}>
                <div style={{padding:"18px 24px",borderBottom:"1px solid #40485d20",
                  display:"flex",alignItems:"center",justifyContent:"space-between"}}>
                  <div style={{fontSize:"0.6rem",color:"#ffe083",fontWeight:700,letterSpacing:".1em",textTransform:"uppercase"}}>
                    🏆 Rep Leaderboard — {{today:"Today","7d":"Last 7 Days","30d":"Last 30 Days",all:"All Time"}[lbRange]}
                  </div>
                  <div style={{display:"flex",gap:4,alignItems:"center"}}>
                    {["today","7d","30d","all"].map(r=>(
                      <button key={r} className="btn btn-g" style={{fontSize:10,padding:"4px 10px",
                        background:lbRange===r?"#a3a6ff":"transparent",color:lbRange===r?"#000011":"#a3aac4",
                        border:lbRange===r?"none":"1px solid #40485d50"}}
                        onClick={()=>setLbRange(r)}>
                        {r==="today"?"Today":r==="7d"?"7D":r==="30d"?"30D":"All"}
                      </button>
                    ))}
                    <button className="btn btn-g" style={{fontSize:11,padding:"5px 12px",marginLeft:4}}
                      onClick={()=>{
                        setLbLoading(true)
                        api(`/api/leaderboard?range=${lbRange}`).then(r=>setLeaderboard(Array.isArray(r)?r:[])).catch(()=>{}).finally(()=>setLbLoading(false))
                      }}>Refresh</button>
                  </div>
                </div>
                {lbLoading?(
                  <div style={{padding:48,textAlign:"center",color:"#40485d"}}>Loading…</div>
                ):leaderboard.length===0?(
                  <div style={{padding:48,textAlign:"center",color:"#40485d",fontSize:13}}>
                    No activity yet — reps will appear here when they sign in or log calls
                  </div>
                ):(
                  <>
                    {/* Header row */}
                    <div style={{display:"grid",gridTemplateColumns:"2fr 1.2fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr",
                      padding:"9px 24px",fontSize:"0.5rem",fontWeight:700,color:"#a3aac4",
                      textTransform:"uppercase",letterSpacing:".08em",borderBottom:"1px solid #40485d15"}}>
                      <div>Rep</div>
                      <div style={{textAlign:"center"}}>Sign-In</div>
                      <div style={{textAlign:"center"}}>Calls</div>
                      <div style={{textAlign:"center"}}>Leads Added</div>
                      <div style={{textAlign:"center"}}>Converted</div>
                      <div style={{textAlign:"center"}}>Interested</div>
                      <div style={{textAlign:"center"}}>Conv %</div>
                      <div style={{textAlign:"center"}}>Contact %</div>
                      <div style={{textAlign:"center"}}>No Answer</div>
                      <div style={{textAlign:"center"}}>Callbacks</div>
                      <div style={{textAlign:"center"}}>Revenue</div>
                      <div style={{textAlign:"center"}}>Sessions</div>
                    </div>
                    {leaderboard.map((rep,i)=>{
                      const medal=i===0?"🥇":i===1?"🥈":i===2?"🥉":null
                      const ac=avatarColor(rep.name)
                      const convPct=parseFloat(rep.conv_rate)||0
                      const isExpanded=expandedCaller===rep.name
                      return(
                        <div key={rep.name}>
                        <div
                          style={{display:"grid",gridTemplateColumns:"2fr 1.2fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr",
                            padding:"14px 24px",alignItems:"center",
                            borderBottom:isExpanded?"none":"1px solid #40485d10",
                            background:isExpanded?"#192540":i===0?"#ffe08306":i===1?"#ffffff04":"transparent",
                            transition:"background .12s",cursor:isAdmin()?"pointer":"default"}}
                          onMouseEnter={e=>{if(!isExpanded)e.currentTarget.style.background="#192540"}}
                          onMouseLeave={e=>{if(!isExpanded)e.currentTarget.style.background=i===0?"#ffe08306":i===1?"#ffffff04":"transparent"}}
                          onClick={()=>{
                            if(!isAdmin()) return
                            if(isExpanded){setExpandedCaller(null);setCallerDetail(null);return}
                            setExpandedCaller(rep.name)
                            setCallerDetailLoading(true)
                            setCallerDetail(null)
                            setCallerDetailDate("")
                            setCallerDetailDateTo("")
                            setCallerSearch("")
                            setCallerFilter("")
                            api(`/api/caller/${encodeURIComponent(rep.name)}/detail`)
                              .then(r=>{setCallerDetail(r)})
                              .catch(()=>{notify("Failed to load caller details","error")})
                              .finally(()=>setCallerDetailLoading(false))
                          }}>
                          {/* Rep name */}
                          <div style={{display:"flex",alignItems:"center",gap:12}}>
                            <div style={{width:34,height:34,borderRadius:"50%",background:ac+"22",flexShrink:0,
                              display:"flex",alignItems:"center",justifyContent:"center",
                              fontSize:12,fontWeight:700,color:ac,fontFamily:"'Space Grotesk',sans-serif"}}>
                              {rep.name.slice(0,2).toUpperCase()}
                            </div>
                            <div>
                              <div style={{fontWeight:600,color:"#dee5ff",fontSize:14,display:"flex",alignItems:"center",gap:6}}>
                                {medal&&<span>{medal}</span>}{rep.name}
                                {isAdmin()&&<span style={{fontSize:10,color:"#40485d",marginLeft:4}}>{isExpanded?"▾":"▸"}</span>}
                                {isAdmin()&&rep.flags&&rep.flags.length>0&&(
                                  <span title={rep.flags.join(", ")} style={{fontSize:10,padding:"1px 6px",borderRadius:8,
                                    background:"#ff6e8420",color:"#ff6e84",border:"1px solid #ff6e8430",marginLeft:4}}>
                                    {rep.flags.length} flag{rep.flags.length>1?"s":""}
                                  </span>
                                )}
                              </div>
                              {rep.leads_assigned>0&&(
                                <div style={{fontSize:11,color:"#40485d"}}>{rep.leads_assigned} lead{rep.leads_assigned!==1?"s":""} assigned</div>
                              )}
                            </div>
                          </div>
                          {/* Sign-in time */}
                          <div style={{textAlign:"center"}}>
                            {rep.signed_in_at?(
                              <div>
                                <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:12,color:"#a3aac4"}}>
                                  {new Date(rep.signed_in_at).toLocaleTimeString([],{hour:"numeric",minute:"2-digit"})}
                                </div>
                                {rep.signed_out_at&&(
                                  <div style={{fontSize:10,color:"#40485d"}}>
                                    out {new Date(rep.signed_out_at).toLocaleTimeString([],{hour:"numeric",minute:"2-digit"})}
                                  </div>
                                )}
                              </div>
                            ):(
                              <span style={{fontSize:12,color:"#40485d"}}>—</span>
                            )}
                          </div>
                          {/* Calls */}
                          <div style={{textAlign:"center"}}>
                            <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:18,fontWeight:700,
                              color:rep.total_calls>0?"#a3a6ff":"#40485d"}}>{rep.total_calls}</span>
                            {rep.calls_today>0&&rep.calls_today!==rep.total_calls&&(
                              <div style={{fontSize:10,color:"#40485d"}}>{rep.calls_today} today</div>
                            )}
                          </div>
                          {/* Leads Added */}
                          <div style={{textAlign:"center"}}>
                            <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:14,fontWeight:700,
                              color:rep.leads_populated>0?"#8b5cf6":"#40485d"}}>{rep.leads_populated||0}</span>
                          </div>
                          {/* Converted */}
                          <div style={{textAlign:"center"}}>
                            <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:14,fontWeight:700,
                              color:rep.conversions>0?"#69f6b8":"#40485d"}}>{rep.conversions}</span>
                          </div>
                          {/* Interested */}
                          <div style={{textAlign:"center"}}>
                            <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:14,fontWeight:600,
                              color:rep.interested>0?"#ffe083":"#40485d"}}>{rep.interested}</span>
                          </div>
                          {/* Conv rate */}
                          <div style={{textAlign:"center"}}>
                            <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:13,fontWeight:700,
                              color:convPct>=10?"#69f6b8":convPct>=5?"#ffe083":"#a3aac4"}}>
                              {rep.conv_rate}%
                            </span>
                          </div>
                          {/* Contact rate */}
                          <div style={{textAlign:"center"}}>
                            <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:13,fontWeight:700,
                              color:parseFloat(rep.contact_rate)>=50?"#8b5cf6":"#a3aac4"}}>{rep.contact_rate}%</span>
                          </div>
                          {/* No answer */}
                          <div style={{textAlign:"center"}}>
                            <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:13,color:"#40485d"}}>{rep.no_answer}</span>
                          </div>
                          {/* Callbacks */}
                          <div style={{textAlign:"center"}}>
                            <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:13,
                              color:rep.callbacks>0?"#8b5cf6":"#40485d"}}>{rep.callbacks}</span>
                          </div>
                          {/* Revenue */}
                          <div style={{textAlign:"center"}}>
                            <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:13,fontWeight:700,
                              color:rep.revenue>0?"#69f6b8":"#40485d"}}>
                              {rep.revenue>0?`$${rep.revenue.toLocaleString()}`:"—"}
                            </span>
                          </div>
                          {/* Sessions */}
                          <div style={{textAlign:"center"}}>
                            <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:13,
                              color:rep.sessions>0?"#a3aac4":"#40485d"}}>{rep.sessions||0}</span>
                          </div>
                        </div>
                        {/* ── Expanded Caller Detail Panel ── */}
                        {isAdmin()&&isExpanded&&(
                          <div style={{background:"#141f38",borderBottom:"1px solid #40485d20",padding:"20px 24px"}}
                            onClick={e=>e.stopPropagation()}>
                            {callerDetailLoading?(
                              <div style={{textAlign:"center",color:"#40485d",padding:24,fontSize:13}}>Loading caller details…</div>
                            ):callerDetail?(
                              <div>
                                {/* Date picker */}
                                <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:16,flexWrap:"wrap"}}>
                                  <span style={{fontSize:11,color:"#a3aac4",fontWeight:600}}>Date:</span>
                                  <input type="date" value={callerDetailDate||callerDetail.date}
                                    onChange={e=>setCallerDetailDate(e.target.value)}
                                    style={{background:"#0f1930",border:"1px solid #40485d30",borderRadius:8,padding:"5px 10px",
                                      color:"#dee5ff",fontSize:12,fontFamily:"'Space Grotesk',sans-serif"}}/>
                                  <span style={{fontSize:11,color:"#40485d"}}>to</span>
                                  <input type="date" value={callerDetailDateTo||(callerDetailDate||callerDetail.date)}
                                    onChange={e=>setCallerDetailDateTo(e.target.value)}
                                    style={{background:"#0f1930",border:"1px solid #40485d30",borderRadius:8,padding:"5px 10px",
                                      color:"#dee5ff",fontSize:12,fontFamily:"'Space Grotesk',sans-serif"}}/>
                                  <button className="btn btn-g" style={{fontSize:11,padding:"5px 12px"}}
                                    onClick={e=>{
                                      e.stopPropagation()
                                      const d=callerDetailDate||callerDetail.date
                                      const d2=callerDetailDateTo||d
                                      setCallerDetailLoading(true)
                                      api(`/api/caller/${encodeURIComponent(expandedCaller)}/detail?date=${d}&date_to=${d2}`)
                                        .then(r=>setCallerDetail(r))
                                        .catch(()=>notify("Failed to load","error"))
                                        .finally(()=>setCallerDetailLoading(false))
                                    }}>Load</button>
                                  {[{label:"Today",d:new Date().toISOString().slice(0,10),d2:""},
                                    {label:"Yesterday",d:new Date(Date.now()-864e5).toISOString().slice(0,10),d2:""},
                                    {label:"Last 7d",d:new Date(Date.now()-7*864e5).toISOString().slice(0,10),d2:new Date().toISOString().slice(0,10)},
                                    {label:"Last 30d",d:new Date(Date.now()-30*864e5).toISOString().slice(0,10),d2:new Date().toISOString().slice(0,10)},
                                  ].map(p=>(
                                    <button key={p.label} className="btn btn-g" style={{fontSize:10,padding:"4px 10px"}}
                                      onClick={e=>{
                                        e.stopPropagation()
                                        setCallerDetailDate(p.d)
                                        setCallerDetailDateTo(p.d2)
                                        setCallerDetailLoading(true)
                                        api(`/api/caller/${encodeURIComponent(expandedCaller)}/detail?date=${p.d}${p.d2?`&date_to=${p.d2}`:""}`)
                                          .then(r=>setCallerDetail(r))
                                          .catch(()=>notify("Failed to load","error"))
                                          .finally(()=>setCallerDetailLoading(false))
                                      }}>{p.label}</button>
                                  ))}
                                  <span style={{fontSize:11,color:"#40485d",marginLeft:8}}>
                                    Showing: {callerDetail.date}{callerDetail.date !== (callerDetailDateTo||callerDetail.date) ? ` → ${callerDetailDateTo}` : ""}
                                    {" · "}{callerDetail.total_calls} call{callerDetail.total_calls!==1?"s":""}
                                  </span>
                                </div>
                                {/* Summary cards */}
                                <div style={{display:"grid",gridTemplateColumns:"repeat(6,1fr)",gap:12,marginBottom:20}}>
                                  {[
                                    {label:"Calls Today",val:callerDetail.total_calls,color:"#a3a6ff"},
                                    {label:"Answered",val:callerDetail.breakdown.answered,color:"#69f6b8"},
                                    {label:"No Answer",val:callerDetail.breakdown.no_answer,color:"#a3aac4"},
                                    {label:"Voicemail",val:callerDetail.breakdown.voicemail,color:"#ffe083"},
                                    {label:"Qualified",val:callerDetail.qualified_count,color:"#8b5cf6"},
                                    {label:"Avg Talk",val:`${callerDetail.avg_talk_time}s`,color:"#a3aac4"},
                                  ].map(c=>(
                                    <div key={c.label} style={{background:"#0f1930",borderRadius:10,padding:"12px 14px",textAlign:"center"}}>
                                      <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:22,fontWeight:700,color:c.color}}>
                                        {c.val}
                                      </div>
                                      <div style={{fontSize:10,color:"#40485d",marginTop:2,textTransform:"uppercase",letterSpacing:".06em"}}>
                                        {c.label}
                                      </div>
                                    </div>
                                  ))}
                                </div>
                                {/* Outcome breakdown bar */}
                                <div style={{marginBottom:16}}>
                                  <div style={{fontSize:10,color:"#a3aac4",fontWeight:700,textTransform:"uppercase",
                                    letterSpacing:".08em",marginBottom:8}}>Outcome Breakdown</div>
                                  {callerDetail.total_calls>0?(
                                    <div style={{display:"flex",height:8,borderRadius:4,overflow:"hidden",background:"#0f1930"}}>
                                      {[
                                        {key:"answered",color:"#69f6b8"},
                                        {key:"interested",color:"#ffe083"},
                                        {key:"converted",color:"#06d6a0"},
                                        {key:"callback",color:"#8b5cf6"},
                                        {key:"not_interested",color:"#ff6e84"},
                                        {key:"voicemail",color:"#a3aac4"},
                                        {key:"no_answer",color:"#40485d"},
                                      ].map(o=>{
                                        const pct=(callerDetail.breakdown[o.key]||0)/callerDetail.total_calls*100
                                        return pct>0?<div key={o.key} title={`${o.key}: ${callerDetail.breakdown[o.key]}`}
                                          style={{width:`${pct}%`,background:o.color,minWidth:pct>0?2:0}}/>:null
                                      })}
                                    </div>
                                  ):(
                                    <div style={{fontSize:12,color:"#40485d"}}>No calls yet today</div>
                                  )}
                                  {callerDetail.total_calls>0&&(
                                    <div style={{display:"flex",gap:12,marginTop:6,flexWrap:"wrap"}}>
                                      {[
                                        {key:"answered",label:"Answered",color:"#69f6b8"},
                                        {key:"interested",label:"Interested",color:"#ffe083"},
                                        {key:"converted",label:"Converted",color:"#06d6a0"},
                                        {key:"callback",label:"Callback",color:"#8b5cf6"},
                                        {key:"not_interested",label:"Not Int.",color:"#ff6e84"},
                                        {key:"voicemail",label:"VM",color:"#a3aac4"},
                                        {key:"no_answer",label:"No Ans.",color:"#40485d"},
                                      ].filter(o=>(callerDetail.breakdown[o.key]||0)>0).map(o=>(
                                        <span key={o.key} style={{fontSize:10,color:o.color}}>
                                          ● {o.label}: {callerDetail.breakdown[o.key]}
                                        </span>
                                      ))}
                                    </div>
                                  )}
                                </div>
                                {/* Call list */}
                                {callerDetail.calls.length>0&&(
                                  <div>
                                    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:8,gap:8,flexWrap:"wrap"}}>
                                      <div style={{display:"flex",alignItems:"center",gap:8}}>
                                        <div style={{fontSize:10,color:"#a3aac4",fontWeight:700,textTransform:"uppercase",
                                          letterSpacing:".08em"}}>Call Log</div>
                                        {[
                                          {label:"All",value:""},
                                          {label:"Has Notes",value:"notes"},
                                          {label:"Interested",value:"interested"},
                                          {label:"Callbacks",value:"callback"},
                                          {label:"Converted",value:"converted"},
                                        ].map(f=>(
                                          <button key={f.value} onClick={e=>{e.stopPropagation();setCallerFilter(f.value)}}
                                            style={{fontSize:10,padding:"3px 8px",borderRadius:6,border:"none",cursor:"pointer",
                                              background:(callerFilter||"")===(f.value)?"#a3a6ff20":"transparent",
                                              color:(callerFilter||"")===(f.value)?"#a3a6ff":"#40485d",
                                              fontFamily:"'Space Grotesk',sans-serif",fontWeight:600}}>
                                            {f.label}
                                            {f.value==="notes"&&callerDetail?` (${callerDetail.calls.filter(c=>c.notes).length})`:
                                             f.value&&callerDetail?` (${callerDetail.calls.filter(c=>c.outcome===f.value).length})`:""}
                                          </button>
                                        ))}
                                      </div>
                                      <input placeholder="Search company, notes..." value={callerSearch||""}
                                        onChange={e=>setCallerSearch(e.target.value)}
                                        onClick={e=>e.stopPropagation()}
                                        style={{background:"#0f1930",border:"1px solid #40485d30",borderRadius:8,padding:"5px 10px",
                                          color:"#dee5ff",fontSize:11,width:200,fontFamily:"inherit"}}/>
                                    </div>
                                    <div style={{maxHeight:400,overflowY:"auto",borderRadius:8,border:"1px solid #40485d15"}}>
                                      <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
                                        <thead>
                                          <tr style={{background:"#0f1930",position:"sticky",top:0}}>
                                            <th style={{padding:"8px 12px",textAlign:"left",color:"#a3aac4",fontWeight:600,fontSize:10}}>Time</th>
                                            <th style={{padding:"8px 12px",textAlign:"left",color:"#a3aac4",fontWeight:600,fontSize:10}}>Company / Lead</th>
                                            <th style={{padding:"8px 12px",textAlign:"left",color:"#a3aac4",fontWeight:600,fontSize:10}}>Outcome</th>
                                            <th style={{padding:"8px 12px",textAlign:"left",color:"#a3aac4",fontWeight:600,fontSize:10}}>Notes</th>
                                            <th style={{padding:"8px 12px",textAlign:"right",color:"#a3aac4",fontWeight:600,fontSize:10}}>Duration</th>
                                          </tr>
                                        </thead>
                                        <tbody>
                                          {callerDetail.calls
                                            .filter(c=>{
                                              if(callerFilter==="notes"&&!c.notes) return false
                                              if(callerFilter&&callerFilter!=="notes"&&c.outcome!==callerFilter) return false
                                              if(!callerSearch) return true
                                              const q=callerSearch.toLowerCase()
                                              return (c.lead_company||"").toLowerCase().includes(q)
                                                || (c.lead_name||"").toLowerCase().includes(q)
                                                || (c.notes||"").toLowerCase().includes(q)
                                                || (c.outcome||"").toLowerCase().includes(q)
                                                || (c.lead_phone||"").includes(q)
                                            })
                                            .map((c,ci)=>{
                                            const outcomeColors={answered:"#69f6b8",interested:"#ffe083",converted:"#06d6a0",
                                              callback:"#8b5cf6",not_interested:"#ff6e84",voicemail:"#a3aac4",no_answer:"#40485d"}
                                            return(
                                              <tr key={c.id||ci} style={{borderBottom:"1px solid #40485d10"}}>
                                                <td style={{padding:"6px 12px",color:"#a3aac4",fontFamily:"'Space Grotesk',sans-serif",whiteSpace:"nowrap"}}>
                                                  {c.calledAt?new Date(c.calledAt).toLocaleTimeString([],{hour:"numeric",minute:"2-digit"}):"—"}
                                                </td>
                                                <td style={{padding:"6px 12px",color:"#dee5ff"}}>
                                                  <div>{c.lead_company||"Unknown"}{c.lead_name?` · ${c.lead_name}`:""}</div>
                                                  {c.lead_phone&&<div style={{fontSize:10,color:"#40485d"}}>{c.lead_phone}</div>}
                                                </td>
                                                <td style={{padding:"6px 12px"}}>
                                                  <span style={{fontSize:11,padding:"2px 8px",borderRadius:8,fontWeight:600,
                                                    background:(outcomeColors[c.outcome]||"#40485d")+"18",
                                                    color:outcomeColors[c.outcome]||"#40485d"}}>
                                                    {c.outcome||"—"}
                                                  </span>
                                                </td>
                                                <td style={{padding:"6px 12px",color:"#a3aac4",maxWidth:250,fontSize:11,lineHeight:1.4}}>
                                                  {c.notes||<span style={{color:"#2a2f3d"}}>—</span>}
                                                </td>
                                                <td style={{padding:"6px 12px",textAlign:"right",color:"#a3aac4",
                                                  fontFamily:"'Space Grotesk',sans-serif"}}>
                                                  {c.duration?`${c.duration}s`:"—"}
                                                </td>
                                              </tr>
                                            )
                                          })}
                                        </tbody>
                                      </table>
                                    </div>
                                  </div>
                                )}
                              </div>
                            ):(
                              <div style={{textAlign:"center",color:"#40485d",padding:24,fontSize:13}}>No data available</div>
                            )}
                          </div>
                        )}
                        </div>
                      )
                    })}
                  </>
                )}
              </div>

              {/* ── Status + Industry ── */}
              <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:24,marginTop:24}}>
                <div style={{background:"#0f1930",borderRadius:16,padding:24}}>
                  <div style={{fontSize:"0.6rem",color:"#a3aac4",fontWeight:700,letterSpacing:".1em",
                    textTransform:"uppercase",marginBottom:20}}>Status Distribution</div>
                  {STATUS_OPTIONS.map(st=>{
                    const count=leads.filter(l=>l.status===st.value).length
                    const pct=leads.length?Math.round(count/leads.length*100):0
                    return(
                      <div key={st.value} style={{marginBottom:14}}>
                        <div style={{display:"flex",justifyContent:"space-between",marginBottom:5}}>
                          <span style={{fontSize:13,color:"#dee5ff"}}>{st.label}</span>
                          <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:13,fontWeight:700,color:st.color}}>{count}</span>
                        </div>
                        <div style={{height:6,background:"#141f38",borderRadius:3,overflow:"hidden"}}>
                          <div style={{height:"100%",width:`${pct}%`,background:st.color,borderRadius:3}}/>
                        </div>
                      </div>
                    )
                  })}
                </div>
                <div style={{background:"#0f1930",borderRadius:16,padding:24}}>
                  <div style={{fontSize:"0.6rem",color:"#a3aac4",fontWeight:700,letterSpacing:".1em",
                    textTransform:"uppercase",marginBottom:20}}>Top Industries</div>
                  {Object.entries(
                    leads.reduce((acc,l)=>{if(l.industry)acc[l.industry]=(acc[l.industry]||0)+1;return acc},{})
                  ).sort((a,b)=>b[1]-a[1]).slice(0,8).map(([ind,count])=>{
                    const pct=leads.length?Math.round(count/leads.length*100):0
                    return(
                      <div key={ind} style={{marginBottom:14}}>
                        <div style={{display:"flex",justifyContent:"space-between",marginBottom:5}}>
                          <span style={{fontSize:13,color:"#dee5ff",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{ind}</span>
                          <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:13,fontWeight:700,
                            color:"#a3a6ff",flexShrink:0,marginLeft:8}}>{count}</span>
                        </div>
                        <div style={{height:6,background:"#141f38",borderRadius:3,overflow:"hidden"}}>
                          <div style={{height:"100%",width:`${pct}%`,background:"#a3a6ff",borderRadius:3}}/>
                        </div>
                      </div>
                    )
                  })}
                  {leads.every(l=>!l.industry)&&(
                    <div style={{color:"#40485d",fontSize:13,textAlign:"center",paddingTop:20}}>No industry data yet</div>
                  )}
                </div>
              </div>

              {/* ── Score tiers ── */}
              <div style={{background:"#0f1930",borderRadius:16,padding:24,marginTop:24}}>
                <div style={{fontSize:"0.6rem",color:"#a3aac4",fontWeight:700,letterSpacing:".1em",
                  textTransform:"uppercase",marginBottom:20}}>Lead Score Tiers</div>
                <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:16}}>
                  {[
                    {label:"Hot",  min:75,max:100,color:"#ff6e84"},
                    {label:"Warm", min:50,max:74, color:"#ffe083"},
                    {label:"Cool", min:25,max:49, color:"#a3a6ff"},
                    {label:"Cold", min:0, max:24, color:"#40485d"},
                  ].map(tier=>{
                    const count=leads.filter(l=>{const s=l.score||scoreLead(l)||0;return s>=tier.min&&s<=tier.max}).length
                    return(
                      <div key={tier.label} style={{background:"#141f38",borderRadius:12,padding:18,
                        textAlign:"center",borderLeft:`4px solid ${tier.color}`}}>
                        <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:32,fontWeight:700,
                          color:tier.color,marginBottom:4}}>{count}</div>
                        <div style={{fontSize:12,color:"#a3aac4"}}>{tier.label} leads</div>
                      </div>
                    )
                  })}
                </div>
              </div>

              {/* ── Google Places Usage + Leads Pulled (admin only) ── */}
              {isAdmin()&&<UsageDashboard/>}

              {/* ── Login Activity (admin only) ── */}
              {isAdmin()&&<LoginActivityPanel/>}

              {/* Campaign Activity now lives on its own top-level "Email Campaigns"
                  nav tab — promoted from a collapsible admin panel because email
                  reply detection is a primary feature. Quick-glance summary card
                  rendered above on the Dashboard. */}

              {/* ── Lead Cleanup (admin only) ── */}
              {isAdmin()&&<LeadCleanupPanel onCleanup={loadLeads}/>}

              {/* ── Audit Log (admin only) ── */}
              {isAdmin()&&<AuditLogPanel/>}

              {/* ── Connectivity Heatmap (admin only) ── */}
              {isAdmin()&&<ConnectivityPanel/>}

              {/* ── Insights: Apollo ROI + Touch-count + Stale callbacks (admin only) ── */}
              {isAdmin()&&<InsightsPanel/>}

              {/* ── Team Management (admin only) ── */}
              {isAdmin()&&<div style={{background:"#0f1930",borderRadius:16,overflow:"hidden",marginTop:24}}>
                <div style={{padding:"18px 24px",borderBottom:"1px solid #40485d20",
                  display:"flex",alignItems:"center",justifyContent:"space-between"}}>
                  <div style={{fontSize:"0.6rem",color:"#a3aac4",fontWeight:700,letterSpacing:".1em",textTransform:"uppercase"}}>
                    Team Management <span style={{color:"#ff6e84",fontSize:9,marginLeft:6}}>ADMIN</span>
                  </div>
                  <button className="btn btn-g" style={{fontSize:11,padding:"5px 12px"}}
                    onClick={()=>{
                      api("/api/reps").then(r=>{
                        if(Array.isArray(r)) setLeaderboard(prev=>{
                          // Store reps data in a temp state via leaderboard refresh
                          window.__lf_reps=r; return prev
                        })
                        // Force re-render
                        setLbLoading(l=>!l); setTimeout(()=>setLbLoading(l=>!l),50)
                      }).catch(()=>{})
                    }}>Load Reps</button>
                </div>
                {(()=>{
                  const reps=window.__lf_reps||[]
                  if(!reps.length) return(
                    <div style={{padding:32,textAlign:"center",color:"#40485d",fontSize:13}}>
                      Click "Load Reps" to see team status
                    </div>
                  )
                  return(
                    <div>
                      {reps.map(rep=>{
                        const ac=avatarColor(rep.name)
                        const statusColor=rep.status==="active"?"#69f6b8":rep.status==="idle"?"#ffe083":"#ff6e84"
                        return(
                          <div key={rep.name} style={{padding:"14px 24px",borderBottom:"1px solid #40485d10",
                            display:"flex",alignItems:"center",gap:14}}>
                            <div style={{width:36,height:36,borderRadius:"50%",background:ac+"22",flexShrink:0,
                              display:"flex",alignItems:"center",justifyContent:"center",
                              fontSize:12,fontWeight:700,color:ac,fontFamily:"'Space Grotesk',sans-serif"}}>
                              {rep.name.slice(0,2).toUpperCase()}
                            </div>
                            <div style={{flex:1,minWidth:0}}>
                              <div style={{fontWeight:600,color:"#dee5ff",fontSize:14,display:"flex",alignItems:"center",gap:8}}>
                                {rep.name}
                                <span style={{fontSize:10,padding:"2px 8px",borderRadius:10,fontWeight:700,
                                  background:statusColor+"20",color:statusColor,border:`1px solid ${statusColor}30`}}>
                                  {rep.status}
                                </span>
                              </div>
                              <div style={{fontSize:12,color:"#a3aac4"}}>
                                {rep.leads} lead{rep.leads!==1?"s":""} assigned
                                {rep.last_call?` \u00b7 last call ${new Date(rep.last_call).toLocaleDateString()} at ${new Date(rep.last_call).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}`:" \u00b7 no calls logged"}
                                {rep.days_inactive>3&&(
                                  <span style={{color:"#ff6e84",marginLeft:6}}>({rep.days_inactive}d inactive)</span>
                                )}
                              </div>
                            </div>
                            <div style={{display:"flex",gap:6,flexShrink:0}}>
                              <button className="btn btn-g" style={{fontSize:11,padding:"5px 10px"}}
                                onClick={async()=>{
                                  const v=window.prompt(`Set daily quota for ${rep.name}:`,60)
                                  if(!v) return
                                  const n=parseInt(v)
                                  if(isNaN(n)||n<1||n>500){alert("Must be 1-500");return}
                                  try{
                                    await api("/api/quota",{method:"PUT",body:JSON.stringify({quota:n,caller:rep.name})})
                                    notify(`${rep.name}'s quota set to ${n} calls/day`)
                                  }catch(e){notify("Error: "+e.message,"error")}
                                }}>Set Quota</button>
                              <button className="btn btn-g" style={{fontSize:11,padding:"5px 10px"}}
                                onClick={async()=>{
                                  const to=window.prompt(`Reassign all of ${rep.name}'s leads to whom?\n(Leave blank to return to pool)`)
                                  if(to===null) return
                                  try{
                                    const r=await api("/api/leads/reassign",{method:"POST",
                                      body:JSON.stringify({from:rep.name,to:to.trim()})})
                                    notify(`${r.reassigned} lead${r.reassigned!==1?"s":""} moved to ${to.trim()||"pool"}`)
                                    // Refresh reps
                                    api("/api/reps").then(r=>{if(Array.isArray(r))window.__lf_reps=r;setLbLoading(l=>!l);setTimeout(()=>setLbLoading(l=>!l),50)}).catch(()=>{})
                                    setTimeout(loadLeads,500)
                                  }catch(e){notify("Error: "+e.message,"error")}
                                }}>
                                Reassign
                              </button>
                              {rep.leads>0&&(
                                <button className="btn btn-p" style={{fontSize:11,padding:"5px 10px"}}
                                  onClick={async()=>{
                                    if(!window.confirm(`Return all ${rep.leads} of ${rep.name}'s leads to the pool?`)) return
                                    try{
                                      const r=await api("/api/leads/reassign",{method:"POST",
                                        body:JSON.stringify({from:rep.name,to:""})})
                                      notify(`${r.reassigned} lead${r.reassigned!==1?"s":""} returned to pool`)
                                      api("/api/reps").then(r=>{if(Array.isArray(r))window.__lf_reps=r;setLbLoading(l=>!l);setTimeout(()=>setLbLoading(l=>!l),50)}).catch(()=>{})
                                      setTimeout(loadLeads,500)
                                    }catch(e){notify("Error: "+e.message,"error")}
                                  }}>
                                  Release to Pool
                                </button>
                              )}
                              <button className="btn" style={{fontSize:11,padding:"5px 10px",
                                background:"#ff6e8415",color:"#ff6e84",border:"1px solid #ff6e8430"}}
                                onClick={async()=>{
                                  if(!window.confirm(`Block ${rep.name} from logging in? Their leads will be released to the pool.`)) return
                                  try{
                                    await api("/api/auth/block",{method:"POST",body:JSON.stringify({username:rep.name})})
                                    if(rep.leads>0){
                                      await api("/api/leads/reassign",{method:"POST",body:JSON.stringify({from:rep.name,to:""})})
                                    }
                                    notify(`${rep.name} blocked and ${rep.leads} lead${rep.leads!==1?"s":""} released`)
                                    api("/api/reps").then(r=>{if(Array.isArray(r))window.__lf_reps=r;setLbLoading(l=>!l);setTimeout(()=>setLbLoading(l=>!l),50)}).catch(()=>{})
                                    setTimeout(loadLeads,500)
                                  }catch(e){notify("Error: "+e.message,"error")}
                                }}>
                                Block
                              </button>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )
                })()}
              </div>}
            </div>
          )}

          {/* ── HISTORY ─────────────────────────────────────────────────────── */}
          {activeNav==="history"&&(
            <div>
              <div style={{marginBottom:24}}>
                <h1 style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:36,fontWeight:700,
                  color:"#dee5ff",letterSpacing:"-.02em"}}>Call History</h1>
                <p style={{color:"#a3aac4",fontSize:14,marginTop:4}}>Filter by rep, date range, and view reports</p>
              </div>

              {/* Filters */}
              <div style={{background:"#0f1930",borderRadius:12,padding:"14px 20px",marginBottom:20,
                display:"flex",gap:10,alignItems:"center",flexWrap:"wrap"}}>
                {/* Range toggles */}
                {[{k:"today",l:"Today"},{k:"week",l:"This Week"},{k:"month",l:"This Month"},{k:"custom",l:"Custom"}].map(({k,l})=>(
                  <button key={k} onClick={()=>{setHistRange(k);if(k!=="custom")loadHistory(k,histCaller)}}
                    style={{padding:"6px 14px",borderRadius:6,fontSize:12,cursor:"pointer",fontFamily:"inherit",
                      background:histRange===k?"#a3a6ff":"transparent",color:histRange===k?"#000011":"#a3aac4",
                      border:`1px solid ${histRange===k?"#a3a6ff":"#40485d40"}`}}>{l}</button>
                ))}
                <div style={{width:1,height:24,background:"#40485d30",margin:"0 4px"}}/>
                {/* Caller filter */}
                <select value={histCaller} onChange={e=>{setHistCaller(e.target.value);loadHistory(histRange,e.target.value)}}
                  style={{background:"#141f38",color:"#dee5ff",border:"1px solid #40485d40",borderRadius:6,
                    padding:"6px 10px",fontSize:12,fontFamily:"inherit"}}>
                  <option value="">All Reps</option>
                  {(histData?.callers||[]).map(c=><option key={c} value={c}>{c}</option>)}
                </select>
                {/* Custom date inputs */}
                {histRange==="custom"&&(
                  <>
                    <input type="date" value={histFrom} onChange={e=>setHistFrom(e.target.value)}
                      style={{background:"#141f38",color:"#dee5ff",border:"1px solid #40485d40",borderRadius:6,
                        padding:"6px 10px",fontSize:12,fontFamily:"inherit"}}/>
                    <span style={{color:"#40485d",fontSize:12}}>to</span>
                    <input type="date" value={histTo} onChange={e=>setHistTo(e.target.value)}
                      style={{background:"#141f38",color:"#dee5ff",border:"1px solid #40485d40",borderRadius:6,
                        padding:"6px 10px",fontSize:12,fontFamily:"inherit"}}/>
                    <button className="btn btn-p" style={{fontSize:11,padding:"6px 12px"}}
                      onClick={()=>loadHistory("custom",histCaller)}>Apply</button>
                  </>
                )}
                <div style={{flex:1}}/>
                <button className="btn btn-g" style={{fontSize:11,padding:"5px 12px"}}
                  onClick={()=>{
                    if(!callHistory.length){notify("No data to export","error");return}
                    const headers=["Date","Time","Rep","Outcome","Duration (min)","Notes"]
                    const rows=callHistory.map(c=>[
                      c.calledAt?new Date(c.calledAt).toLocaleDateString():"",
                      c.calledAt?new Date(c.calledAt).toLocaleTimeString():"",
                      c.calledBy||"",
                      c.outcome||"",
                      c.duration?Math.round(c.duration/60):"0",
                      `"${(c.notes||"").replace(/"/g,'""')}"`,
                    ])
                    const csv=[headers.join(","),...rows.map(r=>r.join(","))].join("\n")
                    const blob=new Blob([csv],{type:"text/csv"})
                    const a=document.createElement("a")
                    a.href=URL.createObjectURL(blob)
                    a.download=`call-history-${histRange}-${new Date().toISOString().split("T")[0]}.csv`
                    a.click()
                    notify("CSV downloaded")
                  }}>Export CSV</button>
                {isAdmin()&&<button className="btn btn-g" style={{fontSize:11,padding:"5px 12px"}}
                  onClick={async()=>{
                    if(!window.confirm("Unassign leads untouched for 7+ days?")) return
                    try{
                      const r=await api("/api/leads/recycle-stale",{method:"POST",body:"{}"})
                      notify(`Recycled ${r.recycled} stale lead${r.recycled!==1?"s":""}`)
                      loadHistory(histRange,histCaller)
                    }catch(e){notify("Error: "+e.message,"error")}
                  }}>Recycle Stale</button>}
                <button className="btn btn-g" style={{fontSize:11,padding:"5px 12px"}}
                  onClick={()=>loadHistory(histRange,histCaller)}>Refresh</button>
              </div>

              {histLoading?(
                <div style={{padding:60,textAlign:"center",color:"#40485d"}}>Loading...</div>
              ):!histData||callHistory.length===0?(
                <div style={{background:"#0f1930",borderRadius:16,padding:72,textAlign:"center"}}>
                  <div style={{fontSize:40,marginBottom:12}}>&#x1f4de;</div>
                  <div style={{color:"#a3aac4",fontSize:14}}>No calls found for this period</div>
                </div>
              ):(
                <>
                  {/* Summary cards */}
                  <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:14,marginBottom:14}}>
                    {[
                      {label:"Total Calls",val:histData.summary?.total||0,color:"#a3a6ff"},
                      {label:"Converted",val:histData.summary?.converted||0,color:"#69f6b8"},
                      {label:"Interested",val:histData.summary?.interested||0,color:"#ffe083"},
                      {label:"Contact Rate",val:(histData.summary?.contact_rate||"0.0")+"%",color:"#8b5cf6"},
                    ].map(({label,val,color})=>(
                      <div key={label} style={{background:"#0f1930",borderRadius:12,padding:18,
                        borderLeft:`4px solid ${color}`,textAlign:"center"}}>
                        <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:28,fontWeight:700,
                          color,marginBottom:4}}>{val}</div>
                        <div style={{fontSize:11,color:"#a3aac4"}}>{label}</div>
                      </div>
                    ))}
                  </div>
                  <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:14,marginBottom:20}}>
                    {[
                      {label:"No Answer",val:histData.summary?.no_answer||0,color:"#40485d"},
                      {label:"Avg Talk Time",val:(()=>{const s=histData.summary?.avg_talk_time||0;return s>0?`${Math.floor(s/60)}m ${s%60}s`:"0m"})(),color:"#dee5ff"},
                      {label:"First Calls",val:histData.summary?.first_calls||0,color:"#69f6b8"},
                      {label:"Follow-Ups",val:histData.summary?.follow_ups||0,color:"#a3a6ff"},
                    ].map(({label,val,color})=>(
                      <div key={label} style={{background:"#0f1930",borderRadius:12,padding:18,
                        borderLeft:`4px solid ${color}`,textAlign:"center"}}>
                        <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:28,fontWeight:700,
                          color,marginBottom:4}}>{val}</div>
                        <div style={{fontSize:11,color:"#a3aac4"}}>{label}</div>
                      </div>
                    ))}
                  </div>

                  {/* Per-rep breakdown */}
                  {(histData.by_caller||[]).length>1&&(
                    <div style={{background:"#0f1930",borderRadius:12,overflow:"hidden",marginBottom:20}}>
                      <div style={{padding:"14px 20px",borderBottom:"1px solid #40485d20",
                        fontSize:"0.55rem",color:"#ffe083",fontWeight:700,letterSpacing:".1em",textTransform:"uppercase"}}>
                        Rep Breakdown
                      </div>
                      <div style={{display:"grid",gridTemplateColumns:"2fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr",
                        padding:"8px 20px",fontSize:"0.5rem",fontWeight:700,color:"#a3aac4",
                        textTransform:"uppercase",letterSpacing:".08em",borderBottom:"1px solid #40485d10"}}>
                        <div>Rep</div><div style={{textAlign:"center"}}>Calls</div>
                        <div style={{textAlign:"center"}}>Converted</div><div style={{textAlign:"center"}}>Interested</div>
                        <div style={{textAlign:"center"}}>Conv %</div><div style={{textAlign:"center"}}>Contact %</div>
                        <div style={{textAlign:"center"}}>Avg Talk</div><div style={{textAlign:"center"}}>1st / F-Up</div>
                      </div>
                      {(histData.by_caller||[]).map(rep=>{
                        const ac=avatarColor(rep.name)
                        return(
                          <div key={rep.name} style={{display:"grid",gridTemplateColumns:"2fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr",
                            padding:"12px 20px",alignItems:"center",borderBottom:"1px solid #40485d08"}}
                            onMouseEnter={e=>e.currentTarget.style.background="#192540"}
                            onMouseLeave={e=>e.currentTarget.style.background="transparent"}>
                            <div style={{display:"flex",alignItems:"center",gap:10}}>
                              <div style={{width:30,height:30,borderRadius:"50%",background:ac+"22",flexShrink:0,
                                display:"flex",alignItems:"center",justifyContent:"center",
                                fontSize:11,fontWeight:700,color:ac,fontFamily:"'Space Grotesk',sans-serif"}}>
                                {rep.name.slice(0,2).toUpperCase()}
                              </div>
                              <span style={{fontWeight:600,color:"#dee5ff",fontSize:13}}>{rep.name}</span>
                            </div>
                            <div style={{textAlign:"center",fontFamily:"'Space Grotesk',sans-serif",fontSize:15,fontWeight:700,color:"#a3a6ff"}}>{rep.total}</div>
                            <div style={{textAlign:"center",fontFamily:"'Space Grotesk',sans-serif",fontSize:14,fontWeight:700,color:rep.converted>0?"#69f6b8":"#40485d"}}>{rep.converted}</div>
                            <div style={{textAlign:"center",fontFamily:"'Space Grotesk',sans-serif",fontSize:14,color:rep.interested>0?"#ffe083":"#40485d"}}>{rep.interested}</div>
                            <div style={{textAlign:"center",fontFamily:"'Space Grotesk',sans-serif",fontSize:13,fontWeight:700,
                              color:parseFloat(rep.conv_rate)>=10?"#69f6b8":"#a3aac4"}}>{rep.conv_rate}%</div>
                            <div style={{textAlign:"center",fontFamily:"'Space Grotesk',sans-serif",fontSize:13,fontWeight:700,
                              color:parseFloat(rep.contact_rate)>=50?"#8b5cf6":"#a3aac4"}}>{rep.contact_rate}%</div>
                            <div style={{textAlign:"center",fontFamily:"'Space Grotesk',sans-serif",fontSize:12,
                              color:rep.avg_talk_time>0?"#dee5ff":"#40485d"}}>
                              {rep.avg_talk_time>0?`${Math.floor(rep.avg_talk_time/60)}m`:"—"}</div>
                            <div style={{textAlign:"center",fontSize:12,color:"#a3aac4"}}>
                              <span style={{color:"#69f6b8"}}>{rep.first_calls}</span>
                              <span style={{color:"#40485d"}}> / </span>
                              <span style={{color:"#a3a6ff"}}>{rep.follow_ups}</span>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )}

                  {/* Daily activity */}
                  {(histData.by_date||[]).length>1&&(
                    <div style={{background:"#0f1930",borderRadius:12,overflow:"hidden",marginBottom:20}}>
                      <div style={{padding:"14px 20px",borderBottom:"1px solid #40485d20",
                        fontSize:"0.55rem",color:"#a3a6ff",fontWeight:700,letterSpacing:".1em",textTransform:"uppercase"}}>
                        Daily Activity
                      </div>
                      {(histData.by_date||[]).map(day=>{
                        const maxCalls=Math.max(...(histData.by_date||[]).map(d=>d.total))
                        const pct=maxCalls?Math.round(day.total/maxCalls*100):0
                        return(
                          <div key={day.date} style={{padding:"10px 20px",display:"flex",alignItems:"center",gap:14,
                            borderBottom:"1px solid #40485d08"}}>
                            <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:12,color:"#a3aac4",
                              width:85,flexShrink:0}}>{day.date}</span>
                            <div style={{flex:1,height:8,background:"#141f38",borderRadius:4,overflow:"hidden"}}>
                              <div style={{height:"100%",width:`${pct}%`,background:"#a3a6ff",borderRadius:4}}/>
                            </div>
                            <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:13,fontWeight:700,
                              color:"#dee5ff",width:30,textAlign:"right"}}>{day.total}</span>
                            {day.converted>0&&<span style={{fontSize:10,color:"#69f6b8"}}>{day.converted} conv</span>}
                          </div>
                        )
                      })}
                    </div>
                  )}

                  {/* Call log table */}
                  <div style={{background:"#0f1930",borderRadius:12,overflow:"hidden"}}>
                    <div style={{padding:"14px 20px",borderBottom:"1px solid #40485d20",
                      fontSize:"0.55rem",color:"#a3aac4",fontWeight:700,letterSpacing:".1em",textTransform:"uppercase"}}>
                      Call Log ({callHistory.length})
                    </div>
                    <div style={{display:"grid",gridTemplateColumns:"2fr 2fr 2fr 3fr",
                      padding:"8px 20px",fontSize:"0.5rem",fontWeight:700,
                      color:"#a3aac4",textTransform:"uppercase",letterSpacing:".08em",
                      borderBottom:"1px solid #40485d15"}}>
                      <div>Outcome</div><div>Rep</div><div>Date / Time</div><div>Notes</div>
                    </div>
                    {callHistory.slice(0,100).map((call,i)=>{
                      const oc=CALL_OUTCOMES.find(o=>o.value===call.outcome)||{label:call.outcome||"Call"}
                      const col=call.outcome==="converted"?"#69f6b8":call.outcome==="interested"?"#ffe083":
                        call.outcome==="callback"?"#8b5cf6":call.outcome==="no_answer"?"#40485d":"#dee5ff"
                      return(
                        <div key={call.id||i} style={{display:"grid",gridTemplateColumns:"2fr 2fr 2fr 3fr",
                          padding:"12px 20px",alignItems:"center",borderBottom:"1px solid #40485d08",
                          transition:"background .12s"}}
                          onMouseEnter={e=>e.currentTarget.style.background="#192540"}
                          onMouseLeave={e=>e.currentTarget.style.background="transparent"}>
                          <span className="pill" style={{background:col+"20",color:col,
                            border:`1px solid ${col}30`,width:"fit-content"}}>{oc.label}</span>
                          <div style={{fontSize:13,color:"#dee5ff"}}>{call.calledBy||"\u2014"}</div>
                          <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:12,color:"#a3aac4"}}>
                            {call.calledAt?new Date(call.calledAt).toLocaleDateString():""}
                            {" "}
                            {call.calledAt?new Date(call.calledAt).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"}):""}
                          </div>
                          <div style={{fontSize:12,color:"#a3aac4",overflow:"hidden",textOverflow:"ellipsis",
                            whiteSpace:"nowrap"}}>{call.notes||"\u2014"}</div>
                        </div>
                      )
                    })}
                    <div style={{padding:"12px 20px",borderTop:"1px solid #40485d20",
                      display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                      <span style={{fontSize:11,color:"#40485d"}}>
                        Showing {Math.min(callHistory.length,100)} of {callHistory.length}
                      </span>
                      <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:13,color:"#a3a6ff",fontWeight:700}}>
                        {callHistory.length} call{callHistory.length!==1?"s":""}
                      </span>
                    </div>
                  </div>
                </>
              )}
            </div>
          )}

        </div>
      </main>

      {/* ── FAB ──────────────────────────────────────────────────────────── */}
      <FabBtn onClick={()=>setEditModal(true)}/>

      {/* ── Mobile Bottom Nav ────────────────────────────────────────────── */}
      <nav className="mobile-nav" style={{display:"none",position:"fixed",bottom:0,left:0,right:0,
        background:"#060e20",height:64,alignItems:"center",justifyContent:"space-around",
        zIndex:50,borderTop:"1px solid #40485d25",padding:"0 8px"}}>
        {[
          {key:"dashboard",label:"Dashboard",Icon:IconDashboard},
          {key:"leads",    label:"Leads",    Icon:IconPeople},
          {key:"dialer",   label:"Dialer",   Icon:IconPhone},
          {key:"history",  label:"History",  Icon:IconHistory},
          {key:"account",  label:"Account",  Icon:IconPerson},
        ].map(({key,label,Icon})=>{
          const active=activeNav===key
          return(
            <a key={key} href="#" onClick={e=>{e.preventDefault();setNav(key)}}
              style={{display:"flex",flexDirection:"column",alignItems:"center",gap:2,
                color:active?"#a3a6ff":"#a3aac4",textDecoration:"none",minWidth:48,padding:4}}>
              <Icon/>
              <span style={{fontSize:"0.6rem",fontWeight:active?700:500}}>{label}</span>
            </a>
          )
        })}
      </nav>

      {/* ── Modals ───────────────────────────────────────────────────────── */}
      {callModal&&<CallModal lead={callModal} onClose={()=>setCallModal(null)} onSaved={loadLeads}/>}
      {emailModal&&<EmailModal lead={emailModal} onClose={()=>setEmailModal(null)} onSent={()=>notify("Email sent!")}/>}
      {editModal&&(
        <LeadModal lead={editModal===true?null:editModal} onClose={()=>setEditModal(null)}
          onSaved={()=>{loadLeads();notify(editModal===true?"Lead added!":"Lead updated")}}/>
      )}
      {showImport&&<ImportModal onClose={()=>setImport(false)} onDone={msg=>{notify(msg);loadLeads()}}/>}
      {showScripts&&<ScriptsModal onClose={()=>setShowScripts(false)}/>}
      {toast&&(
        <div className="toast"
          style={{borderColor:toast.type==="error"?"#ff6e8440":"#a3a6ff40",
            color:toast.type==="error"?"#ff6e84":"#dee5ff"}}>
          {toast.msg}
        </div>
      )}
    </div>
  )
}

// ─── Log Call Button (hover effect with state) ────────────────────────────────

function LogCallBtn({onClick}){
  const [hov,setHov]=useState(false)
  return(
    <button onClick={onClick}
      onMouseEnter={()=>setHov(true)} onMouseLeave={()=>setHov(false)}
      style={{display:"flex",alignItems:"center",gap:8,padding:"8px 16px",
        borderRadius:8,background:hov?"#a3a6ff":"#000011",
        color:hov?"#000011":"#a3a6ff",
        border:"1px solid #a3a6ff25",cursor:"pointer",fontFamily:"inherit",
        fontSize:13,fontWeight:700,transition:"all .15s",whiteSpace:"nowrap"}}>
      <IconCallFwd/>
      Log Call
    </button>
  )
}

// ─── FAB ─────────────────────────────────────────────────────────────────────

// Admin-only: Google Places spend + leads-pulled-per-rep dashboard.
// Mirrors LoginActivityPanel visual style — collapsible card, dark theme.
function UsageDashboard(){
  const [data,setData]=useState(null)
  const [days,setDays]=useState(7)
  const [show,setShow]=useState(false)
  const [loading,setLoading]=useState(false)
  const [ks,setKs]=useState(null)          // { on, source, envLocked }
  const [ksBusy,setKsBusy]=useState(false)

  const load=(d)=>{
    setLoading(true)
    api(`/api/usage?days=${d}`)
      .then(r=>setData(r))
      .catch(e=>setData({error:String(e.message||e)}))
      .finally(()=>setLoading(false))
  }

  const loadKs=()=>{
    api("/api/admin/kill-switch").then(setKs).catch(()=>{})
  }

  const toggleKs=()=>{
    if(!ks) return
    const turnOn=!ks.on
    const msg=turnOn
      ?"KILL the Google Places scraper? Callers will get 503s until you re-enable. Autocomplete also stops."
      :"Re-enable scraping? Callers can resume pulling leads."
    if(!window.confirm(msg)) return
    setKsBusy(true)
    api("/api/admin/kill-switch",{method:"POST",body:JSON.stringify({on:turnOn})})
      .then(r=>setKs(r))
      .catch(e=>alert(e.message||e))
      .finally(()=>setKsBusy(false))
  }

  const money=(c)=>c==null?"—":`$${(Number(c)/100).toFixed(2)}`

  return(
    <div style={{background:"#0f1930",borderRadius:16,overflow:"hidden",marginTop:24}}>
      <div style={{padding:"18px 24px",display:"flex",alignItems:"center",justifyContent:"space-between",
        borderBottom:show?"1px solid #40485d20":"none",cursor:"pointer"}}
        onClick={()=>{
          setShow(p=>!p)
          if(!data) load(days)
          if(!ks) loadKs()
        }}>
        <div style={{fontSize:"0.6rem",color:"#a3aac4",fontWeight:700,letterSpacing:".1em",textTransform:"uppercase",
          display:"flex",alignItems:"center",gap:8}}>
          Google Places Usage & Leads Pulled
          <span style={{color:"#ff6e84",fontSize:9}}>ADMIN</span>
          {ks?.on&&<span style={{color:"#ff6e84",fontSize:10,padding:"2px 8px",borderRadius:10,
            background:"#ff6e8418",border:"1px solid #ff6e8440",letterSpacing:".05em"}}>SCRAPING KILLED</span>}
        </div>
        <span style={{color:"#40485d",fontSize:12,transition:"transform .2s",
          transform:show?"rotate(180deg)":"rotate(0)"}}>{"\u25BC"}</span>
      </div>

      {show&&(
        <div>
          {/* Kill switch strip — always visible, alerts when ON */}
          {ks&&(
            <div style={{padding:"12px 24px",borderBottom:"1px solid #40485d10",
              background:ks.on?"#ff6e8410":"transparent",
              display:"flex",alignItems:"center",gap:12,justifyContent:"space-between"}}>
              <div style={{display:"flex",alignItems:"center",gap:10,flex:1,minWidth:0}}>
                <div style={{width:10,height:10,borderRadius:"50%",flexShrink:0,
                  background:ks.on?"#ff6e84":"#69f6b8",boxShadow:ks.on?"0 0 8px #ff6e8480":"none"}}/>
                <div style={{minWidth:0}}>
                  <div style={{fontSize:13,color:ks.on?"#ff6e84":"#dee5ff",fontWeight:600}}>
                    {ks.on?"Scraping is KILLED":"Scraping is live"}
                  </div>
                  <div style={{fontSize:11,color:"#40485d",marginTop:2}}>
                    {ks.envLocked
                      ?"PLACES_KILL_SWITCH env var is active — unset in Railway to unlock the toggle."
                      :ks.on
                        ?"All /api/scrape + autocomplete calls return 503 / empty."
                        :"Flip to pause every Google Places call instantly — no redeploy needed."}
                  </div>
                </div>
              </div>
              <button
                disabled={ksBusy||ks.envLocked}
                onClick={toggleKs}
                style={{
                  padding:"8px 16px",borderRadius:8,fontSize:12,fontWeight:700,
                  letterSpacing:".05em",textTransform:"uppercase",cursor:ks.envLocked?"not-allowed":"pointer",
                  border:"none",opacity:ksBusy?0.5:1,
                  background:ks.on?"#69f6b8":"#ff6e84",
                  color:ks.on?"#001a0e":"#1a0004",
                }}>
                {ksBusy?"…":ks.on?"Resume":"Kill Switch"}
              </button>
            </div>
          )}

          {/* Window picker */}
          <div style={{padding:"10px 24px",display:"flex",gap:8,alignItems:"center",borderBottom:"1px solid #40485d10"}}>
            {[{label:"Today",d:1},{label:"7 Days",d:7},{label:"30 Days",d:30},{label:"90 Days",d:90}].map(p=>(
              <button key={p.d} className="btn" style={{fontSize:11,padding:"4px 12px",
                background:days===p.d?"#a3a6ff22":"transparent",color:days===p.d?"#a3a6ff":"#40485d",
                border:`1px solid ${days===p.d?"#a3a6ff30":"#40485d20"}`,borderRadius:8}}
                onClick={()=>{setDays(p.d);load(p.d)}}>{p.label}</button>
            ))}
            <button className="btn btn-g" style={{fontSize:11,padding:"5px 12px",marginLeft:"auto"}}
              onClick={()=>load(days)}>Refresh</button>
          </div>

          {loading?(
            <div style={{padding:48,textAlign:"center",color:"#40485d"}}>Loading…</div>
          ):!data?(
            <div style={{padding:48,textAlign:"center",color:"#40485d",fontSize:13}}>No data yet</div>
          ):data.error?(
            <div style={{padding:"24px 24px",color:"#ff6e84",fontSize:13,whiteSpace:"pre-wrap"}}>
              {data.error}
            </div>
          ):(
            <>
              {/* Summary tiles */}
              <div style={{padding:"16px 24px",display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(140px,1fr))",
                gap:12,borderBottom:"1px solid #40485d10"}}>
                <Tile label="Spend (window)"   value={money(data.totals?.cost_cents)}      accent="#ffe083"/>
                <Tile label="Daily avg"        value={money(data.projection?.dailyAverage_cents)}  accent="#a3a6ff"/>
                <Tile label="Monthly proj."    value={money(data.projection?.monthlyEstimate_cents)} accent="#ff6e84"/>
                <Tile label="Leads today"      value={data.totals?.leadsToday||0}          accent="#69f6b8"/>
                <Tile label="Leads (window)"   value={data.totals?.leadsWindow||0}         accent="#69f6b8"/>
                <Tile label="Events (window)"  value={data.totals?.events||0}              accent="#a3aac4"/>
              </div>

              {/* Leads pulled per rep */}
              <div style={{padding:"14px 24px 6px",fontSize:"0.6rem",color:"#a3aac4",
                fontWeight:700,letterSpacing:".1em",textTransform:"uppercase"}}>
                Leads pulled per rep
              </div>
              {(data.leadsByRep||[]).length===0?(
                <div style={{padding:"8px 24px 20px",color:"#40485d",fontSize:13}}>No leads pulled yet in this window</div>
              ):(
                <div style={{padding:"0 12px 14px"}}>
                  <table style={{width:"100%",borderCollapse:"collapse",fontSize:13}}>
                    <thead>
                      <tr style={{color:"#a3aac4",textAlign:"left"}}>
                        <th style={{padding:"8px 12px",fontWeight:600,fontSize:11}}>Rep</th>
                        <th style={{padding:"8px 12px",fontWeight:600,fontSize:11,textAlign:"right"}}>Today</th>
                        <th style={{padding:"8px 12px",fontWeight:600,fontSize:11,textAlign:"right"}}>{days===1?"24h":`${days}d`}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.leadsByRep.map(rep=>(
                        <tr key={rep.username} style={{borderTop:"1px solid #40485d10"}}>
                          <td style={{padding:"8px 12px",color:"#dee5ff"}}>{rep.username}</td>
                          <td style={{padding:"8px 12px",textAlign:"right",color:rep.leadsToday>0?"#69f6b8":"#40485d",fontWeight:600}}>
                            {rep.leadsToday}
                          </td>
                          <td style={{padding:"8px 12px",textAlign:"right",color:"#a3aac4"}}>{rep.leadsWindow}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Per-rep spend breakdown */}
              <div style={{padding:"14px 24px 6px",fontSize:"0.6rem",color:"#a3aac4",
                fontWeight:700,letterSpacing:".1em",textTransform:"uppercase"}}>
                Spend per rep ({days}d)
              </div>
              {(data.byUser||[]).length===0?(
                <div style={{padding:"8px 24px 20px",color:"#40485d",fontSize:13}}>No paid API calls recorded</div>
              ):(
                <div style={{padding:"0 12px 14px"}}>
                  <table style={{width:"100%",borderCollapse:"collapse",fontSize:13}}>
                    <thead>
                      <tr style={{color:"#a3aac4",textAlign:"left"}}>
                        <th style={{padding:"8px 12px",fontWeight:600,fontSize:11}}>Rep</th>
                        <th style={{padding:"8px 12px",fontWeight:600,fontSize:11,textAlign:"right"}}>Scrapes</th>
                        <th style={{padding:"8px 12px",fontWeight:600,fontSize:11,textAlign:"right"}}>Text Search</th>
                        <th style={{padding:"8px 12px",fontWeight:600,fontSize:11,textAlign:"right"}}>Details</th>
                        <th style={{padding:"8px 12px",fontWeight:600,fontSize:11,textAlign:"right"}}>Autocomplete</th>
                        <th style={{padding:"8px 12px",fontWeight:600,fontSize:11,textAlign:"right"}}>Cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.byUser.map(u=>(
                        <tr key={u.username} style={{borderTop:"1px solid #40485d10"}}>
                          <td style={{padding:"8px 12px",color:"#dee5ff"}}>{u.username}</td>
                          <td style={{padding:"8px 12px",textAlign:"right",color:"#a3aac4"}}>{u.scrapes}</td>
                          <td style={{padding:"8px 12px",textAlign:"right",color:"#a3aac4"}}>{u.text_searches}</td>
                          <td style={{padding:"8px 12px",textAlign:"right",color:"#a3aac4"}}>{u.details}</td>
                          <td style={{padding:"8px 12px",textAlign:"right",color:"#a3aac4"}}>{u.autocompletes||0}</td>
                          <td style={{padding:"8px 12px",textAlign:"right",color:"#ffe083",fontWeight:600}}>{money(u.cost_cents)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Daily spend series */}
              {(data.byDay||[]).length>0&&(
                <>
                  <div style={{padding:"14px 24px 6px",fontSize:"0.6rem",color:"#a3aac4",
                    fontWeight:700,letterSpacing:".1em",textTransform:"uppercase"}}>
                    Daily spend
                  </div>
                  <div style={{padding:"0 24px 14px"}}>
                    {data.byDay.map(d=>{
                      const maxCents=Math.max(...data.byDay.map(x=>x.cost_cents||0),1)
                      const pct=Math.min(100,(d.cost_cents/maxCents)*100)
                      return(
                        <div key={d.date} style={{display:"flex",alignItems:"center",gap:12,padding:"4px 0"}}>
                          <div style={{width:80,color:"#40485d",fontSize:11}}>{d.date}</div>
                          <div style={{flex:1,height:6,background:"#40485d15",borderRadius:3,overflow:"hidden"}}>
                            <div style={{width:`${pct}%`,height:"100%",background:"#ffe083",transition:"width .3s"}}/>
                          </div>
                          <div style={{width:70,textAlign:"right",color:"#ffe083",fontSize:12,fontWeight:600}}>
                            {money(d.cost_cents)}
                          </div>
                          <div style={{width:50,textAlign:"right",color:"#40485d",fontSize:11}}>
                            {d.events}ev
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </>
              )}

              {/* Limits summary (kill switch lives in its own strip at the top) */}
              {data.limits&&(
                <div style={{padding:"10px 24px 18px",fontSize:11,color:"#40485d",
                  borderTop:"1px solid #40485d10",display:"flex",flexWrap:"wrap",gap:16}}>
                  <span>Non-admin cap: <span style={{color:"#a3aac4"}}>{data.limits.nonAdminDailyScrapeCap}/day</span></span>
                  <span>Max spend/run: <span style={{color:"#a3aac4"}}>${data.limits.maxSpendPerRun}</span></span>
                  <span>Cache TTL: <span style={{color:"#a3aac4"}}>{data.limits.cacheTtlDays}d</span></span>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

function Tile({label,value,accent}){
  return(
    <div style={{background:"#00001133",borderRadius:10,padding:"12px 14px",
      border:`1px solid ${accent}22`}}>
      <div style={{fontSize:10,color:"#40485d",fontWeight:700,letterSpacing:".08em",textTransform:"uppercase"}}>{label}</div>
      <div style={{fontSize:20,color:accent,fontWeight:700,marginTop:4,fontFamily:"'Space Grotesk',sans-serif"}}>
        {value}
      </div>
    </div>
  )
}

function LeadCleanupPanel({onCleanup}){
  const [data,setData]   = useState(null)
  const [open,setOpen]   = useState(false)
  const [loading,setLoad]= useState(false)
  const [busy,setBusy]   = useState(null)  // category currently being deleted
  const [result,setRes]  = useState(null)
  const [backfilling,setBackfilling] = useState(false)
  const [backfillResult,setBackfillResult] = useState(null)

  async function runBackfill(){
    if(!window.confirm("Rebuild total_calls + last_called_at on every lead from call_outcomes history?\n\nSafe to re-run.")) return
    setBackfilling(true); setBackfillResult(null)
    try{
      const r = await api("/api/admin/leads/backfill-call-stats",{method:"POST"})
      setBackfillResult(r)
      onCleanup&&onCleanup()
    }catch(ex){
      setBackfillResult({error: ex.message||"backfill failed"})
    }finally{ setBackfilling(false) }
  }

  const loadPreview = ()=>{
    setLoad(true)
    api("/api/admin/leads/cleanup-preview")
      .then(setData).catch(()=>setData(null)).finally(()=>setLoad(false))
  }
  useEffect(()=>{ if(open && !data) loadPreview() },[open])

  async function deleteCategory(catKey, label, count){
    if(count===0) return
    if(!window.confirm(`Permanently delete ${count} lead${count!==1?"s":""} from "${label}"?\n\nThis can't be undone.`)) return
    setBusy(catKey); setRes(null)
    try{
      const r = await api("/api/admin/leads/cleanup-execute",{method:"POST",body:JSON.stringify({
        ids: data.categories[catKey].ids, reason: catKey,
        // For duplicate_phones, pass the {extra_id: keep_id} map so the
        // backend re-parents call_outcomes before deleting the extra rows
        // (otherwise dial history orphans).
        reparent_map: data.categories[catKey].reparent_map || {},
      })})
      setRes({cat: label, ...r})
      loadPreview()
      onCleanup&&onCleanup()
    }catch(ex){
      setRes({cat: label, error: ex.message||"delete failed"})
    }finally{ setBusy(null) }
  }

  const cats = data?.categories || {}
  const totalIrrelevant = Object.values(cats).reduce((s,c)=>s+(c.count||0), 0)

  return (
    <div style={{background:"#0f1930",borderRadius:16,overflow:"hidden",marginTop:24}}>
      <div onClick={()=>setOpen(o=>!o)}
        style={{padding:"18px 24px",cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
        <div style={{display:"flex",alignItems:"center",gap:12}}>
          <div style={{fontSize:"0.6rem",color:"#a3aac4",fontWeight:700,letterSpacing:".1em",textTransform:"uppercase"}}>
            🧹 Lead Cleanup <span style={{color:"#ff6e84",fontSize:9,marginLeft:6}}>ADMIN</span>
          </div>
          {data&&totalIrrelevant>0&&(
            <span style={{fontSize:11,color:"#ff6e84",background:"#ff6e8418",padding:"2px 8px",borderRadius:4,fontWeight:700}}>
              {totalIrrelevant} likely-irrelevant
            </span>
          )}
        </div>
        <span style={{color:"#a3aac4",fontSize:14}}>{open?"▾":"▸"}</span>
      </div>
      {open&&(
        <div style={{padding:"0 24px 20px",borderTop:"1px solid #40485d20"}}>
          {/* Dynamic-cleanup notice + manual backfill — sits above the
              category list so admins know auto-dedupe is on. */}
          <div style={{background:"#192540",borderRadius:8,padding:"12px 14px",margin:"14px 0",
            border:"1px solid #69f6b820"}}>
            <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:12,flexWrap:"wrap"}}>
              <div style={{flex:1,minWidth:240}}>
                <div style={{fontSize:11,color:"#69f6b8",fontWeight:700,marginBottom:3}}>
                  🤖 AUTO-DEDUPE ACTIVE
                </div>
                <div style={{fontSize:11,color:"#a3aac4",lineHeight:1.5}}>
                  Phone + Apollo duplicates are auto-cleaned weekly (safe extras only — engaged rows preserved, call history re-parented).
                  Slack-alerts on every run. New scrapes also dedupe against the DB before insert.
                </div>
              </div>
              <button className="btn btn-g" style={{fontSize:11,padding:"7px 14px",whiteSpace:"nowrap"}}
                disabled={backfilling} onClick={runBackfill}
                title="Rebuilds total_calls + last_called_at on every lead from call_outcomes history. Run this once after deploying the timestamp fix.">
                {backfilling?"Rebuilding…":"⟳ Rebuild call stats"}
              </button>
            </div>
            {backfillResult&&(
              <div style={{marginTop:8,fontSize:11,
                color: backfillResult.error?"#ff6e84":"#69f6b8"}}>
                {backfillResult.error
                  ? `✗ ${backfillResult.error}`
                  : `✓ Scanned ${backfillResult.scanned_calls?.toLocaleString()} calls · updated ${backfillResult.updated?.toLocaleString()} leads`}
              </div>
            )}
          </div>
          {loading?(
            <div style={{padding:"30px",textAlign:"center",color:"#40485d",fontSize:12}}>Scanning leads…</div>
          ):!data?(
            <div style={{padding:"20px",textAlign:"center",color:"#40485d",fontSize:12}}>
              <button className="btn btn-g" style={{fontSize:12}} onClick={loadPreview}>Load preview</button>
            </div>
          ):totalIrrelevant===0?(
            <div style={{padding:"30px",textAlign:"center"}}>
              <div style={{fontSize:36,marginBottom:8}}>✓</div>
              <div style={{color:"#69f6b8",fontSize:13,fontWeight:600}}>
                Pipeline is clean — {data.total_leads} leads, no irrelevant data found
              </div>
            </div>
          ):(
            <div style={{paddingTop:16}}>
              <div style={{fontSize:11,color:"#a3aac4",marginBottom:14}}>
                Scanned {data.total_leads.toLocaleString()} leads. Below are categories that look irrelevant.
                Each category has its own delete button — review the samples first, nothing happens until you click + confirm.
              </div>

              {Object.entries(cats).map(([key,cat])=>(
                <div key={key} style={{background:"#060e20",borderRadius:10,padding:14,marginBottom:12,
                  border:"1px solid "+(cat.count>0?"#ff6e8430":"#40485d20")}}>
                  <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:8,gap:10,flexWrap:"wrap"}}>
                    <div>
                      <div style={{fontSize:12,fontWeight:700,color:"#dee5ff",marginBottom:2}}>
                        {cat.label}
                      </div>
                      <div style={{fontSize:11,color:cat.count>0?"#ff6e84":"#69f6b8",fontWeight:600}}>
                        {cat.count} lead{cat.count!==1?"s":""} flagged
                      </div>
                    </div>
                    <button className="btn"
                      disabled={cat.count===0||busy===key}
                      onClick={()=>deleteCategory(key,cat.label,cat.count)}
                      style={{fontSize:11,padding:"6px 14px",
                        background:cat.count>0?"#ff6e84":"#40485d20",
                        color:cat.count>0?"#000011":"#40485d",
                        fontWeight:700,
                        cursor:cat.count>0?"pointer":"not-allowed",
                        border:"none"}}>
                      {busy===key?"Deleting…":`Delete ${cat.count}`}
                    </button>
                  </div>
                  {cat.samples?.length>0&&(
                    <div style={{fontSize:10,color:"#a3aac4",lineHeight:1.5,maxHeight:160,overflowY:"auto",
                      borderTop:"1px solid #40485d20",paddingTop:8,marginTop:6}}>
                      <div style={{color:"#40485d",marginBottom:4,fontSize:9,letterSpacing:".06em",textTransform:"uppercase"}}>
                        Samples ({Math.min(cat.samples.length,12)} of {cat.count}):
                      </div>
                      {cat.samples.map((s,i)=>(
                        <div key={i} style={{padding:"3px 0"}}>
                          {s.kept && s.phone ? (
                            <>
                              📞 <b>{s.phone}</b> — keeping {s.kept?.company} ({s.kept?.createdAt}, {s.kept?.source}),
                              deleting {s.delete?.length} older copy{s.delete?.length!==1?"ies":""}
                            </>
                          ) : s.kept && s.person ? (
                            <>
                              👤 <b>{s.person}</b> — keeping #{String(s.kept?.id||"").slice(0,8)} ({s.kept?.total_calls||0} calls),
                              deleting {s.delete?.length} unworked extra{s.delete?.length!==1?"s":""}
                            </>
                          ) : (
                            <>
                              · {s.company || "(no company)"}
                              {s.phone?<> — 📞 <b>{s.phone}</b></>:null}
                              {s.state!==undefined?<> — state=<i>{String(s.state||"empty")}</i></>:null}
                              {s.city?` · ${s.city}`:""}
                              {s.source?` · ${s.source}`:""}
                            </>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}

              {result&&(
                <div style={{marginTop:12,padding:"10px 14px",fontSize:11,
                  background:result.error?"#ff6e8418":"#69f6b818",
                  border:`1px solid ${result.error?"#ff6e8430":"#69f6b830"}`,
                  borderRadius:8,
                  color:result.error?"#ff6e84":"#69f6b8"}}>
                  {result.error
                    ? `⚠ ${result.cat}: ${result.error}`
                    : `✓ ${result.cat}: deleted ${result.deleted} of ${result.requested} leads${result.failed_batches?` (${result.failed_batches} batch failures)`:""}`}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ─── ConnectivityPanel (admin only) ──────────────────────────────────────────
// Day × hour heatmap of pickup rate (real human picked up / total dials).
// Helps the caller pick better dial windows — a 9am Tuesday with 50% pickup
// beats a 4pm Friday with 8%, so don't keep retrying the same hour after
// no-answers. Also includes a one-shot backfill button for retroactively
// pinning lead.updatedAt to the most recent calledAt for legacy leads.

function ConnectivityPanel(){
  const [data,setData]   = useState(null)
  const [open,setOpen]   = useState(false)
  const [loading,setLoad]= useState(false)
  const [windowDays,setWindowDays] = useState(90)
  const [backfilling,setBackfilling] = useState(false)
  const [bfResult,setBfResult] = useState(null)

  const loadData = (d=windowDays)=>{
    setLoad(true)
    api(`/api/analytics/connectivity?days=${d}`)
      .then(setData).catch(()=>setData(null)).finally(()=>setLoad(false))
  }
  useEffect(()=>{ if(open && !data) loadData() },[open])

  async function runBackfill(){
    if(!window.confirm("Walk every call_outcome and pin each lead's updatedAt to the most recent calledAt? Idempotent — safe to re-run.")) return
    setBackfilling(true); setBfResult(null)
    try{
      const r = await api("/api/admin/leads/backfill-last-contacted",{method:"POST"})
      setBfResult(r)
    }catch(ex){
      setBfResult({error: ex.message||"backfill failed"})
    }finally{ setBackfilling(false) }
  }

  // Heatmap rendering: 7 rows × 13 hours (7am-7pm covers the calling day).
  // Colors: red ≤15%, orange 15-25%, yellow 25-40%, green ≥40%, gray for <5 sample.
  const DAYS = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
  const HOURS = [7,8,9,10,11,12,13,14,15,16,17,18,19]
  const cellByKey = {}
  ;(data?.windows||[]).forEach(w=>{ cellByKey[`${w.day_idx}-${w.hour}`] = w })

  function cellColor(w){
    if(!w || w.total<5) return {bg:"#192540",fg:"#40485d"}
    const r = w.pickup_rate
    if(r>=40) return {bg:"#69f6b830",fg:"#69f6b8"}
    if(r>=25) return {bg:"#ffe08328",fg:"#ffe083"}
    if(r>=15) return {bg:"#ffa44a25",fg:"#ffa44a"}
    return {bg:"#ff6e8420",fg:"#ff6e84"}
  }

  function fmtHour(h){
    if(h===0)  return "12a"
    if(h===12) return "12p"
    return h<12 ? `${h}a` : `${h-12}p`
  }

  return (
    <div style={{background:"#0f1930",borderRadius:16,overflow:"hidden",marginTop:24}}>
      <div onClick={()=>setOpen(o=>!o)}
        style={{padding:"18px 24px",cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
        <div style={{display:"flex",alignItems:"center",gap:12}}>
          <div style={{fontSize:"0.6rem",color:"#a3aac4",fontWeight:700,letterSpacing:".1em",textTransform:"uppercase"}}>
            📊 Connectivity Heatmap <span style={{color:"#ff6e84",fontSize:9,marginLeft:6}}>ADMIN</span>
          </div>
          {data?.summary&&(
            <span style={{fontSize:11,color:"#a3a6ff",background:"#a3a6ff18",padding:"2px 8px",borderRadius:4,fontWeight:700}}>
              {data.summary.overall_pickup_rate}% pickup · {data.summary.total_calls} calls
            </span>
          )}
        </div>
        <span style={{color:"#a3aac4",fontSize:14}}>{open?"▾":"▸"}</span>
      </div>
      {open&&(
        <div style={{padding:"0 24px 20px",borderTop:"1px solid #40485d20"}}>
          {/* Window picker + backfill button */}
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",
            paddingTop:14,paddingBottom:10,gap:12,flexWrap:"wrap"}}>
            <div style={{display:"flex",gap:6}}>
              {[30,90,180,365].map(d=>(
                <button key={d}
                  onClick={()=>{setWindowDays(d);loadData(d)}}
                  className="btn"
                  style={{fontSize:11,padding:"4px 10px",
                    background:windowDays===d?"#a3a6ff":"transparent",
                    color:windowDays===d?"#000011":"#a3aac4",
                    border:`1px solid ${windowDays===d?"#a3a6ff":"#40485d40"}`,
                    fontWeight:windowDays===d?700:500}}>
                  {d}d
                </button>
              ))}
            </div>
            <button className="btn" onClick={runBackfill} disabled={backfilling}
              style={{fontSize:11,padding:"5px 12px",background:"#40485d20",
                color:"#a3aac4",border:"1px solid #40485d40",fontWeight:600}}
              title="One-shot: pin every lead's updatedAt to its most recent calledAt. Idempotent.">
              {backfilling?"Backfilling…":"⏪ Backfill last contacted"}
            </button>
          </div>

          {bfResult&&(
            <div style={{padding:"8px 12px",fontSize:11,marginBottom:10,
              background:bfResult.error?"#ff6e8418":"#69f6b818",
              border:`1px solid ${bfResult.error?"#ff6e8430":"#69f6b830"}`,
              borderRadius:6,
              color:bfResult.error?"#ff6e84":"#69f6b8"}}>
              {bfResult.error
                ? `⚠ ${bfResult.error}`
                : `✓ Backfilled ${bfResult.leads_updated} leads (${bfResult.skipped_already_current} already current, ${bfResult.failed||0} failed)`}
            </div>
          )}

          {loading?(
            <div style={{padding:"30px",textAlign:"center",color:"#40485d",fontSize:12}}>Loading call history…</div>
          ):!data?(
            <div style={{padding:"20px",textAlign:"center",color:"#40485d",fontSize:12}}>
              <button className="btn btn-g" style={{fontSize:12}} onClick={()=>loadData()}>Load heatmap</button>
            </div>
          ):data.summary.total_calls===0?(
            <div style={{padding:"30px",textAlign:"center",color:"#a3aac4",fontSize:12}}>
              No calls in the last {windowDays} days yet — heatmap fills in once your caller starts dialing.
            </div>
          ):(
            <>
              {/* Heatmap grid */}
              <div style={{overflowX:"auto",marginTop:6}}>
                <table style={{borderCollapse:"separate",borderSpacing:2,fontSize:10,
                  fontFamily:"'Space Grotesk',sans-serif"}}>
                  <thead>
                    <tr>
                      <th style={{padding:"4px 6px",color:"#40485d",fontWeight:600,letterSpacing:".06em",textTransform:"uppercase"}}>
                        day / hr
                      </th>
                      {HOURS.map(h=>(
                        <th key={h} style={{padding:"4px 0",color:"#40485d",fontWeight:600,minWidth:36,textAlign:"center"}}>
                          {fmtHour(h)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {DAYS.map((day,di)=>(
                      <tr key={day}>
                        <td style={{padding:"4px 6px",color:"#a3aac4",fontWeight:600,textAlign:"right"}}>{day}</td>
                        {HOURS.map(h=>{
                          const w = cellByKey[`${di}-${h}`]
                          const c = cellColor(w)
                          const tip = w
                            ? `${day} ${fmtHour(h)} · ${w.pickup_rate}% pickup · ${w.pickup}/${w.total} dials (${w.voicemail} vm, ${w.no_answer} no-ans)`
                            : `${day} ${fmtHour(h)} · no dials`
                          return(
                            <td key={h} title={tip}
                              style={{background:c.bg,borderRadius:4,padding:"6px 0",
                                textAlign:"center",color:c.fg,fontWeight:700,minWidth:36,
                                fontSize:10}}>
                              {w&&w.total>=5 ? `${Math.round(w.pickup_rate)}` : (w?".":"·")}
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Legend */}
              <div style={{display:"flex",gap:14,fontSize:10,color:"#40485d",
                marginTop:10,flexWrap:"wrap",alignItems:"center"}}>
                <span>Pickup rate %:</span>
                <span><span style={{display:"inline-block",width:10,height:10,background:"#ff6e8420",border:"1px solid #ff6e8460",borderRadius:2,verticalAlign:"middle"}}/> &lt;15</span>
                <span><span style={{display:"inline-block",width:10,height:10,background:"#ffa44a25",border:"1px solid #ffa44a60",borderRadius:2,verticalAlign:"middle"}}/> 15-25</span>
                <span><span style={{display:"inline-block",width:10,height:10,background:"#ffe08328",border:"1px solid #ffe08360",borderRadius:2,verticalAlign:"middle"}}/> 25-40</span>
                <span><span style={{display:"inline-block",width:10,height:10,background:"#69f6b830",border:"1px solid #69f6b860",borderRadius:2,verticalAlign:"middle"}}/> ≥40</span>
                <span style={{color:"#40485d"}}>· "·" = no dials, "." = &lt;5 sample (gray)</span>
              </div>

              {/* Best/worst windows */}
              {(data.best_windows?.length>0||data.worst_windows?.length>0)&&(
                <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:14,marginTop:16}}>
                  <div style={{background:"#060e20",borderRadius:8,padding:12,border:"1px solid #69f6b820"}}>
                    <div style={{fontSize:10,color:"#69f6b8",fontWeight:700,letterSpacing:".08em",
                      textTransform:"uppercase",marginBottom:8}}>
                      🟢 Best dial windows
                    </div>
                    {(data.best_windows||[]).map((w,i)=>(
                      <div key={i} style={{fontSize:11,color:"#dee5ff",padding:"3px 0"}}>
                        <b>{w.day} {fmtHour(w.hour)}</b>
                        <span style={{color:"#69f6b8",marginLeft:6}}>{w.pickup_rate}%</span>
                        <span style={{color:"#40485d",marginLeft:6}}>· {w.pickup}/{w.total} dials</span>
                      </div>
                    ))}
                    {data.best_windows?.length===0&&<div style={{fontSize:11,color:"#40485d"}}>Need more dials (min 5/window)</div>}
                  </div>
                  <div style={{background:"#060e20",borderRadius:8,padding:12,border:"1px solid #ff6e8420"}}>
                    <div style={{fontSize:10,color:"#ff6e84",fontWeight:700,letterSpacing:".08em",
                      textTransform:"uppercase",marginBottom:8}}>
                      🔴 Worst dial windows — avoid
                    </div>
                    {(data.worst_windows||[]).map((w,i)=>(
                      <div key={i} style={{fontSize:11,color:"#dee5ff",padding:"3px 0"}}>
                        <b>{w.day} {fmtHour(w.hour)}</b>
                        <span style={{color:"#ff6e84",marginLeft:6}}>{w.pickup_rate}%</span>
                        <span style={{color:"#40485d",marginLeft:6}}>· {w.pickup}/{w.total} dials</span>
                      </div>
                    ))}
                    {data.worst_windows?.length===0&&<div style={{fontSize:11,color:"#40485d"}}>Need more dials (min 5/window)</div>}
                  </div>
                </div>
              )}

              {/* Per-state pickup-rate breakdown — tells caller which states
                  are pickup-rich vs pickup-poor independent of timing. */}
              {data.state_breakdown?.length>1&&(
                <div style={{marginTop:18,background:"#060e20",borderRadius:8,
                  padding:12,border:"1px solid #40485d20"}}>
                  <div style={{fontSize:10,color:"#a3aac4",fontWeight:700,
                    letterSpacing:".08em",textTransform:"uppercase",marginBottom:8}}>
                    📍 Pickup rate by state (top 12 by volume)
                  </div>
                  <table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
                    <thead>
                      <tr style={{color:"#40485d",fontSize:10,letterSpacing:".06em",textTransform:"uppercase"}}>
                        <th style={{textAlign:"left",padding:"4px 6px",fontWeight:600}}>State</th>
                        <th style={{textAlign:"right",padding:"4px 6px",fontWeight:600}}>Total</th>
                        <th style={{textAlign:"right",padding:"4px 6px",fontWeight:600}}>Pickup</th>
                        <th style={{textAlign:"right",padding:"4px 6px",fontWeight:600}}>VM</th>
                        <th style={{textAlign:"right",padding:"4px 6px",fontWeight:600}}>No-Ans</th>
                        <th style={{textAlign:"right",padding:"4px 6px",fontWeight:600}}>Pickup %</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.state_breakdown.slice(0,12).map(s=>{
                        const significant = s.total >= 10
                        const rate = s.pickup_rate
                        const color = !significant?"#40485d":rate>=40?"#69f6b8":rate>=25?"#ffe083":rate>=15?"#ffa44a":"#ff6e84"
                        return(
                          <tr key={s.state} style={{borderTop:"1px solid #40485d12"}}>
                            <td style={{padding:"4px 6px",color:"#dee5ff",fontWeight:600}}>{s.state}</td>
                            <td style={{padding:"4px 6px",color:"#a3aac4",textAlign:"right"}}>{s.total}</td>
                            <td style={{padding:"4px 6px",color:"#69f6b8",textAlign:"right"}}>{s.pickup}</td>
                            <td style={{padding:"4px 6px",color:"#ffe083",textAlign:"right"}}>{s.voicemail}</td>
                            <td style={{padding:"4px 6px",color:"#ff6e84",textAlign:"right"}}>{s.no_answer}</td>
                            <td style={{padding:"4px 6px",color:color,textAlign:"right",fontWeight:700}}
                              title={significant?"":"<10 dials — too small to rank reliably"}>
                              {significant?`${rate}%`:`${rate}%*`}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                  <div style={{marginTop:6,fontSize:9,color:"#40485d"}}>
                    * needs ≥10 dials per state to read as significant. Use this to decide where to focus Apollo enrichment.
                  </div>
                </div>
              )}

              <div style={{marginTop:14,fontSize:10,color:"#40485d",lineHeight:1.5}}>
                Bucketed by <b>prospect's local time</b> (lead state → IANA tz, DST-aware).
                {data.summary.fallback_calls>0&&(
                  <> {data.summary.fallback_calls} call{data.summary.fallback_calls===1?"":"s"} fell back to caller-tz (UTC{data.tz_offset_hours_fallback>=0?"+":""}{data.tz_offset_hours_fallback}h) — usually leads with no state set.</>
                )}
                {" "}Min 5 dials per window for ranking.
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ─── InsightsPanel (admin only) ──────────────────────────────────────────
// Three insights bundled in one collapsible card:
//   1. Apollo ROI — conv/engagement/contact rate by source bucket
//   2. Touch-count to conversion — distribution + percentiles
//   3. Stale callbacks — leads with overdue callbackDate and no recent call

function InsightsPanel(){
  const [data,setData]   = useState(null)
  const [open,setOpen]   = useState(false)
  const [loading,setLoad]= useState(false)
  const [windowDays,setWindowDays] = useState(90)

  const loadData = (d=windowDays)=>{
    setLoad(true)
    api(`/api/analytics/insights?days=${d}`)
      .then(setData).catch(()=>setData(null)).finally(()=>setLoad(false))
  }
  useEffect(()=>{ if(open && !data) loadData() },[open])

  const stale = data?.stale_callbacks
  const tc    = data?.touch_count
  const roi   = data?.apollo_roi || []

  // Summary chip on the header — best ROI bucket name + stale count if any
  let headerHint = ""
  if(roi.length){
    const meaningful = roi.filter(r=>r.total>=20).sort((a,b)=>b.conv_rate-a.conv_rate)
    if(meaningful[0]) headerHint = `top: ${meaningful[0].name} ${meaningful[0].conv_rate}%`
  }

  return (
    <div style={{background:"#0f1930",borderRadius:16,overflow:"hidden",marginTop:24}}>
      <div onClick={()=>setOpen(o=>!o)}
        style={{padding:"18px 24px",cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
        <div style={{display:"flex",alignItems:"center",gap:12,flexWrap:"wrap"}}>
          <div style={{fontSize:"0.6rem",color:"#a3aac4",fontWeight:700,letterSpacing:".1em",textTransform:"uppercase"}}>
            🧠 Insights <span style={{color:"#ff6e84",fontSize:9,marginLeft:6}}>ADMIN</span>
          </div>
          {headerHint&&(
            <span style={{fontSize:11,color:"#a3a6ff",background:"#a3a6ff18",padding:"2px 8px",borderRadius:4,fontWeight:700}}>
              {headerHint}
            </span>
          )}
          {stale?.count>0&&(
            <span style={{fontSize:11,color:"#ff6e84",background:"#ff6e8418",padding:"2px 8px",borderRadius:4,fontWeight:700}}>
              {stale.count} stale callback{stale.count===1?"":"s"}
            </span>
          )}
        </div>
        <span style={{color:"#a3aac4",fontSize:14}}>{open?"▾":"▸"}</span>
      </div>
      {open&&(
        <div style={{padding:"0 24px 20px",borderTop:"1px solid #40485d20"}}>
          <div style={{display:"flex",gap:6,paddingTop:14,paddingBottom:10}}>
            {[30,90,180,365].map(d=>(
              <button key={d}
                onClick={()=>{setWindowDays(d);loadData(d)}}
                className="btn"
                style={{fontSize:11,padding:"4px 10px",
                  background:windowDays===d?"#a3a6ff":"transparent",
                  color:windowDays===d?"#000011":"#a3aac4",
                  border:`1px solid ${windowDays===d?"#a3a6ff":"#40485d40"}`,
                  fontWeight:windowDays===d?700:500}}>
                {d}d
              </button>
            ))}
          </div>

          {loading?(
            <div style={{padding:"30px",textAlign:"center",color:"#40485d",fontSize:12}}>Crunching numbers…</div>
          ):!data?(
            <div style={{padding:"20px",textAlign:"center",color:"#40485d",fontSize:12}}>
              <button className="btn btn-g" style={{fontSize:12}} onClick={()=>loadData()}>Load insights</button>
            </div>
          ):(
            <>
              {/* ── Apollo ROI ────────────────────────────────────── */}
              <div style={{background:"#060e20",borderRadius:8,padding:14,border:"1px solid #40485d20",marginBottom:14}}>
                <div style={{fontSize:11,color:"#8b5cf6",fontWeight:700,letterSpacing:".08em",textTransform:"uppercase",marginBottom:10}}>
                  📞 Apollo ROI — does enrichment pay off?
                </div>
                <table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
                  <thead>
                    <tr style={{color:"#40485d",fontSize:10,letterSpacing:".06em",textTransform:"uppercase"}}>
                      <th style={{textAlign:"left",padding:"4px 6px",fontWeight:600}}>Source</th>
                      <th style={{textAlign:"right",padding:"4px 6px",fontWeight:600}}>Total</th>
                      <th style={{textAlign:"right",padding:"4px 6px",fontWeight:600}}>Conv</th>
                      <th style={{textAlign:"right",padding:"4px 6px",fontWeight:600}}>Eng</th>
                      <th style={{textAlign:"right",padding:"4px 6px",fontWeight:600}}>Contacted</th>
                      <th style={{textAlign:"right",padding:"4px 6px",fontWeight:600}}>Conv %</th>
                      <th style={{textAlign:"right",padding:"4px 6px",fontWeight:600}}>Eng %</th>
                    </tr>
                  </thead>
                  <tbody>
                    {roi.map(r=>{
                      const significant = r.total >= 20
                      const convColor = !significant?"#40485d":r.conv_rate>=5?"#69f6b8":r.conv_rate>=2?"#ffe083":"#ff6e84"
                      const engColor  = !significant?"#40485d":r.engagement_rate>=15?"#69f6b8":r.engagement_rate>=8?"#ffe083":"#ff6e84"
                      return(
                        <tr key={r.key} style={{borderTop:"1px solid #40485d12"}}>
                          <td style={{padding:"5px 6px",color:"#dee5ff",fontWeight:600}}>{r.name}</td>
                          <td style={{padding:"5px 6px",color:"#a3aac4",textAlign:"right"}}>{r.total}</td>
                          <td style={{padding:"5px 6px",color:"#69f6b8",textAlign:"right"}}>{r.converted}</td>
                          <td style={{padding:"5px 6px",color:"#ffe083",textAlign:"right"}}>{r.interested+r.callback}</td>
                          <td style={{padding:"5px 6px",color:"#a3aac4",textAlign:"right"}}>{r.contacted}</td>
                          <td style={{padding:"5px 6px",color:convColor,textAlign:"right",fontWeight:700}}
                            title={significant?"":"<20 leads — too small to rank"}>
                            {r.conv_rate}{significant?"%":"%*"}
                          </td>
                          <td style={{padding:"5px 6px",color:engColor,textAlign:"right",fontWeight:700}}>
                            {r.engagement_rate}{significant?"%":"%*"}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
                <div style={{marginTop:8,fontSize:9,color:"#40485d",lineHeight:1.5}}>
                  Eng = converted + interested + callback. * = &lt;20 leads, not statistically meaningful yet.
                  Compare Apollo-enriched vs Google-only — if rates are flat, the credit spend isn't paying off.
                </div>
              </div>

              {/* ── Touch count ────────────────────────────────────── */}
              <div style={{background:"#060e20",borderRadius:8,padding:14,border:"1px solid #40485d20",marginBottom:14}}>
                <div style={{fontSize:11,color:"#69f6b8",fontWeight:700,letterSpacing:".08em",textTransform:"uppercase",marginBottom:10}}>
                  📊 Touch-count to conversion
                </div>
                {tc?.converted_count>0?(
                  <>
                    <div style={{display:"flex",gap:24,fontSize:12,color:"#dee5ff",marginBottom:12,flexWrap:"wrap"}}>
                      <span><b>{tc.converted_count}</b> conversions analyzed</span>
                      <span>median: <b style={{color:"#69f6b8"}}>{tc.median}</b> calls</span>
                      <span>mean: <b style={{color:"#69f6b8"}}>{tc.mean}</b> calls</span>
                      <span>p75: <b style={{color:"#69f6b8"}}>{tc.p75}</b> calls</span>
                    </div>
                    {/* Mini bar chart of distribution */}
                    {tc.distribution?.length>0&&(()=>{
                      const maxCount = Math.max(...tc.distribution.map(d=>d.count))
                      return(
                        <div style={{display:"flex",alignItems:"flex-end",gap:3,height:60,marginBottom:6}}>
                          {tc.distribution.map(d=>(
                            <div key={d.touches} style={{flex:1,display:"flex",flexDirection:"column",alignItems:"center",gap:2,minWidth:18}}
                              title={`${d.count} converters needed exactly ${d.touches} call${d.touches===1?"":"s"}`}>
                              <div style={{
                                background:"#69f6b8",
                                width:"100%",
                                maxWidth:36,
                                height:`${(d.count/maxCount)*100}%`,
                                minHeight:2,
                                borderRadius:"2px 2px 0 0",
                                opacity:0.7+(d.count/maxCount)*0.3,
                              }}/>
                              <div style={{fontSize:9,color:"#a3aac4"}}>{d.touches}</div>
                            </div>
                          ))}
                        </div>
                      )
                    })()}
                    <div style={{fontSize:10,color:"#40485d"}}>
                      Number of calls before conversion. If most converters land by call <b>{tc.p75}</b>,
                      caller can de-prioritize leads past that count.
                    </div>
                  </>
                ):(
                  <div style={{fontSize:11,color:"#a3aac4",fontStyle:"italic"}}>
                    No conversions in the last {windowDays} days yet — distribution fills in as deals close.
                  </div>
                )}
              </div>

              {/* ── Stale callbacks ────────────────────────────────── */}
              <div style={{background:"#060e20",borderRadius:8,padding:14,
                border:`1px solid ${stale?.count>0?"#ff6e8430":"#40485d20"}`}}>
                <div style={{fontSize:11,color:stale?.count>0?"#ff6e84":"#69f6b8",fontWeight:700,
                  letterSpacing:".08em",textTransform:"uppercase",marginBottom:10}}>
                  ⏰ Stale callbacks {stale?.count>0&&`— ${stale.count} need attention`}
                </div>
                {stale?.count===0?(
                  <div style={{fontSize:11,color:"#69f6b8"}}>✓ No stale callbacks — caller is keeping commitments.</div>
                ):(
                  <>
                    <div style={{fontSize:11,color:"#a3aac4",marginBottom:8,lineHeight:1.5}}>
                      These leads have <b>callbackDate in the past</b> with <b>no call logged since</b> —
                      direct conversion leakage. Reassign or recycle.
                    </div>
                    <div style={{maxHeight:200,overflowY:"auto",fontSize:11}}>
                      {(stale.samples||[]).map(s=>(
                        <div key={s.id} style={{display:"flex",justifyContent:"space-between",alignItems:"center",
                          padding:"4px 0",borderTop:"1px solid #40485d12",gap:8}}>
                          <div style={{flex:1,minWidth:0,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",color:"#dee5ff"}}>
                            {s.company||"(no company)"}
                            {s.assignedTo&&<span style={{color:"#40485d",fontSize:10,marginLeft:6}}>· {s.assignedTo}</span>}
                          </div>
                          <span style={{fontSize:10,color:"#ff6e84",fontWeight:600,whiteSpace:"nowrap"}}>
                            {s.days_overdue}d overdue
                          </span>
                          <span style={{fontSize:10,color:"#40485d",whiteSpace:"nowrap"}}>{s.callbackDate}</span>
                        </div>
                      ))}
                      {stale.count>stale.samples.length&&(
                        <div style={{padding:"6px 0",fontSize:10,color:"#40485d",fontStyle:"italic"}}>
                          + {stale.count-stale.samples.length} more not shown
                        </div>
                      )}
                    </div>
                  </>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function EligibleEmailQueue(){
  const [data,setData]    = useState(null)
  const [loading,setLoad] = useState(false)
  const [windowDays,setWindowDays] = useState(7)
  const [selected,setSelected] = useState(new Set())
  const [sending,setSending] = useState(false)
  const [result,setResult] = useState(null)

  const load = (d=windowDays)=>{
    setLoad(true); setResult(null)
    api(`/api/admin/campaigns/eligible?window_days=${d}`)
      .then(r=>{
        setData(r)
        // Default-select every eligible lead so EOD batch is one click.
        // User can deselect any they don't want.
        setSelected(new Set((r?.eligible||[]).map(e=>e.lead_id)))
      })
      .catch(()=>setData(null))
      .finally(()=>setLoad(false))
  }
  useEffect(()=>{ load(windowDays) },[windowDays])

  const eligible = data?.eligible || []
  const allSelected = eligible.length>0 && selected.size===eligible.length

  function toggleAll(){
    if(allSelected) setSelected(new Set())
    else setSelected(new Set(eligible.map(e=>e.lead_id)))
  }
  function toggleOne(id){
    setSelected(prev=>{
      const next = new Set(prev)
      if(next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  async function batchSend(){
    if(selected.size===0) return
    if(!window.confirm(`Send the "tried to call you" email to ${selected.size} lead${selected.size===1?"":"s"}?\n\nEach send burns 1 Resend credit and flips that lead to "Awaiting Email Reply" so callers won't re-dial them.`)) return
    setSending(true); setResult(null)
    try{
      const ids = Array.from(selected)
      const r = await api("/api/admin/campaigns/batch-send",{method:"POST",
        body:JSON.stringify({lead_ids: ids, trigger: "batch_eod"})})
      setResult(r)
      load(windowDays) // refresh — sent leads should drop off the list
    }catch(ex){
      setResult({error: ex.message||"batch send failed"})
    }finally{ setSending(false) }
  }

  const fmtTime = (iso)=>iso?new Date(iso).toLocaleString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}):"—"

  return (
    <div style={{background:"#0f1930",borderRadius:12,marginTop:24,overflow:"hidden",
      borderLeft:"4px solid #ffe083"}}>
      <div style={{padding:"16px 20px",borderBottom:"1px solid #40485d20",
        display:"flex",justifyContent:"space-between",alignItems:"center",gap:14,flexWrap:"wrap"}}>
        <div>
          <div style={{fontSize:12,color:"#a3aac4",letterSpacing:".08em",textTransform:"uppercase",fontWeight:700}}>
            📬 Pending Email Follow-Ups
            {eligible.length>0&&(
              <span style={{marginLeft:10,padding:"2px 10px",background:"#ffe08325",color:"#ffe083",
                borderRadius:10,fontSize:11,fontWeight:700,letterSpacing:0,textTransform:"none"}}>
                {eligible.length}
              </span>
            )}
          </div>
          <div style={{fontSize:11,color:"#40485d",marginTop:4}}>
            Recent no-answer / voicemail calls where the lead has email + name + isn't already in a campaign.
            Review and send the batch when you're ready (e.g. end of day).
          </div>
        </div>
        <div style={{display:"flex",gap:8,alignItems:"center"}}>
          <select className="sel" value={windowDays} onChange={e=>setWindowDays(Number(e.target.value))}
            style={{padding:"5px 10px",fontSize:12}}>
            <option value={1}>Last 24 hours</option>
            <option value={3}>Last 3 days</option>
            <option value={7}>Last 7 days</option>
            <option value={14}>Last 14 days</option>
            <option value={30}>Last 30 days</option>
          </select>
          <button className="btn btn-g" onClick={()=>load(windowDays)} disabled={loading}
            style={{fontSize:11,padding:"6px 12px"}}>
            {loading?"…":"Refresh"}
          </button>
          <button className="btn btn-p" onClick={batchSend}
            disabled={selected.size===0||sending}
            style={{fontSize:12,padding:"7px 14px",
              background:selected.size>0?"#ffe083":"#40485d20",
              color:selected.size>0?"#000011":"#40485d",
              fontWeight:700,cursor:selected.size>0?"pointer":"not-allowed",border:"none"}}>
            {sending?"Sending…":`Send ${selected.size} Selected`}
          </button>
        </div>
      </div>

      {result&&(
        <div style={{padding:"10px 20px",fontSize:11,
          background:result.error?"#ff6e8418":(result.failed>0?"#ffe08315":"#69f6b815"),
          borderBottom:"1px solid #40485d20",
          color:result.error?"#ff6e84":(result.failed>0?"#ffe083":"#69f6b8")}}>
          {result.error
            ? `⚠ ${result.error}`
            : `✓ Sent ${result.sent} email${result.sent===1?"":"s"}${result.skipped?` · ${result.skipped} skipped (already sent recently)`:""}${result.failed?` · ${result.failed} failed`:""}`}
          {result.failures?.length>0&&(
            <div style={{marginTop:6,color:"#ff6e84",fontSize:10}}>
              Failures: {result.failures.map(f=>`${f.company||f.lead_id}: ${f.reason}`).join(" · ")}
            </div>
          )}
        </div>
      )}

      {loading?(
        <div style={{padding:"40px 20px",textAlign:"center",color:"#40485d",fontSize:12}}>Scanning calls…</div>
      ):eligible.length===0?(
        <div style={{padding:"40px 20px",textAlign:"center"}}>
          <div style={{fontSize:30,marginBottom:8}}>📭</div>
          <div style={{color:"#a3aac4",fontSize:13,fontWeight:600}}>No pending follow-ups</div>
          <div style={{color:"#40485d",fontSize:11,marginTop:6}}>
            {data?.error || `No no-answer / voicemail calls in the last ${windowDays} day${windowDays===1?"":"s"} that need an email.`}
          </div>
        </div>
      ):(
        <>
          <div style={{padding:"8px 20px",background:"#060e20",borderBottom:"1px solid #40485d20",
            display:"grid",gridTemplateColumns:"32px 2fr 2fr 1.2fr 1fr",gap:14,
            fontSize:9,color:"#a3aac4",letterSpacing:".06em",textTransform:"uppercase",fontWeight:700}}>
            <input type="checkbox" checked={allSelected} onChange={toggleAll}
              style={{cursor:"pointer",justifySelf:"center"}}
              title={allSelected?"Deselect all":"Select all"}/>
            <span>Contact</span>
            <span>Company / Email</span>
            <span>Last Failed Call</span>
            <span>By</span>
          </div>
          <div style={{maxHeight:520,overflowY:"auto"}}>
            {eligible.map(e=>{
              const isSel = selected.has(e.lead_id)
              return(
                <div key={e.lead_id}
                  onClick={()=>toggleOne(e.lead_id)}
                  style={{display:"grid",gridTemplateColumns:"32px 2fr 2fr 1.2fr 1fr",gap:14,
                    padding:"10px 20px",alignItems:"center",fontSize:12,cursor:"pointer",
                    background:isSel?"#ffe08308":"transparent",
                    borderBottom:"1px solid #40485d12",transition:"background .12s"}}
                  onMouseEnter={ev=>{if(!isSel) ev.currentTarget.style.background="#192540"}}
                  onMouseLeave={ev=>{ev.currentTarget.style.background=isSel?"#ffe08308":"transparent"}}>
                  <input type="checkbox" checked={isSel} onChange={()=>toggleOne(e.lead_id)}
                    onClick={ev=>ev.stopPropagation()}
                    style={{cursor:"pointer",justifySelf:"center"}}/>
                  <div style={{minWidth:0}}>
                    <div style={{color:"#dee5ff",fontWeight:600,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                      {e.first_name} {e.last_name}
                    </div>
                    {e.title&&<div style={{color:"#8b5cf6",fontSize:11,marginTop:1}}>{e.title}</div>}
                  </div>
                  <div style={{minWidth:0}}>
                    <div style={{color:"#dee5ff",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                      {e.company||"—"}
                    </div>
                    <div style={{color:"#a3aac4",fontSize:10,marginTop:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                      {e.email}
                    </div>
                  </div>
                  <div>
                    <div style={{color:e.last_outcome==="voicemail"?"#a3a6ff":"#a3aac4",fontSize:11,fontWeight:600}}>
                      {e.last_outcome==="voicemail"?"📨 Voicemail":"📵 No Answer"}
                    </div>
                    <div style={{color:"#40485d",fontSize:10,marginTop:1}}>
                      {fmtTime(e.last_call_at)}
                    </div>
                  </div>
                  <span style={{color:"#a3aac4",fontSize:11}}>{e.last_call_by||"—"}</span>
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}

// Live count of leads at each step of the 3-touch sequence. Lets the admin
// see at a glance that past sends are queued for follow-up — no need to
// curl the API or trust the bg loop is running.
function EmailSequenceStatus(){
  const [data,setData] = useState(null)
  useEffect(()=>{
    let cancelled = false
    const tick = ()=>{
      api("/api/admin/email-sequence/status").then(r=>{ if(!cancelled) setData(r) }).catch(()=>{})
    }
    tick()
    const id = setInterval(tick, 60_000)  // refresh once a minute
    return ()=>{ cancelled = true; clearInterval(id) }
  },[])
  if(!data) return null
  const total = data.total || 0
  if(total === 0){
    return (
      <div style={{background:"#0f1930",borderRadius:12,padding:"14px 20px",marginTop:24,
        fontSize:12,color:"#a3aac4",display:"flex",alignItems:"center",gap:10}}>
        <span style={{fontSize:16}}>📭</span>
        <span>No leads in the email sequence right now.</span>
      </div>
    )
  }
  const cards = [
    {key:"step_1", label:"At Touch 1",  next:"Touch 2 next", emoji:"📨", color:"#a3a6ff"},
    {key:"step_2", label:"At Touch 2",  next:"Touch 3 next", emoji:"📬", color:"#69f6b8"},
    {key:"step_3", label:"At Touch 3",  next:"Release next", emoji:"🏁", color:"#ffe083"},
  ]
  return (
    <div style={{background:"#0f1930",borderRadius:12,padding:"16px 20px",marginTop:24,overflow:"hidden"}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"baseline",marginBottom:12}}>
        <div style={{fontSize:12,color:"#a3aac4",letterSpacing:".08em",textTransform:"uppercase",fontWeight:700}}>
          📈 Email Sequence — Drip Queue
        </div>
        <div style={{fontSize:11,color:"#40485d"}}>
          {total} lead{total!==1?"s":""} mid-sequence · auto-fires every 10 min
        </div>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:12}}>
        {cards.map(c=>{
          const cell = data[c.key] || {count:0, next_due:null}
          const isEmpty = cell.count === 0
          return (
            <div key={c.key} style={{background:"#060e20",borderRadius:8,padding:"14px 16px",
              borderLeft:`3px solid ${isEmpty?"#40485d40":c.color}`,opacity:isEmpty?0.5:1}}>
              <div style={{fontSize:10,color:"#a3aac4",letterSpacing:".06em",textTransform:"uppercase",
                display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                <span>{c.emoji} {c.label}</span>
              </div>
              <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:28,fontWeight:700,
                color:isEmpty?"#40485d":c.color,lineHeight:1.1,marginTop:8}}>
                {cell.count}
              </div>
              <div style={{fontSize:10,color:"#a3aac4",marginTop:6}}>
                {cell.next_due
                  ? <>{c.next} on <b style={{color:"#dee5ff"}}>{cell.next_due}</b></>
                  : <span style={{color:"#40485d"}}>—</span>}
              </div>
            </div>
          )
        })}
      </div>
      {data.legacy?.count > 0 && (
        <div style={{marginTop:10,padding:"8px 12px",background:"#ff6e8410",borderRadius:6,
          fontSize:11,color:"#ff6e84",borderLeft:"2px solid #ff6e84"}}>
          ⚠ {data.legacy.count} legacy lead{data.legacy.count!==1?"s":""} (pre-sequence) — run backfill: <code>POST /api/admin/email-sequence/backfill?execute=true</code>
        </div>
      )}
    </div>
  )
}

// Three-touch email sequence — admin can edit each step's template separately.
// Touch 1 fires on caller action; Touches 2 and 3 are auto-fired by the
// backend bg-loop sequencer at +3 days and +7 days.
const EMAIL_TEMPLATE_STEPS = [
  {name:"tried-to-call",   label:"Touch 1",  sub:"Day 0 — initial outreach (caller-triggered)"},
  {name:"tried-to-call-2", label:"Touch 2",  sub:"Day 3 — softer nudge (auto-fired)"},
  {name:"tried-to-call-3", label:"Touch 3",  sub:"Day 7 — last touch (auto-fired)"},
]

function EmailTemplateEditor(){
  const [stepName,setStepName] = useState("tried-to-call")
  const [tpl,setTpl]         = useState(null)         // {subject, body, default, placeholders, is_default}
  const [subject,setSubject] = useState("")
  const [bodyText,setBody]   = useState("")
  const [preview,setPreview] = useState(null)
  const [saving,setSaving]   = useState(false)
  const [resetting,setResetting] = useState(false)
  const [status,setStatus]   = useState("")

  const load = ()=>{
    api(`/api/admin/email-template?name=${encodeURIComponent(stepName)}`).then(r=>{
      setTpl(r); setSubject(r.subject||""); setBody(r.body||""); setStatus("")
    }).catch(()=>{})
  }
  // Re-fetch whenever the user switches between Touch 1/2/3.
  useEffect(load, [stepName])

  // Debounced live preview — re-render 400ms after user stops typing.
  useEffect(()=>{
    if(!subject && !bodyText) return
    const t = setTimeout(()=>{
      api("/api/admin/email-template/preview",{method:"POST",
        body:JSON.stringify({subject, body:bodyText, name:stepName})})
        .then(setPreview).catch(()=>{})
    }, 400)
    return ()=>clearTimeout(t)
  }, [subject, bodyText, stepName])

  async function save(){
    setSaving(true); setStatus("")
    try{
      await api("/api/admin/email-template",{method:"PUT",
        body:JSON.stringify({name:stepName, subject, body:bodyText})})
      setStatus("✓ Saved — next send uses this template")
      load()
    }catch(ex){ setStatus("⚠ "+(ex.message||"save failed")) }
    finally{ setSaving(false) }
  }
  async function resetDefault(){
    if(!window.confirm("Reset to the built-in default template? Your custom edits will be lost.")) return
    setResetting(true); setStatus("")
    try{
      await api(`/api/admin/email-template?name=${encodeURIComponent(stepName)}`,{method:"DELETE"})
      setStatus("✓ Reset to default")
      load()
    }catch(ex){ setStatus("⚠ "+(ex.message||"reset failed")) }
    finally{ setResetting(false) }
  }

  const dirty = tpl && (subject !== tpl.subject || bodyText !== tpl.body)
  const isDefault = tpl?.is_default
  const currentStep = EMAIL_TEMPLATE_STEPS.find(s=>s.name===stepName) || EMAIL_TEMPLATE_STEPS[0]

  if(!tpl) return null

  return (
    <div style={{background:"#0f1930",borderRadius:12,marginTop:24,overflow:"hidden"}}>
      {/* Step selector — 3 tabs across the top */}
      <div style={{display:"flex",borderBottom:"1px solid #40485d20",background:"#060e2030"}}>
        {EMAIL_TEMPLATE_STEPS.map(s=>{
          const active = s.name===stepName
          return (
            <button key={s.name} onClick={()=>{
              if(dirty && !window.confirm("Discard unsaved changes to switch?")) return
              setStepName(s.name)
            }}
              style={{flex:1,padding:"14px 16px",border:"none",cursor:"pointer",
                background:active?"#0f1930":"transparent",
                borderBottom:active?"2px solid #a3a6ff":"2px solid transparent",
                fontFamily:"'Inter',sans-serif",textAlign:"left",transition:"all .15s"}}>
              <div style={{fontSize:11,fontWeight:700,letterSpacing:".08em",textTransform:"uppercase",
                color:active?"#a3a6ff":"#a3aac4"}}>{s.label}</div>
              <div style={{fontSize:10,color:active?"#dee5ff":"#40485d",marginTop:2}}>{s.sub}</div>
            </button>
          )
        })}
      </div>

      <div style={{padding:"16px 20px",borderBottom:"1px solid #40485d20",
        display:"flex",justifyContent:"space-between",alignItems:"center",flexWrap:"wrap",gap:10}}>
        <div>
          <div style={{fontSize:12,color:"#a3aac4",letterSpacing:".08em",textTransform:"uppercase",fontWeight:700}}>
            ✏️ {currentStep.label} Template
            {isDefault && <span style={{marginLeft:10,fontSize:9,padding:"2px 8px",background:"#a3a6ff20",color:"#a3a6ff",borderRadius:4,letterSpacing:".05em"}}>USING DEFAULT</span>}
            {!isDefault && <span style={{marginLeft:10,fontSize:9,padding:"2px 8px",background:"#69f6b820",color:"#69f6b8",borderRadius:4,letterSpacing:".05em"}}>CUSTOMIZED</span>}
          </div>
          <div style={{fontSize:11,color:"#40485d",marginTop:4}}>
            Edits apply to every future send. Use {"{placeholders}"} below to personalize.
          </div>
        </div>
        <div style={{display:"flex",gap:8,alignItems:"center"}}>
          {status&&<span style={{fontSize:11,color:status.startsWith("✓")?"#69f6b8":"#ff6e84"}}>{status}</span>}
          {!isDefault && (
            <button className="btn btn-g" onClick={resetDefault} disabled={resetting}
              style={{fontSize:11,padding:"6px 12px"}}>
              {resetting?"Resetting…":"Reset to Default"}
            </button>
          )}
          <button className="btn btn-p" onClick={save} disabled={saving||!dirty}
            style={{fontSize:11,padding:"7px 14px",background:dirty?"#a3a6ff":"#40485d40",
              color:dirty?"#000011":"#40485d",cursor:dirty?"pointer":"not-allowed",fontWeight:700}}>
            {saving?"Saving…":(dirty?"Save Changes":"Saved")}
          </button>
        </div>
      </div>

      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:0}}>
        {/* Editor */}
        <div style={{padding:"16px 20px",borderRight:"1px solid #40485d20"}}>
          <div style={{fontSize:10,color:"#a3aac4",letterSpacing:".08em",textTransform:"uppercase",marginBottom:6}}>
            Subject
          </div>
          <input value={subject} onChange={e=>setSubject(e.target.value)}
            style={{width:"100%",padding:"8px 12px",fontSize:13,fontFamily:"inherit",
              background:"#060e20",border:"1px solid #40485d40",borderRadius:6,color:"#dee5ff",marginBottom:14}}/>

          <div style={{fontSize:10,color:"#a3aac4",letterSpacing:".08em",textTransform:"uppercase",marginBottom:6}}>
            Body
          </div>
          <textarea value={bodyText} onChange={e=>setBody(e.target.value)}
            rows={20}
            style={{width:"100%",padding:"10px 12px",fontSize:12,fontFamily:"'JetBrains Mono', ui-monospace, monospace",
              background:"#060e20",border:"1px solid #40485d40",borderRadius:6,color:"#dee5ff",
              lineHeight:1.5,resize:"vertical"}}/>

          {/* Placeholder reference */}
          <div style={{marginTop:14,padding:"10px 12px",background:"#060e20",borderRadius:8,
            fontSize:11,color:"#a3aac4",lineHeight:1.7,maxHeight:160,overflowY:"auto"}}>
            <div style={{fontSize:10,color:"#a3a6ff",letterSpacing:".06em",textTransform:"uppercase",marginBottom:6,fontWeight:700}}>
              Placeholders
            </div>
            <div style={{display:"grid",gridTemplateColumns:"auto 1fr",gap:"3px 10px"}}>
              {(tpl.placeholders||[]).map(p=>(
                <React.Fragment key={p.key}>
                  <code style={{color:"#69f6b8",fontFamily:"'JetBrains Mono', ui-monospace, monospace",fontSize:10}}>
                    {"{"+p.key+"}"}
                  </code>
                  <span style={{fontSize:10}}>{p.desc}</span>
                </React.Fragment>
              ))}
            </div>
          </div>
        </div>

        {/* Preview */}
        <div style={{padding:"16px 20px",background:"#060e2050"}}>
          <div style={{fontSize:10,color:"#a3aac4",letterSpacing:".08em",textTransform:"uppercase",marginBottom:6,
            display:"flex",justifyContent:"space-between",alignItems:"center"}}>
            <span>Live Preview</span>
            {preview?.lead_used?.is_sample
              ? <span style={{fontSize:9,color:"#40485d"}}>using sample (Sarah Chen, Atlas Healthcare)</span>
              : preview?.lead_used?.name && <span style={{fontSize:9,color:"#40485d"}}>using {preview.lead_used.name}</span>}
          </div>
          {preview ? (
            <div style={{background:"#fffefb",borderRadius:8,padding:"16px 20px",
              fontFamily:"'Inter', sans-serif",color:"#1a1a1a",lineHeight:1.55,fontSize:13,minHeight:300}}>
              <div style={{fontSize:11,color:"#666",marginBottom:8,paddingBottom:8,borderBottom:"1px solid #eee"}}>
                <b style={{color:"#222"}}>Subject:</b> {preview.subject||"(empty)"}
              </div>
              <div style={{whiteSpace:"pre-wrap"}}>{preview.body_text}</div>
            </div>
          ) : (
            <div style={{padding:"40px 20px",textAlign:"center",color:"#40485d",fontSize:11}}>
              Type to see preview…
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// Small Dashboard tile — clickable, navigates to the full Campaigns page.
function CampaignSummaryCard({onClick}){
  const [data,setData] = useState(null)
  useEffect(()=>{
    api("/api/admin/campaigns/recent?days=14").then(setData).catch(()=>{})
  },[])
  if(!data) return null
  const t = data.totals||{}
  // Hide entirely if email feature is dormant — Dashboard real estate is precious.
  if(!data.email_configured && t.sends===0) return null
  return (
    <div onClick={onClick}
      style={{background:"linear-gradient(135deg, #1a1f3d 0%, #0f1930 100%)",
        borderRadius:12,padding:"16px 20px",marginTop:16,cursor:"pointer",
        border:"1px solid #a3a6ff20",transition:"all .15s"}}
      onMouseEnter={e=>e.currentTarget.style.borderColor="#a3a6ff60"}
      onMouseLeave={e=>e.currentTarget.style.borderColor="#a3a6ff20"}>
      <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:14,flexWrap:"wrap"}}>
        <div style={{display:"flex",alignItems:"center",gap:14}}>
          <div style={{fontSize:24}}>📧</div>
          <div>
            <div style={{fontSize:"0.6rem",color:"#a3a6ff",fontWeight:700,letterSpacing:".1em",textTransform:"uppercase"}}>
              Email Campaigns · last 14 days
            </div>
            <div style={{display:"flex",gap:18,marginTop:6,fontSize:13,color:"#dee5ff",flexWrap:"wrap"}}>
              <span><b style={{color:"#a3a6ff"}}>{t.sends||0}</b> sent</span>
              <span><b style={{color:"#69f6b8"}}>{t.replies||0}</b> replied</span>
              <span><b style={{color:"#ff6e84"}}>{t.bounces||0}</b> bounced</span>
              <span style={{color:"#a3aac4",fontSize:12}}>· reply rate <b style={{color:"#69f6b8"}}>{data.reply_rate_pct||0}%</b></span>
            </div>
          </div>
        </div>
        <span style={{fontSize:11,color:"#a3a6ff",fontWeight:600}}>View all →</span>
      </div>
    </div>
  )
}

function CampaignsPage(){
  const [data,setData]   = useState(null)
  const [days,setDays]   = useState(14)
  const [setup,setSetup] = useState(null)
  const [polling,setPolling] = useState(false)
  const [pollResult,setPollResult] = useState("")
  const [releasing,setReleasing] = useState(false)
  const [releaseResult,setReleaseResult] = useState("")

  const load = (d)=>{
    api(`/api/admin/campaigns/recent?days=${d}`).then(setData).catch(()=>setData(null))
    api("/api/admin/email/setup-status").then(setSetup).catch(()=>{})
  }
  useEffect(()=>{ load(days) },[days])

  async function pollNow(){
    setPolling(true); setPollResult("")
    try{
      const r = await api("/api/admin/poll-replies",{method:"POST",body:"{}"})
      if(r.ok){
        const s = r.stats || {}
        setPollResult(`✓ Checked ${s.checked||0} unread, matched ${s.matched||0} replies, ${s.auto_replies_skipped||0} auto-replies skipped, ${s.not_a_reply||0} not-a-reply`)
        load(days)
      }else{
        setPollResult("⚠ "+(r.error||"poll failed"))
      }
    }catch(ex){ setPollResult("⚠ "+(ex.message||"error")) }
    finally{ setPolling(false) }
  }

  async function releaseStale(){
    setReleasing(true); setReleaseResult("")
    try{
      const r = await api("/api/admin/leads/release-stale-emails",{method:"POST",body:"{}"})
      setReleaseResult(r.released>0
        ? `✓ Released ${r.released} stale lead${r.released===1?"":"s"} back to dialer (no reply within ${r.cutoff_days}d)`
        : `✓ Nothing to release — no leads stuck past ${r.cutoff_days}d`)
    }catch(ex){ setReleaseResult("⚠ "+(ex.message||"error")) }
    finally{ setReleasing(false) }
  }

  const t = data?.totals || {sends:0,replies:0,opens:0,clicks:0,bounces:0,unsubs:0}
  const fmtTime = (iso)=>iso?new Date(iso).toLocaleString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}):""

  return (
    <div>
      {/* Header */}
      <div style={{marginBottom:28,display:"flex",alignItems:"flex-end",
        justifyContent:"space-between",gap:20,flexWrap:"wrap"}}>
        <div>
          <h1 style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:36,fontWeight:700,
            color:"#dee5ff",letterSpacing:"-.02em",lineHeight:1.1}}>Email Campaigns</h1>
          <p style={{color:"#a3aac4",fontSize:14,marginTop:4}}>
            "Tried to call you" follow-up sends · Resend delivery + IMAP reply detection
          </p>
        </div>
        <div style={{display:"flex",gap:10,alignItems:"center",flexWrap:"wrap"}}>
          <select className="sel" value={days} onChange={e=>setDays(Number(e.target.value))}
            style={{padding:"6px 12px",fontSize:12}}>
            <option value={7}>Last 7 days</option>
            <option value={14}>Last 14 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>
          {setup?.reply_path?.imap_configured&&(
            <button onClick={pollNow} disabled={polling}
              className="btn btn-p" style={{fontSize:12,padding:"7px 14px",background:"#a3a6ff"}}
              title="Force-check the inbox for new replies right now (background poll runs every 10 min)">
              {polling?"Checking…":"📬 Check Replies Now"}
            </button>
          )}
          <button onClick={releaseStale} disabled={releasing}
            className="btn btn-g" style={{fontSize:12,padding:"7px 14px"}}
            title="Manually pull leads stuck in 'awaiting_email_reply' past the suppression window (7d default) back to the dialer. Bg loop runs this every 10 min anyway.">
            {releasing?"Releasing…":"↻ Release Stale"}
          </button>
        </div>
      </div>

      {/* Setup-status banner (only if something missing) */}
      {setup&&!setup.all_ready&&(
        <div style={{padding:"14px 18px",marginBottom:18,background:"#ffe08315",
          border:"1px solid #ffe08340",borderRadius:10,fontSize:12,color:"#dee5ff",lineHeight:1.6}}>
          <div style={{fontWeight:700,color:"#ffe083",marginBottom:6}}>⚠ Setup incomplete</div>
          {!setup.send_path?.ready&&<div>• Resend not configured — set RESEND_API_KEY in Railway</div>}
          {!setup.webhook_path?.ready&&<div>• Webhook secret missing — set RESEND_WEBHOOK_SECRET in Railway, paste URL in Resend dashboard</div>}
          {!setup.reply_path?.ready&&<div>• IMAP not configured — replies won't auto-flip lead status. Set IMAP_SERVER + IMAP_USERNAME + IMAP_PASSWORD in Railway</div>}
        </div>
      )}

      {pollResult&&(
        <div style={{marginBottom:18,padding:"10px 14px",fontSize:12,
          background:pollResult.startsWith("✓")?"#69f6b815":"#ffe08315",
          border:`1px solid ${pollResult.startsWith("✓")?"#69f6b830":"#ffe08330"}`,
          borderRadius:8,color:pollResult.startsWith("✓")?"#69f6b8":"#ffe083"}}>
          {pollResult}
        </div>
      )}
      {releaseResult&&(
        <div style={{marginBottom:18,padding:"10px 14px",fontSize:12,
          background:releaseResult.startsWith("✓")?"#69f6b815":"#ffe08315",
          border:`1px solid ${releaseResult.startsWith("✓")?"#69f6b830":"#ffe08330"}`,
          borderRadius:8,color:releaseResult.startsWith("✓")?"#69f6b8":"#ffe083"}}>
          {releaseResult}
        </div>
      )}

      {/* Headline metrics */}
      <div style={{display:"grid",gridTemplateColumns:"repeat(6,1fr)",gap:12,marginBottom:24}}>
        {[
          {label:"Sent",     val:t.sends,    color:"#a3a6ff"},
          {label:"Replied",  val:t.replies,  color:"#69f6b8"},
          {label:"Opens",    val:t.opens,    color:"#ffe083"},
          {label:"Clicks",   val:t.clicks,   color:"#8b5cf6"},
          {label:"Bounces",  val:t.bounces,  color:"#ff6e84"},
          {label:"Unsubs",   val:t.unsubs,   color:"#ff6e84"},
        ].map(({label,val,color})=>(
          <div key={label} style={{background:"#0f1930",borderRadius:12,padding:18,
            borderLeft:`4px solid ${color}`,textAlign:"center"}}>
            <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:34,fontWeight:700,color,lineHeight:1}}>
              {val}
            </div>
            <div style={{fontSize:11,color:"#a3aac4",marginTop:6,letterSpacing:".05em",textTransform:"uppercase"}}>
              {label}
            </div>
          </div>
        ))}
      </div>

      {/* Reply rate banner */}
      <div style={{marginBottom:24,padding:"16px 24px",background:"#0f1930",borderRadius:12,
        display:"flex",alignItems:"center",justifyContent:"space-between",gap:14,flexWrap:"wrap"}}>
        <div>
          <div style={{fontSize:11,color:"#a3aac4",letterSpacing:".06em",textTransform:"uppercase",marginBottom:4}}>
            Reply rate over {data?.window_days||days} days
          </div>
          <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:42,fontWeight:700,color:"#69f6b8",lineHeight:1}}>
            {data?.reply_rate_pct||0}%
          </div>
        </div>
        <div style={{fontSize:11,color:"#40485d",textAlign:"right",maxWidth:380}}>
          Industry benchmark for cold "tried to call you" sequences sits 3-8%.
          Reply triggers an auto-flip of lead status to <span style={{color:"#69f6b8"}}>interested</span> + Slack ping.
        </div>
      </div>

      {/* Pending email queue — EOD batch send for failed-call leads */}
      <EligibleEmailQueue/>

      {/* Drip queue status — proves the auto-sequencer is working on past sends */}
      <EmailSequenceStatus/>

      {/* Email template editor — admin-edits the message that goes out on every send */}
      <EmailTemplateEditor/>

      {/* Two-column: recent sends + recent events */}
      <div style={{display:"grid",gridTemplateColumns:"3fr 2fr",gap:18,marginTop:24}}>
        <div style={{background:"#0f1930",borderRadius:12,overflow:"hidden"}}>
          <div style={{padding:"14px 20px",borderBottom:"1px solid #40485d20",
            display:"flex",alignItems:"center",justifyContent:"space-between"}}>
            <span style={{fontSize:12,color:"#a3aac4",letterSpacing:".08em",textTransform:"uppercase",fontWeight:700}}>
              Recent Sends
            </span>
            <span style={{fontSize:11,color:"#40485d"}}>{data?.recent_sends?.length||0} shown</span>
          </div>
          {(!data||data.recent_sends?.length===0)?(
            <div style={{padding:"36px 20px",textAlign:"center",color:"#40485d",fontSize:12}}>
              {setup?.send_path?.ready ? "No sends in this window — fire the email follow-up checkbox on a no-answer call to start." : "Resend not configured."}
            </div>
          ):(
            <div style={{maxHeight:480,overflowY:"auto"}}>
              {data.recent_sends.map((s,i)=>{
                let det = {}
                try{ det = typeof s.details==="string"?JSON.parse(s.details):(s.details||{}) }catch{}
                return (
                  <div key={i} style={{padding:"12px 20px",
                    borderBottom:i<data.recent_sends.length-1?"1px solid #40485d15":"none",
                    display:"grid",gridTemplateColumns:"100px 1fr",gap:14,fontSize:12,alignItems:"start"}}>
                    <span style={{color:"#a3aac4",fontSize:11}}>{fmtTime(s.created_at)}</span>
                    <div>
                      <div style={{color:"#dee5ff",fontWeight:600}}>{det.company||"—"}</div>
                      <div style={{color:"#a3aac4",fontSize:11,marginTop:2}}>{det.to_email||""}</div>
                      {det.trigger&&<div style={{fontSize:10,color:"#a3a6ff",marginTop:3}}>{det.trigger}</div>}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        <div style={{background:"#0f1930",borderRadius:12,overflow:"hidden"}}>
          <div style={{padding:"14px 20px",borderBottom:"1px solid #40485d20",
            display:"flex",alignItems:"center",justifyContent:"space-between"}}>
            <span style={{fontSize:12,color:"#a3aac4",letterSpacing:".08em",textTransform:"uppercase",fontWeight:700}}>
              Recent Events
            </span>
            <span style={{fontSize:11,color:"#40485d"}}>{data?.recent_events?.length||0} shown</span>
          </div>
          {(!data||data.recent_events?.length===0)?(
            <div style={{padding:"36px 20px",textAlign:"center",color:"#40485d",fontSize:12}}>
              No events yet (replies, bounces, opens). They'll appear here as Resend webhooks fire and IMAP polls find replies.
            </div>
          ):(
            <div style={{maxHeight:480,overflowY:"auto"}}>
              {data.recent_events.map((ev,i)=>{
                let det = {}
                try{ det = typeof ev.details==="string"?JSON.parse(ev.details):(ev.details||{}) }catch{}
                const eventType = (det.event||"").toLowerCase()
                const evColor = eventType==="reply"?"#69f6b8":eventType==="bounce"?"#ff6e84":eventType==="unsubscribe"?"#ff6e84":eventType==="click"?"#8b5cf6":"#ffe083"
                return (
                  <div key={i} style={{padding:"10px 20px",
                    borderBottom:i<data.recent_events.length-1?"1px solid #40485d15":"none",
                    fontSize:12}}>
                    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",gap:8}}>
                      <span style={{color:evColor,fontWeight:700,textTransform:"uppercase",fontSize:10,letterSpacing:".06em"}}>
                        {eventType||"event"}
                      </span>
                      <span style={{color:"#40485d",fontSize:10}}>{fmtTime(ev.created_at)}</span>
                    </div>
                    {det.from&&<div style={{color:"#dee5ff",fontSize:11,marginTop:3}}>{det.from}</div>}
                    {det.snippet&&<div style={{color:"#a3aac4",fontSize:11,marginTop:3,fontStyle:"italic"}}>"{det.snippet.slice(0,120)}"</div>}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function CampaignActivityPanel(){
  const [data,setData] = useState(null)
  const [open,setOpen] = useState(false)
  const [days,setDays] = useState(14)
  const [polling,setPolling] = useState(false)
  const [pollResult,setPollResult] = useState("")
  const load = (d)=>api(`/api/admin/campaigns/recent?days=${d}`).then(setData).catch(()=>setData(null))
  useEffect(()=>{ load(days) },[days])

  async function pollNow(){
    setPolling(true); setPollResult("")
    try{
      const r = await api("/api/admin/poll-replies",{method:"POST",body:"{}"})
      if(r.ok){
        const s = r.stats || {}
        setPollResult(`✓ Checked ${s.checked||0} unread, matched ${s.matched||0} replies, skipped ${s.auto_replies_skipped||0} auto-replies`)
        load(days)
      }else{
        setPollResult("⚠ "+(r.error||"poll failed"))
      }
    }catch(ex){ setPollResult("⚠ "+(ex.message||"error")) }
    finally{ setPolling(false) }
  }

  const t = data?.totals || {sends:0,replies:0,opens:0,clicks:0,bounces:0,unsubs:0}
  const fmtTime = (iso)=>iso?new Date(iso).toLocaleString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}):""

  return (
    <div style={{background:"#0f1930",borderRadius:16,overflow:"hidden",marginTop:24}}>
      <div onClick={()=>setOpen(o=>!o)}
        style={{padding:"18px 24px",cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"space-between",
          borderBottom:open?"1px solid #40485d20":"none"}}>
        <div style={{display:"flex",alignItems:"center",gap:12}}>
          <div style={{fontSize:"0.6rem",color:"#a3aac4",fontWeight:700,letterSpacing:".1em",textTransform:"uppercase"}}>
            Campaign Activity <span style={{color:"#ff6e84",fontSize:9,marginLeft:6}}>ADMIN</span>
          </div>
          {data&&!data.email_configured&&(
            <span style={{fontSize:10,color:"#ffe083",background:"#ffe08315",padding:"2px 8px",borderRadius:4}}>
              Resend not configured — sends disabled
            </span>
          )}
          {data&&data.email_configured&&!data.imap_configured&&(
            <span style={{fontSize:10,color:"#ffe083",background:"#ffe08315",padding:"2px 8px",borderRadius:4}}
              title="IMAP poller not configured — replies won't auto-flip lead status. Set IMAP_SERVER/USERNAME/PASSWORD in Railway.">
              IMAP not configured — replies won't auto-detect
            </span>
          )}
        </div>
        <div style={{display:"flex",gap:14,alignItems:"center",fontSize:12}}>
          <span style={{color:"#a3a6ff"}}>📧 {t.sends} sent</span>
          <span style={{color:"#69f6b8"}}>💬 {t.replies} replies</span>
          <span style={{color:"#ff6e84"}}>↩ {t.bounces} bounces</span>
          <span style={{color:"#a3aac4",fontSize:14}}>{open?"▾":"▸"}</span>
        </div>
      </div>
      {open&&(
        <div style={{padding:"20px 24px"}}>
          {/* Headline metrics */}
          <div style={{display:"grid",gridTemplateColumns:"repeat(6,1fr)",gap:10,marginBottom:18}}>
            {[
              {label:"Sent",     val:t.sends,    color:"#a3a6ff"},
              {label:"Replied",  val:t.replies,  color:"#69f6b8"},
              {label:"Opens",    val:t.opens,    color:"#ffe083"},
              {label:"Clicks",   val:t.clicks,   color:"#8b5cf6"},
              {label:"Bounces",  val:t.bounces,  color:"#ff6e84"},
              {label:"Unsubs",   val:t.unsubs,   color:"#ff6e84"},
            ].map(({label,val,color})=>(
              <div key={label} style={{background:"#060e20",borderRadius:8,padding:12,
                borderLeft:`3px solid ${color}`,textAlign:"center"}}>
                <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:22,fontWeight:700,color}}>{val}</div>
                <div style={{fontSize:10,color:"#a3aac4",marginTop:2}}>{label}</div>
              </div>
            ))}
          </div>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:14,flexWrap:"wrap",gap:10}}>
            <span style={{fontSize:12,color:"#a3aac4"}}>
              Reply rate: <b style={{color:"#69f6b8"}}>{data?.reply_rate_pct||0}%</b> over {data?.window_days||days}d
            </span>
            <div style={{display:"flex",gap:8,alignItems:"center"}}>
              {data?.imap_configured&&(
                <button onClick={pollNow} disabled={polling}
                  className="btn btn-g" style={{fontSize:11,padding:"5px 12px"}}
                  title="Force-check the inbox for new replies right now (otherwise the background poller runs every 10 min)">
                  {polling?"Checking…":"📬 Check Replies Now"}
                </button>
              )}
              <select className="sel" value={days} onChange={e=>setDays(Number(e.target.value))}
                style={{padding:"4px 10px",fontSize:11}}>
                <option value={7}>Last 7 days</option>
                <option value={14}>Last 14 days</option>
                <option value={30}>Last 30 days</option>
                <option value={90}>Last 90 days</option>
              </select>
            </div>
          </div>
          {pollResult&&(
            <div style={{marginBottom:12,padding:"8px 12px",fontSize:11,
              background:pollResult.startsWith("✓")?"#69f6b815":"#ffe08315",
              border:`1px solid ${pollResult.startsWith("✓")?"#69f6b830":"#ffe08330"}`,
              borderRadius:6,color:pollResult.startsWith("✓")?"#69f6b8":"#ffe083"}}>
              {pollResult}
            </div>
          )}
          {/* Recent sends */}
          <div style={{fontSize:11,color:"#a3aac4",letterSpacing:".08em",marginBottom:8}}>
            RECENT SENDS ({data?.recent_sends?.length||0})
          </div>
          {(!data||data.recent_sends?.length===0)?(
            <div style={{padding:"20px",textAlign:"center",color:"#40485d",fontSize:12,background:"#060e20",borderRadius:8}}>
              {data?.email_configured ? "No sends yet — they'll appear here once a caller fires the email follow-up."
                : "Set RESEND_API_KEY in Railway to enable email sends."}
            </div>
          ):(
            <div style={{maxHeight:240,overflowY:"auto",border:"1px solid #40485d20",borderRadius:8}}>
              {data.recent_sends.map((s,i)=>{
                let det = {}
                try{ det = typeof s.details==="string"?JSON.parse(s.details):(s.details||{}) }catch{}
                return (
                  <div key={i} style={{padding:"10px 14px",borderBottom:i<data.recent_sends.length-1?"1px solid #40485d15":"none",
                    display:"grid",gridTemplateColumns:"110px 1fr 1fr 1fr",gap:10,fontSize:11,alignItems:"center"}}>
                    <span style={{color:"#a3aac4"}}>{fmtTime(s.created_at)}</span>
                    <span style={{color:"#dee5ff"}}>{det.company||"—"}</span>
                    <span style={{color:"#a3aac4"}}>{det.to_email||"—"}</span>
                    <span style={{color:"#a3a6ff",fontSize:10}}>{det.trigger||""}</span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// Audit log viewer. Every action that writes to audit_log (cleanups,
// scrapes, kill-switch toggles, logins, backfills, dedupe sweeps,
// reassignments) is browsable here. Post-mortems for "who deleted X"
// stop needing direct Supabase access.
function AuditLogPanel(){
  const [entries,setEntries] = useState([])
  const [open,setOpen]       = useState(false)
  const [loading,setLoad]    = useState(false)
  const [filterAction,setFilterAction] = useState("")
  const [filterUser,setFilterUser]     = useState("")

  const load = ()=>{
    setLoad(true)
    const params = new URLSearchParams({limit:"200"})
    if(filterAction) params.set("action", filterAction)
    if(filterUser)   params.set("username", filterUser)
    api(`/api/admin/audit-log?${params}`)
      .then(r => setEntries(r?.entries || []))
      .catch(()=>setEntries([]))
      .finally(()=>setLoad(false))
  }
  useEffect(()=>{ if(open) load() },[open, filterAction, filterUser])

  const fmt = (iso)=>iso?new Date(iso).toLocaleString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}):""

  // Distinct actions in the visible set, so the dropdown is data-driven.
  const actions = Array.from(new Set(entries.map(e=>e.action).filter(Boolean))).sort()

  return (
    <div style={{background:"#0f1930",borderRadius:16,overflow:"hidden",marginTop:24}}>
      <div onClick={()=>setOpen(o=>!o)}
        style={{padding:"18px 24px",cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
        <div style={{fontSize:"0.6rem",color:"#a3aac4",fontWeight:700,letterSpacing:".1em",textTransform:"uppercase"}}>
          📜 Audit Log <span style={{color:"#ff6e84",fontSize:9,marginLeft:6}}>ADMIN</span>
        </div>
        <span style={{color:"#a3aac4",fontSize:14}}>{open?"▾":"▸"}</span>
      </div>
      {open&&(
        <div style={{borderTop:"1px solid #40485d20"}}>
          <div style={{padding:"12px 24px",display:"flex",gap:10,flexWrap:"wrap",alignItems:"center",
            borderBottom:"1px solid #40485d10"}}>
            <select className="sel" value={filterAction} onChange={e=>setFilterAction(e.target.value)}
              style={{fontSize:11,padding:"5px 10px"}}>
              <option value="">All actions</option>
              {actions.map(a=><option key={a} value={a}>{a}</option>)}
            </select>
            <input className="inp" placeholder="username" value={filterUser}
              onChange={e=>setFilterUser(e.target.value)}
              style={{fontSize:11,padding:"5px 10px",width:160}}/>
            <button className="btn btn-g" style={{fontSize:11,padding:"5px 12px"}} onClick={load}
              disabled={loading}>{loading?"…":"↻ Refresh"}</button>
            <div style={{fontSize:11,color:"#40485d",marginLeft:"auto"}}>
              {entries.length} most recent
            </div>
          </div>
          <div style={{maxHeight:480,overflowY:"auto"}}>
            {loading?(
              <div style={{padding:24,textAlign:"center",color:"#40485d",fontSize:12}}>Loading…</div>
            ):entries.length===0?(
              <div style={{padding:24,textAlign:"center",color:"#40485d",fontSize:12}}>No audit entries</div>
            ):entries.map((e,i)=>(
              <div key={e.id||i} style={{padding:"10px 24px",borderBottom:"1px solid #40485d08",
                display:"grid",gridTemplateColumns:"140px 140px 160px 1fr",gap:12,alignItems:"start",fontSize:12}}>
                <div style={{color:"#40485d"}}>{fmt(e.created_at)}</div>
                <div style={{color:"#a3a6ff",fontWeight:600}}>{e.action}</div>
                <div style={{color:"#a3aac4"}}>{e.username}</div>
                <div style={{color:"#a3aac4",fontFamily:"ui-monospace,SFMono-Regular,Menlo,monospace",
                  fontSize:11,whiteSpace:"pre-wrap",wordBreak:"break-word"}}>
                  {e.resource_type&&<span style={{color:"#40485d"}}>{e.resource_type}{e.resource_id?` ${e.resource_id}`:""} · </span>}
                  {e.details?JSON.stringify(e.details).slice(0,300):""}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function LoginActivityPanel(){
  const [logs,setLogs]=useState([])
  const [sessions,setSessions]=useState([])
  const [showLogs,setShowLogs]=useState(false)
  const [showSessions,setShowSessions]=useState(false)
  const [sessDays,setSessDays]=useState(0)

  const loadSessions=(days)=>{
    api(`/api/auth/sessions?days=${days}`).then(r=>setSessions(Array.isArray(r)?r:[])).catch(()=>{})
  }

  const fmtTime=(iso)=>iso?new Date(iso).toLocaleString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}):""
  const fmtDuration=(inTime,outTime)=>{
    if(!inTime||!outTime) return null
    const ms=new Date(outTime)-new Date(inTime)
    if(ms<0) return null
    const mins=Math.floor(ms/60000)
    if(mins<60) return `${mins}m`
    const hrs=Math.floor(mins/60)
    return `${hrs}h ${mins%60}m`
  }

  return(
    <>
    {/* ── Session Tracking (admin only) ── */}
    <div style={{background:"#0f1930",borderRadius:16,overflow:"hidden",marginTop:24}}>
      <div style={{padding:"18px 24px",display:"flex",alignItems:"center",justifyContent:"space-between",
        borderBottom:showSessions?"1px solid #40485d20":"none",cursor:"pointer"}}
        onClick={()=>{
          setShowSessions(p=>!p)
          if(!sessions.length) loadSessions(sessDays)
        }}>
        <div style={{fontSize:"0.6rem",color:"#a3aac4",fontWeight:700,letterSpacing:".1em",textTransform:"uppercase",
          display:"flex",alignItems:"center",gap:8}}>
          User Sessions
          <span style={{color:"#ff6e84",fontSize:9}}>ADMIN</span>
        </div>
        <span style={{color:"#40485d",fontSize:12,transition:"transform .2s",
          transform:showSessions?"rotate(180deg)":"rotate(0)"}}>{"\u25BC"}</span>
      </div>
      {showSessions&&(
        <div>
          <div style={{padding:"10px 24px",display:"flex",gap:8,borderBottom:"1px solid #40485d10"}}>
            {[{label:"Today",d:0},{label:"7 Days",d:7},{label:"30 Days",d:30}].map(p=>(
              <button key={p.d} className="btn" style={{fontSize:11,padding:"4px 12px",
                background:sessDays===p.d?"#a3a6ff22":"transparent",color:sessDays===p.d?"#a3a6ff":"#40485d",
                border:`1px solid ${sessDays===p.d?"#a3a6ff30":"#40485d20"}`,borderRadius:8}}
                onClick={()=>{setSessDays(p.d);loadSessions(p.d)}}>{p.label}</button>
            ))}
          </div>
          <div style={{maxHeight:400,overflowY:"auto"}}>
            {sessions.length===0?(
              <div style={{padding:32,textAlign:"center",color:"#40485d",fontSize:13}}>No sessions found</div>
            ):(
              sessions.map((s,i)=>{
                const isOnline=!s.signed_out
                const dur=fmtDuration(s.signed_in,s.signed_out)
                return(
                  <div key={s.id||i} style={{padding:"12px 24px",borderBottom:"1px solid #40485d08",
                    display:"flex",alignItems:"center",gap:14}}>
                    <div style={{width:8,height:8,borderRadius:"50%",flexShrink:0,
                      background:isOnline?"#69f6b8":"#40485d"}}/>
                    <div style={{flex:1,minWidth:0}}>
                      <div style={{display:"flex",alignItems:"center",gap:8}}>
                        <span style={{fontWeight:600,color:"#dee5ff",fontSize:14}}>{s.username}</span>
                        {isOnline&&<span style={{fontSize:9,padding:"1px 8px",borderRadius:10,fontWeight:600,
                          background:"#69f6b818",color:"#69f6b8",border:"1px solid #69f6b830"}}>ONLINE</span>}
                        {dur&&<span style={{fontSize:10,color:"#40485d"}}>{dur}</span>}
                      </div>
                      <div style={{fontSize:11,color:"#40485d",marginTop:2}}>
                        In: {fmtTime(s.signed_in)}{s.signed_out?` · Out: ${fmtTime(s.signed_out)}`:""}
                      </div>
                    </div>
                  </div>
                )
              })
            )}
          </div>
        </div>
      )}
    </div>

    {/* ── Login Activity (admin only) ── */}
    <div style={{background:"#0f1930",borderRadius:16,overflow:"hidden",marginTop:24}}>
      <div style={{padding:"18px 24px",display:"flex",alignItems:"center",justifyContent:"space-between",
        borderBottom:showLogs?"1px solid #40485d20":"none",cursor:"pointer"}}
        onClick={()=>{
          setShowLogs(p=>!p)
          if(!logs.length) api("/api/auth/login-log").then(r=>setLogs(Array.isArray(r)?r:[])).catch(()=>{})
        }}>
        <div style={{fontSize:"0.6rem",color:"#a3aac4",fontWeight:700,letterSpacing:".1em",textTransform:"uppercase",
          display:"flex",alignItems:"center",gap:8}}>
          Login Activity
          <span style={{color:"#ff6e84",fontSize:9}}>ADMIN</span>
        </div>
        <span style={{color:"#40485d",fontSize:12,transition:"transform .2s",
          transform:showLogs?"rotate(180deg)":"rotate(0)"}}>{"\u25BC"}</span>
      </div>
      {showLogs&&(
        <div style={{maxHeight:360,overflowY:"auto"}}>
          {logs.length===0?(
            <div style={{padding:32,textAlign:"center",color:"#40485d",fontSize:13}}>No login activity yet</div>
          ):(
            logs.map((log,i)=>{
              const isSuccess=log.status==="success"
              const isFailed=log.status==="failed"
              const isBlocked=log.status==="blocked"
              const color=isSuccess?"#69f6b8":isBlocked?"#ff6e84":isFailed?"#ffe083":"#a3aac4"
              const icon=isSuccess?"\u2713":isBlocked?"\u2717":isFailed?"!":"\u2022"
              return(
                <div key={log.id||i} style={{padding:"12px 24px",borderBottom:"1px solid #40485d08",
                  display:"flex",alignItems:"center",gap:14}}>
                  <div style={{width:28,height:28,borderRadius:"50%",background:color+"18",flexShrink:0,
                    display:"flex",alignItems:"center",justifyContent:"center",
                    fontSize:12,fontWeight:700,color}}>{icon}</div>
                  <div style={{flex:1,minWidth:0}}>
                    <div style={{display:"flex",alignItems:"center",gap:8}}>
                      <span style={{fontWeight:600,color:"#dee5ff",fontSize:14}}>{log.username}</span>
                      <span style={{fontSize:10,padding:"1px 8px",borderRadius:10,fontWeight:600,
                        background:color+"18",color,border:`1px solid ${color}30`}}>
                        {log.status}
                      </span>
                      {log.role&&<span style={{fontSize:10,color:"#40485d"}}>{log.role}</span>}
                    </div>
                    {log.detail&&<div style={{fontSize:11,color:"#40485d",marginTop:2}}>{log.detail}</div>}
                  </div>
                  <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:11,color:"#40485d",flexShrink:0,textAlign:"right"}}>
                    {log.logged_at?new Date(log.logged_at).toLocaleString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}):""}
                  </div>
                </div>
              )
            })
          )}
        </div>
      )}
    </div>
    </>
  )
}

function FabBtn({onClick}){
  const [hov,setHov]=useState(false)
  return(
    <button onClick={onClick}
      onMouseEnter={()=>setHov(true)} onMouseLeave={()=>setHov(false)}
      style={{position:"fixed",bottom:32,right:32,zIndex:50,
        display:"flex",alignItems:"center",gap:12,
        background:"#69f6b8",color:"#005a3c",
        padding:"16px 24px",borderRadius:999,border:"none",cursor:"pointer",
        fontFamily:"'Space Grotesk',sans-serif",fontWeight:700,fontSize:15,
        boxShadow:`0 10px 40px -10px rgba(105,246,184,${hov?".65":".45"})`,
        transform:hov?"scale(1.04)":"scale(1)",transition:"all .2s"}}>
      <IconPlus/>
      New Prospect
    </button>
  )
}
