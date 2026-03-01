import { userState } from '@/store/user'
import { isDemoEntryEnabled } from '@/utils/demo'
import { Navigate } from 'react-router-dom'
import { useSnapshot } from 'valtio'

export default function Index() {
  const user = useSnapshot(userState)
  // 已有 token 时优先进 /chat，避免被 demo 入口覆盖真实登录态
  if (user.token) {
    return <Navigate to="/chat" replace />
  }
  if (isDemoEntryEnabled()) {
    return <Navigate to="/demo" replace />
  }
  return <Navigate to="/chat" replace />
}
