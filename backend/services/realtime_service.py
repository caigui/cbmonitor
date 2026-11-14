"""
CBMonitor 实时行情层（2026-11-13）

设计要点：
  1. 服务端单一后台任务按固定节奏抓新浪（30s），结果放内存缓存；
     用户轮询只读内存 => 外网请求频率与用户数完全解耦，恒定 2 次/分钟。
  2. 交易时段门控（9:15-11:30 / 13:00-15:05，北京时间，ARM 时区已核为
     Asia/Shanghai）。非交易时段不抓，直接用档案里的收盘价。
  3. 实时层不写库。DB 的 cb_bond_profile 是唯一静态事实源，
     cb_daily_snapshot 保留为收盘存档/历史回看。
  4. 叠加实时价后复用 AKShareCollector._calculate_derived_metrics 重算
     双低/弹性/YTM/回售收益，保证与盘后口径 100% 一致。

实测（凤凰城 ARM -> 新浪 hq.sinajs.cn）：
  618 个代码合并单次请求 0.69s / 618 全覆盖；故按 500 个一批，约 2 次 HTTP。
"""
import os
import re
import sys
import asyncio
import logging
from datetime import datetime, time as dtime
from typing import Dict, List, Optional

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.database import db_manager
from services.data_collector import AKShareCollector
from services.calc_engine import is_new_listing

logger = logging.getLogger("CBMonitor.Realtime")

REFRESH_SECONDS = 30
BATCH_SIZE = 500
SINA_URL = "http://hq.sinajs.cn/list="
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn/",
}
# 集合竞价 9:15 起，收盘后留 5 分钟抓最后一份定格数据
MORNING = (dtime(9, 15), dtime(11, 30))
AFTERNOON = (dtime(13, 0), dtime(15, 5))


