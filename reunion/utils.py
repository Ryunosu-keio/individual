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


def normalize_transfer_name(name: str) -> str:
    """振込名義を銀行標準形式に正規化する（小文字カタカナ→大文字、半角数字→全角、スペース除去）"""
    if not name:
        return name
    name = name.translate(_SMALL_TO_LARGE_KANA)
    name = name.translate(_HALF_TO_FULL_DIGIT)
    name = name.replace("　", "").replace(" ", "")
    return name
