<template>
  <div class="app-container">
    <!-- 顶部栏 -->
    <header class="top-bar">
      <div class="top-left">
        <span class="title">CBMonitor</span>
        <span class="subtitle">可转债监控</span>
      </div>
      <div class="top-center">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          :class="['tab-btn', { active: activeTab === tab.key }]"
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
        </button>
      </div>
      <div class="top-right">
        <span class="status-info" v-if="status">
          档案 {{ status.profile_date || status.data_date }} | {{ status.total_count }}只
        </span>
        <span :class="['quote-info', status.is_trading ? 'live' : 'closed', { stale: status.stale }]" v-if="status">
          {{ status.is_trading ? '● 实时' : '○ 收盘' }}
          {{ status.quote_time || (status.is_trading ? '--:--:--' : '收盘价') }}
          <template v-if="status.stale"> · 延迟</template>
        </span>
      </div>
    </header>

    <!-- 全量数据 Tab -->
    <main v-if="activeTab === 'all'" class="main-content">
      <!-- 筛选条件栏（搜索 + 仿集思录筛选，合并一行） -->
      <div class="filter-bar">
        <input
          v-model="searchText"
          class="search-input-sm"
          placeholder="代码/名称"
        />
        <span class="filter-label">转债价格</span>
        <input v-model.number="filterPriceMin" class="filter-input" placeholder="低" />
        <span class="filter-sep">到</span>
        <input v-model.number="filterPriceMax" class="filter-input" placeholder="高" />
        <span class="filter-unit">元</span>

        <span class="filter-label">剩余规模≤</span>
        <input v-model.number="filterSizeMax" class="filter-input" placeholder="" />
        <span class="filter-unit">亿</span>

        <span class="filter-label">溢价率≤</span>
        <input v-model.number="filterPremiumMax" class="filter-input" placeholder="" />
        <span class="filter-unit">%</span>

        <span class="filter-label">到期收益率≥</span>
        <input v-model.number="filterYieldMin" class="filter-input" placeholder="" />
        <span class="filter-unit">%</span>

        <span class="filter-label">剩余年限</span>
        <input v-model.number="filterYearsMin" class="filter-input" placeholder="低" />
        <span class="filter-sep">到</span>
        <input v-model.number="filterYearsMax" class="filter-input" placeholder="高" />
        <span class="filter-unit">年</span>

        <button class="filter-reset" @click="resetFilters">清除</button>
      </div>
      <div class="table-area">
        <div v-if="loadError && bonds.length === 0" class="load-error">{{ loadError }}</div>
        <CBTable v-else :bonds="filteredBonds" :loading="loading" />
      </div>
    </main>

    <!-- 老实和尚策略 Tab -->
    <main v-else-if="activeTab === 'lao'" class="main-content">
      <div class="strategy-tabs">
        <button
          v-for="s in laoStrategies"
          :key="s.key"
          :class="['strategy-btn', { active: activeStrategy === s.key }]"
          @click="loadStrategy(s.key)"
        >{{ s.label }}</button>
      </div>
      <div class="filter-desc" v-if="strategyResult">
        筛选条件：{{ strategyResult.filter_desc }}
      </div>
      <div class="table-area">
        <CBTable :bonds="strategyResult?.bonds || []" :loading="strategyLoading" />
      </div>
    </main>

    <!-- 检钢蹦策略 Tab -->
    <main v-else-if="activeTab === 'jgb'" class="main-content">
      <div class="strategy-tabs">
        <button
          v-for="s in jgbStrategies"
          :key="s.key"
          :class="['strategy-btn', { active: activeStrategy === s.key }]"
          @click="loadStrategy(s.key)"
        >{{ s.label }}</button>
      </div>
      <div class="filter-desc" v-if="strategyResult">
        筛选条件：{{ strategyResult.filter_desc }}
      </div>
      <div class="table-area">
        <CBTable :bonds="strategyResult?.bonds || []" :loading="strategyLoading" />
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import CBTable from './components/CBTable.vue'
import { getBonds, getStrategy, getStatus, type BondVO, type StrategyResultVO, type DataStatusVO } from './api'

