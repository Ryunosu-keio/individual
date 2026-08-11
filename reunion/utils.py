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
