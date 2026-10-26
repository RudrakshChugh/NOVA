import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import Icon from '../components/Icon'
import { Badge, Btn, Card, Empty, Explainer, Field, Note, PageHead, Progress, Table, useToast } from '../components/ui'

const PIPELINE = [
  ['target', 'Retrieve', 'The five passages most relevant to the row’s concept are pulled from the indexed material.'],
  ['generate', 'Draft', 'The model writes one question, an answer key and citations, using only those passages.'],
  ['shield', 'Audit', 'A rule-based checker tests the draft against every field of the row, then the novelty engine scores it against the past papers.'],
  ['refresh', 'Repair', 'A failure goes back to the model with the exact violations named — up to two repairs, then it is shown as-is.'],
]

export default function Generate({ course, user, go }) {
  const [blueprints, setBlueprints] = useState([])
  const [selected, setSelected] = useState('')
  const [runs, setRuns] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()

  useEffect(() => {
    api(`/courses/${course.course_id}/blueprints`).then((bps) => {
      const frozen = bps.filter((b) => b.frozen_at)
      setBlueprints(frozen)
      if (frozen.length) setSelected(frozen[frozen.length - 1].blueprint_id)
    })
  }, [course.course_id])

  const loadRuns = useCallback(() => {
    if (!selected) return Promise.resolve()
    return api(`/blueprints/${selected}/runs`).then(setRuns)
  }, [selected])

  useEffect(() => { loadRuns() }, [loadRuns])

  const running = runs.find((r) => r.status === 'running')
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => loadRuns().catch(() => {}), 4000)
    return () => clearInterval(t)
  }, [running, loadRuns])

  const start = async () => {
    setBusy(true); setError('')
    try {
      await api(`/blueprints/${selected}/generate`, { method: 'POST' })
      await loadRuns()
      toast('info', 'Generation started', 'Each question is drafted, audited and repaired in turn — roughly a minute each on a local model.')
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const bp = blueprints.find((b) => b.blueprint_id === selected)

  return (
    <>
      <PageHead
        eyebrow="Step 3 of 5"
        title="Generate questions"
        lede="One candidate per blueprint row. Nothing reaches a reviewer until it has been through both audits."
      />

      <Explainer id="generate">
        <p>For every row of the frozen blueprint the system does four things in order:</p>
        <ol>
          {PIPELINE.map(([, k, v]) => <li key={k}><strong>{k}.</strong> {v}</li>)}
        </ol>
        <p>
          Every draft's audit result is kept, including the ones that were repaired, so the
          first-draft compliance rate stays measurable after the fact.
        </p>
      </Explainer>

      <Card title="Start a run" hint="Runs are tied to a frozen blueprint version, so a comparison is never confounded by an edited specification.">
        {blueprints.length === 0 ? (
          <Empty icon="blueprint" title="No frozen blueprint" action={<Btn variant="primary" onClick={() => go('blueprints')}>Go to step 2</Btn>}>
            Generation needs a locked version to audit against. Create a blueprint, validate it and freeze it.
          </Empty>
        ) : (
          <>
            <div className="row top">
              <Field label="Blueprint version">
                <select value={selected} onChange={(e) => setSelected(e.target.value)}>
                  {blueprints.map((b) => (
                    <option key={b.blueprint_id} value={b.blueprint_id}>
                      Version {b.version} · {b.constraints.length} question{b.constraints.length === 1 ? '' : 's'}
                    </option>
                  ))}
                </select>
              </Field>
              <div style={{ alignSelf: 'flex-end' }}>
                {user.role === 'reviewer'
                  ? <Btn variant="primary" icon="play" busy={busy} disabled={!selected || !!running} onClick={start}>
                    {running ? 'A run is in progress' : 'Start generation run'}
                  </Btn>
                  : <Note tone="warn" icon="lock">Only a faculty reviewer can start a run.</Note>}
              </div>
            </div>

            <div className="stats">
              {PIPELINE.map(([icon, k, v]) => (
                <div key={k} className="stat">
                  <div className="row tight" style={{ color: 'var(--brand-600)' }}>
                    <Icon name={icon} size={16} />
                    <span className="stat-label" style={{ color: 'var(--brand-700)' }}>{k}</span>
                  </div>
                  <div className="stat-hint" style={{ marginTop: 6 }}>{v}</div>
                </div>
              ))}
            </div>

            {bp && (
              <p className="muted">
                This run will produce {bp.constraints.length} candidate{bp.constraints.length === 1 ? '' : 's'},
                one per blueprint row.
              </p>
            )}
            {error && <Note tone="bad">{error}</Note>}
          </>
        )}
      </Card>

      {running && (
        <Card title="In progress" actions={<Badge tone="brand" dot>running</Badge>}>
          <div className="row">
            <strong>{running.candidate_count} of {running.constraint_count}</strong>
            <span className="muted">drafted and audited</span>
          </div>
          <Progress value={running.candidate_count} max={running.constraint_count} />
          <p className="muted">
            Started {new Date(running.started_at).toLocaleTimeString()} on {running.backend_id}. This page
            refreshes itself every few seconds; you can leave and come back.
          </p>
        </Card>
      )}

      {runs.length > 0 && (
        <Card title="Runs for this blueprint" pad={false}>
          <Table head={['Started', 'Status', 'Drafted', 'Model', 'Reproducibility', { label: '', width: 110 }]}>
            {runs.map((r) => (
              <tr key={r.run_id}>
                <td>{new Date(r.started_at).toLocaleString()}</td>
                <td>
                  {r.status === 'completed' && <Badge tone="ok" dot>completed</Badge>}
                  {r.status === 'running' && <Badge tone="brand" dot>running</Badge>}
                  {r.status === 'failed' && <Badge tone="bad" dot>failed</Badge>}
                  {r.error && <div className="error" style={{ marginTop: 4 }}>{r.error}</div>}
                </td>
                <td className="num">{r.candidate_count} / {r.constraint_count}</td>
                <td className="muted mono">{r.backend_id}</td>
                <td className="muted" style={{ fontSize: 12 }}>
                  temp {r.temperature ?? 'n/a'} · seed {r.seed ?? 'n/a'}<br />
                  <span className="mono">prompt {r.prompt_hash.slice(0, 8)}</span>
                </td>
                <td>
                  {r.status === 'completed' && (
                    <Btn variant="primary" size="sm" icon="arrowRight" onClick={() => go('review', r.run_id)}>Review</Btn>
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
