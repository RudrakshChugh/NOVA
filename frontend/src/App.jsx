import { useCallback, useEffect, useState } from 'react'
import { api, setToken } from './api'
import Bank from './pages/Bank'
import Blueprints from './pages/Blueprints'
import Generate from './pages/Generate'
import Login from './pages/Login'
import Materials from './pages/Materials'
import Review from './pages/Review'

const STEPS = [
  { key: 'materials', n: 1, label: 'Upload material', who: 'Coordinator' },
  { key: 'blueprints', n: 2, label: 'Define blueprint', who: 'Coordinator' },
  { key: 'generate', n: 3, label: 'Generate questions', who: 'Reviewer' },
  { key: 'review', n: 4, label: 'Review candidates', who: 'Reviewer' },
  { key: 'bank', n: 5, label: 'Question bank', who: 'Everyone' },
]

export default function App() {
  const [user, setUser] = useState(null)
  const [course, setCourse] = useState(null)
  const [step, setStep] = useState('materials')
  const [runId, setRunId] = useState(null)
  const [progress, setProgress] = useState({})

  useEffect(() => {
    api('/auth/me').then(setUser).catch(() => setToken(''))
    api('/courses').then((cs) => setCourse(cs[0]))
  }, [])

  // Which steps have been completed, so the stepper can show where the course is in the journey.
  const refreshProgress = useCallback(async () => {
    if (!course) return
    const [materials, blueprints, bank] = await Promise.all([
      api(`/courses/${course.course_id}/materials`), api(`/courses/${course.course_id}/blueprints`), api('/bank/entries'),
    ])
    const frozen = blueprints.filter((b) => b.frozen_at)
    const runs = (await Promise.all(frozen.map((b) => api(`/blueprints/${b.blueprint_id}/runs`)))).flat()
    setProgress({
      materials: materials.length > 0, blueprints: frozen.length > 0,
      generate: runs.some((r) => r.status === 'completed'), review: runs.length > 0 && bank.length > 0, bank: bank.length > 0,
    })
  }, [course])
  useEffect(() => { refreshProgress() }, [refreshProgress, step])

  if (!user) return <Login onLogin={setUser} />
  if (!course) return <main>Loading course…</main>

  const go = (key, id) => { if (id) setRunId(id); setStep(key) }
  const pageProps = { course, user, go, runId }

  return (
    <>
      <header className="top">
        <h1>Assessment-Quality Platform</h1>
        <span className="pill brand">{course.code} · {course.title}</span>
        <span className="who">Signed in as <strong>{user.name}</strong> ({user.role})</span>
        <button onClick={() => { setToken(''); setUser(null) }}>Log out</button>
      </header>
      <main>
        <div className="steps">
          {STEPS.map((s) => (
            <button key={s.key} className={`step ${step === s.key ? 'active' : ''} ${progress[s.key] ? 'done' : ''}`} onClick={() => go(s.key)}>
              <span className="n">{progress[s.key] ? '✓' : s.n}</span>{s.label}
              <span className="who">{s.who}</span>
            </button>
          ))}
        </div>
        {step === 'materials' && <Materials {...pageProps} />}
        {step === 'blueprints' && <Blueprints {...pageProps} />}
        {step === 'generate' && <Generate {...pageProps} />}
        {step === 'review' && <Review {...pageProps} />}
        {step === 'bank' && <Bank {...pageProps} />}
      </main>
    </>
  )
}
