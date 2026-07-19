"""
routes/admin.py - 管理画面のルーティング

URL:
  GET  /admin/                          管理画面トップ（ダッシュボード）
  GET  /admin/participants              参加者一覧
  GET  /admin/participant/<id>          参加者詳細
  POST /admin/participant/<id>/memo     メモ更新
  POST /admin/send-final-url/<id>       個別送信
  POST /admin/send-final-url-bulk       一括送信
  GET  /admin/payments                  入金管理一覧
  POST /admin/payment/<id>/update       入金ステータス更新
  GET  /admin/csv-import                CSV取込画面
  POST /admin/csv-import                CSV取込実行
  POST /admin/csv-match                 自動照合実行
  POST /admin/confirm-match             手動照合確定
  POST /admin/unmatch/<id>              照合解除
  GET  /admin/roster                    名簿管理画面
  POST /admin/roster/import             名簿CSV取込
  POST /admin/roster/add                参加者1名手動追加
  POST /admin/roster/delete/<id>        参加者削除
  GET  /admin/roster/export             名簿CSVエクスポート
"""
import csv
import io
import logging
from datetime import datetime
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, current_app, jsonify, Response)
from extensions import db
from models import Participant, ProvisionalResponse, FinalResponse, Payment, BankImport, MailLog, AppSetting, AttendanceRecord
from services.token_service import ensure_token, generate_final_url
from services.mail_service import (send_final_url, send_reminder, send_final_reminder,
                                    MAIL_DEFAULTS, get_daily_send_limit,
                                    get_today_sent_count, get_remaining_today)
from services.csv_service import parse_bank_csv, save_bank_imports
from services.matching_service import run_auto_matching, confirm_match, unmatch
from utils import normalize_transfer_name

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.before_request
def require_admin_auth():
    """管理画面全体にログインを必須にする"""
    from flask import session
    if not session.get("admin_authed"):
        return redirect(url_for("login", next=request.full_path))


