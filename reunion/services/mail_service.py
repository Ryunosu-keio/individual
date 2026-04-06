"""
services/mail_service.py - メール送信サービス

MAIL_MODE=console の場合: コンソールに出力するだけ（開発用）
MAIL_MODE=smtp    の場合: SMTPで実際に送信する
MAIL_MODE=gas     の場合: GAS Webhookで送信する
"""
import json
import smtplib
import logging
import urllib.request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from flask import current_app
from extensions import db
from models import MailLog, AppSetting

logger = logging.getLogger(__name__)


# デフォルトテンプレート（DB未設定時に使用される）
MAIL_DEFAULTS = {
    "mail_final_url_subject": "【{reunion_name}】本出欠のご確認をお願いします",
    "mail_final_url_body": (
        "{name} 様\n\n"
        "いつもお世話になっております。\n"
        "{reunion_name}の幹事です。\n\n"
        "先日は仮出欠にご回答いただき、ありがとうございました。\n"
        "つきましては、本出欠のご確認をお願いいたします。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時: {reunion_date}\n"
        "会場: {reunion_venue}\n"
        "会費: {reunion_fee}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 本出欠フォーム\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "下記URLより、出欠のご回答と会費のお振込をお願いいたします。\n"
        "{final_url}\n\n"
        "※このURLはあなた専用です。他の方には共有しないでください。\n"
        "※回答は何度でも変更できます。\n\n"
        "ご不明な点がございましたら、お気軽にご連絡ください。\n"
        "皆様のご参加を心よりお待ちしております。\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事"
    ),
    "mail_reminder_subject": "【{reunion_name}】本出欠のご回答がまだお済みでない方へ（リマインド）",
    "mail_reminder_body": (
        "{name} 様\n\n"
        "いつもお世話になっております。\n"
        "{reunion_name}の幹事です。\n\n"
        "本出欠フォームをお送りしておりますが、\n"
        "まだご回答をいただけていないようでしたので、\n"
        "リマインドのご連絡を差し上げました。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時: {reunion_date}\n"
        "会場: {reunion_venue}\n"
        "会費: {reunion_fee}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 本出欠フォーム\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "下記URLより、出欠のご回答と会費のお振込をお願いいたします。\n"
        "{final_url}\n\n"
        "※このURLはあなた専用です。他の方には共有しないでください。\n"
        "※既にご回答済みの場合は、本メールは無視してください。\n\n"
        "お忙しいところ恐れ入りますが、ご確認のほどよろしくお願いいたします。\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事"
    ),
    "mail_provisional_confirm_subject": "【{reunion_name}】仮出欠を受け付けました",
    "mail_provisional_confirm_body": (
        "{name} 様\n\n"
        "{reunion_name}の幹事です。\n"
        "仮出欠のご回答ありがとうございます。\n"
        "以下の内容で受け付けました。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ ご回答内容\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "回答: {status}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時: {reunion_date}\n"
        "会場: {reunion_venue}\n"
        "会費: {reunion_fee}\n\n"
        "内容を変更する場合は、下記URLから再度ご回答ください。\n"
        "{provisional_url}\n\n"
        "※同じメールアドレスで再送信すると回答が更新されます。\n\n"
        "後日、本出欠フォームのURLを別途お送りいたします。\n"
        "引き続きよろしくお願いいたします。\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事"
    ),
    "mail_final_confirm_subject": "【{reunion_name}】本出欠を受け付けました",
    "mail_final_confirm_body": (
        "{name} 様\n\n"
        "{reunion_name}の幹事です。\n"
        "本出欠のご回答ありがとうございます。\n"
        "以下の内容で受け付けました。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ ご回答内容\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "回答: {status}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 振込のご案内\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "会費: {reunion_fee}\n"
        "振込先: {transfer_bank} {transfer_branch}支店\n"
        "口座: {transfer_account_type}口座 {transfer_account_number}\n"
        "口座名義: {transfer_account_name}\n"
        "振込期限: {transfer_deadline}\n\n"
        "※振込名義は本出欠フォームでご入力いただいた名義と\n"
        "  一致するようお願いいたします。\n"
        "※振込手数料はご負担をお願いいたします。\n\n"
        "内容を変更する場合は、下記URLから再度ご回答ください。\n"
        "{final_url}\n\n"
        "ご不明な点がございましたら、お気軽にご連絡ください。\n"
        "当日お会いできることを楽しみにしております。\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事"
    ),
}


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
        "transfer_bank":           get("transfer_bank", ""),
        "transfer_branch":         get("transfer_branch", ""),
        "transfer_account_type":   get("transfer_account_type", ""),
        "transfer_account_number": get("transfer_account_number", ""),
        "transfer_account_name":   get("transfer_account_name", ""),
        "transfer_deadline":       get("transfer_deadline", ""),
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
        _get_template('mail_final_url_subject', MAIL_DEFAULTS['mail_final_url_subject']),
        **vars
    )
    body = _render_template(
        _get_template('mail_final_url_body', MAIL_DEFAULTS['mail_final_url_body']),
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
        _get_template('mail_reminder_subject', MAIL_DEFAULTS['mail_reminder_subject']),
        **vars
    )
    body = _render_template(
        _get_template('mail_reminder_body', MAIL_DEFAULTS['mail_reminder_body']),
        **vars
    )
    return subject, body


