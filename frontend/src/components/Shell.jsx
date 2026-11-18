// Application frame: one bar across the top and nothing down the side, so the work gets the
// full width. The bar is the workflow — Home, then the six steps in order, each ticked once the
// subject has completed it — which means where a paper has got to is visible from every screen.

import { useEffect, useRef, useState } from 'react'
import Icon from './Icon'
import ThemeSwitch from './ThemeSwitch'
import { WORKFLOW } from '../workflow'

const MORE = [
  { key: 'engines', label: 'Engines', icon: 'engines', hint: 'Novelty model and ranker' },
  { key: 'evaluation', label: 'Evaluation', icon: 'evaluation', hint: 'Baselines and blinded study' },
]

function initials(name) {
  return name.split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase()
}

/** A button with a panel under it that closes on an outside click or Escape. */
function Menu({ label, trigger, children }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return
    const away = (e) => { if (!ref.current?.contains(e.target)) setOpen(false) }
    const esc = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc) }
  }, [open])
  return (
    <div className="menu" ref={ref}>
      {trigger({ open, toggle: () => setOpen(!open), label })}
      {open && <div className="menu-pop" role="menu" onClick={() => setOpen(false)}>{children}</div>}
    </div>
  )
}

export default function Shell({ user, course, courses = [], onPickCourse, step, progress, waiting = 0, go, onSignOut, children }) {
  // The likeliest mistake in a multi-subject build is doing something to the wrong subject,
  // so the subject sits on every screen rather than on a settings page.
  const canAdminister = user.role === 'admin' || course?.your_role === 'coordinator'
  const role = user.role === 'admin' ? 'administrator' : course?.your_role || user.role
  const inMore = MORE.some((m) => m.key === step) || step === 'people'

  return (
    <div className="app">
      <header className="topbar">
        <button className="wordmark" onClick={() => go('home')} aria-label="NOVA home">NOVA</button>

        {course && (
          <div className="subject" title={`${course.code} — ${course.title}`}>
            <span className="code">{course.code}</span>
            {courses.length > 1 ? (
              <select value={course.course_id} aria-label="Subject" onChange={(e) => onPickCourse(e.target.value)}>
                {courses.map((c) => <option key={c.course_id} value={c.course_id}>{c.title}</option>)}
              </select>
            ) : (
              <span className="title">{course.title}</span>
            )}
          </div>
        )}

        <nav className="stepper" aria-label="Workflow">
          <button className="step home" aria-current={step === 'home' ? 'page' : undefined} onClick={() => go('home')}>
            <Icon name="layers" size={15} /><span className="lbl">Home</span>
          </button>
          {WORKFLOW.map((s) => (
            <span key={s.key} style={{ display: 'contents' }}>
              <span className={`step-sep${progress[s.key] ? ' done' : ''}`} />
              <button
                className={`step${progress[s.key] ? ' done' : ''}`} title={`${s.n}. ${s.title}`}
                aria-current={step === s.key ? 'page' : undefined} onClick={() => go(s.key)}
              >
                <span className="n">{progress[s.key] ? <Icon name="check" size={12} /> : s.n}</span>
                <span className="lbl">{s.label}</span>
              </button>
            </span>
          ))}
        </nav>

        <div className="tools">
          <Menu
            label="More"
            trigger={({ toggle, open }) => (
              <button className={`btn btn-ghost btn-sm${inMore ? ' on' : ''}`} onClick={toggle} aria-expanded={open} aria-haspopup="menu">
                More
                {waiting > 0 && canAdminister && <span className="step"><span className="count">{waiting}</span></span>}
                <Icon name="chevronDown" size={13} />
              </button>
            )}
          >
            <h4>Models and evidence</h4>
            {MORE.map((m) => (
              <button key={m.key} className="menu-item" role="menuitem" aria-current={step === m.key ? 'page' : undefined} onClick={() => go(m.key)}>
                <Icon name={m.icon} size={17} />
                <span>{m.label}<br /><span className="muted" style={{ fontSize: 12 }}>{m.hint}</span></span>
              </button>
            ))}
            {canAdminister && (
              <>
                <h4>Administration</h4>
                <button className="menu-item" role="menuitem" aria-current={step === 'people' ? 'page' : undefined} onClick={() => go('people')}>
                  <Icon name="shield" size={17} />
                  <span>People and roles</span>
                  {waiting > 0 && <span className="count" title={`${waiting} waiting to be admitted`}>{waiting}</span>}
                </button>
              </>
            )}
          </Menu>

          <ThemeSwitch />

          <Menu
            label="Account"
            trigger={({ toggle, open }) => (
              <button className="avatar" onClick={toggle} aria-expanded={open} aria-haspopup="menu" aria-label={`Account: ${user.name}`}>
                {initials(user.name)}
              </button>
            )}
          >
            <div className="head">
              <strong style={{ display: 'block' }}>{user.name}</strong>
              <span className="muted">{user.email}</span><br />
              <span className="badge badge-neutral cap" style={{ marginTop: 6 }}>{role}</span>
            </div>
            <button className="menu-item" role="menuitem" onClick={onSignOut}>
              <Icon name="logout" size={17} /><span>Sign out</span>
            </button>
          </Menu>
        </div>
      </header>

      <main className="content" key={step}>{children}</main>
    </div>
  )
}
