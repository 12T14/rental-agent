const PLATFORM_DEFINITIONS = [
  { key: 'fifty-eight', name: '58同城', aliases: ['58', '58同城', 'fifty-eight'] },
  { key: 'anjuke', name: '安居客', aliases: ['anjuke', '安居客'] },
  { key: 'fang', name: '房天下', aliases: ['fang', '房天下'] }
]

const PLATFORM_BY_ALIAS = new Map(
  PLATFORM_DEFINITIONS.flatMap(item => item.aliases.map(alias => [alias.toLowerCase(), item]))
)
const DETAIL_FACT_KEYS = [
  'monthly_rent_cny', 'payment_rule', 'agency_fee', 'utility_rule',
  'minimum_lease', 'available_date'
]
const PLATFORM_STATUSES = new Set(['idle', 'loading', 'ok', 'partial', 'blocked', 'failed'])
const LISTING_STATUSES = new Set(['pending', 'not_requested', 'ok', 'blocked', 'skipped_after_block', 'error', 'fixture_missing', 'source_list', 'rejected', 'rejected_redirect', 'redirect_not_followed'])
const FILTER_STATUSES = new Set(['passed', 'unknown', 'excluded'])
const REGION_SCOPES = new Set(['administrative_region', 'city'])
const REGION_EVIDENCE = new Set(['card_region_link', 'official_filter_page', 'city_page'])
const COMMUTE_STATUSES = new Set(['pending', 'target_pending', 'ok', 'not_configured', 'provider_error', 'geocode_failed', 'route_failed', 'timeout', 'quota_exceeded', 'missing_address', 'city_conflict'])
const LOCATION_FAILURE_STATUSES = new Set(['not_configured', 'provider_error', 'geocode_failed', 'city_conflict', 'timeout', 'quota_exceeded', 'missing_address'])
const MISSING_LOCATION_TEXT = new Set([
  '未说明',
  '平台列表未提供',
  '平台列表未提供小区',
  '平台列表未提供具体地址'
])

function asText(value, fallback = '', limit = 500) {
  return typeof value === 'string' ? value.trim().slice(0, limit) : fallback
}

function asEnum(value, allowed, fallback = '') {
  const text = asText(value, '', 40).toLowerCase()
  return allowed.has(text) ? text : allowed.has(fallback) ? fallback : ''
}

function meaningfulText(value, fallback = '') {
  const text = asText(value, '', 500)
  return text && !MISSING_LOCATION_TEXT.has(text) ? text : fallback
}

function asNumber(value, minimum = -Infinity, maximum = Infinity) {
  if (value === null || value === undefined || typeof value === 'boolean') return null
  const number = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(number) && number >= minimum && number <= maximum ? number : null
}

function coordinate(value, minimum, maximum) {
  return asNumber(value, minimum, maximum)
}

function asUrl(value) {
  const raw = asText(value, '', 1200)
  if (!raw) return null
  try {
    const url = new URL(raw)
    const host = url.hostname.toLowerCase().replace(/\.$/, '')
    const allowed = ['58.com', 'anjuke.com', 'fang.com'].some(suffix => host === suffix || host.endsWith(`.${suffix}`))
    if (!['http:', 'https:'].includes(url.protocol) || !allowed || url.username || url.password) return null
    url.search = ''
    url.hash = ''
    return url.toString()
  } catch {
    return null
  }
}

function firstSafeUrl(...values) {
  for (const value of values) {
    const url = asUrl(value)
    if (url) return url
  }
  return null
}

function platformDefinition(value) {
  const text = asText(value, '', 100).toLowerCase()
  if (PLATFORM_BY_ALIAS.has(text)) return PLATFORM_BY_ALIAS.get(text)
  return PLATFORM_DEFINITIONS.find(item => item.aliases.some(alias => text.includes(alias.toLowerCase()))) || null
}

