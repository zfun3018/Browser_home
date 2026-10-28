import { describe, expect, it } from 'vitest'
import { buildSearchUrl, visibleFields, type LinkLayout } from './domain'

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
})
