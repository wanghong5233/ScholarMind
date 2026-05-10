export const CUSTOM_LLM_PROVIDER_TYPE = 'openai_compatible' as const
export const CUSTOM_LLM_OPTION_VALUE = '__custom_openai_compatible__'
export const CUSTOM_LLM_OPTION_PREFIX = '__custom_openai_compatible__:'
export const CUSTOM_LLM_STORAGE_KEY = 'scholarmind_custom_llm_profiles_v2'
export const CUSTOM_LLM_LEGACY_STORAGE_KEY = 'scholarmind_custom_llm_profile_v1'
export const CUSTOM_LLM_PROFILES_UPDATED_EVENT = 'scholarmind:custom-llm-profiles-updated'

export type CustomLlmProfile = {
  id: string
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

type CustomLlmProfilesStore = {
  version: number
  profiles: CustomLlmProfile[]
}

const DEFAULT_PROVIDER_LABEL = '自定义模型'

const normalizeTrimmedText = (value: unknown) => String(value || '').trim()
export const createCustomLlmProfileId = () =>
  `cllm_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`

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
  const profileId = normalizeTrimmedText(payload.id) || createCustomLlmProfileId()
  const providerTypeRaw = normalizeTrimmedText(payload.providerType || payload.provider_type).toLowerCase()
  const providerType = providerTypeRaw === CUSTOM_LLM_PROVIDER_TYPE ? CUSTOM_LLM_PROVIDER_TYPE : null
  if (!providerType) return null
  const profile: CustomLlmProfile = {
    id: profileId,
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

export const normalizeCustomLlmProfiles = (value: unknown): CustomLlmProfile[] => {
  const source = Array.isArray(value)
    ? value
    : value && typeof value === 'object' && Array.isArray((value as Record<string, unknown>).profiles)
      ? ((value as Record<string, unknown>).profiles as unknown[])
      : []
  const profiles = source
    .map((item) => normalizeCustomLlmProfile(item))
    .filter((item): item is CustomLlmProfile => Boolean(item))
  const unique = new Map<string, CustomLlmProfile>()
  profiles.forEach((item) => {
    unique.set(item.id, item)
  })
  return Array.from(unique.values())
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

export const toCustomLlmOptionValue = (profileId: string) =>
  `${CUSTOM_LLM_OPTION_PREFIX}${String(profileId || '').trim()}`

export const isCustomLlmOptionValue = (value: unknown) =>
  typeof value === 'string' &&
  (value === CUSTOM_LLM_OPTION_VALUE || value.startsWith(CUSTOM_LLM_OPTION_PREFIX))

export const parseCustomLlmOptionValue = (value: unknown): string | null => {
  if (typeof value !== 'string') return null
  if (!value.startsWith(CUSTOM_LLM_OPTION_PREFIX)) return null
  const profileId = String(value.slice(CUSTOM_LLM_OPTION_PREFIX.length) || '').trim()
  return profileId || null
}

export const findCustomLlmProfileByOptionValue = (
  profiles: CustomLlmProfile[],
  optionValue: unknown,
): CustomLlmProfile | null => {
  const profileId = parseCustomLlmOptionValue(optionValue)
  if (!profileId) return null
  return profiles.find((item) => item.id === profileId) || null
}

const dispatchProfilesUpdated = (profiles: CustomLlmProfile[]) => {
  if (typeof window === 'undefined') return
  window.dispatchEvent(
    new CustomEvent(CUSTOM_LLM_PROFILES_UPDATED_EVENT, {
      detail: {
        count: profiles.length,
      },
    }),
  )
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

export const loadCustomLlmProfiles = (
  storageKey = CUSTOM_LLM_STORAGE_KEY,
): CustomLlmProfile[] => {
  if (typeof window === 'undefined') return []
  const raw = localStorage.getItem(storageKey)
  if (raw) {
    try {
      const parsed = JSON.parse(raw)
      const profiles = normalizeCustomLlmProfiles(parsed)
      if (profiles.length) return profiles
    } catch {
      // ignore malformed new storage and continue legacy migration
    }
  }
  const legacyRaw = localStorage.getItem(CUSTOM_LLM_LEGACY_STORAGE_KEY)
  if (!legacyRaw) return []
  try {
    const parsed = JSON.parse(legacyRaw)
    const legacyProfile = normalizeCustomLlmProfile({
      ...(parsed as Record<string, unknown>),
      id: 'legacy_default',
    })
    if (!legacyProfile) return []
    return [legacyProfile]
  } catch {
    return []
  }
}

export const saveCustomLlmProfiles = (
  profiles: CustomLlmProfile[],
  storageKey = CUSTOM_LLM_STORAGE_KEY,
) => {
  if (typeof window === 'undefined') return
  const normalized = normalizeCustomLlmProfiles(profiles)
  if (!normalized.length) {
    localStorage.removeItem(storageKey)
    localStorage.removeItem(CUSTOM_LLM_LEGACY_STORAGE_KEY)
    dispatchProfilesUpdated([])
    return
  }
  const payload: CustomLlmProfilesStore = {
    version: 2,
    profiles: normalized,
  }
  localStorage.setItem(storageKey, JSON.stringify(payload))
  localStorage.removeItem(CUSTOM_LLM_LEGACY_STORAGE_KEY)
  dispatchProfilesUpdated(normalized)
}

// Backward-compatible helpers kept for existing callsites.
export const loadCustomLlmProfile = (storageKey = CUSTOM_LLM_STORAGE_KEY): CustomLlmProfile | null =>
  loadCustomLlmProfiles(storageKey)[0] || null

export const saveCustomLlmProfile = (
  profile: CustomLlmProfile | null,
  storageKey = CUSTOM_LLM_STORAGE_KEY,
) => {
  saveCustomLlmProfiles(profile ? [profile] : [], storageKey)
}
