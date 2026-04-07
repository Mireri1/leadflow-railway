const SUPABASE_URL  = import.meta.env.VITE_SUPABASE_URL  || "https://ucpwpjokyconwzwqvdad.supabase.co"
const SUPABASE_KEY  = import.meta.env.VITE_SUPABASE_KEY  || "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVjcHdwam9reWNvbnd6d3F2ZGFkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzE5NjMxMjksImV4cCI6MjA4NzUzOTEyOX0.j1Ibnm3rhOnvdnfS3WPf2RLDH91wopuJbTByQmwVZ7w"
const TEAM_PASSWORD = import.meta.env.VITE_TEAM_PASSWORD || "LeadFlow#2024!"

export function scoreLead(lead) {
  const sourceScores = {
    "SAM.gov":25,"SEC EDGAR":20,"OpenCorporates":15,"FL SOS":15,"TX SOS":15,
    "State SOS":15,"LinkedIn":18,"Manual":12,"BBB":10,"Yellow Pages":8,
  }
  const statusScores = { called:5,no_answer:2,callback:15,interested:30,not_interested:-10,converted:40 }
  let score = 5
  const src = lead.source || ""
  for (const [key,val] of Object.entries(sourceScores)) { if (src.includes(key)){score+=val;break} }
  if ((lead.company||"").trim())   score+=8
  if ((lead.phone||"").trim())     score+=8
  if ((lead.email||"").trim())     score+=6
  if ((lead.firstName||"").trim()) score+=3
  if ((lead.lastName||"").trim())  score+=3
  if ((lead.industry||"").trim())  score+=2
  if ((lead.phone||"").trim()&&(lead.email||"").trim()) score+=5
  score+=statusScores[lead.status]||0
  return Math.max(0,Math.min(100,score))
}

const BASE=`${SUPABASE_URL}/rest/v1`
const H=(extra={})=>({
  "apikey":SUPABASE_KEY,"Authorization":`Bearer ${SUPABASE_KEY}`,
  "Content-Type":"application/json","Prefer":"return=representation",...extra,
})
async function sb(path,options={}){
  const res=await fetch(`${BASE}/${path}`,{headers:H(options.headers||{}),...options})
  if(res.status===204)return null
  const body=await res.json()
  if(!res.ok)throw new Error(body?.message||body?.error||JSON.stringify(body))
  return body
}

export function getUser(){return localStorage.getItem("lf_user")||""}

