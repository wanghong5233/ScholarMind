import { AxiosRequestConfig } from 'axios'
import { IRequestPlugin } from './plugin'

const abortControllerMap = new WeakMap<AbortSignal, AbortController>()

export function createAbortController() {
  const controller = new AbortController()
  abortControllerMap.set(controller.signal, controller)
  return controller
}

const map = new Map<string, AxiosRequestConfig>()

function set(config: AxiosRequestConfig) {
  const key = getRepeatKey(config)
  const signal = map.get(key)?.signal as AbortSignal
  if (signal) {
    abortControllerMap.get(signal)?.abort('取消重复请求')
  }
  map.set(key, config)
}
export function remove(config: AxiosRequestConfig) {
  const key = getRepeatKey(config)
  map.delete(key)
}

export function getRepeatKey(config: AxiosRequestConfig) {
  return `${config.method}-${config.url}-${config.repeatKey ?? ''}`
}

// Default rule:
//   - Only mutations (POST/PUT/PATCH/DELETE) opt into "cancel previous
//     in-flight identical request". This prevents double-click submits
//     from causing duplicate side effects.
//   - GET/HEAD are idempotent. Auto-cancelling them is dangerous: it
//     races background polling against user-triggered refresh. E.g.
//     deleting a row triggers `refreshDocuments()`; if a polling GET
//     for the same list is in flight, the refresh is aborted as a
//     "duplicate" and the table appears not to update.
// Callers can still opt in/out explicitly via `cancelRepeat: true/false`
// (see axios-extend.d.ts).
function shouldCancelRepeat(config: AxiosRequestConfig | undefined): boolean {
  if (config?.cancelRepeat === true) return true
  if (config?.cancelRepeat === false) return false
  const method = (config?.method ?? 'get').toLowerCase()
  return method !== 'get' && method !== 'head'
}

export const repeatPlugin: IRequestPlugin = {
  preinstall(instance) {
    instance.interceptors.response.use((response) => {
      const config = response.config as AxiosRequestConfig
      if (!shouldCancelRepeat(config)) return response

      remove(config)

      return response
    })
  },

  postinstall(instance) {
    instance.interceptors.request.use((config) => {
      if (!shouldCancelRepeat(config)) return config

      config.signal = config.signal ?? createAbortController().signal
      set(config)

      return config
    })
  },
}
