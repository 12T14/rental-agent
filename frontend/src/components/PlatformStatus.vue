<template>
  <section class="panel platform-panel">
    <div class="panel-heading compact-heading">
      <div>
        <span class="panel-kicker">来源状态</span>
        <h2>房源来源</h2>
      </div>
      <span class="live-label"><i></i>实时状态</span>
    </div>

    <div class="platform-list">
      <div v-for="platform in platforms" :key="platform.key" class="platform-row">
        <span class="platform-logo" :class="platform.key">{{ logoText(platform.key) }}</span>
        <div class="platform-copy">
          <strong>{{ platform.name }}</strong>
          <small>{{ statusDescription(platform) }}</small>
        </div>
        <div class="platform-result">
          <strong>{{ platform.count }}</strong>
          <span>条</span>
        </div>
        <span class="status-badge" :class="platform.status">{{ platform.label }}</span>
      </div>
    </div>

    <div class="platform-note"><span>i</span> 平台状态只代表本轮抓取结果，详情和可租状态仍需打开原链接核验。</div>
  </section>
</template>

<script setup>
defineProps({
  platforms: { type: Array, default: () => [] }
})

function logoText(key) {
  if (key === 'fifty-eight') return '58'
  if (key === 'anjuke') return '安'
  return '房'
}

function statusDescription(platform) {
  if (platform.status === 'blocked') return '需要人工验证'
  if (platform.status === 'partial') return '仅返回公开列表'
  if (platform.status === 'loading') return '正在抓取公开信息'
  return '公开列表已更新'
}
</script>
