import { Fragment, useEffect, useState } from 'react'
import { api } from '../api'

import { Badge, Btn, Card, Empty, Explainer, Note, PageHead, Stat, Table, useToast } from '../components/ui'

const BLOOM = ['remember', 'understand', 'apply', 'analyze', 'evaluate', 'create']
const TYPES = ['mcq', 'short_answer', 'long_answer', 'numerical']
const DIFF = ['easy', 'medium', 'hard']
const EMPTY = { course_outcome: '', concept: '', bloom_level: 'understand', marks: 2, question_type: 'short_answer', target_difficulty: 'easy' }

const FIELD_HELP = {
  'Course outcome': 'The learning outcome this question assesses, e.g. "CO1 Explain DFD notation". Its content words must appear in the question or the answer.',
  Concept: 'The specific topic the question must be about. This is also the retrieval query, so it decides which passages the model gets.',
  'Bloom level': 'Cognitive level: remember → understand → apply → analyze → evaluate → create. The stem must open with a cue verb at this level.',
  Marks: 'Mark weighting. The answer key has to be proportionate — short for 1–2 marks, substantial for 6–10.',
  Type: 'mcq needs exactly four options with the key among them; numerical needs numbers in both stem and answer; short and long answer need neither.',
  Difficulty: 'Easy pairs with remember and understand, medium with apply and analyze, hard with evaluate and create. A mismatch is flagged at validation.',
}

