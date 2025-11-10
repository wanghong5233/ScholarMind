import classNames from 'classnames'
import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import rehypeRaw from 'rehype-raw'
import { useMemo } from 'react'
import 'katex/dist/katex.min.css'
import './index.scss'

type MarkdownProps = {
  className?: string
  value?: string
  onClick?: React.MouseEventHandler<HTMLDivElement>
}

function MarkdownComponent({ className, value, onClick }: MarkdownProps) {
  const content = useMemo(() => {
    if (!value) return ''
    // 将引用标记 ##0$$ 转换为可点击的 span
    const processed = value.replace(/##(\d+)\$\$/g, (_, index: string) => {
      const num = Number(index)
      return `<span class="refrence-token" data-refrence-index="${num}">[${num + 1}]</span>`
    })
    return processed
  }, [value])

  const components = useMemo<Components>(
    () => ({
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      code({ inline, className, children, ...props }: any) {
        if (inline) {
          return (
            <code className={classNames('inline-code', className)} {...props}>
              {children}
            </code>
          )
        }
        return (
          <code className={classNames('code-block', className)} {...props}>
            {children}
          </code>
        )
      },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      a({ children, href, ...props }: any) {
        return (
          <a href={href} target="_blank" rel="noreferrer" {...props}>
            {children}
          </a>
        )
      },
    }),
    [],
  )

  return (
    <div 
      className={classNames('com-markdown', className)} 
      onClick={onClick}
      style={{
        lineHeight: '1.6',
      }}
    >
      <ReactMarkdown
        remarkPlugins={[
          remarkGfm,
          remarkMath
        ]}
        rehypePlugins={[
          [rehypeKatex, { strict: false, throwOnError: false }],
          rehypeRaw
        ]}
        components={components}
        skipHtml={false}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

// 不使用 memo，确保每次 value 变化都会重新渲染
export default MarkdownComponent
