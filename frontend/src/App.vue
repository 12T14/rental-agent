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
        <button v-for="item in savedListings" :key="item.id" class="saved-item" type="button" @click="selectListing(item.id)"><span class="saved-heart">♥</span><span>{{ item.community }}</span><small>¥{{ item.rent }}/月</small></button>
        <div v-if="!savedListings.length" class="saved-empty"><span class="saved-icon">♡</span><span>在房源卡片上收藏喜欢的房子</span></div>
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
          <div v-if="historyLoading || sessionReadError || recoveryRequestId || pendingInterrupt" class="session-notice" role="status">
            <template v-if="historyLoading">正在读取会话…</template>
            <template v-else-if="sessionReadError">
              {{ sessionReadError }}
              <button type="button" @click="refreshCurrentSession">刷新会话</button>
            </template>
            <template v-else-if="recoveryRequestId">
              上次任务尚未完成。可以恢复执行，也可以新建会话；未完成的工具步骤可能重试。
              <button type="button" :disabled="searching" @click="handleChatMessage('', { recover: true })">恢复上次任务</button>
            </template>
            <template v-else>助手正在等待你补充条件，直接在下方回复即可。</template>
          </div>
          <ChatPanel
            ref="chatPanelRef"
            v-model="chatDraft"
            :messages="messages"
            :listings="filteredListings"
            :platforms="platforms"
            :selected-id="selectedListingId"
            :searching="searching"
            :send-disabled="historyLoading || Boolean(sessionReadError) || Boolean(recoveryRequestId)"
            :verification="verification"
            @send="handleChatMessage"
            @cancel="cancelChat"
            @select-listing="selectListing"
            @open-detail="openDetail"
            @toggle-save="toggleSaved"
            @verify="startVerification"
          />
        </section>

        <aside class="context-column">
          <div class="context-heading"><div><h2>小地图</h2><p>查看目标地点和房源位置</p></div><span class="context-live"><i></i>在线</span></div>
          <MapPanel
            compact
            :target="target"
            :listings="filteredListings"
            :place-candidates="placeCandidates"
            :selected-id="selectedListingId"
            :selected-place-ref="selectedPlaceRef"
            :confirmed-place-ref="confirmedPlaceRef"
            :radius-km="criteria.radiusKm"
            :busy="searching || historyLoading"
            @select="selectListing"
            @select-place="selectPlaceCandidate"
            @confirm-place="confirmPlaceCandidate"
            @center="centerMap"
          />
          <div v-if="selectedListing" class="selected-summary"><div class="summary-heading"><span>当前选中</span><button type="button" @click="selectedListingId = null">×</button></div><strong>{{ selectedListing.community }}</strong><p>¥{{ selectedListing.rent }}/月 · {{ selectedListing.room }}</p><button type="button" @click="openDetail(selectedListing)">查看房源概览 <span>→</span></button></div>
        </aside>
      </section>
    </main>

    <Transition name="drawer">
      <aside v-if="selectedListing" class="detail-drawer">
        <div class="drawer-backdrop" @click="selectedListingId = null"></div>
        <div class="drawer-panel">
          <div class="drawer-header"><div><span class="drawer-kicker">房源概览</span><h2>{{ selectedListing.title }}</h2></div><button class="close-button" type="button" @click="selectedListingId = null">×</button></div>
          <div class="drawer-price"><b>¥{{ selectedListing.rent }}</b><span>/月</span><span class="verified-chip">公开候选</span></div>
          <div class="drawer-grid"><div><span>户型</span><strong>{{ selectedListing.room }}</strong></div><div><span>面积</span><strong>{{ selectedListing.area }}㎡</strong></div><div><span>距离目标</span><strong>{{ selectedListing.distance }} km</strong></div><div><span>通勤参考</span><strong>{{ selectedListing.commute }}</strong></div></div>
          <div class="drawer-block"><span class="drawer-label">位置</span><p>{{ selectedListing.address }}</p><small v-if="selectedListing.locationStatus === 'unverified'" class="warning-text">位置尚未通过地理编码核验</small></div>
          <div class="drawer-block"><span class="drawer-label">来源与状态</span><div class="source-row"><span class="source-dot" :class="selectedListing.platformKey"></span>{{ selectedListing.platform }} · {{ selectedListing.updated }}</div></div>
          <div class="drawer-tags"><span v-for="tag in selectedListing.tags" :key="tag">{{ tag }}</span></div>
          <div class="drawer-actions"><button class="primary-action" type="button" @click="openSource(selectedListing)">打开原平台 <span>↗</span></button><button class="secondary-action" type="button" @click="toggleSaved(selectedListing)">{{ selectedListing.saved ? '已收藏' : '收藏房源' }} <span>{{ selectedListing.saved ? '♥' : '♡' }}</span></button></div>
          <p class="drawer-note">公开列表候选不等于当前可租，请打开原平台核验详情、费用和联系方式。</p>
        </div>
      </aside>
    </Transition>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onBeforeUnmount, reactive, ref } from 'vue'
