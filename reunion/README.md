# 同窓会管理アプリ

同窓会の参加管理・入金管理・メール配信を統合管理する Web アプリケーション。

## 機能概要

**参加管理フロー**

1. 参加者が仮出欠フォームで回答
2. 管理者が本出欠フォーム URL を一括送信
3. 参加者がトークン付き URL で確定回答・振込名義を入力
4. 管理者が銀行 CSV をアップロードして入金を自動照合
5. 直前キャンセルの受付も対応

**管理画面の主な機能**

- 参加者一覧・詳細・検索
- 個別／一括メール送信（本出欠 URL・リマインド・最終リマインド）
- 入金管理・銀行 CSV 取込・自動照合
- 名簿 CSV インポート／エクスポート
- 同窓会情報・メールテンプレートの設定

## 技術スタック

| カテゴリ | 内容 |
|---------|------|
| バックエンド | Python / Flask |
| ORM | SQLAlchemy (Flask-SQLAlchemy) |
| DB | SQLite（開発）／ MySQL（PythonAnywhere）／ PostgreSQL（Render） |
| メール配信 | Brevo API / SMTP / GAS Webhook / コンソール（開発用） |
| フロントエンド | Jinja2 / Bootstrap |
| サーバー | Gunicorn |

## セットアップ

```bash
# 依存ライブラリのインストール
pip install -r requirements.txt

# 環境変数の設定
cp .env.example .env
# .env を編集

# 起動
python app.py
```

管理画面: `http://localhost:5001/admin/`

## 環境変数

`.env.example` を参照。主要なものは以下の通り。

| 変数名 | 説明 |
|-------|------|
| `SECRET_KEY` | Flask セッションキー |
| `DATABASE_URL` | DB 接続 URL |
| `MAIL_MODE` | `console` / `smtp` / `brevo` / `gas` |
| `BREVO_API_KEY` | Brevo API キー（`MAIL_MODE=brevo` 時） |
| `MAIL_FROM` | 送信元メールアドレス |
| `APP_BASE_URL` | 本出欠フォームの URL 生成に使用 |

設定のほとんどは管理画面の設定ページからも変更可能（DB に保存）。

## ファイル構成

```
reunion/
├── app.py                  # エントリーポイント
├── config.py               # 設定管理
├── models.py               # DB モデル定義
├── utils.py                # 振込名義正規化などのユーティリティ
├── routes/
│   ├── forms.py            # 仮出欠・本出欠フォーム
│   └── admin.py            # 管理画面
├── services/
│   ├── mail_service.py     # メール送信・テンプレート
│   ├── csv_service.py      # 銀行 CSV パース
│   ├── matching_service.py # 入金自動照合
│   └── token_service.py    # トークン生成
├── templates/
│   ├── provisional_form.html
│   ├── final_form.html
│   └── admin/
└── static/
```

## DB モデル

| テーブル | 説明 |
|---------|------|
| `participants` | 参加候補者 |
| `provisional_responses` | 仮出欠回答履歴 |
| `final_responses` | 本出欠回答履歴 |
| `payments` | 入金管理 |
| `bank_imports` | 銀行 CSV 生データ |
| `mail_logs` | メール送信ログ |
| `app_settings` | 管理画面設定 |
