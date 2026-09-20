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
        <span><i class="legend-dot target"></i>{{ searchActive ? (targetLocated ? '目标地点' : '房源位置') : '地点候选' }}</span>
        <span v-if="searchActive && targetLocated"><i class="legend-dot listing"></i>房源位置</span>
        <span v-else><i class="legend-dot pending"></i>当前查看</span>
      </div>
      <div v-if="mapState === 'ready'" class="map-attribution">高德地图 JSAPI</div>
    </div>

    <div v-show="viewMode === 'list'" class="map-list-view">
      <button v-for="listing in listings" :key="listing.id" type="button" class="map-list-row" :class="{ selected: selectedId === listing.id }" @click="selectListing(listing.id)">
        <span class="map-list-rank">{{ listing.platform }}</span>
        <span class="map-list-name"><strong>{{ listingTitleLabel(listing) }}</strong><small>{{ listingPlaceLabel(listing) }} · {{ listing.room }} · {{ formatListingDistance(listing) }} · {{ formatListingCommute(listing) }}</small></span>
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
        <span>已显示 {{ listings.length }}/{{ effectiveListingTotal }} 条</span>
      </div>
      <div class="context-listing-list">
        <button
          v-for="listing in listings"
          :key="listing.id"
          type="button"
          class="map-list-row"
          :class="{ selected: selectedId === listing.id }"
          @click="selectListing(listing.id)"
        >
          <span class="map-list-rank">{{ listing.platform }}</span>
          <span class="map-list-name">
            <strong>{{ listingTitleLabel(listing) }}</strong>
            <small>{{ listingPlaceLabel(listing) }} · {{ listing.room }} · {{ formatListingDistance(listing) }} · {{ formatListingCommute(listing) }}</small>
          </span>
          <strong class="map-list-rent">{{ listing.rent === null ? '租金未说明' : '¥' + listing.rent }}<small v-if="listing.rent !== null">/月</small></strong>
        </button>
        <div v-if="!listings.length" class="context-listing-empty" :class="{ loading: searchRunning }">
          <span></span>
          <strong>{{ searchRunning ? (targetLocated ? '正在获取附近房源' : '正在获取房源候选') : '当前没有可展示的房源' }}</strong>
          <small>{{ listingStatusText }}</small>
        </div>
        <button v-if="listingRemainingCount > 0" class="listing-load-more" type="button" @click="emit('load-more')">
          加载更多（剩余 {{ listingRemainingCount }} 条）
        </button>
      </div>
    </div>

    <div class="map-footer">
      <div class="map-summary">
        <template v-if="searchActive"><strong>{{ listings.length }}</strong>/{{ effectiveListingTotal }} 条房源已显示</template>
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
import {
  formatListingCommute,
  formatListingDistance,
  listingPlaceLabel,
  listingTitleLabel
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
  listingRemainingCount: { type: Number, default: 0 }
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
    return `${listingPoints.value.length}/${props.listings.length} 条房源已定位`
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
const listingSectionTitle = computed(() => targetLocated.value ? '附近房源' : '房源候选')
const listingStatusText = computed(() => {
  if (props.searchRunning) return 'Agent 正在调用房源搜索工具'
  if (props.listings.length && listingPoints.value.length < props.listings.length) {
    return `${listingPoints.value.length} 条已定位，其余房源地址待核验`
  }
  if (props.listings.length && !targetLocated.value) return '目标地点未定位，距离未核验'
  if (props.searchSummary?.status === 'failed') return '本轮搜索未完成'
  if (props.searchSummary?.status === 'partial') return '部分平台返回了候选'
  if (props.searchSummary?.status === 'empty') return '本轮搜索没有返回候选'
  if (props.searchSummary?.offline) return '离线夹具结果，仅用于回归验证'
  return props.listings.length ? 'Agent 已同步公开平台候选' : '等待 Agent 搜索结果'
})

function selectListing(id) {
  emit('select', id)
}

function selectPlace(candidate) {
  emit('select-place', candidate)
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

  const points = markerPoints.value

  mapMarkers = points.map(point => {
    const reference = point.candidate.candidate_ref || ''
    const listingId = point.listingOnly ? point.candidate.id : ''
    const selected = point.listingOnly
      ? listingId === props.selectedId
      : reference ? reference === props.selectedPlaceRef : Boolean(point.targetOnly)
    const confirmed = Boolean(reference && reference === props.confirmedPlaceRef)
    const content = markerContent(
      point.targetOnly ? '⌖' : String(point.index + 1),
      selected,
      confirmed,
      point.targetOnly
        ? (point.candidate.name || '目标地点')
        : (point.listingOnly
          ? `${listingTitleLabel(point.candidate)} · ${listingPlaceLabel(point.candidate)}`
          : point.candidate.name),
      point.listingOnly ? 'listing' : (point.targetOnly ? 'target' : 'place')
    )
    if (point.listingOnly) {
      content.addEventListener('click', event => {
        event.stopPropagation()
        selectListing(point.candidate.id)
      })
    } else if (!point.targetOnly) {
      content.addEventListener('click', event => {
        event.stopPropagation()
        selectPlace(point.candidate)
      })
    }
    return new amapApi.Marker({
      position: point.position,
      content,
      offset: new amapApi.Pixel(-17, -40),
      title: point.candidate.name,
      zIndex: selected ? 120 : 100
    })
  })

  if (!mapMarkers.length) return
  mapInstance.value.add(mapMarkers)
  if (!fitAll && selectedCandidateMissingCoordinates.value) return
  if (fitAll && mapMarkers.length > 1) {
    mapInstance.value.setFitView(mapMarkers, false, [52, 42, 58, 42], 15)
  } else {
    const center = preferredCoordinates.value || points[0].position
    mapInstance.value.setZoomAndCenter(15, center)
  }
}

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
watch(() => props.listings, () => syncMarkers(true), { deep: true })
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
  mapInstance.value?.destroy()
  mapInstance.value = null
  amapApi = null
})
</script>
