"""
app.py - Flaskアプリのエントリーポイント

起動方法:
  python app.py
"""
import logging
import logging.handlers
from pathlib import Path
from flask import Flask, redirect, url_for
from config import Config
from extensions import db


def _migrate(db):
    """既存テーブルへのカラム追加マイグレーション（MySQL/SQLite/PostgreSQL互換）"""
    migrations = [
        ("participants", "name_kana",     "VARCHAR(100) DEFAULT ''"),
        ("participants", "new_name",      "VARCHAR(100) DEFAULT ''"),
        ("participants", "new_name_kana", "VARCHAR(100) DEFAULT ''"),
        ("final_responses", "bank_name",      "VARCHAR(100) DEFAULT ''"),
        ("final_responses", "branch_name",    "VARCHAR(100) DEFAULT ''"),
        ("final_responses", "account_number", "VARCHAR(50) DEFAULT ''"),
    ]
    with db.engine.connect() as conn:
        for table, column, col_def in migrations:
            try:
                # カラムの存在をチェックしてから追加（DB非依存）
                from sqlalchemy import inspect
                inspector = inspect(db.engine)
                existing_cols = [c["name"] for c in inspector.get_columns(table)]
                if column not in existing_cols:
                    conn.execute(db.text(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"))
                    conn.commit()
            except Exception:
                conn.rollback()


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)

    # instance フォルダを作成
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    # ログフォルダを作成
    Config.LOG_DIR.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------
    # ログ設定
    # -----------------------------------------------
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.handlers.RotatingFileHandler(
                Config.LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            ),
        ],
    )

    # -----------------------------------------------
    # SQLAlchemy バインド
    # -----------------------------------------------
    db.init_app(app)

    with app.app_context():
        from models import Participant, ProvisionalResponse, FinalResponse, Payment, BankImport, MailLog, AppSetting
        db.create_all()
        # カラム追加マイグレーション（既存DBへの追加カラム）
        _migrate(db)

    # -----------------------------------------------
    # config_obj: メールサービスから参照できるよう
    # config の値をオブジェクトとして持たせる
    # -----------------------------------------------
    class ConfigObj:
        pass
    cfg = ConfigObj()
    for key, value in app.config.items():
        setattr(cfg, key, value)
    app.config_obj = cfg

    # -----------------------------------------------
    # Blueprint 登録
    # -----------------------------------------------
    from routes.forms import forms_bp
    from routes.admin import admin_bp

    app.register_blueprint(forms_bp)
    app.register_blueprint(admin_bp)

    # -----------------------------------------------
    # Jinja2 フィルタ: UTC → JST 変換
    # -----------------------------------------------
    from datetime import timedelta

    @app.template_filter("jst")
    def jst_filter(dt, fmt="%Y/%m/%d %H:%M"):
        if dt is None:
            return ""
        return (dt + timedelta(hours=9)).strftime(fmt)

    # -----------------------------------------------
    # トップページ → 管理画面にリダイレクト
    # -----------------------------------------------
    @app.route("/")
    def index():
        return redirect(url_for("admin.index"))

    # -----------------------------------------------
    # エラーハンドラ
    # -----------------------------------------------
    @app.errorhandler(404)
    def not_found(e):
        from flask import render_template
        return render_template("error.html", code=404, message="ページが見つかりません。"), 404

    @app.errorhandler(500)
    def server_error(e):
        from flask import render_template
        return render_template("error.html", code=500, message="サーバーエラーが発生しました。"), 500

    return app


app = create_app()

if __name__ == "__main__":
    app = create_app()
    print("=" * 50)
    print("同窓会管理アプリを起動します")
    print("管理画面:      http://localhost:5000/admin/")
    print("仮出欠フォーム: http://localhost:5000/form/provisional")
    print("停止: Ctrl+C")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5001, debug=True)