import ChatPanel from './components/ChatPanel.vue'
import MapPanel from './components/MapPanel.vue'
import { canSubmitChatMessage, streamChat, streamResume, streamRecover, listSessions, getSession, renameSession } from './services/chatApi'
import { createViewGuard, sessionAction } from './services/sessionState.js'
import {
  buildSearchContext,
  candidateArea,
  draftKeepsPlaceSelection,
  isResolvedLocationPayload,
  mergePlaceIntoDraft,
  normalizeLocationCandidates
} from './services/placeCandidates.js'

const defaultCriteria = { city: '', targetPlace: '', radiusKm: 3, maxRent: null, rentType: '不限', commuteMode: '公交', maxCommute: 45 }
const criteria = reactive({ ...defaultCriteria })
const target = ref({ name: '', address: '输入地点后选择一个定位结果', city: '', lng: null, lat: null, confidence: '待确认' })
const placeSuggestions = []
const listings = ref([])
const platforms = ref([
  { key: 'fifty-eight', name: '58同城', status: 'idle', label: '等待搜索', count: 0 },
  { key: 'anjuke', name: '安居客', status: 'idle', label: '等待搜索', count: 0 },
  { key: 'fang', name: '房天下', status: 'idle', label: '等待搜索', count: 0 }
])
const conversations = ref([])
const messages = ref([
  { id: 'welcome', role: 'assistant', text: '你好，我是栖居找房助手。你可以直接告诉我工作地点、预算和通勤要求，我会帮你整理附近房源。' }
])
const activeHistory = ref('')
const selectedListingId = ref(null)
const placeCandidates = ref([])
const selectedPlaceRef = ref('')
const confirmedPlaceRef = ref('')
const pendingConfirmedPlaceRef = ref('')
const pendingConfirmedPlace = ref(null)
const chatDraft = ref('')
const chatPanelRef = ref(null)
const searching = ref(false)
const searchActive = ref(false)
const verification = ref(null)
const chatAbortController = ref(null)
const backendChatEnabled = import.meta.env.VITE_USE_BACKEND_CHAT !== 'false'
const pendingInterrupt = ref(null)
const recoveryRequestId = ref('')
const historyLoading = ref(false)
const sessionReadError = ref('')
const storageMode = ref('')
const storageLabel = computed(() => !backendChatEnabled ? '仅使用本地离线数据'
  : storageMode.value === 'mongodb' ? 'MongoDB 会话持久化'
    : storageMode.value === 'memory' ? '内存存储：后端重启会丢失会话' : '尚未连接会话存储')
