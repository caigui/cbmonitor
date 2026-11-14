"""
CBMonitor 独立采集脚本（静态档案 + 收盘存档）

由 systemd timer 在交易日 09:05 / 15:35 / 16:30 / 20:00 调用，进程独立于 uvicorn，
绝不阻塞 Web 服务。三种模式：
  pre   (09:05)      建立当天档案，让当日新上市转债可见；当天已有数据则跳过
  close (15:35)      强制重采，覆盖当天快照为收盘数据
  retry (16:30/20:00) 仅当 15:35 未成功时补跑

质量闸门：条数、到期日、条款完整度、退市债，任一不过则不写库、退出码 1、告警，
保留上一日档案，避免把脏数据推给盘中看板。
"""
import os
import sys
import argparse
import logging
import subprocess
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models.database import db_manager, init_database
from services.data_collector import collector
from services.calc_engine import is_new_listing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("CBCollect")

MIN_BONDS = 250
MIN_TERMS = 800
# 条款是低频静态数据：日常只查新上市债，每 7 天全量重建一次校准退市/评级变动。
TERMS_REFRESH_DAYS = 7


def alert(msg: str):
    logger.error(msg)
    cmd = os.environ.get("CB_ALERT_CMD", "").strip()
    if cmd:
        try:
            subprocess.run([cmd, msg], timeout=20, check=False)
        except Exception as e:
            logger.error("告警发送失败: %s", e)


def is_trading_day(today: str) -> bool:
    """法定节假日兜底：新浪行情日期非今天则不采，避免写出 10-01 那种空快照。

    一次轻量请求（约 0.5s）；接口不可用时返回 True，不阻断正常采集。
    """
    try:
        s = requests.Session()
        s.trust_env = False
        s.headers.update({"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"})
        r = s.get("http://hq.sinajs.cn/list=sh113050", timeout=8)
        r.encoding = "gbk"
        f = r.text.split('"')[1].split(",") if '"' in r.text else []
        d = f[30] if len(f) > 30 else ""
        return (not d) or d == today
    except Exception as e:
        logger.warning("交易日检查不可用，按交易日继续: %s", e)
        return True


def should_skip(mode: str, today: str) -> bool:
    st = db_manager.get_status()
    if not st or st.data_date != today:
        return False
    if mode == "pre":
        return True
    if mode == "retry":
        # 15:35 那次成功写过（updated_at 在 15 点后）就无需补跑
        return bool(st.updated_at and st.updated_at.hour >= 15)
    return False


def quality_gate(bonds, terms, today: str):
    """返回 (是否通过, 说明)。失败一律不写库。"""
    if len(bonds) < MIN_BONDS:
        return False, f"条数过少: {len(bonds)} < {MIN_BONDS}"
    if len(terms) < MIN_TERMS:
        return False, f"东财条款不完整: {len(terms)} < {MIN_TERMS}（退市债过滤会失灵）"
    for b in bonds:
        code = b.get("bond_code", "")
        name = b.get("bond_name", "")
        maturity = (b.get("maturity_date", "") or "")[:10]
        if "退" in name or code.startswith("40"):
            return False, f"疑似退市债混入: {code} {name}"
        if maturity and maturity < today:
            return False, f"已到期债混入: {code} {name} maturity={maturity}"
    return True, f"条数 {len(bonds)} / 条款 {len(terms)} / 无到期退市债"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=["pre", "close", "retry", "auto"], default="auto",
        help="auto 按当前时间推断：09:xx=pre / 15:xx=close / 其余=retry",
    )
    parser.add_argument(
        "--full-terms", action="store_true",
        help="强制全量重建东财条款缓存（默认按缓存年龄自动判断）",
    )
    args = parser.parse_args()

    init_database()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    mode = args.mode
    if mode == "auto":
        hm = now.hour * 100 + now.minute
        mode = "pre" if hm < 1000 else ("close" if hm < 1600 else "retry")

    if not is_trading_day(today):
        logger.info("[%s] 非交易日（%s），跳过", mode, today)
        return 0

    if should_skip(mode, today):
        logger.info("[%s] 当天已有有效数据，幂等跳过", mode)
        return 0

    logger.info("[%s] 开始采集 %s", mode, today)
    cached = db_manager.get_terms()
    age = db_manager.terms_refresh_age_days()
    need_full = (args.full_terms or not cached
                 or (age is not None and age >= TERMS_REFRESH_DAYS))
    logger.info("[%s] 条款缓存 %d 条 / 最老 %.1f 天 ⇒ %s",
                mode, len(cached), age if age is not None else -1,
                "全量重建" if need_full else "只查新债（增量）")
    try:
        bonds, terms, added = collector.collect_with_terms(
            "auto", terms_cache=cached, full_terms=need_full)
    except Exception as e:
        alert(f"CBMonitor 采集异常（{mode} {today}）: {e}")
        return 1

    ok, detail = quality_gate(bonds, terms, today)
    if not ok:
        alert(f"CBMonitor 质量闸门拒绝写库（{mode} {today}）: {detail}")
        return 1

    # 上市首日无昨收，涨跌幅统一归零（与实时层共用 calc_engine 的同一条规则）
    for b in bonds:
        if is_new_listing(b, today):
            b["bond_change_pct"] = 0.0

    try:
        db_manager.save_profile(today, bonds, terms)
        db_manager.save_daily_snapshot(today, bonds, source="auto")
        # 质量闸门通过后才写条款缓存；失败的单代码不在 added 里，下次会自动重试。
        db_manager.save_terms(added)
    except Exception as e:
        alert(f"CBMonitor 写库失败（{mode} {today}）: {e}")
        return 1

    logger.info("[%s] 完成: %s | 条款缓存写回 %d 条", mode, detail, len(added))
    return 0


if __name__ == "__main__":
    sys.exit(main())
