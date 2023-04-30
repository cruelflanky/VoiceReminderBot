import os
import asyncio
import calendar
from datetime import datetime, timedelta

from aiogram import Bot, types
from aiogram.dispatcher import Dispatcher
from aiogram.types import InputFile
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.dispatcher.filters import Command
from aiogram.dispatcher.filters.builtin import CommandHelp
from aiogram.types.inline_keyboard import InlineKeyboardButton
from aiogram.utils.callback_data import CallbackData

API_TOKEN = ""

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# Initialize callback_data factories
hour_cb = CallbackData("hour", "hour", "year", "month", "day")
date_cb = CallbackData("date", "action", "year", "month", "day")
time_cb = CallbackData("time", "hour", "minute", "year", "month", "day")
minute_cb = CallbackData("minute", "minute", "hour", "year", "month", "day")

# Create a dictionary to store reminders
reminders = {}


async def show_reminders(user_id: int) -> str:
    if user_id not in reminders:
        return "You have no reminders."

    reminders_text = ""
    for idx, reminder in enumerate(reminders[user_id]):
        date_str = reminder[0].strftime('%Y-%m-%d %H:%M')
        reminders_text += f"{idx + 1}. {date_str}\n"

    return reminders_text if reminders_text else "You have no reminders."


@dp.message_handler(commands=["reminders"])
async def reminders_handler(message: types.Message):
    user_id = message.from_user.id
    reminders_text = await show_reminders(user_id)
    await message.reply(reminders_text)


@dp.message_handler(Command("start"))
async def cmd_start(message: types.Message):
    await message.reply("Welcome to VoiceReminderBot! Send me a voice message and I'll help you set a reminder.")


@dp.message_handler(content_types=types.ContentType.VOICE)
async def voice_handler(message: types.Message):
    voice_message_id = message.voice.file_id

    today = datetime.now()
    markup = types.InlineKeyboardMarkup(row_width=3)

    for i in range(0, 12):
        month = today + timedelta(days=30 * i)
        markup.insert(InlineKeyboardButton(text=month.strftime("%B"),
                                           callback_data=date_cb.new(action="month", year=month.year, month=month.month,
                                                                     day=month.day)))

    await message.reply("Choose the month for the reminder:", reply_markup=markup)


@dp.callback_query_handler(date_cb.filter(action="month"))
async def process_month_callback(callback_query: types.CallbackQuery, callback_data: dict):
    year, month = int(callback_data["year"]), int(callback_data["month"])
    _, last_day = calendar.monthrange(year, month)

    today = datetime.now().date()
    is_current_month = (year, month) == (today.year, today.month)

    markup = types.InlineKeyboardMarkup(row_width=7)

    for day in range(1, last_day + 1):
        if not is_current_month or day >= today.day:
            markup.insert(InlineKeyboardButton(text=str(day),
                                               callback_data=date_cb.new(action="day", year=year, month=month,
                                                                         day=day)))

    prev_month = datetime(year, month, 1) - timedelta(days=1)
    next_month = datetime(year, month, 1) + timedelta(days=32)

    markup.row(
        InlineKeyboardButton(text="<<",
                             callback_data=date_cb.new(action="month", year=prev_month.year, month=prev_month.month,
                                                       day=prev_month.day)),
        InlineKeyboardButton(text=">>",
                             callback_data=date_cb.new(action="month", year=next_month.year, month=next_month.month,
                                                       day=next_month.day)),
    )

    await callback_query.message.edit_text(
        f"Selected {calendar.month_name[month]} {year}. Choose a day for the reminder:", reply_markup=markup)


@dp.callback_query_handler(date_cb.filter(action="day"))
async def process_day_callback(callback_query: types.CallbackQuery, callback_data: dict):
    year, month, day = int(callback_data["year"]), int(callback_data["month"]), int(callback_data["day"])

    markup = types.InlineKeyboardMarkup(row_width=4)

    for hour in range(0, 24):
        markup.insert(InlineKeyboardButton(text=f"{hour:02}",
                                           callback_data=hour_cb.new(hour=hour, year=year, month=month, day=day)))

    await callback_query.message.edit_text(
        f"Selected {datetime(year, month, day).strftime('%Y-%m-%d')}. Choose an hour for the reminder:",
        reply_markup=markup)


@dp.callback_query_handler(hour_cb.filter())
async def process_hour_callback(callback_query: types.CallbackQuery, callback_data: dict):
    hour, year, month, day = int(callback_data["hour"]), int(callback_data["year"]), int(callback_data["month"]), int(
        callback_data["day"])

    markup = types.InlineKeyboardMarkup(row_width=6)

    for minute in range(0, 60):
        markup.insert(InlineKeyboardButton(text=f"{minute:02}",
                                           callback_data=minute_cb.new(minute=minute, hour=hour, year=year, month=month,
                                                                       day=day)))

    await callback_query.message.edit_text(f"Selected {hour:02}:00. Choose the minutes for the reminder:",
                                           reply_markup=markup)


@dp.callback_query_handler(minute_cb.filter())
async def process_minute_callback(callback_query: types.CallbackQuery, callback_data: dict):
    hour, minute, year, month, day = int(callback_data["hour"]), int(callback_data["minute"]), int(
        callback_data["year"]), int(callback_data["month"]), int(callback_data["day"])
    selected_time = datetime(year, month, day, hour, minute, second=0, microsecond=0)

    user_id = callback_query.from_user.id
    voice_file_id = callback_query.message.reply_to_message.voice.file_id

    # Store the reminder in the reminders dictionary
    if user_id not in reminders:
        reminders[user_id] = []

    reminders[user_id].append((selected_time, voice_file_id))

    # Send confirmation message
    await callback_query.answer()
    await callback_query.message.edit_text(
        f"Reminder set for {selected_time.strftime('%Y-%m-%d %H:%M')}. You'll receive your voice message at the specified time."
    )

    # Schedule sending the voice message
    reminder_delta = (selected_time - datetime.now()).total_seconds()
    await asyncio.sleep(reminder_delta)

    voice_file = await bot.download_file_by_id(voice_file_id)
    with open("temp_voice.ogg", "wb") as f:
        f.write(voice_file.getvalue())

    with open("temp_voice.ogg", "rb") as f:
        await bot.send_voice(chat_id=user_id, voice=InputFile(f))

    try:
        os.remove("temp_voice.ogg")
    except FileNotFoundError:
        pass

    # Remove the reminder from the list
    reminders[user_id].remove((selected_time, voice_file_id))


@dp.message_handler(CommandHelp())
async def cmd_help(message: types.Message):
    text = (
        "This is a VoiceReminderBot.\n"
        "Send me a voice message and I'll help you set a reminder.\n"
        "Use /reminders command to see the list of your reminders.\n"
    )
    await message.reply(text)


if __name__ == "__main__":
    from aiogram import executor

    executor.start_polling(dp, skip_updates=True)
