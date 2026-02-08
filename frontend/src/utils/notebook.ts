import dayjs from 'dayjs'
import { createFileOrDirectory, createWorkspace, fetchWorkspace } from '@/api/docStudio'

export const NOTEBOOK_WORKSPACE_ID = 'notebook'
export const NOTEBOOK_WORKSPACE_NAME = 'Notebook'
export const NOTEBOOK_DIR = 'notes'

function stripQuotes(value: string) {
  return value.replace(/^['"]|['"]$/g, '').trim()
}

function slugify(value: string) {
  const base = value
    .toLowerCase()
    .replace(/[^a-z0-9\s-_]/g, '')
    .trim()
    .replace(/\s+/g, '-')
  return base || 'note'
}

export function buildNoteFileName(title: string) {
  const stamp = dayjs().format('YYYYMMDD_HHmmss')
  const slug = slugify(stripQuotes(title || 'note'))
  return `${stamp}_${slug}.md`
}

export async function ensureNotebookWorkspace() {
  try {
    await fetchWorkspace({ workspaceId: NOTEBOOK_WORKSPACE_ID })
  } catch (error: any) {
    if (error?.response?.status !== 404) throw error
    await createWorkspace({
      name: NOTEBOOK_WORKSPACE_NAME,
      workspaceId: NOTEBOOK_WORKSPACE_ID,
      config: { type: 'notebook' },
    })
  }
}

export async function ensureNotebookDirectory() {
  try {
    await createFileOrDirectory({
      workspaceId: NOTEBOOK_WORKSPACE_ID,
      path: NOTEBOOK_DIR,
      type: 'directory',
    })
  } catch (error: any) {
    if (error?.response?.status !== 400) throw error
  }
}

export async function createNotebookNoteFile(content: string, title: string) {
  await ensureNotebookWorkspace()
  await ensureNotebookDirectory()
  const baseName = buildNoteFileName(title)
  let targetPath = `${NOTEBOOK_DIR}/${baseName}`
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const suffix = attempt ? `_${attempt}` : ''
    targetPath = `${NOTEBOOK_DIR}/${baseName.replace('.md', `${suffix}.md`)}`
    try {
      await createFileOrDirectory({
        workspaceId: NOTEBOOK_WORKSPACE_ID,
        path: targetPath,
        type: 'file',
        content,
      })
      return targetPath
    } catch (error: any) {
      if (error?.response?.status !== 400) throw error
    }
  }
  throw new Error('Notebook file name conflict')
}