export default function Blueprints({ course, user, go }) {
  const [blueprints, setBlueprints] = useState([])
  const [selected, setSelected] = useState(null)
  const [rows, setRows] = useState([])
  const [validation, setValidation] = useState(null)
  const [busy, setBusy] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()

  const select = (bp) => {
    setSelected(bp)
    setRows(bp.constraints.map(({ constraint_id: _id, position: _pos, ...r }) => r))
    setValidation(null); setError(''); setConfirming(false)
  }
  const load = () => api(`/courses/${course.course_id}/blueprints`).then((bps) => { setBlueprints(bps); return bps })
  useEffect(() => { load().then((bps) => { if (bps.length) select(bps[bps.length - 1]) }) }, [course.course_id]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = (name, fn) => async () => {
    setBusy(name); setError('')
    try { await fn() } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const discard = () => run('discard', async () => {
    await api(`/blueprints/${selected.blueprint_id}`, { method: 'DELETE' })
    const left = await load()
    const drafts = left.filter((b) => !b.frozen_at)
    if (left.length) select(drafts[drafts.length - 1] || left[left.length - 1])
    else { setSelected(null); setRows([]) }
    toast('info', 'Draft discarded', 'Only drafts can be removed; a frozen version is permanent.')
  })()

  const createDraft = run('new', async () => {
    const bp = await api(`/courses/${course.course_id}/blueprints`, { method: 'POST' })
    await load(); select(bp)
  })

  const save = run('save', async () => {
    const bp = await api(`/blueprints/${selected.blueprint_id}/constraints`, { method: 'PUT', body: rows })
    await load(); select(bp)
    const v = await api(`/blueprints/${bp.blueprint_id}/validate`, { method: 'POST' })
    setValidation(v)
    if (v.ok) toast('ok', 'Saved and valid', `${v.total_marks} marks across ${Object.keys(v.outcomes).length} outcome(s). Freeze it to lock the version.`)
    else toast('bad', `${v.issues.length} validation issue(s)`, 'Fix them before freezing.')
  })

  const freeze = run('freeze', async () => {
    const bp = await api(`/blueprints/${selected.blueprint_id}/freeze`, { method: 'POST' })
    await load(); select(bp)
    toast('ok', `Frozen as version ${bp.version}`, 'It can no longer change, and every question records which version it came from.')
  })

  const setRow = (i, k, v) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)))
  const frozen = selected?.frozen_at != null
  const editable = user.role === 'coordinator' && selected && !frozen
  const totalMarks = rows.reduce((n, r) => n + (Number(r.marks) || 0), 0)

  return (
    <>
      <PageHead
        eyebrow="Step 2 of 5"
        title="Define the blueprint"
        lede="The exam specification. One row per question the paper needs — and the contract every generated question is audited against."
        actions={frozen
          ? <Btn variant="primary" icon="arrowRight" onClick={() => go('generate')}>Next: generate</Btn>
          : <Btn icon="arrowRight" onClick={() => go('generate')}>Skip to generate</Btn>}
      />

      <Explainer id="blueprints">
        <p>
          Each row states which course outcome a question assesses, which concept it covers, at what
          Bloom level, for how many marks and of what type. Freezing a blueprint gives it a version
          number and locks it: generated questions are checked against their row, and every published
          question records the version it came from.
        </p>
        <dl className="legend">
          {Object.entries(FIELD_HELP).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}
        </dl>
      </Explainer>

      <Card
        title="Blueprints for this course"
        hint="A draft can be edited freely. Once frozen it becomes an immutable version."
        actions={user.role === 'coordinator' && <Btn icon="plus" busy={busy === 'new'} onClick={createDraft}>New draft</Btn>}
      >
        {blueprints.length === 0 ? (
          <Empty icon="blueprint" title="No blueprint yet">
            {user.role === 'coordinator'
              ? 'Create a draft, add one row per question the paper needs, then freeze it as a version.'
              : 'The course coordinator creates these.'}
          </Empty>
        ) : (
          <div className="row tight">
            {blueprints.map((bp) => (
              <Btn
                key={bp.blueprint_id} variant={selected?.blueprint_id === bp.blueprint_id ? 'primary' : ''}
                icon={bp.frozen_at ? 'lock' : 'blueprint'} onClick={() => select(bp)}
              >
                {bp.frozen_at ? `Version ${bp.version}` : 'Draft'}
                <span style={{ opacity: .7 }}>
                  · {bp.constraints.length} row{bp.constraints.length === 1 ? '' : 's'}
                  {!bp.frozen_at && bp.created_at && ` · started ${new Date(bp.created_at).toLocaleDateString()}`}
                </span>
              </Btn>
            ))}
          </div>
        )}
      </Card>

      {selected && (
        <Card
          title={frozen ? `Version ${selected.version}` : 'Draft blueprint'}
          hint={frozen
            ? `Frozen ${new Date(selected.frozen_at).toLocaleString()} — locked against further change.`
            : 'Not yet versioned, so still editable. Nothing can be generated from a draft.'}
          actions={frozen
            ? <Badge tone="ok" dot>frozen</Badge>
            : <Badge tone="neutral" dot>editable</Badge>}
          foot={editable && (
            <>
              <Btn icon="plus" onClick={() => setRows([...rows, { ...EMPTY }])}>Add question row</Btn>
              <Btn
                variant="primary" icon="check" busy={busy === 'save'} disabled={!rows.length}
                title={rows.length ? 'Check the rows, then freeze' : 'Add at least one question row first'}
                onClick={save}
              >
                Save &amp; validate
              </Btn>
              <div className="spacer" style={{ flex: 1 }} />
              {confirming
                ? <>
                  <span className="muted">Discard this draft and its {rows.length} row{rows.length === 1 ? '' : 's'}?</span>
                  <Btn variant="bad" size="sm" busy={busy === 'discard'} onClick={discard}>Yes, discard</Btn>
                  <Btn variant="ghost" size="sm" onClick={() => setConfirming(false)}>Cancel</Btn>
                </>
                : <>
                  <span className="muted">{rows.length} row{rows.length === 1 ? '' : 's'} · {totalMarks} marks</span>
                  <Btn variant="ghost" size="sm" icon="trash" onClick={() => setConfirming(true)}>Discard draft</Btn>
                </>}
              <Btn
                variant="ok" icon="lock" busy={busy === 'freeze'} disabled={!validation?.ok} onClick={freeze}
                title={validation?.ok
                  ? 'Lock this blueprint as a version'
                  : rows.length
                    ? 'Save & validate first, and clear any issues it reports'
                    : 'Add question rows, then save and validate'}
              >
                Freeze as version
              </Btn>
            </>
          )}
        >
          {rows.length === 0 ? (
            <Empty
              icon="blueprint" title="This blueprint has no rows"
              action={editable && (
                <Btn variant="primary" icon="plus" onClick={() => setRows([{ ...EMPTY }])}>
                  Add the first row
                </Btn>
              )}
            >
              {editable
                ? 'One row per question the paper needs: which outcome it assesses, which concept, at what Bloom level, for how many marks.'
                : 'Nothing to show.'}
            </Empty>
          ) : (
            <Table head={[
              { label: '#', width: 42 }, 'Course outcome', 'Concept', 'Bloom level',
              { label: 'Marks', width: 90 }, 'Type', 'Difficulty', ...(editable ? [{ label: '', width: 48 }] : []),
            ]}>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td className="muted">{i + 1}</td>
                  <td>{editable
                    ? <input type="text" style={{ width: '100%' }} value={r.course_outcome} placeholder="CO1 Explain …" onChange={(e) => setRow(i, 'course_outcome', e.target.value)} />
                    : r.course_outcome}</td>
                  <td>{editable
                    ? <input type="text" style={{ width: '100%' }} value={r.concept} placeholder="topic" onChange={(e) => setRow(i, 'concept', e.target.value)} />
                    : r.concept}</td>
                  <td>{editable
                    ? <select value={r.bloom_level} onChange={(e) => setRow(i, 'bloom_level', e.target.value)}>{BLOOM.map((b) => <option key={b}>{b}</option>)}</select>
                    : <span className="cap">{r.bloom_level}</span>}</td>
                  <td>{editable
                    ? <input type="number" min={1} style={{ width: 70 }} value={r.marks} onChange={(e) => setRow(i, 'marks', +e.target.value)} />
                    : r.marks}</td>
                  <td>{editable
                    ? <select value={r.question_type} onChange={(e) => setRow(i, 'question_type', e.target.value)}>{TYPES.map((t) => <option key={t} value={t}>{t.replace('_', ' ')}</option>)}</select>
                    : <span className="cap">{r.question_type.replace('_', ' ')}</span>}</td>
                  <td>{editable
                    ? <select value={r.target_difficulty} onChange={(e) => setRow(i, 'target_difficulty', e.target.value)}>{DIFF.map((d) => <option key={d}>{d}</option>)}</select>
                    : <span className="cap">{r.target_difficulty}</span>}</td>
                  {editable && (
                    <td>
                      <Btn variant="ghost" size="sm" icon="trash" aria-label={`Remove row ${i + 1}`} onClick={() => setRows(rows.filter((_, j) => j !== i))} />
                    </td>
                  )}
                </tr>
              ))}
            </Table>
          )}

          {validation && (
            <>
              <div className="row">
                {validation.ok
                  ? <Badge tone="ok" dot>valid</Badge>
                  : <Badge tone="bad" dot>{validation.issues.length} issue{validation.issues.length === 1 ? '' : 's'}</Badge>}
                <span className="muted">
                  {validation.total_marks} marks across {Object.keys(validation.outcomes).length} outcome(s)
                </span>
              </div>
              {validation.issues.length > 0 && (
                <Note tone="bad">
                  <strong>Fix before freezing</strong>
                  <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                    {validation.issues.map((s, i) => <li key={i}>{s}</li>)}
                  </ul>
                </Note>
              )}
              <div className="stats">
                {Object.entries(validation.outcomes).map(([o, m]) => <Stat key={o} label={o} value={`${m}`} hint="marks" />)}
              </div>
            </>
          )}

          {error && <Note tone="bad">{error}</Note>}

          {frozen && (
            <Note tone="ok" icon="lock">
              Version {selected.version} is locked and ready. Generation runs from this version, and
              every question it produces is audited against the row it was written for.{' '}
              <Btn variant="link" onClick={() => go('generate')}>Generate questions →</Btn>
            </Note>
          )}
        </Card>
      )}
    </>
  )
}
