"""
utils.py - 汎用ユーティリティ
"""

_SMALL_TO_LARGE_KANA = str.maketrans(
    "ァィゥェォッャュョヮヵヶ",
    "アイウエオツヤユヨワカケ",
)


def normalize_transfer_name(name: str) -> str:
    """振込名義を銀行標準形式に正規化する（小文字カタカナ→大文字）"""
    if not name:
        return name
    return name.translate(_SMALL_TO_LARGE_KANA)
