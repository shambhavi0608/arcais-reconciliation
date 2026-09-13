import { useEffect, useMemo, useState } from 'react'

const STATUSES = ['Not Started', 'In Progress', 'Complete']

const STATUS_TO_COMPLETION_HINT = {
  'Not Started': 0,
  Complete: 1,
}

function statusClass(status) {
  if (status === 'Complete') return 'status-complete'
  if (status === 'In Progress') return 'status-progress'
  return 'status-none'
}

function pct(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return `${Math.round(n * 100)}%`
}

/**
 * Defensive fetch: never throws past this boundary. Returns
 * { data, error } so the UI can render something sensible either way
 * instead of a blank crashed screen.
 */
async function safeFetchJson(path) {
  try {
    const res = await fetch(path)
    if (!res.ok) {
      return { data: null, error: `${path} responded with HTTP ${res.status}` }
    }
    const text = await res.text()
    try {
      const json = JSON.parse(text)
      if (!Array.isArray(json)) {
        return { data: null, error: `${path} did not contain a JSON array` }
      }
      return { data: json, error: null }
    } catch {
      return { data: null, error: `${path} is not valid JSON` }
    }
  } catch (err) {
    return { data: null, error: `Could not load ${path}: ${err.message}` }
  }
}

/** Coerce whatever came out of reconciled.json into something the UI can
 * always render safely, even if a field is missing or malformed. */
function sanitizeControl(raw, idx) {
  const id = typeof raw?.id === 'string' && raw.id.trim() ? raw.id.trim() : `UNKNOWN-${idx}`
  const domain = typeof raw?.domain === 'string' && raw.domain.trim() ? raw.domain.trim() : 'Unspecified'
  const name = typeof raw?.name === 'string' && raw.name.trim() ? raw.name.trim() : '(untitled control)'
  const owner = typeof raw?.owner === 'string' ? raw.owner : ''
  const status = STATUSES.includes(raw?.status) ? raw.status : 'Not Started'
  let completion = typeof raw?.completion === 'number' && !Number.isNaN(raw.completion) ? raw.completion : null
  if (completion === null && status in STATUS_TO_COMPLETION_HINT) {
    completion = STATUS_TO_COMPLETION_HINT[status]
  }
  const evidence = typeof raw?.evidence === 'string' ? raw.evidence : ''
  const assessedOn = typeof raw?.assessed_on === 'string' ? raw.assessed_on : null
  return { id, domain, name, owner, status, completion, evidence, assessedOn }
}

function sanitizeException(raw, idx) {
  return {
    key: `${raw?.control_id ?? 'unknown'}-${idx}`,
    type: typeof raw?.type === 'string' ? raw.type : 'unknown',
    controlId: typeof raw?.control_id === 'string' ? raw.control_id : (raw?.control_id === null ? '—' : String(raw?.control_id ?? '—')),
    reason: typeof raw?.reason === 'string' ? raw.reason : JSON.stringify(raw),
  }
}

