import { createRequest } from './request'
import { getApiBase } from '../env'

export const request = createRequest({
  baseURL: getApiBase(),
  // 不再全局开启 loading：交给 loadingPlugin 按 method 智能判断
  // （GET/HEAD 不弹蒙层，POST/PUT/DELETE 弹），避免列表/轮询闪烁。
  errorToast: true,
  cancelRepeat: true,
  unwrap: true,
})
