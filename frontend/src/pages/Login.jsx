import { useEffect, useState } from 'react'
import { api, setToken } from '../api'

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
      <h2>Sign in</h2>
      {users.length === 0 ? (
        <form method="post" action="/api/auth/login"><button>Sign in with institutional SSO</button></form>
      ) : (
        <div className="card">
          <p className="muted">Development login (no identity provider configured). Pick a seeded user:</p>
          {users.map((u) => (
            <p key={u.user_id}><button onClick={() => login(u.email)}>{u.name}</button> <span className="muted">{u.role} · {u.email}</span></p>
          ))}
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </main>
  )
}
