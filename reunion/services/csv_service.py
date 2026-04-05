"""
services/csv_service.py - 銀行CSVの取込・パース処理

対応フォーマット:
  - 汎用CSVフォーマット（列名で自動判別）
  - 列: 日付, 振込名義（名前）, 金額（入金）

銀行によってCSVのフォーマットが異なる。
よくある列名のパターンを複数定義し、自動マッチングする。
"""
import csv
import io
import logging
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)

# 日付列として認識する列名（小文字で比較）
DATE_COLUMN_CANDIDATES = [
    "日付", "取引日", "date", "取引年月日", "振込日"
]

# 振込名義列として認識する列名
NAME_COLUMN_CANDIDATES = [
    "振込名義", "振込人名", "名義", "name", "相手名", "摘要", "振込人"
]

# 入金金額列として認識する列名
AMOUNT_COLUMN_CANDIDATES = [
    "入金額", "入金", "金額", "amount", "入金金額", "お受取金額"
]


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

    # エンコーディングを自動判別（UTF-8 → Shift_JIS の順で試す）
    text = None
    for encoding in ["utf-8-sig", "shift_jis", "cp932", "utf-8"]:
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

    # ヘッダー行を探す（最初の行にヘッダーがあると仮定）
    headers = rows[0]
    date_idx = _find_column(headers, DATE_COLUMN_CANDIDATES)
    name_idx = _find_column(headers, NAME_COLUMN_CANDIDATES)
    amount_idx = _find_column(headers, AMOUNT_COLUMN_CANDIDATES)

    if name_idx is None or amount_idx is None:
        raise ValueError(
            f"CSVの列を認識できませんでした。\n"
            f"検出された列: {headers}\n"
            f"「振込名義」「金額」に相当する列が必要です。"
        )

    # データ行を処理
    for row_num, row in enumerate(rows[1:], start=2):
        if not row or all(cell.strip() == "" for cell in row):
            continue  # 空行はスキップ

        try:
            raw_name = row[name_idx].strip() if name_idx < len(row) else ""
            raw_amount = _parse_amount(row[amount_idx]) if amount_idx < len(row) else 0
            raw_date = None
            if date_idx is not None and date_idx < len(row):
                raw_date = _parse_date(row[date_idx])

            # 金額が0以下の行（出金など）はスキップ
            if raw_amount <= 0:
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

    logger.info(f"CSVパース完了: {len(records)} 件取込 (ファイル: {filename})")
    return records


def save_bank_imports(records: list) -> list:
    """
    パースした銀行CSVレコードをDBに保存する。
    重複チェック（同一ファイル名・名義・日付・金額）を行い、重複はスキップする。
    """
    from models import BankImport
    from extensions import db

    saved = []
    skipped = 0

    for rec in records:
        existing = BankImport.query.filter_by(
            filename=rec["filename"],
            raw_name=rec["raw_name"],
            raw_date=rec["raw_date"],
            raw_amount=rec["raw_amount"],
        ).first()

        if existing:
            skipped += 1
            continue

        bank_import = BankImport(
            filename=rec["filename"],
            raw_name=rec["raw_name"],
            raw_date=rec["raw_date"],
            raw_amount=rec["raw_amount"],
            match_status="unmatched",
        )
        db.session.add(bank_import)
        saved.append(bank_import)

    db.session.commit()
    logger.info(f"DB保存完了: {len(saved)} 件保存, {skipped} 件スキップ（重複）")
    return saved
