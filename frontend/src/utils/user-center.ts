import {
  CUSTOM_LLM_LEGACY_STORAGE_KEY,
  CUSTOM_LLM_STORAGE_KEY,
  isCustomLlmOptionValue,
} from './custom-llm'

const DOC_STUDIO_LLM_OPTIONS_KEY = 'doc_studio_llm_options'
const DEEP_CHAT_LLM_MODEL_KEY = 'deep_chat_llm_model'

export const clearCustomModelLocalCache = (): void => {
  if (typeof window === 'undefined') return

  localStorage.removeItem(CUSTOM_LLM_STORAGE_KEY)
  localStorage.removeItem(CUSTOM_LLM_LEGACY_STORAGE_KEY)

  const deepChatModel = String(localStorage.getItem(DEEP_CHAT_LLM_MODEL_KEY) || '').trim()
  if (isCustomLlmOptionValue(deepChatModel)) {
    localStorage.removeItem(DEEP_CHAT_LLM_MODEL_KEY)
  }

  const docStudioLlmOptionsRaw = localStorage.getItem(DOC_STUDIO_LLM_OPTIONS_KEY)
  if (!docStudioLlmOptionsRaw) return

  try {
    const parsed = JSON.parse(docStudioLlmOptionsRaw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      localStorage.removeItem(DOC_STUDIO_LLM_OPTIONS_KEY)
      return
    }
    const next = { ...(parsed as Record<string, unknown>) }
    delete next.llm_custom
    if (isCustomLlmOptionValue(next.llm_model)) {
      delete next.llm_model
      delete next.llm_provider
    }
    localStorage.setItem(DOC_STUDIO_LLM_OPTIONS_KEY, JSON.stringify(next))
  } catch {
    localStorage.removeItem(DOC_STUDIO_LLM_OPTIONS_KEY)
  }
}
