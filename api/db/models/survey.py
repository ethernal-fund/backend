from sqlalchemy import Column, String, DateTime, Integer, Boolean, SmallInteger
from datetime import datetime
from api.db.base import Base

class AnonymousSurvey(Base):
    __tablename__ = "anonymous_surveys"

    id                       = Column(Integer, primary_key=True, autoincrement=True)
    age                      = Column(String(10), nullable=False)
    trust_traditional        = Column(SmallInteger, nullable=False)   # -2 a 2
    blockchain_familiarity   = Column(SmallInteger, nullable=False)
    retirement_concern       = Column(SmallInteger, nullable=False)
    has_retirement_plan      = Column(SmallInteger, nullable=False)
    values_in_retirement     = Column(SmallInteger, nullable=False)
    interested_in_blockchain = Column(SmallInteger, nullable=False)
    created_at               = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<AnonymousSurvey id={self.id} age={self.age}>"

class SurveyFollowUp(Base):
    __tablename__ = "survey_followups"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    survey_id       = Column(Integer, nullable=True)
    wants_more_info = Column(Boolean, nullable=False)
    email           = Column(String(255), nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<SurveyFollowUp id={self.id} wants_more_info={self.wants_more_info}>"