export const CUSTOM_LLM_PROVIDER_TYPE = 'openai_compatible' as const
export const CUSTOM_LLM_OPTION_VALUE = '__custom_openai_compatible__'
export const CUSTOM_LLM_STORAGE_KEY = 'scholarmind_custom_llm_profile_v1'

export type CustomLlmProfile = {
  enabled: boolean
  providerType: typeof CUSTOM_LLM_PROVIDER_TYPE
  providerLabel: string
  baseUrl: string
  apiKey: string
  model: string
  allowFallback: boolean
}

export type CustomLlmPayload = {
  providerType: typeof CUSTOM_LLM_PROVIDER_TYPE
  providerLabel: string
  baseUrl: string
  apiKey: string
  model: string
  allowFallback: boolean
}

const DEFAULT_PROVIDER_LABEL = '自定义模型'

const normalizeTrimmedText = (value: unknown) => String(value || '').trim()

const normalizeBaseUrl = (value: unknown) => {
  const raw = normalizeTrimmedText(value)
  if (!raw) return ''
  try {
    const parsed = new URL(raw)
    if (!/^https?:$/i.test(parsed.protocol)) {
      return ''
    }
    const normalized = `${parsed.protocol}//${parsed.host}${parsed.pathname}`.replace(/\/+$/, '')
    return normalized || ''
  } catch {
    return ''
  }
}

export const normalizeCustomLlmProfile = (value: unknown): CustomLlmProfile | null => {
  if (!value || typeof value !== 'object') return null
  const payload = value as Record<string, unknown>
  const providerTypeRaw = normalizeTrimmedText(payload.providerType || payload.provider_type).toLowerCase()
  const providerType = providerTypeRaw === CUSTOM_LLM_PROVIDER_TYPE ? CUSTOM_LLM_PROVIDER_TYPE : null
  if (!providerType) return null
  const profile: CustomLlmProfile = {
    enabled: Boolean(payload.enabled),
    providerType,
    providerLabel: normalizeTrimmedText(payload.providerLabel || payload.provider_label) || DEFAULT_PROVIDER_LABEL,
    baseUrl: normalizeBaseUrl(payload.baseUrl || payload.base_url),
    apiKey: normalizeTrimmedText(payload.apiKey || payload.api_key),
    model: normalizeTrimmedText(payload.model),
    allowFallback: Boolean(payload.allowFallback || payload.allow_fallback),
  }
  return profile
}

export const isCustomLlmProfileReady = (profile: CustomLlmProfile | null | undefined) =>
  Boolean(
    profile &&
      profile.providerType === CUSTOM_LLM_PROVIDER_TYPE &&
      profile.enabled &&
      profile.baseUrl &&
      profile.apiKey &&
      profile.model,
  )

export const resolveCustomModelOptionLabel = (profile: CustomLlmProfile | null | undefined) => {
  if (!profile) return '自定义模型（OpenAI兼容）'
  const provider = normalizeTrimmedText(profile.providerLabel) || DEFAULT_PROVIDER_LABEL
  const model = normalizeTrimmedText(profile.model)
  if (!model) return `${provider}（未配置）`
  return `${provider} · ${model}`
}

export const toCustomLlmPayload = (
  profile: CustomLlmProfile | null | undefined,
): CustomLlmPayload | undefined => {
  if (!isCustomLlmProfileReady(profile)) return undefined
  return {
    providerType: CUSTOM_LLM_PROVIDER_TYPE,
    providerLabel: normalizeTrimmedText(profile?.providerLabel) || DEFAULT_PROVIDER_LABEL,
    baseUrl: normalizeBaseUrl(profile?.baseUrl),
    apiKey: normalizeTrimmedText(profile?.apiKey),
    model: normalizeTrimmedText(profile?.model),
    allowFallback: Boolean(profile?.allowFallback),
  }
}

export const loadCustomLlmProfile = (storageKey = CUSTOM_LLM_STORAGE_KEY): CustomLlmProfile | null => {
  if (typeof window === 'undefined') return null
  const raw = localStorage.getItem(storageKey)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw)
    return normalizeCustomLlmProfile(parsed)
  } catch {
    return null
  }
}

export const saveCustomLlmProfile = (
  profile: CustomLlmProfile | null,
  storageKey = CUSTOM_LLM_STORAGE_KEY,
) => {
  if (typeof window === 'undefined') return
  if (!profile) {
    localStorage.removeItem(storageKey)
    return
  }
  localStorage.setItem(storageKey, JSON.stringify(profile))
}
