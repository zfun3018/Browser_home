export type LinkLayout = { showName: boolean; showIcon: boolean; showDescription: boolean; order: number }
export type BookmarkLayoutOrder = Record<string, { order?: number }>

export function visibleFields(layout: LinkLayout) {
  return { name: layout.showName, icon: layout.showIcon, description: layout.showDescription }
}

export function buildSearchUrl(template: string, query: string): string {
  if (!/^https?:\/\//i.test(template)) throw new Error('Only HTTP(S) search engines are allowed')
  const url = new URL(template.replace('{query}', encodeURIComponent(query)))
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('Only HTTP(S) search engines are allowed')
  return url.toString()
}

export function safeHttpUrl(value: string): boolean {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password }
  catch { return false }
}

export function siteFaviconUrl(value: string, revision = ''): string | null {
  try {
    const url = new URL(value)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null
    const favicon = new URL('/favicon.ico', url.origin)
    if (revision) favicon.searchParams.set('v', revision)
    return favicon.toString()
  } catch { return null }
}

export function siteIconUrl(linkId: number, revision = 0): string {
  const refresh = revision ? `?refresh=1&v=${encodeURIComponent(String(revision))}` : ''
  return `/api/links/${linkId}/icon${refresh}`
}

export function reorderLinkIds<T extends { id: number; collectionId: number }>(
  links: T[], layouts: BookmarkLayoutOrder, collectionId: number, activeId: number, overId: number,
): { id: number; order: number }[] {
  const renderedIds = links
    .filter(link => link.collectionId === collectionId)
    .slice()
    .sort((a, b) => (layouts[String(a.id)]?.order ?? a.id) - (layouts[String(b.id)]?.order ?? b.id))
    .map(link => link.id)
  const oldIndex = renderedIds.indexOf(activeId)
  const newIndex = renderedIds.indexOf(overId)
  if (oldIndex < 0 || newIndex < 0 || oldIndex === newIndex) return []
  const reordered = [...renderedIds]
  const [moved] = reordered.splice(oldIndex, 1)
  reordered.splice(newIndex, 0, moved)
  return reordered.map((id, order) => ({ id, order }))
}
