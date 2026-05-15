"""
services/mail_service.py - メール送信サービス

MAIL_MODE=console の場合: コンソールに出力するだけ（開発用）
MAIL_MODE=smtp    の場合: SMTPで実際に送信する
MAIL_MODE=gas     の場合: GAS Webhookで送信する
MAIL_MODE=brevo   の場合: Brevo Transactional Email APIで送信する
"""
import json
import os
import smtplib
import logging
import urllib.request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, date
from flask import current_app
from extensions import db
from models import MailLog, AppSetting

logger = logging.getLogger(__name__)


# デフォルトテンプレート（DB未設定時に使用される）
MAIL_DEFAULTS = {
    # ── 本出欠URL送信（生徒用） ──────────────────────────────
    "mail_final_url_subject": "【{final_deadline_short} 23:59締切】【{reunion_name}】本出欠のご確認をお願いします",
    "mail_final_url_body": (
        "{name} 様\n\n"
        "お世話になっております。\n"
        "{reunion_name}幹事代表の{organizer_name}です。\n\n"
        "先日は仮出欠にご回答いただき、ありがとうございました。\n"
        "つきましては、本出欠フォームのURLをお送りします。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時　: {reunion_date} {reunion_time}\n"
        "会場　: {reunion_venue}\n"
        "服装　: {dress_code}\n"
        "持ち物: {belongings}\n"
        "会費　: {reunion_fee}円\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 本出欠フォーム\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "【回答期限: {final_deadline_short} 23:59】\n"
        "※この期限を過ぎるとフォームはロックされ、回答できなくなります。\n\n"
        "下記URLよりご回答をお願いいたします。\n"
        "ご参加の場合は会費のお振込もこちらからご確認ください。\n"
        "{final_url}\n\n"
        "※期限内であれば、不参加から参加へのご変更は可能です。\n"
        "※ただし、一度お振込いただいた会費の取り消しはできかねますのでご注意ください。\n\n"
        "ご不明な点がございましたら、このメールへのご返信にてお気軽にご連絡ください。\n（連絡先は本メール末尾に記載しています）\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事代表 {organizer_name}"
    ),
    # ── 本出欠リマインド（生徒用） ───────────────────────────
    "mail_reminder_subject": "【{final_deadline_short} 23:59締切】【{reunion_name}】本出欠のご回答をお願いします（リマインド）",
    "mail_reminder_body": (
        "{name} 様\n\n"
        "お世話になっております。\n"
        "{reunion_name}幹事代表の{organizer_name}です。\n\n"
        "先日お送りした本出欠フォームについて、改めてご連絡いたします。\n"
        "ご回答済みの方はご放念ください。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時　: {reunion_date} {reunion_time}\n"
        "会場　: {reunion_venue}\n"
        "服装　: {dress_code}\n"
        "持ち物: {belongings}\n"
        "会費　: {reunion_fee}円\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 本出欠フォーム\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "【回答期限: {final_deadline_short} 23:59】\n"
        "※この期限を過ぎるとフォームはロックされ、回答できなくなります。\n\n"
        "{final_url}\n\n"
        "お忙しいところ恐れ入りますが、よろしくお願いいたします。\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事代表 {organizer_name}"
    ),
    # ── 仮出欠 送信完了（参加予定） ──────────────────────────
    "mail_provisional_confirm_attending_subject": "【{reunion_name}】仮出欠を受け付けました",
    "mail_provisional_confirm_attending_body": (
        "{name} 様\n\n"
        "{reunion_name}幹事代表の{organizer_name}です。\n"
        "仮出欠のご回答ありがとうございます。\n"
        "以下の内容で受け付けました。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ ご回答内容\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "回答: {status}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時　: {reunion_date} {reunion_time}\n"
        "会場　: {reunion_venue}\n"
        "服装　: {dress_code}\n"
        "持ち物: {belongings}\n"
        "会費　: {reunion_fee}円\n\n"
        "内容を変更する場合は、下記URLから再度ご回答ください。\n"
        "{provisional_url}\n\n"
        "※同じメールアドレスで再送信すると回答が更新されます。\n\n"
        "後日、本出欠フォームのURLを別途お送りいたします。\n"
        "引き続きよろしくお願いいたします。\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事代表 {organizer_name}"
    ),
    # ── 仮出欠 送信完了（不参加） ────────────────────────────
    "mail_provisional_confirm_not_attending_subject": "【{reunion_name}】仮出欠を受け付けました",
    "mail_provisional_confirm_not_attending_body": (
        "{name} 様\n\n"
        "{reunion_name}幹事代表の{organizer_name}です。\n"
        "仮出欠のご回答ありがとうございます。\n"
        "以下の内容で受け付けました。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ ご回答内容\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "回答: {status}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時　: {reunion_date} {reunion_time}\n"
        "会場　: {reunion_venue}\n"
        "服装　: {dress_code}\n"
        "持ち物: {belongings}\n"
        "会費　: {reunion_fee}円\n\n"
        "内容を変更する場合は、下記URLから再度ご回答ください。\n"
        "{provisional_url}\n\n"
        "※同じメールアドレスで再送信すると回答が更新されます。\n\n"
        "引き続きよろしくお願いいたします。\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事代表 {organizer_name}"
    ),
    # ── 仮出欠 送信完了（未定） ──────────────────────────────
    "mail_provisional_confirm_undecided_subject": "【{reunion_name}】仮出欠を受け付けました",
    "mail_provisional_confirm_undecided_body": (
        "{name} 様\n\n"
        "{reunion_name}幹事代表の{organizer_name}です。\n"
        "仮出欠のご回答ありがとうございます。\n"
        "以下の内容で受け付けました。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ ご回答内容\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "回答: {status}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時　: {reunion_date} {reunion_time}\n"
        "会場　: {reunion_venue}\n"
        "服装　: {dress_code}\n"
        "持ち物: {belongings}\n"
        "会費　: {reunion_fee}円\n\n"
        "内容を変更する場合は、下記URLから再度ご回答ください。\n"
        "{provisional_url}\n\n"
        "※同じメールアドレスで再送信すると回答が更新されます。\n\n"
        "後日、本出欠フォームのURLを別途お送りいたします。\n"
        "引き続きよろしくお願いいたします。\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事代表 {organizer_name}"
    ),
    # ── 最終リマインド（生徒用） ─────────────────────────────
    "mail_final_reminder_subject": "【{reunion_name}】開催のご案内（最終リマインド）",
    "mail_final_reminder_body": (
        "{name} 様\n\n"
        "お世話になっております。\n"
        "{reunion_name}幹事代表の{organizer_name}です。\n\n"
        "本出欠にてご参加のご回答をいただき、ありがとうございます。\n"
        "開催が近づいてまいりましたので、最終のご案内をお送りいたします。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時　: {reunion_date} {reunion_time}\n"
        "会場　: {reunion_venue}\n"
        "服装　: {dress_code}\n"
        "持ち物: {belongings}\n"
        "会費　: {reunion_fee}円\n\n"
        "※詳細は添付のご案内PDFをご確認ください。\n"
        "※やむを得ずキャンセルされる場合は{final_reminder_deadline_short} 23:59までにこのメールへ返信してご連絡ください。\n\n"
        "当日お会いできることを楽しみにしております。\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事代表 {organizer_name}"
    ),
    # ── 仮出欠リマインド ─────────────────────────────────
    "mail_provisional_reminder_subject": "【{reunion_name}】仮出欠のご回答をお願いします（リマインド）",
    "mail_provisional_reminder_body": (
        "{name} 様\n\n"
        "{reunion_name}幹事代表の{organizer_name}です。\n\n"
        "先日ご案内しました仮出欠フォームへのご回答がまだのようでしたので、\n"
        "リマインドのご連絡を差し上げました。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 仮出欠フォーム\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "{provisional_url}\n\n"
        "{deadline_line}"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時　: {reunion_date} {reunion_time}\n"
        "会場　: {reunion_venue}\n"
        "服装　: {dress_code}\n"
        "持ち物: {belongings}\n"
        "会費　: {reunion_fee}円\n\n"
        "お忙しいところ恐れ入りますが、ご確認のほどよろしくお願いいたします。\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事代表 {organizer_name}"
    ),
    # ── 先生向けテンプレート ──────────────────────────────
    "mail_final_url_subject_teacher": "【{final_deadline_short} 23:59締切】【{reunion_name}】ご出席のご確認をお願いいたします",
    "mail_final_url_body_teacher": (
        "{name} 先生\n\n"
        "お世話になっております。\n"
        "{reunion_name}幹事代表の{organizer_name}です。\n\n"
        "このたびは{reunion_name}を開催する運びとなりました。\n"
        "先生にもぜひご出席いただければ、生徒一同大変嬉しく思います。\n\n"
        "ご多用の中大変恐れ入りますが、ご都合がよろしければ\n"
        "下記URLよりご出欠のご確認をお願いいたします。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時　: {reunion_date} {reunion_time}\n"
        "会場　: {reunion_venue}\n"
        "服装　: {dress_code}\n"
        "持ち物: {belongings}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 出欠フォーム\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "【回答期限: {final_deadline_short} 23:59】\n"
        "※この期限を過ぎるとフォームはロックされ、回答できなくなります。\n\n"
        "{final_url}\n\n"
        "先生のご出席を心よりお待ちしております。\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事代表 {organizer_name}"
    ),
    "mail_reminder_subject_teacher": "【{final_deadline_short} 23:59締切】【{reunion_name}】ご出席のご確認（リマインド）",
    "mail_reminder_body_teacher": (
        "{name} 先生\n\n"
        "お世話になっております。\n"
        "{reunion_name}幹事代表の{organizer_name}です。\n\n"
        "先日お送りした出欠フォームについて、改めてご連絡いたします。\n"
        "ご回答済みの場合はご放念ください。\n\n"
        "ご多用の中お手数をおかけして恐れ入りますが、\n"
        "ご都合がよろしければ下記URLよりご確認をお願いいたします。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時　: {reunion_date} {reunion_time}\n"
        "会場　: {reunion_venue}\n"
        "服装　: {dress_code}\n"
        "持ち物: {belongings}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 出欠フォーム\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "【回答期限: {final_deadline_short} 23:59】\n"
        "※この期限を過ぎるとフォームはロックされ、回答できなくなります。\n\n"
        "{final_url}\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事代表 {organizer_name}"
    ),
    "mail_final_reminder_subject_teacher": "【{reunion_name}】開催のご案内（最終リマインド）",
    "mail_final_reminder_body_teacher": (
        "{name} 先生\n\n"
        "お世話になっております。\n"
        "{reunion_name}幹事代表の{organizer_name}です。\n\n"
        "ご出席のご回答をいただき、誠にありがとうございます。\n"
        "開催が近づいてまいりましたので、最終のご案内をお送りいたします。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時　: {reunion_date} {reunion_time}\n"
        "会場　: {reunion_venue}\n"
        "服装　: {dress_code}\n"
        "持ち物: {belongings}\n\n"
        "※詳細は添付のご案内PDFをご確認ください。\n"
        "※やむを得ずご欠席される場合は{final_reminder_deadline_short} 23:59までにこのメールへ返信してご連絡ください。\n\n"
        "先生にお会いできることを楽しみにしております。\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事代表 {organizer_name}"
    ),
    # ── 仮出欠 送信完了（参加予定・先生用） ───────────────────
    "mail_provisional_confirm_attending_subject_teacher": "【{reunion_name}】仮出欠を受け付けました",
    "mail_provisional_confirm_attending_body_teacher": (
        "{name} 先生\n\n"
        "{reunion_name}幹事代表の{organizer_name}です。\n"
        "仮出欠のご回答をいただき、誠にありがとうございます。\n"
        "以下の内容で受け付けました。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ ご回答内容\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "回答: {status}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時　: {reunion_date} {reunion_time}\n"
        "会場　: {reunion_venue}\n"
        "服装　: {dress_code}\n"
        "持ち物: {belongings}\n"
        "会費　: {reunion_fee}円\n\n"
        "内容を変更する場合は、下記URLから再度ご回答ください。\n"
        "{provisional_url}\n\n"
        "後日、本出欠フォームのURLを別途お送りいたします。\n"
        "引き続きよろしくお願いいたします。\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事代表 {organizer_name}"
    ),
    # ── 仮出欠 送信完了（不参加・先生用） ─────────────────────
    "mail_provisional_confirm_not_attending_subject_teacher": "【{reunion_name}】仮出欠を受け付けました",
    "mail_provisional_confirm_not_attending_body_teacher": (
        "{name} 先生\n\n"
        "{reunion_name}幹事代表の{organizer_name}です。\n"
        "仮出欠のご回答をいただき、誠にありがとうございます。\n"
        "以下の内容で受け付けました。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ ご回答内容\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "回答: {status}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時　: {reunion_date} {reunion_time}\n"
        "会場　: {reunion_venue}\n"
        "服装　: {dress_code}\n"
        "持ち物: {belongings}\n"
        "会費　: {reunion_fee}円\n\n"
        "内容を変更する場合は、下記URLから再度ご回答ください。\n"
        "{provisional_url}\n\n"
        "引き続きよろしくお願いいたします。\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事代表 {organizer_name}"
    ),
    # ── 仮出欠 送信完了（未定・先生用） ───────────────────────
    "mail_provisional_confirm_undecided_subject_teacher": "【{reunion_name}】仮出欠を受け付けました",
    "mail_provisional_confirm_undecided_body_teacher": (
        "{name} 先生\n\n"
        "{reunion_name}幹事代表の{organizer_name}です。\n"
        "仮出欠のご回答をいただき、誠にありがとうございます。\n"
        "以下の内容で受け付けました。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ ご回答内容\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "回答: {status}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時　: {reunion_date} {reunion_time}\n"
        "会場　: {reunion_venue}\n"
        "服装　: {dress_code}\n"
        "持ち物: {belongings}\n"
        "会費　: {reunion_fee}円\n\n"
        "内容を変更する場合は、下記URLから再度ご回答ください。\n"
        "{provisional_url}\n\n"
        "後日、本出欠フォームのURLを別途お送りいたします。\n"
        "引き続きよろしくお願いいたします。\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事代表 {organizer_name}"
    ),
    # ── 本出欠 送信完了（参加・先生用） ───────────────────────
    "mail_final_confirm_attending_subject_teacher": "【{reunion_name}】出欠を受け付けました",
    "mail_final_confirm_attending_body_teacher": (
        "{name} 先生\n\n"
        "{reunion_name}幹事代表の{organizer_name}です。\n"
        "ご出欠のご回答をいただき、誠にありがとうございます。\n"
        "以下の内容で受け付けました。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ ご回答内容\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "回答: {status}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時　: {reunion_date} {reunion_time}\n"
        "会場　: {reunion_venue}\n"
        "服装　: {dress_code}\n"
        "持ち物: {belongings}\n\n"
        "内容を変更する場合は、下記URLから再度ご回答ください。\n"
        "{final_url}\n\n"
        "ご不明な点がございましたら、このメールへのご返信にてお気軽にご連絡ください。\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事代表 {organizer_name}"
    ),
    # ── 本出欠 送信完了（不参加・先生用） ─────────────────────
    "mail_final_confirm_not_attending_subject_teacher": "【{reunion_name}】出欠を受け付けました",
    "mail_final_confirm_not_attending_body_teacher": (
        "{name} 先生\n\n"
        "{reunion_name}幹事代表の{organizer_name}です。\n"
        "ご出欠のご回答をいただき、誠にありがとうございます。\n"
        "以下の内容で受け付けました。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ ご回答内容\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "回答: {status}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時　: {reunion_date} {reunion_time}\n"
        "会場　: {reunion_venue}\n"
        "服装　: {dress_code}\n"
        "持ち物: {belongings}\n\n"
        "内容を変更する場合は、下記URLから再度ご回答ください。\n"
        "{final_url}\n\n"
        "ご不明な点がございましたら、このメールへのご返信にてお気軽にご連絡ください。\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事代表 {organizer_name}"
    ),
    # ── ここまで先生向けテンプレート ─────────────────────────

    # ── 本出欠 送信完了（参加） ──────────────────────────────
    "mail_final_confirm_attending_subject": "【{reunion_name}】本出欠を受け付けました",
    "mail_final_confirm_attending_body": (
        "{name} 様\n\n"
        "{reunion_name}幹事代表の{organizer_name}です。\n"
        "本出欠のご回答ありがとうございます。\n"
        "以下の内容で受け付けました。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ ご回答内容\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "回答: {status}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時　: {reunion_date} {reunion_time}\n"
        "会場　: {reunion_venue}\n"
        "服装　: {dress_code}\n"
        "持ち物: {belongings}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 振込のご案内\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "会費　　: {reunion_fee}円\n"
        "振込先　: {transfer_bank} {transfer_branch}（支店番号: {transfer_branch_number}）\n"
        "口座　　: {transfer_account_type}口座 {transfer_account_number}\n"
        "口座名義: {transfer_account_name}\n"
        "振込期限: {transfer_deadline}\n\n"
        "内容を変更する場合は、下記URLから再度ご回答ください。\n"
        "{final_url}\n\n"
        "ご不明な点がございましたら、このメールへのご返信にてお気軽にご連絡ください。\n（連絡先は本メール末尾に記載しています）\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事代表 {organizer_name}"
    ),
    # ── 本出欠 送信完了（不参加） ────────────────────────────
    "mail_final_confirm_not_attending_subject": "【{reunion_name}】本出欠を受け付けました",
    "mail_final_confirm_not_attending_body": (
        "{name} 様\n\n"
        "{reunion_name}幹事代表の{organizer_name}です。\n"
        "本出欠のご回答ありがとうございます。\n"
        "以下の内容で受け付けました。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ ご回答内容\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "回答: {status}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "■ 同窓会の詳細\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "日時　: {reunion_date} {reunion_time}\n"
        "会場　: {reunion_venue}\n"
        "服装　: {dress_code}\n"
        "持ち物: {belongings}\n\n"
        "内容を変更する場合は、下記URLから再度ご回答ください。\n"
        "{final_url}\n\n"
        "ご不明な点がございましたら、このメールへのご返信にてお気軽にご連絡ください。\n（連絡先は本メール末尾に記載しています）\n\n"
        "──────────────────\n"
        "{reunion_name} 幹事代表 {organizer_name}"
    ),
}