const viewGuard = createViewGuard()
let historyController = null
const editingTitle = ref(false)
const titleDraft = ref('')
const titleInput = ref(null)
let searchVersion = 0
const selectedListing = computed(() => listings.value.find(item => item.id === selectedListingId.value))
const savedListings = computed(() => listings.value.filter(item => item.saved))
const activeConversation = computed(() => conversations.value.find(item => item.id === activeHistory.value))
const sessionTitle = computed(() => activeConversation.value?.title || '新建会话')
const confirmedPlace = computed(() => (
  placeCandidates.value.find(item => item.candidate_ref === confirmedPlaceRef.value) || null
))
const filteredListings = computed(() => {
  if (!searchActive.value || !criteria.targetPlace.trim()) return []
  return listings.value.filter(item => {
    const shared = /合租|次卧|单间/.test(`${item.room}${item.title}`)
    const typeMatches = criteria.rentType === '不限' || (criteria.rentType === '合租' ? shared : !shared)
    const budgetMatches = !criteria.maxRent || item.rent <= criteria.maxRent
    const locationMatches = item.locationStatus === 'unverified' || Number(item.distance) <= Number(criteria.radiusKm)
    const commuteMatches = item.locationStatus === 'unverified' || item.commuteValue <= Number(criteria.maxCommute)
    return typeMatches && budgetMatches && locationMatches && commuteMatches
  })
})

