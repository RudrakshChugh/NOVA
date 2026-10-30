// In development the Vite proxy forwards /api to the backend. In a deployment the frontend and the
// backend are on different hosts, so the base URL is injected at build time.
export const API_BASE = (import.meta.env.VITE_API_BASE || '/api').replace(/\/$/, '')

let token = localStorage.getItem('token') || ''

export function setToken(t) {
  token = t
  if (t) localStorage.setItem('token', t)
  else localStorage.removeItem('token')
}

export const loginUrl = () => `${API_BASE}/auth/login`

export async function api(path, { method = 'GET', body, form } = {}) {
  const headers = token ? { Authorization: `Bearer ${token}` } : {}
  if (body) headers['Content-Type'] = 'application/json'
  const res = await fetch(`${API_BASE}${path}`, { method, headers, body: form || (body ? JSON.stringify(body) : undefined) })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = JSON.stringify((await res.json()).detail) } catch { /* no JSON body */ }
    const err = new Error(`${res.status}: ${detail}`)
    // Carried so the interface can tell "you have no role yet" apart from "you may not do this".
    err.status = res.status
    err.detail = detail
    throw err
  }
  return res.status === 204 ? null : res.json()
}
