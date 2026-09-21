<template>
  <section class="panel map-panel">
    <div class="map-toolbar">
      <div>
        <span class="panel-kicker">地图位置</span>
        <h2>{{ target.name || '等待定位目标地点' }}</h2>
        <p>{{ target.address || '输入一个公司、学校或地标，地图会显示地点候选。' }}</p>
      </div>
      <div class="map-actions">
        <button class="map-tool" :class="{ active: viewMode === 'map' }" type="button" title="地图视图" @click="viewMode = 'map'">地图</button>
        <button v-if="!compact" class="map-tool" :class="{ active: viewMode === 'list' }" type="button" title="房源列表视图" @click="viewMode = 'list'">列表</button>
        <button class="map-icon-button" type="button" title="定位到当前地点" :disabled="!preferredCoordinates" @click="centerTarget">◎</button>
      </div>
    </div>

    <div v-show="viewMode === 'map'" class="map-canvas">
      <div ref="mapContainer" class="amap-map-host" aria-label="高德地图地点候选"></div>

      <div v-if="mapState !== 'ready'" class="map-runtime-state" :class="`is-${mapState}`" role="status">
        <span>{{ mapStateIcon }}</span>
        <strong>{{ mapStateTitle }}</strong>
        <small>{{ mapStateMessage }}</small>
      </div>
      <div v-else-if="!mapMarkerCount" class="map-empty">
        <span>⌖</span>
        <strong>{{ searchActive ? '等待目标地点坐标' : '等待地点坐标' }}</strong>
        <small>{{ searchActive ? '位置核验完成后会显示目标地点' : 'Agent 找到候选后会自动标记在这里' }}</small>
      </div>
      <div v-if="mapState === 'ready' && selectedCandidateMissingCoordinates" class="map-coordinate-note" role="status">
        当前候选没有可用坐标，可以确认文字地址，但地图不会跳到其他地点。
      </div>

      <div v-if="mapState === 'ready'" class="map-legend">
        <span v-if="!searchActive || targetLocated"><i class="legend-dot target"></i>{{ searchActive ? '目标地点' : '地点候选' }}</span>
        <span v-if="searchActive"><i class="legend-dot qualified"></i>通过初筛</span>
        <span v-if="searchActive"><i class="legend-dot recommended"></i>Agent 推荐</span>
        <span v-if="searchActive"><i class="legend-dot detail-pending"></i>详情待核验</span>
        <span v-else><i class="legend-dot pending"></i>当前查看</span>
      </div>
      <div v-if="mapState === 'ready'" class="map-attribution">高德地图 JSAPI</div>
    </div>

    <div v-if="activeClusterListings.length" class="map-cluster-list" aria-live="polite">
      <div class="cluster-heading"><strong>此处聚合 {{ activeClusterListings.length }} 套房源</strong><button type="button" aria-label="关闭聚合房源" @click="activeClusterIds = []">×</button></div>
      <small>位置相近不代表同一套房；可放大地图，或直接查看：</small>
      <button v-for="listing in activeClusterListings" :key="listing.id" type="button" @click="selectListing(listing.id)">
        <b>{{ listingNumberLabel(listing) }}</b> {{ listingPlaceLabel(listing) }}
        <span>{{ listing.rent === null ? '租金未说明' : `¥${listing.rent}/月` }}</span>
        <em v-if="isAgentRecommended(listing)">Agent 推荐</em>
        <em v-else-if="isFilterPassed(listing)">通过初筛 · {{ detailStatusLabel(listing) }}</em>
      </button>
    </div>

    <div v-show="viewMode === 'list'" class="map-list-view">
      <button v-for="listing in pagedListings" :key="listing.id" type="button" class="map-list-row" :class="listingRowClasses(listing)" @click="selectListing(listing.id)">
        <span class="map-list-rank">{{ listingNumberLabel(listing) }}<small>{{ listing.platform }}</small></span>
        <span class="map-list-name"><strong>{{ listingTitleLabel(listing) }}</strong><small>{{ listingPlaceLabel(listing) }} · {{ listing.room }} · {{ formatListingDistance(listing) }} · {{ formatListingCommute(listing) }}</small><em v-if="isAgentRecommended(listing)" class="recommendation-chip">{{ recommendationLabel(listing) }}</em><small v-else-if="isFilterPassed(listing)" class="detail-status-line">{{ detailStatusLabel(listing) }}</small><small v-if="listing.filterStatus === 'excluded'">不符合当前硬条件</small></span>
        <strong class="map-list-rent">{{ listing.rent === null ? '租金未说明' : '¥' + listing.rent }}<small v-if="listing.rent !== null">/月</small></strong>
      </button>
      <div v-if="!listings.length" class="map-list-empty">当前条件下没有可展示的房源</div>
      <button v-if="listingRemainingCount > 0" class="listing-load-more" type="button" @click="emit('load-more')">
        加载更多（剩余 {{ listingRemainingCount }} 条）
      </button>
    </div>

    <div v-if="placeCandidates.length" class="place-candidates" aria-live="polite">
      <div class="place-candidates-heading">
        <div>
          <strong>{{ confirmedPlaceRef ? '已选目标地点' : '请选择目标地点' }}</strong>
          <small>{{ placeCandidatesHint }}</small>
        </div>
        <span>{{ placeCandidates.length }} 个候选</span>
      </div>
      <div class="place-candidates-list">
        <div
          v-for="(candidate, index) in placeCandidates"
          :key="candidate.candidate_ref"
          class="place-candidate-row"
          :class="{
            selected: candidate.candidate_ref === selectedPlaceRef,
            confirmed: candidate.candidate_ref === confirmedPlaceRef
          }"
        >
          <button class="place-candidate-main" type="button" @click="selectPlace(candidate)">
            <span class="place-candidate-rank">{{ index + 1 }}</span>
            <span class="place-candidate-copy">
              <strong>{{ candidate.name }}</strong>
              <small>{{ candidate.formatted_address || candidateArea(candidate) || '地址待补充' }}</small>
              <em v-if="candidateArea(candidate)">{{ candidateArea(candidate) }}</em>
            </span>
            <span v-if="candidate.candidate_ref === confirmedPlaceRef" class="place-candidate-status">已选</span>
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="locationResolution && !placeCandidates.length"
      class="location-resolution-state"
      :class="{ failed: ['timeout', 'provider_error', 'not_configured', 'quota_exceeded'].includes(locationResolution.status) }"
      role="status"
    >
      <span>{{ locationResolution.status === 'resolved' ? '✓' : '!' }}</span>
      <div>
        <strong>{{ locationResolution.title }}</strong>
        <small>{{ locationResolution.message }}</small>
        <em v-if="searchActive && listings.length">
          {{ targetLocated
            ? `本轮房源按${locationResolution.cityHint || '文字条件'}搜索，距离目标未核验`
            : '房源地址会独立定位；确认目标地点后再计算距离和通勤' }}
        </em>
      </div>
    </div>

    <div v-if="compact && searchActive" class="context-listing-results" aria-live="polite">
      <div class="context-results-heading">
        <div><strong>{{ listingSectionTitle }}</strong><small>{{ listingStatusText }}</small></div>
        <span>列表 {{ pagedListings.length }}/{{ effectiveListingTotal }} 套</span>
      </div>
      <p class="candidate-pool-note">全部候选 {{ effectiveListingTotal }} 套 · 初筛通过 {{ qualifiedCount }} 套 · Agent 精选 {{ recommendedCount }} 套 · 待核验 {{ pendingDetailCount }} 套<span v-if="excludedCount"> · {{ excludedCount }} 套不符合硬条件</span>。地图展示所有已定位候选，不受列表分页、详情批次或推荐数量限制；# 为固定房源编号，不是排名。</p>
      <div class="context-listing-list">
        <button
          v-for="listing in pagedListings"
          :key="listing.id"
          type="button"
          class="map-list-row"
          :class="listingRowClasses(listing)"
          @click="selectListing(listing.id)"
        >
          <span class="map-list-rank">{{ listingNumberLabel(listing) }}<small>{{ listing.platform }}</small></span>
          <span class="map-list-name">
            <strong>{{ listingTitleLabel(listing) }}</strong>
            <small>{{ listingPlaceLabel(listing) }} · {{ listing.room }} · {{ formatListingDistance(listing) }} · {{ formatListingCommute(listing) }}</small>
            <em v-if="isAgentRecommended(listing)" class="recommendation-chip">{{ recommendationLabel(listing) }}</em>
            <small v-else-if="isFilterPassed(listing)" class="detail-status-line">{{ detailStatusLabel(listing) }}</small>
            <small v-if="listing.filterStatus === 'excluded'">不符合当前硬条件</small>
          </span>
          <strong class="map-list-rent">{{ listing.rent === null ? '租金未说明' : '¥' + listing.rent }}<small v-if="listing.rent !== null">/月</small></strong>
        </button>
        <div v-if="!listings.length" class="context-listing-empty" :class="{ loading: searchRunning }">
          <span></span>
          <strong>{{ searchRunning ? '正在获取房源候选' : '当前没有可展示的房源' }}</strong>
          <small>{{ listingStatusText }}</small>
        </div>
        <button v-if="listingRemainingCount > 0" class="listing-load-more" type="button" @click="emit('load-more')">
          加载更多（剩余 {{ listingRemainingCount }} 条）
        </button>
      </div>
    </div>

    <div class="map-footer">
      <div class="map-summary">
        <template v-if="searchActive">本轮 <strong>{{ effectiveListingTotal }}</strong> 套候选</template>
        <template v-else><strong>{{ placeCandidates.length }}</strong> 个地点候选</template>
        <span>·</span>{{ mapAvailabilityLabel }}
      </div>
      <div class="map-confidence"><span class="confidence-dot"></span>{{ target.confidence || '待确认' }}定位</div>
    </div>
  </section>
