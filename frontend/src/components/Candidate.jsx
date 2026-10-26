import { useState } from 'react'
import { api } from '../api'
import { FIELD_LABEL, SIGNAL_HELP, pageLabel, pct } from '../format'

function Passage({ c }) {
  // Highlight the quoted span the model cited, when it was found verbatim in the passage.
  const [s, e] = c.char_span === 'not_found' ? [-1, -1] : c.char_span.split(':').map(Number)
  return (
    <div className="passage">
      <div className="meta">
        <strong>{pageLabel(c.page_ref)}</strong>
        <span>support <span className="meter"><i style={{ width: pct(Math.max(0, Math.min(1, c.support_score))) }} /></span> {c.support_score.toFixed(2)}</span>
        {s < 0 && <span className="pill bad">quoted text not found in passage</span>}
      </div>
      {s >= 0 ? <>{c.text.slice(0, s)}<mark>{c.text.slice(s, e)}</mark>{c.text.slice(e)}</> : c.text}
    </div>
  )
}

function Checklist({ checks }) {
  return (
    <div className="checks">
      {checks.map((c) => (
        <div key={c.field} className={`check ${c.passed ? 'ok' : 'bad'}`}>
          <div className="f">{c.passed ? '✓' : '✗'} {FIELD_LABEL[c.field] || c.field}</div>
          {!c.passed && <div className="d">expected: {c.expected}<br />found: {c.observed}</div>}
        </div>
      ))}
    </div>
  )
}

