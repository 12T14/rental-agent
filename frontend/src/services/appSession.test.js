// 使用 Vue 内存渲染器执行真实 App 脚本和模板。
// 测试不依赖 DOM、浏览器、高德加载器、运行中的服务或模型。
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { includeBooleanAttr } from '@vue/shared'
import { createRenderer, nextTick } from 'vue'

const appUrl = new URL('../App.vue', import.meta.url)
const { descriptor } = parse(await readFile(appUrl, 'utf8'))
const compiledScript = compileScript(descriptor, { id: 'offline-app-session', genDefaultAs: 'TestApp' })
const compiledTemplate = compileTemplate({
  source: descriptor.template.content,
  id: 'offline-app-session',
  compilerOptions: { bindingMetadata: compiledScript.bindings }
})
assert.deepEqual(compiledTemplate.errors, [])
let source = compiledScript.content + '\n' + compiledTemplate.code.replace('export function render', 'function render')
source = source
  .replace("import ChatPanel from './components/ChatPanel.vue'", 'const ChatPanel = { render: () => null }')
  .replace("import MapPanel from './components/MapPanel.vue'", 'const MapPanel = { render: () => null }')
  .replace(/from (['"])vue\1/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
  .replaceAll('import.meta.env.VITE_USE_BACKEND_CHAT', "'true'")
  .replace(/from '(\.\/services\/[^']+)'/g, (_, path) => {
    const resolved = path.endsWith('.js') ? path : `${path}.js`
    return `from ${JSON.stringify(new URL(resolved, appUrl).href)}`
  })
source += '\nTestApp.render = render;\nexport default TestApp;\n'
const { default: App } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)

function memoryNode(type, text = '') {
  return { type, text, children: [], props: {}, parent: null }
}
function removeNode(node) {
  if (!node.parent) return
  const siblings = node.parent.children
  siblings.splice(siblings.indexOf(node), 1)
  node.parent = null
}
function insertNode(node, parent, anchor = null) {
  removeNode(node)
  const siblings = parent.children ||= []
  const index = anchor ? siblings.indexOf(anchor) : -1
  siblings.splice(index < 0 ? siblings.length : index, 0, node)
  node.parent = parent
}
const renderer = createRenderer({
  createElement: type => memoryNode(type),
  createText: text => memoryNode('text', text),
  createComment: text => memoryNode('comment', text),
  insertStaticContent(content, parent, anchor) {
    const node = memoryNode('static', content)
    insertNode(node, parent, anchor)
    return [node, node]
  },
  insert: insertNode,
  remove: removeNode,
  setText: (node, text) => { node.text = text },
  setElementText(node, text) {
    node.children.forEach(child => { child.parent = null })
    node.children = []
    node.text = text
  },
  patchProp: (node, key, previous, value) => { node.props[key] = value },
  parentNode: node => node.parent,
  nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] || null
})

function mount() {
  const app = renderer.createApp(App)
  const instance = app.mount({})
  return { app, state: instance.$.setupState, instance }
}

function privacyButtons(mounted) {
  const buttons = []
  function visit(vnode) {
    if (!vnode || typeof vnode !== 'object') return
    if (vnode.type === 'button' && vnode.props?.class?.includes('privacy-action')) buttons.push(vnode)
    if (Array.isArray(vnode.children)) vnode.children.forEach(visit)
  }
  visit(mounted.instance.$.subTree)
  assert.equal(buttons.length, 3)
  return buttons
}

async function settleUntil(predicate) {
  for (let index = 0; index < 100; index += 1) {
    await new Promise(resolve => setImmediate(resolve))
    await nextTick()
    if (predicate()) return
  }
  assert.fail('offline UI state did not settle')
}

function snapshot(id, overrides = {}) {
  return {
    session_id: id, title: id, status: 'completed',
    messages: [{ id: `${id}-assistant`, role: 'assistant', text: `会话${id}` }],
    pending: null, location: null, recovery_request_id: null, latest_request_id: 'r1',
    listings: [], platforms: [], search: null,
    storage: { mode: 'memory', persistent: false }, ...overrides
  }
}

