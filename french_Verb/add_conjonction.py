import pandas as pd
import glob

# 追加する時制のリスト
file_names  = ["impératif","conditionnel_présent", "conditionnel_passé", "subjonctif_présent", "subjonctif_passé"]

# スキップするファイル名リスト
# skip_file_names = ["aller.csv", "se_lever.csv", "partir.csv", "rester.csv", "venir.csv"]

for file_name in file_names:
    mainfile = pd.read_csv(f"french_Verb/verbs/{file_name}.csv", index_col=0)
    print(f"処理中：{file_name}")

    # csvファイル一覧を取得
    csv_files = glob.glob("french_Verb/verbs/csv/*.csv")

    for file in csv_files:
        base_name = file.split("\\")[-1]  # windows環境ならこれでOK
        base_name_noext = base_name.split('.')[0]  # .csvを取る

        # if base_name in skip_file_names:
            # continue

        # ファイルを開く
        df = pd.read_csv(file, index_col=0)

        # 動詞が存在すれば列を追加
        if base_name_noext in mainfile.columns:
            df[file_name] = mainfile[base_name_noext]

            df.to_csv(file)
            print(f"  {base_name_noext}に{file_name}の活用形の列を追加しました")
        else:
            print(f"  {base_name_noext}は{file_name}に存在しません")
