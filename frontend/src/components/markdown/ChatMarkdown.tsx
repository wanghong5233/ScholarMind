/**
 * Agent 对话 Markdown 渲染
 * 与主站 Markdown 一致：始终使用 ReactMarkdown + remarkMath + rehypeKatex
 * 通过共享预处理器兼容 \( \) / \[ \] / [ ... ] 等公式格式。
 */
import 'katex/dist/katex.min.css'
import './ChatMarkdown.scss'
import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import remarkBreaks from 'remark-breaks'
import rehypeKatex from 'rehype-katex'
import rehypeRaw from 'rehype-raw'
import { preprocessMarkdownMath } from './mathPreprocess'

const markdownComponents = {
  a: ({ children, href, ...props }: any) => (
    <a href={href} target="_blank" rel="noreferrer" {...props}>
      {children}
    </a>
  ),
}

type ChatMarkdownProps = { children: string }

export function ChatMarkdown({ children }: ChatMarkdownProps) {
  const processed = useMemo(() => preprocessMarkdownMath(children), [children])

  return (
    <div className="doc-studio-chat-markdown doc-studio-chat-markdown--react">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath, remarkBreaks]}
        rehypePlugins={[[rehypeKatex, { strict: false, throwOnError: false }], rehypeRaw]}
        components={markdownComponents}
      >
        {processed}
      </ReactMarkdown>
    </div>
  )
}
