# 👋 START HERE — ETH Options bot (точка входа для любого агента)

> **Читай этот файл первым.** Он ориентирует за 2 минуты и говорит, куда идти дальше.
> Living-доки (всегда актуальны): **этот файл → `SESSION_STATE.md` → `ROADMAP.md`**.
> **Последнее обновление:** 2026-06-23 · HEAD `main` `f21e512` · local = GitHub = VPS3, дерево чистое.
> ✅ **Mission Control задеплоен** (auth + pause/close-all + 3 отдельных Bybit-аккаунта). Боты
> переименованы в позывные: **Boba1**=BTC straddle, **Grogu1**=ETH straddle, **Sniper1**=ETH signal.

---

## 🚀 НАЧАТЬ ЗДЕСЬ (новая сессия, 2026-06-23)

**Это уже не один ETH-бот — это 3 бота, каждый со своим Bybit-аккаунтом:**

| Позывной | Стратегия | Paper equity | Реальный Bybit-кошелёк | Статус |
|---|---|---|---|---|
| **Boba1** | BTC 24h short straddle | $2005.71 (старт $2000), 5 закрыто (4W/1L, 80% WR), 1 открыта | **$1880** (readOnly исправлен пользователем) | ✅ ключ работает, готов к live по балансу |
| **Grogu1** | ETH 24h short straddle | $1194.39 (старт $1200), 6 закрыто (4W/2L, но avg −11.7%/trade — один SL утянул) | **$0.82** | ✅ ключ работает, ⚠️ счёт пустой — нужно фондировать перед live |
| **Sniper1** | ETH signal bot (V3 hybrid) | $800 (старт $800), 0 сделок | ключ НЕ задан | paper работает; ⚠️ см. подозрение на потерю входов ниже |

Все 3 бота — **paper-режим** (`broker.is_live()=False` везде), реальные Bybit-ключи уже привязаны
и проверены (`get_api_key_information`/`get_wallet_balance`/`get_positions(category="option")` —
все 3 счёта UTA=1, `OptionsTrade`-право есть), но боевые ордера НЕ идут, пока `LIVE_ENABLED` не
взведён через `docker compose --profile trader up -d trader/btc_trader`.

**Mission Control** (`http://187.127.114.34:3000`, пароль у пользователя) — панель управления:
пауза/резюм и экстренное закрытие позиций на каждого бота отдельно + общая паник-кнопка
"Стоп + закрыть всё", плюс смена API-ключа каждого аккаунта прямо из дашборда (без редеплоя,
подхватывается ботом за ~60с). Подробности архитектуры — память `project_mission_control`
(`~/.claude/projects/-Users-sabar/memory/project_mission_control.md`).

**⚠️ Открытый вопрос (не баг, требует бэктеста):** у Sniper1 наблюдался гейдж "Условия входа"
на 100%/ready (макро-фильтры все сошлись), но точный генератор сигнала на закрытии свечи вернул
"no signal" — сделка не открылась. Пользователь подозревает, что так теряется много сделок,
просил перепроверить бэктестами. См. память `finding_sniper1_entry_gap_suspected`. **Не трогать
пороги генератора без свежего train/holdout** (правило проекта, см. §3 ниже).

---

## 1. Что это

Три бота продают опционную премию на Bybit (USDT-settled), каждый — отдельный Docker-контейнер,
отдельные таблицы в Postgres, отдельный Bybit-аккаунт:
- **Sniper1** (`paper_loop.py`) — направленные сигнальные входы, стратегия V2 hybrid + V3 ADX
  (source of truth: `backend/services/strategy_config.py` + `regime.py` + `paper_strategy.py`).
- **Boba1** (`btc_straddle_loop.py`) и **Grogu1** (`eth_straddle_loop.py`) — безусловные 24-часовые
  short-straddle (продают ATM call+put каждый цикл, чистый VRP-харвест, без входного фильтра).

Стек: FastAPI backend + Next.js 16 frontend + Postgres + поллер, всё в Docker Compose на VPS3
(`187.127.114.34`). Бэкенд гейтится паролем (Mission Control), фронт — Next.js `proxy.ts`.

---

## 2. Текущее состояние (снимок 2026-06-23)

- **Все 3 бота живы**, 0 ошибок в логах после деплоя Mission Control + переименования аккаунтов.
- **Sniper1 (ETH signal) гейт:** 0/≥20–30 циклов — рынок даёт сигналы редко (см. открытый вопрос
  выше), реальный блокер — не баг.
- **Boba1/Grogu1 straddle-боты** копят циклы заметно быстрее (без входного фильтра): 5 и 6 закрытых
  циклов соответственно, оба профитные по equity, но Grogu1's avg pnl/trade отрицательный
  (один SL съел несколько TP2) — это ожидаемо для unconditional straddle, не алярм.
- **Mission Control:** auth + per-bot pause/close-all + 3 раздельных зашифрованных Bybit-ключа —
  ЗАДЕПЛОЕНО и проверено живым curl + headless-браузер скриншотом (UI — per-bot HUD-панели,
  Orbitron callsigns, цветовая идентичность на бота).
- **Реальные Bybit-ключи привязаны** (Boba1, Grogu1) и подтверждены аутентифицированным вызовом
  Bybit API — НЕ просто "ключ сохранён", а реально протестировано соединение + права + баланс.
  Sniper1 — слот пустой, пользователь добавит позже через дашборд.

---

## 3. ⛔ Рабочий принцип (НЕ нарушать)

1. **Ничто, что трогает edge (входы/выходы стратегии), не катится без бэктеста** на полной истории
   + holdout (OOS). Урок overfit усвоен на всех 3 ботах.
2. **Каждое изменение тестируется ПЕРЕД деплоем:** edge-изменение → бэктест+holdout, потом paper;
   баг/корректность → unit-тест; live-путь → unit-тест (в paper инертно).
