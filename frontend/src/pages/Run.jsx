import { useEffect, useState } from 'react'
import { api } from '../api'

function Candidate({ q, canReview, onReviewed }) {
  const [mode, setMode] = useState(null) // 'edit' | 'reject'
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const c = q.compliance

  const submit = (body) => api(`/candidates/${q.question_id}/review`, { method: 'POST', body })
    .then((updated) => { setMode(null); onReviewed(updated) })
    .catch((e) => setError(e.message))

  return (
    <div className="card">
      <p className="muted">
        #{q.constraint.position + 1} · {q.constraint.course_outcome} · {q.constraint.concept} · {q.constraint.bloom_level} · {q.constraint.marks} marks · {q.constraint.question_type} · {q.constraint.target_difficulty}
      </p>
      <p>
        {c && (c.is_compliant
          ? <span className="badge ok">compliant</span>
          : c.violations.map((v) => <span key={v.field} className="badge bad" title={`expected ${v.expected}; observed ${v.observed}`}>violates {v.field}</span>))}
        {c && <span className="muted">satisfaction {(c.satisfaction_rate * 100).toFixed(0)}% · repairs {q.repair_attempts}</span>}
        {' '}<span className="badge">{q.state}</span>
      </p>
      <p><strong>{q.review?.edited_stem || q.stem}</strong></p>
      {q.options && <ol type="A">{q.options.map((o) => <li key={o}>{o}</li>)}</ol>}
      <details><summary>Answer key</summary><p>{q.answer_key}</p></details>
      {c && !c.is_compliant && (
        <details><summary>Violations</summary>
          <table><tbody>{c.violations.map((v) => <tr key={v.field}><td>{v.field}</td><td>expected: {v.expected}</td><td>observed: {v.observed}</td></tr>)}</tbody></table>
        </details>
      )}
      {q.citations.map((ct) => (
        <div key={ct.citation_id} className="cite">
          <strong>{ct.page_ref}</strong> · support {ct.support_score.toFixed(2)} · chars {ct.char_span}<br />{ct.text}
        </div>
      ))}
      {q.citations.length === 0 && <p className="cite">No citation attached.</p>}
      {q.review && <p className="muted">{q.review.decision} by {q.review.reviewer.name}{q.review.edit_distance != null && ` · edit distance ${q.review.edit_distance.toFixed(2)}`}{q.review.reason && ` · ${q.review.reason}`}</p>}
      {canReview && !['published', 'superseded'].includes(q.state) && (
        <div>
          {mode === null && <>
            <button onClick={() => submit({ decision: 'accept' })}>Accept</button>{' '}
            <button onClick={() => { setText(q.stem); setMode('edit') }}>Edit</button>{' '}
            <button onClick={() => { setText(''); setMode('reject') }}>Reject</button>
          </>}
          {mode === 'edit' && <>
            <textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} />
            <button onClick={() => submit({ decision: 'edit', edited_stem: text })}>Save edit</button> <button onClick={() => setMode(null)}>Cancel</button>
          </>}
          {mode === 'reject' && <>
            <input placeholder="Reason" value={text} onChange={(e) => setText(e.target.value)} />{' '}
            <button onClick={() => submit({ decision: 'reject', reason: text })}>Confirm reject</button> <button onClick={() => setMode(null)}>Cancel</button>
          </>}
          {error && <p className="error">{error}</p>}
        </div>
      )}
    </div>
  )
}

export default function Run({ course, user, runId, onSelectRun }) {
  const [runs, setRuns] = useState([])
  const [run, setRun] = useState(null)
  const [candidates, setCandidates] = useState([])
  const [summary, setSummary] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api(`/courses/${course.course_id}/blueprints`).then(async (bps) => {
      const all = await Promise.all(bps.filter((b) => b.frozen_at).map((b) => api(`/blueprints/${b.blueprint_id}/runs`).then((rs) => rs.map((r) => ({ ...r, version: b.version })))))
      setRuns(all.flat().sort((a, b) => b.started_at.localeCompare(a.started_at)))
    })
  }, [course.course_id])

  const refresh = async () => {
    if (!runId) return
    const r = await api(`/runs/${runId}`)
    setRun(r)
    if (r.status !== 'running') {
      setCandidates(await api(`/runs/${runId}/candidates`))
      setSummary(await api(`/runs/${runId}/reviews`))
    }
  }

  useEffect(() => { setRun(null); setCandidates([]); setSummary(null); refresh().catch((e) => setError(e.message)) }, [runId])
  useEffect(() => {
    if (run?.status !== 'running') return
    const t = setInterval(() => refresh().catch((e) => setError(e.message)), 3000)
    return () => clearInterval(t)
  }, [run?.status, runId])

  const onReviewed = (updated) => { setCandidates(candidates.map((q) => (q.question_id === updated.question_id ? updated : q))); api(`/runs/${runId}/reviews`).then(setSummary) }
  const publishable = candidates.filter((q) => ['accepted', 'edited'].includes(q.state))
  const publish = () => api('/candidates/publish', { method: 'POST', body: { question_ids: publishable.map((q) => q.question_id) } }).then(refresh).catch((e) => setError(e.message))

  return (
    <>
      <h2>Generation runs</h2>
      <div className="card">
        <select value={runId || ''} onChange={(e) => onSelectRun(e.target.value)}>
          <option value="">— select a run —</option>
          {runs.map((r) => <option key={r.run_id} value={r.run_id}>{new Date(r.started_at).toLocaleString()} · blueprint v{r.version} · {r.status}</option>)}
        </select>
        {run && <p className="muted">
          status <strong>{run.status}</strong> · backend {run.backend_id} · prompt {run.prompt_hash.slice(0, 12)} · temperature {run.temperature ?? 'n/a'} · seed {run.seed ?? 'n/a'}
          {run.error && <span className="error"> · {run.error}</span>}
        </p>}
        {run?.status === 'running' && <p>Retrieving passages, drafting and auditing… (polling)</p>}
        {error && <p className="error">{error}</p>}
      </div>

      {summary && (
        <div className="card">
          <strong>Review summary:</strong> {summary.accepted} accepted · {summary.edited} edited · {summary.rejected} rejected · {summary.pending} pending of {summary.total}
          {summary.mean_edit_distance != null && ` · mean edit distance ${summary.mean_edit_distance.toFixed(2)}`}
          {user.role === 'reviewer' && publishable.length > 0 && <> {' '}<button onClick={publish}>Publish {publishable.length} to question bank</button></>}
        </div>
      )}

      {candidates.map((q) => <Candidate key={q.question_id} q={q} canReview={user.role === 'reviewer'} onReviewed={onReviewed} />)}
    </>
  )
}
