import { useEffect, useState } from 'react'
import { api } from '../api'

const PIPELINE = [
  ['Retrieve', 'For each blueprint row, the 5 passages most relevant to its concept are pulled from the indexed material.'],
  ['Draft', 'The language model writes one question, an answer key and citations, using only those passages.'],
  ['Audit', 'A rule-based checker (not the model) tests the draft against every field of the blueprint row.'],
  ['Repair', 'If a check fails, the model is told exactly what was wrong and asked to redraft — up to 2 times.'],
]

export default function Generate({ course, user, go }) {
  const [blueprints, setBlueprints] = useState([])
  const [selected, setSelected] = useState('')
  const [runs, setRuns] = useState([])
  const [error, setError] = useState('')

  useEffect(() => {
    api(`/courses/${course.course_id}/blueprints`).then((bps) => {
      const frozen = bps.filter((b) => b.frozen_at)
      setBlueprints(frozen)
      if (frozen.length) setSelected(frozen[frozen.length - 1].blueprint_id)
    })
  }, [course.course_id])

  const loadRuns = () => selected && api(`/blueprints/${selected}/runs`).then(setRuns)
  useEffect(() => { loadRuns() }, [selected])
  useEffect(() => {
    if (!runs.some((r) => r.status === 'running')) return
    const t = setInterval(loadRuns, 4000)
    return () => clearInterval(t)
  }, [runs])

  const start = () => api(`/blueprints/${selected}/generate`, { method: 'POST' }).then(loadRuns).catch((e) => setError(e.message))
  const bp = blueprints.find((b) => b.blueprint_id === selected)

  return (
    <>
      <h2>Step 3 · Generate questions</h2>
      <div className="intro">
        <strong>What happens here:</strong> pick a frozen blueprint and start a run. For every row of the blueprint the system does four things:
        <ol style={{ margin: '6px 0 0 18px' }}>{PIPELINE.map(([k, v]) => <li key={k}><strong>{k}.</strong> {v}</li>)}</ol>
        Nothing is shown to a reviewer until the audit has run, and every draft's audit result is kept.
      </div>

      <div className="card">
        <div className="row">
          <label>Blueprint:&nbsp;
            <select value={selected} onChange={(e) => setSelected(e.target.value)}>
              {blueprints.map((b) => <option key={b.blueprint_id} value={b.blueprint_id}>Version {b.version} · {b.constraints.length} questions</option>)}
            </select>
          </label>
          {user.role === 'reviewer'
            ? <button className="primary" disabled={!selected || runs.some((r) => r.status === 'running')} onClick={start}>Start generation run</button>
            : <span className="notice">Only a faculty reviewer can start a run.</span>}
        </div>
        {blueprints.length === 0 && <p className="muted">No frozen blueprint yet — go back to step 2.</p>}
        {bp && <p className="muted">Each run produces {bp.constraints.length} candidate question{bp.constraints.length === 1 ? '' : 's'}, one per blueprint row. On this machine a run takes roughly a minute per question.</p>}
        {error && <p className="error">{error}</p>}
      </div>

      {runs.length > 0 && (
        <div className="card">
          <h3>Runs for this blueprint</h3>
          <table>
            <thead><tr><th>Started</th><th>Status</th><th>Progress</th><th>Model</th><th>Reproducibility</th><th /></tr></thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.run_id}>
                  <td>{new Date(r.started_at).toLocaleString()}</td>
                  <td>
                    {r.status === 'completed' && <span className="pill ok">completed</span>}
                    {r.status === 'running' && <span className="pill brand">running…</span>}
                    {r.status === 'failed' && <span className="pill bad" title={r.error}>failed</span>}
                    {r.error && <div className="error">{r.error}</div>}
                  </td>
                  <td>{r.candidate_count} / {r.constraint_count} drafted & audited</td>
                  <td className="muted">{r.backend_id}</td>
                  <td className="muted">temp {r.temperature ?? 'n/a'} · seed {r.seed ?? 'n/a'} · prompt {r.prompt_hash.slice(0, 8)}</td>
                  <td>{r.status === 'completed' && <button className="primary" onClick={() => go('review', r.run_id)}>Review →</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