</template>

<script setup>
import AMapLoader from '@amap/amap-jsapi-loader'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'

import { candidateArea, candidateCoordinates } from '../services/placeCandidates.js'
import { groupListingPoints } from '../services/mapClusters.js'
import {
  detailStatusLabel,
  formatListingCommute,
  formatListingDistance,
  isAgentRecommended,
  isFilterPassed,
  listingPlaceLabel,
  listingTitleLabel,
  listingNumberLabel,
  recommendationLabel
} from '../services/listingState.js'

const props = defineProps({
  target: { type: Object, required: true },
  listings: { type: Array, default: () => [] },
  placeCandidates: { type: Array, default: () => [] },
  locationResolution: { type: Object, default: null },
  selectedId: { type: String, default: null },
  selectedPlaceRef: { type: String, default: '' },
  confirmedPlaceRef: { type: String, default: '' },
  compact: { type: Boolean, default: false },
  searchActive: { type: Boolean, default: false },
  searchRunning: { type: Boolean, default: false },
  searchSummary: { type: Object, default: null },
  listingTotalCount: { type: Number, default: 0 },
  listingRemainingCount: { type: Number, default: 0 },
  listPageSize: { type: Number, default: 20 }
})

const emit = defineEmits(['select', 'center', 'select-place', 'load-more'])
const viewMode = ref('map')
const mapContainer = ref(null)
const mapInstance = shallowRef(null)
const mapState = ref('not_configured')
const jsapiKey = String(import.meta.env.VITE_AMAP_JSAPI_KEY || '').trim()
const securityCode = String(import.meta.env.VITE_AMAP_SECURITY_CODE || '').trim()
const mapConfigured = Boolean(jsapiKey && securityCode)
let amapApi = null
let mapMarkers = []
let disposed = false
const activeClusterIds = ref([])
const activeClusterListings = computed(() => props.listings.filter(item => activeClusterIds.value.includes(item.id)))
const pagedListings = computed(() => props.listings.slice(0, props.listPageSize))
const recommendedCount = computed(() => props.listings.filter(isAgentRecommended).length)
const qualifiedCount = computed(() => props.listings.filter(isFilterPassed).length)
const pendingDetailCount = computed(() => props.listings.filter(item => (
  isFilterPassed(item) && item.detailStatus !== 'ok'
)).length)
const excludedCount = computed(() => props.listings.filter(item => item.filterStatus === 'excluded').length)

