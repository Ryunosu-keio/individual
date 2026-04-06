"""
config.py - アプリ設定管理
.env ファイルから設定を読み込み、Flaskアプリに渡す
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# プロジェクトルートの .env を読み込む
load_dotenv(Path(__file__).parent / ".env")


class Config:
    # -----------------------------------------------
    # Flask基本設定
    # -----------------------------------------------
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-please-change")
    FLASK_ENV = os.getenv("FLASK_ENV", "development")

    # -----------------------------------------------
    # データベース設定
    # instance/ フォルダに reunion.db を作成する
    # -----------------------------------------------
    BASE_DIR = Path(__file__).parent
    _db_url = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'reunion.db'}")
    # RenderのPostgreSQL URLは "postgres://" で始まるが SQLAlchemy は "postgresql://" が必要
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql://", 1)
    # PythonAnywhere MySQL: mysql://user:pass@host/dbname
    SQLALCHEMY_DATABASE_URI = _db_url
    # MySQL charset設定
    if "mysql" in _db_url:
        SQLALCHEMY_ENGINE_OPTIONS_EXTRA = {"connect_args": {"charset": "utf8mb4"}}
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,      # 使用前に接続が生きているか確認
        "pool_recycle": 280,        # 280秒で接続を再作成（Renderの5分タイムアウト対策）
    }

    # -----------------------------------------------
    # メール設定
    # -----------------------------------------------
    MAIL_MODE = os.getenv("MAIL_MODE", "console")  # "console" or "smtp"
    MAIL_SMTP_HOST = os.getenv("MAIL_SMTP_HOST", "smtp.gmail.com")
    MAIL_SMTP_PORT = int(os.getenv("MAIL_SMTP_PORT", "587"))
    MAIL_SMTP_USER = os.getenv("MAIL_SMTP_USER", "")
    MAIL_SMTP_PASSWORD = os.getenv("MAIL_SMTP_PASSWORD", "")
    MAIL_FROM = os.getenv("MAIL_FROM", "no-reply@example.com")
    MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "同窓会幹事")

    # -----------------------------------------------
    # アプリURL（本出欠メールのURLに使用）
    # -----------------------------------------------
    APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:5000")

    # -----------------------------------------------
    # 同窓会情報（メール本文に使用）
    # -----------------------------------------------
    REUNION_NAME = os.getenv("REUNION_NAME", "同窓会")
    REUNION_DATE = os.getenv("REUNION_DATE", "日程未定")
    REUNION_VENUE = os.getenv("REUNION_VENUE", "会場未定")
    REUNION_FEE = os.getenv("REUNION_FEE", "未定")

    # -----------------------------------------------
    # ログ設定
    # -----------------------------------------------
    LOG_DIR = BASE_DIR / "logs"
    LOG_FILE = LOG_DIR / "app.log"
