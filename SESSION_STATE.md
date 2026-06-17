# Handover / Resume checkpoint — ETH Options project

> 👉 Новый агент: начни с **`START_HERE.md`** (точка входа), затем этот файл и `ROADMAP.md`.
> Самодостаточный файл, чтобы продолжить в новом чате. **Дата:** 2026-06-17 ·
> **HEAD:** `ce5867d` · ветка `main` · **local = GitHub = VPS**, дерево чистое.
> ✅ Гейдж 2.1 (`0a8d72b`) **ЗАДЕПЛОЕН** 2026-06-17 (нативный rebuild backend+frontend на VPS;
> Mac-кросс-сборка фронта отвергнута — `next build`/SWC сегфолтится под qemu-amd64) + UX-фикс
> гейджа `ce5867d` (readout вынесен из-под стрелки). Cleanup-cron — установлен на VPS.
> Контекст: открой `PROJECT_DOSSIER.md` (всё о проекте) первым.

---

## 0. TL;DR — где мы

Бот-продавец опционной премии на ETH (Bybit, USDT-settled), стратегия **V2 hybrid + V3 ADX**
(source of truth: `backend/services/strategy_config.py` + `regime.py`). Работает **paper**-режим
на VPS3, копит сделки к go-live гейту. Live-инфраструктура (P2–P6) **построена и инертна**, не
задеплоена. Реальные деньги — только после гейта + фандинга + армирования.

Что НЕ доказано и блокирует live: paper ещё не прошёл гейт (нужно ≥20–30 полных циклов в разных
режимах в пределах 30–50% бэктеста + наблюдать SL/CB/dynsize вживую). См. §8.3 досье.

**Сессия 2026-06-16 (четвёртая):** (1) **Fix1+Fix2 задеплоены** (`a436302`): `signal_audit` теперь
пишет 1 строку/окно для дисквалифицированных окон (полный eval в payload — закрыта 3.7-дн дыра) +
rowcount-guard в `record_trade_outcome`. Подтверждено в рантайме (`disqualified`-строки идут, 0
ошибок, гейт цел). (2) **ADX-сайзинг ОТВЕРГНУТ** (`2cad943`): на реалистичной модели (компаундинг +
кэп mo=4) режет доходность (+203%→+50..84%) и хвост; вердикт в `FUTURE_WORK §8`, разбор в памяти
`project_options_adx_sizing_rejected`. Артефакты: `adx_sizing_oos.py`, `tail_overlay_sweep.py`
(size_fn), нативный `opt-app-bt:arm64` для быстрых бэктестов. (3) **Гейдж 2.1** (`0a8d72b`): бэкенд
`entry_proximity` (0-100+зона) в `/paper/conditions` + SVG-спидометр на фронте; ADX-скор как
**индикатор**, не сайзинг. ✅ **ЗАДЕПЛОЕН 2026-06-17** (нативный rebuild на VPS) + UX-фикс
`ce5867d`. (4) **Авточистка VPS** (`538b616`):
консервативный `vps_cleanup.sh` + cron 04:00 UTC (без volume/image-a; disk-alert). (5) Правило
зафиксировано: архитектура→код→ревью→тест→ревью→деплой.

**Сессия 2026-06-16 (третья):** **Фаза A1 — CB race condition исправлен** (`85b5aff`,
`FUTURE_WORK §5.2`). Был split read-modify-write `consec_losses` в двух транзакциях → при
2 закрытиях в одной итерации терялся инкремент, CB мог не сработать на 5 убытках. Фикс: вся
транзиция в одной залоченной транзакции `paper_repo.record_trade_outcome()` (`SELECT … FOR UPDATE`
+ rollback), решение CB — чистая `paper_strategy._next_cb_state()`. +7 тестов (`test_cb_race.py`,
race-симуляция через fake-repo); **54/54 backend-теста зелёные**. Задеплоено в paper (Mac amd64
rebuild → save/load → recreate), контейнер `Up`, 0 ошибок, гейт-состояние сохранено (8 циклов,
equity $457.74, consec=0, CB off). local = GitHub = VPS.

