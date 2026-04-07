import { useState, useEffect, useRef, useCallback } from "react"
import { api, scoreLead } from "./api.js"

// ── Constants ──────────────────────────────────────────────────────────────────

const STATUS_OPTIONS = [
  { value: "new",            label: "New",            color: "#6366f1" },
  { value: "called",         label: "Called",          color: "#f59e0b" },
  { value: "no_answer",      label: "No Answer",       color: "#94a3b8" },
  { value: "interested",     label: "Interested",      color: "#10b981" },
  { value: "not_interested", label: "Not Interested",  color: "#ef4444" },
  { value: "callback",       label: "Callback",        color: "#8b5cf6" },
  { value: "converted",      label: "Converted",       color: "#059669" },
]

const CALL_OUTCOMES = [
  { value: "answered",       label: "Answered — spoke to them" },
  { value: "no_answer",      label: "No Answer" },
  { value: "voicemail",      label: "Left Voicemail" },
  { value: "callback",       label: "Requested Callback" },
  { value: "interested",     label: "Interested — follow up" },
  { value: "not_interested", label: "Not Interested" },
  { value: "converted",      label: "Converted — closed!" },
]

const INDUSTRIES = [
  "SaaS / Software","Finance & Accounting","Legal Services","Marketing & Advertising",
  "HR & Staffing","Consulting","Healthcare Services","Real Estate","Insurance",
  "Logistics & Supply Chain","Manufacturing","IT Services","Education & Training","Other",
]

const SIC_CODES = {
  "7372":"Software","7371":"IT Services","7389":"Consulting",
  "8742":"Mgmt Consulting","8721":"Accounting","8111":"Legal",
  "7311":"Marketing","7363":"Staffing","8711":"Engineering",
}

const emptyForm = {
  firstName:"", lastName:"", title:"", company:"", industry:"",
  phone:"", email:"", website:"", address:"", city:"", state:"",
  status:"new", assignedTo:"", notes:"", source:"", callbackDate:"",
}

// ── Score helpers ──────────────────────────────────────────────────────────────

function scoreColor(s) {
  if (s >= 75) return "#ef4444"
  if (s >= 50) return "#f59e0b"
  if (s >= 25) return "#6366f1"
  return "#475569"
}
function scoreLabel(s) {
  if (s >= 75) return "Hot"
  if (s >= 50) return "Warm"
  if (s >= 25) return "Cool"
  return "Cold"
}

// ── CSV parser ─────────────────────────────────────────────────────────────────

function parseCSV(text) {
  const lines = text.trim().split("\n")
  if (lines.length < 2) return []
  const header = lines[0].split(",").map(h =>
    h.replace(/^"|"$/g, "").trim()
      .replace(/\s+/g, "")
      .replace("firstname",  "firstName")
      .replace("lastname",   "lastName")
      .replace("assignedto", "assignedTo")
  )
  return lines.slice(1).filter(l => l.trim()).map(line => {
    const vals = []; let cur = ""; let inQ = false
    for (const ch of line) {
      if (ch === '"') { inQ = !inQ; continue }
      if (ch === "," && !inQ) { vals.push(cur); cur = ""; continue }
      cur += ch
    }
    vals.push(cur)
    const lead = { ...emptyForm }
    header.forEach((k, i) => { if (k in lead && vals[i] !== undefined) lead[k] = vals[i].trim() })
    return lead
  }).filter(l => l.company || l.phone || l.firstName)
}

// ── LinkedIn paste parser ──────────────────────────────────────────────────────

function parseLinkedIn(text) {
  const lines = text.split("\n").map(l => l.trim()).filter(Boolean)
  const result = { ...emptyForm, source: "LinkedIn" }
  if (lines[0]) {
    const parts = lines[0].split(" ")
    result.firstName = parts[0] || ""
    result.lastName  = parts.slice(1).join(" ") || ""
  }
  if (lines[1] && !lines[1].includes("@") && !lines[1].match(/\d{3}/))
    result.title = lines[1]
  for (const line of lines) {
    if (line.includes("@") && line.includes(".")) result.email = line
    const m = line.match(/(\+?1?\s?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})/)
    if (m) result.phone = m[1]
    if (/^https?:\/\//i.test(line)) result.website = line
    if (/^(at |• )/i.test(line)) result.company = line.replace(/^(at |• )/i, "")
  }
  result.notes = "Imported via LinkedIn paste"
  return result
}

// ── Global CSS ─────────────────────────────────────────────────────────────────

