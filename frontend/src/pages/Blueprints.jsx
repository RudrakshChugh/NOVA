import { Fragment, useEffect, useState } from 'react'
import { api } from '../api'

import { Badge, Btn, Card, Empty, Explainer, Note, PageHead, Table, useToast } from '../components/ui'

const BLOOM = ['remember', 'understand', 'apply', 'analyze', 'evaluate', 'create']
const TYPES = ['mcq', 'short_answer', 'long_answer', 'numerical']
const EMPTY = { course_outcome: '', outcome_id: '', concept: '', bloom_level: 'understand', marks: 2, question_type: 'short_answer' }

const FIELD_HELP = {
  'Course outcome': 'Chosen from the outcomes read off this subject\u2019s syllabus, so one outcome is not spelt three ways across a paper. Its content words must appear in the question or the answer.',
  Concept: 'The specific topic the question must be about. This is also the retrieval query, so it decides which passages the model gets.',
  'Bloom level': 'Cognitive level: remember → understand → apply → analyze → evaluate → create. The stem must open with a cue verb at this level.',
  Marks: 'Mark weighting. The answer key has to be proportionate — short for 1–2 marks, substantial for 6–10.',
  Type: 'mcq needs exactly four options with the key among them; numerical needs numbers in both stem and answer; short and long answer need neither.',
}

