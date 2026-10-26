import { useEffect, useState } from 'react'
import { api } from '../api'
import { Interval } from '../components/Stats'
import { SIGNAL_HELP } from '../format'

function ScoreTable({ title, result }) {
  const c = result.confusion
  return (
    <div>
      <h4>{title}</h4>
      <table>
        <tbody>
          <tr><td>Precision</td><td><Interval i={result.precision} /></td></tr>
          <tr><td>Recall</td><td><Interval i={result.recall} /></td></tr>
          <tr><td>F1</td><td><Interval i={result.f1} /></td></tr>
          <tr><td>Confusion</td><td className="muted">{c.tp} true positive · {c.fp} false positive · {c.fn} missed · {c.tn} true negative</td></tr>
        </tbody>
      </table>
    </div>
  )
}

export default function Engines({ course, user }) {
  const [papers, setPapers] = useState([])
  const [pairs, setPairs] = useState(null)
  const [model, setModel] = useState(null)
  const [testResult, setTestResult] = useState(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const refresh = async () => {
    setPapers(await api(`/courses/${course.course_id}/pyq-papers`))
    setPairs(await api('/novelty/pairs'))
    setModel(await api('/novelty/model'))
  }
  useEffect(() => { refresh().catch((e) => setError(e.message)) }, [course.course_id])

  const run = (name, fn) => async () => {
    setBusy(name); setError(''); setMessage('')
    try {
      const result = await fn()
      await refresh()
      return result
    } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const buildPairs = run('pairs', async () => {
    const r = await api(`/courses/${course.course_id}/novelty/pairs`, {
      method: 'POST',
      body: { positives_per_question: 2, hard_negatives_per_question: 1, easy_negatives_per_question: 1 },
    })
    setMessage(r.error ? r.error : `${r.added} new pairs added (${r.skipped_existing} already present).`)
  })

  const exportSplit = run('export', async () => {
    const r = await api('/novelty/pairs/export', { method: 'POST' })
    setMessage(`Split manifest written to ${r.path} (${r.pairs} pairs).`)
  })

  const train = run('train', async () => {
    const r = await api(`/courses/${course.course_id}/novelty/train`, { method: 'POST' })
    setMessage(r.trained ? `Fitted ${r.version}.` : `Not trained: ${r.reason}`)
  })

  const evaluate = run('test', async () => {
    const r = await api(`/courses/${course.course_id}/novelty/evaluate-test`, { method: 'POST' })
    if (!r.evaluated) return setMessage(`Not evaluated: ${r.reason}`)
    setTestResult(r)
    setMessage(`Test fold accessed ${r.test_fold_accesses} time(s).`)
  })

  const corpusSize = papers.reduce((n, p) => n + p.question_count, 0)
  const isCoordinator = user.role === 'coordinator'
  const fitted = model?.is_fitted

  return (
    <>
      <h2>Engines</h2>
      <div className="intro">
        <strong>What happens here:</strong> the auditing engines are models in their own right, and
        this page is where they are built and reported. It is separate from the five-step journey
        because it is set up once per course rather than once per paper.
      </div>
      {!isCoordinator && <p className="notice">Only the course coordinator can build the pair set or fit a model. Everything below is visible to you read-only.</p>}

      <div className="card">
        <h3>Novelty engine</h3>
        <p className="muted">
          Four signals — meaning, wording, shape and numbers — are scored for a candidate against
          every past-year question, and a logistic regression combines them into one duplicate
          probability. Logistic regression is used because its weight per signal is directly
          reportable, which a learned ensemble would not be.
        </p>

        <h4>1. Past-year corpus</h4>
        <p>
          {corpusSize === 0
            ? <span className="pill bad">empty</span>
            : <span className="pill ok">{corpusSize} questions across {papers.length} paper(s)</span>}
          <span className="muted"> Add papers on step 1.</span>
        </p>

        <h4>2. Labelled pair set</h4>
        <p className="muted">
          Duplicates are built by transforming a real past question — renumbering it, reordering its
          conditions, substituting synonyms — so the label is known by construction. Non-duplicates
          include the most semantically similar other questions, which is what stops the task from
          being solvable by similarity alone. Each pair's fold is a fixed function of the pair and
          the committed seed, so adding pairs never moves an existing one.
        </p>
        {pairs && pairs.total > 0 ? (
          <table>
            <thead><tr><th>Fold</th><th>Pairs</th><th>Duplicate</th><th>Not duplicate</th><th>Surface</th><th>Hard</th></tr></thead>
            <tbody>
              {['train', 'val', 'test'].map((fold) => {
                const row = pairs.composition[fold]
                return (
                  <tr key={fold}>
                    <td>{fold}</td>
                    <td>{row?.total ?? 0}</td><td>{row?.duplicate ?? 0}</td><td>{row?.non_duplicate ?? 0}</td>
                    <td>{row?.surface ?? 0}</td><td>{row?.hard ?? 0}</td>
                  </tr>
                )
              })}
              <tr><td><strong>total</strong></td><td><strong>{pairs.total}</strong></td><td colSpan={4} className="muted">seed {pairs.seed} · plan §5 asks for at least 300</td></tr>
            </tbody>
          </table>
        ) : <p className="muted">No pairs built yet.</p>}
        {isCoordinator && (
          <p className="row">
            <button className="primary" disabled={!!busy || corpusSize === 0} onClick={buildPairs}>{busy === 'pairs' ? 'Building…' : 'Build pairs from the corpus'}</button>
            <button disabled={!!busy || !pairs?.total} onClick={exportSplit}>{busy === 'export' ? 'Writing…' : 'Export split manifest'}</button>
          </p>
        )}

        <h4>3. Fitted combiner</h4>
        {model && (
          <>
            <p>
              {fitted
                ? <span className="pill ok">{model.version}</span>
                : <span className="pill bad">not fitted — using the equal-weight placeholder</span>}
              {fitted && <span className="muted"> fitted {new Date(model.trained_at).toLocaleString()} · flags at {model.threshold.toFixed(2)}</span>}
            </p>
            <table>
              <thead><tr><th>Signal</th><th>What it measures</th><th>{fitted ? 'Learned weight' : 'Placeholder weight'}</th></tr></thead>
              <tbody>
                {Object.entries(model.coefficients).map(([name, w]) => (
                  <tr key={name}>
                    <td style={{ textTransform: 'capitalize' }}>{name}</td>
                    <td className="muted">{SIGNAL_HELP[name]}</td>
                    <td>{w.toFixed(3)}</td>
                  </tr>
                ))}
                {fitted && <tr><td>intercept</td><td className="muted">bias term</td><td>{model.intercept.toFixed(3)}</td></tr>}
              </tbody>
            </table>
            {fitted && (
              <p className="muted">
                Regularisation C = {model.params?.C} chosen on the validation fold, along with the
                threshold. Validation F1 {model.validation?.validation_f1?.toFixed(3)} on{' '}
                {model.validation?.n_validation} pairs, fitted on {model.validation?.n_train}.
              </p>
            )}
          </>
        )}
        {isCoordinator && (
          <p><button className="primary" disabled={!!busy || !pairs?.total} onClick={train}>{busy === 'train' ? 'Fitting…' : 'Fit the combiner (train + validation folds)'}</button></p>
        )}

        <h4>4. Held-out evaluation</h4>
        <p className="muted">
          The test fold is reserved for a single reported evaluation. Every access is recorded, so a
          report can state how many times it actually happened.
        </p>
        {model?.test_evaluations?.length > 0 && (
          <p className={model.test_evaluations.length > 1 ? 'notice' : 'muted'}>
            Test fold accessed {model.test_evaluations.length} time(s).
            {model.test_evaluations.length > 1 && ' Report the first unless you state otherwise.'}
          </p>
        )}
        {isCoordinator && (
          <p><button disabled={!!busy || !fitted} onClick={evaluate}>{busy === 'test' ? 'Evaluating…' : 'Evaluate on the test fold'}</button></p>
        )}
        {testResult?.model && (
          <div className="row" style={{ alignItems: 'flex-start', gap: 24 }}>
            <ScoreTable title="Four-signal combiner" result={testResult.model} />
            <ScoreTable title={`Single embedding threshold (${testResult.single_signal_baseline.threshold})`} result={testResult.single_signal_baseline} />
          </div>
        )}
        {testResult?.model && (
          <p className="muted">
            The baseline on the right is the method plan §3.1 says is insufficient: one cosine
            threshold. The comparison is the engine's claim.
          </p>
        )}

        {message && <p className="notice">{message}</p>}
        {error && <p className="error">{error}</p>}
      </div>

      <div className="card">
        <h3>Reviewer-adapted ranker</h3>
        <p className="muted">
          Not yet fitted. It learns from accept / edit / reject records and orders future candidates
          by predicted acceptance, and ships only if it beats both a logistic-regression floor and a
          compliance-score-only floor on held-out review sessions.
        </p>
      </div>
    </>
  )
}