const CSS = `
  @import url('https://fonts.googleapis.com/css2?family=DM+Mono:ital,wght@0,400;0,500;1,400&family=Syne:wght@700;800&display=swap');
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html { font-size: 14px; }
  body { background: #080810; color: #dde1f0; font-family: 'DM Mono', monospace; }
  ::-webkit-scrollbar { width: 4px; height: 4px; }
  ::-webkit-scrollbar-track { background: #0f1018; }
  ::-webkit-scrollbar-thumb { background: #2a2d3e; border-radius: 4px; }
  input, select, textarea, button { font-family: inherit; }

  /* Form fields */
  .ff { display: flex; flex-direction: column; gap: 4px; }
  .ff label { font-size: 10px; letter-spacing: .1em; text-transform: uppercase; color: #525878; }
  .ff input, .ff select, .ff textarea {
    background: #0d0e18; border: 1px solid #1e2236; color: #dde1f0;
    padding: 9px 11px; border-radius: 7px; font-size: 12px; outline: none;
    transition: border-color .15s, box-shadow .15s;
  }
  .ff input:focus, .ff select:focus, .ff textarea:focus {
    border-color: #5b5fef; box-shadow: 0 0 0 3px #5b5fef18;
  }
  .ff select option { background: #0d0e18; }

  /* Buttons */
  .btn { cursor: pointer; border: none; font-family: inherit; font-size: 12px;
    font-weight: 500; letter-spacing: .04em; transition: all .15s; border-radius: 7px; }
  .btn-p  { background: #5b5fef; color: #fff; padding: 9px 20px; }
  .btn-p:hover { background: #7478f5; transform: translateY(-1px); box-shadow: 0 4px 16px #5b5fef30; }
  .btn-g  { background: transparent; color: #6b7194; padding: 8px 14px; border: 1px solid #1e2236; }
  .btn-g:hover { background: #1e2236; color: #dde1f0; }
  .btn-r  { background: transparent; color: #f06060; padding: 5px 10px; border: 1px solid #f0606025; font-size: 11px; }
  .btn-r:hover { background: #f0606012; }
  .btn-gr { background: #1a9e6e; color: #fff; padding: 9px 20px; }
  .btn-gr:hover { background: #22c98a; }
  .btn-amber { background: #b8860b22; color: #f0b429; border: 1px solid #f0b42930; padding: 6px 14px; font-size: 11px; }
  .btn-amber:hover { background: #f0b42918; }

  /* Card */
  .card { background: #0d0e1a; border: 1px solid #181b2e; border-radius: 12px; padding: 18px 22px; }

  /* Status pill */
  .pill { display: inline-block; padding: 2px 9px; border-radius: 999px;
    font-size: 10px; letter-spacing: .07em; font-weight: 500; }

  /* Quick status button */
  .qs { background: transparent; border: 1px solid #1e2236; font-size: 10px;
    padding: 3px 7px; border-radius: 5px; cursor: pointer; font-family: inherit;
    transition: all .1s; }
  .qs:hover { background: #1e2236; }

  /* Modal */
  .modal-bg { position: fixed; inset: 0; background: #00000088; backdrop-filter: blur(4px);
    z-index: 200; display: flex; align-items: center; justify-content: center; padding: 20px; }
  .modal { background: #0d0e1a; border: 1px solid #1e2236; border-radius: 16px;
    padding: 28px; width: 100%; max-width: 600px; max-height: 90vh; overflow-y: auto; }

  /* Upload dropzone */
  .dropzone { background: #0d0e18; border: 2px dashed #1e2236; border-radius: 12px;
    padding: 36px; text-align: center; cursor: pointer; transition: all .2s; }
  .dropzone:hover { border-color: #5b5fef; background: #5b5fef08; }

  /* Toast */
  .toast { position: fixed; bottom: 24px; right: 24px; padding: 11px 18px;
    background: #141628; border: 1px solid #2a2d4a; border-radius: 10px;
    font-size: 12px; z-index: 9999; animation: fadeUp .2s ease; max-width: 300px; }
  @keyframes fadeUp { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:translateY(0); } }

  /* Tab nav */
  .tab { cursor: pointer; background: transparent; border: none; font-family: inherit;
    font-size: 11px; letter-spacing: .08em; font-weight: 500; padding: 7px 14px;
    border-radius: 6px; transition: all .15s; }

  /* Stat card number */
  .stat-num { font-family: 'Syne', sans-serif; font-weight: 800; font-size: 30px; line-height: 1; }

  /* Lead row */
  .lrow { background: #0d0e1a; border: 1px solid #181b2e; border-radius: 10px;
    padding: 14px 18px; transition: border-color .15s; }
  .lrow:hover { border-color: #5b5fef28; }
  .lrow-cb { border-color: #8b5cf635 !important; background: #8b5cf608 !important; }

  /* Score ring */
  .score-ring { border-radius: 50%; display: flex; align-items: center; justify-content: center;
    flex-direction: column; font-family: 'Syne', sans-serif; font-weight: 800; width: 44px; height: 44px; }

  /* Select */
  .sel { background: #0d0e1a; border: 1px solid #1e2236; color: #6b7194; padding: 8px 11px;
    border-radius: 7px; font-size: 12px; font-family: inherit; cursor: pointer; outline: none; }
  .sel:focus { border-color: #5b5fef; }

  /* Divider */
  .divider { height: 1px; background: #181b2e; margin: 16px 0; }

  /* Source tag */
  .src-tag { font-size: 9px; background: #181b2e; color: #525878;
    padding: 2px 6px; border-radius: 4px; letter-spacing: .05em; }
`

// ── Score Ring ─────────────────────────────────────────────────────────────────

function ScoreRing({ score = 0 }) {
  const c = scoreColor(score)
  const l = scoreLabel(score)
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3 }}>
      <div className="score-ring" style={{ background: c + "18", border: `2px solid ${c}35`, color: c, fontSize: 13 }}>
        {score}
      </div>
      <div style={{ fontSize: 9, color: c, letterSpacing: ".06em" }}>{l}</div>
    </div>
  )
}

// ── Login ──────────────────────────────────────────────────────────────────────

function Login({ onLogin }) {
  const [name, setName]     = useState("")
  const [pass, setPass]     = useState("")
  const [err, setErr]       = useState("")
  const [loading, setLoad]  = useState(false)

  async function submit(e) {
    e.preventDefault(); setErr(""); setLoad(true)
    try {
      const res = await api.login(name, pass)
      onLogin(res.username)
    } catch(ex) { setErr(ex.message) }
    finally { setLoad(false) }
  }

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: "#080810" }}>
      <style>{CSS}</style>
      <div style={{ width: 340 }}>
        <div style={{ textAlign: "center", marginBottom: 32 }}>
          <div style={{ fontFamily: "'Syne',sans-serif", fontSize: 34, fontWeight: 800, color: "#fff", letterSpacing: "-.02em" }}>
            LEAD<span style={{ color: "#5b5fef" }}>FLOW</span>
          </div>
          <div style={{ fontSize: 10, color: "#525878", letterSpacing: ".12em", marginTop: 4 }}>B2B COLD CALL PLATFORM</div>
        </div>
        <div className="card" style={{ border: "1px solid #5b5fef28" }}>
          <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div className="ff">
              <label>Your Name</label>
              <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Alice" autoFocus />
            </div>
            <div className="ff">
              <label>Team Password</label>
              <input type="password" value={pass} onChange={e => setPass(e.target.value)} placeholder="••••••••" />
            </div>
            {err && <div style={{ color: "#f06060", fontSize: 11 }}>⚠ {err}</div>}
            <button type="submit" className="btn btn-p" style={{ padding: "11px", fontSize: 13 }} disabled={loading}>
              {loading ? "Signing in..." : "Sign In →"}
            </button>
          </form>
        </div>
        <div style={{ textAlign: "center", marginTop: 16, fontSize: 10, color: "#2a2d3e" }}>
          LeadFlow · Supabase Edition
        </div>
      </div>
    </div>
  )
}

// ── Call Log Modal ─────────────────────────────────────────────────────────────

