import { createRequest } from './request'
import { getApiBase } from '../env'

export const request = createRequest({
  baseURL: getApiBase(),
  // 不在全局打开 loading / cancelRepeat —— 这两个能力都是"调用方知道自己在
  // 做什么才该开"的 opt-in 行为，全局默认开会反过来悄悄改写正常的 HTTP 语义：
  //   - loading: 全局开会让 GET/轮询都弹全屏蒙层，导致列表反复闪烁
  //   - cancelRepeat: 全局开会取消并发上传、并发删除等合法的同 url 请求
  // 防双击是 UI 责任（按钮 disabled / confirmLoading），不是网络层责任。
  errorToast: true,
  unwrap: true,
})
