# -*- coding: utf-8 -*-
"""
French‑Verb Quiz backend  ― フルリファクタ版
* /quiz      : 通常・主語順・復習モードの問題を返す
* /skipverb  : by_subject の途中で押すと “次の動詞＋最初の主語” を返す
  → 1 リクエストで画面を更新できる


"""

from flask import Flask, request, jsonify, render_template, redirect, session
import pandas as pd, random, glob, os

app = Flask(__name__)
import logging
logging.basicConfig(level=logging.DEBUG)

app.secret_key = "your_secret_key_here"

# ───────── ヒント辞書 ─────────
HINTS = {
    "présent":                "主語に応じた現在形語尾",
    "futur simple":           "原形＋未来形語尾（全人称共通語幹）",
    "imparfait":              "現在 1複 語幹＋半過去語尾",
    "passé composé":          "avoir / être ＋ 過去分詞",
    "impératif":              "tu / nous / vous の命令語尾",
    "subjonctif_présent":     "que＋現在 3複 語幹＋接続法語尾(nous vousは半過去語尾)",
    "subjonctif_passé":       "que＋接続法現在 avoir / être ＋ 過去分詞",
    "conditionnel_présent":   "未来語幹＋半過去語尾",
    "conditionnel_passé":     "条件法現在 avoir / être ＋ 過去分詞",
}

ALL_SUBJECTS        = ["je","tu","il","elle","nous","vous","ils","elles"]
VALID_FOR_IMPERATIF = ["tu","nous","vous"]

# ───────── ユーティリティ ─────────
def get_conjugation(df, subj, tense):
    return df.loc[subj, tense]

def valid_subjects_for(tense, df):
    if tense == "impératif":
        subs = [s for s in VALID_FOR_IMPERATIF if s in df.index]
    else:
        subs = [s for s in ALL_SUBJECTS if s in df.index]

    # ★ここで一覧を確認
    app.logger.debug("[SUBJECTS] tense=%s → %s", tense, subs)
    return subs


def _clear_current():
    """現在の動詞セット関連キーをセッションから削除"""
    for k in ("current_verb", "current_tense", "subject_index"):
        session.pop(k, None)

# ───────── 問題生成（共通関数）─────────
def make_quiz(mode="normal", tenses=None):
    """
    各エンドポイントが呼び出す共通ロジック。
    戻り値は dict（呼び出し側で jsonify する）。
    """
    tenses = tenses or []
    possible_tenses = list(HINTS) if not tenses or "ALL" in tenses \
                      else [t for t in tenses if t in HINTS]

    # ---------- review ----------
    if mode == "review":
        if not session.get("wrong_list"):
            return {"finished": True}
        entry = session["wrong_list"][0]
        verb, subj, tense = entry["verb"], entry["subject"], entry["tense"]
        df   = pd.read_csv(f"french_Verb/verbs/csv/{verb}.csv", index_col=0)

        conjugation = get_conjugation(df, subj, tense)
    # ---------- by_subject ----------
    elif mode == "by_subject":
        idx   = session.get("subject_index", 0)
        verb  = session.get("current_verb")
        tense = session.get("current_tense")

        app.logger.debug("[BY_SUBJECT] idx=%s  verb=%s  tense=%s", idx, verb, tense)
        
        if not verb or not tense:               # 新しい動詞を選択
            file  = random.choice(glob.glob("french_Verb/verbs/csv/*.csv"))
            verb  = os.path.basename(file).replace(".csv", "")
            tense = random.choice(possible_tenses)
            session.update({"current_verb": verb, "current_tense": tense,
                            "subject_index": 0})
            idx = 0

        df = pd.read_csv(f"french_Verb/verbs/csv/{verb}.csv", index_col=0)
        subjects = valid_subjects_for(tense, df)
        if not subjects:                        # 主語が取れなければスキップ
            _clear_current()
            return make_quiz(mode, tenses)

        if idx >= len(subjects):                # 範囲外ならリセット
            idx = 0
            session["subject_index"] = 0

        subj = subjects[idx]
        conjugation = get_conjugation(df, subj, tense)

        # 次回の主語インデックスを進める
        idx += 1
        if idx >= len(subjects):
            _clear_current()
        else:
            session["subject_index"] = idx

    # ---------- normal ----------
    else:
        while True:
            file  = random.choice(glob.glob("french_Verb/verbs/csv/*.csv"))
            verb  = os.path.basename(file).replace(".csv", "")
            df    = pd.read_csv(file, index_col=0)
            tense = random.choice(possible_tenses)
            subjects = valid_subjects_for(tense, df)
            if subjects:
                break
        subj = random.choice(subjects)
        conjugation = get_conjugation(df, subj, tense)

    # 共通：セッション保存
    session.update({"answer": conjugation, "verb": verb,
                    "subject": subj, "tense": tense})

    return {
        "verb": verb, "subject": subj, "tense": tense,
        "conjugation": "", "hint": HINTS.get(tense, ""),
        "streak": session.get("streak", 0)
    }