# -----------------------------------------------
# ダッシュボード
# -----------------------------------------------
@admin_bp.route("/")
def index():
    """管理画面トップ：各種集計を表示"""
    total = Participant.query.count()
    provisional_attending = 0
    provisional_not_attending = 0
    provisional_undecided = 0
    final_attending = 0
    final_not_attending = 0
    final_cancelled = 0
    final_no_response = 0
    no_response = 0
    paid_count = 0
    unpaid_count = 0
    final_url_sent = 0
    final_url_unsent = 0

    participants = Participant.query.all()
    for p in participants:
        prov = p.latest_provisional
        final = p.latest_final
        if prov:
            if prov.status == "attending":
                provisional_attending += 1
            elif prov.status == "not_attending":
                provisional_not_attending += 1
            elif prov.status == "undecided":
                provisional_undecided += 1
        else:
            no_response += 1

        if final:
            if final.status == "attending":
                final_attending += 1
            elif final.status == "cancelled":
                final_cancelled += 1
            else:
                final_not_attending += 1
        else:
            final_no_response += 1

        has_final_url_mail = any(
            ml.mail_type == "final_url" and ml.status in ("sent", "simulated")
            for ml in p.mail_logs
        )
        if has_final_url_mail:
            final_url_sent += 1
        else:
            final_url_unsent += 1

        if p.payment:
            final_status = final.status if final else None
            if p.payment.payment_status == "paid":
                paid_count += 1
            elif final_status not in ("not_attending", "cancelled"):
                unpaid_count += 1

    no_email_count = Participant.query.filter(
        Participant.email.like("%@placeholder.local")
    ).count()

    daily_limit = get_daily_send_limit()
    today_sent = get_today_sent_count()
    remaining_today = max(0, daily_limit - today_sent)
    send_stage = (final_url_sent // daily_limit) + 1 if daily_limit > 0 else 1

    stats = {
        "total": total,
        "provisional_attending": provisional_attending,
        "provisional_not_attending": provisional_not_attending,
        "provisional_undecided": provisional_undecided,
        "final_attending": final_attending,
        "final_not_attending": final_not_attending,
        "final_cancelled": final_cancelled,
        "final_no_response": final_no_response,
        "no_provisional_response": no_response,
        "paid": paid_count,
        "unpaid": unpaid_count,
        "no_email": no_email_count,
        "final_url_sent": final_url_sent,
        "final_url_unsent": final_url_unsent,
        "daily_limit": daily_limit,
        "today_sent": today_sent,
        "remaining_today": remaining_today,
        "send_stage": send_stage,
    }
    def _form_locked(key):
        s = AppSetting.query.filter_by(key=key).first()
        return s.value == "1" if s else False

    locks = {
        "provisional": _form_locked("provisional_form_locked"),
        "final": _form_locked("final_form_locked"),
    }
    return render_template("admin/index.html", stats=stats, locks=locks)


@admin_bp.route("/qr-attendance")
def qr_attendance():
    """会場QR出席の管理画面"""
    # 名簿CSVの読み込み順（DB登録順: created_at）を保ちつつクラス順に表示
    participants = Participant.query.order_by(Participant.class_name, Participant.created_at).all()
    records = AttendanceRecord.query.order_by(AttendanceRecord.checked_in_at.desc()).all()
    qr_url = url_for("attendance_scan", _external=True)
    total = len(participants)
    checked_ids = {r.participant_id for r in records if r.status == "checked_in"}
    checked_count = len(checked_ids)
    not_checked = max(0, total - checked_count)

    # クラス別名簿（幹事用）: 本出欠参加・来場済み・未来場を名前順で一覧化
    latest_checkin = {}
    for r in records:  # records は checked_in_at 降順
        if r.status == "checked_in" and r.participant_id not in latest_checkin:
            latest_checkin[r.participant_id] = r

    def _member_info(p):
        final = p.latest_final
        rec = latest_checkin.get(p.id)
        return {
            "participant": p,
            "final_attending": bool(final and final.status == "attending"),
            "final_status": final.status if final else None,
            "checked_in": rec is not None,
            "checked_in_at": rec.checked_in_at if rec else None,
        }

    def _kana_key(p):
        return (p.name_kana or p.name or "")

    roster = []
    for key in [str(n) for n in range(31, 40)]:
        members = sorted(
            [p for p in participants
             if p.class_name == key and p.role in ("生徒", "幹事")],
            key=_kana_key,
        )
        infos = [_member_info(p) for p in members]
        roster.append({
            "key": key,
            "label": key,
            "members": infos,
            "attending_count": sum(1 for m in infos if m["final_attending"]),
            "arrived_count": sum(1 for m in infos if m["checked_in"]),
            "pending_count": sum(1 for m in infos if m["final_attending"] and not m["checked_in"]),
        })
    teachers = sorted(
        [p for p in participants if p.role in ("教師", "学年主任")],
        key=_kana_key,
    )
    teacher_infos = [_member_info(p) for p in teachers]
    roster.append({
        "key": "teacher",
        "label": "教職員",
        "members": teacher_infos,
        "attending_count": sum(1 for m in teacher_infos if m["final_attending"]),
        "arrived_count": sum(1 for m in teacher_infos if m["checked_in"]),
        "pending_count": sum(1 for m in teacher_infos if m["final_attending"] and not m["checked_in"]),
    })

    return render_template(
        "admin/qr_attendance.html",
        participants=participants,
        records=records,
        qr_url=qr_url,
        total=total,
        checked_count=checked_count,
        not_checked=not_checked,
        roster=roster,
    )


@admin_bp.route('/participant/<int:participant_id>/set-attendance', methods=['POST'])
def set_attendance(participant_id):
    """管理画面から参加/不参加を手動で設定する。"""
    from datetime import datetime
    status = request.form.get('status', '').strip()
    p = Participant.query.get(participant_id)
    if not p:
        flash('参加者が見つかりません。', 'danger')
        return redirect(url_for('admin.qr_attendance'))

    if status == 'checked_in':
        rec = AttendanceRecord(participant_id=participant_id, checked_in_at=datetime.utcnow(), source='admin', status='checked_in')
        db.session.add(rec)
        db.session.commit()
        flash(f'{p.name} を出席に設定しました。', 'success')
    elif status == 'not_attending':
        rec = AttendanceRecord(participant_id=participant_id, checked_in_at=datetime.utcnow(), source='admin', status='not_attending')
        db.session.add(rec)
        db.session.commit()
        flash(f'{p.name} を不参加に設定しました。', 'warning')
    else:
        flash('不正な操作です。', 'danger')

    return redirect(url_for('admin.qr_attendance'))


@admin_bp.route("/toggle-form-lock/<form_type>", methods=["POST"])
def toggle_form_lock(form_type):
    """仮出欠・本出欠フォームのロックを手動切替"""
    if form_type not in ("provisional", "final"):
        flash("不正なフォーム種別です。", "danger")
        return redirect(url_for("admin.index"))
    key = f"{form_type}_form_locked"
    s = AppSetting.query.filter_by(key=key).first()
    current = s.value == "1" if s else False
    new_val = "0" if current else "1"
    if s:
        s.value = new_val
        s.updated_at = datetime.utcnow()
    else:
        db.session.add(AppSetting(key=key, value=new_val))
    db.session.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        from flask import make_response
        return make_response("", 204)
    label = "仮出欠" if form_type == "provisional" else "本出欠"
    state = "ロック" if new_val == "1" else "解除"
    flash(f"{label}フォームを{state}しました。", "success" if new_val == "0" else "warning")
    return redirect(url_for("admin.index"))


# -----------------------------------------------
# 参加者一覧・詳細
# -----------------------------------------------
@admin_bp.route("/participants")
def participants():
    """参加者一覧（検索・絞り込み・並べ替え対応）"""
    from sqlalchemy import case as sa_case

    q              = request.args.get("q", "").strip()
    status_filter  = request.args.get("status", "all")
    final_filter   = request.args.get("final_status", "all")
    role_filter    = request.args.get("role", "all")
    class_filter   = request.args.get("class_name", "all")
    sort           = request.args.get("sort", "class")
    order          = request.args.get("order", "asc")

    query = Participant.query

    if q:
        query = query.filter(
            db.or_(
                Participant.name.ilike(f"%{q}%"),
                Participant.email.ilike(f"%{q}%"),
            )
        )
    if role_filter != "all":
        query = query.filter(Participant.role == role_filter)
    if class_filter != "all":
        query = query.filter(Participant.class_name == class_filter)

    all_participants = query.all()

    # Python側で並べ替え（DB依存のregexp_replaceを回避）
    def _num(p):
        return int(p.student_number) if p.student_number and p.student_number.isdigit() else 9999

    def _role_order(p):
        return {"生徒": 0, "教師": 1, "学年主任": 2, "幹事": 3}.get(p.role, 4)

    sort_key_map = {
        "class":   lambda p: (p.class_name or "", _role_order(p), _num(p)),
        "name":    lambda p: (p.name or "",),
        "number":  lambda p: (p.class_name or "", _num(p)),
        "role":    lambda p: (_role_order(p), p.class_name or "", _num(p)),
        "created": lambda p: (p.created_at,),
        "email":   lambda p: (p.email or "",),
    }
    key_func = sort_key_map.get(sort, sort_key_map["class"])
    all_participants.sort(key=key_func, reverse=(order == "desc"))

    # 仮出欠ステータスで絞り込み（Python側）
    if status_filter != "all":
        filtered = []
        for p in all_participants:
            prov = p.latest_provisional
            if status_filter == "no_response" and prov is None:
                filtered.append(p)
            elif prov and prov.status == status_filter:
                filtered.append(p)
        all_participants = filtered

    # 本出欠ステータスで絞り込み（Python側）
    if final_filter != "all":
        filtered = []
        for p in all_participants:
            final = p.latest_final
            if final_filter == "no_response" and final is None:
                filtered.append(p)
            elif final and final.status == final_filter:
                filtered.append(p)
        all_participants = filtered

    # クラス一覧（絞り込み用）
    classes = [r[0] for r in db.session.query(Participant.class_name)
               .filter(Participant.class_name != "")
               .distinct().order_by(Participant.class_name).all()]

    def sort_url(col):
        new_order = "desc" if (sort == col and order == "asc") else "asc"
        return url_for("admin.participants", q=q, status=status_filter,
                       final_status=final_filter,
                       role=role_filter, class_name=class_filter,
                       sort=col, order=new_order)

    def sort_icon(col):
        if sort != col:
            return "bi-arrow-down-up text-muted"
        return "bi-sort-up" if order == "asc" else "bi-sort-down"

    return render_template("admin/participants.html",
                           participants=all_participants,
                           q=q,
                           status_filter=status_filter,
                           final_filter=final_filter,
                           role_filter=role_filter,
                           class_filter=class_filter,
                           sort=sort, order=order,
                           classes=classes,
                           sort_url=sort_url,
                           sort_icon=sort_icon)


@admin_bp.route("/participant/<int:participant_id>")
def participant_detail(participant_id):
    """参加者詳細"""
    participant = db.session.get(Participant, participant_id)
    if participant is None:
        flash("参加者が見つかりません。", "danger")
        return redirect(url_for("admin.participants"))

    base_url = current_app.config.get("APP_BASE_URL", "http://localhost:5000")
    final_url = None
    if participant.token:
        final_url = generate_final_url(participant, base_url)

    mail_logs = MailLog.query.filter_by(participant_id=participant_id)\
                             .order_by(MailLog.sent_at.desc()).all()

    return render_template("admin/participant_detail.html",
                           participant=participant,
                           final_url=final_url,
                           mail_logs=mail_logs)


@admin_bp.route("/participant/<int:participant_id>/set-provisional-status", methods=["POST"])
def set_provisional_status(participant_id):
    """仮出欠ステータスを手動変更"""
    from models import ProvisionalResponse
    participant = db.session.get(Participant, participant_id)
    if participant is None:
        flash("参加者が見つかりません。", "danger")
        return redirect(url_for("admin.participants"))
    status = request.form.get("status", "").strip()
    if status not in ("attending", "not_attending", "undecided"):
        flash("無効なステータスです。", "danger")
        return redirect(url_for("admin.participant_detail", participant_id=participant_id))
    response = ProvisionalResponse(
        participant_id=participant.id,
        status=status,
        submitted_at=datetime.utcnow(),
        ip_address="admin",
    )
    db.session.add(response)
    participant.updated_at = datetime.utcnow()
    db.session.commit()
    flash(f"仮出欠を「{response.status_label}」に変更しました。", "success")
    return redirect(url_for("admin.participant_detail", participant_id=participant_id))


@admin_bp.route("/participant/<int:participant_id>/set-final-status", methods=["POST"])
def set_final_status(participant_id):
    """本出欠ステータスを手動変更"""
    participant = db.session.get(Participant, participant_id)
    if participant is None:
        flash("参加者が見つかりません。", "danger")
        return redirect(url_for("admin.participants"))
    status = request.form.get("status", "").strip()
    if status not in ("attending", "not_attending", "cancelled"):
        flash("無効なステータスです。", "danger")
        return redirect(url_for("admin.participant_detail", participant_id=participant_id))
    from models import FinalResponse, Payment, AppSetting
    response = FinalResponse(
        participant_id=participant.id,
        status=status,
        submitted_at=datetime.utcnow(),
        ip_address="admin",
    )
    db.session.add(response)

    if status == "attending":
        fee_setting = AppSetting.query.filter_by(key="reunion_fee").first()
        reunion_fee_str = fee_setting.value if fee_setting else "0"
        try:
            payment_expected = int(reunion_fee_str.replace(",", "").replace("円", "").strip())
        except ValueError:
            payment_expected = 0
        payment = participant.payment
        if payment is None:
            payment = Payment(participant_id=participant.id)
            db.session.add(payment)
        payment.expected_amount = payment_expected
        payment.payment_method = "bank_transfer"
    elif status in ("not_attending", "cancelled"):
        payment = participant.payment
        if payment and payment.payment_status != "paid":
            db.session.delete(payment)

    participant.updated_at = datetime.utcnow()
    db.session.commit()
    flash(f"本出欠を「{response.status_label}」に変更しました。", "success")
    return redirect(url_for("admin.participant_detail", participant_id=participant_id))


@admin_bp.route("/participant/<int:participant_id>/toggle-consent/<response_type>", methods=["POST"])
def toggle_consent(participant_id, response_type):
    """仮/本出欠の名前共有同意を切り替え"""
    participant = db.session.get(Participant, participant_id)
    if participant is None:
        flash("参加者が見つかりません。", "danger")
        return redirect(url_for("admin.participants"))
    prov  = participant.latest_provisional
    final = participant.latest_final
    if prov is None and final is None:
        flash("回答が見つかりません。", "danger")
        return redirect(url_for("admin.participant_detail", participant_id=participant_id))
    current = bool(prov.share_consent if prov else False) or bool(final.share_consent if final else False)
    new_val = not current
    if prov:
        prov.share_consent = new_val
    if final:
        final.share_consent = new_val
    db.session.commit()
    label = "名前の共有を許可しました。" if new_val else "共有許可を取り消しました。"
    flash(label, "success")
    return redirect(url_for("admin.participant_detail", participant_id=participant_id))


@admin_bp.route("/participant/<int:participant_id>/memo", methods=["POST"])
def update_memo(participant_id):
    """幹事メモを更新"""
    participant = db.session.get(Participant, participant_id)
    if participant is None:
        flash("参加者が見つかりません。", "danger")
        return redirect(url_for("admin.participants"))

    participant.teacher_memo = request.form.get("teacher_memo", "").strip()
    participant.updated_at = datetime.utcnow()
    db.session.commit()
    flash("メモを更新しました。", "success")
    return redirect(url_for("admin.participant_detail", participant_id=participant_id))


@admin_bp.route("/participant/<int:participant_id>/update-basic", methods=["POST"])
def update_basic_info(participant_id):
    """基本情報（氏名・フリガナ・メール・クラス・出席番号）を更新"""
    participant = db.session.get(Participant, participant_id)
    if participant is None:
        flash("参加者が見つかりません。", "danger")
        return redirect(url_for("admin.participants"))

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    if not name or not email:
        flash("氏名とメールアドレスは必須です。", "danger")
        return redirect(url_for("admin.participant_detail", participant_id=participant_id))

    duplicate = Participant.query.filter(
        Participant.email == email, Participant.id != participant_id
    ).first()
    if duplicate:
        flash(f"メールアドレス {email} は {duplicate.name} さんが既に使用しています。", "danger")
        return redirect(url_for("admin.participant_detail", participant_id=participant_id))

    participant.name = name
    participant.name_kana = request.form.get("name_kana", "").strip()
    participant.email = email
    participant.class_name = request.form.get("class_name", "").strip()
    participant.student_number = request.form.get("student_number", "").strip()
    participant.updated_at = datetime.utcnow()
    db.session.commit()
    flash("基本情報を更新しました。", "success")
    return redirect(url_for("admin.participant_detail", participant_id=participant_id))


@admin_bp.route("/participant/<int:participant_id>/clear-responses", methods=["POST"])
def clear_responses(participant_id):
    """参加者の回答・入金データを全てクリアして未回答状態に戻す"""
    from models import ProvisionalResponse, FinalResponse, Payment
    participant = db.session.get(Participant, participant_id)
    if participant is None:
        flash("参加者が見つかりません。", "danger")
        return redirect(url_for("admin.participants"))

    ProvisionalResponse.query.filter_by(participant_id=participant_id).delete()
    FinalResponse.query.filter_by(participant_id=participant_id).delete()
    Payment.query.filter_by(participant_id=participant_id).delete()
    participant.updated_at = datetime.utcnow()
    db.session.commit()
    flash(f"{participant.name} の回答・入金データをクリアしました。", "success")
    return redirect(url_for("admin.participant_detail", participant_id=participant_id))


@admin_bp.route("/participant/<int:participant_id>/update-role", methods=["POST"])
def update_role(participant_id):
    """役割を更新"""
    participant = db.session.get(Participant, participant_id)
    if participant is None:
        flash("参加者が見つかりません。", "danger")
        return redirect(url_for("admin.participants"))

    role = request.form.get("role", "").strip()
    if role not in {"生徒", "教師", "学年主任", "幹事"}:
        flash("無効な役割です。", "danger")
        return redirect(url_for("admin.participant_detail", participant_id=participant_id))

    participant.role = role
    participant.updated_at = datetime.utcnow()
    db.session.commit()
    flash(f"役割を「{role}」に変更しました。", "success")
    return redirect(url_for("admin.participant_detail", participant_id=participant_id))


# -----------------------------------------------
# 自動送信（フェーズ自動判定 + 次の100人）
# -----------------------------------------------
BATCH_SIZE = 100

PHASE_LABELS = {
    "final_url": "本出欠URL送信",
    "reminder": "リマインド送信",
    "final_reminder": "最終リマインド送信",
}


def _today_jst():
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=9))).date()


