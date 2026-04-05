"""
services/token_service.py - トークン生成・管理サービス

本出欠フォームURLは participant ごとに一意のトークンを発行する。
トークンはURLに含まれ、本人確認の代わりとなる。
例: http://localhost:5000/form/final/abc123def456...
"""
import secrets
import string
from models import Participant
from extensions import db


def generate_token(length: int = 32) -> str:
    """
    URLセーフなランダムトークンを生成する。
    secrets モジュールを使うことで、推測困難なトークンを生成する。
    """
    # URLセーフな文字のみ使用（英数字 + - _ ）
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def ensure_token(participant: Participant) -> str:
    """
    参加者にトークンがなければ生成して保存する。
    既にある場合はそのまま返す（冪等操作）。
    """
    if not participant.token:
        # 衝突チェック付きでトークン生成
        while True:
            token = generate_token()
            # 同じトークンが存在しないか確認
            existing = Participant.query.filter_by(token=token).first()
            if existing is None:
                break
        participant.token = token
        db.session.commit()
    return participant.token


def get_participant_by_token(token: str):
    """
    トークンから参加者を取得する。
    見つからない場合は None を返す。
    """
    if not token:
        return None
    return Participant.query.filter_by(token=token).first()


def generate_final_url(participant: Participant, base_url: str) -> str:
    """
    本出欠フォームのURLを生成する。
    トークンがなければ自動生成する。
    """
    token = ensure_token(participant)
    return f"{base_url.rstrip('/')}/form/final/{token}"
