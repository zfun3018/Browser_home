import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import MarkdownNotes from './MarkdownNotes'

const base = '# 原始笔记\n\n一条记录。'
const baseHash = '2eae713fd10004a56a10a7b23df6379b19e72af3982202825003632e8a5666e7'
function response(data: unknown, status = 200) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(data) } as Response)
}

describe('Obsidian Markdown notes', () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

  it('loads Markdown and previews it without rendering raw HTML', async () => {
    vi.stubGlobal('fetch', vi.fn(() => response({ content: `${base}\n\n<script>bad()</script>`, hash: baseHash, status: 'ok' })))
    render(<MarkdownNotes />)
    const editor = await screen.findByLabelText('Markdown 笔记内容')
    expect(editor).toHaveValue(`${base}\n\n<script>bad()</script>`)
    fireEvent.click(screen.getByRole('button', { name: /预览/ }))
    expect(await screen.findByRole('heading', { name: '原始笔记' })).toBeInTheDocument()
    expect(document.querySelector('script')).toBeNull()
  })

  it('autosaves changed text to the fixed note endpoint with its base hash', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/notes') {
        if (init?.method === 'PUT') {
          expect(JSON.parse(String(init.body))).toEqual({ content: '# 更新', expectedHash: baseHash })
          return response({ content: '# 更新', hash: 'new-hash', status: 'saved' })
        }
        return response({ content: base, hash: baseHash, status: 'ok' })
      }
      return response({ changed: false, status: 'ok' })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<MarkdownNotes />)
    const editor = await screen.findByLabelText('Markdown 笔记内容')
    fireEvent.change(editor, { target: { value: '# 更新' } })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/notes', expect.objectContaining({ method: 'PUT' })), { timeout: 2000 })
    expect(await screen.findByText('已保存到 Obsidian')).toBeInTheDocument()
  })

  it('does not replace text typed while a save response is pending', async () => {
    let resolveSave: ((value: Response) => void) | undefined
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/notes') {
        if (init?.method === 'PUT') return new Promise<Response>(resolve => { resolveSave = resolve })
        return response({ content: base, hash: baseHash, status: 'ok' })
      }
      return response({ changed: false, status: 'ok' })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<MarkdownNotes />)
    const editor = await screen.findByLabelText('Markdown 笔记内容')
    fireEvent.change(editor, { target: { value: '# 已提交的快照' } })
    await waitFor(() => expect(resolveSave).toBeDefined(), { timeout: 2000 })
    fireEvent.change(editor, { target: { value: '# 保存期间继续输入' } })
    await act(async () => { resolveSave?.(await response({ content: '# 已提交的快照', hash: 'new-base-hash', status: 'saved' })) })
    expect(editor).toHaveValue('# 保存期间继续输入')
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/notes', expect.objectContaining({ method: 'PUT', body: JSON.stringify({ content: '# 保存期间继续输入', expectedHash: 'new-base-hash' }) })), { timeout: 2000 })
  })

  it('preserves typing made while an autosave is in flight and saves it against the new hash', async () => {
    let resolveFirst: ((value: Response) => void) | undefined
    const writes: Array<{ content: string; expectedHash: string }> = []
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/notes') {
        if (init?.method === 'PUT') {
          const body = JSON.parse(String(init.body)) as { content: string; expectedHash: string }
          writes.push(body)
          if (writes.length === 1) return new Promise<Response>(resolve => { resolveFirst = resolve })
          return response({ content: body.content, hash: 'hash-after-second-save', status: 'saved' })
        }
        return response({ content: base, hash: baseHash, status: 'ok' })
      }
      return response({ changed: false, status: 'ok' })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<MarkdownNotes />)
    const editor = await screen.findByLabelText('Markdown 笔记内容')
    fireEvent.change(editor, { target: { value: '# 第一次输入' } })
    await waitFor(() => expect(writes).toHaveLength(1), { timeout: 2000 })
    fireEvent.change(editor, { target: { value: '# 请求期间继续输入' } })
    await act(async () => { resolveFirst?.(await response({ content: '# 第一次输入', hash: 'hash-after-first-save', status: 'saved' })) })
    await waitFor(() => expect(writes).toHaveLength(2), { timeout: 2000 })
    expect(writes[1]).toEqual({ content: '# 请求期间继续输入', expectedHash: 'hash-after-first-save' })
    expect(editor).toHaveValue('# 请求期间继续输入')
    expect(await screen.findByText('已保存到 Obsidian')).toBeInTheDocument()
  })

  it('keeps the local draft visible and offers explicit choices on a write conflict', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/notes') {
        if (init?.method === 'PUT') return response({ detail: 'conflict', content: '# Obsidian 版本', hash: 'remote-hash' }, 409)
        return response({ content: base, hash: baseHash, status: 'ok' })
      }
      return response({ changed: false, status: 'ok' })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<MarkdownNotes />)
    const editor = await screen.findByLabelText('Markdown 笔记内容')
    fireEvent.change(editor, { target: { value: '# 首页草稿' } })
    expect(await screen.findByRole('alert')).toHaveTextContent('Obsidian 中的内容已经变化')
    expect(editor).toHaveValue('# 首页草稿')
    expect(screen.getByRole('button', { name: /使用 Obsidian 版本/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /保留首页版本/ })).toBeInTheDocument()
  })

  it('detects Obsidian edits while open and snapshots the unsaved local draft', async () => {
    let poll: (() => void) | undefined
    vi.spyOn(window, 'setInterval').mockImplementation((handler: TimerHandler) => { poll = handler as () => void; return 1 })
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/notes') {
        if (init?.method === 'PUT') return response({ detail: 'conflict', content: '# Obsidian 新版', hash: 'remote-hash' }, 409)
        return response({ content: base, hash: baseHash, status: 'ok' })
      }
      if (String(input).startsWith('/api/notes/events')) return response({ changed: true, content: '# Obsidian 新版', hash: 'remote-hash', status: 'ok' })
      return response({})
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<MarkdownNotes />)
    const editor = await screen.findByLabelText('Markdown 笔记内容')
    fireEvent.change(editor, { target: { value: '# 首页未保存草稿' } })
    await act(async () => { poll?.(); await Promise.resolve(); await Promise.resolve() })
    expect(await screen.findByRole('alert')).toHaveTextContent('Obsidian 中的内容已经变化')
    expect(editor).toHaveValue('# 首页未保存草稿')
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/notes', expect.objectContaining({ method: 'PUT' })))
  })

  it('recovers when the mounted note becomes available after startup', async () => {
    let poll: (() => void) | undefined
    vi.spyOn(window, 'setInterval').mockImplementation((handler: TimerHandler) => { poll = handler as () => void; return 1 })
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      if (String(input) === '/api/notes') return response({ content: '', hash: '', status: 'unavailable' })
      return response({ changed: true, content: base, hash: baseHash, status: 'ok' })
    }))
    render(<MarkdownNotes />)
    expect(await screen.findByText(/找不到已配置的 Obsidian 笔记文件/)).toBeInTheDocument()
    await act(async () => { poll?.(); await Promise.resolve() })
    expect(await screen.findByLabelText('Markdown 笔记内容')).toHaveValue(base)
    expect(await screen.findByText('已保存到 Obsidian')).toBeInTheDocument()
  })
})
