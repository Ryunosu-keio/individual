"""
routes/forms.py - 仮出欠・本出欠フォームのルーティング

URL:
  GET  /form/provisional        仮出欠フォーム表示
  POST /form/provisional        仮出欠フォーム登録
  GET  /form/final/<token>      本出欠フォーム表示
  POST /form/final/<token>      本出欠フォーム登録
  GET  /form/done               完了ページ

名寄せロジック（仮出欠フォーム）:
  1. 入力されたメールアドレスで既存レコードを検索
  2. 見つかれば → そのレコードに仮出欠を紐付け
  3. 見つからなければ → 入力された氏名で名簿レコードを検索（名寄せ）
     a. 一致1件 → メールを更新して紐付け
     b. 一致複数（同姓同名）→ クラス・出席番号で絞り込み
     c. 一致なし → 新規登録
"""
import re
import logging
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
from extensions import db
from models import Participant, ProvisionalResponse, FinalResponse, Payment
from models import AppSetting
from services.token_service import get_participant_by_token, ensure_token, generate_final_url
from services.mail_service import send_provisional_confirmation, send_final_confirmation, send_cancel_confirmation
from utils import normalize_transfer_name, decompose_voiced

logger = logging.getLogger(__name__)

forms_bp = Blueprint("forms", __name__, url_prefix="/form")

PLACEHOLDER_DOMAIN = "@placeholder.local"


def _normalize_name(name: str) -> str:
    """氏名を正規化（スペース除去・全角半角統一）して比較用に返す"""
    return re.sub(r'[\s\u3000]+', '', name)


def _find_roster_match(name: str, class_name: str, student_number: str):
    """
    名簿（プレースホルダーメール保持レコード）から氏名で一致を探す。

    優先順位:
      1. 氏名 + クラス + 出席番号 が完全一致
      2. 氏名 + クラス が一致
      3. 氏名のみ一致（スペース正規化後）

    Returns:
        Participant or None: 一意に特定できた場合のみ返す。
                             複数候補が残る場合は None（新規登録に委ねる）。
    """
    # プレースホルダーメールを持つ（＝まだ本人未連絡）レコードを対象に絞る
    candidates = Participant.query.filter(
        Participant.email.like(f"%{PLACEHOLDER_DOMAIN}")
    ).all()

    norm_input = _normalize_name(name)

    # 氏名が一致するものを抽出（スペース差異を無視）
    name_matches = [
        p for p in candidates
        if _normalize_name(p.name) == norm_input
    ]

    if not name_matches:
        return None

    if len(name_matches) == 1:
        return name_matches[0]

    # 同姓同名が複数いる場合 → クラスで絞り込む
    if class_name:
        class_matches = [p for p in name_matches if p.class_name == class_name]
        if len(class_matches) == 1:
            return class_matches[0]

        # クラスも一致が複数 → 出席番号で絞り込む
        if class_matches and student_number:
            num_matches = [p for p in class_matches if p.student_number == student_number]
            if len(num_matches) == 1:
                return num_matches[0]

    # 絞り込めなかった → 新規登録に任せる
    logger.warning(f"名寄せ失敗（同姓同名複数）: {name} / クラス={class_name}")
    return None


