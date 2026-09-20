const TEXT_FIELDS = [
  'candidate_ref',
  'name',
  'formatted_address',
  'city',
  'district',
  'adcode',
  'confidence',
  'provider',
  'data_quality'
]

const LOCATION_STATUS_COPY = Object.freeze({
  timeout: {
    title: '地点服务响应超时',
    message: '本轮没有返回可选择的地点；可以稍后重试，或补充更完整的城市和地址。'
  },
  not_configured: {
    title: '地点服务未配置',
    message: '后端没有可用的高德地点服务配置，因此暂时无法生成可选择的坐标。'
  },
  quota_exceeded: {
    title: '地点服务暂时达到限额',
    message: '本轮没有返回可选择的地点；请稍后重试。'
  },
  provider_error: {
    title: '地点服务暂时不可用',
    message: '本轮没有返回可选择的地点；可以稍后重试，或直接在聊天中补充完整地址。'
  },
  no_match: {
    title: '没有匹配到可定位地点',
    message: '请补充城市、区县或更完整的地点名称，再让 Agent 继续解析。'
  },
  invalid_input: {
    title: '地点名称不完整',
    message: '请提供公司、学校、地标或具体地址名称。'
  },
  needs_city_confirmation: {
    title: '需要确认地点所在城市',
    message: '这些候选可能影响房源和通勤范围，请在聊天中确认城市或选择候选。'
  },
  city_conflict: {
    title: '地点与城市条件不一致',
    message: '请在聊天中确认正确的城市和地点后再继续搜索。'
  },
  needs_confirmation: {
    title: '请选择目标地点',
    message: '点击候选只会填入聊天草稿；发送下一条消息后，Agent 才会按该地点继续。'
  },
  candidates_ready: {
    title: '请选择目标地点',
    message: '点击候选只会填入聊天草稿；发送下一条消息后，Agent 才会按该地点继续。'
  },
  resolved: {
    title: '目标地点已定位',
    message: 'Agent 已采用这个地点；后续房源结果会以它作为目标。'
  }
})

function asText(value) {
  return typeof value === 'string' ? value.trim() : ''
}

export function locationResolutionCopy(status) {
  const normalized = asText(status).toLowerCase()
  return LOCATION_STATUS_COPY[normalized] || {
    title: '地点尚未解析',
    message: '等待 Agent 返回可选择的地点。'
  }
}

function asCoordinate(value, min, max) {
  if (value === null || value === undefined || typeof value === 'boolean') return null
  if (typeof value === 'string' && !value.trim()) return null
  const number = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(number) && number >= min && number <= max ? number : null
}

function safeReference(value, index) {
  const reference = asText(value)
  return /^[a-zA-Z0-9_-]{1,128}$/.test(reference) ? reference : `place-${index + 1}`
}

export function normalizeLocationCandidates(payload) {
  const source = Array.isArray(payload?.candidates) ? payload.candidates : []
  const seen = new Set()

  return source.flatMap((rawCandidate, index) => {
    if (!rawCandidate || typeof rawCandidate !== 'object') return []

    const candidate = {}
    for (const field of TEXT_FIELDS) candidate[field] = asText(rawCandidate[field])
    candidate.lng = asCoordinate(rawCandidate.lng, -180, 180)
    candidate.lat = asCoordinate(rawCandidate.lat, -90, 90)

    if (!candidate.name) return []
    candidate.candidate_ref = safeReference(candidate.candidate_ref, index)
    if (seen.has(candidate.candidate_ref)) return []
    seen.add(candidate.candidate_ref)
    return [candidate]
  })
}

export function candidateCoordinates(candidate) {
  if (!candidate || typeof candidate !== 'object') return null
  const lng = asCoordinate(candidate.lng, -180, 180)
  const lat = asCoordinate(candidate.lat, -90, 90)
  return lng === null || lat === null ? null : [lng, lat]
}

export function candidateArea(candidate) {
  return [asText(candidate?.city), asText(candidate?.district)].filter(Boolean).join(' · ')
}

export function placeDraftPrefix(candidate) {
  const name = asText(candidate?.name)
  return name ? `我想在${name}附近找房，` : ''
}

export function mergePlaceIntoDraft(draft, candidate, previousCandidate = null) {
  const currentDraft = typeof draft === 'string' ? draft : ''
  const nextPrefix = placeDraftPrefix(candidate)
  if (!nextPrefix) return currentDraft

  const previousPrefix = placeDraftPrefix(previousCandidate)
  if (previousPrefix && currentDraft.startsWith(previousPrefix)) {
    return `${nextPrefix}${currentDraft.slice(previousPrefix.length)}`
  }
  if (currentDraft.startsWith(nextPrefix)) return currentDraft
  return `${nextPrefix}${currentDraft}`
}

export function draftKeepsPlaceSelection(draft, candidate) {
  const prefix = placeDraftPrefix(candidate)
  const currentDraft = typeof draft === 'string' ? draft.trimStart() : ''
  return Boolean(prefix && currentDraft.startsWith(prefix))
}

export function isResolvedLocationPayload(payload) {
  return payload?.status === 'resolved' && payload?.requires_user_confirmation === false
}

export function buildSearchContext(candidate) {
  const normalized = normalizeLocationCandidates({ candidates: [candidate] })[0]
  if (!normalized) return null

  const confirmedTarget = {
    candidate_ref: normalized.candidate_ref,
    name: normalized.name,
    formatted_address: normalized.formatted_address,
    city: normalized.city,
    district: normalized.district,
    adcode: normalized.adcode
  }
  const coordinates = candidateCoordinates(normalized)
  if (coordinates) {
    confirmedTarget.lng = coordinates[0]
    confirmedTarget.lat = coordinates[1]
  }
  return { confirmed_target: confirmedTarget }
}
