import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { Badge, Btn, Card, Empty, Explainer, Field, Note, PageHead, Stat, Table, useToast } from '../components/ui'

const COURSE_TONE = { coordinator: 'brand', reviewer: 'ok' }
const PLATFORM_TONE = { admin: 'brand', member: 'ok', pending: 'warn' }

const COURSE_HELP = {
  coordinator: 'Uploads sources, freezes blueprints, fits the engines and launches studies on this subject.',
  reviewer: 'Generates candidates, records accept / edit / reject decisions, publishes.',
}

const PLATFORM_HELP = {
  admin: 'Creates subjects, names their coordinators, and admits people to the platform.',
  member: 'Admitted. What they may do is decided subject by subject.',
  pending: 'Signed in, but cannot see or do anything yet.',
}

/* ─── one subject ────────────────────────────────────────────────────────────────────────── */

function SubjectMembers({ course, user }) {
  const [members, setMembers] = useState([])
  const [directory, setDirectory] = useState([])
  const [pick, setPick] = useState({ user_id: '', role: 'reviewer' })
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const toast = useToast()

  const canGrant = course.your_role === 'coordinator'

  const load = useCallback(() => (
    Promise.all([
      api(`/courses/${course.course_id}/members`),
      canGrant ? api('/auth/directory') : Promise.resolve([]),
    ])
      .then(([m, d]) => { setMembers(m); setDirectory(d) })
      .catch((e) => setError(e.message))
  ), [course.course_id, canGrant])
  useEffect(() => { load() }, [load])

  const grant = async (userId, role, name) => {
    setBusy(userId); setError('')
    try {
      await api(`/courses/${course.course_id}/members`, { method: 'POST', body: { user_id: userId, role } })
      await load()
      setPick({ user_id: '', role: 'reviewer' })
      toast('ok', `${name} is now ${role} on ${course.code}`, COURSE_HELP[role])
    } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const revoke = async (userId, name) => {
    setBusy(userId); setError('')
    try {
      await api(`/courses/${course.course_id}/members/${userId}`, { method: 'DELETE' })
      await load()
      toast('info', `${name} removed from ${course.code}`, 'Decisions they already recorded are kept.')
    } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const addable = directory.filter((d) => !members.some((m) => m.user_id === d.user_id))

  return (
    <Card
      title={`People on ${course.code}`}
      hint="A role is held on one subject. Somebody who coordinates this subject has no standing on any other."
      pad={false}
    >
      {error && <div className="card-body"><Note tone="bad">{error}</Note></div>}

      {members.length === 0 ? (
        <Empty icon="shield" title="Nobody is on this subject yet" />
      ) : (
        <Table head={['Person', 'Role on this subject', 'How they got it', { label: '', width: 250 }]}>
          {members.map((m) => {
            const self = m.user_id === user.user_id
            return (
              <tr key={m.user_id}>
                <td>
                  <div style={{ fontWeight: 550 }}>{m.name}{self && <span className="muted"> (you)</span>}</div>
                  <div className="muted">{m.email}</div>
                </td>
                <td><Badge tone={COURSE_TONE[m.role]} dot>{m.role}</Badge></td>
                <td className="muted prose">
                  {m.reason}
                  <div style={{ fontSize: 12 }}>{new Date(m.granted_at).toLocaleString()}</div>
                </td>
                <td>
                  {!canGrant ? null : self ? (
                    <span className="muted">You cannot change your own role here.</span>
                  ) : (
                    <div className="row tight">
                      {m.role !== 'reviewer' && (
                        <Btn size="sm" variant="ok" busy={busy === m.user_id}
                             onClick={() => grant(m.user_id, 'reviewer', m.name)}>Make reviewer</Btn>
                      )}
                      {m.role !== 'coordinator' && (
                        <Btn size="sm" busy={busy === m.user_id}
                             onClick={() => grant(m.user_id, 'coordinator', m.name)}>Make coordinator</Btn>
                      )}
                      <Btn size="sm" variant="bad" busy={busy === m.user_id}
                           onClick={() => revoke(m.user_id, m.name)}>Remove</Btn>
                    </div>
                  )}
                </td>
              </tr>
            )
          })}
        </Table>
      )}

      {canGrant && (
        <div className="card-body">
          {addable.length === 0 ? (
            <p className="muted">
              Everyone admitted to the platform is already on this subject. An administrator admits
              new people on the card below.
            </p>
          ) : (
            <div className="row top">
              <Field grow label="Add somebody to this subject">
                <select value={pick.user_id} onChange={(e) => setPick({ ...pick, user_id: e.target.value })}>
                  <option value="">Choose a person…</option>
                  {addable.map((d) => <option key={d.user_id} value={d.user_id}>{d.name} — {d.email}</option>)}
                </select>
              </Field>
              <Field label="As">
                <select value={pick.role} onChange={(e) => setPick({ ...pick, role: e.target.value })}>
                  <option value="reviewer">reviewer</option>
                  <option value="coordinator">coordinator</option>
                </select>
              </Field>
              <div style={{ alignSelf: 'flex-end' }}>
                <Btn
                  variant="primary" icon="plus" disabled={!pick.user_id} busy={busy === pick.user_id}
                  onClick={() => grant(pick.user_id, pick.role,
                    addable.find((d) => d.user_id === pick.user_id)?.name || 'They')}
                >
                  Add
                </Btn>
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

/* ─── the platform ───────────────────────────────────────────────────────────────────────── */

function PlatformPeople({ user }) {
  const [people, setPeople] = useState([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const toast = useToast()

  const load = useCallback(() => (
    api('/auth/people').then(setPeople).catch((e) => setError(e.message)).finally(() => setLoading(false))
  ), [])
  useEffect(() => { load() }, [load])

  const setRole = async (target, role) => {
    setBusy(target.user_id); setError('')
    try {
      await api(`/auth/people/${target.user_id}/role`, { method: 'POST', body: { role } })
      await load()
      toast('ok', `${target.name} is now ${role}`, role === 'pending'
        ? 'Their access is withdrawn; decisions they already recorded are kept.'
        : PLATFORM_HELP[role])
    } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const waiting = people.filter((p) => p.role === 'pending')

  return (
    <>
      {waiting.length > 0 && (
        <Note tone="warn">
          <strong>{waiting.length} {waiting.length === 1 ? 'person is' : 'people are'} waiting to be admitted.</strong>
          {' '}They can sign in but cannot do anything yet.
        </Note>
      )}
      {error && <Note tone="bad">{error}</Note>}

      <div className="stats" style={{ marginBottom: 'var(--s4)' }}>
        <Stat label="Administrators" value={people.filter((p) => p.role === 'admin').length} />
        <Stat label="Admitted" value={people.filter((p) => p.role === 'member').length} hint="can be put on a subject" />
        <Stat label="Awaiting admission" value={waiting.length} tone={waiting.length ? 'brand' : ''} />
      </div>

      <Card
        title="Everyone who has signed in"
        hint="Platform standing only. Being admitted says nothing about which subjects somebody may open."
        pad={false}
      >
        {loading ? (
          <div className="card-body"><div className="skeleton" style={{ height: 80 }} /></div>
        ) : people.length === 0 ? (
          <Empty icon="shield" title="Nobody has signed in yet" />
        ) : (
          <Table head={['Person', 'Standing', 'How they got it', { label: '', width: 260 }]}>
            {people.map((p) => {
              const self = p.user_id === user.user_id
              return (
                <tr key={p.user_id}>
                  <td>
                    <div style={{ fontWeight: 550 }}>{p.name}{self && <span className="muted"> (you)</span>}</div>
                    <div className="muted">{p.email}</div>
                  </td>
                  <td><Badge tone={PLATFORM_TONE[p.role]} dot>{p.role}</Badge></td>
                  <td className="muted prose">
                    {p.role_set_at ? (
                      <>
                        {p.role_set_by
                          ? <>Set by <strong>{p.role_set_by}</strong></>
                          : <>Set automatically — {p.role_reason}</>}
                        <div style={{ fontSize: 12 }}>{new Date(p.role_set_at).toLocaleString()}</div>
                      </>
                    ) : (
                      <span title="This account predates role auditing">no record</span>
                    )}
                  </td>
                  <td>
                    {self ? (
                      <span className="muted">You cannot change your own standing.</span>
                    ) : (
                      <div className="row tight">
                        {p.role !== 'member' && (
                          <Btn size="sm" variant="ok" busy={busy === p.user_id} onClick={() => setRole(p, 'member')}>
                            Admit
                          </Btn>
                        )}
                        {p.role !== 'admin' && (
                          <Btn size="sm" busy={busy === p.user_id} onClick={() => setRole(p, 'admin')}>
                            Make administrator
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

/* ─── page ───────────────────────────────────────────────────────────────────────────────── */

export default function People({ course, user }) {
  const isAdmin = user.role === 'admin'

  return (
    <>
      <PageHead
        eyebrow="Administration"
        title="People and roles"
        lede="Anyone with an institute account can sign in. Being admitted and being on a subject are two separate acts."
      />

      <Explainer id="people">
        <p>
          A first sign-in creates an account with no standing at all. An administrator admits it to
          the platform; the coordinator of a subject then adds it to that subject, as a reviewer or
          as a second coordinator. Until both have happened, the account can authenticate and
          nothing else.
        </p>
        <p>
          Two gates rather than one, because they answer different questions. Admission asks whether
          somebody belongs on this platform. Membership asks which subject's material, drafts and
          question bank they may see — and a coordinator of one subject is deliberately a stranger
          to every other.
        </p>
        <p>
          That second gate matters more than it looks: review decisions are the ranker's training
          data and the input to the inter-rater agreement figure, so a decision recorded by someone
          outside the study is not a harmless extra row — it changes a reported number.
        </p>
      </Explainer>

      <SubjectMembers course={course} user={user} />

      {isAdmin && <PlatformPeople user={user} />}
    </>
  )
}
