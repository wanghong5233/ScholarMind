import { AxiosRequestConfig, AxiosResponse, CanceledError } from 'axios'
import { ResponseError } from '../error'
import { IRequestPlugin } from './plugin'

const NETWORK_ERROR_MAP = {
  // '400': 'Bad Request',
  // '401': 'Unauthorized, please login again',
  // '403': 'Access Denied',
  // '404': 'Request Error, Resource Not Found',
  // '405': 'Method Not Allowed',
  // '408': 'Request Timeout',
  429: '请求过于频繁，请稍后再试',
  // '500': 'Internal Server Error',
  // '501': 'Not Implemented',
  // '502': 'Network Error',
  // '503': 'Service Unavailable',
  // '504': 'Network Timeout',
  // '505': 'HTTP Version Not Supported',
}

const isAbortLikeError = (error: unknown): boolean => {
  const err = (error ?? {}) as Record<string, unknown>
  const name = String(err.name || '').toLowerCase()
  const code = String(err.code || '').toLowerCase()
  const message = String(err.message || '').toLowerCase()
  if (name === 'aborterror' || name === 'cancelederror') return true
  if (code === 'err_canceled' || code === 'abort_err') return true
  return (
    message.includes('signal is aborted') ||
    message.includes('request aborted') ||
    message.includes('operation was aborted')
  )
}

export const errorToastPlugin: IRequestPlugin = {
  postinstall(instance) {
    instance.interceptors.response.use(
      (response) => response,
      (error: unknown) => {
        const err = (error ?? {}) as {
          response?: AxiosResponse<unknown>
          config?: AxiosRequestConfig
          message?: string
        }
        const response = err.response
        const config = (response?.config ?? err.config) as AxiosRequestConfig

        if (config && !config.errorToast) return Promise.reject(error)

        // CanceledError 主要来源于 repeat.ts 取消重复请求
        // 该错误不应展示给用户
        if (error instanceof CanceledError) return Promise.reject(error)
        // Fetch adapter 下，主动 abort 可能不是 CanceledError
        if (isAbortLikeError(error)) return Promise.reject(error)

        const status = response?.status ?? ''
        const responseData = (response?.data ?? {}) as Record<string, unknown>
        const message =
          error instanceof ResponseError
            ? error.message
            : NETWORK_ERROR_MAP[status as keyof typeof NETWORK_ERROR_MAP] ||
              responseData.message ||
              responseData.detail ||
              responseData.error ||
              err.message ||
              '请求错误'

        window.$app.message.error(
          typeof message === 'string' ? message : JSON.stringify(message),
        )

        return Promise.reject(error)
      },
    )
  },
}