function probableListUrl(value) {
  const raw = asUrl(value)
  if (!raw) return false
  try {
    const url = new URL(raw)
    const host = url.hostname.toLowerCase()
    let path = url.pathname.replace(/\/+$/, '')
    if (host.endsWith('.zf.58.com')) return true
    if (host.endsWith('zu.fang.com')) {
      if (host === 'zu.fang.com') path = path.replace(/^\/[a-z]{2,6}(?=\/(?:house|hezu|chuzu))/, '')
      if (path.startsWith('/house-') || path === '/hezu') return true
      if (path === '' || path === '/house' || (path.startsWith('/house/') && path.split('/').length - 1 <= 2)) return true
      if (/\/a\d+$/.test(path)) return true
    }
    if (host.endsWith('zu.anjuke.com')) {
      if (path === '' || path === '/fangyuan') return true
      if (path.startsWith('/fangyuan/') && url.pathname.endsWith('/')) return true
    }
    return false
  } catch {
    return false
  }
}

export function defaultPlatformStatuses(status = 'idle', label = '等待搜索') {
  return PLATFORM_DEFINITIONS.map(item => ({
    key: item.key,
    name: item.name,
    status,
    label,
    count: 0
  }))
}

export function normalizePlatformStatuses(payload, fallback = defaultPlatformStatuses()) {
  const source = Array.isArray(payload?.platforms) ? payload.platforms : []
  if (!source.length) return fallback.map(item => ({ ...item }))
  const byKey = new Map()
  for (const raw of source) {
    if (!raw || typeof raw !== 'object') continue
    const definition = platformDefinition(raw.key) || platformDefinition(raw.name)
    if (!definition) continue
    const status = asText(raw.status, 'idle', 30).toLowerCase()
    const count = asNumber(raw.count, 0, 100000)
    byKey.set(definition.key, {
      key: definition.key,
      name: definition.name,
      status: PLATFORM_STATUSES.has(status) ? status : 'idle',
      label: asText(raw.label, status === 'ok' ? '已获取' : '等待搜索', 80),
      count: count === null ? 0 : Math.floor(count)
    })
  }
  return PLATFORM_DEFINITIONS.map(definition => byKey.get(definition.key) || {
    key: definition.key,
    name: definition.name,
    status: 'idle',
    label: '等待搜索',
    count: 0
  })
}

function normalizeFacts(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  const facts = {}
  for (const key of DETAIL_FACT_KEYS) {
    if (key === 'monthly_rent_cny') {
      const number = asNumber(value[key], 0, 10000000)
      if (number !== null) facts[key] = number
    } else {
      const text = asText(value[key], '', 300)
      if (text) facts[key] = text
    }
  }
  return facts
}

function normalizeDetailLocation(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  const location = {}
  const address = meaningfulText(value.address, '')
  const community = meaningfulText(value.community, '')
  const city = meaningfulText(value.city, '')
  const district = meaningfulText(value.district, '')
  const region = meaningfulText(value.region, '')
  const confidence = asText(value.confidence, '', 80)
  if (address) location.address = address
  if (community) location.community = community
  if (city) location.city = city
  if (district) location.district = district
  if (region) location.region = region
  if (confidence) location.confidence = confidence
  return location
}

function normalizeListingType(value, fallback = '') {
  const text = asText(value, '', 40)
  const hasWhole = text.includes('整租') || text.includes('整套')
  const hasShared = text.includes('合租') || text.includes('拼租')
  if (hasWhole !== hasShared) return hasWhole ? '整租' : '合租'
  return fallback
}

function listingPlatform(raw) {
  return platformDefinition(raw?.platformKey ?? raw?.platform_key)
    || platformDefinition(raw?.platform)
    || platformDefinition(raw?.detailUrl ?? raw?.detail_url)
    || platformDefinition(raw?.sourceUrl ?? raw?.source_url ?? raw?.source_page ?? raw?.listing_url)
}