# ───────── ルーティング ─────────
@app.route("/quiz", methods=["POST"])
def quiz_api():
    data   = request.json or {}
    mode   = data.get("mode",   "normal")
    tenses = data.get("tenses", [])
    if "wrong_list" not in session:
        session["wrong_list"] = []
    return jsonify(make_quiz(mode, tenses))

@app.route("/skipverb", methods=["POST"])
def skipverb_api():
    """by_subject で動詞を丸ごとスキップ → 新しい動詞の1問目を返す"""
    _clear_current()
    # data   = request.json or {}
    # tenses = data.get("tenses", [])
    # mode を明示しなくても by_subject 固定で OK
    # return jsonify(make_quiz(mode="by_subject", tenses=tenses))
    return jsonify({"skipped":True})

@app.route("/answer", methods=["POST"])
def answer_api():
    user = (request.json.get("user_answer","") or "").strip().lower()
    corr = (session.get("answer","")           or "").strip().lower()
    correct = user == corr
    session["streak"] = session.get("streak",0)+1 if correct else 0
    if not correct:
        wl = session.get("wrong_list",[])
        wl.append({k:session.get(k) for k in ("verb","subject","tense")})
        session["wrong_list"] = wl
         # -------- 誤答登録／正解時 pop --------
    wl = session.get("wrong_list", [])
    current = {k: session.get(k) for k in ("verb","subject","tense")}

    if correct:
        # review モード中で　“いま解いた問題” が先頭なら削除
        if wl and wl[0] == current:
            wl.pop(0)
    else:
        # まだ登録されていないときだけ追加
        if current not in wl:
            wl.append(current)

    session["wrong_list"] = wl
    return jsonify({"correct":correct,"correct_answer":corr,
                    "streak":session["streak"]})

# ───────── そのほか ─────────
@app.route("/")
def home():           return render_template("menu.html")

@app.route("/quizpage")
def quizpage():       return render_template("quiz.html")

@app.route("/wrongcount")
def wrongcount():     return jsonify({"count":len(session.get("wrong_list",[]))})

@app.route("/reset")
def reset():          session.clear(); return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)
# -*- coding: utf-8 -*-
"""
French‑Verb Quiz backend  ― フルリファクタ版
* /quiz      : 通常・主語順・復習モードの問題を返す
* /skipverb  : by_subject の途中で押すと “次の動詞＋最初の主語” を返す
  → 1 リクエストで画面を更新できる
"""

from flask import Flask, request, jsonify, render_template, redirect, session
import pandas as pd, random, glob, os

app = Flask(__name__)
app.secret_key = "your_secret_key_here"

# ───────── ヒント辞書 ─────────
HINTS = {
    "présent":                "主語に応じた現在形語尾",
    "futur simple":           "原形＋未来形語尾（全人称共通語幹）",
    "imparfait":              "現在 1複 語幹＋半過去語尾",
    "passé composé":          "avoir / être ＋ 過去分詞",
    "impératif":              "tu / nous / vous の命令語尾",
    "subjonctif_présent":     "que＋現在語幹＋接続法語尾",
    "subjonctif_passé":       "que＋接続法現在 avoir / être ＋ 過去分詞",
    "conditionnel_présent":   "未来語幹＋半過去語尾",
    "conditionnel_passé":     "条件法現在 avoir / être ＋ 過去分詞",
}

ALL_SUBJECTS        = ["je","tu","il","elle","nous","vous","ils","elles"]
VALID_FOR_IMPERATIF = ["tu","nous","vous"]

# ───────── ユーティリティ ─────────
def get_conjugation(df, subj, tense):
    return df.loc[subj, tense]

def valid_subjects_for(tense, df):
    if tense == "impératif":
        return [s for s in VALID_FOR_IMPERATIF if s in df.index]
    return [s for s in ALL_SUBJECTS if s in df.index]

def _clear_current():
    """現在の動詞セット関連キーをセッションから削除"""
    for k in ("current_verb", "current_tense", "subject_index"):
        session.pop(k, None)

