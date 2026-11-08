import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { DndContext, DragOverlay, KeyboardSensor, PointerSensor, closestCenter, pointerWithin, useDroppable, useSensor, useSensors, type CollisionDetection, type DragEndEvent, type DragStartEvent } from '@dnd-kit/core'
import { arrayMove, rectSortingStrategy, SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { createPortal } from 'react-dom'
import { ArrowDown, ArrowUp, BookOpen, Check, ChevronDown, ChevronRight, Command, GripVertical, LoaderCircle, LockKeyhole, Pencil, Plus, RefreshCw, Search, Trash2, X } from 'lucide-react'
import { buildSearchUrl, reorderLinkIds, safeHttpUrl, siteIconUrl, type LinkLayout } from './domain'
import BackupControls from './components/BackupControls'
import MaterialIcon from './components/MaterialIcon'
import './styles.css'

type Collection = { id: number; name: string; ownerId: number; linkCount?: number }
type Link = { id: number; name: string; url: string; description: string; icon: string | null; iconWeight: string | null; color: string | null; collectionId: number; iconSize?: number; createdAt?: string | null }
type CollectionPreference = { collapsed?: boolean; order?: number; displayMode?: 'grid' | 'list'; locked?: boolean }
type HomeData = { collections: Collection[]; links: Link[]; preferences: { links: Record<string, Partial<LinkLayout>>; collections: Record<string, CollectionPreference> }; sync: { status: 'ok' | 'error'; message?: string } }
type Provider = { id: string; name: string; url: string }
type SearchData = { providers: Provider[]; activeProviderId: string }
type UnlockResponse = { collectionId: number; unlockToken: string; links: Link[] }
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
function mergeLinks(visible: Link[], unlocked: Record<number, Link[]>): Link[] {
  const byId = new Map<number, Link>(visible.map(link => [link.id, link]))
  for (const links of Object.values(unlocked)) for (const link of links) byId.set(link.id, link)
  return [...byId.values()]
}
function favoriteTime(value?: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date)
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
  const [collectionCollapsedState, setCollectionCollapsedState] = useState<Record<number, boolean>>({})
  const [unlockedCollections, setUnlockedCollections] = useState<Record<number, boolean>>({})
  const unlockedLinksRef = useRef<Record<number, Link[]>>({})
  const [unlockingCollection, setUnlockingCollection] = useState<Collection | null>(null)
  const [activeBookmark, setActiveBookmark] = useState<Link | null>(null)
  const orderSaveQueue = useRef<Promise<void>>(Promise.resolve())
  const orderSaveVersion = useRef(0)
  const refreshInFlight = useRef(false)
  const lastQuietRefreshAt = useRef(0)

  const refresh = useCallback(async (quiet = false) => {
    if (refreshInFlight.current) return
    if (quiet && Date.now() - lastQuietRefreshAt.current < 2000) return
    if (quiet) lastQuietRefreshAt.current = Date.now()
    refreshInFlight.current = true
    if (!quiet) setLoading(true)
    setError('')
    try {
      const [nextHome, nextSearch] = await Promise.all([api<HomeData>('/api/home'), api<SearchData>('/api/search-engines')])
      setSearch(nextSearch)
      setOffline(nextHome.sync.status === 'error')
      if (nextHome.sync.status === 'error') setError(nextHome.sync.message || '本地书签库暂时不可用')
      else setHome({ ...nextHome, links: mergeLinks(nextHome.links, unlockedLinksRef.current) })
    } catch (err) {
      setOffline(true)
      setError(err instanceof Error ? err.message : '本地书签库暂时不可用')
    } finally { refreshInFlight.current = false; setLoading(false) }
  }, [])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    const poll = window.setInterval(() => { if (!document.hidden) void refresh(true) }, 60000)
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
  async function patchCollectionLayout(id: number, change: { collapsed?: boolean; order?: number; displayMode?: 'grid' | 'list' }) {
    if (change.collapsed !== undefined) {
      setCollectionCollapsedState(current => {
        const next = { ...current }
        delete next[id]
        return next
      })
    }
    const before = home
    setHome(current => ({ ...current, preferences: { ...current.preferences, collections: { ...current.preferences.collections, [id]: { ...current.preferences.collections[String(id)], ...change } } } }))
    try {
      const saved = await api<{ collapsed: boolean; order: number }>(`/api/collections/${id}/layout`, { method: 'PATCH', body: JSON.stringify(change) })
      setHome(current => ({ ...current, preferences: { ...current.preferences, collections: { ...current.preferences.collections, [id]: saved } } }))
    } catch (err) { setHome(before); setError(err instanceof Error ? err.message : '分组设置保存失败') }
  }
  async function setCollectionPassword(id: number, password: string) {
    await api(`/api/collections/${id}/password`, { method: 'PUT', body: JSON.stringify({ password }) })
    const nextUnlocked = { ...unlockedLinksRef.current }
    delete nextUnlocked[id]
    unlockedLinksRef.current = nextUnlocked
    setUnlockedCollections(current => { const next = { ...current }; delete next[id]; return next })
    await refresh(true)
  }
  async function clearCollectionPassword(id: number) {
    await api(`/api/collections/${id}/password`, { method: 'DELETE' })
    const nextUnlocked = { ...unlockedLinksRef.current }
    delete nextUnlocked[id]
    unlockedLinksRef.current = nextUnlocked
    setUnlockedCollections(current => { const next = { ...current }; delete next[id]; return next })
    await refresh(true)
  }
  async function unlockCollection(id: number, password: string) {
    const result = await api<UnlockResponse>(`/api/collections/${id}/unlock`, { method: 'POST', body: JSON.stringify({ password }) })
    const nextUnlocked = { ...unlockedLinksRef.current, [id]: result.links }
    unlockedLinksRef.current = nextUnlocked
    setUnlockedCollections(current => ({ ...current, [id]: true }))
    setHome(current => ({ ...current, links: mergeLinks(current.links, { [id]: result.links }) }))
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
      if (home.preferences.collections[String(target)]?.locked && !unlockedCollections[target]) {
        setError('请先解锁目标分类，再移动网址。')
        return
      }
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
    <header className="topbar"><div className="top-actions"><span className="sync-indicator"><i className={offline ? 'dot dot-error' : 'dot'}/>{offline ? '连接异常' : '本地书签已同步'}</span><button className="icon-button" title="刷新" aria-label="刷新" onClick={() => void refresh()}><MaterialIcon name="refresh" /></button><button className="icon-button" title="切换深色主题" aria-label="切换深色主题" onClick={() => setDark(v => !v)}><MaterialIcon name="auto_awesome" /></button><button className="icon-button" title="设置" aria-label="设置" onClick={() => setSettingsOpen(true)}><MaterialIcon name="tune" /></button><BackupControls onImported={() => void refresh(true)} /></div></header>
    <section className="welcome"><form className="searchbar" onSubmit={submitSearch}><Search size={21} className="search-icon"/><input aria-label="搜索网页" placeholder="搜索任何内容…" value={query} onChange={event => setQuery(event.target.value)}/>{search.providers.length > 1 && <select aria-label="搜索引擎" value={activeSearch?.id || ''} onChange={event => { const next = { ...search, activeProviderId: event.target.value }; setSearch(next); void api<SearchData>('/api/search-engines', { method: 'PUT', body: JSON.stringify(next) }).then(setSearch).catch(err => setError(err instanceof Error ? err.message : '搜索配置保存失败')) }}>{search.providers.map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</select>}<button type="submit" aria-label="开始搜索" className="search-submit"><Command size={14}/> Enter</button></form></section>
    {error && <div className={offline ? 'notice notice-offline' : 'notice'} role="status"><span>{offline ? '本地数据需要检查' : '提示'}</span><div>{offline && home.links.length ? '暂时无法读取本地书签库，正在显示上次加载的网址。' : error}</div><button aria-label="关闭提示" onClick={() => setError('')}><X size={16}/></button></div>}
    {loading && !home.collections.length && <div className="loading-state"><LoaderCircle className="spin" size={22}/><span>正在整理你的空间…</span></div>}
    {!loading && !home.collections.length && !offline && <div className="empty-state"><div className="empty-icon"><BookOpen size={25}/></div><h2>你的空间还空着</h2><p>先使用浏览器扩展一键收藏网址，或在设置中准备好默认分组。</p></div>}
    <div className="home-workspace">
      <DndContext sensors={useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }))} collisionDetection={pointerFirstCollision} onDragStart={handleDragStart} onDragEnd={handleDragEnd} onDragCancel={() => setActiveBookmark(null)}>
        <div className="collections">{collections.map((collection, index) => <CollectionPanel key={collection.id} collection={collection} links={linksByCollection.get(collection.id) || []} index={index} home={home} sessionCollapsed={collectionCollapsedState[collection.id]} unlocked={Boolean(unlockedCollections[collection.id])} faviconRevision={faviconRevision} onRefreshMetadata={refreshLinkMetadata} onDelete={deleteLink} onUnlock={() => setUnlockingCollection(collection)} onCollapse={collapsed => setCollectionCollapsedState(current => ({ ...current, [collection.id]: collapsed }))} onMoveGroup={delta => {
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
    {settingsOpen && <SettingsDialog search={search} setSearch={setSearch} collections={collections} collectionPreferences={home.preferences.collections} dark={dark} onDark={setDark} onClose={() => setSettingsOpen(false)} onSaved={() => void refresh(true)} onCreateCollection={createCollection} onUpdateCollection={updateCollection} onDeleteCollection={deleteCollection} onCollectionLayout={(id, change) => patchCollectionLayout(id, change)} onSetCollectionPassword={setCollectionPassword} onClearCollectionPassword={clearCollectionPassword} />}
    {unlockingCollection && <CollectionUnlockDialog collection={unlockingCollection} onClose={() => setUnlockingCollection(null)} onUnlock={password => unlockCollection(unlockingCollection.id, password)} />}
  </main>
}

function CollectionPanel({ collection, links, index, home, sessionCollapsed, unlocked, faviconRevision, onRefreshMetadata, onDelete, onUnlock, onCollapse, onMoveGroup, onEdit, onLayout }: { collection: Collection; links: Link[]; index: number; home: HomeData; sessionCollapsed?: boolean; unlocked: boolean; faviconRevision: Record<number, number>; onRefreshMetadata: (link: Link) => void; onDelete: (link: Link) => Promise<void>; onUnlock: () => void; onCollapse: (value: boolean) => void; onMoveGroup: (delta: number) => void; onEdit: (link: Link) => void; onLayout: (id: number, change: Partial<LinkLayout>) => Promise<void> | void }) {
  const preference = home.preferences.collections[String(collection.id)] || {}
  const collapsed = sessionCollapsed ?? preference.collapsed ?? false
  const displayMode = preference.displayMode === 'list' ? 'list' : 'grid'
  const locked = Boolean(preference.locked) && !unlocked
  const { setNodeRef, isOver } = useDroppable({ id: `collection-${collection.id}` })
  return <section className={`collection ${isOver ? 'collection-drop' : ''}`} ref={setNodeRef} aria-label={`${collection.name} 分组`}>
    <div className="collection-heading"><span className="section-kicker">{String(index + 1).padStart(2, '0')}</span><button className="collection-title" aria-expanded={!collapsed} aria-label={`${collapsed ? '展开' : '收起'} ${collection.name}`} onClick={() => onCollapse(!collapsed)}>{collapsed ? <ChevronRight size={17}/> : <ChevronDown size={17}/>}<h2>{collection.name}</h2><span className="count-pill">{locked ? (collection.linkCount ?? links.length) : links.length}</span></button><div className="collection-tools" aria-label={`调整分组 ${collection.name} 顺序`}><button className="tiny-button" aria-label={`上移分组 ${collection.name}`} onClick={() => onMoveGroup(-1)}><ArrowUp size={14}/></button><button className="tiny-button" aria-label={`下移分组 ${collection.name}`} onClick={() => onMoveGroup(1)}><ArrowDown size={14}/></button></div><span className="heading-rule"/></div>
    {!collapsed && locked && <div className="locked-collection"><span className="locked-collection-icon"><LockKeyhole size={18}/></span><div><b>此分类已加密</b><small>输入密码后查看 {collection.linkCount ?? links.length} 个网址</small></div><button className="secondary-button" onClick={onUnlock}>解锁</button></div>}
    {!collapsed && !locked && <SortableContext items={links.map(link => `link-${link.id}`)} strategy={displayMode === 'list' ? verticalListSortingStrategy : rectSortingStrategy}><div className={`bookmark-grid bookmark-${displayMode}`} role="list">{links.map(link => <SortableBookmark key={link.id} link={link} mode={displayMode} faviconRevision={faviconRevision[link.id] || 0} layout={layoutFor(home, link.id)} onRefreshMetadata={onRefreshMetadata} onDelete={onDelete} onEdit={onEdit} onLayout={onLayout}/>)}</div></SortableContext>}
    {!collapsed && !locked && links.length === 0 && <p className="empty-collection">拖动网址到这里，或使用浏览器扩展一键收藏。</p>}
  </section>
}

function SortableBookmark({ link, mode, layout, faviconRevision, onRefreshMetadata, onDelete, onEdit, onLayout }: { link: Link; mode: 'grid' | 'list'; layout: LinkLayout; faviconRevision: number; onRefreshMetadata: (link: Link) => void; onDelete: (link: Link) => Promise<void>; onEdit: (link: Link) => void; onLayout: (id: number, change: Partial<LinkLayout>) => Promise<void> | void }) {
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
    const menuWidth = 232
    const menuHeight = 300
    const offset = 6
    const left = Math.min(Math.max(8, x + offset), Math.max(8, window.innerWidth - menuWidth - 8))
    const top = Math.min(Math.max(8, y + offset), Math.max(8, window.innerHeight - menuHeight - 8))
    setMenu({ left, top })
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
  return <article ref={sortable.setNodeRef} style={style} className={`bookmark bookmark-${mode} ${sortable.isDragging ? 'bookmark-dragging' : ''}`} role="listitem">
    <a className="bookmark-link" href={safe ? link.url : '#'} target={safe ? '_blank' : undefined} rel={safe ? 'noreferrer' : undefined} aria-label={`${link.name}，打开网址`} {...sortable.listeners} onClick={event => { if (!safe) event.preventDefault() }} onContextMenu={handleContextMenu} onKeyDown={handleKeyDown}>
      {layout.showIcon && <BookmarkSiteIcon link={link} revision={faviconRevision} sizeOverride={mode === 'list' ? 28 : undefined}/>}
      {layout.showName && <span className="bookmark-name">{link.name}</span>}
      {(mode === 'list' || layout.showDescription) && link.description.trim() && <span className="bookmark-description" role={mode === 'list' ? undefined : 'tooltip'}>{link.description}</span>}
      {mode === 'list' && <time className="bookmark-time" dateTime={link.createdAt || undefined}>{favoriteTime(link.createdAt)}</time>}
    </a>
    {menu && createPortal(<div ref={menuRef} className="bookmark-context-menu" role="menu" aria-label={`${link.name} 设置`} style={menu} onPointerDown={event => event.stopPropagation()}>
      <button role="menuitemcheckbox" aria-checked={layout.showName} onClick={() => { void onLayout(link.id, { showName: !layout.showName }); setMenu(null) }}>显示网站名</button>
      <button role="menuitemcheckbox" aria-checked={layout.showIcon} onClick={() => { void onLayout(link.id, { showIcon: !layout.showIcon }); setMenu(null) }}>显示网站图标</button>
      <button role="menuitemcheckbox" aria-checked={layout.showDescription} onClick={() => { void onLayout(link.id, { showDescription: !layout.showDescription }); setMenu(null) }}>悬停显示简介</button>
      <div className="bookmark-menu-divider"/>
      <button role="menuitem" onClick={() => { onRefreshMetadata(link); setMenu(null) }}><RefreshCw size={14}/>刷新网站信息</button>
      <button role="menuitem" onClick={() => { onEdit(link); setMenu(null) }}><Pencil size={14}/>编辑网址</button>
      <button role="menuitem" className="danger-menu-item" onClick={() => { setMenu(null); void onDelete(link) }}><Trash2 size={14}/>删除网址</button>
    </div>, document.body)}
  </article>
}

function BookmarkDragPreview({ link, layout, faviconRevision }: { link: Link; layout: LinkLayout; faviconRevision: number }) {
  return <div className="bookmark-drag-preview" aria-hidden="true">
    {layout.showIcon && <BookmarkSiteIcon link={link} revision={faviconRevision}/>}
    {layout.showName && <span className="bookmark-name">{link.name}</span>}
  </div>
}

function BookmarkSiteIcon({ link, revision, sizeOverride }: { link: Link; revision: number; sizeOverride?: number }) {
  const [failed, setFailed] = useState(false)
  useEffect(() => { setFailed(false) }, [link.url, link.icon, link.iconWeight, revision])
  const favicon = siteIconUrl(link.id, revision)
  const size = sizeOverride || link.iconSize || 52
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

function SettingsDialog({ search, setSearch, collections, collectionPreferences, dark, onDark, onClose, onSaved, onCreateCollection, onUpdateCollection, onDeleteCollection, onCollectionLayout, onSetCollectionPassword, onClearCollectionPassword }: { search: SearchData; setSearch: (value: SearchData) => void; collections: Collection[]; collectionPreferences: Record<string, CollectionPreference>; dark: boolean; onDark: (value: boolean) => void; onClose: () => void; onSaved: () => void; onCreateCollection: (name: string) => Promise<void>; onUpdateCollection: (id: number, name: string) => Promise<void>; onDeleteCollection: (id: number) => Promise<void>; onCollectionLayout: (id: number, change: { collapsed?: boolean; order?: number; displayMode?: 'grid' | 'list' }) => Promise<void>; onSetCollectionPassword: (id: number, password: string) => Promise<void>; onClearCollectionPassword: (id: number) => Promise<void> }) {
  const [providers, setProviders] = useState(search.providers)
  const [active, setActive] = useState(search.activeProviderId)
  const [error, setError] = useState('')
  const [collectionNames, setCollectionNames] = useState<Record<number, string>>(() => Object.fromEntries(collections.map(collection => [collection.id, collection.name])))
  const [newCollection, setNewCollection] = useState('')
  const [collectionError, setCollectionError] = useState('')
  const [passwordDrafts, setPasswordDrafts] = useState<Record<number, string>>({})
  const [passwordEnabled, setPasswordEnabled] = useState<Record<number, boolean>>(() => Object.fromEntries(collections.map(collection => [collection.id, Boolean(collectionPreferences[String(collection.id)]?.locked)])))
  const [passwordBusy, setPasswordBusy] = useState<number | null>(null)
  const [collectionOrder, setCollectionOrder] = useState(collections)
  const settingsSensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }))

  useEffect(() => {
    setCollectionOrder(current => {
      const incoming = new Map(collections.map(collection => [collection.id, collection]))
      const currentIds = current.map(collection => collection.id)
      const incomingIds = collections.map(collection => collection.id)
      if (currentIds.length === incomingIds.length && currentIds.every(id => incoming.has(id))) {
        return current.map(collection => incoming.get(collection.id) || collection)
      }
      return collections
    })
  }, [collections])

  useEffect(() => {
    setCollectionNames(current => {
      const next = { ...current }
      const ids = new Set(collections.map(collection => collection.id))
      for (const collection of collections) {
        if (!(collection.id in next)) next[collection.id] = collection.name
      }
      for (const key of Object.keys(next)) {
        if (!ids.has(Number(key))) delete next[Number(key)]
      }
      return next
    })
  }, [collections])

  useEffect(() => {
    setPasswordEnabled(current => {
      const next = { ...current }
      for (const collection of collections) {
        const id = collection.id
        if (next[id] === undefined) next[id] = Boolean(collectionPreferences[String(id)]?.locked)
      }
      return next
    })
  }, [collections, collectionPreferences])

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
    const linkCount = collection.linkCount ?? 0
    if (linkCount > 0) {
      setCollectionError('分组“' + collection.name + '”还有 ' + linkCount + ' 个网址，请先移动网址后再删除。')
      return
    }
    if (!window.confirm('确定删除分组“' + collection.name + '”吗？')) return
    try { await onDeleteCollection(collection.id); setCollectionError('') }
    catch (err) { setCollectionError(err instanceof Error ? err.message : '删除分组失败') }
  }
  async function saveCollectionPassword(id: number) {
    const password = (passwordDrafts[id] || '').trim()
    if (!password) { setCollectionError('请输入分类密码'); return }
    setPasswordBusy(id)
    try { await onSetCollectionPassword(id, password); setPasswordDrafts(current => ({ ...current, [id]: '' })); setPasswordEnabled(current => ({ ...current, [id]: true })); setCollectionError('') }
    catch (err) { setCollectionError(err instanceof Error ? err.message : '设置分类密码失败') }
    finally { setPasswordBusy(null) }
  }
  async function removeCollectionPassword(id: number) {
    setPasswordBusy(id)
    try { await onClearCollectionPassword(id); setPasswordDrafts(current => { const next = { ...current }; delete next[id]; return next }); setPasswordEnabled(current => ({ ...current, [id]: false })); setCollectionError('') }
    catch (err) { setCollectionError(err instanceof Error ? err.message : '移除分类密码失败'); throw err }
    finally { setPasswordBusy(null) }
  }
  async function toggleCollectionPassword(id: number, enabled: boolean, wasLocked: boolean) {
    setPasswordEnabled(current => ({ ...current, [id]: enabled }))
    if (enabled) {
      setPasswordDrafts(current => ({ ...current, [id]: current[id] || '' }))
      return
    }
    setPasswordDrafts(current => { const next = { ...current }; delete next[id]; return next })
    if (!wasLocked) return
    try { await removeCollectionPassword(id) }
    catch { setPasswordEnabled(current => ({ ...current, [id]: true })) }
  }
  async function persistCollectionOrder(next: Collection[], previous: Collection[]) {
    setCollectionOrder(next)
    try {
      for (const [index, collection] of next.entries()) await onCollectionLayout(collection.id, { order: index })
      setCollectionError('')
    } catch (err) {
      setCollectionOrder(previous)
      setCollectionError(err instanceof Error ? err.message : '分组排序保存失败')
    }
  }
  function moveCollection(id: number, delta: number) {
    const index = collectionOrder.findIndex(collection => collection.id === id)
    const target = index + delta
    if (index < 0 || target < 0 || target >= collectionOrder.length) return
    void persistCollectionOrder(arrayMove(collectionOrder, index, target), collectionOrder)
  }
  function handleCollectionDragEnd(event: DragEndEvent) {
    if (!event.over || event.active.id === event.over.id) return
    const activeId = Number(String(event.active.id).replace('collection-setting-', ''))
    const overId = Number(String(event.over.id).replace('collection-setting-', ''))
    const oldIndex = collectionOrder.findIndex(collection => collection.id === activeId)
    const newIndex = collectionOrder.findIndex(collection => collection.id === overId)
    if (oldIndex < 0 || newIndex < 0) return
    void persistCollectionOrder(arrayMove(collectionOrder, oldIndex, newIndex), collectionOrder)
  }
  return <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}><div className="dialog settings-dialog" role="dialog" aria-modal="true" aria-label="首页设置"><div className="dialog-heading"><div><span className="dialog-kicker">PERSONAL PREFERENCES</span><h2>首页设置</h2></div><button className="icon-button" aria-label="关闭" onClick={onClose}><X size={18}/></button></div><label className="switch-row"><span><b>深色外观</b><small>适合夜间浏览</small></span><input type="checkbox" checked={dark} onChange={e => onDark(e.target.checked)}/></label><div className="settings-section-title"><div><b>分组管理</b><small>拖动或使用箭头调整顺序</small></div></div><div className="collection-create-row"><input aria-label="新分组名称" placeholder="新分组名称" value={newCollection} onChange={e => setNewCollection(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); void addCollection() } }}/><button className="add-provider" onClick={() => void addCollection()}><Plus size={14}/>新增</button></div><div className="collection-manage-list" role="list"><DndContext sensors={settingsSensors} collisionDetection={closestCenter} onDragEnd={handleCollectionDragEnd}><SortableContext items={collectionOrder.map(collection => 'collection-setting-' + collection.id)} strategy={verticalListSortingStrategy}>{collectionOrder.map((collection, index) => <CollectionSettingsRow key={collection.id} collection={collection} index={index} total={collectionOrder.length} preference={collectionPreferences[String(collection.id)] || {}} name={collectionNames[collection.id] || ''} passwordEnabled={Boolean(passwordEnabled[collection.id])} passwordDraft={passwordDrafts[collection.id] || ''} passwordBusy={passwordBusy === collection.id} onNameChange={value => setCollectionNames(current => ({ ...current, [collection.id]: value }))} onRename={() => void renameCollection(collection.id)} onDelete={() => void removeCollection(collection)} onLayout={change => void onCollectionLayout(collection.id, change)} onMove={delta => moveCollection(collection.id, delta)} onPasswordToggle={enabled => void toggleCollectionPassword(collection.id, enabled, Boolean(collectionPreferences[String(collection.id)]?.locked))} onPasswordDraftChange={value => setPasswordDrafts(current => ({ ...current, [collection.id]: value }))} onSavePassword={() => void saveCollectionPassword(collection.id)} />)}</SortableContext></DndContext></div>{collectionError && <p className="field-error">{collectionError}</p>}<div className="settings-section-title"><div><b>搜索引擎</b><small>使用 {'{query}'} 作为搜索词</small></div><button className="add-provider" onClick={() => setProviders([...providers, { id: 'engine-' + Date.now(), name: '新搜索引擎', url: 'https://example.com/search?q={query}' }])}><Plus size={14}/>添加</button></div>{providers.map((p, index) => <div className="provider-row" key={p.id}><input aria-label={'引擎名称 ' + (index + 1)} value={p.name} onChange={e => setProviders(providers.map(item => item.id === p.id ? { ...item, name: e.target.value } : item))}/><input aria-label={'搜索地址 ' + (index + 1)} value={p.url} onChange={e => setProviders(providers.map(item => item.id === p.id ? { ...item, url: e.target.value } : item))}/><label className="radio-label"><input type="radio" name="active-search" checked={active === p.id} onChange={() => setActive(p.id)}/> 默认</label><button className="remove-provider" aria-label={'删除 ' + p.name} disabled={providers.length < 2} onClick={() => { const next = providers.filter(item => item.id !== p.id); setProviders(next); if (active === p.id) setActive(next[0]?.id || '') }}><X size={14}/></button></div>)}{error && <p className="field-error">{error}</p>}<div className="dialog-actions"><button className="secondary-button" onClick={onClose}>关闭</button><button className="primary-button" onClick={() => void save()}><Check size={15}/> 保存</button></div></div></div>
}

