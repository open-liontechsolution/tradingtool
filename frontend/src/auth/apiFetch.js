/**
 * Thin wrapper around fetch() that injects the Authorization header
 * when an access token is available.
 *
 * Usage (drop-in replacement for fetch):
 *   import { apiFetch } from '../auth/apiFetch'
 *   const res = await apiFetch('/api/signals/configs')
 */

// Module-level token reference, updated by AuthProvider.
let _accessToken = null

/** Called by AuthProvider to keep the token in sync. */
export function setAccessToken(token) {
  _accessToken = token
}

/**
 * fetch() wrapper that adds Authorization: Bearer <token>.
 * Signature matches window.fetch so it's a drop-in replacement.
 */
export function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {})
  if (_accessToken) {
    headers.set('Authorization', `Bearer ${_accessToken}`)
  }
  return fetch(url, { ...options, headers })
}
