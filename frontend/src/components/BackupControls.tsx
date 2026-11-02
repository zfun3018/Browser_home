import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, FileUp, HardDriveDownload, LoaderCircle, X } from 'lucide-react'
import './backup.css'

type BackupStatus = { lastBackupAt: string | null }
type BackupJob = { id: string; filename: string; lastBackupAt: string }
type ImportJob = { id: string; manifest: { createdAt: string; applicationVersion?: string }; rollbackFilename?: string }
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
    const timer = window.setInterval(focus, 5 * 60 * 1000)
    return () => {
      window.removeEventListener('focus', focus)
      window.clearInterval(timer)
    }
  }, [refreshStatus])

  async function backup() {
    setBusy(true)
    setError('')
    try {
      const job = await request<BackupJob>('/api/backup', { method: 'POST', body: '{}' })
      setLastBackupAt(job.lastBackupAt)
      setImportStatus(`已保存：${job.filename}`)
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
      setImportStatus('正在将回滚备份写入本地文件夹…')

      for (let attempt = 0; attempt < 360; attempt += 1) {
        await new Promise(resolve => window.setTimeout(resolve, 1000))
        const status = await request<ImportStatus>(`/api/import/${encodeURIComponent(job.id)}/status`)
        if (status.status === 'ready_to_commit' && status.rollbackDelivered) {
          setImportStatus(`回滚备份已保存${job.rollbackFilename ? `：${job.rollbackFilename}` : '到本地文件夹'}`)
          setConfirmReplace(true)
          setBusy(false)
          return
        }
        if (status.status === 'failed') throw new Error(status.error || '导入准备失败')
      }
      throw new Error('回滚备份写入本地文件夹未完成，请检查备份服务状态')
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
  return <section className="backup-widget" aria-label="首页备份">
    <div className={`backup-status${due ? ' backup-status-due' : ''}${error ? ' backup-status-error' : ''}`} role="status" aria-live="polite" title={error || importStatus || (lastBackupAt ? `最近备份：${new Date(lastBackupAt).toLocaleString()}` : '尚无备份；自动备份每周执行一次')}>
      <i aria-hidden="true"/>
      <span>{busy ? '备份中' : due ? '待备份' : `已备份 ${new Date(lastBackupAt!).toLocaleDateString()}`}</span>
    </div>
    <div className="backup-actions">
      <button className="backup-button" aria-label="立即备份到本地文件夹" title="立即备份到本地文件夹（ZIP 含明文凭据）" onClick={() => void backup()} disabled={busy}>
        {busy ? <LoaderCircle className="spin" size={14}/> : <HardDriveDownload size={14}/>}
      </button>
      <button className="backup-button" aria-label="导入备份" title="导入备份" onClick={() => fileRef.current?.click()} disabled={busy}>
        {busy ? <LoaderCircle className="spin" size={14}/> : <FileUp size={14}/>}
      </button>
      <input ref={fileRef} className="backup-file-input" type="file" accept=".zip,application/zip" aria-label="选择备份 ZIP 文件" onChange={event => void chooseImport(event.target.files?.[0])}/>
    </div>
    {confirmReplace && importJob && <div className="modal-backdrop" role="presentation"><section className="dialog import-dialog" role="dialog" aria-modal="true" aria-labelledby="import-title">
      <div className="dialog-heading"><div><span className="dialog-kicker">RESTORE DATA</span><h2 id="import-title">确认导入备份</h2></div><button className="icon-button" aria-label="关闭" onClick={cancelImport}><X size={18}/></button></div>
      <p>备份生成时间：{new Date(importJob.manifest.createdAt).toLocaleString()}</p>
      <p>将整体替换本地书签、首页设置、Obsidian 笔记及笔记冲突副本。当前部署配置和密码保持不变。</p>
      <p className="import-warning">当前数据的回滚包已保存到本机备份文件夹。备份文件含明文密码和 Token，请妥善保管。</p>
      <label className="import-confirm"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)}/><span>我确认替换当前数据</span></label>
      <div className="dialog-actions"><button className="secondary-button" onClick={cancelImport}>取消</button><button className="primary-button" onClick={() => void commitImport()} disabled={!confirmed || busy}><Check size={15}/>确认导入</button></div>
    </section></div>}
  </section>
}
