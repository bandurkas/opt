# 👋 START HERE — ETH Options bot (точка входа для любого агента)

> **Читай этот файл первым.** Он ориентирует за 2 минуты и говорит, куда идти дальше.
> Living-доки (всегда актуальны): **этот файл → `SESSION_STATE.md` → `ROADMAP.md`**.
> **Последнее обновление:** 2026-06-16 · HEAD `main` `85b5aff` · local = GitHub = VPS, дерево чистое.

---

## 1. Что это

Бот-продавец опционной премии на **ETH** (Bybit, USDT-settled). Стратегия **V2 hybrid + V3 ADX**
(source of truth — код: `backend/services/strategy_config.py` + `regime.py` + `paper_strategy.py`).
Сейчас работает в **paper-режиме** на VPS3, копит сделки к go-live гейту. Live-инфраструктура
(P2–P6) построена, но **инертна** (за `broker.is_live()`), реальные деньги — только после гейта.

Стек: FastAPI backend + Next.js 16 frontend + Postgres + Redis + поллер, всё в Docker Compose на VPS3.

---

## 2. Текущее состояние (снимок)

- **Paper-бот:** жив на свежем образе, `MAX_OPEN_POSITIONS=4` активен. Данные свежие (поллер
  `Up`, spot живой, свечи ~4 мин). Бот поминутно считает `ret_7d/side/regime/mtf/vol`.
- **Почему не торгует прямо сейчас:** `regime=transition`, а вход разрешён только в `range` —
  это **спроектированный фильтр, не баг**. Ждёт смены режима рынка.
- **Go-live гейт (`PROJECT_DOSSIER.md §8.3`):** **8/≥20–30 циклов** (все TP2, +$57.74,
  equity $457.74). П.4 (концентрация) ✅ закрыт кэпом. SL/CB/dynsize ещё НЕ наблюдались вживую.
- **Главный блокер гейта:** рынок не даёт `range`-окон → циклы не копятся. Не пункт бэклога.
- **Недавно сделано (2026-06-16):** ✅ Фаза A1 — исправлен CB race (`§5.2`): атомарный
  `record_trade_outcome` (`SELECT … FOR UPDATE`) + чистая `_next_cb_state`, +7 тестов, задеплоено;
  ранее: tail-risk кэп `MAX_OPEN_POSITIONS=4`, устранён deploy-landmine, чистка Docker (~5GB).

---

## 3. ⛔ Рабочий принцип (НЕ нарушать)

1. **Ничто, что трогает edge, не катится без бэктеста** на 365д + holdout (OOS). Урок overfit усвоен.
2. **Каждое изменение тестируется ПЕРЕД деплоем:** edge-изменение → бэктест+holdout, потом paper;
   баг/корректность → unit-тест; live-путь → unit-тест (в paper инертно).
3. **Двигаемся по одному шагу**, по заранее заданному критерию. Полный пошаговый план — **`ROADMAP.md`**.
4. **Деплой только rebuild на Mac** (НЕ на VPS — 1 CPU, >40 мин, стопор SSH). См. `ROADMAP.md §2`.

---

## 4. Карта документов

### ✅ Актуальные (читать)
| Файл | Зачем |
|---|---|
| **`START_HERE.md`** | этот файл — точка входа |
| **`SESSION_STATE.md`** | подробный handover: что сделано, состояние, доступ, как продолжить |
| **`ROADMAP.md`** | пошаговый backtest-gated план (гипотеза→тест→критерий→деплой) + инструменты |
| `PROJECT_DOSSIER.md` | всё о проекте: стратегия, ВСЕ бэктесты, changelog, постмортемы, гейт §8.3 |
| `FUTURE_WORK.md` | детальный бэклог §1–8 (баги код-ревью, идеи) + вердикты исследований |
| `LIVE_TRADING_HANDOFF.md` | сборка live-режима (P2–P6), как армировать |

### 🗄️ Исторические (НЕ руководство к действию — только контекст прошлого)
`HANDOFF.md` (2026-05-21, заброшенная fade-стратегия) · `STRATEGY.md` (эпоха sell-Call) ·
`CHANGELOG_V3_HYBRID.md` (эпоха V3 ±2%) · `REBUILD_GUIDE.md` · `README.md`.
⚠️ `HANDOFF.md` содержит УТЁКШИЙ пароль VPS в открытом виде — его надо ротировать и вычистить;
секреты в репо хранить нельзя (только в `/root/opt-app/.env` на VPS).

---

## 5. Следующий шаг

Из `ROADMAP.md §3/§4`, на выбор (решение — за пользователем). Фаза A1 (CB race) ✅ закрыта:
- **Продолжить Фазу A** — A2 отравление пула (`§5.1`), A3 ZeroDiv при spot=0 (`§5.6`),
  A4 `open_position`→0 (`§5.7`); чистые unit-тесты, без риска для edge, одним rebuild на все.
- **Фаза B1 — диагностика range-детектора**: прогон `variant_backtest.py`, понять, почему мало
  циклов (не отсекает ли `regime.py` валидные окна). Без изменений кода.

---

## 6. Доступ и команды

```bash
# VPS (секреты только в /root/opt-app/.env; пароль НЕ хранить в репо)
ssh root@187.127.114.34            # репо /root/opt-app, БД options_assistant, контейнер opt-app-paper-1

# Прогресс к гейту / здоровье бота
ssh root@187.127.114.34 'bash /root/opt-app/monitor_paper.sh'

# Логи paper-бота — ⚠️ `docker logs` ВИСНЕТ (containerd image-store, 1 CPU). Читать json-логфайл:
ssh root@187.127.114.34 "tail \$(docker inspect opt-app-paper-1 --format '{{.LogPath}}')"

# Бэктест / тесты (локально). Данные: data/eth_{5m,15m,1h}.json (365д до 2026-05-31)
cd backend && PYTHONPATH=. python3 services/variant_backtest.py
cd backend && PYTHONPATH=. python3 services/tail_overlay_sweep.py
# Unit-тесты: локальный python 3.9 НЕ импортирует код (нужен 3.10+); гонять в контейнере (py3.11):
#   см. SESSION_STATE.md §7 / ROADMAP.md §2

# Деплой (после прохождения теста) — полный рецепт в ROADMAP.md §2:
#   правка → commit → git pull на VPS → buildx --platform linux/amd64 на Mac → save/load → recreate
```

**Гочи (важно):**
- `docker logs opt-app-paper-1` зависает → читать json-логфайл (см. выше).
- Сборка образа — **на Mac** (`--platform linux/amd64`), НЕ на VPS.
- Локальный `python3` = 3.9 → код (3.10+ синтаксис) импортируется только в контейнере/на VPS (3.11).
- `/Users/sabar` (домашняя папка Mac) — тоже git-репо: коммить из `~/Desktop/options`, не из `$HOME`.

---

*Когда закончишь сессию — обнови `SESSION_STATE.md` и журнал `ROADMAP.md §1`, держи local=GitHub=VPS в синке.*
