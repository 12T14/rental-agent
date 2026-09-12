// FastAPI SSE 聊天桥接接口的轻量浏览器客户端。

function parseEventBlock(block) {
  const lines = block.split(/\r?\n/)
  let id = ''
  let event = 'message'
  const dataLines = []

  for (const line of lines) {
    if (line.startsWith('id:')) {
      id = line.slice('id:'.length).trim()
    } else if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim() || 'message'
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice('data:'.length).trimStart())
    }
  }

  if (!dataLines.length) return null
  const rawData = dataLines.join('\n')
  try {
    return { id, event, data: JSON.parse(rawData) }
  } catch {
    return { id, event, data: { raw: rawData } }
  }
}

export function canSubmitChatMessage(message, busy = false) {
  return !busy && typeof message === 'string' && Boolean(message.trim())
}

export async function streamChat({ sessionId, message, clientRequestId, searchContext, onEvent, signal }) {
  const requestBody = { message, client_request_id: clientRequestId }
  if (searchContext?.confirmed_target) requestBody.search_context = searchContext
  return postStream(sessionId, 'chat', requestBody, onEvent, signal)
}

export async function streamResume({ sessionId, message, clientRequestId, interruptId, searchContext, onEvent, signal }) {
  const requestBody = { message, client_request_id: clientRequestId, interrupt_id: interruptId }
  if (searchContext?.confirmed_target) requestBody.search_context = searchContext
  return postStream(sessionId, 'resume', requestBody, onEvent, signal)
}

export async function streamRecover({ sessionId, clientRequestId, onEvent, signal }) {
  return postStream(sessionId, 'recover', { client_request_id: clientRequestId }, onEvent, signal)
}

async function responseError(response) {
  let payload = null
  try { payload = await response.json() } catch { /* 解析失败时只报告安全的 HTTP 状态。 */ }
  const error = new Error(payload?.error?.message || `请求失败（${response.status}）`)
  error.code = payload?.error?.code || 'http_error'
  error.status = response.status
  return error
}

async function sessionJson(path, options = {}) {
  const response = await fetch(`/api/v1/sessions${path}`, options)
  if (!response.ok) throw await responseError(response)
  return response.json()
}

export function listSessions({ signal, offset = 0 } = {}) {
  return sessionJson(`?limit=100&offset=${offset}`, { signal })
}

export function getSession(sessionId, { signal } = {}) {
  return sessionJson(`/${encodeURIComponent(sessionId)}`, { signal })
}

export function renameSession(sessionId, title) {
  return sessionJson(`/${encodeURIComponent(sessionId)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title })
  })
}

async function postStream(sessionId, action, requestBody, onEvent, signal) {

  const response = await fetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}/${action}/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(requestBody),
    signal
  })

  if (!response.ok) {
    throw await responseError(response)
  }

  if (!response.body) throw new Error('浏览器不支持流式响应')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let sawDone = false

  const dispatch = (block) => {
    const parsed = parseEventBlock(block)
    if (!parsed) return
    if (parsed.event === 'done') sawDone = true
    onEvent?.(parsed)
  }

  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
      const blocks = buffer.split(/\r?\n\r?\n/)
      buffer = blocks.pop() || ''
      blocks.forEach(dispatch)
      if (done) break
    }

    if (buffer.trim()) dispatch(buffer)
    if (!sawDone && !signal?.aborted) {
      const error = new Error('流式响应意外中断，请重试。')
      error.code = 'stream_incomplete'
      throw error
    }
  } finally {
    reader.releaseLock()
  }
}

export { parseEventBlock }
