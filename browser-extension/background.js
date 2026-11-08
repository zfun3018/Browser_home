const DEFAULT_HOME = 'http://127.0.0.1:3011'

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'capture-current-page',
    title: '收藏到浏览器首页',
    contexts: ['page', 'link'],
  })
})

chrome.contextMenus.onClicked.addListener((info, tab) => {
  const target = info.linkUrl || tab?.url
  if (target) void capture({ ...tab, url: target, title: tab?.title || target })
})

async function settings() {
  return chrome.storage.local.get({ homepageUrl: DEFAULT_HOME, captureToken: '', defaultCollectionId: '' })
}

async function capture(tab) {
  const config = await settings()
  if (!config.captureToken) {
    await chrome.runtime.openOptionsPage()
    return
  }
  const url = tab.url || ''
  if (!/^https?:\/\//i.test(url)) {
    notify('无法收藏', '当前页面不是 HTTP(S) 地址。')
    return
  }
  try {
    const response = await fetch(`${config.homepageUrl.replace(/\/$/, '')}/api/capture`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Capture-Token': config.captureToken },
    body: JSON.stringify({ url, title: tab.title || url, ...(Number(config.defaultCollectionId) > 0 ? { collectionId: Number(config.defaultCollectionId) } : {}) }),
    })
    const body = await response.json().catch(() => ({}))
    if (response.status === 401) {
      notify('收藏失败', '首页令牌无效，请重新打开扩展设置。')
      await chrome.runtime.openOptionsPage()
      return
    }
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '首页暂时不可用')
    notify('收藏成功', body.link?.name || tab.title || url)
  } catch (error) {
    notify('收藏失败', error instanceof Error ? error.message : '无法连接浏览器首页。')
  }
}

function notify(title, message) {
  chrome.notifications.create({ type: 'basic', iconUrl: 'icons/icon48.png', title, message })
}
