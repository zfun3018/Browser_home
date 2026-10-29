import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import BackupControls, { backupIsDue } from './BackupControls'

describe('homepage backup reminder', () => {
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

  it('is due before any backup and only after the exact seven-day boundary', () => {
    const now = Date.parse('2026-10-29T00:00:00Z')
    expect(backupIsDue(null, now)).toBe(true)
    expect(backupIsDue('invalid', now)).toBe(true)
    expect(backupIsDue('2026-10-22T00:00:00Z', now)).toBe(false)
    expect(backupIsDue('2026-10-21T23:59:59.999Z', now)).toBe(true)
  })

  it('shows a backup action when status has never been recorded', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ lastBackupAt: null }),
    } as Response)))

    render(<BackupControls />)

    expect(await screen.findByText('还没有首页备份')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '立即备份' })).toBeInTheDocument()
    expect(screen.getByText(/明文凭据/)).toBeInTheDocument()
  })

  it('downloads the current rollback before enabling full replacement', async () => {
    const identity = '11111111-1111-4111-8111-111111111111'
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/backup/status') return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ lastBackupAt: null }) } as Response)
      if (url === '/api/import/prepare') return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ id: identity, manifest: { createdAt: '2026-10-20T00:00:00Z' } }) } as Response)
      if (url.endsWith('/status')) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: 'ready_to_commit', rollbackDelivered: true }) } as Response)
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: 'complete' }) } as Response)
    })
    vi.stubGlobal('fetch', fetchMock)
    const clickedAnchors: HTMLAnchorElement[] = []
    const clickDownload = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clickedAnchors.push(this)
    })
    const onImported = vi.fn()
    render(<BackupControls onImported={onImported}/>)
    expect(await screen.findByText('还没有首页备份')).toBeInTheDocument()

    vi.useFakeTimers()
    fireEvent.change(screen.getByLabelText('选择备份 ZIP 文件'), { target: { files: [new File(['archive'], 'backup.zip', { type: 'application/zip' })] } })
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
      await vi.advanceTimersByTimeAsync(1000)
      await Promise.resolve()
    })

    expect(screen.getByRole('dialog', { name: '确认导入备份' })).toBeInTheDocument()
    expect(clickDownload).toHaveBeenCalled()
    const rollbackAnchor = clickedAnchors[0]
    expect(rollbackAnchor.href).toContain(`/api/import/${identity}/rollback`)
    expect(screen.getByRole('button', { name: '确认导入' })).toBeDisabled()

    fireEvent.click(screen.getByRole('checkbox', { name: /我确认替换当前数据/ }))
    fireEvent.click(screen.getByRole('button', { name: '确认导入' }))
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(fetchMock).toHaveBeenCalledWith(`/api/import/${identity}/commit`, expect.objectContaining({ method: 'POST' }))
    expect(onImported).toHaveBeenCalledOnce()
  })
})
