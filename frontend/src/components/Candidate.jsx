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

// What a draft was nearest to. A repeat is not only a repeat of a past paper: it can be of
// another question on the same paper, or of one already published.
const SOURCE_COPY = {
  pyq: { a: 'a past-year question', closest: 'Closest past-year question' },
  run: { a: 'another question on this paper', closest: 'Closest question on this paper' },
  bank: { a: 'a question already in the bank', closest: 'Closest question in the bank' },
}
const COMPARED_WITH = 'past papers, the question bank and the other questions of this run'

/** Duplicate probability as an arc, with the flagging threshold marked on it. */
function Dial({ value, threshold, tone }) {
  const r = 46
  const c = 2 * Math.PI * r
  const v = Math.max(0, Math.min(1, value ?? 0))
  const at = Math.max(0, Math.min(1, threshold ?? 0)) * 2 * Math.PI
  return (
    <div className={`dial ${tone}`} role="img" aria-label={`Duplicate probability ${v.toFixed(2)}, flagged at ${(threshold ?? 0).toFixed(2)}`}>
      <svg width="108" height="108" viewBox="0 0 108 108">
        <circle className="track" cx="54" cy="54" r={r} fill="none" strokeWidth="8" />
        <circle className="fill" cx="54" cy="54" r={r} fill="none" strokeWidth="8" strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - v)} />
        <line className="tick" strokeWidth="2.5" strokeLinecap="round"
          x1={54 + (r - 8) * Math.cos(at)} y1={54 + (r - 8) * Math.sin(at)}
          x2={54 + (r + 8) * Math.cos(at)} y2={54 + (r + 8) * Math.sin(at)} />
      </svg>
      <div className="read"><div><b>{v.toFixed(2)}</b><span>duplicate</span></div></div>
    </div>
  )
}

function Novelty({ n }) {
  if (!n) {
    return (
      <Empty icon="papers" title="Nothing to compare against">
        No past-year paper has been added for this subject, the bank is empty and this was the
        first question of its run — so there was nothing for the novelty engine to compare it with.
      </Empty>
    )
  }
  const fitted = !n.model_version.startsWith('untrained')
  const source = n.nearest_source || (n.nearest_pyq ? 'pyq' : null)
  const what = SOURCE_COPY[source] || SOURCE_COPY.pyq
  const nearestText = n.nearest_text || n.nearest_pyq?.text
  // Close to the line without crossing it: worth a reviewer's glance, not a flag.
  const near = !n.is_duplicate && n.threshold > 0 && n.duplicate_probability >= n.threshold * 0.8
  return (
    <>
      <div className="dial-row">
        <Dial value={n.duplicate_probability} threshold={n.threshold} tone={n.is_duplicate ? 'bad' : near ? 'warn' : ''} />
        <div className="dial-copy">
          <h3>{n.is_duplicate ? `Repeats ${what.a}` : near ? `Close to ${what.a}` : 'New question'}</h3>
          <p className="muted">
            {n.is_duplicate
              ? `Its duplicate probability is at or above the ${n.threshold.toFixed(2)} line, so it was sent back to be rewritten${source === 'pyq' ? '' : ` — it matched ${what.a}, not a past paper`}.`
              : `Compared with ${COMPARED_WITH}. The nearest match scores ${n.duplicate_probability.toFixed(2)}; a draft is flagged at ${n.threshold.toFixed(2)}.`}
          </p>
        </div>
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
            <td className="muted prose">{SIGNAL_HELP[sig.name]}</td>
            <td><Meter value={sig.value} /></td>
            {fitted && <td className="num">{sig.weight == null ? '—' : sig.weight.toFixed(3)}</td>}
          </tr>
        ))}
      </Table>

      {nearestText && (
        <div className="passage">
          <div className="meta">
            <strong>{what.closest}</strong>
            <span>
              {source === 'pyq' || !source
                ? <>
                  {n.nearest_paper}
                  {n.nearest_pyq?.number ? ` · Q${n.nearest_pyq.number}` : ''}
                  {n.nearest_pyq?.marks ? ` · ${n.nearest_pyq.marks} marks` : ''}
                </>
                : n.nearest_label}
            </span>
          </div>
          {nearestText}
        </div>
      )}

      <p className="muted">Scored by <span className="mono">{n.model_version}</span>.</p>
    </>
  )
}

