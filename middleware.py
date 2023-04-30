from aiogram import types, Bot
from aiogram.contrib.fsm_storage.redis import RedisStorage2
from aiogram.dispatcher.middlewares import LifetimeControllerMiddleware
from aiogram.dispatcher.middlewares import BaseMiddleware


class TimezoneMiddleware(BaseMiddleware):
    def __init__(self, bot: Bot, storage: RedisStorage2):
        super().__init__()
        self.bot: Bot = bot
        self.storage: RedisStorage2 = storage

    async def on_process_message(self, message: types.Message, data: dict):
        if message.content_type == 'text' and ('/timezone' in message.text or '/start' in message.text):
            # Skip processing if the message is a /timezone command
            return True
        user = message.from_user
        user_timezone = await self.storage.get_data(chat=message.chat.id, user=user.id, default=None)
        if not user_timezone:
            # If user timezone is not set in storage, ask the user to set it
            await self.bot.send_message(
                chat_id=message.from_user.id,
                text="Please set your timezone using /timezone <City, Country> command."
            )
            # Stop processing the current update
            return False
        return True
