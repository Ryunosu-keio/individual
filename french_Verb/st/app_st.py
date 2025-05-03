import streamlit as st
import pandas as pd
import glob
import random
import os

# --- 定義 ---
HINTS = {
    "présent": "主語に応じた現在形の語尾",
    "futur simple": "原形＋未来語尾",
    "imparfait": "直現1人称複数語幹＋半過去語尾",
    "passé composé": "avoir/être ＋ 過去分詞",
    "impératif": "命令主語に対応した語尾",
    "subjonctif_présent": "que + 現在語幹 + 接続法語尾",
    "subjonctif_passé": "que + 接続avoir/être + 過去分詞",
    "conditionnel_présent": "未来語幹 + 半過去語尾",
    "conditionnel_passé": "条件avoir/être + 過去分詞"
}

# --- セッション初期化 ---
if "streak" not in st.session_state:
    st.session_state.streak = 0
if "wrong_list" not in st.session_state:
    st.session_state.wrong_list = []
if "mode" not in st.session_state:
    st.session_state.mode = "normal"
if "correct_answer" not in st.session_state:
    st.session_state.correct_answer = None
if "selected_tenses" not in st.session_state:
    st.session_state.selected_tenses = list(HINTS.keys())

st.title("French Verb Conjugation Quiz")

# --- 時制選択 ---
st.write("### 出題する時制を選んでください（複数可）")
selected = st.multiselect("※チェックがない場合は全てから出題されます", options=list(HINTS.keys()), default=st.session_state.selected_tenses)
st.session_state.selected_tenses = selected if selected else list(HINTS.keys())

# --- 出題ボタン ---
col1, col2 = st.columns(2)
with col1:
    if st.button("▶ 通常モードで出題"):
        st.session_state.mode = "normal"
        st.session_state.current = "quiz"
with col2:
    if st.button(f"🔁 間違えた問題を復習 ({len(st.session_state.wrong_list)})"):
        st.session_state.mode = "review"
        st.session_state.current = "quiz"

# --- 出題処理 ---
if st.session_state.get("current") == "quiz":
    if st.session_state.mode == "review":
        if not st.session_state.wrong_list:
            st.warning("復習する問題がありません。")
            st.session_state.current = None
        else:
            entry = st.session_state.wrong_list.pop(0)
            verb, subject, tense = entry['verb'], entry['subject'], entry['tense']
            df = pd.read_csv(f"french_Verb/verbs/csv/{verb}.csv", index_col=0)
            conjugation = df.loc[subject, tense]
    else:
        files = glob.glob("french_Verb/verbs/csv/*.csv")
        selected_file = random.choice(files)
        verb = os.path.basename(selected_file).replace(".csv", "")
        df = pd.read_csv(selected_file, index_col=0)
        tense = random.choice(st.session_state.selected_tenses)
        subject = random.choice(df.index)
        conjugation = df.loc[subject, tense]

    st.session_state.correct_answer = conjugation
    st.session_state.verb = verb
    st.session_state.subject = subject
    st.session_state.tense = tense

    st.subheader(f"動詞: {verb}")
    st.write(f"主語: {subject}")
    st.write(f"時制: {tense}")

    answer = st.text_input("📥 活用を入力してください", key="input_answer")

    if st.button("✅ 答え合わせ"):
        if answer.strip().lower() == st.session_state.correct_answer.strip().lower():
            st.success("正解！")
            st.session_state.streak += 1
        else:
            st.error(f"不正解。正解は「{st.session_state.correct_answer}」")
            st.session_state.streak = 0
            st.session_state.wrong_list.append({
                "verb": st.session_state.verb,
                "subject": st.session_state.subject,
                "tense": st.session_state.tense
            })

    st.info(f"現在の連続正解数: {st.session_state.streak}")

    with st.expander("💡 ヒントを見る"):
        st.write(HINTS.get(st.session_state.tense, "ヒントはありません"))

    if st.button("➡ 次の問題へ"):
        st.session_state.current = "quiz"
        st.rerun()

