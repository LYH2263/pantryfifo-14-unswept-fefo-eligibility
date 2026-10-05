export async function api(path, opts = {}) {
  const r = await fetch('/api' + path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  })
  if (!r.ok) {
    let body = null
    let detail = r.statusText
    try { body = await r.json(); detail = body.detail || body } catch {}
    const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
    err.status = r.status
    err.body = body
    throw err
  }
  if (r.status === 204) return null
  return r.json()
}

// Fired after any mutation (consume / sweep / inbound) so fridge, layer
// pages and the alert banner re-sync instead of showing stale quantities.
export function pantryChanged() {
  window.dispatchEvent(new Event('pantry:changed'))
}

export function onPantryChanged(fn) {
  window.addEventListener('pantry:changed', fn)
  return () => window.removeEventListener('pantry:changed', fn)
}
