"""旁路写库子包。

仅在 Config.DATABASE_URL 非空时由 main.py 实例化；其余路径不应直接 import 本包，
避免在未安装 SQLAlchemy/psycopg 的纯 OCR/LLM 环境上 ImportError。
"""
