<template>
  <section class="panel listing-panel">
    <div class="listing-heading">
      <div>
        <span class="panel-kicker">房源清单</span>
        <h2>附近房源 <span v-if="!searching">{{ listings.length }}</span></h2>
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
        :class="{ selected: selectedId === listing.id }"
        @click="emit('select', listing.id)"
      >
        <div class="listing-card-top">
          <span class="source-badge" :class="listing.platformKey">{{ listing.platform }}</span>
          <span class="freshness">{{ listing.updated }}</span>
          <button class="save-button" type="button" :class="{ saved: listing.saved }" :title="listing.saved ? '取消收藏' : '收藏'" @click.stop="emit('toggle-save', listing)">{{ listing.saved ? '♥' : '♡' }}</button>
        </div>
        <h3>{{ listing.title }}</h3>
        <div class="listing-meta"><span>{{ listing.room }}</span><b>·</b><span>{{ listing.area }}㎡</span><b>·</b><span>{{ listing.community }}</span></div>
        <div class="listing-bottom">
          <div class="rent-price"><strong>¥{{ listing.rent }}</strong><span>/月</span></div>
          <div class="distance-info" :class="{ unverified: listing.locationStatus === 'unverified' }"><span>{{ listing.locationStatus === 'unverified' ? '⌖' : '↗' }}</span>{{ listing.locationStatus === 'unverified' ? '位置待核验' : `${listing.distance} km · ${listing.commute}` }}</div>
        </div>
        <div class="tag-row"><span v-for="tag in listing.tags.slice(0, 3)" :key="tag">{{ tag }}</span></div>
        <div class="listing-card-footer"><span>{{ listing.locationStatus === 'verified' ? '位置已核验' : '列表地址待核验' }}</span><button type="button" @click.stop="emit('open-detail', listing)">查看概览 <span>→</span></button></div>
      </article>
    </div>

    <div v-else class="empty-listing"><span>⌂</span><strong>还没有房源候选</strong><p>输入一个目标地点并开始搜索，助手会把结果放在这里。</p></div>

    <div class="listing-footer"><span><i class="footer-dot"></i>公开列表候选</span><button type="button" @click="emit('run-again')">重新搜索 <span>↻</span></button></div>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  listings: { type: Array, default: () => [] },
  selectedId: { type: String, default: null },
  searching: { type: Boolean, default: false }
})

const emit = defineEmits(['select', 'open-detail', 'toggle-save', 'run-again'])
const sortBy = ref('recommended')
const filtersOpen = ref(false)
const activeFilters = ref([])

const sortedListings = computed(() => {
  let output = [...props.listings]
  if (activeFilters.value.includes('已核验位置')) output = output.filter(item => item.locationStatus === 'verified')
  if (activeFilters.value.includes('独立卫浴')) output = output.filter(item => item.tags.includes('独立卫浴'))
  if (activeFilters.value.includes('可短租')) output = output.filter(item => item.tags.includes('可短租'))
  if (sortBy.value === 'rent') output.sort((a, b) => a.rent - b.rent)
  if (sortBy.value === 'distance') output.sort((a, b) => Number(a.distance) - Number(b.distance))
  if (sortBy.value === 'updated') output.sort((a, b) => (a.updatedMinutes ?? 999) - (b.updatedMinutes ?? 999))
  return output
})

function toggleFilter(label) {
  activeFilters.value = activeFilters.value.includes(label)
    ? activeFilters.value.filter(item => item !== label)
    : [...activeFilters.value, label]
}
</script>