export function normalizeListing(raw, index = 0, previous = null) {
  if (!raw || typeof raw !== 'object') return null
  const definition = listingPlatform(raw) || listingPlatform(previous || {})
  const candidateDetailUrl = firstSafeUrl(raw.detailUrl, raw.detail_url) || firstSafeUrl(previous?.detailUrl, previous?.detail_url)
  const candidateSourceUrl = firstSafeUrl(
    raw.sourceUrl, raw.source_url, raw.source_page, raw.url, raw.listing_url
  ) || firstSafeUrl(previous?.sourceUrl, previous?.source_url)
  const requestedUrlType = asText(raw.urlType ?? raw.url_type, candidateDetailUrl ? 'detail' : 'source_list', 30).toLowerCase()
  const detailUrl = requestedUrlType === 'detail' && candidateDetailUrl && !probableListUrl(candidateDetailUrl)
    ? candidateDetailUrl
    : null
  const urlType = detailUrl
    ? 'detail'
    : 'source_list'
  const id = asText(raw.id ?? raw.listing_id, '', 128) || asText(previous?.id, '', 128) || `listing-${index + 1}`
  const rent = asNumber(raw.rent ?? raw.monthly_rent_cny ?? raw.price, 0, 10000000)
    ?? asNumber(previous?.rent, 0, 10000000)
  const area = asNumber(raw.area ?? raw.area_sqm, 0, 100000)
    ?? asNumber(previous?.area, 0, 100000)
  const rawLocationStatus = asText(raw.locationStatus ?? raw.location_status, '', 30).toLowerCase()
  const rawGeocodeStatus = asText(raw.geocodeStatus ?? raw.geocode_status, '', 40).toLowerCase()
  const rawCommuteStatus = asText(raw.commuteStatus ?? raw.commute_status, '', 40).toLowerCase()
  const locationFailureStatus = LOCATION_FAILURE_STATUSES.has(rawGeocodeStatus)
    || rawCommuteStatus === 'city_conflict'
  const canReusePreviousCoordinates = !locationFailureStatus
  const locationStatus = rawLocationStatus === 'verified' && !locationFailureStatus
    || (!rawLocationStatus && previous?.locationStatus === 'verified' && canReusePreviousCoordinates)
    ? 'verified'
    : 'unverified'
  const rawLng = coordinate(raw.lng ?? raw.longitude, -180, 180)
  const rawLat = coordinate(raw.lat ?? raw.latitude, -90, 90)
  const lng = locationFailureStatus
    ? null
    : rawLng ?? (canReusePreviousCoordinates ? coordinate(previous?.lng, -180, 180) : null)
  const lat = locationFailureStatus
    ? null
    : rawLat ?? (canReusePreviousCoordinates ? coordinate(previous?.lat, -90, 90) : null)
  const distanceMeters = locationStatus === 'verified'
    ? asNumber(raw.distanceMeters ?? raw.distance_meters, 0, 100000000)
      ?? asNumber(previous?.distanceMeters, 0, 100000000)
    : null
  const distance = locationStatus === 'verified'
    ? asNumber(raw.distance ?? raw.distance_km, 0, 1000)
      ?? asNumber(previous?.distance, 0, 1000)
      ?? (distanceMeters === null ? null : Number((distanceMeters / 1000).toFixed(2)))
    : null
  const commuteDurationSeconds = locationStatus === 'verified'
    ? asNumber(raw.commuteDurationSeconds ?? raw.commute_duration_seconds, 0, 86400)
      ?? asNumber(previous?.commuteDurationSeconds, 0, 86400)
    : null
  const commuteValue = locationStatus === 'verified'
    ? asNumber(raw.commuteValue ?? raw.commute_value, 0, 1440)
      ?? asNumber(previous?.commuteValue, 0, 1440)
      ?? (commuteDurationSeconds === null ? null : Number((commuteDurationSeconds / 60).toFixed(1)))
    : null
  const rawFilterStatus = asText(raw.filterStatus ?? raw.filter_status, '', 30).toLowerCase()
  const filterStatus = FILTER_STATUSES.has(rawFilterStatus)
    ? rawFilterStatus
    : (FILTER_STATUSES.has(previous?.filterStatus) ? previous.filterStatus : 'unknown')
  const commuteStatus = COMMUTE_STATUSES.has(rawCommuteStatus)
    ? rawCommuteStatus
    : (locationStatus === 'verified'
      ? 'pending'
      : ((rawGeocodeStatus === 'ok' || rawGeocodeStatus === 'provided') && lng !== null && lat !== null)
        ? 'target_pending'
        : LOCATION_FAILURE_STATUSES.has(rawGeocodeStatus) ? rawGeocodeStatus : 'missing_address')
  const rawHardFilterPass = raw.hardFilterPass ?? raw.hard_filter_pass
  const hardFilterPass = typeof rawHardFilterPass === 'boolean'
    ? rawHardFilterPass
    : (typeof previous?.hardFilterPass === 'boolean' ? previous.hardFilterPass : null)
  const rawFilterReasons = raw.filterReasons ?? raw.filter_reasons
  const filterReasons = Array.isArray(rawFilterReasons)
    ? rawFilterReasons.slice(0, 8).map(value => asText(value, '', 160)).filter(Boolean)
    : (Array.isArray(previous?.filterReasons) ? previous.filterReasons.slice(0, 8) : [])
  const rawRankingReasons = raw.rankingReasons ?? raw.ranking_reasons
  const rankingReasons = Array.isArray(rawRankingReasons)
    ? rawRankingReasons.slice(0, 8).map(value => asText(value, '', 160)).filter(Boolean)
    : (Array.isArray(previous?.rankingReasons) ? previous.rankingReasons.slice(0, 8) : [])
  const rawDetailStatus = asText(raw.detailStatus ?? raw.detail_status, '', 40).toLowerCase()
  const previousDetailStatus = asText(previous?.detailStatus, '', 40).toLowerCase()
  const detailStatus = urlType === 'source_list'
    ? 'not_requested'
    : (LISTING_STATUSES.has(rawDetailStatus)
      ? rawDetailStatus
      : (LISTING_STATUSES.has(previousDetailStatus) ? previousDetailStatus : 'pending'))
  const tags = typeof raw.tags === 'string'
    ? [...new Set(raw.tags.split(/[,，、|｜;；]+/).map(value => asText(value, '', 80)).filter(Boolean))].slice(0, 8)
    : Array.isArray(raw.tags)
      ? [...new Set(raw.tags.slice(0, 8).map(value => asText(value, '', 80)).filter(Boolean))]
      : (Array.isArray(previous?.tags) ? previous.tags.slice(0, 8) : [])
  const rawDetailFacts = normalizeFacts(raw.detailFacts ?? raw.detail_facts)
  const detailFacts = { ...normalizeFacts(previous?.detailFacts), ...rawDetailFacts }
  const detailLocation = {
    ...normalizeDetailLocation(previous?.detailLocation ?? previous?.detail_location),
    ...normalizeDetailLocation(raw.detailLocation ?? raw.detail_location)
  }
  const city = meaningfulText(raw.city ?? raw.city_name ?? raw.cityName, '')
    || meaningfulText(detailLocation.city, '')
    || meaningfulText(previous?.city, '')
  const district = meaningfulText(raw.district ?? raw.region ?? raw.area, '')
    || meaningfulText(detailLocation.district ?? detailLocation.region, '')
    || meaningfulText(previous?.district, '')
  const title = asText(raw.title, '', 300) || asText(previous?.title, '未命名房源', 300)
  const community = meaningfulText(raw.community, '')
    || meaningfulText(previous?.community, '')
    || asText(raw.community, '未说明', 500)
  const address = meaningfulText(raw.address, '')
    || meaningfulText(previous?.address, '')
    || asText(raw.address, '未说明', 500)
  const room = asText(raw.room, '', 100) || asText(previous?.room, '未说明', 100)
  const listingType = normalizeListingType(raw.listingType ?? raw.listing_type, '')
    || normalizeListingType(previous?.listingType ?? previous?.listing_type, '')
    || '未说明'
  const rawCommuteText = asText(raw.commute ?? raw.commute_text, '', 120)
  const commuteText = commuteStatus === 'target_pending'
    ? '目标地点待确认'
    : LOCATION_FAILURE_STATUSES.has(commuteStatus)
      ? '位置待核验'
      : rawCommuteText
        || (canReusePreviousCoordinates ? asText(previous?.commute, '', 120) : '')
        || (locationStatus === 'verified' ? '通勤待计算' : '位置待核验')
  return {
    id,
    platformKey: definition?.key || asText(raw.platformKey ?? raw.platform_key, 'other', 50),
    platform: definition?.name || asText(raw.platform, '其他平台', 100),
    title,
    community,
    address,
    rent,
    room,
    listingType,
    area,
    tags,
    metro: asText(raw.metro, '', 500),
    city,
    district,
    regionScope: asEnum(raw.regionScope ?? raw.region_scope, REGION_SCOPES, previous?.regionScope),
    regionScopeName: asText(raw.regionScopeName ?? raw.region_scope_name, '', 100)
      || asText(previous?.regionScopeName, '', 100),
    regionEvidence: asEnum(raw.regionEvidence ?? raw.region_evidence, REGION_EVIDENCE, previous?.regionEvidence),
    detailUrl: urlType === 'detail' ? detailUrl : null,
    sourceUrl: candidateSourceUrl || (urlType === 'source_list' ? candidateDetailUrl : null),
    urlType,
    updated: asText(raw.updated, '刚刚', 80),
    locationStatus,
    lng,
    lat,
    geocodedAddress: asText(raw.geocodedAddress ?? raw.geocoded_address, '', 500)
      || (canReusePreviousCoordinates ? asText(previous?.geocodedAddress, '', 500) : ''),
    locationConfidence: asText(raw.locationConfidence ?? raw.location_confidence, '', 80)
      || (canReusePreviousCoordinates ? asText(previous?.locationConfidence, '', 80) : ''),
    geocodeStatus: rawGeocodeStatus || (canReusePreviousCoordinates ? asText(previous?.geocodeStatus, '', 40) : ''),
    distance,
    distanceMeters,
    commute: commuteText,
    commuteValue,
    commuteDurationSeconds,
    commuteDistanceMeters: locationStatus === 'verified'
      ? asNumber(raw.commuteDistanceMeters ?? raw.commute_distance_meters, 0, 100000000)
      : null,
    commuteMode: asText(raw.commuteMode ?? raw.commute_mode, '', 20)
      || asText(previous?.commuteMode, 'transit', 20),
    commuteStatus,
    filterStatus,
    filterReasons,
    hardFilterPass,
    rankingScore: asNumber(raw.rankingScore ?? raw.ranking_score, 0, 200)
      ?? asNumber(previous?.rankingScore, 0, 200),
    rankingReasons,
    rankingExplanation: asText(raw.rankingExplanation ?? raw.ranking_explanation, '', 500)
      || asText(previous?.rankingExplanation, '', 500),
    dataQuality: asText(raw.dataQuality ?? raw.data_quality, '', 180)
      || asText(previous?.dataQuality, 'public_listing_candidate', 180),
    locationMatch: asText(raw.locationMatch ?? raw.location_match, '', 40)
      || asText(previous?.locationMatch, 'unverified', 40),
    locationMatchScore: asNumber(raw.locationMatchScore ?? raw.location_match_score, 0, 1)
      ?? asNumber(previous?.locationMatchScore, 0, 1),
    locationMatchTerms: Array.isArray(raw.locationMatchTerms ?? raw.location_match_terms)
      ? (raw.locationMatchTerms ?? raw.location_match_terms).slice(0, 8).map(value => asText(value, '', 80)).filter(Boolean)
      : (Array.isArray(previous?.locationMatchTerms) ? previous.locationMatchTerms.slice(0, 8) : []),
    detailFacts,
    detailLocation,
    detailStatus,
    offline: raw.offline === true || previous?.offline === true,
    saved: previous?.saved === true
  }
}

