
import pandas as pd
import random
import glob

def get_conjugation(df, subject, tense):
    return df.loc[subject, tense]

def select_tense():
    tenses_options = {
        "0": "random",
        "1": "présent",
        "2": "futur simple",
        "3": "imparfait",
        "4": "passé composé",
        "5": "impératif"
    }
    
    print("解きたい時制を選んでください:")
    for key, value in tenses_options.items():
        print(f"{key}. {value}")
    
    while True:
        selection = input("解きたい時制の番号: ")
        if selection in tenses_options:
            return tenses_options[selection]
    return None

def quiz(selected_tense):
    # ランダムにCSVを選択
    files = glob.glob("french_Verb/verbs/csv/*.csv")
    csv_files = [file for file in files]
    selected_file = random.choice(csv_files)
    verb = selected_file.split(".")[0].split("\\")[1]
    
    # CSVからデータを読み込む
    df = pd.read_csv(selected_file, index_col=0)

    subjects = df.index.tolist()
    tenses = df.columns.tolist() if selected_tense == "random" else [selected_tense]

    # 空白でない conjugation を取得するまでループ
    while True:
        subject = random.choice(subjects)
        tense = random.choice(tenses)
        conjugation = get_conjugation(df, subject, tense)
        if pd.notna(conjugation) and conjugation.strip() != "":
            break

    print(f"{verb} ({subject},{tense})?")

    # 答えの入力
    answer = input("答えを入力してください: ")

    # 正解のチェック
    if answer == conjugation:
        print("正解!")
    else:
        print(f"不正解。正解は「{conjugation}」です。")

    # 続行するかの確認
    next_step = input("続けるならenter、やめるならqを入力してください: ")
    return next_step

# クイズの実行
if __name__ == "__main__":
    tense = select_tense()
    while True:
        action = quiz(tense)
        if action == "q":
            break

# %%
