import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import Candidate from '../components/Candidate'
import { Interval, Verdict } from '../components/Stats'
import { Badge, Btn, Card, Empty, Explainer, Field, Note, PageHead, Progress, Stat, Table, Tabs, useToast } from '../components/ui'

const KIND = { system: { label: 'full system', tone: 'ok' }, baseline: { label: 'baseline', tone: 'neutral' }, ablation: { label: 'ablation', tone: 'warn' } }

const yn = (v) => (v ? <Badge tone="ok">yes</Badge> : <Badge tone="neutral">no</Badge>)

/* ─── arms ───────────────────────────────────────────────────────────────────────────────── */

function Arms({ arms, selected, onToggle, editable }) {
  return (
    <Table head={[
      ...(editable ? [{ label: 'Run', width: 50 }] : []),
      'Arm', 'What it is', 'Retrieval', 'Blueprint stated', 'Compliance enforced', 'Novelty enforced', 'Ordering',
    ]}>
      {Object.entries(arms).map(([label, a]) => (
        <tr key={label}>
          {editable && (
            <td>
              <input
                type="checkbox" checked={selected.includes(label)} onChange={() => onToggle(label)}
                aria-label={`include ${label}`}
              />
            </td>
          )}
          <td>
            <Badge tone={KIND[a.kind]?.tone}>{KIND[a.kind]?.label}</Badge>
            <div className="mono" style={{ marginTop: 3 }}>{label}</div>
          </td>
          <td className="muted" style={{ maxWidth: 320 }}>{a.description}</td>
          <td className="muted">{a.full_material ? 'whole material' : a.retrieval ? 'retrieved passages' : 'none'}</td>
          <td>{yn(a.blueprint_in_prompt)}</td>
          <td>{yn(a.enforce_compliance)}</td>
          <td>{yn(a.enforce_novelty)}</td>
          <td className="muted">{a.rank ? 'ranker' : 'compliance order'}</td>
        </tr>
      ))}
    </Table>
  )
}

/* ─── report ─────────────────────────────────────────────────────────────────────────────── */

function PerArm({ report }) {
  const arms = Object.entries(report.arms)
  return (
    <>
      <Table head={[
        'Arm', { label: 'Reviewed', num: true }, { label: 'Accepted without edits', num: true },
        { label: 'First-draft compliance', num: true }, { label: 'Flagged duplicate', num: true },
        { label: 'Mean edit distance', num: true },
      ]}>
        {arms.map(([label, a]) => (
          <tr key={label}>
            <td>
              <div className="mono">{label}</div>
              <Badge tone={KIND[a.kind]?.tone}>{KIND[a.kind]?.label}</Badge>
            </td>
            <td className="num">{a.acceptance_without_edits.reviewed} / {a.candidates}</td>
            <td className="num"><Interval i={a.acceptance_without_edits.rate} percent /></td>
            <td className="num"><Interval i={a.first_draft_compliance.rate} percent /></td>
            <td className="num"><Interval i={a.novelty.rate} percent /></td>
            <td className="num"><Interval i={a.edit_distance.mean} /></td>
          </tr>
        ))}
      </Table>
      <p className="muted">
        Proportions carry a Wilson score interval, means a bootstrap interval over{' '}
        {report.protocol.bootstrap_resamples.toLocaleString()} resamples. Arms are paired by{' '}
        {report.protocol.pairing}.
      </p>

      <div>
        <h4>Acceptance by seed</h4>
        <p className="muted" style={{ margin: 'var(--s2) 0' }}>
          Generation is stochastic, so each condition runs under several seeds and the spread is shown
          rather than averaged away.
        </p>
        <Table head={['Arm', 'Acceptance per seed']}>
          {arms.map(([label, a]) => (
            <tr key={label}>
              <td className="mono">{label}</td>
              <td>
                {Object.keys(a.acceptance_by_seed).length === 0
                  ? <span className="muted">nothing reviewed yet</span>
                  : <div className="row tight">
                    {Object.entries(a.acceptance_by_seed).map(([seed, rate]) => (
                      <Badge key={seed} tone="outline">seed {seed}: {(rate * 100).toFixed(0)}%</Badge>
                    ))}
                  </div>}
              </td>
            </tr>
          ))}
        </Table>
      </div>
    </>
  )
}

