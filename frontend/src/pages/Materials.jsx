import { Fragment, useEffect, useState } from 'react'
import { api } from '../api'
import { pageLabel } from '../format'

export default function Materials({ course, user, go }) {
  const [materials, setMaterials] = useState([])
  const [preview, setPreview] = useState({}) // material_id -> chunks
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
    <>
      <h2>Step 1 · Upload course material</h2>
      <div className="intro">
        <strong>What happens here:</strong> you upload one unit of course material (PDF or Word). The system reads it page by page,
        splits it into short overlapping passages, and indexes each passage so that every generated question can be traced back
        to the exact page it came from. Questions are only ever written from these passages — never from the model's own memory.
      </div>

      {user.role === 'coordinator' ? (
        <form className="card" onSubmit={upload}>
          <h3>Upload a file</h3>
          <div className="row">
            <input type="file" accept=".pdf,.docx" onChange={(e) => setFile(e.target.files[0])} />
            <button className="primary" disabled={!file || busy}>{busy ? 'Reading, splitting and indexing…' : 'Upload & index'}</button>
          </div>
          <p className="muted">Lecture notes, slides or a textbook chapter. Indexing takes a few seconds per document.</p>
          {error && <p className="error">{error}</p>}
        </form>
      ) : (
        <p className="notice">Only the course coordinator can upload material. You can still see what has been indexed below.</p>
      )}

      <div className="card">
        <h3>Indexed material</h3>
        {materials.length === 0 ? <p className="muted">Nothing uploaded yet.</p> : (
          <table>
            <thead><tr><th>File</th><th>Uploaded</th><th>Passages indexed</th><th>Pages covered</th><th /></tr></thead>
            <tbody>
              {materials.map((m) => (
                <Fragment key={m.material_id}>
                  <tr>
                    <td>{m.filename} <span className="pill neutral">{m.source_type}</span></td>
                    <td>{new Date(m.uploaded_at).toLocaleString()}</td>
                    <td>{m.chunk_count}</td>
                    <td>{m.pages_covered}</td>
                    <td><button className="link" onClick={() => togglePreview(m.material_id)}>{preview[m.material_id] ? 'hide passages' : 'show sample passages'}</button></td>
                  </tr>
                  {preview[m.material_id] && (
                    <tr><td colSpan={5}>
                      <p className="muted">First few indexed passages — each one carries the page it was read from:</p>
                      {preview[m.material_id].map((c) => <div key={c.chunk_id} className="passage"><div className="meta">{pageLabel(c.page_ref)}</div>{c.text}</div>)}
                    </td></tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {materials.length > 0 && <p><button className="primary" onClick={() => go('blueprints')}>Next: define the blueprint →</button></p>}
    </>
  )
}