const tabs = [
  { key: 'all', label: '全部转债' },
  { key: 'lao', label: 'A策略集' },
  { key: 'jgb', label: 'B策略集' }
]

const laoStrategies = [
  { key: 'double_low', label: '双低' },
  { key: 'low_premium', label: '低溢价' },
  { key: 'low_price', label: '低价' },
  { key: 'high_yield', label: '高收益率' }
]

const jgbStrategies = [
  { key: 'jgb_micro_cap', label: '微盘转债' },
  { key: 'jgb_low_price_low_premium', label: '低价低溢价' },
  { key: 'jgb_defensive', label: '防守型' },
  { key: 'jgb_active', label: '活跃品种' }
]

const activeTab = ref('all')
const activeStrategy = ref('double_low')
const bonds = ref<BondVO[]>([])
const strategyResult = ref<StrategyResultVO | null>(null)
const status = ref<DataStatusVO | null>(null)
const loadError = ref('')
const loading = ref(false)
const strategyLoading = ref(false)
let retryTimer: number | undefined
let retryCount = 0
const searchText = ref('')

// 筛选条件
const filterPriceMin = ref<number | undefined>(undefined)
const filterPriceMax = ref<number | undefined>(undefined)
const filterSizeMax = ref<number | undefined>(undefined)
const filterPremiumMax = ref<number | undefined>(undefined)
const filterYieldMin = ref<number | undefined>(undefined)
const filterYearsMin = ref<number | undefined>(undefined)
const filterYearsMax = ref<number | undefined>(undefined)

function resetFilters() {
  filterPriceMin.value = undefined
  filterPriceMax.value = undefined
  filterSizeMax.value = undefined
  filterPremiumMax.value = undefined
  filterYieldMin.value = undefined
  filterYearsMin.value = undefined
  filterYearsMax.value = undefined
}

const filteredBonds = computed(() => {
  let result = bonds.value

  // 文本搜索
  if (searchText.value) {
    const q = searchText.value.toLowerCase()
    result = result.filter(b =>
      b.bond_code.includes(q) ||
      b.bond_name.toLowerCase().includes(q) ||
      b.stock_code.includes(q) ||
      b.stock_name.toLowerCase().includes(q)
    )
  }

  // 价格范围
  if (filterPriceMin.value != null) result = result.filter(b => b.bond_price >= filterPriceMin.value)
  if (filterPriceMax.value != null) result = result.filter(b => b.bond_price <= filterPriceMax.value)

  // 剩余规模≤
  if (filterSizeMax.value != null) result = result.filter(b => b.remaining_size <= filterSizeMax.value)

  // 溢价率≤
  if (filterPremiumMax.value != null) result = result.filter(b => b.premium_rate <= filterPremiumMax.value)

  // 到期收益率≥
  if (filterYieldMin.value != null) result = result.filter(b => b.pre_tax_yield >= filterYieldMin.value)

  // 剩余年限范围
  if (filterYearsMin.value != null) result = result.filter(b => b.remaining_years >= filterYearsMin.value)
  if (filterYearsMax.value != null) result = result.filter(b => b.remaining_years <= filterYearsMax.value)

  return result
})

async function loadBonds() {
  if (loading.value) return
  loading.value = true
  try {
    const res = await getBonds()
    // 防御：非数组响应不当成成功，避免表格永远空白
    bonds.value = Array.isArray(res.data) ? res.data : []
    loadError.value = ''
    retryCount = 0
  } catch (e) {
    console.error('加载失败', e)
    retryCount += 1
    if (retryCount <= 3) {
      const wait = retryCount * 5
      loadError.value = `数据加载失败，${wait} 秒后自动重试（${retryCount}/3）`
      if (retryTimer) clearTimeout(retryTimer)
      retryTimer = window.setTimeout(loadBonds, wait * 1000)
    } else {
      loadError.value = '数据加载失败，请检查网络后刷新页面'
    }
  } finally {
    loading.value = false
  }
}

