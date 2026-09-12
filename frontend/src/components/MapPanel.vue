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
        <strong>等待地点坐标</strong>
        <small>Agent 找到候选后会自动标记在这里</small>
      </div>
      <div v-if="mapState === 'ready' && selectedCandidateMissingCoordinates" class="map-coordinate-note" role="status">
        当前候选没有可用坐标，可以确认文字地址，但地图不会跳到其他地点。
      </div>

      <div v-if="mapState === 'ready'" class="map-legend">
        <span><i class="legend-dot target"></i>地点候选</span>
        <span><i class="legend-dot pending"></i>当前选择</span>
      </div>
      <div v-if="mapState === 'ready'" class="map-attribution">高德地图 JSAPI</div>
    </div>

    <div v-show="viewMode === 'list'" class="map-list-view">
      <button v-for="listing in listings" :key="listing.id" type="button" class="map-list-row" :class="{ selected: selectedId === listing.id }" @click="selectListing(listing.id)">
        <span class="map-list-rank">{{ listing.platform }}</span>
        <span class="map-list-name"><strong>{{ listing.community }}</strong><small>{{ listing.room }} · {{ listing.locationStatus === 'verified' ? `${listing.distance} km` : '位置待核验' }}</small></span>
        <strong class="map-list-rent">¥{{ listing.rent }}<small>/月</small></strong>
      </button>
      <div v-if="!listings.length" class="map-list-empty">当前条件下没有可展示的房源</div>
    </div>

    <div v-if="placeCandidates.length" class="place-candidates" aria-live="polite">
      <div class="place-candidates-heading">
        <div><strong>地点候选</strong><small>点击候选查看地图，需要时可快速填入聊天</small></div>
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
          </button>
          <button
            class="place-confirm-button"
            type="button"
            :disabled="busy"
            @click="confirmPlace(candidate)"
          >
            填入找房需求
          </button>
        </div>
      </div>
    </div>

    <div class="map-footer">
      <div class="map-summary">
        <strong>{{ placeCandidates.length || listings.length }}</strong>
        {{ placeCandidates.length ? '个地点候选' : '条房源候选' }}
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

const props = defineProps({
  target: { type: Object, required: true },
  listings: { type: Array, default: () => [] },
  placeCandidates: { type: Array, default: () => [] },
  selectedId: { type: String, default: null },
  selectedPlaceRef: { type: String, default: '' },
  confirmedPlaceRef: { type: String, default: '' },
  radiusKm: { type: Number, default: 3 },
  compact: { type: Boolean, default: false },
  busy: { type: Boolean, default: false }
})

const emit = defineEmits(['select', 'center', 'select-place', 'confirm-place'])
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
const targetCoordinates = computed(() => candidateCoordinates(props.target))
const selectedCandidate = computed(() => props.placeCandidates.find(candidate => candidate.candidate_ref === props.selectedPlaceRef) || null)
const preferredCoordinates = computed(() => {
  if (selectedCandidate.value) return candidateCoordinates(selectedCandidate.value)
  return candidatePoints.value[0]?.position || targetCoordinates.value
})
const selectedCandidateMissingCoordinates = computed(() => (
  Boolean(selectedCandidate.value) && !candidateCoordinates(selectedCandidate.value)
))
const mapMarkerCount = computed(() => candidatePoints.value.length || (targetCoordinates.value ? 1 : 0))
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
  if (mapState.value === 'ready') return '地图已就绪'
  if (mapState.value === 'loading') return '地图加载中'
  return mapConfigured ? '地图不可用' : '地图未配置'
})

function selectListing(id) {
  emit('select', id)
}

function selectPlace(candidate) {
  emit('select-place', candidate)
}

function confirmPlace(candidate) {
  if (props.busy) return
  emit('confirm-place', candidate)
}

function markerContent(label, selected = false, confirmed = false, title = '') {
  const element = document.createElement('button')
  element.type = 'button'
  element.className = [
    'amap-place-marker',
    selected ? 'is-selected' : '',
    confirmed ? 'is-confirmed' : ''
  ].filter(Boolean).join(' ')
  element.textContent = label
  element.title = title
  element.setAttribute('aria-label', title || `地点候选 ${label}`)
  return element
}

function clearMarkers() {
  if (mapInstance.value && mapMarkers.length) mapInstance.value.remove(mapMarkers)
  mapMarkers = []
}

function syncMarkers(fitAll = true) {
  if (!mapInstance.value || !amapApi || mapState.value !== 'ready') return
  clearMarkers()

  const points = candidatePoints.value.length
    ? candidatePoints.value
    : (targetCoordinates.value
        ? [{ candidate: props.target, index: 0, position: targetCoordinates.value, targetOnly: true }]
        : [])

  mapMarkers = points.map(point => {
    const reference = point.candidate.candidate_ref || ''
    const selected = reference
      ? reference === props.selectedPlaceRef
      : Boolean(point.targetOnly)
    const confirmed = Boolean(reference && reference === props.confirmedPlaceRef)
    const content = markerContent(point.targetOnly ? '⌖' : String(point.index + 1), selected, confirmed, point.candidate.name)
    if (!point.targetOnly) {
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
    const loadedApi = await AMapLoader.load({ key: jsapiKey, version: '2.0', plugins: [] })
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
watch(() => [props.selectedPlaceRef, props.confirmedPlaceRef], () => syncMarkers(false))
watch(() => [props.target?.lng, props.target?.lat], () => {
  if (!props.placeCandidates.length) syncMarkers(true)
})
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