function Headline({ h }) {
  return (
    <>
      <p className="muted">{h.comparison} on acceptance without edits, McNemar's test on paired outcomes.</p>
      {h.paired_questions === 0 ? (
        <Note tone="warn">{h.verdict}</Note>
      ) : (
        <>
          <div className="stats">
            <Stat label="Paired questions" value={h.paired_questions} />
            <Stat label="Difference in acceptance" value={<Interval i={h.mcnemar.difference} percent />} />
            <Stat label="p-value" value={h.mcnemar.p_value.toFixed(4)} hint="exact binomial" />
          </div>
          <p className="muted">
            Discordant pairs: {h.mcnemar.b} favouring the system, {h.mcnemar.c} favouring the baseline.
            Only these carry information — the pairs both arms got right or wrong cancel.
          </p>
          <Verdict text={h.verdict} />
        </>
      )}
    </>
  )
}

function Ranker({ report }) {
  const rows = Object.entries(report.ranker_ablation)
  return (
    <>
      <p className="muted">
        The ranker changes the order candidates are seen in, not which ones survive, so it is judged
        on NDCG@k over reviewer decisions rather than on acceptance.
      </p>
      {rows.length === 0 ? <Empty icon="sort" title="Nothing reviewed yet" /> : (
        <Table head={['Arm', { label: 'Runs scored', num: true }, { label: 'NDCG@k', num: true }]}>
          {rows.map(([label, r]) => (
            <tr key={label}>
              <td>
                <div className="row tight">
                  <span className="mono">{label}</span>
                  {r.order_only_arm && <Badge tone="outline">order only</Badge>}
                </div>
              </td>
              <td className="num">{r.runs}</td>
              <td className="num"><Interval i={r.ndcg} /></td>
            </tr>
          ))}
        </Table>
      )}
    </>
  )
}

function Quality({ report }) {
  const ir = report.inter_rater_agreement
  const g = report.grounding
  return (
    <div className="grid-2">
      <div className="stack">
        <h4>Inter-rater agreement</h4>
        {ir.kappa == null ? (
          <Note tone="warn">{ir.note} ({ir.shared_questions} shared so far)</Note>
        ) : (
          <>
            <Stat label="Cohen's kappa" value={<Interval i={ir.kappa} />} hint={`${ir.shared_questions} questions judged by both reviewers`} tone={ir.weakly_grounded ? 'bad' : 'ok'} />
            <p className={ir.weakly_grounded ? 'error' : 'muted'}>{ir.note}</p>
          </>
        )}
      </div>
      <div className="stack">
        <h4>Grounding</h4>
        <Stat
          label="Quote found verbatim in the cited passage"
          value={<Interval i={g.quote_found_verbatim} percent />}
          hint={`over ${g.citations} citations`}
        />
        <p className="muted">{g.note}</p>
      </div>
    </div>
  )
}

/* ─── page ───────────────────────────────────────────────────────────────────────────────── */

