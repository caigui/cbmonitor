python -m venv .venv

.\.venv\Scripts\activate.bat

python -m pip install -r requirements.txt

创建数据库

=====================================================================
2026-11-21 更新：C 策略集 + 税前收益率修正 + 1小时钉钉提醒
=====================================================================

【1】C 策略集（前端「C策略集」Tab，3 个策略）
  - 临期债  (near_maturity)：剩余年限 < 0.5 年，且 现价 - 到期赎回价 < 1 元
  - 收益率为正 (positive_yield)：税前收益率 > 1%，且评级 AA- 及以上
  - 面值债  (par_bond)：现价 < 104 元，且评级 AA- 及以上
  评级归一化：AA+sti / AA-sti 等带后缀写法会归一到 AA+ / AA- 再判断。
  【sti 含义澄清 2026-11-21】sti = Science Technology Innovation（科技创新），
  是评级机构对「采用科创企业评级方法 / 募资投向科创领域」的债项加注的特殊标识，
  不是「私募」标识，等级符号本身（AA+/AA/AA-）与普通评级同义。
  例：国微转债 127038 = AA+sti（中诚信科创评级方法），晶能转债 118034 = AA+sti。
  修正：原 _rating_ok() 只做精确字符串匹配，导致 22 只带 sti 的科创转债
  （其中 21 只评级>=A-）在 A/B 策略集的通用过滤中被整体误剔除；
  现 _rating_ok 与 C 策略共用 _norm_rating，A/B/C 全部策略均正确处理 sti。
  API: GET /api/strategy/{near_maturity|positive_yield|par_bond}
  这三个策略返回全部命中（不截断前 N）。

【2】税前收益率修正（重要数据修复）
  根因：原把东财 EXECUTE_PRICE_SH/HS（强赎执行价，典型 103 元）误当成
        「到期赎回价」用于 YTM 现金流 → 收益率算出 -113%~+32% 荒谬值（73 只 |YTM|>10%）。
  修正：到期赎回价改从 REDEEM_CLAUSE 条款文本解析「面值的 105%」（新增
        parse_maturity_redeem_price()，覆盖率 475/500），解析失败才回退强赎价。
  结果：重采集后 |YTM|>10% 降到 34 只（剩余均为 300~700 元高价转债，YTM 为负属数学合理）。
  数据库新增列 cb_daily_snapshot.maturity_redeem_price，init_database() 自动迁移旧表。
  已重采集 2026-11-21 快照 309 条，309/309 均有到期赎回价。

【3】C 策略集 1 小时钉钉提醒
  文件：backend/strategy_c_alert.py（参考 etfarb-master 的 strategy_cash_alert.py）
  取数：直接读本地 SQLite 最新快照 + strategy_engine 同口径策略，不新增取数。
  触发：三策略任一有新命中即推送；同一标的同一策略当日去重（价格变动产生新命中会再推）。
  钉钉：backend/dingtalk_utils.py（纯标准库版，与 etfarb 同群同 webhook；12:30-13:00 静默）。
  启动：随 main.py 的 FastAPI startup 事件自动挂 daemon 线程（每 3600 秒 / 1 小时），无需单独开窗口。
  自测：python backend/strategy_c_alert.py --once --dry-run （只打印不发送）
  日志：log/strategy_c_alert.log

【部署注意】
  - 后端改了字段/策略/提醒，需重启后端服务生效（start_dashboard.bat 会先杀旧进程）。
  - 前端改了 tab/列，需重新构建：cd frontend && npm run build（或 dev 模式自动热更）。
  - 生产库首次启动会自动加 maturity_redeem_price 列；如有旧快照该列为空，重新 /api/collect 一次即可。
