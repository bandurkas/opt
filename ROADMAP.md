# ROADMAP — ETH Options bot (методичный, backtest-gated)

> Рабочий документ исполнения: **что сделано** и **что делаем дальше — по одному шагу,
> каждый через тест ПЕРЕД решением**. Двигаемся планомерно: гипотеза → тест → критерий →
> деплой/откат. Детальный бэклог: `FUTURE_WORK.md`. Контекст/стратегия: `PROJECT_DOSSIER.md`.
> Handover для нового чата: `SESSION_STATE.md`. **Дата:** 2026-06-16 · HEAD `ba3aa21`+.

---

## 0. Главный принцип (НЕ нарушать)

1. **Ничто, что трогает edge стратегии, не катится без бэктеста** на 365д + holdout (OOS).
   Урок overfit усвоен: in-sample улучшение ≠ реальное.
2. **Каждое изменение тестируется ПЕРЕД деплоем** — тип теста зависит от изменения:
   - меняет вход/выход/сайзинг/режим (**edge**) → бэктест+holdout, потом наблюдение в paper;
   - чинит корректность/баг (**не edge**) → unit-тест + наблюдение в paper;
   - live-only путь (за `broker.is_live()`) → unit-тест (в paper инертно).
3. **Решение принимается по заранее заданному критерию** (см. каждый шаг), а не «на глаз».
4. **Деплой только через rebuild на Mac** (см. §2). НЕ собирать на VPS (1 CPU, >40 мин, стопор).
5. Один шаг за раз. Закрыли шаг (тест прошёл + задеплоено + наблюдаем) → следующий.

---

## 1. ✅ Сделано (журнал)

### Сессия 2026-06-15/16 (первая)
- 🔴 `52f9dc6` фикс `close_position` (неэкранированный `%` → IndexError → 0 закрытий 3 дня).
  После рестарта 8 позиций закрылись по TP2: **+$57.74, equity $457.74**.
- `22dbbde` харденинг: traceback в логи, изоляция закрытий, Telegram-алерт на ошибки цикла.
- `df52b6d` доки+мониторинг: `PROJECT_DOSSIER.md`, `FUTURE_WORK.md`, `monitor_paper.sh`+cron 3ч.
- `1e3f61a..f296e6c` **live-инфра P2–P6** (инертна, за `broker.is_live()`): `live_sizing`,
  `broker`, `live_safety`, `reconcile`, отдельная БД `options_trader` + сервис `trader` (armed-OFF).
- `987efab`+`7e11b66` 🔬 исследование tail-risk (harness `tail_overlay_sweep.py`, train/holdout):
  победитель `MAX_OPEN_POSITIONS=4` (OOS-подтверждён).

### Сессия 2026-06-16 (четвёртая) — audit-fix deploy + ADX-research + гейдж + VPS-чистка
- ✅ `a436302` **Fix1** (`signal_audit` пишет 1 строку/окно для дисквалифицированных окон, полный
  eval в payload — закрыта 3.7-дн дыра) **+ Fix2** (rowcount-guard в `record_trade_outcome`).
  Задеплоено в paper, подтверждено (`disqualified`-строки идут, 0 ошибок, гейт цел).
- ❌ `2cad943` **ADX-сайзинг ОТВЕРГНУТ** (OOS + tail с компаундингом+кэпом mo=4): режет доходность
  и хвост. `adx_sizing_oos.py` + `tail_overlay_sweep.py(size_fn)`; вердикт в `FUTURE_WORK §8`.
  Нативный `opt-app-bt:arm64` для быстрых локальных бэктестов (2 мин vs 33 мин эмуляции).
- ✅ `0a8d72b` **Гейдж 2.1** «Близость ко входу»: `entry_proximity` (0-100+зона) в `/paper/conditions`
  + SVG-спидометр. ADX-скор как индикатор, не сайзинг. ✅ **ЗАДЕПЛОЕН 2026-06-17** (нативный rebuild на
  VPS — фронт НЕ кросс-собирать на Mac, qemu-segfault) + `ce5867d` UX-фикс (readout вынесен из-под стрелки).
- ✅ `538b616` **Авточистка VPS** `vps_cleanup.sh` + cron 04:00 UTC (консервативно: без volume/image-a).

### Сессия 2026-06-16 (третья) — Фаза A1 (CB race)
- ✅ `85b5aff` **Фаза A1 — CB race condition исправлен** (`FUTURE_WORK §5.2`). Был split
  read-modify-write `consec_losses` в двух транзакциях (`get_state`→compute→`update_state`):
  при закрытии 2 позиций в одной итерации второй апдейт мог потеряться → CB мог не сработать
  на 5 убытках. **Фикс:** вся транзиция в одной залоченной транзакции
  `paper_repo.record_trade_outcome()` (`SELECT … FOR UPDATE` + rollback на ошибке); решение CB —
  чистая `paper_strategy._next_cb_state()`. Не edge (только корректность). **+7 тестов**
  `tests/test_cb_race.py` (чистая логика + race-симуляция через fake-repo со «протухшим»
  `get_state`); **54/54 backend-теста зелёные** на py3.11.
