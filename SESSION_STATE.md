# Resume checkpoint — ETH Options project

> Снимок состояния, чтобы продолжить с любого момента (как git stash, но всё уже
> закоммичено). **Дата:** 2026-06-16 · **HEAD:** `7556004` · ветка `main` ·
> local = GitHub = VPS, дерево чистое (0 несохранённого).

---

## TL;DR — где мы

Бот-продавец опционной премии на ETH (Bybit, USDT-settled). Работает **paper**-режим
на VPS3. Этой сессией: починили «молчание» бота (критич. SQL-баг), захарденили,
написали полное досье, настроили мониторинг, и построили **всю live-инфраструктуру
P2–P6** (инертна, не задеплоена). Реальные деньги — после прохождения gate.

---

## Что сделано в этой сессии (9 коммитов `b45ecbb..7556004`)

1. **`52f9dc6` — критич. фикс.** `close_position` падал на неэкранированном `%` в
   `LIKE 'closed_%'` → `IndexError` на каждом закрытии → **0 закрытий за весь деплой**,
   8 позиций залипли, маржа залочена, 3 дня тишины. Фикс `'closed_%%'`. После рестарта
   все 8 закрылись по TP2: **+$57.74, equity $457.74, WR 100%**.
2. **`22dbbde` — харденинг.** Traceback в логи (баг прятался из-за «голого» `repr(e)`),
   изоляция закрытий по позициям, Telegram-алерт на ошибки цикла.
3. **`df52b6d` — доки/ops.** `PROJECT_DOSSIER.md` (полное досье), `FUTURE_WORK.md`
   (бэклог), `monitor_paper.sh` + `paper_cron.sh` (мониторинг на VPS, cron 3ч + TG-алерты).
4. **`1e3f61a` P2** — `live_sizing.py` (сайзинг от реального USDT, reduce-on-reject, 11 тестов).
5. **`76e4d5b` P3** — `broker.py` (реальные филлы + equity из кошелька, 8 тестов).
6. **`13a4780` P4** — `live_safety.py` (kill-switch/дневной лимит/спред/слиппедж, 8 тестов).
7. **`b7c1412` P5** — `reconcile.py` (сверка биржа↔БД, лечит ручные закрытия, 9 тестов).
8. **`f296e6c` P6** — отдельная БД `options_trader` + сервис `trader` (`profiles:[trader]`, armed-OFF).
9. **`7556004`** — обновлён `LIVE_TRADING_HANDOFF.md` (P2–P6 + landmine + gate).

Все live-пути за `broker.is_live()` (=`trading_armed()`) → в paper поведение прежнее.
**42 backend-теста проходят** (`test_{live_sizing,broker,live_safety,reconcile,execution}.py`).

---

## Текущее состояние системы

- **Paper-бот:** жив, без ошибок, копит циклы к gate. Сейчас `regime=trend` (аптренд)
  → Put дисквалифицирован (нужен `range`) → корректно ждёт. Это не баг.
- **Мониторинг:** VPS-cron каждые 3ч (`/root/opt-app/paper_cron.sh`) шлёт Telegram на
  SL/CB/dynsize/+5 циклов/gate. Ручная проверка: `bash /root/opt-app/monitor_paper.sh`.
- **Live-инфра P2–P6:** в репозитории, **НЕ задеплоена**, полностью инертна.

---

## ⚠️ Deploy landmine (прочитать перед любым деплоем)

Запущенный `paper`-контейнер пропатчен через `docker cp` поверх образа **старше Phase 0**.
`docker compose up -d` / пересоздание вернёт старый образ (вернёт SQL-баг + потеряет
харденинг). **Перед пересозданием — `docker compose build paper backend`.**

---

## Как продолжить (выбрать одно)

1. **Ждать gate.** Ничего не делать; cron сам уведомит. Проверять прогресс:
   `ssh root@187.127.114.34 'bash /root/opt-app/monitor_paper.sh'`.
   Gate (см. `PROJECT_DOSSIER.md` §8.3): ≥20–30 циклов в разных режимах, paper в
   пределах 30–50% бэктеста, наблюдать SL+CB+dynsize в бою.
2. **Tail-risk overlay (`FUTURE_WORK.md` §1).** Tier 1 = дневной лимит убытка (в paper
   его нет, хотя в live-конфиге заложен) + лимит концентрации; сначала бэктест на
   `variant_backtest.py` против месяца −42%, потом деплой.
3. **Идти в live (P7),** когда gate пройден: rebuild образа → фандинг USDT $500–1000 →
   `docker compose --profile trader up -d trader` (создаст `options_trader` БД) →
   проверить чистый reconcile + `bybit_probe.py` → `LIVE_ENABLED=true` (взвести).

---

## Карта ключевых файлов

- **Это досье/резюме:** `SESSION_STATE.md` (этот файл), `PROJECT_DOSSIER.md` (всё о проекте +
  стратегия + все бэктесты + changelog), `LIVE_TRADING_HANDOFF.md` (live-сборка), `FUTURE_WORK.md` (бэклог).
- **Стратегия (source of truth):** `backend/services/strategy_config.py`,
  `paper_strategy.py`, `paper_loop.py`, `regime.py`.
- **Live-инфра:** `backend/services/{live_sizing,broker,live_safety,reconcile}.py`,
  `execution.py`, `execution_config.py`, `db/bootstrap.py`, `docker-compose.yml` (`trader`).
- **VPS:** `187.127.114.34`, репо `/root/opt-app`, БД `options_assistant`, контейнер `opt-app-paper-1`.
  Память сессий: `~/.claude/.../memory/project_options_paper_validation.md`.
