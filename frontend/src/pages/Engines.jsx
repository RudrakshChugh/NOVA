import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import Icon from '../components/Icon'
import { Interval } from '../components/Stats'
import { Badge, Btn, Card, Empty, Explainer, Note, PageHead, Stat, Table, useToast } from '../components/ui'
import { SIGNAL_HELP } from '../format'

const PAIR_FLOOR = 300

const MODEL_LABEL = {
  compliance_only: 'Compliance score only',
  logistic_regression: 'Logistic regression',
  lightgbm: 'LightGBM',
}
const MODEL_ROLE = {
  compliance_only: 'rule-based floor',
  logistic_regression: 'learned floor',
  lightgbm: 'the candidate model',
}

/* A numbered stage inside an engine, with its own state badge so the order of operations and
   where it has got to are both obvious. */
function Stage({ n, title, state, hint, children, action }) {
  const tone = { done: 'ok', blocked: 'bad', ready: 'brand', idle: 'neutral' }[state] || 'neutral'
  const word = { done: 'done', blocked: 'blocked', ready: 'ready', idle: 'not started' }[state]
  return (
    <section className="divide">
      <div className="row top" style={{ marginBottom: 'var(--s2)' }}>
        <div className="subhead" style={{ flex: 1, margin: 0 }}>
          <span className="step-n">{n}</span>
          <h3>{title}</h3>
          <Badge tone={tone} dot>{word}</Badge>
        </div>
        {action}
      </div>
      {hint && <p className="muted" style={{ marginBottom: 'var(--s3)', maxWidth: '82ch' }}>{hint}</p>}
      <div className="stack">{children}</div>
    </section>
  )
}

function ScoreTable({ title, subtitle, result, highlight }) {
  const c = result.confusion
  return (
    <div className="card" style={{ margin: 0, borderColor: highlight ? 'var(--brand-200)' : undefined }}>
      <div className="card-head">
        <div className="ht">
          <h3>{title}</h3>
          <p className="hint">{subtitle}</p>
        </div>
      </div>
      <div className="card-body tight">
        <Table head={['Metric', { label: 'Held-out value', num: true }]}>
          <tr><td>Precision</td><td className="num"><Interval i={result.precision} /></td></tr>
          <tr><td>Recall</td><td className="num"><Interval i={result.recall} /></td></tr>
          <tr><td>F1</td><td className="num"><Interval i={result.f1} /></td></tr>
        </Table>
        <p className="muted" style={{ marginTop: 'var(--s2)' }}>
          {c.tp} true positive · {c.fp} false positive · {c.fn} missed · {c.tn} true negative
        </p>
      </div>
    </div>
  )
}

