"""
可转债数据模型 - SQLAlchemy ORM
参考 arbTest/arbcore/database/db_manager.py 的设计模式
"""
import os
import json
from datetime import datetime
from typing import List, Optional, Dict
from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime, Text, event, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from contextlib import contextmanager

# ---- 程序根定位（双端兼容，详见 <ARB>/README.md §2）----
#   本地: src/backend/models/database.py → 含 "src" 的那层父目录的父 = 程序根
#   ARM : /home/ubuntu/cbmonitor/backend/models/... → 上溯 2 层即程序根（扁平布局，无 src 层）
# ⚠️ 绝不用 dirname(dirname(__file__)) 当程序根：本地会算成 src/backend，DB 落进 src/ 内。
_MODELS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_MODELS_DIR)
_PARENT = os.path.dirname(_BACKEND_DIR)                      # 本地 = .../CBMonitor/src，ARM = 程序根
PROJ_ROOT = os.path.dirname(_PARENT) if os.path.basename(_PARENT) == "src" else _PARENT

# ---- 数据库目录与 src/ 平级（2026-10-07 搬迁）----
# 🔴 必须与 src/ 物理隔离：db 落在 src/ 内会进 git 仓库（有被 push 的风险），
#    且按部署约定推 src/ 上 ARM 时会覆盖生产库。
DB_DIR = os.path.join(PROJ_ROOT, "database")
DB_PATH = os.path.join(DB_DIR, "cb_monitor.db")
os.makedirs(DB_DIR, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
    echo=False
)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class CBRecord(Base):
    """可转债每日快照表"""
    __tablename__ = "cb_daily_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    data_date = Column(String(10), nullable=False, index=True)
    bond_code = Column(String(10), nullable=False, index=True)
    bond_name = Column(String(50), nullable=False)
    stock_code = Column(String(10), default="")
    stock_name = Column(String(50), default="")

    bond_price = Column(Float, default=0.0)
    bond_change_pct = Column(Float, default=0.0)
    stock_price = Column(Float, default=0.0)
    stock_change_pct = Column(Float, default=0.0)

    convert_price = Column(Float, default=0.0)
    convert_value = Column(Float, default=0.0)
    premium_rate = Column(Float, default=0.0)

    double_low = Column(Float, default=0.0)
    pre_tax_yield = Column(Float, default=0.0)
    pre_tax_put_yield = Column(Float, default=0.0)

    remaining_years = Column(Float, default=0.0)
    remaining_size = Column(Float, default=0.0)
    maturity_date = Column(String(10), default="")

    credit_rating = Column(String(10), default="")

    pure_bond_value = Column(Float, default=0.0)
    put_value = Column(Float, default=0.0)
    # 到期赎回价（含末期利息，东财 REDEEM_CLAUSE 真值）。DB 表已有此列（迁移加入），
    # ORM 模型补声明后 C 策略「临期债」才能从快照读到真值（否则 getattr 恒为 0）。
    maturity_redeem_price = Column(Float, default=0.0)
    elasticity = Column(Float, default=0.0)
    pb_ratio = Column(Float, default=0.0)

    redeem_trigger_price = Column(Float, default=0.0)
    put_trigger_price = Column(Float, default=0.0)

    turnover_rate = Column(Float, default=0.0)
    turnover_amount = Column(Float, default=0.0)

    historical_volatility = Column(Float, default=0.0)
    ma20_premium_rate = Column(Float, default=0.0)
    theoretical_value = Column(Float, default=0.0)
    deviation = Column(Float, default=0.0)

    source = Column(String(20), default="ninwin")
    created_at = Column(DateTime, default=datetime.now)


class CBProfile(Base):
    """当前债券档案（DB 唯一权威）。

    与 cb_daily_snapshot 的分工：
      - cb_daily_snapshot：历史收盘存档，一天一条，供回看/对账，只增不覆盖语义
      - cb_bond_profile  ：当前工作集，保存「完整 bond dict + 东财条款」，
                           盘中实时层以它为基准叠加实时行情。
    存完整 payload 而非 ORM 列，是为了不丢失 maturity_redeem_price / terms 等
    重算 YTM 必需的中间量（这些字段原本写库时会被丢弃）。
    """
    __tablename__ = "cb_bond_profile"

    bond_code = Column(String(10), primary_key=True)
    data_date = Column(String(10), nullable=False, index=True)
    payload = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class DataStatusRecord(Base):
    """数据采集状态表"""
    __tablename__ = "data_status"

    id = Column(Integer, primary_key=True, autoincrement=True)
    data_date = Column(String(10), nullable=False, unique=True)
    source = Column(String(20), default="")
    total_count = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.now)


class CBTerms(Base):
    """东方财富条款缓存（低频静态数据）。

    与 cb_bond_profile 的分工：档案只存「当前活跃债」的完整工作集；
    本表存「全市场历史债」的条款（含已退市债），采集时用于过滤退市/到期、
    补全到期日与票息。条款几乎不变，只有新债上市才需要新增，因此本地常驻，
    不再每天全量翻东财 11 页（跨洋 30~45s 且末页常超时）。
    """
    __tablename__ = "cb_bond_terms"

    bond_code = Column(String(10), primary_key=True)
    payload = Column(Text, nullable=False)
    fetched_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


