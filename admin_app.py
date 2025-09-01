import os
import streamlit as st
import database
import asyncio
from dotenv import load_dotenv

load_dotenv()
GROUPS = [int(x) for x in os.getenv("GROUPS", "").split(",") if x]
SUPER_ADMINS = [int(x) for x in os.getenv("SUPER_ADMINS", "").split(",") if x]


def run_async(coro):
    return asyncio.run(coro)


@st.cache_resource
def init_db():
    run_async(database.init())


init_db()

st.sidebar.title("MSR Admin")

groups = run_async(database.get_groups_info())
group_dict = {title: cid for cid, title in groups}
if not group_dict:
    group_dict = {str(cid): cid for cid in GROUPS}

selected_title = st.sidebar.selectbox("Группа", list(group_dict.keys()))
chat_id = group_dict[selected_title]

tab1, tab2, tab3, tab4, tab5 = st.tabs(["Вопросы", "Добавить", "Попытки", "Админы", "Статистика"])

with tab1:
    st.header("Вопросы")
    questions = run_async(database.get_questions(chat_id))
    if questions:
        for qid, q, a in questions:
            col1, col2 = st.columns([4, 1])
            col1.write(f"{q} → {a}")
            if col2.button("🗑", key=f"del_{qid}"):
                run_async(database.delete_question(qid))
                st.experimental_rerun()
    else:
        st.info("Нет вопросов")

with tab2:
    st.header("Добавить вопрос")
    question = st.text_input("Вопрос")
    answer = st.text_input("Ответ")
    if st.button("Добавить", key="add_question"):
        run_async(database.add_question(chat_id, question, answer))
        st.success("Добавлено!")

with tab3:
    st.header("Попытки")
    current = run_async(database.get_max_attempts(chat_id))
    new_val = st.number_input("Количество попыток", min_value=1, max_value=10, value=current)
    if st.button("Сохранить", key="save_attempts"):
        run_async(database.set_max_attempts(chat_id, new_val))
        st.success("Сохранено!")

with tab4:
    st.header("Админы группы")
    admins = run_async(database.get_group_admins(chat_id))
    if admins:
        st.write("Текущие админы:")
        for uid in admins:
            st.write(uid)
    else:
        st.write("Нет админов")

    new_admin = st.number_input("ID нового админа", min_value=1, step=1)
    if st.button("Добавить", key="add_admin"):
        run_async(database.add_admin(chat_id, new_admin))
        st.success("Добавлен!")

with tab5:
    st.header("Статистика")
    total, ok, bad, rows = run_async(database.get_stats(chat_id))
    st.metric("Всего", total)
    st.metric("Правильно", ok)
    st.metric("Неправильно", bad)
    if rows:
        st.subheader("Последние:")
        for un, q, ans, ok_flag in rows:
            mark = "✅" if ok_flag else "❌"
            st.text(f"{mark} {un}: {q} → {ans}")

st.sidebar.markdown("---")
st.sidebar.caption("Создано на Streamlit")