# -*- coding: utf-8 -*-
"""
backend/strategy_c_alert.py —— C策略集「临期债 / 收益率为正 / 面值债」钉钉提醒
=====================================================================
启动即定时执行，每 1 小时一轮。三策略任一有命中（触发）即推送钉钉到既有群。

监控策略与触发条件（来自 strategy_engine.C 策略集）
---------------------------------------------------
| 策略       | key            | 触发条件                                    |
|------------|----------------|---------------------------------------------|
| 临期债     | near_maturity  | 剩余年限<0.5年 且 现价-到期赎回价<1元        |
| 收益率为正 | positive_yield | 税前收益率>1% 且 评级AA-及以上               |
| 面值债     | par_bond       | 现价<104元 且 评级AA-及以上                  |

数据来源：**直接读本地 SQLite 最新快照**（db_manager.get_latest_bonds），
再跑与前端完全相同的 strategy_engine.run_strategy —— 保证提醒与页面口径一致，
不新增任何取数逻辑。快照由 /api/collect 采集更新（约 30 秒一轮数据）。

同一标的+同一策略当日只提醒一次（去重），避免每小时刷屏；
价格变动导致新一轮新命中会正常提醒。

钉钉推送：复用 backend/dingtalk_utils.DingTalkNotifier（含 12:30–13:00 静默窗口）。

用法
----
    python backend/strategy_c_alert.py              # 常驻，每 1 小时一轮
    python backend/strategy_c_alert.py --once --dry-run   # 只跑一轮，打印不发送
    python backend/strategy_c_alert.py --interval 3600    # 自定义间隔秒

被 backend/main.py 启动时走 start_background()，作为 daemon 线程随服务一起跑。
"""
from __future__ import print_function

import argparse
import datetime
import os
import sys
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

INTERVAL = 3600                             # 1 小时
TITLE = "C策略集可转债提醒（临期债/收益率为正/面值债）"
LOG_FILE = os.path.join(os.path.dirname(_HERE), "log", "strategy_c_alert.log")

# 三个 C 策略：(key, 中文名)
C_STRATEGIES = [
    ("near_maturity", "临期债"),
    ("positive_yield", "收益率为正"),
    ("par_bond", "面值债"),
]

# {当日: {(code, key): True}} —— 同一标的同一策略当日已提醒去重
_ALERTED = {}
_NOTIFIER = None


def _logf(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        d = os.path.dirname(LOG_FILE)
        if not os.path.isdir(d):
            os.makedirs(d)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line)
    except Exception:
        pass


def _notifier():
    global _NOTIFIER
    if _NOTIFIER is None:
        try:
            from dingtalk_utils import DingTalkNotifier
            _NOTIFIER = DingTalkNotifier()
        except Exception as e:
            _logf("钉钉通知器初始化失败: %s" % e)
            _NOTIFIER = False
    return _NOTIFIER or None


def _fmt(v, nd=2, suffix=""):
    try:
        return ("%." + str(nd) + "f") % float(v) + suffix
    except (TypeError, ValueError):
        return "—"


def fetch_and_judge():
    """读最新快照 → 跑三个 C 策略 → 返回 [(策略名, key, bonds, 命中列表), ...]。"""
    from models.database import db_manager
    from services.strategy_engine import run_strategy
    from main import _record_to_vo

    recs = db_manager.get_latest_bonds()
    if not recs:
        return None, "数据库无快照，请先 /api/collect 采集"
    bonds = [_record_to_vo(r) for r in recs]
    data_date = bonds[0].data_date if bonds else ""

    results = []
    for key, name in C_STRATEGIES:
        res = run_strategy(key, bonds)
        results.append((name, key, res, data_date))
    return results, None


def dedup_and_hits(results):
    """按当日去重，返回 (明细行列表, 本轮真正要提醒的命中列表)。"""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    store = _ALERTED.setdefault(today, set())
    details, hits = [], []
    for name, key, res, data_date in results:
        for b in res.bonds:
            ident = (b.bond_code, key)
            is_new = ident not in store
            details.append({
                "strategy": name, "key": key, "code": b.bond_code,
                "name": b.bond_name, "price": b.bond_price,
                "ytm": b.pre_tax_yield, "years": b.remaining_years,
                "redeem": b.maturity_redeem_price, "rating": b.credit_rating,
                "is_new": is_new,
            })
            if is_new:
                hits.append(details[-1])
    return details, hits, store