**Сессия 2026-06-16 (вторая):** (1) почистили Docker на VPS3 — освобождено ~5GB (build-кэш
4.39GB), диск 37%→28%, мусора в контейнерах/томах не было; (2) **внедрили tail-risk кэп
`MAX_OPEN_POSITIONS=4`** (P1 приоритет, закрывает п.4 гейта) — код+5 тестов (47 всего, все
зелёные), **чистая пересборка образа на Mac (amd64) → save/load на VPS → задеплоено в paper**;
это же **устранило старый deploy-landmine** (образ больше не «старше Phase 0»); (3) приоритизировали
`FUTURE_WORK.md` под близость к live-валидации (§0 там). Бот пересоздан с 0 открытых позиций,
`MAX_OPEN_POSITIONS=4` подтверждён в рантайме, режим уже сменился `trend→transition`.

---

## 1. Что сделано (хронология, коммиты `b45ecbb..739b00b`)

**Сессия 2026-06-16 (четвёртая) — audit-fix deploy + ADX-research + гейдж + VPS-чистка**
- `a436302` ✅ **Fix1+Fix2 ЗАДЕПЛОЕНЫ**: `signal_audit` пишет 1 строку/окно для дисквалифицированных
  окон (полный eval в `signal_payload`, `reject_reason='disqualified'`) — закрыта 3.7-дн дыра в
  записи параметров; + rowcount-guard в `record_trade_outcome` (тихий промах CB-апдейта → RuntimeError).
  Подтверждено в paper (`disqualified`-строки идут, 0 ошибок, гейт цел). +2 теста `test_paper_repo.py`.
- `2cad943` ❌ **ADX-сайзинг ОТВЕРГНУТ** (research): на реалистичной модели (компаундинг + кэп mo=4)
  любой вариант режет компаунд-доходность (FULL +202.8%→+50..84%) и хвост. Опровергает `FUTURE_WORK §8`
  (там был арифметический расчёт). Артефакты: `adx_sizing_oos.py` (V2+OOS), `tail_overlay_sweep.py`
  (+`size_fn`). Полный разбор — память `project_options_adx_sizing_rejected`.
- `0a8d72b` ✅ **Гейдж 2.1 «Близость ко входу»** — бэкенд `paper_strategy.entry_proximity()` (0-100+зона)
  в `/paper/conditions` + ADX-скор; фронт — SVG-спидометр (`page.tsx`) + per-factor бары. ADX = индикатор,
  НЕ сайзинг. +6 тестов `test_proximity.py`, tsc чист. ✅ **ЗАДЕПЛОЕН 2026-06-17** — `/paper/conditions`
  отдаёт `proximity` (53.4%/preparing подтверждено), дашборд рисует. Mac-кросс-сборка фронта отвергнута
  (`next build`/SWC → `qemu: signal 11`); собирали backend+frontend **нативно на VPS** (см. память
  `vps3-build-images-locally`), деплой отвязанно через `setsid`+опрос лога (SSH рвётся на 1 CPU).
- `ce5867d` ✅ **UX-фикс гейджа**: readout (%/зона) вынесен **под дугу** — стрелка больше не наезжает на
  текст; + базовый трек, яркая заливка шкалы 0→pct, сужающаяся стрелка + hub. tsc чист. Задеплоен 2026-06-17.
- `538b616` ✅ **Авточистка VPS** `vps_cleanup.sh` + cron 04:00 UTC: prune только stopped/dangling/
  build-cache>168h/networks; НИКОГДА `image -a`/`volume prune`; усечение больших логов; disk-alert ≥80%.
  Протестирован на VPS (сервисы целы). `739b00b` docs.
- 🛠️ Собран нативный **`opt-app-bt:arm64`** для быстрых ЛОКАЛЬНЫХ бэктестов (2 мин vs 33 мин эмуляции amd64).

**Сессия 2026-06-16 (третья) — Фаза A1 (CB race)**
- `85b5aff` ✅ **CB race condition** (`§5.2`): атомарный `paper_repo.record_trade_outcome()`
  (`SELECT … FOR UPDATE` + rollback) + чистая `paper_strategy._next_cb_state()`; устранён
  split read-modify-write `consec_losses`. +7 тестов `test_cb_race.py` (54/54 зелёные).
  Задеплоено в paper (rebuild Mac amd64 → save/load → recreate), 0 ошибок, гейт сохранён.

