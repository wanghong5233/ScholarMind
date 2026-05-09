import { createRequest } from './request'
import { getApiBase } from '../env'

export const request = createRequest({
  baseURL: getApiBase(),
  // loading / cancelRepeat 都不再全局开启，交给各自插件按 HTTP method 智能判断：
  //   - loadingPlugin: GET/HEAD 不弹蒙层，POST/PUT/DELETE 弹（避免列表/轮询闪烁）
  //   - repeatPlugin:  GET/HEAD 不取消重复（避免轮询把用户的手动 refresh 取消，
  //                    导致删除后列表表面"卡住"），POST/PUT/DELETE 才防重复提交
  errorToast: true,
  unwrap: true,
})
