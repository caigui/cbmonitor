"""
策略引擎 - 两组策略，区别在「用法」不在「来源」：

  全市场排序（double_low / low_premium / low_price / high_yield）
      全市场按单一指标排序取前 N 只。单值可比、可画成曲线，
      用来看极端值（谁最便宜、谁的收益率最高），不适合直接当买入清单。
      口径来自集思录长期公开的四个通用统计维度。

  候选池（jgb_*）
      多条件区间筛选，输出候选，不做名次比较。
      源自集思录「捡钢镚」的四类分类选债思路。

2026-11-14 实测两组并集交集仅 3 只（Jaccard 1.7%），name 相似不等于重复：
「低价低溢价」设了溢价下界 20%，恰好把双低榜（溢价全 <20%）整批排除。
"""
import logging
from typing import List, Dict
from models.schemas import BondVO, StrategyResultVO

logger = logging.getLogger(__name__)

# 通用剔除条件
RATING_ORDER = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "BB", "B", "CCC", "CC", "C", "D"]
MIN_RATING = "A-"  # 剔除 A- 以下


def _rating_ok(rating: str) -> bool:
    """评级是否 >= A-"""
    if not rating:
        return False
    try:
        return RATING_ORDER.index(rating) <= RATING_ORDER.index(MIN_RATING)
    except ValueError:
        return False


def _filter_basic(bonds: List[BondVO]) -> List[BondVO]:
    """通用剔除：评级A-以下、到期"""
    result = []
    for b in bonds:
        if not _rating_ok(b.credit_rating):
            continue
        if b.remaining_years <= 0:
            continue
        result.append(b)
    return result


# ========== 全市场排序（通用统计口径，取前 N 看极端值） ==========

def strategy_double_low(bonds: List[BondVO], n: int = 10) -> StrategyResultVO:
    """双低策略：双低值升序取前N"""
    filtered = _filter_basic(bonds)
    filtered.sort(key=lambda x: x.double_low if x.double_low > 0 else 9999)
    selected = filtered[:n]
    return StrategyResultVO(
        strategy_name="双低策略", strategy_key="double_low",
        bonds=selected, filter_desc=f"双低值最低{n}只，剔除评级<A-及到期",
        data_date=bonds[0].data_date if bonds else ""
    )


def strategy_low_premium(bonds: List[BondVO], n: int = 10) -> StrategyResultVO:
    """低溢价策略：溢价率升序取前N"""
    filtered = _filter_basic(bonds)
    # 负溢价（转债价格低于转股价值）是本策略的最优标的，必须按真实数值参与排序。
    # 旧写法 `> 0 else 9999` 会把负溢价全部踢到队尾（2026-11-14 实测漏掉 113615/127110）。
    filtered.sort(key=lambda x: x.premium_rate if x.premium_rate is not None else 9999)
    selected = filtered[:n]
    return StrategyResultVO(
        strategy_name="低溢价策略", strategy_key="low_premium",
        bonds=selected, filter_desc=f"溢价率最低{n}只，剔除评级<A-及到期",
        data_date=bonds[0].data_date if bonds else ""
    )


def strategy_low_price(bonds: List[BondVO], n: int = 10) -> StrategyResultVO:
    """低价策略：价格升序取前N"""
    filtered = _filter_basic(bonds)
    filtered.sort(key=lambda x: x.bond_price if x.bond_price > 0 else 9999)
    selected = filtered[:n]
    return StrategyResultVO(
        strategy_name="低价策略", strategy_key="low_price",
        bonds=selected, filter_desc=f"价格最低{n}只，剔除评级<A-及到期",
        data_date=bonds[0].data_date if bonds else ""
    )


def strategy_high_yield(bonds: List[BondVO], n: int = 10) -> StrategyResultVO:
    """高收益率策略：只保留正 YTM，降序取前N；不足N只就如实返回，不拿负收益凑数。

    2026-11-14 之前会把负收益率债填进前10（实测前10里只有2只正收益）。
    高收益率债往往伴随信用风险，评级列的 ⚠ 标识用于提示，不改变选债口径。
    """
    filtered = _filter_basic(bonds)
    # 排除剩余年限 < 0.25年的（快到期的债收益率没意义）
    filtered = [b for b in filtered if b.remaining_years >= 0.25 and b.pre_tax_yield > 0]
    filtered.sort(key=lambda x: x.pre_tax_yield, reverse=True)
    selected = filtered[:n]
    return StrategyResultVO(
        strategy_name="高收益率策略", strategy_key="high_yield",
        bonds=selected,
        filter_desc=f"正YTM共{len(filtered)}只，取最高{len(selected)}只；剔除评级<A-、到期、剩余年限<0.25年",
        data_date=bonds[0].data_date if bonds else ""
    )