def _is_teacher(role: str) -> bool:
    return role in ("教師", "学年主任", "副担任")


def _text_to_html(body: str) -> str:
    """プレーンテキストのメール本文をHTML形式に変換する"""
    import html as _html
    lines = body.split('\n')
    parts = []
    for line in lines:
        e = _html.escape(line)
        if not e:
            parts.append('<br>')
        elif '━' in e:
            parts.append('<hr style="border:none;border-top:1px solid #ddd;margin:10px 0;">')
        elif e.startswith('■ '):
            parts.append(f'<p style="font-weight:bold;margin:14px 0 4px;color:#333;">{e[2:]}</p>')
        elif e.startswith('──'):
            parts.append('<hr style="border:none;border-top:1px solid #eee;margin:12px 0;">')
        elif e.startswith('【') and '】' in e:
            parts.append(
                f'<p style="background:#fff3cd;border-left:4px solid #ffc107;'
                f'padding:8px 12px;margin:8px 0;font-weight:bold;border-radius:3px;">'
                f'⚠ {e}</p>'
            )
        elif e.startswith('※'):
            parts.append(f'<p style="color:#666;font-size:0.88em;margin:4px 0;">{e}</p>')
        else:
            parts.append(f'<p style="margin:4px 0;">{e}</p>')
    html_body = '\n'.join(parts)
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
        '<body style="font-family:sans-serif;line-height:1.7;max-width:600px;'
        'margin:0 auto;padding:20px;color:#333;">'
        f'{html_body}'
        '</body></html>'
    )


