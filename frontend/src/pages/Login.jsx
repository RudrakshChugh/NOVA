import { useEffect, useState } from 'react'
import { api, setToken } from '../api'

const ROLE_HELP = {
  coordinator: 'Uploads the course material and defines the exam blueprint (steps 1–2).',
  reviewer: 'Generates candidate questions, reviews them and publishes accepted ones (steps 3–5).',
}

export default function Login({ onLogin }) {
  const [users, setUsers] = useState([])
  const [error, setError] = useState('')

  useEffect(() => { api('/auth/users').then(setUsers).catch(() => setUsers([])) }, [])

  const login = async (email) => {
    try {
      const { token, user } = await api('/auth/login', { method: 'POST', body: { email } })
      setToken(token)
      onLogin(user)
    } catch (e) { setError(e.message) }
  }

  return (
    <main>
      <h1>Assessment-Quality Platform</h1>
      <p className="muted">Generates exam questions from your own course material, checks every question against the blueprint, and records every reviewer decision.</p>
      <div className="card">
        <h2>Sign in</h2>
        {users.length === 0 ? (
          <form method="post" action="/api/auth/login"><button className="primary">Sign in with institutional SSO</button></form>
        ) : (
          <>
            <p className="muted">No identity provider is configured, so pick one of the seeded users. Each role sees the steps it is responsible for.</p>
            {users.map((u) => (
              <div key={u.user_id} className="row" style={{ margin: '10px 0' }}>
                <button className="primary" onClick={() => login(u.email)}>{u.name}</button>
                <span className="muted"><strong>{u.role}</strong> — {ROLE_HELP[u.role]}</span>
              </div>
            ))}
          </>
        )}
        {error && <p className="error">{error}</p>}
      </div>
    </main>
  )
}
