import { useEffect, useState } from 'react'
import { api } from '../api'
import Candidate from '../components/Candidate'

export default function Review({ course, user, runId, go }) {
  const [runs, setRuns] = useState([])
  const [current, setCurrent] = useState(runId)
  const [run, setRun] = useState(null)
  const [candidates, setCandidates] = useState([])
  const [summary, setSummary] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api(`/courses/${course.course_id}/blueprints`).then(async (bps) => {
      const frozen = bps.filter((b) => b.frozen_at)
      const all = await Promise.all(frozen.map((b) => api(`/blueprints/${b.blueprint_id}/runs`).then((rs) => rs.map((r) => ({ ...r, version: b.version })))))
      const completed = all.flat().filter((r) => r.status === 'completed').sort((a, b) => b.started_at.localeCompare(a.started_at))
      setRuns(completed)
      if (!current && completed.length) setCurrent(completed[0].run_id)
    })
  }, [course.course_id])

  const refresh = async () => {
    if (!current) return
    setRun(await api(`/runs/${current}`))
    setCandidates(await api(`/runs/${current}/candidates`))
    setSummary(await api(`/runs/${current}/reviews`))
  }
  useEffect(() => { refresh().catch((e) => setError(e.message)) }, [current])

  const onReviewed = (updated) => { setCandidates(candidates.map((q) => (q.question_id === updated.question_id ? updated : q))); api(`/runs/${current}/reviews`).then(setSummary) }
  const publishable = candidates.filter((q) => ['accepted', 'edited'].includes(q.state))
  const publish = () => api('/candidates/publish', { method: 'POST', body: { question_ids: publishable.map((q) => q.question_id) } }).then(refresh).catch((e) => setError(e.message))
  const compliant = candidates.filter((q) => q.compliance?.is_compliant).length

  return (
    <>
      <h2>Step 4 · Review candidates</h2>
      <div className="intro">
        <strong>What happens here:</strong> every candidate is shown with the blueprint row it was written for, the result of each
        blueprint check, and the exact passages it was grounded in. Accept it, edit the wording, or reject it with a reason —
        every decision is stored and becomes training data for ranking future candidates. Accepted and edited questions can then
        be published to the question bank.
      </div>

      <div className="card">
        <div className="row">
          <label>Run:&nbsp;
            <select value={current || ''} onChange={(e) => setCurrent(e.target.value)}>
              {runs.length === 0 && <option value="">no completed runs yet</option>}
              {runs.map((r) => <option key={r.run_id} value={r.run_id}>{new Date(r.started_at).toLocaleString()} · blueprint v{r.version} · {r.candidate_count} questions</option>)}
            </select>
          </label>
          {runs.length === 0 && <button onClick={() => go('generate')}>Go to step 3 to generate →</button>}
        </div>
        {run && summary && (
          <div className="row" style={{ marginTop: 10 }}>
            <span className="pill ok">{compliant} passed all checks</span>
            <span className="pill bad">{candidates.length - compliant} with failed checks</span>
            <span className="muted">|</span>
            <span className="pill neutral">{summary.accepted} accepted</span>
            <span className="pill neutral">{summary.edited} edited</span>
            <span className="pill neutral">{summary.rejected} rejected</span>
            <span className="pill neutral">{summary.pending} awaiting decision</span>
            {summary.mean_edit_distance != null && <span className="muted">mean edit distance {summary.mean_edit_distance.toFixed(2)}</span>}
          </div>
        )}
        {user.role === 'reviewer' && publishable.length > 0 && (
          <p style={{ marginTop: 10 }}>
            <button className="primary" onClick={publish}>Publish {publishable.length} accepted/edited question{publishable.length === 1 ? '' : 's'} to the bank</button>
            <span className="muted"> — each gets a versioned entry with full provenance.</span>
          </p>
        )}
        {user.role !== 'reviewer' && candidates.length > 0 && <p className="notice" style={{ marginTop: 10 }}>You are viewing as coordinator; only reviewers can record decisions.</p>}
        {error && <p className="error">{error}</p>}
      </div>

      {candidates.map((q) => <Candidate key={q.question_id} q={q} canReview={user.role === 'reviewer'} onReviewed={onReviewed} />)}
      {candidates.some((q) => q.state === 'published') && <p><button className="primary" onClick={() => go('bank')}>Next: see the question bank →</button></p>}
    </>
  )
}
