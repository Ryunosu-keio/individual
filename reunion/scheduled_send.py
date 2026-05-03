"""
scheduled_send.py - 毎日の自動メール送信スクリプト

PythonAnywhereのScheduled Tasksに登録して毎日実行する。
1日の送信上限まで未送信の参加者にメールを送る。

PythonAnywhere設定例:
  コマンド: cd /home/ユーザー名/reunion && python scheduled_send.py
  時刻: 09:00 UTC（日本時間18:00）

対応する送信タイプ:
  final_url      ... 本出欠URL送信（仮参加かつ未送信の人）
  reminder       ... リマインド（本出欠URL送信済み＆本出欠未回答の人）
  final_reminder ... 最終リマインド（本参加者にPDF添付）

実行時に --type で指定する。デフォルトは final_url。

使い方:
  python scheduled_send.py                    # 本出欠URLを送信
  python scheduled_send.py --type reminder    # リマインドを送信
  python scheduled_send.py --type final_reminder  # 最終リマインドを送信
  python scheduled_send.py --dry-run          # 送信せず対象者だけ表示
"""
import argparse
import logging
import os
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def get_targets(send_type, Participant, MailLog):
    participants = Participant.query.filter(
        ~Participant.email.like("%@placeholder.local"),
    ).all()

    targets = []
    if send_type == "final_url":
        for p in participants:
            prov = p.latest_provisional
            if prov and prov.status == "attending":
                has_sent = any(
                    ml.mail_type == "final_url" and ml.status in ("sent", "simulated")
                    for ml in p.mail_logs
                )
                if not has_sent:
                    targets.append(p)

    elif send_type == "reminder":
        for p in participants:
            has_sent = any(
                ml.mail_type == "final_url" and ml.status in ("sent", "simulated")
                for ml in p.mail_logs
            )
            if has_sent and p.latest_final is None:
                targets.append(p)

    elif send_type == "final_reminder":
        for p in participants:
            final = p.latest_final
            if final and final.status == "attending":
                has_final_reminder = any(
                    ml.mail_type == "final_reminder" and ml.status in ("sent", "simulated")
                    for ml in p.mail_logs
                )
                if not has_final_reminder:
                    targets.append(p)

    return targets


def run(send_type, dry_run=False):
    from app import create_app
    app = create_app()

    with app.app_context():
        from models import Participant, MailLog, AppSetting
        from services.mail_service import (
            send_final_url, send_reminder, send_final_reminder,
            get_daily_send_limit, get_today_sent_count, get_remaining_today,
        )
        from services.token_service import generate_final_url

        base_url = app.config.get("APP_BASE_URL", "http://localhost:5000")
        targets = get_targets(send_type, Participant, MailLog)
        remaining = get_remaining_today()
        daily_limit = get_daily_send_limit()
        today_sent = get_today_sent_count()

        logger.info(f"送信タイプ: {send_type}")
        logger.info(f"対象者数: {len(targets)}")
        logger.info(f"本日送信済み: {today_sent} / 上限: {daily_limit} / 残り: {remaining}")

        if not targets:
            logger.info("送信対象者がいません。完了。")
            return

        if remaining <= 0:
            logger.warning("本日の送信上限に達しています。明日再実行されます。")
            return

        batch = targets[:remaining]
        logger.info(f"今回送信: {len(batch)}件（残り{len(targets) - len(batch)}件は翌日以降）")

        if dry_run:
            for p in batch:
                logger.info(f"  [DRY-RUN] {p.name} <{p.email}>")
            logger.info("ドライラン完了。実際の送信は行いませんでした。")
            return

        sent = 0
        failed = 0

        # PDF添付パス（final_reminder用）
        pdf_path = None
        if send_type == "final_reminder":
            pdf_setting = AppSetting.query.filter_by(key="reunion_guide_pdf").first()
            if pdf_setting and pdf_setting.value:
                pdf_path = pdf_setting.value
            else:
                default_pdf = os.path.join(app.root_path, "static", "uploads", "reunion_guide.pdf")
                if os.path.isfile(default_pdf):
                    pdf_path = default_pdf

        for p in batch:
            try:
                if send_type == "final_url":
                    final_url = generate_final_url(p, base_url)
                    send_final_url(p, final_url)
                elif send_type == "reminder":
                    final_url = generate_final_url(p, base_url)
                    send_reminder(p, final_url)
                elif send_type == "final_reminder":
                    send_final_reminder(p, attachment_path=pdf_path)

                sent += 1
                logger.info(f"  送信成功: {p.name} <{p.email}> ({sent}/{len(batch)})")
            except Exception as e:
                failed += 1
                logger.error(f"  送信失敗: {p.name} <{p.email}> - {e}")

            time.sleep(0.5)

        remaining_after = len(targets) - len(batch)
        logger.info(f"完了: {sent}件成功 / {failed}件失敗")
        if remaining_after > 0:
            logger.info(f"残り{remaining_after}件は翌日以降に自動送信されます。")
        else:
            logger.info("全員への送信が完了しました。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="毎日の自動メール送信")
    parser.add_argument(
        "--type",
        choices=["final_url", "reminder", "final_reminder"],
        default="final_url",
        help="送信タイプ (default: final_url)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="送信せず対象者だけ表示する",
    )
    args = parser.parse_args()
    run(args.type, dry_run=args.dry_run)
