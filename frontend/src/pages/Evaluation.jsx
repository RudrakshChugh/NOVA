import { useEffect, useState } from 'react'
import { api } from '../api'
import Candidate from '../components/Candidate'
import { Interval, Verdict } from '../components/Stats'

const KIND_LABEL = { system: 'full system', baseline: 'baseline', ablation: 'ablation' }

function Arms({ arms, selected, onToggle, editable }) {
  return (
    <table>
      <thead>
        <tr>
          {editable && <th />}
          <th>Arm</th><th>What it is</th><th>Retrieval</th><th>Blueprint stated</th>
          <th>Compliance enforced</th><th>Novelty enforced</th><th>Ranked</th>
        </tr>
      </thead>
      <tbody>
        {Object.entries(arms).map(([label, a]) => (
          <tr key={label}>
            {editable && (
              <td><input type="checkbox" checked={selected.includes(label)} onChange={() => onToggle(label)} /></td>
            )}
            <td>
              <span className={`pill ${a.kind === 'system' ? 'ok' : 'neutral'}`}>{KIND_LABEL[a.kind]}</span><br />
              <span className="mono">{label}</span>
            </td>
            <td className="muted">{a.description}</td>
            <td>{a.full_material ? 'whole material' : a.retrieval ? 'retrieved passages' : 'none'}</td>
            <td>{a.blueprint_in_prompt ? 'yes' : 'no'}</td>
            <td>{a.enforce_compliance ? 'yes' : 'no'}</td>
            <td>{a.enforce_novelty ? 'yes' : 'no'}</td>
            <td>{a.rank ? 'ranker' : 'compliance order'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Report({ report }) {
  const arms = Object.entries(report.arms)
  return (
    <>
      <h4>Per arm</h4>
      <table>
        <thead>
          <tr>
            <th>Arm</th><th>Reviewed</th><th>Accepted without edits</th>
            <th>First-draft compliance</th><th>Flagged duplicate</th><th>Mean edit distance</th>
          </tr>
        </thead>
        <tbody>
          {arms.map(([label, a]) => (
            <tr key={label}>
              <td><span className="mono">{label}</span><br /><span className="muted">{KIND_LABEL[a.kind]}</span></td>
              <td>{a.acceptance_without_edits.reviewed} / {a.candidates}</td>
              <td><Interval i={a.acceptance_without_edits.rate} percent /></td>
              <td><Interval i={a.first_draft_compliance.rate} percent /></td>
              <td><Interval i={a.novelty.rate} percent /></td>
              <td><Interval i={a.edit_distance.mean} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted">
        Proportions carry a Wilson score interval, means a bootstrap interval over{' '}
        {report.protocol.bootstrap_resamples.toLocaleString()} resamples. Arms are paired by{' '}
        {report.protocol.pairing}.
      </p>

      <h4>Acceptance by seed</h4>
      <p className="muted">Generation is stochastic, so each condition is run under several seeds and the spread is shown rather than averaged away.</p>
      <table>
        <thead><tr><th>Arm</th><th>Acceptance per seed</th></tr></thead>
        <tbody>
          {arms.map(([label, a]) => (
            <tr key={label}>
              <td className="mono">{label}</td>
              <td>
                {Object.keys(a.acceptance_by_seed).length === 0
                  ? <span className="muted">nothing reviewed yet</span>
                  : Object.entries(a.acceptance_by_seed).map(([seed, rate]) => (
                    <span key={seed} className="pill neutral">seed {seed}: {(rate * 100).toFixed(0)}%</span>
                  ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h4>Headline comparison</h4>
      <p className="muted">{report.headline.comparison} on acceptance without edits, McNemar's test on paired outcomes.</p>
      {report.headline.paired_questions === 0 ? (
        <p className="notice">{report.headline.verdict}</p>
      ) : (
        <>
          <table>
            <tbody>
              <tr><td>Paired questions</td><td>{report.headline.paired_questions}</td></tr>
              <tr><td>Difference in acceptance</td><td><Interval i={report.headline.mcnemar.difference} percent /></td></tr>
              <tr><td>Discordant pairs</td><td className="muted">{report.headline.mcnemar.b} favouring the system · {report.headline.mcnemar.c} favouring the baseline</td></tr>
              <tr><td>p-value</td><td>{report.headline.mcnemar.p_value.toFixed(4)}</td></tr>
            </tbody>
          </table>
          <p><Verdict text={report.headline.verdict} /></p>
        </>
      )}

      <h4>Ranker ablation</h4>
      <p className="muted">
        The ranker changes the order of candidates, not which survive, so it is judged on NDCG@k over
        reviewer decisions rather than on acceptance.
      </p>
      {Object.keys(report.ranker_ablation).length === 0 ? <p className="muted">Nothing reviewed yet.</p> : (
        <table>
          <thead><tr><th>Arm</th><th>Runs scored</th><th>NDCG@k</th></tr></thead>
          <tbody>
            {Object.entries(report.ranker_ablation).map(([label, r]) => (
              <tr key={label}>
                <td className="mono">{label}{r.order_only_arm && <span className="pill neutral">order only</span>}</td>
                <td>{r.runs}</td><td><Interval i={r.ndcg} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4>Inter-rater agreement</h4>
      {report.inter_rater_agreement.kappa == null ? (
        <p className="muted">{report.inter_rater_agreement.note} ({report.inter_rater_agreement.shared_questions} shared so far)</p>
      ) : (
        <p>
          Cohen's kappa <Interval i={report.inter_rater_agreement.kappa} /> on{' '}
          {report.inter_rater_agreement.shared_questions} questions judged by both reviewers.{' '}
          <span className={report.inter_rater_agreement.weakly_grounded ? 'error' : 'muted'}>
            {report.inter_rater_agreement.note}
          </span>
        </p>
      )}

      <h4>Grounding</h4>
      <p>
        Cited quote found verbatim in the cited passage:{' '}
        <Interval i={report.grounding.quote_found_verbatim} percent /> over {report.grounding.citations} citations.
      </p>
      <p className="muted">{report.grounding.note}</p>
    </>
  )
}

export default function Evaluation({ course, user }) {
  const [arms, setArms] = useState(null)
  const [blueprints, setBlueprints] = useState([])
  const [blueprintId, setBlueprintId] = useState('')
  const [selected, setSelected] = useState([])
  const [seeds, setSeeds] = useState(3)
  const [status, setStatus] = useState(null)
  const [report, setReport] = useState(null)
  const [queue, setQueue] = useState(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    api('/evaluation/arms').then((a) => { setArms(a); setSelected(Object.keys(a)) }).catch((e) => setError(e.message))
    api(`/courses/${course.course_id}/blueprints`).then((bps) => {
      const frozen = bps.filter((b) => b.frozen_at)
      setBlueprints(frozen)
      if (frozen.length) setBlueprintId(frozen[frozen.length - 1].blueprint_id)
    })
  }, [course.course_id])

  const loadStudy = async (id = blueprintId) => {
    if (!id) return
    setStatus(await api(`/evaluation/blueprints/${id}/study`))
    setReport(await api(`/evaluation/blueprints/${id}/report`))
  }
  useEffect(() => { loadStudy().catch((e) => setError(e.message)) }, [blueprintId])
  useEffect(() => {
    if (!status || status.complete || status.total_runs === 0) return
    const t = setInterval(() => loadStudy().catch(() => {}), 5000)
    return () => clearInterval(t)
  }, [status])

  const launch = async () => {
    setBusy('launch'); setError('')
    try {
      await api(`/evaluation/blueprints/${blueprintId}/study`, { method: 'POST', body: { arms: selected, seeds: Number(seeds) } })
      await loadStudy()
    } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const openQueue = async () => {
    setBusy('queue'); setError('')
    try { setQueue(await api(`/evaluation/blueprints/${blueprintId}/queue`)) }
    catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const onReviewed = (updated) => {
    setQueue(queue.filter((q) => q.question_id !== updated.question_id))
    loadStudy().catch(() => {})
  }

  const toggle = (label) => setSelected(selected.includes(label) ? selected.filter((l) => l !== label) : [...selected, label])
  const isCoordinator = user.role === 'coordinator'

  return (
    <>
      <h2>Evaluation</h2>
      <div className="intro">
        <strong>What happens here:</strong> the platform is compared against the baselines it claims
        to beat, and each engine is switched off in turn to show what it contributes. Every
        condition runs on the same frozen blueprint under the same seeds, reviewers judge the
        questions without being told which system wrote them, and every number is reported with the
        interval around it.
      </div>

      <div className="card">
        <h3>Arms</h3>
        <p className="muted">Each arm is a pinned configuration, so the comparison isolates the auditing layers rather than confounding them with a change of model or prompt.</p>
        {arms && <Arms arms={arms} selected={selected} onToggle={toggle} editable={isCoordinator} />}
      </div>

      <div className="card">
        <h3>Study</h3>
        <div className="row">
          <label>Blueprint:&nbsp;
            <select value={blueprintId} onChange={(e) => setBlueprintId(e.target.value)}>
              {blueprints.length === 0 && <option value="">no frozen blueprint</option>}
              {blueprints.map((b) => <option key={b.blueprint_id} value={b.blueprint_id}>Version {b.version} · {b.constraints.length} questions</option>)}
            </select>
          </label>
          {isCoordinator && (
            <>
              <label>Seeds per arm <input type="number" min={1} max={10} style={{ width: 60 }} value={seeds} onChange={(e) => setSeeds(e.target.value)} /></label>
              <button className="primary" disabled={!blueprintId || !selected.length || !!busy} onClick={launch}>
                {busy === 'launch' ? 'Launching…' : `Run ${selected.length} arm(s) × ${seeds} seed(s)`}
              </button>
            </>
          )}
        </div>
        {status && status.total_runs > 0 && (
          <>
            <p>
              {status.complete ? <span className="pill ok">complete</span> : <span className="pill brand">running…</span>}
              <span className="muted"> {status.total_runs} runs</span>
            </p>
            <table>
              <thead><tr><th>Arm</th><th>Runs</th><th>Completed</th><th>Candidates</th><th>Seeds</th></tr></thead>
              <tbody>
                {Object.entries(status.arms).map(([label, a]) => (
                  <tr key={label}>
                    <td className="mono">{label}</td><td>{a.runs}</td>
                    <td>{a.completed}{a.failed > 0 && <span className="error"> · {a.failed} failed</span>}</td>
                    <td>{a.candidates}</td><td className="muted">{a.seeds.join(', ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        {status && status.total_runs === 0 && <p className="muted">No study has been run on this blueprint yet.</p>}
        {error && <p className="error">{error}</p>}
      </div>

      {user.role === 'reviewer' && status?.total_runs > 0 && (
        <div className="card">
          <h3>Blinded review</h3>
          <p className="muted">
            Questions from every arm, shuffled together, with no indication of which system wrote
            which. Your order is your own and stays the same if you come back to it.
          </p>
          <button className="primary" disabled={!!busy} onClick={openQueue}>
            {busy === 'queue' ? 'Loading…' : queue ? 'Refresh queue' : 'Open the blinded queue'}
          </button>
          {queue && <p className="muted" style={{ marginTop: 8 }}>{queue.length} question(s) left to judge.</p>}
        </div>
      )}
      {queue?.map((q) => <Candidate key={q.question_id} q={q} canReview blinded onReviewed={onReviewed} />)}

      {report && report.candidates > 0 && (
        <div className="card">
          <h3>Results</h3>
          <p className="muted">{report.reviewed} of {report.candidates} candidates judged so far.</p>
          <Report report={report} />
        </div>
      )}
    </>
  )
}
