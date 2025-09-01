import logging
import os
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    ChatMemberUpdated,
    Message,
    ChatPermissions,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.filters import Command
from aiogram.enums.chat_type import ChatType
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from dotenv import load_dotenv
import database

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPER_ADMINS = [int(x) for x in os.getenv("SUPER_ADMINS", "").split(",") if x]
GROUPS = [int(x) for x in os.getenv("GROUPS", "").split(",") if x]
DELETE_AFTER = 30

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=storage)

EXPECT_QA_KEY = "expect_qa_chat"
bot_username: str | None = None


# ---------- helpers ----------
async def restrict(chat_id: int, user_id: int, restrict: bool):
    perms = ChatPermissions(
        can_send_messages=not restrict,
        can_send_audios=not restrict,
        can_send_documents=not restrict,
        can_send_photos=not restrict,
        can_send_videos=not restrict,
        can_send_video_notes=not restrict,
        can_send_voice_notes=not restrict,
        can_send_polls=not restrict,
        can_send_other_messages=not restrict,
        can_add_web_page_previews=not restrict,
        can_invite_users=not restrict,
    )
    await bot.restrict_chat_member(chat_id, user_id, permissions=perms)


async def delete_msg_after(chat_id: int, message_id: int, delay: int):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


# ---------- Вступление ----------
@dp.chat_member()
async def on_member(event: ChatMemberUpdated):
    if event.new_chat_member.status == "member" and event.chat.id in GROUPS:
        user = event.new_chat_member.user
        chat_id = event.chat.id
        title = event.chat.title or str(chat_id)

        await database.ensure_group(chat_id, title)
        questions = await database.get_questions(chat_id)

        if not questions:
            msg = await bot.send_message(
                chat_id,
                f"{user.full_name} вступил в группу, но в ней нет вопросов для проверки. "
                f"Добавьте вопросы через админку.",
            )
            asyncio.create_task(delete_msg_after(chat_id, msg.message_id, DELETE_AFTER))
            return

        await database.upsert_user_state(
            user.id, chat_id, status="not_verified", attempts=0, current_q_index=0
        )
        await restrict(chat_id, user.id, True)
        msg = await bot.send_message(
            chat_id,
            f"Добро пожаловать, {user.full_name}!\n"
            f"Пройдите проверку: нажмите /start у @{bot_username}?start={chat_id}",
        )
        asyncio.create_task(delete_msg_after(chat_id, msg.message_id, DELETE_AFTER))


# ---------- /start ----------
@dp.message(Command("start"), F.chat.type == ChatType.PRIVATE)
async def cmd_start_private(message: Message, command: Command):
    user = message.from_user
    try:
        chat_id = int(command.args)
    except (TypeError, ValueError):
        groups = await database.get_groups_info()
        kb_rows = [
            [InlineKeyboardButton(text=title, url=f"https://t.me/{bot_username}?start={cid}")]
            for cid, title in groups
        ]
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        return await message.answer(
            "Выберите группу, в которой хотите пройти проверку:",
            reply_markup=kb,
        )

    if chat_id not in GROUPS:
        return await message.answer("Неверная ссылка.")

    questions = await database.get_questions(chat_id)
    if not questions:
        return await message.answer("В этой группе пока нет вопросов для проверки.")

    await database.upsert_user_state(
        user.id, chat_id, status="not_verified", attempts=0, current_q_index=0
    )
    q, a = questions[0][1], questions[0][2]
    await message.answer(f"Ответьте на вопрос:\n<b>{q}</b>")


