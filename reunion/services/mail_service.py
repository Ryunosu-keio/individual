"""
services/mail_service.py - メール送信サービス

MAIL_MODE=console の場合: コンソールに出力するだけ（開発用）
MAIL_MODE=smtp    の場合: SMTPで実際に送信する

.env の設定で切り替え可能。
"""
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from flask import current_app
from extensions import db
from models import MailLog, AppSetting

logger = logging.getLogger(__name__)


def _render_template(template: str, **kwargs) -> str:
    """テンプレート文字列の {変数} を置換する"""
    for key, val in kwargs.items():
        template = template.replace("{" + key + "}", str(val))
    return template


def _get_template(key: str, fallback: str) -> str:
    """DBからテンプレートを取得。なければfallbackを返す"""
    s = AppSetting.query.filter_by(key=key).first()
    return s.value if (s and s.value) else fallback


def _get_reunion_info() -> dict:
    """DBから同窓会情報を取得。なければcurrent_app.configから取得"""
    cfg = current_app.config
    def get(key, fallback):
        s = AppSetting.query.filter_by(key=key).first()
        return s.value if (s and s.value) else fallback
    return {
        "reunion_name":  get("reunion_name",  cfg.get("REUNION_NAME", "同窓会")),
        "reunion_date":  get("reunion_date",  cfg.get("REUNION_DATE", "")),
        "reunion_venue": get("reunion_venue", cfg.get("REUNION_VENUE", "")),
        "reunion_fee":   get("reunion_fee",   cfg.get("REUNION_FEE", "")),
    }


def _build_final_url_mail_body(participant_name: str, final_url: str, config=None) -> tuple:
    """本出欠URL送信メールの件名・本文を生成する（DBテンプレート優先）"""
    reunion = _get_reunion_info()
    vars = dict(
        name=participant_name,
        final_url=final_url,
        reunion_name=reunion["reunion_name"],
        reunion_date=reunion["reunion_date"],
        reunion_venue=reunion["reunion_venue"],
        reunion_fee=reunion["reunion_fee"],
    )
    subject = _render_template(
        _get_template('mail_final_url_subject', '【{reunion_name}】本出欠のご確認をお願いします'),
        **vars
    )
    body = _render_template(
        _get_template('mail_final_url_body', '{name} 様\n\n本出欠フォーム:\n{final_url}'),
        **vars
    )
    return subject, body


def _build_reminder_mail_body(participant_name: str, final_url: str, config=None) -> tuple:
    """リマインドメールの件名・本文を生成する（DBテンプレート優先）"""
    reunion = _get_reunion_info()
    vars = dict(
        name=participant_name,
        final_url=final_url,
        reunion_name=reunion["reunion_name"],
        reunion_date=reunion["reunion_date"],
        reunion_venue=reunion["reunion_venue"],
        reunion_fee=reunion["reunion_fee"],
    )
    subject = _render_template(
        _get_template('mail_reminder_subject', '【{reunion_name}】リマインド'),
        **vars
    )
    body = _render_template(
        _get_template('mail_reminder_body', '{name} 様\n\n本出欠フォーム:\n{final_url}'),
        **vars
    )
    return subject, body


def _send_smtp_cfg(to_email: str, subject: str, body: str, cfg: dict) -> None:
    """
    SMTPでメールを送信する（DB設定辞書を使用）。
    Gmail の場合はアプリパスワードを使用すること。
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{cfg['from_name']} <{cfg['from_addr']}>"
    msg["To"] = to_email

    part = MIMEText(body, "plain", "utf-8")
    msg.attach(part)

    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as server:
        server.ehlo()
        server.starttls()
        server.login(cfg["smtp_user"], cfg["smtp_password"])
        server.sendmail(cfg["from_addr"], [to_email], msg.as_string())


def _send_console(to_email: str, subject: str, body: str) -> None:
    """
    コンソール（ログ）にメール内容を出力する。開発用。
    実際の送信は行わない。
    """
    separator = "=" * 60
    logger.info(f"\n{separator}\n[メール送信シミュレーション]\nTo: {to_email}\nSubject: {subject}\n\n{body}\n{separator}")
    print(f"\n{separator}")
    print(f"[メール送信シミュレーション]")
    print(f"To: {to_email}")
    print(f"Subject: {subject}")
    print(f"\n{body}")
    print(f"{separator}\n")


def _get_mail_config():
    """
    DB設定を優先、なければ .env（current_app.config）を使う。
    """
    def get(key, fallback):
        s = AppSetting.query.filter_by(key=key).first()
        return s.value if (s and s.value) else fallback

    cfg = current_app.config
    return {
        "mode":          get("mail_mode",          cfg.get("MAIL_MODE", "console")),
        "smtp_host":     get("mail_smtp_host",     cfg.get("MAIL_SMTP_HOST", "smtp.gmail.com")),
        "smtp_port": int(get("mail_smtp_port",     str(cfg.get("MAIL_SMTP_PORT", 587)))),
        "smtp_user":     get("mail_smtp_user",     cfg.get("MAIL_SMTP_USER", "")),
        "smtp_password": get("mail_smtp_password", cfg.get("MAIL_SMTP_PASSWORD", "")),
        "from_addr":     get("mail_from",          cfg.get("MAIL_FROM", "")),
        "from_name":     get("mail_from_name",     cfg.get("MAIL_FROM_NAME", "同窓会幹事")),
    }


def send_final_url(participant, final_url: str) -> MailLog:
    """
    参加者に本出欠URLを送信する。
    送信結果を mail_logs テーブルに記録して返す。
    """
    mail_cfg = _get_mail_config()
    mail_mode = mail_cfg["mode"]

    subject, body = _build_final_url_mail_body(participant.name, final_url)

    log = MailLog(
        participant_id=participant.id,
        mail_type="final_url",
        sent_at=datetime.utcnow(),
    )

    try:
        if mail_mode == "smtp":
            _send_smtp_cfg(participant.email, subject, body, mail_cfg)
            log.status = "sent"
            logger.info(f"本出欠URL送信成功: {participant.email}")
        else:
            _send_console(participant.email, subject, body)
            log.status = "simulated"

        db.session.add(log)
        db.session.commit()

    except Exception as e:
        log.status = "failed"
        log.error_message = str(e)
        db.session.add(log)
        db.session.commit()
        logger.error(f"本出欠URL送信失敗: {participant.email} - {e}", exc_info=True)
        raise

    return log


def send_reminder(participant, final_url: str) -> MailLog:
    """
    参加者にリマインドメールを送信する。
    """
    mail_cfg = _get_mail_config()
    mail_mode = mail_cfg["mode"]

    subject, body = _build_reminder_mail_body(participant.name, final_url)

    log = MailLog(
        participant_id=participant.id,
        mail_type="reminder",
        sent_at=datetime.utcnow(),
    )

    try:
        if mail_mode == "smtp":
            _send_smtp_cfg(participant.email, subject, body, mail_cfg)
            log.status = "sent"
        else:
            _send_console(participant.email, subject, body)
            log.status = "simulated"

        db.session.add(log)
        db.session.commit()

    except Exception as e:
        log.status = "failed"
        log.error_message = str(e)
        db.session.add(log)
        db.session.commit()
        logger.error(f"リマインド送信失敗: {participant.email} - {e}", exc_info=True)
        raise

    return log