export default function Blueprints({ course, user, go }) {
  const [blueprints, setBlueprints] = useState([])
  const [selected, setSelected] = useState(null)
  const [rows, setRows] = useState([])
  const [validation, setValidation] = useState(null)
  const [outcomes, setOutcomes] = useState([])
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
  // The vocabulary a row points at. Empty for a subject whose syllabus has not been read yet,
  // and the outcome cell falls back to free text so such a subject is not blocked.
  useEffect(() => { api(`/courses/${course.course_id}/outcomes`).then(setOutcomes).catch(() => setOutcomes([])) }, [course.course_id])
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
    // An unchosen dropdown is an empty string in the DOM and a null on the wire.
    const body = rows.map((r) => ({ ...r, outcome_id: r.outcome_id || null }))
    const bp = await api(`/blueprints/${selected.blueprint_id}/constraints`, { method: 'PUT', body })
    await load(); select(bp)
    const v = await api(`/blueprints/${bp.blueprint_id}/validate`, { method: 'POST' })
    setValidation(v)
    if (v.ok && v.warnings?.length) toast('info', 'Saved — valid, with advisories', `${v.warnings.length} row(s) need a look. You can still freeze.`)
    else if (v.ok) toast('ok', 'Saved and valid', `${v.total_marks} marks across ${Object.keys(v.outcomes).length} outcome(s). Freeze it to lock the version.`)
    else toast('bad', `${v.issues.length} validation issue(s)`, 'Fix them before freezing.')
  })

  // A frozen version is copied rather than edited: runs and compliance reports point at its
  // rows by id, so changing them in place would rewrite what those reports mean.
  const revise = run('revise', async () => {
    const bp = await api(`/blueprints/${selected.blueprint_id}/revise`, { method: 'POST' })
    await load(); select(bp)
    toast('ok', `Draft copied from version ${selected.version}`,
      'Edit it and freeze it as the next version. Version ' + selected.version + ' is untouched.')
  })

  const freeze = run('freeze', async () => {
    const bp = await api(`/blueprints/${selected.blueprint_id}/freeze`, { method: 'POST' })
    await load(); select(bp)
    toast('ok', `Frozen as version ${bp.version}`, 'It can no longer change, and every question records which version it came from.')
  })

  const setRow = (i, k, v) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)))
  // Choosing an outcome sets the wording too. The server does the same thing on save, but a
  // row whose outcome is chosen and whose text is still empty would not survive validation on
  // the way there, and the reader of the table should see what they picked.
  const setOutcome = (i, outcomeId) => {
    const picked = outcomes.find((o) => o.outcome_id === outcomeId)
    setRows(rows.map((r, j) => (j === i
      ? { ...r, outcome_id: outcomeId, course_outcome: picked ? `${picked.code} ${picked.text}` : '' }
      : r)))
  }
  const frozen = selected?.frozen_at != null
  const editable = course.your_role === 'coordinator' && selected && !frozen
  const totalMarks = rows.reduce((n, r) => n + (Number(r.marks) || 0), 0)
  // A chip shows what is on screen for the blueprint being edited, and what is stored for the
  // rest, so the count beside a draft never contradicts the table under it.
  const isSelected = (bp) => bp.blueprint_id === selected?.blueprint_id
  const countFor = (bp) => (isSelected(bp) ? rows.length : bp.constraints.length)
  const isDirty = (bp) => isSelected(bp) && !frozen
    && JSON.stringify(rows) !== JSON.stringify(bp.constraints.map(({ constraint_id: _i, position: _p, ...r }) => r))

  return (
    <>
      <PageHead
        eyebrow="Step 2 of 6"
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
        actions={course.your_role === 'coordinator' && <Btn icon="plus" busy={busy === 'new'} onClick={createDraft}>New draft</Btn>}
      >
        {blueprints.length === 0 ? (
          <Empty icon="blueprint" title="No blueprint yet">
            {course.your_role === 'coordinator'
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
                  · {countFor(bp)} row{countFor(bp) === 1 ? '' : 's'}
                  {isDirty(bp) && ' · unsaved'}
                  {bp.revised_from_version != null && ` · from v${bp.revised_from_version}`}
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
              { label: 'Marks', width: 90 }, 'Type', ...(editable ? [{ label: '', width: 48 }] : []),
            ]}>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td className="muted">{i + 1}</td>
                  <td>{!editable
                    ? r.course_outcome
                    : outcomes.length > 0
                      ? <select value={r.outcome_id || ''} onChange={(e) => setOutcome(i, e.target.value)}>
                          <option value="">Choose an outcome…</option>
                          {outcomes.map((o) => (
                            <option key={o.outcome_id} value={o.outcome_id}>{o.code} — {o.text}</option>
                          ))}
                        </select>
                      : <input type="text" style={{ width: '100%' }} value={r.course_outcome} placeholder="CO1 Explain …" onChange={(e) => setRow(i, 'course_outcome', e.target.value)} />}</td>
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
              {validation.uncovered?.length > 0 && (
                <Note tone="warn">
                  <strong>No marks allocated to {validation.uncovered.length} outcome{validation.uncovered.length === 1 ? '' : 's'}</strong>
                  {' '}— a gap rather than an error, but worth seeing before you freeze.
                  <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                    {validation.uncovered.map((s, i) => <li key={i}>{s}</li>)}
                  </ul>
                </Note>
              )}
              {validation.warnings?.length > 0 && (
                <Note tone="warn">
                  <strong>{validation.warnings.length} thing{validation.warnings.length === 1 ? '' : 's'} to check</strong>
                  {' '}— advisory, you can still freeze.
                  <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                    {validation.warnings.map((s, i) => <li key={i}>{s}</li>)}
                  </ul>
                </Note>
              )}
              <div>
                <h4 style={{ marginBottom: 'var(--s3)' }}>Marks by course outcome</h4>
                <div className="coverage">
                  {Object.entries(validation.outcomes).map(([o, m]) => {
                    const [code, ...rest] = o.split(' ')
                    return (
                      <div key={o} className="cov-row" title={o}>
                        <span className="code">{code}</span>
                        <span className="progress"><i style={{ width: `${validation.total_marks ? (m / validation.total_marks) * 100 : 0}%` }} /></span>
                        <span className="val">{m} mark{m === 1 ? '' : 's'}</span>
                        <span className="name">{rest.join(' ')}</span>
                      </div>
                    )
                  })}
                  {validation.uncovered?.map((o) => {
                    const [code, ...rest] = o.split(' ')
                    return (
                      <div key={o} className="cov-row none" title={o}>
                        <span className="code">{code}</span>
                        <span className="progress"><i style={{ width: 0 }} /></span>
                        <span className="val">none</span>
                        <span className="name">{rest.join(' ')}</span>
                      </div>
                    )
                  })}
                </div>
              </div>
            </>
          )}

          {error && <Note tone="bad">{error}</Note>}

          {frozen && (
            <Note tone="ok" icon="lock">
              Version {selected.version} is locked and ready. Generation runs from this version, and
              every question it produces is audited against the row it was written for.{' '}
              <Btn variant="link" onClick={() => go('generate')}>Generate questions →</Btn>
              {course.your_role === 'coordinator' && (
                <div className="row" style={{ marginTop: 'var(--s3)' }}>
                  <Btn icon="blueprint" busy={busy === 'revise'} onClick={revise}>
                    Revise as a new draft
                  </Btn>
                  <span className="hint">
                    Copies these rows into a draft you can edit. This version, and everything
                    generated from it, stays exactly as it is.
                  </span>
                </div>
              )}
            </Note>
          )}
        </Card>
      )}
    </>
  )
}