export function listingTitleLabel(listing) {
  return meaningfulText(listing?.title, '未命名房源')
}

export function listingPlaceLabel(listing) {
  return meaningfulText(listing?.community)
    || meaningfulText(listing?.address)
    || '地址未提供'
}

export function listingAddressLabel(listing) {
  return meaningfulText(listing?.address)
    || meaningfulText(listing?.community)
    || '平台列表未提供具体地址'
}

export function listingHasAddress(listing) {
  return Boolean(meaningfulText(listing?.address))
}

export function normalizeListings(payload, previous = []) {
  const source = Array.isArray(payload?.listings) ? payload.listings : []
  const previousById = new Map((Array.isArray(previous) ? previous : []).map(item => [item.id, item]))
  const seen = new Set()
  return source.flatMap((raw, index) => {
    const item = normalizeListing(raw, index, previousById.get(raw?.id ?? raw?.listing_id))
    if (!item || seen.has(item.id)) return []
    seen.add(item.id)
    return [item]
  })
}

export function normalizeSearchSummary(payload) {
  if (!payload || typeof payload !== 'object') return null
  const status = asText(payload.status, '', 40).toLowerCase()
  const detailStatus = asText(payload.detail_status ?? payload.detailStatus, '', 40).toLowerCase()
  return {
    status: status || null,
    detail_status: detailStatus || null,
    enrichment_status: asText(payload.enrichment_status ?? payload.enrichmentStatus, '', 40).toLowerCase() || null,
    map_status: asText(payload.map_status ?? payload.mapStatus, '', 40).toLowerCase() || null,
    criteria: payload.criteria && typeof payload.criteria === 'object' && !Array.isArray(payload.criteria)
      ? { ...payload.criteria }
      : null,
    offline: payload.offline === true,
    retrieved_at: asText(payload.retrieved_at, '', 80) || null
  }
}