# ---------- Ответ ----------
@dp.message(F.chat.type == ChatType.PRIVATE)
async def answer_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    if data.get(EXPECT_QA_KEY):
        return

    user = message.from_user
    for gid in GROUPS:
        st_row = await database.get_user_state(user.id, gid)
        if st_row and st_row[0] == "not_verified":
            chat_id = gid
            break
    else:
        return

    questions = await database.get_questions(chat_id)
    idx = (await database.get_user_state(user.id, chat_id))[2]
    q, a = questions[idx][1], questions[idx][2]
    given = message.text.strip().lower()
    ok = given == a.lower()
    max_attempts = await database.get_max_attempts(chat_id)

    await database.log_answer(
        chat_id, user.id, user.username or "", q, message.text, ok
    )

    if ok:
        idx += 1
        if idx < len(questions):
            next_q, next_a = questions[idx][1], questions[idx][2]
            await database.update_user_state(
                user.id, chat_id, current_q_index=idx, attempts=0
            )
            await message.answer(f"✅ Верно! Следующий вопрос:\n<b>{next_q}</b>")
        else:
            await database.update_user_state(user.id, chat_id, status="verified")
            await restrict(chat_id, user.id, False)
            await message.answer(
                "Отлично! Вы ответили на все вопросы, добро пожаловать в группу."
            )
            msg = await bot.send_message(chat_id, f"{user.full_name} прошёл проверку!")
            asyncio.create_task(delete_msg_after(chat_id, msg.message_id, DELETE_AFTER))
    else:
        attempts = (await database.get_user_state(user.id, chat_id))[1] + 1
        await database.update_user_state(user.id, chat_id, attempts=attempts)
        if attempts >= max_attempts:
            await database.update_user_state(user.id, chat_id, status="banned")
            await message.answer("Превышено число попыток, вы заблокированы.")
            await bot.ban_chat_member(chat_id, user.id)
            msg = await bot.send_message(chat_id, f"{user.full_name} заблокирован за попытки.")
            asyncio.create_task(delete_msg_after(chat_id, msg.message_id, DELETE_AFTER))
        else:
            await message.answer(
                f"Неверно, попробуйте ещё раз (осталось {max_attempts - attempts})."
            )


# ---------- Назначение админов ----------
@dp.message(Command("addadmin"), F.chat.type != ChatType.PRIVATE)
async def cmd_addadmin(message: Message):
    chat_id = message.chat.id
    caller = message.from_user.id
    if not await database.is_admin(chat_id, caller):
        return await message.answer("У вас нет прав.")
    try:
        username = message.text.split()[1].lstrip("@")
        member = await bot.get_chat_member(chat_id, username)
        await database.add_admin(chat_id, member.user.id)
        await message.answer(f"{member.user.full_name} назначен админом.")
    except Exception as e:
        await message.answer("Не удалось найти пользователя: " + str(e))


# ---------- Главное меню ----------
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    user_id = message.from_user.id
    if message.chat.type == ChatType.PRIVATE:
        groups = await database.get_groups_info()
        kb_rows = [
            [InlineKeyboardButton(text=title, callback_data=f"pick_{cid}")]
            for cid, title in groups
        ]
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        return await message.answer("Выберите группу:", reply_markup=kb)

    chat_id = message.chat.id
    if not await database.is_admin(chat_id, user_id):
        return await message.answer("Нет доступа.")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data=f"stats_{chat_id}")],
            [InlineKeyboardButton(text="➕ Добавить вопрос", callback_data=f"addq_{chat_id}")],
            [InlineKeyboardButton(text="🔍 Вопросы", callback_data=f"listq_{chat_id}")],
            [InlineKeyboardButton(text="⚙️ Попытки", callback_data=f"att_{chat_id}")],
        ]
    )
    await message.answer("Панель управления:", reply_markup=kb)


# ---------- Коллбэки ----------
@dp.callback_query(F.data.startswith("pick_"))
async def pick_group(callback: CallbackQuery):
    chat_id = int(callback.data.split("_", 1)[1])
    user_id = callback.from_user.id
    if not await database.is_admin(chat_id, user_id):
        return await callback.answer("Нет доступа")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data=f"stats_{chat_id}")],
            [InlineKeyboardButton(text="➕ Добавить вопрос", callback_data=f"addq_{chat_id}")],
            [InlineKeyboardButton(text="🔍 Вопросы", callback_data=f"listq_{chat_id}")],
            [InlineKeyboardButton(text="⚙️ Попытки", callback_data=f"att_{chat_id}")],
        ]
    )
    await callback.message.edit_text(f"Управление группой {chat_id}:", reply_markup=kb)


