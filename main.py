from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneNumberInvalidError
import asyncio
from config import *
import re
import os

bot = Bot(token=bot_token)
dp = Dispatcher()

users = {}


@dp.message(Command('start'))
async def start(message: Message):
    await message.answer("Привет! Введите свой номер телефона для авторизации в Telegram "
                         "(в международном формате, например +11234567890).")


@dp.message(lambda message: message.text[0] == '+')
async def request_phone(message: Message):
    user_id = message.from_user.id
    phone = message.text.strip()

    session_name = f'session_{user_id}_{phone}'

    client = TelegramClient(session_name, api_id, api_hash)

    users[user_id] = {
        'phone': phone,
        'client': client,
        'is_sending': False
    }

    await client.connect()

    try:
        if not await client.is_user_authorized():
            await client.send_code_request(phone)
            await message.answer("Код отправлен! Введите код, который пришел на ваш Телеграм.")
        else:
            await show_group_list(user_id, message)
    except PhoneNumberInvalidError:
        await message.answer("Неверный формат номера телефона. Пожалуйста, введите номер в международном формате с кодом страны.")
    except Exception as e:
        await message.answer(f"Ошибка при отправке кода: {e}")


@dp.message(lambda message: message.text.isdigit() and len(message.text) == 5)
async def request_code(message: Message):
    user_id = message.from_user.id
    code = message.text.strip()

    print(f"User {user_id} is trying to authenticate with code: {code}")

    client = users[user_id]['client']

    try:
        await client.sign_in(users[user_id]['phone'], code)
        await message.answer("Вы успешно авторизовались!")
        await show_group_list(user_id, message)

    except PhoneCodeInvalidError:
        await message.answer("Неверный код. Попробуйте еще раз.")
        print("Invalid code entered.")
    except SessionPasswordNeededError:
        await message.answer("Введите пароль двухфакторной аутентификации.")
        users[user_id]['awaiting_password'] = True
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
        print(f"Error during sign in: {e}")


@dp.message(lambda message: users.get(message.from_user.id, {}).get('awaiting_password'))
async def request_password(message: Message):
    user_id = message.from_user.id
    password = message.text.strip()

    client = users[user_id]['client']

    try:
        await client.sign_in(password=password)
        await message.answer("Вы успешно авторизовались!")
        await show_group_list(user_id, message)
        users[user_id]['awaiting_password'] = False

    except Exception as e:
        await message.answer(f"Ошибка при вводе пароля: {e}")
        print(f"Error during password input: {e}")


async def show_group_list(user_id, message):
    client = users[user_id]['client']
    dialogs = await client.get_dialogs()
    users[user_id]['dialogs'] = dialogs

    group_list = [f"{i}. {dialog.title}" for i, dialog in enumerate(dialogs) if dialog.is_group]
    await message.answer("Вы состоите в следующих группах:\n" + "\n".join(group_list) +
                         "\n\nВведите номера групп через запятую, чтобы выбрать, куда отправить сообщение (минимум 2 группы).")


@dp.message(lambda message: re.match(r'^\d+(,\d+)*$', message.text) and ',' in message.text)
async def select_groups(message: Message):
    user_id = message.from_user.id
    selected_groups = message.text.strip().split(',')

    try:
        selected_indexes = [int(index.strip()) for index in selected_groups]
        dialogs = users[user_id]['dialogs']

        users[user_id]['selected_groups'] = [dialogs[index] for index in selected_indexes]
        await message.answer(
            f"Вы выбрали группы: {', '.join(dialog.title for dialog in users[user_id]['selected_groups'])}. "
            "Теперь введите сообщение, которое вы хотите отправить.")

    except (IndexError, ValueError):
        await message.answer("Ошибка: Убедитесь, что вы ввели корректные номера групп.")


@dp.message(lambda message: ('selected_groups' in users.get(message.from_user.id, {}))
                            and message.text.isdigit() == False
                            and message.text != '/stop'
                            and message.text != '/logout')
async def send_message_to_groups(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()

    users[user_id]['message_text'] = text
    await message.answer("Введите интервал (в секундах) между отправками сообщений (1-9999):")


@dp.message(lambda message: message.text.isdigit())
async def set_interval(message: Message):
    user_id = message.from_user.id
    interval = int(message.text.strip())

    if interval <= 0:
        await message.answer("Интервал должен быть положительным числом. Попробуйте еще раз.")
        return

    users[user_id]['interval'] = interval
    users[user_id]['is_sending'] = True

    await message.answer(f"Сообщение будет отправлено каждые {interval} секунд.")
    asyncio.create_task(send_periodic_messages(user_id))


async def send_periodic_messages(user_id):
    client = users[user_id]['client']
    message_text = users[user_id]['message_text']
    selected_groups = users[user_id]['selected_groups']
    interval = users[user_id]['interval']

    while users[user_id]['is_sending']:
        for group in selected_groups:
            try:
                await client.send_message(group.id, message_text)
            except Exception as e:
                print(f"Ошибка при отправке сообщения в группу {group.title}: {e}")

        await asyncio.sleep(interval)


@dp.message(Command('stop'))
async def stop_sending(message: Message):
    user_id = message.from_user.id
    if user_id in users and users[user_id].get('is_sending'):
        users[user_id]['is_sending'] = False
        await message.answer("Отправка сообщений остановлена. Вот список ваших групп:")
        await show_group_list(user_id, message)  # Показываем список групп после остановки отправки
    else:
        await message.answer("Вы не находитесь в процессе отправки сообщений.")


@dp.message(Command('logout'))
async def logout(message: Message):
    user_id = message.from_user.id
    if user_id in users:
        client = users[user_id]['client']
        session_path = client.session.filename

        await client.log_out()
        await client.disconnect()

        if os.path.exists(session_path):
            os.remove(session_path)

        del users[user_id]
        await message.answer("Вы вышли из аккаунта Telegram и сессия была удалена.")
    else:
        await message.answer("Вы не авторизованы.")


async def main():
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
