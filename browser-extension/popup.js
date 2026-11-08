const DEFAULT_HOME = 'http://127.0.0.1:3011'
const pageTitle = document.querySelector('#pageTitle')
const formArea = document.querySelector('#formArea')
const bookmarkName = document.querySelector('#bookmarkName')
const bookmarkUrl = document.querySelector('#bookmarkUrl')
const collection = document.querySelector('#collection')
const rememberDefault = document.querySelector('#rememberDefault')
const save = document.querySelector('#save')
const status = document.querySelector('#status')
const setup = document.querySelector('#setup')

let tab = null
let config = null

function showStatus(message, kind = '') {
  status.textContent = message
  status.className = `status ${kind}`
}

function openOptions() { chrome.runtime.openOptionsPage() }
document.querySelector('#openOptions').addEventListener('click', openOptions)

async function load() {
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
  pageTitle.textContent = tab?.title || tab?.url || '当前页面'
  bookmarkName.value = tab?.title || tab?.url || ''
  bookmarkUrl.value = tab?.url || ''
  config = await chrome.storage.local.get({ homepageUrl: DEFAULT_HOME, captureToken: '', defaultCollectionId: '' })
  if (!config.captureToken) {
    showStatus('请先配置首页令牌。', 'error'); setup.classList.remove('hidden'); return
  }
  if (!/^https?:\/\//i.test(tab?.url || '')) {
    showStatus('当前页面不是 HTTP(S) 地址，无法收藏。', 'error'); return
  }
  try {
    const response = await fetch(`${config.homepageUrl.replace(/\/$/, '')}/api/collections`)
    const collections = await response.json()
    if (!response.ok || !Array.isArray(collections) || !collections.length) throw new Error('首页中没有可用分类')
    collection.replaceChildren(...collections.map(item => { const option = document.createElement('option'); option.value = item.id; option.textContent = item.name; return option }))
    const preferred = String(config.defaultCollectionId || collections[0].id)
    collection.value = collections.some(item => String(item.id) === preferred) ? preferred : String(collections[0].id)
    rememberDefault.checked = String(config.defaultCollectionId || '') === collection.value
    formArea.classList.remove('hidden')
  } catch (error) { showStatus(error instanceof Error ? error.message : '无法读取首页分类。', 'error') }
}

save.addEventListener('click', async () => {
  const url = bookmarkUrl.value.trim()
  const name = (bookmarkName.value.trim() || url).slice(0, 2048)
  if (!/^https?:\/\//i.test(url)) {
    showStatus('请输入有效的 HTTP(S) 网址。', 'error')
    bookmarkUrl.focus()
    return
  }
  save.disabled = true
  showStatus('正在收藏…')
  const selected = collection.options[collection.selectedIndex]
  try {
    if (rememberDefault.checked) await chrome.storage.local.set({ defaultCollectionId: Number(collection.value) })
    const response = await fetch(`${config.homepageUrl.replace(/\/$/, '')}/api/capture`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Capture-Token': config.captureToken },
      body: JSON.stringify({ url, title: name, collectionId: Number(collection.value) }),
    })
    const body = await response.json().catch(() => ({}))
    if (response.status === 401) { showStatus('令牌无效，请重新配置。', 'error'); setup.classList.remove('hidden'); openOptions(); return }
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '首页暂时不可用')
    showStatus(`已收藏到「${selected?.textContent || '所选分类'}」`, 'success')
    chrome.notifications.create({ type: 'basic', iconUrl: 'icons/icon48.png', title: '收藏成功', message: `${name}\n分类：${selected?.textContent || '所选分类'}` })
  } catch (error) { showStatus(error instanceof Error ? error.message : '无法连接浏览器首页。', 'error') }
  finally { save.disabled = false }
})

void load()