const candidatePoints = computed(() => props.placeCandidates.flatMap((candidate, index) => {
  const position = candidateCoordinates(candidate)
  return position ? [{ candidate, index, position }] : []
}))
const listingPoints = computed(() => props.listings.flatMap((listing, index) => {
  const position = candidateCoordinates(listing)
  return position ? [{ listing, index, position }] : []
}))
const targetCoordinates = computed(() => candidateCoordinates(props.target))
const selectedCandidate = computed(() => props.placeCandidates.find(candidate => candidate.candidate_ref === props.selectedPlaceRef) || null)
const preferredCoordinates = computed(() => {
  if (!props.searchActive && selectedCandidate.value) return candidateCoordinates(selectedCandidate.value)
  if (props.searchActive) return targetCoordinates.value || listingPoints.value[0]?.position || null
  return candidatePoints.value[0]?.position || targetCoordinates.value
})
const selectedCandidateMissingCoordinates = computed(() => (
  !props.searchActive && Boolean(selectedCandidate.value) && !candidateCoordinates(selectedCandidate.value)
))
const markerPoints = computed(() => {
  if (!props.searchActive) {
    if (candidatePoints.value.length) return candidatePoints.value
    return targetCoordinates.value
      ? [{ candidate: props.target, index: 0, position: targetCoordinates.value, targetOnly: true }]
      : []
  }
  const points = []
  if (targetCoordinates.value) {
    points.push({ candidate: props.target, index: 0, position: targetCoordinates.value, targetOnly: true })
  }
  points.push(...listingPoints.value.map(point => ({
    candidate: point.listing,
    index: point.index,
    position: point.position,
    listingOnly: true
  })))
  return points
})
const mapMarkerCount = computed(() => markerPoints.value.length)
const effectiveListingTotal = computed(() => Math.max(props.listingTotalCount, props.listings.length))
const mapStateIcon = computed(() => ({ loading: '◌', error: '!', not_configured: '○' }[mapState.value] || '⌖'))
const mapStateTitle = computed(() => ({
  loading: '正在加载高德地图',
  error: '地图暂时不可用',
  not_configured: '地图尚未配置'
}[mapState.value] || '等待地点'))
const mapStateMessage = computed(() => ({
  loading: '地点候选仍会正常显示在下方',
  error: '请检查 JSAPI 配置、域名白名单或网络；聊天不受影响',
  not_configured: '配置前端 JSAPI Key 与安全密钥后显示底图；聊天和候选确认仍可用'
}[mapState.value] || ''))
const mapAvailabilityLabel = computed(() => {
  if (mapState.value === 'ready' && props.searchActive && props.listings.length) {
    return `${listingPoints.value.length} 套已定位 · ${props.listings.length - listingPoints.value.length} 套位置待核验`
  }
  if (mapState.value === 'ready') return '地图已就绪'
  if (mapState.value === 'loading') return '地图加载中'
  return mapConfigured ? '地图不可用' : '地图未配置'
})
const targetLocated = computed(() => (
  Boolean(targetCoordinates.value)
))
const placeCandidatesHint = computed(() => {
  if (props.confirmedPlaceRef) {
    return props.searchActive
      ? '本轮结果以此地点为目标；切换后发送新消息，Agent 才会重新处理'
      : '已填入聊天草稿；发送下一条消息后 Agent 才会继续'
  }
  return '点击候选填入聊天草稿；也可直接在聊天中回复名称或序号'
})
const listingSectionTitle = computed(() => '本轮全部候选')
const listingStatusText = computed(() => {
  if (props.searchRunning) return 'Agent 正在调用房源搜索工具'
  if (props.listings.length && listingPoints.value.length < props.listings.length) return '未定位房源保留在列表，不生成猜测坐标'
  if (props.listings.length && !targetLocated.value) return '目标地点未定位，距离未核验'
  if (props.searchSummary?.status === 'failed') return '本轮搜索未完成'
  if (props.searchSummary?.status === 'partial') return '部分平台返回了候选'
  if (props.searchSummary?.status === 'empty') return '本轮搜索没有返回候选'
  if (props.searchSummary?.offline) return '离线夹具结果，仅用于回归验证'
  return props.listings.length ? '位置标记不代表推荐或当前可租' : '等待 Agent 搜索结果'
})

