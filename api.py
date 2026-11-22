import os
from fastapi import FastAPI, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import (
    Course, Chapter, FeatureConfig, LearningResource, 
    QuizQuestion, Flashcard, Student, PaymentMethodConfig, PaymentReceipt
)

app = FastAPI(title="QineSystems Shared Backend API", version="1.0.0")
ADMIN_SECRET = os.getenv("ADMIN_API_SECRET", "super_secret_qine_admin_key")

def verify_admin(x_admin_key: str = Header(...)):
    if x_admin_key != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Unauthorized Admin Key")

class ChapterCreate(BaseModel):
    course_id: int
    chapter_number: int
    title: str

class ResourceCreate(BaseModel):
    chapter_id: int
    feature_key: str
    title: str
    content_type: str = "text"
    telegram_file_id: str | None = None
    text_content: str | None = None

class QuizQuestionCreate(BaseModel):
    chapter_id: int
    level: str
    question_text: str
    options: list[str]
    correct_option: int
    explanation: str
    time_limit_sec: int = 45

class FeatureAccessUpdate(BaseModel):
    is_free: bool
    is_enabled: bool

class PaymentMethodUpdate(BaseModel):
    account_holder: str
    account_number: str
    is_verified_active: bool

@app.get("/api/admin/overview", dependencies=[Depends(verify_admin)])
async def admin_overview(db: AsyncSession = Depends(get_db)):
    students = (await db.execute(select(func.count(Student.telegram_id)))).scalar()
    pending_payments = (await db.execute(select(func.count(PaymentReceipt.id)).where(PaymentReceipt.status == "PENDING"))).scalar()
    courses = (await db.execute(select(func.count(Course.id)))).scalar()
    return {
        "total_students": students,
        "pending_receipts": pending_payments,
        "catalogue_courses": courses
    }

@app.put("/api/admin/features/{feature_key}", dependencies=[Depends(verify_admin)])
async def update_feature_access(feature_key: str, payload: FeatureAccessUpdate, db: AsyncSession = Depends(get_db)):
    """Section 3.2 & 14: Authorized administrators can configure a feature's Free/Premium status."""
    feature = (await db.execute(select(FeatureConfig).where(FeatureConfig.key == feature_key))).scalar_one_or_none()
    if not feature:
        raise HTTPException(404, "Feature not found")
    feature.is_free = payload.is_free
    feature.is_enabled = payload.is_enabled
    await db.commit()
    return {"status": "updated", "feature": feature.key, "is_free": feature.is_free}

@app.post("/api/admin/chapters", dependencies=[Depends(verify_admin)])
async def create_chapter(payload: ChapterCreate, db: AsyncSession = Depends(get_db)):
    ch = Chapter(**payload.model_dump(), is_published=True)
    db.add(ch)
    await db.commit()
    await db.refresh(ch)
    return ch

@app.post("/api/admin/resources", dependencies=[Depends(verify_admin)])
async def publish_resource(payload: ResourceCreate, db: AsyncSession = Depends(get_db)):
    res = LearningResource(**payload.model_dump(), is_published=True)
    db.add(res)
    await db.commit()
    await db.refresh(res)
    return res

@app.post("/api/admin/quiz-questions", dependencies=[Depends(verify_admin)])
async def create_quiz_question(payload: QuizQuestionCreate, db: AsyncSession = Depends(get_db)):
    q = QuizQuestion(**payload.model_dump(), is_published=True)
    db.add(q)
    await db.commit()
    await db.refresh(q)
    return q

@app.put("/api/admin/payment-methods/{code}", dependencies=[Depends(verify_admin)])
async def verify_payment_method(code: str, payload: PaymentMethodUpdate, db: AsyncSession = Depends(get_db)):
    """Section 9: Telebirr, CBE, and BOA appear to students ONLY when verified & configured by Admin."""
    pm = (await db.execute(select(PaymentMethodConfig).where(PaymentMethodConfig.code == code.upper()))).scalar_one_or_none()
    if not pm:
        raise HTTPException(404, "Payment method not found")
    pm.account_holder = payload.account_holder
    pm.account_number = payload.account_number
    pm.is_verified_active = payload.is_verified_active
    await db.commit()
    return {"status": "verified", "code": pm.code, "active": pm.is_verified_active}