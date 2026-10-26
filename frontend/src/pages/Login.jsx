import { useEffect, useState } from 'react'
import { api, setToken } from '../api'
import Icon from '../components/Icon'
import { Btn, Note } from '../components/ui'

const ROLE_HELP = {
  coordinator: 'Uploads course material and past papers, defines and freezes the blueprint, fits the engines.',
  reviewer: 'Generates candidates, reviews them against the checks, publishes the accepted ones.',
}

const PITCH = [
  ['shield', 'Blueprint compliance', 'Six deterministic checks per draft. Failures are named by field and sent back to the model — it never grades itself.'],
  ['target', 'Novelty against past papers', 'Four independent signals, combined by a fitted model whose weight per signal is reported.'],
  ['sort', 'Reviewer-adapted ranking', 'Learns from accept, edit and reject decisions — and ships only if it beats two floors.'],
  ['evaluation', 'Evidence, not claims', 'Pinned baselines, blinded review and an interval on every number.'],
]

export default function Login({ onLogin }) {
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    api('/auth/users').then(setUsers).catch(() => setUsers([])).finally(() => setLoading(false))
  }, [])

  const login = async (email) => {
    setPending(email); setError('')
    try {
      const { token, user } = await api('/auth/login', { method: 'POST', body: { email } })
      setToken(token)
      onLogin(user)
    } catch (e) { setError(e.message); setPending('') }
  }

  return (
    <div className="login">
      <div className="login-pitch">
        <div className="brand-mark">AQ</div>
        <h1>Exam questions that are checked before anyone reads them</h1>
        <p>
          Blueprint-compliant, source-grounded question generation with compliance auditing,
          novelty detection against your own past papers, and a reviewer-adapted ranker.
        </p>
        <ul>
          {PITCH.map(([icon, title, body]) => (
            <li key={title}>
              <Icon name={icon} size={19} />
              <span><strong>{title}</strong>{body}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="login-form">
        <div className="inner">
          <h2 style={{ fontSize: 22 }}>Sign in</h2>

          {loading ? (
            <div className="stack" style={{ marginTop: 'var(--s5)' }}>
              <div className="skeleton" style={{ height: 54 }} />
              <div className="skeleton" style={{ height: 54 }} />
            </div>
          ) : users.length === 0 ? (
            <>
              <p className="muted" style={{ margin: 'var(--s2) 0 var(--s5)' }}>
                This deployment is connected to your institution's identity provider.
              </p>
              <form method="post" action="/api/auth/login">
                <Btn variant="primary" size="lg" icon="lock" style={{ width: '100%' }}>
                  Continue with institutional SSO
                </Btn>
              </form>
            </>
          ) : (
            <>
              <p className="muted" style={{ margin: 'var(--s2) 0 var(--s5)' }}>
                No identity provider is configured, so pick a seeded user. Each role sees the part of
                the workflow it is responsible for.
              </p>
              <div className="stack">
                {users.map((u) => (
                  <button key={u.user_id} className="user-pick" onClick={() => login(u.email)} disabled={!!pending}>
                    <span className="avatar">{u.name.split(/\s+/).map((w) => w[0]).slice(0, 2).join('')}</span>
                    <span className="who-text">
                      <strong>{u.name}</strong>
                      <span>{ROLE_HELP[u.role]}</span>
                    </span>
                    {pending === u.email
                      ? <span className="spinner" />
                      : <Icon name="arrowRight" size={16} className="muted" />}
                  </button>
                ))}
              </div>
            </>
          )}

          {error && <div style={{ marginTop: 'var(--s4)' }}><Note tone="bad">{error}</Note></div>}
        </div>
      </div>
    </div>
  )
}
