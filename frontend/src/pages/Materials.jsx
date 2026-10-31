import { Fragment, useEffect, useState } from 'react'
import { api } from '../api'
import Icon from '../components/Icon'
import { Badge, Btn, Card, Empty, Explainer, Field, Note, PageHead, Segmented, Stat, Table, useToast } from '../components/ui'
import { pageLabel } from '../format'

/* ─── course material ────────────────────────────────────────────────────────────────────── */

function CourseMaterial({ course, canUpload }) {
  const [materials, setMaterials] = useState([])
  const [preview, setPreview] = useState({})
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()

  const load = () => api(`/courses/${course.course_id}/materials`).then(setMaterials)
  useEffect(() => { load() }, [course.course_id]) // eslint-disable-line react-hooks/exhaustive-deps

  const upload = async (e) => {
    e.preventDefault()
    if (!file) return
    setBusy(true); setError('')
    const form = new FormData()
    form.append('file', file)
    try {
      const m = await api(`/courses/${course.course_id}/materials`, { method: 'POST', form })
      setFile(null)
      e.target.reset()
      await load()
      toast('ok', `Indexed ${m.filename}`, `${m.chunk_count} passages across ${m.pages_covered}.`)
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  const togglePreview = async (id) => {
    if (preview[id]) return setPreview({ ...preview, [id]: null })
    setPreview({ ...preview, [id]: await api(`/materials/${id}/chunks?limit=5`) })
  }

  const passages = materials.reduce((n, m) => n + m.chunk_count, 0)

  return (
    <Card
      step="A" title="Course material"
      hint="Lecture notes, slides or a textbook chapter — what questions are written from. Each file is split into short overlapping passages and indexed, so every generated question can be traced to the page it came from."
    >
      {materials.length > 0 && (
        <div className="stats">
          <Stat label="Files indexed" value={materials.length} />
          <Stat label="Passages" value={passages} hint="5-sentence windows, stride 3" />
          <Stat label="Embedding" value="384-d" hint="MiniLM via ONNX Runtime" />
        </div>
      )}

      {canUpload && (
        <form className="row" onSubmit={upload}>
          <Field grow label="Add a PDF or Word file">
            <input type="file" accept=".pdf,.docx" onChange={(e) => setFile(e.target.files[0] || null)} />
          </Field>
          <Btn variant="primary" icon="upload" busy={busy} disabled={!file}>
            {busy ? 'Indexing…' : 'Upload & index'}
          </Btn>
        </form>
      )}
      {error && <Note tone="bad">{error}</Note>}

      {materials.length === 0 ? (
        <Empty icon="file" title="No course material yet">
          Questions are written from this material and cite it, so nothing can be generated until at
          least one file is indexed.
        </Empty>
      ) : (
        <Table head={['File', 'Uploaded', { label: 'Passages', num: true }, 'Pages covered', { label: '', width: 150 }]}>
          {materials.map((m) => (
            <Fragment key={m.material_id}>
              <tr className={preview[m.material_id] ? 'expanded' : undefined}>
                <td>
                  <div className="row tight">
                    <Icon name="file" size={15} className="muted" />
                    <strong style={{ fontWeight: 550 }}>{m.filename}</strong>
                    <Badge tone="outline">{m.source_type}</Badge>
                  </div>
                </td>
                <td className="muted">{new Date(m.uploaded_at).toLocaleString()}</td>
                <td className="num">{m.chunk_count}</td>
                <td className="muted">{m.pages_covered}</td>
                <td>
                  <Btn variant="link" onClick={() => togglePreview(m.material_id)}>
                    {preview[m.material_id] ? 'Hide passages' : 'Sample passages'}
                  </Btn>
                </td>
              </tr>
              {preview[m.material_id] && (
                <tr className="panel-row">
                  <td colSpan={5}>
                    <div className="stack">
                      {preview[m.material_id].map((c) => (
                        <div key={c.chunk_id} className="passage">
                          <div className="meta"><strong>{pageLabel(c.page_ref)}</strong></div>
                          {c.text}
                        </div>
                      ))}
                    </div>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </Table>
      )}
    </Card>
  )
}

/* ─── past-year papers ───────────────────────────────────────────────────────────────────── */

const PASTE_HINT = `One question per block, separated by a blank line.

1. Define a data store. [2]

2. Explain balancing in a DFD. [5]`

function PastPapers({ course, canUpload }) {
  const [papers, setPapers] = useState([])
  const [questions, setQuestions] = useState({})
  const [form, setForm] = useState({ year: '', label: '', text: '' })
  const [file, setFile] = useState(null)
  const [mode, setMode] = useState('file')
  const [busy, setBusy] = useState(false)
  const [confirming, setConfirming] = useState(null)
  const [error, setError] = useState('')
  const toast = useToast()

  const load = () => api(`/courses/${course.course_id}/pyq-papers`).then(setPapers)
  useEffect(() => { load() }, [course.course_id]) // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      let paper
      if (mode === 'file') {
        if (!file) return
        const data = new FormData()
        data.append('file', file)
        data.append('year', form.year)
        data.append('label', form.label || `Paper ${form.year}`)
        paper = await api(`/courses/${course.course_id}/pyq-papers`, { method: 'POST', form: data })
        setFile(null)
      } else {
        paper = await api(`/courses/${course.course_id}/pyq-papers/text`, {
          method: 'POST',
          body: { year: Number(form.year), label: form.label || `Paper ${form.year}`, text: form.text },
        })
        setForm({ ...form, text: '' })
      }
      await load()
      toast('ok', `Added ${paper.label}`, `${paper.question_count} questions extracted — check the extraction before relying on it.`)
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  const toggle = async (id) => {
    if (questions[id]) return setQuestions({ ...questions, [id]: null })
    setQuestions({ ...questions, [id]: await api(`/pyq-papers/${id}/questions`) })
  }

  const remove = async (id) => {
    const paper = papers.find((p) => p.paper_id === id)
    try {
      await api(`/pyq-papers/${id}`, { method: 'DELETE' })
      setConfirming(null)
      await load()
      toast('info', `Removed ${paper?.label || 'paper'}`, 'Its questions are no longer part of the novelty corpus.')
    } catch (err) { setError(err.message) }
  }

  const validYear = /^\d{4}$/.test(String(form.year))
  const corpus = papers.reduce((n, p) => n + p.question_count, 0)
  const years = new Set(papers.map((p) => p.year))

  return (
    <Card
      step="B" title="Past-year papers"
      hint="What generated questions are checked against. Each paper is split into individual questions, with marks, type and Bloom level read off. A question that repeats one of these is caught before a reviewer sees it."
      actions={years.size >= 2
        ? <Badge tone="ok" dot>{years.size} years of coverage</Badge>
        : <Badge tone="warn" dot>{years.size === 0 ? 'no papers' : '1 year — two or more needed'}</Badge>}
    >
      {papers.length > 0 && (
        <div className="stats">
          <Stat label="Papers" value={papers.length} />
          <Stat label="Questions in corpus" value={corpus} hint="every candidate is scored against all of them" />
          <Stat
            label="Toward 300 pairs" value={corpus < 75 ? 'short' : 'ready'} tone={corpus < 75 ? 'bad' : 'ok'}
            hint="roughly 4 labelled pairs per question"
          />
        </div>
      )}

      {canUpload && (
        <form onSubmit={submit} className="stack">
          <div className="row top">
            <Field label="Year" hint="as printed on the paper">
              <input
                type="number" required placeholder="2024" value={form.year}
                onChange={(e) => setForm({ ...form, year: e.target.value })}
              />
            </Field>
            <Field grow label="Label">
              <input type="text" placeholder="End Semester 2024" value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} />
            </Field>
            <Field label="Source">
              <Segmented
                value={mode} onChange={setMode}
                options={[{ value: 'file', label: 'Upload file' }, { value: 'text', label: 'Paste text' }]}
              />
            </Field>
          </div>
          {mode === 'file' ? (
            <Field hint="If the splitter misreads the layout, paste the questions instead — the extraction is always shown back for checking.">
              <input type="file" accept=".pdf,.docx" onChange={(e) => setFile(e.target.files[0] || null)} />
            </Field>
          ) : (
            <textarea rows={6} placeholder={PASTE_HINT} value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })} />
          )}
          <div className="row">
            <Btn variant="primary" icon="plus" busy={busy} disabled={!validYear || (mode === 'file' ? !file : !form.text.trim())}>
              {busy ? 'Extracting questions…' : 'Add paper'}
            </Btn>
          </div>
        </form>
      )}
      {error && <Note tone="bad">{error}</Note>}

      {papers.length === 0 ? (
        <Empty icon="papers" title="No past papers yet">
          The novelty engine has nothing to compare against until at least one paper is added, and
          needs two years or more before its result means anything.
        </Empty>
      ) : (
        <Table head={['Paper', { label: 'Year', num: true }, { label: 'Questions', num: true }, { label: '', width: 220 }]}>
          {papers.map((p) => (
            <Fragment key={p.paper_id}>
              <tr className={questions[p.paper_id] ? 'expanded' : undefined}>
                <td>
                  <div className="row tight">
                    <Icon name="papers" size={15} className="muted" />
                    <strong style={{ fontWeight: 550 }}>{p.label}</strong>
                    <Badge tone="outline">{p.source_type}</Badge>
                  </div>
                </td>
                <td className="num">
                  {p.year}
                  {p.detected_year != null && p.detected_year !== p.year && (
                    <div style={{ marginTop: 4 }}>
                      <Badge tone="warn" dot>paper says {p.detected_year}</Badge>
                    </div>
                  )}
                </td>
                <td className="num">{p.question_count}</td>
                <td>
                  <div className="row tight">
                    <Btn variant="link" onClick={() => toggle(p.paper_id)}>
                      {questions[p.paper_id] ? 'Hide extraction' : 'Check extraction'}
                    </Btn>
                    {canUpload && (confirming === p.paper_id
                      ? <>
                        <Btn variant="bad" size="sm" onClick={() => remove(p.paper_id)}>Confirm removal</Btn>
                        <Btn variant="ghost" size="sm" onClick={() => setConfirming(null)}>Cancel</Btn>
                      </>
                      : <Btn variant="ghost" size="sm" icon="trash" onClick={() => setConfirming(p.paper_id)} aria-label="Remove paper" />)}
                  </div>
                </td>
              </tr>
              {questions[p.paper_id] && (
                <tr className="panel-row">
                  <td colSpan={4}>
                    <p className="muted" style={{ marginBottom: 'var(--s2)' }}>
                      What the splitter read off this paper. A dash means the field could not be parsed —
                      harmless for text, but it weakens the structural signal.
                    </p>
                    <Table head={[{ label: '#', width: 48 }, 'Question', { label: 'Marks', num: true }, 'Type', 'Bloom']}>
                      {questions[p.paper_id].map((q) => (
                        <tr key={q.pyq_id}>
                          <td className="muted">{q.number || '—'}</td>
                          <td>{q.text}</td>
                          <td className="num">{q.marks ?? '—'}</td>
                          <td className="muted cap">{q.question_type?.replace('_', ' ') || '—'}</td>
                          <td className="muted cap">{q.bloom_level || '—'}</td>
                        </tr>
                      ))}
                    </Table>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </Table>
      )}
    </Card>
  )
}

/* ─── page ───────────────────────────────────────────────────────────────────────────────── */

export default function Materials({ course, user, go }) {
  const canUpload = user.role === 'coordinator'
  return (
    <>
      <PageHead
        eyebrow="Step 1 of 5"
        title="Upload the sources"
        lede="Two kinds of source, with two different jobs: the course material questions are written from, and the past papers they are checked against."
        actions={<Btn variant="primary" icon="arrowRight" onClick={() => go('blueprints')}>Next: blueprint</Btn>}
      />

      <Explainer id="materials">
        <p>
          <strong>Course material</strong> is parsed, stripped of repeated headers and footers, split
          into five-sentence windows and embedded. Retrieval pulls from these windows, and every
          citation a question carries points back to one of them with its page number.
        </p>
        <p>
          <strong>Past-year papers</strong> are split into individual questions. The marks, type and
          Bloom level of each are read off, because the novelty engine's structural signal compares
          those alongside the wording. Papers whose layout defeats the splitter can be pasted as
          text instead, and the extraction is shown back for checking either way.
        </p>
      </Explainer>

      {!canUpload && (
        <Note tone="warn" icon="lock">
          Only the course coordinator can add sources. Everything below is visible to you read-only.
        </Note>
      )}

      <CourseMaterial course={course} canUpload={canUpload} />
      <PastPapers course={course} canUpload={canUpload} />
    </>
  )
}
