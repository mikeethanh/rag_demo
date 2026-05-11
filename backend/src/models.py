import logging

from sqlalchemy import Column, String, DateTime, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func

from database import engine, get_db

Base = declarative_base()
Base.metadata.create_all(bind=engine)
db = next(get_db())

logger = logging.getLogger(__name__)


class Document(Base):
    __tablename__ = 'document'

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False, default="")
    content = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


def insert_document(title: str, content: str):
    new_doc = Document(title=title, content=content)
    db.add(new_doc)
    db.commit()
    db.refresh(new_doc)
    logger.info("Created document: %s", new_doc)
    return new_doc