const listingEventStream = [
  'event: platform_status\ndata: {"platforms":[{"key":"58","name":"58同城","status":"ok","label":"已获取","count":1}],"status":"completed","offline":true}\n\n',
  'event: listings\ndata: {"listings":[{"id":"l1","platformKey":"58","platform":"58同城","title":"列表标题","community":"示例小区","rent":1000,"room":"一室","area":45,"detailUrl":"https://example.58.com/zufang/l1.html","urlType":"detail","offline":true}],"status":"completed","offline":true}\n\n',
  'event: listing_details\ndata: {"details":[{"url":"https://example.58.com/zufang/l1.html","status":"ok","title":"详情标题","facts":{"monthly_rent_cny":1050,"payment_rule":"押一付一"}}],"status":"completed","offline":true}\n\n',
  'event: listing_update\ndata: {"listings":[{"id":"l1","platformKey":"58","platform":"58同城","title":"详情标题","community":"示例小区","rent":1050,"room":"一室","area":45,"detailUrl":"https://example.58.com/zufang/l1.html","urlType":"detail","detailFacts":{"monthly_rent_cny":1050,"payment_rule":"押一付一"},"detailStatus":"ok","offline":true}],"status":"completed","offline":true}\n\n'
].join('') + 'event: assistant\ndata: {"text":"已整理"}\n\nevent: done\ndata: {"status":"completed"}\n\n'

test('App renders structured listing events and restores them from session history', async () => {
  const originalFetch = globalThis.fetch
  let saved = snapshot('s-listings')
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('?')) return Response.json({ sessions: [{ session_id: 's-listings', title: 's-listings' }], storage: saved.storage })
    if (url.endsWith('/chat/stream')) {
      saved = snapshot('s-listings', {
        messages: [{ id: 'assistant', role: 'assistant', text: '已整理' }],
        listings: [{ id: 'l1', platformKey: '58', platform: '58同城', title: '详情标题', community: '示例小区', rent: 1050, room: '一室', area: 45, detailUrl: 'https://example.58.com/zufang/l1.html', urlType: 'detail', detailFacts: { monthly_rent_cny: 1050, payment_rule: '押一付一' }, detailStatus: 'ok', offline: true }],
        platforms: [{ key: '58', name: '58同城', status: 'ok', label: '已获取', count: 1 }],
        search: { status: 'completed', detail_status: 'completed', offline: true }
      })
      return new Response(listingEventStream)
    }
    return Response.json(saved)
  }
  let mounted
  try {
    mounted = mount()
    await settleUntil(() => !mounted.state.historyLoading && mounted.state.activeHistory === 's-listings')
    await mounted.state.handleChatMessage('开始找房')
    assert.equal(mounted.state.listings.length, 1)
    assert.equal(mounted.state.listings[0].rent, 1050)
    assert.equal(mounted.state.listings[0].detailFacts.payment_rule, '押一付一')
    assert.equal(mounted.state.platforms[0].key, 'fifty-eight')
    assert.equal(mounted.state.searchSummary.detail_status, 'completed')
    assert.equal(mounted.state.messages.some(message => message.kind === 'result'), false)

    await mounted.state.refreshCurrentSession()
    assert.equal(mounted.state.listings[0].title, '详情标题')
    assert.match(mounted.state.messages.at(-1).text, /^已整理/)
    assert.equal(mounted.state.messages.some(message => message.kind === 'result'), false)
    assert.equal(mounted.state.searchSummary.offline, true)
  } finally {
    mounted?.app.unmount()
    globalThis.fetch = originalFetch
  }
})

