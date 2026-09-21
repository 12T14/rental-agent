<template>
  <div class="app-shell chat-shell">
    <aside class="sidebar">
      <div class="brand-block">
        <div class="brand-mark">栖</div>
        <div>
          <div class="brand-name">栖居</div>
          <div class="brand-caption">智能找房助手</div>
        </div>
      </div>

      <button class="new-search" type="button" @click="resetSearch">
        <span class="plus-icon">＋</span><span>新建会话</span>
      </button>

      <div class="sidebar-section">
        <div class="section-label">历史会话</div>
        <div v-for="item in conversations" :key="item.id" class="conversation-item" :class="{ active: item.id === activeHistory }">
          <button class="history-item" type="button" @click="loadHistory(item)">
            <span class="history-pin">◌</span>
            <span class="history-copy"><strong>{{ item.title }}</strong></span>
          </button>
          <button class="conversation-edit" type="button" title="编辑会话标题" @click="startTitleEdit(item)">✎</button>
        </div>
      </div>

      <div class="sidebar-section saved-section">
        <div class="section-label">我的收藏 <span class="count-pill">{{ savedListings.length }}</span></div>
       <button v-for="item in savedListings" :key="item.id" class="saved-item" type="button" @click="selectListing(item.id)"><span class="saved-heart">♥</span><span>{{ listingPlaceLabel(item) }}</span><small>{{ item.rent === null ? '租金未说明' : `¥${item.rent}/月` }}</small></button>
        <div v-if="!savedListings.length" class="saved-empty"><span class="saved-icon">♡</span><span>在房源卡片上收藏喜欢的房子</span></div>
      </div>

      <div class="sidebar-section privacy-section">
        <div class="section-label">本地数据</div>
        <button class="privacy-action danger" type="button" :disabled="!backendChatEnabled || !activeHistory || searching || Boolean(privacyBusy)" :aria-busy="privacyBusy === 'session'" @click="deleteActiveSession">
          <span aria-hidden="true">⌫</span><span>删除当前会话</span>
        </button>
        <button class="privacy-action" type="button" :disabled="!backendChatEnabled || Boolean(privacyBusy)" :aria-busy="privacyBusy === 'platform'" @click="clearPlatformVerification">
          <span aria-hidden="true">◌</span><span>清除平台验证数据</span>
        </button>
        <button class="privacy-action" type="button" :disabled="!backendChatEnabled || Boolean(privacyBusy)" :aria-busy="privacyBusy === 'artifacts'" @click="clearLocalArtifacts">
          <span aria-hidden="true">⌁</span><span>清除抓取产物</span>
        </button>
      </div>

      <div class="sidebar-foot">
        <div class="privacy-line"><span class="status-dot"></span>{{ backendChatEnabled ? '本地单用户模式' : '本地离线模式' }}</div>
        <div class="version-line">{{ storageLabel }}</div>
      </div>
    </aside>

    <main class="main-area">
      <header class="topbar">
        <div class="breadcrumb"><span class="eyebrow">找房工作台</span><span class="crumb-separator">/</span><span>与助手对话</span></div>
        <div class="top-actions"><span class="local-badge"><span class="pulse-dot"></span>本地模式</span><button class="icon-button" type="button" title="使用说明">?</button><div class="avatar">U</div></div>
      </header>

      <section class="conversation-layout">
        <section class="conversation-column">
          <div class="conversation-heading">
            <div class="conversation-title-wrap">
              <div v-if="!editingTitle" class="conversation-title-row">
                <h1>{{ sessionTitle }}</h1>
                <button class="title-edit" type="button" title="编辑会话标题" @click="startTitleEdit()">✎</button>
              </div>
              <form v-else class="title-editor" @submit.prevent="saveTitle">
                <input ref="titleInput" v-model="titleDraft" aria-label="会话标题" maxlength="32" />
                <button type="submit" title="保存标题">✓</button>
                <button type="button" title="取消编辑" @click="cancelTitle">×</button>
              </form>
              <p>直接说出你的工作地点和偏好，我会边聊边整理房源。</p>
            </div>
          </div>
          <div
            v-if="historyLoading || sessionReadError || recoveryRequestId || pendingInterrupt || runtimeNotice"
            class="session-notice"
            :class="{ 'is-error': runtimeNotice?.tone === 'error' }"
            role="status"
          >
            <template v-if="historyLoading">正在读取会话…</template>
            <template v-else-if="sessionReadError">
              {{ sessionReadError }}
              <button type="button" @click="refreshCurrentSession">刷新会话</button>
            </template>
            <template v-else-if="recoveryRequestId">
              {{ runtimeNotice?.message || '上次任务尚未完成。可以恢复执行，也可以新建会话；未完成的工具步骤可能重试。' }}
              <button type="button" :disabled="searching" @click="handleChatMessage('', { recover: true })">恢复上次任务</button>
            </template>
            <template v-else-if="pendingInterrupt">助手正在等待你补充条件，直接在下方回复即可。</template>
            <template v-else>
              {{ runtimeNotice.message }}
              <button type="button" aria-label="关闭提示" @click="runtimeNotice = null">关闭</button>
            </template>
          </div>
          <ChatPanel
            v-model="chatDraft"
            :messages="messages"
            :platforms="platforms"
            :searching="searching"
            :send-disabled="historyLoading || Boolean(sessionReadError) || Boolean(recoveryRequestId)"
            :recovery-available="Boolean(recoveryRequestId) && !historyLoading && !sessionReadError"
            :verification="verification"
            @send="handleChatMessage"
            @recover="handleChatMessage('', { recover: true })"
            @cancel="cancelChat"
            @verify="startVerification"
          />
        </section>

        <aside class="context-column">
          <div class="context-heading"><div><h2>地点与房源</h2><p>Agent 的位置与搜索结果</p></div><span class="context-live"><i></i>在线</span></div>
          <MapPanel
            compact
            :target="target"
            :listings="allCandidateListings"
            :list-page-size="listingDisplayLimit"
            :place-candidates="placeCandidates"
            :selected-id="selectedListingId"
            :selected-place-ref="selectedPlaceRef"
            :confirmed-place-ref="confirmedPlaceRef"
            :location-resolution="locationResolution"
            :search-active="searchActive"
            :search-running="listingSearchRunning"
            :search-summary="searchSummary"
            :listing-total-count="allCandidateListings.length"
            :listing-remaining-count="remainingListingCount"
            @select="selectListing"
            @select-place="selectPlaceCandidate"
            @center="centerMap"
            @load-more="loadMoreListings"
          />
          <div v-if="selectedListing" class="selected-summary"><div class="summary-heading"><span>当前选中</span><button type="button" @click="selectedListingId = null">×</button></div><strong>{{ listingPlaceLabel(selectedListing) }}</strong><p>{{ selectedListing.rent === null ? '租金未说明' : `¥${selectedListing.rent}/月` }} · {{ selectedListing.room }}</p><button type="button" @click="openDetail(selectedListing)">查看房源概览 <span>→</span></button></div>
        </aside>
      </section>
    </main>

    <Transition name="drawer">
      <aside v-if="selectedListing" class="detail-drawer">
        <div class="drawer-backdrop" @click="selectedListingId = null"></div>
        <div class="drawer-panel">
          <div class="drawer-header"><div><span class="drawer-kicker">房源 {{ listingNumberLabel(selectedListing) }}</span><h2>{{ selectedListing.title }}</h2></div><button class="close-button" type="button" @click="selectedListingId = null">×</button></div>
          <div v-if="isAgentRecommended(selectedListing)" class="drawer-block recommendation-summary"><span class="drawer-label">{{ recommendationLabel(selectedListing) }}</span><p>{{ selectedListing.recommendation.reason }}</p><small v-if="selectedListing.recommendation.caveat">注意：{{ selectedListing.recommendation.caveat }}</small></div>
          <div class="drawer-price"><b>{{ selectedListing.rent === null ? '租金未说明' : `¥${selectedListing.rent}` }}</b><span v-if="selectedListing.rent !== null">/月</span><span class="verified-chip">{{ selectedListing.offline ? '离线夹具' : '公开候选' }}</span></div>
          <div class="drawer-grid"><div><span>户型</span><strong>{{ selectedListing.room }}</strong></div><div><span>面积</span><strong>{{ selectedListing.area === null ? '未说明' : `${selectedListing.area}㎡` }}</strong></div><div><span>距离目标</span><strong>{{ formatListingDistance(selectedListing) }}</strong></div><div><span>通勤参考</span><strong>{{ formatListingCommute(selectedListing) }}</strong></div></div>
          <div class="drawer-block"><span class="drawer-label">位置</span><p>{{ listingAddressLabel(selectedListing) }}</p><small :class="selectedListing.locationStatus === 'verified' ? 'verified-text' : 'warning-text'">{{ listingLocationNotice(selectedListing) }}</small></div>
          <div class="drawer-block"><span class="drawer-label">来源与状态</span><div class="source-row"><span class="source-dot" :class="selectedListing.platformKey"></span>{{ selectedListing.platform }} · {{ detailStatusLabel(selectedListing) }} · {{ selectedListing.updated }}</div></div>
          <div v-if="detailFactEntries(selectedListing.detailFacts).length" class="drawer-block"><span class="drawer-label">详情页明确条件</span><div class="detail-facts"><div v-for="fact in detailFactEntries(selectedListing.detailFacts)" :key="fact.key"><span>{{ fact.label }}</span><strong>{{ fact.value }}</strong></div></div></div>
          <div class="drawer-block ranking-block"><span class="drawer-label">条件判断</span><p class="filter-result" :class="selectedListing.filterStatus">{{ filterStatusLabel(selectedListing) }}</p><small v-if="selectedListing.filterReasons?.length">{{ selectedListing.filterReasons.join('；') }}</small><small v-if="selectedListing.rankingExplanation">{{ selectedListing.rankingExplanation }}</small></div>
          <div class="drawer-tags"><span v-for="tag in selectedListing.tags" :key="tag">{{ tag }}</span></div>
          <div class="drawer-actions"><button class="primary-action" type="button" @click="openSource(selectedListing)">{{ selectedListing.urlType === 'source_list' ? '打开平台列表' : '打开原平台' }} <span>↗</span></button><button class="secondary-action" type="button" @click="toggleSaved(selectedListing)">{{ selectedListing.saved ? '已收藏' : '收藏房源' }} <span>{{ selectedListing.saved ? '♥' : '♡' }}</span></button></div>
          <p class="drawer-note">公开列表候选不等于当前可租，请打开原平台核验详情、费用和联系方式。</p>
        </div>
      </aside>
    </Transition>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onBeforeUnmount, ref } from 'vue'
