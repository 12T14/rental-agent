// 只有服务端状态才能决定恢复或重试；点击地点永远不会触发这两种操作。
export function sessionAction({ pending, recoveryRequestId } = {}) {
  if (recoveryRequestId) return 'recover'
  return pending?.interrupt_id ? 'resume' : 'chat'
}

// 防止切换会话后，延迟到达的请求或流回调修改新会话。
export function createViewGuard() {
  let version = 0
  return {
    advance() { version += 1; return version },
    capture() { return version },
    isCurrent(value) { return value === version }
  }
}
