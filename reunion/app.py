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
        ("provisional_responses", "share_consent", "BOOLEAN DEFAULT 0"),
        ("final_responses",       "share_consent", "BOOLEAN DEFAULT 0"),
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

    @app.route("/status")
    def status():
        from flask import render_template
        from models import Participant

        def _class_label(cls):
            if cls and cls.isdigit():
                return f"{cls}組"
            return cls or "不明"

        TEACHER_ROLES = {"教師", "学年主任", "副担任"}

        participants = Participant.query.all()
        prov_stats  = {"attending": 0, "not_attending": 0, "undecided": 0, "no_response": 0}
        final_stats = {"attending": 0, "not_attending": 0, "no_response": 0}
        student_map = {}
        teachers    = []

        for p in participants:
            prov  = p.latest_provisional
            final = p.latest_final
            ps = prov.status  if prov  else "no_response"
            fs = (final.status if final.status != "cancelled" else "not_attending") if final else "no_response"
            prov_consent  = bool(prov.share_consent)  if prov  else False
            final_consent = bool(final.share_consent) if final else False

            prov_stats[ps]  = prov_stats.get(ps, 0)  + 1
            final_stats[fs] = final_stats.get(fs, 0) + 1

            info = {"name": p.name, "prov": ps, "final": fs,
                    "prov_consent": prov_consent, "final_consent": final_consent}
            if p.role in TEACHER_ROLES:
                teachers.append(info)
            else:
                cls = p.class_name or ""
                student_map.setdefault(cls, []).append(info)

        sorted_classes = [
            {"key": cls, "label": _class_label(cls), "people": people}
            for cls, people in sorted(student_map.items(), key=lambda x: (not x[0], x[0]))
        ]

        return render_template("status.html",
            prov_stats=prov_stats,
            final_stats=final_stats,
            sorted_classes=sorted_classes,
            teachers=teachers,
            total=len(participants),
        )

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