def _get_reminder_send_date_passed() -> bool:
    """リマインドメール送信日を過ぎているか判定する（JST基準）。未設定の場合は False。"""
    from datetime import date as _date
    s = AppSetting.query.filter_by(key="reminder_send_date").first()
    if not (s and s.value):
        return False
    try:
        return _today_jst() >= _date.fromisoformat(s.value)
    except ValueError:
        return False


def _get_final_reminder_date_passed() -> bool:
    """最終リマインド送信日を過ぎているか判定する（JST基準）。未設定の場合は False。"""
    from datetime import date as _date
    s = AppSetting.query.filter_by(key="final_reminder_date").first()
    if not (s and s.value):
        return False
    try:
        return _today_jst() >= _date.fromisoformat(s.value)
    except ValueError:
        return False


def _collect_pending_jobs(base_url: str) -> list:
    """
    全フェーズの未送信ジョブをフェーズ間ブロッキングなしで収集する。
    各要素: {"phase": str, "pid": int, "final_url": str|None}

    Phase 1 (final_url):      常時 — 仮出欠回答済み & URL未送信
    Phase 2 (reminder):       reminder_send_date 以降 & URL送信済み & 本出欠未回答
    Phase 3 (final_reminder): final_reminder_date 以降 & 本参加確定 & 最終リマインド未送信
    """
    participants = Participant.query.filter(
        ~Participant.email.like("%@placeholder.local"),
    ).all()

    jobs = []

    for p in participants:
        if p.latest_provisional:
            if not any(ml.mail_type == "final_url" and ml.status in ("sent", "simulated") for ml in p.mail_logs):
                jobs.append({"phase": "final_url", "pid": p.id, "final_url": generate_final_url(p, base_url)})

    if _get_reminder_send_date_passed():
        for p in participants:
            has_url = any(ml.mail_type == "final_url" and ml.status in ("sent", "simulated") for ml in p.mail_logs)
            has_reminded = any(ml.mail_type == "reminder" and ml.status in ("sent", "simulated") for ml in p.mail_logs)
            if has_url and not has_reminded:
                jobs.append({"phase": "reminder", "pid": p.id, "final_url": generate_final_url(p, base_url)})

    if _get_final_reminder_date_passed():
        for p in participants:
            final = p.latest_final
            if final and final.status == "attending":
                if not any(ml.mail_type == "final_reminder" and ml.status in ("sent", "simulated") for ml in p.mail_logs):
                    jobs.append({"phase": "final_reminder", "pid": p.id, "final_url": None})

    return jobs


@admin_bp.route("/api/auto-send-preview")
def api_auto_send_preview():
    """自動送信のプレビュー情報をJSON返却"""
    from collections import Counter
    base_url = current_app.config.get("APP_BASE_URL", "http://localhost:5000")
    all_jobs = _collect_pending_jobs(base_url)
    remaining = get_remaining_today()
    batch = all_jobs[:min(BATCH_SIZE, remaining)]

    total_by_phase = Counter(j["phase"] for j in all_jobs)
    batch_by_phase = Counter(j["phase"] for j in batch)

    phases = [
        {"key": ph, "label": PHASE_LABELS[ph],
         "total": total_by_phase[ph], "batch": batch_by_phase[ph]}
        for ph in PHASE_LABELS
        if total_by_phase[ph] > 0
    ]

    return jsonify({
        "has_targets": len(all_jobs) > 0,
        "total_targets": len(all_jobs),
        "batch_size": len(batch),
        "remaining_today": remaining,
        "daily_limit": get_daily_send_limit(),
        "today_sent": get_today_sent_count(),
        "phases": phases,
        "targets": [
            {"id": j["pid"], "name": next((p.name for p in Participant.query.filter_by(id=j["pid"]).limit(1)), ""),
             "phase": j["phase"]}
            for j in batch
        ],
    })


@admin_bp.route("/auto-send", methods=["POST"])
def auto_send():
    """全フェーズ並行判定 → 次の最大100件に送信"""
    import threading
    import time as _time
    import os
    from collections import Counter

    base_url = current_app.config.get("APP_BASE_URL", "http://localhost:5000")
    all_jobs = _collect_pending_jobs(base_url)

    if not all_jobs:
        flash("送信対象者がいません。全フェーズ完了済みです。", "info")
        return redirect(url_for("admin.index"))

    remaining = get_remaining_today()
    if remaining <= 0:
        flash("本日の送信上限に達しています。", "warning")
        return redirect(url_for("admin.index"))

    batch = all_jobs[:min(BATCH_SIZE, remaining)]
    app = current_app._get_current_object()

    pdf_path = None
    if any(j["phase"] == "final_reminder" for j in batch):
        pdf_setting = AppSetting.query.filter_by(key="reunion_guide_pdf").first()
        if pdf_setting and pdf_setting.value:
            pdf_path = pdf_setting.value
        else:
            default_pdf = os.path.join(current_app.root_path, "static", "uploads", "reunion_guide.pdf")
            if os.path.isfile(default_pdf):
                pdf_path = default_pdf

    def bulk_send():
        with app.app_context():
            sent = failed = 0
            counts = {ph: 0 for ph in PHASE_LABELS}
            for job in batch:
                p = db.session.get(Participant, job["pid"])
                if not p:
                    continue
                try:
                    if job["phase"] == "final_url":
                        send_final_url(p, job["final_url"])
                    elif job["phase"] == "reminder":
                        send_reminder(p, job["final_url"])
                    elif job["phase"] == "final_reminder":
                        send_final_reminder(p, attachment_path=pdf_path)
                    sent += 1
                    counts[job["phase"]] += 1
                except Exception as e:
                    failed += 1
                    logger.error(f"自動送信失敗: {e}", exc_info=True)
                _time.sleep(0.5)
            summary = " / ".join(
                f"{PHASE_LABELS[ph]}: {cnt}件" for ph, cnt in counts.items() if cnt > 0
            )
            logger.info(f"自動送信完了: {summary} | 成功{sent}件 / 失敗{failed}件")

    thread = threading.Thread(target=bulk_send, daemon=True)
    thread.start()

    phase_counter = Counter(j["phase"] for j in batch)
    parts = [f"{PHASE_LABELS[ph]} {cnt}件" for ph, cnt in phase_counter.items() if ph in PHASE_LABELS]
    remaining_after = len(all_jobs) - len(batch)
    msg = "送信開始: " + "、".join(parts)
    if remaining_after > 0:
        msg += f"（残り{remaining_after}件は次回送信してください）"
    flash(msg, "info")
    return redirect(url_for("admin.index"))


# -----------------------------------------------
# メール送信ハブ
# -----------------------------------------------
@admin_bp.route("/mail-hub")
def mail_hub():
    """メール送信ハブ画面"""
    return render_template("admin/mail_hub.html")


@admin_bp.route("/api/mail-preview/<mail_type>")
def api_mail_preview(mail_type):
    """メール種別ごとのプレビュー・対象者リストをJSON返却"""
    from services.mail_service import MAIL_DEFAULTS, _get_template, _get_reunion_info, _get_mail_config

    is_teacher = request.args.get("teacher", "0") == "1"
    reunion = _get_reunion_info()
    base_url = current_app.config.get("APP_BASE_URL", "http://localhost:5000")

    VALID_TYPES = {
        "final_url": {
            "label": "本出欠URL送信",
            "subject_key": "mail_final_url_subject",
            "body_key": "mail_final_url_body",
            "subject_key_teacher": "mail_final_url_subject_teacher",
            "body_key_teacher": "mail_final_url_body_teacher",
        },
        "reminder": {
            "label": "リマインド送信",
            "subject_key": "mail_reminder_subject",
            "body_key": "mail_reminder_body",
            "subject_key_teacher": "mail_reminder_subject_teacher",
            "body_key_teacher": "mail_reminder_body_teacher",
        },
        "final_reminder": {
            "label": "最終リマインド送信",
            "subject_key": "mail_final_reminder_subject",
            "body_key": "mail_final_reminder_body",
            "subject_key_teacher": "mail_final_reminder_subject_teacher",
            "body_key_teacher": "mail_final_reminder_body_teacher",
        },
    }

    if mail_type not in VALID_TYPES:
        return jsonify({"error": "不正なメール種別です"}), 400

    info = VALID_TYPES[mail_type]
    s_key = info["subject_key_teacher"] if is_teacher else info["subject_key"]
    b_key = info["body_key_teacher"]    if is_teacher else info["body_key"]
    subject_tmpl = _get_template(s_key, MAIL_DEFAULTS[s_key])
    body_tmpl    = _get_template(b_key, MAIL_DEFAULTS[b_key])

    preview_name = "（先生名）" if is_teacher else "（参加者名）"
    preview_vars = {
        "name": preview_name,
        "reunion_name": reunion["reunion_name"],
        "reunion_date": reunion["reunion_date"],
        "reunion_time": reunion["reunion_time"],
        "reunion_venue": reunion["reunion_venue"],
        "reunion_fee": reunion["reunion_fee"],
        "dress_code": reunion["dress_code"],
        "belongings": reunion["belongings"],
        "organizer_name": reunion["organizer_name"],
        "final_deadline": reunion["final_deadline"],
        "final_deadline_short": reunion["final_deadline_short"],
        "final_reminder_deadline": reunion["final_reminder_deadline"],
        "final_reminder_deadline_short": reunion["final_reminder_deadline_short"],
        "final_url": f"{base_url}/form/final/（トークン）",
        "provisional_url": f"{base_url}/form/provisional",
        "status": "参加",
        "transfer_bank": reunion["transfer_bank"],
        "transfer_branch": reunion["transfer_branch"],
        "transfer_branch_number": reunion["transfer_branch_number"],
        "transfer_account_type": reunion["transfer_account_type"],
        "transfer_account_number": reunion["transfer_account_number"],
        "transfer_account_name": reunion["transfer_account_name"],
        "transfer_deadline": reunion["transfer_deadline"],
    }
    for k, v in preview_vars.items():
        subject_tmpl = subject_tmpl.replace("{" + k + "}", str(v))
        body_tmpl    = body_tmpl.replace("{" + k + "}", str(v))

    mail_cfg = _get_mail_config()
    from_addr = mail_cfg.get("from_addr", "")
    if from_addr:
        body_tmpl = body_tmpl.rstrip("\n") + f"\nE-mail: {from_addr}\n"

    participants = Participant.query.filter(
        ~Participant.email.like("%@placeholder.local"),
    ).all()

    targets = []
    if mail_type == "final_url":
        for p in participants:
            if p.latest_provisional:
                has_sent = any(
                    ml.mail_type == "final_url" and ml.status in ("sent", "simulated")
                    for ml in p.mail_logs
                )
                if not has_sent:
                    targets.append(p)
    elif mail_type == "reminder":
        if _get_reminder_send_date_passed():
            for p in participants:
                has_url = any(ml.mail_type == "final_url" and ml.status in ("sent", "simulated") for ml in p.mail_logs)
                has_reminded = any(ml.mail_type == "reminder" and ml.status in ("sent", "simulated") for ml in p.mail_logs)
                if has_url and not has_reminded:
                    targets.append(p)
    elif mail_type == "final_reminder":
        if _get_final_reminder_date_passed():
            for p in participants:
                final = p.latest_final
                if final and final.status == "attending":
                    if not any(ml.mail_type == "final_reminder" and ml.status in ("sent", "simulated") for ml in p.mail_logs):
                        targets.append(p)

    remaining = get_remaining_today()

    import os
    pdf_setting = AppSetting.query.filter_by(key="reunion_guide_pdf").first()
    pdf_path = pdf_setting.value if pdf_setting and pdf_setting.value else None
    if not pdf_path:
        default_pdf = os.path.join(current_app.root_path, "static", "uploads", "reunion_guide.pdf")
        if os.path.isfile(default_pdf):
            pdf_path = default_pdf
    attachment_info = None
    if mail_type == "final_reminder" and pdf_path and os.path.isfile(pdf_path):
        attachment_info = {
            "filename": os.path.basename(pdf_path),
            "size_kb": round(os.path.getsize(pdf_path) / 1024, 1),
        }

    from services.mail_service import _text_to_html
    return jsonify({
        "label": info["label"],
        "subject": subject_tmpl,
        "body": body_tmpl,
        "html_body": _text_to_html(body_tmpl),
        "targets": [
            {"id": p.id, "name": p.name, "email": p.email, "role": p.role or "",
             "class_name": p.class_name or "", "number": p.student_number or ""}
            for p in targets
        ],
        "target_count": len(targets),
        "remaining_today": remaining,
        "daily_limit": get_daily_send_limit(),
        "today_sent": get_today_sent_count(),
        "attachment": attachment_info,
    })


