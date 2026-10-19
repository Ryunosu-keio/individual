"""
services/csv_service.py - 銀行CSVの取込・パース処理

対応フォーマット:
  - 汎用CSVフォーマット（列名で自動判別）
  - 列: 日付, 振込名義（名前）, 金額（入金）

銀行によってCSVのフォーマットが異なる。
よくある列名のパターンを複数定義し、自動マッチングする。

三井住友銀行（SMBC）の明細CSV（2026年8月〜の取引銀行）:
  エンコーディング: cp932
  ヘッダー: 年月日,お引出し,お預入れ,お取り扱い内容,残高,メモ,ラベル
  入出金が別列に分かれており、入金は「お預入れ」列に入る。
  「お引出し」のみの行（出金）は入金額0として自動スキップされる。
  摘要は「パソコン振替　ﾖﾐｶﾞﾅ」のようにプレフィックス＋全角スペース＋名義。
  プレフィックスの除去は services/matching_service.py で行う。
"""
import csv
import io
import logging
import unicodedata
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)

# 日付列として認識する列名（小文字で比較）
DATE_COLUMN_CANDIDATES = [
    "日付", "取引日", "date", "取引年月日", "振込日", "年月日"
]

# 振込名義列として認識する列名
# 「お取り扱い内容」は三井住友銀行の表記。送り仮名の有無でゆれるため両方登録する。
NAME_COLUMN_CANDIDATES = [
    "振込名義", "振込人名", "名義", "name", "相手名", "摘要", "振込人",
    "お取り扱い内容", "お取扱い内容", "お取扱内容", "取扱内容", "取り扱い内容",
    "内容", "適用",
    "お取引内容", "取引内容", "取引摘要",
]

# 入金金額列として認識する列名
# 「お預入れ」は三井住友銀行の表記。
AMOUNT_COLUMN_CANDIDATES = [
    "入金額", "入金", "金額", "amount", "入金金額", "お受取金額",
    "お預入れ", "お預け入れ", "預入れ", "預入", "預入金額",
    "お預り金額", "預り金額", "お預かり金額", "お預入金額",
]

# 出金列として認識する列名（入金列と取り違えないよう明示的に除外する）
WITHDRAWAL_COLUMN_CANDIDATES = [
    "お引出し", "お引き出し", "引出し", "引出", "出金", "出金額", "お支払金額",
    "支払金額", "お引落し金額",
]

# 摘要がこれらを含む行は振込ではないため取込まない（利息の入金など）
NON_TRANSFER_KEYWORDS = ["利息"]


def _find_column(headers: list, candidates: list) -> Optional[int]:
    """
    ヘッダー行から候補列名にマッチする列インデックスを返す。
    見つからない場合は None を返す。
    """
    headers_lower = [h.strip().lower() for h in headers]
    for candidate in candidates:
        try:
            return headers_lower.index(candidate.lower())
        except ValueError:
            continue
    return None


