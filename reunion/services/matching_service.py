"""
services/matching_service.py - 銀行CSV照合サービス
"""
import re
import unicodedata
import logging
from models import BankImport, Participant, Payment
from extensions import db
from utils import get_reunion_fee

logger = logging.getLogger(__name__)

# 銀行CSVの摘要欄から除去するプレフィックス（長いものを先に並べる）
#
# 三井住友銀行の明細は「パソコン振替　ﾖﾐｶﾞﾅ」「振込　ﾖﾐｶﾞﾅ」「口座振込　ﾖﾘｶﾞﾅ」の形式。
# ここで除去しないと名義に「パソコン振替」が残り、照合スコアが
# しきい値（0.8）を下回って全件手作業になる。
_BANK_PREFIX_WORDS = [
    # 三井住友銀行
    "パソコン振替", "パソコン振込",
    "口座振込", "総合振込", "他行宛振込", "他行振込",
    # 他行で見られる表記
    "ＡＴＭ振込", "ATM振込", "ATM振替",
    "モバイル振込", "ネット振込", "テレ振込", "テレホン振込",
    "自動振込", "給料振込", "給与振込", "カード振込",
    "振込", "振替",
]
_BANK_PREFIXES = re.compile(
    r'^(?:' + "|".join(re.escape(w) for w in _BANK_PREFIX_WORDS) + r')\s*'
)


def _hankaku_to_zenkaku_kana(text: str) -> str:
    """半角カナを全角カナに変換する"""
    return unicodedata.normalize("NFKC", text)


def _normalize_name(name: str) -> str:
    if not name:
        return ""
    # 半角カナ→全角カナ（NFKC正規化）
    normalized = _hankaku_to_zenkaku_kana(name)
    # 銀行CSVのプレフィックス除去
    normalized = _BANK_PREFIXES.sub("", normalized)
    normalized = normalized.upper().strip()
    for char in [" ", "　", "-", "ー", ".", "．", ")", "）", "(", "（"]:
        normalized = normalized.replace(char, "")
    return normalized


def _similarity_score(a: str, b: str) -> float:
    a_norm = _normalize_name(a)
    b_norm = _normalize_name(b)
    if not a_norm or not b_norm:
        return 0.0
    if a_norm == b_norm:
        return 1.0
    if a_norm in b_norm or b_norm in a_norm:
        shorter = min(len(a_norm), len(b_norm))
        longer = max(len(a_norm), len(b_norm))
        return shorter / longer
    common = sum(1 for c in a_norm if c in b_norm)
    longer = max(len(a_norm), len(b_norm))
    return common / longer if longer > 0 else 0.0


def _expected_transfer_name(participant) -> str:
    """参加者の期待振込名義（新カナ優先）を返す"""
    student_id = ""
    if participant.class_name and participant.student_number:
        student_id = f"{participant.class_name}{participant.student_number.zfill(2)}"
    kana = participant.display_name_kana or ""
    if student_id and kana:
        return f"{student_id} {kana}"
    return kana or participant.display_name or ""


def _alt_transfer_name(participant) -> str:
    """旧カナによる照合候補（旧姓で振込む人向け）"""
    student_id = ""
    if participant.class_name and participant.student_number:
        student_id = f"{participant.class_name}{participant.student_number.zfill(2)}"
    kana = participant.name_kana or ""
    if student_id and kana:
        return f"{student_id} {kana}"
    return kana or participant.name or ""


