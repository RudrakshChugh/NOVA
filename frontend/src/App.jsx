import { useCallback, useEffect, useState } from 'react'
import { api, hasToken, onSessionExpired, setToken } from './api'
import Shell from './components/Shell'
import { Btn, Card, ToastHost } from './components/ui'
import Bank from './pages/Bank'
import Blueprints from './pages/Blueprints'
import Engines from './pages/Engines'
import Evaluation from './pages/Evaluation'
import Generate from './pages/Generate'
import Home from './pages/Home'
import Login from './pages/Login'
import Materials from './pages/Materials'
import Paper from './pages/Paper'
import People from './pages/People'
import Review from './pages/Review'

const PAGES = {
  home: Home, materials: Materials, blueprints: Blueprints, generate: Generate, review: Review,
  bank: Bank, paper: Paper, engines: Engines, evaluation: Evaluation, people: People,
}

const LAST_COURSE = 'course_id'

/** Signed in, but not yet admitted. The account is real and the session is valid — there is
 *  simply nothing this person may do until an administrator acts, so saying so plainly beats a
 *  permission error on every screen. */
function AwaitingApproval({ user, onSignOut }) {
  return (
    <div className="boot">
      <Card title="Your account is waiting for approval">
        <p>
          You are signed in as <strong>{user.email}</strong>, but nobody has admitted you to this
          platform yet.
        </p>
        <p className="muted">
          Ask an administrator to admit you, and the coordinator of your subject to add you to it.
          Nothing is visible until they do — which is deliberate: review decisions become training
          data for the ranker and are counted in the inter-rater agreement figure.
        </p>
        <div className="row">
          <Btn icon="refresh" onClick={() => window.location.reload()}>Check again</Btn>
          <Btn variant="ghost" icon="logout" onClick={onSignOut}>Sign out</Btn>
        </div>
      </Card>
    </div>
  )
}

/** Admitted, but on no subject. Separate from the screen above because the remedy is different:
 *  a coordinator of a subject adds you to it, not an administrator. */
function NoSubjects({ user, onSignOut }) {
  return (
    <div className="boot">
      <Card title="You are not on any subject yet">
        <p>
          Your account (<strong>{user.email}</strong>) has been admitted, but you have not been
          added to a subject.
        </p>
        <p className="muted">
          Ask the coordinator of the subject you are teaching to add you as a reviewer. Material,
          blueprints and the question bank all belong to a subject, so there is nothing to show
          until you are on one.
        </p>
        <div className="row">
          <Btn icon="refresh" onClick={() => window.location.reload()}>Check again</Btn>
          <Btn variant="ghost" icon="logout" onClick={onSignOut}>Sign out</Btn>
        </div>
      </Card>
    </div>
  )
}

function Boot() {
  return (
    <div className="boot" role="status" aria-label="Loading">
      <span className="wordmark big">NOVA</span>
      <span>Loading…</span>
    </div>
  )
}

export default function App() {
  const [user, setUser] = useState(null)
  const [courses, setCourses] = useState(null)  // null while unknown, [] when there are none
  const [course, setCourse] = useState(null)
  const [booting, setBooting] = useState(true)
  const [step, setStep] = useState('home')
  const [runId, setRunId] = useState(null)
  const [progress, setProgress] = useState({})
  const [waiting, setWaiting] = useState(0)

  const signOut = useCallback(() => { setToken(''); setUser(null); setCourses(null); setCourse(null) }, [])

  // A session lasts twelve hours. When the server stops accepting it, every screen would
  // otherwise fail in its own way; this returns to sign-in once, wherever the 401 came from.
  useEffect(() => { onSessionExpired(signOut) }, [signOut])

  useEffect(() => {
    // Only probe when a session is actually stored. Arriving from the sign-in callback the token
    // is still in the URL fragment and nothing is in storage yet, so probing here would send a
    // header-less request and log a 401 on every single sign-in — noise that hides real errors.
    // Login captures the fragment and hands the user straight over.
    if (!hasToken()) return setBooting(false)
    api('/auth/me').then(setUser).catch(() => setToken('')).finally(() => setBooting(false))
  }, [])

  // Subjects need an admitted account, so they are only fetched once there is one. The last
  // subject used is remembered per browser, because somebody who coordinates one subject and
  // reviews another should not have to re-pick on every visit.
  useEffect(() => {
    if (!user || user.role === 'pending') return
    api('/courses').then((cs) => {
      setCourses(cs)
      let remembered = null
      try { remembered = localStorage.getItem(LAST_COURSE) } catch { /* private window */ }
      setCourse(cs.find((c) => c.course_id === remembered) || cs[0] || null)
    }).catch(() => { setCourses([]); setCourse(null) })
  }, [user])

  const pickCourse = (id) => {
    const next = courses?.find((c) => c.course_id === id)
    if (!next) return
    try { localStorage.setItem(LAST_COURSE, id) } catch { /* private window */ }
    setCourse(next)
    setRunId(null)
    setProgress({})
    setStep('home')
  }

  // Which steps are complete, so the sidebar can show where this subject is in the journey.
  const refreshProgress = useCallback(async () => {
    if (!course) return
    const [materials, blueprints, bank, papers] = await Promise.all([
      api(`/courses/${course.course_id}/materials`),
      api(`/courses/${course.course_id}/blueprints`),
      api(`/bank/entries?course_id=${course.course_id}`),
      api(`/courses/${course.course_id}/papers`),
    ])
    const frozen = blueprints.filter((b) => b.frozen_at)
    const runs = (await Promise.all(frozen.map((b) => api(`/blueprints/${b.blueprint_id}/runs`)))).flat()
    setProgress({
      materials: materials.length > 0, blueprints: frozen.length > 0,
      generate: runs.some((r) => r.status === 'completed'), review: runs.length > 0 && bank.length > 0,
      bank: bank.length > 0, paper: papers.some((p) => p.state === 'final'),
    })
  }, [course])
  useEffect(() => { refreshProgress().catch(() => {}) }, [refreshProgress, step])

  // Someone signing in creates an account that can do nothing until an administrator admits it.
  // Nothing else would say so, so the count rides on the navigation itself.
  useEffect(() => {
    if (user?.role !== 'admin') return
    api('/auth/people')
      .then((people) => setWaiting(people.filter((p) => p.role === 'pending').length))
      .catch(() => {})
  }, [user, step])

  if (booting) return <Boot />
  if (!user) return <ToastHost><Login onLogin={setUser} /></ToastHost>
  if (user.role === 'pending') return <ToastHost><AwaitingApproval user={user} onSignOut={signOut} /></ToastHost>
  if (courses === null) return <Boot />
  if (!course) return <ToastHost><NoSubjects user={user} onSignOut={signOut} /></ToastHost>

  const go = (key, id) => { if (id) setRunId(id); setStep(key); window.scrollTo({ top: 0 }) }
  const Page = PAGES[step]

  return (
    <ToastHost>
      <Shell
        user={user} course={course} courses={courses} onPickCourse={pickCourse}
        step={step} progress={progress} waiting={waiting} go={go} onSignOut={signOut}
      >
        <Page course={course} user={user} go={go} runId={runId} progress={progress} />
      </Shell>
    </ToastHost>
  )
}
