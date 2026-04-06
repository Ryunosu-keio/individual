"""
wsgi.py - PythonAnywhere用 WSGIエントリーポイント
"""
import sys
import os

# PythonAnywhereのプロジェクトパスを追加
project_home = os.path.dirname(os.path.abspath(__file__))
if project_home not in sys.path:
    sys.path.insert(0, project_home)

from app import create_app

application = create_app()