/** What the answer key rests on. Each general statement it makes is shown with the sentence of
 *  the course material found, word for word, behind it — or marked as having none. */
function Evidence({ report }) {
  if (!report) {
    return (
      <Empty icon="shield" title="No evidence check for this question">
        It is made for written answers on normal runs. Multiple-choice keys and questions from
        earlier runs do not have one.
      </Empty>
    )
  }
  const material = report.claims.filter((c) => c.kind === 'material')
  const scenario = report.claims.filter((c) => c.kind === 'scenario')
  const missing = report.material_total - report.material_verified
  return (
    <>
      <div className="row">
        {missing === 0
          ? <Badge tone="ok" dot>{report.material_total === 0 ? 'no general statements to check' : `all ${report.material_total} statements found in the material`}</Badge>
          : <Badge tone="warn" dot>{missing} of {report.material_total} statements not found in the material</Badge>}
      </div>
      <p className="muted">
        A statement counts as backed only when the quoted sentence was found word for word in a
        passage. This is evidence for you to read, not a verdict: it cannot tell whether the
        sentence truly proves the statement, and it does not check the scenario the question invents.
      </p>
      {material.map((c, i) => (
        <div key={i} className={`check ${c.verified ? 'ok' : 'bad'}`}>
          <Icon name={c.verified ? 'check' : 'x'} size={15} />
          <div>
            <div className="f">{c.text}</div>
            <div className="d">
              {c.verified
                ? <>{pageLabel(c.page_ref)}: “{c.quote}”</>
                : 'No sentence of the material was found that states this. Check it before accepting.'}
            </div>
          </div>
        </div>
      ))}
      {scenario.length > 0 && (
        <p className="muted">
          {plural(scenario.length, 'statement')} only describe the question’s own scenario and cannot be checked against the material.
        </p>
      )}
      {report.checked_by && <p className="muted">Statements listed by <span className="mono">{report.checked_by}</span>; quotes verified by the program.</p>}
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
    {
      value: 'evidence', label: 'Evidence', icon: 'eye',
      badge: q.claim_check ? `${q.claim_check.material_verified}/${q.claim_check.material_total}` : null,
      badgeTone: q.claim_check && q.claim_check.material_verified < q.claim_check.material_total ? 'bad' : '',
    },
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
          {q.novelty?.is_duplicate && <Badge tone="bad" dot>repeats {(SOURCE_COPY[q.novelty.nearest_source] || SOURCE_COPY.pyq).a}</Badge>}
          {q.novelty && !q.novelty.is_duplicate && <Badge tone="brand" dot>new</Badge>}
          <Badge tone="outline">{q.repair_attempts === 0 ? 'first draft' : `after ${q.repair_attempts} repair${q.repair_attempts === 1 ? '' : 's'}`}</Badge>
          {q.review && (
            <Badge tone="brand" dot>
              {q.review.decision === 'edit' ? 'edited' : `${q.review.decision}ed`} by {q.review.reviewer.name}
            </Badge>
          )}
          {q.state === 'published' && <Badge tone="ok" dot>published</Badge>}
        </div>

        <div className="cand-meta">
          <span><b>Outcome</b> {c.course_outcome}</span>
          <span><b>Concept</b> {c.concept}</span>
          <span className="cap"><b>Level</b> {c.bloom_level}</span>
          <span><b>Marks</b> {c.marks}</span>
          <span className="cap"><b>Type</b> {c.question_type.replace('_', ' ')}</span>
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
            {tab === 'evidence' && <div className="stack"><Evidence report={q.claim_check} /></div>}
            {tab === 'drafts' && <Drafts questionId={q.question_id} />}
          </div>
        </div>
      </div>

      {!done && <DecisionBar q={q} canReview={canReview} onReviewed={onReviewed} shortcuts={shortcuts} />}
    </article>
  )
}