import ChatPanel from './components/ChatPanel.vue'
import MapPanel from './components/MapPanel.vue'
import {
  canSubmitChatMessage,
  streamChat,
  streamResume,
  streamRecover,
  listSessions,
  getSession,
  renameSession,
  deleteSession,
  clearPlatformSessions,
  clearArtifacts
} from './services/chatApi'
import { createViewGuard, sessionAction } from './services/sessionState.js'
import {
  candidateArea,
  buildSearchContext,
  draftKeepsPlaceSelection,
  isResolvedLocationPayload,
  locationResolutionCopy,
  mergePlaceIntoDraft,
  normalizeLocationCandidates
} from './services/placeCandidates.js'
import {
  applyDetailResults,
  applyListingEnrichment,
  defaultPlatformStatuses,
  detailStatusLabel,
  detailFactEntries,
  filterStatusLabel,
  formatListingCommute,
  formatListingDistance,
  isAgentRecommended,
  listingAddressLabel,
  listingLocationNotice,
  listingPlaceLabel,
  listingNumberLabel,
  recommendationLabel,
  mergeSearchSummary,
  normalizeListings,
  normalizePlatformStatuses
} from './services/listingState.js'

const target = ref({ name: '', address: '输入地点后选择一个定位结果', city: '', lng: null, lat: null, confidence: '待确认' })
const listings = ref([])
const platforms = ref(defaultPlatformStatuses())
const searchSummary = ref(null)
const conversations = ref([])
const messages = ref([
  { id: 'welcome', role: 'assistant', text: '你好，我是栖居找房助手。你可以直接告诉我工作地点、预算和通勤要求，我会帮你整理附近房源。' }
])
const activeHistory = ref('')
const selectedListingId = ref(null)
const placeCandidates = ref([])
const selectedPlaceRef = ref('')
const confirmedPlaceRef = ref('')
const pendingPlaceSelectionRef = ref('')
const pendingPlaceSelection = ref(null)
const locationResolution = ref(null)
const chatDraft = ref('')
const searching = ref(false)
const searchActive = ref(false)
const listingSearchRunning = ref(false)
const verification = ref(null)
const chatAbortController = ref(null)
const backendChatEnabled = import.meta.env.VITE_USE_BACKEND_CHAT !== 'false'
const pendingInterrupt = ref(null)
const recoveryRequestId = ref('')
const historyLoading = ref(false)
const sessionReadError = ref('')
const runtimeNotice = ref(null)
const storageMode = ref('')
const privacyBusy = ref('')
const LISTING_PAGE_SIZE = 20
const listingDisplayLimit = ref(LISTING_PAGE_SIZE)
const storageLabel = computed(() => !backendChatEnabled ? '仅使用本地离线数据'
  : storageMode.value === 'mongodb' ? 'MongoDB 会话持久化'
    : storageMode.value === 'memory' ? '内存存储：后端重启会丢失会话' : '尚未连接会话存储')
