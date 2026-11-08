const homepageUrl = document.querySelector('#homepageUrl')
const captureToken = document.querySelector('#captureToken')
const defaultCollectionId = document.querySelector('#defaultCollectionId')
const status = document.querySelector('#status')

async function loadCollections(preferred = defaultCollectionId.value) {
  const url = homepageUrl.value.trim().replace(/\/$/, '')
  const token = captureToken.value.trim()
  if (!/^https?:\/\/(127\.0\.0\.1|localhost):3011$/i.test(url) || !token) {
    defaultCollectionId.disabled = true
    return
  }
  try {
    const response = await fetch(`${url}/api/collections`)
    const collections = await response.json()
    if (!response.ok || !Array.isArray(collections)) throw new Error('无法读取分类')
    defaultCollectionId.replaceChildren(new Option('未设置（使用第一个分类）', ''), ...collections.map(item => new Option(item.name, item.id)))
    defaultCollectionId.value = collections.some(item => String(item.id) === String(preferred)) ? String(preferred) : ''
    defaultCollectionId.disabled = false
  } catch { defaultCollectionId.disabled = true }
}

chrome.storage.local.get({ homepageUrl: 'http://127.0.0.1:3011', captureToken: '', defaultCollectionId: '' }).then(values => {
  homepageUrl.value = values.homepageUrl
  captureToken.value = values.captureToken
  defaultCollectionId.value = values.defaultCollectionId || ''
  void loadCollections(values.defaultCollectionId || '')
})

captureToken.addEventListener('change', () => void loadCollections())
homepageUrl.addEventListener('change', () => void loadCollections())

document.querySelector('#save').addEventListener('click', async () => {
  const url = homepageUrl.value.trim().replace(/\/$/, '')
  const token = captureToken.value.trim()
  if (!/^https?:\/\/(127\.0\.0\.1|localhost):3011$/i.test(url) || !token) {
    status.textContent = '请输入本机首页地址和令牌。'
    return
  }
  await chrome.storage.local.set({ homepageUrl: url, captureToken: token, defaultCollectionId: defaultCollectionId.value ? Number(defaultCollectionId.value) : '' })
  status.textContent = '已保存。'
})