# ========== 候选池（多条件区间筛选，输出候选不是排名） ==========

def jgb_micro_cap(bonds: List[BondVO]) -> StrategyResultVO:
    """第1类：微盘转债 - 价格~130，规模<3亿"""
    filtered = _filter_basic(bonds)
    selected = [b for b in filtered if 115 <= b.bond_price <= 145 and 0 < b.remaining_size < 3]
    selected.sort(key=lambda x: x.bond_price)
    return StrategyResultVO(
        strategy_name="捡钢镚-微盘转债", strategy_key="jgb_micro_cap",
        bonds=selected,
        filter_desc="价格115-145，发行规模<3亿（非未转股余额），向下波动有限，弹性高",
        data_date=bonds[0].data_date if bonds else ""
    )


def jgb_low_price_low_premium(bonds: List[BondVO]) -> StrategyResultVO:
    """第2类：低价低溢价波段 - 价格<140，溢价~40%，0.5-5.5年，<10亿"""
    filtered = _filter_basic(bonds)
    selected = [b for b in filtered
                if b.bond_price < 140
                and 20 <= b.premium_rate <= 60
                and 0.5 <= b.remaining_years <= 5.5
                and 0 < b.remaining_size < 10]
    selected.sort(key=lambda x: x.bond_price)
    return StrategyResultVO(
        strategy_name="捡钢镚-中小盘波段", strategy_key="jgb_low_price_low_premium",
        bonds=selected,
        filter_desc="价格<140，溢价20-60%，0.5-5.5年，发行规模<10亿；区间框选，溢价下界 20% 与双低榜天然不重叠",
        data_date=bonds[0].data_date if bonds else ""
    )


def jgb_defensive(bonds: List[BondVO]) -> StrategyResultVO:
    """第3类：防守型 - 价格<130，溢价~70%，<10亿"""
    filtered = _filter_basic(bonds)
    selected = [b for b in filtered
                if b.bond_price < 130
                and 50 <= b.premium_rate <= 90
                and 0 < b.remaining_size < 10]
    selected.sort(key=lambda x: x.bond_price)
    return StrategyResultVO(
        strategy_name="捡钢镚-防守型", strategy_key="jgb_defensive",
        bonds=selected,
        filter_desc="价格<130，溢价50-90%，发行规模<10亿，防守为主",
        data_date=bonds[0].data_date if bonds else ""
    )


def jgb_active(bonds: List[BondVO]) -> StrategyResultVO:
    """第4类：活跃品种 - 价格~140，弹性尚可"""
    filtered = _filter_basic(bonds)
    selected = [b for b in filtered
                if 120 <= b.bond_price <= 155
                and b.elasticity > 0.05
                and b.remaining_size < 15]
    selected.sort(key=lambda x: x.elasticity, reverse=True)
    return StrategyResultVO(
        strategy_name="捡钢镚-活跃品种", strategy_key="jgb_active",
        bonds=selected,
        filter_desc="价格120-155，弹性>0.05，发行规模<15亿，概念丰富",
        data_date=bonds[0].data_date if bonds else ""
    )


# ========== 策略路由 ==========

ALL_STRATEGIES = {
    "double_low": strategy_double_low,
    "low_premium": strategy_low_premium,
    "low_price": strategy_low_price,
    "high_yield": strategy_high_yield,
    "jgb_micro_cap": jgb_micro_cap,
    "jgb_low_price_low_premium": jgb_low_price_low_premium,
    "jgb_defensive": jgb_defensive,
    "jgb_active": jgb_active,
}


def run_strategy(key: str, bonds: List[BondVO], n: int = 10) -> StrategyResultVO:
    """运行指定策略"""
    func = ALL_STRATEGIES.get(key)
    if not func:
        return StrategyResultVO(strategy_name="未知策略", strategy_key=key, filter_desc=f"策略 {key} 不存在")
    if key in ("double_low", "low_premium", "low_price", "high_yield"):
        return func(bonds, n)
    return func(bonds)
