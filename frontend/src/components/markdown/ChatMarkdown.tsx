/**
 * Agent 对话 Markdown 渲染
 * 与主站 Markdown 一致：始终使用 ReactMarkdown + remarkMath + rehypeKatex
 * 增加大模型输出预处理：将 [formula] 转为 \[formula\]（remark-math 仅支持 $ $$ \( \) \[ \]）
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

// 检测内容是否像 LaTeX 公式（避免误伤普通文本）
const LOOKS_LIKE_LATEX = /\\(frac|sqrt|sum|int|exp|infty|prod|left|right|alpha|beta|gamma|pi|sigma|sim|to|quad)/

/**
 * 预处理大模型输出：将 \( \) \[ \] 转为 $ $$，避免被 Markdown 解析器转义
 * remark-parse 会把 \[ 转成 [，导致 remark-math 无法识别；$ 不会被转义
 */
function preprocessLLMMath(content: string): string {
  if (!content || typeof content !== 'string') return content

  let out = content

  // 1) 兼容双转义：将 \\( \\) \\[ \\] 归一为 \( \) \[ \]
  out = out.replace(/\\\\([\[\]\(\)])/g, '\\$1')

  // 2) 成对转换 display math：\[...\] -> $$...$$
  out = out.replace(/\\\[([\s\S]*?)\\\]/g, (_, formula) => {
    const trimmed = String(formula).trim()
    return `$$${trimmed}$$`
  })

  // 3) 成对转换 inline math：\(...\) -> $...$
  //    注意必须去掉两端空格，否则 `$ ... $` 在 remark-math 下常被当成普通文本。
  out = out.replace(/\\\(([\s\S]*?)\\\)/g, (_, formula) => {
    const trimmed = String(formula).trim()
    if (!trimmed) return '$$'
    // inline 内部如果跨行，提升为 display，避免解析歧义
    if (/\r?\n/.test(trimmed)) return `$$${trimmed}$$`
    return `$${trimmed}$`
  })

  // 4. 非标准 [formula] 多行块 → $$ ... $$
  out = out.replace(/^\s*\[\s*\r?\n([\s\S]*?)\r?\n\s*\]\s*$/gm, (_, formula) => {
    const trimmed = formula.trim()
    if (LOOKS_LIKE_LATEX.test(trimmed)) return `$$${trimmed}$$`
    return `[${formula}]`
  })

  return out
}

const markdownComponents = {
  a: ({ children, href, ...props }: any) => (
    <a href={href} target="_blank" rel="noreferrer" {...props}>
      {children}
    </a>
  ),
}

type ChatMarkdownProps = { children: string }

export function ChatMarkdown({ children }: ChatMarkdownProps) {
  const processed = useMemo(() => preprocessLLMMath(children), [children])

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
