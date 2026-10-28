import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
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
      if (url.includes('/collections/3/layout')) return response({ collapsed: true, order: 0 })
      if (url.includes('/links/12/layout')) return response({ showName: true, showIcon: true, showDescription: false, order: 0 })
      return response({})
    }))
  })
  afterEach(() => { cleanup(); vi.unstubAllGlobals() })

  it('folds a collection without removing its bookmarks', async () => {
    const user = userEvent.setup()
    render(<App />)
    const link = await screen.findByRole('link', { name: /文档中心/ })
    await user.click(screen.getByRole('button', { name: /收起 工作/ }))
    expect(link).not.toBeVisible()
    expect(screen.getByRole('button', { name: /展开 工作/ })).toBeInTheDocument()
  })

  it('retains canonical bookmarks and shows an offline state when a later refresh fails', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.mocked(fetch)
    render(<App />)
    expect(await screen.findByRole('link', { name: /文档中心/ })).toBeInTheDocument()
    fetchMock.mockImplementation((input: RequestInfo | URL) => String(input).includes('/api/home')
      ? Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({ detail: 'Linkwarden unavailable' }) } as Response)
      : response(searches))
    await user.click(screen.getByRole('button', { name: '刷新' }))
    expect(await screen.findByText(/暂时无法连接 Linkwarden/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /文档中心/ })).toBeInTheDocument()
  })

  it('retains the last bookmark snapshot when the backend reports Linkwarden offline in a 200 response', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.mocked(fetch)
    render(<App />)
    expect(await screen.findByRole('link', { name: /文档中心/ })).toBeInTheDocument()
    fetchMock.mockImplementation((input: RequestInfo | URL) => String(input).includes('/api/home')
      ? response({ ...home, collections: [], links: [], sync: { status: 'error', message: 'upstream down' } })
      : response(searches))
    await user.click(screen.getByRole('button', { name: '刷新' }))
    expect(await screen.findByText(/暂时无法连接 Linkwarden/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /文档中心/ })).toBeInTheDocument()
  })

  it('offers a non-drag collection move action', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('link', { name: /文档中心/ })
    await user.click(screen.getByRole('button', { name: '编辑 文档中心' }))
    expect(await screen.findByLabelText('分组')).toBeInTheDocument()
  })

  it('writes only changed bookmark fields and includes their original values for conflict checks', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/home')) return response(home)
      if (url.includes('/api/search-engines')) return response(searches)
      if (url.includes('/api/links/12')) {
        expect(JSON.parse(String(init?.body))).toEqual({ name: '新名称', expected: { name: '文档中心' } })
        return response({ ...home.links[0], name: '新名称' })
      }
      return response({})
    })
    render(<App />)
    await screen.findByRole('link', { name: /文档中心/ })
    await user.click(screen.getByRole('button', { name: '编辑 文档中心' }))
    await user.clear(screen.getByLabelText('名称'))
    await user.type(screen.getByLabelText('名称'), '新名称')
    await user.click(screen.getByRole('button', { name: /保存到 Linkwarden/ }))
    expect(await screen.findByRole('link', { name: /新名称/ })).toBeInTheDocument()
  })
})
