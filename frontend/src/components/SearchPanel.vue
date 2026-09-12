<template>
  <section class="panel search-panel">
    <div class="panel-heading">
      <div>
        <span class="panel-kicker">搜索条件</span>
        <h2>从工作地点开始</h2>
      </div>
      <span class="panel-step">01</span>
    </div>

    <form class="search-form" @submit.prevent="submitSearch">
      <label class="field-label" for="target-place">目标地点</label>
      <div class="search-input-wrap">
        <span class="input-icon">⌖</span>
        <input
          id="target-place"
          v-model="criteria.targetPlace"
          type="text"
          autocomplete="off"
          placeholder="公司、学校或地标名称"
          @focus="showSuggestions = true"
          @input="showSuggestions = true"
        />
        <button v-if="criteria.targetPlace" class="clear-input" type="button" title="清空地点" @click="clearTarget">×</button>
      </div>

      <div v-if="showSuggestions && filteredSuggestions.length" class="suggestion-list">
        <button
          v-for="suggestion in filteredSuggestions"
          :key="suggestion.name"
          type="button"
          class="suggestion-item"
          @click="chooseSuggestion(suggestion)"
        >
          <span class="suggestion-pin">⌖</span>
          <span class="suggestion-copy">
            <strong>{{ suggestion.name }}</strong>
            <small>{{ suggestion.address }}</small>
          </span>
          <span class="suggestion-city">{{ suggestion.city }}</span>
        </button>
      </div>

      <div class="field-grid">
        <div>
          <label class="field-label" for="city">城市</label>
          <select id="city" v-model="criteria.city">
            <option value="">请选择城市</option>
            <option>南京</option>
            <option>上海</option>
            <option>北京</option>
            <option>广州</option>
            <option>深圳</option>
          </select>
        </div>
        <div>
          <label class="field-label" for="radius">搜索半径</label>
          <div class="unit-input">
            <input id="radius" v-model.number="criteria.radiusKm" type="number" min="1" max="20" step="1" />
            <span>km</span>
          </div>
        </div>
      </div>

      <div class="field-grid">
        <div>
          <label class="field-label" for="rent-type">房源类型</label>
          <select id="rent-type" v-model="criteria.rentType">
            <option value="整租">整租</option>
            <option value="合租">合租</option>
            <option value="不限">不限</option>
          </select>
        </div>
        <div>
          <label class="field-label" for="max-rent">月租上限</label>
          <div class="unit-input">
            <span class="unit-prefix">¥</span>
            <input id="max-rent" v-model.number="criteria.maxRent" type="number" min="0" step="100" />
          </div>
        </div>
      </div>

      <div class="range-field">
        <div class="range-heading">
          <label class="field-label" for="max-commute">最长通勤</label>
          <strong>{{ criteria.maxCommute }} 分钟</strong>
        </div>
        <input id="max-commute" v-model.number="criteria.maxCommute" type="range" min="15" max="90" step="5" />
        <div class="range-scale"><span>15 分钟</span><span>90 分钟</span></div>
      </div>

      <div class="mode-row">
        <span class="field-label">通勤方式</span>
        <div class="segmented-control">
          <button
            v-for="mode in commuteModes"
            :key="mode.value"
            type="button"
            :class="{ active: criteria.commuteMode === mode.value }"
            @click="criteria.commuteMode = mode.value"
          >
            <span>{{ mode.icon }}</span>{{ mode.label }}
          </button>
        </div>
      </div>

      <button class="search-submit" type="submit" :disabled="searching || !criteria.targetPlace.trim()">
        <span v-if="searching" class="button-spinner"></span>
        <span v-else class="search-submit-icon">⌕</span>
        {{ searching ? '正在整理房源…' : '开始搜索附近房源' }}
        <span v-if="!searching" class="submit-arrow">→</span>
      </button>
    </form>

    <p class="search-hint"><span>✦</span> 助手会先定位地点，再按平台顺序获取公开候选。</p>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  criteria: { type: Object, required: true },
  target: { type: Object, required: true },
  searching: { type: Boolean, default: false }
})

const emit = defineEmits(['submit', 'select-suggestion'])
const showSuggestions = ref(false)

const suggestions = []

const commuteModes = [
  { value: '公交', label: '公交', icon: '↗' },
  { value: '驾车', label: '驾车', icon: '⌁' },
  { value: '骑行', label: '骑行', icon: '⌁' }
]

const filteredSuggestions = computed(() => {
  const keyword = props.criteria.targetPlace.trim().toLowerCase()
  if (!keyword) return suggestions
  return suggestions.filter(item => `${item.name}${item.address}${item.city}`.toLowerCase().includes(keyword))
})

function chooseSuggestion(item) {
  emit('select-suggestion', item)
  showSuggestions.value = false
}

function submitSearch() {
  showSuggestions.value = false
  emit('submit', { ...props.criteria })
}

function clearTarget() {
  props.criteria.targetPlace = ''
  showSuggestions.value = true
}
</script>
