import { useEffect, useState } from 'react'
import { api } from '../api'

export default function Materials({ course, user }) {
  const [materials, setMaterials] = useState([])
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

  return (
    <>
      <h2>Course material</h2>
      {user.role === 'coordinator' && (
        <form className="card" onSubmit={upload}>
          <input type="file" accept=".pdf,.docx" onChange={(e) => setFile(e.target.files[0])} />{' '}
          <button disabled={!file || busy}>{busy ? 'Parsing, chunking, embedding…' : 'Upload & ingest'}</button>
          {error && <p className="error">{error}</p>}
        </form>
      )}
      <table>
        <thead><tr><th>File</th><th>Type</th><th>Uploaded</th><th>Chunks indexed</th><th>Pages / paragraphs covered</th></tr></thead>
        <tbody>
          {materials.map((m) => (
            <tr key={m.material_id}>
              <td>{m.filename}</td><td>{m.source_type}</td><td>{new Date(m.uploaded_at).toLocaleString()}</td>
              <td>{m.chunk_count}</td><td>{m.pages_covered}</td>
            </tr>
          ))}
          {materials.length === 0 && <tr><td colSpan={5} className="muted">Nothing uploaded yet.</td></tr>}
        </tbody>
      </table>
    </>
  )
}
