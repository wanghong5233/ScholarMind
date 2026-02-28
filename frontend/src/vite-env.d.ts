/// <reference types="vite/client" />

// MathJax 类型定义
interface Window {
  $app: import('antd/es/app/context').useAppProps
  $showLoading: (options?: { title?: string }) => void
  $hideLoading: () => void
}

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string
  readonly VITE_DOC_STUDIO_BASE?: string
  readonly VITE_DEEP_RESEARCH_BASE?: string
  readonly VITE_ENABLE_ADMIN_UI?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module '@monaco-editor/react'