@admin_bp.route("/api/mail-preview-individual/<int:participant_id>/<mail_type>")
def api_mail_preview_individual(participant_id, mail_type):
    """参加者個人向けメールプレビューをJSON返却"""
    from services.mail_service import (
        MAIL_DEFAULTS, _get_template, _get_reunion_info, _get_mail_config,
        _text_to_html, _is_teacher as _ms_is_teacher,
    )
    from datetime import timedelta

    participant = db.session.get(Participant, participant_id)
    if participant is None:
        return jsonify({"error": "参加者が見つかりません"}), 404

    reunion = _get_reunion_info()
    base_url = current_app.config.get("APP_BASE_URL", "http://localhost:5000")
    is_teacher = _ms_is_teacher(participant.role or "")
    token = ensure_token(participant)
    final_url = f"{base_url}/form/final/{token}"

    unlock_deadline = datetime.utcnow() + timedelta(hours=9, weeks=1)
    unlock_deadline_str = f"{unlock_deadline.month}月{unlock_deadline.day}日"

    CONFIGS = {
        "final_url": {
            "s_key": "mail_final_url_subject_teacher" if is_teacher else "mail_final_url_subject",
            "b_key": "mail_final_url_body_teacher"    if is_teacher else "mail_final_url_body",
        },
        "reminder": {
            "s_key": "mail_reminder_subject_teacher" if is_teacher else "mail_reminder_subject",
            "b_key": "mail_reminder_body_teacher"    if is_teacher else "mail_reminder_body",
        },
        "final_reminder": {
            "s_key": "mail_final_reminder_subject_teacher" if is_teacher else "mail_final_reminder_subject",
            "b_key": "mail_final_reminder_body_teacher"    if is_teacher else "mail_final_reminder_body",
        },
        "unlock_notice": {
            "s_key": "mail_unlock_notice_subject",
            "b_key": "mail_unlock_notice_body",
        },
    }
    if mail_type not in CONFIGS:
        return jsonify({"error": "不正なメール種別です"}), 400

    cfg = CONFIGS[mail_type]
    subject_tmpl = _get_template(cfg["s_key"], MAIL_DEFAULTS.get(cfg["s_key"], ""))
    body_tmpl    = _get_template(cfg["b_key"], MAIL_DEFAULTS.get(cfg["b_key"], ""))

    vars = dict(
        name=participant.display_name,
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
        provisional_url=f"{base_url}/form/provisional",
        status="参加",
        transfer_bank=reunion["transfer_bank"],
        transfer_branch=reunion["transfer_branch"],
        transfer_branch_number=reunion["transfer_branch_number"],
        transfer_account_type=reunion["transfer_account_type"],
        transfer_account_number=reunion["transfer_account_number"],
        transfer_account_name=reunion["transfer_account_name"],
        transfer_deadline=reunion["transfer_deadline"],
        deadline=unlock_deadline_str,
    )
    for k, v in vars.items():
        subject_tmpl = subject_tmpl.replace("{" + k + "}", str(v))
        body_tmpl    = body_tmpl.replace("{" + k + "}", str(v))

    mail_cfg = _get_mail_config()
    from_addr = mail_cfg.get("from_addr", "")
    if from_addr:
        body_tmpl = body_tmpl.rstrip("\n") + f"\nE-mail: {from_addr}\n"

    import os
    attachment_info = None
    if mail_type == "final_reminder":
        pdf_setting = AppSetting.query.filter_by(key="reunion_guide_pdf").first()
        pdf_path = pdf_setting.value if pdf_setting and pdf_setting.value else None
        if not pdf_path:
            default_pdf = os.path.join(current_app.root_path, "static", "uploads", "reunion_guide.pdf")
            if os.path.isfile(default_pdf):
                pdf_path = default_pdf
        if pdf_path and os.path.isfile(pdf_path):
            attachment_info = {
                "filename": os.path.basename(pdf_path),
                "size_kb": round(os.path.getsize(pdf_path) / 1024, 1),
            }

    return jsonify({
        "subject": subject_tmpl,
        "body": body_tmpl,
        "html_body": _text_to_html(body_tmpl),
        "to_name": participant.name,
        "to_email": participant.email,
        "attachment": attachment_info,
        "unlock_deadline": unlock_deadline_str,
    })


# -----------------------------------------------
# メール送信（個別・一括）
# -----------------------------------------------
@admin_bp.route("/send-final-url/<int:participant_id>", methods=["POST"])
def send_final_url_single(participant_id):
    """本出欠URLを個別送信"""
    participant = db.session.get(Participant, participant_id)
    if participant is None:
        flash("参加者が見つかりません。", "danger")
        return redirect(url_for("admin.participants"))

    base_url = current_app.config.get("APP_BASE_URL", "http://localhost:5000")
    final_url = generate_final_url(participant, base_url)

    try:
        # current_app.config_obj はapp.pyで設定する簡易オブジェクト
        log = send_final_url(participant, final_url)
        if log.status == "simulated":
            flash(f"[開発モード] {participant.name} へのメール内容をコンソールに出力しました。", "info")
        else:
            flash(f"{participant.name} ({participant.email}) へ本出欠URLを送信しました。", "success")
    except Exception as e:
        flash(f"送信失敗: {e}", "danger")

    return redirect(url_for("admin.participant_detail", participant_id=participant_id))