def run_auto_matching(threshold: float = 0.8) -> dict:
    """未照合のCSVレコードに対して自動照合を実行する"""
    unmatched_imports = BankImport.query.filter_by(match_status="unmatched").all()
    all_participants = Participant.query.all()

    results = {"auto_confirmed": 0, "matched": 0, "unmatched": 0}

    for bank_import in unmatched_imports:
        best_score = 0.0
        best_participant = None

        for participant in all_participants:
            # 照合候補名を集める（本出欠の振込名義 + 名簿から生成した名義）
            candidate_names = []
            final = participant.latest_final
            if final and final.transfer_name:
                candidate_names.append(final.transfer_name)
            expected = _expected_transfer_name(participant)
            if expected:
                candidate_names.append(expected)
            # 旧カナでも照合（旧姓で振込む場合に対応）
            alt = _alt_transfer_name(participant)
            if alt and alt != expected:
                candidate_names.append(alt)
            # カナ氏名単体でも照合（新旧両方）
            if participant.name_kana:
                candidate_names.append(participant.name_kana)

            for name in candidate_names:
                score = _similarity_score(bank_import.raw_name, name)
                if score > best_score:
                    best_score = score
                    best_participant = participant

        if best_participant and best_score >= threshold:
            bank_import.matched_participant_id = best_participant.id
            if best_score == 1.0:
                bank_import.match_status = "confirmed"
                _update_payment_from_import(best_participant, bank_import)
                db.session.flush()
                results["auto_confirmed"] += 1
            else:
                bank_import.match_status = "matched"
                results["matched"] += 1
        else:
            results["unmatched"] += 1

    db.session.commit()
    return results


def confirm_match(bank_import_id: int, participant_id: int) -> Payment:
    """管理者が手動で照合を確定する"""
    bank_import = db.session.get(BankImport, bank_import_id)
    participant = db.session.get(Participant, participant_id)
    bank_import.matched_participant_id = participant.id
    bank_import.match_status = "confirmed"
    payment = _update_payment_from_import(participant, bank_import)
    db.session.commit()
    return payment


def resolve_expected_amount(payment: Payment) -> int:
    """
    入金判定に使う予定金額（円）を返す。

    優先順位:
      1. Payment.expected_amount（本出欠回答時に確定した額／管理画面で手動変更した額）
      2. 現在の会費設定（reunion_fee）
    どちらも0なら0を返し、呼び出し側は金額判定をしない。
    """
    manual = payment.expected_amount or 0
    if manual > 0:
        return manual
    return get_reunion_fee()


def _update_payment_from_import(participant: Participant, bank_import: BankImport) -> Payment:
    payment = participant.payment
    if payment is None:
        payment = Payment(participant_id=participant.id)
        db.session.add(payment)
    payment.bank_csv_matched = True
    payment.bank_csv_amount = bank_import.raw_amount
    payment.bank_csv_date = bank_import.raw_date
    payment.bank_csv_raw_name = bank_import.raw_name
    payment.bank_import_id = bank_import.id

    expected = resolve_expected_amount(payment)
    # 予定金額が未設定のレコードには、判定に使った会費を書き戻しておく
    # （画面上で「予定金額0円」のまま残ると、何と比較されたのか分からなくなる）
    if not (payment.expected_amount or 0) and expected > 0:
        payment.expected_amount = expected

    if bank_import.raw_amount > 0:
        payment.paid_amount = bank_import.raw_amount
        payment.payment_date = bank_import.raw_date
        payment.payment_status = compute_payment_status(bank_import.raw_amount, expected)
    return payment


def compute_payment_status(paid: int, expected: int) -> str:
    """
    入金額と予定金額から支払ステータスを決める。

    expected が0（会費未設定）のときは満額かどうか判断できないため
    partial（一部支払い）に留める。入金自体は記録されているので
    「未払い」にはしない。該当レコードは入金管理画面で警告表示する。
    """
    paid = paid or 0
    expected = expected or 0
    if paid <= 0:
        return "unpaid"
    if expected <= 0:
        return "partial"
    return "paid" if paid >= expected else "partial"


def unmatch(bank_import_id: int) -> None:
    """照合を解除して未照合状態に戻す"""
    bank_import = db.session.get(BankImport, bank_import_id)
    if bank_import.matched_participant_id:
        participant = db.session.get(Participant, bank_import.matched_participant_id)
        if participant and participant.payment and participant.payment.bank_import_id == bank_import.id:
            p = participant.payment
            p.bank_csv_matched = False
            p.bank_csv_amount = None
            p.bank_csv_date = None
            p.bank_csv_raw_name = ""
            p.bank_import_id = None
            if p.payment_status in ("paid", "partial"):
                p.payment_status = "unpaid"
    bank_import.matched_participant_id = None
    bank_import.match_status = "unmatched"
    db.session.commit()
