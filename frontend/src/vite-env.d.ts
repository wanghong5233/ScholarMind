/// <reference types="vite/client" />

// MathJax 类型定义
interface Window {
  $app: import('antd/es/app/context').useAppProps
  $showLoading: (options?: { title?: string }) => void
  $hideLoading: () => void
}