def build_markdown(results, details, hits, data_date):
    """拼钉钉 markdown。"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_hit = sum(len(r[2].bonds) for r in results)
    head = ("### %s\n\n> %s · 数据日期 %s · 触发 **%d** / 本轮新命中 **%d**\n\n"
            % (TITLE, now, data_date or "—", total_hit, len(hits)))

    # 分策略展示命中明细
    body = ""
    for name, key, res, _ in results:
        body += "\n#### %s（命中 %d 只）\n\n" % (name, len(res.bonds))
        if not res.bonds:
            body += "> 无\n"
            continue
        body += "| 标记 | 代码 | 名称 | 现价 | 税前收益率 | 剩余年限 | 到期赎回价 | 评级 |\n"
        body += "|---|---|---|---|---|---|---|---|\n"
        for b in res.bonds[:30]:  # 单策略最多列 30 行，防止刷屏
            ident = (b.bond_code, key)
            flag = "🔴新" if any(h["code"] == b.bond_code and h["key"] == key for h in hits) else "—"
            body += ("| %s | %s | %s | %s | %s | %s年 | %s | %s |\n" % (
                flag, b.bond_code, b.bond_name,
                _fmt(b.bond_price, 3), _fmt(b.pre_tax_yield, 2, "%"),
                _fmt(b.remaining_years, 2), _fmt(b.maturity_redeem_price, 1),
                b.credit_rating or "—"))
        if len(res.bonds) > 30:
            body += "\n> ...另有 %d 只未列出\n" % (len(res.bonds) - 30)

    # 判定条件说明
    cond = ("\n\n#### 判定条件\n\n"
            "- **临期债**：剩余年限 < 0.5 年，且 现价 − 到期赎回价 < 1 元\n"
            "- **收益率为正**：税前收益率 > 1%，且评级 AA- 及以上\n"
            "- **面值债**：现价 < 104 元，且评级 AA- 及以上\n")
    return head + body + cond


def run_once(dry_run=False):
    results, err = fetch_and_judge()
    if err:
        _logf("取数失败: %s" % err)
        return {"ok": False, "sent": False, "hits": 0, "reason": err}

    details, hits, store = dedup_and_hits(results)
    data_date = results[0][3] if results else ""
    total_hit = sum(len(r[2].bonds) for r in results)

    if not hits:
        _logf("无新增触发（命中 %d 只，均已提醒过）" % total_hit)
        return {"ok": True, "sent": False, "hits": 0, "items": details, "reason": "无新增触发"}

    msg = build_markdown(results, details, hits, data_date)

    if dry_run:
        print("\n" + "=" * 70 + "\n" + msg + "=" * 70 + "\n")
        _logf("dry-run：新命中 %d 只，消息已打印未发送" % len(hits))
        return {"ok": True, "sent": False, "hits": len(hits), "reason": "dry-run"}

    bot = _notifier()
    if not bot:
        _logf("钉钉通知器不可用，未发送（新命中 %d 只）" % len(hits))
        return {"ok": False, "sent": False, "hits": len(hits), "reason": "通知器不可用"}

    ok = bot.send_alert(msg, TITLE)
    if ok:
        # 发送成功才落去重标记（失败/静默窗口不吞信号，下轮重试）
        for h in hits:
            store.add((h["code"], h["key"]))
        _logf("已发送钉钉：新命中 %d 只（%s）" % (
            len(hits), "、".join("%s-%s" % (h["strategy"], h["code"]) for h in hits[:12])))
    else:
        _logf("钉钉未发送（静默窗口或失败），新命中 %d 只，下轮重试" % len(hits))
    return {"ok": True, "sent": bool(ok), "hits": len(hits),
            "reason": "已发送" if ok else "静默窗口/未发送"}


def run_forever(interval=INTERVAL, **kw):
    _logf("C策略提醒启动：间隔 %d 秒，监控 3 策略（临期债/收益率为正/面值债）" % interval)
    while True:
        t0 = time.time()
        try:
            run_once(**kw)
        except KeyboardInterrupt:
            _logf("C策略提醒已停止")
            raise
        except Exception:
            _logf("本轮异常（继续下一轮）:\n%s" % traceback.format_exc())
        time.sleep(max(1.0, interval - (time.time() - t0)))


def start_background(interval=INTERVAL, **kw):
    """给 backend/main.py 用：挂 daemon 线程，随服务启动一起跑。"""
    import threading
    th = threading.Thread(target=_bg_guard, args=(interval, kw),
                          name="strategy_c_alert", daemon=True)
    th.start()
    return th


def _bg_guard(interval, kw):
    try:
        run_forever(interval=interval, **kw)
    except Exception:
        _logf("C策略提醒线程退出:\n%s" % traceback.format_exc())


def main():
    try:
        sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="C策略集可转债钉钉提醒（每 1 小时）")
    ap.add_argument("--once", action="store_true", help="只跑一轮后退出")
    ap.add_argument("--dry-run", action="store_true", help="只打印消息，不发钉钉")
    ap.add_argument("--interval", type=int, default=INTERVAL, help="轮询间隔秒（默认 3600）")
    a = ap.parse_args()
    kw = dict(dry_run=a.dry_run)
    if a.once:
        r = run_once(**kw)
        print("结果: %s" % r)
        return 0 if r.get("ok") else 1
    try:
        run_forever(interval=a.interval, **kw)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
