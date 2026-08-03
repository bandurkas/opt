import os
import asyncio
import requests
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# Токен берем из переменной окружения (или можно вписать сюда для теста)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")

# URL бэкенда (внутри Docker сети он называется backend, локально - localhost)
API_URL = os.getenv("API_URL", "http://localhost:8000/api/v1")

if not TELEGRAM_TOKEN:
    print("ВНИМАНИЕ: TELEGRAM_TOKEN не установлен! Бот не сможет запуститься. Вставьте токен в файл telegram_bot.py или передайте через окружение.")

bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

@dp.message(Command("start", "help"))
async def cmd_start(message: types.Message):
    text = (
        "👋 <b>Добро пожаловать в ETH Options Assistant!</b>\n\n"
        "Доступные команды:\n"
        "/eth - Анализ текущего рынка и случайного Call опциона (тест)\n"
        "/btc - В разработке\n"
        "/calls - В разработке\n"
        "/puts - В разработке\n"
        "/watch - В разработке"
    )
    await message.answer(text)

@dp.message(Command("eth"))
async def cmd_eth(message: types.Message):
    msg = await message.answer("🔍 Запрашиваю данные с Bybit и анализирую...")
    try:
        # Получаем цену ETH
        price_resp = requests.get(f"{API_URL}/market/eth-price").json()
        price = price_resp.get("price", 0)
        
        # Получаем анализ (тестовый эндпоинт, который мы написали)
        analysis_resp = requests.get(f"{API_URL}/analysis/test").json()
        contract = analysis_resp["contract"]
        distance = analysis_resp["distance"]["distance_usd"]
        distance_perc = analysis_resp["distance"]["distance_percent"]
        hours = analysis_resp["time"]["hours_to_expiry"]
        theta = analysis_resp["time"]["theta_risk"]
        signal = analysis_resp["entry_evaluation"]["signal"]
        score = analysis_resp["entry_evaluation"]["score"]
        
        text = (
            f"📈 <b>ETH:</b> {price}$\n"
            f"📄 <b>Контракт:</b> {contract}\n"
            f"📏 <b>До страйка:</b> {distance}$ ({distance_perc}%)\n"
            f"⏳ <b>До экспирации:</b> {hours}ч\n"
            f"⚠️ <b>Theta риск:</b> {theta}\n"
            f"⚡ <b>Импульс:</b> сильный (test)\n\n"
            f"🎯 <b>Оценка: {score}/10</b> ({signal})"
        )
        await msg.edit_text(text)
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка получения данных: {e}")

# Boba1 profit-milestone alerts (services/btc_straddle_loop.py ->
# telegram_notify.notify_profit_decision) carry inline Закрыть/Держать
# buttons. Long-polling (dp.start_polling below) already receives
# callback_query updates with no extra setup — just handlers.
@dp.callback_query(F.data.startswith("boba1_close:"))
async def cb_boba1_close(callback: types.CallbackQuery):
    cycle_id = callback.data.split(":", 1)[1]
    try:
        requests.post(f"{API_URL}/control/btc_straddle/close_pair/{cycle_id}", timeout=5)
        await callback.answer("Закрываю...")
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.edit_text(callback.message.html_text + "\n\n✅ <b>Закрыто оператором</b>")
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)


@dp.callback_query(F.data.startswith("boba1_hold:"))
async def cb_boba1_hold(callback: types.CallbackQuery):
    await callback.answer("Держим 👍")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.edit_text(callback.message.html_text + "\n\n⏳ <b>Решение: держим</b>")


async def main():
    if not TELEGRAM_TOKEN:
        return
    print("Бот успешно запущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
