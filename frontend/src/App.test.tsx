import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'

const home = {
  collections: [{ id: 3, name: '工作', ownerId: 7 }],
  links: [{ id: 12, name: '文档中心', url: 'https://docs.example.org', description: '团队资料入口', icon: 'book-open', iconWeight: 'regular', color: '#2563eb', collectionId: 3 }],
  preferences: { links: {}, collections: {} },
  sync: { status: 'ok' },
}
const searches = { providers: [{ id: 'google', name: 'Google', url: 'https://www.google.com/search?q={query}' }], activeProviderId: 'google' }

function response(data: unknown, status = 200) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(data) } as Response)
}

describe('homepage interactions', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/home')) return response(home)
      if (url.includes('/api/search-engines')) return response(searches)
      if (url === '/api/backup/status') return response({ status: 'ok', lastBackupAt: null, nextBackupAt: null })
      if (url.includes('/collections/3/layout')) return response({ collapsed: true, order: 0 })
      if (url.includes('/links/12/layout')) return response({ showName: true, showIcon: true, showDescription: false, order: 0 })
      return response({})
    }))
  })
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('folds a collection without removing its bookmarks', async () => {
    const user = userEvent.setup()
    render(<App />)
    const link = await screen.findByRole('link', { name: /文档中心/ })
    await user.click(screen.getByRole('button', { name: /收起 工作/ }))
    expect(link).not.toBeVisible()
    expect(screen.getByRole('button', { name: /展开 工作/ })).toHaveAttribute('aria-expanded', 'false')
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes('/api/collections/3/layout'))).toBe(false)
  })

  it('keeps a collapsed collection in its current position when saving its first preference', async () => {
    const user = userEvent.setup()
    const groups = { ...home, collections: [...home.collections, { id: 7, name: '收件箱', ownerId: 7 }] }
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/home')) return response(groups)
      if (url.includes('/api/search-engines')) return response(searches)
      if (url.includes('/collections/7/layout')) return response({ collapsed: true, order: 7 })
      return response({})
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    await screen.findByRole('link', { name: /文档中心/ })

    await user.click(screen.getByRole('button', { name: '收起 收件箱' }))

    expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/api/collections/7/layout'))).toBe(false)
    expect(screen.getAllByRole('button', { name: /收起|展开/ }).map(button => button.getAttribute('aria-label'))).toEqual(['收起 工作', '展开 收件箱'])
  })

  it('does not render the removed calendar or notes sidebar', async () => {
    render(<App />)
    await screen.findByRole('link', { name: /文档中心/ })
    expect(screen.queryByRole('region', { name: '日历' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Obsidian 笔记' })).not.toBeInTheDocument()
  })

  it('shows the local bookmark sync state', async () => {
    render(<App />)
    expect(await screen.findByText('本地书签已同步')).toBeInTheDocument()
  })

  it('retains canonical bookmarks and shows an offline state when a later refresh fails', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.mocked(fetch)
    render(<App />)
    expect(await screen.findByRole('link', { name: /文档中心/ })).toBeInTheDocument()
    fetchMock.mockImplementation((input: RequestInfo | URL) => String(input).includes('/api/home')
      ? Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({ detail: 'Local bookmark store unavailable' }) } as Response)
      : response(searches))
    await user.click(screen.getByRole('button', { name: '刷新' }))
    expect(await screen.findByText(/暂时无法读取本地书签库/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /文档中心/ })).toBeInTheDocument()
  })

  it('retains the last bookmark snapshot when the local store reports an offline state in a 200 response', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.mocked(fetch)
    render(<App />)
    expect(await screen.findByRole('link', { name: /文档中心/ })).toBeInTheDocument()
    fetchMock.mockImplementation((input: RequestInfo | URL) => String(input).includes('/api/home')
      ? response({ ...home, collections: [], links: [], sync: { status: 'error', message: 'upstream down' } })
      : response(searches))
    await user.click(screen.getByRole('button', { name: '刷新' }))
    expect(await screen.findByText(/暂时无法读取本地书签库/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /文档中心/ })).toBeInTheDocument()
  })

  it('offers a non-drag collection move action', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('link', { name: /文档中心/ })
    const link = await screen.findByRole('link', { name: /文档中心/ })
    fireEvent.contextMenu(link)
    await user.click(screen.getByRole('menuitem', { name: '编辑网址' }))
    expect(await screen.findByLabelText('分组')).toBeInTheDocument()
  })

  it('opens the local bookmark editor from the context menu', async () => {
    render(<App />)
    const link = await screen.findByRole('link', { name: /文档中心/ })
    fireEvent.contextMenu(link)
    fireEvent.click(screen.getByRole('menuitem', { name: '编辑网址' }))
    expect(await screen.findByRole('form', { name: '编辑 文档中心' })).toBeInTheDocument()
    expect(screen.getByLabelText('网址')).toHaveValue('https://docs.example.org')
  })

  it('confirms and removes a bookmark from the page', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fetchMock = vi.mocked(fetch)
    render(<App />)
    const link = await screen.findByRole('link', { name: /文档中心/ })
    fireEvent.contextMenu(link)

    await user.click(screen.getByRole('menuitem', { name: '删除网址' }))

    expect(window.confirm).toHaveBeenCalledWith('确定删除网址“文档中心”吗？')
    expect(fetchMock).toHaveBeenCalledWith('/api/links/12', expect.objectContaining({ method: 'DELETE' }))
    expect(screen.queryByRole('link', { name: /文档中心/ })).not.toBeInTheDocument()
  })

  it('uploads a selected image from the bookmark editor', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.mocked(fetch)
    render(<App />)
    const link = await screen.findByRole('link', { name: /文档中心/ })
    fireEvent.contextMenu(link)
    await user.click(screen.getByRole('menuitem', { name: '编辑网址' }))
    const file = new File(['image-data'], 'icon.webp', { type: 'image/webp' })
    await user.upload(screen.getByLabelText('上传图标'), file)
    expect(fetchMock).toHaveBeenCalledWith('/api/links/12/icon/custom', expect.objectContaining({ method: 'PUT', body: file }))
  })

  it('uploads an image pasted into the bookmark editor', async () => {
    render(<App />)
    const link = await screen.findByRole('link', { name: /文档中心/ })
    fireEvent.contextMenu(link)
    fireEvent.click(screen.getByRole('menuitem', { name: '编辑网址' }))
    const file = new File(['clipboard-image'], 'clipboard.png', { type: 'image/png' })
    fireEvent.paste(screen.getByRole('form', { name: '编辑 文档中心' }), {
      clipboardData: { items: [{ type: 'image/png', getAsFile: () => file }] },
    })
    await screen.findByText(/图标会保存到本地 SQLite/)
    expect(vi.mocked(fetch)).toHaveBeenCalledWith('/api/links/12/icon/custom', expect.objectContaining({ method: 'PUT', body: file }))
  })

  it('fetches an online icon and replaces the current local icon', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/home')) return response(home)
      if (url.includes('/api/search-engines')) return response(searches)
      return response({})
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)
    const link = await screen.findByRole('link', { name: /文档中心/ })
    fireEvent.contextMenu(link)
    await user.click(screen.getByRole('menuitem', { name: '编辑网址' }))
    await user.click(screen.getByRole('button', { name: '在线获取并替换' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/links/12/icon/fetch', expect.objectContaining({ method: 'POST' }))
  })

  it('loads the local icon endpoint and falls back to a colored glyph on failure', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/home')) return response({ ...home, links: [{ ...home.links[0], icon: null }] })
      if (url.includes('/api/search-engines')) return response(searches)
      return response({})
    }))
    render(<App />)
    await screen.findByRole('link', { name: /文档中心/ })
    const favicon = screen.getByRole('img', { name: '文档中心网站图标' })
    expect(favicon.getAttribute('src')).toContain('/api/links/12/icon')
    fireEvent.error(favicon)
    expect(screen.getByLabelText('文档中心 图标')).toHaveStyle({ color: '#2563eb' })
    expect(favicon).not.toBeVisible()
  })
})