def init_database():
    Base.metadata.create_all(bind=engine)
    print(f"[DB] 数据库初始化完成: {DB_PATH}")


@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class CBDatabaseManager:
    """可转债数据库管理器 - 参考 arbcore.database.db_manager.DatabaseManager"""

    def save_daily_snapshot(self, data_date: str, bonds: List[dict], source: str = "ninwin"):
        with get_db() as db:
            db.query(CBRecord).filter(CBRecord.data_date == data_date).delete()
            for b in bonds:
                cols = CBRecord.__table__.columns.keys()
                record = CBRecord(
                    data_date=data_date, source=source,
                    **{k: v for k, v in b.items() if k in cols and k not in ('id', 'created_at', 'data_date', 'source')}
                )
                db.add(record)

            status = db.query(DataStatusRecord).filter(DataStatusRecord.data_date == data_date).first()
            if status:
                status.source = source
                status.total_count = len(bonds)
                status.updated_at = datetime.now()
            else:
                db.add(DataStatusRecord(data_date=data_date, source=source, total_count=len(bonds)))
            db.commit()
            print(f"[DB] 保存 {data_date} 快照: {len(bonds)} 条")

    def get_latest_bonds(self) -> List[CBRecord]:
        with get_db() as db:
            latest = db.query(CBRecord.data_date).order_by(CBRecord.data_date.desc()).first()
            if not latest:
                return []
            return db.query(CBRecord).filter(CBRecord.data_date == latest[0]).all()

    def get_snapshot_by_date(self, data_date: str) -> List[CBRecord]:
        """获取指定日期的数据快照"""
        with get_db() as db:
            return db.query(CBRecord).filter(
                CBRecord.data_date == data_date
            ).all()

    def get_status(self) -> Optional[DataStatusRecord]:
        with get_db() as db:
            return db.query(DataStatusRecord).order_by(DataStatusRecord.data_date.desc()).first()

    # ---- 当前档案（实时层的静态基准）----

    def save_profile(self, data_date: str, bonds: List[dict], terms_map: Dict[str, dict]):
        """整表替换当前档案。与 save_daily_snapshot 在同一次采集里写入，同源、无第二事实源。"""
        with get_db() as db:
            db.query(CBProfile).delete()
            for b in bonds:
                payload = dict(b)
                payload["_terms"] = terms_map.get(b.get("bond_code", ""), {}) or {}
                db.add(CBProfile(
                    bond_code=b.get("bond_code", ""),
                    data_date=data_date,
                    payload=json.dumps(payload, ensure_ascii=False, default=str),
                ))
            db.commit()
            print(f"[DB] 保存 {data_date} 档案: {len(bonds)} 条")

    def get_profile(self) -> List[dict]:
        """读取当前档案为 list[dict]（含 _terms）。"""
        with get_db() as db:
            rows = db.query(CBProfile).all()
        out = []
        for r in rows:
            try:
                out.append(json.loads(r.payload))
            except (ValueError, TypeError):
                continue
        return out

    def get_profile_date(self) -> str:
        with get_db() as db:
            row = db.query(CBProfile.data_date).first()
            return row[0] if row else ""

    # ---- 东财条款缓存（2026-11-14 增量采集）----

    def get_terms(self) -> Dict[str, dict]:
        """读取全部缓存条款；空表返回 {}（首次采集会走全量）。"""
        with get_db() as db:
            rows = db.query(CBTerms).all()
        out = {}
        for r in rows:
            try:
                out[r.bond_code] = json.loads(r.payload)
            except (ValueError, TypeError):
                continue
        return out

    def save_terms(self, terms: Dict[str, dict]) -> int:
        """upsert 传入的条款；只覆盖传入代码，绝不删其他历史条目。

        全量重建时调用方传入完整 terms ⇒ 所有 fetched_at 刷新为今天；
        增量时只传新增代码 ⇒ 老条目年龄继续增长，到期触发下一次全量重建。
        """
        if not terms:
            return 0
        now = datetime.now()
        rows = [
            {"c": code, "p": json.dumps(t, ensure_ascii=False, default=str), "f": now}
            for code, t in terms.items()
        ]
        with get_db() as db:
            db.execute(
                text("INSERT OR REPLACE INTO cb_bond_terms "
                     "(bond_code, payload, fetched_at) VALUES (:c, :p, :f)"),
                rows,
            )
            db.commit()
        return len(rows)

    def terms_refresh_age_days(self) -> Optional[float]:
        """缓存里最老一条的年龄（天）；空表返回 None。用于决定是否全量重建。"""
        with get_db() as db:
            row = db.query(CBTerms.fetched_at).order_by(CBTerms.fetched_at.asc()).first()
        if not row or not row[0]:
            return None
        return (datetime.now() - row[0]).total_seconds() / 86400.0


db_manager = CBDatabaseManager()
