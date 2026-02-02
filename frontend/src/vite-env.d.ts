/// <reference types="vite/client" />

// MathJax 类型定义
interface Window {
  $app: import('antd/es/app/context').useAppProps
  $showLoading: (options?: { title?: string }) => void
  $hideLoading: () => void
}

interface ImportMetaEnv {
  readonly VITE_LATEX_AGENT_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module '@monaco-editor/react'
