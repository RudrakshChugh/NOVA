import { useCallback, useEffect, useState } from 'react'
import { api, download } from '../api'
import { Badge, Btn, Card, Empty, Explainer, Field, Note, PageHead, Stat, Table, useToast } from '../components/ui'

const PARTS = ['', 'a', 'b', 'c', 'd']

/* ─── the institutional furniture ────────────────────────────────────────────────────────── */

function TemplateCard({ course, canEdit }) {
  const [form, setForm] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()

  useEffect(() => {
    api(`/courses/${course.course_id}/paper-template`).then(setForm).catch((e) => setError(e.message))
  }, [course.course_id])

  const save = async () => {
    setBusy(true); setError('')
    try {
      const saved = await api(`/courses/${course.course_id}/paper-template`, {
        method: 'PUT',
        body: {
          institution: form.institution || '', department: form.department || '',
          exam_name: form.exam_name || '', instructions: form.instructions || '',
          duration_minutes: form.duration_minutes ? Number(form.duration_minutes) : null,
        },
      })
      setForm(saved)
      toast('ok', 'Header saved', 'Every paper for this subject prints with it.')
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  if (!form) return null
  const set = (k, v) => setForm({ ...form, [k]: v })

  return (
    <Card
      step="A" title="Paper header"
      hint="The first page of every paper for this subject: who is setting it, what the examination is called, how long it runs, and what candidates are told before question one."
    >
      <div className="row top">
        <Field grow label="Institution">
          <input type="text" value={form.institution || ''} disabled={!canEdit}
                 placeholder="Thapar Institute of Engineering and Technology"
                 onChange={(e) => set('institution', e.target.value)} />
        </Field>
        <Field grow label="Department">
          <input type="text" value={form.department || ''} disabled={!canEdit}
                 placeholder="Department of Computer Science and Engineering"
                 onChange={(e) => set('department', e.target.value)} />
        </Field>
      </div>
      <div className="row top">
        <Field grow label="Examination">
          <input type="text" value={form.exam_name || ''} disabled={!canEdit}
                 placeholder="End Semester Examination"
                 onChange={(e) => set('exam_name', e.target.value)} />
        </Field>
        <Field label="Duration (minutes)">
          <input type="number" min={1} max={600} style={{ width: 120 }} disabled={!canEdit}
                 value={form.duration_minutes || ''}
                 onChange={(e) => set('duration_minutes', e.target.value)} />
        </Field>
      </div>
      <Field label="Instructions to candidates">
        <textarea rows={2} value={form.instructions || ''} disabled={!canEdit}
                  placeholder="Answer all questions. Assume any missing data."
                  onChange={(e) => set('instructions', e.target.value)} />
      </Field>
      {error && <Note tone="bad">{error}</Note>}
      {canEdit && <div className="row"><Btn variant="primary" icon="check" busy={busy} onClick={save}>Save header</Btn></div>}
    </Card>
  )
}

/* ─── page ───────────────────────────────────────────────────────────────────────────────── */

export default function Paper({ course, go }) {
  const [papers, setPapers] = useState([])
  const [blueprints, setBlueprints] = useState([])
  const [selected, setSelected] = useState(null)
  const [items, setItems] = useState([])
  const [check, setCheck] = useState(null)
  const [draft, setDraft] = useState({ title: '', blueprint_id: '', exam_date: '' })
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const toast = useToast()

  const canEdit = course.your_role === 'coordinator'

  const load = useCallback(() => (
    api(`/courses/${course.course_id}/papers`).then((ps) => {
      setPapers(ps)
      setSelected((cur) => ps.find((p) => p.paper_id === cur?.paper_id) || ps[0] || null)
      return ps
    })
  ), [course.course_id])

  useEffect(() => { load().catch((e) => setError(e.message)) }, [load])
  useEffect(() => {
    api(`/courses/${course.course_id}/blueprints`)
      .then((bs) => setBlueprints(bs.filter((b) => b.frozen_at)))
      .catch(() => setBlueprints([]))
  }, [course.course_id])
  useEffect(() => {
    setItems(selected?.items || [])
    setCheck(null)
  }, [selected])

  const run = (key, fn) => async () => {
    setBusy(key); setError('')
    try { await fn() } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const create = run('new', async () => {
    const paper = await api(`/courses/${course.course_id}/papers`, {
      method: 'POST',
      body: {
        title: draft.title.trim() || 'Untitled paper',
        blueprint_id: draft.blueprint_id || null,
        exam_date: draft.exam_date || null,
      },
    })
    setDraft({ title: '', blueprint_id: '', exam_date: '' })
    await load()
    setSelected(paper)
    toast('ok', `Started ${paper.title}`, paper.items.length
      ? `${paper.items.length} published question(s) brought in, in blueprint order.`
      : 'Add published questions to it below.')
  })

  const saveItems = run('save', async () => {
    const paper = await api(`/papers/${selected.paper_id}/items`, {
      method: 'PUT',
      body: items.map((i) => ({ entry_id: i.entry_id, number: Number(i.number),
                                part: i.part || null, marks: Number(i.marks) })),
    })
    await load()
    setSelected(paper)
    setCheck(await api(`/papers/${paper.paper_id}/check`, { method: 'POST' }))
  })

  const finalise = run('final', async () => {
    const paper = await api(`/papers/${selected.paper_id}/finalise`, { method: 'POST' })
    await load()
    setSelected(paper)
    toast('ok', `${paper.title} is final`, 'It prints from a frozen snapshot from now on.')
  })

  const discard = run('discard', async () => {
    await api(`/papers/${selected.paper_id}`, { method: 'DELETE' })
    setSelected(null)
    await load()
  })

  const getPdf = (suffix, label) => run(suffix, async () => {
    await download(`/papers/${selected.paper_id}/${suffix}`,
      `${selected.title.replace(/\s+/g, '-').toLowerCase()}${suffix === 'answer-key' ? '-answer-key' : ''}.pdf`)
    toast('ok', `${label} downloaded`, 'Rendered on the server, so it looks the same everywhere.')
  })

  const setItem = (i, k, v) => setItems(items.map((it, j) => (j === i ? { ...it, [k]: v } : it)))
  const move = (i, by) => {
    const next = [...items]
    const target = i + by
    if (target < 0 || target >= next.length) return
    ;[next[i], next[target]] = [next[target], next[i]]
    setItems(next)
  }
  const total = items.reduce((n, i) => n + (Number(i.marks) || 0), 0)
  const frozen = selected?.state === 'final'
  const dirty = JSON.stringify(items) !== JSON.stringify(selected?.items || [])

  return (
    <>
      <PageHead
        eyebrow="Step 6 of 6"
        title="Assemble the paper"
        lede="Published questions, numbered and bound into something a candidate can be handed."
      />

      <Explainer id="paper">
        <p>
          Only <strong>published</strong> questions can go on a paper — a question nobody accepted
          has no business on one. Naming a frozen blueprint brings its published questions in
          already in order, which is the usual case.
        </p>
        <p>
          The paper is checked against that blueprint before it can be finalised. Catching a marks
          error on every question and then losing it at the last step would be absurd, so a paper
          that does not add up to its blueprint is refused here rather than discovered in the exam
          hall.
        </p>
        <p>
          Finalising freezes a snapshot, for the same reason a bank entry carries one: a paper that
          was printed has to keep printing the same thing after its questions are revised. The
          answer key is a separate download, and only a coordinator can take it.
        </p>
      </Explainer>

      <TemplateCard course={course} canEdit={canEdit} />

      <Card step="B" title="Papers"
            hint="One per examination. A draft can be rearranged freely; a final one is frozen."
            actions={papers.length > 0 && <Badge tone="brand" dot>{papers.length}</Badge>}>
        {canEdit && (
          <div className="row top">
            <Field grow label="Title"><input type="text" value={draft.title} placeholder="End Semester 2026"
                                             onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></Field>
            <Field label="From blueprint">
              <select value={draft.blueprint_id} onChange={(e) => setDraft({ ...draft, blueprint_id: e.target.value })}>
                <option value="">Start empty</option>
                {blueprints.map((b) => <option key={b.blueprint_id} value={b.blueprint_id}>Version {b.version}</option>)}
              </select>
            </Field>
            <Field label="Date"><input type="date" value={draft.exam_date}
                                       onChange={(e) => setDraft({ ...draft, exam_date: e.target.value })} /></Field>
            <div style={{ alignSelf: 'flex-end' }}>
              <Btn variant="primary" icon="plus" busy={busy === 'new'} disabled={!draft.title.trim()} onClick={create}>
                Start a paper
              </Btn>
            </div>
          </div>
        )}

        {papers.length === 0 ? (
          <Empty icon="file" title="No paper yet" action={<Btn variant="primary" onClick={() => go('bank')}>See the question bank</Btn>}>
            A paper is assembled from published questions, so publish some first.
          </Empty>
        ) : (
          <div className="row tight">
            {papers.map((p) => (
              <Btn key={p.paper_id} variant={selected?.paper_id === p.paper_id ? 'primary' : ''}
                   icon={p.state === 'final' ? 'lock' : 'file'} onClick={() => setSelected(p)}>
                {p.title}
                <span style={{ opacity: .7 }}> · {p.total_marks} marks</span>
              </Btn>
            ))}
          </div>
        )}
      </Card>

      {error && <Note tone="bad">{error}</Note>}

      {selected && (
        <Card
          step="C" title={selected.title}
          hint={frozen
            ? `Finalised ${new Date(selected.finalised_at).toLocaleString()} — it prints from a frozen snapshot.`
            : 'Set the number, sub-part and marks of each question, then check it against the blueprint.'}
          actions={<Badge tone={frozen ? 'ok' : 'warn'} dot>{frozen ? 'final' : 'draft'}</Badge>}
          pad={false}
        >
          <div className="card-body">
            <div className="stats">
              <Stat label="Questions" value={items.length} />
              <Stat label="Marks on the paper" value={total}
                    tone={check && check.blueprint_marks != null && total !== check.blueprint_marks ? 'bad' : ''} />
              <Stat label="Blueprint total" value={check?.blueprint_marks ?? '—'} hint="what it should add up to" />
            </div>
          </div>

          {items.length === 0 ? (
            <Empty icon="file" title="Nothing on this paper yet">
              Start it from a frozen blueprint to bring its published questions in.
            </Empty>
          ) : (
            <Table head={[
              { label: 'Q', width: 70 }, { label: 'Part', width: 80 }, 'Question',
              { label: 'Marks', width: 90 }, 'CO', 'BL', ...(canEdit && !frozen ? [{ label: '', width: 90 }] : []),
            ]}>
              {items.map((item, i) => (
                <tr key={item.item_id || item.entry_id}>
                  <td>{canEdit && !frozen
                    ? <input type="number" min={1} style={{ width: 56 }} value={item.number}
                             onChange={(e) => setItem(i, 'number', e.target.value)} />
                    : item.number}</td>
                  <td>{canEdit && !frozen
                    ? <select value={item.part || ''} onChange={(e) => setItem(i, 'part', e.target.value)}>
                        {PARTS.map((p) => <option key={p} value={p}>{p || '—'}</option>)}
                      </select>
                    : item.part || '—'}</td>
                  <td className="prose">{item.stem}</td>
                  <td>{canEdit && !frozen
                    ? <input type="number" min={1} style={{ width: 70 }} value={item.marks}
                             onChange={(e) => setItem(i, 'marks', e.target.value)} />
                    : item.marks}</td>
                  <td className="muted">{(item.course_outcome || '').split(' ')[0]}</td>
                  <td className="muted cap">{item.bloom_level}</td>
                  {canEdit && !frozen && (
                    <td>
                      <div className="row tight">
                        <Btn variant="ghost" size="sm" aria-label="Move up" onClick={() => move(i, -1)}>↑</Btn>
                        <Btn variant="ghost" size="sm" aria-label="Move down" onClick={() => move(i, 1)}>↓</Btn>
                        <Btn variant="ghost" size="sm" icon="trash" aria-label="Remove"
                             onClick={() => setItems(items.filter((_, j) => j !== i))} />
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </Table>
          )}

          <div className="card-body">
            {check && !check.ok && (
              <Note tone="bad">
                <strong>Fix before finalising</strong>
                <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                  {check.issues.map((s, i) => <li key={i}>{s}</li>)}
                </ul>
              </Note>
            )}
            {check?.ok && <Note tone="ok">The paper adds up: {check.total_marks} marks, matching the blueprint.</Note>}

            <div className="row">
              {canEdit && !frozen && (
                <>
                  <Btn variant="primary" icon="check" busy={busy === 'save'} disabled={!dirty} onClick={saveItems}>
                    {dirty ? 'Save and check' : 'Saved'}
                  </Btn>
                  <Btn icon="lock" busy={busy === 'final'} disabled={!items.length} onClick={finalise}>
                    Finalise
                  </Btn>
                </>
              )}
              <Btn icon="download" busy={busy === 'pdf'} onClick={getPdf('pdf', 'Question paper')}>
                Download the paper
              </Btn>
              {canEdit && (
                <Btn icon="download" busy={busy === 'answer-key'} onClick={getPdf('answer-key', 'Answer key')}>
                  Download the answer key
                </Btn>
              )}
              {canEdit && !frozen && (
                <Btn variant="bad" icon="trash" busy={busy === 'discard'} onClick={discard}>Discard</Btn>
              )}
            </div>
          </div>
        </Card>
      )}
    </>
  )
}
