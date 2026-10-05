<template>
  <div>
    <div class="alert-bar alert-urgent" v-if="urgent.length">
      临期预警：{{ urgent.map(a => a.name + (a.days_left === 0 ? '(今天到期)' : '(' + a.days_left + '天)')).join(' · ') }}
    </div>
    <div class="alert-bar alert-expired" v-if="expiredUnscanned.length">
      过期未扫 {{ expiredUnscanned.length }} 批，建议下架：
      {{ expiredUnscanned.map(a => a.name + ' ×' + fmt(a.qty_remain)).join(' · ') }}
      <span class="alert-note">（仍在架，新鲜余量不足时消费可兜底扣到）</span>
    </div>
    <div class="alert-bar alert-dirty" v-if="dirty.length">
      脏数据 {{ dirty.length }} 批，不参与扣减/下架：{{ dirty.map(a => a.name + '#' + a.id).join(' · ') }}
    </div>
    <div class="alert-bar" v-if="!urgent.length && !expiredUnscanned.length && !dirty.length">临期预警带：暂无紧急批次</div>
    <div class="wrap">
      <nav class="layer-tabs">
        <router-link to="/">全层</router-link>
        <router-link to="/layer/upper">上层</router-link>
        <router-link to="/layer/mid">中层</router-link>
        <router-link to="/layer/lower">下层</router-link>
        <router-link to="/inbound">入库</router-link>
        <router-link to="/consume">消费</router-link>
        <router-link to="/settings">设置</router-link>
      </nav>
      <router-view />
    </div>
  </div>
</template>
<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api, onPantryChanged } from './api'
const alerts = ref([])
const urgent = computed(() => alerts.value.filter(a => a.level === 'soon' || a.level === 'expiring_today'))
const expiredUnscanned = computed(() => alerts.value.filter(a => a.level === 'expired_unscanned'))
const dirty = computed(() => alerts.value.filter(a => a.level === 'dirty'))
function fmt(n) { return Number(n).toString() }
async function load() {
  try { alerts.value = await api('/alerts') } catch { alerts.value = [] }
}
let off
onMounted(() => { load(); off = onPantryChanged(load) })
onUnmounted(() => off && off())
</script>
