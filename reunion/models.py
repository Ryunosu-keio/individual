"""
models.py - データベースモデル定義（SQLAlchemy ORM）

テーブル構成:
  participants       : 参加候補者（一意の人物）
  provisional_responses : 仮出欠回答
  final_responses    : 本出欠回答
  payments           : 入金管理
  bank_imports       : 銀行CSV取込データ
  mail_logs          : メール送信ログ
"""
from datetime import datetime
from extensions import db


class Participant(db.Model):
    """
    参加候補者テーブル
    仮出欠フォームで登録される。以降の処理の起点となる。
    """
    __tablename__ = "participants"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)          # 氏名
    email = db.Column(db.String(200), nullable=False, unique=True)  # メールアドレス（重複排除の基準）
    token = db.Column(db.String(64), unique=True, nullable=True)    # 本出欠フォームURL用トークン
    class_name = db.Column(db.String(50), default="")         # クラス（2桁数字: 31=3年1組、学年主任は空）
    student_number = db.Column(db.String(20), default="")     # 出席番号
    # role: 生徒 / 教師 / 学年主任
    role = db.Column(db.String(20), default="生徒")
    notes = db.Column(db.Text, default="")                    # 参加者自身のメモ（備考）
    teacher_memo = db.Column(db.Text, default="")             # 幹事用メモ
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # リレーション
    provisional_responses = db.relationship(
        "ProvisionalResponse", backref="participant", lazy=True, cascade="all, delete-orphan"
    )
    final_responses = db.relationship(
        "FinalResponse", backref="participant", lazy=True, cascade="all, delete-orphan"
    )
    payment = db.relationship(
        "Payment", backref="participant", uselist=False, cascade="all, delete-orphan"
    )
    mail_logs = db.relationship(
        "MailLog", backref="participant", lazy=True, cascade="all, delete-orphan"
    )

    @property
    def latest_provisional(self):
        """最新の仮出欠回答を返す"""
        if self.provisional_responses:
            return sorted(self.provisional_responses, key=lambda r: r.submitted_at, reverse=True)[0]
        return None

    @property
    def latest_final(self):
        """最新の本出欠回答を返す"""
        if self.final_responses:
            return sorted(self.final_responses, key=lambda r: r.submitted_at, reverse=True)[0]
        return None

    def __repr__(self):
        return f"<Participant {self.id}: {self.name} ({self.email})>"


class ProvisionalResponse(db.Model):
    """
    仮出欠回答テーブル
    同一人物が複数回回答した場合も履歴として保持する。
    最新のものが有効な回答として扱われる。
    """
    __tablename__ = "provisional_responses"

    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participants.id"), nullable=False)
    # status: attending=参加 / not_attending=不参加 / undecided=未定
    status = db.Column(db.String(20), nullable=False, default="undecided")
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    ip_address = db.Column(db.String(50), default="")  # 送信元IPアドレス（簡易ログ用）

    STATUS_LABELS = {
        "attending": "参加",
        "not_attending": "不参加",
        "undecided": "未定",
    }

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, self.status)

    def __repr__(self):
        return f"<ProvisionalResponse {self.id}: participant={self.participant_id} status={self.status}>"


class FinalResponse(db.Model):
    """
    本出欠回答テーブル
    トークン付きURLから回答。同一人物の複数回答も履歴として保持。
    """
    __tablename__ = "final_responses"

    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participants.id"), nullable=False)
    # status: attending=参加 / not_attending=不参加
    status = db.Column(db.String(20), nullable=False)
    companions = db.Column(db.Integer, default=0)              # 同伴者数
    transfer_name = db.Column(db.String(100), default="")      # 振込名義（カタカナ）
    bank_name = db.Column(db.String(100), default="")          # 銀行名
    branch_name = db.Column(db.String(100), default="")        # 支店名
    account_number = db.Column(db.String(50), default="")      # 口座番号
    payment_expected = db.Column(db.Integer, default=0)        # 支払予定金額（円）
    payment_method = db.Column(db.String(50), default="bank_transfer")  # 支払方法
    remarks = db.Column(db.Text, default="")                   # 備考
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    ip_address = db.Column(db.String(50), default="")

    STATUS_LABELS = {
        "attending": "参加",
        "not_attending": "不参加",
    }

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, self.status)

    def __repr__(self):
        return f"<FinalResponse {self.id}: participant={self.participant_id} status={self.status}>"


