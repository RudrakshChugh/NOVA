import { Fragment, useEffect, useState } from 'react'
import { api } from '../api'
import { pageLabel } from '../format'

function CourseMaterial({ course, canUpload }) {
  const [materials, setMaterials] = useState([])
  const [preview, setPreview] = useState({})
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = () => api(`/courses/${course.course_id}/materials`).then(setMaterials)
  useEffect(() => { load() }, [course.course_id])

  const upload = async (e) => {
    e.preventDefault()
    if (!file) return
    setBusy(true); setError('')
    const form = new FormData()
    form.append('file', file)
    try {
      await api(`/courses/${course.course_id}/materials`, { method: 'POST', form })
      setFile(null)
      await load()
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  const togglePreview = async (id) => {
    if (preview[id]) return setPreview({ ...preview, [id]: null })
    setPreview({ ...preview, [id]: await api(`/materials/${id}/chunks?limit=5`) })
  }

  return (
    <div className="card">
      <h3>Course material — what questions are written from</h3>
      <p className="muted">
        Lecture notes, slides or a textbook chapter. The file is split into short overlapping
        passages and indexed, so every generated question can be traced to the page it came from.
      </p>
      {canUpload && (
        <form className="row" onSubmit={upload} style={{ margin: '10px 0' }}>
          <input type="file" accept=".pdf,.docx" onChange={(e) => setFile(e.target.files[0])} />
          <button className="primary" disabled={!file || busy}>{busy ? 'Indexing…' : 'Upload & index'}</button>
        </form>
      )}
      {error && <p className="error">{error}</p>}
      {materials.length === 0 ? <p className="muted">Nothing uploaded yet.</p> : (
        <table>
          <thead><tr><th>File</th><th>Uploaded</th><th>Passages</th><th>Pages covered</th><th /></tr></thead>
          <tbody>
            {materials.map((m) => (
              <Fragment key={m.material_id}>
                <tr>
                  <td>{m.filename} <span className="pill neutral">{m.source_type}</span></td>
                  <td>{new Date(m.uploaded_at).toLocaleString()}</td>
                  <td>{m.chunk_count}</td>
                  <td>{m.pages_covered}</td>
                  <td><button className="link" onClick={() => togglePreview(m.material_id)}>{preview[m.material_id] ? 'hide' : 'sample passages'}</button></td>
                </tr>
                {preview[m.material_id] && (
                  <tr><td colSpan={5}>
                    {preview[m.material_id].map((c) => (
                      <div key={c.chunk_id} className="passage"><div className="meta">{pageLabel(c.page_ref)}</div>{c.text}</div>
                    ))}
                  </td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function PastPapers({ course, canUpload }) {
  const [papers, setPapers] = useState([])
  const [questions, setQuestions] = useState({})
  const [form, setForm] = useState({ year: new Date().getFullYear() - 1, label: '', text: '' })
  const [file, setFile] = useState(null)
  const [mode, setMode] = useState('file') // file | text
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = () => api(`/courses/${course.course_id}/pyq-papers`).then(setPapers)
  useEffect(() => { load() }, [course.course_id])

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      if (mode === 'file') {
        if (!file) return
        const data = new FormData()
        data.append('file', file)
        data.append('year', form.year)
        data.append('label', form.label || `Paper ${form.year}`)
        await api(`/courses/${course.course_id}/pyq-papers`, { method: 'POST', form: data })
        setFile(null)
      } else {
        await api(`/courses/${course.course_id}/pyq-papers/text`, {
          method: 'POST',
          body: { year: Number(form.year), label: form.label || `Paper ${form.year}`, text: form.text },
        })
        setForm({ ...form, text: '' })
      }
      await load()
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  const toggle = async (id) => {
    if (questions[id]) return setQuestions({ ...questions, [id]: null })
    setQuestions({ ...questions, [id]: await api(`/pyq-papers/${id}/questions`) })
  }

  const remove = async (id) => {
    await api(`/pyq-papers/${id}`, { method: 'DELETE' })
    await load()
  }

  return (
    <div className="card">
      <h3>Past-year papers — what questions are checked against</h3>
      <p className="muted">
        Each paper is split into individual questions, and the marks, type and Bloom level of each
        are read off. A generated question that repeats one of these is caught before a reviewer
        sees it. At least two years is what plan §5 asks for.
      </p>
      {canUpload && (
        <form onSubmit={submit} style={{ margin: '10px 0' }}>
          <div className="row">
            <label>Year <input type="number" style={{ width: 90 }} value={form.year} onChange={(e) => setForm({ ...form, year: e.target.value })} /></label>
            <label style={{ flex: 1 }}>Label <input type="text" placeholder="End Semester 2024" value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} /></label>
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="file">upload a file</option>
              <option value="text">paste the questions</option>
            </select>
          </div>
          {mode === 'file'
            ? <p className="row"><input type="file" accept=".pdf,.docx" onChange={(e) => setFile(e.target.files[0])} /></p>
            : <p><textarea rows={5} placeholder={'One question per block, separated by a blank line.\n\n1. Define a data store. [2]\n\n2. Explain balancing. [5]'} value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })} /></p>}
          <button className="primary" disabled={busy || (mode === 'file' ? !file : !form.text.trim())}>
            {busy ? 'Extracting questions…' : 'Add paper'}
          </button>
          {mode === 'file' && <span className="muted"> If the splitter misreads the layout, paste the questions instead.</span>}
        </form>
      )}
      {error && <p className="error">{error}</p>}
      {papers.length === 0 ? <p className="muted">No past papers yet — the novelty check cannot run without them.</p> : (
        <table>
          <thead><tr><th>Paper</th><th>Year</th><th>Questions extracted</th><th /></tr></thead>
          <tbody>
            {papers.map((p) => (
              <Fragment key={p.paper_id}>
                <tr>
                  <td>{p.label} <span className="pill neutral">{p.source_type}</span></td>
                  <td>{p.year}</td>
                  <td>{p.question_count}</td>
                  <td>
                    <button className="link" onClick={() => toggle(p.paper_id)}>{questions[p.paper_id] ? 'hide' : 'check extraction'}</button>
                    {canUpload && <> · <button className="link" onClick={() => remove(p.paper_id)}>remove</button></>}
                  </td>
                </tr>
                {questions[p.paper_id] && (
                  <tr><td colSpan={4}>
                    <table>
                      <thead><tr><th>#</th><th>Question</th><th>Marks</th><th>Type</th><th>Bloom</th></tr></thead>
                      <tbody>
                        {questions[p.paper_id].map((q) => (
                          <tr key={q.pyq_id}>
                            <td>{q.number || '—'}</td><td>{q.text}</td><td>{q.marks ?? '—'}</td>
                            <td>{q.question_type || '—'}</td><td>{q.bloom_level || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

export default function Materials({ course, user, go }) {
  const canUpload = user.role === 'coordinator'
  return (
    <>
      <h2>Step 1 · Upload the sources</h2>
      <div className="intro">
        <strong>What happens here:</strong> the platform needs two kinds of source. The
        <strong> course material</strong> is what questions are written from — it is indexed so every
        question can cite the page it came from. The <strong>past-year papers</strong> are what
        questions are checked against, so a question that repeats an old one is caught automatically.
      </div>
      {!canUpload && <p className="notice">Only the course coordinator can upload. You can see what has been indexed below.</p>}
      <CourseMaterial course={course} canUpload={canUpload} />
      <PastPapers course={course} canUpload={canUpload} />
      <p><button className="primary" onClick={() => go('blueprints')}>Next: define the blueprint →</button></p>
    </>
  )
}
