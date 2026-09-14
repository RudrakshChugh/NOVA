let token = localStorage.getItem('token') || ''

export function setToken(t) {
  token = t
  localStorage.setItem('token', t)
}

export async function api(path, { method = 'GET', body, form } = {}) {
  const headers = token ? { Authorization: `Bearer ${token}` } : {}
  if (body) headers['Content-Type'] = 'application/json'
  const res = await fetch(`/api${path}`, { method, headers, body: form || (body ? JSON.stringify(body) : undefined) })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = JSON.stringify((await res.json()).detail) } catch {}
    throw new Error(`${res.status}: ${detail}`)
  }
  return res.status === 204 ? null : res.json()
}
