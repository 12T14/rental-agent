// 使用 Vue 内存渲染器执行真实 App 脚本。
// 测试不依赖 DOM、浏览器、高德加载器、运行中的服务或模型。
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createRenderer, nextTick } from 'vue'

const appUrl = new URL('../App.vue', import.meta.url)
const { descriptor } = parse(await readFile(appUrl, 'utf8'))
let source = compileScript(descriptor, { id: 'offline-app-session', genDefaultAs: 'TestApp' }).content
source = source
  .replace("import ChatPanel from './components/ChatPanel.vue'", 'const ChatPanel = {}')
  .replace("import MapPanel from './components/MapPanel.vue'", 'const MapPanel = {}')
  .replaceAll("from 'vue'", `from ${JSON.stringify(import.meta.resolve('vue'))}`)
  .replaceAll('import.meta.env.VITE_USE_BACKEND_CHAT', "'true'")
  .replace(/from '(\.\/services\/[^']+)'/g, (_, path) => {
    const resolved = path.endsWith('.js') ? path : `${path}.js`
    return `from ${JSON.stringify(new URL(resolved, appUrl).href)}`
  })
source += '\nTestApp.render = () => null;\nexport default TestApp;\n'
const { default: App } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)

const renderer = createRenderer({
  createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
  parentNode: () => null, nextSibling: () => null
})

function mount() {
  const app = renderer.createApp(App)
  const instance = app.mount({})
  return { app, state: instance.$.setupState }
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
    storage: { mode: 'memory', persistent: false }, ...overrides
  }
}

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
      'event: token\ndata: {"text":"old-reply-must-not-appear"}\n\nevent: done\ndata: {"status":"completed"}\n\n'
    ))
    oldStreamController.close()
    await inFlight
    assert.equal(mounted.state.searching, true)
    assert.deepEqual(mounted.state.messages.map(message => message.text), ['会话s2'])
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
