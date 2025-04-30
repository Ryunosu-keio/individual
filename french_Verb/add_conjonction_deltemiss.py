import pandas as pd
import glob
import os

# 削除したいカラム名リスト
columns_to_delete = ["conditionnel_présent", "conditionnel_passé", "subjonctif_présent", "subjonctif_passé"]

# ファイルを取得
csv_files = glob.glob("french_Verb/verbs/csv/*.csv")

for file_path in csv_files:
    df = pd.read_csv(file_path, index_col=0)

    print(f"処理中ファイル: {os.path.basename(file_path)}")

    for column in columns_to_delete:
        if column in df.columns:
            df.drop(column, axis=1, inplace=True)
            print(f"  {column} 列を削除しました")
        else:
            print(f"  {column} 列は存在しません")

    # 必要なら上書き保存（ここ注意！本当に上書きしたい？）
    # df.to_csv(file_path, encoding='utf-8')

    # または別フォルダに保存（安全な方法）
    # new_path = file_path.replace('csv', 'csv_cleaned')
    # df.to_csv(new_path, encoding='utf-8')