def _send_gas(to_email: str, subject: str, body: str, from_name: str) -> None:
    """GAS Webhookを使ってメールを送信する"""
    import os
    webhook_url = os.environ.get("GAS_WEBHOOK_URL", "") or current_app.config.get("GAS_WEBHOOK_URL", "")
    secret = os.environ.get("GAS_SECRET", "") or current_app.config.get("GAS_SECRET", "")

    if not webhook_url:
        raise ValueError("GAS_WEBHOOK_URL が設定されていません")

    payload = json.dumps({
        "secret": secret,
        "to": to_email,
        "subject": subject,
        "body": body,
        "from_name": from_name,
    }).encode("utf-8")

    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        result = json.loads(res.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError(f"GAS送信エラー: {result.get('error')}")


def _send_smtp_cfg(to_email: str, subject: str, body: str, cfg: dict) -> None:
    """SMTPでメールを送信する"""
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
    """コンソールにメール内容を出力する。開発用。"""
    separator = "=" * 60
    logger.info(f"\n{separator}\n[メール送信シミュレーション]\nTo: {to_email}\nSubject: {subject}\n\n{body}\n{separator}")
    print(f"\n{separator}")
    print(f"[メール送信シミュレーション]")
    print(f"To: {to_email}")
    print(f"Subject: {subject}")
    print(f"\n{body}")
    print(f"{separator}\n")


def _get_mail_config():
    """DB設定を優先、なければ .env（current_app.config）を使う。"""
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


def _dispatch_send(to_email: str, subject: str, body: str, mail_cfg: dict) -> str:
    """モードに応じてメール送信し、ステータス文字列を返す"""
    mode = mail_cfg["mode"]
    if mode == "gas":
        _send_gas(to_email, subject, body, mail_cfg["from_name"])
        return "sent"
    elif mode == "smtp":
        _send_smtp_cfg(to_email, subject, body, mail_cfg)
        return "sent"
    else:
        _send_console(to_email, subject, body)
        return "simulated"


def send_final_url(participant, final_url: str) -> MailLog:
    """参加者に本出欠URLを送信する。"""
    mail_cfg = _get_mail_config()
    subject, body = _build_final_url_mail_body(participant.name, final_url)

    log = MailLog(
        participant_id=participant.id,
        mail_type="final_url",
        sent_at=datetime.utcnow(),
    )

    try:
        log.status = _dispatch_send(participant.email, subject, body, mail_cfg)
        if log.status == "sent":
            logger.info(f"本出欠URL送信成功: {participant.email}")
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


def _build_provisional_confirm_body(participant_name: str, status_label: str, provisional_url: str) -> tuple:
    """仮出欠送信完了メールの件名・本文を生成する"""
    reunion = _get_reunion_info()
    vars = dict(
        name=participant_name,
        status=status_label,
        provisional_url=provisional_url,
        reunion_name=reunion["reunion_name"],
        reunion_date=reunion["reunion_date"],
        reunion_venue=reunion["reunion_venue"],
        reunion_fee=reunion["reunion_fee"],
    )
    subject = _render_template(
        _get_template('mail_provisional_confirm_subject',
                      MAIL_DEFAULTS['mail_provisional_confirm_subject']),
        **vars
    )
    body = _render_template(
        _get_template('mail_provisional_confirm_body',
                      MAIL_DEFAULTS['mail_provisional_confirm_body']),
        **vars
    )
    return subject, body


def _build_final_confirm_body(participant_name: str, status_label: str, final_url: str) -> tuple:
    """本出欠送信完了メールの件名・本文を生成する"""
    reunion = _get_reunion_info()
    vars = dict(
        name=participant_name,
        status=status_label,
        final_url=final_url,
        reunion_name=reunion["reunion_name"],
        reunion_date=reunion["reunion_date"],
        reunion_venue=reunion["reunion_venue"],
        reunion_fee=reunion["reunion_fee"],
        transfer_bank=reunion["transfer_bank"],
        transfer_branch=reunion["transfer_branch"],
        transfer_account_type=reunion["transfer_account_type"],
        transfer_account_number=reunion["transfer_account_number"],
        transfer_account_name=reunion["transfer_account_name"],
        transfer_deadline=reunion["transfer_deadline"],
    )
    subject = _render_template(
        _get_template('mail_final_confirm_subject',
                      MAIL_DEFAULTS['mail_final_confirm_subject']),
        **vars
    )
    body = _render_template(
        _get_template('mail_final_confirm_body',
                      MAIL_DEFAULTS['mail_final_confirm_body']),
        **vars
    )
    return subject, body


def send_provisional_confirmation(participant, status_label: str, provisional_url: str) -> MailLog:
    """仮出欠フォーム送信完了メールを送信する。"""
    mail_cfg = _get_mail_config()
    subject, body = _build_provisional_confirm_body(participant.name, status_label, provisional_url)

    log = MailLog(
        participant_id=participant.id,
        mail_type="provisional_confirm",
        sent_at=datetime.utcnow(),
    )

    try:
        log.status = _dispatch_send(participant.email, subject, body, mail_cfg)
        if log.status == "sent":
            logger.info(f"仮出欠確認メール送信成功: {participant.email}")
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        log.status = "failed"
        log.error_message = str(e)
        db.session.add(log)
        db.session.commit()
        logger.error(f"仮出欠確認メール送信失敗: {participant.email} - {e}", exc_info=True)

    return log


def send_final_confirmation(participant, status_label: str, final_url: str) -> MailLog:
    """本出欠フォーム送信完了メールを送信する。"""
    mail_cfg = _get_mail_config()
    subject, body = _build_final_confirm_body(participant.name, status_label, final_url)

    log = MailLog(
        participant_id=participant.id,
        mail_type="final_confirm",
        sent_at=datetime.utcnow(),
    )

    try:
        log.status = _dispatch_send(participant.email, subject, body, mail_cfg)
        if log.status == "sent":
            logger.info(f"本出欠確認メール送信成功: {participant.email}")
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        log.status = "failed"
        log.error_message = str(e)
        db.session.add(log)
        db.session.commit()
        logger.error(f"本出欠確認メール送信失敗: {participant.email} - {e}", exc_info=True)

    return log


def send_reminder(participant, final_url: str) -> MailLog:
    """参加者にリマインドメールを送信する。"""
    mail_cfg = _get_mail_config()
    subject, body = _build_reminder_mail_body(participant.name, final_url)

    log = MailLog(
        participant_id=participant.id,
        mail_type="reminder",
        sent_at=datetime.utcnow(),
    )

    try:
        log.status = _dispatch_send(participant.email, subject, body, mail_cfg)
        if log.status == "sent":
            logger.info(f"リマインド送信成功: {participant.email}")
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