export default function Engines({ course, user }) {
  const [papers, setPapers] = useState([])
  const [pairs, setPairs] = useState(null)
  const [model, setModel] = useState(null)
  const [ranker, setRanker] = useState(null)
  const [testResult, setTestResult] = useState(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const toast = useToast()

  const refresh = useCallback(async () => {
    const [ps, pr, m, rk] = await Promise.all([
      api(`/courses/${course.course_id}/pyq-papers`), api('/novelty/pairs'),
      api('/novelty/model'), api('/ranker/model'),
    ])
    setPapers(ps); setPairs(pr); setModel(m); setRanker(rk)
  }, [course.course_id])

  useEffect(() => { refresh().catch((e) => setError(e.message)) }, [refresh])

  const run = (name, fn) => async () => {
    setBusy(name); setError('')
    try { await fn(); await refresh() } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const buildPairs = run('pairs', async () => {
    const r = await api(`/courses/${course.course_id}/novelty/pairs`, {
      method: 'POST',
      body: { positives_per_question: 2, hard_negatives_per_question: 1, easy_negatives_per_question: 1 },
    })
    if (r.error) toast('bad', 'Could not build pairs', r.error)
    else toast('ok', `${r.added} pairs added`, `${r.skipped_existing} were already present and kept their fold.`)
  })

  const exportSplit = run('export', async () => {
    const r = await api('/novelty/pairs/export', { method: 'POST' })
    toast('ok', 'Split manifest written', `${r.pairs} pairs → ${r.path}`)
  })

  const train = run('train', async () => {
    const r = await api(`/courses/${course.course_id}/novelty/train`, { method: 'POST' })
    if (r.trained) toast('ok', `Fitted ${r.version}`, 'Selected on the validation fold; the test fold was not touched.')
    else toast('bad', 'Not fitted', r.reason)
  })

  const evaluate = run('test', async () => {
    const r = await api(`/courses/${course.course_id}/novelty/evaluate-test`, { method: 'POST' })
    if (!r.evaluated) return toast('bad', 'Not evaluated', r.reason)
    setTestResult(r)
    toast('info', 'Held-out evaluation complete', `The test fold has now been accessed ${r.test_fold_accesses} time(s).`)
  })

  const trainRanker = run('ranker', async () => {
    const r = await api(`/courses/${course.course_id}/ranker/train`, { method: 'POST' })
    if (r.trained) toast('ok', `Ranker fitted: ${MODEL_LABEL[r.algorithm] || r.algorithm}`, r.metrics?.selection?.lightgbm_beat_both_floors
      ? 'LightGBM beat both floors, so it ships.'
      : 'LightGBM did not beat both floors, so the simpler model ships.')
    else toast('bad', 'Ranker not fitted', r.reason)
  })

  const corpus = papers.reduce((n, p) => n + p.question_count, 0)
  const isCoordinator = user.role === 'coordinator'
  const fitted = model?.is_fitted
  const total = pairs?.total ?? 0
  const accesses = model?.test_evaluations?.length ?? 0
  const cv = ranker?.metrics?.cross_validation || {}
  const selection = ranker?.metrics?.selection

  return (
    <>
      <PageHead
        eyebrow="Models &amp; evidence"
        title="Auditing engines"
        lede="The two engines that judge a draft are models in their own right. This is where they are built, fitted and reported."
      />

      <Explainer id="engines">
        <p>
          This page sits outside the five-step workflow because an engine is set up once per course,
          not once per paper. The order matters: a corpus produces labelled pairs, pairs fit the
          combiner, and only then does a held-out number mean anything.
        </p>
        <p>
          The test fold is deliberately awkward to reach. It lives behind its own action, and every
          access is counted and shown, so a reported figure can state how many times it was really
          looked at.
        </p>
      </Explainer>

      {!isCoordinator && (
        <Note tone="warn" icon="lock">
          Only the course coordinator can build a pair set or fit a model. Everything below is
          visible to you read-only.
        </Note>
      )}
      {error && <Note tone="bad">{error}</Note>}

      {/* ─── novelty ─── */}
      <Card
        title="Novelty engine"
        hint="Four signals — meaning, wording, shape and numbers — are scored for each candidate against every past-year question, and a logistic regression combines them into one duplicate probability. Logistic regression rather than an ensemble, because the weight per signal has to be reportable."
        actions={fitted ? <Badge tone="ok" dot>fitted</Badge> : <Badge tone="warn" dot>placeholder in use</Badge>}
      >
        <Stage
          n="1" title="Past-year corpus" state={corpus === 0 ? 'blocked' : 'done'}
          hint="Questions the engine compares against, added on step 1 of the workflow."
        >
          {corpus === 0 ? (
            <Empty icon="papers" title="No corpus">
              Add at least one past-year paper on step 1. Without it the novelty check cannot run at all.
            </Empty>
          ) : (
            <div className="stats">
              <Stat label="Questions" value={corpus} />
              <Stat label="Papers" value={papers.length} />
              <Stat label="Years" value={new Set(papers.map((p) => p.year)).size} tone={new Set(papers.map((p) => p.year)).size >= 2 ? 'ok' : 'bad'} hint="two or more needed" />
            </div>
          )}
        </Stage>

        <Stage
          n="2" title="Labelled pair set"
          state={total === 0 ? (corpus ? 'ready' : 'idle') : total < PAIR_FLOOR ? 'ready' : 'done'}
          hint="Duplicates are built by transforming a real past question — renumbering it, reordering its conditions, substituting synonyms — so the label is known by construction. Non-duplicates include the nearest other questions, which is what stops the task being solvable by similarity alone. A pair's fold is a fixed function of the pair and the committed seed, so adding pairs never moves an existing one."
          action={isCoordinator && (
            <div className="row tight">
              <Btn variant="primary" icon="plus" busy={busy === 'pairs'} disabled={!!busy || corpus === 0} onClick={buildPairs}>Build pairs</Btn>
              <Btn icon="download" busy={busy === 'export'} disabled={!!busy || !total} onClick={exportSplit}>Export split</Btn>
            </div>
          )}
        >
          {total === 0 ? (
            <Empty icon="sort" title="No pairs built yet">
              Pairs are what the combiner is fitted and judged on.
            </Empty>
          ) : (
            <>
              <div className="stats">
                <Stat label="Pairs" value={total} tone={total >= PAIR_FLOOR ? 'ok' : 'bad'} hint={`plan §5 asks for ${PAIR_FLOOR}+`} />
                <Stat label="Test fold" value={pairs.composition.test?.total ?? 0} hint="20% — reserved" />
                <Stat label="Split seed" value={pairs.seed} hint="committed, never re-drawn" />
              </div>
              <Table head={['Fold', { label: 'Pairs', num: true }, { label: 'Duplicate', num: true }, { label: 'Not duplicate', num: true }, { label: 'Surface', num: true }, { label: 'Hard', num: true }]}>
                {['train', 'val', 'test'].map((fold) => {
                  const row = pairs.composition[fold] || {}
                  return (
                    <tr key={fold}>
                      <td style={{ fontWeight: 550 }}>{fold}</td>
                      <td className="num">{row.total ?? 0}</td>
                      <td className="num">{row.duplicate ?? 0}</td>
                      <td className="num">{row.non_duplicate ?? 0}</td>
                      <td className="num">{row.surface ?? 0}</td>
                      <td className="num">{row.hard ?? 0}</td>
                    </tr>
                  )
                })}
              </Table>
              {total < PAIR_FLOOR && (
                <Note tone="warn">
                  {total} pairs leaves {pairs.composition.test?.total ?? 0} in the test fold. Below
                  roughly 60 the interval on F1 is too wide to separate the engine from a single
                  cosine threshold — the comparison runs, but it does not yet decide anything.
                </Note>
              )}
            </>
          )}
        </Stage>

        <Stage
          n="3" title="Fitted combiner" state={fitted ? 'done' : total ? 'ready' : 'idle'}
          hint="Fitted on the train fold; the regularisation strength and the decision threshold are chosen on the validation fold. The test fold takes no part in either."
          action={isCoordinator && (
            <Btn variant="primary" icon="engines" busy={busy === 'train'} disabled={!!busy || !total} onClick={train}>
              Fit the combiner
            </Btn>
          )}
        >
          {model && (
            <>
              <div className="row">
                {fitted
                  ? <Badge tone="ok" dot>{model.version}</Badge>
                  : <Badge tone="warn" dot>equal-weight placeholder</Badge>}
                {fitted && (
                  <span className="muted">
                    fitted {new Date(model.trained_at).toLocaleString()} · flags at {model.threshold.toFixed(3)} ·
                    C = {model.params?.C}
                  </span>
                )}
              </div>
              <Table head={['Signal', 'What it measures', { label: fitted ? 'Learned weight' : 'Placeholder weight', num: true }]}>
                {Object.entries(model.coefficients).map(([name, w]) => (
                  <tr key={name}>
                    <td className="cap" style={{ fontWeight: 550 }}>{name}</td>
                    <td className="muted">{SIGNAL_HELP[name]}</td>
                    <td className="num">{w.toFixed(3)}</td>
                  </tr>
                ))}
                {fitted && (
                  <tr><td style={{ fontWeight: 550 }}>intercept</td><td className="muted">bias term</td><td className="num">{model.intercept.toFixed(3)}</td></tr>
                )}
              </Table>
              {fitted && (
                <p className="muted">
                  Validation F1 {model.validation?.validation_f1?.toFixed(3)} on {model.validation?.n_validation} pairs,
                  fitted on {model.validation?.n_train}. At small sample sizes every regularisation
                  setting tends to tie on validation, and the grid is ordered so a tie keeps the
                  simplest model — which is why a strong C here means the selection was uninformative
                  rather than that the data demanded it.
                </p>
              )}
            </>
          )}
        </Stage>

        <Stage
          n="4" title="Held-out evaluation" state={accesses ? 'done' : fitted ? 'ready' : 'idle'}
          hint="The test fold is reserved for a single reported evaluation, and plan §3.1 names the method this engine has to beat: one embedding threshold."
          action={isCoordinator && (
            <Btn icon="target" busy={busy === 'test'} disabled={!!busy || !fitted} onClick={evaluate}>
              Evaluate on the test fold
            </Btn>
          )}
        >
          {accesses > 0 && (
            <Note tone={accesses > 1 ? 'warn' : 'info'}>
              The test fold has been accessed <strong>{accesses} time{accesses === 1 ? '' : 's'}</strong>.
              {accesses > 1 && ' Report the first unless you say otherwise — repeated looks turn a held-out number into a selected one.'}
            </Note>
          )}
          {testResult?.model ? (
            <>
              <div className="grid-2">
                <ScoreTable
                  highlight title="Four-signal combiner" subtitle="what this project proposes"
                  result={testResult.model}
                />
                <ScoreTable
                  title="Single embedding threshold"
                  subtitle={`the baseline it must beat, at cosine ≥ ${testResult.single_signal_baseline.threshold}`}
                  result={testResult.single_signal_baseline}
                />
              </div>
              <Note tone="info">
                The comparison <em>is</em> the engine's claim. If both columns separate the data
                perfectly, the pair set is too easy to tell them apart and neither number is evidence
                of anything yet.
              </Note>
            </>
          ) : (
            <p className="muted">No held-out evaluation in this session.</p>
          )}
        </Stage>
      </Card>

      {/* ─── ranker ─── */}
      <Card
        title="Reviewer-adapted ranker"
        hint="Learns from accept, edit and reject records and orders future candidates by predicted acceptance. It is fitted on pre-review features only — nothing derived from the decision can reach it — and compared by leave-one-session-out NDCG@k, holding out one whole review sitting at a time."
        actions={ranker?.is_fitted
          ? <Badge tone="ok" dot>{MODEL_LABEL[ranker.algorithm] || ranker.algorithm}</Badge>
          : <Badge tone="neutral" dot>not fitted</Badge>}
        foot={isCoordinator && (
          <Btn variant="primary" icon="sort" busy={busy === 'ranker'} disabled={!!busy} onClick={trainRanker}>
            Fit and compare the ranker
          </Btn>
        )}
      >
        {!ranker?.is_fitted ? (
          <Empty icon="sort" title="Not fitted yet">
            {ranker?.note || 'Needs at least 20 reviewed candidates across 3 review sittings before a comparison means anything.'}
          </Empty>
        ) : (
          <>
            <div className="stats">
              <Stat label="Shipping" value={MODEL_LABEL[ranker.algorithm] || ranker.algorithm} hint={MODEL_ROLE[ranker.algorithm]} tone="brand" />
              <Stat label="Examples" value={ranker.metrics?.n_examples} hint="reviewed candidates" />
              <Stat label="Sittings" value={ranker.metrics?.n_sessions} hint="reviewer × run" />
              <Stat label="Version" value={`v${ranker.version}`} hint={new Date(ranker.trained_at).toLocaleDateString()} />
            </div>

            {selection && (
              <Note tone={selection.lightgbm_beat_both_floors ? 'ok' : 'info'}>
                <strong>
                  {selection.lightgbm_beat_both_floors
                    ? 'LightGBM beat both floors, so it ships.'
                    : `LightGBM did not beat both floors, so ${MODEL_LABEL[selection.chosen] || selection.chosen} ships instead.`}
                </strong>
                <br />{selection.rule}.
              </Note>
            )}

            <Table head={['Model', 'Role', { label: 'Sittings scored', num: true }, { label: 'NDCG@k (leave-one-out)', num: true }, { label: 'Last sitting only', num: true }]}>
              {Object.entries(cv).map(([name, entry]) => (
                <tr key={name}>
                  <td style={{ fontWeight: 550 }}>
                    <div className="row tight">
                      {MODEL_LABEL[name] || name}
                      {name === ranker.algorithm && <Icon name="check" size={14} style={{ color: 'var(--ok-600)' }} title="ships" />}
                    </div>
                  </td>
                  <td className="muted">{MODEL_ROLE[name]}</td>
                  <td className="num">{entry.sessions}</td>
                  <td className="num"><Interval i={entry.ndcg} /></td>
                  <td className="num">
                    {ranker.metrics?.last_session_ndcg?.[name] == null
                      ? <span className="muted">—</span>
                      : ranker.metrics.last_session_ndcg[name].toFixed(3)}
                  </td>
                </tr>
              ))}
            </Table>
            <p className="muted">
              The last column holds out the chronologically final sitting on its own, so the headline
              number cannot be helped by later decisions leaking into earlier ones.
            </p>
          </>
        )}
      </Card>
    </>
  )
}
