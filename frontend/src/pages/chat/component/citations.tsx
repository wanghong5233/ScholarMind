import IconFilter from '@/assets/chat/filter.svg'
import IconObject from '@/assets/chat/object.svg'
import IconSearch from '@/assets/chat/search.svg'
import Markdown from '@/components/markdown'
import '@/components/markdown/index.scss'
import { getDocumentPreviewUrl } from '@/api/repository'
import { userState } from '@/store/user'
import { Button, Drawer, Input, Tooltip, message } from 'antd'
import { useCallback, useMemo, useState } from 'react'
import { useSnapshot } from 'valtio'
import styles from './citations.module.scss'

interface MetaDetail {
  label: string
  value: string | number
  hint?: string
}

const isNumberArray = (value: unknown): value is number[] =>
  Array.isArray(value) && value.every((item) => typeof item === 'number')

const extractFirstBBox = (
  bboxList: API.Reference['bbox_list'],
): number[] | null => {
  if (!Array.isArray(bboxList)) return null
  for (const entry of bboxList) {
    if (isNumberArray(entry) && entry.length >= 4) {
      return entry
    }
    if (Array.isArray(entry)) {
      for (const inner of entry) {
        if (isNumberArray(inner) && inner.length >= 4) {
          return inner
        }
      }
    }
  }
  return null
}

function CitationsItem(props: {
  item: API.Reference
  index: number
  onRead: () => void
}) {
  const { item, index, onRead } = props

  const content = useMemo(() => {
    const sourceText = item.snippet || item.source_text || item.content_with_weight || ''
    if (!sourceText) return '暂无内容'
    const dom = document.createElement('div')
    dom.innerHTML = sourceText
    return dom.innerText || sourceText
  }, [item.snippet, item.source_text, item.content_with_weight])

  return (
    <div className={styles['citations__item']}>
      <div className={styles['actions']}>
        <Tooltip
          classNames={{
            root: styles['citations-tooltip'],
          }}
          title="Drill-down"
        >
          <Button color="primary" variant="text" shape="circle" size="small">
            <img src={IconObject} />
          </Button>
        </Tooltip>
      </div>

      <div className={styles['header']}>
        <div className={styles['name']} title={item.document_name || item.document_id}>
          {item.document_name || `文档 ${item.document_id ?? '-'}`}
        </div>
        <div className={styles['score']}>{index + 1}</div>
      </div>

      <div className={styles['desc']}>{content}</div>

      <div className={styles['footer']}>
        <div className={styles['footer-desc']}>
          页码 {item.page ?? item.positions?.[0]?.[0] ?? '-'}
        </div>
        <Button
          className={styles['footer-button']}
          color="primary"
          variant="solid"
          onClick={onRead}
        >
          阅读
        </Button>
      </div>
    </div>
  )
}