export default function App() {
  const [tab, setTab] = useState('controls')
  const [controls, setControls] = useState([])
  const [exceptions, setExceptions] = useState([])
  const [loadErrors, setLoadErrors] = useState([])
  const [search, setSearch] = useState('')
  const [activeDomain, setActiveDomain] = useState(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const [ctrlResult, excResult] = await Promise.all([
        safeFetchJson('/reconciled.json'),
        safeFetchJson('/exceptions.json'),
      ])
      if (cancelled) return

      const errs = []
      if (ctrlResult.error) errs.push(ctrlResult.error)
      if (excResult.error) errs.push(excResult.error)

      setControls((ctrlResult.data || []).map(sanitizeControl))
      setExceptions((excResult.data || []).map(sanitizeException))
      setLoadErrors(errs)
    })()
    return () => { cancelled = true }
  }, [])

  function updateControl(id, patch) {
    setControls((prev) => prev.map((c) => (c.id === id ? { ...c, ...patch } : c)))
  }

  const filteredControls = useMemo(() => {
    const q = search.trim().toLowerCase()
    return controls.filter((c) => {
      if (activeDomain && c.domain !== activeDomain) return false
      if (!q) return true
      return (
        c.id.toLowerCase().includes(q) ||
        c.name.toLowerCase().includes(q) ||
        c.owner.toLowerCase().includes(q) ||
        c.domain.toLowerCase().includes(q)
      )
    })
  }, [controls, search, activeDomain])

  const grouped = useMemo(() => {
    const map = new Map()
    for (const c of filteredControls) {
      if (!map.has(c.domain)) map.set(c.domain, [])
      map.get(c.domain).push(c)
    }
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]))
  }, [filteredControls])

  const domainStats = useMemo(() => {
    const map = new Map()
    for (const c of controls) {
      if (!map.has(c.domain)) map.set(c.domain, { total: 0, sum: 0, counted: 0 })
      const s = map.get(c.domain)
      s.total += 1
      if (typeof c.completion === 'number') {
        s.sum += c.completion
        s.counted += 1
      }
    }
    const out = []
    for (const [domain, s] of map.entries()) {
      out.push({ domain, total: s.total, pct: s.counted ? s.sum / s.counted : null })
    }
    return out.sort((a, b) => a.domain.localeCompare(b.domain))
  }, [controls])

  const overall = useMemo(() => {
    const withCompletion = controls.filter((c) => typeof c.completion === 'number')
    if (!withCompletion.length) return { pct: null, total: controls.length }
    const sum = withCompletion.reduce((acc, c) => acc + c.completion, 0)
    return { pct: sum / withCompletion.length, total: controls.length }
  }, [controls])

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          Control Review
          <small>ARCAIS compliance engagement</small>
        </div>

        <nav className="nav-tabs">
          <button
            className={`nav-tab ${tab === 'controls' ? 'active' : ''}`}
            onClick={() => setTab('controls')}
          >
            <span>Controls</span>
            <span className="count">{controls.length}</span>
          </button>
          <button
            className={`nav-tab ${tab === 'exceptions' ? 'active' : ''}`}
            onClick={() => setTab('exceptions')}
          >
            <span>Exceptions</span>
            <span className="count">{exceptions.length}</span>
          </button>
        </nav>

        {tab === 'controls' && (
          <div className="domain-filter-list">
            <h4>Domains</h4>
            <div
              className={`domain-filter-row ${activeDomain === null ? 'active' : ''}`}
              onClick={() => setActiveDomain(null)}
            >
              <span>All domains</span>
              <span className="pct">{controls.length}</span>
            </div>
            {domainStats.map((d) => (
              <div
                key={d.domain}
                className={`domain-filter-row ${activeDomain === d.domain ? 'active' : ''}`}
                onClick={() => setActiveDomain(d.domain)}
              >
                <span>{d.domain}</span>
                <span className="pct">{pct(d.pct)}</span>
              </div>
            ))}
          </div>
        )}
      </aside>

      <main className="main">
        {loadErrors.length > 0 && (
          <div className="load-error">
            {loadErrors.map((e, i) => (
              <div key={i}>⚠ {e}</div>
            ))}
          </div>
        )}

        {tab === 'controls' ? (
          <>
            <div className="top-row">
              <div>
                <h1 className="page-title">Control assessment review</h1>
                <p className="page-sub">Reconciled results from the latest assessment cycle</p>
              </div>
              <input
                className="search-box"
                placeholder="Search by id, name, owner, domain…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>

            <div className="summary-bar">
              <div className="summary-overall">
                <span className="value">{pct(overall.pct)}</span>
                <span className="label">overall complete</span>
              </div>
              <div className="summary-track">
                <div className="bar-outer">
                  <div className="bar-inner" style={{ width: overall.pct ? `${overall.pct * 100}%` : '0%' }} />
                </div>
                <div className="summary-meta">
                  <span>{overall.total} controls reconciled</span>
                  <span>{exceptions.length} exceptions logged</span>
                </div>
              </div>
            </div>

            {grouped.length === 0 && (
              <div className="empty-state">No controls match your search.</div>
            )}

            {grouped.map(([domain, items]) => {
              const stat = domainStats.find((d) => d.domain === domain)
              return (
                <div className="domain-group" key={domain}>
                  <div className="domain-heading">
                    <h3>{domain}</h3>
                    <span className="domain-pct">{pct(stat?.pct ?? null)} complete · {items.length} shown</span>
                  </div>
                  {items.map((c) => (
                    <div className="control-row" key={c.id}>
                      <div className="control-name-block">
                        <div className="id">{c.id}</div>
                        <div className="name">{c.name}</div>
                        {c.owner && <div className="owner">{c.owner}</div>}
                      </div>

                      <select
                        className={`status-select ${statusClass(c.status)}`}
                        value={c.status}
                        onChange={(e) => {
                          const newStatus = e.target.value
                          const patch = { status: newStatus }
                          if (newStatus === 'Not Started') patch.completion = 0
                          if (newStatus === 'Complete') patch.completion = 1
                          updateControl(c.id, patch)
                        }}
                      >
                        {STATUSES.map((s) => (
                          <option key={s} value={s}>{s}</option>
                        ))}
                      </select>

                      <div className="completion-cell">{pct(c.completion)}</div>

                      <textarea
                        className="evidence-input"
                        value={c.evidence}
                        placeholder="Evidence note…"
                        onChange={(e) => updateControl(c.id, { evidence: e.target.value })}
                      />
                    </div>
                  ))}
                </div>
              )
            })}
          </>
        ) : (
          <>
            <div className="top-row">
              <div>
                <h1 className="page-title">Exceptions</h1>
                <p className="page-sub">Records that could not be cleanly reconciled — see DATA_ISSUES.md for full reasoning</p>
              </div>
            </div>

            {exceptions.length === 0 ? (
              <div className="empty-state">No exceptions loaded.</div>
            ) : (
              <div>
                {exceptions.map((e) => (
                  <div className="exception-row" key={e.key}>
                    <div><span className="exception-type">{e.type}</span></div>
                    <div className="exception-id">{e.controlId}</div>
                    <div>{e.reason}</div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </main>
    </div>
  )
}
