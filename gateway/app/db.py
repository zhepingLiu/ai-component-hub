# app/db.py
from sqlalchemy import create_engine, Column, Integer, String, DateTime, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import datetime
import os

# SQLite 文件路径，可在 docker-compose 里映射到宿主机
DB_PATH = os.getenv("DB_PATH", "/app/data/gateway.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

# 创建 engine / session / base
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 日志表定义
class Log(Base):
    __tablename__ = "logs"
    id = Column(Integer, primary_key=True, index=True)
    trace_id = Column(String, index=True)
    path = Column(String)
    method = Column(String)
    status = Column(Integer)
    ms = Column(Integer)
    time = Column(DateTime, default=datetime.datetime.utcnow)
    detail = Column(JSON)

# 初始化数据库
def init_db():
    Base.metadata.create_all(bind=engine)
