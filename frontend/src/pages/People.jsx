import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { Badge, Btn, Card, Empty, Explainer, Note, PageHead, Stat, Table, useToast } from '../components/ui'

const ROLE_TONE = { coordinator: 'brand', reviewer: 'ok', pending: 'warn' }

const ROLE_HELP = {
  coordinator: 'Uploads sources, freezes blueprints, fits the engines, launches studies.',
  reviewer: 'Generates candidates, records accept / edit / reject decisions, publishes.',
  pending: 'Signed in, but cannot see or do anything yet.',
}

export default function People({ user }) {
  const [people, setPeople] = useState([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const toast = useToast()

  const load = useCallback(() => api('/auth/people').then(setPeople).catch((e) => setError(e.message)).finally(() => setLoading(false)), [])
  useEffect(() => { load() }, [load])

  const setRole = async (target, role) => {
    setBusy(target.user_id); setError('')
    try {
      await api(`/auth/people/${target.user_id}/role`, { method: 'POST', body: { role } })
      await load()
      toast('ok', `${target.name} is now ${role}`, role === 'pending'
        ? 'Their access is withdrawn; decisions they already recorded are kept.'
        : ROLE_HELP[role])
    } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const waiting = people.filter((p) => p.role === 'pending')

  return (
    <>
      <PageHead
        eyebrow="Administration"
        title="People and roles"
        lede="Anyone with an institute account can sign in. What they may do is granted here."
      />

      <Explainer id="people">
        <p>
          A first sign-in creates an account with no role. Until a coordinator grants one, that
          account can authenticate but cannot read material, generate, review or publish.
        </p>
        <p>
          That gate matters more than it looks: review decisions are the ranker's training data and
          the input to the inter-rater agreement figure, so a decision recorded by someone outside
          the study is not a harmless extra row — it changes a reported number.
        </p>
      </Explainer>

      {waiting.length > 0 && (
        <Note tone="warn">
          <strong>{waiting.length} {waiting.length === 1 ? 'person is' : 'people are'} waiting for a role.</strong>
          {' '}They can sign in but cannot do anything yet.
        </Note>
      )}
      {error && <Note tone="bad">{error}</Note>}

      <div className="stats" style={{ marginBottom: 'var(--s4)' }}>
        <Stat label="Coordinators" value={people.filter((p) => p.role === 'coordinator').length} />
        <Stat label="Reviewers" value={people.filter((p) => p.role === 'reviewer').length} hint="can record decisions" />
        <Stat label="Awaiting a role" value={waiting.length} tone={waiting.length ? 'brand' : ''} />
      </div>

      <Card title="Everyone who has signed in" pad={false}>
        {loading ? (
          <div className="card-body"><div className="skeleton" style={{ height: 80 }} /></div>
        ) : people.length === 0 ? (
          <Empty icon="shield" title="Nobody has signed in yet" />
        ) : (
          <Table head={['Person', 'Role', 'What they can do', { label: '', width: 260 }]}>
            {people.map((p) => {
              const self = p.user_id === user.user_id
              return (
                <tr key={p.user_id}>
                  <td>
                    <div style={{ fontWeight: 550 }}>{p.name}{self && <span className="muted"> (you)</span>}</div>
                    <div className="muted">{p.email}</div>
                  </td>
                  <td><Badge tone={ROLE_TONE[p.role]} dot>{p.role}</Badge></td>
                  <td className="muted">{ROLE_HELP[p.role]}</td>
                  <td>
                    {self ? (
                      <span className="muted">You cannot change your own role.</span>
                    ) : (
                      <div className="row tight">
                        {p.role !== 'reviewer' && (
                          <Btn size="sm" variant="ok" busy={busy === p.user_id} onClick={() => setRole(p, 'reviewer')}>
                            Grant reviewer
                          </Btn>
                        )}
                        {p.role !== 'coordinator' && (
                          <Btn size="sm" busy={busy === p.user_id} onClick={() => setRole(p, 'coordinator')}>
                            Make coordinator
                          </Btn>
                        )}
                        {p.role !== 'pending' && (
                          <Btn size="sm" variant="bad" busy={busy === p.user_id} onClick={() => setRole(p, 'pending')}>
                            Revoke
                          </Btn>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              )
            })}
          </Table>
        )}
      </Card>
    </>
  )
}