@dp.callback_query(F.data.startswith("stats_"))
async def stats_cb(callback: CallbackQuery):
    chat_id = int(callback.data.split("_", 1)[1])
    user_id = callback.from_user.id
    if not await database.is_admin(chat_id, user_id):
        return await callback.answer("Нет доступа")
    total, ok, bad, rows = await database.get_stats(chat_id)
    text = f"📊 {chat_id}\n✅ {ok} ❌ {bad} 📓 {total}\n\nПоследние:\n"
    for r in rows:
        mark = "✅" if r[3] else "❌"
        text += f"{mark} {r[0]}: {r[1]} → {r[2]}\n"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data=f"pick_{chat_id}")]]
    )
    await callback.message.edit_text(text, reply_markup=kb)


@dp.callback_query(F.data.startswith("addq_"))
async def addq_cb(callback: CallbackQuery, state: FSMContext):
    chat_id = int(callback.data.split("_", 1)[1])
    user_id = callback.from_user.id
    if not await database.is_admin(chat_id, user_id):
        return await callback.answer("Нет доступа")
    await callback.bot.send_message(
        user_id,
        f"Отправьте новый вопрос и ответ для группы {chat_id}:\n"
        "<code>Вопрос|ответ</code>",
        parse_mode="HTML",
    )
    await state.set_data({EXPECT_QA_KEY: chat_id})
    await callback.answer("Ожидаю вопрос в личке.")


@dp.message(F.text.contains("|"), F.from_user.id.in_(SUPER_ADMINS))
async def add_question_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    chat_id = data.get(EXPECT_QA_KEY)
    if not chat_id:
        return
    try:
        q, a = message.text.split("|", 1)
        await database.add_question(chat_id, q.strip(), a.strip())
        await message.answer("Вопрос добавлен.")
    except ValueError:
        await message.answer("Формат: Вопрос|ответ")
    finally:
        await state.clear()


@dp.callback_query(F.data.startswith("listq_"))
async def listq_cb(callback: CallbackQuery):
    chat_id = int(callback.data.split("_", 1)[1])
    user_id = callback.from_user.id
    if not await database.is_admin(chat_id, user_id):
        return await callback.answer("Нет доступа")
    questions = await database.get_questions(chat_id)
    kb_rows = []
    for qid, q, a in questions:
        kb_rows.append(
            [InlineKeyboardButton(text=q[:30], callback_data=f"delq_{chat_id}_{qid}")]
        )
    kb_rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"pick_{chat_id}")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await callback.message.edit_text("Нажмите чтобы удалить:", reply_markup=kb)


@dp.callback_query(F.data.startswith("delq_"))
async def delq_cb(callback: CallbackQuery):
    _, chat_id_str, qid_str = callback.data.split("_")
    chat_id, qid = int(chat_id_str), int(qid_str)
    user_id = callback.from_user.id
    if not await database.is_admin(chat_id, user_id):
        return await callback.answer("Нет доступа")
    await database.delete_question(qid)
    await callback.answer("Удалено")
    await listq_cb(callback)


@dp.callback_query(F.data.startswith("att_"))
async def att_cb(callback: CallbackQuery):
    chat_id = int(callback.data.split("_", 1)[1])
    user_id = callback.from_user.id
    if not await database.is_admin(chat_id, user_id):
        return await callback.answer("Нет доступа")
    current = await database.get_max_attempts(chat_id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=str(i), callback_data=f"setatt_{chat_id}_{i}")
                for i in range(1, 6)
            ]
        ]
    )
    kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"pick_{chat_id}")])
    await callback.message.edit_text(f"Текущее: {current}. Выберите:", reply_markup=kb)


@dp.callback_query(F.data.startswith("setatt_"))
async def setatt_cb(callback: CallbackQuery):
    _, chat_id_str, n_str = callback.data.split("_")
    chat_id, n = int(chat_id_str), int(n_str)
    user_id = callback.from_user.id
    if not await database.is_admin(chat_id, user_id):
        return await callback.answer("Нет доступа")
    await database.set_max_attempts(chat_id, n)
    await callback.answer("Сохранено")
    await callback.message.edit_text(
        f"Попытки для {chat_id} = {n}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data=f"pick_{chat_id}")]]
        ),
    )


# ---------- Запуск ----------
async def on_startup():
    global bot_username
    me = await bot.get_me()
    bot_username = me.username
    await database.init()


async def main():
    await on_startup()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())