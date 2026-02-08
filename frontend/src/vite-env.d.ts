/// <reference types="vite/client" />

// MathJax 类型定义
interface Window {
  $app: import('antd/es/app/context').useAppProps
  $showLoading: (options?: { title?: string }) => void
  $hideLoading: () => void
}

interface ImportMetaEnv {
  readonly VITE_DOC_STUDIO_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module '@monaco-editor/react'
