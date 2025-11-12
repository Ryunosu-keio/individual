# -*- coding: utf-8 -*-
"""
Bilingual Verb Quiz backend (FR + IT)
- /quiz       : 通常・主語順・復習モードの問題を返す（tenses と lang を受け付け）
- /skipverb   : by_subject の途中で動詞をスキップし、次の動詞+最初の主語から再開
- /answer     : 採点＆streak更新、誤答は wrong_list に登録（review時は先頭pop）
- /wrongcount : 復習キュー件数
- /reset      : セッションクリア

CSV（app.root_path 基準）：
- FR:  <project_root>/verbs/csv/*.csv
- IT:  <project_root>/verbs_it/csv/*.csv
"""

from flask import Flask, request, jsonify, render_template, redirect, session
import pandas as pd, random, unicodedata
from pathlib import Path

app = Flask(__name__)
app.secret_key = "your_secret_key_here"

import logging
logging.basicConfig(level=logging.DEBUG)
log = app.logger

# ---- 正規化 ----
def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s).strip().casefold()

# ---- ヒント ----
HINTS_FR = {
    "présent":"主語に応じた現在形語尾",
    "futur simple":"原形＋未来形語尾（全人称共通語幹）",
    "imparfait":"現在 1複 語幹＋半過去語尾",
    "passé composé":"avoir / être ＋ 過去分詞",
    "impératif":"tu / nous / vous の命令語尾",
    "subjonctif_présent":"que＋現在3複語幹＋接続法語尾",
    "subjonctif_passé":"que＋接続法現在 avoir / être ＋ 過去分詞",
    "conditionnel_présent":"未来語幹＋半過去語尾",
    "conditionnel_passé":"条件法現在 avoir / être ＋ 過去分詞",
}
HINTS_IT = {
    "presente":               "現在：-are系(-o,-i,-a,-iamo,-ate,-ano)／-ere系(-o,-i,-e,-iamo,-ete,-ono)／-ire系(-o,-i,-e,-iamo,-ite,-ono)・‘-isc-’型は(isco, isci, isce, iamo, ite, iscono)",
    "futuro semplice":        "不定形語幹＋未来語尾(-ò,-ai,-à,-emo,-ete,-anno)／-care,-gareは語幹にh／-ciare,-giareはi脱落",
    "imperfetto":             "語幹＋半過去語尾(-vo,-vi,-va,-vamo,-vate,-vano)（1複現在語幹を基準に）",
    "passato prossimo":       "avere/essere＋過去分詞（reflexiveと多くの移動・状態変化=essere）／essereは主語と性数一致(–o,–a,–i,–e)",
    "imperativo":             "tu/noi/voiのみ（-are系はtu: -a）／否定のtuは“non + 不定詞”(non parlare)／肯定命令は代名詞を後置融合(dimmi)",
    "congiuntivo presente":   "接続法現在：-are系(-i,-i,-i,-iamo,-iate,-ino)／-ere/-ire系(-a,-a,-a,-iamo,-iate,-ano)",
    "congiuntivo passato":    "接続法現在の avere/essere ＋ 過去分詞（essereは性数一致）",
    "condizionale presente":  "条件法語尾(-rei,-resti,-rebbe,-remmo,-reste,-rebbero)／未来語幹と同じ語幹規則",
    "condizionale passato":   "条件法現在の avere/essere ＋ 過去分詞（essereは性数一致）",
}

# ---- 主語と命令法名 ----
SUBJECTS = {
    "fr": {"all":["je","tu","il","elle","nous","vous","ils","elles"],
           "imp":["tu","nous","vous"], "imp_name":"impératif"},
    "it": {"all":["io","tu","lui","lei","noi","voi","loro"],
           "imp":["tu","noi","voi"], "imp_name":"imperativo"},
}

