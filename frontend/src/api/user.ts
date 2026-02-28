import { AxiosRequestConfig } from 'axios'
import { request } from './request'

export function register(
  params: {
    username: string
    password: string
  },
  options?: AxiosRequestConfig,
) {
  return request.post<{}>('users/register', params, options)
}

export function login(
  params: {
    username: string
    password: string
  },
  options?: AxiosRequestConfig,
) {
  return request.post<{
    access_token: string
  }>('users/login', params, options)
}

export function demoEntry(
  params?: {
    code?: string
  },
  options?: AxiosRequestConfig,
) {
  return request.post<{
    access_token: string
    token_type: string
    username: string
  }>('users/demo-entry', params ?? {}, options)
}