def _format_deadline_short(deadline: str) -> str:
    """ISO日付を m/d 形式に変換する。未設定なら空文字。"""
    if not deadline:
        return ""
    try:
        from datetime import date as _date
        d = _date.fromisoformat(deadline)
        return f"{d.month}/{d.day}"
    except ValueError:
        return ""


def _format_deadline_jp(deadline: str) -> str:
    """ISO日付を M月D日 形式に変換する。未設定なら空文字。"""
    if not deadline:
        return ""
    try:
        from datetime import date as _date
        d = _date.fromisoformat(deadline)
        return f"{d.month}月{d.day}日"
    except ValueError:
        return deadline


def _render_template(template: str, **kwargs) -> str:
    """テンプレート文字列の {変数} を置換し、空になったブラケットを除去する"""
    import re
    for key, val in kwargs.items():
        template = template.replace("{" + key + "}", str(val))
    # 期限未設定時に残る空パターンを除去
    template = re.sub(r'【\s*23:59締切】', '', template)
    template = re.sub(r'【\s*23:59までに】', '', template)
    template = re.sub(r'※回答期限（\s*23:59）まで[^\n]*\n', '', template)
    template = re.sub(r'※ご回答期限（\s*23:59）まで[^\n]*\n', '', template)
    template = re.sub(r'※やむを得ず[^\n]*23:59まで[^\n]*ご連絡ください。\n', lambda m: m.group() if '/' in m.group() else '', template)
    # 署名末尾スペース除去
    template = re.sub(r' +\n', '\n', template)
    template = template.rstrip() + '\n' if template.strip() else template
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
    info = {
        "reunion_name":     get("reunion_name",     cfg.get("REUNION_NAME", "同窓会")),
        "reunion_date":     get("reunion_date",     cfg.get("REUNION_DATE", "")),
        "reunion_time":     get("reunion_time",     cfg.get("REUNION_TIME", "")),
        "reunion_venue":    get("reunion_venue",    cfg.get("REUNION_VENUE", "")),
        "reunion_fee":      get("reunion_fee",      cfg.get("REUNION_FEE", "")),
        "dress_code":       get("dress_code",       cfg.get("DRESS_CODE", "")),
        "belongings":       get("belongings",       cfg.get("BELONGINGS", "")),
        "provisional_deadline":    get("provisional_deadline",    cfg.get("PROVISIONAL_DEADLINE", "")),
        "transfer_bank":           get("transfer_bank",           cfg.get("TRANSFER_BANK", "")),
        "transfer_branch":         get("transfer_branch",         cfg.get("TRANSFER_BRANCH", "")),
        "transfer_branch_number":  get("transfer_branch_number",  cfg.get("TRANSFER_BRANCH_NUMBER", "")),
        "transfer_account_type":   get("transfer_account_type",   cfg.get("TRANSFER_ACCOUNT_TYPE", "")),
        "transfer_account_number": get("transfer_account_number", cfg.get("TRANSFER_ACCOUNT_NUMBER", "")),
        "transfer_account_name":   get("transfer_account_name",   cfg.get("TRANSFER_ACCOUNT_NAME", "")),
        "transfer_deadline":       get("transfer_deadline",       cfg.get("TRANSFER_DEADLINE", "")),
        "organizer_name":          get("organizer_name",          cfg.get("ORGANIZER_NAME", "")),
        "final_deadline":          get("final_deadline",          cfg.get("FINAL_DEADLINE", "")),
        "reminder_send_date":      get("reminder_send_date",      cfg.get("REMINDER_SEND_DATE", "")),
        "final_reminder_deadline": get("final_reminder_deadline", cfg.get("FINAL_REMINDER_DEADLINE", "")),
    }
    info["final_deadline_short"]          = _format_deadline_short(info["final_deadline"])
    info["final_reminder_deadline_short"] = _format_deadline_short(info["final_reminder_deadline"])
    return info


