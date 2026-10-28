import { useCallback, useEffect, useMemo, useState } from 'react'
import { DndContext, KeyboardSensor, PointerSensor, closestCenter, useDroppable, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core'
import { SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { ArrowDown, ArrowUp, BookOpen, CalendarDays, Check, ChevronDown, ChevronRight, Cloud, Code, Command, ExternalLink, FileText, Folder, Github, Globe, GripVertical, House, Image, LayoutGrid, LoaderCircle, Mail, MessageCircle, Music, NotebookPen, Pencil, Plus, RefreshCw, Search, Settings2, Sparkles, Video, X, type LucideIcon } from 'lucide-react'
import { buildSearchUrl, reorderLinkIds, safeHttpUrl, siteFaviconUrl, type LinkLayout } from './domain'
import './styles.css'

type Collection = { id: number; name: string; ownerId: number }
type Link = { id: number; name: string; url: string; description: string; icon: string | null; iconWeight: string | null; color: string | null; collectionId: number }
type HomeData = { collections: Collection[]; links: Link[]; preferences: { links: Record<string, Partial<LinkLayout>>; collections: Record<string, { collapsed?: boolean; order?: number }> }; sync: { status: 'ok' | 'error'; message?: string } }
type Provider = { id: string; name: string; url: string }
type SearchData = { providers: Provider[]; activeProviderId: string }
const defaults: SearchData = { providers: [{ id: 'google', name: 'Google', url: 'https://www.google.com/search?q={query}' }, { id: 'bing', name: 'Bing', url: 'https://www.bing.com/search?q={query}' }], activeProviderId: 'google' }
const emptyHome: HomeData = { collections: [], links: [], preferences: { links: {}, collections: {} }, sync: { status: 'ok' } }

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw Object.assign(new Error(typeof body.detail === 'string' ? body.detail : '请求失败，请稍后重试'), { status: response.status })
  return body as T
}
function layoutFor(home: HomeData, linkId: number): LinkLayout {
  return { showName: true, showIcon: true, showDescription: false, order: 0, ...home.preferences.links[String(linkId)] }
}
function iconFor(glyph: string | null): LucideIcon {
  const name = (glyph || '').split(/[-_]/).filter(Boolean).map(part => part[0].toUpperCase() + part.slice(1)).join('')
  const available: Record<string, LucideIcon> = { BookOpen, CalendarDays, Cloud, Code, FileText, Folder, Github, Globe, House, Image, Mail, MessageCircle, Music, NotebookPen, Search, Video }
  return available[name] || BookOpen
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

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true)
    setError('')
    try {
      const [nextHome, nextSearch] = await Promise.all([api<HomeData>('/api/home'), api<SearchData>('/api/search-engines')])
      setSearch(nextSearch)
      setOffline(nextHome.sync.status === 'error')
      if (nextHome.sync.status === 'error') setError(nextHome.sync.message || 'Linkwarden 暂时不可用')
      else setHome(nextHome)
    } catch (err) {
      setOffline(true)
      setError(err instanceof Error ? err.message : 'Linkwarden 暂时不可用')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    const poll = window.setInterval(() => { if (!document.hidden) void refresh(true) }, 15000)
    const focus = () => { void refresh(true) }
    window.addEventListener('focus', focus)
    return () => { window.clearInterval(poll); window.removeEventListener('focus', focus) }
  }, [refresh])

  const collections = useMemo(() => [...home.collections].sort((a, b) => {
    const aa = home.preferences.collections[String(a.id)]?.order ?? a.id
    const bb = home.preferences.collections[String(b.id)]?.order ?? b.id
    return aa - bb
  }), [home.collections, home.preferences.collections])

  async function patchLinkLayout(id: number, change: Partial<LinkLayout>) {
    const before = home
    setHome(current => ({ ...current, preferences: { ...current.preferences, links: { ...current.preferences.links, [id]: { ...layoutFor(current, id), ...change } } } }))
    try {
      const saved = await api<LinkLayout>(`/api/links/${id}/layout`, { method: 'PATCH', body: JSON.stringify(change) })
      setHome(current => ({ ...current, preferences: { ...current.preferences, links: { ...current.preferences.links, [id]: saved } } }))
    } catch (err) { setHome(before); setError(err instanceof Error ? err.message : '布局保存失败') }
  }
  async function patchCollectionLayout(id: number, change: { collapsed?: boolean; order?: number }) {
    const before = home
    setHome(current => ({ ...current, preferences: { ...current.preferences, collections: { ...current.preferences.collections, [id]: { ...current.preferences.collections[String(id)], ...change } } } }))
    try {
      const saved = await api<{ collapsed: boolean; order: number }>(`/api/collections/${id}/layout`, { method: 'PATCH', body: JSON.stringify(change) })
      setHome(current => ({ ...current, preferences: { ...current.preferences, collections: { ...current.preferences.collections, [id]: saved } } }))
    } catch (err) { setHome(before); setError(err instanceof Error ? err.message : '分组设置保存失败') }
  }
  async function saveCanonical(link: Link, values: Partial<Link>) {
    const changed = Object.fromEntries(Object.entries(values).filter(([key, value]) => link[key as keyof Link] !== value)) as Partial<Link>
    if (!Object.keys(changed).length) { setEditing(null); return }
    const expected = Object.fromEntries(Object.keys(changed).map(key => [key === 'collectionId' ? key : key, link[key as keyof Link]]))
    const payload = { ...changed, expected }
    try {
      const saved = await api<Link>(`/api/links/${link.id}`, { method: 'PATCH', body: JSON.stringify(payload) })
      setHome(current => ({ ...current, links: current.links.map(item => item.id === link.id ? saved : item) }))
      setEditing(null)
    } catch (err) {
      if ((err as { status?: number }).status === 409) {
        setError('这个网址在 Linkwarden 中也有更新。已刷新当前数据，请重新打开编辑并确认。')
        await refresh(true)
        setEditing(null)
      } else setError(err instanceof Error ? err.message : '网址更新失败')
    }
  }

  function handleDragEnd(event: DragEndEvent) {
    const activeId = Number(String(event.active.id).replace('link-', ''))
    if (!event.over) return
    const overString = String(event.over.id)
    const item = home.links.find(link => link.id === activeId)
    if (!item) return
    if (overString.startsWith('collection-')) {
      const target = Number(overString.slice('collection-'.length))
      if (target !== item.collectionId) void saveCanonical(item, { collectionId: target })
      return
    }
    if (overString.startsWith('link-')) {
      const overId = Number(overString.slice('link-'.length))
      const over = home.links.find(link => link.id === overId)
      if (over && over.collectionId !== item.collectionId) { void saveCanonical(item, { collectionId: over.collectionId }); return }
      reorderLinkIds(home.links, home.preferences.links, item.collectionId, activeId, overId)
        .forEach(({ id, order }) => { void patchLinkLayout(id, { order }) })
    }
  }
  const activeSearch = search.providers.find(provider => provider.id === search.activeProviderId) || search.providers[0]
  function submitSearch(event: React.FormEvent) {
    event.preventDefault()
    if (!activeSearch || !query.trim()) return
    try { window.location.assign(buildSearchUrl(activeSearch.url, query.trim())) }
    catch (err) { setError(err instanceof Error ? err.message : '搜索配置无效') }
  }

  return <main className={dark ? 'app app-dark' : 'app'}>
    <header className="topbar"><a className="brand" href="/" aria-label="我的起始页"><span className="brand-mark"><LayoutGrid size={19}/></span><span>我的空间</span></a><div className="top-actions"><span className="sync-indicator"><i className={offline ? 'dot dot-error' : 'dot'}/>{offline ? '连接异常' : 'Linkwarden 已同步'}</span><button className="icon-button" title="切换深色主题" aria-label="切换深色主题" onClick={() => setDark(v => !v)}><Sparkles size={17}/></button><button className="icon-button" title="设置" aria-label="设置" onClick={() => setSettingsOpen(true)}><Settings2 size={18}/></button></div></header>
    <section className="welcome"><div className="eyebrow"><span className="eyebrow-line"/>PERSONAL START PAGE</div><h1>把常去的地方，<em>放在一起。</em></h1><p className="welcome-sub">整齐一点，轻松开始。</p>
      <form className="searchbar" onSubmit={submitSearch}><Search size={21} className="search-icon"/><input aria-label="搜索网页" placeholder="搜索任何内容…" value={query} onChange={event => setQuery(event.target.value)}/>{search.providers.length > 1 && <select aria-label="搜索引擎" value={activeSearch?.id || ''} onChange={event => { const next = { ...search, activeProviderId: event.target.value }; setSearch(next); void api<SearchData>('/api/search-engines', { method: 'PUT', body: JSON.stringify(next) }).then(setSearch).catch(err => setError(err instanceof Error ? err.message : '搜索配置保存失败')) }}>{search.providers.map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</select>}<button type="submit" aria-label="开始搜索" className="search-submit"><Command size={14}/> Enter</button></form>
      <div className="quick-meta"><span><span className="meta-number">{home.links.length}</span> 个网址</span><span className="meta-divider">/</span><span><span className="meta-number">{home.collections.length}</span> 个分组</span><button className="refresh-button" onClick={() => void refresh()} aria-label="刷新"><RefreshCw size={13}/> 刷新</button></div>
    </section>
    {error && <div className={offline ? 'notice notice-offline' : 'notice'} role="status"><span>{offline ? '网络稍作休息' : '提示'}</span><div>{offline && home.links.length ? '暂时无法连接 Linkwarden，正在显示上次加载的网址。' : error}</div><button aria-label="关闭提示" onClick={() => setError('')}><X size={16}/></button></div>}
    {loading && !home.collections.length && <div className="loading-state"><LoaderCircle className="spin" size={22}/><span>正在整理你的空间…</span></div>}
    {!loading && !home.collections.length && !offline && <div className="empty-state"><div className="empty-icon"><BookOpen size={25}/></div><h2>你的空间还空着</h2><p>先在 Linkwarden 创建分组并收藏几个网址，它们就会出现在这里。</p><a href="http://localhost:3010" target="_blank" rel="noreferrer">打开 Linkwarden <ExternalLink size={14}/></a></div>}
    <DndContext sensors={useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }))} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <div className="collections">{collections.map((collection, index) => <CollectionPanel key={collection.id} collection={collection} index={index} home={home} onCollapse={collapsed => void patchCollectionLayout(collection.id, { collapsed })} onMoveGroup={delta => {
        const current = collections[index], target = collections[index + delta]
        if (!target) return
        void patchCollectionLayout(current.id, { order: index + delta })
        void patchCollectionLayout(target.id, { order: index })
      }} onEdit={setEditing} onLayout={patchLinkLayout} />)}</div>
    </DndContext>
    <footer className="footer"><span>MADE FOR YOUR EVERYDAY</span><span>书签由 Linkwarden 管理 <span className="footer-dot">·</span> 轻松开始</span></footer>
    {editing && <LinkEditor link={editing} collections={home.collections} onClose={() => setEditing(null)} onSave={values => void saveCanonical(editing, values)} />}
    {settingsOpen && <SettingsDialog search={search} setSearch={setSearch} dark={dark} onDark={setDark} onClose={() => setSettingsOpen(false)} onSaved={() => void refresh(true)} />}
  </main>
}

