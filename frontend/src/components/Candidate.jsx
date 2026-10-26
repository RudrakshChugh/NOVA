import { useEffect, useState } from 'react'
import { api } from '../api'
import { FIELD_LABEL, SIGNAL_HELP, optionText, pageLabel, plural } from '../format'
import Icon from './Icon'
import { Badge, Btn, Empty, Kbd, Meter, Note, Table, useShortcuts } from './ui'

/* ─── panels ─────────────────────────────────────────────────────────────────────────────── */

function Passage({ c }) {
  // Highlight the span the model quoted, when it was found verbatim in the passage.
  const [s, e] = c.char_span === 'not_found' ? [-1, -1] : c.char_span.split(':').map(Number)
  return (
    <div className="passage">
      <div className="meta">
        <strong>{pageLabel(c.page_ref)}</strong>
        <span className="row tight">support <Meter value={c.support_score} /></span>
        {s < 0 && <Badge tone="bad" dot>quoted text not found here</Badge>}
      </div>
      {s >= 0 ? <>{c.text.slice(0, s)}<mark>{c.text.slice(s, e)}</mark>{c.text.slice(e)}</> : c.text}
    </div>
  )
}

function Checklist({ comp }) {
  if (!comp) return <p className="muted">No compliance report.</p>
  return (
    <>
      <p className="muted">
        Each check is a fixed rule applied to the text. The model that wrote the question has no part
        in grading it.
      </p>
      <div className="checks">
        {comp.checks.map((c) => (
          <div key={c.field} className={`check ${c.passed ? 'ok' : 'bad'}`}>
            <Icon name={c.passed ? 'check' : 'x'} size={15} />
            <div>
              <div className="f">{FIELD_LABEL[c.field] || c.field}</div>
              {!c.passed && (
                <div className="d">
                  expected <b>{c.expected}</b><br />found <b>{c.observed}</b>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </>
  )
}

function Novelty({ n }) {
  if (!n) {
    return (
      <Empty icon="papers" title="Nothing to compare against">
        No past-year paper has been added for this course, so the novelty engine had no corpus.
      </Empty>
    )
  }
  const fitted = !n.model_version.startsWith('untrained')
  return (
    <>
      <div className="row">
        {n.is_duplicate
          ? <Badge tone="bad" dot>repeats a past-year question</Badge>
          : <Badge tone="ok" dot>no past-year match</Badge>}
        <span className="muted">
          duplicate probability <strong>{n.duplicate_probability.toFixed(2)}</strong> · flagged at {n.threshold.toFixed(2)}
        </span>
      </div>

      {!fitted && (
        <Note tone="warn">
          The combiner has not been fitted yet, so this score is the equal-weight average of the four
          signals — a placeholder, not a trained model. Build the pair set and fit it on the Engines page.
        </Note>
      )}

      <Table head={['Signal', 'What it measures', { label: 'Value', width: 150 }, ...(fitted ? [{ label: 'Weight', num: true, width: 90 }] : [])]}>
        {n.signals.map((sig) => (
          <tr key={sig.name}>
            <td className="cap" style={{ fontWeight: 550 }}>{sig.name}</td>
            <td className="muted">{SIGNAL_HELP[sig.name]}</td>
            <td><Meter value={sig.value} /></td>
            {fitted && <td className="num">{sig.weight == null ? '—' : sig.weight.toFixed(3)}</td>}
          </tr>
        ))}
      </Table>

      {n.nearest_pyq && (
        <div className="passage">
          <div className="meta">
            <strong>Closest past-year question</strong>
            <span>
              {n.nearest_paper}
              {n.nearest_pyq.number ? ` · Q${n.nearest_pyq.number}` : ''}
              {n.nearest_pyq.marks ? ` · ${n.nearest_pyq.marks} marks` : ''}
            </span>
          </div>
          {n.nearest_pyq.text}
        </div>
      )}

      <p className="muted">Scored by <span className="mono">{n.model_version}</span>.</p>
    </>
  )
}

function Drafts({ questionId }) {
  const [history, setHistory] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api(`/candidates/${questionId}/evidence`)
      .then((e) => setHistory(e.reports)).catch((e) => setError(e.message))
  }, [questionId])

  if (error) return <Note tone="bad">{error}</Note>
  if (!history) return <div className="skeleton" style={{ height: 70 }} />
  return (
    <>
      <p className="muted">
        Every draft of this question and what the audit said about it. The first entry is the
        first-draft result, which is what the compliance rate is measured on.
      </p>
      <ul className="timeline">
        {history.map((r, i) => (
          <li key={r.report_id} className={r.is_compliant ? 'ok' : 'bad'}>
            <strong>Draft {i + 1}</strong>
            {r.is_compliant
              ? ' passed every check'
              : ` failed ${r.violations.map((v) => FIELD_LABEL[v.field] || v.field).join(', ')}`}
            {!r.is_compliant && i < history.length - 1 && <span className="muted"> → sent back for repair</span>}
            {!r.is_compliant && i === history.length - 1 && <span className="muted"> → repair budget exhausted, shown as-is</span>}
          </li>
        ))}
      </ul>
    </>
  )
}

/* ─── decision ───────────────────────────────────────────────────────────────────────────── */

function DecisionBar({ q, canReview, onReviewed, shortcuts }) {
  const [mode, setMode] = useState(null) // null | 'edit' | 'reject'
  const [text, setText] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  useEffect(() => { setMode(null); setText(''); setError('') }, [q.question_id])

  const submit = async (body) => {
    setBusy(body.decision); setError('')
    try {
      const updated = await api(`/candidates/${q.question_id}/review`, { method: 'POST', body })
      setMode(null)
      onReviewed(updated)
    } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const startEdit = () => { setText(q.review?.edited_stem || q.stem); setMode('edit') }
  const startReject = () => { setText(''); setMode('reject') }

  useShortcuts({
    a: () => mode === null && submit({ decision: 'accept' }),
    e: () => mode === null && startEdit(),
    r: () => mode === null && startReject(),
    escape: () => setMode(null),
  }, shortcuts && canReview)

  if (!canReview) return null

  return (
    <div className="decision-bar">
      {mode === null && (
        <>
          <Btn variant="ok" icon="check" busy={busy === 'accept'} onClick={() => submit({ decision: 'accept' })}>
            Accept as-is {shortcuts && <Kbd>A</Kbd>}
          </Btn>
          <Btn icon="file" onClick={startEdit}>Edit wording {shortcuts && <Kbd>E</Kbd>}</Btn>
          <Btn variant="bad" icon="x" onClick={startReject}>Reject {shortcuts && <Kbd>R</Kbd>}</Btn>
          <div className="spacer" />
          {q.review && <span className="muted">Your decision can change until it is published.</span>}
        </>
      )}

      {mode === 'edit' && (
        <div className="stack" style={{ width: '100%' }}>
          <label className="field">
            <span className="lbl">Edited question stem</span>
            <textarea rows={3} autoFocus value={text} onChange={(e) => setText(e.target.value)} />
            <span className="hint">How much you change is recorded as a normalised edit distance.</span>
          </label>
          <div className="row">
            <Btn variant="primary" icon="check" busy={busy === 'edit'} disabled={!text.trim()} onClick={() => submit({ decision: 'edit', edited_stem: text })}>
              Save edited question
            </Btn>
            <Btn variant="ghost" onClick={() => setMode(null)}>Cancel {shortcuts && <Kbd>Esc</Kbd>}</Btn>
          </div>
        </div>
      )}

      {mode === 'reject' && (
        <div className="stack" style={{ width: '100%' }}>
          <label className="field">
            <span className="lbl">Why is it rejected?</span>
            <input type="text" autoFocus placeholder="e.g. answerable without the material" value={text} onChange={(e) => setText(e.target.value)} />
            <span className="hint">The reason is stored with the decision and read back during failure analysis.</span>
          </label>
          <div className="row">
            <Btn variant="bad" icon="x" busy={busy === 'reject'} disabled={!text.trim()} onClick={() => submit({ decision: 'reject', reason: text })}>
              Confirm reject
            </Btn>
            <Btn variant="ghost" onClick={() => setMode(null)}>Cancel {shortcuts && <Kbd>Esc</Kbd>}</Btn>
          </div>
        </div>
      )}

      {error && <div style={{ width: '100%' }}><Note tone="bad">{error}</Note></div>}
    </div>
  )
}

/* ─── candidate ──────────────────────────────────────────────────────────────────────────── */

export default function Candidate({ q, canReview, onReviewed, blinded = false, shortcuts = false }) {
  const [tab, setTab] = useState('checks')
  const c = q.constraint
  const comp = q.compliance
  const done = ['published', 'superseded'].includes(q.state)
  const stem = q.review?.edited_stem || q.stem

  useEffect(() => { setTab('checks') }, [q.question_id])

  const body = (
    <>
      <p className="cand-stem">{stem}</p>
      {q.options && <ol className="cand-options" type="A">{q.options.map((o) => <li key={o}>{optionText(o)}</li>)}</ol>}
      {q.review?.edited_stem && (
        <p className="muted">
          Original wording: {q.stem} <Badge tone="neutral">edit distance {q.review.edit_distance.toFixed(2)}</Badge>
        </p>
      )}
      <div className="keyline">
        <span className="k">Answer key · {plural(q.answer_key.trim().split(/\s+/).length, 'word')}</span>
        {q.answer_key}
      </div>
    </>
  )

  /* During a blinded study a reviewer sees only what they are judging. The audit panels are
     withheld because an arm that enforces nothing would be recognisable by their absence, and the
     repair count alone would identify the full system (plan §3.3.4). */
  if (blinded) {
    return (
      <article className="card">
        <div className="card-body">
          <div className="cand-head">
            <Badge tone="neutral" >{c.bloom_level}</Badge>
            <Badge tone="neutral">{c.marks} mark{c.marks === 1 ? '' : 's'}</Badge>
            <Badge tone="neutral">{c.question_type.replace('_', ' ')}</Badge>
          </div>
          {body}
        </div>
        <DecisionBar q={q} canReview={canReview} onReviewed={onReviewed} shortcuts={shortcuts} />
      </article>
    )
  }

  const violations = comp?.violations.length ?? 0
  const tabs = [
    { value: 'checks', label: 'Blueprint checks', icon: 'shield', badge: violations || '✓', badgeTone: violations ? 'bad' : '' },
    { value: 'novelty', label: 'Novelty', icon: 'target', badge: q.novelty?.is_duplicate ? '!' : null, badgeTone: 'bad' },
    { value: 'sources', label: 'Sources', icon: 'papers', badge: q.citations.length },
    { value: 'drafts', label: 'Draft history', icon: 'clock', badge: q.repair_attempts ? q.repair_attempts + 1 : null },
  ]

  return (
    <article className="card">
      <div className="card-body">
        <div className="cand-head">
          <Badge tone="solid">Q{c.position + 1}</Badge>
          {comp?.is_compliant
            ? <Badge tone="ok" dot>all {comp.checks.length} checks passed</Badge>
            : <Badge tone="bad" dot>{violations} of {comp?.checks.length} checks failed</Badge>}
          {q.novelty?.is_duplicate && <Badge tone="bad" dot>duplicate of a past paper</Badge>}
          <Badge tone="outline">{q.repair_attempts === 0 ? 'first draft' : `after ${q.repair_attempts} repair${q.repair_attempts === 1 ? '' : 's'}`}</Badge>
          {q.review && (
            <Badge tone="brand" dot>
              {q.review.decision === 'edit' ? 'edited' : `${q.review.decision}ed`} by {q.review.reviewer.name}
            </Badge>
          )}
          {q.state === 'published' && <Badge tone="ok" dot>published</Badge>}
        </div>

        <div className="row tight" style={{ fontSize: 12 }}>
          <span className="muted">{c.course_outcome}</span>
          <span className="muted">·</span>
          <span className="muted">{c.concept}</span>
          <span className="muted">·</span>
          <span className="muted cap">{c.bloom_level}, {c.marks} mark{c.marks === 1 ? '' : 's'}, {c.question_type.replace('_', ' ')}, {c.target_difficulty}</span>
        </div>

        {body}

        {q.review?.reason && <Note tone="bad" icon="x"><strong>Rejected:</strong> {q.review.reason}</Note>}

        <div className="divide">
          <div className="tabs" role="tablist">
            {tabs.map((t) => (
              <button key={t.value} role="tab" aria-selected={tab === t.value} onClick={() => setTab(t.value)}>
                <Icon name={t.icon} size={15} />
                {t.label}
                {t.badge != null && <span className={`tab-badge${t.badgeTone ? ` ${t.badgeTone}` : ''}`}>{t.badge}</span>}
              </button>
            ))}
          </div>

          <div className="stack">
            {tab === 'checks' && <Checklist comp={comp} />}
            {tab === 'novelty' && <Novelty n={q.novelty} />}
            {tab === 'sources' && (
              q.citations.length === 0
                ? <Note tone="bad">No passage was cited, so this question is not grounded in the material.</Note>
                : <>
                  <p className="muted">
                    Where the question came from. Highlighted text is what the model quoted as its
                    evidence, matched verbatim against the passage.
                  </p>
                  {q.citations.map((ct) => <Passage key={ct.citation_id} c={ct} />)}
                </>
            )}
            {tab === 'drafts' && <Drafts questionId={q.question_id} />}
          </div>
        </div>
      </div>

      {!done && <DecisionBar q={q} canReview={canReview} onReviewed={onReviewed} shortcuts={shortcuts} />}
    </article>
  )
}
