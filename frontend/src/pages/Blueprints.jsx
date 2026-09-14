import { Fragment, useEffect, useState } from 'react'
import { api } from '../api'

const BLOOM = ['remember', 'understand', 'apply', 'analyze', 'evaluate', 'create']
const TYPES = ['mcq', 'short_answer', 'long_answer', 'numerical']
const DIFF = ['easy', 'medium', 'hard']
const EMPTY = { course_outcome: '', concept: '', bloom_level: 'understand', marks: 2, question_type: 'short_answer', target_difficulty: 'easy' }

const FIELD_HELP = {
  course_outcome: 'The learning outcome this question assesses (e.g. "CO1 Explain DFD notation").',
  concept: 'The specific topic the question must be about. Used to retrieve the right passages.',
  bloom_level: 'Cognitive level: remember → understand → apply → analyze → evaluate → create. The question must open with a verb at this level.',
  marks: 'Mark weighting. The answer key must be proportionate (short for 1–2 marks, long for 6–10).',
  question_type: 'mcq (4 options), short_answer, long_answer, or numerical (numbers in question and answer).',
  target_difficulty: 'Easy pairs with remember/understand, medium with apply/analyze, hard with evaluate/create.',
}

export default function Blueprints({ course, user, go }) {
  const [blueprints, setBlueprints] = useState([])
  const [selected, setSelected] = useState(null)
  const [rows, setRows] = useState([])
  const [validation, setValidation] = useState(null)
  const [error, setError] = useState('')
  const [showHelp, setShowHelp] = useState(false)

  const select = (bp) => { setSelected(bp); setRows(bp.constraints.map(({ constraint_id: _id, position: _pos, ...r }) => r)); setValidation(null); setError('') }
  const load = () => api(`/courses/${course.course_id}/blueprints`).then((bps) => { setBlueprints(bps); return bps })
  useEffect(() => { load().then((bps) => { if (bps.length) select(bps[bps.length - 1]) }) }, [course.course_id]) // eslint-disable-line react-hooks/exhaustive-deps
  const run = (fn) => fn().catch((e) => setError(e.message))
  const createDraft = () => run(async () => { const bp = await api(`/courses/${course.course_id}/blueprints`, { method: 'POST' }); await load(); select(bp) })
  const save = () => run(async () => { const bp = await api(`/blueprints/${selected.blueprint_id}/constraints`, { method: 'PUT', body: rows }); await load(); select(bp); setValidation(await api(`/blueprints/${bp.blueprint_id}/validate`, { method: 'POST' })) })
  const freeze = () => run(async () => { const bp = await api(`/blueprints/${selected.blueprint_id}/freeze`, { method: 'POST' }); await load(); select(bp) })
  const setRow = (i, k, v) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)))

  const frozen = selected?.frozen_at != null
  const editable = user.role === 'coordinator' && selected && !frozen

  return (
    <>
      <h2>Step 2 · Define the blueprint</h2>
      <div className="intro">
        <strong>What happens here:</strong> the blueprint is the exam specification. Each row is one question the paper needs:
        which course outcome it assesses, which concept, at what Bloom level, for how many marks, and of what type.
        Once frozen, a blueprint gets a version number and can no longer change — every generated question is checked
        against its row and every published question records which version it came from.
        <button className="link" style={{ marginLeft: 8 }} onClick={() => setShowHelp(!showHelp)}>{showHelp ? 'hide field guide' : 'what does each field mean?'}</button>
        {showHelp && <dl className="legend" style={{ marginTop: 8 }}>{Object.entries(FIELD_HELP).map(([k, v]) => <Fragment key={k}><dt>{k.replace('_', ' ')}</dt><dd>{v}</dd></Fragment>)}</dl>}
      </div>

      <div className="card">
        <div className="row">
          <strong>Blueprints for this course:</strong>
          {blueprints.map((bp) => (
            <button key={bp.blueprint_id} className={selected?.blueprint_id === bp.blueprint_id ? 'primary' : ''} onClick={() => select(bp)}>
              {bp.frozen_at ? `Version ${bp.version}` : 'Draft'} · {bp.constraints.length} question{bp.constraints.length === 1 ? '' : 's'}
            </button>
          ))}
          {user.role === 'coordinator' && <button onClick={createDraft}>+ New draft</button>}
          {blueprints.length === 0 && user.role !== 'coordinator' && <span className="muted">No blueprints yet — the coordinator creates them.</span>}
        </div>
      </div>

      {selected && (
        <div className="card">
          <div className="row">
            <h3 style={{ margin: 0 }}>{frozen ? `Version ${selected.version}` : 'Draft'}</h3>
            {frozen
              ? <span className="pill ok">frozen {new Date(selected.frozen_at).toLocaleString()} — locked</span>
              : <span className="pill neutral">not yet versioned — editable</span>}
          </div>
          <table style={{ marginTop: 10 }}>
            <thead><tr><th>#</th><th>Course outcome</th><th>Concept</th><th>Bloom level</th><th>Marks</th><th>Type</th><th>Difficulty</th>{editable && <th />}</tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td>{i + 1}</td>
                  <td>{editable ? <input type="text" value={r.course_outcome} placeholder="CO1 Explain …" onChange={(e) => setRow(i, 'course_outcome', e.target.value)} /> : r.course_outcome}</td>
                  <td>{editable ? <input type="text" value={r.concept} placeholder="topic" onChange={(e) => setRow(i, 'concept', e.target.value)} /> : r.concept}</td>
                  <td>{editable ? <select value={r.bloom_level} onChange={(e) => setRow(i, 'bloom_level', e.target.value)}>{BLOOM.map((b) => <option key={b}>{b}</option>)}</select> : r.bloom_level}</td>
                  <td>{editable ? <input type="number" min={1} style={{ width: 60 }} value={r.marks} onChange={(e) => setRow(i, 'marks', +e.target.value)} /> : r.marks}</td>
                  <td>{editable ? <select value={r.question_type} onChange={(e) => setRow(i, 'question_type', e.target.value)}>{TYPES.map((t) => <option key={t}>{t}</option>)}</select> : r.question_type}</td>
                  <td>{editable ? <select value={r.target_difficulty} onChange={(e) => setRow(i, 'target_difficulty', e.target.value)}>{DIFF.map((d) => <option key={d}>{d}</option>)}</select> : r.target_difficulty}</td>
                  {editable && <td><button onClick={() => setRows(rows.filter((_, j) => j !== i))} title="remove row">✕</button></td>}
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={8} className="muted">No rows yet{editable ? ' — add one below' : ''}.</td></tr>}
            </tbody>
          </table>

          {editable && (
            <div className="row" style={{ marginTop: 12 }}>
              <button onClick={() => setRows([...rows, { ...EMPTY }])}>+ Add question row</button>
              <button className="primary" onClick={save}>Save & validate</button>
              <button className="ok" disabled={!validation?.ok} onClick={freeze} title={validation?.ok ? '' : 'save and fix validation issues first'}>Freeze as version</button>
            </div>
          )}
          {validation && (
            <div className="section">
              <h3>Validation</h3>
              {validation.ok
                ? <p><span className="pill ok">valid</span> {validation.total_marks} marks in total across {Object.keys(validation.outcomes).length} outcome(s). Freeze it to lock the version.</p>
                : <p><span className="pill bad">{validation.issues.length} issue(s)</span> fix these before freezing:</p>}
              {validation.issues.map((s, i) => <p key={i} className="error">• {s}</p>)}
              <dl className="kv">{Object.entries(validation.outcomes).map(([o, m]) => <Fragment key={o}><dt>{o}</dt><dd>{m} marks</dd></Fragment>)}</dl>
            </div>
          )}
          {error && <p className="error">{error}</p>}
          {frozen && <p style={{ marginTop: 12 }}><button className="primary" onClick={() => go('generate')}>Next: generate questions for version {selected.version} →</button></p>}
        </div>
      )}
    </>
  )
}
