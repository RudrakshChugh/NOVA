import { useEffect, useState } from 'react'
import { api } from '../api'
import { FIELD_LABEL, pageLabel } from '../format'

function Provenance({ s }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
      <div>
        <h3>Blueprint row</h3>
        <dl className="kv">
          <dt>Blueprint version</dt><dd>v{s.blueprint.version} (frozen {new Date(s.blueprint.frozen_at).toLocaleString()})</dd>
          <dt>Course outcome</dt><dd>{s.constraint.course_outcome}</dd>
          <dt>Concept</dt><dd>{s.constraint.concept}</dd>
          <dt>Bloom / marks / type</dt><dd>{s.constraint.bloom_level} · {s.constraint.marks} · {s.constraint.question_type}</dd>
        </dl>
        <h3 style={{ marginTop: 12 }}>Generation run</h3>
        <dl className="kv">
          <dt>Model</dt><dd>{s.run.backend_id}</dd>
          <dt>Started</dt><dd>{new Date(s.run.started_at).toLocaleString()}</dd>
          <dt>Temperature / seed</dt><dd>{s.run.temperature ?? 'n/a'} / {s.run.seed ?? 'n/a'}</dd>
          <dt>Prompt fingerprint</dt><dd className="muted">{s.run.prompt_hash.slice(0, 16)}…</dd>
        </dl>
        <h3 style={{ marginTop: 12 }}>Review</h3>
        <dl className="kv">
          <dt>Decision</dt><dd>{s.review.decision} by {s.review.reviewer}</dd>
          <dt>When</dt><dd>{new Date(s.review.reviewed_at).toLocaleString()}</dd>
          {s.review.edit_distance != null && <><dt>Edit distance</dt><dd>{s.review.edit_distance.toFixed(2)}</dd></>}
          {s.review.decision === 'edit' && <><dt>Original wording</dt><dd>{s.original_stem}</dd></>}
        </dl>
      </div>
      <div>
        <h3>Blueprint checks at publication</h3>
        <p className="muted">{s.compliance.is_compliant ? 'Passed every check' : `${s.compliance.violations.length} check(s) still failing`} after {s.compliance.repair_attempts} repair(s).</p>
        {s.compliance.violations.map((v) => <p key={v.field} className="error">✗ {FIELD_LABEL[v.field] || v.field}: expected {v.expected}; found {v.observed}</p>)}
        <h3 style={{ marginTop: 12 }}>Grounded in</h3>
        {s.citations.map((c, i) => <p key={i} className="muted">{c.material}, {pageLabel(c.page_ref)} — support {c.support_score.toFixed(2)}</p>)}
        <h3 style={{ marginTop: 12 }}>Answer key</h3>
        <p style={{ fontSize: 14 }}>{s.answer_key}</p>
      </div>
    </div>
  )
}

export default function Bank() {
  const [entries, setEntries] = useState([])
  const [open, setOpen] = useState(null)

  useEffect(() => { api('/bank/entries').then(setEntries) }, [])

  return (
    <>
      <h2>Step 5 · Question bank</h2>
      <div className="intro">
        <strong>What happens here:</strong> published questions live here with a version number and their complete history — which
        blueprint version and row asked for them, which model run wrote them, which passages they came from, how they fared
        on the blueprint checks, and who approved them. Nothing here can be silently changed; a re-publish creates a new version.
      </div>

      {entries.length === 0 ? <div className="card"><p className="muted">Nothing published yet. Accept or edit candidates in step 4, then publish.</p></div> : entries.map((e) => {
        const s = e.snapshot
        return (
          <div key={e.entry_id} className="card">
            <div className="row">
              <span className="pill brand">v{e.version}</span>
              <span className="pill neutral">{s.constraint.bloom_level}</span>
              <span className="pill neutral">{s.constraint.marks} marks</span>
              <span className="pill neutral">{s.constraint.question_type.replace('_', ' ')}</span>
              <span className="muted">published {new Date(e.published_at).toLocaleString()}</span>
              <button className="link" style={{ marginLeft: 'auto' }} onClick={() => setOpen(open === e.entry_id ? null : e.entry_id)}>{open === e.entry_id ? 'hide provenance' : 'show provenance'}</button>
            </div>
            <p className="question" style={{ fontSize: 16, fontWeight: 600 }}>{s.stem}</p>
            {s.options && <ol type="A">{s.options.map((o) => <li key={o}>{o}</li>)}</ol>}
            {open === e.entry_id && <div className="section"><Provenance s={s} /></div>}
          </div>
        )
      })}
    </>
  )
}