test('App picks up a model-generated title after the terminal chat response', async () => {
  const originalFetch = globalThis.fetch
  let saved = snapshot('s-title', { title: '首条需求', title_source: 'pending' })
  let sessionReads = 0
  globalThis.fetch = async (url) => {
    if (url.includes('?')) {
      return Response.json({ sessions: [{ session_id: 's-title', title: saved.title }], storage: saved.storage })
    }
    if (url.endsWith('/chat/stream')) {
      saved = snapshot('s-title', {
        title: '首条需求', title_source: 'pending',
        messages: [{ id: 'assistant', role: 'assistant', text: '已完成' }]
      })
      return new Response('event: done\ndata: {"status":"completed"}\n\n')
    }
    sessionReads += 1
    if (sessionReads >= 3) saved = { ...saved, title: '常州大学城租房', title_source: 'model' }
    return Response.json(saved)
  }
  let mounted
  try {
    mounted = mount()
    await settleUntil(() => !mounted.state.historyLoading && mounted.state.activeHistory === 's-title')
    await mounted.state.handleChatMessage('找常州大学城附近房源')
    await new Promise(resolve => setTimeout(resolve, 400))
    assert.equal(mounted.state.sessionTitle, '常州大学城租房')
    assert.equal(mounted.state.conversations[0].title, '常州大学城租房')
  } finally {
    mounted?.app.unmount()
    globalThis.fetch = originalFetch
  }
})

test('App reveals eligible listings twenty at a time', async () => {
  const originalFetch = globalThis.fetch
  const candidates = Array.from({ length: 46 }, (_, index) => ({
    id: `listing-${index + 1}`,
    platformKey: '58',
    platform: '58同城',
    title: `候选 ${index + 1}`,
    rent: 1000 + index,
    filterStatus: index === 45 ? 'excluded' : 'passed'
  }))
  const saved = snapshot('s-pagination', {
    listings: candidates,
    search: { status: 'completed' }
  })
  globalThis.fetch = async url => {
    if (url.includes('?')) {
      return Response.json({ sessions: [{ session_id: 's-pagination', title: 's-pagination' }], storage: saved.storage })
    }
    return Response.json(saved)
  }
  let mounted
  try {
    mounted = mount()
    await settleUntil(() => !mounted.state.historyLoading && mounted.state.activeHistory === 's-pagination')
    assert.equal(mounted.state.eligibleListings.length, 45)
    assert.equal(mounted.state.displayListings.length, 20)
    assert.equal(mounted.state.remainingListingCount, 25)

    mounted.state.loadMoreListings()
    await nextTick()
    assert.equal(mounted.state.displayListings.length, 40)
    assert.equal(mounted.state.remainingListingCount, 5)

    mounted.state.loadMoreListings()
    await nextTick()
    assert.equal(mounted.state.displayListings.length, 45)
    assert.equal(mounted.state.remainingListingCount, 0)
  } finally {
    mounted?.app.unmount()
    globalThis.fetch = originalFetch
  }
})

test('A saved empty search never becomes a synthetic result message on a later turn', async () => {
  const originalFetch = globalThis.fetch
  let saved = snapshot('s-empty', {
    messages: [{ id: 'old-assistant', role: 'assistant', text: '上一轮没有找到房源。' }],
    search: { status: 'empty' }
  })
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('?')) {
      return Response.json({ sessions: [{ session_id: 's-empty', title: 's-empty' }], storage: saved.storage })
    }
    if (url.endsWith('/chat/stream')) {
      const body = JSON.parse(options.body)
      saved = snapshot('s-empty', {
        messages: [
          ...saved.messages,
          { id: 'follow-up-user', role: 'user', text: body.message },
          { id: 'follow-up-assistant', role: 'assistant', text: '可以，我继续帮你调整条件。' }
        ],
        search: { status: 'empty' }
      })
      return new Response('event: assistant\ndata: {"text":"可以，我继续帮你调整条件。"}\n\nevent: done\ndata: {"status":"completed"}\n\n')
    }
    return Response.json(saved)
  }
  let mounted
  try {
    mounted = mount()
    await settleUntil(() => !mounted.state.historyLoading && mounted.state.activeHistory === 's-empty')
    assert.equal(mounted.state.messages.some(message => message.kind === 'result'), false)

    await mounted.state.handleChatMessage('那把预算提高一些')

    assert.equal(mounted.state.messages.at(-1).text, '可以，我继续帮你调整条件。')
    assert.equal(mounted.state.messages.some(message => message.kind === 'result'), false)
    assert.equal(mounted.state.searchSummary.status, 'empty')
  } finally {
    mounted?.app.unmount()
    globalThis.fetch = originalFetch
  }
})