function CallModal({ lead, onClose, onSaved }) {
  const [calls, setCalls]               = useState([])
  const [outcome, setOutcome]           = useState("answered")
  const [notes, setNotes]               = useState("")
  const [cbDate, setCbDate]             = useState("")
  const [duration, setDuration]         = useState("")
  const [saving, setSaving]             = useState(false)
  const [callsLoading, setCLoad]        = useState(true)
  const [scripts, setScripts]           = useState([])
  const [scriptId, setScriptId]         = useState("")
  const [showScript, setShowScript]     = useState(false)
  const [contractValue, setContractValue] = useState("")

  useEffect(() => {
    api.getCalls(lead.id)
      .then(setCalls)
      .catch(() => setCalls([]))
      .finally(() => setCLoad(false))
    api.getScripts().then(s => setScripts(s || [])).catch(() => {})
  }, [lead.id])

  async function log() {
    setSaving(true)
    try {
      await api.logCall({
        leadId:         lead.id,
        outcome,
        notes,
        duration:       duration ? parseInt(duration) * 60 : 0,
        callbackDate:   outcome === "callback" ? cbDate : "",
        script_id:      scriptId ? parseInt(scriptId) : null,
        converted:      outcome === "converted",
        contract_value: outcome === "converted" && contractValue ? parseFloat(contractValue) : null,
      })
      if (scriptId) await api.incrementScriptUsage(scriptId)
      onSaved()
      onClose()
    } catch (ex) { alert("Error: " + ex.message) }
    finally { setSaving(false) }
  }

  const selectedScript = scripts.find(s => s.id === parseInt(scriptId))
  const si = v => STATUS_OPTIONS.find(s => s.value === v) || STATUS_OPTIONS[0]

  return (
    <div className="modal-bg" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 620 }}>

        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
          <div>
            <div style={{ fontFamily: "'Syne',sans-serif", fontSize: 15, fontWeight: 800, color: "#fff", marginBottom: 4 }}>
              {[lead.firstName, lead.lastName].filter(Boolean).join(" ") || lead.company}
            </div>
            <div style={{ display: "flex", gap: 12, fontSize: 11, color: "#525878" }}>
              {lead.phone && <span>📞 {lead.phone}</span>}
              {lead.email && <span>✉ {lead.email}</span>}
              {lead.company && lead.firstName && <span>🏢 {lead.company}</span>}
            </div>
          </div>
          <button className="btn btn-g" style={{ fontSize: 11, padding: "5px 10px" }} onClick={onClose}>✕</button>
        </div>

        {/* Script selector */}
        {scripts.length > 0 && (
          <div style={{ marginBottom: 16, background: "#080810", border: "1px solid #5b5fef25", borderRadius: 10, padding: 14 }}>
            <div style={{ fontSize: 10, color: "#5b5fef", letterSpacing: ".1em", marginBottom: 10 }}>CALL SCRIPT</div>
            <div style={{ display: "flex", gap: 8, marginBottom: scriptId ? 10 : 0 }}>
              <select value={scriptId} onChange={e => { setScriptId(e.target.value); setShowScript(false) }}
                style={{ flex: 1, background: "#0d0e1a", border: "1px solid #1e2236", color: "#dde1f0", padding: "8px 10px", borderRadius: 7, fontSize: 12, fontFamily: "inherit" }}>
                <option value="">No script selected</option>
                {scripts.map(s => <option key={s.id} value={s.id}>{s.name}{s.industry ? ` (${s.industry})` : ""}</option>)}
              </select>
              {scriptId && (
                <button className="btn btn-g" style={{ fontSize: 11, padding: "5px 12px" }}
                  onClick={() => setShowScript(p => !p)}>
                  {showScript ? "Hide" : "View"}
                </button>
              )}
            </div>
            {showScript && selectedScript && (
              <div style={{ background: "#0d0e1a", border: "1px solid #1e2236", borderRadius: 8, padding: 14, fontSize: 12, color: "#a0a3b8", lineHeight: 1.8, whiteSpace: "pre-wrap", maxHeight: 200, overflowY: "auto" }}>
                {selectedScript.script_text}
                {selectedScript.objection_handlers?.length > 0 && (
                  <div style={{ marginTop: 12, borderTop: "1px solid #1e2236", paddingTop: 10 }}>
                    <div style={{ fontSize: 10, color: "#5b5fef", letterSpacing: ".08em", marginBottom: 8 }}>OBJECTION HANDLERS</div>
                    {selectedScript.objection_handlers.map((obj, i) => (
                      <div key={i} style={{ marginBottom: 8 }}>
                        <div style={{ color: "#f0b429", fontSize: 11 }}>"{obj.objection}"</div>
                        <div style={{ color: "#a0a3b8", fontSize: 11, paddingLeft: 10 }}>→ {obj.response}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Log call form */}
        <div style={{ background: "#080810", border: "1px solid #5b5fef25", borderRadius: 10, padding: 16, marginBottom: 20 }}>
          <div style={{ fontSize: 10, color: "#5b5fef", letterSpacing: ".1em", marginBottom: 12 }}>LOG THIS CALL</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 10 }}>
            <div className="ff">
              <label>Outcome</label>
              <select value={outcome} onChange={e => setOutcome(e.target.value)}>
                {CALL_OUTCOMES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
            <div className="ff">
              <label>Duration (minutes)</label>
              <input type="number" value={duration} onChange={e => setDuration(e.target.value)} placeholder="5" min="0" />
            </div>
          </div>
          {outcome === "callback" && (
            <div className="ff" style={{ marginBottom: 10 }}>
              <label>Callback Date</label>
              <input type="date" value={cbDate} onChange={e => setCbDate(e.target.value)} />
            </div>
          )}
          {outcome === "converted" && (
            <div className="ff" style={{ marginBottom: 10 }}>
              <label>Contract Value ($)</label>
              <input type="number" value={contractValue} onChange={e => setContractValue(e.target.value)} placeholder="2400" min="0" />
            </div>
          )}
          <div className="ff" style={{ marginBottom: 12 }}>
            <label>Notes</label>
            <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={2}
              placeholder="Gatekeeper said decision maker is traveling. Try Thursday AM..." style={{ resize: "vertical" }} />
          </div>
          <button className="btn btn-p" onClick={log} disabled={saving}>
            {saving ? "Saving..." : "Log Call ↵"}
          </button>
        </div>

        {/* Call history */}
        <div style={{ fontSize: 10, color: "#525878", letterSpacing: ".08em", marginBottom: 10 }}>
          CALL HISTORY {callsLoading ? "..." : `(${calls.length})`}
        </div>
        {!callsLoading && calls.length === 0 && (
          <div style={{ fontSize: 11, color: "#2a2d3e", textAlign: "center", padding: "16px 0" }}>No calls logged yet</div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 220, overflowY: "auto" }}>
          {calls.map(c => {
            const outcomeStatus = c.outcome.replace("voicemail", "no_answer").replace("answered", "called")
            const info = si(outcomeStatus)
            return (
              <div key={c.id} style={{ background: "#080810", border: "1px solid #181b2e", borderRadius: 8, padding: "10px 12px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: c.notes ? 5 : 0 }}>
                  <span className="pill" style={{ background: info.color + "20", color: info.color, border: `1px solid ${info.color}35` }}>
                    {c.outcome.replace(/_/g, " ")}
                  </span>
                  <div style={{ fontSize: 10, color: "#525878" }}>
                    {new Date(c.calledAt).toLocaleDateString()} {new Date(c.calledAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                    {c.duration > 0 && <span style={{ marginLeft: 8 }}>{Math.round(c.duration / 60)}m</span>}
                    <span style={{ marginLeft: 8, color: "#2a2d3e" }}>· {c.calledBy}</span>
                  </div>
                </div>
                {c.notes && <div style={{ fontSize: 11, color: "#6b7194", marginTop: 4 }}>{c.notes}</div>}
                {c.callbackDate && <div style={{ fontSize: 10, color: "#8b5cf6", marginTop: 3 }}>🔔 Callback: {c.callbackDate}</div>}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

// ── Lead Form Modal ────────────────────────────────────────────────────────────

function LeadModal({ lead, onClose, onSaved }) {
  const [form, setForm] = useState(lead ? { ...emptyForm, ...lead } : emptyForm)
  const [saving, setSave] = useState(false)
  const isEdit = !!lead?.id

  const f = key => ({ value: form[key] || "", onChange: e => setForm(p => ({ ...p, [key]: e.target.value })) })

  async function save() {
    if (!form.company && !form.firstName) { alert("Enter a company name or contact name"); return }
    setSave(true)
    try {
      if (isEdit) await api.updateLead(lead.id, form)
      else        await api.createLead(form)
      onSaved()
      onClose()
    } catch (ex) { alert("Error: " + ex.message) }
    finally { setSave(false) }
  }

  return (
    <div className="modal-bg" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 660 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 20 }}>
          <div style={{ fontFamily: "'Syne',sans-serif", fontSize: 15, fontWeight: 800 }}>{isEdit ? "EDIT LEAD" : "NEW LEAD"}</div>
          <button className="btn btn-g" onClick={onClose}>✕</button>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10, marginBottom: 10 }}>
          {[["firstName","First Name","Jane"],["lastName","Last Name","Smith"],["title","Title","VP of Sales"],
            ["company","Company *","Acme Corp"],["phone","Phone","(555) 000-0000"],["email","Email","jane@acme.com"],
            ["website","Website","https://acme.com"],["city","City","Chicago"],["state","State","IL"]].map(([k,l,p]) => (
            <div key={k} className="ff"><label>{l}</label><input {...f(k)} placeholder={p} /></div>
          ))}
          <div className="ff"><label>Address</label><input {...f("address")} placeholder="123 Main St" /></div>
          <div className="ff">
            <label>Industry</label>
            <select {...f("industry")}>
              <option value="">Select...</option>
              {INDUSTRIES.map(i => <option key={i} value={i}>{i}</option>)}
            </select>
          </div>
          <div className="ff">
            <label>Status</label>
            <select {...f("status")}>
              {STATUS_OPTIONS.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </div>
          <div className="ff"><label>Assigned To</label><input {...f("assignedTo")} placeholder="Alice" /></div>
          <div className="ff"><label>Callback Date</label><input type="date" {...f("callbackDate")} /></div>
          <div className="ff">
            <label>Source</label>
            <input {...f("source")} placeholder="SAM.gov, LinkedIn, Manual..." />
          </div>
        </div>
        <div className="ff" style={{ marginBottom: 18 }}>
          <label>Notes</label>
          <textarea {...f("notes")} rows={2} placeholder="Gatekeeper is Nancy. Decision maker is CFO. Best time: Thursday PM." style={{ resize: "vertical" }} />
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16, padding: "10px 14px", background: "#080810", borderRadius: 8, border: "1px solid #181b2e" }}>
          <ScoreRing score={scoreLead(form)} />
          <div style={{ fontSize: 11, color: "#525878" }}>
            Estimated score based on current data.
            Score updates automatically when saved.
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn-p" onClick={save} disabled={saving}>{saving ? "Saving..." : isEdit ? "Save Changes" : "Add Lead"}</button>
          <button className="btn btn-g" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  )
}

// ── Import Modal ───────────────────────────────────────────────────────────────

function ImportModal({ onClose, onDone }) {
  const [tab, setTab]       = useState("csv")
  const [preview, setPrev]  = useState(null)
  const [assignTo, setAT]   = useState("")
  const [loading, setLoad]  = useState(false)
  const [liText, setLiText] = useState("")
  const [liParsed, setLiP]  = useState(null)
  const fileRef = useRef()

  function handleFile(e) {
    const file = e.target.files[0]; if (!file) return
    const r = new FileReader()
    r.onload = ev => setPrev(parseCSV(ev.target.result))
    r.readAsText(file)
  }

  async function doCSVImport() {
    if (!preview) return
    setLoad(true)
    try {
      const res = await api.importLeads(preview, assignTo)
      onDone(`✓ Imported ${res.inserted} leads`)
      onClose()
    } catch (ex) { alert("Error: " + ex.message) }
    finally { setLoad(false) }
  }

  async function doLinkedinImport() {
    if (!liParsed) return
    setLoad(true)
    try {
      await api.createLead(liParsed)
      onDone("✓ LinkedIn lead added")
      onClose()
    } catch (ex) { alert("Error: " + ex.message) }
    finally { setLoad(false) }
  }

  return (
    <div className="modal-bg" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 580 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 20 }}>
          <div style={{ fontFamily: "'Syne',sans-serif", fontSize: 15, fontWeight: 800 }}>IMPORT LEADS</div>
          <button className="btn btn-g" onClick={onClose}>✕</button>
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 20 }}>
          {[["csv","↑ CSV File"],["linkedin","in LinkedIn Paste"]].map(([id,label]) => (
            <button key={id} className="tab" onClick={() => setTab(id)}
              style={{ background: tab===id ? "#5b5fef" : "transparent", color: tab===id ? "#fff" : "#525878", border: tab===id ? "none" : "1px solid #1e2236" }}>
              {label}
            </button>
          ))}
        </div>

        {tab === "csv" && (
          <>
            <div style={{ marginBottom: 14, padding: "10px 14px", background: "#080810", border: "1px solid #181b2e", borderRadius: 8, fontSize: 11, color: "#525878", lineHeight: 1.8 }}>
              Works with output from <code style={{ color: "#5b5fef" }}>gov_scraper.py</code>, Apollo exports, or any CSV with <code style={{ color: "#5b5fef" }}>company</code> + <code style={{ color: "#5b5fef" }}>phone</code> columns. Each lead is auto-scored on import.
            </div>
            {!preview ? (
              <div className="dropzone" onClick={() => fileRef.current?.click()}>
                <div style={{ fontSize: 28, marginBottom: 8 }}>↑</div>
                <div style={{ color: "#6b7194", fontSize: 13, marginBottom: 4 }}>Click to upload CSV</div>
                <div style={{ color: "#2a2d3e", fontSize: 11 }}>Scraper output, Apollo, Hunter, or any leads CSV</div>
                <input ref={fileRef} type="file" accept=".csv" style={{ display: "none" }} onChange={handleFile} />
              </div>
            ) : (
              <div>
                <div style={{ padding: 14, background: "#080810", border: "1px solid #1a9e6e30", borderRadius: 8, marginBottom: 14 }}>
                  <div style={{ color: "#1a9e6e", fontSize: 13, marginBottom: 8 }}>✓ Parsed {preview.length} leads — preview:</div>
                  {preview.slice(0, 5).map((l, i) => (
                    <div key={i} style={{ fontSize: 11, color: "#6b7194", borderLeft: "2px solid #5b5fef", paddingLeft: 8, marginBottom: 3 }}>
                      {[l.firstName, l.lastName, l.company, l.phone].filter(Boolean).join(" · ")}
                      <span style={{ color: scoreColor(scoreLead(l)), marginLeft: 8, fontSize: 10 }}>score: {scoreLead(l)}</span>
                    </div>
                  ))}
                  {preview.length > 5 && <div style={{ fontSize: 11, color: "#2a2d3e", marginTop: 4 }}>...and {preview.length - 5} more</div>}
                </div>
                <div className="ff" style={{ marginBottom: 14, maxWidth: 220 }}>
                  <label>Assign all to (optional)</label>
                  <input value={assignTo} onChange={e => setAT(e.target.value)} placeholder="Alice, Bob..." />
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button className="btn btn-gr" onClick={doCSVImport} disabled={loading}>
                    {loading ? "Importing..." : `Import ${preview.length} Leads`}
                  </button>
                  <button className="btn btn-g" onClick={() => { setPrev(null); if (fileRef.current) fileRef.current.value = "" }}>Re-upload</button>
                </div>
              </div>
            )}
          </>
        )}

        {tab === "linkedin" && (
          <>
            <div style={{ marginBottom: 14, padding: "10px 14px", background: "#080810", border: "1px solid #181b2e", borderRadius: 8, fontSize: 11, color: "#525878", lineHeight: 1.8 }}>
              1. Open a LinkedIn profile in your browser<br />
              2. Select all text (Ctrl+A / Cmd+A) and copy<br />
              3. Paste below — LeadFlow extracts name, title, company, email, phone
            </div>
            <div className="ff" style={{ marginBottom: 12 }}>
              <label>Paste LinkedIn profile text</label>
              <textarea value={liText} onChange={e => setLiText(e.target.value)} rows={8}
                placeholder={"John Smith\nVP of Operations at Acme Corp\nChicago, IL\njohn.smith@acme.com\n(312) 555-0000"}
                style={{ resize: "vertical" }} />
            </div>
            <button className="btn btn-p" style={{ marginBottom: 16 }}
              onClick={() => setLiP(parseLinkedIn(liText))}>
              Parse Profile →
            </button>
            {liParsed && (
              <div style={{ background: "#080810", border: "1px solid #5b5fef28", borderRadius: 10, padding: 16 }}>
                <div style={{ fontSize: 10, color: "#5b5fef", letterSpacing: ".1em", marginBottom: 12 }}>PARSED — EDIT BEFORE SAVING</div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 12 }}>
                  {["firstName","lastName","title","company","phone","email","website"].map(k => (
                    <div key={k} className="ff">
                      <label>{k}</label>
                      <input value={liParsed[k]||""} onChange={e => setLiP(p => ({...p,[k]:e.target.value}))} />
                    </div>
                  ))}
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button className="btn btn-gr" onClick={doLinkedinImport} disabled={loading}>
                    {loading ? "Saving..." : "Add to Leads"}
                  </button>
                  <button className="btn btn-g" onClick={() => setLiP(null)}>Discard</button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── Scripts Modal ──────────────────────────────────────────────────────────────

function ScriptsModal({ onClose }) {
  const [scripts, setScripts]   = useState([])
  const [loading, setLoad]      = useState(true)
  const [editing, setEditing]   = useState(null)
  const [form, setForm]         = useState({ name: "", industry: "", script_text: "", objection_handlers: [] })
  const [saving, setSaving]     = useState(false)
  const [newObj, setNewObj]     = useState({ objection: "", response: "" })
  const [toast, setToast]       = useState(null)

  useEffect(() => { fetchScripts() }, [])

  async function fetchScripts() {
    setLoad(true)
    try { setScripts(await api.getScripts() || []) } catch { }
    finally { setLoad(false) }
  }

  function showToast(msg) { setToast(msg); setTimeout(() => setToast(null), 2500) }

  function openNew() {
    setEditing("new")
    setForm({ name: "", industry: "", script_text: "", objection_handlers: [] })
  }

  function openEdit(s) {
    setEditing(s.id)
    setForm({ name: s.name, industry: s.industry || "", script_text: s.script_text, objection_handlers: s.objection_handlers || [] })
  }

  function addObjection() {
    if (!newObj.objection || !newObj.response) return
    setForm(f => ({ ...f, objection_handlers: [...f.objection_handlers, { ...newObj }] }))
    setNewObj({ objection: "", response: "" })
  }

  function removeObjection(i) {
    setForm(f => ({ ...f, objection_handlers: f.objection_handlers.filter((_, idx) => idx !== i) }))
  }

  async function save() {
    if (!form.name || !form.script_text) { showToast("Name and script required"); return }
    setSaving(true)
    try {
      if (editing === "new") await api.createScript(form)
      else await api.updateScript(editing, form)
      showToast("✓ Saved")
      setEditing(null)
      fetchScripts()
    } catch (ex) { showToast("Error: " + ex.message) }
    finally { setSaving(false) }
  }

  async function del(id) {
    if (!window.confirm("Delete this script?")) return
    await api.deleteScript(id)
    fetchScripts()
  }

  return (
    <div className="modal-bg" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 680 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
          <div style={{ fontFamily: "'Syne',sans-serif", fontSize: 15, fontWeight: 800 }}>CALL SCRIPTS</div>
          <div style={{ display: "flex", gap: 8 }}>
            {!editing && <button className="btn btn-p" style={{ fontSize: 11 }} onClick={openNew}>+ New Script</button>}
            <button className="btn btn-g" onClick={onClose}>✕</button>
          </div>
        </div>

        {editing ? (
          <div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 12 }}>
              <div className="ff">
                <label>Script Name</label>
                <input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="Cold intro script" />
              </div>
              <div className="ff">
                <label>Industry (optional)</label>
                <select value={form.industry} onChange={e => setForm(f => ({ ...f, industry: e.target.value }))}>
                  <option value="">All Industries</option>
                  {INDUSTRIES.map(i => <option key={i} value={i}>{i}</option>)}
                </select>
              </div>
            </div>
            <div className="ff" style={{ marginBottom: 14 }}>
              <label>Script</label>
              <textarea value={form.script_text} onChange={e => setForm(f => ({ ...f, script_text: e.target.value }))}
                rows={8} placeholder={"Hi, this is [name] calling from Vision Cleaning Company...\n\nI'm reaching out because we specialize in commercial cleaning for facilities like yours..."} style={{ resize: "vertical" }} />
            </div>

            <div style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 10, color: "#5b5fef", letterSpacing: ".1em", marginBottom: 10 }}>OBJECTION HANDLERS</div>
              {form.objection_handlers.map((obj, i) => (
                <div key={i} style={{ background: "#080810", border: "1px solid #1e2236", borderRadius: 8, padding: 10, marginBottom: 8 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <div style={{ fontSize: 11, color: "#f0b429" }}>"{obj.objection}"</div>
                    <button onClick={() => removeObjection(i)} style={{ background: "none", border: "none", color: "#f0606060", cursor: "pointer", fontSize: 12 }}>✕</button>
                  </div>
                  <div style={{ fontSize: 11, color: "#a0a3b8" }}>→ {obj.response}</div>
                </div>
              ))}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8, marginTop: 8 }}>
                <input value={newObj.objection} onChange={e => setNewObj(o => ({ ...o, objection: e.target.value }))}
                  placeholder="We already have a cleaner..."
                  style={{ background: "#0d0e18", border: "1px solid #1e2236", color: "#dde1f0", padding: "8px 10px", borderRadius: 7, fontSize: 11 }} />
                <input value={newObj.response} onChange={e => setNewObj(o => ({ ...o, response: e.target.value }))}
                  placeholder="That's great — what I've found is..."
                  style={{ background: "#0d0e18", border: "1px solid #1e2236", color: "#dde1f0", padding: "8px 10px", borderRadius: 7, fontSize: 11 }} />
                <button className="btn btn-g" style={{ fontSize: 11 }} onClick={addObjection}>+ Add</button>
              </div>
            </div>

            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button className="btn btn-g" onClick={() => setEditing(null)}>Cancel</button>
              <button className="btn btn-p" onClick={save} disabled={saving}>{saving ? "Saving..." : "Save Script"}</button>
            </div>
            {toast && <div style={{ marginTop: 10, fontSize: 12, color: "#10b981" }}>{toast}</div>}
          </div>
        ) : loading ? (
          <div style={{ textAlign: "center", padding: 40, color: "#525878" }}>Loading...</div>
        ) : scripts.length === 0 ? (
          <div style={{ textAlign: "center", padding: 40, color: "#525878" }}>
            <div style={{ fontSize: 32, marginBottom: 12 }}>📋</div>
            <div style={{ fontSize: 13, marginBottom: 16 }}>No scripts yet</div>
            <button className="btn btn-p" onClick={openNew}>Create your first script</button>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {scripts.map(s => (
              <div key={s.id} style={{ background: "#080810", border: "1px solid #1e2236", borderRadius: 10, padding: 14 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 8 }}>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "#dde1f0", marginBottom: 3 }}>{s.name}</div>
                    <div style={{ display: "flex", gap: 8, fontSize: 10, color: "#525878" }}>
                      {s.industry && <span style={{ background: "#5b5fef18", color: "#5b5fef", padding: "1px 6px", borderRadius: 4 }}>{s.industry}</span>}
                      <span>{s.usage_count || 0} uses</span>
                      {s.objection_handlers?.length > 0 && <span>{s.objection_handlers.length} objection{s.objection_handlers.length !== 1 ? "s" : ""}</span>}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 6 }}>
                    <button className="btn btn-g" style={{ fontSize: 11, padding: "4px 10px" }} onClick={() => openEdit(s)}>Edit</button>
                    <button className="btn btn-r" style={{ fontSize: 11, padding: "4px 10px" }} onClick={() => del(s.id)}>Del</button>
                  </div>
                </div>
                <div style={{ fontSize: 11, color: "#525878", lineHeight: 1.6, maxHeight: 60, overflow: "hidden" }}>
                  {s.script_text.slice(0, 150)}{s.script_text.length > 150 ? "..." : ""}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Stats Bar ──────────────────────────────────────────────────────────────────

function StatsBar({ stats, onCallbacks }) {
  if (!stats) return null
  return (
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 22 }}>
      {[
        { l: "TOTAL LEADS",    v: stats.total,           c: "#5b5fef" },
        { l: "NEW TODAY",      v: stats.newToday,         c: "#f0b429" },
        { l: "CALLS TODAY",    v: stats.callsToday,       c: "#94a3b8" },
        { l: "CALLBACKS DUE",  v: stats.callbacksDue,     c: "#8b5cf6", onClick: onCallbacks },
        { l: "INTERESTED",     v: stats.interested,       c: "#10b981" },
        { l: "CONVERTED",      v: stats.converted,        c: "#059669" },
        { l: "CONV RATE",      v: stats.conversionRate+"%", c: "#dde1f0" },
        { l: "AVG SCORE",      v: stats.avgScore,         c: scoreColor(stats.avgScore || 0) },
        ...(stats.totalRevenue > 0 ? [{ l: "REVENUE", v: "$" + stats.totalRevenue.toLocaleString(), c: "#059669" }] : []),
      ].map(s => (
        <div key={s.l} className="card"
          onClick={s.onClick}
          style={{ flex: "1 1 90px", minWidth: 90, padding: "14px 16px", cursor: s.onClick ? "pointer" : "default",
            border: s.onClick && stats.callbacksDue > 0 ? "1px solid #8b5cf640" : undefined }}>
          <div style={{ fontSize: 9, letterSpacing: ".1em", color: "#525878", marginBottom: 6 }}>{s.l}</div>
          <div className="stat-num" style={{ color: s.c }}>{s.v}</div>
        </div>
      ))}
    </div>
  )
}

// ── Rep Leaderboard ────────────────────────────────────────────────────────────

function RepBoard({ stats, statsRange, onRangeChange }) {
  const [expanded, setExpanded] = useState(null)
  if (!stats?.topReps?.length) return null
  const rangeLabel = { today: "Today", "7d": "Last 7 Days", "30d": "Last 30 Days" }
  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div style={{ fontSize: 10, letterSpacing: ".1em", color: "#525878" }}>
          REP SIGN-INS & PERFORMANCE — {rangeLabel[statsRange] || "Today"}
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          {["today", "7d", "30d"].map(r => (
            <button key={r} className="tab" onClick={() => onRangeChange(r)}
              style={{ background: statsRange === r ? "#5b5fef" : "transparent", color: statsRange === r ? "#fff" : "#525878",
                border: statsRange === r ? "none" : "1px solid #1e2236", fontSize: 10, padding: "4px 10px" }}>
              {r === "today" ? "Today" : r === "7d" ? "7 Days" : "30 Days"}
            </button>
          ))}
        </div>
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {stats.topReps.map(r => {
          const isExpanded = expanded === r.rep
          return (
            <div key={r.rep} onClick={() => setExpanded(isExpanded ? null : r.rep)}
              style={{ background: "#080810", border: `1px solid ${isExpanded ? "#5b5fef35" : "#181b2e"}`, borderRadius: 8,
                padding: "10px 14px", minWidth: 180, cursor: "pointer", transition: "border-color .15s" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <div style={{ fontSize: 12, color: "#dde1f0", fontWeight: 500 }}>👤 {r.rep}</div>
                {r.sessions > 0 && <div style={{ fontSize: 9, color: "#6b7194" }}>{r.sessions} session{r.sessions !== 1 ? "s" : ""}</div>}
              </div>
              {r.signedInAt && (
                <div style={{ fontSize: 10, color: "#6b7194", marginBottom: 4 }}>
                  signed in {new Date(r.signedInAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
                  {r.signedOutAt && <span> — out {new Date(r.signedOutAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span>}
                </div>
              )}
              <div style={{ display: "flex", gap: 12, fontSize: 11, color: "#525878" }}>
                <span>{r.calls} call{r.calls !== 1 ? "s" : ""}</span>
                {r.interested > 0 && <span style={{ color: "#10b981" }}>+{r.interested} int.</span>}
                {r.converted > 0 && <span style={{ color: "#059669" }}>✓{r.converted} conv.</span>}
              </div>
              {/* Expanded performance detail */}
              {isExpanded && (
                <div style={{ marginTop: 10, borderTop: "1px solid #181b2e", paddingTop: 10 }}>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, fontSize: 10 }}>
                    <div>
                      <div style={{ color: "#525878", marginBottom: 2 }}>CONV RATE</div>
                      <div style={{ color: parseFloat(r.convRate) > 0 ? "#059669" : "#525878", fontWeight: 600, fontSize: 14, fontFamily: "'Syne',sans-serif" }}>
                        {r.convRate}%
                      </div>
                    </div>
                    <div>
                      <div style={{ color: "#525878", marginBottom: 2 }}>INTEREST RATE</div>
                      <div style={{ color: parseFloat(r.interestRate) > 0 ? "#10b981" : "#525878", fontWeight: 600, fontSize: 14, fontFamily: "'Syne',sans-serif" }}>
                        {r.interestRate}%
                      </div>
                    </div>
                    <div>
                      <div style={{ color: "#525878", marginBottom: 2 }}>NO-ANSWER</div>
                      <div style={{ color: parseFloat(r.noAnswerRate) > 30 ? "#f06060" : "#525878", fontWeight: 600, fontSize: 14, fontFamily: "'Syne',sans-serif" }}>
                        {r.noAnswerRate}%
                      </div>
                    </div>
                    <div>
                      <div style={{ color: "#525878", marginBottom: 2 }}>CALLBACKS</div>
                      <div style={{ color: r.callbacks > 0 ? "#8b5cf6" : "#525878", fontWeight: 600, fontSize: 14, fontFamily: "'Syne',sans-serif" }}>
                        {r.callbacks}
                      </div>
                    </div>
                    {r.revenue > 0 && (
                      <div style={{ gridColumn: "1 / -1" }}>
                        <div style={{ color: "#525878", marginBottom: 2 }}>REVENUE</div>
                        <div style={{ color: "#059669", fontWeight: 600, fontSize: 14, fontFamily: "'Syne',sans-serif" }}>
                          ${r.revenue.toLocaleString()}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Main App ───────────────────────────────────────────────────────────────────

export default function App() {
  const [user, setUser]             = useState(() => api.isLoggedIn() ? localStorage.getItem("lf_user") : null)
  const [leads, setLeads]           = useState([])
  const [stats, setStats]           = useState(null)
  const [loading, setLoad]          = useState(false)
  const [toast, setToast]           = useState(null)

  // Modals
  const [callModal, setCallModal]   = useState(null)
  const [editModal, setEditModal]   = useState(null)
  const [showImport, setImport]     = useState(false)
  const [showScripts, setShowScripts] = useState(false)

  // Filters
  const [search, setSearch]         = useState("")
  const [fStatus, setFStatus]       = useState("all")
  const [sortBy, setSort]           = useState("newest")
  const [cbOnly, setCbOnly]         = useState(false)
  const [statsRange, setStatsRange] = useState("today")

  function toast_(msg, type = "success") {
    setToast({ msg, type }); setTimeout(() => setToast(null), 3200)
  }

  const load = useCallback(async () => {
    setLoad(true)
    try {
      const [l, s] = await Promise.all([
        api.getLeads({ search, status: fStatus, sortBy, callbacks_only: cbOnly }),
        api.getStats(statsRange),
      ])
      setLeads(l || []); setStats(s)
    } catch (ex) { toast_("Error loading — check Supabase config", "error") }
    finally { setLoad(false) }
  }, [search, fStatus, sortBy, cbOnly, statsRange])

  useEffect(() => {
    if (!user) return
    const t = setTimeout(load, search ? 350 : 0)
    return () => clearTimeout(t)
  }, [user, load])

  async function logout() {
    await api.logout(); setUser(null)
  }

  async function deleteL(id) {
    if (!window.confirm("Delete this lead?")) return
    try {
      await api.deleteLead(id)
      setLeads(p => p.filter(l => l.id !== id))
      toast_("Deleted", "error")
      api.getStats().then(setStats)
    } catch (ex) { toast_("Error: " + ex.message, "error") }
  }

  async function quickStatus(lead, status) {
    let cbDate = ""
    if (status === "callback") {
      cbDate = window.prompt("Callback date (YYYY-MM-DD):", new Date().toISOString().split("T")[0]) || ""
    }
    try {
      const updated = await api.updateStatus(lead.id, status, cbDate)
      if (updated) setLeads(p => p.map(l => l.id === lead.id ? updated : l))
      else setLeads(p => p.map(l => l.id === lead.id ? { ...l, status, callbackDate: cbDate } : l))
      api.getStats().then(setStats)
    } catch (ex) { toast_("Error updating status", "error") }
  }

  if (!user) return <Login onLogin={u => setUser(u)} />

  const si = v => STATUS_OPTIONS.find(s => s.value === v) || STATUS_OPTIONS[0]
  const today = new Date().toISOString().split("T")[0]

  return (
    <div style={{ minHeight: "100vh", background: "#080810" }}>
      <style>{CSS}</style>

      {/* ── Header ── */}
      <div style={{ borderBottom: "1px solid #181b2e", padding: "14px 28px", display: "flex", alignItems: "center", gap: 12 }}>
        <div style={{ fontFamily: "'Syne',sans-serif", fontSize: 19, fontWeight: 800, color: "#fff", letterSpacing: "-.02em" }}>
          LEAD<span style={{ color: "#5b5fef" }}>FLOW</span>
        </div>
        <div style={{ flex: 1 }} />
        {stats?.callbacksDue > 0 && (
          <button className="btn-amber btn" onClick={() => { setCbOnly(p => !p) }}>
            🔔 {stats.callbacksDue} callback{stats.callbacksDue !== 1 ? "s" : ""} due
          </button>
        )}
        <button className="btn btn-g" style={{ fontSize: 11 }} onClick={() => setShowScripts(true)}>📋 Scripts</button>
        <button className="btn btn-g" style={{ fontSize: 11 }} onClick={() => setImport(true)}>↑ Import</button>
        <button className="btn btn-p" style={{ fontSize: 11 }} onClick={() => setEditModal(true)}>+ Add Lead</button>
        <div style={{ fontSize: 11, color: "#525878", borderLeft: "1px solid #181b2e", paddingLeft: 12, display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ color: "#6b7194" }}>{user}</span>
          <button onClick={logout} style={{ background: "none", border: "none", color: "#f0606080", cursor: "pointer", fontSize: 10, fontFamily: "inherit" }}>sign out</button>
        </div>
      </div>

      <div style={{ padding: "22px 28px", maxWidth: 1500, margin: "0 auto" }}>

        <StatsBar stats={stats} onCallbacks={() => setCbOnly(p => !p)} />
        <RepBoard stats={stats} statsRange={statsRange} onRangeChange={setStatsRange} />

        {/* ── Filters ── */}
        <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap", alignItems: "center" }}>
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search name, company, phone..."
            style={{ background: "#0d0e1a", border: "1px solid #1e2236", color: "#dde1f0", padding: "8px 12px", borderRadius: 7, fontSize: 12, fontFamily: "inherit", outline: "none", flex: "1 1 200px", transition: "border-color .15s" }}
            onFocus={e => e.target.style.borderColor = "#5b5fef"}
            onBlur={e => e.target.style.borderColor = "#1e2236"} />
          <select className="sel" value={fStatus} onChange={e => setFStatus(e.target.value)}>
            <option value="all">All Statuses</option>
            {STATUS_OPTIONS.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
          <select className="sel" value={sortBy} onChange={e => setSort(e.target.value)}>
            <option value="newest">Newest First</option>
            <option value="oldest">Oldest First</option>
            <option value="score">Highest Score</option>
            <option value="company">Company A–Z</option>
            <option value="callbacks">Callbacks Due</option>
          </select>
          <button className="btn" onClick={() => setCbOnly(p => !p)}
            style={{ background: cbOnly ? "#8b5cf6" : "transparent", color: cbOnly ? "#fff" : "#6b7194",
              border: "1px solid " + (cbOnly ? "#8b5cf6" : "#1e2236"), padding: "8px 14px", fontSize: 11 }}>
            {cbOnly ? "✕ Show All" : "🔔 Callbacks Only"}
          </button>
          <div style={{ fontSize: 11, color: "#2a2d3e", marginLeft: "auto" }}>
            {loading ? "loading..." : `${leads.length} lead${leads.length !== 1 ? "s" : ""}`}
          </div>
        </div>

        {/* ── Lead list ── */}
        {!loading && leads.length === 0 ? (
          <div style={{ textAlign: "center", padding: "60px 0", color: "#2a2d3e" }}>
            <div style={{ fontFamily: "'Syne',sans-serif", fontSize: 44, fontWeight: 800, marginBottom: 8 }}>0</div>
            <div style={{ fontSize: 11, letterSpacing: ".1em" }}>
              {cbOnly ? "NO CALLBACKS DUE" : "NO LEADS YET — IMPORT A CSV OR ADD MANUALLY"}
            </div>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {leads.map(lead => {
              const info = si(lead.status)
              const isCb = lead.callbackDate && lead.callbackDate <= today && lead.status !== "converted"
              return (
                <div key={lead.id} className={`lrow${isCb ? " lrow-cb" : ""}`}
                  style={{ display: "grid", gridTemplateColumns: "auto 1fr auto auto", gap: 14, alignItems: "center" }}>

                  {/* Score */}
                  <ScoreRing score={lead.score || 0} />

                  {/* Info */}
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3, flexWrap: "wrap" }}>
                      <span style={{ fontFamily: "'Syne',sans-serif", fontSize: 13, fontWeight: 800, color: "#f0f2ff" }}>
                        {[lead.firstName, lead.lastName].filter(Boolean).join(" ") || lead.company}
                      </span>
                      {lead.title && <span style={{ fontSize: 10, color: "#525878" }}>· {lead.title}</span>}
                      {lead.source && <span className="src-tag">{lead.source}</span>}
                      {isCb && (
                        <span style={{ fontSize: 9, background: "#8b5cf618", color: "#8b5cf6", padding: "2px 7px", borderRadius: 4, border: "1px solid #8b5cf635" }}>
                          🔔 callback {lead.callbackDate}
                        </span>
                      )}
                      {lead.contract_value > 0 && (
                        <span style={{ fontSize: 9, background: "#059669", color: "#fff", padding: "2px 7px", borderRadius: 4 }}>
                          ${lead.contract_value?.toLocaleString()}
                        </span>
                      )}
                    </div>
                    {lead.firstName && lead.company && (
                      <div style={{ fontSize: 11, color: "#6b7194", marginBottom: 3 }}>
                        {lead.company}{lead.industry ? ` · ${lead.industry}` : ""}
                      </div>
                    )}
                    <div style={{ display: "flex", gap: 12, fontSize: 11, color: "#525878", flexWrap: "wrap" }}>
                      {lead.phone    && <span>📞 {lead.phone}</span>}
                      {lead.email    && <span>✉ {lead.email}</span>}
                      {(lead.city || lead.state) && <span>📍 {[lead.city, lead.state].filter(Boolean).join(", ")}</span>}
                      {lead.assignedTo && <span>👤 {lead.assignedTo}</span>}
                      {lead.total_calls > 0 && <span>📞 {lead.total_calls} call{lead.total_calls !== 1 ? "s" : ""}</span>}
                    </div>
                    {lead.notes && (
                      <div style={{ marginTop: 4, fontSize: 11, color: "#525878", borderLeft: "2px solid #1e2236", paddingLeft: 7, maxWidth: 520, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {lead.notes}
                      </div>
                    )}
                  </div>

                  {/* Status + quick-change */}
                  <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 115 }}>
                    <span className="pill" style={{ background: info.color + "20", color: info.color, border: `1px solid ${info.color}35`, textAlign: "center" }}>
                      {info.label}
                    </span>
                    <div style={{ display: "flex", gap: 3, flexWrap: "wrap" }}>
                      {STATUS_OPTIONS.filter(s => s.value !== lead.status).slice(0, 3).map(s => (
                        <button key={s.value} className="qs" onClick={() => quickStatus(lead, s.value)}
                          style={{ color: s.color, borderColor: s.color + "35" }}>{s.label}</button>
                      ))}
                    </div>
                  </div>

                  {/* Actions */}
                  <div style={{ display: "flex", flexDirection: "column", gap: 5, alignItems: "flex-end" }}>
                    <div style={{ fontSize: 9, color: "#2a2d3e" }}>{new Date(lead.createdAt).toLocaleDateString()}</div>
                    <button
                      onClick={() => setCallModal(lead)}
                      style={{ background: "#5b5fef18", color: "#5b5fef", border: "1px solid #5b5fef35", padding: "5px 11px", borderRadius: 6, cursor: "pointer", fontFamily: "inherit", fontSize: 11, fontWeight: 500, transition: "all .15s" }}
                      onMouseEnter={e => e.currentTarget.style.background = "#5b5fef28"}
                      onMouseLeave={e => e.currentTarget.style.background = "#5b5fef18"}>
                      📞 Log Call
                    </button>
                    <button className="btn btn-g" style={{ fontSize: 11, padding: "5px 11px" }} onClick={() => setEditModal(lead)}>Edit</button>
                    <button className="btn btn-r" onClick={() => deleteL(lead.id)}>Delete</button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* ── Modals ── */}
      {callModal && <CallModal lead={callModal} onClose={() => setCallModal(null)} onSaved={load} />}
      {editModal && (
        <LeadModal
          lead={editModal === true ? null : editModal}
          onClose={() => setEditModal(null)}
          onSaved={() => { load(); toast_(editModal === true ? "Lead added!" : "Lead updated") }}
        />
      )}
      {showImport && <ImportModal onClose={() => setImport(false)} onDone={msg => { toast_(msg); load() }} />}
      {showScripts && <ScriptsModal onClose={() => setShowScripts(false)} />}

      {/* ── Toast ── */}
      {toast && (
        <div className="toast" style={{ borderColor: toast.type === "error" ? "#f0606040" : "#5b5fef40", color: toast.type === "error" ? "#f06060" : "#dde1f0" }}>
          {toast.msg}
        </div>
      )}
    </div>
  )
}