**Сессия 2026-06-16 (вторая) — tail-risk + чистка**
- `34e150b` ✅ **tail-risk кэп `MAX_OPEN_POSITIONS=4`** в `execution_config.py` (mode-agnostic,
  paper+live) + `paper_loop.at_position_cap()` гейт перед margin-check + `tests/test_tail_risk.py`
  (5 тестов). Reject-причина `max_open_positions` в signal_audit. Дневной лимит убытка — НЕ внедрён
  (отвергнут бэктестом). **Задеплоено в paper** через rebuild (Mac amd64) → docker save/load →
  `compose up -d --no-build --force-recreate paper`. Landmine устранён (образ теперь актуальный).
- `48f2fc0` 📋 приоритизация `FUTURE_WORK.md §0` под live-валидацию (P0 баги достоверности данных →
  P1 live-корректность → P2 edge-изменения отложены → P3 гигиена).
- Docker-чистка VPS3: `docker container/image/builder prune` — освобождено ~5GB (build-кэш 4.39GB),
  диск 37%→28%. 6 контейнеров (все нужны) + 1 том (postgres_data) — мусора нет.

**Сессия 2026-06-16 (первая) — починка/харденинг/live-инфра/research**

**Починка + харденинг paper-бота**
- `52f9dc6` 🔴 фикс: `close_position` падал на неэкранированном `%` в `LIKE 'closed_%'`
  → `IndexError` на каждом закрытии → **0 закрытий за весь деплой**, 8 позиций залипли, 3 дня
  тишины. Фикс `'closed_%%'`. После рестарта 8 закрылись по TP2: **+$57.74, equity $457.74**.
- `22dbbde` харденинг: traceback в логи, изоляция закрытий по позициям, Telegram-алерт на ошибки.

**Документация / мониторинг**
- `df52b6d` `PROJECT_DOSSIER.md` (полное досье), `FUTURE_WORK.md` (бэклог),
  `monitor_paper.sh` + `paper_cron.sh` (VPS-мониторинг, cron 3ч + Telegram на SL/CB/dynsize/гейт).

**Live-инфра P2–P6 (инертна, gated на `broker.is_live()`; в paper поведение прежнее; 42 теста)**
- `1e3f61a` P2 `live_sizing.py` — сайзинг от реального USDT, reduce-on-reject (11 тестов).
- `76e4d5b` P3 `broker.py` — реальные fills + equity из кошелька; вшито в open/close/equity (8).
- `13a4780` P4 `live_safety.py` — kill-switch/дневной лимит/спред/слиппедж + `realized_pnl_since` (8).
- `b7c1412` P5 `reconcile.py` — сверка биржа↔БД, лечит ручные закрытия, блок открытий при дрейфе (9).
- `f296e6c` P6 отдельная БД `options_trader` (`db/bootstrap.py`) + сервис `trader`
  (`profiles:[trader]`, armed-OFF, НЕ авто-стартует).
- `7556004` обновлён `LIVE_TRADING_HANDOFF.md` (P2–P6 + landmine + гейт).

**Исследование (синхронизировано/проведено)**
- `3bfe2aa` чужой/прошлый коммит «ADX Readiness Score + sizing sweeps» — подтянут на GitHub/VPS.
- `987efab` + `7e11b66` 🔬 **исследование tail-risk** (см. §4): найден победитель
  `MAX_OPEN_POSITIONS=4`, OOS-подтверждён, **внедрение отложено** по решению пользователя.

---

## 2. Текущее состояние системы

- **Paper-бот:** жив на образе `a436302`, без ошибок, копит циклы к гейту. `MAX_OPEN_POSITIONS=4`
  активен. Часто `regime=trend/transition` → сторона дисквалифицируется (нужен `range`) → корректно
  ждёт (не баг). Гейт: **8/≥20–30 циклов** (все TP2, +$57.74, equity $457.74); п.4 (концентрация)
  закрыт кэпом; SL/CB/dynsize ещё не наблюдались вживую.
- **`signal_audit` теперь полный:** после `a436302` каждое 5m-окно даёт 1 строку (fire-time решение
  ИЛИ `disqualified`-наблюдение с полным eval в `signal_payload`). Дыра в записи параметров закрыта.
- **✅ Гейдж 2.1 ЗАДЕПЛОЕН (2026-06-17):** backend (`/paper/conditions` отдаёт `proximity`) + frontend
  (SVG-спидометр) пересобраны **нативно на VPS** и подняты; UX-фикс `ce5867d` применён. Дашборд :3000 чист
  (HTTP 200, 0 ошибок). ⚠️ Урок: фронт НЕ кросс-собирать на Mac (qemu segfault на `next build`) — только на VPS.
