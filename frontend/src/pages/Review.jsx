import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import Candidate from '../components/Candidate'
import Icon from '../components/Icon'
import { Badge, Btn, Card, Empty, Explainer, Field, Kbd, Note, PageHead, Progress, Segmented, Stat, useShortcuts, useToast } from '../components/ui'

const FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'pending', label: 'To decide' },
  { value: 'failed', label: 'Failed' },
  { value: 'duplicate', label: 'Dupes' },
]

const matches = (q, filter) => {
  if (filter === 'pending') return !q.review
  if (filter === 'failed') return !q.compliance?.is_compliant
  if (filter === 'duplicate') return !!q.novelty?.is_duplicate
  return true
}

function stateOf(q) {
  if (q.state === 'published') return 'ok'
  if (q.review?.decision === 'reject') return 'bad'
  if (q.review) return 'ok'
  if (!q.compliance?.is_compliant || q.novelty?.is_duplicate) return 'warn'
  return ''
}

const DECISION_LABEL = { accept: 'accepted', edit: 'edited', reject: 'rejected' }

export default function Review({ course, user, runId, go }) {
  const [runs, setRuns] = useState([])
  const [current, setCurrent] = useState(runId)
  const [candidates, setCandidates] = useState([])
  const [summary, setSummary] = useState(null)
  const [filter, setFilter] = useState('all')
  const [selectedId, setSelectedId] = useState(null)
  const [help, setHelp] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()

  useEffect(() => {
    api(`/courses/${course.course_id}/blueprints`).then(async (bps) => {
      const frozen = bps.filter((b) => b.frozen_at)
      const all = await Promise.all(frozen.map((b) =>
        api(`/blueprints/${b.blueprint_id}/runs`).then((rs) => rs.map((r) => ({ ...r, version: b.version })))))
      const completed = all.flat().filter((r) => r.status === 'completed')
        .sort((a, b) => b.started_at.localeCompare(a.started_at))
      setRuns(completed)
      setCurrent((cur) => cur || completed[0]?.run_id || null)
    }).catch((e) => setError(e.message))
  }, [course.course_id])

  const refresh = useCallback(async () => {
    if (!current) return
    const [cs, s] = await Promise.all([api(`/runs/${current}/candidates`), api(`/runs/${current}/reviews`)])
    setCandidates(cs)
    setSummary(s)
    setSelectedId((id) => (cs.some((q) => q.question_id === id) ? id : (cs.find((q) => !q.review) || cs[0])?.question_id ?? null))
  }, [current])

  useEffect(() => { refresh().catch((e) => setError(e.message)) }, [refresh])

  const shown = useMemo(() => candidates.filter((q) => matches(q, filter)), [candidates, filter])
  const selected = candidates.find((q) => q.question_id === selectedId) || shown[0] || null
  const index = shown.findIndex((q) => q.question_id === selected?.question_id)

  const move = (delta) => {
    if (!shown.length) return
    const next = shown[Math.min(shown.length - 1, Math.max(0, (index < 0 ? 0 : index) + delta))]
    if (next) setSelectedId(next.question_id)
  }

  useShortcuts({
    j: () => move(1), arrowdown: () => move(1),
    k: () => move(-1), arrowup: () => move(-1),
    '?': () => setHelp((h) => !h),
  }, !!selected)

  // A decision moves the reviewer on to the next thing needing one, which is how a queue should
  // behave; the decided question stays in the list so it can be revisited.
  const onReviewed = (updated) => {
    setCandidates((xs) => xs.map((q) => (q.question_id === updated.question_id ? updated : q)))
    api(`/runs/${current}/reviews`).then(setSummary).catch(() => {})
    const rest = candidates.filter((q) => !q.review && q.question_id !== updated.question_id)
    if (rest.length) setSelectedId(rest[0].question_id)
  }

  const publishable = candidates.filter((q) => ['accepted', 'edited'].includes(q.state))
  const publish = async () => {
    setBusy(true); setError('')
    try {
      const entries = await api('/candidates/publish', { method: 'POST', body: { question_ids: publishable.map((q) => q.question_id) } })
      await refresh()
      toast('ok', `Published ${entries.length} question${entries.length === 1 ? '' : 's'}`, 'Each carries its blueprint row, run, citations and review decision.')
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const compliant = candidates.filter((q) => q.compliance?.is_compliant).length
  const decided = candidates.filter((q) => q.review).length
  const canReview = user.role === 'reviewer'

  if (runs.length === 0) {
    return (
      <>
        <PageHead eyebrow="Step 4 of 5" title="Review candidates" />
        <Card>
          <Empty icon="review" title="No completed run yet" action={<Btn variant="primary" onClick={() => go('generate')}>Go to step 3</Btn>}>
            Candidates appear here once a generation run finishes. Each one arrives with its audit
            results already attached.
          </Empty>
        </Card>
      </>
    )
  }

  return (
    <>
      <PageHead
        eyebrow="Step 4 of 5"
        title="Review candidates"
        lede="Every candidate arrives with the row it was written for, the result of each check, and the passages it was grounded in."
        actions={
          <>
            <Btn variant="ghost" icon="keyboard" onClick={() => setHelp(!help)} aria-label="Keyboard shortcuts" />
            {canReview && publishable.length > 0 && (
              <Btn variant="primary" icon="bank" busy={busy} onClick={publish}>
                Publish {publishable.length} to the bank
              </Btn>
            )}
          </>
        }
      />

      {help && (
        <Card title="Keyboard shortcuts" actions={<Btn variant="ghost" icon="x" onClick={() => setHelp(false)} aria-label="Close" />}>
          <dl className="shortcut-sheet">
            <dt><Kbd>J</Kbd> / <Kbd>↓</Kbd></dt><dd>Next candidate</dd>
            <dt><Kbd>K</Kbd> / <Kbd>↑</Kbd></dt><dd>Previous candidate</dd>
            <dt><Kbd>A</Kbd></dt><dd>Accept as-is</dd>
            <dt><Kbd>E</Kbd></dt><dd>Edit the wording</dd>
            <dt><Kbd>R</Kbd></dt><dd>Reject, with a reason</dd>
            <dt><Kbd>Esc</Kbd></dt><dd>Cancel an edit or rejection</dd>
            <dt><Kbd>?</Kbd></dt><dd>Show or hide this list</dd>
          </dl>
          <p className="muted">Shortcuts are ignored while the caret is in a text field.</p>
        </Card>
      )}

      <Explainer id="review">
        <p>
          Accept a question as it stands, edit its wording, or reject it with a reason. Every decision
          is stored with the reviewer, the time, and — for an edit — the normalised distance between
          the original and your version.
        </p>
        <p>
          Those decisions are the ranker's training data, so a rejection reason is worth writing
          properly. Accepted and edited questions can then be published to the bank, where each one
          keeps its full provenance.
        </p>
      </Explainer>

      <Card pad={false}>
        <div className="card-body tight">
          <div className="row">
            <Field inline label="Run">
              <select value={current || ''} onChange={(e) => setCurrent(e.target.value)}>
                {runs.map((r) => (
                  <option key={r.run_id} value={r.run_id}>
                    {new Date(r.started_at).toLocaleString()} · blueprint v{r.version} · {r.candidate_count} questions
                  </option>
                ))}
              </select>
            </Field>
            <div style={{ flex: 1, minWidth: 140 }}>
              <Progress value={decided} max={candidates.length} tone={decided === candidates.length ? 'ok' : ''} />
            </div>
            <span className="muted">{decided} of {candidates.length} decided</span>
          </div>
        </div>
        {summary && (
          <div className="card-body" style={{ borderTop: '1px solid var(--line)' }}>
            <div className="stats">
              <Stat label="Passed all checks" value={compliant} tone={compliant === candidates.length ? 'ok' : ''} hint={`of ${candidates.length} candidates`} />
              <Stat label="Accepted" value={summary.accepted} />
              <Stat label="Edited" value={summary.edited} />
              <Stat label="Rejected" value={summary.rejected} />
              <Stat label="Awaiting decision" value={summary.pending} tone={summary.pending ? 'brand' : ''} />
              <Stat
                label="Mean edit distance"
                value={summary.mean_edit_distance == null ? '—' : summary.mean_edit_distance.toFixed(2)}
                hint="0 means untouched"
              />
            </div>
          </div>
        )}
      </Card>

      {!canReview && (
        <Note tone="warn" icon="lock">
          You are viewing as coordinator. Only reviewers can record decisions.
        </Note>
      )}
      {error && <Note tone="bad">{error}</Note>}

      <div className="workbench">
        <div className="card queue">
          <div className="queue-head">
            <Segmented
              value={filter} onChange={setFilter}
              options={FILTERS.map((f) => ({ ...f, count: candidates.filter((q) => matches(q, f.value)).length }))}
            />
          </div>
          <div className="queue-list">
            {shown.length === 0 && <p className="muted" style={{ padding: 'var(--s3)' }}>Nothing matches this filter.</p>}
            {shown.map((q) => (
              <button
                key={q.question_id} className="queue-item" aria-current={q.question_id === selected?.question_id}
                onClick={() => setSelectedId(q.question_id)}
              >
                <span className={`q-state ${stateOf(q)}`} />
                <span className="q-body">
                  <span className="q-top">
                    <span className="q-n">Q{q.constraint.position + 1}</span>
                    {q.review && <Badge tone={q.review.decision === 'reject' ? 'bad' : 'ok'}>{DECISION_LABEL[q.review.decision]}</Badge>}
                    {!q.compliance?.is_compliant && <Icon name="alert" size={13} className="error" title="failed a check" />}
                    {q.novelty?.is_duplicate && <Icon name="target" size={13} className="error" title="duplicate" />}
                  </span>
                  <span className="q-stem">{q.review?.edited_stem || q.stem}</span>
                  <span className="q-meta">{q.constraint.concept} · {q.constraint.marks} mark{q.constraint.marks === 1 ? '' : 's'}</span>
                </span>
              </button>
            ))}
          </div>
        </div>

        <div style={{ minWidth: 0 }}>
          {selected ? (
            <>
              <div className="row tight" style={{ marginBottom: 'var(--s3)' }}>
                <Btn size="sm" variant="ghost" disabled={index <= 0} onClick={() => move(-1)}>Previous <Kbd>K</Kbd></Btn>
                <Btn size="sm" variant="ghost" disabled={index >= shown.length - 1} onClick={() => move(1)}>Next <Kbd>J</Kbd></Btn>
                <span className="muted">{index + 1} of {shown.length}{filter !== 'all' ? ` matching “${FILTERS.find((f) => f.value === filter).label}”` : ''}</span>
              </div>
              <Candidate q={selected} canReview={canReview} onReviewed={onReviewed} shortcuts />
            </>
          ) : (
            <Card><Empty icon="review" title="Nothing selected">Pick a candidate from the queue.</Empty></Card>
          )}
        </div>
      </div>

      {candidates.some((q) => q.state === 'published') && (
        <p style={{ marginTop: 'var(--s4)' }}>
          <Btn variant="primary" icon="arrowRight" onClick={() => go('bank')}>Next: the question bank</Btn>
        </p>
      )}
    </>
  )
}