class Payment(db.Model):
    """
    入金管理テーブル
    参加者1人につき1レコード。本出欠回答後に自動作成または手動作成。
    銀行CSVとの照合結果もここに記録する。
    """
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participants.id"), nullable=False, unique=True)
    expected_amount = db.Column(db.Integer, default=0)         # 支払予定金額（円）
    paid_amount = db.Column(db.Integer, default=0)             # 実際の支払金額（円）
    # payment_status: unpaid=未払い / paid=支払済み / partial=一部支払い
    payment_status = db.Column(db.String(20), default="unpaid")
    payment_date = db.Column(db.Date, nullable=True)           # 支払日
    payment_method = db.Column(db.String(50), default="bank_transfer")  # 支払方法
    transfer_name = db.Column(db.String(100), default="")      # 振込名義（本出欠フォームからコピー）

    # 銀行CSV照合結果
    bank_csv_matched = db.Column(db.Boolean, default=False)    # 照合済みか
    bank_csv_amount = db.Column(db.Integer, nullable=True)     # CSV上の金額
    bank_csv_date = db.Column(db.Date, nullable=True)          # CSV上の日付
    bank_csv_raw_name = db.Column(db.String(200), default="")  # CSV上の振込名義（生データ）
    bank_import_id = db.Column(db.Integer, db.ForeignKey("bank_imports.id"), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    STATUS_LABELS = {
        "unpaid": "未払い",
        "paid": "支払済み",
        "partial": "一部支払い",
    }

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.payment_status, self.payment_status)

    def __repr__(self):
        return f"<Payment {self.id}: participant={self.participant_id} status={self.payment_status}>"


class BankImport(db.Model):
    """
    銀行CSV取込データテーブル
    銀行からダウンロードしたCSVの生データを1行1レコードで保存する。
    照合は matching_service.py で実施。
    """
    __tablename__ = "bank_imports"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200), default="")           # 元のファイル名
    import_date = db.Column(db.DateTime, default=datetime.utcnow)  # 取込日時
    raw_name = db.Column(db.String(200), default="")           # CSV上の振込名義（生データ）
    raw_date = db.Column(db.Date, nullable=True)               # CSV上の取引日
    raw_amount = db.Column(db.Integer, default=0)              # CSV上の金額（円）
    # match_status: unmatched=未照合 / matched=照合済み / confirmed=確定済み
    match_status = db.Column(db.String(20), default="unmatched")
    matched_participant_id = db.Column(db.Integer, db.ForeignKey("participants.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # 照合された参加者（リレーション）
    matched_participant = db.relationship("Participant", foreign_keys=[matched_participant_id])
    # このBankImportを参照しているPaymentレコード
    payments = db.relationship("Payment", backref="bank_import", lazy=True)

    STATUS_LABELS = {
        "unmatched": "未照合",
        "matched": "照合済み",
        "confirmed": "確定済み",
    }

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.match_status, self.match_status)

    def __repr__(self):
        return f"<BankImport {self.id}: {self.raw_name} {self.raw_amount}円>"


class AppSetting(db.Model):
    """
    アプリ設定テーブル
    管理画面から変更できる設定値を保存する。
    .env の値がデフォルトで、ここに保存された値が優先される。
    """
    __tablename__ = "app_settings"

    id    = db.Column(db.Integer, primary_key=True)
    key   = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, default="")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<AppSetting {self.key}={self.value}>"


class MailLog(db.Model):
    """
    メール送信ログテーブル
    送信日時・種別・成否を記録する。再送信・リマインド管理に使用。
    """
    __tablename__ = "mail_logs"

    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participants.id"), nullable=False)
    # mail_type: final_url=本出欠URL送信 / reminder=リマインド
    mail_type = db.Column(db.String(50), nullable=False)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    # status: sent=送信成功 / failed=送信失敗 / simulated=コンソール出力（開発用）
    status = db.Column(db.String(20), default="sent")
    error_message = db.Column(db.Text, default="")             # 失敗時のエラーメッセージ

    def __repr__(self):
        return f"<MailLog {self.id}: participant={self.participant_id} type={self.mail_type} status={self.status}>"
