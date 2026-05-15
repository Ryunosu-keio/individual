"""
utils.py - 汎用ユーティリティ
"""

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