# ---- 時制エイリアス ----
TENSE_ALIAS = {
    # fr
    _norm("présent"):("fr","présent"),
    _norm("futur simple"):("fr","futur simple"),
    _norm("imparfait"):("fr","imparfait"),
    _norm("passé composé"):("fr","passé composé"),
    _norm("impératif"):("fr","impératif"),
    _norm("subjonctif_présent"):("fr","subjonctif_présent"),
    _norm("subjonctif_passé"):("fr","subjonctif_passé"),
    _norm("conditionnel_présent"):("fr","conditionnel_présent"),
    _norm("conditionnel_passé"):("fr","conditionnel_passé"),
    # it
    _norm("presente"):("it","presente"),
    _norm("futuro semplice"):("it","futuro semplice"),
    _norm("imperfetto"):("it","imperfetto"),
    _norm("passato prossimo"):("it","passato prossimo"),
    _norm("imperativo"):("it","imperativo"),
    _norm("congiuntivo presente"):("it","congiuntivo presente"),
    _norm("congiuntivo passato"):("it","congiuntivo passato"),
    _norm("condizionale presente"):("it","condizionale presente"),
    _norm("condizionale passato"):("it","condizionale passato"),
}

# ---- CSV ディレクトリ（絶対パス）----
CSV_DIR = {
    "fr": (Path(app.root_path) / "verbs" / "csv").resolve(),
    "it": (Path(app.root_path) / "verbs_it" / "csv").resolve(),
}

# ---- ツール関数 ----
def resolve_lang_and_tenses(raw_tenses, forced_lang=None):
    # 型揺れ吸収
    if raw_tenses is None:
        items = []
    elif isinstance(raw_tenses, str):
        items = [s for s in raw_tenses.split(",") if s.strip()]
    else:
        items = [str(s) for s in raw_tenses]

    # 明示言語を優先
    if forced_lang in ("fr","it"):
        lang = forced_lang
        hints = HINTS_FR if lang=="fr" else HINTS_IT
        if not items or any(_norm(s)=="all" for s in items):
            return (lang, list(hints.keys()), hints)
        mapped = [TENSE_ALIAS.get(_norm(s)) for s in items]
        tens = [t for lg,t in mapped if lg==lang and t in hints]
        tens = list(dict.fromkeys(tens)) or list(hints.keys())
        return (lang, tens, hints)

    # 言語未指定→推定
    if not items or any(_norm(s)=="all" for s in items):
        return ("fr", list(HINTS_FR.keys()), HINTS_FR)  # 既定 fr
    mapped = [TENSE_ALIAS.get(_norm(s)) for s in items]
    mapped = [m for m in mapped if m]
    votes = {"fr":0,"it":0}
    for lg,_ in mapped: votes[lg]+=1
    lang = "fr" if votes["fr"]>=votes["it"] else "it"
    hints = HINTS_FR if lang=="fr" else HINTS_IT
    tens = [t for lg,t in mapped if lg==lang and t in hints]
    tens = list(dict.fromkeys(tens)) or list(hints.keys())
    return (lang, tens, hints)

def pick_csv_file(lang: str) -> Path:
    files = list(CSV_DIR[lang].glob("*.csv"))
    log.debug("[DATA] lang=%s dir=%s found=%d files=%s",
              lang, str(CSV_DIR[lang]), len(files), [f.name for f in files])
    if not files:
        raise FileNotFoundError(f"No CSV files in {CSV_DIR[lang]}")
    return random.choice(files)

def read_verb_csv(lang: str, verb: str) -> pd.DataFrame:
    file = CSV_DIR[lang] / f"{verb}.csv"
    if not file.exists():
        raise FileNotFoundError(f"{file} not found")
    return pd.read_csv(file, index_col=0, encoding="utf-8-sig")

def valid_subjects_for(tense: str, df: pd.DataFrame, lang: str):
    spec = SUBJECTS[lang]
    subs = spec["imp"] if tense==spec["imp_name"] else spec["all"]
    subs = [s for s in subs if s in df.index]
    log.debug("[SUBJECTS] lang=%s tense=%s -> %s", lang, tense, subs)
    return subs

def get_conjugation(df, subj, tense):
    v = df.loc[subj, tense]
    return str(v.iloc[0]) if hasattr(v, "iloc") else str(v)

def _clear_current():
    for k in ("current_verb","current_tense","subject_index"):
        session.pop(k, None)