test('A transient SSE error is cleared when the saved turn is completed', async () => {
  const originalFetch = globalThis.fetch
  let saved = snapshot('s-completed')
  globalThis.fetch = async (url) => {
    if (url.includes('?')) {
      return Response.json({ sessions: [{ session_id: 's-completed', title: 's-completed' }], storage: saved.storage })
    }
    if (url.endsWith('/chat/stream')) {
      saved = snapshot('s-completed', {
        messages: [{ id: 'saved-assistant', role: 'assistant', text: '后端已经保存了最终回复。' }]
      })
      return new Response(
        'event: error\ndata: {"error":{"code":"agent_error","message":"临时传输错误"}}\n\nevent: done\ndata: {"status":"error"}\n\n'
      )
    }
    return Response.json(saved)
  }
  let mounted
  try {
    mounted = mount()
    await settleUntil(() => !mounted.state.historyLoading && mounted.state.activeHistory === 's-completed')
    await mounted.state.handleChatMessage('继续')

    assert.equal(mounted.state.searching, false)
    assert.equal(mounted.state.recoveryRequestId, '')
    assert.equal(mounted.state.runtimeNotice, null)
    assert.deepEqual(mounted.state.messages.map(message => message.text), ['后端已经保存了最终回复。'])
  } finally {
    mounted?.app.unmount()
    globalThis.fetch = originalFetch
  }
})

test('App loads saved pause and sends a natural-language reply through resume', async () => {
  const originalFetch = globalThis.fetch
  const pending = { interrupt_id: 'p1', type: 'rental_preferences', message: '请补充预算', missing_fields: ['budget'] }
  let saved = snapshot('s1', { status: 'interrupted', pending })
  let submitted = null
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('?')) return Response.json({ sessions: [{ session_id: 's1', title: 's1' }], storage: saved.storage })
    if (url === '/api/v1/sessions/s1') return Response.json(saved)
    assert.equal(url, '/api/v1/sessions/s1/resume/stream')
    submitted = JSON.parse(options.body)
    saved = snapshot('s1', { latest_request_id: submitted.client_request_id, messages: [
      { id: 'reply-user', role: 'user', text: submitted.message },
      { id: 'reply-assistant', role: 'assistant', text: '好的，继续' }
    ] })
    return new Response('event: assistant\ndata: {"text":"好的，继续"}\n\nevent: done\ndata: {"status":"completed"}\n\n')
  }
  let mounted
  try {
    mounted = mount()
    await settleUntil(() => !mounted.state.historyLoading && mounted.state.activeHistory === 's1')
    assert.equal(mounted.state.pendingInterrupt.interrupt_id, 'p1')
    assert.equal(mounted.state.messages.length, 1)
    assert.equal(mounted.state.conversations.length, 1)
    await mounted.state.handleChatMessage('预算2000元')
    assert.equal(submitted.message, '预算2000元')
    assert.equal(submitted.interrupt_id, 'p1')
    assert.equal(mounted.state.pendingInterrupt, null)
    assert.equal(mounted.state.messages.at(-1).text, '好的，继续')
    assert.equal(mounted.state.searching, false)
  } finally {
    mounted?.app.unmount()
    globalThis.fetch = originalFetch
  }
})

