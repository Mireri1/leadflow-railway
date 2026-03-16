import { useState, useEffect, useRef, useCallback } from "react"

const API_BASE = ""

const STATES = [
  "","AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
  "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
  "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
  "VA","WA","WV","WI","WY"
]

const STATUS_OPTIONS = [
  { value:"new",            label:"New",            color:"#6366f1" },
  { value:"called",         label:"Called",          color:"#f59e0b" },
  { value:"no_answer",      label:"No Answer",       color:"#94a3b8" },
  { value:"interested",     label:"Interested",      color:"#10b981" },
  { value:"not_interested", label:"Not Interested",  color:"#ef4444" },
  { value:"callback",       label:"Callback",        color:"#8b5cf6" },
  { value:"converted",      label:"Converted",       color:"#059669" },
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
function scoreColor(s){return s>=75?"#ef4444":s>=50?"#f59e0b":s>=25?"#6366f1":"#475569"}
function scoreLabel(s){return s>=75?"Hot":s>=50?"Warm":s>=25?"Cool":"Cold"}

function getUser()  { return localStorage.getItem("lf_user") || "" }
function getToken() { return localStorage.getItem("lf_token") || "" }
function isLoggedIn(){ return !!localStorage.getItem("lf_token") }

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
  @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@700;800&display=swap');
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{background:#080810;color:#dde1f0;font-family:'DM Mono',monospace;font-size:13px}
  ::-webkit-scrollbar{width:4px}::-webkit-scrollbar-track{background:#0f1018}::-webkit-scrollbar-thumb{background:#2a2d3e;border-radius:4px}
  input,select,textarea,button{font-family:inherit}
  .ff{display:flex;flex-direction:column;gap:4px}
  .ff label{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:#525878}
  .ff input,.ff select,.ff textarea{background:#0d0e18;border:1px solid #1e2236;color:#dde1f0;padding:9px 11px;border-radius:7px;font-size:12px;outline:none;transition:border-color .15s}
  .ff input:focus,.ff select:focus,.ff textarea:focus{border-color:#5b5fef;box-shadow:0 0 0 3px #5b5fef18}
  .ff select option{background:#0d0e18}
  .btn{cursor:pointer;border:none;font-family:inherit;font-size:12px;font-weight:500;letter-spacing:.04em;transition:all .15s;border-radius:7px}
  .btn-p{background:#5b5fef;color:#fff;padding:9px 20px}.btn-p:hover{background:#7478f5;transform:translateY(-1px)}
  .btn-g{background:transparent;color:#6b7194;padding:8px 14px;border:1px solid #1e2236}.btn-g:hover{background:#1e2236;color:#dde1f0}
  .btn-r{background:transparent;color:#f06060;padding:5px 10px;border:1px solid #f0606025;font-size:11px}.btn-r:hover{background:#f0606012}
  .btn-gr{background:#1a9e6e;color:#fff;padding:9px 20px}.btn-gr:hover{background:#22c98a}
  .btn-amber{background:#f0b42918;color:#f0b429;border:1px solid #f0b42930;padding:6px 14px;font-size:11px}.btn-amber:hover{background:#f0b42928}
  .card{background:#0d0e1a;border:1px solid #181b2e;border-radius:12px;padding:18px 22px}
  .pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:10px;letter-spacing:.07em;font-weight:500}
  .qs{background:transparent;border:1px solid #1e2236;font-size:10px;padding:3px 7px;border-radius:5px;cursor:pointer;font-family:inherit;transition:all .1s}.qs:hover{background:#1e2236}
  .modal-bg{position:fixed;inset:0;background:#00000090;backdrop-filter:blur(4px);z-index:200;display:flex;align-items:center;justify-content:center;padding:20px}
  .modal{background:#0d0e1a;border:1px solid #1e2236;border-radius:16px;padding:28px;width:100%;max-width:600px;max-height:90vh;overflow-y:auto}
  .toast{position:fixed;bottom:24px;right:24px;padding:11px 18px;background:#141628;border:1px solid #2a2d4a;border-radius:10px;font-size:12px;z-index:9999;animation:fadeUp .2s ease}
  @keyframes fadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
  .dropzone{background:#0d0e18;border:2px dashed #1e2236;border-radius:12px;padding:36px;text-align:center;cursor:pointer;transition:all .2s}.dropzone:hover{border-color:#5b5fef;background:#5b5fef08}
  .sel{background:#0d0e1a;border:1px solid #1e2236;color:#6b7194;padding:8px 11px;border-radius:7px;font-size:12px;font-family:inherit;cursor:pointer;outline:none}
  .lrow{background:#0d0e1a;border:1px solid #181b2e;border-radius:10px;padding:14px 18px;transition:border-color .15s}.lrow:hover{border-color:#5b5fef28}
  .lrow-cb{border-color:#8b5cf635!important;background:#8b5cf608!important}
  .src-tag{font-size:9px;background:#181b2e;color:#525878;padding:2px 6px;border-radius:4px;letter-spacing:.05em}
  .finder{background:linear-gradient(135deg,#0d0e1a 0%,#0f1022 100%);border:1px solid #5b5fef30;border-radius:14px;padding:24px;margin-bottom:22px}
  .finder-title{font-family:'Syne',sans-serif;font-size:18px;font-weight:800;color:#fff;margin-bottom:4px}
  .finder-sub{font-size:11px;color:#525878;margin-bottom:20px}
  .range-wrap{display:flex;flex-direction:column;gap:6px}
  .range-wrap input[type=range]{-webkit-appearance:none;width:100%;height:4px;border-radius:2px;background:#1e2236;outline:none}
  .range-wrap input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:16px;height:16px;border-radius:50%;background:#5b5fef;cursor:pointer}
  .pulse{animation:pulse 1.5s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
`

function ScoreRing({score=0}){
  const c=scoreColor(score),l=scoreLabel(score)
  return(
    <div style={{display:"flex",flexDirection:"column",alignItems:"center",gap:3}}>
      <div style={{width:44,height:44,borderRadius:"50%",display:"flex",alignItems:"center",justifyContent:"center",
        background:c+"18",border:`2px solid ${c}35`,color:c,fontFamily:"'Syne',sans-serif",fontSize:13,fontWeight:800}}>
        {score}
      </div>
      <div style={{fontSize:9,color:c,letterSpacing:".06em"}}>{l}</div>
    </div>
  )
}

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
      onLogin(res.username)
    } catch(ex){ setErr(ex.message) }
    finally{ setLoad(false) }
  }

  return(
    <div style={{minHeight:"100vh",display:"flex",alignItems:"center",justifyContent:"center",background:"#080810"}}>
      <style>{CSS}</style>
      <div style={{width:340}}>
        <div style={{textAlign:"center",marginBottom:32}}>
          <div style={{fontFamily:"'Syne',sans-serif",fontSize:34,fontWeight:800,color:"#fff",letterSpacing:"-.02em"}}>
            LEAD<span style={{color:"#5b5fef"}}>FLOW</span>
          </div>
          <div style={{fontSize:10,color:"#525878",letterSpacing:".12em",marginTop:4}}>B2B COLD CALL PLATFORM</div>
        </div>
        <div className="card" style={{border:"1px solid #5b5fef28"}}>
          <form onSubmit={submit} style={{display:"flex",flexDirection:"column",gap:14}}>
            <div className="ff"><label>Your Name</label>
              <input value={name} onChange={e=>setName(e.target.value)} placeholder="e.g. Alice" autoFocus/>
            </div>
            <div className="ff"><label>Team Password</label>
              <input type="password" value={pass} onChange={e=>setPass(e.target.value)} placeholder="••••••••"/>
            </div>
            {err&&<div style={{color:"#f06060",fontSize:11}}>⚠ {err}</div>}
            <button type="submit" className="btn btn-p" style={{padding:"11px",fontSize:13}} disabled={loading}>
              {loading?"Signing in...":"Sign In →"}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}

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
          <select value={industry} onChange={e=>setIndustry(e.target.value)} className="sel" style={{color:"#dde1f0"}}>
            {industries.map(i=><option key={i} value={i}>{i}</option>)}
          </select>
        </div>
        <div className="ff">
          <label>State (optional)</label>
          <select value={state} onChange={e=>setState(e.target.value)} className="sel" style={{color:state?"#dde1f0":"#525878"}}>
            <option value="">All States</option>
            {STATES.filter(s=>s).map(s=><option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="range-wrap">
          <label style={{fontSize:10,letterSpacing:".1em",textTransform:"uppercase",color:"#525878",display:"flex",justifyContent:"space-between"}}>
            <span>How Many</span><span style={{color:"#5b5fef"}}>{limit}</span>
          </label>
          <input type="range" min={25} max={200} step={25} value={limit} onChange={e=>setLimit(Number(e.target.value))}/>
          <div style={{display:"flex",justifyContent:"space-between",fontSize:9,color:"#2a2d3e"}}><span>25</span><span>200</span></div>
        </div>
        <button className="btn btn-p" onClick={find} disabled={loading} style={{padding:"10px 22px",whiteSpace:"nowrap",alignSelf:"flex-end"}}>
          {loading?"Searching...":"Find Leads →"}
        </button>
      </div>
      {loading&&<div style={{marginTop:14,display:"flex",alignItems:"center",gap:8,fontSize:12,color:"#6b7194"}}>
        <div className="pulse" style={{width:6,height:6,borderRadius:"50%",background:"#5b5fef"}}/>Pulling records...
      </div>}
      {lastResult&&!loading&&(
        <div style={{marginTop:14,padding:"10px 14px",background:"#1a9e6e15",border:"1px solid #1a9e6e30",borderRadius:8,fontSize:12,color:"#1a9e6e"}}>
          ✓ {lastResult.saved} leads saved — ready to call
        </div>
      )}
    </div>
  )
}

const FOLLOW_UP_SEQUENCES = [
  { value:"", label:"No Follow-up Sequence" },
  { value:"48h-5d-7d", label:"Standard: 48h → 5 days → 7 days" },
  { value:"48h-7d-14d", label:"Slow Burn: 48h → 7 days → 14 days" },
  { value:"24h-48h-5d", label:"Hot Lead: 24h → 48h → 5 days" },
]

const FOLLOW_UP_DAYS = {
  "48h-5d-7d":  [2, 5, 7],
  "48h-7d-14d": [2, 7, 14],
  "24h-48h-5d": [1, 2, 5],
}

function addDays(days){
  const d = new Date()
  d.setDate(d.getDate() + days)
  return d.toISOString().split("T")[0]
}

function QualChip({label, value, options, onChange}){
  return(
    <div style={{display:"flex",flexDirection:"column",gap:4}}>
      <div style={{fontSize:9,letterSpacing:".1em",textTransform:"uppercase",color:"#525878"}}>{label}</div>
      <div style={{display:"flex",gap:4,flexWrap:"wrap"}}>
        {options.map(o=>(
          <button key={o} onClick={()=>onChange(value===o?"":o)}
            style={{padding:"4px 10px",borderRadius:5,fontSize:11,fontFamily:"inherit",cursor:"pointer",
              background:value===o?"#5b5fef":"transparent",
              color:value===o?"#fff":"#6b7194",
              border:`1px solid ${value===o?"#5b5fef":"#1e2236"}`,
              transition:"all .1s"}}>
            {o}
          </button>
        ))}
      </div>
    </div>
  )
}

function CallModal({lead,onClose,onSaved}){
  const [calls,setCalls]          = useState([])
  const [outcome,setOutcome]      = useState("answered")
  const [notes,setNotes]          = useState("")
  const [cbDate,setCbDate]        = useState("")
  const [duration,setDur]         = useState("")
  const [saving,setSave]          = useState(false)
  const [tab,setTab]              = useState("call")
  const [followUpSeq,setFuSeq]    = useState("")
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

  async function log(){
    setSave(true)
    try{
      await api("/api/calls",{method:"POST",body:JSON.stringify({
        leadId:lead.id, outcome, notes,
        duration:duration?parseInt(duration)*60:0,
        callbackDate:outcome==="callback"?cbDate:"",
        calledBy:getUser(), calledAt:new Date().toISOString(),
        budgetFocus, vendorStatus, decisionMaker, timeline, qualified,
        followUpSequence: followUpSeq,
        script_id: scriptId ? parseInt(scriptId) : null,
        converted: outcome === "converted",
        contract_value: outcome === "converted" && contractValue ? parseFloat(contractValue) : null,
      })})
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
        followUpSequence: followUpSeq||null,
        nextFollowUp: nextFollowUp||null,
        followUpStep: fuDays ? 0 : null,
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
            <div style={{fontFamily:"Syne,sans-serif",fontSize:15,fontWeight:800,color:"#fff",marginBottom:4}}>
              {[lead.firstName,lead.lastName].filter(Boolean).join(" ")||lead.company}
            </div>
            <div style={{display:"flex",gap:12,fontSize:11,color:"#525878"}}>
              {lead.phone&&<span>📞 {lead.phone}</span>}
              {lead.email&&<span>✉ {lead.email}</span>}
            </div>
          </div>
          <button className="btn btn-g" style={{fontSize:11,padding:"5px 10px"}} onClick={onClose}>✕</button>
        </div>

        {/* Script selector */}
        {scripts.length>0&&(
          <div style={{marginBottom:16,background:"#080810",border:"1px solid #5b5fef25",borderRadius:10,padding:14}}>
            <div style={{fontSize:10,color:"#5b5fef",letterSpacing:".1em",marginBottom:10}}>CALL SCRIPT</div>
            <div style={{display:"flex",gap:8,marginBottom:scriptId?10:0}}>
              <select value={scriptId} onChange={e=>{setScriptId(e.target.value);setShowScript(false)}}
                style={{flex:1,background:"#0d0e1a",border:"1px solid #1e2236",color:"#dde1f0",padding:"8px 10px",borderRadius:7,fontSize:12,fontFamily:"inherit"}}>
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
              <div style={{background:"#0d0e1a",border:"1px solid #1e2236",borderRadius:8,padding:14,fontSize:12,color:"#a0a3b8",lineHeight:1.8,whiteSpace:"pre-wrap",maxHeight:200,overflowY:"auto"}}>
                {selectedScript.script_text}
                {selectedScript.objection_handlers?.length>0&&(
                  <div style={{marginTop:12,borderTop:"1px solid #1e2236",paddingTop:10}}>
                    <div style={{fontSize:10,color:"#5b5fef",letterSpacing:".08em",marginBottom:8}}>OBJECTION HANDLERS</div>
                    {selectedScript.objection_handlers.map((obj,i)=>(
                      <div key={i} style={{marginBottom:8}}>
                        <div style={{color:"#f0b429",fontSize:11}}>"{obj.objection}"</div>
                        <div style={{color:"#a0a3b8",fontSize:11,paddingLeft:10}}>→ {obj.response}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        <div style={{display:"flex",gap:4,marginBottom:16,borderBottom:"1px solid #1e2236",paddingBottom:12}}>
          {[["call","📞 Log Call"],["qualify","🎯 Qualify"]].map(([t,l])=>(
            <button key={t} onClick={()=>setTab(t)}
              style={{padding:"6px 14px",borderRadius:6,fontSize:11,fontFamily:"inherit",cursor:"pointer",
                background:tab===t?"#5b5fef":"transparent",color:tab===t?"#fff":"#6b7194",
                border:`1px solid ${tab===t?"#5b5fef":"#1e2236"}`}}>
              {l}
            </button>
          ))}
        </div>
        {tab==="call"&&(
          <div style={{background:"#080810",border:"1px solid #5b5fef25",borderRadius:10,padding:16,marginBottom:16}}>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:10,marginBottom:10}}>
              <div className="ff"><label>Outcome</label>
                <select value={outcome} onChange={e=>setOutcome(e.target.value)}>
                  {CALL_OUTCOMES.map(o=><option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
              <div className="ff"><label>Duration (min)</label>
                <input type="number" value={duration} onChange={e=>setDur(e.target.value)} placeholder="5" min="0"/>
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
              <div style={{fontSize:11,color:"#f0b429",background:"#f0b42910",border:"1px solid #f0b42925",borderRadius:6,padding:"8px 12px",marginBottom:12}}>
                🔔 Next follow-up: <strong>{addDays(FOLLOW_UP_DAYS[followUpSeq][0])}</strong>
                {" · then "}
                {FOLLOW_UP_DAYS[followUpSeq].slice(1).map(d=>"+"+d+"d").join(" · ")}
              </div>
            )}
            <button className="btn btn-p" onClick={log} disabled={saving}>{saving?"Saving...":"Log Call ↵"}</button>
          </div>
        )}
        {tab==="qualify"&&(
          <div style={{background:"#080810",border:"1px solid #10b98125",borderRadius:10,padding:16,marginBottom:16,display:"flex",flexDirection:"column",gap:14}}>
            <div style={{fontSize:10,color:"#10b981",letterSpacing:".1em"}}>QUALIFICATION DATA</div>
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
              {saving?"Saving...":"Save ↵"}
            </button>
          </div>
        )}
        {calls.length>0&&(
          <>
            <div style={{fontSize:10,color:"#525878",letterSpacing:".08em",marginBottom:10}}>HISTORY ({calls.length})</div>
            <div style={{display:"flex",flexDirection:"column",gap:6,maxHeight:200,overflowY:"auto"}}>
              {calls.map(c=>{
                const info=si(c.outcome?.replace("voicemail","no_answer").replace("answered","called"))
                return(
                  <div key={c.id} style={{background:"#080810",border:"1px solid #181b2e",borderRadius:8,padding:"10px 12px"}}>
                    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:4}}>
                      <span className="pill" style={{background:info.color+"20",color:info.color,border:`1px solid ${info.color}35`}}>
                        {(c.outcome||"").replace(/_/g," ")}
                      </span>
                      <div style={{fontSize:10,color:"#525878"}}>
                        {new Date(c.calledAt).toLocaleDateString()}
                        {c.duration>0&&<span style={{marginLeft:8}}>{Math.round(c.duration/60)}m</span>}
                        <span style={{marginLeft:8,color:"#2a2d3e"}}>· {c.calledBy}</span>
                      </div>
                    </div>
                    {c.notes&&<div style={{fontSize:11,color:"#6b7194",marginBottom:4}}>{c.notes}</div>}
                    {(c.budgetFocus||c.vendorStatus||c.decisionMaker||c.timeline||c.qualified)&&(
                      <div style={{display:"flex",gap:4,flexWrap:"wrap"}}>
                        {[c.budgetFocus,c.vendorStatus,c.decisionMaker,c.timeline,c.qualified].filter(Boolean).map((t,i)=>(
                          <span key={i} style={{fontSize:9,background:"#1e2236",color:"#94a3b8",padding:"2px 6px",borderRadius:4}}>{t}</span>
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
          <div style={{fontFamily:"'Syne',sans-serif",fontSize:15,fontWeight:800}}>{isEdit?"EDIT LEAD":"NEW LEAD"}</div>
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
          <div className="ff"><label>Source</label><input {...f("source")} placeholder="SAM.gov..."/></div>
        </div>
        <div className="ff" style={{marginBottom:18}}>
          <label>Notes</label>
          <textarea {...f("notes")} rows={2} style={{resize:"vertical"}} placeholder="Notes..."/>
        </div>
        <div style={{display:"flex",gap:8}}>
          <button className="btn btn-p" onClick={save} disabled={saving}>{saving?"Saving...":isEdit?"Save Changes":"Add Lead"}</button>
          <button className="btn btn-g" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  )
}

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
          <div style={{fontFamily:"'Syne',sans-serif",fontSize:15,fontWeight:800}}>IMPORT CSV</div>
          <button className="btn btn-g" onClick={onClose}>✕</button>
        </div>
        {!preview?(
          <div className="dropzone" onClick={()=>fileRef.current?.click()}>
            <div style={{fontSize:28,marginBottom:8}}>↑</div>
            <div style={{color:"#6b7194",fontSize:13}}>Click to upload CSV</div>
            <input ref={fileRef} type="file" accept=".csv" style={{display:"none"}} onChange={handleFile}/>
          </div>
        ):(
          <div>
            <div style={{padding:14,background:"#080810",border:"1px solid #1a9e6e30",borderRadius:8,marginBottom:14}}>
              <div style={{color:"#1a9e6e",fontSize:13,marginBottom:8}}>✓ {preview.length} leads parsed</div>
              {preview.slice(0,4).map((l,i)=>(
                <div key={i} style={{fontSize:11,color:"#6b7194",borderLeft:"2px solid #5b5fef",paddingLeft:8,marginBottom:3}}>
                  {[l.firstName,l.lastName,l.company,l.phone].filter(Boolean).join(" · ")}
                </div>
              ))}
              {preview.length>4&&<div style={{fontSize:11,color:"#2a2d3e"}}>...+{preview.length-4} more</div>}
            </div>
            <div className="ff" style={{marginBottom:14,maxWidth:220}}>
              <label>Assign all to (optional)</label>
              <input value={assignTo} onChange={e=>setAT(e.target.value)} placeholder="Alice"/>
            </div>
            <div style={{display:"flex",gap:8}}>
              <button className="btn btn-gr" onClick={doImport} disabled={loading}>
                {loading?"Importing...":"Import "+preview.length+" Leads"}
              </button>
              <button className="btn btn-g" onClick={()=>{setPrev(null);if(fileRef.current)fileRef.current.value=""}}>Re-upload</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

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

  function openNew(){
    setEditing("new")
    setForm({name:"",industry:"",script_text:"",objection_handlers:[]})
  }

  function openEdit(s){
    setEditing(s.id)
    setForm({name:s.name,industry:s.industry||"",script_text:s.script_text,objection_handlers:s.objection_handlers||[]})
  }

  function addObjection(){
    if(!newObj.objection||!newObj.response) return
    setForm(f=>({...f,objection_handlers:[...f.objection_handlers,{...newObj}]}))
    setNewObj({objection:"",response:""})
  }

  function removeObjection(i){
    setForm(f=>({...f,objection_handlers:f.objection_handlers.filter((_,idx)=>idx!==i)}))
  }

  async function save(){
    if(!form.name||!form.script_text){ showToast("Name and script required"); return }
    setSaving(true)
    try{
      if(editing==="new") await api("/api/scripts",{method:"POST",body:JSON.stringify(form)})
      else await api(`/api/scripts/${editing}`,{method:"PATCH",body:JSON.stringify(form)})
      showToast("✓ Saved")
      setEditing(null)
      fetchScripts()
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
          <div style={{fontFamily:"'Syne',sans-serif",fontSize:15,fontWeight:800}}>CALL SCRIPTS</div>
          <div style={{display:"flex",gap:8}}>
            {!editing&&<button className="btn btn-p" style={{fontSize:11}} onClick={openNew}>+ New Script</button>}
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
              <div style={{fontSize:10,color:"#5b5fef",letterSpacing:".1em",marginBottom:10}}>OBJECTION HANDLERS</div>
              {form.objection_handlers.map((obj,i)=>(
                <div key={i} style={{background:"#080810",border:"1px solid #1e2236",borderRadius:8,padding:10,marginBottom:8}}>
                  <div style={{display:"flex",justifyContent:"space-between",marginBottom:4}}>
                    <div style={{fontSize:11,color:"#f0b429"}}>"{obj.objection}"</div>
                    <button onClick={()=>removeObjection(i)} style={{background:"none",border:"none",color:"#f0606060",cursor:"pointer",fontSize:12}}>✕</button>
                  </div>
                  <div style={{fontSize:11,color:"#a0a3b8"}}>→ {obj.response}</div>
                </div>
              ))}
              <div style={{display:"grid",gridTemplateColumns:"1fr 1fr auto",gap:8,marginTop:8}}>
                <input value={newObj.objection} onChange={e=>setNewObj(o=>({...o,objection:e.target.value}))}
                  placeholder="We already have a cleaner..."
                  style={{background:"#0d0e18",border:"1px solid #1e2236",color:"#dde1f0",padding:"8px 10px",borderRadius:7,fontSize:11}}/>
                <input value={newObj.response} onChange={e=>setNewObj(o=>({...o,response:e.target.value}))}
                  placeholder="That's great — what I've found is..."
                  style={{background:"#0d0e18",border:"1px solid #1e2236",color:"#dde1f0",padding:"8px 10px",borderRadius:7,fontSize:11}}/>
                <button className="btn btn-g" style={{fontSize:11}} onClick={addObjection}>+ Add</button>
              </div>
            </div>
            <div style={{display:"flex",gap:8,justifyContent:"flex-end"}}>
              <button className="btn btn-g" onClick={()=>setEditing(null)}>Cancel</button>
              <button className="btn btn-p" onClick={save} disabled={saving}>{saving?"Saving...":"Save Script"}</button>
            </div>
            {toast&&<div style={{marginTop:10,fontSize:12,color:"#10b981"}}>{toast}</div>}
          </div>
        ):loading?(
          <div style={{textAlign:"center",padding:40,color:"#525878"}}>Loading...</div>
        ):scripts.length===0?(
          <div style={{textAlign:"center",padding:40,color:"#525878"}}>
            <div style={{fontSize:32,marginBottom:12}}>📋</div>
            <div style={{fontSize:13,marginBottom:16}}>No scripts yet</div>
            <button className="btn btn-p" onClick={openNew}>Create your first script</button>
          </div>
        ):(
          <div style={{display:"flex",flexDirection:"column",gap:8}}>
            {scripts.map(s=>(
              <div key={s.id} style={{background:"#080810",border:"1px solid #1e2236",borderRadius:10,padding:14}}>
                <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:8}}>
                  <div>
                    <div style={{fontSize:13,fontWeight:600,color:"#dde1f0",marginBottom:3}}>{s.name}</div>
                    <div style={{display:"flex",gap:8,fontSize:10,color:"#525878"}}>
                      {s.industry&&<span style={{background:"#5b5fef18",color:"#5b5fef",padding:"1px 6px",borderRadius:4}}>{s.industry}</span>}
                      <span>{s.usage_count||0} uses</span>
                      {s.objection_handlers?.length>0&&<span>{s.objection_handlers.length} objection{s.objection_handlers.length!==1?"s":""}</span>}
                    </div>
                  </div>
                  <div style={{display:"flex",gap:6}}>
                    <button className="btn btn-g" style={{fontSize:11,padding:"4px 10px"}} onClick={()=>openEdit(s)}>Edit</button>
                    <button className="btn btn-r" style={{fontSize:11,padding:"4px 10px"}} onClick={()=>del(s.id)}>Del</button>
                  </div>
                </div>
                <div style={{fontSize:11,color:"#525878",lineHeight:1.6,maxHeight:60,overflow:"hidden"}}>
                  {s.script_text.slice(0,150)}{s.script_text.length>150?"...":""}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function StatsBar({stats,onCallbacks}){
  if(!stats) return null
  return(
    <div style={{display:"flex",gap:10,flexWrap:"wrap",marginBottom:22}}>
      {[
        {l:"TOTAL",      v:stats.total,             c:"#5b5fef"},
        {l:"NEW TODAY",  v:stats.newToday,           c:"#f0b429"},
        {l:"CALLS TODAY",v:stats.callsToday,         c:"#94a3b8"},
        {l:"CALLBACKS",  v:stats.callbacksDue,       c:"#8b5cf6",onClick:onCallbacks},
        {l:"INTERESTED", v:stats.interested,         c:"#10b981"},
        {l:"CONVERTED",  v:stats.converted,          c:"#059669"},
        {l:"CONV RATE",  v:stats.conversionRate+"%", c:"#dde1f0"},
      ].map(s=>(
        <div key={s.l} className="card" onClick={s.onClick}
          style={{flex:"1 1 90px",minWidth:90,padding:"14px 16px",cursor:s.onClick?"pointer":"default"}}>
          <div style={{fontSize:9,letterSpacing:".1em",color:"#525878",marginBottom:6}}>{s.l}</div>
          <div style={{fontFamily:"'Syne',sans-serif",fontWeight:800,fontSize:28,color:s.c}}>{s.v}</div>
        </div>
      ))}
    </div>
  )
}

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
      try {
        const statsData = await api("/api/stats")
        if(statsData) setStats(statsData)
      } catch(e) {}
    }catch(ex){ notify("Error loading leads","error") }
    finally{ setLoad(false) }
  },[search,fStatus,sortBy,cbOnly])

  useEffect(()=>{
    if(!user) return
    const t=setTimeout(loadLeads,search?350:0)
    return()=>clearTimeout(t)
  },[user,loadLeads])

  async function quickStatus(lead,status){
    let cbDate=""
    if(status==="callback") cbDate=window.prompt("Callback date (YYYY-MM-DD):",new Date().toISOString().split("T")[0])||""
    try{
      await api(`/api/leads/${lead.id}`,{method:"PATCH",body:JSON.stringify({status,callbackDate:cbDate,updatedAt:new Date().toISOString()})})
      setLeads(p=>p.map(l=>l.id===lead.id?{...l,status,callbackDate:cbDate}:l))
      setTimeout(loadLeads, 500)
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

  return(
    <div style={{minHeight:"100vh",background:"#080810"}}>
      <style>{CSS}</style>
      <div style={{borderBottom:"1px solid #181b2e",padding:"14px 28px",display:"flex",alignItems:"center",gap:12}}>
        <div style={{fontFamily:"'Syne',sans-serif",fontSize:19,fontWeight:800,color:"#fff",letterSpacing:"-.02em"}}>
          LEAD<span style={{color:"#5b5fef"}}>FLOW</span>
        </div>
        <div style={{flex:1}}/>
        {stats?.callbacksDue>0&&(
          <button className="btn-amber btn" onClick={()=>setCbOnly(p=>!p)}>
            🔔 {stats.callbacksDue} callback{stats.callbacksDue!==1?"s":""} due
          </button>
        )}
        <button className="btn btn-g" style={{fontSize:11}} onClick={()=>setShowScripts(true)}>📋 Scripts</button>
        <button className="btn btn-g" style={{fontSize:11}} onClick={()=>setImport(true)}>↑ Import CSV</button>
        <button className="btn btn-p" style={{fontSize:11}} onClick={()=>setEditModal(true)}>+ Add Lead</button>
        <div style={{fontSize:11,color:"#525878",borderLeft:"1px solid #181b2e",paddingLeft:12,display:"flex",alignItems:"center",gap:8}}>
          <span style={{color:"#6b7194"}}>{user}</span>
          <button onClick={()=>{localStorage.clear();setUser(null)}}
            style={{background:"none",border:"none",color:"#f0606080",cursor:"pointer",fontSize:10,fontFamily:"inherit"}}>
            sign out
          </button>
        </div>
      </div>

      <div style={{padding:"22px 28px",maxWidth:1500,margin:"0 auto"}}>
        <StatsBar stats={stats} onCallbacks={()=>setCbOnly(p=>!p)}/>
        {industries.length>0&&<LeadFinder onFound={loadLeads} industries={industries}/>}

        <div style={{display:"flex",gap:8,marginBottom:14,flexWrap:"wrap",alignItems:"center"}}>
          <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search name, company, phone..."
            style={{background:"#0d0e1a",border:"1px solid #1e2236",color:"#dde1f0",padding:"8px 12px",
              borderRadius:7,fontSize:12,fontFamily:"inherit",outline:"none",flex:"1 1 200px"}}/>
          <select className="sel" value={fStatus} onChange={e=>setFStatus(e.target.value)}>
            <option value="all">All Statuses</option>
            {STATUS_OPTIONS.map(s=><option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
          <select className="sel" value={sortBy} onChange={e=>setSort(e.target.value)}>
            <option value="score">Highest Score</option>
            <option value="newest">Newest First</option>
            <option value="company">Company A–Z</option>
            <option value="callbacks">Callbacks Due</option>
          </select>
          <button className="btn" onClick={()=>setCbOnly(p=>!p)}
            style={{background:cbOnly?"#8b5cf6":"transparent",color:cbOnly?"#fff":"#6b7194",
              border:"1px solid "+(cbOnly?"#8b5cf6":"#1e2236"),padding:"8px 14px",fontSize:11}}>
            {cbOnly?"✕ Show All":"🔔 Callbacks Only"}
          </button>
          <div style={{fontSize:11,color:"#2a2d3e",marginLeft:"auto"}}>
            {loading?"loading...":leads.length+" lead"+(leads.length!==1?"s":"")}
          </div>
        </div>

        {!loading&&leads.length===0?(
          <div style={{textAlign:"center",padding:"60px 0",color:"#2a2d3e"}}>
            <div style={{fontFamily:"'Syne',sans-serif",fontSize:44,fontWeight:800,marginBottom:8}}>0</div>
            <div style={{fontSize:11,letterSpacing:".1em"}}>
              {cbOnly?"NO CALLBACKS DUE":"USE FIND LEADS ABOVE OR IMPORT A CSV TO GET STARTED"}
            </div>
          </div>
        ):(
          <div style={{display:"flex",flexDirection:"column",gap:6}}>
            {leads.map(lead=>{
              const info=si(lead.status)
              const isCb=lead.callbackDate&&lead.callbackDate<=today&&lead.status!=="converted"
              return(
                <div key={lead.id} className={`lrow${isCb?" lrow-cb":""}`}
                  style={{display:"grid",gridTemplateColumns:"auto 1fr auto auto",gap:14,alignItems:"center"}}>
                  <ScoreRing score={lead.score||0}/>
                  <div>
                    <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:3,flexWrap:"wrap"}}>
                      <span style={{fontFamily:"'Syne',sans-serif",fontSize:13,fontWeight:800,color:"#f0f2ff"}}>
                        {[lead.firstName,lead.lastName].filter(Boolean).join(" ")||lead.company}
                      </span>
                      {lead.source&&<span className="src-tag">{lead.source}</span>}
                      {isCb&&<span style={{fontSize:9,background:"#8b5cf618",color:"#8b5cf6",padding:"2px 7px",borderRadius:4,border:"1px solid #8b5cf635"}}>🔔 {lead.callbackDate}</span>}
                      {lead.followUpSequence&&<span style={{fontSize:9,background:"#f0b42912",color:"#f0b429",padding:"2px 7px",borderRadius:4,border:"1px solid #f0b42930"}}>⏱ {lead.followUpSequence}</span>}
                      {lead.contract_value>0&&<span style={{fontSize:9,background:"#059669",color:"#fff",padding:"2px 7px",borderRadius:4}}>${(lead.contract_value||0).toLocaleString()}</span>}
                    </div>
                    {lead.company&&(
                      <div style={{fontSize:11,color:"#6b7194",marginBottom:3}}>
                        {lead.company}{lead.industry?` · ${lead.industry}`:""}
                      </div>
                    )}
                    <div style={{display:"flex",gap:12,fontSize:11,color:"#525878",flexWrap:"wrap"}}>
                      {lead.phone&&<span>📞 {lead.phone}</span>}
                      {lead.email&&<span>✉ {lead.email}</span>}
                      {(lead.city||lead.state)&&<span>📍 {[lead.city,lead.state].filter(Boolean).join(", ")}</span>}
                      {lead.assignedTo&&<span>👤 {lead.assignedTo}</span>}
                      {lead.total_calls>0&&<span>📞 {lead.total_calls} call{lead.total_calls!==1?"s":""}</span>}
                    </div>
                    {lead.notes&&<div style={{marginTop:4,fontSize:11,color:"#525878",borderLeft:"2px solid #1e2236",paddingLeft:7,maxWidth:520,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{lead.notes}</div>}
                  </div>
                  <div style={{display:"flex",flexDirection:"column",gap:6,minWidth:115}}>
                    <span className="pill" style={{background:info.color+"20",color:info.color,border:`1px solid ${info.color}35`,textAlign:"center"}}>
                      {info.label}
                    </span>
                    <div style={{display:"flex",gap:3,flexWrap:"wrap"}}>
                      {STATUS_OPTIONS.filter(s=>s.value!==lead.status).slice(0,3).map(s=>(
                        <button key={s.value} className="qs" onClick={()=>quickStatus(lead,s.value)}
                          style={{color:s.color,borderColor:s.color+"35"}}>{s.label}</button>
                      ))}
                    </div>
                  </div>
                  <div style={{display:"flex",flexDirection:"column",gap:5,alignItems:"flex-end"}}>
                    <div style={{fontSize:9,color:"#2a2d3e"}}>{lead.createdAt?new Date(lead.createdAt).toLocaleDateString():""}</div>
                    <button onClick={()=>setCallModal(lead)}
                      style={{background:"#5b5fef18",color:"#5b5fef",border:"1px solid #5b5fef35",padding:"5px 11px",
                        borderRadius:6,cursor:"pointer",fontFamily:"inherit",fontSize:11,fontWeight:500}}>
                      📞 Log Call
                    </button>
                    <button className="btn btn-g" style={{fontSize:11,padding:"5px 11px"}} onClick={()=>setEditModal(lead)}>Edit</button>
                    <button className="btn btn-r" onClick={()=>deleteL(lead.id)}>Delete</button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {callModal&&<CallModal lead={callModal} onClose={()=>setCallModal(null)} onSaved={loadLeads}/>}
      {editModal&&(
        <LeadModal lead={editModal===true?null:editModal} onClose={()=>setEditModal(null)}
          onSaved={()=>{loadLeads();notify(editModal===true?"Lead added!":"Lead updated")}}/>
      )}
      {showImport&&<ImportModal onClose={()=>setImport(false)} onDone={msg=>{notify(msg);loadLeads()}}/>}
      {showScripts&&<ScriptsModal onClose={()=>setShowScripts(false)}/>}
      {toast&&(
        <div className="toast" style={{borderColor:toast.type==="error"?"#f0606040":"#5b5fef40",color:toast.type==="error"?"#f06060":"#dde1f0"}}>
          {toast.msg}
        </div>
      )}
    </div>
  )
}
// bust Thu Feb 26 17:20:03 PST 2026
