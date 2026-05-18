# ETH Options Assistant

Сканер опционов Bybit с ранжированной выдачей точек входа. Бэкенд (FastAPI)
тянет live-данные (spot, 1h-свечи, цепочка опционов с greeks/IV/OI), считает
рыночный контекст и оценивает каждый контракт 0–10. Фронтенд (Next.js 16)
показывает топ-3 с конкретным планом сделки.

## Что внутри

- **backend/** — FastAPI + pybit.
  - `services/bybit_client.py` — обёртка над Bybit V5 (spot, klines, options chain, orderbook).
  - `services/market_data.py` — EMA9/21, RSI 1h, импульс, всплеск объёма, ближайшие уровни.
  - `services/analysis.py` — фильтрация цепочки, скоринг, генерация плана входа.
  - `main.py` — эндпоинты:
    - `GET /api/v1/market/eth-price`
    - `GET /api/v1/market/snapshot`
    - `GET /api/v1/analysis/top?base_coin=ETH&top_n=3&side=call|put&max_distance_pct=8&max_hours=168`
    - `GET /api/v1/analysis/test` (legacy stub для Telegram)
  - `telegram_bot.py` — aiogram-бот, команда `/eth`.
- **frontend/** — Next.js 16 + Tailwind v4, дашборд с фильтрами и автообновлением 30с.

## Локальный запуск

```bash
# 1. Поднять бэкенд + redis + postgres
docker compose up backend redis postgres

# 2. В отдельном терминале — фронтенд
cd frontend
cp .env.local.example .env.local
npm install
npm run dev
```

Открыть http://localhost:3000.

Если хочешь подключить Telegram-бот: добавь `TELEGRAM_TOKEN=...` в `.env`
рядом с docker-compose.yml и подними сервис `bot`:

```bash
TELEGRAM_TOKEN=123:abc docker compose up bot
```

## Деплой

### Бэкенд → Railway

1. Загрузить репо на GitHub.
2. На railway.app → New Project → Deploy from GitHub repo → выбрать репо,
   root directory = `backend/`.
3. Railway сам подхватит `Dockerfile` и `railway.json`.
4. Скопировать сгенерированный public URL — пригодится для фронта.

### Фронтенд → Vercel

1. Импортировать репо на vercel.com.
2. Root directory = `frontend/`.
3. Environment variable: `NEXT_PUBLIC_API_URL=https://<railway-url>/api/v1`.
4. Deploy.

## Как читать сигнал

Каждая карточка топа содержит:

- **Лимит-цена** — куда выставлять заявку (середина bid/ask минус 0.5%).
- **Контрактов** — размер позиции под бюджет риска $100 (зашит в коде, легко поправить).
- **Max риск** — что потеряешь если контракт обнулится.
- **TP/SL по premium** — +60% / −40% от премии.
- **Target / Stop spot** — куда должен пойти ETH, чтобы зафиксировать или резать.
- **Theta / Спред / IV / Delta / OI** — контекст для ручной проверки.
- **Разбор оценки** — раскрывается, показывает, за что добавили/сняли баллы.

## Дисклеймер

Образовательный инструмент. Сигналы строятся на простых правилах
(EMA-кросс + RSI + ликвидность + Theta + расстояние до страйка). Не финансовая
рекомендация.