@forms_bp.route("/provisional", methods=["GET", "POST"])
def provisional():
    """仮出欠フォーム"""
    if request.method == "GET":
        from services.mail_service import _format_deadline_jp
        deadline = ""
        s = AppSetting.query.filter_by(key="provisional_deadline").first()
        if s and s.value:
            deadline = _format_deadline_jp(s.value)
        locked = _is_provisional_form_locked()
        return render_template("provisional_form.html", provisional_deadline=deadline, locked=locked)

    if _is_provisional_form_locked():
        flash("回答期限を過ぎているため、フォームはロックされています。", "danger")
        return redirect(url_for("forms.provisional"))

    # クラス選択→名前選択方式: participant_id が送ら��てくる場合はそれを優先
    participant_id  = request.form.get("participant_id", "").strip()
    name           = request.form.get("name", "").strip()
    name_kana      = request.form.get("name_kana", "").strip()
    email          = request.form.get("email", "").strip().lower()
    status         = request.form.get("status", "undecided")
    class_name     = request.form.get("class_name", "").strip()
    student_number = ""  # フォーム��らは収集しない

    # バリデーション
    errors = []
    if not name and not participant_id:
        errors.append("氏名を選択または入力してください。")
    if not email or "@" not in email:
        errors.append("正しいメールアドレスを入力してください。")
    if status not in ("attending", "not_attending", "undecided"):
        errors.append("参加意思を選択してください。")

    if errors:
        for e in errors:
            flash(e, "danger")
        return render_template("provisional_form.html",
                               name=name, name_kana=name_kana,
                               email=email, status=status,
                               class_name=class_name)

    matched_how = ""

    # ── ステップ1: ドロップダウンで名簿IDが選択された場合（最優先）──
    if participant_id:
        participant = db.session.get(Participant, int(participant_id))
        if participant:
            stored_email_real = participant.email and PLACEHOLDER_DOMAIN not in participant.email
            already_responded = bool(participant.provisional_responses)

            # 回答済み＆別メールアドレス → 別人による上書き試みをブロック
            if already_responded and stored_email_real and participant.email != email:
                flash(
                    f"「{participant.name}」さんはすでに別のメールアドレスで回答済みです。"
                    " ご本人のメールアドレスで再度ご回答いただくか、幹事にお問い合わせください。",
                    "danger"
                )
                return render_template("provisional_form.html",
                                       name=name, name_kana=name_kana,
                                       email=email, status=status,
                                       class_name=class_name)

            # 同じメールを持つ別の参加者が既にいるか確認
            existing_by_email = Participant.query.filter_by(email=email).first()
            if existing_by_email and existing_by_email.id != participant.id:
                flash("このメールアドレスは既に別の方が登録済みです。ご自身のメールアドレスを入力してください。", "danger")
                return render_template("provisional_form.html",
                                       name=name, name_kana=name_kana,
                                       email=email, status=status,
                                       class_name=class_name)
            participant.email = email
            # 既に名前が登録されている場合は上書きしない
            if name and not participant.name:
                participant.name = name
            if name_kana and not participant.name_kana:
                participant.name_kana = name_kana
            participant.updated_at = datetime.utcnow()
            matched_how = "selected"
            name = participant.name
            logger.info(f"名簿選択: {participant.name} ({email})")
        else:
            participant_id = ""  # 無効なIDは無視してフォールスルー

    if not participant_id:
        # ── ステップ2: メールアドレスで既存レコードを検索 ──
        participant = Participant.query.filter_by(email=email).first()

        if participant and PLACEHOLDER_DOMAIN not in participant.email:
            # 名前は上書きせず登録済みの情報を保持する
            participant.updated_at = datetime.utcnow()
            matched_how = "email"
            name = participant.name
            logger.info(f"既存参加者（メール一致）: {participant.name} ({email})")

        elif not participant:
            # ── ステップ3: 氏名で名簿を検索（名寄せ）──
            roster_match = _find_roster_match(name, class_name, student_number)
            if roster_match:
                roster_match.email = email
                roster_match.name  = name
                if name_kana:
                    roster_match.name_kana = name_kana
                if class_name:
                    roster_match.class_name = class_name
                roster_match.updated_at = datetime.utcnow()
                participant = roster_match
                matched_how = "roster"
                logger.info(f"名簿に名寄せ: {name} ({email}) → ID={roster_match.id}")
            else:
                # ── ステップ4: 完全新規 ──
                participant = Participant(name=name, email=email, class_name=class_name,
                                          name_kana=name_kana)
                db.session.add(participant)
                db.session.flush()
                matched_how = "new"
                logger.info(f"新規参加者登録: {name} ({email})")

    # 仮出欠回答を追加
    response = ProvisionalResponse(
        participant_id=participant.id,
        status=status,
        share_consent=True,
        submitted_at=datetime.utcnow(),
        ip_address=request.remote_addr or "",
    )
    db.session.add(response)
    db.session.commit()

    logger.info(f"仮出欠登録完了: {name} / {status} / 紐付け={matched_how}")

    # 送信完了メールを送信（失敗してもフォーム送信はブロックしない）
    try:
        status_label = ProvisionalResponse.STATUS_LABELS.get(status, status)
        base_url = current_app.config.get("APP_BASE_URL", request.host_url.rstrip("/"))
        provisional_url = f"{base_url}/form/provisional"
        send_provisional_confirmation(participant, status_label, provisional_url, status)
    except Exception as e:
        logger.error(f"仮出欠確認メール送信エラー: {e}", exc_info=True)

    flash("仮出欠を受け付けました。ありがとうございます。", "success")
    return redirect(url_for("forms.done", type="provisional"))


def _today_jst():
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=9))).date()


