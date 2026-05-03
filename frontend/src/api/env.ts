/**
 * 统一 API 地址解析，保证本地开发与演示部署行为一致。
 * 演示部署时前端在 Vercel（demo 域名），API 在后端（api 域名），
 * 必须使用绝对地址，否则相对路径会打到 Vercel 导致 405 / 404。
 */

function trimTrailingSlash(s: string): string {
  return s.replace(/\/+$/, '')
}

function isLocalBackendBase(value: string): boolean {
  return /^https?:\/\/(localhost|127\.0\.0\.1):8000\/api\/?$/i.test(value)
}

/**
 * 主 API base（用于 request、getDocumentPreviewUrl 等）。
 * 生产环境必须通过 VITE_API_BASE 配置为绝对地址，否则会打到前端托管域名。
 */
export function getApiBase(): string {
  const v = (import.meta.env.VITE_API_BASE as string | undefined)?.trim()
  if (import.meta.env.DEV && (!v || isLocalBackendBase(v))) return '/api'
  return trimTrailingSlash(v || '/api')
}

/**
 * Doc Studio API base。若未显式配置，则从 VITE_API_BASE 派生。
 */
export function getDocStudioBase(): string {
  const explicit = (import.meta.env.VITE_DOC_STUDIO_BASE as string | undefined)?.trim()
  if (explicit) return trimTrailingSlash(explicit)
  if (import.meta.env.DEV) return '/api/doc-studio'
  const apiBase = (import.meta.env.VITE_API_BASE as string | undefined)?.trim()
  if (apiBase && /^https?:\/\//i.test(apiBase)) {
    return `${trimTrailingSlash(apiBase)}/doc-studio`
  }
  return '/api/doc-studio'
}

/**
 * Deep Research API base。若未显式配置，则从 VITE_API_BASE 派生。
 */
export function getDeepResearchBase(): string {
  const explicit = (import.meta.env.VITE_DEEP_RESEARCH_BASE as string | undefined)?.trim()
  if (explicit) return trimTrailingSlash(explicit)
  if (import.meta.env.DEV) return '/api/deep-research'
  const apiBase = (import.meta.env.VITE_API_BASE as string | undefined)?.trim()
  if (apiBase && /^https?:\/\//i.test(apiBase)) {
    return `${trimTrailingSlash(apiBase)}/deep-research`
  }
  return '/api/deep-research'
}