function CollectionSettingsRow({ collection, index, total, preference, name, passwordEnabled, passwordDraft, passwordBusy, onNameChange, onRename, onDelete, onLayout, onMove, onPasswordToggle, onPasswordDraftChange, onSavePassword }: { collection: Collection; index: number; total: number; preference: CollectionPreference; name: string; passwordEnabled: boolean; passwordDraft: string; passwordBusy: boolean; onNameChange: (value: string) => void; onRename: () => void; onDelete: () => void; onLayout: (change: { collapsed?: boolean; displayMode?: 'grid' | 'list' }) => void; onMove: (delta: number) => void; onPasswordToggle: (enabled: boolean) => void; onPasswordDraftChange: (value: string) => void; onSavePassword: () => void }) {
  const sortable = useSortable({ id: 'collection-setting-' + collection.id })
  const rowStyle = { transform: CSS.Transform.toString(sortable.transform), transition: sortable.transition }
  return <div ref={sortable.setNodeRef} style={rowStyle} className={'collection-manage-row collection-settings-row ' + (sortable.isDragging ? 'collection-settings-row-dragging' : '')} role="listitem"><button type="button" ref={sortable.setActivatorNodeRef} className="collection-drag-handle" aria-label={'拖动分组 ' + collection.name} {...sortable.attributes} {...sortable.listeners}><GripVertical size={17}/></button><div className="collection-name-cell"><input className="collection-name-input" aria-label={'分组名称 ' + collection.name} value={name} onChange={event => onNameChange(event.target.value)}/><span className="collection-link-count" title="网址数量">{collection.linkCount ?? 0}</span><button type="button" className="tiny-button" aria-label={'保存分组 ' + collection.name} onClick={onRename}><Check size={15}/></button></div><label className="collection-display-mode"><select aria-label={'显示方式 ' + collection.name} value={preference.displayMode || 'grid'} onChange={event => onLayout({ displayMode: event.target.value as 'grid' | 'list' })}><option value="grid">App</option><option value="list">列表</option></select></label><div className="collection-sort-actions" aria-label={'排序 ' + collection.name}><button type="button" className="tiny-button" aria-label={'上移 ' + collection.name} disabled={index === 0} onClick={() => onMove(-1)}><ArrowUp size={14}/></button><button type="button" className="tiny-button" aria-label={'下移 ' + collection.name} disabled={index === total - 1} onClick={() => onMove(1)}><ArrowDown size={14}/></button></div><label className="collection-toggle"><input type="checkbox" checked={Boolean(preference.collapsed)} onChange={event => onLayout({ collapsed: event.target.checked })}/>折叠</label><div className="collection-password-cell"><label className="collection-toggle"><input type="checkbox" checked={passwordEnabled} onChange={event => onPasswordToggle(event.target.checked)}/>密码</label>{passwordEnabled && <div className="collection-password"><input type="password" name={'collection-password-' + collection.id} autoComplete="new-password" data-1p-ignore="true" data-lpignore="true" aria-label={'分类密码 ' + collection.name} placeholder={preference.locked ? '输入新密码' : '设置密码'} value={passwordDraft} onChange={event => onPasswordDraftChange(event.target.value)}/><button type="button" className="secondary-button collection-password-button" disabled={passwordBusy} onClick={onSavePassword}>{preference.locked ? '更新密码' : '设置密码'}</button></div>}</div><button type="button" className="tiny-button danger-button collection-delete-action" aria-label={'删除分组 ' + collection.name} onClick={onDelete}><Trash2 size={15}/></button></div>
}
function CollectionUnlockDialog({ collection, onClose, onUnlock }: { collection: Collection; onClose: () => void; onUnlock: (password: string) => Promise<void> }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit() {
    if (!password) { setError('请输入密码'); return }
    setBusy(true)
    try { await onUnlock(password); onClose() }
    catch (err) { setError(err instanceof Error ? err.message : '解锁失败') }
    finally { setBusy(false) }
  }
  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Enter') { event.preventDefault(); void submit() }
  }
  return <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget && !busy) onClose() }}><div className="dialog unlock-dialog" role="dialog" aria-modal="true" aria-label={'解锁' + collection.name} data-autocomplete="off" onKeyDown={handleKeyDown}><div className="dialog-heading"><div><span className="dialog-kicker">PROTECTED COLLECTION</span><h2>解锁{collection.name}</h2></div><button type="button" className="icon-button" aria-label="关闭" disabled={busy} onClick={onClose}><X size={18}/></button></div><p className="unlock-message">输入分类密码后，当前页面会显示其中的网址。刷新页面后需要重新解锁。</p><label>分类密码<input autoFocus type="password" name="collection-unlock-password" autoComplete="new-password" data-1p-ignore="true" data-lpignore="true" value={password} onChange={event => setPassword(event.target.value)} /></label>{error && <p className="field-error">{error}</p>}<div className="dialog-actions"><button type="button" className="secondary-button" disabled={busy} onClick={onClose}>取消</button><button type="button" className="primary-button" disabled={busy} onClick={() => void submit()}>{busy ? '验证中…' : '解锁分类'}</button></div></div></div>
}
