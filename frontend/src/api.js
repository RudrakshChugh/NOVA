// In development the Vite proxy forwards /api to the backend. In a deployment the frontend and the
// backend are on different hosts, so the base URL is injected at build time.
export const API_BASE = (import.meta.env.VITE_API_BASE || '/api').replace(/\/$/, '')

let token = localStorage.getItem('token') || ''

export function setToken(t) {
  token = t
  if (t) localStorage.setItem('token', t)
  else localStorage.removeItem('token')
}

export const hasToken = () => !!token

export const loginUrl = () => `${API_BASE}/auth/login`

export async function download(path, filename) {
  // The API needs the bearer token, and an <a href> cannot carry one, so the bytes are
  // fetched and handed to the browser as a blob.
  const res = await fetch(`${API_BASE}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
  if (!res.ok) throw new Error(`${res.status}: ${res.statusText}`)
  const url = URL.createObjectURL(await res.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

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
