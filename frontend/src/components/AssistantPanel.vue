<template>
  <section class="assistant-panel">
    <div class="assistant-header">
      <div class="assistant-title"><span class="assistant-orb">✦</span><div><span class="panel-kicker">处理进度</span><h2>栖居正在工作</h2></div></div>
      <button class="close-assistant" type="button" title="收起助手" @click="emit('close')">×</button>
    </div>
    <div class="agent-timeline">
      <div class="timeline-item done"><span class="timeline-icon">✓</span><div><strong>已定位目标地点</strong><small>高德地理编码 · {{ platforms.length ? '高置信度' : '等待输入' }}</small></div><time>完成</time></div>
      <div v-for="platform in platforms" :key="platform.key" class="timeline-item" :class="timelineClass(platform)">
        <span class="timeline-icon">{{ platform.status === 'blocked' ? '!' : platform.status === 'loading' ? '…' : platform.status === 'ok' || platform.status === 'partial' ? '✓' : '·' }}</span>
        <div><strong>读取 {{ platform.name }} 公开列表</strong><small>{{ platform.status === 'blocked' ? '等待人工验证后继续' : platform.status === 'partial' ? '已拿到列表，详情需打开原平台' : platform.status === 'ok' ? `已整理 ${platform.count} 条候选` : '尚未开始' }}</small></div>
        <time>{{ platform.status === 'blocked' ? '需处理' : platform.status === 'ok' || platform.status === 'partial' ? '完成' : '等待' }}</time>
      </div>
      <div class="timeline-item pending"><span class="timeline-icon">○</span><div><strong>筛选通勤与预算</strong><small>等待全部平台返回后执行</small></div><time>下一步</time></div>
    </div>
    <div class="assistant-footer"><span><i class="secure-dot"></i>本地模式 · 不会发送个人信息</span><button type="button" @click="emit('run-search')">再次运行 <span>↻</span></button></div>
  </section>
</template>

<script setup>
const props = defineProps({ platforms: { type: Array, default: () => [] } })
const emit = defineEmits(['close', 'run-search'])

function timelineClass(platform) {
  if (platform.status === 'blocked') return 'blocked'
  if (platform.status === 'ok' || platform.status === 'partial') return 'done'
  if (platform.status === 'loading') return 'active'
  return 'pending'
}
</script>
