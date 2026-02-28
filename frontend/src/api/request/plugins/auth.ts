import { userActions, userState } from '@/store/user'
import { ResponseError } from '../error'
import { IRequestPlugin } from './plugin'
import { MESSAGE_KEY } from './service'

const AUTH_ERROR_MAP = {
  401: '登录状态已失效，请重新登录',
}

const blackList = [
  '/users/login',
  'users/login',
  '/api/users/login',
  '/users/register',
  'users/register',
  '/api/users/register',
  '/users/sts-token',
  'users/sts-token',
  '/api/users/sts-token',
  '/users/demo-entry',
  'users/demo-entry',
  '/api/users/demo-entry',
  '/admin/auth/login',
  'admin/auth/login',
  '/api/admin/auth/login',
]

// 延迟导入 router 以避免循环依赖
function getRouter() {
  return import('@/router/routes').then(module => module.router)
}

export const authPlugin: IRequestPlugin = {
  install(instance) {
    instance.interceptors.request.use((config) => {
      const { token } = userState
      if (token && !config.headers['Authorization']) {
        config.headers['Authorization'] = `Bearer ${token}`
      }
      return config
    })

    instance.interceptors.response.use(
      (response) => response,
      async (error) => {
        const response = error.response
        if (!response) return Promise.reject(error)
        const url = response.config.url as string
        if (blackList.includes(url)) return Promise.reject(error)

        const code = response?.status
        const msg = response?.data?.[MESSAGE_KEY]

        let message: string
        switch (code) {
          case 401:
            if (url.includes('/admin/') || url.startsWith('admin/')) {
              return Promise.reject(error)
            }
            // token 失效
            userActions.setToken('')
            const router = await getRouter()
            router.navigate('/login')

            message =
              AUTH_ERROR_MAP[code as keyof typeof AUTH_ERROR_MAP] ||
              msg ||
              '请求发生错误'

            return Promise.reject(new ResponseError(message, response))
          case 461:
            // 知识库中没有文档
            message = '请先上传文档'
            const router461 = await getRouter()
            router461.navigate('/repository')

            return Promise.reject(new ResponseError(message, response))
          default:
            return Promise.reject(error)
        }
      },
    )
  },
}
