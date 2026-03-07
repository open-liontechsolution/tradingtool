import { useContext } from 'react'
import { AuthContext } from './AuthContext'

/**
 * Hook to access auth state: { user, isAuthenticated, isLoading, login, logout, accessToken, roles, isAdmin }.
 */
export function useAuth() {
  const ctx = useContext(AuthContext)
  if (ctx === undefined) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return ctx
}