@admin_bp.route("/send-final-url-bulk", methods=["POST"])
def send_final_url_bulk():
    """本出欠URLを一括送信（仮出欠回答済み＆URL未送信の全員・段階送信）"""
    import threading
    import time

    base_url = current_app.config.get("APP_BASE_URL", "http://localhost:5000")

    participants = Participant.query.filter(
        ~Participant.email.like("%@placeholder.local"),
    ).all()

    targets = []
    for p in participants:
        if not p.latest_provisional:
            continue
        has_final_url_mail = any(
            ml.mail_type == "final_url" and ml.status in ("sent", "simulated")
            for ml in p.mail_logs
        )
        if not has_final_url_mail:
            targets.append(p)

    if not targets:
        flash("送信対象の参加者がいません（全員送信済みです）。", "info")
        return redirect(url_for("admin.participants"))

    remaining = get_remaining_today()
    if remaining <= 0:
        flash("本日の送信上限に達しています。明日以降に再度送信してください。", "warning")
        return redirect(url_for("admin.participants"))

    batch = targets[:remaining]
    daily_limit = get_daily_send_limit()
    stage = (get_today_sent_count() // daily_limit) + 1 if daily_limit > 0 else 1

    app = current_app._get_current_object()
    jobs = [(p.id, generate_final_url(p, base_url)) for p in batch]

    def bulk_send():
        with app.app_context():
            sent = 0
            failed = 0
            for pid, final_url in jobs:
                p = db.session.get(Participant, pid)
                if p is None:
                    continue
                try:
                    send_final_url(p, final_url)
                    sent += 1
                except Exception as e:
                    logger.error(f"一括送信失敗: {p.email} - {e}", exc_info=True)
                    failed += 1
                time.sleep(0.5)
            logger.info(f"第{stage}段階送信完了: {sent} 件成功 / {failed} 件失敗")

    thread = threading.Thread(target=bulk_send, daemon=True)
    thread.start()

    remaining_after = len(targets) - len(batch)
    msg = f"第{stage}段階: {len(batch)} 件の送信を開始しました。"
    if remaining_after > 0:
        msg += f"（残り {remaining_after} 件は次回送信してください）"
    flash(msg, "info")
    return redirect(url_for("admin.participants"))


@admin_bp.route("/send-reminder/<int:participant_id>", methods=["POST"])
def send_reminder_single(participant_id):
    """リマインドメールを個別送信"""
    participant = db.session.get(Participant, participant_id)
    if participant is None:
        flash("参加者が見つかりません。", "danger")
        return redirect(url_for("admin.participants"))

    base_url = current_app.config.get("APP_BASE_URL", "http://localhost:5000")
    final_url = generate_final_url(participant, base_url)

    try:
        log = send_reminder(participant, final_url)
        if log.status == "simulated":
            flash(f"[開発モード] {participant.name} へのリマインド内容をコンソールに出力しました。", "info")
        else:
            flash(f"{participant.name} へリマインドを送信しました。", "success")
    except Exception as e:
        flash(f"送信失敗: {e}", "danger")

    return redirect(url_for("admin.participant_detail", participant_id=participant_id))


@admin_bp.route("/send-final-reminder/<int:participant_id>", methods=["POST"])
def send_final_reminder_single(participant_id):
    """最終リマインドを個別送信"""
    participant = db.session.get(Participant, participant_id)
    if participant is None:
        flash("参加者が見つかりません。", "danger")
        return redirect(url_for("admin.participants"))

    pdf_path = None
    s = AppSetting.query.filter_by(key="reunion_guide_pdf").first()
    if s and s.value and os.path.isfile(s.value):
        pdf_path = s.value

    try:
        log = send_final_reminder(participant, attachment_path=pdf_path)
        if log.status == "simulated":
            flash(f"[開発モード] {participant.name} への最終リマインド内容をコンソールに出力しました。", "info")
        else:
            flash(f"{participant.name} へ最終リマインドを送信しました。", "success")
    except Exception as e:
        flash(f"送信失敗: {e}", "danger")

    return redirect(url_for("admin.participant_detail", participant_id=participant_id))


@admin_bp.route("/send-unlock-notice/<int:participant_id>", methods=["POST"])
def send_unlock_notice_single(participant_id):
    """フォームロック解除通知を個別送信"""
    from services.mail_service import send_unlock_notice
    from datetime import datetime, timedelta
    participant = db.session.get(Participant, participant_id)
    if participant is None:
        flash("参加者が見つかりません。", "danger")
        return redirect(url_for("admin.participants"))

    token = ensure_token(participant)
    base_url = current_app.config.get("APP_BASE_URL", request.host_url.rstrip("/"))
    final_url = f"{base_url}/form/final/{token}"

    deadline_date = datetime.utcnow() + timedelta(hours=9) + timedelta(weeks=1)
    deadline_str = f"{deadline_date.month}月{deadline_date.day}日"


    try:
        log = send_unlock_notice(participant, final_url, deadline_str)
        if log.status == "simulated":
            flash(f"[開発モード] {participant.name} へのロック解除通知をコンソールに出力しました。", "info")
        else:
            flash(f"{participant.name} へロック解除通知を送信しました（期限: {deadline_str} 23:59 JST）。", "success")
    except Exception as e:
        flash(f"送信失敗: {e}", "danger")

    return redirect(url_for("admin.participant_detail", participant_id=participant_id))


@admin_bp.route("/send-reminder-bulk", methods=["POST"])
def send_reminder_bulk():
    """リマインドメールを一括送信（本出欠URL送信済み＆本出欠未回答の参加者）"""
    import threading
    import time

    base_url = current_app.config.get("APP_BASE_URL", "http://localhost:5000")

    participants = Participant.query.filter(
        ~Participant.email.like("%@placeholder.local"),
    ).all()
    targets = [
        p for p in participants
        if any(ml.mail_type == "final_url" and ml.status in ("sent", "simulated") for ml in p.mail_logs)
        and not any(ml.mail_type == "reminder" and ml.status in ("sent", "simulated") for ml in p.mail_logs)
    ]

    if not targets:
        flash("リマインド送信対象の参加者がいません。", "info")
        return redirect(url_for("admin.participants"))

    remaining = get_remaining_today()
    if remaining <= 0:
        flash("本日の送信上限に達しています。明日以降に再度送信してください。", "warning")
        return redirect(url_for("admin.participants"))

    batch = targets[:remaining]
    app = current_app._get_current_object()
    jobs = [(p.id, generate_final_url(p, base_url)) for p in batch]

    def bulk_send():
        with app.app_context():
            sent = 0
            failed = 0
            for pid, final_url in jobs:
                p = db.session.get(Participant, pid)
                if p is None:
                    continue
                try:
                    send_reminder(p, final_url)
                    sent += 1
                except Exception as e:
                    logger.error(f"リマインド一括送信失敗: {p.email} - {e}", exc_info=True)
                    failed += 1
                time.sleep(0.5)
            logger.info(f"リマインド一括送信完了: {sent} 件成功 / {failed} 件失敗")

    thread = threading.Thread(target=bulk_send, daemon=True)
    thread.start()

    remaining_after = len(targets) - len(batch)
    msg = f"{len(batch)} 件のリマインド送信を開始しました。"
    if remaining_after > 0:
        msg += f"（残り {remaining_after} 件は次回送信してください）"
    flash(msg, "info")
    return redirect(url_for("admin.participants"))


@admin_bp.route("/send-final-reminder-bulk", methods=["POST"])
def send_final_reminder_bulk():
    """最終リマインドメールを一括送信（本出欠参加者にPDF添付）"""
    import threading
    import time

    participants = Participant.query.filter(
        ~Participant.email.like("%@placeholder.local"),
    ).all()

    targets = []
    for p in participants:
        final = p.latest_final
        if final and final.status == "attending":
            has_final_reminder = any(
                ml.mail_type == "final_reminder" and ml.status in ("sent", "simulated")
                for ml in p.mail_logs
            )
            if not has_final_reminder:
                targets.append(p)

    if not targets:
        flash("最終リマインド送信対象の参加者がいません。", "info")
        return redirect(url_for("admin.participants"))

    remaining = get_remaining_today()
    if remaining <= 0:
        flash("本日の送信上限に達しています。明日以降に再度送信してください。", "warning")
        return redirect(url_for("admin.participants"))

    batch = targets[:remaining]

    # PDF添付ファイルのパス
    pdf_setting = AppSetting.query.filter_by(key="reunion_guide_pdf").first()
    pdf_path = None
    if pdf_setting and pdf_setting.value:
        pdf_path = pdf_setting.value
    else:
        import os
        default_pdf = os.path.join(current_app.root_path, "static", "uploads", "reunion_guide.pdf")
        if os.path.isfile(default_pdf):
            pdf_path = default_pdf

    app = current_app._get_current_object()
    jobs = [p.id for p in batch]

    def bulk_send():
        with app.app_context():
            sent = 0
            failed = 0
            for pid in jobs:
                p = db.session.get(Participant, pid)
                if p is None:
                    continue
                try:
                    send_final_reminder(p, attachment_path=pdf_path)
                    sent += 1
                except Exception as e:
                    logger.error(f"最終リマインド一括送信失敗: {p.email} - {e}", exc_info=True)
                    failed += 1
                time.sleep(0.5)
            logger.info(f"最終リマインド一括送信完了: {sent} 件成功 / {failed} 件失敗")

    thread = threading.Thread(target=bulk_send, daemon=True)
    thread.start()

    remaining_after = len(targets) - len(batch)
    msg = f"{len(batch)} 件の最終リマインド送信を開始しました。"
    if pdf_path:
        msg += "（PDF添付あり）"
    else:
        msg += "（PDF未設定のため添付なし）"
    if remaining_after > 0:
        msg += f"（残り {remaining_after} 件は次回送信してください）"
    flash(msg, "info")
    return redirect(url_for("admin.participants"))


# -----------------------------------------------
# 入金管理
# -----------------------------------------------
@admin_bp.route("/payments")
def payments():
    """入金管理一覧（入金一覧＋CSV照合を統合）"""
    status_filter = request.args.get("status", "all")

    query = Payment.query
    if status_filter != "all":
        query = query.filter_by(payment_status=status_filter)

    all_payments = query.all()

    # 集計
    total_expected = sum(p.expected_amount or 0 for p in all_payments)
    total_paid = sum(p.paid_amount or 0 for p in all_payments)

    # CSV照合タブ用のデータ
    bank_imports = BankImport.query.order_by(BankImport.import_date.desc()).limit(200).all()

    return render_template("admin/payments.html",
                           payments=all_payments,
                           status_filter=status_filter,
                           total_expected=total_expected,
                           total_paid=total_paid,
                           bank_imports=bank_imports)


@admin_bp.route("/payments/export")
def payments_export():
    """入金一覧をCSVエクスポート"""
    from models import Payment
    payments = (
        Payment.query
        .join(Participant, Payment.participant_id == Participant.id)
        .order_by(Participant.class_name, Participant.student_number)
        .all()
    )

    PAY_LABELS = {"unpaid": "未払い", "paid": "支払済み", "partial": "一部支払い"}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "氏名", "氏名（カナ）", "クラス", "出席番号", "役割",
        "入金ステータス", "入金金額", "支払予定金額", "支払日", "振込名義",
        "CSV照合", "CSV上の名義", "CSV上の金額", "CSV上の日付",
    ])
    for pay in payments:
        p = pay.participant
        writer.writerow([
            p.name,
            p.name_kana or "",
            p.class_name or "",
            p.student_number or "",
            p.role or "生徒",
            PAY_LABELS.get(pay.payment_status, pay.payment_status),
            pay.paid_amount or 0,
            pay.expected_amount or 0,
            pay.payment_date.strftime("%Y-%m-%d") if pay.payment_date else "",
            pay.transfer_name or "",
            "照合済み" if pay.bank_csv_matched else "未照合",
            pay.bank_csv_raw_name or "",
            pay.bank_csv_amount or "",
            pay.bank_csv_date.strftime("%Y-%m-%d") if pay.bank_csv_date else "",
        ])

    return Response(
        "﻿" + output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=payments_export.csv"}
    )


@admin_bp.route("/payment/<int:payment_id>/update", methods=["POST"])
def update_payment(payment_id):
    """入金ステータスを手動更新"""
    payment = db.session.get(Payment, payment_id)
    if payment is None:
        flash("入金レコードが見つかりません。", "danger")
        return redirect(url_for("admin.payments"))

    payment.payment_status = request.form.get("payment_status", payment.payment_status)
    paid_amount_str = request.form.get("paid_amount", "").strip()
    payment_date_str = request.form.get("payment_date", "").strip()
    payment.payment_method = request.form.get("payment_method", payment.payment_method)
    payment.transfer_name = normalize_transfer_name(request.form.get("transfer_name", payment.transfer_name))

    if paid_amount_str:
        try:
            payment.paid_amount = int(paid_amount_str)
        except ValueError:
            pass

    if payment_date_str:
        try:
            payment.payment_date = datetime.strptime(payment_date_str, "%Y-%m-%d").date()
        except ValueError:
            flash("日付の形式が正しくありません（例: 2025-01-01）", "warning")

    payment.updated_at = datetime.utcnow()
    db.session.commit()
    flash("入金情報を更新しました。", "success")
    return redirect(url_for("admin.payments"))