def _is_provisional_form_locked() -> bool:
    """手動ロック設定を優先し、なければ provisional_deadline 翌日以降（JST）でロック。"""
    manual = AppSetting.query.filter_by(key="provisional_form_locked").first()
    if manual:
        return manual.value == "1"
    s = AppSetting.query.filter_by(key="provisional_deadline").first()
    if not (s and s.value):
        return False
    try:
        from datetime import date as _date
        return _today_jst() > _date.fromisoformat(s.value)
    except ValueError:
        return False


def _is_final_form_locked() -> bool:
    """手動ロック設定を優先し、なければ final_deadline 翌日以降（JST）でロック。"""
    manual = AppSetting.query.filter_by(key="final_form_locked").first()
    if manual:
        return manual.value == "1"
    s = AppSetting.query.filter_by(key="final_deadline").first()
    if not (s and s.value):
        return False
    try:
        from datetime import date as _date
        return _today_jst() > _date.fromisoformat(s.value)
    except ValueError:
        return False



@forms_bp.route("/final/<token>", methods=["GET", "POST"])
def final(token):
    """本出欠フォーム（トークン付きURL）"""
    participant = get_participant_by_token(token)
    if participant is None:
        abort(404)

    existing = participant.latest_final

    # 振込先情報をDBから取得
    transfer_keys = [
        "transfer_bank", "transfer_branch", "transfer_branch_number",
        "transfer_account_type", "transfer_account_number", "transfer_account_name", "transfer_deadline",
        "reunion_fee",
    ]
    transfer_info = {}
    for s in AppSetting.query.filter(AppSetting.key.in_(transfer_keys)).all():
        transfer_info[s.key] = s.value

    # 振込名義を自動生成: 学籍番号(クラス+出席番号) + カナ氏名
    # 学籍番号がない場合は "3000" をプレフィックスとして使用
    if participant.class_name and participant.student_number:
        student_id = f"{participant.class_name}{participant.student_number.zfill(2)}"
    else:
        student_id = "3000"
    kana = participant.display_name_kana
    default_transfer_name = normalize_transfer_name(
        f"{student_id}{kana}" if kana else student_id
    )
    default_transfer_name_alt = decompose_voiced(default_transfer_name)

    is_teacher = participant.role in ("教師", "学年主任", "副担任")
    locked = _is_final_form_locked()
    can_cancel = locked and existing and existing.status == "attending"

    from services.mail_service import _format_deadline_jp
    fd = AppSetting.query.filter_by(key="final_deadline").first()
    final_deadline_jp = _format_deadline_jp(fd.value) if fd and fd.value else ""

    if request.method == "GET":
        return render_template("final_form.html",
                               participant=participant,
                               existing=existing,
                               token=token,
                               transfer_info=transfer_info,
                               default_transfer_name=default_transfer_name,
                               default_transfer_name_alt=default_transfer_name_alt,
                               locked=locked,
                               can_cancel=can_cancel,
                               is_teacher=is_teacher,
                               final_deadline_jp=final_deadline_jp)

    # ロック中の制御
    if locked:
        if can_cancel and request.form.get("status") == "cancelled":
            cancel_reason = request.form.get("cancel_reason", "").strip()
            if not cancel_reason:
                flash("欠席の理由を入力してください。", "danger")
                return redirect(url_for("forms.final", token=token))
            response = FinalResponse(
                participant_id=participant.id,
                status="cancelled",
                remarks=cancel_reason,
                submitted_at=datetime.utcnow(),
                ip_address=request.remote_addr or "",
            )
            db.session.add(response)
            payment = participant.payment
            if payment and payment.payment_status != "paid":
                db.session.delete(payment)
            participant.updated_at = datetime.utcnow()
            db.session.commit()
            logger.info(f"直前キャンセル: {participant.name} ({participant.email})")
            try:
                send_cancel_confirmation(participant, cancel_reason)
            except Exception as e:
                logger.error(f"キャンセル確認メール送信エラー: {e}", exc_info=True)
            flash("欠席のご連絡を受け付けました。確認メールをお送りしました。", "success")
            return redirect(url_for("forms.done", type="final"))
        else:
            flash("回答期限を過ぎているため変更できません。", "danger")
            return redirect(url_for("forms.final", token=token))

    status        = request.form.get("status", "").strip()
    transfer_name = normalize_transfer_name(request.form.get("transfer_name", "").strip())
    remarks       = request.form.get("remarks", "").strip()

    transfer_name_confirmed = request.form.get("transfer_name_confirm") == "on"
    transfer_done           = request.form.get("transfer_done") == "on"

    errors = []
    if status not in ("attending", "not_attending"):
        errors.append("参加・不参加を選択してください。")
    if status == "attending" and not is_teacher and not transfer_name_confirmed:
        errors.append("振込名義に学籍番号を含めましたのチェックを入れてください。")
    if status == "attending" and not is_teacher and not transfer_done:
        errors.append("振込完了のチェックを入れてください。")

    if errors:
        for e in errors:
            flash(e, "danger")
        return render_template("final_form.html",
                               participant=participant,
                               existing=existing,
                               token=token,
                               transfer_info=transfer_info,
                               default_transfer_name=default_transfer_name,
                               default_transfer_name_alt=default_transfer_name_alt,
                               locked=False,
                               can_cancel=False,
                               is_teacher=is_teacher)

    # 会費を取得（AppSettingから）
    fee_setting = AppSetting.query.filter_by(key="reunion_fee").first()
    reunion_fee_str = fee_setting.value if fee_setting else "0"
    try:
        payment_expected = int(reunion_fee_str.replace(",", "").replace("円", "").strip())
    except ValueError:
        payment_expected = 0

    response = FinalResponse(
        participant_id=participant.id,
        status=status,
        companions=0,
        transfer_name=transfer_name,
        payment_expected=payment_expected,
        payment_method="bank_transfer",
        remarks=remarks,
        share_consent=True,
        submitted_at=datetime.utcnow(),
        ip_address=request.remote_addr or "",
    )
    db.session.add(response)

    if status == "attending":
        payment = participant.payment
        if payment is None:
            payment = Payment(participant_id=participant.id)
            db.session.add(payment)
        payment.expected_amount = payment_expected
        payment.payment_method  = "bank_transfer"
        payment.transfer_name   = transfer_name
    elif status == "not_attending":
        payment = participant.payment
        if payment and payment.payment_status != "paid":
            db.session.delete(payment)

    participant.updated_at = datetime.utcnow()
    db.session.commit()

    logger.info(f"本出欠登録: {participant.name} ({participant.email}) → {status}")

    # 送信完了メールを送信（失敗してもフォーム送信はブロックしない）
    try:
        status_label = FinalResponse.STATUS_LABELS.get(status, status)
        base_url = current_app.config.get("APP_BASE_URL", request.host_url.rstrip("/"))
        final_url = f"{base_url}/form/final/{token}"
        send_final_confirmation(participant, status_label, final_url, status)
    except Exception as e:
        logger.error(f"本出欠確認メール送信エラー: {e}", exc_info=True)

    flash("本出欠を受け付けました。ありがとうございます。", "success")
    return redirect(url_for("forms.done", type="final"))


