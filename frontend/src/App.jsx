import { useCallback, useEffect, useState } from 'react'
import { api, hasToken, setToken } from './api'
import Icon from './components/Icon'
import Shell from './components/Shell'
import { Btn, Card, ToastHost } from './components/ui'
import Bank from './pages/Bank'
import Blueprints from './pages/Blueprints'
import Engines from './pages/Engines'
import Evaluation from './pages/Evaluation'
import Generate from './pages/Generate'
import Login from './pages/Login'
import Materials from './pages/Materials'
import People from './pages/People'
import Review from './pages/Review'

const PAGES = {
  materials: Materials, blueprints: Blueprints, generate: Generate, review: Review,
  bank: Bank, engines: Engines, evaluation: Evaluation, people: People,
}

/** Signed in, but not yet granted a role. The account is real and the session is valid — there is
 *  simply nothing this person may do until a coordinator acts, so saying so plainly beats a
 *  permission error on every screen. */
function AwaitingApproval({ user, onSignOut }) {
  return (
    <div className="boot">
      <Card title="Your account is waiting for approval" className="" >
        <p>
          You are signed in as <strong>{user.email}</strong>, but a coordinator has not yet given you
          a role on this platform.
        </p>
        <p className="muted">
          Ask the course coordinator to grant you <strong>reviewer</strong> access. Nothing is
          visible until they do — which is deliberate: review decisions become training data for the
          ranker and are counted in the inter-rater agreement figure.
        </p>
        <div className="row">
          <Btn icon="refresh" onClick={() => window.location.reload()}>Check again</Btn>
          <Btn variant="ghost" icon="logout" onClick={onSignOut}>Sign out</Btn>
        </div>
      </Card>
    </div>
  )
}

export default function App() {
  const [user, setUser] = useState(null)
  const [course, setCourse] = useState(null)
  const [booting, setBooting] = useState(true)
  const [step, setStep] = useState('materials')
  const [runId, setRunId] = useState(null)
  const [progress, setProgress] = useState({})

  const signOut = () => { setToken(''); setUser(null); setCourse(null) }

  useEffect(() => {
    // Only probe when a session is actually stored. Arriving from the sign-in callback the token
    // is still in the URL fragment and nothing is in storage yet, so probing here would send a
    // header-less request and log a 401 on every single sign-in — noise that hides real errors.
    // Login captures the fragment and hands the user straight over.
    if (!hasToken()) return setBooting(false)
    api('/auth/me').then(setUser).catch(() => setToken('')).finally(() => setBooting(false))
  }, [])

  // The course list needs a granted role, so it is only fetched once there is one.
  useEffect(() => {
    if (!user || user.role === 'pending') return
    api('/courses').then((cs) => setCourse(cs[0])).catch(() => setCourse(null))
  }, [user])

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

  if (booting) return <div className="boot"><span className="spinner" style={{ width: 20, height: 20 }} />Loading…</div>
  if (!user) return <ToastHost><Login onLogin={setUser} /></ToastHost>
  if (user.role === 'pending') return <ToastHost><AwaitingApproval user={user} onSignOut={signOut} /></ToastHost>
  if (!course) {
    return (
      <div className="boot">
        <Icon name="alert" size={24} />
        No course is configured on this deployment yet.
        <Btn variant="ghost" icon="logout" onClick={signOut}>Sign out</Btn>
      </div>
    )
  }

  const go = (key, id) => { if (id) setRunId(id); setStep(key); window.scrollTo({ top: 0 }) }
  const Page = PAGES[step]

  return (
    <ToastHost>
      <Shell user={user} course={course} step={step} progress={progress} go={go} onSignOut={signOut}>
        <Page course={course} user={user} go={go} runId={runId} />
      </Shell>
    </ToastHost>
  )
}