# -----------------------------------------------
# 銀行CSV取込・照合
# -----------------------------------------------
@admin_bp.route("/csv-import", methods=["GET", "POST"])
def csv_import():
    """銀行CSV取込（統合ページへリダイレクト）"""
    if request.method == "GET":
        return redirect(url_for("admin.payments") + "#csv")

    # POST: ファイルアップロード
    if "csv_file" not in request.files or request.files["csv_file"].filename == "":
        flash("ファイルを選択してください。", "danger")
        return redirect(url_for("admin.payments") + "#csv")

    file = request.files["csv_file"]
    try:
        content = file.read()
        records = parse_bank_csv(content, filename=file.filename)
        saved = save_bank_imports(records)
        flash(f"CSV取込完了: {len(records)} 件読込、{len(saved)} 件新規保存しました。", "success")
    except ValueError as e:
        flash(f"CSVの読み込みに失敗しました: {e}", "danger")
    except Exception as e:
        logger.error(f"CSV取込エラー: {e}")
        flash(f"予期しないエラーが発生しました: {e}", "danger")

    return redirect(url_for("admin.payments") + "#csv")


@admin_bp.route("/csv-match", methods=["POST"])
def csv_match():
    """自動照合を実行"""
    try:
        results = run_auto_matching()
        flash(
            f"自動照合完了: 自動確定 {results['auto_confirmed']} 件、"
            f"候補あり {results['matched']} 件、"
            f"未照合 {results['unmatched']} 件",
            "success"
        )
    except Exception as e:
        flash(f"照合エラー: {e}", "danger")
    return redirect(url_for("admin.payments") + "#csv")


@admin_bp.route("/confirm-match", methods=["POST"])
def confirm_match_route():
    """手動照合確定"""
    bank_import_id = request.form.get("bank_import_id", type=int)
    participant_id = request.form.get("participant_id", type=int)

    if not bank_import_id or not participant_id:
        flash("パラメータが不正です。", "danger")
        return redirect(url_for("admin.payments") + "#csv")

    try:
        confirm_match(bank_import_id, participant_id)
        flash("照合を確定しました。", "success")
    except Exception as e:
        flash(f"照合確定エラー: {e}", "danger")

    return redirect(url_for("admin.payments") + "#csv")


@admin_bp.route("/unmatch/<int:bank_import_id>", methods=["POST"])
def unmatch_route(bank_import_id):
    """照合を解除"""
    try:
        unmatch(bank_import_id)
        flash("照合を解除しました。", "success")
    except Exception as e:
        flash(f"解除エラー: {e}", "danger")
    return redirect(url_for("admin.payments") + "#csv")


@admin_bp.route("/csv-delete/<int:bank_import_id>", methods=["POST"])
def csv_delete(bank_import_id):
    """取込済みCSVレコードを1件削除"""
    try:
        bank_import = db.session.get(BankImport, bank_import_id)
        if not bank_import:
            flash("データが見つかりません。", "danger")
            return redirect(url_for("admin.payments") + "#csv")
        # 照合済みの場合は先に照合解除
        if bank_import.match_status != "unmatched":
            unmatch(bank_import_id)
        db.session.delete(bank_import)
        db.session.commit()
        flash("取込データを削除しました。", "success")
    except Exception as e:
        flash(f"削除エラー: {e}", "danger")
    return redirect(url_for("admin.payments") + "#csv")


@admin_bp.route("/csv-delete-all", methods=["POST"])
def csv_delete_all():
    """取込済みCSVレコードをすべて削除"""
    try:
        all_imports = BankImport.query.all()
        for bi in all_imports:
            if bi.match_status != "unmatched":
                unmatch(bi.id)
            db.session.delete(bi)
        db.session.commit()
        flash(f"{len(all_imports)} 件の取込データをすべて削除しました。", "success")
    except Exception as e:
        flash(f"一括削除エラー: {e}", "danger")
    return redirect(url_for("admin.payments") + "#csv")


# -----------------------------------------------
# メール設定
# -----------------------------------------------
@admin_bp.route("/settings/mail", methods=["GET", "POST"])
def settings_mail():
    """メール設定画面（送信アカウントの確認・変更）"""
    KEYS = [
        "mail_mode", "brevo_api_key",
        "mail_smtp_host", "mail_smtp_port",
        "mail_smtp_user", "mail_smtp_password",
        "mail_from", "mail_from_name", "mail_daily_limit",
    ]

    if request.method == "POST":
        for key in KEYS:
            val = request.form.get(key, "").strip()
            if key in ("mail_smtp_password", "brevo_api_key") and not val:
                continue
            setting = AppSetting.query.filter_by(key=key).first()
            if setting:
                setting.value = val
            else:
                db.session.add(AppSetting(key=key, value=val))
        db.session.commit()
        flash("メール設定を保存しました。", "success")
        return redirect(url_for("admin.settings_mail"))

    settings = {s.key: s.value for s in AppSetting.query.filter(AppSetting.key.in_(KEYS)).all()}
    cfg = current_app.config
    defaults = {
        "mail_mode":          cfg.get("MAIL_MODE", "console"),
        "mail_smtp_host":     cfg.get("MAIL_SMTP_HOST", "smtp.gmail.com"),
        "mail_smtp_port":     str(cfg.get("MAIL_SMTP_PORT", 587)),
        "mail_smtp_user":     cfg.get("MAIL_SMTP_USER", ""),
        "mail_smtp_password": "",
        "mail_from":          cfg.get("MAIL_FROM", ""),
        "mail_from_name":     cfg.get("MAIL_FROM_NAME", "同窓会幹事"),
        "mail_daily_limit":   "100",
    }
    for key in KEYS:
        if key not in settings or not settings[key]:
            settings[key] = defaults.get(key, "")
    return render_template("admin/settings_mail.html", settings=settings)


