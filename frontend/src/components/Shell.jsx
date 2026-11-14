// Application frame: a grouped sidebar that doubles as the workflow tracker, and a sticky topbar.
// The five workflow entries carry their own progress tick, so where a course has got to is visible
// from anywhere in the app rather than only on the step pages.

import { useState } from 'react'
import Icon from './Icon'
import { Badge, Btn } from './ui'

const WORKFLOW = [
  { key: 'materials', n: 1, label: 'Sources', icon: 'upload', title: 'Upload the sources', who: 'Coordinator' },
  { key: 'blueprints', n: 2, label: 'Blueprint', icon: 'blueprint', title: 'Define the blueprint', who: 'Coordinator' },
  { key: 'generate', n: 3, label: 'Generate', icon: 'generate', title: 'Generate questions', who: 'Reviewer' },
  { key: 'review', n: 4, label: 'Review', icon: 'review', title: 'Review candidates', who: 'Reviewer' },
  { key: 'bank', n: 5, label: 'Question bank', icon: 'bank', title: 'Question bank', who: 'Everyone' },
  { key: 'paper', n: 6, label: 'Paper', icon: 'file', title: 'Assemble the paper', who: 'Coordinator' },
]

const ANALYSIS = [
  { key: 'engines', label: 'Engines', icon: 'engines', title: 'Auditing engines' },
  { key: 'evaluation', label: 'Evaluation', icon: 'evaluation', title: 'Evaluation study' },
]

const ADMIN = [
  { key: 'people', label: 'People', icon: 'shield', title: 'People and roles' },
]

const ALL = [...WORKFLOW, ...ANALYSIS, ...ADMIN]

function initials(name) {
  return name.split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase()
}

export default function Shell({ user, course, courses = [], onPickCourse, step, progress, waiting = 0, go, onSignOut, children }) {
  const [open, setOpen] = useState(false)
  const current = ALL.find((s) => s.key === step)
  // The likeliest mistake in a multi-subject build is doing something to the wrong subject,
  // so the picker sits on every screen rather than on a settings page.
  const canAdminister = user.role === 'admin' || course?.your_role === 'coordinator'
  const inWorkflow = WORKFLOW.some((s) => s.key === step)
  const visit = (key) => { setOpen(false); go(key) }

  return (
    <div className="app">
      {open && <div className="scrim" onClick={() => setOpen(false)} />}
      <aside className={`sidebar${open ? ' open' : ''}`}>
        <div className="brand">
          <div className="brand-mark">AQ</div>
          <div className="brand-text">
            <strong>Assessment Quality</strong>
            <span>Question generation</span>
          </div>
        </div>

        {course && (
          <div className="course-chip">
            <div className="code">
              {course.code}
              {course.your_role && <span className="role">{course.your_role}</span>}
            </div>
            {courses.length > 1 ? (
              <select
                className="course-pick" value={course.course_id} aria-label="Subject"
                onChange={(e) => onPickCourse(e.target.value)}
              >
                {courses.map((c) => (
                  <option key={c.course_id} value={c.course_id}>{c.code} — {c.title}</option>
                ))}
              </select>
            ) : (
              <div className="title" title={course.title}>{course.title}</div>
            )}
          </div>
        )}

        <nav className="nav">
          <div className="nav-group">
            <h4>Workflow</h4>
            {WORKFLOW.map((s) => (
              <button
                key={s.key} className={`nav-item${progress[s.key] ? ' done' : ''}`}
                aria-current={step === s.key ? 'page' : undefined} onClick={() => visit(s.key)}
                title={`${s.n}. ${s.title}`}
              >
                <span className="nav-step">{progress[s.key] ? <Icon name="check" size={11} /> : s.n}</span>
                <span className="label">{s.label}</span>
              </button>
            ))}
          </div>

          <div className="nav-group">
            <h4>Models &amp; evidence</h4>
            {ANALYSIS.map((s) => (
              <button
                key={s.key} className="nav-item" aria-current={step === s.key ? 'page' : undefined}
                onClick={() => visit(s.key)} title={s.title}
              >
                <Icon name={s.icon} size={17} />
                <span className="label">{s.label}</span>
              </button>
            ))}
          </div>
          {canAdminister && (
            <div className="nav-group">
              <h4>Administration</h4>
              {ADMIN.map((s) => (
                <button
                  key={s.key} className="nav-item" aria-current={step === s.key ? 'page' : undefined}
                  onClick={() => visit(s.key)} title={s.title}
                >
                  <Icon name={s.icon} size={17} />
                  <span className="label">{s.label}</span>
                  {s.key === 'people' && waiting > 0 && (
                    <span className="nav-count" title={`${waiting} waiting for a role`}>{waiting}</span>
                  )}
                </button>
              ))}
            </div>
          )}
        </nav>

        <div className="sidebar-foot">
          <div className="who" style={{ marginBottom: 'var(--s3)' }}>
            <span className="avatar">{initials(user.name)}</span>
            <span className="who-text">
              <strong title={user.email}>{user.name}</strong>
              <span>{user.role === 'admin' ? 'administrator' : course?.your_role || user.role}</span>
            </span>
          </div>
          <Btn variant="ghost" size="sm" icon="logout" onClick={onSignOut}>Sign out</Btn>
        </div>
      </aside>

      <div style={{ minWidth: 0 }}>
        <header className="topbar">
          <Btn variant="ghost" className="menu-btn" onClick={() => setOpen(!open)} aria-label="Menu">
            <Icon name="menu" size={18} />
          </Btn>
          <div className="crumb">
            <span>{inWorkflow ? 'Workflow' : ADMIN.some((a) => a.key === step) ? 'Administration' : 'Models & evidence'}</span>
            <Icon name="chevronRight" size={13} />
            <strong>{current?.title}</strong>
          </div>
          <div className="spacer" />
          {inWorkflow && current && <Badge tone="outline">{current.who}’s step</Badge>}
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  )
}