function CollectionPanel({ collection, index, home, onCollapse, onMoveGroup, onEdit, onLayout }: { collection: Collection; index: number; home: HomeData; onCollapse: (value: boolean) => void; onMoveGroup: (delta: number) => void; onEdit: (link: Link) => void; onLayout: (id: number, change: Partial<LinkLayout>) => Promise<void> | void }) {
  const collapsed = home.preferences.collections[String(collection.id)]?.collapsed ?? false
  const links = home.links.filter(link => link.collectionId === collection.id).sort((a, b) => (home.preferences.links[String(a.id)]?.order ?? a.id) - (home.preferences.links[String(b.id)]?.order ?? b.id))
  const { setNodeRef, isOver } = useDroppable({ id: `collection-${collection.id}` })
  return <section className={`collection ${isOver ? 'collection-drop' : ''}`} ref={setNodeRef} aria-label={`${collection.name} 分组`}>
    <div className="collection-heading"><span className="section-kicker">{String(index + 1).padStart(2, '0')}</span><button className="collection-title" aria-label={`${collapsed ? '展开' : '收起'} ${collection.name}`} onClick={() => onCollapse(!collapsed)}>{collapsed ? <ChevronRight size={17}/> : <ChevronDown size={17}/>}<h2>{collection.name}</h2><span className="count-pill">{links.length}</span></button><div className="collection-tools"><button className="tiny-button" aria-label={`上移分组 ${collection.name}`} onClick={() => onMoveGroup(-1)}><ArrowUp size={14}/></button><button className="tiny-button" aria-label={`下移分组 ${collection.name}`} onClick={() => onMoveGroup(1)}><ArrowDown size={14}/></button></div><span className="heading-rule"/></div>
    {!collapsed && <SortableContext items={links.map(link => `link-${link.id}`)} strategy={verticalListSortingStrategy}><div className="bookmark-grid" role="list">{links.map(link => <SortableBookmark key={link.id} link={link} layout={layoutFor(home, link.id)} onEdit={onEdit} onLayout={onLayout}/>)}</div></SortableContext>}
    {!collapsed && links.length === 0 && <p className="empty-collection">拖动网址到这里，或在 Linkwarden 中添加网址。</p>}
  </section>
}

