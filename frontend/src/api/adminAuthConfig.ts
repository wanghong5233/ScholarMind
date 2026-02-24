import { AxiosRequestConfig, AxiosRequestHeaders } from 'axios'
import { adminAuthState } from '@/store/adminAuth'

export function withAdminAuth(options?: AxiosRequestConfig): AxiosRequestConfig {
  const token = adminAuthState.token
  const headers: AxiosRequestHeaders = {
    ...((options?.headers || {}) as AxiosRequestHeaders),
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }
  return {
    ...options,
    headers,
  }
}