- 🚀 Задеплоено в paper (Mac amd64 rebuild → save/load → recreate). Контейнер `Up`, новый код
  подтверждён в рантайме, 0 ошибок. Гейт-состояние сохранено (8 циклов, equity $457.74,
  consec=0, CB off). local = GitHub = VPS.

### Сессия 2026-06-16 (вторая)
- 🧹 **Docker-чистка VPS3:** освобождено ~5GB (build-кэш 4.39GB), диск 37%→28%. Мусора в
  контейнерах/томах не было (6 контейнеров нужны, 1 том = БД).
- ✅ `34e150b` **tail-risk кэп `MAX_OPEN_POSITIONS=4`** внедрён: `execution_config.py`
  (mode-agnostic) + `paper_loop.at_position_cap()` гейт перед margin-check + `test_tail_risk.py`
  (5 тестов; **47/47 backend-тестов зелёные** на py3.11). Дневной лимит убытка НЕ внедрён
  (отвергнут данными). Закрывает **п.4 go-live гейта** (концентрация).
- 🚀 **Деплой в paper чистой пересборкой** (Mac amd64 → save/load → recreate) → **устранён
  старый deploy-landmine** (образ == коду репо). `MAX_OPEN_POSITIONS=4` подтверждён в рантайме.
- `48f2fc0` приоритизация `FUTURE_WORK.md §0`. `ba3aa21` обновлён `SESSION_STATE.md`.
- 🔍 **Проверка конвейра данных:** поллер `Up 6d`, тик 30с, spot живой, свечи свежие
  (5m age ~4 мин), бот грузит 2100×5m/220×15m/270×1h, поминутно считает ret_7d/side/regime/mtf/vol.
  Вход не открывается ТОЛЬКО из-за `regime=transition` (нужен `range`) — спроектированный фильтр,
  не баг. Gate стоит на **8/≥20–30 циклов** потому что рынок не даёт range-окон.

---

## 2. 🧰 Инструменты (как тестировать и деплоить)

**Бэктест-данные:** `data/eth_{5m,15m,1h}.json` — 365д (2025-05-31 → 2026-05-31), лежат локально.
Обновить при необходимости: `cd backend && PYTHONPATH=. python3 -c "from services.backtest_data import fetch_set; fetch_set(days=400)"` (или экспорт с VPS). Свежий holdout требует свежих данных.

**Харнессы (запуск: `cd backend && PYTHONPATH=. python3 services/<name>.py`):**
| Харнесс | Что проверяет | Метрики |
|---|---|---|
| `variant_backtest.py` | варианты входа/режима (V1/V2/V3, ADX-порог) | n, WR, avg/сделку, sharpe, max-consec-loss, убыт.месяцы, по сторонам/зонам |
| `tail_overlay_sweep.py` | риск-оверлеи (max_open, CB, daily-limit, dyn_size) | edge train/holdout, худший месяц, maxDD — **OOS** |
| `adx_score_sweep.py` / `adx_hybrid_sweep.py` | ADX Readiness Score, динамический сайзинг | прибыль, avg/сделку vs baseline |
| `holdout_eval.py` / `holdout_split.py` | строгая OOS-проверка (train/holdout split) | generalisation gap |

**Unit-тесты:** `cd backend && PYTHONPATH=. python3 tests/test_<name>.py` (без сети/БД).
Сейчас 47 (`test_{live_sizing,broker,live_safety,reconcile,execution,tail_risk}.py`).
Прогон в контейнере на py3.11 (локально только 3.9): см. `SESSION_STATE.md §7`.

**Деплой (после прохождения теста):**
```
# 1. коммит локально   2. git pull на VPS   3. rebuild на Mac:
docker buildx build --platform linux/amd64 -t opt-app-paper:latest ./backend --load
# 4. перенос           5. на VPS load + recreate:
docker save opt-app-paper:latest | gzip > /tmp/opt-app-paper.tar.gz
scp /tmp/opt-app-paper.tar.gz root@187.127.114.34:/tmp/
ssh root@187.127.114.34 'gunzip -c /tmp/opt-app-paper.tar.gz | docker load && \
  cd /root/opt-app && docker compose up -d --no-build --force-recreate paper'
```
**Проверка после деплоя:** `MAX_OPEN_POSITIONS` в рантайме + json-логфайл (НЕ `docker logs` — виснет):
`tail $(docker inspect opt-app-paper-1 --format '{{.LogPath}}')`.

---

## 3. 🗺️ Дорожная карта (по порядку; каждый шаг — со своим тестом и критерием)

### Фаза A — Достоверность paper-данных (P0). Тип теста: unit + наблюдение в paper.
> Эти баги не меняют edge (бэктест не нужен), но могут молча искажать статистику гейта или
> «обронить» бота. Чиним ПЕРВЫМИ, чтобы данные, по которым решаем идти в live, были честными.