- **Мониторинг:** VPS-cron 3ч (`paper_cron.sh`) → Telegram на SL/CB/dynsize/+5 циклов/гейт. Ручная
  проверка: `bash /root/opt-app/monitor_paper.sh`. **Авточистка:** cron 04:00 UTC (`vps_cleanup.sh`).
  ⚠️ `docker logs opt-app-paper-1` виснет (1 CPU) — читать json-логфайл:
  `tail $(docker inspect opt-app-paper-1 --format '{{.LogPath}}')`.
- **Live-инфра P2–P6:** в репозитории, инертна, **НЕ задеплоена** (trader-сервис не стартовал).

---

## 3. ✅ Deploy landmine — УСТРАНЕН (2026-06-16)

Раньше: `paper`-контейнер был пропатчен через `docker cp` поверх образа старше Phase 0 →
`compose up` откатывал бы код. **Теперь исправлено:** собран свежий образ `opt-app-paper:latest`
(Mac, `--platform linux/amd64`) со ВСЕМ актуальным кодом, перенесён `docker save|gzip → scp →
docker load`, контейнер пересоздан (`compose up -d --no-build --force-recreate paper`). Образ ==
коду репо. **Деплой-процесс впредь:** правишь код → коммит → `git pull` на VPS → rebuild на Mac
(VPS 1 CPU, сборка >40 мин стопорит SSH) → save/load → recreate. НЕ собирать на VPS.

---

## 4. 🔬 Вывод исследования tail-risk (важно)

Задача была: убрать «плохой месяц» (−42% Sep), сохранив плюсы. Харнесс
`backend/services/tail_overlay_sweep.py` (event-driven портфельный replay, train/holdout, без
look-ahead; trades кэшируются в `/tmp/tail_trades_v3_adx.json` — `/tmp` эфемерен, регенерация ~2–3 мин).

**Победитель (OOS-подтверждён): `MAX_OPEN_POSITIONS=4`** — жёсткий лимит одновременных позиций:
- худший месяц **−33.7% → −11.3%**, edge **+4.9 → +7.7%/сделку** (holdout +11.4%), maxDD 91%→29%.
- режет именно кластерные сделки с отриц. EV → одновременно режет хвост И усиливает edge.
- **Отвергнуто данными:** дневной лимит убытка (хуже edge и хвост), любой CB (режет edge —
  текущий live-CB 5/48h кандидат на отключение), по-сторонний лимит. **dyn_size** — оставить.

**Статус:** ✅ **ВНЕДРЕНО и задеплоено 2026-06-16** (коммит `34e150b`). `MAX_OPEN_POSITIONS=4` в
`execution_config.py` + `paper_loop.at_position_cap()` гейт перед открытием. Дневной лимит убытка
НЕ внедрён (отвергнут данными). Детали в `FUTURE_WORK.md §1`/§0.

---

## 5. Как продолжить (канон плана — `ROADMAP.md`)

> **`ROADMAP.md`** — пошаговый backtest-gated план (гипотеза→тест→критерий→деплой) + журнал
> сделанного + инструменты тестирования/деплоя. Двигаемся по нему по одному шагу. Ниже — сводка.


0. ✅ **ХВОСТ ЗАКРЫТ (2026-06-17): гейдж 2.1 задеплоен** (`0a8d72b`) + UX-фикс (`ce5867d`). Собирали
   backend+frontend **НАТИВНО на VPS** (`git pull` → `docker compose build frontend backend` → `up -d`),
   а НЕ кросс-сборкой на Mac: `next build`/SWC сегфолтится под qemu-amd64 (`signal 11`). Деплой отвязанно
   (`setsid` + опрос `/tmp/*.log`), т.к. SSH рвётся на 1 CPU при долгих командах; `compose up` без
   `--force-recreate` может НЕ пересоздать контейнер при том же теге — форсить. Подробности — память
   `vps3-build-images-locally`.
1. **Ждать гейт** (реальный блокер — рынок в trend/transition, сделки не копятся; не баг). Прогресс:
   `ssh root@187.127.114.34 'bash /root/opt-app/monitor_paper.sh'`. Гейт — `PROJECT_DOSSIER.md` §8.3.
