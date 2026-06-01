# Стратегическое исследование sell-premium ETH (2025-05 → 2026-06)

**Цель документа:** зафиксировать всё, что мы узнали о двух конкурирующих
стратегиях продажи опционов на ETH, проверить нынешний кризис LIVE-конфига,
и предложить план гибридной стратегии, выбирающей сторону (Put / Call) по
рыночному режиму.

**Дата составления:** 2026-06-01
**Статус LIVE:** sell-Put cd=4 h=96 — **сломана** в текущем регимe (см. §3).
**Бэктест период:** 2025-05-31 → 2026-05-31 (365 дней, 105 120 баров 5m)

---

## 1. Хронология решений

### Фаза 0 — пре-исследование (до 2025-05)
- Стратегия:  **sell-Call MTF-down**, vol≥0.6, regime ∈ {range, transition},
  bull-cap 1.05, cd=6, exit tp1=30%/tp2=50%/sl=50%/hold=24h
- Эта конфигурация известна как `BASELINE_CALL_GEN_KWARGS` /
  `BASELINE_CALL_EXIT` ([backend/services/strategy_config.py:57-72](../backend/services/strategy_config.py#L57-L72)).
- Контекст: ETH был в нисходящем тренде, продажа Call'ов на MTF-down пиках
  работала на mean-reversion.

### Фаза 1 — переход на sell-Put MTF-up (commit `9ba4797`, май 2025)
- Команда: «централизовать конфиг и попробовать sell-Put на сильном бычьем
  рынке».
- Параметры: side=P, MTF=up, regime=range, vol=0.5, bull_cap=1.08, cd=6,
  exit tp1=30/tp2=50/sl=50/hold=24h.
- Идея: продавать Put'ы в восходящем тренде → премия декай'ит, тейк-профит
  быстрый.

### Фаза 2 — Bybit-реалистичная модель (commit `47baffa`, май 2025)
- Изменения: лоты 0.1 ETH, IM ≈ 10%·strike + premium, 2% round-trip spread,
  0.03% taker fee (cap 12.5% от премии), стартовый капитал $400.
- Старые $-числа из бэктестов умножились на ~4× в реальной модели.

### Фаза 3 — concurrent positions + margin (commit `b719a81`, май 2025)
- Одновременно до 80% portfolio margin. Динамический sizing по
  свободной марже.

### Фаза 4 — circuit breaker (commit `8935125`, май 2025)
- CB: 5 убытков подряд → пауза 12h.
- MARGIN_PCT 15%, hold_h 12h → 24h.

### Фаза 5 — расширенная оптимизация (commit `9ba4797`, конец мая 2025)
- Sweep over 144 grid cells; победитель cd=6/h=72/sl=150/bull=1.08.

### Фаза 6 — proper holdout protocol (commit `22bb748`, май 2026)
- ⚠️ Аудит обнаружил методологический баг: «90d holdout» брался от уже
  отфильтрованных сигналов, для разреженного `cd=12` это совпадало с
  test-сплитом → composite double-counted.
- Решение: [backend/services/holdout_split.py](../backend/services/holdout_split.py)
  обрезает данные **до** генерации сигналов. train/test/holdout — disjoint
  klines.
- Результат: bull_filter ∈ {None, 1.05, 1.08} дают **идентичный** holdout PnL
  для каждого cd — фильтр не добавлял alpha. Преимущество bull=1.08 было
  selection bias.

### Фаза 7 — bull=None cd=6 LIVE (commit `f791998`, 2026-06-01)
- holdout n=148, avg +13.78%, sharpe 0.28, $/мес +$54.
- Не самое прибыльное по theoretical $/mo, но лучший composite после
  selection_bias_pen.

### Фаза 8 — 54-cell parallel sweep (commit `54ea32e`, 2026-06-01)
- Подробности: [sweep_results/parallel_cd_vol_hold.json](../sweep_results/parallel_cd_vol_hold.json)
- Grid: cd ∈ {3,4,5,6,8,12} × vol ∈ {0.45,0.5,0.55} × hold_h ∈ {48,72,96}.
- Топ по $/мес:
  | rank | cd/vol/hold | n_hold | avg | sharpe | $/мес |
  |------|-------------|--------|-----|--------|-------|
  | 1 | 3/0.5/96 | 284 | +18.89% | 0.36 | +$143 (margin-capped до ~$95) |
  | 4 | **4/0.5/96 (LIVE)** | 219 | +19.57% | 0.38 | **+$114** |
  | 8 | 6/0.5/96 | 148 | +19.78% | 0.38 | +$78 |
- Выбран cd=4 как «sweet spot»: theoretical $/мес × устойчивый Sharpe ×
  меньшая margin contention чем cd=3.

### Фаза 9 — отказ от hybrid (commit `54ea32e`)
- Phase 4 hybrid test: [sweep_results/hybrid_test.json](../sweep_results/hybrid_test.json)
- На 365d данных:
  | Вариант | n | avg | sharpe | $/мес |
  |---------|---|-----|--------|-------|
  | Put-only | 148 | +13.78% | 0.28 | +$54 |
  | Call-only | 149 | **-31.74%** | -0.37 | -$126 |
  | Hybrid (Put+Call merge) | 297 | -9.06% | -0.12 | -$72 |
- Вывод: Call с MTF-down проигрывает на 365d, потому что ETH был в общем
  uptrend → merge только разбавляет alpha.
- **Ошибка**: hybrid test использовал статичный BASELINE_CALL без
  переключения по регимe. На самом деле Call и Put работают в разных
  фазах рынка (см. §3 ниже).

---

## 2. Что сейчас в LIVE на VPS

### Конфиг
[backend/services/strategy_config.py:31-47](../backend/services/strategy_config.py#L31-L47):
```python
LIVE_GEN_KWARGS = {
    "vol_threshold": 0.50,
    "regime_filter": ["range"],
    "side": "P",                    # ← sell Put only
    "mtf_direction_filter": "up",
    "bull_market_ratio_max": None,
    "adx_max": None,
    "cooldown_bars": 4,
}
LIVE_EXIT = {"tp1_pct":0.50, "tp2_pct":0.70, "sl_pct":1.50, "hold_h":96}
```

### Реалистичная модель
[backend/services/missed_signals.py](../backend/services/missed_signals.py) и
[backend/services/paper_strategy.py](../backend/services/paper_strategy.py):
- σ=0.6 константа (live ETH IV ≈ 0.40 — модель завышает премию на ~50%)
- BS-цены: реальные fills ±20%
- 0.1-ETH лоты, IM = 10%·strike + premium
- 2% round-trip spread, 0.03% taker fee (cap 12.5% от премии)
- $400 старт, до 80% portfolio margin
- CB: 5 losses подряд → 12h пауза

### ⚠️ Найденный баг в missed_signals.py:119
```python
strike = round(spot / STRIKE_GRID) * STRIKE_GRID
premium_mid = _bs_call(spot, strike)        # ← ВСЕГДА Call!
```
Для всех трейдов (включая sell-Put) расчёт entry-credit использует
`_bs_call`. По put-call parity (r=0): `put = call + (strike - spot)`.
Для ITM Put (strike > spot, что часто бывает при rounding до $25 grid)
премия занижена на $10-15. Это занижает `gross_pnl_usd` и `margin_locked`
в frontend-отчёте на 15-25%. **На сам simulator не влияет** — simulator
использует свой `bs.price(side, ...)`. Но числа в UI неточные.
*Action: фикс на одну строку, см. §6.*

---

## 3. Кризис LIVE-конфига (2026-05)

### Симптомы (live данные с VPS, 2026-06-01)
30-дневный backtest через `/api/v1/paper/missed-signals?lookback_days=30`:
- **n_signals=10**, n_skipped_by_cb=14, n_skipped_by_margin=5
- **wins=0, losses=10, WR=0%**
- Total P&L: **−$54** (−13.5%)
- Avg/trade: **−68.6%**
- ВСЕ exits: `time_stop` (96h hold, ни одного TP/SL)
- Equity: $400 → $346 за 30 дней

### Подтверждение локально
Запустили [holdout_eval.py](../backend/services/holdout_eval.py) на тех же
365d cached data с разными окнами:

| Window | LIVE (cd=4 P up range vol=0.5 h=96) | BASELINE_CALL (cd=6 C down range+trans vol=0.6 h=24) |
|--------|-------------------------------------|----------------------------------------------------|
| **Last 30d** | n=46 avg **−50.6%** WR 10.9% | n=139 avg **+22.5%** WR 82% |
| 60d | n=130 avg +6.2% WR 54.6% | n=167 avg +13.5% WR 73% |
| 90d | n=219 avg +19.6% WR 70% | n=216 avg +3.7% WR 59% |

90d holdout (на котором делали optimization) — front-loaded: вся прибыль
LIVE сидит в днях 60-90, а в днях 0-30 — катастрофа.

### Помесячный breakdown — главная находка

```
=== LIVE (sell Put MTF=up) ===
  2025-06: n= 77  WR= 46.8%  avg= +12.25%
  2025-07: n= 86  WR= 72.1%  avg=  +7.66%
  2025-08: n=116  WR= 96.6%  avg= +61.82%    ← peak
  2025-09: n= 67  WR= 41.8%  avg= −64.62%    ← crash
  2025-10: n= 18  WR= 72.2%  avg= +29.06%
  2025-11: n= 19  WR= 31.6%  avg= −80.53%    ← crash
  2025-12: n=107  WR= 92.5%  avg= +51.52%    ← peak
  2026-01: n= 25  WR= 76.0%  avg= +26.48%
  2026-02: n= 37  WR= 64.9%  avg= −15.72%
  2026-03: n= 97  WR= 93.8%  avg= +41.63%    ← peak
  2026-04: n= 84  WR= 78.6%  avg= +37.32%
  2026-05: n= 46  WR= 10.9%  avg= −50.60%    ← crash (where we are)

=== BASELINE_CALL (sell Call MTF=down) ===
  2025-06: n= 68  WR= 83.8%  avg= +29.84%    ← peak
  2025-07: n= 39  WR= 10.3%  avg= −34.29%
  2025-08: n= 71  WR= 54.9%  avg=  +3.37%
  2025-09: n= 79  WR= 49.4%  avg= −18.88%
  2025-10: n= 70  WR= 80.0%  avg= +31.45%    ← peak
  2025-11: n= 62  WR= 48.4%  avg=  −1.61%
  2025-12: n=100  WR= 26.0%  avg= −20.15%
  2026-01: n= 68  WR= 72.1%  avg= +17.83%
  2026-02: n= 53  WR= 92.5%  avg= +40.11%    ← peak
  2026-03: n= 60  WR= 10.0%  avg= −33.64%
  2026-04: n= 28  WR= 28.6%  avg= −30.96%
  2026-05: n=139  WR= 82.0%  avg= +22.49%    ← peak (where we are)
```

### Наблюдение: LIVE и BASELINE_CALL почти антикоррелированы

Месяцы pivot'а Put → Call (LIVE проиграл, CALL выиграл):
- 2025-06, 2025-09, 2025-11, 2026-02, 2026-05

Месяцы pivot'а Call → Put (CALL проиграл, LIVE выиграл):
- 2025-08, 2025-12, 2026-03, 2026-04

Это **классическая регимная стратегия**: одна сторона — для трендового
рынка, другая — для коррекций/боковика. **Если знать режим заранее**,
можно зарабатывать в оба направления.

### Почему провалилась 90d оптимизация
1. **Front-loaded** holdout: 90d окно покрывает Mar+Apr+May. Mar+Apr =
   +37-42% LIVE (peak), May = −51%. Среднее +19% маскирует катастрофу
   последнего месяца.
2. **Регимная смена** ETH ~1 мая 2026 (uptrend → downtrend) сделала
   sell-Put опасным.
3. **Маленький sample** (n=148-219) — sharpe 0.28-0.38 *не* говорит
   о стабильности на коротких горизонтах.
4. **Phase 4 hybrid test** на 365d показал агрегатную картину, в которой
   месяцы взаимоисключающих регимов сложились в нулевую alpha. Правильный
   тест — **switching hybrid**, который мы не делали.

---

## 4. Гипотеза: switching hybrid стратегия

### Простая идея
Каждые 5m бар, перед генерацией сигнала, классифицировать рынок:
- Если MTF(5m+15m+1h) **up** AND regime ∈ {range} → sell Put (текущий LIVE)
- Если MTF **down** AND regime ∈ {range, transition} → sell Call (BASELINE)
- Если MTF neutral OR regime=trend → **не входить**

Это `gen_sell_premium_iv_high` уже умеет — он перебирает обе стороны и
эмитит сигнал, если хоть одна сторона "fires". Проблема в том, что
сейчас фильтр `side="P"` отбрасывает Call-сигналы.

### Что нужно сделать
1. Снять `side` фильтр в `LIVE_GEN_KWARGS` (`side: None` или `side: "both"`).
2. Расширить regime_filter: для Put оставить `["range"]`, для Call —
   `["range", "transition"]` (как BASELINE_CALL).
3. Подобрать индивидуальные exit-правила для каждой стороны:
   - Put: tp1=50, tp2=70, sl=150, hold=96 (LIVE)
   - Call: tp1=30, tp2=50, sl=50, hold=24 (BASELINE)
4. Adaptive sizing: при resent loss-streak уменьшать size до полного
   восстановления (часть `realistic_size_lots`).

### Что нужно проверить
- ⚠️ **Switching cost**: при смене стороны через бар, не открываются
  ли противоположные позиции одновременно? Сейчас sizing разделяет
  margin, но *логика противоречия* (один Put + один Call на близкие
  страйки) даёт почти-delta-neutral положение, которое теряет на двух
  spread'ах и не зарабатывает на theta.
- **Регимный detector**: достаточно ли MTF+regime для выбора стороны,
  или нужен дополнительный фильтр (например, 7d return: <-2% → Call,
  >+2% → Put, между → нет входа).
- **Дрифт σ**: при смене регима волатильность тоже меняется. Использовать
  dynamic_sigma из realized 168h RV — это уже есть в
  [backend/services/backtest.py:343-401](../backend/services/backtest.py#L343-L401),
  но в LIVE не включено.

---

## 5. План улучшения (next steps)

### Этап A — экстренная стабилизация (этот день)
1. **Pause paper-trading** или **переключить на baseline_call**:
   - Установить `PAPER_VARIANT=baseline_call` (нужно добавить такой
     вариант в `active_gen_kwargs`).
   - Reasoning: на последних 30д baseline_call даёт +22.5% avg, LIVE −50.6%.
     Хотя bilateral risk остаётся, лучше работать в текущем регимe.
2. **Reset paper-equity до $400** (опционально — для чистого теста
   нового конфига).
3. **Зафиксировать в README:** «strategy under research, do not deposit
   real money».

### Этап B — switching hybrid (1-2 дня)
1. Создать `gen_sell_premium_hybrid` в [backend/services/strategy_registry.py](../backend/services/strategy_registry.py):
   - Внутри для каждого бара compute MTF + regime
   - Если MTF up & regime=range → emit Put-сигнал
   - Если MTF down & regime ∈ {range, transition} & 7d_ret < −1.5% → emit Call
   - Иначе пропуск
2. Симулировать через `simulate_signal_set` с per-side exit-правилами
   (нужен `_simulate_option_trade` с раздельными tp/sl per side — небольшой
   рефакторинг).
3. Backtest по тем же 365d:
   - Per-month breakdown
   - Sharpe, max DD, $/мес
   - Сравнение с pure Put / pure Call / current LIVE.

### Этап C — robust holdout (1 день)
1. Использовать **walk-forward** проверку вместо одной 90d holdout:
   - Каждые 30 дней rebalance: trained на предыдущих 60д, deployed
     следующие 30д.
   - Получим 12 не-overlapping windows за 365д → robust estimate $/мес.
2. Если walk-forward avg ≥ +5%/trade с stable sharpe ≥ 0.25 → green light.

### Этап D — risk hardening (1-2 дня)
1. **Dynamic σ**: включить `dynamic_sigma=True` в LIVE missed_signals и
   paper_loop. Использует 168h RV × 1.05 calibration. Уменьшит overstatement
   премии в спокойные периоды.
2. **Adaptive size**: после 2 убытков подряд — half size; после 3 — quarter;
   после 5 — CB активируется как сейчас.
3. **Daily DD cap**: если equity упала на >5% за день — пауза до конца дня.
4. **Strike selection**: вместо `round(spot/$25)*$25` использовать ближайший
   **OTM** strike с фильтром по delta (например, |delta| ≤ 0.35). Сейчас мы
   берём ATM, часто оказываясь ITM из-за rounding.

### Этап E — ремонт UI и измерения (0.5 дня)
1. Фикс [missed_signals.py:119](../backend/services/missed_signals.py#L119):
   ```python
   from services.backtest import bs  # uses bs.price(side, ...)
   premium_mid = bs.price(sim["side"], spot, strike, _T_YEARS, DEFAULT_SIGMA)
   ```
2. Frontend: добавить регимный индикатор (текущий MTF + regime + 7d
   return) на главной — пользователь должен видеть, *почему* бот выбирает
   ту или иную сторону.
3. Logging: в paper_loop писать decision-trace ("MTF=up, regime=range,
   7d_ret=+0.8% → side=P; rejected because vol_pctile=42<50").

### Этап F — продление данных (на будущее)
- Сейчас тестируем на одних 365д. Расширить до 2-3 лет для большего
  числа регимных шагов. ETH историю можно тянуть с Bybit API за
  последние ~3 года.
- Добавить cross-asset features (BTC, SOL) — корреляция может улучшить
  регимный детектор.

---

## 6. Метрики успеха для switching hybrid

Цели для switching hybrid стратегии (целевые на walk-forward 12 окон):

| Метрика | Min acceptable | Target |
|---------|----------------|--------|
| avg %/trade | +5% | +12% |
| Win rate | 55% | 65% |
| Sharpe per-trade | 0.25 | 0.45 |
| Max DD | <12% | <8% |
| Worst-month avg | >−10% | >−5% |
| $/мес на $400 | +$30 | +$80 |
| Не более 1 окна с avg<0 | да | да |

Если switching hybrid не достигает Min — оставляем `BASELINE_CALL` как
безопасный fallback, исследуем другие подходы (calendar spreads,
volatility-targeting size, etc).

---

## 7. Ссылки на файлы

- Конфиг: [backend/services/strategy_config.py](../backend/services/strategy_config.py)
- Симулятор: [backend/services/backtest.py](../backend/services/backtest.py)
- Live missed signals: [backend/services/missed_signals.py](../backend/services/missed_signals.py)
- Holdout protocol: [backend/services/holdout_split.py](../backend/services/holdout_split.py)
- Holdout eval CLI: [backend/services/holdout_eval.py](../backend/services/holdout_eval.py)
- Paper loop: [backend/services/paper_loop.py](../backend/services/paper_loop.py)
- Sweep результаты:
  - [sweep_results/parallel_cd_vol_hold.json](../sweep_results/parallel_cd_vol_hold.json) — 54 cells
  - [sweep_results/final_validation.json](../sweep_results/final_validation.json) — 8-cell composite
  - [sweep_results/hybrid_test.json](../sweep_results/hybrid_test.json) — Put+Call merge
  - [sweep_results/holdout_90d.json](../sweep_results/holdout_90d.json) — последний eval
- Доклад: [sweep_results/OPTIMIZATION_REPORT.md](../sweep_results/OPTIMIZATION_REPORT.md)

---

## 8. Принципы дальнейшей работы

1. **Никогда не deploy'им без walk-forward**. Одна 90d holdout — это
   гарантированный selection bias на коротких сэмплах (n<300).
2. **Регим >> параметры**. Tuning внутри одной стороны (cd, vol, hold)
   даёт <30% улучшения. Переключение между Put/Call — потенциальный +
   100%+ за счёт antikorrelation.
3. **Sample size first**. n<100 trades в окне → не доверяем результату.
4. **σ-bias matters**. BS @ σ=0.6 завышает премию на 50% против live IV
   ≈ 0.40. Нужен dynamic_sigma для точных $-чисел.
5. **UI ≠ Backtest**. Числа в UI должны точно соответствовать симулятору
   (фикс _bs_call → bs.price в missed_signals).
6. **Real money discipline**. Сначала walk-forward + 1-3 месяца paper
   на новом конфиге → только потом 0.01 ETH live с tight DD cap.

---

*Последнее обновление: 2026-06-01*
*Автор: Claude Opus 4.7 + bandurkas*
