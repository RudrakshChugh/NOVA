// The landing screen for a subject: where the paper stands, the one thing to do next, and what
// needs attention — instead of dropping straight into the upload form.

import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import Icon from '../components/Icon'
import { WORKFLOW } from '../workflow'
import { Btn, Card, Empty } from '../components/ui'

/** A number that counts up to its value the first time it appears. */
function CountUp({ value }) {
  const [shown, setShown] = useState(0)
  const from = useRef(0)
  useEffect(() => {
    const start = performance.now()
    const a = from.current
    let raf
    const tick = (now) => {
      const p = Math.min(1, (now - start) / 700)
      setShown(Math.round(a + (value - a) * (1 - Math.pow(1 - p, 3))))
      if (p < 1) raf = requestAnimationFrame(tick)
      else from.current = value
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [value])
  return shown.toLocaleString()
}

// What to say and offer for the first step that is not done yet.
const NEXT = {
  materials: ['Start by adding your <em>course material</em>.', 'Upload sources'],
  blueprints: ['Material is in. Now <em>define the paper</em>.', 'Open the blueprint'],
  generate: ['Blueprint frozen. Time to <em>generate</em>.', 'Generate questions'],
  review: ['Candidates are waiting for <em>your decision</em>.', 'Review candidates'],
  bank: ['Publish what you accepted to the <em>bank</em>.', 'Go to review'],
  paper: ['Questions are banked. <em>Assemble the paper</em>.', 'Assemble the paper'],
  done: ['This paper is <em>finalised</em>.', 'Open the paper'],
}

export default function Home({ course, go, progress }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    setData(null); setError('')
    const id = course.course_id
    Promise.all([
      api(`/courses/${id}/materials`), api(`/courses/${id}/pyq-papers`), api(`/bank/entries?course_id=${id}`),
      api(`/courses/${id}/blueprints`), api(`/courses/${id}/status`).catch(() => null),
    ]).then(async ([materials, papers, bank, blueprints, status]) => {
      const frozen = blueprints.filter((b) => b.frozen_at)
      const latest = frozen[frozen.length - 1] || null
      const runs = latest ? await api(`/blueprints/${latest.blueprint_id}/runs`) : []
      const lastRun = runs.find((r) => r.status === 'completed' && r.candidate_count > 0) || null
      const candidates = lastRun ? await api(`/runs/${lastRun.run_id}/candidates`) : []
      if (live) setData({ materials, papers, bank, latest, runs, lastRun, candidates, status })
    }).catch((e) => live && setError(e.message))
    return () => { live = false }
  }, [course.course_id])

  const nextStep = WORKFLOW.find((s) => !progress[s.key])
  const [headline, cta] = NEXT[nextStep?.key || 'done']
  const target = nextStep?.key === 'bank' ? 'review' : nextStep?.key || 'paper'
  const doneCount = WORKFLOW.filter((s) => progress[s.key]).length

  const passages = data ? data.materials.reduce((n, m) => n + m.chunk_count, 0) : 0
  const pastQuestions = data ? data.papers.reduce((n, p) => n + p.question_count, 0) : 0
  const pending = data ? data.candidates.filter((c) => !c.review && !['published', 'superseded'].includes(c.state)).length : 0

  // Marks per course outcome in the latest frozen blueprint.
  const coverage = []
  if (data?.latest) {
    const by = new Map()
    for (const c of data.latest.constraints) by.set(c.course_outcome, (by.get(c.course_outcome) || 0) + c.marks)
    const total = [...by.values()].reduce((a, b) => a + b, 0) || 1
    for (const [name, marks] of by) coverage.push({ name, marks, share: marks / total })
  }

  const attention = []
  if (data) {
    for (const c of data.status?.checks || []) {
      if (c.state === 'bad') attention.push({ tone: 'bad', title: c.label, detail: c.detail, to: c.key === 'material' ? 'materials' : 'generate', go: 'Fix' })
    }
    const running = data.runs.find((r) => r.status === 'running')
    if (running) attention.push({ tone: 'ok', title: `A run is in progress — ${running.candidate_count} of ${running.constraint_count} drafted`, to: 'generate', go: 'Watch' })
    if (pending) attention.push({ tone: 'warn', title: `${pending} candidate${pending === 1 ? '' : 's'} await a decision`, to: 'review', run: data.lastRun.run_id, go: 'Review' })
    for (const c of data.status?.checks || []) {
      if (c.state === 'warn') attention.push({ tone: 'warn', title: c.label, detail: c.detail, to: c.key === 'novelty' ? 'engines' : 'materials', go: 'Open' })
    }
  }

  return (
    <>
      <section className="hero">
        <div className="txt">
          <h1 dangerouslySetInnerHTML={{ __html: headline }} />
          <p>
            {course.code} · {course.title}
            {data?.latest && ` · blueprint v${data.latest.version}: ${data.latest.constraints.length} questions, ${data.latest.constraints.reduce((n, c) => n + c.marks, 0)} marks`}
          </p>
        </div>
        <Btn variant="primary" size="lg" icon="arrowRight" onClick={() => go(target, target === 'review' ? data?.lastRun?.run_id : undefined)}>{cta}</Btn>
      </section>

      <div className="track">
        {WORKFLOW.map((s) => {
          const done = !!progress[s.key]
          const next = s.key === nextStep?.key
          return (
            <button key={s.key} className={`tile${done ? ' done' : ''}${next ? ' next' : ''}`} onClick={() => go(s.key)}>
              <span className="n">STEP {s.n}</span>
              <strong>{s.label}</strong>
              <span>{next ? 'Up next' : done ? 'Done' : s.blurb}</span>
              {done && <Icon name="check" size={16} className="tick" />}
            </button>
          )
        })}
      </div>

      {error && <p className="error" style={{ marginTop: 'var(--s5)' }}>{error}</p>}

      <div className="stats" style={{ marginTop: 'var(--s6)' }}>
        <div className="stat stat-sun">
          <div className="stat-label">Paper progress</div>
          <div className="stat-value"><CountUp value={doneCount} /> / {WORKFLOW.length}</div>
          <div className="stat-hint">steps complete</div>
        </div>
        <div className="stat">
          <div className="stat-label">Passages indexed</div>
          <div className="stat-value">{data ? <CountUp value={passages} /> : '—'}</div>
          <div className="stat-hint">{data ? `from ${data.materials.length} file${data.materials.length === 1 ? '' : 's'}` : ' '}</div>
        </div>
        <div className="stat stat-brand">
          <div className="stat-label">Past questions</div>
          <div className="stat-value">{data ? <CountUp value={pastQuestions} /> : '—'}</div>
          <div className="stat-hint">{data ? `from ${data.papers.length} paper${data.papers.length === 1 ? '' : 's'}` : ' '}</div>
        </div>
        <div className="stat stat-rose">
          <div className="stat-label">In the bank</div>
          <div className="stat-value">{data ? <CountUp value={data.bank.length} /> : '—'}</div>
          <div className="stat-hint">published questions</div>
        </div>
      </div>

      <div className="home-grid">
        <div className="col">
          <Card title="Needs attention">
            {!data ? <div className="skeleton" style={{ height: 96 }} />
              : attention.length === 0
                ? <p className="muted">Nothing is blocking you. Carry on with the next step above.</p>
                : <div>{attention.map((a, i) => (
                  <button key={i} className="todo" onClick={() => go(a.to, a.run)}>
                    <span className={`lamp ${a.tone === 'warn' ? '' : a.tone}`} />
                    <span className="t">{a.title}{a.detail && <small>{a.detail}</small>}</span>
                    <span className="go">{a.go} →</span>
                  </button>
                ))}</div>}
          </Card>

          <Card title="Latest candidates" actions={data?.lastRun && <Btn size="sm" icon="arrowRight" onClick={() => go('review', data.lastRun.run_id)}>Open review</Btn>}>
            {!data ? <div className="skeleton" style={{ height: 120 }} />
              : data.candidates.length === 0
                ? <Empty icon="sparkle" title="No questions yet">Once a run finishes, its candidates appear here with their audit results.</Empty>
                : <div>{data.candidates.slice(0, 4).map((c) => (
                  <button key={c.question_id} className="todo" onClick={() => go('review', data.lastRun.run_id)}>
                    <span className={`lamp ${c.compliance?.is_compliant && !c.novelty?.is_duplicate ? 'ok' : 'bad'}`} />
                    <span className="t">
                      {c.stem}
                      <small>
                        Q{c.constraint.position + 1} · {c.constraint.course_outcome.split(' ')[0]} · {c.constraint.bloom_level} · {c.constraint.marks} marks
                        {c.novelty?.is_duplicate ? ' · repeats an existing question' : ''}
                        {c.compliance && !c.compliance.is_compliant ? ` · ${c.compliance.violations.length} check(s) failed` : ''}
                      </small>
                    </span>
                  </button>
                ))}</div>}
          </Card>
        </div>

        <div className="col">
          <Card title="Marks by course outcome" hint={data?.latest ? `Blueprint version ${data.latest.version}` : undefined}>
            {!data ? <div className="skeleton" style={{ height: 120 }} />
              : coverage.length === 0
                ? <p className="muted">Freeze a blueprint to see how its marks are spread across the course outcomes.</p>
                : <div className="coverage">{coverage.map((c) => {
                  const [code, ...rest] = c.name.split(' ')
                  return (
                    <div key={c.name} className="cov-row" title={c.name}>
                      <span className="code">{code}</span>
                      <span className="progress"><i style={{ width: `${c.share * 100}%` }} /></span>
                      <span className="val">{c.marks} · {Math.round(c.share * 100)}%</span>
                      <span className="name">{rest.join(' ')}</span>
                    </div>
                  )
                })}</div>}
          </Card>

        </div>
      </div>
    </>
  )
}