function selectListing(id) {
  emit('select', id)
}

function selectPlace(candidate) {
  emit('select-place', candidate)
}

function listingRowClasses(listing) {
  return {
    selected: props.selectedId === listing.id,
    recommended: isAgentRecommended(listing),
    qualified: isFilterPassed(listing) && !isAgentRecommended(listing),
    detailPending: isFilterPassed(listing) && listing.detailStatus !== 'ok',
    excluded: listing.filterStatus === 'excluded'
  }
}

function markerContent(label, selected = false, confirmed = false, title = '', kind = 'place') {
  const element = document.createElement('button')
  element.type = 'button'
  const accessibleTitle = title || `地点候选 ${label}`
  element.className = [
    'amap-place-marker',
    kind === 'listing' ? 'amap-listing-marker' : '',
    kind === 'target' ? 'amap-target-marker' : '',
    selected ? 'is-selected' : '',
    confirmed ? 'is-confirmed' : ''
  ].filter(Boolean).join(' ')
  element.textContent = label
  element.title = accessibleTitle
  element.setAttribute('aria-label', accessibleTitle)
  return element
}

function clearMarkers() {
  if (mapInstance.value && mapMarkers.length) mapInstance.value.remove(mapMarkers)
  mapMarkers = []
}

function syncMarkers(fitAll = true) {
  if (!mapInstance.value || !amapApi || mapState.value !== 'ready') return
  clearMarkers()

  const points = markerPoints.value.filter(point => !point.listingOnly)

  mapMarkers = points.map(point => {
    const reference = point.candidate.candidate_ref || ''
    const selected = reference ? reference === props.selectedPlaceRef : Boolean(point.targetOnly)
    const confirmed = Boolean(reference && reference === props.confirmedPlaceRef)
    const content = markerContent(
      point.targetOnly ? '⌖' : String(point.index + 1),
      selected,
      confirmed,
      point.targetOnly
        ? (point.candidate.name || '目标地点')
        : point.candidate.name,
      point.targetOnly ? 'target' : 'place'
    )
    if (!point.targetOnly) {
      content.addEventListener('click', event => {
        event.stopPropagation()
        selectPlace(point.candidate)
      })
    }
    return new amapApi.Marker({
      position: point.position,
      content,
      offset: new amapApi.Pixel(point.targetOnly ? 8 : -17, -40),
      title: point.candidate.name,
      zIndex: selected ? 120 : 100
    })
  })

  if (props.searchActive) {
    const groups = groupListingPoints(listingPoints.value, position => mapInstance.value.lngLatToContainer(position))
    for (const group of groups) {
      const listing = group.listings[0]
      const selected = group.listings.some(item => item.id === props.selectedId)
      const isCluster = group.count > 1
      const content = markerContent(
        isCluster ? `${group.count}套` : listingNumberLabel(listing),
        selected, false,
        isCluster ? `${group.count} 套房源：${group.listings.map(listingNumberLabel).join('、')}`
          : `${listingNumberLabel(listing)} ${listingTitleLabel(listing)}`,
        'listing'
      )
      if (isCluster) content.classList.add('is-cluster')
      if (group.recommended) content.classList.add('is-recommended')
      else if (group.qualified) content.classList.add('is-qualified')
      if (group.detailPending) content.classList.add('is-detail-pending')
      if (group.listings.every(item => item.filterStatus === 'excluded')) content.classList.add('is-excluded')
      content.addEventListener('click', event => {
        event.stopPropagation()
        if (isCluster) activeClusterIds.value = group.listings.map(item => item.id)
        else selectListing(listing.id)
      })
      mapMarkers.push(new amapApi.Marker({
        position: group.position, content, offset: new amapApi.Pixel(isCluster ? -23 : -17, -40),
        zIndex: selected ? 140 : group.recommended ? 125 : 110
      }))
    }
  }
  if (!mapMarkers.length) return
  mapInstance.value.add(mapMarkers)
  if (!fitAll) return
  // Fit the original positions, not cluster representatives.
  if (markerPoints.value.length > 1) {
    const boundsMarkers = markerPoints.value.map(point => new amapApi.Marker({ position: point.position }))
    try {
      mapInstance.value.setFitView(boundsMarkers, false, [52, 42, 58, 42], 15)
    } finally {
      // These markers are only bounds inputs, never added to the map. Release
      // any SDK-side overlay reference immediately after setFitView returns.
      for (const marker of boundsMarkers) marker.setMap?.(null)
    }
  } else {
    const center = preferredCoordinates.value || markerPoints.value[0].position
    mapInstance.value.setZoomAndCenter(15, center)
  }
}