@forms_bp.route("/api/names")
def api_names():
    """クラスに属する生徒名一覧をJSON返却（仮出欠フォームのドロップダウン用）"""
    from flask import jsonify
    from sqlalchemy import or_
    class_name = request.args.get("class", "").strip()
    if not class_name:
        return jsonify([])
    def _teacher_sort_key(p):
        role_order = 0 if p.role == "学年主任" else 1
        num = int(p.student_number) if p.student_number and p.student_number.isdigit() else 9999
        return (role_order, p.class_name or "", num)

    if class_name == "teacher":
        participants = Participant.query.filter(
            or_(Participant.role == "教師", Participant.role == "学年主任")
        ).all()
        participants.sort(key=_teacher_sort_key)
    else:
        students = Participant.query.filter(
            Participant.class_name == class_name,
            or_(Participant.role == "生徒", Participant.role == "幹事")
        ).all()
        students.sort(key=lambda p: (int(p.student_number) if p.student_number and p.student_number.isdigit() else 9999))
        # 学年主任（class_nameが空）＋当クラスの教師
        heads = Participant.query.filter_by(role="学年主任").all()
        class_teachers = Participant.query.filter_by(class_name=class_name, role="教師").all()
        class_teachers.sort(key=lambda p: (int(p.student_number) if p.student_number and p.student_number.isdigit() else 9999))
        participants = students + heads + class_teachers
    return jsonify([{"id": p.id, "name": p.name, "role": p.role or "", "name_kana": p.name_kana or "", "number": p.student_number or ""} for p in participants])


@forms_bp.route("/done")
def done():
    """フォーム送信完了ページ"""
    form_type = request.args.get("type", "provisional")
    return render_template("done.html", form_type=form_type)