function Novelty({ n }) {
  if (!n) {
    return <p className="muted">No past-year paper has been uploaded for this course, so the novelty engine had nothing to compare against.</p>
  }
  const fitted = !n.model_version.startsWith('untrained')
  return (
    <>
      <p>
        {n.is_duplicate
          ? <span className="pill bad">repeats a past-year question</span>
          : <span className="pill ok">no past-year match</span>}
        <span className="muted">
          duplicate probability {n.duplicate_probability.toFixed(2)} · flagged at {n.threshold.toFixed(2)}
        </span>
      </p>
      {!fitted && (
        <p className="notice">
          The combiner has not been fitted yet, so this score is the placeholder equal-weight average of the
          four signals — not a trained model. Build the labelled pair set and train it from the Engines page.
        </p>
      )}
      <table>
        <thead><tr><th>Signal</th><th>What it measures</th><th>Value</th>{fitted && <th>Learned weight</th>}</tr></thead>
        <tbody>
          {n.signals.map((sig) => (
            <tr key={sig.name}>
              <td style={{ textTransform: 'capitalize' }}>{sig.name}</td>
              <td className="muted">{SIGNAL_HELP[sig.name]}</td>
              <td>
                <span className="meter"><i style={{ width: pct(Math.max(0, Math.min(1, sig.value))) }} /></span>{' '}
                {sig.value.toFixed(2)}
              </td>
              {fitted && <td>{sig.weight == null ? '—' : sig.weight.toFixed(2)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
      {n.nearest_pyq && (
        <div className="passage">
          <div className="meta"><strong>Closest past-year question</strong><span>{n.nearest_paper}{n.nearest_pyq.number ? ` · Q${n.nearest_pyq.number}` : ''}{n.nearest_pyq.marks ? ` · ${n.nearest_pyq.marks} marks` : ''}</span></div>
          {n.nearest_pyq.text}
        </div>
      )}
      <p className="muted">Scored by {n.model_version}.</p>
    </>
  )
}


export default function Candidate({ q, canReview, onReviewed, blinded = false }) {
  const [mode, setMode] = useState(null) // 'edit' | 'reject'
  const [text, setText] = useState('')
  const [history, setHistory] = useState(null)
  const [error, setError] = useState('')
  const c = q.constraint
  const comp = q.compliance
  const done = ['published', 'superseded'].includes(q.state)

  const submit = (body) => api(`/candidates/${q.question_id}/review`, { method: 'POST', body })
    .then((updated) => { setMode(null); onReviewed(updated) })
    .catch((e) => setError(e.message))

  const toggleHistory = async () => {
    if (history) return setHistory(null)
    setHistory((await api(`/candidates/${q.question_id}/evidence`)).reports)
  }

  if (blinded) {
    // During a blinded evaluation a reviewer sees only what they are judging. The audit panels
    // are withheld because an arm that enforces nothing would be recognisable by their absence,
    // and the repair count alone would identify the full system (plan §3.3.4).
    return (
      <div className="card cand">
        <div className="constraint">
          <span className="pill neutral">{c.bloom_level}</span>
          <span className="pill neutral">{c.marks} mark{c.marks === 1 ? '' : 's'}</span>
          <span className="pill neutral">{c.question_type.replace('_', ' ')}</span>
        </div>
        <p className="question">{q.stem}</p>
        {q.options && <ol type="A">{q.options.map((o) => <li key={o}>{o}</li>)}</ol>}
        <details><summary className="muted">Answer key</summary><p>{q.answer_key}</p></details>
        {canReview && (
          <div className="section">
            <h3>Your decision</h3>
            {mode === null && (
              <div className="row">
                <button className="ok" onClick={() => submit({ decision: 'accept' })}>Accept as-is</button>
                <button onClick={() => { setText(q.stem); setMode('edit') }}>Edit wording</button>
                <button className="bad" onClick={() => { setText(''); setMode('reject') }}>Reject</button>
              </div>
            )}
            {mode === 'edit' && (
              <>
                <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} />
                <div className="row" style={{ marginTop: 6 }}>
                  <button className="primary" onClick={() => submit({ decision: 'edit', edited_stem: text })}>Save edited question</button>
                  <button onClick={() => setMode(null)}>Cancel</button>
                </div>
              </>
            )}
            {mode === 'reject' && (
              <div className="row">
                <input type="text" style={{ maxWidth: 420 }} placeholder="Why is it rejected? (required)" value={text} onChange={(e) => setText(e.target.value)} />
                <button className="bad" disabled={!text.trim()} onClick={() => submit({ decision: 'reject', reason: text })}>Confirm reject</button>
                <button onClick={() => setMode(null)}>Cancel</button>
              </div>
            )}
            {error && <p className="error">{error}</p>}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className={`card cand ${comp?.is_compliant && q.state !== 'duplicate' ? 'ok' : 'bad'}`}>
      <div className="constraint">
        <span className="pill brand">Q{c.position + 1}</span>
        <span className="pill neutral">{c.course_outcome}</span>
        <span className="pill neutral">{c.concept}</span>
        <span className="pill neutral">{c.bloom_level}</span>
        <span className="pill neutral">{c.marks} mark{c.marks === 1 ? '' : 's'}</span>
        <span className="pill neutral">{c.question_type.replace('_', ' ')}</span>
        <span className="pill neutral">{c.target_difficulty}</span>
      </div>

      <div className="row">
        {comp?.is_compliant
          ? <span className="pill ok">passed all {comp.checks.length} blueprint checks</span>
          : <span className="pill bad">failed {comp?.violations.length} of {comp?.checks.length} blueprint checks</span>}
        <span className="muted">{q.repair_attempts === 0 ? 'first draft' : `after ${q.repair_attempts} repair${q.repair_attempts === 1 ? '' : 's'}`}</span>
        {q.review && <span className="pill brand">{q.review.decision === 'edit' ? 'edited' : q.review.decision + 'ed'} by {q.review.reviewer.name}</span>}
        {q.novelty?.is_duplicate && <span className="pill bad">duplicate of a past paper</span>}
        {q.state === 'published' && <span className="pill ok">published</span>}
      </div>

      <p className="question">{q.review?.edited_stem || q.stem}</p>
      {q.review?.edited_stem && <p className="muted">Original: {q.stem} (edit distance {q.review.edit_distance.toFixed(2)})</p>}
      {q.options && <ol type="A">{q.options.map((o) => <li key={o}>{o}</li>)}</ol>}
      <details><summary className="muted">Answer key ({q.answer_key.split(/\s+/).length} words)</summary><p>{q.answer_key}</p></details>

      <div className="section">
        <h3>Blueprint checks</h3>
        <p className="muted">Each check is a fixed rule applied to the text — the model does not grade itself.</p>
        {comp && <Checklist checks={comp.checks} />}
        <p><button className="link" onClick={toggleHistory}>{history ? 'hide draft history' : 'show draft history'}</button></p>
        {history && (
          <ul className="timeline">
            {history.map((r, i) => (
              <li key={r.report_id} className={r.is_compliant ? 'ok' : 'bad'}>
                <strong>Draft {i + 1}</strong>: {r.is_compliant ? 'passed every check' : `failed ${r.violations.map((v) => FIELD_LABEL[v.field] || v.field).join(', ')}`}
                {!r.is_compliant && i < history.length - 1 && ' → sent back for repair'}
                {!r.is_compliant && i === history.length - 1 && ' → repair budget exhausted, shown to reviewer as-is'}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="section">
        <h3>Novelty check</h3>
        <p className="muted">How much this question resembles the course's past-year papers. Four signals are scored and combined; the model does not judge its own output here either.</p>
        <Novelty n={q.novelty} />
      </div>

      <div className="section">
        <h3>Source passages</h3>
        <p className="muted">Where this question came from in the uploaded material. Highlighted text is what the model quoted as its evidence.</p>
        {q.citations.length === 0 ? <p className="error">No passage cited.</p> : q.citations.map((ct) => <Passage key={ct.citation_id} c={ct} />)}
      </div>

      {canReview && !done && (
        <div className="section">
          <h3>Your decision</h3>
          {q.review && <p className="muted">You can change your decision until the question is published.</p>}
          {mode === null && (
            <div className="row">
              <button className="ok" onClick={() => submit({ decision: 'accept' })}>Accept as-is</button>
              <button onClick={() => { setText(q.review?.edited_stem || q.stem); setMode('edit') }}>Edit wording</button>
              <button className="bad" onClick={() => { setText(''); setMode('reject') }}>Reject</button>
            </div>
          )}
          {mode === 'edit' && (
            <>
              <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} />
              <div className="row" style={{ marginTop: 6 }}>
                <button className="primary" onClick={() => submit({ decision: 'edit', edited_stem: text })}>Save edited question</button>
                <button onClick={() => setMode(null)}>Cancel</button>
                <span className="muted">How much you change is recorded as an edit distance.</span>
              </div>
            </>
          )}
          {mode === 'reject' && (
            <div className="row">
              <input type="text" style={{ maxWidth: 420 }} placeholder="Why is it rejected? (required)" value={text} onChange={(e) => setText(e.target.value)} />
              <button className="bad" disabled={!text.trim()} onClick={() => submit({ decision: 'reject', reason: text })}>Confirm reject</button>
              <button onClick={() => setMode(null)}>Cancel</button>
            </div>
          )}
          {error && <p className="error">{error}</p>}
        </div>
      )}
      {q.review?.reason && <p className="muted">Reason: {q.review.reason}</p>}
    </div>
  )
}
