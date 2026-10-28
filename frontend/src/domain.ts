export type LinkLayout = { showName: boolean; showIcon: boolean; showDescription: boolean; order: number }

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