async function loadStrategy(key: string) {
  activeStrategy.value = key
  strategyLoading.value = true
  try {
    const res = await getStrategy(key)
    strategyResult.value = res.data
  } catch (e) {
    console.error('策略加载失败', e)
  } finally {
    strategyLoading.value = false
  }
}

async function loadStatus() {
  try {
    const res = await getStatus()
    status.value = res.data
  } catch {}
}

// 盘中实时：每 30s 只打轻量的 /status，version 变化才重拉全量 /bonds。
// 外网抓取由服务端单一后台任务承担，这里无论多少用户、刷多少次都不产生额外外网请求。
let pollTimer: number | undefined

async function pollRealtime() {
  if (document.visibilityState === 'hidden') return
  try {
    const prevVersion = status.value?.version ?? -1
    const res = await getStatus()
    status.value = res.data
    // 正常靠 version 变化触发；额外兜底：bonds 为空说明首屏加载失败过，无条件重拉，
    // 否则一次跨洋抖动会让看板永久停在「暂无数据」（2026-11-13 线上事故）。
    if (!loading.value && (res.data.version !== prevVersion || bonds.value.length === 0)) {
      await loadBonds()
      if (activeTab.value !== 'all') await loadStrategy(activeStrategy.value)
    }
  } catch {}
}

function onVisible() {
  if (document.visibilityState === 'visible') pollRealtime()
}

onMounted(() => {
  loadBonds()
  loadStatus()
  pollTimer = window.setInterval(pollRealtime, 30000)
  document.addEventListener('visibilitychange', onVisible)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (retryTimer) clearTimeout(retryTimer)
  document.removeEventListener('visibilitychange', onVisible)
})
</script>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, "Microsoft YaHei", sans-serif; background: #f5f7fa; }
html, body { height: 100%; overflow: hidden; }
#app { height: 100%; }

/* [2026-09-27 手机适配] 原来只写 100vh：手机浏览器（iOS Safari / Android Chrome）
   的 100vh 包含被地址栏遮住的那段高度，会让底部内容顶出可视区、页面整体被裁。
   100dvh（动态视口高度）跟随地址栏收起/展开，是移动端正确做法；
   不支持 dvh 的老浏览器自动回落到上一行的 100vh。 */
.app-container { height: 100vh; height: 100dvh; display: flex; flex-direction: column; padding: 4px; }

.top-bar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 12px; background: #fff; border-radius: 6px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06); margin-bottom: 4px;
  flex-shrink: 0;
}
.top-left { display: flex; align-items: baseline; gap: 8px; }
.title { font-size: 18px; font-weight: 700; color: #2c3e50; }
.subtitle { font-size: 13px; color: #95a5a6; }
.top-center { display: flex; gap: 4px; }
.tab-btn {
  padding: 6px 16px; border: 1px solid #e0e6ed; background: #fff;
  border-radius: 6px; cursor: pointer; font-size: 14px; color: #606266;
  transition: all 0.2s;
}
.tab-btn:hover { background: #f0f5ff; border-color: #c0d8f0; }
.tab-btn.active { background: #4a90d9; color: #fff; border-color: #4a90d9; }
.top-right { display: flex; align-items: center; gap: 12px; }
.status-info { font-size: 12px; color: #95a5a6; white-space: nowrap; }
.quote-info { font-size: 12px; white-space: nowrap; }
.quote-info.live { color: #52c41a; }
.quote-info.closed { color: #95a5a6; }
.quote-info.stale { color: #e6a23c; }
.load-error {
  margin: 24px auto; max-width: 420px; padding: 16px 20px;
  background: #fdf6ec; border: 1px solid #faecd8; border-radius: 6px;
  font-size: 13px; color: #e6a23c; text-align: center; line-height: 1.6;
}

.main-content {
  background: #fff; border-radius: 6px; padding: 6px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
  flex: 1; display: flex; flex-direction: column; overflow: hidden;
  min-height: 0;
}
.table-area {
  flex: 1; overflow: auto; min-height: 0;
}

.search-input-sm {
  width: 100px; padding: 3px 6px; border: 1px solid #dcdfe6;
  border-radius: 3px; font-size: 12px; outline: none;
}
.search-input-sm:focus { border-color: #4a90d9; }

.filter-bar {
  display: flex; align-items: center; gap: 4px; flex-wrap: nowrap;
  padding: 6px 8px; background: #f8f9fb; border: 1px solid #e8edf2;
  border-radius: 4px; margin-bottom: 6px; font-size: 12px;
  flex-shrink: 0; overflow-x: auto;
}
.filter-label { color: #606266; white-space: nowrap; margin-right: 2px; }
.filter-input {
  width: 52px; padding: 3px 4px; border: 1px solid #dcdfe6;
  border-radius: 3px; font-size: 12px; text-align: center; outline: none;
}
.filter-input:focus { border-color: #4a90d9; }
.filter-sep { color: #999; margin: 0 1px; }
.filter-unit { color: #999; margin-right: 6px; }
.filter-reset {
  margin-left: auto; padding: 3px 10px; background: #fff;
  border: 1px solid #dcdfe6; border-radius: 3px; cursor: pointer;
  font-size: 12px; color: #606266;
}
.filter-reset:hover { background: #f0f5ff; }

/* flex-shrink:0：策略 Tab 下这两个元素是 .table-area 的兄弟，不固定就会被表格挤扁 */
.strategy-tabs { display: flex; gap: 4px; margin-bottom: 8px; flex-wrap: wrap; flex-shrink: 0; }
.strategy-btn {
  padding: 5px 14px; border: 1px solid #e0e6ed; background: #fafafa;
  border-radius: 6px; cursor: pointer; font-size: 13px; color: #606266;
  transition: all 0.2s;
}
.strategy-btn:hover { background: #f0f5ff; }
.strategy-btn.active { background: #67c23a; color: #fff; border-color: #67c23a; }
.filter-desc {
  padding: 6px 12px; background: #fdf6ec; border: 1px solid #faecd8;
  border-radius: 4px; font-size: 12px; color: #e6a23c; margin-bottom: 8px;
  flex-shrink: 0;
}

/* ============ 手机适配（2026-09-27 新增）============
   原样式是桌面端设计：顶栏三区块（标题 / 3个Tab / 状态+刷新）挤在一行，
   390px 屏幕放不下约 470px 的内容，文字被压成竖排。
   这里让顶栏换行、Tab 独占第二行。
   表格的横向滚动由 CBTable 的 min-width 配合 .table-area 完成，此处不处理。 */
@media (max-width: 640px) {
  .top-bar {
    flex-wrap: wrap;
    row-gap: 6px;
    padding: 7px 10px;
  }
  /* 第一行：标题 + 档案/行情双状态 */
  .top-left { flex: 0 1 auto; min-width: 0; }
  .title { font-size: 16px; }
  .subtitle { display: none; }        /* 省空间，"CBMonitor" 已足够表意 */
  .top-right { flex: 0 0 auto; margin-left: auto; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
  .status-info { font-size: 11px; }
  .quote-info { font-size: 11px; }
  /* 第二行：3 个 Tab 等宽铺满 */
  .top-center { flex: 1 1 100%; order: 3; gap: 5px; }
  .tab-btn { flex: 1 1 0; padding: 6px 4px; font-size: 13px; white-space: nowrap; }

  .main-content { padding: 5px; }
  .strategy-btn { padding: 5px 11px; font-size: 12.5px; }
  .filter-bar { padding: 5px 7px; }
  .filter-reset { margin-left: 4px; }
}

/* 极窄屏（320px 老机型）：进一步压缩 */
@media (max-width: 360px) {
  .title { font-size: 15px; }
  .status-info { font-size: 10px; }
  .quote-info { font-size: 10px; }
  .tab-btn { font-size: 12px; }
}
</style>