function redrawMarkers() { syncMarkers(false) }

async function initializeMap() {
  if (!mapConfigured || !mapContainer.value) return
  mapState.value = 'loading'
  window._AMapSecurityConfig = {
    ...(window._AMapSecurityConfig || {}),
    securityJsCode: securityCode
  }

  try {
    const loadedApi = await AMapLoader.load({ key: jsapiKey, version: '2.0', plugins: [] }).then((AMap) => {
      AMap.getConfig().appname = 'amap-jsapi-skill'
      return AMap
    })
    if (disposed || !mapContainer.value) return
    amapApi = loadedApi
    const center = preferredCoordinates.value || [104.195397, 35.86166]
    mapInstance.value = new amapApi.Map(mapContainer.value, {
      viewMode: '2D',
      zoom: preferredCoordinates.value ? 13 : 4,
      center,
      mapStyle: 'amap://styles/whitesmoke',
      resizeEnable: true
    })
    mapState.value = 'ready'
    for (const event of ['zoomend', 'moveend', 'resize', 'rotateend']) mapInstance.value.on(event, redrawMarkers)
    syncMarkers(true)
  } catch {
    if (!disposed) mapState.value = 'error'
  }
}

function centerTarget() {
  if (mapInstance.value && preferredCoordinates.value) {
    mapInstance.value.setZoomAndCenter(15, preferredCoordinates.value)
  }
  emit('center', selectedCandidate.value || props.target)
}

watch(() => props.placeCandidates, () => syncMarkers(true), { deep: true })
watch(() => props.listings, () => {
  activeClusterIds.value = []
  syncMarkers(true)
}, { deep: true })
watch(() => props.selectedId, () => syncMarkers(false))
watch(() => [props.selectedPlaceRef, props.confirmedPlaceRef], () => syncMarkers(false))
watch(() => props.searchActive, () => syncMarkers(true))
watch(() => [props.target?.lng, props.target?.lat], () => syncMarkers(true))
watch(viewMode, async mode => {
  if (mode !== 'map' || !mapInstance.value) return
  await nextTick()
  mapInstance.value.resize()
  syncMarkers(true)
})

onMounted(initializeMap)
onBeforeUnmount(() => {
  disposed = true
  clearMarkers()
  for (const event of ['zoomend', 'moveend', 'resize', 'rotateend']) mapInstance.value?.off(event, redrawMarkers)
  mapInstance.value?.destroy()
  mapInstance.value = null
  amapApi = null
})
</script>
