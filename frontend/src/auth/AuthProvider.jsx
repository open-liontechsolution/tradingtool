import { createContext, useCallback, useEffect, useRef, useState } from 'react'
import { UserManager, WebStorageStateStore } from 'oidc-client-ts'
import { AUTH_ENABLED, oidcConfig } from './authConfig'
import { setAccessToken } from './apiFetch'

export const AuthContext = createContext(undefined)

// ---------------------------------------------------------------------------
// Mock user for local dev (AUTH_ENABLED=false)
// ---------------------------------------------------------------------------
const MOCK_USER = {
  profile: {
    sub: 'dev-local-user',
    email: 'dev@localhost',
    preferred_username: 'dev',
    resource_access: {
      'tradingtool-api': { roles: ['app_user', 'app_admin'] },
    },
  },
}

function extractRoles(user) {
  try {
    return user?.profile?.resource_access?.['tradingtool-api']?.roles ?? []
  } catch {
    return []
  }
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function AuthProvider({ children }) {
  if (!AUTH_ENABLED) {
    return <MockAuthProvider>{children}</MockAuthProvider>
  }
  return <KeycloakAuthProvider>{children}</KeycloakAuthProvider>
}

// ---------------------------------------------------------------------------
// Mock provider (local dev)
// ---------------------------------------------------------------------------

function MockAuthProvider({ children }) {
  // Keep token ref in sync so apiFetch works even in mock mode
  useEffect(() => {
    setAccessToken('mock-dev-token')
    return () => setAccessToken(null)
  }, [])

  const roles = extractRoles(MOCK_USER)

  const value = {
    user: MOCK_USER,
    isAuthenticated: true,
    isLoading: false,
    accessToken: 'mock-dev-token',
    roles,
    isAdmin: roles.includes('app_admin'),
    login: () => {},
    logout: () => {},
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// ---------------------------------------------------------------------------
// Real Keycloak provider
// ---------------------------------------------------------------------------

function KeycloakAuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const mgr = useRef(null)

  // Initialise UserManager once
  useEffect(() => {
    const userManager = new UserManager({
      ...oidcConfig,
      userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    })
    mgr.current = userManager

    // Keep access token synced
    const onUserLoaded = (u) => {
      setUser(u)
      setAccessToken(u?.access_token ?? null)
    }
    const onUserUnloaded = () => {
      setUser(null)
      setAccessToken(null)
    }

    userManager.events.addUserLoaded(onUserLoaded)
    userManager.events.addUserUnloaded(onUserUnloaded)

    // Attempt to complete a redirect callback, or load existing session
    const init = async () => {
      try {
        // If the URL has ?code= we are returning from Keycloak
        if (window.location.search.includes('code=')) {
          const u = await userManager.signinRedirectCallback()
          // Clean URL
          window.history.replaceState({}, document.title, window.location.pathname)
          setUser(u)
          setAccessToken(u?.access_token ?? null)
        } else {
          // Try to get existing session
          const u = await userManager.getUser()
          if (u && !u.expired) {
            setUser(u)
            setAccessToken(u?.access_token ?? null)
          }
        }
      } catch (err) {
        console.error('[Auth] init error:', err)
      } finally {
        setIsLoading(false)
      }
    }

    init()

    return () => {
      userManager.events.removeUserLoaded(onUserLoaded)
      userManager.events.removeUserUnloaded(onUserUnloaded)
    }
  }, [])

  const login = useCallback(() => {
    mgr.current?.signinRedirect()
  }, [])

  const logout = useCallback(() => {
    mgr.current?.signoutRedirect()
  }, [])

  const roles = extractRoles(user)

  const value = {
    user,
    isAuthenticated: !!user && !user.expired,
    isLoading,
    accessToken: user?.access_token ?? null,
    roles,
    isAdmin: roles.includes('app_admin'),
    login,
    logout,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
