import { useEffect, useState } from 'react'
import { api } from '../api'

const BLOOM = ['remember', 'understand', 'apply', 'analyze', 'evaluate', 'create']
const TYPES = ['mcq', 'short_answer', 'long_answer', 'numerical']
const DIFF = ['easy', 'medium', 'hard']
const EMPTY = { course_outcome: '', concept: '', bloom_level: 'understand', marks: 2, question_type: 'short_answer', target_difficulty: 'easy' }

export default function Blueprints({ course, user, onRunStarted }) {
  const [blueprints, setBlueprints] = useState([])
  const [selected, setSelected] = useState(null)
  const [rows, setRows] = useState([])
  const [validation, setValidation] = useState(null)
  const [error, setError] = useState('')

  const load = () => api(`/courses/${course.course_id}/blueprints`).then(setBlueprints)
  useEffect(() => { load() }, [course.course_id])

  const select = (bp) => { setSelected(bp); setRows(bp.constraints.map(({ constraint_id, position, ...r }) => r)); setValidation(null); setError('') }
  const run = (fn) => fn().catch((e) => setError(e.message))

  const createDraft = () => run(async () => { const bp = await api(`/courses/${course.course_id}/blueprints`, { method: 'POST' }); await load(); select(bp) })
  const save = () => run(async () => { const bp = await api(`/blueprints/${selected.blueprint_id}/constraints`, { method: 'PUT', body: rows }); await load(); select(bp) })
  const validate = () => run(async () => setValidation(await api(`/blueprints/${selected.blueprint_id}/validate`, { method: 'POST' })))
  const freeze = () => run(async () => { const bp = await api(`/blueprints/${selected.blueprint_id}/freeze`, { method: 'POST' }); await load(); select(bp) })
  const generate = () => run(async () => { const r = await api(`/blueprints/${selected.blueprint_id}/generate`, { method: 'POST' }); onRunStarted(r.run_id) })

  const setRow = (i, k, v) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)))
  const frozen = selected?.frozen_at != null
  const editable = user.role === 'coordinator' && selected && !frozen

  return (
    <>
      <h2>Blueprints</h2>
      <div className="card">
        {blueprints.map((bp) => (
          <button key={bp.blueprint_id} disabled={selected?.blueprint_id === bp.blueprint_id} onClick={() => select(bp)}>
            {bp.frozen_at ? `v${bp.version} (frozen)` : 'draft'} · {bp.constraints.length} rows
          </button>
        ))}
        {user.role === 'coordinator' && <button onClick={createDraft}>+ New draft</button>}
        {blueprints.length === 0 && user.role !== 'coordinator' && <span className="muted">No blueprints yet.</span>}
      </div>

      {selected && (
        <div className="card">
          <p className="muted">
            {frozen ? `Version ${selected.version}, frozen ${new Date(selected.frozen_at).toLocaleString()}` : 'Draft (not yet versioned)'}
          </p>
          <table>
            <thead><tr><th>#</th><th>Course outcome</th><th>Concept</th><th>Bloom</th><th>Marks</th><th>Type</th><th>Difficulty</th>{editable && <th />}</tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td>{i + 1}</td>
                  <td>{editable ? <input value={r.course_outcome} onChange={(e) => setRow(i, 'course_outcome', e.target.value)} /> : r.course_outcome}</td>
                  <td>{editable ? <input value={r.concept} onChange={(e) => setRow(i, 'concept', e.target.value)} /> : r.concept}</td>
                  <td>{editable ? <select value={r.bloom_level} onChange={(e) => setRow(i, 'bloom_level', e.target.value)}>{BLOOM.map((b) => <option key={b}>{b}</option>)}</select> : r.bloom_level}</td>
                  <td>{editable ? <input type="number" min={1} style={{ width: 50 }} value={r.marks} onChange={(e) => setRow(i, 'marks', +e.target.value)} /> : r.marks}</td>
                  <td>{editable ? <select value={r.question_type} onChange={(e) => setRow(i, 'question_type', e.target.value)}>{TYPES.map((t) => <option key={t}>{t}</option>)}</select> : r.question_type}</td>
                  <td>{editable ? <select value={r.target_difficulty} onChange={(e) => setRow(i, 'target_difficulty', e.target.value)}>{DIFF.map((d) => <option key={d}>{d}</option>)}</select> : r.target_difficulty}</td>
                  {editable && <td><button onClick={() => setRows(rows.filter((_, j) => j !== i))}>✕</button></td>}
                </tr>
              ))}
            </tbody>
          </table>
          <p>
            {editable && <><button onClick={() => setRows([...rows, { ...EMPTY }])}>+ Row</button> <button onClick={save}>Save rows</button> </>}
            {selected && <button onClick={validate}>Validate</button>}{' '}
            {editable && <button onClick={freeze}>Freeze as version</button>}{' '}
            {frozen && user.role === 'reviewer' && <button onClick={generate}>Generate questions</button>}
          </p>
          {validation && (
            <div>
              <span className={`badge ${validation.ok ? 'ok' : 'bad'}`}>{validation.ok ? 'valid' : `${validation.issues.length} issue(s)`}</span>
              total marks {validation.total_marks} · outcomes: {Object.entries(validation.outcomes).map(([o, m]) => `${o} (${m})`).join(', ')}
              {validation.issues.map((s, i) => <p key={i} className="error">{s}</p>)}
            </div>
          )}
          {error && <p className="error">{error}</p>}
        </div>
      )}
    </>
  )
}
