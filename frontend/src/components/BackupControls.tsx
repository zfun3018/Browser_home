import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, Download, FileUp, LoaderCircle, X } from 'lucide-react'
import './backup.css'

type BackupStatus = { lastBackupAt: string | null }
type BackupJob = { id: string; filename: string }
type ImportJob = { id: string; manifest: { createdAt: string; applicationVersion?: string } }
type ImportStatus = { status: string; rollbackDelivered: boolean; error?: string | null }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '备份请求失败')
  return body as T
}

export function backupIsDue(lastBackupAt: string | null, now = Date.now()): boolean {
  if (!lastBackupAt) return true
  const timestamp = Date.parse(lastBackupAt)
  return !Number.isFinite(timestamp) || now - timestamp > 7 * 24 * 60 * 60 * 1000
}

export default function BackupControls({ onImported }: { onImported?: () => void }) {
  const [lastBackupAt, setLastBackupAt] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [importJob, setImportJob] = useState<ImportJob | null>(null)
  const [importStatus, setImportStatus] = useState('')
  const [confirmReplace, setConfirmReplace] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const refreshStatus = useCallback(async () => {
    try {
      const status = await request<BackupStatus>('/api/backup/status')
      setLastBackupAt(status.lastBackupAt)
      setError('')
      return status.lastBackupAt
    } catch (err) {
      setError(err instanceof Error ? err.message : '无法读取备份状态')
      return null
    }
  }, [])

  useEffect(() => {
    void refreshStatus()
    const focus = () => { void refreshStatus() }
    window.addEventListener('focus', focus)
    return () => window.removeEventListener('focus', focus)
  }, [refreshStatus])

  async function backup() {
    setBusy(true)
    setError('')
    try {
      const previous = lastBackupAt
      const job = await request<BackupJob>('/api/backup', { method: 'POST', body: '{}' })
      const anchor = document.createElement('a')
      anchor.href = `/api/backup/${encodeURIComponent(job.id)}/download`
      anchor.download = job.filename
      anchor.hidden = true
      document.body.append(anchor)
      anchor.click()
      anchor.remove()

      for (let attempt = 0; attempt < 360; attempt += 1) {
        await new Promise(resolve => window.setTimeout(resolve, 1000))
        const next = await refreshStatus()
        if (next && next !== previous) return
      }
      throw new Error('备份尚未完成，请检查浏览器下载状态后重试')
    } catch (err) {
      setError(err instanceof Error ? err.message : '备份失败，请稍后重试')
    } finally {
      setBusy(false)
    }
  }

  async function chooseImport(file?: File) {
    if (!file) return
    setBusy(true)
    setError('')
    setImportJob(null)
    setImportStatus('正在验证备份文件…')
    try {
      const response = await fetch('/api/import/prepare', {
        method: 'POST',
        headers: { 'x-backup-filename': file.name, 'Content-Type': 'application/zip' },
        body: file,
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '备份校验失败')
      const job = body as ImportJob
      setImportJob(job)
      setImportStatus('正在下载当前数据的回滚备份…')
      const anchor = document.createElement('a')
      anchor.href = `/api/import/${encodeURIComponent(job.id)}/rollback`
      anchor.download = 'browser-homepage-rollback.zip'
      anchor.hidden = true
      document.body.append(anchor)
      anchor.click()
      anchor.remove()

      for (let attempt = 0; attempt < 360; attempt += 1) {
        await new Promise(resolve => window.setTimeout(resolve, 1000))
        const status = await request<ImportStatus>(`/api/import/${encodeURIComponent(job.id)}/status`)
        if (status.status === 'ready_to_commit' && status.rollbackDelivered) {
          setImportStatus('当前数据的回滚备份已下载')
          setConfirmReplace(true)
          setBusy(false)
          return
        }
        if (status.status === 'failed') throw new Error(status.error || '导入准备失败')
      }
      throw new Error('回滚备份下载未完成，请检查浏览器下载状态')
    } catch (err) {
      setError(err instanceof Error ? err.message : '导入准备失败')
      setImportStatus('')
      setImportJob(null)
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function commitImport() {
    if (!importJob || !confirmed) return
    setBusy(true)
    setError('')
    setImportStatus('正在替换数据，请勿关闭此页面…')
    try {
      await request(`/api/import/${encodeURIComponent(importJob.id)}/commit`, { method: 'POST', body: '{}' })
      await refreshStatus()
      setConfirmReplace(false)
      setImportStatus('导入完成，首页正在刷新')
      onImported?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : '导入失败，原数据已恢复')
      setImportStatus('导入未完成')
    } finally {
      setBusy(false)
    }
  }

  function cancelImport() {
    if (importJob) void request(`/api/import/${encodeURIComponent(importJob.id)}/cancel`, { method: 'POST', body: '{}' }).catch(() => undefined)
    setConfirmReplace(false)
    setConfirmed(false)
    setImportJob(null)
    setImportStatus('导入已取消；服务端暂存文件会自动清理')
  }

  const due = backupIsDue(lastBackupAt)
  return <section className={`backup-banner${due ? ' backup-banner-due' : ' backup-banner-current'}`} aria-label="首页备份">
    <div className="backup-copy" role="status" aria-live="polite">
      <strong>{due ? (lastBackupAt ? '首页备份已超过 7 天' : '还没有首页备份') : `最近备份：${new Date(lastBackupAt!).toLocaleString()}`}</strong>
      <span>{error || importStatus || '备份包括 Linkwarden 数据和明文凭据，请像保管密码文件一样保护下载文件。'}</span>
    </div>
    <div className="backup-actions">
      <button className="backup-button" onClick={() => void backup()} disabled={busy}>
        {busy ? <LoaderCircle className="spin" size={15}/> : <Download size={15}/>}
        {busy ? '处理中…' : '立即备份'}
      </button>
      <button className="backup-button" onClick={() => fileRef.current?.click()} disabled={busy}>
        {busy ? <LoaderCircle className="spin" size={15}/> : <FileUp size={15}/>}
        导入备份
      </button>
      <input ref={fileRef} className="backup-file-input" type="file" accept=".zip,application/zip" aria-label="选择备份 ZIP 文件" onChange={event => void chooseImport(event.target.files?.[0])}/>
    </div>
    {confirmReplace && importJob && <div className="modal-backdrop" role="presentation"><section className="dialog import-dialog" role="dialog" aria-modal="true" aria-labelledby="import-title">
      <div className="dialog-heading"><div><span className="dialog-kicker">RESTORE DATA</span><h2 id="import-title">确认导入备份</h2></div><button className="icon-button" aria-label="关闭" onClick={cancelImport}><X size={18}/></button></div>
      <p>备份生成时间：{new Date(importJob.manifest.createdAt).toLocaleString()}</p>
      <p>将整体替换 Linkwarden 收藏和附件、首页设置、Obsidian 笔记及笔记冲突副本。当前部署配置、密码和 Token 保持不变。</p>
      <p className="import-warning">当前数据回滚包已下载到电脑。备份文件含明文密码和 Token，请妥善保管。</p>
      <label className="import-confirm"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)}/><span>我确认替换当前数据</span></label>
      <div className="dialog-actions"><button className="secondary-button" onClick={cancelImport}>取消</button><button className="primary-button" onClick={() => void commitImport()} disabled={!confirmed || busy}><Check size={15}/>确认导入</button></div>
    </section></div>}
  </section>
}
