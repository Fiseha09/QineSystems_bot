import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Boolean, DateTime,
    ForeignKey, Text, Float, JSON, UniqueConstraint, func
)
from sqlalchemy.orm import relationship
from database import Base


class FeatureKey(str, enum.Enum):
    MODULES = "modules"               # Default Free
    MID_EXAMS = "mid_exams"           # Default Free
    NOTES = "notes"                   # Default Free
    QUIZ = "quiz"                     # Default Premium (Has Level Selection)
    FINAL_EXAMS = "final_exams"       # Default Premium
    EXAM_TRAINER = "exam_trainer"     # Default Premium
    FLASHCARDS = "flashcards"         # Default Premium
    PRACTICE_Q = "practice_questions" # Default Premium
    SEARCH = "search"                 # Default Premium
    STUDY_TOOLS = "study_tools"       # Default Premium
    MORE_RESOURCES = "more_resources" # Default Premium
    AI_ASSISTANT = "ai_assistant"     # Default Premium


class FeatureConfig(Base):
    """Controls Free vs Premium status and dynamic Telegram Menu items."""
    __tablename__ = "feature_configs"
    key = Column(String(50), primary_key=True)
    title = Column(String(100), nullable=False)
    is_free = Column(Boolean, default=False, nullable=False)
    is_enabled = Column(Boolean, default=True, nullable=False)
    has_levels = Column(Boolean, default=False, nullable=False)  # True ONLY for Quiz
    sort_order = Column(Integer, default=0)


class Course(Base):
    """Central Course Catalogue."""
    __tablename__ = "courses"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(150), unique=True, nullable=False)
    year_level = Column(String(30), default="1st Year", nullable=False, index=True)  # e.g., '1st Year', '2nd Year', '3rd Year'
    is_active = Column(Boolean, default=True)
    chapters = relationship("Chapter", back_populates="course", cascade="all, delete-orphan")


class Chapter(Base):
    __tablename__ = "chapters"
    id = Column(Integer, primary_key=True, autoincrement=True)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    title = Column(String(200), nullable=False)
    is_published = Column(Boolean, default=True)

    course = relationship("Course", back_populates="chapters")
    resources = relationship("LearningResource", back_populates="chapter", cascade="all, delete-orphan")
    quiz_questions = relationship("QuizQuestion", back_populates="chapter", cascade="all, delete-orphan")
    flashcards = relationship("Flashcard", back_populates="chapter", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("course_id", "chapter_number", name="uq_course_chapter"),)


class LearningResource(Base):
    """Stores Modules, Mid-Exams, Notes, Final Exams, Exam Trainer, Practice Questions, More Resources."""
    __tablename__ = "learning_resources"
    id = Column(Integer, primary_key=True, autoincrement=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id"), nullable=False)
    feature_key = Column(String(50), ForeignKey("feature_configs.key"), nullable=False)
    title = Column(String(255), nullable=False)
    content_type = Column(String(30), nullable=False)  # 'telegram_file', 'text', 'link'
    telegram_file_id = Column(String(255), nullable=True)
    text_content = Column(Text, nullable=True)
    is_free_override = Column(Boolean, nullable=True)  # Optional per-resource override
    is_published = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    chapter = relationship("Chapter", back_populates="resources")


class QuizQuestion(Base):
    """Time-based quiz questions with level selection and mandatory explanations."""
    __tablename__ = "quiz_questions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id"), nullable=False)
    level = Column(String(50), nullable=False)
    question_text = Column(Text, nullable=False)
    options = Column(JSON, nullable=False)
    correct_option = Column(Integer, nullable=False)
    explanation = Column(Text, nullable=False)
    time_limit_sec = Column(Integer, default=45)
    is_published = Column(Boolean, default=True)

    chapter = relationship("Chapter", back_populates="quiz_questions")


class Flashcard(Base):
    """Chapter-based flashcards."""
    __tablename__ = "flashcards"
    id = Column(Integer, primary_key=True, autoincrement=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id"), nullable=False)
    front_text = Column(Text, nullable=False)
    back_text = Column(Text, nullable=False)
    is_published = Column(Boolean, default=True)

    chapter = relationship("Chapter", back_populates="flashcards")


class Student(Base):
    __tablename__ = "students"
    telegram_id = Column(BigInteger, primary_key=True)
    username = Column(String(100), nullable=True)
    full_name = Column(String(200), nullable=False)
    plan_code = Column(String(30), default="FREE")  # 'FREE', 'QINE_PLUS', 'QINE_PRO'
    subscription_expires_at = Column(DateTime, nullable=True)
    referral_code = Column(String(32), unique=True, nullable=False)
    referred_by_id = Column(BigInteger, ForeignKey("students.telegram_id"), nullable=True)
    ai_daily_used = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class SubscriptionPlan(Base):
    """Admin-configurable plans: Free (0), Qine Plus (300 ETB/yr), Qine Pro (500 ETB/yr)."""
    __tablename__ = "subscription_plans"
    code = Column(String(30), primary_key=True)
    name = Column(String(100), nullable=False)
    price_etb = Column(Float, nullable=False)
    duration_days = Column(Integer, nullable=False)
    benefits_text = Column(Text, nullable=False)
    daily_ai_quota = Column(Integer, default=5)
    is_active = Column(Boolean, default=True)


class PaymentMethodConfig(Base):
    """Telebirr, CBE, Bank of Abyssinia - shown ONLY when verified & enabled by Admin."""
    __tablename__ = "payment_methods"
    code = Column(String(30), primary_key=True)
    provider_name = Column(String(100), nullable=False)
    account_holder = Column(String(150), nullable=False)
    account_number = Column(String(100), nullable=False)
    instructions = Column(Text, nullable=True)
    is_verified_active = Column(Boolean, default=False)


class PaymentReceipt(Base):
    __tablename__ = "payment_receipts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(BigInteger, ForeignKey("students.telegram_id"), nullable=False)
    plan_code = Column(String(30), ForeignKey("subscription_plans.code"), nullable=False)
    payment_method = Column(String(30), nullable=False)
    transaction_ref = Column(String(100), nullable=True)
    receipt_file_id = Column(String(255), nullable=False)
    status = Column(String(30), default="PENDING")  # 'PENDING', 'APPROVED', 'REJECTED'
    reviewed_by = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(BigInteger, ForeignKey("students.telegram_id"), nullable=False)
    chapter_id = Column(Integer, ForeignKey("chapters.id"), nullable=False)
    level = Column(String(50), nullable=False)
    score = Column(Integer, default=0)
    total_questions = Column(Integer, default=0)
    duration_seconds = Column(Integer, default=0)
    completed_at = Column(DateTime, default=datetime.utcnow)


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(BigInteger, ForeignKey("students.telegram_id"), nullable=False)
    message = Column(Text, nullable=False)
    attachment_file_id = Column(String(255), nullable=True)
    status = Column(String(30), default="OPEN")  # 'OPEN', 'RESOLVED'
    admin_reply = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class DailyChallengeCompletion(Base):
    __tablename__ = "daily_challenge_completions"
    __table_args__ = (
        UniqueConstraint("student_id", "challenge_date", "track", name="uq_student_daily_track"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(BigInteger, index=True, nullable=False)
    challenge_date = Column(String(16), index=True, nullable=False)  # YYYY-MM-DD (UTC)
    track = Column(String(32), index=False, nullable=False)           # coding, science, vocab
    hint_used = Column(Boolean, default=False, nullable=False)
    xp_earned = Column(Integer, default=15, nullable=False)
    completed_at = Column(DateTime, server_default=func.now(), nullable=False)