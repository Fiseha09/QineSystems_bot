import os
from dotenv import load_dotenv
from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, 
    InlineKeyboardButton, WebAppInfo
)
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, or_

from database import AsyncSessionLocal
from models import LearningResource, Chapter, Course
from bot import SearchFlow, back_and_home_row
from services import get_or_create_student, check_feature_entitlement

tools_router = Router()

# --- 1. Live Resource Search Execution ---
@tools_router.message(SearchFlow.waiting_for_query)
async def msg_execute_search(message: Message, state: FSMContext):
    query_str = (message.text or "").strip()
    if len(query_str) < 2:
        await message.answer("⚠️ Please enter at least 2 characters to search.")
        return

    async with AsyncSessionLocal() as db:
        stmt = (
            select(LearningResource, Chapter, Course)
            .join(Chapter, LearningResource.chapter_id == Chapter.id)
            .join(Course, Chapter.course_id == Course.id)
            .where(
                LearningResource.is_published == True,
                or_(
                    LearningResource.title.ilike(f"%{query_str}%"),
                    Chapter.title.ilike(f"%{query_str}%"),
                    Course.name.ilike(f"%{query_str}%")
                )
            )
            .limit(15)
        )
        rows = (await db.execute(stmt)).all()

    await state.clear()
    if not rows:
        await message.answer(
            f"🔍 <b>No published resources matched</b> <code>{query_str}</code>.\n"
            f"Try searching another course or chapter keyword.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_and_home_row("feat:search")]),
            parse_mode="HTML"
        )
        return

    buttons = [
        [InlineKeyboardButton(
            text=f"📄 [{crs.name} Ch.{ch.chapter_number}] {res.title}",
            callback_data=f"res:{res.id}:{crs.id}"
        )]
        for res, ch, crs in rows
    ]
    buttons.append(back_and_home_row("nav:main"))

    await message.answer(
        f"🔍 <b>Search Results for</b> <code>{query_str}</code> ({len(rows)} found):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )

# --- 2. Study Tools Hub (Launches GPA & Pomodoro Web Apps) ---
@tools_router.callback_query(F.data == "feat:study_tools")
async def cb_study_tools_hub(callback: CallbackQuery):
    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
        allowed, _ = await check_feature_entitlement(db, student, "study_tools")
        if not allowed:
            await callback.answer()
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👑 View Premium Membership & Upgrade", callback_data="nav:membership")],
                back_and_home_row("nav:main")
            ])
            await callback.message.edit_text(
                "🔒 <b>Study Tools is a Premium Feature</b>\n\n"
                "Upgrade to <b>Qine Plus</b> or <b>Qine Pro</b> to access the Freshman GPA Calculator and Pomodoro Web Apps.",
                reply_markup=kb,
                parse_mode="HTML"
            )
            return

    load_dotenv(override=True)
    gpa_url = os.getenv("GPA_WEBAPP_URL", "https://your-gpa-app.vercel.app").strip()
    pomodoro_url = os.getenv("POMODORO_WEBAPP_URL", "https://your-pomodoro-app.vercel.app").strip()

    await callback.answer()
    buttons = []

    # Use native Telegram Mini App (WebAppInfo) if https://, otherwise standard URL button
    if gpa_url.startswith("https://"):
        buttons.append([InlineKeyboardButton(
            text="🧮 Launch GPA Calculator Web App",
            web_app=WebAppInfo(url=gpa_url)
        )])
    else:
        buttons.append([InlineKeyboardButton(text="🧮 Open GPA Calculator", url=gpa_url)])

    if pomodoro_url.startswith("https://"):
        buttons.append([InlineKeyboardButton(
            text="🍅 Launch Pomodoro Timer Web App",
            web_app=WebAppInfo(url=pomodoro_url)
        )])
    else:
        buttons.append([InlineKeyboardButton(text="🍅 Open Pomodoro Timer", url=pomodoro_url)])

    buttons.append(back_and_home_row("nav:main"))

    await callback.message.edit_text(
        "🛠 <b>QineSystems Interactive Study Tools</b>\n\n"
        "Tap either tool below to launch the interactive web application directly inside Telegram:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )