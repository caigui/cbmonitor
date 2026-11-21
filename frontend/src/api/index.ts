import axios from 'axios'

const api = axios.create({
  // 本地开发走 vite proxy 的 /api；ARM 上部署在 /cb/ 下，需走 /cb_api（ARM 的 /api/ 已被 ArbDashboard 占用）
  baseURL: location.pathname.startsWith('/cb') ? '/cb_api' : '/api',
  // 轻量探针（/status、/realtime）毫秒级，15s 足够；
  // 大响应 /bonds 跨洋传输可能抖动，单独放宽到 90s（下面 getBonds 覆盖）。
  // 历史教训：30s 曾导致偶发超时被前端静默吞掉，表格空白且不自愈。
  timeout: 15000
})

export interface BondVO {
  bond_code: string
  bond_name: string
  stock_code: string
  stock_name: string
  bond_price: number
  bond_change_pct: number
  stock_price: number
  stock_change_pct: number
  convert_price: number
  convert_value: number
  premium_rate: number
  double_low: number
  pre_tax_yield: number
  pre_tax_put_yield: number
  remaining_years: number
  remaining_size: number
  maturity_date: string
  credit_rating: string
  risk_flag?: string
  pure_bond_value: number
  put_value: number
  maturity_redeem_price: number
  elasticity: number
  pb_ratio: number
  redeem_trigger_price: number
  put_trigger_price: number
  turnover_rate: number
  turnover_amount: number
  data_date: string
  source: string
}

export interface StrategyResultVO {
  strategy_name: string
  strategy_key: string
  bonds: BondVO[]
  filter_desc: string
  data_date: string
}

export interface DataStatusVO {
  data_date: string
  source: string
  total_count: number
  updated_at: string
  profile_date: string
  quote_date: string
  quote_time: string
  is_trading: boolean
  stale: boolean
  error: string
  refresh_seconds: number
  version: number
}

export interface RealtimeStateVO {
  profile_date: string
  quote_date: string
  quote_time: string
  is_trading: boolean
  stale: boolean
  error: string
  total_count: number
  refresh_seconds: number
  version: number
}

// 边缘缓存版本片：CF 免费版 Edge TTL 最小 2 小时，光靠源站 max-age=30 可能被抬高，
// 盘中价格就有陈旧风险。把 URL 挂一个 30 秒时间片后：
//   · 同一 30 秒窗口内所有用户 URL 相同 ⇒ 共享同一条 CF 边缘缓存，回源次数与用户数解耦；
//   · 跨窗口 URL 必变 ⇒ 即使边缘缓存 TTL 被抬到 2 小时，也永远绕到当前窗口的数据。
// CF 默认 Cache Key 含完整 query string，因此不同 v 是不同条目。
// /status、/realtime 是 no-store 探针，绝不加 v。
const cacheKey = () => Math.floor(Date.now() / 30000)

export const getBonds = (params?: { sort_by?: string; sort_order?: string }) =>
  api.get<BondVO[]>('/bonds', { params: { ...params, v: cacheKey() }, timeout: 90000 })

export const getStrategy = (key: string, n?: number) =>
  api.get<StrategyResultVO>(`/strategy/${key}`, { params: { n, v: cacheKey() }, timeout: 60000 })

export const getStrategies = () =>
  api.get<string[]>('/strategies')

export const getStatus = () =>
  api.get<DataStatusVO>('/status')

// 轻量探针：只返回 version/时间戳，version 变了才重拉 /bonds，避免每 30s 跨洋拉 234KB
export const getRealtime = () =>
  api.get<RealtimeStateVO>('/realtime')
