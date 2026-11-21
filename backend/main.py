"""
CBMonitor - 可转债监控 FastAPI 主应用
参考 ArbDashboard/backend/main.py 的结构
"""
import os
import sys
import logging
from datetime import datetime
from typing import List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# 确保 models 和 services 可以被导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models.database import db_manager, init_database, CBRecord
from models.schemas import BondVO, StrategyResultVO, DataStatusVO
from services.realtime_service import realtime
from services.strategy_engine import run_strategy, ALL_STRATEGIES
from services.calc_engine import bond_risk_flag

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("CBMonitor")

app = FastAPI(
    title="CBMonitor - 可转债监控",
    description="可转债数据采集、策略分析、投资决策支持",
    version="1.0.0"
)

# CORS - 允许前端跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _record_to_vo(record: CBRecord) -> BondVO:
    """ORM 记录转 VO"""
    return BondVO(
        bond_code=record.bond_code or "",
        bond_name=record.bond_name or "",
        stock_code=record.stock_code or "",
        stock_name=record.stock_name or "",
        bond_price=record.bond_price or 0.0,
        bond_change_pct=record.bond_change_pct or 0.0,
        stock_price=record.stock_price or 0.0,
        stock_change_pct=record.stock_change_pct or 0.0,
        convert_price=record.convert_price or 0.0,
        convert_value=record.convert_value or 0.0,
        premium_rate=record.premium_rate or 0.0,
        double_low=record.double_low or 0.0,
        pre_tax_yield=record.pre_tax_yield or 0.0,
        pre_tax_put_yield=record.pre_tax_put_yield or 0.0,
        remaining_years=record.remaining_years or 0.0,
        remaining_size=record.remaining_size or 0.0,
        maturity_date=record.maturity_date or "",
        credit_rating=record.credit_rating or "",
        risk_flag=bond_risk_flag(record.stock_name or "", record.credit_rating or ""),
        pure_bond_value=record.pure_bond_value or 0.0,
        put_value=record.put_value or 0.0,
        # 到期赎回价存于 payload JSON（非独立列）；DB 回退路径取不到则留 0，
        # 主路径 _current_bonds() 由 realtime 字典直接带值。C 策略「临期债」依赖此字段。
        maturity_redeem_price=getattr(record, "maturity_redeem_price", 0.0) or 0.0,
        elasticity=record.elasticity or 0.0,
        pb_ratio=record.pb_ratio or 0.0,
        redeem_trigger_price=record.redeem_trigger_price or 0.0,
        put_trigger_price=record.put_trigger_price or 0.0,
        turnover_rate=record.turnover_rate or 0.0,
        turnover_amount=record.turnover_amount or 0.0,
        historical_volatility=record.historical_volatility or 0.0,
        ma20_premium_rate=record.ma20_premium_rate or 0.0,
        theoretical_value=record.theoretical_value or 0.0,
        deviation=record.deviation or 0.0,
        data_date=record.data_date or "",
        source=record.source or "",
    )


def _current_bonds() -> List[BondVO]:
    """主看板数据：静态档案 + 盘中实时行情（内存），档案未就绪时回退 DB 最新快照。"""
    merged = realtime.get_bonds()
    if merged:
        # BondVO 已声明 maturity_redeem_price，realtime 字典带此字段会正确透传（C 策略依赖）
        vos = [BondVO(**b) for b in merged]
        # 风险标识统一在此计算（规则只在 calc_engine.bond_risk_flag 一处），
        # 主看板 / 8 个策略集 / 刷新接口共用同一份 VO，不会出现口径分叉。
        for v in vos:
            v.risk_flag = bond_risk_flag(v.stock_name, v.credit_rating)
        return vos
    return [_record_to_vo(r) for r in db_manager.get_latest_bonds()]


# ========== 生命周期 ==========

@app.on_event("startup")
async def on_startup():
    init_database()
    await realtime.start()
    # C 策略集钉钉提醒（从 master 增量移植）：daemon 线程，失败不影响主服务
    try:
        from strategy_c_alert import start_background
        start_background(interval=3600)
        logger.info("C策略集提醒线程已启动（每 1 小时）")
    except Exception as e:
        logger.warning("C策略集提醒线程启动失败（不影响主服务）: %s" % e)


