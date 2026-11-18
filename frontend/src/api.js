// In development the Vite proxy forwards /api to the backend. In a deployment the frontend and the
// backend are on different hosts, so the base URL is injected at build time.
export const API_BASE = (import.meta.env.VITE_API_BASE || '/api').replace(/\/$/, '')

// Storage can throw (a private window, a locked-down lab browser). A session that cannot be
// remembered is still a session, so every access is guarded and falls back to memory.
const stored = {
  get: (k) => { try { return localStorage.getItem(k) } catch { return null } },
  set: (k, v) => {
    try {
      if (v) localStorage.setItem(k, v)
      else localStorage.removeItem(k)
    } catch { /* memory only */ }
  },
}

let token = stored.get('token') || ''
let onExpired = null

export function setToken(t) {
  token = t || ''
  stored.set('token', token)
}

export const hasToken = () => !!token

/** Called once by the app: what to do when the server says the session is no longer valid. */
export function onSessionExpired(fn) { onExpired = fn }

export const loginUrl = () => `${API_BASE}/auth/login`

/** The server's `detail` in words. FastAPI sends a string for a raised error, an object for the
 *  ones this app raises with structure, and a list of {loc, msg} for a validation failure —
 *  shown raw, those read as JSON with quotes and brackets in the middle of a sentence. */
export function describeDetail(detail, fallback = 'Request failed') {
  if (detail == null || detail === '') return fallback
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((d) => {
      if (typeof d === 'string') return d
      const where = Array.isArray(d?.loc) ? d.loc.filter((x) => x !== 'body').join(' › ') : ''
      return where ? `${where}: ${d.msg}` : (d?.msg || JSON.stringify(d))
    }).join('; ')
  }
  if (Array.isArray(detail.issues)) return detail.issues.join('; ')
  if (typeof detail.message === 'string') return detail.message
  return JSON.stringify(detail)
}

async function request(path, init) {
  let res
  try {
    res = await fetch(`${API_BASE}${path}`, init)
  } catch {
    // fetch only rejects when no response arrived at all: the server is down, or the network is.
    const err = new Error('Cannot reach the server. Check that the backend is running, then try again.')
    err.status = 0
    err.offline = true
    throw err
  }
  if (res.ok) return res

  let detail = null
  try { detail = (await res.json()).detail } catch { /* not a JSON body */ }
  let message = describeDetail(detail, res.statusText || `Error ${res.status}`)
  // A 502/504 from the dev proxy means the same thing as a failed fetch, with a worse message.
  if ([502, 503, 504].includes(res.status) && !detail) {
    message = 'The backend is not responding. Check that it is running, then try again.'
  }
  const err = new Error(message)
  // Carried so the interface can tell "you have no role yet" apart from "you may not do this".
  err.status = res.status
  err.detail = detail
  // A session the server no longer accepts. Signing out here, once, beats every screen showing
  // its own permission error until the user works out they need to sign in again. The login
  // call itself is exempt: a 401 there is a wrong answer, not an expired session.
  if (res.status === 401 && token && !path.startsWith('/auth/login')) {
    setToken('')
    onExpired?.()
  }
  throw err
}

const authHeaders = () => (token ? { Authorization: `Bearer ${token}` } : {})

export async function download(path, filename) {
  // The API needs the bearer token, and an <a href> cannot carry one, so the bytes are
  // fetched and handed to the browser as a blob.
  const res = await request(path, { headers: authHeaders() })
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
  const headers = authHeaders()
  if (body) headers['Content-Type'] = 'application/json'
  const res = await request(path, { method, headers, body: form || (body ? JSON.stringify(body) : undefined) })
  return res.status === 204 ? null : res.json()
}
