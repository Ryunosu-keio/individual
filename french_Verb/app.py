from flask import Flask, request, jsonify, render_template, redirect, session
import pandas as pd
import random
import glob
import os

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # セッション使用のために必要

# ヒント辞書（時制 → 説明）
HINTS = {
    "présent": "主語に応じた現在形の語尾",
    "futur simple": "原形（または未来の語幹）＋未来形の語尾",
    "imparfait": "直説法現在の1人称複数形の語幹＋半過去の語尾",
    "passé composé": "avoir または être ＋ 過去分詞",
    "impératif": "命令の主語に対応した語尾",
    "subjonctif_présent": "que＋現在形の語幹＋接続法現在の語尾",
    "subjonctif_passé": "que＋接続法現在の avoir / être ＋ 過去分詞",
    "conditionnel_présent": "未来形の語幹＋半過去の語尾",
    "conditionnel_passé": "条件法現在の avoir / être ＋ 過去分詞"
}

def get_conjugation(df, subject, tense):
    return df.loc[subject, tense]

@app.route('/quiz', methods=['POST'])
def quiz_endpoint():
    mode = request.json.get('mode', 'normal')

    # ✅ 追加：複数時制を受け取る（リスト形式）
    tenses = request.json.get('tenses', [])

    if 'wrong_list' not in session:
        session['wrong_list'] = []

    if mode == 'review':
        if not session['wrong_list']:
            return jsonify({"finished": True})
        entry = session['wrong_list'].pop(0)
        verb, subject, tense = entry['verb'], entry['subject'], entry['tense']
        df = pd.read_csv(f"french_Verb/verbs/csv/{verb}.csv", index_col=0)
        conjugation = get_conjugation(df, subject, tense)
    else:
        # ✅ 空 or "ALL" の場合は全時制を使う
        if not tenses or "ALL" in tenses:
            possible_tenses = list(HINTS.keys())
        else:
            possible_tenses = [t for t in tenses if t in HINTS]

        files = glob.glob("french_Verb/verbs/csv/*.csv")
        selected_file = random.choice(files)
        verb = os.path.basename(selected_file).replace(".csv", "")
        df = pd.read_csv(selected_file, index_col=0)
        tense = random.choice(possible_tenses)

        subjects = df.index.tolist()
        while True:
            subject = random.choice(subjects)
            conjugation = get_conjugation(df, subject, tense)
            if pd.notna(conjugation) and conjugation.strip() != "":
                break

    session['answer'] = conjugation
    session['verb'] = verb
    session['subject'] = subject
    session['tense'] = tense

    return jsonify({
        "verb": verb,
        "subject": subject,
        "tense": tense,
        "conjugation": "",
        "hint": HINTS.get(tense, "ヒントはありません"),
        "streak": session.get('streak', 0)
    })

@app.route('/answer', methods=['POST'])
def check_answer():
    user_input = request.json.get('user_answer', '').strip().lower()
    correct_answer = session.get('answer', '').strip().lower()

    if user_input == correct_answer:
        session['streak'] = session.get('streak', 0) + 1
        correct = True
    else:
        session['streak'] = 0
        correct = False
        wrong_list = session.get('wrong_list', [])
        wrong_list.append({
            "verb": session.get('verb'),
            "subject": session.get('subject'),
            "tense": session.get('tense')
        })
        session['wrong_list'] = wrong_list

    return jsonify({
        "correct": correct,
        "correct_answer": correct_answer,
        "streak": session['streak']
    })

@app.route('/')
def home():
    return render_template('menu.html')

@app.route('/quizpage')
def show_quiz_page():
    return render_template('quiz.html')

@app.route('/wrongcount')
def wrongcount():
    # ✅ 修正：セッションキーのタイプミス修正
    wrong_list = session.get("wrong_list", [])
    return jsonify({"count": len(wrong_list)})

@app.route('/reset')
def reset_quiz():
    session.clear()
    return redirect('/')

if __name__ == '__main__':
    app.run(debug=True)