export default function Citations(props: { list?: API.Reference[] }) {
  const { list } = props

  const [read, setRead] = useState<API.Reference | null>(null)
  const user = useSnapshot(userState)

  const metaDetails: MetaDetail[] = useMemo(() => {
    if (!read) return []
    
    const pageText = read.page_range?.length
      ? `第 ${read.page_range.join(' ~ ')} 页`
      : read.page
      ? `第 ${read.page} 页`
      : '-'
    
    // 分块序号
    const chunkText =
      typeof read.structure_chunk_index === 'number'
        ? `第 ${read.structure_chunk_index + 1} 块 / 共 ${
            typeof read.structure_chunk_total === 'number'
              ? read.structure_chunk_total
              : '?'
          } 块`
        : read.structure_chunk_total
        ? `? / 共 ${read.structure_chunk_total} 块`
        : '-'
    
    // 字符区间
    const offsetText =
      read.offsets && (typeof read.offsets.start === 'number' || typeof read.offsets.end === 'number')
        ? `${read.offsets.start ?? 0} - ${read.offsets.end ?? 0}`
        : '-'
    
    // 相关性得分
    const scoreText =
      typeof read.score === 'number' ? read.score.toFixed(3) : '-'
    
    // 结构路径（更友好的显示）
    const structurePathText = read.structure_path
      ? read.structure_path.replace(/\./g, ' > ')
      : '-'
    
    // 结构标题（如果有的话，作为章节信息）
    const sectionText = read.structure_title || '-'

    // DOI 和数据来源信息
    const doiText = read.doi || '-'
    const sourceText = read.source || '-'
    const alignmentText = read.alignment_status || '-'
    const parserText = read.parser_engine || '-'
    
    // BBox 信息（用于精确定位）
    const bboxText = read.bbox_list?.length
      ? `${read.bbox_list.length} 个边界框`
      : '-'

    return [
      { label: 'DOI', value: doiText },
      { label: '所属章节', value: sectionText },
      { label: '结构路径', value: structurePathText },
      { label: '内容类型', value: read.logical_type || read.element_type || '-' },
      { label: '页码范围', value: pageText },
      { label: '边界框信息', value: bboxText },
      { label: '分块信息', value: chunkText },
      { label: '字符区间', value: offsetText },
      { label: '相关性得分', value: scoreText },
      { label: '数据来源', value: sourceText },
      { label: '对齐状态', value: alignmentText },
      { label: '解析引擎', value: parserText },
      { label: '文档 ID', value: read.document_id ?? '-' },
      { label: '知识库 ID', value: read.knowledge_base_id ?? '-' },
    ]
  }, [read])

  const locationDescription = useMemo(() => {
    if (!read) return null

    const DEFAULT_PAGE_WIDTH = 595
    const DEFAULT_PAGE_HEIGHT = 842

    const firstPosition = read.positions?.find(
      (pos) => Array.isArray(pos) && typeof pos[0] === 'number',
    )
    const pageNumber =
      typeof read.page === 'number'
        ? read.page
        : firstPosition && typeof firstPosition[0] === 'number'
        ? firstPosition[0]
        : read.page_range?.[0]

    const firstBBox = extractFirstBBox(read.bbox_list)

    const normalize = (
      value: number | undefined,
      scale: number,
      normalized: boolean,
    ) => {
      if (typeof value !== 'number' || Number.isNaN(value)) return undefined
      return normalized ? value * scale : value
    }

    const bboxNormalized =
      !!firstBBox &&
      [0, 1, 2, 3].every((idx) => {
        const v = firstBBox[idx]
        return typeof v === 'number' && Math.abs(v) <= 1.2
      })

    const x0 = normalize(firstBBox?.[0], DEFAULT_PAGE_WIDTH, bboxNormalized)
    const x1 = normalize(firstBBox?.[2], DEFAULT_PAGE_WIDTH, bboxNormalized)
    const y0 = normalize(firstBBox?.[1], DEFAULT_PAGE_HEIGHT, bboxNormalized)

    let yCoord: number | undefined = y0
    if (
      typeof yCoord !== 'number' &&
      firstPosition &&
      typeof firstPosition[1] === 'number'
    ) {
      yCoord = firstPosition[1]
    }

    const pageWidth =
      typeof x0 === 'number' && typeof x1 === 'number'
        ? Math.max(Math.abs(x0), Math.abs(x1), DEFAULT_PAGE_WIDTH)
        : DEFAULT_PAGE_WIDTH

    const pageHeight =
      typeof y0 === 'number'
        ? Math.max(Math.abs(y0) * 1.2, DEFAULT_PAGE_HEIGHT)
        : DEFAULT_PAGE_HEIGHT

    const verticalLabel =
      typeof yCoord === 'number'
        ? yCoord < pageHeight / 3
          ? '上'
          : yCoord < (pageHeight * 2) / 3
          ? '中'
          : '下'
        : ''

    let horizontalLabel = ''
    if (
      typeof x0 === 'number' &&
      typeof x1 === 'number' &&
      pageWidth > 0
    ) {
      const blockWidth = Math.abs(x1 - x0)
      if (blockWidth >= pageWidth * 0.85) {
        horizontalLabel = '整栏'
      } else {
        const midX = (x0 + x1) / 2
        const margin = pageWidth * 0.05
        if (midX < pageWidth / 2 - margin) {
          horizontalLabel = '左'
        } else if (midX > pageWidth / 2 + margin) {
          horizontalLabel = '右'
        } else {
          horizontalLabel = '中'
        }
      }
    }

    let areaLabel = ''
    if (horizontalLabel) {
      if (horizontalLabel === '整栏') {
        areaLabel = verticalLabel ? `${horizontalLabel}${verticalLabel}` : horizontalLabel
      } else {
        const columnLabel = `${horizontalLabel}栏`
        areaLabel = `${columnLabel}${verticalLabel || ''}区域`
      }
    } else if (verticalLabel) {
      areaLabel = `${verticalLabel}区域`
    }

    const parts: string[] = []
    if (typeof pageNumber === 'number') {
      parts.push(`第 ${pageNumber} 页`)
    }
    if (areaLabel) {
      parts.push(areaLabel)
    }
    if (typeof yCoord === 'number') {
      parts.push(`y≈${Math.round(yCoord)}`)
    }

    if (!parts.length) {
      return null
    }
    return parts.join(' · ')
  }, [read])

  const handleOpenDocument = useCallback(
    (item: API.Reference) => {
      if (!item.document_id || !item.knowledge_base_id) {
        message.warning('引用缺少文档或知识库信息，无法打开原文')
        return
      }
      const docId = Number(item.document_id)
      if (Number.isNaN(docId)) {
        message.warning('文档 ID 格式不正确，无法打开原文')
        return
      }
      if (!user.token) {
        message.warning('请先登录以获取原文预览权限')
        return
      }
      const url = getDocumentPreviewUrl(
        Number(item.knowledge_base_id),
        docId,
        user.token,
      )
      window.open(url, '_blank', 'noopener')
    },
    [user.token],
  )

  return (
    <div className={styles['citations']}>
      <div className={styles['citations__search']}>
        <Input
          placeholder="Search keywords in citations"
          suffix={<img src={IconSearch} alt="search" />}
        />

        <Button color="default" variant="outlined">
          <img src={IconFilter} />
          Filter
        </Button>
      </div>

      <div className={styles['citations__title']}>Selected citations</div>

      <div className={styles['citations__list']}>
        {list?.map((item, index) => (
          <CitationsItem
            key={item.id ?? item.chunk_id ?? `${item.document_id}-${index}`}
            item={item}
            index={index}
            onRead={() => setRead(item)}
          />
        ))}
      </div>

      <Drawer
        title={read?.document_name || `文档 ${read?.document_id ?? '-'}`}
        width={800}
        onClose={() => setRead(null)}
        open={!!read}
        destroyOnClose
      >
        {read ? (
          <div className={styles['citation-detail']}>
            <div className={styles['citation-detail__meta']}>
              <div className={styles['meta-item']}>
                <div className={styles['meta-label']}>论文标题</div>
                <div className={styles['meta-value']}>
                  {read.document_name || `文档 ${read.document_id ?? '-'}`}
                </div>
              </div>
              {metaDetails.map((meta) => (
                <div key={meta.label} className={styles['meta-item']}>
                  <div className={styles['meta-label']}>{meta.label}</div>
                  <div className={styles['meta-value']}>
                    {meta.value}
                    {meta.hint && (
                      <div className={styles['meta-hint']}>{meta.hint}</div>
                    )}
                  </div>
                </div>
              ))}
            </div>
            <div className={styles['citation-detail__content']}>
              <Markdown
                value={
                  read.source_text ||
                  read.content_with_weight ||
                  read.snippet ||
                  ''
                }
              />
              <div className={styles['citation-detail__foot']}>
                {locationDescription && (
                  <div className={styles['citation-detail__location']}>
                    <span className={styles['location-icon']}>📍</span>
                    <span className={styles['location-text']}>
                      原文位置：{locationDescription}
                    </span>
                  </div>
                )}
                <div className={styles['citation-detail__actions']}>
                  <Button
                    type="primary"
                    ghost
                    disabled={
                      !read.document_id ||
                      !read.knowledge_base_id ||
                      !user.token
                    }
                    onClick={() => handleOpenDocument(read)}
                  >
                    打开原文（新窗口）
                  </Button>
                  {(!read.structure_path || !read.structure_title) && (
                    <div className={styles['citation-detail__hint']}>
                      💡 提示：该引用缺少结构化元数据（章节、路径等），可能是旧版本解析的文档。建议在知识库中重新解析此文档以获得完整的定位信息。
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        ) : null}
      </Drawer>
    </div>
  )
}