# ───────── 問題生成（共通関数）─────────
def make_quiz(mode="normal", tenses=None):
    """
    各エンドポイントが呼び出す共通ロジック。
    戻り値は dict（呼び出し側で jsonify する）。
    """
    tenses = tenses or []
    possible_tenses = list(HINTS) if not tenses or "ALL" in tenses \
                      else [t for t in tenses if t in HINTS]

    # ---------- review ----------
    if mode == "review":# ---------- review ----------          ← ★ 修正文

        wrong_list = session.get("wrong_list", [])
        if not wrong_list:                   # もう無ければ終了
            return {"finished": True}

        entry = wrong_list.pop(0)            # 先頭を取り出す
        session["wrong_list"] = wrong_list   # ★ ここを必ず再代入する
        # あるいは  session.modified = True  でも可

        verb, subj, tense = entry["verb"], entry["subject"], entry["tense"]
        df   = pd.read_csv(f"french_Verb/verbs/csv/{verb}.csv", index_col=0)

    # ---------- by_subject ----------
    elif mode == "by_subject":
        idx   = session.get("subject_index", 0)
        verb  = session.get("current_verb")
        tense = session.get("current_tense")

        if not verb or not tense:               # 新しい動詞を選択
            file  = random.choice(glob.glob("french_Verb/verbs/csv/*.csv"))
            verb  = os.path.basename(file).replace(".csv", "")
            tense = random.choice(possible_tenses)
            session.update({"current_verb": verb, "current_tense": tense,
                            "subject_index": 0})
            idx = 0

        df = pd.read_csv(f"french_Verb/verbs/csv/{verb}.csv", index_col=0)
        subjects = valid_subjects_for(tense, df)
        if not subjects:                        # 主語が取れなければスキップ
            _clear_current()
            return make_quiz(mode, tenses)

        if idx >= len(subjects):                # 範囲外ならリセット
            idx = 0
            session["subject_index"] = 0

        subj = subjects[idx]
        conjugation = get_conjugation(df, subj, tense)

        # 次回の主語インデックスを進める
        idx += 1
        if idx >= len(subjects):
            _clear_current()
        else:
            session["subject_index"] = idx

    # ---------- normal ----------
    else:
        while True:
            file  = random.choice(glob.glob("french_Verb/verbs/csv/*.csv"))
            verb  = os.path.basename(file).replace(".csv", "")
            df    = pd.read_csv(file, index_col=0)
            tense = random.choice(possible_tenses)
            subjects = valid_subjects_for(tense, df)
            if subjects:
                break
        subj = random.choice(subjects)
        conjugation = get_conjugation(df, subj, tense)

    # 共通：セッション保存
    session.update({"answer": conjugation, "verb": verb,
                    "subject": subj, "tense": tense})

    return {
        "verb": verb, "subject": subj, "tense": tense,
        "conjugation": "", "hint": HINTS.get(tense, ""),
        "streak": session.get("streak", 0)
    }

# ───────── ルーティング ─────────
@app.route("/quiz", methods=["POST"])
def quiz_api():
    data   = request.json or {}
    mode   = data.get("mode",   "normal")
    tenses = data.get("tenses", [])
    if "wrong_list" not in session:
        session["wrong_list"] = []
    return jsonify(make_quiz(mode, tenses))

@app.route("/skipverb", methods=["POST"])
def skipverb_api():
    app.logger.debug("---- /skipverb called ----")
    """by_subject で動詞を丸ごとスキップ → 新しい動詞の1問目を返す"""
    _clear_current()
    data   = request.json or {}
    tenses = data.get("tenses", [])
    # mode を明示しなくても by_subject 固定で OK
    return jsonify(make_quiz(mode="by_subject", tenses=tenses))

@app.route("/answer", methods=["POST"])
def answer_api():
    user = (request.json.get("user_answer","") or "").strip().lower()
    corr = (session.get("answer","")           or "").strip().lower()
    correct = user == corr
    session["streak"] = session.get("streak",0)+1 if correct else 0
    if not correct:
        wl = session.get("wrong_list",[])
        wl.append({k:session.get(k) for k in ("verb","subject","tense")})
        session["wrong_list"] = wl
    app.logger.debug("[ANSWER] correct=%s  wl=%s", correct, session.get("wrong_list"))

    return jsonify({"correct":correct,"correct_answer":corr,
                    "streak":session["streak"]})

# ───────── そのほか ─────────
@app.route("/")
def home():           return render_template("menu.html")

@app.route("/quizpage")
def quizpage():       return render_template("quiz.html")

@app.route("/wrongcount")
def wrongcount():     return jsonify({"count":len(session.get("wrong_list",[]))})

@app.route("/reset")
def reset():          session.clear(); return redirect("/")


if __name__ == "__main__":
    app.run(debug=True)