export default function Evaluation({ course, user }) {
  const [arms, setArms] = useState(null)
  const [blueprints, setBlueprints] = useState([])
  const [blueprintId, setBlueprintId] = useState('')
  const [selected, setSelected] = useState([])
  const [seeds, setSeeds] = useState(3)
  const [status, setStatus] = useState(null)
  const [report, setReport] = useState(null)
  const [queue, setQueue] = useState(null)
  const [tab, setTab] = useState('arms')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const toast = useToast()

  useEffect(() => {
    api('/evaluation/arms').then((a) => { setArms(a); setSelected(Object.keys(a)) }).catch((e) => setError(e.message))
    api(`/courses/${course.course_id}/blueprints`).then((bps) => {
      const frozen = bps.filter((b) => b.frozen_at)
      setBlueprints(frozen)
      if (frozen.length) setBlueprintId(frozen[frozen.length - 1].blueprint_id)
    })
  }, [course.course_id])

  const loadStudy = useCallback(async () => {
    if (!blueprintId) return
    const [s, r] = await Promise.all([
      api(`/evaluation/blueprints/${blueprintId}/study`),
      api(`/evaluation/blueprints/${blueprintId}/report`),
    ])
    setStatus(s); setReport(r)
  }, [blueprintId])

  useEffect(() => { loadStudy().catch((e) => setError(e.message)) }, [loadStudy])

  const pending = status && !status.complete && status.total_runs > 0
  useEffect(() => {
    if (!pending) return
    const t = setInterval(() => loadStudy().catch(() => {}), 5000)
    return () => clearInterval(t)
  }, [pending, loadStudy])

  const launch = async () => {
    setBusy('launch'); setError('')
    try {
      await api(`/evaluation/blueprints/${blueprintId}/study`, { method: 'POST', body: { arms: selected, seeds: Number(seeds) } })
      await loadStudy()
      toast('info', 'Study launched', `${selected.length} arm(s) × ${seeds} seed(s) on the same frozen blueprint.`)
    } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const openQueue = async () => {
    setBusy('queue'); setError('')
    try { setQueue(await api(`/evaluation/blueprints/${blueprintId}/queue`)) }
    catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const onReviewed = (updated) => {
    setQueue((qs) => qs.filter((q) => q.question_id !== updated.question_id))
    loadStudy().catch(() => {})
  }

  const toggle = (label) => setSelected(selected.includes(label) ? selected.filter((l) => l !== label) : [...selected, label])
  const isCoordinator = user.role === 'coordinator'
  const completedRuns = status ? Object.values(status.arms).reduce((n, a) => n + a.completed, 0) : 0

  return (
    <>
      <PageHead
        eyebrow="Models &amp; evidence"
        title="Evaluation study"
        lede="The platform against the baselines it claims to beat, and each engine switched off in turn to show what it contributes."
      />

      <Explainer id="evaluation">
        <p>
          Every condition runs on the same frozen blueprint under the same seeds, so candidates pair
          up across arms and the comparison isolates the auditing layers rather than confounding
          them with a change of model or prompt.
        </p>
        <p>
          <strong>Measurement is never switched off.</strong> Every arm's drafts are still checked and
          audited, so each has a compliance rate and a duplicate rate. What an arm changes is whether
          a failure sends the draft back.
        </p>
        <p>
          Reviewers judge through a blinded queue: questions from all arms shuffled together, with
          neither the arm label nor the run id in the response, and an order that is stable per
          reviewer so coming back to it does not reshuffle the work.
        </p>
      </Explainer>

      {error && <Note tone="bad">{error}</Note>}

      <Card
        title="Study"
        hint="Seeds are derived from one committed base seed, so the same question constraint gets the same seed in every arm."
      >
        {blueprints.length === 0 ? (
          <Empty icon="blueprint" title="No frozen blueprint">A study needs a locked version to run every arm against.</Empty>
        ) : (
          <>
            <div className="row top">
              <Field label="Blueprint version">
                <select value={blueprintId} onChange={(e) => setBlueprintId(e.target.value)}>
                  {blueprints.map((b) => (
                    <option key={b.blueprint_id} value={b.blueprint_id}>
                      Version {b.version} · {b.constraints.length} questions
                    </option>
                  ))}
                </select>
              </Field>
              {isCoordinator && (
                <>
                  <Field label="Seeds per arm">
                    <input type="number" min={1} max={10} value={seeds} onChange={(e) => setSeeds(e.target.value)} />
                  </Field>
                  <div style={{ alignSelf: 'flex-end' }}>
                    <Btn variant="primary" icon="play" busy={busy === 'launch'} disabled={!blueprintId || !selected.length} onClick={launch}>
                      Run {selected.length} arm{selected.length === 1 ? '' : 's'} × {seeds} seed{Number(seeds) === 1 ? '' : 's'}
                    </Btn>
                  </div>
                </>
              )}
            </div>

            {status && status.total_runs > 0 ? (
              <>
                <div className="row">
                  {status.complete ? <Badge tone="ok" dot>complete</Badge> : <Badge tone="brand" dot>running</Badge>}
                  <div style={{ flex: 1, minWidth: 140 }}>
                    <Progress value={completedRuns} max={status.total_runs} tone={status.complete ? 'ok' : ''} />
                  </div>
                  <span className="muted">{completedRuns} of {status.total_runs} runs finished</span>
                </div>
                <Table head={['Arm', { label: 'Runs', num: true }, { label: 'Completed', num: true }, { label: 'Candidates', num: true }, 'Seeds']}>
                  {Object.entries(status.arms).map(([label, a]) => (
                    <tr key={label}>
                      <td className="mono">{label}</td>
                      <td className="num">{a.runs}</td>
                      <td className="num">
                        {a.completed}
                        {a.failed > 0 && <span className="error"> · {a.failed} failed</span>}
                      </td>
                      <td className="num">{a.candidates}</td>
                      <td className="muted">{a.seeds.join(', ')}</td>
                    </tr>
                  ))}
                </Table>
              </>
            ) : (
              <Note tone="info">No study has been run on this blueprint yet.</Note>
            )}
          </>
        )}
      </Card>

      {user.role === 'reviewer' && status?.total_runs > 0 && (
        <Card
          title="Blinded review"
          hint="Questions from every arm, shuffled together, with no indication of which system wrote which. Your order is your own and stays the same if you come back to it."
          actions={
            <Btn variant={queue ? '' : 'primary'} icon="eye" busy={busy === 'queue'} onClick={openQueue}>
              {queue ? 'Refresh queue' : 'Open the blinded queue'}
            </Btn>
          }
        >
          {queue && (
            queue.length === 0
              ? <Note tone="ok">Nothing left to judge in your queue.</Note>
              : <>
                <div className="row">
                  <div style={{ flex: 1, minWidth: 140 }}><Progress value={1} max={queue.length + 1} /></div>
                  <span className="muted">{queue.length} question{queue.length === 1 ? '' : 's'} left to judge</span>
                </div>
                <p className="muted">
                  One at a time, so nothing about the surrounding questions can hint at which arm this
                  one came from. <kbd>A</kbd> accept, <kbd>E</kbd> edit, <kbd>R</kbd> reject.
                </p>
              </>
          )}
        </Card>
      )}
      {queue?.length > 0 && (
        <Candidate key={queue[0].question_id} q={queue[0]} canReview blinded shortcuts onReviewed={onReviewed} />
      )}

      {report && (
        <Card
          title="Results"
          hint={report.candidates > 0
            ? `${report.reviewed} of ${report.candidates} candidates judged so far.`
            : 'Nothing generated for this blueprint yet.'}
          actions={isCoordinator && report.candidates > 0 && <Badge tone="outline">coordinator view</Badge>}
        >
          {report.candidates === 0 ? (
            <Empty icon="evaluation" title="No results yet">Launch a study, then judge its questions through the blinded queue.</Empty>
          ) : (
            <>
              <Tabs
                value={tab} onChange={setTab}
                tabs={[
                  { value: 'arms', label: 'Arms', icon: 'evaluation' },
                  { value: 'perarm', label: 'Per arm', icon: 'sort' },
                  { value: 'headline', label: 'Headline comparison', icon: 'target' },
                  { value: 'ranker', label: 'Ranker ablation', icon: 'sort' },
                  { value: 'quality', label: 'Agreement & grounding', icon: 'shield' },
                ]}
              />
              {tab === 'arms' && arms && (
                <>
                  <p className="muted">
                    Each arm is a pinned configuration. Tick the ones a study should run; measurement
                    stays on in all of them.
                  </p>
                  <Arms arms={arms} selected={selected} onToggle={toggle} editable={isCoordinator} />
                </>
              )}
              {tab === 'perarm' && <PerArm report={report} />}
              {tab === 'headline' && <Headline h={report.headline} />}
              {tab === 'ranker' && <Ranker report={report} />}
              {tab === 'quality' && <Quality report={report} />}
            </>
          )}
        </Card>
      )}

      {report && report.candidates === 0 && arms && (
        <Card title="Arms" hint="The seven pinned configurations a study can run.">
          <Arms arms={arms} selected={selected} onToggle={toggle} editable={isCoordinator} />
        </Card>
      )}
    </>
  )
}
