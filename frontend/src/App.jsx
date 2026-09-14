import { useEffect, useState } from 'react'
import { api, setToken } from './api'
import Bank from './pages/Bank'
import Blueprints from './pages/Blueprints'
import Login from './pages/Login'
import Materials from './pages/Materials'
import Run from './pages/Run'

const PAGES = { materials: 'Materials', blueprints: 'Blueprints', run: 'Generate & Review', bank: 'Question Bank' }

export default function App() {
  const [user, setUser] = useState(null)
  const [course, setCourse] = useState(null)
  const [page, setPage] = useState('materials')
  const [runId, setRunId] = useState(null)

  useEffect(() => {
    api('/auth/me').then(setUser).catch(() => setToken(''))
    api('/courses').then((cs) => setCourse(cs[0]))
  }, [])

  if (!user) return <Login onLogin={setUser} />
  if (!course) return <main>Loading course…</main>

  const logout = () => { setToken(''); setUser(null) }
  const openRun = (id) => { setRunId(id); setPage('run') }

  return (
    <>
      <header>
        <strong>{course.code} · {course.title}</strong>
        <nav>
          {Object.entries(PAGES).map(([k, label]) => (
            <button key={k} disabled={page === k} onClick={() => setPage(k)}>{label}</button>
          ))}
        </nav>
        <span style={{ marginLeft: 'auto' }} className="muted">{user.name} ({user.role})</span>
        <button onClick={logout}>Log out</button>
      </header>
      <main>
        {page === 'materials' && <Materials course={course} user={user} />}
        {page === 'blueprints' && <Blueprints course={course} user={user} onRunStarted={openRun} />}
        {page === 'run' && <Run course={course} user={user} runId={runId} onSelectRun={setRunId} />}
        {page === 'bank' && <Bank />}
      </main>
    </>
  )
}