def _build_final_url_mail_body(participant_name: str, final_url: str, role: str = "") -> tuple:
    """本出欠URL送信メールの件名・本文を生成する（DBテンプレート優先）"""
    reunion = _get_reunion_info()
    teacher = _is_teacher(role)
    vars = dict(
        name=participant_name,
        final_url=final_url,
        reunion_name=reunion["reunion_name"],
        reunion_date=reunion["reunion_date"],
        reunion_time=reunion["reunion_time"],
        reunion_venue=reunion["reunion_venue"],
        reunion_fee=reunion["reunion_fee"],
        dress_code=reunion["dress_code"],
        belongings=reunion["belongings"],
        organizer_name=reunion["organizer_name"],

        final_deadline=reunion["final_deadline"],
        final_deadline_short=reunion["final_deadline_short"],
        final_reminder_deadline=reunion["final_reminder_deadline"],
        final_reminder_deadline_short=reunion["final_reminder_deadline_short"],
    )
    s_key = 'mail_final_url_subject_teacher' if teacher else 'mail_final_url_subject'
    b_key = 'mail_final_url_body_teacher'    if teacher else 'mail_final_url_body'
    subject = _render_template(_get_template(s_key, MAIL_DEFAULTS[s_key]), **vars)
    body    = _render_template(_get_template(b_key, MAIL_DEFAULTS[b_key]), **vars)
    return subject, body