test('App ignores late stream events and finalizers after switching sessions', async () => {
  const originalFetch = globalThis.fetch
  let oldStreamController
  const encoder = new TextEncoder()
  globalThis.fetch = async (url) => {
    if (url.includes('?')) return Response.json({ sessions: [
      { session_id: 's1', title: 's1' }, { session_id: 's2', title: 's2' }
    ], storage: { mode: 'memory' } })
    if (url.endsWith('/chat/stream')) {
      return new Response(new ReadableStream({
        start(controller) {
          oldStreamController = controller
          controller.enqueue(encoder.encode('event: status\ndata: {"status":"running"}\n\n'))
        }
      }))
    }
    return Response.json(snapshot(url.endsWith('s1') ? 's1' : 's2'))
  }
  let mounted
  try {
    mounted = mount()
    await settleUntil(() => !mounted.state.historyLoading && mounted.state.activeHistory === 's1')
    const inFlight = mounted.state.handleChatMessage('新要求')
    await settleUntil(() => Boolean(oldStreamController))
    await mounted.state.loadHistory({ id: 's2', title: 's2' })
    assert.equal(mounted.state.activeHistory, 's2')
    // 模拟另一个当前操作；旧请求的 finally 不能清除忙碌标记。
    mounted.state.searching = true
    oldStreamController.enqueue(encoder.encode(
      'event: listings\ndata: {"listings":[{"id":"old-listing","title":"old-listing"}],"status":"completed"}\n\nevent: token\ndata: {"text":"old-reply-must-not-appear"}\n\nevent: done\ndata: {"status":"completed"}\n\n'
    ))
    oldStreamController.close()
    await inFlight
    assert.equal(mounted.state.searching, true)
    assert.deepEqual(mounted.state.messages.map(message => message.text), ['会话s2'])
    assert.deepEqual(mounted.state.listings, [])
  } finally {
    mounted?.app.unmount()
    globalThis.fetch = originalFetch
  }
})

test('App recovery does not append a fake user message or resend edited draft', async () => {
  const originalFetch = globalThis.fetch
  let saved = snapshot('s1', { status: 'recoverable', recovery_request_id: 'r-original' })
  let submitted
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('?')) return Response.json({ sessions: [{ session_id: 's1', title: 's1' }], storage: saved.storage })
    if (url.endsWith('/recover/stream')) {
      submitted = JSON.parse(options.body)
      saved = snapshot('s1', { latest_request_id: 'r-original' })
      return new Response('event: done\ndata: {"status":"completed"}\n\n')
    }
    return Response.json(saved)
  }
  let mounted
  try {
    mounted = mount()
    await settleUntil(() => !mounted.state.historyLoading && mounted.state.activeHistory === 's1')
    mounted.state.chatDraft = '这是下一条新需求'
    await mounted.state.handleChatMessage('', { recover: true })
    assert.deepEqual(submitted, { client_request_id: 'r-original' })
    assert.equal(mounted.state.chatDraft, '这是下一条新需求')
    assert.equal(mounted.state.recoveryRequestId, '')
    assert.equal(mounted.state.messages.some(message => message.role === 'user'), false)
  } finally {
    mounted?.app.unmount()
    globalThis.fetch = originalFetch
  }
})

test('Agent failure exposes recovery without clearing the next draft', async () => {
  const originalFetch = globalThis.fetch
  let saved = snapshot('s1')
  let submitted
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('?')) return Response.json({ sessions: [{ session_id: 's1', title: 's1' }], storage: saved.storage })
    if (url.endsWith('/chat/stream')) {
      submitted = JSON.parse(options.body)
      saved = snapshot('s1', { status: 'recoverable', recovery_request_id: submitted.client_request_id })
      return new Response(
        'event: error\ndata: {"error":{"code":"agent_error","message":"Agent 运行失败"}}\n\nevent: done\ndata: {"status":"error"}\n\n'
      )
    }
    return Response.json(saved)
  }
  let mounted
  try {
    mounted = mount()
    await settleUntil(() => !mounted.state.historyLoading && mounted.state.activeHistory === 's1')
    mounted.state.chatDraft = '继续'
    await mounted.state.handleChatMessage('新的搜索要求')
    assert.equal(submitted.message, '新的搜索要求')
    assert.equal('search_context' in submitted, false)
    assert.equal(mounted.state.searching, false)
    assert.equal(mounted.state.recoveryRequestId, submitted.client_request_id)
    assert.equal(mounted.state.chatDraft, '继续')
    assert.equal(mounted.state.runtimeNotice.message, 'Agent 运行失败')
    assert.equal(mounted.state.messages.some(message => message.text === 'Agent 运行失败'), false)
  } finally {
    mounted?.app.unmount()
    globalThis.fetch = originalFetch
  }
})

