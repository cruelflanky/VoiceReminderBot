import os
import asyncio
import logging
import calendar
from pytz import timezone
from dotenv import load_dotenv
from datetime import datetime, timedelta

from aiogram import Bot, types
from aiogram.types import InputFile
from geopy.geocoders import Nominatim
from aiogram.dispatcher import Dispatcher
from timezonefinder import TimezoneFinder
from aiogram.utils.callback_data import CallbackData
from aiogram.dispatcher.filters.builtin import CommandHelp
from aiogram.contrib.fsm_storage.redis import RedisStorage2
from aiogram.types.inline_keyboard import InlineKeyboardButton
from aiogram.contrib.middlewares.logging import LoggingMiddleware

from middleware import TimezoneMiddleware

logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

# Get values from the .env file
REDIS_HOST = os.getenv('REDIS_HOST')
REDIS_PORT = int(os.getenv('REDIS_PORT'))
API_TOKEN = os.getenv('API_TOKEN')

bot = Bot(API_TOKEN)
storage = RedisStorage2(REDIS_HOST, REDIS_PORT)
dp = Dispatcher(bot, storage=storage)
dp.middleware.setup(LoggingMiddleware())
dp.middleware.setup(TimezoneMiddleware(bot, storage))

# Initialize callback_data factories
hour_cb = CallbackData("hour", "hour", "year", "month", "day")
date_cb = CallbackData("date", "action", "year", "month", "day")
time_cb = CallbackData("time", "hour", "minute", "year", "month", "day")
minute_cb = CallbackData("minute", "minute", "hour", "year", "month", "day")


async def add_reminder(user_id: int, reminder_time: datetime, voice_file_id: str):
    reminder_data = await dp.storage.get_data(chat=user_id, user=user_id)
    reminders = reminder_data.get("reminders", [])

    reminders.append({
        "reminder_time": reminder_time.strftime("%Y-%m-%d %H:%M:%S"),
        "voice_file_id": voice_file_id
    })

    reminder_data["reminders"] = reminders
    await dp.storage.update_data(chat=user_id, user=user_id, data=reminder_data)


async def remove_reminder(user_id, voice_file_id):
    reminder_data = await dp.storage.get_data(user=user_id, chat=user_id)

    for reminder in reminder_data['reminders']:
        if reminder['voice_file_id'] == voice_file_id:
            reminder_data['reminders'].remove(reminder)
            logger.info(f"Reminder with voice_file_id '{voice_file_id}' was deleted.")
            break
    else:
        logger.info(f"No reminder with voice_file_id '{voice_file_id}' was found.")

    await dp.storage.update_data(chat=user_id, user=user_id, data=reminder_data)


async def schedule_reminder(user_id, user_tz, selected_time, voice_file_id):
    # Calculate the time delta between now and the scheduled reminder
    local_now = datetime.now(user_tz)
    reminder_delta = (selected_time - local_now).total_seconds()

    # Sleep for the calculated time delta
    await asyncio.sleep(reminder_delta)

    # Send the voice message at the scheduled time
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
    await remove_reminder(user_id, voice_file_id)


@dp.message_handler(commands=["reminders"])
async def reminders_handler(message: types.Message):
    user = message.from_user
    user_data = await dp.storage.get_data(chat=message.chat.id, user=user.id)
    user_reminders = user_data.get("reminders", [])

    if not user_reminders:
        await message.reply("No reminders found for you.")

    reminders_string = ""
    for reminder in user_reminders:
        selected_time = datetime.strptime(reminder["reminder_time"], "%Y-%m-%d %H:%M:%S")
        reminders_string += f"{selected_time.strftime('%Y-%m-%d %H:%M')}\n"

    if reminders_string:
        await message.reply(reminders_string)
    else:
        await message.reply("No reminders found for you.")


@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    # Ask for the user's city
    await message.reply("Welcome to VoiceReminderBot!\n"
                        "Please set your timezone using /timezone <City, Country> command.")


@dp.message_handler(commands=["timezone"])
async def timezone_handler(message: types.Message):
    # Use geopy to get the timezone for the city
    tf = TimezoneFinder()
    city = message.text.split(" ", 1)[1]
    geolocator = Nominatim(user_agent="VoiceReminderBot")
    coords = geolocator.geocode(city)
    timezone_name = tf.timezone_at(lng=coords.longitude, lat=coords.latitude)
    if timezone:
        timezone_name = timezone(timezone_name).zone
        timezone_data = {"timezone": timezone_name}
        # Save timezone in Redis cache
        await dp.storage.set_data(chat=message.chat.id, user=message.from_user.id, data=timezone_data)
        await message.reply(f"Your timezone is {timezone_name}. Send me a voice message to set a reminder.")
    else:
        await message.reply("Sorry, I couldn't find that city. Please try again.")


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

    # Get the user's timezone
    user_timezone = await dp.storage.get_data(
        chat=callback_query.message.chat.id, user=callback_query.from_user.id)
    user_tz = timezone(user_timezone["timezone"])

    # Convert the current time to the user's timezone
    local_now = datetime.now(user_tz)

    today = local_now.date()
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

    # Get the user's timezone
    user_timezone = await dp.storage.get_data(
        chat=callback_query.message.chat.id, user=callback_query.from_user.id)
    user_tz = timezone(user_timezone["timezone"])

    selected_time_naive = datetime(year, month, day, hour, minute, second=0, microsecond=0)
    selected_time = user_tz.localize(selected_time_naive)

    user_id = callback_query.from_user.id
    voice_file_id = callback_query.message.reply_to_message.voice.file_id

    # Add the reminder to the reminders storage
    await add_reminder(user_id, selected_time, voice_file_id)

    # Send confirmation message
    await callback_query.answer()
    await callback_query.message.edit_text(
        f"Reminder set for {selected_time.strftime('%Y-%m-%d %H:%M')}.\n"
        f"You'll receive your voice message at the specified time."
    )

    # Schedule sending the voice message
    await schedule_reminder(user_id, user_tz, selected_time, voice_file_id)


@dp.message_handler(CommandHelp())
async def cmd_help(message: types.Message):
    text = (
        "This is a VoiceReminderBot.\n"
        "Send me a voice message and I'll help you set a reminder.\n"
        "Use /reminders command to see the list of your reminders.\n"
    )
    await message.reply(text)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    logger.info('Starting VoiceReminderBot')
    try:
        await dp.start_polling()
    finally:
        logger.info('Stopping VoiceReminderBot')
        await dp.storage.close()
        await dp.storage.wait_closed()
        session = await dp.bot.get_session()
        await session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.error("Exit")