def _build_reminder_mail_body(participant_name: str, final_url: str, role: str = "") -> tuple:
    """リマインドメールの件名・本文を生成する（DBテンプレート優先）"""
    reunion = _get_reunion_info()
    teacher = _is_teacher(role)
    vars = dict(
        name=participant_name,
        final_url=final_url,
        reunion_name=reunion["reunion_name"],
        reunion_date=reunion["reunion_date"],
        reunion_time=reunion["reunion_time"],
        reunion_venue=reunion["reunion_venue"],
        reunion_fee=reunion["reunion_fee"],
        dress_code=reunion["dress_code"],
        belongings=reunion["belongings"],
        organizer_name=reunion["organizer_name"],

        final_deadline=reunion["final_deadline"],
        final_deadline_short=reunion["final_deadline_short"],
        final_reminder_deadline=reunion["final_reminder_deadline"],
        final_reminder_deadline_short=reunion["final_reminder_deadline_short"],
    )
    s_key = 'mail_reminder_subject_teacher' if teacher else 'mail_reminder_subject'
    b_key = 'mail_reminder_body_teacher'    if teacher else 'mail_reminder_body'
    subject = _render_template(_get_template(s_key, MAIL_DEFAULTS[s_key]), **vars)
    body    = _render_template(_get_template(b_key, MAIL_DEFAULTS[b_key]), **vars)
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