3. **Workflow строгий:** архитектура → код → ревью → тест → ревью → деплой. Для live-money-adjacent
   изменений (Mission Control, ключи) — минимум 2-3 раунда ревью перед деплоем (правило подтверждено
   пользователем явно 2026-06-23).
4. **Сборка образов — на VPS3** (2 CPU теперь, нативная сборка работает быстро). Старое правило
   "собирать на Mac" устарело (было актуально при 1 CPU VPS).
5. **Каждый бот — свой Bybit-аккаунт.** Никогда не делить капитал/ключи между ботами — архитектура
   на это рассчитана (`db/accounts_repo.py`, `MC_ACCOUNT_NAME` per service в `docker-compose.yml`).

---

## 4. Карта документов

### ✅ Актуальные (читать)
| Файл | Зачем |
|---|---|
| **`START_HERE.md`** | этот файл — точка входа |
| **`SESSION_STATE.md`** | подробный handover: что сделано, состояние, доступ, как продолжить |
| **`ROADMAP.md`** | пошаговый backtest-gated план (для Sniper1/ETH-сигнального бота) |
| `PROJECT_DOSSIER.md` | стратегия Sniper1 + бэктесты + changelog + постмортемы |
| `BTC_STRADDLE_HANDOFF.md` / `ETH_STRADDLE_PAPER_BOT_HANDOFF.md` | архитектура straddle-ботов |
| `LIVE_TRADING_HANDOFF.md` | сборка live-режима (P2–P6), как армировать |
| Память `project_mission_control` | архитектура Mission Control + таблица позывной↔аккаунт↔стратегия |
| Память `finding_sniper1_entry_gap_suspected` | открытый вопрос про потерю входов |

### 🗄️ Исторические (контекст прошлого, НЕ руководство к действию)
`HANDOFF.md` (2026-05-21, заброшенная fade-стратегия — содержит УТЁКШИЙ старый пароль VPS,
давно неактуален) · `STRATEGY.md` · `CHANGELOG_V3_HYBRID.md` · `REBUILD_GUIDE.md` · `README.md`.

---

## 5. Следующий шаг (на выбор, решение — за пользователем)

- **Разобраться с подозрением Sniper1** — перепроверить бэктестами, не теряет ли генератор сигнала
  реальные входы при зелёном гейдже (см. §2 выше). Не менять пороги без train/holdout.
- **Фондировать Grogu1** ($0.82 на реальном кошельке) перед тем как думать про live.
- **systemd-автозапуск для slow carry бота** — давно отложено (`finding_slow_carry_bot_no_autostart`),
  предложить пользователю снова.
- **Postgres-бэкапы (P0 из reliability roadmap)** — до сих пор НЕ реализовано, единственный том с
  данными всех 3 ботов без бэкапа. См. `project_options_reliability_roadmap`.

---

## 6. Доступ и команды

```bash
# VPS3 (секреты в /root/opt-app/.env, НЕ в репо)
ssh root@187.127.114.34            # репо /root/opt-app, дашборд :3000, API :8000

# Mission Control — войти через UI или curl:
curl -s -X POST http://187.127.114.34:8000/api/v1/auth/login \
  -H "Content-Type: application/json" -d '{"password":"<пароль у пользователя>"}'
# Cookie из ответа (Set-Cookie: mc_session=...) → передавать в Cookie-заголовке дальше:
#   GET  /api/v1/control/status                       — статус всех 3 ботов
#   POST /api/v1/control/{bot}/pause|resume            — bot ∈ eth_signal|btc_straddle|eth_straddle
#   POST /api/v1/control/{bot}/close-all               — экстренное закрытие (per-bot)
#   POST /api/v1/control/close-all                     — экстренное закрытие ВСЕХ
#   GET  /api/v1/settings/credentials                  — список 3 аккаунтов (masked)
#   POST /api/v1/settings/credentials/{Boba1|Grogu1|Sniper1} — смена ключа

# Логи — `docker logs` может виснуть (1 CPU history). Читать json-логфайл:
ssh root@187.127.114.34 "tail \$(docker inspect opt-app-paper-1 --format '{{.LogPath}}')"
#   opt-app-paper-1=Sniper1, opt-app-btc_paper-1=Boba1, opt-app-eth_straddle_paper-1=Grogu1

# Деплой (теперь — нативно на VPS3, не на Mac):
ssh root@187.127.114.34 'cd /root/opt-app && git pull && docker compose build <service> && \
  docker compose up -d --no-build --force-recreate <service>'

# Бэктест / unit-тесты Sniper1-стратегии (локально, нужен py3.11 контейнер):
cd backend && PYTHONPATH=. python3 services/variant_backtest.py
docker run --rm -v "$(pwd)":/app -w /app opt-app-backend:latest \
  sh -c "pip install -q pytest cryptography && python -m pytest tests/ -q"
```

**Гочи (важно):**
- `docker logs opt-app-paper-1` зависает → читать json-логфайл (см. выше).
- Сборка образа — **нативно на VPS3** (2 CPU, быстро). Старое правило "только на Mac" устарело.
- Локальный `python3` на Mac = 3.9 → код (3.10+ синтаксис) импортируется только в контейнере (3.11).
- `/Users/sabar` (домашняя папка Mac) — тоже git-репо: коммить из `~/Desktop/options`, не из `$HOME`.
- Пароль/ключи Bybit — **никогда не в репо**, только в `/root/opt-app/.env` на VPS3 + зашифрованно
  в Postgres (`exchange_credentials`, Fernet, ключ `CREDENTIALS_MASTER_KEY`).

---

*Когда закончишь сессию — обнови `SESSION_STATE.md` и держи local=GitHub=VPS3 в синке.*