export function mergeSearchSummary(previous, payload) {
  const next = normalizeSearchSummary(payload)
  if (!next) return previous || null
  const hasOffline = Object.prototype.hasOwnProperty.call(payload, 'offline')
  return {
    ...(previous || {}),
    ...(next.status ? { status: next.status } : {}),
    ...(next.detail_status ? { detail_status: next.detail_status } : {}),
    ...(next.enrichment_status ? { enrichment_status: next.enrichment_status } : {}),
    ...(next.map_status ? { map_status: next.map_status } : {}),
    ...(next.criteria ? { criteria: next.criteria } : {}),
    ...(next.retrieved_at ? { retrieved_at: next.retrieved_at } : {}),
    ...(hasOffline ? { offline: next.offline } : {})
  }
}

export function applyDetailResults(listings, payload) {
  const details = Array.isArray(payload?.details) ? payload.details : []
  const byUrl = new Map(details.map(raw => [firstSafeUrl(raw?.url, raw?.detail_url), raw]).filter(([url]) => url))
  return (Array.isArray(listings) ? listings : []).map(listing => {
    const detail = byUrl.get(listing.detailUrl) || byUrl.get(listing.sourceUrl)
    if (!detail) return listing
    const facts = normalizeFacts(detail.facts ?? detail.detail_facts)
    const status = asText(detail.status ?? detail.detail_status, '', 40).toLowerCase()
    const location = detail.location && typeof detail.location === 'object' ? detail.location : {}
    const detailLocation = {
      ...normalizeDetailLocation(listing.detailLocation),
      ...normalizeDetailLocation(location)
    }
    return {
      ...listing,
      title: asText(detail.title, listing.title, 300) || listing.title,
      rent: facts.monthly_rent_cny ?? listing.rent,
      community: meaningfulText(location.community, '') || listing.community,
      address: meaningfulText(location.address, '') || listing.address,
      detailLocation,
      detailFacts: { ...normalizeFacts(listing.detailFacts), ...facts },
      detailStatus: LISTING_STATUSES.has(status) ? status : 'error'
    }
  })
}

