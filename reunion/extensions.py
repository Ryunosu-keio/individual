"""
extensions.py - Flask拡張の初期化
循環インポートを避けるために、拡張は別ファイルで初期化してからapp.pyでバインドする
"""
from flask_sqlalchemy import SQLAlchemy

# SQLAlchemyインスタンス（app.pyでapp.init_app(app)する）
db = SQLAlchemy()
