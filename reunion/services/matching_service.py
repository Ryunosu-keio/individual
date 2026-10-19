"""
services/matching_service.py - 銀行CSV照合サービス
"""
import difflib
import re
import unicodedata
import logging
from models import BankImport, Participant, Payment
from extensions import db
from utils import get_reunion_fee, _SMALL_TO_LARGE_KANA, _FULL_TO_HALF_DIGIT

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
    """
    照合用に名義を正規化する。

    銀行の振込名義は拗音・促音（ャュョッ等）を使えず大文字カナになるため、
    名簿側のカナ（例: キヨタリュウノスケ）と銀行側（キヨタリユウノスケ）で
    文字が食い違う。小文字カナを大文字カナへ寄せてから比較する。
    """
    if not name:
        return ""
    # 半角カナ→全角カナ（NFKC正規化）
    normalized = _hankaku_to_zenkaku_kana(name)
    # 銀行CSVのプレフィックス除去
    normalized = _BANK_PREFIXES.sub("", normalized)
    # 拗音・促音を大文字カナへ（ュ→ユ、ッ→ツ など）
    normalized = normalized.translate(_SMALL_TO_LARGE_KANA)
    # 全角数字→半角数字（名簿側は全角、銀行側は半角になりうる）
    normalized = normalized.translate(_FULL_TO_HALF_DIGIT)
    normalized = normalized.upper().strip()
    for char in [" ", "　", "-", "ー", ".", "．", ")", "）", "(", "（",
                 "ﬞ", "゛", "゜", "ﾞ", "ﾟ"]:
        normalized = normalized.replace(char, "")
    return normalized


def _leading_id(name: str) -> str:
    """名義の先頭にある学籍番号（クラス+出席番号）を取り出す。無ければ空文字。"""
    m = re.match(r"^(\d{3,6})", name)
    return m.group(1) if m else ""


def _similarity_score(a: str, b: str) -> float:
    """
    銀行CSVの名義と参加者の期待名義の類似度を 0.0〜1.0 で返す。

    名義の先頭には振込指示で学籍番号（クラス+出席番号）を付けてもらっている。
    両方に学籍番号があって不一致なら別人なので0を返す。
    これが無いと、同姓同名に近い別人（例: ナカタハルカ と タナカハルヤ）が
    文字の重なりだけで満点になり、入金が別人に紐づいてしまう。
    """
    a_norm = _normalize_name(a)
    b_norm = _normalize_name(b)
    if not a_norm or not b_norm:
        return 0.0
    if a_norm == b_norm:
        return 1.0

    # 学籍番号が両方にあって違うなら別人
    id_a, id_b = _leading_id(a_norm), _leading_id(b_norm)
    if id_a and id_b and id_a != id_b:
        return 0.0

    # 片方がもう片方に完全に含まれる（姓だけで振込んだ場合など）
    if a_norm in b_norm or b_norm in a_norm:
        return min(len(a_norm), len(b_norm)) / max(len(a_norm), len(b_norm))

    # 文字の並び順を考慮した類似度
    # （文字の重なりだけで数えると並べ替えただけの別人が満点になる）
    return difflib.SequenceMatcher(None, a_norm, b_norm).ratio()


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
      2. 現在の会費設定（参加者が先生なら先生用の会費）
    どちらも0なら0を返し、呼び出し側は金額判定をしない。
    """
    manual = payment.expected_amount or 0
    if manual > 0:
        return manual
    role = payment.participant.role if payment.participant else None
    return get_reunion_fee(role)


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


# 入金照合の判定ルール
MATCH_RULE_AT_LEAST = "at_least"   # 予定金額以上なら支払済み
MATCH_RULE_EXACT    = "exact"      # 予定金額とピッタリ一致なら支払済み
DEFAULT_MATCH_RULE  = MATCH_RULE_AT_LEAST

MATCH_RULE_LABELS = {
    MATCH_RULE_AT_LEAST: "予定金額以上",
    MATCH_RULE_EXACT:    "予定金額とピッタリ一致",
}
MATCH_RULE_DESCRIPTIONS = {
    MATCH_RULE_AT_LEAST: "振込額が予定金額以上なら「支払済み」。多めに振込まれた場合も支払済みになります。",
    MATCH_RULE_EXACT:    "振込額が予定金額と同額のときだけ「支払済み」。多ければ「過払い」として要確認で残ります。",
}


def get_payment_match_rule() -> str:
    """現在の入金照合ルールを返す（未設定なら「以上」）"""
    from models import AppSetting
    s = AppSetting.query.filter_by(key="payment_match_rule").first()
    value = s.value if (s and s.value) else ""
    return value if value in MATCH_RULE_LABELS else DEFAULT_MATCH_RULE


def compute_payment_status(paid: int, expected: int, rule: str = None) -> str:
    """
    入金額と予定金額から支払ステータスを決める。

    rule を省略すると現在の設定（payment_match_rule）に従う。
      MATCH_RULE_AT_LEAST : 予定金額以上なら paid（超過も paid）
      MATCH_RULE_EXACT    : 同額なら paid、超過は overpaid（過払い）

    expected が0（会費未設定）のときは満額かどうか判断できないため
    partial（一部支払い）に留める。入金自体は記録されているので
    「未払い」にはしない。該当レコードは入金管理画面で警告表示する。
    """
    if rule is None:
        rule = get_payment_match_rule()
    paid = paid or 0
    expected = expected or 0
    if paid <= 0:
        return "unpaid"
    if expected <= 0:
        return "partial"
    if paid == expected:
        return "paid"
    if paid > expected:
        return "paid" if rule == MATCH_RULE_AT_LEAST else "overpaid"
    return "partial"


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
            if p.payment_status in Payment.RECEIVED_STATUSES:
                p.payment_status = "unpaid"
    bank_import.matched_participant_id = None
    bank_import.match_status = "unmatched"
    db.session.commit()
