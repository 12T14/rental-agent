import assert from 'node:assert/strict'
import test from 'node:test'

import {
  canSubmitChatMessage,
  clearArtifacts,
  clearPlatformSessions,
  deleteSession,
  parseEventBlock,
  streamChat
} from './chatApi.js'


test('parses SSE ids, event names and JSON payloads', () => {
  assert.deepEqual(
    parseEventBlock('id: stream-a:2\nevent: token\ndata: {"text":"你好"}'),
    { id: 'stream-a:2', event: 'token', data: { text: '你好' } }
  )
})


test('blocks empty or concurrent chat submissions', () => {
  assert.equal(canSubmitChatMessage('目标地点是示例园区', false), true)
  assert.equal(canSubmitChatMessage('目标地点是示例园区', true), false)
  assert.equal(canSubmitChatMessage('   ', false), false)
  assert.equal(canSubmitChatMessage(null, false), false)
})


test('consumes fragmented token events and sends confirmed target context', async () => {
  const originalFetch = globalThis.fetch
  const encoder = new TextEncoder()
  const received = []
  let requestBody = null

  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/v1/sessions/session-1/chat/stream')
    requestBody = JSON.parse(options.body)
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('id: a:1\nevent: status\ndata: {"status":"running"}\n\nid: a'))
        controller.enqueue(encoder.encode(':2\nevent: token\ndata: {"text":"第一段"}\n\nid: a:3\nevent: token\ndata: {"text":"第二段"}\n\n'))
        controller.enqueue(encoder.encode('id: a:4\nevent: assistant\ndata: {"text":"第一段第二段"}\n\nid: a:5\nevent: done\ndata: {"status":"completed"}\n\n'))
        controller.close()
      }
    })
    return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
  }

  try {
    await streamChat({
      sessionId: 'session-1',
      message: '预算 2500 元',
      clientRequestId: 'req-1',
      searchContext: {
        confirmed_target: { candidate_ref: 'pc_1', name: '示例园区东区' }
      },
      onEvent: event => received.push(event)
    })
  } finally {
    globalThis.fetch = originalFetch
  }

  assert.deepEqual(received.map(item => item.event), ['status', 'token', 'token', 'assistant', 'done'])
  assert.deepEqual(received.map(item => item.id), ['a:1', 'a:2', 'a:3', 'a:4', 'a:5'])
  assert.equal(requestBody.message, '预算 2500 元')
  assert.equal(requestBody.search_context.confirmed_target.candidate_ref, 'pc_1')
})


test('rejects a response that closes without a done event', async () => {
  const originalFetch = globalThis.fetch
  const encoder = new TextEncoder()
  globalThis.fetch = async () => new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('id: b:1\nevent: token\ndata: {"text":"未完成"}\n\n'))
        controller.close()
      }
    }),
    { status: 200, headers: { 'Content-Type': 'text/event-stream' } }
  )

  try {
    await assert.rejects(
      streamChat({ sessionId: 'session-2', message: '测试', onEvent: () => {} }),
      error => error?.code === 'stream_incomplete'
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})


test('sends explicit session and privacy cleanup requests', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' })
    return Response.json({ ok: true })
  }

  try {
    await deleteSession('session/1')
    await clearPlatformSessions()
    await clearArtifacts()
  } finally {
    globalThis.fetch = originalFetch
  }

  assert.deepEqual(calls, [
    { url: '/api/v1/sessions/session%2F1', method: 'DELETE' },
    { url: '/api/v1/privacy/platform-sessions/clear', method: 'POST' },
    { url: '/api/v1/privacy/artifacts/clear', method: 'POST' }
  ])
})
