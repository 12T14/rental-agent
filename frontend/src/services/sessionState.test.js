import assert from 'node:assert/strict'
import test from 'node:test'
import { createViewGuard, sessionAction } from './sessionState.js'
import { streamResume, streamRecover, getSession, listSessions, renameSession } from './chatApi.js'

test('location selection cannot force resume; only saved session state routes it', () => {
  assert.equal(sessionAction({ confirmed_target: { name: '学校' } }), 'chat')
  assert.equal(sessionAction({ pending: { interrupt_id: 'pause-1' } }), 'resume')
  assert.equal(sessionAction({ pending: { interrupt_id: 'old' }, recoveryRequestId: 'r1' }), 'recover')
})

test('late fetch and stream callbacks cannot own a newly selected session', () => {
  const guard = createViewGuard()
  const old = guard.capture()
  assert.equal(guard.isCurrent(old), true)
  guard.advance()
  const current = guard.capture()
  assert.equal(guard.isCurrent(old), false)
  assert.equal(guard.isCurrent(current), true)
})

test('resume and recover have distinct bounded payloads and preserve interrupted done', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  const events = []
  globalThis.fetch = async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) })
    return new Response(
      'event: interrupt\ndata: {"interrupt_id":"new-pause"}\n\nevent: done\ndata: {"status":"interrupted"}\n\n',
      { headers: { 'Content-Type': 'text/event-stream' } }
    )
  }
  try {
    await streamResume({
      sessionId: 's1', message: '不限', clientRequestId: 'r2', interruptId: 'pause1',
      onEvent: event => events.push(event)
    })
    await streamRecover({ sessionId: 's1', clientRequestId: 'r2', message: 'must-not-resend' })
  } finally {
    globalThis.fetch = originalFetch
  }
  assert.deepEqual(requests, [
    { url: '/api/v1/sessions/s1/resume/stream', body: { message: '不限', client_request_id: 'r2', interrupt_id: 'pause1' } },
    { url: '/api/v1/sessions/s1/recover/stream', body: { client_request_id: 'r2' } }
  ])
  assert.equal(events.at(-1).data.status, 'interrupted')
})

test('history and titles use the real session API; storage failures are not demo history', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options })
    return Response.json(url.endsWith('/missing')
      ? { error: { code: 'session_not_found', message: '会话不存在' } }
      : { sessions: [], storage: { mode: 'memory', persistent: false } },
    { status: url.endsWith('/missing') ? 404 : 200 })
  }
  try {
    assert.deepEqual((await listSessions()).sessions, [])
    await getSession('s1')
    await renameSession('s1', '我的会话')
    await assert.rejects(getSession('missing'), error => error.code === 'session_not_found')
  } finally {
    globalThis.fetch = originalFetch
  }
  assert.equal(requests[0].url, '/api/v1/sessions?limit=100&offset=0')
  assert.equal(requests[1].url, '/api/v1/sessions/s1')
  assert.equal(requests[2].options.method, 'PATCH')
  assert.deepEqual(JSON.parse(requests[2].options.body), { title: '我的会话' })
})