export function applyListingEnrichment(listings, payload) {
  if (!payload || typeof payload !== 'object' || !Array.isArray(payload.listings)) return listings
  return normalizeListings({ listings: payload.listings }, listings)
}

export function visibleListings(listings) {
  return (Array.isArray(listings) ? listings : []).filter(listing => listing?.filterStatus !== 'excluded')
}

export function formatListingDistance(listing) {
  if (listing?.locationStatus !== 'verified') {
    const hasCoordinates = coordinate(listing?.lng, -180, 180) !== null
      && coordinate(listing?.lat, -90, 90) !== null
    if ((listing?.geocodeStatus === 'ok' || listing?.geocodeStatus === 'provided') && hasCoordinates) {
      return '目标地点待确认'
    }
    return '位置待核验'
  }
  if (listing?.distance === null || listing?.distance === undefined) return '距离待计算'
  return `${listing.distance < 1 ? Math.round(listing.distance * 1000) + ' m' : listing.distance + ' km'}`
}

export function formatListingCommute(listing) {
  if (!listing) return '通勤待计算'
  if (listing.commuteStatus === 'target_pending') return '目标地点待确认'
  if (listing.commuteStatus === 'not_configured') return '地图服务未配置'
  if (listing.commuteStatus === 'missing_address' || listing.commuteStatus === 'city_conflict') return '位置待核验'
  if (listing.locationStatus !== 'verified') {
    const hasCoordinates = coordinate(listing?.lng, -180, 180) !== null
      && coordinate(listing?.lat, -90, 90) !== null
    if ((listing.geocodeStatus === 'ok' || listing.geocodeStatus === 'provided') && hasCoordinates) {
      return '目标地点待确认'
    }
    return '位置待核验'
  }
  if (listing.commuteStatus === 'pending') return '通勤待计算'
  return listing.commute || '通勤待计算'
}

