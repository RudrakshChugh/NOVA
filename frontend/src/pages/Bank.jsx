import { useEffect, useState } from 'react'
import { api } from '../api'

export default function Bank() {
  const [entries, setEntries] = useState([])
  const [open, setOpen] = useState(null)

  useEffect(() => { api('/bank/entries').then(setEntries) }, [])

  return (
    <>
      <h2>Question bank</h2>
      <table>
        <thead><tr><th>Published</th><th>v</th><th>Question</th><th>Outcome</th><th>Bloom / marks / type</th><th>Blueprint</th><th>Reviewer</th><th /></tr></thead>
        <tbody>
          {entries.map((e) => {
            const s = e.snapshot
            return (
              <tr key={e.entry_id}>
                <td>{new Date(e.published_at).toLocaleString()}</td><td>{e.version}</td><td>{s.stem}</td>
                <td>{s.constraint.course_outcome}</td><td>{s.constraint.bloom_level} / {s.constraint.marks} / {s.constraint.question_type}</td>
                <td>v{s.blueprint.version}</td><td>{s.review.reviewer} ({s.review.decision})</td>
                <td><button onClick={() => setOpen(open === e.entry_id ? null : e.entry_id)}>provenance</button></td>
              </tr>
            )
          })}
          {entries.length === 0 && <tr><td colSpan={8} className="muted">Nothing published yet.</td></tr>}
        </tbody>
      </table>
      {open && <pre>{JSON.stringify(entries.find((e) => e.entry_id === open).snapshot, null, 2)}</pre>}
    </>
  )
}