# ---- 出題本体 ----
def make_quiz(mode="normal", tenses=None):
    # lang: payload.lang > session['lang'] > tenses推定
    forced_lang = None
    if request.is_json:
        lg = request.json.get("lang")
        if lg in ("fr","it"):
            forced_lang = lg
    if not forced_lang:
        forced_lang = session.get("lang")

    lang, possible_tenses, hints = resolve_lang_and_tenses(tenses, forced_lang)
    session["lang"] = lang
    log.debug("[TENSES] raw=%s -> lang=%s tenses=%s", tenses, lang, possible_tenses)

    if mode == "review":
        wl = session.get("wrong_list", [])
        if not wl:
            return {"finished": True, "lang": lang}
        entry = wl[0]
        verb, subj, tense = entry["verb"], entry["subject"], entry["tense"]
        df = read_verb_csv(lang, verb)
        conjugation = get_conjugation(df, subj, tense)

    elif mode == "by_subject":
        idx   = session.get("subject_index", 0)
        verb  = session.get("current_verb")
        tense = session.get("current_tense")
        log.debug("[BY_SUBJECT] idx=%s verb=%s tense=%s", idx, verb, tense)

        if not verb or not tense:
            file  = pick_csv_file(lang)
            verb  = file.stem
            tense = random.choice(possible_tenses)
            session.update({"current_verb": verb, "current_tense": tense, "subject_index": 0})
            idx = 0

        df = read_verb_csv(lang, verb)
        subjects = valid_subjects_for(tense, df, lang)
        if not subjects:
            _clear_current()
            return make_quiz(mode, tenses)

        if idx >= len(subjects):
            idx = 0
            session["subject_index"] = 0

        subj = subjects[idx]
        conjugation = get_conjugation(df, subj, tense)

        idx += 1
        if idx >= len(subjects):
            _clear_current()
        else:
            session["subject_index"] = idx

    else:
        while True:
            file  = pick_csv_file(lang)
            verb  = file.stem
            df    = pd.read_csv(file, index_col=0, encoding="utf-8-sig")
            tense = random.choice(possible_tenses)
            subjects = valid_subjects_for(tense, df, lang)
            if subjects:
                break
        subj = random.choice(subjects)
        conjugation = get_conjugation(df, subj, tense)

    session.update({"answer": conjugation, "verb": verb, "subject": subj, "tense": tense})
    return {
        "verb": verb, "subject": subj, "tense": tense,
        "conjugation": "", "hint": hints.get(tense, ""),
        "streak": session.get("streak", 0), "lang": lang
    }

# ---- ルーティング ----
@app.route("/quiz", methods=["POST"])
def quiz_api():
    data   = request.json or {}
    mode   = data.get("mode",   "normal")
    tenses = data.get("tenses", [])
    # lang 明示は保存
    if data.get("lang") in ("fr","it"):
        session["lang"] = data["lang"]
    if "wrong_list" not in session:
        session["wrong_list"] = []
    return jsonify(make_quiz(mode, tenses))

@app.route("/skipverb", methods=["POST"])
def skipverb_api():
    _clear_current()
    data = request.json or {}
    tenses = data.get("tenses", [])
    return jsonify(make_quiz(mode="by_subject", tenses=tenses))

@app.route("/answer", methods=["POST"])
def answer_api():
    user = (request.json.get("user_answer","") or "").strip().lower()
    corr = (session.get("answer","")           or "").strip().lower()
    correct = user == corr
    session["streak"] = session.get("streak",0)+1 if correct else 0

    wl = session.get("wrong_list", [])
    current = {k: session.get(k) for k in ("verb","subject","tense")}
    if correct:
        if wl and wl[0] == current:
            wl.pop(0)
    else:
        if current not in wl:
            wl.append(current)
    session["wrong_list"] = wl
    log.debug("[ANSWER] correct=%s wl=%s", correct, wl)

    return jsonify({"correct":correct,"correct_answer":corr,"streak":session["streak"]})

@app.route("/")
def home():
    return render_template("menu.html")

@app.route("/quizpage")
def quizpage():
    return render_template("quiz.html")

@app.route("/wrongcount")
def wrongcount():
    return jsonify({"count":len(session.get("wrong_list",[]))})

@app.route("/reset")
def reset():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)
