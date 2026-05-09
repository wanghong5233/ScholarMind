import type { NavigateFunction } from 'react-router-dom'

export function buildLoginPath(redirectPath?: string) {
  const fallback =
    typeof window === 'undefined'
      ? '/chat'
      : `${window.location.pathname}${window.location.search || ''}`
  const target = redirectPath || fallback || '/chat'
  const normalizedPath = target.split('?')[0]
  const isLoginLike = normalizedPath === '/login' || normalizedPath === '/admin/login'
  const safeTarget =
    target.startsWith('/') && !target.startsWith('/admin') && !isLoginLike
      ? target
      : '/chat'
  return `/login?redirect=${encodeURIComponent(safeTarget)}`
}

export function requireLogin(
  token: string | null | undefined,
  navigate: NavigateFunction,
  options?: {
    redirectPath?: string
    message?: string
  },
) {
  if (token) return true
  window.$app?.message?.info(options?.message || '请先登录后继续使用')
  navigate(buildLoginPath(options?.redirectPath))
  return false
}
