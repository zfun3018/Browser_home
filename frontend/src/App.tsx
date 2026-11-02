import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { DndContext, DragOverlay, KeyboardSensor, PointerSensor, closestCenter, pointerWithin, useDroppable, useSensor, useSensors, type CollisionDetection, type DragEndEvent, type DragStartEvent } from '@dnd-kit/core'
import { rectSortingStrategy, SortableContext, sortableKeyboardCoordinates, useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { ArrowDown, ArrowUp, BookOpen, Check, ChevronDown, ChevronRight, Command, LoaderCircle, Pencil, Plus, RefreshCw, Search, Settings2, Sparkles, Trash2, X } from 'lucide-react'
import { buildSearchUrl, reorderLinkIds, safeHttpUrl, siteIconUrl, type LinkLayout } from './domain'
import BackupControls from './components/BackupControls'
import './styles.css'

type Collection = { id: number; name: string; ownerId: number }
type Link = { id: number; name: string; url: string; description: string; icon: string | null; iconWeight: string | null; color: string | null; collectionId: number; iconSize?: number }
type HomeData = { collections: Collection[]; links: Link[]; preferences: { links: Record<string, Partial<LinkLayout>>; collections: Record<string, { collapsed?: boolean; order?: number }> }; sync: { status: 'ok' | 'error'; message?: string } }
type Provider = { id: string; name: string; url: string }
type SearchData = { providers: Provider[]; activeProviderId: string }
const defaults: SearchData = { providers: [{ id: 'google', name: 'Google', url: 'https://www.google.com/search?q={query}' }, { id: 'bing', name: 'Bing', url: 'https://www.bing.com/search?q={query}' }], activeProviderId: 'google' }
const emptyHome: HomeData = { collections: [], links: [], preferences: { links: {}, collections: {} }, sync: { status: 'ok' } }
const pointerFirstCollision: CollisionDetection = args => {
  const pointerCollisions = pointerWithin(args)
  const bookmark = pointerCollisions.find(collision => String(collision.id).startsWith('link-'))
  if (bookmark) return [bookmark]
  return pointerCollisions.length ? pointerCollisions : closestCenter(args)
}
async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw Object.assign(new Error(typeof body.detail === 'string' ? body.detail : '请求失败，请稍后重试'), { status: response.status })
  return body as T
}
function layoutFor(home: HomeData, linkId: number): LinkLayout {
  return { showName: true, showIcon: true, showDescription: true, order: 0, ...home.preferences.links[String(linkId)] }
}
export default function App() {
  const [home, setHome] = useState<HomeData>(emptyHome)
  const [search, setSearch] = useState<SearchData>(defaults)
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [offline, setOffline] = useState(false)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState<Link | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [dark, setDark] = useState(false)
  const [faviconRevision, setFaviconRevision] = useState<Record<number, number>>({})
  const [activeBookmark, setActiveBookmark] = useState<Link | null>(null)
  const orderSaveQueue = useRef<Promise<void>>(Promise.resolve())
  const orderSaveVersion = useRef(0)
  const refreshInFlight = useRef(false)

  const refresh = useCallback(async (quiet = false) => {
    if (refreshInFlight.current) return
    refreshInFlight.current = true
    if (!quiet) setLoading(true)
    setError('')
    try {
      const [nextHome, nextSearch] = await Promise.all([api<HomeData>('/api/home'), api<SearchData>('/api/search-engines')])
      setSearch(nextSearch)
      setOffline(nextHome.sync.status === 'error')
      if (nextHome.sync.status === 'error') setError(nextHome.sync.message || '本地书签库暂时不可用')
      else setHome(nextHome)
    } catch (err) {
      setOffline(true)
      setError(err instanceof Error ? err.message : '本地书签库暂时不可用')
    } finally { refreshInFlight.current = false; setLoading(false) }
  }, [])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    const poll = window.setInterval(() => { if (!document.hidden) void refresh(true) }, 15000)
    const focus = () => { void refresh(true) }
    window.addEventListener('focus', focus)
    return () => { window.clearInterval(poll); window.removeEventListener('focus', focus) }
  }, [refresh])

  useEffect(() => {
    if (loading || offline || document.hidden) return
    const now = Date.now()
    const hourKey = 'homepage-metadata-auto-last-at'
    try {
      const lastRun = Number(localStorage.getItem(hourKey) || 0)
      if (now - lastRun < 60 * 60 * 1000) return
      const candidate = home.links.find(link => {
        if (link.description.trim()) return false
        const key = `homepage-metadata-attempt-${link.id}-${encodeURIComponent(link.url)}`
        const lastAttempt = Number(localStorage.getItem(key) || 0)
        return now - lastAttempt >= 7 * 24 * 60 * 60 * 1000
      })
      if (!candidate) return
      localStorage.setItem(hourKey, String(now))
      localStorage.setItem(`homepage-metadata-attempt-${candidate.id}-${encodeURIComponent(candidate.url)}`, String(now))
      void refreshLinkMetadata(candidate, false)
    } catch { /* Storage can be disabled; manual refresh remains available. */ }
  }, [home.links, loading, offline])

  const collections = useMemo(() => [...home.collections].sort((a, b) => {
    const aa = home.preferences.collections[String(a.id)]?.order ?? a.id
    const bb = home.preferences.collections[String(b.id)]?.order ?? b.id
    return aa - bb
  }), [home.collections, home.preferences.collections])

  const linksByCollection = useMemo(() => {
    const grouped = new Map<number, Link[]>()
    for (const link of home.links) {
      const links = grouped.get(link.collectionId)
      if (links) links.push(link)
      else grouped.set(link.collectionId, [link])
    }
    for (const links of grouped.values()) {
      links.sort((a, b) => (home.preferences.links[String(a.id)]?.order ?? a.id) - (home.preferences.links[String(b.id)]?.order ?? b.id))
    }
    return grouped
  }, [home.links, home.preferences.links])

  async function patchLinkLayout(id: number, change: Partial<LinkLayout>) {
    const before = home
    setHome(current => ({ ...current, preferences: { ...current.preferences, links: { ...current.preferences.links, [id]: { ...layoutFor(current, id), ...change } } } }))
    try {
      const saved = await api<LinkLayout>(`/api/links/${id}/layout`, { method: 'PATCH', body: JSON.stringify(change) })
      setHome(current => ({ ...current, preferences: { ...current.preferences, links: { ...current.preferences.links, [id]: saved } } }))
    } catch (err) { setHome(before); setError(err instanceof Error ? err.message : '布局保存失败') }
  }
  function saveLinkOrders(changes: { id: number; order: number }[]) {
    if (!changes.length) return
    const previous = Object.fromEntries(changes.map(({ id }) => [id, home.preferences.links[String(id)]]))
    const version = ++orderSaveVersion.current
    setHome(current => ({ ...current, preferences: { ...current.preferences, links: { ...current.preferences.links, ...Object.fromEntries(changes.map(({ id, order }) => [id, { ...layoutFor(current, id), order }])) } } }))
    const persist = async () => {
      await api('/api/links/layouts', { method: 'PATCH', body: JSON.stringify({ orders: changes }) })
    }
    const pending = orderSaveQueue.current.then(persist, persist)
    orderSaveQueue.current = pending.catch(err => {
      if (version !== orderSaveVersion.current) return
      setHome(current => {
        const links = { ...current.preferences.links }
        for (const { id } of changes) {
          const previousLayout = previous[id]
          const latestLayout = { ...links[String(id)] }
          if (previousLayout?.order !== undefined) latestLayout.order = previousLayout.order
          else delete latestLayout.order
          if (Object.keys(latestLayout).length) links[String(id)] = latestLayout
          else delete links[String(id)]
        }
        return { ...current, preferences: { ...current.preferences, links } }
      })
      setError(err instanceof Error ? err.message : '顺序保存失败')
    })
  }
  async function patchCollectionLayout(id: number, change: { collapsed?: boolean; order?: number }) {
    const before = home
    setHome(current => ({ ...current, preferences: { ...current.preferences, collections: { ...current.preferences.collections, [id]: { ...current.preferences.collections[String(id)], ...change } } } }))
    try {
      const saved = await api<{ collapsed: boolean; order: number }>(`/api/collections/${id}/layout`, { method: 'PATCH', body: JSON.stringify(change) })
      setHome(current => ({ ...current, preferences: { ...current.preferences, collections: { ...current.preferences.collections, [id]: saved } } }))
    } catch (err) { setHome(before); setError(err instanceof Error ? err.message : '分组设置保存失败') }
  }
  async function createCollection(name: string) {
    await api<Collection>('/api/collections', { method: 'POST', body: JSON.stringify({ name }) })
    await refresh(true)
  }
  async function updateCollection(id: number, name: string) {
    await api<Collection>(`/api/collections/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) })
    await refresh(true)
  }
  async function deleteCollection(id: number) {
    await api<void>(`/api/collections/${id}`, { method: 'DELETE' })
    await refresh(true)
  }
  async function deleteLink(link: Link) {
    if (!window.confirm(`确定删除网址“${link.name}”吗？`)) return
    try {
      await api<void>(`/api/links/${link.id}`, { method: 'DELETE' })
      setHome(current => {
        const links = current.links.filter(item => item.id !== link.id)
        const preferences = { ...current.preferences.links }
        delete preferences[String(link.id)]
        return { ...current, links, preferences: { ...current.preferences, links: preferences } }
      })
      setFaviconRevision(current => {
        const next = { ...current }
        delete next[link.id]
        return next
      })
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除网址失败')
    }
  }
  async function saveCanonical(link: Link, values: Partial<Link>, optimisticMove = false) {
    const changed = Object.fromEntries(Object.entries(values).filter(([key, value]) => link[key as keyof Link] !== value)) as Partial<Link>
    if (!Object.keys(changed).length) { setEditing(null); return }
    const expected = Object.fromEntries(Object.keys(changed).map(key => [key === 'collectionId' ? key : key, link[key as keyof Link]]))
    const payload = { ...changed, expected }
    if (optimisticMove && typeof changed.collectionId === 'number') {
      setHome(current => ({ ...current, links: current.links.map(item => item.id === link.id ? { ...item, collectionId: changed.collectionId! } : item) }))
    }
    try {
      const saved = await api<Link>(`/api/links/${link.id}`, { method: 'PATCH', body: JSON.stringify(payload) })
      setHome(current => ({ ...current, links: current.links.map(item => item.id === link.id ? saved : item) }))
      setEditing(null)
    } catch (err) {
      if ((err as { status?: number }).status === 409) {
        setError('这个网址在本地书签库中也有更新。已刷新当前数据，请重新打开编辑并确认。')
        await refresh(true)
        setEditing(null)
      } else {
        if (optimisticMove && typeof changed.collectionId === 'number') {
          setHome(current => ({ ...current, links: current.links.map(item => item.id === link.id && item.collectionId === changed.collectionId ? { ...item, collectionId: link.collectionId } : item) }))
        }
        setError(err instanceof Error ? err.message : '网址更新失败')
      }
    }
  }

  async function refreshLinkMetadata(link: Link, manual = true) {
    try {
      const metadata = await api<{ description: string }>(`/api/links/${link.id}/metadata`, { method: 'POST' })
      const description = metadata.description.trim()
      if (!description) { if (manual) setError('未找到网站简介；已重新请求网站图标'); return }
      if (!manual && link.description.trim()) return
      if (link.description.trim() && link.description.trim() !== description && !window.confirm(`网站简介：\n${description.slice(0, 500)}\n\n要覆盖本地书签中的现有简介吗？`)) {
        return
      }
      await saveCanonical(link, { description })
    } catch (err) {
      if (manual) {
        const message = err instanceof Error ? err.message : '拉取网站简介失败'
        setError(message.includes('不能抓取内网或本机地址')
          ? '服务端将此域名解析到了非公网地址，已安全拦截简介抓取；网站图标已重新请求，请检查 DNS 或代理设置。'
          : `${message}；网站图标已重新请求`)
      }
    } finally {
      setFaviconRevision(current => ({ ...current, [link.id]: Date.now() }))
    }
  }

  async function saveCustomIcon(link: Link, data: Blob) {
    const response = await fetch(`/api/links/${link.id}/icon/custom`, { method: 'PUT', body: data, headers: { 'Content-Type': data.type || 'application/octet-stream' } })
    const body = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '自定义图标保存失败')
    setFaviconRevision(current => ({ ...current, [link.id]: Date.now() }))
  }

  async function fetchOnlineIcon(link: Link) {
    const result = await api<{ contentType: string }>(`/api/links/${link.id}/icon/fetch`, { method: 'POST' })
    setFaviconRevision(current => ({ ...current, [link.id]: Date.now() }))
    return result
  }

  async function saveIconSize(link: Link, size: number) {
    await api(`/api/links/${link.id}/icon/size?size=${size}`, { method: 'PATCH' })
    setHome(current => ({ ...current, links: current.links.map(item => item.id === link.id ? { ...item, iconSize: size } : item) }))
  }

  function handleDragEnd(event: DragEndEvent) {
    setActiveBookmark(null)
    const activeId = Number(String(event.active.id).replace('link-', ''))
    if (!event.over) return
    const overString = String(event.over.id)
    const item = home.links.find(link => link.id === activeId)
    if (!item) return
    if (overString.startsWith('collection-')) {
      const target = Number(overString.slice('collection-'.length))
      if (target !== item.collectionId) void saveCanonical(item, { collectionId: target }, true)
      return
    }
    if (overString.startsWith('link-')) {
      const overId = Number(overString.slice('link-'.length))
      const over = home.links.find(link => link.id === overId)
      if (over && over.collectionId !== item.collectionId) { void saveCanonical(item, { collectionId: over.collectionId }, true); return }
      saveLinkOrders(reorderLinkIds(home.links, home.preferences.links, item.collectionId, activeId, overId))
    }
  }
  function handleDragStart(event: DragStartEvent) {
    const activeId = Number(String(event.active.id).replace('link-', ''))
    setActiveBookmark(home.links.find(link => link.id === activeId) || null)
  }
  const activeSearch = search.providers.find(provider => provider.id === search.activeProviderId) || search.providers[0]
  function submitSearch(event: React.FormEvent) {
    event.preventDefault()
    if (!activeSearch || !query.trim()) return
    try { window.location.assign(buildSearchUrl(activeSearch.url, query.trim())) }
    catch (err) { setError(err instanceof Error ? err.message : '搜索配置无效') }
  }

  return <main className={dark ? 'app app-dark' : 'app'}>
    <header className="topbar"><div className="top-actions"><span className="sync-indicator"><i className={offline ? 'dot dot-error' : 'dot'}/>{offline ? '连接异常' : '本地书签已同步'}</span><button className="icon-button" title="刷新" aria-label="刷新" onClick={() => void refresh()}><RefreshCw size={16}/></button><button className="icon-button" title="切换深色主题" aria-label="切换深色主题" onClick={() => setDark(v => !v)}><Sparkles size={17}/></button><button className="icon-button" title="设置" aria-label="设置" onClick={() => setSettingsOpen(true)}><Settings2 size={18}/></button><BackupControls onImported={() => void refresh(true)} /></div></header>
    <section className="welcome"><form className="searchbar" onSubmit={submitSearch}><Search size={21} className="search-icon"/><input aria-label="搜索网页" placeholder="搜索任何内容…" value={query} onChange={event => setQuery(event.target.value)}/>{search.providers.length > 1 && <select aria-label="搜索引擎" value={activeSearch?.id || ''} onChange={event => { const next = { ...search, activeProviderId: event.target.value }; setSearch(next); void api<SearchData>('/api/search-engines', { method: 'PUT', body: JSON.stringify(next) }).then(setSearch).catch(err => setError(err instanceof Error ? err.message : '搜索配置保存失败')) }}>{search.providers.map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</select>}<button type="submit" aria-label="开始搜索" className="search-submit"><Command size={14}/> Enter</button></form></section>
    {error && <div className={offline ? 'notice notice-offline' : 'notice'} role="status"><span>{offline ? '本地数据需要检查' : '提示'}</span><div>{offline && home.links.length ? '暂时无法读取本地书签库，正在显示上次加载的网址。' : error}</div><button aria-label="关闭提示" onClick={() => setError('')}><X size={16}/></button></div>}
    {loading && !home.collections.length && <div className="loading-state"><LoaderCircle className="spin" size={22}/><span>正在整理你的空间…</span></div>}
    {!loading && !home.collections.length && !offline && <div className="empty-state"><div className="empty-icon"><BookOpen size={25}/></div><h2>你的空间还空着</h2><p>先使用浏览器扩展一键收藏网址，或在设置中准备好默认分组。</p></div>}
    <div className="home-workspace">
      <DndContext sensors={useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }))} collisionDetection={pointerFirstCollision} onDragStart={handleDragStart} onDragEnd={handleDragEnd} onDragCancel={() => setActiveBookmark(null)}>
        <div className="collections">{collections.map((collection, index) => <CollectionPanel key={collection.id} collection={collection} links={linksByCollection.get(collection.id) || []} index={index} home={home} faviconRevision={faviconRevision} onRefreshMetadata={refreshLinkMetadata} onDelete={deleteLink} onCollapse={collapsed => void patchCollectionLayout(collection.id, { collapsed })} onMoveGroup={delta => {
          const current = collections[index], target = collections[index + delta]
          if (!target) return
          void patchCollectionLayout(current.id, { order: index + delta })
          void patchCollectionLayout(target.id, { order: index })
        }} onEdit={setEditing} onLayout={patchLinkLayout} />)}</div>
        <DragOverlay dropAnimation={{ duration: 180, easing: 'cubic-bezier(0.18, 0.67, 0.6, 1.22)' }}>{activeBookmark && <BookmarkDragPreview link={activeBookmark} layout={layoutFor(home, activeBookmark.id)} faviconRevision={faviconRevision[activeBookmark.id] || 0}/>}</DragOverlay>
      </DndContext>
    </div>
    <footer className="footer"><span>MADE FOR YOUR EVERYDAY</span><span>书签由本地 SQLite 管理 <span className="footer-dot">·</span> 轻松开始</span></footer>
    {editing && <LinkEditor link={editing} collections={home.collections} onClose={() => setEditing(null)} onSave={values => void saveCanonical(editing, values)} onSaveIcon={data => void saveCustomIcon(editing, data)} onFetchIcon={() => fetchOnlineIcon(editing)} onSaveIconSize={size => saveIconSize(editing, size)} />}
    {settingsOpen && <SettingsDialog search={search} setSearch={setSearch} collections={home.collections} dark={dark} onDark={setDark} onClose={() => setSettingsOpen(false)} onSaved={() => void refresh(true)} onCreateCollection={createCollection} onUpdateCollection={updateCollection} onDeleteCollection={deleteCollection} />}
  </main>
}

function CollectionPanel({ collection, links, index, home, faviconRevision, onRefreshMetadata, onDelete, onCollapse, onMoveGroup, onEdit, onLayout }: { collection: Collection; links: Link[]; index: number; home: HomeData; faviconRevision: Record<number, number>; onRefreshMetadata: (link: Link) => void; onDelete: (link: Link) => Promise<void>; onCollapse: (value: boolean) => void; onMoveGroup: (delta: number) => void; onEdit: (link: Link) => void; onLayout: (id: number, change: Partial<LinkLayout>) => Promise<void> | void }) {
  const collapsed = home.preferences.collections[String(collection.id)]?.collapsed ?? false
  const { setNodeRef, isOver } = useDroppable({ id: `collection-${collection.id}` })
  return <section className={`collection ${isOver ? 'collection-drop' : ''}`} ref={setNodeRef} aria-label={`${collection.name} 分组`}>
    <div className="collection-heading"><span className="section-kicker">{String(index + 1).padStart(2, '0')}</span><button className="collection-title" aria-expanded={!collapsed} aria-label={`${collapsed ? '展开' : '收起'} ${collection.name}`} onClick={() => onCollapse(!collapsed)}>{collapsed ? <ChevronRight size={17}/> : <ChevronDown size={17}/>}<h2>{collection.name}</h2><span className="count-pill">{links.length}</span></button><div className="collection-tools" aria-label={`调整分组 ${collection.name} 顺序`}><button className="tiny-button" aria-label={`上移分组 ${collection.name}`} onClick={() => onMoveGroup(-1)}><ArrowUp size={14}/></button><button className="tiny-button" aria-label={`下移分组 ${collection.name}`} onClick={() => onMoveGroup(1)}><ArrowDown size={14}/></button></div><span className="heading-rule"/></div>
    {!collapsed && <SortableContext items={links.map(link => `link-${link.id}`)} strategy={rectSortingStrategy}><div className="bookmark-grid" role="list">{links.map(link => <SortableBookmark key={link.id} link={link} faviconRevision={faviconRevision[link.id] || 0} layout={layoutFor(home, link.id)} onRefreshMetadata={onRefreshMetadata} onDelete={onDelete} onEdit={onEdit} onLayout={onLayout}/>)}</div></SortableContext>}
    {!collapsed && links.length === 0 && <p className="empty-collection">拖动网址到这里，或使用浏览器扩展一键收藏。</p>}
  </section>
}

function SortableBookmark({ link, layout, faviconRevision, onRefreshMetadata, onDelete, onEdit, onLayout }: { link: Link; layout: LinkLayout; faviconRevision: number; onRefreshMetadata: (link: Link) => void; onDelete: (link: Link) => Promise<void>; onEdit: (link: Link) => void; onLayout: (id: number, change: Partial<LinkLayout>) => Promise<void> | void }) {
  const sortable = useSortable({ id: `link-${link.id}` })
  const [menu, setMenu] = useState<{ left: number; top: number } | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const style = { transform: CSS.Transform.toString(sortable.transform), transition: sortable.transition }
  useEffect(() => {
    if (!menu) return
    const dismiss = (event: PointerEvent) => { if (!menuRef.current?.contains(event.target as Node)) setMenu(null) }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenu(null) }
    window.addEventListener('pointerdown', dismiss)
    window.addEventListener('keydown', escape)
    return () => { window.removeEventListener('pointerdown', dismiss); window.removeEventListener('keydown', escape) }
  }, [menu])
  function openMenu(x: number, y: number) {
    setMenu({ left: Math.max(8, Math.min(x, window.innerWidth - 224)), top: Math.max(8, Math.min(y, window.innerHeight - 282)) })
  }
  function handleContextMenu(event: React.MouseEvent<HTMLAnchorElement>) {
    event.preventDefault()
    event.stopPropagation()
    openMenu(event.clientX, event.clientY)
  }
  function handleKeyDown(event: React.KeyboardEvent<HTMLAnchorElement>) {
    if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
      event.preventDefault()
      const bounds = event.currentTarget.getBoundingClientRect()
      openMenu(bounds.left + 12, bounds.bottom)
    }
  }
  const safe = safeHttpUrl(link.url)
  return <article ref={sortable.setNodeRef} style={style} className={`bookmark ${sortable.isDragging ? 'bookmark-dragging' : ''}`} role="listitem">
    <a className="bookmark-link" href={safe ? link.url : '#'} target={safe ? '_blank' : undefined} rel={safe ? 'noreferrer' : undefined} aria-label={`${link.name}，打开网址`} {...sortable.listeners} onClick={event => { if (!safe) event.preventDefault() }} onContextMenu={handleContextMenu} onKeyDown={handleKeyDown}>
      {layout.showIcon && <BookmarkSiteIcon link={link} revision={faviconRevision}/>}
      {layout.showName && <span className="bookmark-name">{link.name}</span>}
      {layout.showDescription && link.description.trim() && <span className="bookmark-description" role="tooltip">{link.description}</span>}
    </a>
    {menu && <div ref={menuRef} className="bookmark-context-menu" role="menu" aria-label={`${link.name} 设置`} style={menu} onPointerDown={event => event.stopPropagation()}>
      <button role="menuitemcheckbox" aria-checked={layout.showName} onClick={() => { void onLayout(link.id, { showName: !layout.showName }); setMenu(null) }}>显示网站名</button>
      <button role="menuitemcheckbox" aria-checked={layout.showIcon} onClick={() => { void onLayout(link.id, { showIcon: !layout.showIcon }); setMenu(null) }}>显示网站图标</button>
      <button role="menuitemcheckbox" aria-checked={layout.showDescription} onClick={() => { void onLayout(link.id, { showDescription: !layout.showDescription }); setMenu(null) }}>悬停显示简介</button>
      <div className="bookmark-menu-divider"/>
      <button role="menuitem" onClick={() => { onRefreshMetadata(link); setMenu(null) }}><RefreshCw size={14}/>刷新网站信息</button>
      <button role="menuitem" onClick={() => { onEdit(link); setMenu(null) }}><Pencil size={14}/>编辑网址</button>
      <button role="menuitem" className="danger-menu-item" onClick={() => { setMenu(null); void onDelete(link) }}><Trash2 size={14}/>删除网址</button>
    </div>}
  </article>
}

function BookmarkDragPreview({ link, layout, faviconRevision }: { link: Link; layout: LinkLayout; faviconRevision: number }) {
  return <div className="bookmark-drag-preview" aria-hidden="true">
    {layout.showIcon && <BookmarkSiteIcon link={link} revision={faviconRevision}/>}
    {layout.showName && <span className="bookmark-name">{link.name}</span>}
  </div>
}

function BookmarkSiteIcon({ link, revision }: { link: Link; revision: number }) {
  const [failed, setFailed] = useState(false)
  useEffect(() => { setFailed(false) }, [link.url, link.icon, link.iconWeight, revision])
  const favicon = siteIconUrl(link.id, revision)
  const size = link.iconSize || 52
  if (failed) return <span className="bookmark-icon" style={{ color: link.color || undefined, width: size, height: size, flexBasis: size }} aria-label={`${link.name} 图标`}><BookOpen size={size * .68} strokeWidth={1.5} aria-hidden="true"/></span>
  return <span className="bookmark-icon" style={{ color: link.color || undefined, width: size, height: size, flexBasis: size }}><img src={favicon} alt={`${link.name}网站图标`} loading="lazy" onError={() => setFailed(true)}/></span>
}

function LinkEditor({ link, collections, onClose, onSave, onSaveIcon, onFetchIcon, onSaveIconSize }: { link: Link; collections: Collection[]; onClose: () => void; onSave: (value: Partial<Link>) => void; onSaveIcon: (data: Blob) => Promise<void> | void; onFetchIcon: () => Promise<{ contentType: string }>; onSaveIconSize: (size: number) => Promise<void> }) {
  const [values, setValues] = useState({ name: link.name, url: link.url, description: link.description || '', icon: link.icon || '', iconWeight: link.iconWeight || 'regular', color: link.color || '#57745f', collectionId: link.collectionId })
  const [iconPreview, setIconPreview] = useState<string | null>(`/api/links/${link.id}/icon?v=${Date.now()}`)
  const [iconError, setIconError] = useState('')
  const [iconBusy, setIconBusy] = useState(false)
  const [iconSize, setIconSize] = useState(link.iconSize || 52)
  async function saveIcon(data: Blob) {
    setIconError(''); setIconBusy(true)
    try { await onSaveIcon(data); setIconPreview(URL.createObjectURL(data)) }
    catch (error) { setIconError(error instanceof Error ? error.message : '自定义图标保存失败') }
    finally { setIconBusy(false) }
  }
  async function fetchIcon() {
    setIconError(''); setIconBusy(true)
    try { await onFetchIcon(); setIconPreview(`/api/links/${link.id}/icon?v=${Date.now()}`) }
    catch (error) { setIconError(error instanceof Error ? error.message : '在线获取图标失败') }
    finally { setIconBusy(false) }
  }
  async function resizeIcon(value: number) {
    setIconSize(value)
    try {
      await onSaveIconSize(value)
    } catch (error) { setIconError(error instanceof Error ? error.message : '图标大小保存失败') }
  }
  return <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}><form className="dialog" aria-label={`编辑 ${link.name}`} onPaste={event => { const item = Array.from(event.clipboardData.items).find(item => item.type.startsWith('image/')); if (item) { event.preventDefault(); const file = item.getAsFile(); if (file) void saveIcon(file) } }} onSubmit={event => { event.preventDefault(); if (!safeHttpUrl(values.url)) return; onSave({ ...values, icon: values.icon || null, color: values.color || null }) }}>
    <div className="dialog-heading"><div><span className="dialog-kicker">LOCAL · BOOKMARK</span><h2>编辑网址</h2></div><button type="button" className="icon-button" aria-label="关闭" onClick={onClose}><X size={18}/></button></div>
    <label>名称<input autoFocus value={values.name} onChange={e => setValues({ ...values, name: e.target.value })}/></label><label>网址<input value={values.url} type="url" required onChange={e => setValues({ ...values, url: e.target.value })}/></label><label>简介<textarea rows={3} value={values.description} onChange={e => setValues({ ...values, description: e.target.value })}/></label><div className="form-row"><label>图标名称<input value={values.icon} onChange={e => setValues({ ...values, icon: e.target.value })} placeholder="book-open"/></label><label>图标字重<select aria-label="图标字重" value={values.iconWeight} onChange={e => setValues({ ...values, iconWeight: e.target.value })}>{['thin','light','regular','bold','fill','duotone'].map(weight => <option key={weight} value={weight}>{weight}</option>)}</select></label></div><div className="form-row"><label>强调色<input type="color" value={values.color} onChange={e => setValues({ ...values, color: e.target.value })}/></label><span/></div><label>分组<select aria-label="分组" value={values.collectionId} onChange={e => setValues({ ...values, collectionId: Number(e.target.value) })}>{collections.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
    <div className="icon-customizer"><div className="icon-customizer-heading"><b>网站图标</b><small>支持上传任意尺寸图片，也可直接粘贴图片</small></div><div className="icon-customizer-actions"><label className="secondary-button icon-upload"><input type="file" accept="image/*" onChange={event => { const file = event.target.files?.[0]; if (file) void saveIcon(file); event.currentTarget.value = '' }}/><Plus size={14}/>上传图标</label><button type="button" className="secondary-button" disabled={iconBusy} onClick={() => void fetchIcon()}><RefreshCw size={14}/>在线获取并替换</button></div>{iconPreview && <img className="icon-custom-preview" style={{ width: iconSize, height: iconSize }} src={iconPreview} alt="自定义图标预览"/>}<label className="icon-size-control">显示大小：{iconSize}px<input type="range" min="24" max="96" step="1" value={iconSize} onChange={event => void resizeIcon(Number(event.target.value))}/></label>{iconError && <p className="field-error">{iconError}</p>}<small className="icon-customizer-hint">图标会保存到本地 SQLite，在线获取会直接替换当前自定义图标。</small></div>
    {!safeHttpUrl(values.url) && <p className="field-error">请填写有效的 HTTP 或 HTTPS 网址。</p>}<div className="dialog-actions"><button type="button" className="secondary-button" onClick={onClose}>取消</button><button type="submit" className="primary-button"><Check size={15}/> 保存书签</button></div>
  </form></div>
}

function SettingsDialog({ search, setSearch, collections, dark, onDark, onClose, onSaved, onCreateCollection, onUpdateCollection, onDeleteCollection }: { search: SearchData; setSearch: (value: SearchData) => void; collections: Collection[]; dark: boolean; onDark: (value: boolean) => void; onClose: () => void; onSaved: () => void; onCreateCollection: (name: string) => Promise<void>; onUpdateCollection: (id: number, name: string) => Promise<void>; onDeleteCollection: (id: number) => Promise<void> }) {
  const [providers, setProviders] = useState(search.providers)
  const [active, setActive] = useState(search.activeProviderId)
  const [error, setError] = useState('')
  const [collectionNames, setCollectionNames] = useState<Record<number, string>>(() => Object.fromEntries(collections.map(collection => [collection.id, collection.name])))
  const [newCollection, setNewCollection] = useState('')
  const [collectionError, setCollectionError] = useState('')
  async function save() {
    try {
      const result = await api<SearchData>('/api/search-engines', { method: 'PUT', body: JSON.stringify({ providers, activeProviderId: active }) })
      setSearch(result); onSaved(); onClose()
    } catch (err) { setError(err instanceof Error ? err.message : '搜索配置保存失败') }
  }
  async function addCollection() {
    const name = newCollection.trim()
    if (!name) return
    try { await onCreateCollection(name); setNewCollection(''); setCollectionError('') }
    catch (err) { setCollectionError(err instanceof Error ? err.message : '新增分组失败') }
  }
  async function renameCollection(id: number) {
    const name = (collectionNames[id] || '').trim()
    if (!name) return
    try { await onUpdateCollection(id, name); setCollectionError('') }
    catch (err) { setCollectionError(err instanceof Error ? err.message : '保存分组失败') }
  }
  async function removeCollection(collection: Collection) {
    if (!window.confirm(`确定删除分组“${collection.name}”吗？`)) return
    try { await onDeleteCollection(collection.id); setCollectionError('') }
    catch (err) { setCollectionError(err instanceof Error ? err.message : '删除分组失败') }
  }
  return <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}><div className="dialog settings-dialog" role="dialog" aria-modal="true" aria-label="首页设置"><div className="dialog-heading"><div><span className="dialog-kicker">PERSONAL PREFERENCES</span><h2>首页设置</h2></div><button className="icon-button" aria-label="关闭" onClick={onClose}><X size={18}/></button></div><label className="switch-row"><span><b>深色外观</b><small>适合夜间浏览</small></span><input type="checkbox" checked={dark} onChange={e => onDark(e.target.checked)}/></label><div className="settings-section-title"><div><b>分组管理</b><small>新增、重命名或删除空分组</small></div></div><div className="collection-create-row"><input aria-label="新分组名称" placeholder="新分组名称" value={newCollection} onChange={e => setNewCollection(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); void addCollection() } }}/><button className="add-provider" onClick={() => void addCollection()}><Plus size={14}/>新增分组</button></div><div className="collection-manage-list">{collections.map(collection => <div className="collection-manage-row" key={collection.id}><input aria-label={`分组名称 ${collection.name}`} value={collectionNames[collection.id] || ''} onChange={e => setCollectionNames({ ...collectionNames, [collection.id]: e.target.value })}/><button className="tiny-button" aria-label={`保存分组 ${collection.name}`} onClick={() => void renameCollection(collection.id)}><Check size={15}/></button><button className="tiny-button danger-button" aria-label={`删除分组 ${collection.name}`} onClick={() => void removeCollection(collection)}><Trash2 size={15}/></button></div>)}</div>{collectionError && <p className="field-error">{collectionError}</p>}<div className="settings-section-title"><div><b>搜索引擎</b><small>使用 {`{query}`} 作为搜索词</small></div><button className="add-provider" onClick={() => setProviders([...providers, { id: `engine-${Date.now()}`, name: '新搜索引擎', url: 'https://example.com/search?q={query}' }])}><Plus size={14}/>添加</button></div>{providers.map((p, index) => <div className="provider-row" key={p.id}><input aria-label={`引擎名称 ${index + 1}`} value={p.name} onChange={e => setProviders(providers.map(item => item.id === p.id ? { ...item, name: e.target.value } : item))}/><input aria-label={`搜索地址 ${index + 1}`} value={p.url} onChange={e => setProviders(providers.map(item => item.id === p.id ? { ...item, url: e.target.value } : item))}/><label className="radio-label"><input type="radio" name="active-search" checked={active === p.id} onChange={() => setActive(p.id)}/> 默认</label><button className="remove-provider" aria-label={`删除 ${p.name}`} disabled={providers.length < 2} onClick={() => { const next = providers.filter(item => item.id !== p.id); setProviders(next); if (active === p.id) setActive(next[0]?.id || '') }}><X size={14}/></button></div>)}{error && <p className="field-error">{error}</p>}<div className="dialog-actions"><button className="secondary-button" onClick={onClose}>关闭</button><button className="primary-button" onClick={() => void save()}><Check size={15}/> 保存设置</button></div></div></div>
}