function addMessage(message) {
  const id = `${Date.now()}-${Math.random()}`
  messages.value.push({ id, role: 'assistant', ...message })
  return id
}
function updateMessage(id, patch) {
  const message = messages.value.find(item => item.id === id)
  if (message) Object.assign(message, patch)
}
const toolLabels = {
  resolve_target_place: '解析目标地点',
  search_rental_candidates: '搜索公开房源',
  batch_fetch_listing_details: '读取房源详情',
  human_verify_rental_platform: '等待平台人工验证',
  playwright_browser: '查看公开网页',
  request_rental_preferences: '等待补充找房条件'
}
function toolLabel(name) {
  return toolLabels[name] || '处理找房任务'
}
function parseCriteria(text) {
  const rent = text.match(/(\d{3,5})\s*(?:元|块)/); const radius = text.match(/(\d+(?:\.\d+)?)\s*(?:公里|km)/i); const commute = text.match(/(\d+)\s*分钟/)
  if (rent) criteria.maxRent = Number(rent[1]); if (radius) criteria.radiusKm = Number(radius[1]); if (commute) criteria.maxCommute = Number(commute[1])
  if (/合租/.test(text)) criteria.rentType = '合租'; else if (/整租/.test(text)) criteria.rentType = '整租'
  if (/驾车|开车/.test(text)) criteria.commuteMode = '驾车'; else if (/骑行|骑车/.test(text)) criteria.commuteMode = '骑行'
  const place = placeSuggestions.find(item => text.includes(item.name)); if (place) { criteria.targetPlace = place.name; criteria.city = place.city; target.value = { ...target.value, name: place.name, address: place.address, city: place.city, lng: place.lng, lat: place.lat, confidence: '高置信度' } }
}
function makeTitle(text) {
  const cleaned = text.replace(/[“”"。！？!?，,、]/g, ' ').replace(/\s+/g, ' ').trim()
  if (!cleaned) return '新建会话'
  return cleaned.length > 18 ? `${cleaned.slice(0, 18)}…` : cleaned
}
function ensureConversation(text) {
  if (!activeHistory.value) {
    const id = `h-${requestId()}`
    conversations.value.unshift({ id, title: makeTitle(text), query: { ...criteria } })
    activeHistory.value = id
    rememberActiveSession(id)
  } else if (activeConversation.value && activeConversation.value.title === '新建会话') {
    activeConversation.value.title = makeTitle(text)
  }
}
function requestId() {
  return globalThis.crypto?.randomUUID?.() || `req-${Date.now()}-${Math.random().toString(16).slice(2)}`
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
  placeCandidates.value = nextCandidates
  if (!nextCandidates.length) {
    selectedPlaceRef.value = ''
    confirmedPlaceRef.value = ''
    const unresolvedQuery = typeof payload?.query === 'string' ? payload.query.trim() : ''
    const cityHint = typeof payload?.city_hint === 'string' ? payload.city_hint.trim() : ''
    target.value = {
      ...target.value,
      name: unresolvedQuery || target.value.name,
      address: payload?.status === 'not_configured'
        ? '地图未配置，仍可继续按文字条件找房'
        : '没有可定位的坐标，请补充城市或更具体的地址',
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
    criteria.targetPlace = resolvedCandidate.name
    if (resolvedCandidate.city) criteria.city = resolvedCandidate.city.replace(/市$/, '')
    if (activeConversation.value) activeConversation.value.query = { ...criteria }
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
    resolvedCandidate ? '已定位' : (confirmedCandidate ? '已确认' : '待用户确认')
  )
}

function findPlaceCandidate(candidate) {
  return placeCandidates.value.find(item => item.candidate_ref === candidate?.candidate_ref) || null
}

function selectPlaceCandidate(candidate) {
  const safeCandidate = findPlaceCandidate(candidate)
  if (!safeCandidate) return
  selectedPlaceRef.value = safeCandidate.candidate_ref
  applyPlacePreview(
    safeCandidate,
    safeCandidate.candidate_ref === confirmedPlaceRef.value ? '已确认' : '待用户确认'
  )
}

function confirmPlaceCandidate(candidate) {
  const safeCandidate = findPlaceCandidate(candidate)
  if (!safeCandidate || searching.value) return
  const previousCandidate = confirmedPlace.value
  chatDraft.value = mergePlaceIntoDraft(chatDraft.value, safeCandidate, previousCandidate)
  selectedPlaceRef.value = safeCandidate.candidate_ref
  confirmedPlaceRef.value = safeCandidate.candidate_ref
  pendingConfirmedPlaceRef.value = safeCandidate.candidate_ref
  pendingConfirmedPlace.value = safeCandidate
  criteria.targetPlace = safeCandidate.name
  if (safeCandidate.city) criteria.city = safeCandidate.city.replace(/市$/, '')
  applyPlacePreview(safeCandidate, '已确认')
  if (activeConversation.value) activeConversation.value.query = { ...criteria }
  nextTick(() => chatPanelRef.value?.focusComposer())
}

async function handleChatMessage(text, { recover = false } = {}) {
  if (searching.value || historyLoading.value || sessionReadError.value) return
  if (recover ? !recoveryRequestId.value : !canSubmitChatMessage(text, Boolean(recoveryRequestId.value))) return
  text = text.trim()
  const pendingPlaceAtSubmit = pendingConfirmedPlace.value?.candidate_ref === pendingConfirmedPlaceRef.value
    ? pendingConfirmedPlace.value
    : null
  const submittedPlace = draftKeepsPlaceSelection(text, pendingPlaceAtSubmit)
    ? pendingPlaceAtSubmit
    : null
  const submittedPlaceRef = submittedPlace?.candidate_ref || ''
  if (pendingConfirmedPlaceRef.value && !submittedPlace) {
    const staleReference = pendingConfirmedPlaceRef.value
    pendingConfirmedPlaceRef.value = ''
    pendingConfirmedPlace.value = null
    if (confirmedPlaceRef.value === staleReference) confirmedPlaceRef.value = ''
    if (target.value.candidate_ref === staleReference) {
      target.value = { ...target.value, confidence: '待核验' }
    }
  }
  if (!recover) {
    ensureConversation(text)
    messages.value.push({ id: `${Date.now()}-user`, role: 'user', text })
    parseCriteria(text)
  }
  if (!backendChatEnabled && /详情|怎么联系|联系方式|这套/.test(text) && selectedListing.value) {
    addMessage({ text: '我已经把这套房源标记在右侧地图上了。联系方式和最新费用需要打开原平台页面核验。' })
    return
  }

  if (!backendChatEnabled) { runSearch(); return }

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
  let fallbackStarted = false
  let turnFailed = false
  let paused = false
  let turnFeedback = ''
  searchActive.value = false
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
      searchContext: buildSearchContext(submittedPlace),
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
          toolMessageIds.set(data.tool_call_id, { messageId, label })
          activeToolIds.add(data.tool_call_id)
        } else if (event === 'tool_end' && data?.tool_call_id) {
          const tool = toolMessageIds.get(data.tool_call_id)
          if (tool) {
            updateMessage(tool.messageId, {
              text: data.status === 'error' ? `${tool.label}失败。` : `${tool.label}完成。`
            })
          }
          activeToolIds.delete(data.tool_call_id)
      // 工具结果之后生成的文本属于下一条助手消息。
          streamedMessageId = ''
        } else if (event === 'location_candidates') {
          receiveLocationCandidates(data)
        } else if (event === 'assistant' && data?.text) {
          if (streamedMessageId) updateMessage(streamedMessageId, { text: data.text })
          else streamedMessageId = addMessage({ text: data.text })
        } else if (event === 'interrupt' && data?.interrupt_id) {
          paused = true
          pendingInterrupt.value = data
          recoveryRequestId.value = ''
          updateMessage(progressMessageId, { text: '已保存进度，等待你补充条件。' })
          for (const toolId of activeToolIds) {
            const tool = toolMessageIds.get(toolId)
            if (tool) updateMessage(tool.messageId, { text: `${tool.label}：已暂停。` })
          }
          activeToolIds.clear()
          addMessage({ text: data.message })
        } else if (event === 'error') {
          turnFailed = true
          const message = data?.error?.message || 'Agent 暂时无法处理这条消息。'
          turnFeedback = message
          for (const toolId of activeToolIds) {
            const tool = toolMessageIds.get(toolId)
            if (tool) updateMessage(tool.messageId, { text: `${tool.label}未完成。` })
          }
          activeToolIds.clear()
          updateMessage(progressMessageId, { text: '本轮 Agent 处理失败。' })
          addMessage({ text: message })
        } else if (event === 'done') {
          if (data?.status === 'completed') {
            pendingInterrupt.value = null
            recoveryRequestId.value = ''
            if (!replayed) updateMessage(progressMessageId, { text: '本轮 Agent 解析完成。' })
            if (!turnFailed && pendingConfirmedPlaceRef.value === submittedPlaceRef) {
              pendingConfirmedPlaceRef.value = ''
              pendingConfirmedPlace.value = null
            }
          }
          if (data?.status === 'interrupted') paused = true
        }
      }
    })
  } catch (error) {
    if (!viewGuard.isCurrent(viewVersion)) return
    if (error?.name === 'AbortError') {
      turnFeedback = '已停止本轮生成；若仍有未完成步骤，可以恢复上次任务。'
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
      turnFeedback = error.message || '本轮请求未能开始。'
      updateMessage(progressMessageId, { text: '本轮请求未能开始。' })
      addMessage({ text: error.message || '请求参数有误，请稍后重试。' })
    } else if (!streamStarted.value && action === 'chat') {
      updateMessage(progressMessageId, { text: '后端暂未连接。' })
      addMessage({ kind: 'progress', text: '后端暂未连接，先展示本地离线示例。' })
      fallbackStarted = true
      runSearch()
    } else {
      turnFeedback = '流式连接中断，以下为后端已保存的进度。'
      for (const toolId of activeToolIds) {
        const tool = toolMessageIds.get(toolId)
        if (tool) updateMessage(tool.messageId, { text: `${tool.label}未完成。` })
      }
      updateMessage(progressMessageId, { text: '流式连接意外中断。' })
      addMessage({ text: '与后端的流式连接中断，请稍后重试。' })
    }
  } finally {
    if (chatAbortController.value === controller && viewGuard.isCurrent(viewVersion)) {
      chatAbortController.value = null
      if (!fallbackStarted) searching.value = false
      if (paused && pendingConfirmedPlaceRef.value === submittedPlaceRef) {
        pendingConfirmedPlaceRef.value = ''
        pendingConfirmedPlace.value = null
      }
      // 出错或取消后也读取已保存状态；绝不自动重试工具。
      if (!fallbackStarted) {
        const state = await refreshCurrentSession({ feedback: turnFeedback })
        if (viewGuard.isCurrent(viewVersion) && state?.latest_request_id === submittedRequestId
            && ['completed', 'interrupted'].includes(state.status)
            && pendingConfirmedPlaceRef.value === submittedPlaceRef) {
          pendingConfirmedPlaceRef.value = ''
          pendingConfirmedPlace.value = null
        }
      }
    }
  }
}
function cancelChat() {
  chatAbortController.value?.abort()
}
function runSearch() {
  if (!criteria.targetPlace.trim()) { addMessage({ text: '请先告诉我一个公司、学校或地标名称，我才能开始定位。' }); return }
  const version = ++searchVersion; searchActive.value = true; searching.value = true; selectedListingId.value = null; verification.value = null
  const hasConfirmedTarget = Boolean(confirmedPlaceRef.value && target.value.candidate_ref === confirmedPlaceRef.value)
  if (activeConversation.value) activeConversation.value.query = { ...criteria }
  platforms.value = platforms.value.map(item => ({ ...item, status: 'loading', label: '抓取中' })); if (!hasConfirmedTarget) target.value = { ...target.value, name: criteria.targetPlace, address: `${criteria.city} · 正在解析地点地址`, city: criteria.city, confidence: '解析中' }
  messages.value.push({ id: `${Date.now()}-activity`, role: 'assistant', kind: 'activity', text: '我正在按条件读取公开房源列表。' })
  window.setTimeout(() => { if (version !== searchVersion) return; searching.value = false; const match = placeSuggestions.find(item => item.name === criteria.targetPlace); if (hasConfirmedTarget) target.value = { ...target.value, confidence: '已确认' }; else target.value = { ...target.value, address: match?.address || `${criteria.city} · ${criteria.targetPlace}`, city: match?.city || criteria.city, lng: match?.lng ?? target.value.lng, lat: match?.lat ?? target.value.lat, confidence: match ? '高置信度' : '待核验' }; platforms.value = platforms.value.map(item => ({ ...item, status: item.key === 'fang' ? 'partial' : 'ok', label: item.key === 'fang' ? '列表候选' : '已获取', count: item.key === 'fang' ? 1 : 2 })); addMessage({ kind: 'result', text: `目标地点已确认。我按“${criteria.rentType}、月租 ${criteria.maxRent ? `不超过 ${criteria.maxRent} 元` : '不限'}”整理了下面的候选。` }) }, 900)
}
function selectListing(id) { selectedListingId.value = id }
function openDetail(item) { selectedListingId.value = item.id }
function openSource(item) { if (item.detailUrl) window.open(item.detailUrl, '_blank', 'noopener,noreferrer'); else window.alert('本地离线示例未配置详情链接；联网结果会跳转到原平台页面。') }
function toggleSaved(item) { item.saved = !item.saved }
function centerMap() { selectedListingId.value = null }
function startVerification(platformKey) { const platform = platforms.value.find(item => item.key === platformKey); if (!platform) return; verification.value = { platformKey, platformName: platform.name }; platform.status = 'blocked'; platform.label = '等待验证' }
async function loadHistory(item) {
  if (backendChatEnabled) {
    resetSearch()
    activeHistory.value = item.id
    rememberActiveSession(item.id)
    await refreshCurrentSession()
    return
  }
  activeHistory.value = item.id; Object.assign(criteria, item.query); const place = placeSuggestions.find(value => value.name === item.query.targetPlace); placeCandidates.value = []; selectedPlaceRef.value = ''; confirmedPlaceRef.value = ''; pendingConfirmedPlaceRef.value = ''; pendingConfirmedPlace.value = null; chatDraft.value = ''; target.value = { ...target.value, name: item.query.targetPlace, address: place?.address || `${item.query.city} · ${item.query.targetPlace}`, city: place?.city || item.query.city, lng: place?.lng ?? null, lat: place?.lat ?? null, candidate_ref: '', confidence: place ? '高置信度' : '待核验' }; searchActive.value = true; selectedListingId.value = null; messages.value = [{ id: `${Date.now()}-history`, role: 'assistant', text: `已打开“${item.title}”这次会话。你可以继续追问，或者直接提出新的找房要求。` }, { id: `${Date.now()}-history-result`, role: 'assistant', kind: 'result', text: '这是该会话保存的本地离线候选快照。' }]
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
    if (activeHistory.value === item.id) addMessage({ text: error.message || '标题保存失败。' })
  }
}
function cancelTitle() { editingTitle.value = false }
function resetSearch() {
  viewGuard.advance()
  historyController?.abort()
  historyController = null
  historyLoading.value = false
  pendingInterrupt.value = null
  recoveryRequestId.value = ''
  sessionReadError.value = ''
  editingTitle.value = false
  rememberActiveSession('')
  const controller = chatAbortController.value; chatAbortController.value = null; controller?.abort(); searchVersion += 1; Object.assign(criteria, defaultCriteria); activeHistory.value = ''; searchActive.value = false; searching.value = false; selectedListingId.value = null; placeCandidates.value = []; selectedPlaceRef.value = ''; confirmedPlaceRef.value = ''; pendingConfirmedPlaceRef.value = ''; pendingConfirmedPlace.value = null; chatDraft.value = ''; verification.value = null; platforms.value = platforms.value.map(item => ({ ...item, status: 'idle', label: '等待搜索', count: 0 })); target.value = { ...target.value, name: '', address: '输入地点后选择一个定位结果', city: '', lng: null, lat: null, candidate_ref: '', confidence: '待确认' }; messages.value = [{ id: `${Date.now()}-new`, role: 'assistant', text: '新的找房会话已准备好。你可以直接说：“我在某某公司上班，预算 2000 元，想找整租。”' }]
}

