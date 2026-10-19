"""
utils.py - 汎用ユーティリティ
"""

# 振込先に選べる主要6行。
# templates/final_form.html の銀行アプリボタンと同じ6行・同じ並びに揃えている。
# どちらかを変えるときは両方直すこと。
MAJOR_BANKS = [
    "三菱UFJ銀行",
    "三井住友銀行",
    "みずほ銀行",
    "りそな銀行",
    "楽天銀行",
    "ゆうちょ銀行",
]

# プルダウンで「その他（手入力）」を表す値
OTHER_BANK_VALUE = "__other__"


# -----------------------------------------------
# 参加費（会費）
#
# 設定キー reunion_fee は「数字のみ」で保存する。
# 入金照合の予定金額（Payment.expected_amount）に使うため、
# 「未定」「8000円〜」のような文字列が入ると金額が0になり、
# いくら入金されても「一部支払い」のままになってしまう。
#
# メール本文のテンプレートは「会費　: {reunion_fee}円」の形なので、
# 変数には単位を付けず桁区切りだけを付けた文字列（例: "8,000"）を渡す。
# -----------------------------------------------

_FULL_TO_HALF_DIGIT = str.maketrans("０１２３４５６７８９", "0123456789")


def parse_fee_amount(value) -> int:
    """
    参加費の入力値から円単位の整数を取り出す。取れない場合は0。

    全角数字・桁区切りカンマ・「￥」「¥」「円」・前後の空白を許容する。
    「未定」のように数字を含まない値は0になる。
    """
    if value is None:
        return 0
    if isinstance(value, int):
        return max(0, value)
    import re
    text = str(value).translate(_FULL_TO_HALF_DIGIT).replace("，", ",")
    # 最初に現れた数値だけを採用する。
    # 全ての数字を連結すると「一般8000円/教員5000円」が 80005000 になってしまう。
    m = re.search(r"\d[\d,]*", text)
    if not m:
        return 0
    try:
        return int(m.group().replace(",", ""))
    except ValueError:
        return 0


def format_fee(amount: int) -> str:
    """円単位の整数を桁区切り文字列にする（単位は付けない）。0以下は空文字。"""
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return ""
    return f"{amount:,}" if amount > 0 else ""


# 先生（教師・学年主任）として扱う役割
TEACHER_ROLES = ("教師", "学年主任", "副担任")

# 参加費の設定キー
FEE_KEY_STUDENT = "reunion_fee"
FEE_KEY_TEACHER = "reunion_fee_teacher"


def is_teacher_role(role: str) -> bool:
    return (role or "") in TEACHER_ROLES


def get_student_fee() -> int:
    """生徒の参加費（円）。未設定・不正なら0。"""
    from models import AppSetting
    from flask import current_app
    s = AppSetting.query.filter_by(key=FEE_KEY_STUDENT).first()
    if s and s.value:
        return parse_fee_amount(s.value)
    return parse_fee_amount(current_app.config.get("REUNION_FEE", ""))


def get_teacher_fee() -> int:
    """
    先生の参加費（円）。未設定なら生徒と同額として扱う。

    「0円（無料）」にしたい場合は 0 を明示的に保存する。
    未設定（空）と 0 を区別するため、値の有無で判定している。
    """
    from models import AppSetting
    s = AppSetting.query.filter_by(key=FEE_KEY_TEACHER).first()
    if s is not None and s.value != "" and s.value is not None:
        return parse_fee_amount(s.value)
    return get_student_fee()


def is_teacher_fee_set() -> bool:
    """先生用の参加費が明示的に設定されているか（未設定なら生徒と同額）"""
    from models import AppSetting
    s = AppSetting.query.filter_by(key=FEE_KEY_TEACHER).first()
    return s is not None and s.value != "" and s.value is not None


def get_reunion_fee(role: str = None) -> int:
    """
    参加費を円単位の整数で返す。未設定・不正なら0。

    role を渡すと、先生（教師・学年主任）なら先生用の金額を返す。
    入金照合の予定金額はすべてこの関数を基準にする。
    """
    if role is not None and is_teacher_role(role):
        return get_teacher_fee()
    return get_student_fee()

_SMALL_TO_LARGE_KANA = str.maketrans(
    "ァィゥェォッャュョヮヵヶ",
    "アイウエオツヤユヨワカケ",
)

_HALF_TO_FULL_DIGIT = str.maketrans(
    "0123456789",
    "０１２３４５６７８９",
)

# 合成済み濁点・半濁点カタカナ → 基底文字＋独立濁点（゛U+309B / ゜U+309C）
_VOICED_TO_BASE = {
    'ガ': 'カ゛', 'ギ': 'キ゛', 'グ': 'ク゛', 'ゲ': 'ケ゛', 'ゴ': 'コ゛',
    'ザ': 'サ゛', 'ジ': 'シ゛', 'ズ': 'ス゛', 'ゼ': 'セ゛', 'ゾ': 'ソ゛',
    'ダ': 'タ゛', 'ヂ': 'チ゛', 'ヅ': 'ツ゛', 'デ': 'テ゛', 'ド': 'ト゛',
    'バ': 'ハ゛', 'ビ': 'ヒ゛', 'ブ': 'フ゛', 'ベ': 'ヘ゛', 'ボ': 'ホ゛',
    'パ': 'ハ゜', 'ピ': 'ヒ゜', 'プ': 'フ゜', 'ペ': 'ヘ゜', 'ポ': 'ホ゜',
    'ヴ': 'ウ゛',
}


def normalize_transfer_name(name: str) -> str:
    """振込名義を銀行標準形式に正規化する（小文字カタカナ→大文字、半角数字→全角、スペース除去）"""
    if not name:
        return name
    name = name.translate(_SMALL_TO_LARGE_KANA)
    name = name.translate(_HALF_TO_FULL_DIGIT)
    name = name.replace("　", "").replace(" ", "")
    return name


def decompose_voiced(name: str) -> str:
    """合成済み濁点・半濁点カタカナを基底文字＋独立記号に分解する（銀行システム向け）"""
    if not name:
        return name
    return "".join(_VOICED_TO_BASE.get(ch, ch) for ch in name)


# 全角カタカナ・全角数字 → 半角（濁点は独立記号のまま残す）
_FULL_TO_HALF = str.maketrans(
    "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲンー・０１２３４５６７８９",
    "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜｦﾝｰ･0123456789",
)

# 全角の濁点・半濁点記号 → 半角
_FULL_TO_HALF_MARK = str.maketrans("゛゜", "ﾞﾟ")


def to_halfwidth_transfer_name(name: str, halfwidth_mark: bool = True) -> str:
    """振込名義を半角表記に変換する。

    半角カタカナには濁点合字が存在しないため、必ず基底文字＋濁点記号になる。
    halfwidth_mark=True  → 濁点も半角（ﾔﾏﾀﾞ：濁点が半角1文字分）
    halfwidth_mark=False → 濁点は全角のまま（ﾔﾏﾀ゛：濁点が半角2文字分）
    """
    if not name:
        return name
    result = decompose_voiced(name).translate(_FULL_TO_HALF)
    if halfwidth_mark:
        result = result.translate(_FULL_TO_HALF_MARK)
    return result