@app.on_event("shutdown")
async def on_shutdown():
    await realtime.stop()


# ========== API 路由 ==========

@app.get("/")
async def root():
    return {"message": "CBMonitor API 运行中", "version": "2.0.0"}


@app.get("/api/status", response_model=DataStatusVO)
async def get_status():
    """静态档案状态 + 实时行情状态，两个时间戳分开报"""
    st = db_manager.get_status()
    s = realtime.get_state()
    return DataStatusVO(
        data_date=st.data_date if st else "",
        source=st.source if st else "",
        total_count=s["total_count"] or (st.total_count if st else 0),
        updated_at=st.updated_at.strftime("%Y-%m-%d %H:%M:%S") if st and st.updated_at else "",
        profile_date=s["profile_date"],
        quote_date=s["quote_date"],
        quote_time=s["quote_time"],
        is_trading=s["is_trading"],
        stale=s["stale"],
        error=s["error"],
        refresh_seconds=s["refresh_seconds"],
        version=s["version"],
    )


@app.get("/api/realtime")
async def get_realtime():
    """轻量探针：前端每 30s 打这个，version 变化才重拉 /api/bonds（省跨洋回源）"""
    return realtime.get_state()


@app.get("/api/bonds", response_model=List[BondVO])
async def get_bonds(
    date: str = Query("", description="数据日期，空=最新实时"),
    sort_by: str = Query("double_low", description="排序字段"),
    sort_order: str = Query("asc", description="排序方向 asc/desc"),
):
    """获取可转债列表；带 date 时读历史收盘快照，否则读盘中实时数据"""
    if date:
        bonds = [_record_to_vo(r) for r in db_manager.get_snapshot_by_date(date)]
    else:
        bonds = _current_bonds()

    reverse = sort_order == "desc"
    if sort_by and hasattr(bonds[0] if bonds else None, sort_by):
        bonds.sort(key=lambda x: getattr(x, sort_by, 0) or 0, reverse=reverse)
    return bonds


@app.get("/api/strategy/{strategy_key}", response_model=StrategyResultVO)
async def get_strategy(
    strategy_key: str,
    n: int = Query(10, description="选取数量"),
    date: str = Query("", description="数据日期，空=最新"),
):
    """运行策略筛选"""
    if strategy_key not in ALL_STRATEGIES:
        raise HTTPException(status_code=404, detail=f"策略不存在: {strategy_key}")

    if date:
        records = db_manager.get_snapshot_by_date(date)
        bonds = [_record_to_vo(r) for r in records]
    else:
        # 与主看板同一份实时数据，避免「主看板实时 / 策略集静止」的口径分裂
        bonds = _current_bonds()

    if not bonds:
        raise HTTPException(status_code=404, detail="暂无数据，请先采集")

    return run_strategy(strategy_key, bonds, n)


@app.get("/api/strategies", response_model=List[str])
async def list_strategies():
    """列出所有可用策略"""
    return list(ALL_STRATEGIES.keys())


# ⚠️ POST /api/collect 已移除（2026-11-13）。
#   原因：原实现在 async def 里同步跑 30~45s 采集，会阻塞 uvicorn 事件循环；
#   采集改由独立脚本 collect_once.py + systemd timer 承担，服务只读。
#   nginx 的 `location = /cb_api/collect { return 403; }` 保留作纵深防御。


# ========== 启动 ==========

if __name__ == "__main__":
    import uvicorn
    init_database()
    logger.info("=" * 50)
    logger.info("  CBMonitor - 可转债监控系统")
    logger.info("  API: http://127.0.0.1:8100")
    logger.info("  Docs: http://127.0.0.1:8100/docs")
    logger.info("=" * 50)
    try:
        # 只监听本机，公网经 nginx 反代进入（绝不听 0.0.0.0）
        uvicorn.run(app, host="127.0.0.1", port=8100)
    except KeyboardInterrupt:
        logger.info("服务器已停止")
