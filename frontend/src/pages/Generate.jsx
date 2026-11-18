import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import Icon from '../components/Icon'
import { Badge, Btn, Card, Empty, Explainer, Field, Note, PageHead, Progress, Table, useToast } from '../components/ui'

const PIPELINE = [
  ['target', 'Retrieve', 'The five passages closest to the row’s concept and outcome.'],
  ['sparkle', 'Draft', 'One question for that outcome, told what already exists.'],
  ['shield', 'Audit', 'Six rule checks, then novelty against papers, bank and this run.'],
  ['refresh', 'Repair', 'A failure goes back with the reason named.'],
]

/** What a run needs, and whether it is there. Asked before the button is pressed, because a
 *  run takes minutes and the commonest reasons it fails are all knowable up front. */
function SystemCheck({ status, error, onRefresh, busy }) {
  if (error) {
    return (
      <Note tone="warn">
        Could not check readiness ({error}). You can still start a run; if something is missing
        the run will say so when it fails.
      </Note>
    )
  }
  if (!status) return <div className="skeleton" style={{ height: 76 }} />
  const blocking = status.checks.filter((c) => c.state === 'bad')
  return (
    <div className="stack">
      <div className="row">
        {blocking.length === 0
          ? <Badge tone="ok" dot>Ready to generate</Badge>
          : <Badge tone="bad" dot>{blocking.length} thing{blocking.length === 1 ? '' : 's'} to fix first</Badge>}
        <span className="muted">Checked just now against this subject.</span>
        <div style={{ flex: 1 }} />
        <Btn variant="ghost" size="sm" icon="refresh" busy={busy} onClick={onRefresh}>Check again</Btn>
      </div>
      <div className="syscheck">
        {status.checks.map((c) => (
          <div key={c.key} className={`sysitem ${c.state}`}>
            <span className="lamp" />
            <div style={{ minWidth: 0 }}>
              <div className="t">{c.label}</div>
              <div className="d">{c.detail}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function Generate({ course, go }) {
  const [blueprints, setBlueprints] = useState([])
  const [loaded, setLoaded] = useState(false)
  const [selected, setSelected] = useState('')
  const [runs, setRuns] = useState([])
  const [suggestion, setSuggestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState(null)
  const [statusError, setStatusError] = useState('')
  const [checking, setChecking] = useState(false)
  const toast = useToast()

  useEffect(() => {
    setLoaded(false)
    api(`/courses/${course.course_id}/blueprints`).then((bps) => {
      const frozen = bps.filter((b) => b.frozen_at)
      setBlueprints(frozen)
      setSelected(frozen.length ? frozen[frozen.length - 1].blueprint_id : '')
    }).catch((e) => setError(e.message)).finally(() => setLoaded(true))
  }, [course.course_id])

  const checkStatus = useCallback(() => {
    setChecking(true)
    return api(`/courses/${course.course_id}/status`)
      .then((s) => { setStatus(s); setStatusError('') })
      .catch((e) => setStatusError(e.message))
      .finally(() => setChecking(false))
  }, [course.course_id])
  useEffect(() => { setStatus(null); checkStatus() }, [checkStatus])

  const loadRuns = useCallback(() => {
    if (!selected) { setRuns([]); return Promise.resolve() }
    return api(`/blueprints/${selected}/runs`).then(setRuns)
  }, [selected])

  useEffect(() => { loadRuns().catch((e) => setError(e.message)) }, [loadRuns])

  const running = runs.find((r) => r.status === 'running')
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => loadRuns().catch(() => {}), 4000)
    return () => clearInterval(t)
  }, [running, loadRuns])

  // Say so when a run that was being watched finishes, whichever way it went.
  const runningId = running?.run_id
  useEffect(() => {
    if (!runningId) return
    return () => {
      api(`/runs/${runningId}`).then((r) => {
        if (r.status === 'completed') toast('ok', 'Run finished', `${r.candidate_count} candidate${r.candidate_count === 1 ? '' : 's'} ready to review.${r.error ? ' Some rows were skipped.' : ''}`)
        else if (r.status === 'failed') toast('bad', 'Run failed', r.error || 'See the run table for the reason.')
      }).catch(() => {})
    }
  }, [runningId]) // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    setBusy(true); setError('')
    try {
      await api(`/blueprints/${selected}/generate`, { method: 'POST', body: { suggestion: suggestion.trim() || null } })
      await loadRuns()
      toast('info', 'Generation started', 'Each row is drafted, audited and repaired in turn. You can leave this page.')
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const bp = blueprints.find((b) => b.blueprint_id === selected)
  const blocked = status && !status.ready
  const outcomes = bp ? new Set(bp.constraints.map((c) => c.course_outcome)).size : 0
  const marks = bp ? bp.constraints.reduce((n, c) => n + c.marks, 0) : 0

  return (
    <>
      <PageHead
        eyebrow="Step 3 of 6"
        title="Generate questions"
        lede="One candidate per blueprint row, written for that row’s course outcome. Nothing reaches a reviewer until it has been through both audits."
      />

      <Explainer id="generate">
        <p>
          A row whose concept the course material does not cover is skipped and named, rather
          than drafted from unrelated passages.
        </p>
        <p>
          Every draft's audit result is kept, including the ones that were repaired, so the
          first-draft compliance rate stays measurable after the fact. A reply the model garbles
          is asked for again; a row that still cannot be drafted is skipped and reported, and the
          rest of the paper is generated anyway.
        </p>
      </Explainer>

      <Card title="Readiness">
        <SystemCheck status={status} error={statusError} onRefresh={checkStatus} busy={checking} />
      </Card>

      <Card title="Start a run">
        {!loaded ? (
          <div className="skeleton" style={{ height: 120 }} />
        ) : blueprints.length === 0 ? (
          <Empty icon="blueprint" title="No frozen blueprint" action={<Btn variant="primary" icon="arrowRight" onClick={() => go('blueprints')}>Go to step 2</Btn>}>
            Generation needs a locked version to audit against. Create a blueprint, validate it and freeze it.
          </Empty>
        ) : (
          <>
            <div className={`pipeline${running ? ' running' : ''}`}>
              {PIPELINE.map(([icon, k, v]) => (
                <div key={k} className="stage">
                  <div className="top"><Icon name={icon} size={17} /><strong>{k}</strong></div>
                  <p>{v}</p>
                </div>
              ))}
            </div>

            <div className="row top">
              <Field label="Blueprint version" hint={bp && `${bp.constraints.length} question${bp.constraints.length === 1 ? '' : 's'} · ${marks} marks · ${outcomes} course outcome${outcomes === 1 ? '' : 's'}`}>
                <select value={selected} onChange={(e) => setSelected(e.target.value)}>
                  {blueprints.map((b) => (
                    <option key={b.blueprint_id} value={b.blueprint_id}>
                      Version {b.version} · {b.constraints.length} question{b.constraints.length === 1 ? '' : 's'}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                grow label="Anything particular you are after? (optional)"
                hint="A preference, not a rule. The blueprint row still decides what passes."
              >
                <textarea
                  rows={2} value={suggestion} maxLength={1000}
                  placeholder="e.g. numerical, on asymptotic analysis — avoid code-reading questions"
                  onChange={(e) => setSuggestion(e.target.value)}
                />
              </Field>
            </div>

            {blocked && (
              <Note tone="bad">
                <strong>Not ready.</strong> {status.checks.filter((c) => c.state === 'bad').map((c) => `${c.label}: ${c.detail}`).join(' ')}
              </Note>
            )}
            {error && <Note tone="bad">{error}</Note>}

            <div className="row">
              <Btn variant="primary" size="lg" icon="play" busy={busy} disabled={!selected || !!running || blocked} onClick={start}>
                {running ? 'A run is in progress' : `Generate ${bp ? bp.constraints.length : ''} question${bp?.constraints.length === 1 ? '' : 's'}`}
              </Btn>
              {blocked && <span className="muted">Fix the red items under Readiness, then check again.</span>}
            </div>
          </>
        )}
      </Card>

      {running && (
        <Card title="Run in progress" actions={<Badge tone="brand" live>running</Badge>}>
          <div className="run-live">
            <div className="big">
              {running.candidate_count}<small> / {running.constraint_count}</small>
            </div>
            <div className="grow stack" style={{ gap: 'var(--s2)' }}>
              <Progress value={running.candidate_count} max={running.constraint_count} live />
              <span className="muted">
                rows drafted and audited · started {new Date(running.started_at).toLocaleTimeString()} on <span className="mono">{running.backend_id}</span>
              </span>
            </div>
          </div>
          <p className="muted">Updates every few seconds. You can leave this page; the run carries on.</p>
        </Card>
      )}

      {runs.length > 0 && (
        <Card title="Runs for this blueprint" pad={false}>
          <Table head={['Started', 'Status', { label: 'Drafted', num: true }, 'Model', 'Reproducibility', { label: '', width: 120 }]}>
            {runs.map((r) => (
              <tr key={r.run_id}>
                <td>{new Date(r.started_at).toLocaleString()}</td>
                <td className="prose">
                  {r.status === 'completed' && !r.error && <Badge tone="ok" dot>completed</Badge>}
                  {r.status === 'completed' && r.error && <Badge tone="warn" dot>completed, rows skipped</Badge>}
                  {r.status === 'running' && <Badge tone="brand" live>running</Badge>}
                  {r.status === 'failed' && <Badge tone="bad" dot>failed</Badge>}
                  {r.error && <div className={r.status === 'failed' ? 'error' : 'muted'} style={{ marginTop: 6 }}>{r.error}</div>}
                </td>
                <td className="num">{r.candidate_count} / {r.constraint_count}</td>
                <td className="muted mono">{r.backend_id}</td>
                <td className="muted" style={{ fontSize: 12.5 }}>
                  temp {r.temperature ?? 'n/a'} · seed {r.seed ?? 'n/a'}<br />
                  <span className="mono">prompt {r.prompt_hash.slice(0, 8)}</span>
                  {r.suggestion && (
                    <div style={{ marginTop: 4 }} title={r.suggestion}>
                      <Badge tone="warn">asked for: {r.suggestion.slice(0, 40)}{r.suggestion.length > 40 ? '…' : ''}</Badge>
                    </div>
                  )}
                </td>
                <td>
                  {r.candidate_count > 0 && r.status !== 'running' && (
                    <Btn variant={r.status === 'completed' ? 'primary' : ''} size="sm" icon="arrowRight" onClick={() => go('review', r.run_id)}>Review</Btn>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
    </>
  )
}