def _send_smtp_cfg(to_email: str, subject: str, body: str, cfg: dict, attachment_path: str = None) -> None:
    """SMTPでメールを送信する"""
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = f"{cfg['from_name']} <{cfg['from_addr']}>"
    msg["To"] = to_email
    msg["Reply-To"] = cfg["from_addr"]

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body, "plain", "utf-8"))
    alt.attach(MIMEText(_text_to_html(body), "html", "utf-8"))
    msg.attach(alt)

    if attachment_path and os.path.isfile(attachment_path):
        with open(attachment_path, "rb") as f:
            attach = MIMEBase("application", "octet-stream")
            attach.set_payload(f.read())
        encoders.encode_base64(attach)
        filename = os.path.basename(attachment_path)
        attach.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(attach)

    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as server:
        server.ehlo()
        server.starttls()
        server.login(cfg["smtp_user"], cfg["smtp_password"])
        server.sendmail(cfg["from_addr"], [to_email], msg.as_string())


def _send_brevo(to_email: str, subject: str, body: str, cfg: dict) -> None:
    """Brevo Transactional Email APIでメールを送信する"""
    api_key = cfg.get("brevo_api_key", "") or os.environ.get("BREVO_API_KEY", "") or current_app.config.get("BREVO_API_KEY", "")
    if not api_key:
        raise ValueError("BREVO_API_KEY が設定されていません")

    payload = json.dumps({
        "sender":      {"name": cfg["from_name"], "email": cfg["from_addr"]},
        "to":          [{"email": to_email}],
        "replyTo":     {"email": cfg["from_addr"]},
        "subject":     subject,
        "htmlContent": _text_to_html(body),
        "textContent": body,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "api-key":       api_key,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        if res.status not in (200, 201):
            raise RuntimeError(f"Brevo APIエラー: HTTP {res.status}")


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
        "brevo_api_key": get("brevo_api_key",      cfg.get("BREVO_API_KEY", "")),
        "smtp_host":     get("mail_smtp_host",     cfg.get("MAIL_SMTP_HOST", "smtp.gmail.com")),
        "smtp_port": int(get("mail_smtp_port",     str(cfg.get("MAIL_SMTP_PORT", 587)))),
        "smtp_user":     get("mail_smtp_user",     cfg.get("MAIL_SMTP_USER", "")),
        "smtp_password": get("mail_smtp_password", cfg.get("MAIL_SMTP_PASSWORD", "")),
        "from_addr":     get("mail_from",          cfg.get("MAIL_FROM", "")),
        "from_name":     get("mail_from_name",     cfg.get("MAIL_FROM_NAME", "同窓会幹事")),
    }


def _dispatch_send(to_email: str, subject: str, body: str, mail_cfg: dict, attachment_path: str = None) -> str:
    """モードに応じてメール送信し、ステータス文字列を返す"""
    from_addr = mail_cfg.get("from_addr", "")
    if from_addr:
        body = body.rstrip("\n") + f"\nE-mail: {from_addr}\n"
    mode = mail_cfg["mode"]
    if mode == "gas":
        _send_gas(to_email, subject, body, mail_cfg["from_name"])
        return "sent"
    elif mode == "smtp":
        _send_smtp_cfg(to_email, subject, body, mail_cfg, attachment_path=attachment_path)
        return "sent"
    elif mode == "brevo":
        _send_brevo(to_email, subject, body, mail_cfg)
        return "sent"
    else:
        if attachment_path:
            logger.info(f"[添付ファイル] {attachment_path}")
        _send_console(to_email, subject, body)
        return "simulated"


def send_final_url(participant, final_url: str) -> MailLog:
    """参加者に本出欠URLを送信する。"""
    mail_cfg = _get_mail_config()
    subject, body = _build_final_url_mail_body(participant.display_name, final_url, role=participant.role or "")

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


def _build_provisional_confirm_body(participant_name: str, status_label: str, provisional_url: str, status: str = "attending", role: str = "") -> tuple:
    """仮出欠送信完了メールの件名・本文を生成する（status・roleでテンプレートを切り替え）"""
    key_suffix = {"attending": "attending", "not_attending": "not_attending"}.get(status, "undecided")
    teacher_suffix = "_teacher" if _is_teacher(role) else ""
    s_key = f"mail_provisional_confirm_{key_suffix}_subject{teacher_suffix}"
    b_key = f"mail_provisional_confirm_{key_suffix}_body{teacher_suffix}"
    reunion = _get_reunion_info()
    vars = dict(
        name=participant_name,
        status=status_label,
        provisional_url=provisional_url,
        reunion_name=reunion["reunion_name"],
        reunion_date=reunion["reunion_date"],
        reunion_time=reunion["reunion_time"],
        reunion_venue=reunion["reunion_venue"],
        reunion_fee=reunion["reunion_fee"],
        dress_code=reunion["dress_code"],
        belongings=reunion["belongings"],
        organizer_name=reunion["organizer_name"],
        final_deadline=reunion["final_deadline"],
        final_deadline_short=reunion["final_deadline_short"],
        final_reminder_deadline=reunion["final_reminder_deadline"],
        final_reminder_deadline_short=reunion["final_reminder_deadline_short"],
    )
    subject = _render_template(_get_template(s_key, MAIL_DEFAULTS[s_key]), **vars)
    body    = _render_template(_get_template(b_key, MAIL_DEFAULTS[b_key]), **vars)
    return subject, body


def _build_final_confirm_body(participant_name: str, status_label: str, final_url: str, status: str = "attending", role: str = "") -> tuple:
    """本出欠送信完了メールの件名・本文を生成する（status・roleでテンプレートを切り替え）"""
    key_suffix = "attending" if status == "attending" else "not_attending"
    teacher_suffix = "_teacher" if _is_teacher(role) else ""
    s_key = f"mail_final_confirm_{key_suffix}_subject{teacher_suffix}"
    b_key = f"mail_final_confirm_{key_suffix}_body{teacher_suffix}"
    reunion = _get_reunion_info()
    vars = dict(
        name=participant_name,
        status=status_label,
        final_url=final_url,
        reunion_name=reunion["reunion_name"],
        reunion_date=reunion["reunion_date"],
        reunion_time=reunion["reunion_time"],
        reunion_venue=reunion["reunion_venue"],
        reunion_fee=reunion["reunion_fee"],
        dress_code=reunion["dress_code"],
        belongings=reunion["belongings"],
        organizer_name=reunion["organizer_name"],
        transfer_bank=reunion["transfer_bank"],
        transfer_branch=reunion["transfer_branch"],
        transfer_branch_number=reunion["transfer_branch_number"],
        transfer_account_type=reunion["transfer_account_type"],
        transfer_account_number=reunion["transfer_account_number"],
        transfer_account_name=reunion["transfer_account_name"],
        transfer_deadline=reunion["transfer_deadline"],
        final_deadline=reunion["final_deadline"],
        final_deadline_short=reunion["final_deadline_short"],
        final_reminder_deadline=reunion["final_reminder_deadline"],
        final_reminder_deadline_short=reunion["final_reminder_deadline_short"],
    )
    subject = _render_template(_get_template(s_key, MAIL_DEFAULTS[s_key]), **vars)
    body    = _render_template(_get_template(b_key, MAIL_DEFAULTS[b_key]), **vars)
    return subject, body


def _build_provisional_reminder_body(participant_name: str, provisional_url: str) -> tuple:
    """仮出欠リマインドメールの件名・本文を生成する"""
    reunion = _get_reunion_info()
    deadline = _format_deadline_jp(reunion.get("provisional_deadline", ""))
    deadline_line = f"※ 回答期限: {deadline}\n\n" if deadline else ""
    vars = dict(
        name=participant_name,
        provisional_url=provisional_url,
        deadline_line=deadline_line,
        reunion_name=reunion["reunion_name"],
        reunion_date=reunion["reunion_date"],
        reunion_time=reunion["reunion_time"],
        reunion_venue=reunion["reunion_venue"],
        reunion_fee=reunion["reunion_fee"],
        dress_code=reunion["dress_code"],
        belongings=reunion["belongings"],
        organizer_name=reunion["organizer_name"],

        final_deadline=reunion["final_deadline"],
        final_deadline_short=reunion["final_deadline_short"],
        final_reminder_deadline=reunion["final_reminder_deadline"],
        final_reminder_deadline_short=reunion["final_reminder_deadline_short"],
    )
    subject = _render_template(
        _get_template('mail_provisional_reminder_subject',
                      MAIL_DEFAULTS['mail_provisional_reminder_subject']),
        **vars
    )
    body = _render_template(
        _get_template('mail_provisional_reminder_body',
                      MAIL_DEFAULTS['mail_provisional_reminder_body']),
        **vars
    )
    return subject, body


def send_provisional_reminder(participant, provisional_url: str) -> MailLog:
    """仮出欠リマインドメールを送信する。"""
    mail_cfg = _get_mail_config()
    subject, body = _build_provisional_reminder_body(participant.name, provisional_url)

    log = MailLog(
        participant_id=participant.id,
        mail_type="provisional_reminder",
        sent_at=datetime.utcnow(),
    )

    try:
        log.status = _dispatch_send(participant.email, subject, body, mail_cfg)
        if log.status == "sent":
            logger.info(f"仮出欠リマインド送信成功: {participant.email}")
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        log.status = "failed"
        log.error_message = str(e)
        db.session.add(log)
        db.session.commit()
        logger.error(f"仮出欠リマインド送信失敗: {participant.email} - {e}", exc_info=True)
        raise

    return log


def send_provisional_confirmation(participant, status_label: str, provisional_url: str, status: str = "attending") -> MailLog:
    """仮出欠フォーム送信完了メールを送信する。"""
    mail_cfg = _get_mail_config()
    subject, body = _build_provisional_confirm_body(participant.name, status_label, provisional_url, status, participant.role or "")

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


def send_final_confirmation(participant, status_label: str, final_url: str, status: str = "attending") -> MailLog:
    """本出欠フォーム送信完了メールを送信する。"""
    mail_cfg = _get_mail_config()
    subject, body = _build_final_confirm_body(participant.display_name, status_label, final_url, status, participant.role or "")

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
    subject, body = _build_reminder_mail_body(participant.display_name, final_url, role=participant.role or "")

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


def _build_final_reminder_body(participant_name: str, role: str = "") -> tuple:
    """最終リマインドメールの件名・本文を生成する"""
    reunion = _get_reunion_info()
    teacher = _is_teacher(role)
    vars = dict(
        name=participant_name,
        reunion_name=reunion["reunion_name"],
        reunion_date=reunion["reunion_date"],
        reunion_time=reunion["reunion_time"],
        reunion_venue=reunion["reunion_venue"],
        reunion_fee=reunion["reunion_fee"],
        dress_code=reunion["dress_code"],
        belongings=reunion["belongings"],
        organizer_name=reunion["organizer_name"],

        final_deadline=reunion["final_deadline"],
        final_deadline_short=reunion["final_deadline_short"],
        final_reminder_deadline=reunion["final_reminder_deadline"],
        final_reminder_deadline_short=reunion["final_reminder_deadline_short"],
    )
    s_key = 'mail_final_reminder_subject_teacher' if teacher else 'mail_final_reminder_subject'
    b_key = 'mail_final_reminder_body_teacher'    if teacher else 'mail_final_reminder_body'
    subject = _render_template(_get_template(s_key, MAIL_DEFAULTS[s_key]), **vars)
    body    = _render_template(_get_template(b_key, MAIL_DEFAULTS[b_key]), **vars)
    return subject, body


def send_final_reminder(participant, attachment_path: str = None) -> MailLog:
    """本出欠参加者に最終リマインドメール（PDF添付）を送信する。"""
    mail_cfg = _get_mail_config()
    subject, body = _build_final_reminder_body(participant.display_name, role=participant.role or "")

    log = MailLog(
        participant_id=participant.id,
        mail_type="final_reminder",
        sent_at=datetime.utcnow(),
    )

    try:
        log.status = _dispatch_send(participant.email, subject, body, mail_cfg,
                                    attachment_path=attachment_path)
        if log.status == "sent":
            logger.info(f"最終リマインド送信成功: {participant.email}")
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        log.status = "failed"
        log.error_message = str(e)
        db.session.add(log)
        db.session.commit()
        logger.error(f"最終リマインド送信失敗: {participant.email} - {e}", exc_info=True)
        raise

    return log


def get_daily_send_limit() -> int:
    """1日のメール送信制限数を取得する"""
    s = AppSetting.query.filter_by(key="mail_daily_limit").first()
    if s and s.value:
        try:
            return int(s.value)
        except ValueError:
            pass
    return 100  # デフォルト100件/日


def get_today_sent_count() -> int:
    """今日送信済みのメール件数を取得する"""
    today_start = datetime.combine(date.today(), datetime.min.time())
    return MailLog.query.filter(
        MailLog.sent_at >= today_start,
        MailLog.status.in_(["sent", "simulated"]),
    ).count()


def get_remaining_today() -> int:
    """今日の残り送信可能件数を取得する"""
    return max(0, get_daily_send_limit() - get_today_sent_count())