const viewGuard = createViewGuard()
let historyController = null
let titleRefreshTimer = null
let titleRefreshGeneration = 0
const editingTitle = ref(false)
const titleDraft = ref('')
const titleInput = ref(null)
const selectedListing = computed(() => listings.value.find(item => item.id === selectedListingId.value))
const savedListings = computed(() => listings.value.filter(item => item.saved))
const activeConversation = computed(() => conversations.value.find(item => item.id === activeHistory.value))
const sessionTitle = computed(() => activeConversation.value?.title || '新建会话')
// The map always receives the complete bounded pool. Pagination affects only
// the scrollable list, never which houses exist on the map.
const allCandidateListings = computed(() => searchActive.value ? listings.value : [])
const displayListings = computed(() => allCandidateListings.value.slice(0, listingDisplayLimit.value))
const remainingListingCount = computed(() => Math.max(0, allCandidateListings.value.length - displayListings.value.length))

function resetListingDisplayLimit() {
  listingDisplayLimit.value = LISTING_PAGE_SIZE
}

function loadMoreListings() {
  listingDisplayLimit.value = Math.min(
    allCandidateListings.value.length,
    listingDisplayLimit.value + LISTING_PAGE_SIZE
  )
}

function addMessage(message) {
  const id = `${Date.now()}-${Math.random()}`
  messages.value.push({ id, role: 'assistant', ...message })
  return id
}
function updateMessage(id, patch) {
  const message = messages.value.find(item => item.id === id)
  if (message) Object.assign(message, patch)
}
function receivePlatformStatus(data) {
  searchActive.value = true
  platforms.value = normalizePlatformStatuses(data, platforms.value)
  searchSummary.value = mergeSearchSummary(searchSummary.value, data)
}
function receiveListings(data) {
  resetListingDisplayLimit()
  const savedIds = new Set(listings.value.filter(item => item.saved).map(item => item.id))
  listings.value = normalizeListings(data).map(item => ({ ...item, saved: item.saved || savedIds.has(item.id) }))
  searchActive.value = true
  searchSummary.value = mergeSearchSummary(searchSummary.value, data)
}
function receiveListingDetails(data) {
  listings.value = applyDetailResults(listings.value, data)
  const summary = { detail_status: data?.status }
  if (Object.prototype.hasOwnProperty.call(data || {}, 'offline')) summary.offline = data.offline
  if (Object.prototype.hasOwnProperty.call(data || {}, 'retrieved_at')) summary.retrieved_at = data.retrieved_at
  searchSummary.value = mergeSearchSummary(searchSummary.value, summary)
}
function receiveListingUpdate(data) {
  listings.value = normalizeListings(data, listings.value)
  searchActive.value = true
  const summary = { detail_status: data?.detail_status ?? data?.status }
  if (Object.prototype.hasOwnProperty.call(data || {}, 'offline')) summary.offline = data.offline
  if (Object.prototype.hasOwnProperty.call(data || {}, 'retrieved_at')) summary.retrieved_at = data.retrieved_at
  searchSummary.value = mergeSearchSummary(searchSummary.value, summary)
}
function receiveListingEnrichment(data) {
  listings.value = applyListingEnrichment(listings.value, data)
  searchActive.value = true
  const mapTarget = data?.target
  if (mapTarget && typeof mapTarget === 'object' && (mapTarget.name || mapTarget.lng !== undefined)) {
    const targetRef = typeof mapTarget.candidate_ref === 'string'
      ? mapTarget.candidate_ref.trim()
      : ''
    if (targetRef && placeCandidates.value.some(candidate => candidate.candidate_ref === targetRef)) {
      selectedPlaceRef.value = targetRef
      confirmedPlaceRef.value = targetRef
      pendingPlaceSelectionRef.value = ''
      pendingPlaceSelection.value = null
    }
    target.value = {
      ...target.value,
      name: mapTarget.name || target.value.name,
      address: mapTarget.geocoded_address || mapTarget.formatted_address || target.value.address,
      city: mapTarget.city || target.value.city,
      district: mapTarget.district || target.value.district,
      adcode: mapTarget.adcode || target.value.adcode,
      lng: mapTarget.lng ?? target.value.lng,
      lat: mapTarget.lat ?? target.value.lat,
      candidate_ref: targetRef || target.value.candidate_ref || '',
      confidence: target.value.confidence === '待确认'
        ? (mapTarget.location_confidence || target.value.confidence)
        : target.value.confidence
    }
  }
  const summary = {
    enrichment_status: data?.status,
    map_status: data?.status,
    criteria: data?.criteria,
  }
  if (Object.prototype.hasOwnProperty.call(data || {}, 'offline')) summary.offline = data.offline
  searchSummary.value = mergeSearchSummary(searchSummary.value, summary)
}
const toolLabels = {
  resolve_target_place: '解析目标地点',
  search_rental_candidates: '搜索公开房源',
  batch_fetch_listing_details: '读取房源详情',
  human_verify_rental_platform: '等待平台人工验证',
  playwright_browser: '查看公开网页',
  request_rental_preferences: '等待补充找房条件',
  publish_rental_recommendations: '整理推荐房源'
}
function toolLabel(name) {
  return toolLabels[name] || '处理找房任务'
}
function makeTitle(text) {
  const cleaned = text.replace(/[“”"。！？!?，,、]/g, ' ').replace(/\s+/g, ' ').trim()
  if (!cleaned) return '新建会话'
  return cleaned.length > 18 ? `${cleaned.slice(0, 18)}…` : cleaned
}
function ensureConversation(text) {
  if (!activeHistory.value) {
    const id = `h-${requestId()}`
    conversations.value.unshift({ id, title: backendChatEnabled ? '新建会话' : makeTitle(text) })
    activeHistory.value = id
    rememberActiveSession(id)
  } else if (!backendChatEnabled && activeConversation.value && activeConversation.value.title === '新建会话') {
    activeConversation.value.title = makeTitle(text)
  }
}
function requestId() {
  return globalThis.crypto?.randomUUID?.() || `req-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function cancelTitleRefresh() {
  titleRefreshGeneration += 1
  if (titleRefreshTimer !== null) {
    globalThis.clearTimeout(titleRefreshTimer)
    titleRefreshTimer = null
  }
}

function scheduleTitleRefresh(sessionId) {
  cancelTitleRefresh()
  const generation = titleRefreshGeneration
  const viewVersion = viewGuard.capture()
  const delays = [250, 750, 1500, 3000, 5000]
  let attempt = 0

  const poll = async () => {
    titleRefreshTimer = null
    if (
      generation !== titleRefreshGeneration
      || !backendChatEnabled
      || sessionId !== activeHistory.value
      || searching.value
      || !viewGuard.isCurrent(viewVersion)
    ) return

    try {
      const state = await getSession(sessionId)
      if (
        generation !== titleRefreshGeneration
        || sessionId !== activeHistory.value
        || !viewGuard.isCurrent(viewVersion)
      ) return
      if (activeConversation.value) activeConversation.value.title = state.title
      if (['model', 'user', 'fallback'].includes(state.title_source)) return
    } catch {
      // 标题是展示增强；本轮已完成时不因后台轮询失败打扰用户。
      return
    }

    if (attempt < delays.length) {
      titleRefreshTimer = globalThis.setTimeout(poll, delays[attempt])
      attempt += 1
    }
  }

  titleRefreshTimer = globalThis.setTimeout(poll, delays[attempt])
  attempt += 1
}

function applyPlacePreview(candidate, confidence = '待用户确认') {
  target.value = {
    ...target.value,
    name: candidate.name,
    address: candidate.formatted_address,
    city: candidateArea(candidate),
    lng: candidate.lng,
    lat: candidate.lat,
    candidate_ref: candidate.candidate_ref,
    confidence
  }
}

function receiveLocationCandidates(payload) {
  const nextCandidates = normalizeLocationCandidates(payload)
  const status = typeof payload?.status === 'string' ? payload.status.trim().toLowerCase() : ''
  const copy = locationResolutionCopy(status)
  locationResolution.value = {
    status,
    title: copy.title,
    message: copy.message,
    query: typeof payload?.query === 'string' ? payload.query.trim() : '',
    cityHint: typeof payload?.city_hint === 'string' ? payload.city_hint.trim() : '',
    hasCandidates: nextCandidates.length > 0
  }
  placeCandidates.value = nextCandidates
  if (!nextCandidates.length) {
    selectedPlaceRef.value = ''
    confirmedPlaceRef.value = ''
    pendingPlaceSelectionRef.value = ''
    pendingPlaceSelection.value = null
    const unresolvedQuery = locationResolution.value.query
    const cityHint = locationResolution.value.cityHint
    target.value = {
      ...target.value,
      name: unresolvedQuery || target.value.name,
      address: copy.message,
      city: cityHint,
      lng: null,
      lat: null,
      candidate_ref: '',
      confidence: '未定位'
    }
    return
  }

  const resolvedCandidate = isResolvedLocationPayload(payload) ? nextCandidates[0] : null
  if (resolvedCandidate) {
    confirmedPlaceRef.value = resolvedCandidate.candidate_ref
    pendingPlaceSelectionRef.value = ''
    pendingPlaceSelection.value = null
  }

  const confirmedCandidate = resolvedCandidate
    || nextCandidates.find(candidate => candidate.candidate_ref === confirmedPlaceRef.value)
  const selectedCandidate = confirmedCandidate
    || nextCandidates.find(candidate => candidate.candidate_ref === selectedPlaceRef.value)
    || nextCandidates[0]
  if (!confirmedCandidate) confirmedPlaceRef.value = ''
  selectedPlaceRef.value = selectedCandidate.candidate_ref
  applyPlacePreview(
    selectedCandidate,
    resolvedCandidate ? '已定位' : (confirmedCandidate ? '已选' : '待用户确认')
  )
}

function findPlaceCandidate(candidate) {
  return placeCandidates.value.find(item => item.candidate_ref === candidate?.candidate_ref) || null
}

function selectPlaceCandidate(candidate) {
  if (searching.value) return
  const safeCandidate = findPlaceCandidate(candidate)
  if (!safeCandidate) return
  const previousCandidate = findPlaceCandidate({ candidate_ref: selectedPlaceRef.value })
  selectedPlaceRef.value = safeCandidate.candidate_ref
  confirmedPlaceRef.value = safeCandidate.candidate_ref
  pendingPlaceSelectionRef.value = safeCandidate.candidate_ref
  pendingPlaceSelection.value = safeCandidate
  chatDraft.value = mergePlaceIntoDraft(chatDraft.value, safeCandidate, previousCandidate)
  applyPlacePreview(
    safeCandidate,
    '已选'
  )
}

function currentSearchContext(message = '') {
  const candidate = findPlaceCandidate({ candidate_ref: pendingPlaceSelectionRef.value })
  if (!candidate || !draftKeepsPlaceSelection(message, candidate)) return null
  return buildSearchContext(candidate)
}

async function handleChatMessage(text, { recover = false } = {}) {
  if (searching.value || historyLoading.value || sessionReadError.value) return
  if (recover ? !recoveryRequestId.value : !canSubmitChatMessage(text, Boolean(recoveryRequestId.value))) return
  cancelTitleRefresh()
  text = text.trim()
  const submittedPlaceRef = recover ? '' : pendingPlaceSelectionRef.value
  const searchContext = recover ? null : currentSearchContext(text)
  if (!recover && submittedPlaceRef && !searchContext) {
    pendingPlaceSelectionRef.value = ''
    pendingPlaceSelection.value = null
    if (confirmedPlaceRef.value === submittedPlaceRef) {
      confirmedPlaceRef.value = ''
      if (target.value.candidate_ref === submittedPlaceRef) {
        target.value = { ...target.value, confidence: '待确认' }
      }
    }
  }
  runtimeNotice.value = null
  if (!recover) {
    ensureConversation(text)
    messages.value.push({ id: `${Date.now()}-user`, role: 'user', text })
  }
  if (!backendChatEnabled) {
    runtimeNotice.value = { tone: 'error', message: '后端聊天当前未启用，无法运行找房 Agent。' }
    return
  }

  const sessionId = activeHistory.value
  const viewVersion = viewGuard.capture()
  const action = sessionAction({ pending: pendingInterrupt.value, recoveryRequestId: recoveryRequestId.value })
  const submittedRequestId = recover ? recoveryRequestId.value : requestId()
  const submittedInterruptId = pendingInterrupt.value?.interrupt_id
  const controller = new AbortController()
  chatAbortController.value = controller
  const streamStarted = { value: false }
  const toolMessageIds = new Map()
  const activeToolIds = new Set()
  let streamedMessageId = ''
  let replayed = false
  const activeListingToolIds = new Set()
  searching.value = true
  selectedListingId.value = null
  verification.value = null
  const progressMessageId = addMessage({ kind: 'progress', text: '我正在把需求交给找房 Agent 解析。' })

  try {
    const send = action === 'recover' ? streamRecover : action === 'resume' ? streamResume : streamChat
    await send({
      sessionId,
       message: text,
       clientRequestId: submittedRequestId,
       interruptId: submittedInterruptId,
        searchContext,
       signal: controller.signal,
      onEvent: ({ event, data }) => {
        if (!viewGuard.isCurrent(viewVersion) || chatAbortController.value !== controller) return
        if (event === 'status') {
          streamStarted.value = true
          if (data?.status === 'replayed') {
            replayed = true
            updateMessage(progressMessageId, { text: '已安全重放本次请求结果。' })
          }
        } else if (event === 'token' && typeof data?.text === 'string') {
          if (!streamedMessageId) streamedMessageId = addMessage({ text: data.text })
          else {
            const current = messages.value.find(item => item.id === streamedMessageId)
            if (current) current.text += data.text
          }
        } else if (event === 'tool_start' && data?.tool_call_id) {
          const label = toolLabel(data.tool_name)
          const messageId = addMessage({ kind: 'progress', text: `${label}中…` })
          toolMessageIds.set(data.tool_call_id, { messageId, label, name: data.tool_name })
          activeToolIds.add(data.tool_call_id)
          if (['search_rental_candidates', 'batch_fetch_listing_details'].includes(data.tool_name)) {
            activeListingToolIds.add(data.tool_call_id)
            listingSearchRunning.value = true
          }
          if (data.tool_name === 'search_rental_candidates') {
            resetListingDisplayLimit()
            searchActive.value = true
            listings.value = []
            searchSummary.value = { status: 'running' }
            platforms.value = defaultPlatformStatuses('loading', '抓取中')
            selectedListingId.value = null
          }
        } else if (event === 'tool_end' && data?.tool_call_id) {
          const tool = toolMessageIds.get(data.tool_call_id)
          if (tool) {
            updateMessage(tool.messageId, {
              text: data.status === 'error' ? `${tool.label}失败。` : `${tool.label}完成。`
            })
          }
          activeToolIds.delete(data.tool_call_id)
          activeListingToolIds.delete(data.tool_call_id)
          listingSearchRunning.value = activeListingToolIds.size > 0
          // 工具结果之后生成的文本属于下一条助手消息。
          streamedMessageId = ''
        } else if (event === 'location_candidates') {
          receiveLocationCandidates(data)
        } else if (event === 'platform_status') {
          receivePlatformStatus(data)
        } else if (event === 'listings') {
          receiveListings(data)
        } else if (event === 'listing_details') {
          receiveListingDetails(data)
        } else if (event === 'listing_update') {
          receiveListingUpdate(data)
        } else if (event === 'listing_recommendations') {
          listings.value = normalizeListings(data, listings.value)
        } else if (event === 'listing_enrichment') {
          receiveListingEnrichment(data)
        } else if (event === 'assistant' && data?.text) {
          if (streamedMessageId) updateMessage(streamedMessageId, { text: data.text })
          else streamedMessageId = addMessage({ text: data.text })
        } else if (event === 'interrupt' && data?.interrupt_id) {
          pendingInterrupt.value = data
          recoveryRequestId.value = ''
          updateMessage(progressMessageId, { text: '已保存进度，等待你补充条件。' })
          for (const toolId of activeToolIds) {
            const tool = toolMessageIds.get(toolId)
            if (tool) updateMessage(tool.messageId, { text: `${tool.label}：已暂停。` })
          }
          activeToolIds.clear()
          activeListingToolIds.clear()
          listingSearchRunning.value = false
          addMessage({ text: data.message })
        } else if (event === 'error') {
          const message = data?.error?.message || 'Agent 暂时无法处理这条消息。'
          runtimeNotice.value = { tone: 'error', message }
          for (const toolId of activeToolIds) {
            const tool = toolMessageIds.get(toolId)
            if (tool) updateMessage(tool.messageId, { text: `${tool.label}未完成。` })
          }
          activeToolIds.clear()
          activeListingToolIds.clear()
          listingSearchRunning.value = false
          updateMessage(progressMessageId, { text: '本轮 Agent 处理失败。' })
        } else if (event === 'done') {
          if (data?.status === 'completed') {
            pendingInterrupt.value = null
            recoveryRequestId.value = ''
            runtimeNotice.value = null
            if (!replayed) updateMessage(progressMessageId, { text: '本轮 Agent 解析完成。' })
            if (submittedPlaceRef && pendingPlaceSelectionRef.value === submittedPlaceRef) {
              pendingPlaceSelectionRef.value = ''
              pendingPlaceSelection.value = null
            }
          }
          listingSearchRunning.value = false
        }
      }
    })
  } catch (error) {
    if (!viewGuard.isCurrent(viewVersion)) return
    if (error?.name === 'AbortError') {
      runtimeNotice.value = { tone: 'info', message: '已停止本轮生成；若仍有未完成步骤，可以恢复上次任务。' }
      if (chatAbortController.value === controller) {
        for (const toolId of activeToolIds) {
          const tool = toolMessageIds.get(toolId)
          if (tool) updateMessage(tool.messageId, { text: `${tool.label}已停止。` })
        }
        updateMessage(progressMessageId, { text: '已停止本轮 Agent 生成。' })
      }
      return
    }
    if (error?.status) {
      runtimeNotice.value = { tone: 'error', message: error.message || '本轮请求未能开始。' }
      updateMessage(progressMessageId, { text: '本轮请求未能开始。' })
    } else if (!streamStarted.value) {
      updateMessage(progressMessageId, { text: '后端暂未连接。' })
      runtimeNotice.value = { tone: 'error', message: '后端暂未连接，本轮没有运行找房 Agent。' }
    } else {
      runtimeNotice.value = { tone: 'error', message: '与后端的流式连接中断，正在读取后端已保存的状态。' }
      for (const toolId of activeToolIds) {
        const tool = toolMessageIds.get(toolId)
        if (tool) updateMessage(tool.messageId, { text: `${tool.label}未完成。` })
      }
      updateMessage(progressMessageId, { text: '流式连接意外中断。' })
    }
  } finally {
    if (chatAbortController.value === controller && viewGuard.isCurrent(viewVersion)) {
      chatAbortController.value = null
      searching.value = false
      listingSearchRunning.value = false
      // 出错或取消后也读取已保存状态；绝不自动重试工具。
      const state = await refreshCurrentSession()
      if (state && ['completed', 'interrupted'].includes(state.status)) {
        scheduleTitleRefresh(sessionId)
      }
    }
  }
}
function cancelChat() {
  chatAbortController.value?.abort()
}
function selectListing(id) { selectedListingId.value = id }
function openDetail(item) { selectedListingId.value = item.id }
function openSource(item) { const url = item.detailUrl || item.sourceUrl; if (url) window.open(url, '_blank', 'noopener,noreferrer'); else window.alert('这条候选没有可打开的公开平台链接。') }
function toggleSaved(item) { item.saved = !item.saved }
function centerMap() { selectedListingId.value = null }
function startVerification(platformKey) { const platform = platforms.value.find(item => item.key === platformKey); if (!platform) return; verification.value = { platformKey, platformName: platform.name }; platform.status = 'blocked'; platform.label = '等待验证' }
async function loadHistory(item) {
  if (backendChatEnabled) {
    cancelTitleRefresh()
    resetSearch()
    activeHistory.value = item.id
    rememberActiveSession(item.id)
    await refreshCurrentSession()
    return
  }
  runtimeNotice.value = { tone: 'error', message: '后端聊天当前未启用，无法读取 Agent 会话。' }
}
async function startTitleEdit(item = activeConversation.value) {
  if (!item || searching.value || historyLoading.value) return
  if (item.id !== activeHistory.value) await loadHistory(item)
  if (item.id !== activeHistory.value) return
  titleDraft.value = item.title; editingTitle.value = true; nextTick(() => titleInput.value?.focus())
}
async function saveTitle() {
  const value = titleDraft.value.trim()
  const item = activeConversation.value
  if (!item || !value) return
  try {
    if (backendChatEnabled) await renameSession(item.id, value)
    item.title = value
    editingTitle.value = false
  } catch (error) {
    if (activeHistory.value === item.id) {
      runtimeNotice.value = { tone: 'error', message: error.message || '标题保存失败。' }
    }
  }
}
function cancelTitle() { editingTitle.value = false }

async function deleteActiveSession() {
  const sessionId = activeHistory.value
  if (!sessionId || searching.value || privacyBusy.value || !backendChatEnabled) return
  if (!globalThis.confirm?.('将删除聊天记录、房源结果和 Agent 执行状态，无法恢复。确定继续吗？')) return
  privacyBusy.value = 'session'
  try {
    await deleteSession(sessionId)
    conversations.value = conversations.value.filter(item => item.id !== sessionId)
    resetSearch()
    runtimeNotice.value = { tone: 'info', message: '当前会话已删除，聊天记录、房源结果和执行状态均已清理。' }
  } catch (error) {
    runtimeNotice.value = { tone: 'error', message: error.message || '当前会话删除失败。' }
  } finally {
    privacyBusy.value = ''
  }
}

async function clearPlatformVerification() {
  if (privacyBusy.value || !backendChatEnabled) return
  if (!globalThis.confirm?.('将清除 58 同城、安居客和房天下的本地验证状态；聊天记录和房源结果不会删除。确定继续吗？')) return
  privacyBusy.value = 'platform'
  try {
    await clearPlatformSessions()
    runtimeNotice.value = { tone: 'info', message: '平台验证数据已清除，下次访问受限页面时可能需要重新验证。' }
  } catch (error) {
    runtimeNotice.value = { tone: 'error', message: error.message || '平台验证数据清理失败。' }
  } finally {
    privacyBusy.value = ''
  }
}

async function clearLocalArtifacts() {
  if (privacyBusy.value || !backendChatEnabled) return
  if (!globalThis.confirm?.('将删除本地抓取产生的 HTML、JSON 和快照文件；聊天记录和结构化房源结果不会删除。确定继续吗？')) return
  privacyBusy.value = 'artifacts'
  try {
    await clearArtifacts()
    runtimeNotice.value = { tone: 'info', message: '本地抓取产物已清除，当前会话和房源展示未受影响。' }
  } catch (error) {
    runtimeNotice.value = { tone: 'error', message: error.message || '本地抓取产物清理失败。' }
  } finally {
    privacyBusy.value = ''
  }
}

function resetSearch() {
  cancelTitleRefresh()
  viewGuard.advance()
  historyController?.abort()
  historyController = null
  historyLoading.value = false
  pendingInterrupt.value = null
  recoveryRequestId.value = ''
  sessionReadError.value = ''
  runtimeNotice.value = null
  editingTitle.value = false
  rememberActiveSession('')
  const controller = chatAbortController.value
  chatAbortController.value = null
  controller?.abort()
  activeHistory.value = ''
  searchActive.value = false
  listingSearchRunning.value = false
  searching.value = false
  selectedListingId.value = null
  resetListingDisplayLimit()
  listings.value = []
  searchSummary.value = null
  placeCandidates.value = []
  selectedPlaceRef.value = ''
  confirmedPlaceRef.value = ''
  pendingPlaceSelectionRef.value = ''
  pendingPlaceSelection.value = null
  locationResolution.value = null
  chatDraft.value = ''
  verification.value = null
  platforms.value = defaultPlatformStatuses()
  target.value = {
    ...target.value,
    name: '',
    address: '输入地点后选择一个定位结果',
    city: '',
    lng: null,
    lat: null,
    candidate_ref: '',
    confidence: '待确认'
  }
  messages.value = [{
    id: `${Date.now()}-new`,
    role: 'assistant',
    text: '新的找房会话已准备好。你可以直接说：“我在某某公司上班，预算 2000 元，想找整租。”'
  }]
}

function rememberActiveSession(id) {
  try {
    if (id) localStorage.setItem('rental-active-session', id)
    else localStorage.removeItem('rental-active-session')
  } catch { /* 浏览器存储是可选的，以后端数据为准。 */ }
}

async function refreshCurrentSession() {
  const sessionId = activeHistory.value
  if (!backendChatEnabled || !sessionId || searching.value) return
  const version = viewGuard.capture()
  historyController?.abort()
  const controller = new AbortController()
  historyController = controller
  historyLoading.value = true
  try {
    const state = await getSession(sessionId, { signal: controller.signal })
    if (!viewGuard.isCurrent(version) || historyController !== controller) return
    messages.value = state.messages
    pendingInterrupt.value = state.pending
    recoveryRequestId.value = state.recovery_request_id || ''
    storageMode.value = state.storage?.mode || ''
    resetListingDisplayLimit()
    listings.value = normalizeListings(state, listings.value)
    platforms.value = Array.isArray(state.platforms) && state.platforms.length
      ? normalizePlatformStatuses(state, platforms.value)
      : defaultPlatformStatuses()
    searchSummary.value = mergeSearchSummary(null, state.search)
    searchActive.value = listings.value.length > 0 || Boolean(state.search)
    if (state.location) receiveLocationCandidates(state.location)
    if (state.map?.target && typeof state.map.target === 'object') {
      receiveListingEnrichment({
        target: state.map.target,
        listings: state.listings,
        status: state.map.status,
        criteria: state.search?.criteria
      })
    }
    if (activeConversation.value) activeConversation.value.title = state.title
    if (state.status === 'completed') {
      pendingInterrupt.value = null
      recoveryRequestId.value = ''
      sessionReadError.value = ''
      runtimeNotice.value = null
    } else if (state.status === 'interrupted') {
      recoveryRequestId.value = ''
      sessionReadError.value = ''
      runtimeNotice.value = null
    } else {
      sessionReadError.value = state.status === 'running'
        ? '该会话仍在后端执行，请稍后刷新状态。'
        : ''
    }
    return state
  } catch (error) {
    if (!viewGuard.isCurrent(version) || historyController !== controller || error.name === 'AbortError') return
    if (error.status === 404) {
      pendingInterrupt.value = null
      recoveryRequestId.value = ''
      sessionReadError.value = ''
      runtimeNotice.value = { tone: 'error', message: '后端没有这次会话的记录。若刚重启过内存模式，请重新发送需求。' }
    } else {
      sessionReadError.value = error.message || '暂时无法读取会话，请刷新后继续。'
    }
  } finally {
    if (viewGuard.isCurrent(version) && historyController === controller) {
      historyLoading.value = false
      historyController = null
    }
  }
}

onMounted(async () => {
  if (!backendChatEnabled) return
  let remembered = ''
  try { remembered = localStorage.getItem('rental-active-session') || '' } catch { /* 浏览器存储不可用时忽略。 */ }
  conversations.value = []
  resetSearch()
  const version = viewGuard.capture()
  historyLoading.value = true
  try {
    const result = await listSessions()
    if (!viewGuard.isCurrent(version)) return
    storageMode.value = result.storage?.mode || ''
    conversations.value = result.sessions.map(item => ({ id: item.session_id, title: item.title }))
    const selected = conversations.value.find(item => item.id === remembered) || conversations.value[0]
    if (selected) await loadHistory(selected)
  } catch {
    if (viewGuard.isCurrent(version)) {
      runtimeNotice.value = { tone: 'error', message: '尚未连接后端会话存储。启动后端后即可新建或继续会话。' }
    }
  } finally {
    if (viewGuard.isCurrent(version)) historyLoading.value = false
  }
})

onBeforeUnmount(() => {
  cancelTitleRefresh()
  viewGuard.advance()
  chatAbortController.value?.abort()
  historyController?.abort()
})
</script>

<style scoped>
.session-notice {
  margin: 0 24px 10px;
  padding: 10px 14px;
  border-radius: 10px;
  background: #f2f6f3;
  color: #465e50;
  font-size: 13px;
}
.session-notice.is-error {
  border: 1px solid #e4c2b6;
  color: #825345;
  background: #fff7f3;
}
.session-notice button {
  margin-left: 10px;
  border: 1px solid #b4c9bc;
  border-radius: 6px;
  padding: 4px 8px;
  background: white;
  cursor: pointer;
}
</style>
