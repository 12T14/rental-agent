<template>
  <section class="chat-panel">
    <div ref="scrollBox" class="conversation-scroll">
      <div v-for="message in messages" :key="message.id" class="message-row" :class="message.role">
        <div v-if="message.role === 'assistant'" class="message-avatar">栖</div>
        <div class="message-body">
          <div v-if="message.role === 'assistant'" class="message-name">找房助手</div>
          <div class="message-bubble" :class="{ plain: message.kind === 'progress' || message.kind === 'result' }">
            <p v-for="(line, index) in message.text.split('\n')" :key="index">{{ line }}</p>
          </div>

          <div v-if="message.kind === 'activity'" class="activity-card">
            <div class="activity-title"><span class="activity-spinner" :class="{ finished: !searching }"></span><strong>{{ searching ? '正在整理附近房源' : '本轮搜索已完成' }}</strong><small>{{ searching ? '按平台顺序读取公开列表' : '结果已汇总到下面的房源卡片' }}</small></div>
            <div class="activity-platforms">
              <div v-for="platform in platforms" :key="platform.key" class="activity-platform">
                <span class="activity-status" :class="platform.status">{{ platform.status === 'ok' || platform.status === 'partial' ? '✓' : platform.status === 'loading' ? '…' : platform.status === 'blocked' ? '!' : '·' }}</span>
                <span>{{ platform.name }}</span>
                <small>{{ platform.status === 'loading' ? '抓取中' : platform.status === 'blocked' ? '需要验证' : platform.status === 'partial' ? `${platform.count} 条列表` : `${platform.count} 条` }}</small>
              </div>
            </div>
            <button v-if="verification" class="verification-button" type="button" @click="emit('verify', verification.platformKey)">打开{{ verification.platformName }}验证窗口</button>
          </div>

          <div v-if="message.kind === 'result'" class="result-block">
            <div class="result-heading"><strong>为你挑出 {{ listings.length }} 条候选</strong><span>点击卡片可在地图定位</span></div>
            <div class="chat-listings">
              <article v-for="listing in listings.slice(0, 5)" :key="listing.id" class="chat-listing-card" :class="{ selected: selectedId === listing.id }" @click="emit('select-listing', listing.id)">
                <div class="chat-card-top"><span class="source-badge" :class="listing.platformKey">{{ listing.platform }}</span><span>{{ listing.updated }}</span><button type="button" class="chat-save" :class="{ saved: listing.saved }" @click.stop="emit('toggle-save', listing)">{{ listing.saved ? '♥' : '♡' }}</button></div>
                <h3>{{ listing.title }}</h3>
                <p class="chat-card-meta">{{ listing.community }} · {{ listing.room }} · {{ listing.area }}㎡</p>
                <div class="chat-card-bottom"><strong>¥{{ listing.rent }}<small>/月</small></strong><span :class="{ pending: listing.locationStatus === 'unverified' }">{{ listing.locationStatus === 'verified' ? `${listing.distance} km · ${listing.commute}` : '位置待核验' }}</span></div>
                <div class="chat-tag-row"><span v-for="tag in listing.tags.slice(0, 3)" :key="tag">{{ tag }}</span></div>
                <button class="chat-detail-link" type="button" @click.stop="emit('open-detail', listing)">查看房源概览 <span>→</span></button>
              </article>
            </div>
            <div class="result-disclaimer">公开列表候选不等于当前可租，费用和联系方式请打开原平台核验。</div>
          </div>
        </div>
      </div>
      <div v-if="searching" class="typing-row"><span class="message-avatar">栖</span><div class="typing-bubble"><i></i><i></i><i></i></div></div>
    </div>

    <form class="chat-composer" @submit.prevent="submitMessage">
      <textarea
        ref="composerInput"
        :value="modelValue"
        rows="1"
        placeholder="告诉我你想在哪工作、预算多少，或直接追问某套房源…"
        @input="emit('update:modelValue', $event.target.value)"
        @keydown.enter.exact.prevent="submitMessage"
      ></textarea>
      <button v-if="searching" class="stop-stream" type="button" title="停止生成" @click="emit('cancel')">■</button>
      <button v-else type="submit" :disabled="sendDisabled || !modelValue.trim()" title="发送">↑</button>
    </form>
    <div class="composer-note">
      <span>⌁</span>
      Agent 回复会实时显示；地图或通勤未配置时仍可继续按文字条件找房。
    </div>
  </section>
</template>

<script setup>
import { nextTick, ref, watch } from 'vue'

import { canSubmitChatMessage } from '../services/chatApi.js'

const props = defineProps({
  messages: { type: Array, default: () => [] },
  listings: { type: Array, default: () => [] },
  platforms: { type: Array, default: () => [] },
  selectedId: { type: String, default: null },
  searching: { type: Boolean, default: false },
  sendDisabled: { type: Boolean, default: false },
  verification: { type: Object, default: null },
  modelValue: { type: String, default: '' }
})

const emit = defineEmits(['update:modelValue', 'send', 'cancel', 'select-listing', 'open-detail', 'toggle-save', 'verify'])
const scrollBox = ref(null)
const composerInput = ref(null)

watch(
  () => [props.messages.length, props.messages.at(-1)?.text],
  () => nextTick(() => { if (scrollBox.value) scrollBox.value.scrollTop = scrollBox.value.scrollHeight })
)

function submitMessage() {
  if (!canSubmitChatMessage(props.modelValue, props.searching || props.sendDisabled)) return
  const value = props.modelValue.trim()
  emit('send', value)
  emit('update:modelValue', '')
}

function focusComposer() {
  nextTick(() => {
    const input = composerInput.value
    if (!input) return
    input.focus()
    input.setSelectionRange(input.value.length, input.value.length)
  })
}

defineExpose({ focusComposer })

</script>