@admin_bp.route("/settings/pdf-upload", methods=["POST"])
def settings_pdf_upload():
    """案内PDFをアップロードする"""
    import os
    if "pdf_file" not in request.files:
        flash("ファイルを選択してください。", "danger")
        return redirect(url_for("admin.settings_mail"))

    file = request.files["pdf_file"]
    if file.filename == "" or not file.filename.lower().endswith(".pdf"):
        flash("PDFファイルを選択してください。", "danger")
        return redirect(url_for("admin.settings_mail"))

    upload_dir = os.path.join(current_app.root_path, "static", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    save_path = os.path.join(upload_dir, "reunion_guide.pdf")
    file.save(save_path)

    setting = AppSetting.query.filter_by(key="reunion_guide_pdf").first()
    if setting:
        setting.value = save_path
    else:
        db.session.add(AppSetting(key="reunion_guide_pdf", value=save_path))
    db.session.commit()

    flash("案内PDFをアップロードしました。", "success")
    return redirect(url_for("admin.settings_mail"))


@admin_bp.route("/settings/mail-template", methods=["GET", "POST"])
def settings_mail_template():
    """メール文章編集画面"""
    KEYS = [
        "mail_provisional_confirm_attending_subject",             "mail_provisional_confirm_attending_body",
        "mail_provisional_confirm_attending_subject_teacher",     "mail_provisional_confirm_attending_body_teacher",
        "mail_provisional_confirm_not_attending_subject",         "mail_provisional_confirm_not_attending_body",
        "mail_provisional_confirm_not_attending_subject_teacher", "mail_provisional_confirm_not_attending_body_teacher",
        "mail_provisional_confirm_undecided_subject",             "mail_provisional_confirm_undecided_body",
        "mail_provisional_confirm_undecided_subject_teacher",     "mail_provisional_confirm_undecided_body_teacher",
        "mail_final_url_subject",                                 "mail_final_url_body",
        "mail_final_url_subject_teacher",                         "mail_final_url_body_teacher",
        "mail_reminder_subject",                                  "mail_reminder_body",
        "mail_reminder_subject_teacher",                          "mail_reminder_body_teacher",
        "mail_final_confirm_attending_subject",                   "mail_final_confirm_attending_body",
        "mail_final_confirm_attending_subject_teacher",           "mail_final_confirm_attending_body_teacher",
        "mail_final_confirm_not_attending_subject",               "mail_final_confirm_not_attending_body",
        "mail_final_confirm_not_attending_subject_teacher",       "mail_final_confirm_not_attending_body_teacher",
        "mail_final_reminder_subject",                            "mail_final_reminder_body",
        "mail_final_reminder_subject_teacher",                    "mail_final_reminder_body_teacher",
        "mail_final_confirm_cancelled_subject",                   "mail_final_confirm_cancelled_body",
        "mail_final_confirm_cancelled_subject_teacher",           "mail_final_confirm_cancelled_body_teacher",
        "mail_unlock_notice_subject",                             "mail_unlock_notice_body",
        "mail_unlock_notice_subject_teacher",                     "mail_unlock_notice_body_teacher",
        "mail_verification_subject",                              "mail_verification_body",
    ]
    if request.method == "POST":
        for key in KEYS:
            val = request.form.get(key, "")
            setting = AppSetting.query.filter_by(key=key).first()
            if setting:
                setting.value = val
            else:
                db.session.add(AppSetting(key=key, value=val))
        db.session.commit()
        flash("メール文章を保存しました。", "success")
        return redirect(url_for("admin.settings_mail_template"))

    settings = {s.key: s.value for s in AppSetting.query.filter(AppSetting.key.in_(KEYS)).all()}
    # DB未設定のキーにはデフォルト値を入れる
    for key in KEYS:
        if key not in settings or not settings[key]:
            settings[key] = MAIL_DEFAULTS.get(key, "")
    return render_template("admin/settings_mail_template.html", settings=settings)


@admin_bp.route("/settings/mail/test", methods=["POST"])
def settings_mail_test():
    """テストメール送信（設定が正しいか確認用）"""
    to_email = request.form.get("test_email", "").strip()
    if not to_email or "@" not in to_email:
        flash("送信先メールアドレスを入力してください。", "danger")
        return redirect(url_for("admin.settings_mail"))

    from services.mail_service import _get_mail_config, _send_smtp_cfg, _send_gas, _send_console
    mail_cfg = _get_mail_config()

    subject = "【テスト】同窓会管理アプリ メール送信テスト"
    body = f"このメールは送信テストです。\n送信元: {mail_cfg['from_addr']}\nモード: {mail_cfg['mode']}"

    try:
        if mail_cfg["mode"] == "smtp":
            _send_smtp_cfg(to_email, subject, body, mail_cfg)
            flash(f"テストメールを {to_email} にSMTPで送信しました。", "success")
        elif mail_cfg["mode"] == "gas":
            _send_gas(to_email, subject, body, mail_cfg["from_name"])
            flash(f"テストメールを {to_email} にGAS経由で送信しました。", "success")
        else:
            _send_console(to_email, subject, body)
            flash(f"[コンソールモード] テストメールの内容をターミナルに出力しました。", "info")
    except Exception as e:
        flash(f"送信失敗: {e}", "danger")

    return redirect(url_for("admin.settings_mail"))


@admin_bp.route("/settings/reunion", methods=["GET", "POST"])
def settings_reunion():
    """同窓会情報設定画面"""
    KEYS = [
        "reunion_name", "organizer_name", "reunion_date", "reunion_time", "reunion_venue", "reunion_fee",
        "dress_code", "belongings", "provisional_deadline",
        "final_deadline", "reminder_send_date", "final_reminder_deadline", "final_reminder_date",
        "transfer_bank", "transfer_branch", "transfer_branch_number",
        "transfer_account_type", "transfer_account_number", "transfer_account_name", "transfer_deadline",
    ]

    if request.method == "POST":
        for key in KEYS:
            val = request.form.get(key, "").strip()
            s = AppSetting.query.filter_by(key=key).first()
            if s:
                s.value = val
                s.updated_at = datetime.utcnow()
            else:
                db.session.add(AppSetting(key=key, value=val))
        db.session.commit()
        flash("同窓会情報を保存しました。", "success")
        return redirect(url_for("admin.settings_reunion"))

    settings = {s.key: s.value for s in AppSetting.query.filter(AppSetting.key.in_(KEYS)).all()}
    cfg = current_app.config
    defaults = {
        "reunion_name":  cfg.get("REUNION_NAME", "同窓会"),
        "reunion_date":  cfg.get("REUNION_DATE", ""),
        "reunion_venue": cfg.get("REUNION_VENUE", ""),
        "reunion_fee":   cfg.get("REUNION_FEE", ""),
    }
    for key in KEYS:
        if key not in settings or not settings[key]:
            settings[key] = defaults.get(key, "")
    return render_template("admin/settings_reunion.html", settings=settings)


# -----------------------------------------------
# 参加者の本出欠URLを手動発行（トークン生成）
# -----------------------------------------------
@admin_bp.route("/generate-token/<int:participant_id>", methods=["POST"])
def generate_token_route(participant_id):
    """本出欠用トークンを生成して詳細ページに戻る"""
    participant = db.session.get(Participant, participant_id)
    if participant is None:
        flash("参加者が見つかりません。", "danger")
        return redirect(url_for("admin.participants"))
    ensure_token(participant)
    flash("本出欠URLを発行しました。", "success")
    return redirect(url_for("admin.participant_detail", participant_id=participant_id))


# -----------------------------------------------
# 名簿管理
# -----------------------------------------------
@admin_bp.route("/roster")
def roster():
    """名簿管理画面"""
    from sqlalchemy import case
    # クラス31-39 → 出席番号昇順 → 教師（クラスなし or 役割が教師/学年主任）は各クラスの後
    role_order = case(
        (Participant.role == "生徒", 0),
        (Participant.role == "教師", 1),
        (Participant.role == "学年主任", 2),
        (Participant.role == "幹事", 3),
        else_=4,
    )
    participants = Participant.query.all()

    def _num(p):
        return int(p.student_number) if p.student_number and p.student_number.isdigit() else 9999

    def _role_ord(p):
        return {"生徒": 0, "教師": 1, "学年主任": 2, "幹事": 3}.get(p.role, 4)

    participants.sort(key=lambda p: (p.class_name or "", _role_ord(p), _num(p)))
    return render_template("admin/roster.html", participants=participants)


@admin_bp.route("/roster/import", methods=["POST"])
def roster_import():
    """
    名簿CSVを取込んで参加者を一括登録する。

    CSVフォーマット（1行目はヘッダー行、列順序はヘッダー名で自動判別）:
      氏名, メールアドレス, クラス, 出席番号, 幹事メモ
    例（順番は問わない）:
      氏名,メールアドレス,クラス,出席番号,幹事メモ
      山田太郎,yamada@example.com,1-A,5,幹事
      鈴木花子,suzuki@example.com,2-B,12

    ヘッダーがない場合は 氏名・メール・クラス・出席番号・幹事メモ の固定順とみなす。
    メールアドレスが既存の場合はデータを更新する（重複登録にならない）。
    """
    if "csv_file" not in request.files:
        flash("ファイルを選択してください。", "danger")
        return redirect(url_for("admin.participants") + "#csv")

    file = request.files["csv_file"]
    if file.filename == "":
        flash("ファイルを選択してください。", "danger")
        return redirect(url_for("admin.participants") + "#csv")

    content = file.read()
    text = None
    for encoding in ["utf-8-sig", "shift_jis", "cp932", "utf-8"]:
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue

    if text is None:
        flash("CSVのエンコーディングを判別できませんでした。UTF-8かShift_JISで保存してください。", "danger")
        return redirect(url_for("admin.participants") + "#csv")

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)

    if not rows:
        flash("CSVが空です。", "danger")
        return redirect(url_for("admin.participants") + "#csv")

    # ヘッダー行の検出と列インデックスの解決
    NAME_HEADERS          = {"氏名", "名前", "name"}
    NAME_KANA_HEADERS     = {"氏名カナ", "氏名（カナ）", "フリガナ", "ふりがな", "kana", "name_kana"}
    EMAIL_HEADERS         = {"メールアドレス", "メール", "email", "mail"}
    CLASS_HEADERS         = {"クラス", "class", "組", "担当クラス"}
    NUMBER_HEADERS        = {"出席番号", "番号", "number", "no"}
    ROLE_HEADERS          = {"役割", "role", "種別", "区分"}
    MEMO_HEADERS          = {"幹事メモ", "メモ", "memo"}
    TOKEN_HEADERS         = {"トークン", "token"}
    PROV_STATUS_HEADERS   = {"仮出欠", "provisional_status"}
    FINAL_STATUS_HEADERS  = {"本出欠", "final_status"}
    COMPANIONS_HEADERS    = {"同伴者数", "companions"}
    TRANSFER_NAME_HEADERS = {"振込名義", "transfer_name"}
    REMARKS_HEADERS       = {"備考", "remarks", "本出欠備考"}
    PAY_STATUS_HEADERS    = {"入金ステータス", "payment_status"}
    PAID_AMOUNT_HEADERS   = {"入金金額", "paid_amount"}
    EXPECTED_AMOUNT_HEADERS = {"支払予定金額", "expected_amount", "予定金額"}
    PAYMENT_DATE_HEADERS  = {"支払日", "payment_date"}

    first = [h.strip().lower() for h in rows[0]]
    has_header = any(h in NAME_HEADERS or h in EMAIL_HEADERS for h in first)

    if has_header:
        def find_idx(candidates):
            for i, h in enumerate(first):
                if h in {c.lower() for c in candidates}:
                    return i
            return None

        idx_name          = find_idx(NAME_HEADERS)
        idx_name_kana     = find_idx(NAME_KANA_HEADERS)
        idx_email         = find_idx(EMAIL_HEADERS)
        idx_class         = find_idx(CLASS_HEADERS)
        idx_number        = find_idx(NUMBER_HEADERS)
        idx_role          = find_idx(ROLE_HEADERS)
        idx_memo          = find_idx(MEMO_HEADERS)
        idx_token         = find_idx(TOKEN_HEADERS)
        idx_prov_status   = find_idx(PROV_STATUS_HEADERS)
        idx_final_status  = find_idx(FINAL_STATUS_HEADERS)
        idx_companions    = find_idx(COMPANIONS_HEADERS)
        idx_transfer_name    = find_idx(TRANSFER_NAME_HEADERS)
        idx_remarks          = find_idx(REMARKS_HEADERS)
        idx_pay_status       = find_idx(PAY_STATUS_HEADERS)
        idx_paid_amount      = find_idx(PAID_AMOUNT_HEADERS)
        idx_expected_amount  = find_idx(EXPECTED_AMOUNT_HEADERS)
        idx_payment_date     = find_idx(PAYMENT_DATE_HEADERS)
        data_rows            = rows[1:]
    else:
        # ヘッダーなし → 固定順: 氏名, 氏名カナ, メール, クラス, 出席番号, 役割, 幹事メモ
        idx_name, idx_name_kana, idx_email, idx_class, idx_number, idx_role, idx_memo = 0, 1, 2, 3, 4, 5, 6
        idx_token = idx_prov_status = idx_final_status = None
        idx_companions = idx_transfer_name = idx_remarks = None
        idx_pay_status = idx_paid_amount = idx_expected_amount = idx_payment_date = None
        data_rows = rows

    if idx_name is None:
        flash("CSVに「氏名」列が見つかりません。", "danger")
        return redirect(url_for("admin.participants") + "#csv")

    def get_col(row, idx):
        if idx is not None and idx < len(row):
            return row[idx].strip()
        return ""

    # 有効な役割値
    VALID_ROLES = {"生徒", "教師", "学年主任", "幹事"}

    PROV_STATUS_MAP  = {"参加": "attending", "不参加": "not_attending", "未定": "undecided",
                        "attending": "attending", "not_attending": "not_attending", "undecided": "undecided"}
    FINAL_STATUS_MAP = {"参加": "attending", "不参加": "not_attending", "直前キャンセル": "cancelled",
                        "attending": "attending", "not_attending": "not_attending", "cancelled": "cancelled"}
    PAY_STATUS_MAP   = {"未払い": "unpaid", "支払済み": "paid", "一部支払い": "partial",
                        "unpaid": "unpaid", "paid": "paid", "partial": "partial"}

    # CSVの行を先にパースしてから全削除→全追加
    import re
    from datetime import date as date_type
    new_participants = []
    skipped = 0

    for row in data_rows:
        if not row or all(cell.strip() == "" for cell in row):
            continue

        name          = get_col(row, idx_name)
        name_kana     = get_col(row, idx_name_kana)
        email         = get_col(row, idx_email).lower()
        class_        = get_col(row, idx_class)
        number        = get_col(row, idx_number)
        role          = get_col(row, idx_role) or "生徒"
        memo          = get_col(row, idx_memo)
        token         = get_col(row, idx_token)
        prov_status   = PROV_STATUS_MAP.get(get_col(row, idx_prov_status), "")
        final_status  = FINAL_STATUS_MAP.get(get_col(row, idx_final_status), "")
        companions_raw = get_col(row, idx_companions)
        companions    = int(companions_raw) if companions_raw.isdigit() else 0
        transfer_name = normalize_transfer_name(get_col(row, idx_transfer_name))
        remarks       = get_col(row, idx_remarks)
        pay_status    = PAY_STATUS_MAP.get(get_col(row, idx_pay_status), "")
        paid_raw      = get_col(row, idx_paid_amount)
        paid_amount   = int(paid_raw) if paid_raw.isdigit() else 0
        expected_raw  = get_col(row, idx_expected_amount)
        expected_amount = int(expected_raw) if expected_raw.isdigit() else 0
        pay_date_raw  = get_col(row, idx_payment_date)
        payment_date  = None
        if pay_date_raw:
            try:
                from datetime import datetime as dt
                payment_date = dt.strptime(pay_date_raw, "%Y-%m-%d").date()
            except ValueError:
                pass

        if not name:
            skipped += 1
            continue

        if role not in VALID_ROLES:
            role = "生徒"

        if role == "学年主任":
            class_ = ""
        elif class_:
            if not re.fullmatch(r'\d{2}', class_):
                digits = re.sub(r'\D', '', class_)
                class_ = digits[:2] if len(digits) >= 2 else digits

        if not email or "@" not in email:
            email = f"__no_email_{name}_{class_}_{role}@placeholder.local"

        new_participants.append(dict(
            name=name, name_kana=name_kana,
            email=email, class_name=class_,
            student_number=number, role=role, teacher_memo=memo,
            _token=token,
            _prov_status=prov_status, _final_status=final_status,
            _companions=companions, _transfer_name=transfer_name, _remarks=remarks,
            _pay_status=pay_status, _paid_amount=paid_amount,
            _expected_amount=expected_amount, _payment_date=payment_date,
        ))

    # 全テーブルをリセットして再登録
    MailLog.query.delete()
    from models import ProvisionalResponse, FinalResponse, Payment, BankImport
    FinalResponse.query.delete()
    ProvisionalResponse.query.delete()
    Payment.query.delete()
    BankImport.query.delete()
    Participant.query.delete()
    db.session.flush()

    for p_data in new_participants:
        token           = p_data.pop("_token")
        prov_status     = p_data.pop("_prov_status")
        final_status    = p_data.pop("_final_status")
        companions      = p_data.pop("_companions")
        transfer_name   = p_data.pop("_transfer_name")
        remarks         = p_data.pop("_remarks")
        pay_status      = p_data.pop("_pay_status")
        paid_amount     = p_data.pop("_paid_amount")
        expected_amount = p_data.pop("_expected_amount")
        payment_date    = p_data.pop("_payment_date")

        p = Participant(**p_data)
        if token:
            p.token = token
        db.session.add(p)
        db.session.flush()

        if prov_status:
            db.session.add(ProvisionalResponse(participant_id=p.id, status=prov_status))

        if final_status:
            db.session.add(FinalResponse(
                participant_id=p.id, status=final_status,
                companions=companions, transfer_name=transfer_name,
                remarks=remarks,
            ))
            db.session.add(Payment(
                participant_id=p.id,
                payment_status=pay_status or "unpaid",
                paid_amount=paid_amount,
                expected_amount=expected_amount,
                payment_date=payment_date,
                transfer_name=transfer_name,
            ))

    db.session.commit()
    flash(f"名簿を全件上書きしました: {len(new_participants)} 名登録、{skipped} 行スキップ", "success")
    return redirect(url_for("admin.participants"))