function SortableBookmark({ link, layout, onEdit, onLayout }: { link: Link; layout: LinkLayout; onEdit: (link: Link) => void; onLayout: (id: number, change: Partial<LinkLayout>) => Promise<void> | void }) {
  const sortable = useSortable({ id: `link-${link.id}` })
  const style = { transform: CSS.Transform.toString(sortable.transform), transition: sortable.transition }
  return <article ref={sortable.setNodeRef} style={style} className={`bookmark ${layout.showDescription ? 'bookmark-wide' : ''}`} role="listitem">
    <div className="bookmark-main">
      <span className="drag-handle" {...sortable.attributes} {...sortable.listeners} aria-label="拖动网址"><GripVertical size={15}/></span>
      {layout.showIcon && <BookmarkSiteIcon link={link}/>}
      <div className="bookmark-copy">{layout.showName && <a className="bookmark-name" href={safeHttpUrl(link.url) ? link.url : '#'} target="_blank" rel="noreferrer" aria-label={`${link.name}，打开网址`}>{link.name}<ExternalLink size={11}/></a>}{layout.showDescription && <p className="bookmark-description">{link.description || link.url}</p>}</div>
      <div className="bookmark-hover-actions"><button aria-label={`编辑 ${link.name}`} onPointerDown={event => event.stopPropagation()} onClick={() => onEdit(link)}><Pencil size={13}/></button></div>
    </div>
    <div className="bookmark-customize" aria-label={`${link.name} 显示选项`}><button className={layout.showName ? 'field-active' : ''} aria-label={`切换名称 ${link.name}`} aria-pressed={layout.showName} onClick={() => void onLayout(link.id, { showName: !layout.showName })}>N</button><button className={layout.showIcon ? 'field-active' : ''} aria-label={`切换图标 ${link.name}`} aria-pressed={layout.showIcon} onClick={() => void onLayout(link.id, { showIcon: !layout.showIcon })}>◉</button><button className={layout.showDescription ? 'field-active' : ''} aria-label={`切换简介 ${link.name}`} aria-pressed={layout.showDescription} onClick={() => void onLayout(link.id, { showDescription: !layout.showDescription })}>≡</button></div>
  </article>
}

