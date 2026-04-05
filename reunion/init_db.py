"""
init_db.py - データベース初期化スクリプト

使い方:
  python init_db.py

実行すると:
  - instance/reunion.db を作成
  - 全テーブルを作成（既存テーブルはスキップ）
"""
from app import create_app
from extensions import db

app = create_app()

with app.app_context():
    db.create_all()
    print("データベースを初期化しました: instance/reunion.db")
    print("作成されたテーブル:")
    from sqlalchemy import inspect
    inspector = inspect(db.engine)
    for table_name in inspector.get_table_names():
        print(f"  - {table_name}")
