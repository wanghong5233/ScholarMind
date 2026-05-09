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

// Default: OFF. cancelRepeat is opt-in.
//
// Why not "default ON for mutations to prevent double-click"?
//   Double-click prevention belongs in the UI layer (button disabled /
//   confirmLoading), not in a transport-layer interceptor. Letting the
//   network silently drop one of two clicks creates a worse UX: the
//   button is still enabled, the user thinks nothing happened and clicks
//   again, side effects fire in unexpected order.
//
// Why not "default ON for any method"?
//   Idempotent GETs (list polling, refresh-after-delete) compose with
//   user-triggered refreshes; cancelling them races user actions against
//   background polls. Mutating POSTs may legitimately fan out (e.g.
//   batch upload sends N parallel POST /upload, all sharing method+url).
//
// When IS cancelRepeat appropriate? Narrow cases where the caller
// explicitly knows "I only ever want the latest one to land", e.g.
// search-as-you-type. In that case the caller passes `cancelRepeat: true`
// (and ideally a `repeatKey` so unrelated requests aren't lumped together).
function shouldCancelRepeat(config: AxiosRequestConfig | undefined): boolean {
  return config?.cancelRepeat === true
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