export const api={
  login:async(username,password)=>{
    if(!username.trim())throw new Error("Enter your name")
    if(password!==TEAM_PASSWORD)throw new Error("Invalid password")
    localStorage.setItem("lf_user",username.trim())
    localStorage.setItem("lf_token","authenticated")
    // Record sign-in to user_sessions table
    try{await sb("user_sessions",{method:"POST",body:JSON.stringify({username:username.trim(),signed_in:new Date().toISOString()})})}catch(e){console.warn("Failed to record sign-in:",e)}
    return{username:username.trim()}
  },
  logout:()=>{localStorage.removeItem("lf_user");localStorage.removeItem("lf_token")},
  isLoggedIn:()=>localStorage.getItem("lf_token")==="authenticated",

  getLeads:async(params={})=>{
    const parts=["leads?select=*"]
    if(params.status&&params.status!=="all")parts.push(`status=eq.${encodeURIComponent(params.status)}`)
    if(params.assignedTo&&params.assignedTo!=="all")parts.push(`assignedTo=eq.${encodeURIComponent(params.assignedTo)}`)
    if(params.search){
      const s=encodeURIComponent(`%${params.search}%`)
      parts.push(`or=(company.ilike.${s},firstName.ilike.${s},lastName.ilike.${s},phone.ilike.${s},email.ilike.${s})`)
    }
    if(params.callbacks_only){
      const today=new Date().toISOString().split("T")[0]
      parts.push(`callbackDate=lte.${today}`,`callbackDate=neq.`,`status=neq.converted`)
    }
    const orderMap={newest:"createdAt.desc",oldest:"createdAt.asc",score:"score.desc",company:"company.asc",callbacks:"callbackDate.asc"}
    parts.push(`order=${orderMap[params.sortBy]||"createdAt.desc"}`)
    const[base,...filters]=parts
    return sb(filters.length?`${base}&${filters.join("&")}`:base)
  },

  createLead:async(lead)=>{
    const payload={...lead,score:scoreLead(lead),createdBy:getUser(),user_id:getUser(),createdAt:new Date().toISOString(),updatedAt:new Date().toISOString()}
    const res=await sb("leads",{method:"POST",body:JSON.stringify(payload)})
    return Array.isArray(res)?res[0]:res
  },

  updateLead:async(id,lead)=>{
    const payload={...lead,score:scoreLead(lead),updatedAt:new Date().toISOString()}
    const res=await sb(`leads?id=eq.${id}`,{method:"PATCH",body:JSON.stringify(payload)})
    return Array.isArray(res)?res[0]:res
  },

  deleteLead:async(id)=>{
    await sb(`leads?id=eq.${id}`,{method:"DELETE",headers:{Prefer:""}})
    return{ok:true}
  },

  updateStatus:async(id,status,callbackDate="")=>{
    const current=await sb(`leads?id=eq.${id}&select=*`)
    const lead=Array.isArray(current)?current[0]:current
    const newScore=scoreLead({...lead,status,callbackDate})
    const updates={status,callbackDate,score:newScore,updatedAt:new Date().toISOString()}
    if(status==="converted") updates.converted_at=new Date().toISOString()
    const res=await sb(`leads?id=eq.${id}`,{method:"PATCH",body:JSON.stringify(updates)})
    return Array.isArray(res)?res[0]:res
  },

  importLeads:async(leads,assignedTo="")=>{
    const user=getUser(),now=new Date().toISOString()
    const toInsert=leads.map(l=>{
      const lead={...l,assignedTo:assignedTo||l.assignedTo||"",createdBy:user,user_id:user,status:l.status||"new",createdAt:now,updatedAt:now}
      lead.score=scoreLead(lead);delete lead.id;return lead
    })
    await sb("leads",{method:"POST",headers:{Prefer:"resolution=ignore-duplicates"},body:JSON.stringify(toInsert)})
    return{inserted:toInsert.length}
  },

  getCalls:(leadId)=>sb(`call_outcomes?leadId=eq.${leadId}&order=calledAt.desc`),

  logCall:async(call)=>{
    const user=getUser()
    await sb("call_outcomes",{method:"POST",body:JSON.stringify({
      ...call,
      calledBy:user,
      user_id:user,
      calledAt:new Date().toISOString(),
      converted:call.outcome==="converted",
    })})
    const statusMap={answered:"called",no_answer:"no_answer",voicemail:"no_answer",callback:"callback",interested:"interested",not_interested:"not_interested",converted:"converted"}
    await api.updateStatus(call.leadId,statusMap[call.outcome]||"called",call.callbackDate||"")

    // Update lead call tracking
    const current=await sb(`leads?id=eq.${call.leadId}&select=total_calls,last_called_at`)
    const lead=Array.isArray(current)?current[0]:current
    await sb(`leads?id=eq.${call.leadId}`,{
      method:"PATCH",
      body:JSON.stringify({
        last_called_at:new Date().toISOString(),
        total_calls:(lead?.total_calls||0)+1,
        updatedAt:new Date().toISOString(),
      })
    })
    return{ok:true}
  },

  getStats:async()=>{
    const today=new Date().toISOString().split("T")[0]
    const[leads,callsToday,sessionsToday]=await Promise.all([
      sb("leads?select=status,score,callbackDate,createdAt,user_id"),
      sb(`call_outcomes?select=outcome,calledBy,user_id&calledAt=gte.${today}T00:00:00`),
      sb(`user_sessions?select=username,signed_in&signed_in=gte.${today}T00:00:00&order=signed_in.desc`).catch(()=>[])
    ])
    const sl=leads||[],sc=callsToday||[],ss=sessionsToday||[]
    const total=sl.length
    const repMap={}
    // Add all signed-in users first (so they appear even with 0 calls)
    for(const s of ss){
      const rep=s.username
      if(!rep)continue
      if(!repMap[rep])repMap[rep]={rep,calls:0,interested:0,converted:0,signedInAt:s.signed_in}
    }
    for(const c of sc){
      const rep=c.user_id||c.calledBy
      if(!rep)continue
      if(!repMap[rep])repMap[rep]={rep,calls:0,interested:0,converted:0,signedInAt:null}
      repMap[rep].calls++
      if(c.outcome==="interested")repMap[rep].interested++
      if(c.outcome==="converted")repMap[rep].converted++
    }
    const converted=sl.filter(l=>l.status==="converted").length
    return{
      total,
      newToday:sl.filter(l=>(l.createdAt||"").startsWith(today)).length,
      interested:sl.filter(l=>l.status==="interested").length,
      converted,
      callbacksDue:sl.filter(l=>l.callbackDate&&l.callbackDate<=today&&l.status!=="converted").length,
      callsToday:sc.length,
      conversionRate:total?((converted/total)*100).toFixed(1):"0.0",
      avgScore:total?Math.round(sl.reduce((a,l)=>a+(l.score||0),0)/total):0,
      topReps:Object.values(repMap).sort((a,b)=>b.calls-a.calls),
    }
  },

  // Scripts CRUD
  getScripts:async(industry="")=>{
    const q=industry?`scripts?is_active=eq.true&industry=eq.${encodeURIComponent(industry)}&order=usage_count.desc`:"scripts?is_active=eq.true&order=usage_count.desc"
    return sb(q)||[]
  },

  createScript:async(script)=>{
    const res=await sb("scripts",{method:"POST",body:JSON.stringify({...script,is_active:true,usage_count:0,created_at:new Date().toISOString()})})
    return Array.isArray(res)?res[0]:res
  },

  updateScript:async(id,script)=>{
    const res=await sb(`scripts?id=eq.${id}`,{method:"PATCH",body:JSON.stringify({...script,updated_at:new Date().toISOString()})})
    return Array.isArray(res)?res[0]:res
  },

  deleteScript:async(id)=>{
    await sb(`scripts?id=eq.${id}`,{method:"PATCH",body:JSON.stringify({is_active:false})})
    return{ok:true}
  },

  incrementScriptUsage:async(id)=>{
    const current=await sb(`scripts?id=eq.${id}&select=usage_count`)
    const s=Array.isArray(current)?current[0]:current
    await sb(`scripts?id=eq.${id}`,{method:"PATCH",body:JSON.stringify({usage_count:(s?.usage_count||0)+1,last_used:new Date().toISOString()})})
  },

  getScraperConfig:async()=>({enabled:false}),
  setScraperConfig:async()=>({ok:true}),
  runScraperNow:async()=>({ok:true}),
}