function BookmarkSiteIcon({ link }: { link: Link }) {
  const [failed, setFailed] = useState(false)
  useEffect(() => { setFailed(false) }, [link.url])
  const favicon = siteFaviconUrl(link.url)
  const Glyph = iconFor(link.icon)
  if (failed || !favicon) return <span className="bookmark-icon" style={{ color: link.color || undefined }} aria-label={`${link.name} Linkwarden 图标`}><Glyph size={18}/></span>
  return <span className="bookmark-icon" style={{ color: link.color || undefined }}><img src={favicon} alt={`${link.name}网站图标`} onError={() => setFailed(true)}/></span>
}

function LinkEditor({ link, collections, onClose, onSave }: { link: Link; collections: Collection[]; onClose: () => void; onSave: (value: Partial<Link>) => void }) {
  const [values, setValues] = useState({ name: link.name, url: link.url, description: link.description || '', icon: link.icon || '', iconWeight: link.iconWeight || 'regular', color: link.color || '#57745f', collectionId: link.collectionId })
  return <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}><form className="dialog" aria-label={`编辑 ${link.name}`} onSubmit={event => { event.preventDefault(); if (!safeHttpUrl(values.url)) return; onSave({ ...values, icon: values.icon || null, color: values.color || null }) }}>
    <div className="dialog-heading"><div><span className="dialog-kicker">LINKWARDEN · BOOKMARK</span><h2>编辑网址</h2></div><button type="button" className="icon-button" aria-label="关闭" onClick={onClose}><X size={18}/></button></div>
    <label>名称<input autoFocus value={values.name} onChange={e => setValues({ ...values, name: e.target.value })}/></label><label>网址<input value={values.url} type="url" required onChange={e => setValues({ ...values, url: e.target.value })}/></label><label>简介<textarea rows={3} value={values.description} onChange={e => setValues({ ...values, description: e.target.value })}/></label><div className="form-row"><label>图标名称<input value={values.icon} onChange={e => setValues({ ...values, icon: e.target.value })} placeholder="book-open"/></label><label>图标字重<select aria-label="图标字重" value={values.iconWeight} onChange={e => setValues({ ...values, iconWeight: e.target.value })}>{['thin','light','regular','bold','fill','duotone'].map(weight => <option key={weight} value={weight}>{weight}</option>)}</select></label></div><div className="form-row"><label>强调色<input type="color" value={values.color} onChange={e => setValues({ ...values, color: e.target.value })}/></label><span/></div><label>分组<select aria-label="分组" value={values.collectionId} onChange={e => setValues({ ...values, collectionId: Number(e.target.value) })}>{collections.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
    {!safeHttpUrl(values.url) && <p className="field-error">请填写有效的 HTTP 或 HTTPS 网址。</p>}<div className="dialog-actions"><button type="button" className="secondary-button" onClick={onClose}>取消</button><button type="submit" className="primary-button"><Check size={15}/> 保存到 Linkwarden</button></div>
  </form></div>
}