2. **P0: баги достоверности paper-данных** (`FUTURE_WORK §5`): ✅ A1 CB-race (§5.2) СДЕЛАН (`85b5aff`).
   Осталось: отравление пула (§5.1), ZeroDiv при spot=0 (§5.6), open_position→0 (§5.7). Один rebuild
   на все. ⚠️ меняет код → деплой через rebuild (см. §3).
3. **P1: live-корректность** (после гейта, до денег) — broker exception-handling (§5.4),
   reconcile PnL≠0 (§5.3), live-safety мелочи (§6.4-6.6).
4. **ADX dynamic sizing — ❌ ОТВЕРГНУТ** (`2cad943`, бэктест OOS+tail: режет доходность и хвост).
   НЕ внедрять. ADX-скор используется только как индикатор в гейдже 2.1. См. `FUTURE_WORK §8`.
5. **Идти в live (P7)** когда гейт пройден: rebuild (Mac) → фандинг USDT $500–1000 →
   `docker compose --profile trader up -d trader` (создаст `options_trader` БД) → проверить
   чистый reconcile + `bybit_probe.py` → `LIVE_ENABLED=true` (взвести).

---

## 6. Карта ключевых файлов

- **Резюме/доки:** `SESSION_STATE.md` (этот файл) · **`ROADMAP.md`** (пошаговый backtest-gated
  план + журнал + инструменты) · `PROJECT_DOSSIER.md` (стратегия + все бэктесты + changelog +
  постмортем + гейт) · `LIVE_TRADING_HANDOFF.md` (live-сборка) · `FUTURE_WORK.md` (детальный
  бэклог §1-8 + вердикты исследований/код-ревью).
- **Стратегия (source of truth):** `backend/services/strategy_config.py` · `paper_strategy.py` ·
  `paper_loop.py` · `regime.py`.
- **Live-инфра:** `backend/services/{live_sizing,broker,live_safety,reconcile}.py` ·
  `execution.py` · `execution_config.py` · `db/bootstrap.py` · `docker-compose.yml` (`trader`).
- **Гейдж 2.1:** `paper_strategy.entry_proximity()` · эндпоинт `/paper/conditions` (`main.py`) ·
  фронт `frontend/app/page.tsx` (ProximityGauge/FactorBar) + `app/lib/api.ts` (типы).
- **Research:** `tail_overlay_sweep.py` (tail-risk + ADX-sizing×cap, `size_fn`) · `adx_sizing_oos.py`
  (V2+OOS sizing) · `variant_backtest.py` (V2/V3) · `adx_score*.py` · данные в **корне репо** `data/eth_{5m,15m,1h}.json` (365д).
- **Тесты:** `backend/tests/test_{live_sizing,broker,live_safety,reconcile,execution,tail_risk,cb_race,paper_repo,proximity}.py`
  (**62 шт**, все зелёные на py3.11). Фронт: `tsc --noEmit` чист. Прогон без сети/БД: см. §7.
- **Ops:** `vps_cleanup.sh` (авточистка, cron 04:00 UTC) · `monitor_paper.sh` · `paper_cron.sh`.

---

## 7. Доступ

- **VPS:** `ssh root@187.127.114.34` · репо `/root/opt-app` · БД `options_assistant` (live будет
  `options_trader`) · контейнер `opt-app-paper-1` · дашборд :3000 · API :8000.
- **Repo:** `git@github.com:bandurkas/opt.git` · ветка `main` · локально `~/Desktop/options`.
- **Секреты** только в `/root/opt-app/.env` на VPS (НЕ в репо): `BYBIT_API_*`, `TELEGRAM_*`.
- **Память сессий (авто-recall):** `~/.claude/.../memory/project_options_paper_validation.md`.
- **Запуск тестов:** `cd backend && PYTHONPATH=. python3 tests/test_<name>.py` (без сети/БД).
- **⚡ Быстрые локальные бэктесты (нативно arm64, без эмуляции — 2 мин vs 33):**
  `docker run --rm --platform linux/arm64 -v "$PWD/backend:/app" -v "$PWD/data:/data" -w /app
  -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/<harness>.py`. Образ: пересобрать при нужде
  `docker buildx build --platform linux/arm64 -t opt-app-bt:arm64 ./backend --load`. ⚠️ при пайпе
  через `grep` вывод буферизуется до конца; в zsh код возврата — `$?`, не `PIPESTATUS`.
- **Фронт-typecheck:** `frontend/node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`.
