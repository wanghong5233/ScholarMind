/**
 * 判断当前环境是否启用演示入口（免登录进入演示）。
 * 当 VITE_DEMO_ENTRY_ENABLED=true 或 hostname 匹配 demo 域名时返回 true。
 */
export function isDemoEntryEnabled(): boolean {
  if (String(import.meta.env.VITE_DEMO_ENTRY_ENABLED ?? '').toLowerCase() === 'true') return true
  if (typeof window !== 'undefined' && /demo-scholarmind\.wh5233\.me/i.test(window.location.hostname)) return true
  return false
}