- **A1. CB race condition** ✅ СДЕЛАНО 2026-06-16 (`85b5aff`). Был split read-modify-write в 2
  транзакциях → потеря инкремента при 2 закрытиях/итерацию. Фикс: одна залоченная транзакция
  `paper_repo.record_trade_outcome()` (`SELECT … FOR UPDATE`) + чистая `_next_cb_state()`.
  +7 тестов (`test_cb_race.py`), 54/54 зелёные. Задеплоено в paper, гейт-состояние сохранено.
- **A2. Отравление пула соединений** (`§5.1`). Тест: unit — форсить ошибку между get_conn и
  commit, затем ассертить, что следующий запрос на том же пуле работает. Фикс: `except: rollback; raise`.
- **A3. ZeroDivisionError при `spot==0`** (`§5.6`). Тест: unit на ветку paper. Фикс: guard `spot>0`.
- **A4. `open_position` возвращает `0`** (`§5.7`). Тест: unit — ошибка вставки → `None`, не `0`.
- **Деплой фазы A:** один rebuild на все фиксы. Наблюдать paper ≥2–3 дня: 0 ошибок в логах,
  корректные записи позиций. **Только после этого — Фаза B.**

### Фаза B — Ускорить набор циклов к гейту (range-детектор). Тип теста: бэктест+holdout.
> Реальный блокер гейта — рынок редко в `range`, бот простаивает. Вопрос: не отсекает ли
> `regime.py` валидные торгуемые окна. ⚠️ ЭТО меняет edge — строгий бэктест обязателен.

- **B1. Диагностика (без изменений кода).** Прогнать `variant_backtest.py` и посчитать
  распределение сигналов по режимам (range/transition/trend) + сколько сделок теряется в
  transition. Понять масштаб простоя на истории.
- **B2. Кандидаты послабления** (по одному): (а) разрешить вход в `transition` при сильном
  выравнивании MTF; (б) сдвиг ADX-порога range/trend; (в) V3-варианты из `variant_backtest.py`.
  Тест каждого: `variant_backtest.py` + `holdout_eval.py`.
  **Критерий деплоя:** число сделок РАСТЁТ, при этом avg/сделку, худший месяц и holdout-edge
  **не хуже** текущего baseline (V2 hybrid +7.09%/365д, 2 убыт.месяца). Если edge падает — отказ.
- **Деплой B:** только победивший вариант, затем наблюдать paper на паритет с бэктестом.

### Фаза C — Live-execution корректность (P1). Тип теста: unit (live-путь, в paper инертен).
> Нужно ДО реальных денег. На edge не влияет (за `broker.is_live()`).
- **C1.** broker exception-handling вокруг вызовов биржи (`§5.4`) + retry-задержка (`§6.2`).
- **C2.** reconcile считает реальный PnL вместо 0 (`§5.3`) + дедуп символов (`§6.6`).
- **C3.** live-safety: `side` case-insensitive (`§6.5`), slippage не маскирует ошибку (`§6.4`).
- Тест: расширить `test_{broker,reconcile,live_safety}.py`. Критерий: unit зелёные.

### Фаза D — Edge-улучшения (P2). ТОЛЬКО ПОСЛЕ прохождения гейта текущей стратегией.
> Меняют стратегию → сбрасывают валидацию и теряют накопленные циклы. Не раньше.
- **D1. ADX dynamic sizing** (`§8`): score≥8 → ×1.5. Бэктест `adx_score_sweep.py` (показал
  +$5547→+$7181/год) → подтвердить holdout → внедрить в `live_sizing.py` → paper на паритет.
- **D2.** (опционально) отключение live-CB 5/48h (бэктест намекает, что вредит) — отдельный sweep.
- **D3.** (большой) Put-спред = defined risk (`§1 Tier 3`) — требует доработки движка бэктеста.

### Фаза E — Go-live (P7). После: гейт пройден + Фазы A,C закрыты.
- Rebuild → фандинг USDT $500–1000 → `docker compose --profile trader up -d trader` (создаст
  `options_trader` БД) → чистый `reconcile` + `bybit_probe.py` → `LIVE_ENABLED=true` (взвести).
  Подробности: `LIVE_TRADING_HANDOFF.md`.

---

## 4. 📍 Текущий статус / следующий шаг

- **Сейчас:** paper жив на свежем образе (CB-race фикс задеплоен 2026-06-16), `MAX_OPEN_POSITIONS=4`
  активен, данные свежие, бот корректно ждёт `range`. Гейт 8/≥20–30 (п.4 закрыт; SL/CB/dynsize
  ещё не наблюдались вживую).
- **Следующий шаг (предложение):** продолжить Фазу A — **A2** (отравление пула соединений, `§5.1`),
  **A3** (ZeroDiv при spot=0, `§5.6`), **A4** (`open_position`→0, `§5.7`); одним rebuild на все.
  Либо **Фаза B1** (диагностика range-детектора, без изменений кода) — понять, почему мало циклов.
- Решение по каждому шагу — после теста, по критерию выше.