test('keeps location candidates visible and sends a selected target on the next message', async () => {
  const originalFetch = globalThis.fetch
  const location = {
    status: 'needs_confirmation',
    provider: 'fake',
    query: '示例大学',
    city_hint: '示例市',
    requires_user_confirmation: true,
    candidates: [{
      candidate_ref: 'place-1',
      name: '示例大学东校区',
      formatted_address: '示例市示例区示例路 1 号',
      city: '示例市', district: '示例区', adcode: '320000',
      lng: 120.1, lat: 32.1
    }]
  }
  let saved = snapshot('s-location')
  const requests = []
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('?')) return Response.json({ sessions: [{ session_id: 's-location', title: 's-location' }], storage: saved.storage })
    if (url === '/api/v1/sessions/s-location') return Response.json(saved)
    if (url.endsWith('/chat/stream')) {
      const body = JSON.parse(options.body)
      requests.push(body)
      saved = snapshot('s-location', { location, messages: [{ id: 'assistant', role: 'assistant', text: '已收到' }] })
      if (requests.length === 1) {
        return new Response(
          `event: location_candidates\ndata: ${JSON.stringify(location)}\n\nevent: done\ndata: {"status":"completed"}\n\n`
        )
      }
      return new Response('event: assistant\ndata: {"text":"已按目标地点继续"}\n\nevent: done\ndata: {"status":"completed"}\n\n')
    }
    return Response.json(saved)
  }
  let mounted
  try {
    mounted = mount()
    await settleUntil(() => !mounted.state.historyLoading && mounted.state.activeHistory === 's-location')
    await mounted.state.handleChatMessage('先找这个学校')
    assert.equal(mounted.state.placeCandidates.length, 1)

    mounted.state.selectPlaceCandidate(mounted.state.placeCandidates[0])
    assert.equal(mounted.state.confirmedPlaceRef, 'place-1')
    assert.equal(mounted.state.placeCandidates.length, 1)
    assert.match(mounted.state.chatDraft, /^我想在示例大学东校区附近找房，/)

    await mounted.state.handleChatMessage(mounted.state.chatDraft)
    assert.equal(requests.length, 2)
    assert.equal(requests[1].search_context.confirmed_target.candidate_ref, 'place-1')
  } finally {
    mounted?.app.unmount()
    globalThis.fetch = originalFetch
  }
})

test('privacy buttons render boolean false while an existing session is idle', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async url => Response.json(url.includes('?')
    ? { sessions: [{ session_id: 's-privacy', title: 's-privacy' }], storage: { mode: 'memory' } }
    : snapshot('s-privacy'))
  let mounted
  try {
    mounted = mount()
    await settleUntil(() => !mounted.state.historyLoading && mounted.state.activeHistory === 's-privacy')
    // Vue treats an empty string as a present boolean HTML attribute, not false.
    assert.equal(includeBooleanAttr(''), true)
    for (const button of privacyButtons(mounted)) {
      assert.equal(button.props.disabled, false)
      assert.equal(includeBooleanAttr(button.props.disabled), false)
    }
    mounted.state.searching = true
    await nextTick()
    assert.deepEqual(privacyButtons(mounted).map(button => button.props.disabled), [true, false, false])
    mounted.state.searching = false
    mounted.state.resetSearch()
    await nextTick()
    assert.deepEqual(privacyButtons(mounted).map(button => button.props.disabled), [true, false, false])
  } finally {
    mounted?.app.unmount()
    globalThis.fetch = originalFetch
  }
})

const privacyActions = [
  { method: 'deleteActiveSession', busy: 'session', url: '/api/v1/sessions/s-privacy', httpMethod: 'DELETE' },
  { method: 'clearPlatformVerification', busy: 'platform', url: '/api/v1/privacy/platform-sessions/clear', httpMethod: 'POST' },
  { method: 'clearLocalArtifacts', busy: 'artifacts', url: '/api/v1/privacy/artifacts/clear', httpMethod: 'POST' }
]

