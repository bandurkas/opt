# Handover / Resume checkpoint — ETH Options project

> 👉 Новый агент: начни с **`START_HERE.md`** (точка входа), затем этот файл и `ROADMAP.md`.
> Самодостаточный файл, чтобы продолжить в новом чате. **Дата:** 2026-06-16 ·
> **HEAD:** `48f2fc0`+ · ветка `main` · **local = GitHub = VPS**, дерево чистое.
> Контекст: открой `PROJECT_DOSSIER.md` (всё о проекте) первым.

---

## 0. TL;DR — где мы

Бот-продавец опционной премии на ETH (Bybit, USDT-settled), стратегия **V2 hybrid + V3 ADX**
(source of truth: `backend/services/strategy_config.py` + `regime.py`). Работает **paper**-режим
на VPS3, копит сделки к go-live гейту. Live-инфраструктура (P2–P6) **построена и инертна**, не
задеплоена. Реальные деньги — только после гейта + фандинга + армирования.

Что НЕ доказано и блокирует live: paper ещё не прошёл гейт (нужно ≥20–30 полных циклов в разных
режимах в пределах 30–50% бэктеста + наблюдать SL/CB/dynsize вживую). См. §8.3 досье.

**Сессия 2026-06-16 (вторая):** (1) почистили Docker на VPS3 — освобождено ~5GB (build-кэш
4.39GB), диск 37%→28%, мусора в контейнерах/томах не было; (2) **внедрили tail-risk кэп
`MAX_OPEN_POSITIONS=4`** (P1 приоритет, закрывает п.4 гейта) — код+5 тестов (47 всего, все
зелёные), **чистая пересборка образа на Mac (amd64) → save/load на VPS → задеплоено в paper**;
это же **устранило старый deploy-landmine** (образ больше не «старше Phase 0»); (3) приоритизировали
`FUTURE_WORK.md` под близость к live-валидации (§0 там). Бот пересоздан с 0 открытых позиций,
`MAX_OPEN_POSITIONS=4` подтверждён в рантайме, режим уже сменился `trend→transition`.

---

## 1. Что сделано (хронология, коммиты `b45ecbb..48f2fc0`)

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

- **Paper-бот:** жив (пересоздан на свежем образе 2026-06-16), без ошибок, копит циклы к гейту.
  `MAX_OPEN_POSITIONS=4` активен в рантайме. Часто `regime=trend/transition` → сторона
  дисквалифицируется (нужен `range`) → корректно ждёт. Не баг. Гейт: **8/≥20–30 циклов** (все TP2,
  +$57.74, equity $457.74); п.4 (концентрация) теперь закрыт кэпом; SL/CB/dynsize ещё не наблюдались.
- **Мониторинг:** VPS-cron каждые 3ч (`/root/opt-app/paper_cron.sh`) → Telegram на
  SL/CB/dynsize/+5 циклов/гейт. Ручная проверка: `bash /root/opt-app/monitor_paper.sh`.
  ⚠️ `docker logs opt-app-paper-1` подвисает (containerd image-store, 1 CPU) — читать json-логфайл
  напрямую: `tail $(docker inspect opt-app-paper-1 --format '{{.LogPath}}')`.
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
look-ahead; trades кэшируются в `/tmp/tail_trades_v3.json` — `/tmp` эфемерен, регенерация ~3 мин).

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


1. **Ждать гейт** (реальный блокер — рынок в trend/transition, сделки не копятся; не баг). Прогресс:
   `ssh root@187.127.114.34 'bash /root/opt-app/monitor_paper.sh'`. Гейт — `PROJECT_DOSSIER.md` §8.3.
2. **P0: баги достоверности paper-данных** (`FUTURE_WORK §5`, приоритизация в §0) — CB-race (§5.2),
   отравление пула (§5.1), ZeroDiv при spot=0 (§5.6), open_position→0 (§5.7). Чинить, чтобы
   статистика гейта не искажалась молча. ⚠️ это меняет код → деплой через rebuild (см. §3).
3. **P1: live-корректность** (после гейта, до денег) — broker exception-handling (§5.4),
   reconcile PnL≠0 (§5.3), live-safety мелочи (§6.4-6.6).
4. **P2 (НЕ сейчас): ADX dynamic sizing** (§8, +$1600/год бэктест) — меняет стратегию, сбросит
   валидацию; только ПОСЛЕ гейта.
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
- **Research:** `backend/services/tail_overlay_sweep.py` (tail-risk) · `variant_backtest.py`
  (V2/V3) · `adx_score*.py` (ADX-исследование) · данные `backend/data/eth_{5m,15m,1h}.json` (365д).
- **Тесты:** `backend/tests/test_{live_sizing,broker,live_safety,reconcile,execution,tail_risk}.py`
  (47 шт, все зелёные на py3.11). Прогон без сети/БД в контейнере: см. §7.

---

## 7. Доступ

- **VPS:** `ssh root@187.127.114.34` · репо `/root/opt-app` · БД `options_assistant` (live будет
  `options_trader`) · контейнер `opt-app-paper-1` · дашборд :3000 · API :8000.
- **Repo:** `git@github.com:bandurkas/opt.git` · ветка `main` · локально `~/Desktop/options`.
- **Секреты** только в `/root/opt-app/.env` на VPS (НЕ в репо): `BYBIT_API_*`, `TELEGRAM_*`.
- **Память сессий (авто-recall):** `~/.claude/.../memory/project_options_paper_validation.md`.
- **Запуск тестов:** `cd backend && PYTHONPATH=. python3 tests/test_<name>.py` (без сети/БД).
- **Бэктест:** `cd backend && PYTHONPATH=. python3 services/tail_overlay_sweep.py` (или `variant_backtest.py`).
