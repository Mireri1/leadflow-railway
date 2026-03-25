import { useState, useEffect, useRef, useCallback } from "react"

const API_BASE = ""

const STATES = [
  "","AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
  "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
  "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
  "VA","WA","WV","WI","WY"
]

const STATUS_OPTIONS = [
  { value:"new",            label:"New",            color:"#a3a6ff" },
  { value:"called",         label:"Called",          color:"#ffe083" },
  { value:"no_answer",      label:"No Answer",       color:"#a3aac4" },
  { value:"interested",     label:"Interested",      color:"#69f6b8" },
  { value:"not_interested", label:"Not Interested",  color:"#ff6e84" },
  { value:"callback",       label:"Callback",        color:"#8b5cf6" },
  { value:"converted",      label:"Converted",       color:"#06d6a0" },
]

const CALL_OUTCOMES = [
  { value:"answered",       label:"Answered" },
  { value:"no_answer",      label:"No Answer" },
  { value:"voicemail",      label:"Left Voicemail" },
  { value:"callback",       label:"Requested Callback" },
  { value:"interested",     label:"Interested" },
  { value:"not_interested", label:"Not Interested" },
  { value:"converted",      label:"Converted!" },
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

// ─── LeadFinder ──────────────────────────────────────────────────────────────

function LeadFinder({onFound, industries}){
  const [industry,setIndustry] = useState(industries[0]||"Healthcare")
  const [state,setState]       = useState("")
  const [limit,setLimit]       = useState(25)
  const [loading,setLoad]      = useState(false)
  const [lastResult,setLast]   = useState(null)

  async function find(){
    setLoad(true); setLast(null)
    try {
      const res = await api("/api/scrape",{method:"POST",body:JSON.stringify({industry,state,limit,source:"sam"})})
      setLast(res)
      if(res.saved > 0) onFound()
    } catch(ex){ alert("Error: "+ex.message) }
    finally { setLoad(false) }
  }

  return(
    <div className="finder">
      <div className="finder-title">🔍 Find Leads</div>
      <div className="finder-sub">Pull fresh leads from government databases — no CSV needed</div>
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr auto",gap:12,alignItems:"flex-end"}}>
        <div className="ff">
          <label>Industry</label>
          <select value={industry} onChange={e=>setIndustry(e.target.value)} className="sel" style={{color:"#dee5ff"}}>
            {industries.map(i=><option key={i} value={i}>{i}</option>)}
          </select>
        </div>
        <div className="ff">
          <label>State (optional)</label>
          <select value={state} onChange={e=>setState(e.target.value)} className="sel" style={{color:state?"#dee5ff":"#a3aac4"}}>
            <option value="">All States</option>
            {STATES.filter(s=>s).map(s=><option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="range-wrap">
          <label style={{fontSize:10,letterSpacing:".1em",textTransform:"uppercase",color:"#a3aac4",display:"flex",justifyContent:"space-between"}}>
            <span>How Many</span><span style={{color:"#a3a6ff"}}>{limit}</span>
          </label>
          <input type="range" min={25} max={200} step={25} value={limit} onChange={e=>setLimit(Number(e.target.value))}/>
          <div style={{display:"flex",justifyContent:"space-between",fontSize:9,color:"#40485d"}}><span>25</span><span>200</span></div>
        </div>
        <button className="btn btn-p" onClick={find} disabled={loading} style={{padding:"10px 22px",whiteSpace:"nowrap",alignSelf:"flex-end"}}>
          {loading?"Searching…":"Find Leads →"}
        </button>
      </div>
      {loading&&<div style={{marginTop:14,display:"flex",alignItems:"center",gap:8,fontSize:12,color:"#a3aac4"}}>
        <div className="pulse" style={{width:6,height:6,borderRadius:"50%",background:"#a3a6ff"}}/>Pulling records…
      </div>}
      {lastResult&&!loading&&(
        <div style={{marginTop:14,padding:"10px 14px",background:"#69f6b815",border:"1px solid #69f6b830",borderRadius:8,fontSize:12,color:"#69f6b8"}}>
          ✓ {lastResult.saved} leads saved — ready to call
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

function CallModal({lead,onClose,onSaved}){
  const [calls,setCalls]          = useState([])
  const [outcome,setOutcome]      = useState("answered")
  const [notes,setNotes]          = useState("")
  const [cbDate,setCbDate]        = useState("")
  const [duration,setDur]         = useState("")
  const [saving,setSave]          = useState(false)
  const [tab,setTab]              = useState("call")
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
  },[lead.id])

  // Outcomes that require qualification data
  const REQUIRES_QUAL = ["interested", "converted", "callback"]
  const hasQualData = budgetFocus || vendorStatus || decisionMaker || timeline || qualified

  async function log(){
    // Enforce qualification for key outcomes
    if(REQUIRES_QUAL.includes(outcome) && !hasQualData){
      alert(`"${outcome}" requires qualification data.\n\nSwitch to the Qualify tab and fill out at least one field before saving.`)
      return
    }
    if(outcome==="callback" && !cbDate){
      alert("Callback requires a date. Please set a callback date.")
      return
    }
    setSave(true)
    try{
      setTimerRunning(false)
      const finalDuration = duration ? parseInt(duration)*60 : timerSeconds
      const callPayload = {
        leadId:lead.id, outcome, notes,
        duration:finalDuration,
        callbackDate:outcome==="callback"?cbDate:"",
        calledBy:getUser(), calledAt:new Date().toISOString(),
        budgetfocus: budgetFocus||null, vendorstatus: vendorStatus||null,
        decisionmaker: decisionMaker||null, timeline: timeline||null,
        qualified: qualified||null,
        followupsequence: followUpSeq||null,
        script_id: scriptId ? parseInt(scriptId) : null,
        converted: outcome === "converted",
      }
      await api("/api/calls",{method:"POST",body:JSON.stringify(callPayload)})
      if(scriptId) {
        api(`/api/scripts/${scriptId}/use`,{method:"POST",body:JSON.stringify({})}).catch(()=>{})
      }
      const statusMap={answered:"called",no_answer:"no_answer",voicemail:"no_answer",
        callback:"callback",interested:"interested",not_interested:"not_interested",converted:"converted"}
      const fuDays = FOLLOW_UP_DAYS[followUpSeq]
      const nextFollowUp = fuDays ? addDays(fuDays[0]) : (outcome==="callback"?cbDate:"")
      await api(`/api/leads/${lead.id}`,{method:"PATCH",body:JSON.stringify({
        status:statusMap[outcome]||"called",
        callbackDate:outcome==="callback"?cbDate:nextFollowUp||"",
        followupsequence: followUpSeq||null,
        nextfollowup: nextFollowUp||null,
        followupstep: fuDays ? 0 : null,
        ...(!lead.assignedTo ? {assignedTo: getUser()} : {}),
        updatedAt:new Date().toISOString()
      })})
      onSaved(); onClose()
    }catch(ex){alert("Error: "+ex.message)}
    finally{setSave(false)}
  }

  const selectedScript = scripts.find(s=>s.id===parseInt(scriptId))
  const si=v=>STATUS_OPTIONS.find(s=>s.value===v)||STATUS_OPTIONS[0]

  return(
    <div className="modal-bg" onClick={e=>e.target===e.currentTarget&&onClose()}>
      <div className="modal" style={{maxWidth:640}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:16}}>
          <div>
            <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:16,fontWeight:700,color:"#dee5ff",marginBottom:4}}>
              {[lead.firstName,lead.lastName].filter(Boolean).join(" ")||lead.company}
            </div>
            <div style={{display:"flex",gap:12,fontSize:12,color:"#a3aac4"}}>
              {lead.phone&&<span>📞 {lead.phone}</span>}
              {lead.email&&<span>✉ {lead.email}</span>}
            </div>
          </div>
          <button className="btn btn-g" style={{fontSize:12,padding:"5px 10px"}} onClick={onClose}>✕</button>
        </div>

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

        <div style={{display:"flex",gap:4,marginBottom:16,borderBottom:"1px solid #40485d30",paddingBottom:12}}>
          {[["call","📞 Log Call"],["qualify","🎯 Qualify"]].map(([t,l])=>(
            <button key={t} onClick={()=>setTab(t)}
              style={{padding:"6px 14px",borderRadius:6,fontSize:12,fontFamily:"inherit",cursor:"pointer",
                background:tab===t?"#a3a6ff":"transparent",color:tab===t?"#000011":"#a3aac4",
                border:`1px solid ${tab===t?"#a3a6ff":"#40485d40"}`}}>
              {l}
            </button>
          ))}
        </div>

        {tab==="call"&&(
          <div style={{background:"#060e20",border:"1px solid #a3a6ff20",borderRadius:10,padding:16,marginBottom:16}}>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:10,marginBottom:10}}>
              <div className="ff"><label>Outcome</label>
                <select value={outcome} onChange={e=>setOutcome(e.target.value)}>
                  {CALL_OUTCOMES.map(o=><option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
              <div className="ff"><label>Duration</label>
                <div style={{display:"flex",alignItems:"center",gap:8}}>
                  <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:22,fontWeight:700,
                    color:timerRunning?"#69f6b8":"#a3a6ff",letterSpacing:".05em",
                    background:"#060e20",border:"1px solid #40485d40",borderRadius:6,
                    padding:"4px 12px",minWidth:70,textAlign:"center"}}>
                    {timerDisplay}
                  </div>
                  <button type="button" onClick={()=>setTimerRunning(r=>!r)}
                    style={{fontSize:11,padding:"6px 10px",borderRadius:6,border:"1px solid #40485d40",
                      background:"transparent",color:"#a3aac4",cursor:"pointer",fontFamily:"inherit"}}>
                    {timerRunning?"Pause":"Resume"}
                  </button>
                  <span style={{fontSize:10,color:"#40485d"}}>or</span>
                  <input type="number" value={duration} onChange={e=>setDur(e.target.value)}
                    placeholder="min" min="0" style={{width:50,fontSize:12}}/>
                </div>
              </div>
            </div>
            {outcome==="callback"&&(
              <div className="ff" style={{marginBottom:10}}>
                <label>Callback Date</label>
                <input type="date" value={cbDate} onChange={e=>setCbDate(e.target.value)}/>
              </div>
            )}
            {outcome==="converted"&&(
              <div className="ff" style={{marginBottom:10}}>
                <label>Contract Value ($)</label>
                <input type="number" value={contractValue} onChange={e=>setContractValue(e.target.value)} placeholder="2400" min="0"/>
              </div>
            )}
            <div className="ff" style={{marginBottom:12}}>
              <label>Notes</label>
              <textarea value={notes} onChange={e=>setNotes(e.target.value)} rows={2} style={{resize:"vertical"}}
                placeholder="Spoke to gatekeeper, call back Tuesday..."/>
            </div>
            <div className="ff" style={{marginBottom:12}}>
              <label>Follow-up Sequence</label>
              <select value={followUpSeq} onChange={e=>setFuSeq(e.target.value)}>
                {FOLLOW_UP_SEQUENCES.map(s=><option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
            {followUpSeq&&(
              <div style={{fontSize:11,color:"#ffe083",background:"#ffe08310",border:"1px solid #ffe08325",borderRadius:6,padding:"8px 12px",marginBottom:12}}>
                🔔 Next follow-up: <strong>{addDays(FOLLOW_UP_DAYS[followUpSeq][0])}</strong>
                {" · then "}
                {FOLLOW_UP_DAYS[followUpSeq].slice(1).map(d=>"+"+d+"d").join(" · ")}
              </div>
            )}
            {REQUIRES_QUAL.includes(outcome)&&!hasQualData&&(
              <div style={{background:"#2d1a0a",border:"1px solid #92400e",borderRadius:6,
                padding:"8px 12px",marginBottom:10,fontSize:12,color:"#fbbf24"}}>
                ⚠ "{outcome}" requires qualification — fill out the <strong
                  style={{cursor:"pointer",textDecoration:"underline"}}
                  onClick={()=>setTab("qualify")}>Qualify tab</strong> first
              </div>
            )}
            <button className="btn btn-p" onClick={log} disabled={saving||
              (REQUIRES_QUAL.includes(outcome)&&!hasQualData)}>{saving?"Saving…":"Log Call ↵"}</button>
          </div>
        )}
        {tab==="qualify"&&(
          <div style={{background:"#060e20",border:"1px solid #69f6b825",borderRadius:10,padding:16,marginBottom:16,display:"flex",flexDirection:"column",gap:14}}>
            <div style={{fontSize:10,color:"#69f6b8",letterSpacing:".1em"}}>QUALIFICATION DATA</div>
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
            <button className="btn btn-p" onClick={log} disabled={saving} style={{alignSelf:"flex-start"}}>
              {saving?"Saving…":"Save ↵"}
            </button>
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
  { key:"dialer",    label:"Dialer",          Icon:IconPhone },
  { key:"future",    label:"Future Follow-Ups", Icon:IconCalendarFar },
  { key:"qualified", label:"Qualified",        Icon:IconClipCheck },
  { key:"analytics", label:"Analytics",       Icon:IconChart },
  { key:"history",   label:"History",         Icon:IconHistory },
]

export default function App(){
  const [user,setUser]             = useState(()=>isLoggedIn()?localStorage.getItem("lf_user"):null)
  const [leads,setLeads]           = useState([])
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
  const [sortBy,setSort]           = useState("score")
  const [cbOnly,setCbOnly]         = useState(false)
  const [fIndustry,setFIndustry]   = useState("")
  const [fState,setFState]         = useState("")
  const [availableOnly,setAvailOnly] = useState(false)
  const [activeNav,setNav]         = useState("dashboard")
  const [callHistory,setCallHistory] = useState([])
  const [histData,setHistData]       = useState(null)
  const [histLoading,setHistLoading] = useState(false)
  const [histCaller,setHistCaller]   = useState("")
  const [histFrom,setHistFrom]       = useState("")
  const [histTo,setHistTo]           = useState("")
  const [histRange,setHistRange]     = useState("week")
  const [dialerIdx,setDialerIdx]   = useState(0)
  const [showNotifs,setShowNotifs]  = useState(false)
  const [leaderboard,setLeaderboard] = useState([])
  const [lbLoading,setLbLoading]   = useState(false)
  const [qualifiedCalls,setQualifiedCalls] = useState([])
  const [qualLoading,setQualLoading] = useState(false)

  function notify(msg,type="success"){ setToast({msg,type}); setTimeout(()=>setToast(null),3200) }

  useEffect(()=>{
    if(user) api("/api/industries").then(r=>setIndustries(r.industries||[])).catch(()=>{})
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
    api("/api/leaderboard").then(r=>setLeaderboard(Array.isArray(r)?r:[])).catch(()=>{}).finally(()=>setLbLoading(false))
  },[activeNav,user])

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

  if(!user) return <Login onLogin={u=>setUser(u)}/>

  const si=v=>STATUS_OPTIONS.find(s=>s.value===v)||STATUS_OPTIONS[0]
  const today=new Date().toISOString().split("T")[0]
  const tomorrow=(()=>{const d=new Date();d.setDate(d.getDate()+1);return d.toISOString().split("T")[0]})()
  const threeDays=(()=>{const d=new Date();d.setDate(d.getDate()+3);return d.toISOString().split("T")[0]})()

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
  },[leads.length>0&&today])

  const displayLeads=leads.filter(l=>{
    if(fIndustry&&l.industry!==fIndustry) return false
    if(fState&&l.state!==fState) return false
    if(availableOnly&&l.assignedTo&&l.assignedTo!==user) return false
    return true
  })

  function reset(){setSearch("");setFIndustry("");setFState("");setFStatus("all");setCbOnly(false);setAvailOnly(false)}

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
            placeholder="Global Search…"
            style={{background:"#000011",border:"none",borderRadius:12,
              padding:"8px 16px 8px 36px",color:"#dee5ff",fontSize:13,
              fontFamily:"'Inter',sans-serif",outline:"none",width:220}}/>
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
          onClick={()=>{if(window.confirm("Sign out?")){{localStorage.clear();setUser(null)}}}}
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
            {label:"Account",    Icon:IconPerson, action:()=>{if(window.confirm("Sign out?")){{localStorage.clear();setUser(null)}}}},
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
              <StatsBar stats={stats} onCallbacks={()=>{setNav("leads");setCbOnly(p=>!p)}}/>

              {/* Daily Call Target */}
              {(()=>{
                const target=50
                const done=stats?.callsToday||0
                const pct=Math.min(Math.round(done/target*100),100)
                return(
                  <div style={{background:"#0f1930",borderRadius:12,padding:"16px 20px",marginTop:16}}>
                    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:8}}>
                      <span style={{fontSize:"0.6rem",color:"#a3aac4",fontWeight:700,letterSpacing:".1em",
                        textTransform:"uppercase"}}>Daily Call Target</span>
                      <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:14,fontWeight:700,
                        color:pct>=100?"#69f6b8":pct>=50?"#ffe083":"#a3a6ff"}}>{done} / {target}</span>
                    </div>
                    <div style={{height:8,background:"#141f38",borderRadius:4,overflow:"hidden"}}>
                      <div style={{height:"100%",width:`${pct}%`,borderRadius:4,transition:"width .3s",
                        background:pct>=100?"#69f6b8":pct>=50?"#ffe083":"#a3a6ff"}}/>
                    </div>
                    {pct>=100&&<div style={{fontSize:11,color:"#69f6b8",marginTop:6,fontWeight:600}}>Target hit! Keep going.</div>}
                  </div>
                )
              })()}

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
            </div>
          )}

          {/* ── LEADS ───────────────────────────────────────────────────────── */}
          {activeNav==="leads"&&(
            <div>
              <div style={{marginBottom:28}}>
                <h1 style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:36,fontWeight:700,
                  color:"#dee5ff",letterSpacing:"-.02em",lineHeight:1.1}}>Leads</h1>
                <p style={{color:"#a3aac4",fontSize:14,marginTop:4}}>
                  {leads.length} prospect{leads.length!==1?"s":""} in your pipeline
                </p>
              </div>

              <section style={{background:"#141f38",borderRadius:16,padding:"16px 20px",
                display:"flex",flexWrap:"wrap",alignItems:"center",gap:12,marginBottom:20}}>
                <div style={{flex:"1 1 200px",position:"relative",display:"flex",alignItems:"center"}}>
                  <span style={{position:"absolute",left:12,color:"#40485d",display:"flex"}}><IconFilter/></span>
                  <input value={search} onChange={e=>setSearch(e.target.value)}
                    placeholder="Filter by company or contact…"
                    style={{width:"100%",background:"#000011",border:"1px solid #40485d30",
                      borderRadius:8,padding:"8px 12px 8px 32px",color:"#dee5ff",
                      fontSize:13,fontFamily:"'Inter',sans-serif",outline:"none"}}/>
                </div>
                <div style={{display:"flex",gap:10,alignItems:"center",flexWrap:"wrap"}}>
                  <select className="sel" value={fIndustry} onChange={e=>setFIndustry(e.target.value)}>
                    <option value="">Industry: All</option>
                    {(industries.length>0?industries:INDUSTRIES).map(i=><option key={i} value={i}>{i}</option>)}
                  </select>
                  <select className="sel" value={fState} onChange={e=>setFState(e.target.value)}>
                    <option value="">State: All States</option>
                    {STATES.filter(s=>s).map(s=><option key={s} value={s}>{s}</option>)}
                  </select>
                  <select className="sel" value={fStatus} onChange={e=>setFStatus(e.target.value)}>
                    <option value="all">Status: All</option>
                    {STATUS_OPTIONS.map(s=><option key={s.value} value={s.value}>{s.label}</option>)}
                  </select>
                  <select className="sel" value={sortBy} onChange={e=>setSort(e.target.value)}>
                    <option value="score">Highest Score</option>
                    <option value="newest">Newest First</option>
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
                ):(
                  displayLeads.map(lead=>{
                    const info=si(lead.status)
                    const isCb=lead.callbackDate&&lead.callbackDate<=today&&lead.status!=="converted"
                    const score=lead.score||scoreLead(lead)||0
                    const ac=avatarColor(lead.company||lead.firstName||"?")
                    const isMine=!lead.assignedTo||lead.assignedTo===user
                    const takenBy=!isMine?lead.assignedTo:null
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
                            <div style={{fontSize:13,color:"#a3aac4",marginTop:1}}>{lead.company||"—"}</div>
                            <div style={{display:"flex",gap:5,marginTop:4,flexWrap:"wrap"}}>
                              {takenBy&&<span style={{fontSize:9,background:"#ff6e8420",color:"#ff6e84",padding:"2px 7px",borderRadius:4,border:"1px solid #ff6e8430"}}>🔒 {takenBy}</span>}
                              {!takenBy&&lead.assignedTo&&<span style={{fontSize:9,background:"#69f6b818",color:"#69f6b8",padding:"2px 7px",borderRadius:4}}>✓ mine</span>}
                              {lead.source&&<span className="src-tag">{lead.source}</span>}
                              {isCb&&<span style={{fontSize:9,background:"#8b5cf618",color:"#8b5cf6",padding:"2px 7px",borderRadius:4,border:"1px solid #8b5cf630"}}>🔔 {lead.callbackDate}</span>}
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
                          <IconBtn onClick={e=>{e.stopPropagation();setEditModal(lead)}} title="Edit lead"><IconEdit/></IconBtn>
                          <IconBtn onClick={e=>{e.stopPropagation();deleteL(lead.id)}} title="Delete"
                            hoverColor="#ff6e84" baseColor="#40485d"><IconTrash/></IconBtn>
                        </div>
                      </div>
                    )
                  })
                )}
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

          {/* ── DIALER ──────────────────────────────────────────────────────── */}
          {activeNav==="dialer"&&(
            <div>
              <div style={{marginBottom:24}}>
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
              {(()=>{
                const dialerLeads=leads.filter(l=>!l.assignedTo||l.assignedTo===user)
                if(dialerLeads.length===0) return(
                  <div style={{background:"#0f1930",borderRadius:16,padding:72,textAlign:"center"}}>
                    <div style={{fontSize:40,marginBottom:12}}>✅</div>
                    <div style={{color:"#a3aac4",fontSize:14,marginBottom:8}}>
                      {leads.length>0?"All leads have been claimed by your team!":"No leads to dial yet"}
                    </div>
                    <div style={{color:"#40485d",fontSize:12,marginBottom:20}}>
                      {leads.length>0?`${leads.length} lead${leads.length!==1?"s":""} total, all assigned`:"Import a CSV or use Find Leads to get started"}
                    </div>
                    <button className="btn btn-p" onClick={()=>setNav("leads")}>View All Leads</button>
                  </div>
                )
                const idx=Math.min(dialerIdx,dialerLeads.length-1)
                const lead=dialerLeads[idx]
                const score=lead.score||scoreLead(lead)||0
                const ac=avatarColor(lead.company||lead.firstName||"?")
                const info=STATUS_OPTIONS.find(s=>s.value===lead.status)||STATUS_OPTIONS[0]
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
                      <div style={{display:"flex",justifyContent:"center",gap:10,marginBottom:24,flexWrap:"wrap"}}>
                        <ScoreRing score={score}/>
                        <span className="pill" style={{background:info.color+"20",color:info.color,border:`1px solid ${info.color}30`}}>{info.label}</span>
                        {lead.industry&&<span className="pill" style={{background:"#a3a6ff18",color:"#a3a6ff"}}>{lead.industry}</span>}
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
                    </div>
                    <div style={{display:"flex",gap:12}}>
                      <button className="btn btn-g" style={{flex:1,padding:11}}
                        onClick={()=>setDialerIdx(i=>Math.max(0,i-1))}
                        disabled={idx===0}>← Previous</button>
                      <button className="btn btn-g" style={{flex:1,padding:11}}
                        onClick={()=>setDialerIdx(i=>Math.min(dialerLeads.length-1,i+1))}
                        disabled={idx>=dialerLeads.length-1}>Skip →</button>
                    </div>
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
                    const qualColor=call.qualified==="Hot"?"#ff6e84":call.qualified==="Warm"?"#ffe083":
                      call.qualified==="Not Yet"?"#a3a6ff":"#40485d"
                    const qualChips=[
                      {label:"Focus",val:call.budgetfocus},
                      {label:"Vendor",val:call.vendorstatus},
                      {label:"Contact",val:call.decisionmaker},
                      {label:"Timeline",val:call.timeline},
                      {label:"Qualified",val:call.qualified},
                    ].filter(c=>c.val)
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
                                {lead.company}{lead.industry?` \u00b7 ${lead.industry}`:""}{lead.state?` \u00b7 ${lead.state}`:""}
                              </div>
                            </div>
                          </div>
                          <div style={{display:"flex",alignItems:"center",gap:12,flexShrink:0}}>
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
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {/* ── FUTURE FOLLOW-UPS ───────────────────────────────────────────── */}
          {activeNav==="future"&&(()=>{
            const sixMonths=addDays(180)
            const futureLeads=leads
              .filter(l=>l.callbackDate&&l.callbackDate>=sixMonths&&l.status!=="converted")
              .sort((a,b)=>a.callbackDate.localeCompare(b.callbackDate))

            function monthsAway(dateStr){
              const diff=Math.round((new Date(dateStr)-new Date())/(1000*60*60*24*30))
              return diff<=1?"~1 month":diff+" months"
            }

            return(
              <div>
                <div style={{marginBottom:28,display:"flex",alignItems:"flex-end",
                  justifyContent:"space-between",gap:20,flexWrap:"wrap"}}>
                  <div>
                    <h1 style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:36,fontWeight:700,
                      color:"#dee5ff",letterSpacing:"-.02em",lineHeight:1.1}}>Future Follow-Ups</h1>
                    <p style={{color:"#a3aac4",fontSize:14,marginTop:4}}>
                      Leads scheduled 6+ months out
                    </p>
                  </div>
                  {futureLeads.length>0&&(
                    <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:28,fontWeight:700,
                      color:"#a3a6ff"}}>{futureLeads.length}</div>
                  )}
                </div>

                {futureLeads.length===0?(
                  <div style={{background:"#0f1930",borderRadius:16,padding:72,textAlign:"center"}}>
                    <div style={{fontSize:40,marginBottom:12}}>&#x1f4c5;</div>
                    <div style={{color:"#a3aac4",fontSize:14}}>No future follow-ups scheduled</div>
                    <div style={{color:"#40485d",fontSize:12,marginTop:6}}>
                      When you log a callback date 6+ months away, it will appear here
                    </div>
                  </div>
                ):(
                  <div style={{background:"#0f1930",borderRadius:12,overflow:"hidden"}}>
                    <div style={{display:"grid",gridTemplateColumns:"3fr 2fr 1fr 2fr 2fr",
                      padding:"10px 24px",fontSize:"0.55rem",fontWeight:700,
                      color:"#a3aac4",textTransform:"uppercase",letterSpacing:".08em",
                      borderBottom:"1px solid #40485d20"}}>
                      <div>Contact &amp; Company</div>
                      <div>Scheduled</div>
                      <div>Status</div>
                      <div>Notes</div>
                      <div style={{textAlign:"right"}}>Actions</div>
                    </div>
                    {futureLeads.map(lead=>{
                      const ac=avatarColor(lead.company||lead.firstName||"?")
                      const info=STATUS_OPTIONS.find(s=>s.value===lead.status)||STATUS_OPTIONS[0]
                      return(
                        <div key={lead.id}
                          style={{display:"grid",gridTemplateColumns:"3fr 2fr 1fr 2fr 2fr",
                            padding:"16px 24px",alignItems:"center",gap:16,
                            borderBottom:"1px solid #40485d10",transition:"background .12s"}}
                          onMouseEnter={e=>e.currentTarget.style.background="#192540"}
                          onMouseLeave={e=>e.currentTarget.style.background="transparent"}>
                          <div style={{display:"flex",alignItems:"center",gap:12,minWidth:0}}>
                            <div style={{width:38,height:38,borderRadius:"50%",background:ac+"22",flexShrink:0,
                              display:"flex",alignItems:"center",justifyContent:"center",
                              fontSize:12,fontWeight:700,color:ac,fontFamily:"'Space Grotesk',sans-serif"}}>
                              {getInitials(lead)}</div>
                            <div style={{minWidth:0}}>
                              <div style={{fontWeight:600,color:"#dee5ff",fontSize:14,
                                overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                                {[lead.firstName,lead.lastName].filter(Boolean).join(" ")||lead.company}</div>
                              <div style={{fontSize:12,color:"#a3aac4"}}>{lead.company}</div>
                            </div>
                          </div>
                          <div>
                            <div style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:13,
                              fontWeight:700,color:"#a3a6ff"}}>{lead.callbackDate}</div>
                            <div style={{fontSize:11,color:"#40485d",marginTop:2}}>
                              {monthsAway(lead.callbackDate)} away
                            </div>
                          </div>
                          <span className="pill" style={{background:info.color+"20",color:info.color,
                            border:`1px solid ${info.color}30`,width:"fit-content"}}>{info.label}</span>
                          <div style={{fontSize:12,color:"#a3aac4",overflow:"hidden",
                            display:"-webkit-box",WebkitLineClamp:2,WebkitBoxOrient:"vertical"}}>
                            {lead.notes||<span style={{color:"#40485d"}}>{"\u2014"}</span>}
                          </div>
                          <div style={{display:"flex",gap:6,justifyContent:"flex-end",flexWrap:"wrap"}}>
                            <button className="btn btn-g"
                              style={{fontSize:11,padding:"6px 12px",whiteSpace:"nowrap"}}
                              onClick={async()=>{
                                const d=window.prompt(
                                  `Move follow-up for ${lead.company||lead.firstName} to when?\n(YYYY-MM-DD)`,
                                  addDays(30)
                                )
                                if(d&&d.match(/^\d{4}-\d{2}-\d{2}$/)){
                                  await api(`/api/leads/${lead.id}`,{method:"PATCH",body:JSON.stringify({
                                    callbackDate:d,status:"callback",updatedAt:new Date().toISOString()})})
                                  setLeads(p=>p.map(l=>l.id===lead.id?{...l,callbackDate:d,status:"callback"}:l))
                                  notify("Follow-up moved to "+d)
                                }
                              }}>
                              Bring Forward
                            </button>
                            <IconBtn onClick={()=>setCallModal(lead)} title="Log call"><IconPhone/></IconBtn>
                            <IconBtn onClick={()=>setEditModal(lead)} title="Edit"><IconEdit/></IconBtn>
                          </div>
                        </div>
                      )
                    })}
                    <div style={{padding:"12px 24px",borderTop:"1px solid #40485d20",
                      display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                      <span style={{fontSize:12,color:"#40485d"}}>
                        Earliest: <span style={{color:"#a3a6ff",fontFamily:"'Space Grotesk',sans-serif",fontWeight:600}}>{futureLeads[0]?.callbackDate}</span>
                        {" \u00b7 "}Latest: <span style={{color:"#a3a6ff",fontFamily:"'Space Grotesk',sans-serif",fontWeight:600}}>{futureLeads[futureLeads.length-1]?.callbackDate}</span>
                      </span>
                      <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:13,
                        fontWeight:700,color:"#a3a6ff"}}>{futureLeads.length} lead{futureLeads.length!==1?"s":""}</span>
                    </div>
                  </div>
                )}
              </div>
            )
          })()}

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
                    🏆 Rep Leaderboard — Today
                  </div>
                  <button className="btn btn-g" style={{fontSize:11,padding:"5px 12px"}}
                    onClick={()=>{
                      setLbLoading(true)
                      api("/api/leaderboard").then(r=>setLeaderboard(Array.isArray(r)?r:[])).catch(()=>{}).finally(()=>setLbLoading(false))
                    }}>Refresh</button>
                </div>
                {lbLoading?(
                  <div style={{padding:48,textAlign:"center",color:"#40485d"}}>Loading…</div>
                ):leaderboard.length===0?(
                  <div style={{padding:48,textAlign:"center",color:"#40485d",fontSize:13}}>
                    No call data yet — start logging calls to see rep stats
                  </div>
                ):(
                  <>
                    {/* Header row */}
                    <div style={{display:"grid",gridTemplateColumns:"2fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr",
                      padding:"9px 24px",fontSize:"0.5rem",fontWeight:700,color:"#a3aac4",
                      textTransform:"uppercase",letterSpacing:".08em",borderBottom:"1px solid #40485d15"}}>
                      <div>Rep</div>
                      <div style={{textAlign:"center"}}>Today</div>
                      <div style={{textAlign:"center"}}>All-Time</div>
                      <div style={{textAlign:"center"}}>Converted</div>
                      <div style={{textAlign:"center"}}>Interested</div>
                      <div style={{textAlign:"center"}}>Conv %</div>
                      <div style={{textAlign:"center"}}>Contact %</div>
                      <div style={{textAlign:"center"}}>Avg Talk</div>
                      <div style={{textAlign:"center"}}>No Answer</div>
                      <div style={{textAlign:"center"}}>Callbacks</div>
                    </div>
                    {leaderboard.map((rep,i)=>{
                      const medal=i===0?"🥇":i===1?"🥈":i===2?"🥉":null
                      const ac=avatarColor(rep.name)
                      const convPct=parseFloat(rep.conv_rate)||0
                      return(
                        <div key={rep.name}
                          style={{display:"grid",gridTemplateColumns:"2fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr",
                            padding:"14px 24px",alignItems:"center",
                            borderBottom:"1px solid #40485d10",
                            background:i===0?"#ffe08306":i===1?"#ffffff04":"transparent",
                            transition:"background .12s"}}
                          onMouseEnter={e=>e.currentTarget.style.background="#192540"}
                          onMouseLeave={e=>e.currentTarget.style.background=i===0?"#ffe08306":i===1?"#ffffff04":"transparent"}>
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
                          {/* Calls today */}
                          <div style={{textAlign:"center"}}>
                            <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:18,fontWeight:700,
                              color:rep.calls_today>0?"#a3a6ff":"#40485d"}}>{rep.calls_today}</span>
                          </div>
                          {/* All-time */}
                          <div style={{textAlign:"center"}}>
                            <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:14,fontWeight:600,color:"#dee5ff"}}>{rep.total_calls}</span>
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
                          {/* Avg talk time */}
                          <div style={{textAlign:"center"}}>
                            <span style={{fontFamily:"'Space Grotesk',sans-serif",fontSize:13,
                              color:rep.avg_talk_time>0?"#dee5ff":"#40485d"}}>
                              {rep.avg_talk_time>0?`${Math.floor(rep.avg_talk_time/60)}m${rep.avg_talk_time%60?` ${rep.avg_talk_time%60}s`:""}`:"—"}
                            </span>
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
                                {rep.last_call?` \u00b7 last call ${new Date(rep.last_call).toLocaleDateString()}`:" \u00b7 no calls logged"}
                                {rep.days_inactive>3&&(
                                  <span style={{color:"#ff6e84",marginLeft:6}}>({rep.days_inactive}d inactive)</span>
                                )}
                              </div>
                            </div>
                            <div style={{display:"flex",gap:6,flexShrink:0}}>
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
                              {rep.status==="inactive"&&rep.leads>0&&(
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