function SettingsDialog({ search, setSearch, dark, onDark, onClose, onSaved }: { search: SearchData; setSearch: (value: SearchData) => void; dark: boolean; onDark: (value: boolean) => void; onClose: () => void; onSaved: () => void }) {
  const [providers, setProviders] = useState(search.providers)
  const [active, setActive] = useState(search.activeProviderId)
  const [error, setError] = useState('')
  async function save() {
    try {
      const result = await api<SearchData>('/api/search-engines', { method: 'PUT', body: JSON.stringify({ providers, activeProviderId: active }) })
      setSearch(result); onSaved(); onClose()
    } catch (err) { setError(err instanceof Error ? err.message : '搜索配置保存失败') }
  }
  return <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}><div className="dialog settings-dialog" role="dialog" aria-modal="true" aria-label="首页设置"><div className="dialog-heading"><div><span className="dialog-kicker">PERSONAL PREFERENCES</span><h2>首页设置</h2></div><button className="icon-button" aria-label="关闭" onClick={onClose}><X size={18}/></button></div><label className="switch-row"><span><b>深色外观</b><small>适合夜间浏览</small></span><input type="checkbox" checked={dark} onChange={e => onDark(e.target.checked)}/></label><div className="settings-section-title"><div><b>搜索引擎</b><small>使用 {`{query}`} 作为搜索词</small></div><button className="add-provider" onClick={() => setProviders([...providers, { id: `engine-${Date.now()}`, name: '新搜索引擎', url: 'https://example.com/search?q={query}' }])}><Plus size={14}/>添加</button></div>{providers.map((p, index) => <div className="provider-row" key={p.id}><input aria-label={`引擎名称 ${index + 1}`} value={p.name} onChange={e => setProviders(providers.map(item => item.id === p.id ? { ...item, name: e.target.value } : item))}/><input aria-label={`搜索地址 ${index + 1}`} value={p.url} onChange={e => setProviders(providers.map(item => item.id === p.id ? { ...item, url: e.target.value } : item))}/><label className="radio-label"><input type="radio" name="active-search" checked={active === p.id} onChange={() => setActive(p.id)}/> 默认</label><button className="remove-provider" aria-label={`删除 ${p.name}`} disabled={providers.length < 2} onClick={() => { const next = providers.filter(item => item.id !== p.id); setProviders(next); if (active === p.id) setActive(next[0]?.id || '') }}><X size={14}/></button></div>)}{error && <p className="field-error">{error}</p>}<div className="dialog-actions"><button className="secondary-button" onClick={onClose}>关闭</button><button className="primary-button" onClick={() => void save()}><Check size={15}/> 保存设置</button></div></div></div>
}