export function filterStatusLabel(listing) {
  if (listing?.filterStatus === 'passed') return '符合硬条件'
  if (listing?.filterStatus === 'excluded') return '不符合硬条件'
  return '条件待核验'
}

export function locationStatusLabel(listing) {
  if (listing?.locationStatus === 'verified') return '位置已核验'
  if (listing?.geocodeStatus === 'not_configured') return '地图未配置'
  if (listing?.geocodeStatus === 'missing_address') return '平台未提供地址'
  if (listing?.geocodeStatus === 'city_conflict' || listing?.commuteStatus === 'city_conflict') return '城市信息冲突'
  if (listing?.geocodeStatus === 'ok' || listing?.geocodeStatus === 'provided') return '房源地址已定位'
  return '位置待核验'
}

export function listingLocationNotice(listing) {
  if (!listing) return '房源位置待核验。'
  if (listing.locationStatus === 'verified') {
    return listing.locationConfidence
      ? `房源地址已通过${listing.locationConfidence}定位，并已计算到确认目标的距离。`
      : '房源地址已定位，并已计算到确认目标的距离。'
  }
  if (listing.geocodeStatus === 'city_conflict' || listing.commuteStatus === 'city_conflict') {
    return '平台地址与目标城市信息冲突，未用于距离和通勤计算。'
  }
  if (listing.geocodeStatus === 'not_configured') {
    return '地图服务尚未配置，暂时无法核验房源地址。'
  }
  const regionNotice = listing.regionScope === 'administrative_region'
    && ['card_region_link', 'official_filter_page'].includes(listing.regionEvidence)
    ? `候选来自${asText(listing.regionScopeName, '', 100)}官方行政区筛选页，仅有区域范围依据。`
    : ''
  if (listing.geocodeStatus === 'missing_address') {
    return `${regionNotice}平台没有提供可用于定位的具体地址，暂时无法计算距离。`
  }
  if ((listing.geocodeStatus === 'ok' || listing.geocodeStatus === 'provided')
      && coordinate(listing.lng, -180, 180) !== null
      && coordinate(listing.lat, -90, 90) !== null) {
    return '房源地址已独立定位；确认目标地点后，才会计算距离和通勤。'
  }
  if (regionNotice) {
    return `${regionNotice}具体地址和到目标的距离仍待核验，不能仅据此认定为附近。`
  }
  if (listing.address && listing.address !== '平台列表未提供具体地址') {
    return '平台提供了房源地址，但地址还没有通过地图服务核验。'
  }
  return '平台没有提供可核验的具体地址。'
}

export function detailFactEntries(facts) {
  const labels = {
    payment_rule: '押付规则',
    agency_fee: '服务费',
    utility_rule: '水电说明',
    minimum_lease: '最短租期',
    available_date: '入住时间'
  }
  return Object.entries(normalizeFacts(facts))
    .filter(([key]) => key !== 'monthly_rent_cny')
    .map(([key, value]) => ({ key, label: labels[key] || key, value }))
}