def is_trading_now(now: Optional[datetime] = None) -> bool:
    """北京时间交易时段门控；周末直接停。节假日由行情日期校验兜底。"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return (MORNING[0] <= t <= MORNING[1]) or (AFTERNOON[0] <= t <= AFTERNOON[1])


def _bond_prefix(code: str) -> str:
    return "sh" if code.startswith(("11", "13")) else "sz"


def _f(v: str) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _fetch_quotes(symbols: List[str]) -> Dict[str, dict]:
    """同步抓取，由调用方放进 asyncio.to_thread，绝不阻塞事件循环。"""
    session = requests.Session()
    session.trust_env = False
    session.headers.update(HEADERS)
    out: Dict[str, dict] = {}
    for i in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[i:i + BATCH_SIZE]
        try:
            resp = session.get(SINA_URL + ",".join(batch), timeout=10)
            resp.encoding = "gbk"
            for line in resp.text.split("\n"):
                m = re.match(r'var hq_str_(\w+)="(.*)"', line.strip())
                if not m:
                    continue
                f = m.group(2).split(",")
                if len(f) < 10:
                    continue
                out[m.group(1)] = {
                    "name": f[0],
                    "prev_close": _f(f[2]),
                    "price": _f(f[3]),
                    "volume": _f(f[8]),
                    "amount": _f(f[9]),
                    "date": f[30] if len(f) > 30 else "",
                    "time": f[31] if len(f) > 31 else "",
                }
        except Exception as e:
            logger.warning("[Sina] 实时批次 %d 失败: %s", i // BATCH_SIZE, e)
    return out


# 复用原计算引擎，保证衍生指标口径与盘后完全一致
_CALC = AKShareCollector()

# _calculate_derived_metrics 直接按 key 取值，档案若缺字段会 KeyError 并静默跳过重算。
# 补齐默认值，保证任何历史档案都能完整重算。
_CALC_DEFAULTS = {
    "pure_bond_value": 0.0,
    "maturity_redeem_price": 0.0,
    "put_value": 0.0,
    "remaining_years": 0.0,
    "maturity_date": "",
    "convert_value": 0.0,
    "bond_price": 0.0,
    "premium_rate": 0.0,
    "elasticity": 0.0,
    "pre_tax_yield": 0.0,
    "pre_tax_put_yield": 0.0,
}


def _apply_realtime(profile: List[dict], quotes: Dict[str, dict]) -> List[dict]:
    """档案 + 实时行情 -> 完整 BondVO dict（含所有价格驱动列重算）。"""
    out = []
    today = datetime.now().strftime("%Y-%m-%d")
    for base in profile:
        b = dict(base)
        terms = b.pop("_terms", {}) or {}
        bc = b.get("bond_code", "")
        sc = b.get("stock_code", "") or ""
        bq = quotes.get(_bond_prefix(bc) + bc) if bc else None
        sq = quotes.get(AKShareCollector._stock_market_prefix(sc) + sc) if sc else None

        if bq and bq["price"] > 0:
            b["bond_price"] = round(bq["price"], 3)
            if bq["prev_close"] > 0:
                b["bond_change_pct"] = round(
                    (bq["price"] - bq["prev_close"]) / bq["prev_close"] * 100, 2)
            b["turnover_amount"] = round(bq["amount"] / 10000, 2)  # 元 -> 万元
            size = b.get("remaining_size", 0) or 0
            if size > 0:
                b["turnover_rate"] = round(bq["volume"] / (size * 1000000) * 100, 2)

        if sq and sq["price"] > 0:
            b["stock_price"] = round(sq["price"], 2)
            if sq["prev_close"] > 0:
                b["stock_change_pct"] = round(
                    (sq["price"] - sq["prev_close"]) / sq["prev_close"] * 100, 2)

        # 上市首日无昨收：行情源会把昨收填成面值 100，涨跌幅一律归零（规则见 calc_engine）
        if is_new_listing(b, today):
            b["bond_change_pct"] = 0.0

        # 转股价值 / 溢价率：与 AKShare 一览表口径核对为零偏差（2026-11-13 实测 318 只）
        cp = b.get("convert_price", 0) or 0
        sp = b.get("stock_price", 0) or 0
        bp = b.get("bond_price", 0) or 0
        if cp > 0 and sp > 0:
            cv = 100.0 / cp * sp
            b["convert_value"] = round(cv, 4)
            if cv > 0 and bp > 0:
                b["premium_rate"] = round((bp - cv) / cv * 100, 2)

        # 双低 / 弹性 / 税前收益率 / 税前回售收益：复用原引擎，不复写简化公式
        try:
            for k, v in _CALC_DEFAULTS.items():
                b.setdefault(k, v)
            _CALC._calculate_derived_metrics(b, terms)
        except Exception as e:
            logger.warning("[Realtime] %s 衍生指标重算失败: %s", bc, e)

        out.append(b)
    return out


class RealtimeCache:
    """进程内实时缓存；后台任务写，API 读。"""

    def __init__(self):
        self._profile: List[dict] = []
        self._merged: List[dict] = []
        self._profile_date = ""
        self._quote_time = ""
        self._quote_date = ""
        self._is_trading = False
        self._stale = True
        self._error = ""
        self._failures = 0
        self._task: Optional[asyncio.Task] = None
        self._last_success: Optional[datetime] = None
        # 任何一次数据变化（档案重载 / 行情刷新）都会递增，前端据此决定是否重拉全量
        self._version = 0

    async def start(self):
        self._reload_profile()
        self._task = asyncio.create_task(self._loop())
        logger.info("[Realtime] 后台任务已启动，刷新间隔 %ss", REFRESH_SECONDS)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def _reload_profile(self):
        profile = db_manager.get_profile()
        if not profile:
            logger.warning("[Realtime] 档案为空，等待采集脚本写入")
            return
        self._profile = profile
        self._profile_date = db_manager.get_profile_date()
        self._merged = _apply_realtime(profile, {})
        self._version += 1
        logger.info("[Realtime] 已加载档案 %s / %d 只", self._profile_date, len(profile))

    def _symbols(self) -> List[str]:
        syms = []
        for b in self._profile:
            bc = b.get("bond_code", "")
            sc = b.get("stock_code", "") or ""
            if bc:
                syms.append(_bond_prefix(bc) + bc)
            pfx = AKShareCollector._stock_market_prefix(sc) if sc else ""
            if pfx:
                syms.append(pfx + sc)
        return list(dict.fromkeys(syms))

    async def _loop(self):
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[Realtime] tick 异常")
            await asyncio.sleep(REFRESH_SECONDS)

    async def _tick(self):
        # 采集脚本写库后自动生效，无需 reload 接口
        pdate = await asyncio.to_thread(db_manager.get_profile_date)
        if pdate and pdate != self._profile_date:
            await asyncio.to_thread(self._reload_profile)

        self._is_trading = is_trading_now()
        if not self._is_trading or not self._profile:
            # 盘后保留档案价（收盘价），不产生任何外网请求
            if not self._merged and self._profile:
                self._merged = _apply_realtime(self._profile, {})
            return

        quotes = await asyncio.to_thread(_fetch_quotes, self._symbols())
        valid = [q for q in quotes.values() if q["price"] > 0]
        if not valid:
            self._failures += 1
            self._error = "行情返回为空或全部无效"
            self._stale = True
            if self._failures in (3, 10, 30):
                logger.error("[Realtime] 连续 %d 次行情失败: %s", self._failures, self._error)
            return

        # 节假日兜底：新浪返回上一交易日数据时，不覆盖成"今天"的实时价
        today = datetime.now().strftime("%Y-%m-%d")
        dates = [q["date"] for q in valid if q["date"]]
        if dates and dates.count(today) < len(dates) * 0.5:
            self._is_trading = False
            self._error = "非交易日（行情日期非今日）"
            self._stale = True
            logger.info("[Realtime] %s，跳过实时覆盖", self._error)
            return

        self._merged = _apply_realtime(self._profile, quotes)
        now = datetime.now()
        self._quote_time = now.strftime("%H:%M:%S")
        self._quote_date = now.strftime("%Y-%m-%d")
        self._last_success = now
        self._stale = False
        self._error = ""
        self._failures = 0
        self._version += 1

    # ---- 供 API 读取 ----

    def get_bonds(self) -> List[dict]:
        return list(self._merged)

    def get_state(self) -> dict:
        # 盘后不该有实时行情，此时不报「延迟」，否则顶栏会长期误标黄色告警
        stale = self._is_trading and self._stale
        if self._is_trading and self._last_success:
            if (datetime.now() - self._last_success).total_seconds() > 90:
                stale = True
        return {
            "profile_date": self._profile_date,
            "quote_date": self._quote_date,
            "quote_time": self._quote_time,
            "is_trading": self._is_trading,
            "stale": stale,
            "error": self._error,
            "total_count": len(self._merged),
            "refresh_seconds": REFRESH_SECONDS,
            "version": self._version,
        }


realtime = RealtimeCache()
