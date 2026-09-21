<template>
  <section class="panel listing-panel">
    <div class="listing-heading">
      <div>
        <span class="panel-kicker">房源清单</span>
        <h2>{{ targetLocated ? '附近房源' : '房源候选' }} <span v-if="!searching">{{ listings.length }}</span></h2>
        <small v-if="summaryLabel" class="listing-summary">{{ summaryLabel }}</small>
      </div>
      <div class="listing-controls">
        <select v-model="sortBy" aria-label="排序方式">
          <option value="recommended">推荐排序</option>
          <option value="rent">租金最低</option>
          <option value="distance">距离最近</option>
          <option value="updated">最新发布</option>
        </select>
        <button class="filter-button" type="button" title="筛选" @click="filtersOpen = !filtersOpen">☷</button>
      </div>
    </div>

    <div v-if="filtersOpen" class="quick-filters">
      <button v-for="label in ['已核验位置', '独立卫浴', '可短租']" :key="label" type="button" :class="{ active: activeFilters.includes(label) }" @click="toggleFilter(label)">{{ label }}</button>
    </div>

    <div v-if="searching" class="listing-loading">
      <div v-for="n in 3" :key="n" class="skeleton-card"><span></span><div><i></i><i></i><i></i></div></div>
      <p>助手正在汇总不同平台的公开候选…</p>
    </div>

    <div v-else-if="sortedListings.length" class="listing-scroll">
        <article
        v-for="listing in sortedListings"
        :key="listing.id"
        class="listing-card"
        :class="{
          selected: selectedId === listing.id,
          'agent-recommended': isAgentRecommended(listing),
          qualified: isFilterPassed(listing) && !isAgentRecommended(listing),
          'detail-pending': isFilterPassed(listing) && listing.detailStatus !== 'ok'
        }"
        @click="emit('select', listing.id)"
      >
        <div class="listing-card-top">
          <span class="source-badge" :class="listing.platformKey">{{ listing.platform }}</span>
          <span class="freshness">{{ listing.updated }}</span>
          <button class="save-button" type="button" :class="{ saved: listing.saved }" :title="listing.saved ? '取消收藏' : '收藏'" @click.stop="emit('toggle-save', listing)">{{ listing.saved ? '♥' : '♡' }}</button>
        </div>
        <h3>{{ listing.title }}</h3>
        <div class="listing-meta"><span>{{ listing.listingType }}</span><b>·</b><span>{{ listing.room }}</span><b>·</b><span>{{ listing.area === null ? '面积未说明' : listing.area + '㎡' }}</span><b>·</b><span>{{ listingPlaceLabel(listing) }}</span></div>
          <div class="listing-bottom">
            <div class="rent-price"><strong>{{ listing.rent === null ? '租金未说明' : '¥' + listing.rent }}</strong><span v-if="listing.rent !== null">/月</span></div>
           <div class="distance-info" :class="{ unverified: listing.locationStatus !== 'verified' }"><span>{{ listing.locationStatus === 'verified' ? '↗' : '⌖' }}</span>{{ formatListingDistance(listing) }} · {{ formatListingCommute(listing) }}</div>
         </div>
         <div class="tag-row"><span v-for="tag in listing.tags.slice(0, 3)" :key="tag">{{ tag }}</span></div>
         <div class="listing-status-row">
           <span v-if="isAgentRecommended(listing)" class="listing-status-chip agent-recommended">Agent 推荐</span>
           <span class="listing-status-chip" :class="listing.filterStatus">{{ filterStatusLabel(listing) }}</span>
           <span>{{ detailStatusLabel(listing) }}</span>
           <span>{{ locationStatusLabel(listing) }}</span>
         </div>
         <p v-if="listing.filterReasons?.length || listing.rankingReasons?.length" class="listing-reason">{{ listing.filterReasons?.[0] || listing.rankingReasons?.[0] }}</p>
         <div class="listing-card-footer"><span>{{ detailStatusLabel(listing) }}{{ listing.detailStatus === 'ok' ? '' : (listingHasAddress(listing) ? ' · 列表地址待核验' : ' · 平台未提供地址') }}</span><button type="button" @click.stop="emit('open-detail', listing)">查看概览 <span>→</span></button></div>
      </article>
    </div>

    <div v-else class="empty-listing"><span>⌂</span><strong>还没有房源候选</strong><p>输入一个目标地点并开始搜索，助手会把结果放在这里。</p></div>

    <div class="listing-footer"><span><i class="footer-dot"></i>公开列表候选</span><button type="button" @click="emit('run-again')">重新搜索 <span>↻</span></button></div>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import {
  filterStatusLabel,
  formatListingCommute,
  formatListingDistance,
  detailStatusLabel,
  isAgentRecommended,
  isFilterPassed,
  listingHasAddress,
  listingPlaceLabel,
  locationStatusLabel
} from '../services/listingState.js'

const props = defineProps({
  listings: { type: Array, default: () => [] },
  selectedId: { type: String, default: null },
  searching: { type: Boolean, default: false },
  searchSummary: { type: Object, default: null }
})

const emit = defineEmits(['select', 'open-detail', 'toggle-save', 'run-again'])
const sortBy = ref('recommended')
const filtersOpen = ref(false)
const activeFilters = ref([])
const targetLocated = computed(() => {
  const status = props.searchSummary?.map_status
  return status ? !['pending', 'not_requested'].includes(status) : true
})

const summaryLabel = computed(() => {
  if (props.searching) return 'Agent 正在整理平台候选'
  if (props.searchSummary?.offline) return '离线夹具结果，仅用于回归验证'
  if (['pending', 'not_requested'].includes(props.searchSummary?.map_status)) return '目标位置待确认，距离和通勤未核验'
  if (props.searchSummary?.map_status === 'partial') return '部分位置或通勤信息待核验'
  return ''
})

function compareNullable(left, right) {
  const leftNumber = typeof left === 'number' && Number.isFinite(left) ? left : null
  const rightNumber = typeof right === 'number' && Number.isFinite(right) ? right : null
  if (leftNumber === null && rightNumber === null) return 0
  if (leftNumber === null) return 1
  if (rightNumber === null) return -1
  return leftNumber - rightNumber
}

const sortedListings = computed(() => {
  let output = [...props.listings]
  if (activeFilters.value.includes('已核验位置')) output = output.filter(item => item.locationStatus === 'verified')
  if (activeFilters.value.includes('独立卫浴')) output = output.filter(item => item.tags.includes('独立卫浴'))
  if (activeFilters.value.includes('可短租')) output = output.filter(item => item.tags.includes('可短租'))
  const indexed = output.map((item, index) => ({ item, index }))
  if (sortBy.value === 'rent') indexed.sort((a, b) => compareNullable(a.item.rent, b.item.rent) || a.index - b.index)
  if (sortBy.value === 'distance') indexed.sort((a, b) => {
    const left = a.item.locationStatus === 'verified' ? a.item.distance : null
    const right = b.item.locationStatus === 'verified' ? b.item.distance : null
    return compareNullable(left, right) || a.index - b.index
  })
  if (sortBy.value === 'updated') indexed.sort((a, b) => compareNullable(a.item.updatedMinutes, b.item.updatedMinutes) || a.index - b.index)
  return indexed.map(entry => entry.item)
})

function toggleFilter(label) {
  activeFilters.value = activeFilters.value.includes(label)
    ? activeFilters.value.filter(item => item !== label)
    : [...activeFilters.value, label]
}
</script>