for (const action of privacyActions) {
  for (const outcome of ['success', 'failure']) {
    test(`${action.method} disables all privacy actions and releases them after ${outcome}`, async () => {
      const originalFetch = globalThis.fetch
      const originalConfirm = globalThis.confirm
      let resolveCleanup
      let rejectCleanup
      let requests = 0
      let confirmations = 0
      globalThis.confirm = () => { confirmations += 1; return true }
      globalThis.fetch = async (url, options = {}) => {
        if (options.method === 'DELETE' || options.method === 'POST') {
          requests += 1
          assert.equal(url, action.url)
          assert.equal(options.method, action.httpMethod)
          return new Promise((resolve, reject) => { resolveCleanup = resolve; rejectCleanup = reject })
        }
        return Response.json(url.includes('?')
          ? { sessions: [{ session_id: 's-privacy', title: 's-privacy' }], storage: { mode: 'memory' } }
          : snapshot('s-privacy'))
      }
      let mounted
      let cleanup
      try {
        mounted = mount()
        await settleUntil(() => !mounted.state.historyLoading && mounted.state.activeHistory === 's-privacy')
        cleanup = mounted.state[action.method]()
        await nextTick()
        assert.deepEqual(privacyButtons(mounted).map(button => button.props.disabled), [true, true, true])
        assert.deepEqual(privacyButtons(mounted).map(button => button.props['aria-busy']),
          privacyActions.map(candidate => candidate.busy === action.busy))
        for (const otherAction of privacyActions) await mounted.state[otherAction.method]()
        assert.equal(requests, 1)
        assert.equal(confirmations, 1)

        if (outcome === 'success') resolveCleanup(Response.json({ status: 'ok' }))
        else rejectCleanup(new Error('test cleanup failure'))
        await cleanup
        await nextTick()
        const deleted = action.busy === 'session' && outcome === 'success'
        assert.deepEqual(privacyButtons(mounted).map(button => button.props.disabled), [deleted, false, false])
        assert.deepEqual(privacyButtons(mounted).map(button => button.props['aria-busy']), [false, false, false])
        assert.equal(mounted.state.privacyBusy, '')
        assert.equal(mounted.state.activeHistory, deleted ? '' : 's-privacy')
        assert.equal(mounted.state.conversations.length, deleted ? 0 : 1)
        assert.equal(mounted.state.runtimeNotice.tone, outcome === 'success' ? 'info' : 'error')
      } finally {
        rejectCleanup?.(new Error('test finished'))
        await cleanup
        mounted?.app.unmount()
        globalThis.fetch = originalFetch
        globalThis.confirm = originalConfirm
      }
    })
  }

  test(`${action.method} leaves data and buttons unchanged when confirmation is cancelled`, async () => {
    const originalFetch = globalThis.fetch
    const originalConfirm = globalThis.confirm
    let requests = 0
    globalThis.confirm = () => false
    globalThis.fetch = async (url, options = {}) => {
      if (options.method === 'DELETE' || options.method === 'POST') requests += 1
      return Response.json(url.includes('?')
        ? { sessions: [{ session_id: 's-privacy', title: 's-privacy' }], storage: { mode: 'memory' } }
        : snapshot('s-privacy'))
    }
    let mounted
    try {
      mounted = mount()
      await settleUntil(() => !mounted.state.historyLoading && mounted.state.activeHistory === 's-privacy')
      await mounted.state[action.method]()
      await nextTick()
      assert.equal(requests, 0)
      assert.equal(mounted.state.activeHistory, 's-privacy')
      assert.equal(mounted.state.privacyBusy, '')
      assert.deepEqual(privacyButtons(mounted).map(button => button.props.disabled), [false, false, false])
    } finally {
      mounted?.app.unmount()
      globalThis.fetch = originalFetch
      globalThis.confirm = originalConfirm
    }
  })
}
