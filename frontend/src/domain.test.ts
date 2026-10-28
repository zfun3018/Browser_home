import { describe, expect, it } from 'vitest'
import { buildSearchUrl, reorderLinkIds, siteFaviconUrl, visibleFields, type LinkLayout } from './domain'

describe('bookmark presentation', () => {
  it('keeps name, icon, and description visibility independent', () => {
    const layout: LinkLayout = { showName: false, showIcon: true, showDescription: true, order: 0 }
    expect(visibleFields(layout)).toEqual({ name: false, icon: true, description: true })
  })

  it('encodes a search query as a single parameter value', () => {
    expect(buildSearchUrl('https://www.google.com/search?q={query}', '猫 and coffee & tea'))
      .toBe('https://www.google.com/search?q=%E7%8C%AB%20and%20coffee%20%26%20tea')
  })

  it('rejects unsafe search destinations before navigation', () => {
    expect(() => buildSearchUrl('javascript:alert({query})', 'hello')).toThrow('HTTP(S)')
  })

  it('uses the rendered order as the base for consecutive bookmark reorders', () => {
    const links = [{ id: 1, collectionId: 8 }, { id: 2, collectionId: 8 }, { id: 3, collectionId: 8 }]
    const first = reorderLinkIds(links, { 1: { order: 2 }, 2: { order: 0 }, 3: { order: 1 } }, 8, 1, 2)
    expect(first.map(item => item.id)).toEqual([1, 2, 3])
    const second = reorderLinkIds(links, Object.fromEntries(first.map(item => [item.id, { order: item.order }])), 8, 3, 1)
    expect(second.map(item => item.id)).toEqual([3, 1, 2])
  })

  it('derives a favicon URL directly from the site origin', () => {
    expect(siteFaviconUrl('https://docs.example.org/reference/page?q=private')).toBe('https://docs.example.org/favicon.ico')
    expect(siteFaviconUrl('javascript:alert(1)')).toBeNull()
  })
})
