import { Fragment, useEffect, useState } from 'react'
import { api } from '../api'
import { Badge, Btn, Card, Empty, Explainer, Note, PageHead, Stat } from '../components/ui'
import { FIELD_LABEL, optionText, pageLabel } from '../format'

function Provenance({ s }) {
  return (
    <div className="grid-2">
      <div className="stack">
        <div>
          <h4>Blueprint row</h4>
          <dl className="kv" style={{ marginTop: 'var(--s2)' }}>
            <dt>Version</dt><dd>v{s.blueprint.version}, frozen {new Date(s.blueprint.frozen_at).toLocaleString()}</dd>
            <dt>Course outcome</dt><dd>{s.constraint.course_outcome}</dd>
            <dt>Concept</dt><dd>{s.constraint.concept}</dd>
            <dt>Bloom / marks / type</dt>
            <dd className="cap">{s.constraint.bloom_level} · {s.constraint.marks} · {s.constraint.question_type.replace('_', ' ')}</dd>
          </dl>
        </div>
        <div>
          <h4>Generation run</h4>
          <dl className="kv" style={{ marginTop: 'var(--s2)' }}>
            <dt>Model</dt><dd className="mono">{s.run.backend_id}</dd>
            <dt>Started</dt><dd>{new Date(s.run.started_at).toLocaleString()}</dd>
            <dt>Temperature / seed</dt><dd>{s.run.temperature ?? 'n/a'} / {s.run.seed ?? 'n/a'}</dd>
            <dt>Prompt fingerprint</dt><dd className="mono">{s.run.prompt_hash.slice(0, 16)}…</dd>
          </dl>
        </div>
        <div>
          <h4>Review</h4>
          <dl className="kv" style={{ marginTop: 'var(--s2)' }}>
            <dt>Decision</dt><dd>{s.review.decision} by {s.review.reviewer}</dd>
            <dt>When</dt><dd>{new Date(s.review.reviewed_at).toLocaleString()}</dd>
            {s.review.edit_distance != null && <><dt>Edit distance</dt><dd>{s.review.edit_distance.toFixed(2)}</dd></>}
            {s.review.decision === 'edit' && <><dt>Original wording</dt><dd>{s.original_stem}</dd></>}
          </dl>
        </div>
      </div>

      <div className="stack">
        <div>
          <h4>Blueprint checks at publication</h4>
          <p className="muted" style={{ marginTop: 'var(--s2)' }}>
            {s.compliance.is_compliant
              ? `Passed every check after ${s.compliance.repair_attempts} repair(s).`
              : `${s.compliance.violations.length} check(s) still failing after ${s.compliance.repair_attempts} repair(s).`}
          </p>
          {s.compliance.violations.map((v) => (
            <p key={v.field} className="error">
              {FIELD_LABEL[v.field] || v.field}: expected {v.expected}; found {v.observed}
            </p>
          ))}
        </div>
        <div>
          <h4>Grounded in</h4>
          <dl className="kv" style={{ marginTop: 'var(--s2)' }}>
            {s.citations.map((c, i) => (
              <Fragment key={i}>
                <dt>{c.material}</dt>
                <dd>{pageLabel(c.page_ref)} · support {c.support_score.toFixed(2)}</dd>
              </Fragment>
            ))}
          </dl>
        </div>
        <div>
          <h4>Answer key</h4>
          <div className="keyline" style={{ marginTop: 'var(--s2)' }}>{s.answer_key}</div>
        </div>
      </div>
    </div>
  )
}

export default function Bank({ go }) {
  const [entries, setEntries] = useState([])
  const [open, setOpen] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => { api('/bank/entries').then(setEntries).finally(() => setLoading(false)) }, [])

  const marks = entries.reduce((n, e) => n + (e.snapshot.constraint.marks || 0), 0)
  const edited = entries.filter((e) => e.snapshot.review.decision === 'edit').length

  return (
    <>
      <PageHead
        eyebrow="Step 5 of 5"
        title="Question bank"
        lede="Published questions, each with a version number and the complete record of where it came from."
      />

      <Explainer id="bank">
        <p>
          Every entry keeps which blueprint version and row asked for it, which model run wrote it,
          which passages it cited, how it fared on the checks, and who approved it. Nothing here can
          be changed in place — re-publishing creates a new version and supersedes the old one.
        </p>
      </Explainer>

      {loading ? (
        <Card><div className="skeleton" style={{ height: 90 }} /></Card>
      ) : entries.length === 0 ? (
        <Card>
          <Empty icon="bank" title="Nothing published yet" action={<Btn variant="primary" onClick={() => go('review')}>Go to step 4</Btn>}>
            Accept or edit candidates in the review step, then publish them. They arrive here with
            their provenance attached.
          </Empty>
        </Card>
      ) : (
        <>
          <div className="stats" style={{ marginBottom: 'var(--s4)' }}>
            <Stat label="Published questions" value={entries.length} />
            <Stat label="Total marks" value={marks} />
            <Stat label="Published as written" value={entries.length - edited} hint={`${edited} needed an edit`} />
          </div>

          {entries.map((e) => {
            const s = e.snapshot
            const isOpen = open === e.entry_id
            return (
              <Card
                key={e.entry_id}
                actions={
                  <Btn variant="link" onClick={() => setOpen(isOpen ? null : e.entry_id)}>
                    {isOpen ? 'Hide provenance' : 'Show provenance'}
                  </Btn>
                }
                title={
                  <span className="row tight" style={{ fontWeight: 400 }}>
                    <Badge tone="solid">v{e.version}</Badge>
                    <Badge tone="neutral">{s.constraint.bloom_level}</Badge>
                    <Badge tone="neutral">{s.constraint.marks} marks</Badge>
                    <Badge tone="neutral">{s.constraint.question_type.replace('_', ' ')}</Badge>
                    {s.compliance.is_compliant
                      ? <Badge tone="ok" dot>compliant</Badge>
                      : <Badge tone="bad" dot>{s.compliance.violations.length} failing</Badge>}
                    <span className="muted">published {new Date(e.published_at).toLocaleString()}</span>
                  </span>
                }
              >
                <p className="cand-stem">{s.stem}</p>
                {s.options && <ol className="cand-options" type="A">{s.options.map((o) => <li key={o}>{optionText(o)}</li>)}</ol>}
                {!s.compliance.is_compliant && (
                  <Note tone="warn">
                    This question was published with failing checks. The reviewer's decision overrides
                    the audit, and the failure stays on the record.
                  </Note>
                )}
                {isOpen && <div className="divide"><Provenance s={s} /></div>}
              </Card>
            )
          })}
        </>
      )}
    </>
  )
}
