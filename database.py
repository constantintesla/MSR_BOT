import aiosqlite
import os

DB = "database.db"


# ---------- инициализация ----------
async def init():
    async with aiosqlite.connect(DB) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS groups(
                chat_id INTEGER PRIMARY KEY,
                title   TEXT,
                max_attempts INTEGER NOT NULL DEFAULT 3
            );
            CREATE TABLE IF NOT EXISTS questions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS group_admins(
                chat_id INTEGER,
                user_id INTEGER,
                PRIMARY KEY(chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS answers_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                username TEXT,
                question TEXT,
                given_answer TEXT,
                is_correct INTEGER,
                ts DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS user_group_state(
                user_id INTEGER,
                chat_id INTEGER,
                status TEXT CHECK(status IN ('not_verified','verified','banned')),
                attempts INTEGER DEFAULT 0,
                current_q_index INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
            );
        """)
        await db.commit()


# ---------- группы ----------
async def ensure_group(chat_id: int, title: str | None = None):
    title = title or str(chat_id)
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR IGNORE INTO groups(chat_id, title, max_attempts) VALUES(?,?,?)",
            (chat_id, title, int(os.getenv("DEFAULT_ATTEMPTS", 3))),
        )
        await db.commit()


async def set_group_title(chat_id: int, title: str):
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE groups SET title=? WHERE chat_id=?", (title, chat_id))
        await db.commit()


async def get_groups_info():
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT chat_id, title FROM groups")
        return await cur.fetchall()


async def set_max_attempts(chat_id: int, n: int):
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE groups SET max_attempts=? WHERE chat_id=?", (n, chat_id))
        await db.commit()


async def get_max_attempts(chat_id: int):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT max_attempts FROM groups WHERE chat_id=?", (chat_id,))
        row = await cur.fetchone()
    return row[0] if row else int(os.getenv("DEFAULT_ATTEMPTS", 3))


# ---------- вопросы ----------
async def add_question(chat_id: int, q: str, a: str):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO questions(chat_id, question, answer) VALUES(?,?,?)",
            (chat_id, q, a),
        )
        await db.commit()


async def delete_question(qid: int):
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM questions WHERE id=?", (qid,))
        await db.commit()


async def get_questions(chat_id: int):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT id, question, answer FROM questions WHERE chat_id=? ORDER BY id", (chat_id,)
        )
        return await cur.fetchall()


# ---------- админы ----------
async def add_admin(chat_id: int, user_id: int):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR IGNORE INTO group_admins(chat_id, user_id) VALUES(?,?)",
            (chat_id, user_id),
        )
        await db.commit()


async def is_admin(chat_id: int, user_id: int):
    if user_id in [int(x) for x in os.getenv("SUPER_ADMINS", "").split(",") if x]:
        return True
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT 1 FROM group_admins WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
        return bool(await cur.fetchone())


async def get_group_admins(chat_id: int):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT user_id FROM group_admins WHERE chat_id=?", (chat_id,))
        return [row[0] for row in await cur.fetchall()]


# ---------- логи ----------
async def log_answer(chat_id, user_id, username, question, given, ok):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """
            INSERT INTO answers_log(chat_id,user_id,username,question,given_answer,is_correct)
            VALUES(?,?,?,?,?,?)
            """,
            (chat_id, user_id, username, question, given, int(ok)),
        )
        await db.commit()


# ---------- статистика ----------
async def get_stats(chat_id: int):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT COUNT(*), SUM(is_correct), SUM(NOT is_correct) FROM answers_log WHERE chat_id=?",
            (chat_id,),
        )
        row = await cur.fetchone()
        total, ok, bad = row[0] or 0, row[1] or 0, row[2] or 0

        last = await db.execute(
            "SELECT username, question, given_answer, is_correct "
            "FROM answers_log WHERE chat_id=? ORDER BY id DESC LIMIT 10",
            (chat_id,),
        )
        last_rows = await last.fetchall()
    return total, ok, bad, last_rows


# ---------- пользователи ----------
async def upsert_user_state(user_id: int, chat_id: int, status="not_verified", attempts=0, current_q_index=0):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO user_group_state(user_id, chat_id, status, attempts, current_q_index)
            VALUES(?,?,?,?,?)
            """,
            (user_id, chat_id, status, attempts, current_q_index),
        )
        await db.commit()


async def get_user_state(user_id: int, chat_id: int):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT status, attempts, current_q_index FROM user_group_state WHERE user_id=? AND chat_id=?",
            (user_id, chat_id),
        )
        return await cur.fetchone()


async def update_user_state(user_id: int, chat_id: int, **kwargs):
    set_part = ", ".join([f"{k}=?" for k in kwargs])
    values = tuple(kwargs.values()) + (user_id, chat_id)
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            f"UPDATE user_group_state SET {set_part} WHERE user_id=? AND chat_id=?", values
        )
        await db.commit()