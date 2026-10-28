import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Check, Cloud, FileText, Pencil, RefreshCw } from 'lucide-react'

type NoteData = { content: string; hash: string; status: string }
type RemoteVersion = { content: string; hash: string }
type SaveResult = NoteData

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw Object.assign(new Error(typeof body.detail === 'string' ? body.detail : '同步失败'), { status: response.status, body })
  return body as T
}

function acceptSave(result: SaveResult, submitted: string, setContent: (value: (current: string) => string) => void, setSaved: (value: string) => void, setHash: (value: string) => void) {
  // A response may arrive after the user has typed more. Advance the base version
  // but replace the visible draft only when it still matches the submitted text.
  setContent(current => current === submitted ? result.content : current)
  setSaved(result.content)
  setHash(result.hash)
}

export default function MarkdownNotes() {
  const [content, setContent] = useState('')
  const [saved, setSaved] = useState('')
  const [hash, setHash] = useState('')
  const [remote, setRemote] = useState<RemoteVersion | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [draftCaptured, setDraftCaptured] = useState(false)
  const [mode, setMode] = useState<'edit' | 'preview'>('edit')
  const [status, setStatus] = useState<'loading' | 'saving' | 'saved' | 'conflict' | 'issue' | 'unavailable'>('loading')
  const capturedConflict = useRef(false)
  const current = useRef({ content, saved, hash, remote })
  current.current = { content, saved, hash, remote }

  useEffect(() => {
    let alive = true
    void request<NoteData>('/api/notes').then(note => {
      if (!alive) return
      if (note.status !== 'ok') { setStatus('unavailable'); return }
      setContent(note.content); setSaved(note.content); setHash(note.hash); setLoaded(true); setStatus('saved')
    }).catch(() => { if (alive) setStatus('issue') })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    const poll = window.setInterval(() => {
      const state = current.current
      void request<{ changed: boolean; content?: string; hash?: string; status: string }>(`/api/notes/events?since=${encodeURIComponent(state.hash)}`).then(async event => {
        if (event.status === 'unavailable') { setStatus('unavailable'); return }
        if (!event.changed) {
          if (current.current.hash && status === 'unavailable') setStatus('saved')
          return
        }
        if (event.content === undefined || !event.hash) return
        const latest = current.current
        const version = { content: event.content, hash: event.hash }
        if (!latest.hash) {
          setContent(event.content); setSaved(event.content); setHash(event.hash); setLoaded(true); setStatus('saved')
          return
        }
        if (latest.content === latest.saved && !latest.remote) {
          setContent(event.content); setSaved(event.content); setHash(event.hash); setStatus('saved'); capturedConflict.current = false
          return
        }
        setRemote(version); setStatus('conflict')
        if (!capturedConflict.current) {
          capturedConflict.current = true
          try {
            // A stale save deliberately receives 409 and makes a durable copy of
            // the local draft while leaving the Obsidian version untouched.
            const savedVersion = await request<SaveResult>('/api/notes', { method: 'PUT', body: JSON.stringify({ content: latest.content, expectedHash: latest.hash }) })
            acceptSave(savedVersion, latest.content, setContent, setSaved, setHash); setRemote(null); setStatus('saved'); capturedConflict.current = false; setDraftCaptured(false)
          } catch (error) {
            const conflict = (error as { body?: { content?: string; hash?: string } }).body
            if (conflict?.content !== undefined && conflict.hash) { setRemote({ content: conflict.content, hash: conflict.hash }); setDraftCaptured(true) }
          }
        }
      }).catch(() => setStatus('issue'))
    }, 2500)
    return () => window.clearInterval(poll)
  }, [hash, status])

  useEffect(() => {
    if (!hash || content === saved || remote || status === 'unavailable' || status === 'issue') return
    setStatus('saving')
    const timer = window.setTimeout(() => {
      const submitted = content
      void request<SaveResult>('/api/notes', { method: 'PUT', body: JSON.stringify({ content: submitted, expectedHash: hash }) }).then(result => {
        acceptSave(result, submitted, setContent, setSaved, setHash); setStatus('saved'); capturedConflict.current = false
        setDraftCaptured(false)
      }).catch(error => {
        const conflict = (error as { status?: number; body?: { content?: string; hash?: string } })
        if (conflict.status === 409 && conflict.body?.content !== undefined && conflict.body.hash) {
          setRemote({ content: conflict.body.content, hash: conflict.body.hash }); setDraftCaptured(true); setStatus('conflict')
        } else setStatus('issue')
      })
    }, 700)
    return () => window.clearTimeout(timer)
  }, [content, hash, saved, remote, status])

  function reloadRemote() {
    if (!remote) return
    setContent(remote.content); setSaved(remote.content); setHash(remote.hash); setRemote(null); setStatus('saved'); capturedConflict.current = false; setDraftCaptured(false)
  }

  async function keepLocal() {
    if (!remote) return
    const submitted = content
    setStatus('saving')
    try {
      const result = await request<SaveResult>('/api/notes', { method: 'PUT', body: JSON.stringify({ content: submitted, expectedHash: remote.hash }) })
      acceptSave(result, submitted, setContent, setSaved, setHash); setRemote(null); setStatus('saved'); capturedConflict.current = false; setDraftCaptured(false)
    } catch (error) {
      const conflict = (error as { status?: number; body?: { content?: string; hash?: string } })
      if (conflict.status === 409 && conflict.body?.content !== undefined && conflict.body.hash) {
        setRemote({ content: conflict.body.content, hash: conflict.body.hash }); setDraftCaptured(true); setStatus('conflict')
      } else setStatus('issue')
    }
  }

  const labels = { loading: '正在读取…', saving: '正在保存', saved: '已保存到 Obsidian', conflict: '发现同步冲突', issue: '同步遇到问题', unavailable: '笔记文件暂时不可用' }
  return <section className="notes-panel" aria-label="Obsidian 笔记">
    <header className="notes-heading"><div className="notes-title"><span className="notes-icon"><FileText size={17}/></span><div><h2>随手记</h2><small>与 Obsidian 笔记实时同步</small></div></div><div className={`notes-status notes-${status}`} role="status"><i/>{labels[status]}</div></header>
    {status === 'unavailable' && <div className="notes-unavailable"><Cloud size={18}/> {loaded ? 'Obsidian 笔记暂时不可用，首页草稿仍保留在当前页面。' : '找不到已配置的 Obsidian 笔记文件。文件恢复后会自动重新连接。'}</div>}
    {status === 'issue' && content !== saved && <div className="notes-unavailable"><Cloud size={18}/> 保存失败，草稿仍保留。<button className="notes-retry" onClick={() => setStatus('saved')}>重试保存</button></div>}
    {(status !== 'unavailable' || loaded) && <>
      {remote && <div className="notes-conflict" role="alert"><div><strong>Obsidian 中的内容已经变化。</strong><span>{draftCaptured ? '首页草稿已另存为冲突副本；磁盘上的 Obsidian 版本仍保留。' : '首页草稿暂时保留在此页面；Obsidian 版本仍在磁盘上。'} 选择要继续使用的版本。</span></div><div className="notes-conflict-actions"><button onClick={reloadRemote}><RefreshCw size={14}/> 使用 Obsidian 版本</button><button onClick={() => void keepLocal()}><Pencil size={14}/> 保留首页版本</button></div></div>}
      <div className="notes-toolbar"><button className={mode === 'edit' ? 'notes-tab active' : 'notes-tab'} onClick={() => setMode('edit')}><Pencil size={13}/> 编辑</button><button className={mode === 'preview' ? 'notes-tab active' : 'notes-tab'} onClick={() => setMode('preview')}><FileText size={13}/> 预览</button><span><Check size={13}/> 自动保存</span></div>
      {mode === 'edit' ? <textarea className="notes-editor" aria-label="Markdown 笔记内容" value={content} onChange={event => setContent(event.target.value)} placeholder={'# 今天\n\n记录想法、待办和灵感…'} disabled={!loaded}/>
        : <div className="notes-preview" aria-label="Markdown 预览"><ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown></div>}
    </>}
  </section>
}