@admin_bp.route("/roster/add", methods=["POST"])
def roster_add():
    """参加者を1名手動追加"""
    name      = request.form.get("name", "").strip()
    name_kana = request.form.get("name_kana", "").strip()
    email     = request.form.get("email", "").strip().lower()
    class_    = request.form.get("class_name", "").strip()
    number    = request.form.get("student_number", "").strip()
    role      = request.form.get("role", "生徒").strip()
    memo      = request.form.get("teacher_memo", "").strip()

    if not name or not email or "@" not in email:
        flash("氏名と正しいメールアドレスを入力してください。", "danger")
        return redirect(url_for("admin.roster"))

    if not class_ and not name_kana:
        flash("クラス未入力の場合は氏名（カナ）が必須です。", "danger")
        return redirect(url_for("admin.roster"))

    if role == "学年主任":
        class_ = ""

    existing = Participant.query.filter_by(email=email).first()
    if existing:
        flash(f"メールアドレス {email} はすでに登録されています（{existing.name}）。", "warning")
        return redirect(url_for("admin.roster"))

    p = Participant(
        name=name, name_kana=name_kana, email=email,
        class_name=class_, student_number=number,
        role=role, teacher_memo=memo,
    )
    db.session.add(p)
    db.session.commit()
    flash(f"{name} を追加しました。", "success")
    return redirect(url_for("admin.roster"))


@admin_bp.route("/roster/delete/<int:participant_id>", methods=["POST"])
def roster_delete(participant_id):
    """参加者を削除（関連データも削除）"""
    participant = db.session.get(Participant, participant_id)
    if participant is None:
        flash("参加者が見つかりません。", "danger")
        return redirect(url_for("admin.roster"))

    name = participant.name
    db.session.delete(participant)
    db.session.commit()
    flash(f"{name} を削除しました。", "success")
    return redirect(url_for("admin.roster"))


@admin_bp.route("/roster/export")
def roster_export():
    """
    現在の参加者名簿を全データ付きでCSVエクスポートする。
    このCSVを再インポートすることで仮出欠・本出欠・入金状態を含め完全復元できる。
    """
    from models import ProvisionalResponse, FinalResponse, Payment
    participants = Participant.query.order_by(Participant.class_name, Participant.student_number).all()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "氏名", "氏名（カナ）",
        "メールアドレス", "クラス", "出席番号", "役割", "幹事メモ",
        "トークン", "仮出欠", "本出欠", "同伴者数", "振込名義", "備考",
        "入金ステータス", "入金金額", "支払予定金額", "支払日",
    ])

    PROV_LABELS  = {"attending": "参加", "not_attending": "不参加", "undecided": "未定"}
    FINAL_LABELS = {"attending": "参加", "not_attending": "不参加", "cancelled": "直前キャンセル"}
    PAY_LABELS   = {"unpaid": "未払い", "paid": "支払済み", "partial": "一部支払い"}

    for p in participants:
        email_out = "" if p.email and "@placeholder.local" in p.email else (p.email or "")
        prov  = p.latest_provisional
        final = p.latest_final
        pay   = p.payment
        writer.writerow([
            p.name,
            p.name_kana or "",
            email_out,
            p.class_name or "",
            p.student_number or "",
            p.role or "生徒",
            p.teacher_memo or "",
            p.token or "",
            PROV_LABELS.get(prov.status, "")    if prov  else "",
            FINAL_LABELS.get(final.status, "")  if final else "",
            final.companions                    if final else "",
            final.transfer_name                 if final else "",
            final.remarks                       if final else "",
            PAY_LABELS.get(pay.payment_status, "") if pay else "",
            pay.paid_amount                     if pay else "",
            pay.expected_amount                 if pay else "",
            pay.payment_date.strftime("%Y-%m-%d") if (pay and pay.payment_date) else "",
        ])

    csv_content = output.getvalue()

    return Response(
        "\ufeff" + csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=roster_export.csv"}
    )


@admin_bp.route("/final-form-preview")
def final_form_preview():
    """トークンを持つ代表者の本出欠フォーム実URLにリダイレクト"""
    participant = (
        Participant.query
        .filter(Participant.token.isnot(None))
        .order_by(Participant.class_name, Participant.student_number)
        .first()
    )
    if not participant:
        flash("本出欠URLが発行済みの参加者がいません。先にトークンを発行してください。", "warning")
        return redirect(url_for("admin.participants"))

    base_url = current_app.config.get("APP_BASE_URL", request.host_url.rstrip("/"))
    return redirect(f"{base_url}/form/final/{participant.token}")


GUIDE_DEFAULTS = {
    "guide_greeting": (
        "拝啓　時下ますますご清栄のこととお慶び申し上げます。\n"
        "このたび、皆様のご多忙にもかかわらず多数のご参加を賜り、誠にありがとうございます。\n"
        "懐かしい仲間と語らい、学生時代の思い出を振り返る素晴らしいひとときになりますよう、幹事一同心を込めてご用意いたしました。\n"
        "皆様のお越しを心よりお待ち申し上げております。"
    ),
    "guide_schedule": (
        "18:00|受付開始|\n"
        "18:30|開会|開会の辞・幹事挨拶\n"
        "|ご歓談・お食事|\n"
        "|レクリエーション|景品付きビンゴ大会\n"
        "|写真撮影|集合写真・フリー撮影\n"
        "21:00|閉会|閉会の辞"
    ),
    "guide_events": "景品付きビンゴ大会",
    "guide_afterparty": "3年次のクラスごとに開催予定です。詳細は当日ご案内いたします。",
    "guide_notes": (
        "会場内は禁煙です。お荷物はクロークをご利用ください。\n"
        "やむを得ずご欠席される場合は、事前にご連絡いただけますと幸いです。"
    ),
}


@admin_bp.route("/reunion-guide")
def reunion_guide():
    """同窓会ご案内PDF用プレビュー（ブラウザからPDF保存）"""
    settings = {}
    for s in AppSetting.query.all():
        settings[s.key] = s.value
    for k, v in GUIDE_DEFAULTS.items():
        settings.setdefault(k, v)

    schedule_rows = []
    for line in settings["guide_schedule"].splitlines():
        parts = line.split("|")
        schedule_rows.append({
            "time": parts[0] if len(parts) > 0 else "",
            "item": parts[1] if len(parts) > 1 else "",
            "note": parts[2] if len(parts) > 2 else "",
        })
    return render_template("admin/reunion_guide.html", settings=settings, schedule_rows=schedule_rows)


@admin_bp.route("/settings/reunion-guide", methods=["GET", "POST"])
def settings_reunion_guide():
    """同窓会ご案内の編集"""
    keys = list(GUIDE_DEFAULTS.keys())
    if request.method == "POST":
        for k in keys:
            val = request.form.get(k, "").strip()
            s = AppSetting.query.filter_by(key=k).first()
            if s:
                s.value = val
                s.updated_at = datetime.utcnow()
            else:
                db.session.add(AppSetting(key=k, value=val))
        db.session.commit()
        flash("案内状の内容を保存しました。", "success")
        return redirect(url_for("admin.settings_reunion_guide"))

    settings = {}
    for s in AppSetting.query.filter(AppSetting.key.in_(keys)).all():
        settings[s.key] = s.value
    for k, v in GUIDE_DEFAULTS.items():
        settings.setdefault(k, v)
    return render_template("admin/settings_reunion_guide.html", settings=settings)
