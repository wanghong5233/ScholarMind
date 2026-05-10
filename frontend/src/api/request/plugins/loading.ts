import { AxiosRequestConfig } from 'axios'
import { IRequestPlugin } from './plugin'

function show(title?: string) {
  const normalizedTitle = String(title || '').trim() || '加载中...'
  window.$showLoading({
    title: normalizedTitle,
  })
}
function hide() {
  window.$hideLoading()
}

// Default UX rule:
//   - Mutations (POST/PUT/PATCH/DELETE) the user actively triggers should
//     block the UI so the user knows their action is in flight.
//   - GET/HEAD requests are passive data fetches (lists, polls, search)
//     and must NEVER flash a full-screen overlay; doing so makes any
//     background refresh visible as a flicker / fake "page hang".
// Callers can still opt in or out explicitly via `loading: true/false`
// in the AxiosRequestConfig (see axios-extend.d.ts).
function shouldShowLoading(config: AxiosRequestConfig | undefined): boolean {
  if (config?.loading === true) return true
  if (config?.loading === false) return false
  const method = (config?.method ?? 'get').toLowerCase()
  return method !== 'get' && method !== 'head'
}

export const loadingPlugin: IRequestPlugin = {
  preinstall(instance) {
    instance.interceptors.response.use(
      (response) => {
        if (shouldShowLoading(response.config as AxiosRequestConfig)) hide()
        return response
      },
      (error) => {
        const config = (error.response?.config ?? error?.config) as
          | AxiosRequestConfig
          | undefined
        if (shouldShowLoading(config)) hide()
        return Promise.reject(error)
      },
    )
  },

  postinstall(instance) {
    instance.interceptors.request.use((config) => {
      if (shouldShowLoading(config)) show(config.loadingTitle)
      return config
    })
  },
}
