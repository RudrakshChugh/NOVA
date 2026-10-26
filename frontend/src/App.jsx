import { useCallback, useEffect, useState } from 'react'
import { api, setToken } from './api'
import Shell from './components/Shell'
import { ToastHost } from './components/ui'
import Bank from './pages/Bank'
import Blueprints from './pages/Blueprints'
import Engines from './pages/Engines'
import Evaluation from './pages/Evaluation'
import Generate from './pages/Generate'
import Login from './pages/Login'
import Materials from './pages/Materials'
import Review from './pages/Review'

const PAGES = { materials: Materials, blueprints: Blueprints, generate: Generate, review: Review, bank: Bank, engines: Engines, evaluation: Evaluation }

export default function App() {
  const [user, setUser] = useState(null)
  const [course, setCourse] = useState(null)
  const [booting, setBooting] = useState(true)
  const [step, setStep] = useState('materials')
  const [runId, setRunId] = useState(null)
  const [progress, setProgress] = useState({})

  useEffect(() => {
    api('/auth/me').then(setUser).catch(() => setToken(''))
    api('/courses').then((cs) => setCourse(cs[0])).finally(() => setBooting(false))
  }, [])

  // Which steps are complete, so the sidebar can show where the course is in the journey.
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
  useEffect(() => { refreshProgress().catch(() => {}) }, [refreshProgress, step])

  if (!user) return <ToastHost><Login onLogin={setUser} /></ToastHost>
  if (booting) return <div className="boot"><span className="spinner" style={{ width: 20, height: 20 }} />Loading course…</div>
  if (!course) return <div className="boot">No course is configured yet.</div>

  const go = (key, id) => { if (id) setRunId(id); setStep(key); window.scrollTo({ top: 0 }) }
  const Page = PAGES[step]

  return (
    <ToastHost>
      <Shell
        user={user} course={course} step={step} progress={progress} go={go}
        onSignOut={() => { setToken(''); setUser(null) }}
      >
        <Page course={course} user={user} go={go} runId={runId} />
      </Shell>
    </ToastHost>
  )
}