function rememberActiveSession(id) {
  try {
    if (id) localStorage.setItem('rental-active-session', id)
    else localStorage.removeItem('rental-active-session')
  } catch { /* 浏览器存储是可选的，以后端数据为准。 */ }
}

async function refreshCurrentSession({ feedback = '' } = {}) {
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
    if (state.location) receiveLocationCandidates(state.location)
    if (activeConversation.value) activeConversation.value.title = state.title
    sessionReadError.value = state.status === 'running'
      ? '该会话仍在后端执行，请稍后刷新状态。'
      : ''
    if (feedback) addMessage({ kind: 'progress', text: feedback })
    return state
  } catch (error) {
    if (!viewGuard.isCurrent(version) || historyController !== controller || error.name === 'AbortError') return
    if (error.status === 404) {
      pendingInterrupt.value = null
      recoveryRequestId.value = ''
      sessionReadError.value = ''
      addMessage({ text: '后端没有这次会话的记录。若刚重启过内存模式，请重新发送需求。' })
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
    if (viewGuard.isCurrent(version)) addMessage({ text: '尚未连接后端会话存储。可以启动后端后新建会话；目前没有加载本地离线历史。' })
  } finally {
    if (viewGuard.isCurrent(version)) historyLoading.value = false
  }
})

onBeforeUnmount(() => {
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
.session-notice button {
  margin-left: 10px;
  border: 1px solid #b4c9bc;
  border-radius: 6px;
  padding: 4px 8px;
  background: white;
  cursor: pointer;
}
</style>
