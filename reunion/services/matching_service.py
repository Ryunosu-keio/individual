"""
services/matching_service.py - 銀行CSV照合サービス
"""
import logging
from models import BankImport, Participant, Payment
from extensions import db

logger = logging.getLogger(__name__)


def _normalize_name(name: str) -> str:
    if not name:
        return ""
    normalized = name.upper().strip()
    for char in [" ", "　", "-", "ー", ".", "．"]:
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


def run_auto_matching(threshold: float = 0.8) -> dict:
    """未照合のCSVレコードに対して自動照合を実行する"""
    unmatched_imports = BankImport.query.filter_by(match_status="unmatched").all()
    participants_with_final = Participant.query.all()

    results = {"auto_confirmed": 0, "matched": 0, "unmatched": 0}

    for bank_import in unmatched_imports:
        best_score = 0.0
        best_participant = None

        for participant in participants_with_final:
            final = participant.latest_final
            if not final or not final.transfer_name:
                continue
            score = _similarity_score(bank_import.raw_name, final.transfer_name)
            if score > best_score:
                best_score = score
                best_participant = participant

        if best_participant and best_score >= threshold:
            bank_import.matched_participant_id = best_participant.id
            if best_score == 1.0:
                bank_import.match_status = "confirmed"
                _update_payment_from_import(best_participant, bank_import)
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
    if payment.expected_amount > 0 and bank_import.raw_amount >= payment.expected_amount:
        payment.payment_status = "paid"
        payment.paid_amount = bank_import.raw_amount
        payment.payment_date = bank_import.raw_date
    elif bank_import.raw_amount > 0:
        payment.payment_status = "partial"
        payment.paid_amount = bank_import.raw_amount
        payment.payment_date = bank_import.raw_date
    return payment


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