def _parse_date(date_str: str) -> Optional[date]:
    """
    さまざまな日付フォーマットをパースして date オブジェクトを返す。
    パースできない場合は None を返す。
    """
    date_str = date_str.strip()
    formats = [
        "%Y/%m/%d",
        "%Y-%m-%d",
        "%Y.%m.%d",
        "%Y年%m月%d日",
        "%m/%d/%Y",
        "%Y%m%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    logger.warning(f"日付のパースに失敗: {date_str}")
    return None


def _parse_amount(amount_str: str) -> int:
    """
    金額文字列を整数（円）に変換する。
    カンマや円記号を除去してから変換する。
    """
    cleaned = amount_str.strip().replace(",", "").replace("円", "").replace("¥", "").replace(" ", "")
    if not cleaned:
        return 0
    try:
        return int(float(cleaned))
    except ValueError:
        logger.warning(f"金額のパースに失敗: {amount_str}")
        return 0


def parse_bank_csv(file_content: bytes, filename: str = "") -> list:
    """
    銀行CSVファイルのバイト列を解析して、レコードのリストを返す。

    Returns:
        list[dict]: 各行のデータ。キー: raw_name, raw_date, raw_amount, filename

    Raises:
        ValueError: CSVの形式が認識できない場合
    """
    records = []

    # エンコーディングを自動判別
    # cp932 は shift_jis の上位互換（NEC特殊文字・IBM拡張文字を含む）なので先に試す。
    text = None
    for encoding in ["utf-8-sig", "cp932", "shift_jis", "utf-8"]:
        try:
            text = file_content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue

    if text is None:
        raise ValueError("CSVファイルのエンコーディングを判別できませんでした。UTF-8かShift_JISで保存してください。")

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)

    if not rows:
        raise ValueError("CSVファイルが空です。")

    # ヘッダー行をスキャンして探す（上部メタデータ行をスキップ）
    all_candidates = set(c.lower() for c in DATE_COLUMN_CANDIDATES + NAME_COLUMN_CANDIDATES + AMOUNT_COLUMN_CANDIDATES)
    header_row_idx = None
    for i, row in enumerate(rows):
        cells_lower = [c.strip().lower() for c in row]
        if sum(1 for c in cells_lower if c in all_candidates) >= 2:
            header_row_idx = i
            break

    if header_row_idx is None:
        raise ValueError(
            f"CSVのヘッダー行を認識できませんでした。\n"
            f"「日付」「振込名義」「金額」に相当する列名が必要です。"
        )

    headers = rows[header_row_idx]
    date_idx = _find_column(headers, DATE_COLUMN_CANDIDATES)
    name_idx = _find_column(headers, NAME_COLUMN_CANDIDATES)
    amount_idx = _find_column(headers, AMOUNT_COLUMN_CANDIDATES)
    withdrawal_idx = _find_column(headers, WITHDRAWAL_COLUMN_CANDIDATES)

    # 入金列と出金列が同じ列に解決された場合は入金列を見つけられていない
    # （「金額」1列だけの明細で出金列名と衝突したケース）
    if amount_idx is not None and amount_idx == withdrawal_idx:
        amount_idx = None

    if name_idx is None or amount_idx is None:
        raise ValueError(
            f"CSVの列を認識できませんでした。\n"
            f"検出された列: {headers}\n"
            f"「振込名義」「金額」に相当する列が必要です。"
        )

    logger.info(
        f"CSV列を検出: 日付={date_idx} 名義={name_idx} 入金={amount_idx} "
        f"出金={withdrawal_idx} (ファイル: {filename})"
    )

    # データ行を処理
    skipped_outgoing = 0   # 出金・入金なしの行
    skipped_non_transfer = 0  # 利息など振込でない入金

    for row_num, row in enumerate(rows[header_row_idx + 1:], start=header_row_idx + 2):
        if not row or all(cell.strip() == "" for cell in row):
            continue  # 空行はスキップ

        try:
            raw_name = unicodedata.normalize("NFKC", row[name_idx].strip()) if name_idx < len(row) else ""
            raw_amount = _parse_amount(row[amount_idx]) if amount_idx < len(row) else 0
            raw_date = None
            if date_idx is not None and date_idx < len(row):
                raw_date = _parse_date(row[date_idx])

            # 入金額が0以下の行はスキップ
            # 三井住友のように入出金が別列の明細では、出金行は入金列が空になる
            if raw_amount <= 0:
                skipped_outgoing += 1
                continue

            # 利息の入金など、振込ではない行はスキップ
            if any(kw in raw_name for kw in NON_TRANSFER_KEYWORDS):
                skipped_non_transfer += 1
                continue

            records.append({
                "raw_name": raw_name,
                "raw_date": raw_date,
                "raw_amount": raw_amount,
                "filename": filename,
            })

        except Exception as e:
            logger.warning(f"行 {row_num} の解析をスキップ: {e}")
            continue

    logger.info(
        f"CSVパース完了: {len(records)} 件取込 / "
        f"{skipped_outgoing} 件スキップ(出金・入金なし) / "
        f"{skipped_non_transfer} 件スキップ(利息など) (ファイル: {filename})"
    )
    return records


def save_bank_imports(records: list, replace: bool = True) -> dict:
    """
    パースした銀行CSVレコードをDBに保存する。

    replace=True（既定）: 取込済みのレコードを全て削除してから保存する。
      銀行明細は毎回「期間分まとめて」ダウンロードするため、取込を重ねると
      同じ取引が別ファイル名で積み上がる。常に最新のファイルの内容だけを
      残すことで、画面に出る明細をダウンロードしたCSVと一致させる。

      確定済み（confirmed）の照合から作られた Payment はお金が届いた記録なので
      消さない。削除するレコードを指していた Payment.bank_import_id だけ外す。

    replace=False: 既存を残し、同一内容（ファイル名・名義・日付・金額）はスキップ。

    Returns:
        dict: {"saved": 保存件数, "deleted": 削除件数, "skipped": スキップ件数}
    """
    from models import BankImport, Payment
    from extensions import db

    deleted = 0
    if replace:
        old_ids = [r.id for r in BankImport.query.all()]
        if old_ids:
            # 削除対象を参照している Payment の参照を外す（入金記録自体は残す）
            for pay in Payment.query.filter(Payment.bank_import_id.in_(old_ids)).all():
                pay.bank_import_id = None
            db.session.flush()
            deleted = BankImport.query.filter(BankImport.id.in_(old_ids)).delete(
                synchronize_session=False
            )
            db.session.flush()

    saved = 0
    skipped = 0
    for rec in records:
        if not replace:
            existing = BankImport.query.filter_by(
                filename=rec["filename"],
                raw_name=rec["raw_name"],
                raw_date=rec["raw_date"],
                raw_amount=rec["raw_amount"],
            ).first()
            if existing:
                skipped += 1
                continue

        db.session.add(BankImport(
            filename=rec["filename"],
            raw_name=rec["raw_name"],
            raw_date=rec["raw_date"],
            raw_amount=rec["raw_amount"],
            match_status="unmatched",
        ))
        saved += 1

    db.session.commit()
    logger.info(
        f"DB保存完了: {saved} 件保存 / {deleted} 件削除(入れ替え) / {skipped} 件スキップ"
    )
    return {"saved": saved, "deleted": deleted, "skipped": skipped}
