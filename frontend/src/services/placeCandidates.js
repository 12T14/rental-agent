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

function asText(value) {
  return typeof value === 'string' ? value.trim() : ''
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
