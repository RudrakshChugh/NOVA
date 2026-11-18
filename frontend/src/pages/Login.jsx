import { useEffect, useState } from 'react'
import { api, loginUrl, setToken } from '../api'
import Icon from '../components/Icon'
import { Btn, Note } from '../components/ui'

const ROLE_HELP = {
  coordinator: 'Uploads course material and past papers, defines and freezes the blueprint, fits the engines.',
  reviewer: 'Generates candidates, reviews them against the checks, publishes the accepted ones.',
  pending: 'Signed in, but not yet granted a role by a coordinator.',
  // Platform standings. The seeded accounts carry these rather than a per-subject role, so
  // without them the sign-in cards showed a name and nothing about what it can do.
  admin: 'Administrator and coordinator: uploads sources, freezes the blueprint, admits people.',
  member: 'Reviewer: generates candidates, reviews them against the checks, publishes.',
}

// Why a sign-in was refused, in the user's terms. The backend sends a short code rather than a
// message so the wording lives with the interface.
const SIGN_IN_ERROR = {
  domain: 'That account is not an institute account. Sign in with your @thapar.edu address.',
  unverified: 'Google reports that address as unverified, so it cannot be used to sign in.',
  cancelled: 'Sign-in was cancelled.',
  state: 'That sign-in link had expired. Please try again.',
  exchange: 'Could not complete sign-in with Google. Please try again.',
}

const PITCH = [
  ['shield', 'Aligned to the outcome', 'Six deterministic checks per draft against its blueprint row. The model never grades itself.'],
  ['sparkle', 'New, not recycled', 'Scored against past papers, the bank and the rest of the paper on four independent signals.'],
  ['sort', 'Ranked by your reviewers', 'Learns from accept, edit and reject — and ships only if it beats two floors.'],
  ['evaluation', 'Evidence, not claims', 'Pinned baselines, blinded review and an interval on every number.'],
]

export default function Login({ onLogin }) {
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState('')
  const [error, setError] = useState('')

  // The callback hands the browser back with the session token in the URL fragment. It is read
  // once and wiped from the address bar so it is not left in history or copied with the link.
  useEffect(() => {
    const params = new URLSearchParams(window.location.hash.slice(1))
    const token = params.get('token')
    const failed = params.get('error')
    if (token || failed) history.replaceState(null, '', window.location.pathname)
    if (failed) { setError(SIGN_IN_ERROR[failed] || 'Sign-in failed.'); setLoading(false); return }
    if (token) {
      setToken(token)
      api('/auth/me').then(onLogin).catch((e) => { setToken(''); setError(e.message) }).finally(() => setLoading(false))
      return
    }
    // No identity provider configured means the seeded development users are offered instead.
    api('/auth/users').then(setUsers).catch(() => setUsers([])).finally(() => setLoading(false))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const devLogin = async (email) => {
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
        <span className="wordmark big">NOVA</span>
        <h1>Exam questions, <em>audited</em> before anyone reads them.</h1>
        <p>
          Source-grounded questions written to your blueprint, checked against every course
          outcome and compared with everything you have set before.
        </p>
        <ul>
          {PITCH.map(([icon, title, body]) => (
            <li key={title}>
              <Icon name={icon} size={20} />
              <strong>{title}</strong>{body}
            </li>
          ))}
        </ul>
      </div>

      <div className="login-form">
        <div className="inner">
          <h2>Sign in</h2>

          {loading ? (
            <div className="stack" style={{ marginTop: 'var(--s5)' }}>
              <div className="skeleton" style={{ height: 54 }} />
              <div className="skeleton" style={{ height: 54 }} />
            </div>
          ) : users.length === 0 ? (
            <>
              <p className="muted" style={{ margin: 'var(--s2) 0 var(--s5)' }}>
                Use your institute Google account. Anyone can open this site, but only
                <strong> @thapar.edu </strong> accounts can sign in, and a coordinator grants the role.
              </p>
              <Btn variant="primary" size="lg" icon="lock" style={{ width: '100%' }} onClick={() => { window.location.href = loginUrl() }}>
                Continue with your Thapar account
              </Btn>
            </>
          ) : (
            <>
              <p className="muted" style={{ margin: 'var(--s2) 0 var(--s5)' }}>
                Development sign-in: no identity provider is configured, so choose a seeded
                account. Each role sees the part of the workflow it is responsible for.
              </p>
              <div className="stack">
                {users.map((u) => (
                  <button key={u.user_id} className="user-pick" onClick={() => devLogin(u.email)} disabled={!!pending}>
                    <span className="avatar">{u.name.split(/\s+/).map((w) => w[0]).slice(0, 2).join('')}</span>
                    <span className="who-text">
                      <strong>{u.name}</strong>
                      <span>{ROLE_HELP[u.role]}</span>
                    </span>
                    {pending === u.email
                      ? <span className="spinner" />
                      : <Icon name="arrowRight" size={16} />}
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
