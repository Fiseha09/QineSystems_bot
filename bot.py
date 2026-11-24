import html
import os
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from database import AsyncSessionLocal
from models import (
    Course, Chapter, FeatureConfig, LearningResource, QuizQuestion,
    Flashcard, Student, SubscriptionPlan, PaymentMethodConfig,
    PaymentReceipt, QuizAttempt, SupportTicket
)
from services import (
    get_or_create_student, check_feature_entitlement, has_active_premium,
    get_all_courses_for_feature, get_chapters_with_content_status,
    get_quiz_levels_for_chapter, is_direct_access_feature,
    get_leaderboard_data, get_daily_challenge,
    get_daily_challenge_status, record_daily_challenge_completion,
    get_student_challenge_streak_days
)
from student_resources import cb_resources_hub

load_dotenv(override=True)
BOT_TOKEN = os.getenv("BOT_TOKEN")

CHANNEL_HANDLE = "@QineSystems"
CHANNEL_URL = "https://t.me/QineSystems"
REFERRAL_REWARD_ETB = 30
COURSES_PER_PAGE = 10
RESOURCES_PER_PAGE = 6

YEAR_LABELS: dict[int, str] = {
    1: "1st Year",
    2: "2nd Year",
    3: "3rd Year",
}

YEAR_BADGES: dict[int, str] = {
    1: "🎓 1st Year (Freshman)",
    2: "📘 2nd Year",
    3: "📙 3rd Year",
}

YEAR_FEATURE_BUTTONS: list[tuple[str, str]] = [
    ("📚 Modules", "modules"),
    ("🗒 Study Notes", "notes"),
    ("📝 Mid-Exams", "mid_exams"),
    ("🎓 Final Exams", "final_exams"),
    ("⏱ Timed Quiz", "quiz"),
    ("🃏 Flashcards", "flashcards"),
]

DIRECT_COURSE_FEATURES = {"modules", "mid_exams", "final_exams", "notes"}
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}

TRACK_META = {
    "coding": ("💻 Coding", "dc:coding"),
    "science": ("🔬 Science", "dc:science"),
    "vocab": ("📖 English Vocab", "dc:vocab"),
}

SUPPORT_TOPICS = {
    "billing": ("💳 Subscriptions & Receipts", "Subscriptions & Receipts"),
    "access": ("🔐 Course & Tier Access", "Course & Tier Access"),
    "content": ("📘 Quizzes, Notes & Materials", "Quizzes, Notes & Materials"),
    "account": ("👤 Account, Referral & Progress", "Account, Referral & Progress"),
    "ideas": ("✨ Feature Ideas & Suggestions", "Feature Ideas & Suggestions"),
}


def _parse_year_num(val: str | int | None) -> int:
    try:
        num = int(val)
        return num if num in YEAR_LABELS else 1
    except (TypeError, ValueError):
        return 1


def is_direct_feature(feature_key: str) -> bool:
    return feature_key in DIRECT_COURSE_FEATURES or is_direct_access_feature(feature_key)


def build_resource_caption(title: str) -> str:
    safe_title = html.escape(title or "Study Resource")
    return (
        f"📘 <b>{safe_title}</b>\n\n"
        f"🎓 Curated for <b>Ethiopian University Students</b> by <b>QineSystems</b>.\n"
        f"📢 <b>Official Channel:</b> <a href=\"{CHANNEL_URL}\">{CHANNEL_HANDLE}</a>\n"
        f"🤖 <b>Study Bot:</b> @QineSystems_bot"
    )


async def safe_edit_message(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str = "HTML",
):
    try:
        await callback.message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )
    except Exception:
        await callback.message.answer(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )


async def _show_premium_required_card(
    callback: CallbackQuery,
    feature_title: str,
    selection_label: str,
    back_cb: str,
):
    await callback.answer()
    safe_feat = html.escape(feature_title)
    safe_sel = html.escape(selection_label)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👑 View Premium & Upgrade", callback_data="nav:membership")],
        back_and_home_row(back_cb),
    ])
    await safe_edit_message(
        callback,
        (
            f"🔒 <b>{safe_feat} — Premium Access</b>\n\n"
            f"📌 <b>Selected:</b> {safe_sel}\n\n"
            f"Unlock <b>{safe_feat}</b> and supercharge your studies! You need an active "
            f"<b>Qine Plus</b> or <b>Qine Pro</b> membership to view these materials.\n\n"
            f"✨ <b>Why Upgrade?</b>\n"
            f"• Unlimited access to exclusive notes, past exams &amp; guides\n"
            f"• Full access to the <b>QineSystems AI Study Assistant Suite</b>\n"
            f"• Faster study sessions anytime\n\n"
            f"👇 <i>Choose an option below to upgrade or go back:</i>"
        ),
        reply_markup=kb,
    )


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()


def get_live_admin_ids() -> list[int]:
    load_dotenv(override=True)
    return [
        int(x.strip())
        for x in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
        if x.strip().isdigit()
    ]


def format_plan_badge(student: Student) -> str:
    if has_active_premium(student):
        clean_name = html.escape(student.plan_code.replace("_", " "))
        return f"💎 {clean_name}"
    return "🟢 Free Plan"


# --- FSM States ---
class QuizSession(StatesGroup):
    in_progress = State()


class PaymentFlow(StatesGroup):
    waiting_for_receipt = State()


class SupportFlow(StatesGroup):
    waiting_for_issue = State()


class SearchFlow(StatesGroup):
    waiting_for_query = State()


class DailyChallengeFlow(StatesGroup):
    active = State()


# --- Navigation Builders ---

def build_main_menu(user_id: int | None = None) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="🎓 1st Year", callback_data="yr:1"),
            InlineKeyboardButton(text="📘 2nd Year", callback_data="yr:2"),
            InlineKeyboardButton(text="📙 3rd Year", callback_data="yr:3"),
        ],
        [
            InlineKeyboardButton(text="🤖 AI Study & Help", callback_data="feat:ai_assistant"),
            InlineKeyboardButton(text="⚡ Daily Challenge", callback_data="dc:hub"),
        ],
        [
            InlineKeyboardButton(text="📊 My Progress", callback_data="nav:my_progress"),
            InlineKeyboardButton(text="🏆 Leaderboard", callback_data="nav:leaderboard:weekly"),
        ],
        [
            InlineKeyboardButton(text="💎 Membership", callback_data="nav:membership"),
            InlineKeyboardButton(text="🆘 Support & Community", callback_data="nav:help"),
        ],
        [
            InlineKeyboardButton(text="🎁 Referral Hub", callback_data="nav:referral"),
        ],
    ]

    if user_id and user_id in get_live_admin_ids():
        rows.append([
            InlineKeyboardButton(text="🛠 Admin Panel", callback_data="adm:home")
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_and_home_row(back_cb: str) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text="⬅️ Back", callback_data=back_cb),
        InlineKeyboardButton(text="🏠 Main Menu", callback_data="nav:main"),
    ]


def build_year_hub_keyboard(year_num: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(YEAR_FEATURE_BUTTONS), 2):
        pair = [
            InlineKeyboardButton(
                text=label,
                callback_data=f"yr_feat:{year_num}:{fkey}",
            )
            for label, fkey in YEAR_FEATURE_BUTTONS[i:i + 2]
        ]
        rows.append(pair)

    rows.append([
        InlineKeyboardButton(text="📂 Study Resources", callback_data=f"yr_res:{year_num}"),
        InlineKeyboardButton(text="🔍 Search Materials", callback_data="feat:search"),
    ])
    rows.append([
        InlineKeyboardButton(text="🔄 Switch Year", callback_data="nav:main"),
        InlineKeyboardButton(text="🏠 Main Menu", callback_data="nav:main"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- 1. /start, Main Menu & Academic Year Hubs ---
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    args = (message.text or "").split()
    ref_code = args[1] if len(args) > 1 else None

    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, message.from_user, ref_code)
        full_name = student.full_name
        plan_badge = format_plan_badge(student)

    safe_name = html.escape(full_name or "Student")
    await message.answer(
        f"🎓 <b>Welcome to QineSystems, {safe_name}!</b>\n"
        f"<i>Your digital study companion for Ethiopian university courses.</i>\n\n"
        f"👤 <b>Account:</b> {safe_name} · <b>{plan_badge}</b>\n"
        f"📢 <b>Updates:</b> <a href=\"{CHANNEL_URL}\">{CHANNEL_HANDLE}</a>\n\n"
        f"<b>✨ How to get started:</b>\n"
        f"• <b>Pick Your Year:</b> Open <b>1st</b>, <b>2nd</b>, or <b>3rd Year</b> for modules, notes, exams &amp; quizzes\n"
        f"• <b>Universal Tools:</b> Use <b>AI Study &amp; Help</b>, <b>Daily Challenge</b>, <b>Progress</b> &amp; <b>Leaderboard</b> anytime\n\n"
        f"👇 <b>Choose your academic year or a study tool below:</b>",
        reply_markup=build_main_menu(message.from_user.id),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "nav:main")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
        plan_badge = format_plan_badge(student)
        full_name = student.full_name

    safe_name = html.escape(full_name or "Student")
    text = (
        f"🎓 <b>QineSystems Dashboard</b>\n"
        f"<i>Learn. Practice. Improve. Achieve.</i>\n\n"
        f"👤 <b>{safe_name}</b> · <b>{plan_badge}</b>\n"
        f"📢 <a href=\"{CHANNEL_URL}\">{CHANNEL_HANDLE}</a>\n\n"
        f"<b>Academic Years:</b>\n"
        f"• <b>1st / 2nd / 3rd Year:</b> Year-specific courses, notes, exams, quizzes &amp; study resources\n\n"
        f"<b>Shared Study Hub:</b>\n"
        f"• <b>AI Study &amp; Help</b> · <b>Daily Challenge</b> · <b>Progress</b> · <b>Leaderboard</b>\n\n"
        f"👇 <b>What would you like to work on today?</b>"
    )
    await safe_edit_message(callback, text, reply_markup=build_main_menu(callback.from_user.id))


@router.callback_query(F.data.startswith("yr:"))
async def cb_open_year_hub(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    year_num = _parse_year_num(callback.data.split(":", 1)[1])
    year_label = YEAR_LABELS[year_num]
    year_badge = YEAR_BADGES[year_num]

    async with AsyncSessionLocal() as db:
        courses = await get_all_courses_for_feature(db, "modules", year_level=year_label)

    await callback.answer()
    course_preview = (
        ", ".join(html.escape(c.name) for c in courses[:5])
        + (f" +{len(courses) - 5} more" if len(courses) > 5 else "")
        if courses
        else "<i>Courses being organized for this year</i>"
    )
    coc_note = (
        "• 📜 <b>COC Past Exams</b> included for <b>1st Year</b>\n"
        if year_num == 1
        else "• <i>COC Past Exams apply to 1st Year only</i>\n"
    )

    text = (
        f"{year_badge} <b>Study Hub</b>\n"
        f"<i>Focused materials &amp; practice tailored to {year_label}.</i>\n\n"
        f"📚 <b>Courses ({len(courses)}):</b> {course_preview}\n\n"
        f"<b>Available in {year_label}:</b>\n"
        f"• 📚 Modules, 🗒 Notes, 📝 Mid-Exams &amp; 🎓 Final Exams\n"
        f"• ⏱ Timed Quizzes &amp; 🃏 Flashcards\n"
        f"• 📂 Study Resources (Worksheets, አጤሬራ, Reference Books)\n"
        f"{coc_note}\n"
        f"👇 <b>Select a category below:</b>"
    )
    await safe_edit_message(callback, text, reply_markup=build_year_hub_keyboard(year_num))


@router.callback_query(F.data.startswith("yr_res:"))
async def cb_year_study_resources(callback: CallbackQuery, state: FSMContext):
    year_num = _parse_year_num(callback.data.split(":", 1)[1])
    year_label = YEAR_LABELS[year_num]
    year_badge = YEAR_BADGES[year_num]

    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
        allowed, feature = await check_feature_entitlement(db, student, "more_resources")
        if not feature or not feature.is_enabled:
            await callback.answer("Study Resources is currently disabled.", show_alert=True)
            return
        if not allowed:
            await _show_premium_required_card(
                callback,
                "Study Resources",
                f"{year_label} Study Resources",
                f"yr:{year_num}",
            )
            return

    await state.clear()
    await callback.answer()

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="📋 Worksheets", callback_data=f"yrcat:{year_num}:worksheets")],
    ]
    if year_num == 1:
        rows.append([InlineKeyboardButton(text="📜 COC Past Exams", callback_data=f"yrcat:{year_num}:coc")])
    rows.append([
        InlineKeyboardButton(text="📄 አጤሬራ", callback_data=f"yrcat:{year_num}:atirera"),
        InlineKeyboardButton(text="📚 Reference Books", callback_data=f"yrcat:{year_num}:refbooks"),
    ])
    rows.append(back_and_home_row(f"yr:{year_num}"))

    coc_line = "• 📜 <b>COC Past Exams:</b> Track-based COC prep (1st Year)\n" if year_num == 1 else ""
    text = (
        f"📂 <b>{year_badge} — Study Resources</b>\n\n"
        f"Select a resource category for <b>{year_label}</b>:\n"
        f"• 📋 <b>Worksheets:</b> Practice sheets filtered by {year_label} courses\n"
        f"{coc_line}"
        f"• 📄 <b>አጤሬራ:</b> Chapter summaries &amp; exam highlights\n"
        f"• 📚 <b>Reference Books:</b> Textbooks &amp; extra reading guides"
    )
    await safe_edit_message(callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("yrcat:"))
async def cb_year_resource_category(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":", 2)
    if len(parts) < 3:
        await callback.answer("Invalid selection.", show_alert=True)
        return

    year_num = _parse_year_num(parts[1])
    cat_key = parts[2]
    year_label = YEAR_LABELS[year_num]

    # COC is strictly for 1st Year only
    if cat_key == "coc" and year_num != 1:
        await callback.answer("COC Past Exams are available for 1st Year only.", show_alert=True)
        return

    if cat_key == "coc":
        # Reuse existing COC track handler if available
        callback.data = "res_cat:coc"
        await cb_resources_hub(callback, state)
        return

    cat_titles = {
        "worksheets": "📋 Worksheets",
        "atirera": "📄 አጤሬራ",
        "refbooks": "📚 Reference Books",
    }
    cat_label = cat_titles.get(cat_key, "📂 Study Resources")

    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
        allowed, feature = await check_feature_entitlement(db, student, "more_resources")
        if not feature or not feature.is_enabled:
            await callback.answer("Study Resources is currently disabled.", show_alert=True)
            return
        if not allowed:
            await _show_premium_required_card(
                callback,
                cat_label,
                f"{year_label} · {cat_label}",
                f"yr_res:{year_num}",
            )
            return
        courses = await get_all_courses_for_feature(db, "more_resources", year_level=year_label)

    await callback.answer()
    if not courses:
        await safe_edit_message(
            callback,
            build_coming_soon_message(cat_label, f"{year_label} courses"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_and_home_row(f"yr_res:{year_num}")]),
        )
        return

    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for c in courses:
        row.append(
            InlineKeyboardButton(
                text=f"📚 {c.name}",
                callback_data=f"yr_rcrs:{year_num}:{cat_key}:{c.id}",
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append(back_and_home_row(f"yr_res:{year_num}"))

    await safe_edit_message(
        callback,
        f"<b>{cat_label}</b> → <b>{year_label}</b>\nSelect a course below:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("yr_rcrs:"))
async def cb_year_resource_course(callback: CallbackQuery):
    parts = callback.data.split(":", 3)
    if len(parts) < 4:
        await callback.answer("Invalid selection.", show_alert=True)
        return

    year_num = _parse_year_num(parts[1])
    cat_key = parts[2]
    course_id = int(parts[3])
    year_label = YEAR_LABELS[year_num]

    cat_titles = {
        "worksheets": "📋 Worksheets",
        "atirera": "📄 አጤሬራ",
        "refbooks": "📚 Reference Books",
    }
    cat_label = cat_titles.get(cat_key, "📂 Study Resources")

    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
        allowed, feature = await check_feature_entitlement(db, student, "more_resources")
        if not feature or not feature.is_enabled:
            await callback.answer("Study Resources is currently disabled.", show_alert=True)
            return
        if not allowed:
            await _show_premium_required_card(callback, cat_label, f"{year_label} · {cat_label}", f"yrcat:{year_num}:{cat_key}")
            return

        course = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none()
        if not course:
            await callback.answer("Course not found.", show_alert=True)
            return

        resources = (await db.execute(
            select(LearningResource)
            .join(Chapter, LearningResource.chapter_id == Chapter.id)
            .where(
                Chapter.course_id == course_id,
                LearningResource.feature_key == "more_resources",
                LearningResource.is_published == True,
            )
            .order_by(Chapter.chapter_number, LearningResource.id)
        )).scalars().all()

        # Match category keyword in title if tagged, otherwise show all published more_resources for that course
        cat_keywords = {
            "worksheets": ["worksheet"],
            "atirera": ["atirera", "አጤሬራ"],
            "refbooks": ["book", "reference", "textbook"],
        }
        keywords = cat_keywords.get(cat_key, [])
        filtered = [
            r for r in resources
            if any(kw in (r.title or "").lower() for kw in keywords)
        ]
        display_resources = filtered if filtered else list(resources)

    await callback.answer()
    if not display_resources:
        await safe_edit_message(
            callback,
            build_coming_soon_message(cat_label, f"{course.name} ({year_label})"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_and_home_row(f"yrcat:{year_num}:{cat_key}")]),
        )
        return

    buttons = [
        [InlineKeyboardButton(text=f"📄 {res.title}", callback_data=f"res:{res.id}:{course_id}")]
        for res in display_resources[:12]
    ]
    buttons.append(back_and_home_row(f"yrcat:{year_num}:{cat_key}"))

    await safe_edit_message(
        callback,
        f"<b>{cat_label}</b> → <b>{html.escape(course.name)}</b> (<i>{year_label}</i>)\nSelect a file below:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("yr_feat:"))
async def cb_year_feature_courses(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":", 2)
    if len(parts) < 3:
        await callback.answer("Invalid selection.", show_alert=True)
        return

    year_num = _parse_year_num(parts[1])
    feature_key = parts[2]
    year_label = YEAR_LABELS[year_num]

    if feature_key == "ai_assistant":
        await show_ai_suite(callback, state)
        return

    if feature_key == "more_resources":
        callback.data = f"yr_res:{year_num}"
        await cb_year_study_resources(callback, state)
        return

    async with AsyncSessionLocal() as db:
        feature = (
            await db.execute(select(FeatureConfig).where(FeatureConfig.key == feature_key))
        ).scalar_one_or_none()
        if not feature or not feature.is_enabled:
            await callback.answer("This feature is currently disabled by administrators.", show_alert=True)
            return
        courses = await get_all_courses_for_feature(db, feature_key, year_level=year_label)

    await callback.answer()
    if not courses:
        await safe_edit_message(
            callback,
            build_coming_soon_message(feature.title, f"{year_label} courses"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_and_home_row(f"yr:{year_num}")]),
        )
        return

    kb, page_num, total_pages = build_paginated_courses_keyboard(
        courses=courses,
        feature_key=feature_key,
        back_target=f"yr:{year_num}",
        page=0,
        year_num=year_num,
    )
    await safe_edit_message(
        callback,
        f"<b>{feature.title}</b> → <b>{year_label}</b> (Page {page_num}/{total_pages})\n"
        f"Select a {year_label} course below:",
        reply_markup=kb,
    )


# --- 2. Free Tools vs. Premium Tools Grouping ---
@router.callback_query(F.data == "nav:free_tools")
async def cb_free_tools(callback: CallbackQuery):
    await callback.answer()
    async with AsyncSessionLocal() as db:
        features = (await db.execute(
            select(FeatureConfig)
            .where(FeatureConfig.is_free == True, FeatureConfig.is_enabled == True)
            .order_by(FeatureConfig.sort_order)
        )).scalars().all()

    buttons = [[InlineKeyboardButton(text=f.title, callback_data=f"feat:{f.key}")] for f in features]
    buttons.append(back_and_home_row("nav:main"))

    await safe_edit_message(
        callback,
        "🟢 <b>Free Learning Resources</b>\n\n"
        "Explore university course materials, including study modules, mid-semester exams, and study notes, all available for free.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


def build_coming_soon_message(area_title: str, subject_label: str = "this specific course") -> str:
    safe_area = html.escape(area_title)
    safe_sub = html.escape(subject_label)
    return (
        f"🚀 <b>{safe_area} — Coming Soon!</b>\n\n"
        f"Something big is on the way for your exam prep! <b>QineSystems</b> is actively preparing "
        f"specialized <b>{safe_area.lower()}</b> for <b>{safe_sub}</b>.\n\n"
        f"📢 Join <a href=\"{CHANNEL_URL}\">{CHANNEL_HANDLE}</a> and stay tuned so you’re the first to get access!"
    )


@router.callback_query(F.data == "nav:premium_tools")
async def cb_premium_tools(callback: CallbackQuery):
    await callback.answer()
    async with AsyncSessionLocal() as db:
        features = (await db.execute(
            select(FeatureConfig)
            .where(FeatureConfig.is_free == False, FeatureConfig.is_enabled == True)
            .order_by(FeatureConfig.sort_order)
        )).scalars().all()

    buttons = []
    row = []
    for f in features:
        row.append(InlineKeyboardButton(text=f.title, callback_data=f"feat:{f.key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append(back_and_home_row("nav:main"))

    await safe_edit_message(
        callback,
        "💎 <b>Premium Tools</b>\n"
        "Select a premium learning tool below:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


async def show_ai_suite(callback: CallbackQuery, state: FSMContext | None = None):
    if state is not None:
        await state.clear()
    await callback.answer()

    tools = [
        ("🧑‍🏫 AI Tutor", "ai:tutor"),
        ("❓ Ask AI", "ai:ask"),
        ("➗ Math Solver", "ai:math"),
        ("🗣 English Coach", "ai:english"),
        ("💻 Coding Assistant", "ai:code"),
        ("📝 Exam Preparation", "ai:exam"),
        ("📄 Document Q&A", "ai:doc"),
        ("📋 Summarize Materials", "ai:sum"),
    ]
    rows = [
        [
            InlineKeyboardButton(text=tools[i][0], callback_data=tools[i][1]),
            InlineKeyboardButton(text=tools[i + 1][0], callback_data=tools[i + 1][1]),
        ]
        for i in range(0, len(tools), 2)
    ]
    rows.append(back_and_home_row("nav:main"))

    text = (
        "🤖 <b>QineSystems AI Study Suite</b>\n"
        "<i>Available across all academic years — 24/7 study support.</i>\n\n"
        "🧑‍🏫 <b>AI Tutor</b> – Step-by-step explanations on tough concepts\n"
        "❓ <b>Ask AI</b> – Quick answers to any academic question\n"
        "➗ <b>Math Solver</b> – Solve equations with detailed workings\n"
        "🗣 <b>English Coach</b> – Practice grammar, writing, and fluency\n"
        "💻 <b>Coding Assistant</b> – Debug, write, and understand code\n"
        "📝 <b>Exam Prep</b> – Generate practice quizzes and mock questions\n"
        "📄 <b>Document Q&amp;A</b> – Upload notes or PDFs and ask questions\n"
        "📋 <b>Summarize</b> – Turn long lectures into crisp study notes\n\n"
        "👇 <i>Tap a mode below to begin:</i>"
    )
    await safe_edit_message(
        callback,
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "feat:ai_assistant")
async def cb_open_ai_suite(callback: CallbackQuery, state: FSMContext):
    await show_ai_suite(callback, state)


def build_paginated_courses_keyboard(
    courses: list,
    feature_key: str,
    back_target: str,
    page: int = 0,
    year_num: int | None = None,
) -> tuple[InlineKeyboardMarkup, int, int]:
    total_courses = len(courses)
    total_pages = max(1, (total_courses + COURSES_PER_PAGE - 1) // COURSES_PER_PAGE)
    current_page = max(0, min(page, total_pages - 1))

    start_idx = current_page * COURSES_PER_PAGE
    end_idx = start_idx + COURSES_PER_PAGE
    page_courses = courses[start_idx:end_idx]
    remaining_after = max(0, total_courses - end_idx)

    yr_suffix = f":{year_num}" if year_num is not None else ""

    rows: list[list[InlineKeyboardButton]] = []
    current_row: list[InlineKeyboardButton] = []
    for c in page_courses:
        current_row.append(
            InlineKeyboardButton(
                text=f"📚 {c.name}",
                callback_data=f"crs:{feature_key}:{c.id}{yr_suffix}",
            )
        )
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)

    if current_page == 0 and remaining_after > 0:
        rows.append([
            InlineKeyboardButton(
                text=f"Other Courses ({remaining_after} more) ➡️",
                callback_data=f"cpage:{feature_key}:1{yr_suffix}",
            )
        ])
    elif current_page > 0:
        nav_step_row: list[InlineKeyboardButton] = [
            InlineKeyboardButton(
                text="⬅️ Previous",
                callback_data=f"cpage:{feature_key}:{current_page - 1}{yr_suffix}",
            )
        ]
        if remaining_after > 0:
            nav_step_row.append(
                InlineKeyboardButton(
                    text="Next ➡️",
                    callback_data=f"cpage:{feature_key}:{current_page + 1}{yr_suffix}",
                )
            )
        rows.append(nav_step_row)

    rows.append(back_and_home_row(back_target))
    return InlineKeyboardMarkup(inline_keyboard=rows), current_page + 1, total_pages


@router.callback_query(F.data.startswith("cpage:"))
async def cb_paginate_courses(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Invalid page.", show_alert=True)
        return

    feature_key = parts[1]
    page_str = parts[2]
    year_num = _parse_year_num(parts[3]) if len(parts) >= 4 else None
    year_label = YEAR_LABELS.get(year_num) if year_num else None
    target_page = int(page_str) if page_str.isdigit() else 0

    async with AsyncSessionLocal() as db:
        feature = (await db.execute(select(FeatureConfig).where(FeatureConfig.key == feature_key))).scalar_one_or_none()
        if not feature or not feature.is_enabled:
            await callback.answer("This feature is currently disabled.", show_alert=True)
            return
        courses = await get_all_courses_for_feature(db, feature_key, year_level=year_label)

    await callback.answer()
    back_target = f"yr:{year_num}" if year_num else ("nav:free_tools" if feature.is_free else "nav:premium_tools")
    kb, page_num, total_pages = build_paginated_courses_keyboard(
        courses=courses,
        feature_key=feature_key,
        back_target=back_target,
        page=target_page,
        year_num=year_num,
    )

    scope_label = f"{feature.title} · {year_label}" if year_label else feature.title
    await safe_edit_message(
        callback,
        f"<b>{scope_label}</b> → <b>Select Course</b> (Page {page_num}/{total_pages})\n"
        f"Select a course below:",
        reply_markup=kb,
    )


# --- 3. Feature Entry ---
@router.callback_query(F.data.startswith("feat:"))
async def cb_open_feature(callback: CallbackQuery, state: FSMContext):
    feature_key = callback.data.split(":", 1)[1]
    if feature_key == "ai_assistant":
        await show_ai_suite(callback, state)
        return

    if feature_key == "more_resources":
        await cb_resources_hub(callback, state)
        return

    async with AsyncSessionLocal() as db:
        feature = (
            await db.execute(select(FeatureConfig).where(FeatureConfig.key == feature_key))
        ).scalar_one_or_none()
        if not feature or not feature.is_enabled:
            await callback.answer("This feature is currently disabled by administrators.", show_alert=True)
            return

        if feature_key == "search":
            await callback.answer()
            await state.set_state(SearchFlow.waiting_for_query)
            await safe_edit_message(
                callback,
                (
                    "🔍 <b>Search Learning Resources</b>\n\n"
                    "Looking for something specific? Type and send a keyword, topic, or title "
                    "to find published notes, past exams, and study guides.\n\n"
                    "💡 <b>Search Tips:</b>\n"
                    "• Use specific subjects or course names (e.g., <i>Biology</i>, <i>Database</i>)\n"
                    "• Search by topic (e.g., <i>Personality</i>, <i>Thermodynamics</i>)\n"
                    "• Keep it brief—1 to 3 words usually works best!"
                ),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_and_home_row("nav:main")]),
            )
            return

        courses = await get_all_courses_for_feature(db, feature_key)

    await callback.answer()
    if not courses:
        back_target = "nav:free_tools" if feature.is_free else "nav:premium_tools"
        await safe_edit_message(
            callback,
            f"📭 <b>{feature.title}</b>\n\nNo courses found in the catalogue.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_and_home_row(back_target)]),
        )
        return

    back_target = "nav:free_tools" if feature.is_free else "nav:premium_tools"
    kb, page_num, total_pages = build_paginated_courses_keyboard(
        courses=courses,
        feature_key=feature_key,
        back_target=back_target,
        page=0,
    )

    await safe_edit_message(
        callback,
        f"<b>{feature.title}</b> → <b>Select Course</b> (Page {page_num}/{total_pages})\n"
        f"Select a course below:",
        reply_markup=kb,
    )


@router.message(SearchFlow.waiting_for_query, F.text & ~F.text.startswith("/"))
async def msg_execute_resource_search(message: Message, state: FSMContext):
    raw_query = (message.text or "").strip()
    if not raw_query:
        await message.answer("⚠️ Please type a keyword, subject, or topic to search.")
        return

    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, message.from_user)
        allowed, feature = await check_feature_entitlement(db, student, "search")
        if not feature or not feature.is_enabled:
            await state.clear()
            await message.answer(
                "⚠️ <b>Search Resources</b> is currently disabled by administrators.",
                reply_markup=build_main_menu(message.from_user.id),
                parse_mode="HTML",
            )
            return

        if not allowed:
            await state.clear()
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👑 View Premium & Upgrade", callback_data="nav:membership")],
                back_and_home_row("nav:main"),
            ])
            await message.answer(
                "🔒 <b>Search Resources — Premium Access</b>\n\n"
                "Upgrade to <b>Qine Plus</b> or <b>Qine Pro</b> via Membership to search "
                "across published notes, modules, and past exams.",
                reply_markup=kb,
                parse_mode="HTML",
            )
            return

        pattern = f"%{raw_query}%"
        rows = (
            await db.execute(
                select(LearningResource, Chapter.course_id, Course.name)
                .join(Chapter, LearningResource.chapter_id == Chapter.id)
                .join(Course, Chapter.course_id == Course.id)
                .where(
                    LearningResource.is_published == True,
                    Chapter.is_published == True,
                    Course.is_active == True,
                    (
                        LearningResource.title.ilike(pattern)
                        | LearningResource.text_content.ilike(pattern)
                        | Course.name.ilike(pattern)
                        | Chapter.title.ilike(pattern)
                    ),
                )
                .order_by(LearningResource.id.desc())
                .limit(12)
            )
        ).all()

    safe_query = html.escape(raw_query)
    if not rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Search Again", callback_data="feat:search")],
            back_and_home_row("nav:main"),
        ])
        await message.answer(
            f"🔎 <b>No results found</b> for <code>{safe_query}</code>.\n\n"
            f"Try a shorter keyword (1–3 words) like <i>Biology</i>, <i>Economics</i>, or <i>Chapter 1</i>.",
            reply_markup=kb,
            parse_mode="HTML",
        )
        return

    buttons: list[list[InlineKeyboardButton]] = []
    for res, crs_id, crs_name in rows:
        short_title = (res.title or "Resource").strip()[:36]
        short_crs = (crs_name or "").strip()[:14]
        btn_label = f"📄 {short_title}" + (f" · {short_crs}" if short_crs else "")
        buttons.append([
            InlineKeyboardButton(text=btn_label[:64], callback_data=f"res:{res.id}:{crs_id}")
        ])

    buttons.append([InlineKeyboardButton(text="🔍 New Search", callback_data="feat:search")])
    buttons.append(back_and_home_row("nav:main"))

    await message.answer(
        f"🔍 <b>Search Results for</b> <code>{safe_query}</code> ({len(rows)} found)\n"
        f"Tap a resource below to open it:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


def build_paginated_resources_keyboard(
    resources: list,
    course_id: int,
    back_cb: str,
    page: int = 0,
    scope: str = "crs",
    scope_id: int = 0,
) -> tuple[InlineKeyboardMarkup, int, int]:
    total_items = len(resources)
    total_pages = max(1, (total_items + RESOURCES_PER_PAGE - 1) // RESOURCES_PER_PAGE)
    current_page = max(0, min(page, total_pages - 1))

    start_idx = current_page * RESOURCES_PER_PAGE
    end_idx = start_idx + RESOURCES_PER_PAGE
    page_items = resources[start_idx:end_idx]
    remaining_after = max(0, total_items - end_idx)

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=f"📄 {res.title}", callback_data=f"res:{res.id}:{course_id}")]
        for res in page_items
    ]

    if current_page == 0 and remaining_after > 0:
        rows.append([
            InlineKeyboardButton(
                text=f"More ({remaining_after} more) ➡️",
                callback_data=f"rpage:{scope}:{scope_id}:{course_id}:1",
            )
        ])
    elif current_page > 0:
        step_row: list[InlineKeyboardButton] = [
            InlineKeyboardButton(
                text="⬅️ Previous",
                callback_data=f"rpage:{scope}:{scope_id}:{course_id}:{current_page - 1}",
            )
        ]
        if remaining_after > 0:
            step_row.append(
                InlineKeyboardButton(
                    text="Next ➡️",
                    callback_data=f"rpage:{scope}:{scope_id}:{course_id}:{current_page + 1}",
                )
            )
        rows.append(step_row)

    rows.append(back_and_home_row(back_cb))
    return InlineKeyboardMarkup(inline_keyboard=rows), current_page + 1, total_pages


@router.callback_query(F.data.startswith("rpage:"))
async def cb_paginate_resources(callback: CallbackQuery):
    parts = callback.data.split(":", 4)
    if len(parts) < 5:
        await callback.answer("Invalid resource page.", show_alert=True)
        return

    _, scope, scope_id_str, course_id_str, page_str = parts
    try:
        scope_id = int(scope_id_str)
        course_id = int(course_id_str)
        target_page = int(page_str)
    except ValueError:
        await callback.answer("Invalid page parameter.", show_alert=True)
        return

    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
        if scope != "chp":
            await callback.answer("Use the course button to browse pages.", show_alert=True)
            return

        chapter = (await db.execute(select(Chapter).where(Chapter.id == scope_id))).scalar_one_or_none()
        if not chapter:
            await callback.answer("Chapter not found.", show_alert=True)
            return

        resources = (await db.execute(
            select(LearningResource)
            .where(
                LearningResource.chapter_id == scope_id,
                LearningResource.is_published == True,
            )
            .order_by(LearningResource.id)
        )).scalars().all()
        if not resources:
            await callback.answer("No resources found.", show_alert=True)
            return

        feature_key = resources[0].feature_key
        allowed, feature = await check_feature_entitlement(db, student, feature_key)
        if not feature or not feature.is_enabled:
            await callback.answer("This feature is currently disabled.", show_alert=True)
            return
        if not allowed:
            await _show_premium_required_card(
                callback,
                feature.title,
                f"Chapter {chapter.chapter_number}",
                f"crs:{feature_key}:{course_id}",
            )
            return

        await callback.answer()
        kb, page_num, total_pages = build_paginated_resources_keyboard(
            resources=resources,
            course_id=course_id,
            back_cb=f"crs:{feature_key}:{course_id}",
            page=target_page,
            scope="chp",
            scope_id=scope_id,
        )
        header = (
            f"<b>{feature.title}</b> → <b>Chapter {chapter.chapter_number}</b> "
            f"(Page {page_num}/{total_pages})\nSelect a file or resource below:"
        )

    await safe_edit_message(callback, header, reply_markup=kb)


@router.callback_query(F.data.startswith("dpage:"))
async def cb_paginate_direct_resources(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("Invalid page.", show_alert=True)
        return

    feature_key = parts[1]
    course_id = int(parts[2])
    target_page = max(0, int(parts[3]))
    year_num = _parse_year_num(parts[4]) if len(parts) >= 5 else None
    back_cb = f"yr_feat:{year_num}:{feature_key}" if year_num else f"feat:{feature_key}"

    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
        allowed, feature = await check_feature_entitlement(db, student, feature_key)
        course = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none()
        course_label = html.escape(course.name) if course else "Course"

        if not feature or not feature.is_enabled:
            await callback.answer("This feature is currently disabled.", show_alert=True)
            return
        if not allowed:
            await _show_premium_required_card(callback, feature.title, course_label, back_cb)
            return

        stmt = (
            select(LearningResource)
            .join(Chapter, LearningResource.chapter_id == Chapter.id)
            .where(
                Chapter.course_id == course_id,
                LearningResource.feature_key == feature_key,
                LearningResource.is_published == True,
            )
            .order_by(Chapter.chapter_number, LearningResource.id)
        )
        resources = (await db.execute(stmt)).scalars().all()

    if not resources:
        await callback.answer("No files found.", show_alert=True)
        return

    await callback.answer()
    total_pages = max(1, (len(resources) + RESOURCES_PER_PAGE - 1) // RESOURCES_PER_PAGE)
    current_page = min(target_page, total_pages - 1)
    start_idx = current_page * RESOURCES_PER_PAGE
    end_idx = start_idx + RESOURCES_PER_PAGE
    page_items = resources[start_idx:end_idx]
    remaining_after = max(0, len(resources) - end_idx)

    yr_suffix = f":{year_num}" if year_num is not None else ""
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=f"📄 {res.title}", callback_data=f"res:{res.id}:{course_id}")]
        for res in page_items
    ]

    if current_page == 0 and remaining_after > 0:
        rows.append([
            InlineKeyboardButton(
                text=f"More ({remaining_after} more) ➡️",
                callback_data=f"dpage:{feature_key}:{course_id}:1{yr_suffix}",
            )
        ])
    elif current_page > 0:
        nav_row: list[InlineKeyboardButton] = [
            InlineKeyboardButton(
                text="⬅️ Previous",
                callback_data=f"dpage:{feature_key}:{course_id}:{current_page - 1}{yr_suffix}",
            )
        ]
        if remaining_after > 0:
            nav_row.append(
                InlineKeyboardButton(
                    text="Next ➡️",
                    callback_data=f"dpage:{feature_key}:{course_id}:{current_page + 1}{yr_suffix}",
                )
            )
        rows.append(nav_row)

    rows.append(back_and_home_row(back_cb))
    await safe_edit_message(
        callback,
        f"<b>{feature.title}</b> → <b>{course_label}</b> (Page {current_page + 1}/{total_pages})\n"
        f"Select a file or resource below:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


# --- 4. Course Selection (Gate on Course Tap for Direct Features; Show Chapters for Chapter Features) ---
@router.callback_query(F.data.startswith("crs:"))
async def cb_select_course(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Invalid course selection.", show_alert=True)
        return

    feature_key = parts[1]
    course_id = int(parts[2])
    year_num = _parse_year_num(parts[3]) if len(parts) >= 4 else None
    back_cb = f"yr_feat:{year_num}:{feature_key}" if year_num else f"feat:{feature_key}"
    yr_suffix = f":{year_num}" if year_num is not None else ""

    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
        allowed, feature = await check_feature_entitlement(db, student, feature_key)
        if not feature or not feature.is_enabled:
            await callback.answer("Feature is currently disabled.", show_alert=True)
            return

        course = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none()
        if not course:
            await callback.answer("Course not found.", show_alert=True)
            return

        safe_course = html.escape(course.name)

        if is_direct_feature(feature_key):
            if not allowed:
                await _show_premium_required_card(
                    callback,
                    feature.title,
                    f"{feature.title} · {course.name}",
                    back_cb,
                )
                return

            stmt = (
                select(LearningResource)
                .join(Chapter, LearningResource.chapter_id == Chapter.id)
                .where(
                    Chapter.course_id == course_id,
                    LearningResource.feature_key == feature_key,
                    LearningResource.is_published == True,
                )
                .order_by(Chapter.chapter_number, LearningResource.id)
            )
            resources = (await db.execute(stmt)).scalars().all()
            if not resources:
                await callback.answer()
                await safe_edit_message(
                    callback,
                    build_coming_soon_message(feature.title, course.name),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_and_home_row(back_cb)]),
                )
                return

            await callback.answer()
            total_pages = max(1, (len(resources) + RESOURCES_PER_PAGE - 1) // RESOURCES_PER_PAGE)
            rows_with_feat: list[list[InlineKeyboardButton]] = [
                [InlineKeyboardButton(text=f"📄 {res_obj.title}", callback_data=f"res:{res_obj.id}:{course_id}")]
                for res_obj in resources[:RESOURCES_PER_PAGE]
            ]
            remaining_after = max(0, len(resources) - RESOURCES_PER_PAGE)
            if remaining_after > 0:
                rows_with_feat.append([
                    InlineKeyboardButton(
                        text=f"More ({remaining_after} more) ➡️",
                        callback_data=f"dpage:{feature_key}:{course_id}:1{yr_suffix}",
                    )
                ])
            rows_with_feat.append(back_and_home_row(back_cb))

            await safe_edit_message(
                callback,
                f"<b>{feature.title}</b> → <b>{safe_course}</b> (Page 1/{total_pages})\n"
                f"Select a file or resource below:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows_with_feat),
            )
            return

        chapter_tuples = await get_chapters_with_content_status(db, course_id, feature_key)
        if not chapter_tuples:
            await callback.answer()
            await safe_edit_message(
                callback,
                build_coming_soon_message(feature.title, course.name),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_and_home_row(back_cb)]),
            )
            return

        await callback.answer()
        buttons = [
            [InlineKeyboardButton(
                text=f"{'🟢' if has_content else '🔴'} Chapter {ch.chapter_number}",
                callback_data=f"chp:{feature_key}:{course_id}:{ch.id}{yr_suffix}",
            )]
            for ch, has_content in chapter_tuples
        ]
        buttons.append(back_and_home_row(back_cb))

        await safe_edit_message(
            callback,
            f"<b>{feature.title}</b> → <b>{safe_course}</b>\n"
            f"<i>🟢 Ready to study · 🔴 Coming soon</i>\n"
            f"Select a chapter:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )


# --- 5. Chapter Selection (Gate on Chapter Tap for Chapter Features) ---
@router.callback_query(F.data.startswith("chp:"))
async def cb_select_chapter(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("Invalid chapter selection.", show_alert=True)
        return

    feature_key = parts[1]
    course_id = int(parts[2])
    chapter_id = int(parts[3])
    year_num = _parse_year_num(parts[4]) if len(parts) >= 5 else None
    yr_suffix = f":{year_num}" if year_num is not None else ""
    course_back_cb = f"crs:{feature_key}:{course_id}{yr_suffix}"

    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
        allowed, feature = await check_feature_entitlement(db, student, feature_key)
        if not feature or not feature.is_enabled:
            await callback.answer("Feature is currently disabled.", show_alert=True)
            return

        chapter = (await db.execute(select(Chapter).where(Chapter.id == chapter_id))).scalar_one_or_none()
        if not chapter:
            await callback.answer("Chapter not found.", show_alert=True)
            return

        chapter_tuples = await get_chapters_with_content_status(db, course_id, feature_key)
        has_content = next((has_c for ch, has_c in chapter_tuples if ch.id == chapter_id), False)
        if not has_content:
            await callback.answer()
            course = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none()
            subject_str = f"{course.name} · Chapter {chapter.chapter_number}" if course else f"Chapter {chapter.chapter_number}"
            await safe_edit_message(
                callback,
                build_coming_soon_message(feature.title, subject_str),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_and_home_row(course_back_cb)]),
            )
            return

        if not allowed:
            await _show_premium_required_card(
                callback,
                feature.title,
                f"{feature.title} · Chapter {chapter.chapter_number}",
                course_back_cb,
            )
            return

        if feature_key == "quiz":
            await callback.answer()
            course = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none()
            course_label = html.escape(course.name) if course else "Course"
            chapter_label = (
                f"Chapter {chapter.chapter_number}"
                + (f": {html.escape(chapter.title)}" if getattr(chapter, "title", None) else "")
            )

            all_q = (await db.execute(
                select(QuizQuestion).where(
                    QuizQuestion.chapter_id == chapter_id,
                    QuizQuestion.is_published == True,
                )
            )).scalars().all()

            counts_lower: dict[str, int] = {}
            label_by_lower: dict[str, str] = {}
            for q in all_q:
                raw_lvl = (q.level or "Medium").strip()
                key_l = raw_lvl.lower()
                counts_lower[key_l] = counts_lower.get(key_l, 0) + 1
                label_by_lower.setdefault(key_l, raw_lvl)

            db_levels = await get_quiz_levels_for_chapter(db, chapter_id)
            active_levels: list[str] = []
            for lvl in db_levels:
                clean_lvl = (lvl or "").strip()
                if clean_lvl and clean_lvl.lower() not in {x.lower() for x in active_levels}:
                    active_levels.append(clean_lvl)

            if not active_levels:
                active_levels = ["Simple", "Medium", "Hard"]

            level_badges = {"simple": "🟢", "medium": "🟡", "hard": "🔴"}
            count_lines = []
            buttons: list[list[InlineKeyboardButton]] = []
            for lvl in active_levels:
                key_l = lvl.lower()
                display_lvl = label_by_lower.get(key_l, lvl)
                cnt = counts_lower.get(key_l, 0)
                icon = level_badges.get(key_l, "⚡")
                count_lines.append(f"• {icon} {html.escape(display_lvl)}: <b>{cnt}</b> {'question' if cnt == 1 else 'questions'}")
                buttons.append([
                    InlineKeyboardButton(
                        text=f"{icon} {display_lvl} ({cnt})",
                        callback_data=f"qlvl:{chapter_id}:{display_lvl}",
                    )
                ])

            buttons.append([InlineKeyboardButton(text="⬅️ Back to Chapters", callback_data=course_back_cb)])
            buttons.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="nav:main")])

            text = (
                f"⏱ <b>Select Difficulty</b>\n\n"
                f"📚 <b>Course:</b> {course_label}\n"
                f"📖 <b>Chapter:</b> {chapter_label}\n\n"
                f"📊 <b>Available questions:</b>\n"
                + "\n".join(count_lines)
                + "\n\n<i>Choose a difficulty level to start:</i>"
            )
            await safe_edit_message(callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
            return

        if feature_key == "flashcards":
            cards = (await db.execute(
                select(Flashcard).where(Flashcard.chapter_id == chapter_id, Flashcard.is_published == True)
            )).scalars().all()
            if not cards:
                await callback.answer("No flashcards available.", show_alert=True)
                return
            await callback.answer()
            await render_flashcard(callback, cards, 0, course_id, chapter_id, show_back=False)
            return

        await callback.answer()
        resources = (await db.execute(
            select(LearningResource).where(
                LearningResource.chapter_id == chapter_id,
                LearningResource.feature_key == feature_key,
                LearningResource.is_published == True,
            )
        )).scalars().all()

        buttons = [
            [InlineKeyboardButton(text=f"📄 {res.title}", callback_data=f"res:{res.id}:{course_id}")]
            for res in resources
        ]
        buttons.append(back_and_home_row(course_back_cb))

        await safe_edit_message(
            callback,
            f"<b>{feature.title}</b> → <b>Chapter {chapter.chapter_number}</b>\nSelect a resource to open:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )


async def _send_learning_resource(
    callback: CallbackQuery,
    resource: LearningResource,
    feature: FeatureConfig | None,
    is_protected: bool,
):
    base_caption = build_resource_caption(resource.title)
    protection_note = (
        "\n\n🔒 <i>Protected Premium Content: Forwarding and saving are disabled.</i>"
        if is_protected
        else ""
    )
    caption = f"{base_caption}{protection_note}"

    if resource.content_type == "telegram_file" and resource.telegram_file_id:
        file_id = resource.telegram_file_id[4:] if resource.telegram_file_id.startswith("DOC:") else resource.telegram_file_id
        try:
            await callback.message.answer_document(
                document=file_id,
                caption=caption,
                parse_mode="HTML",
                protect_content=is_protected,
            )
        except Exception:
            await callback.message.answer_photo(
                photo=file_id,
                caption=caption,
                parse_mode="HTML",
                protect_content=is_protected,
            )
        return

    body_text = resource.text_content or "No text content available."
    await callback.message.answer(
        f"{caption}\n\n{body_text}",
        parse_mode="HTML",
        disable_web_page_preview=True,
        protect_content=is_protected,
    )


# --- 6. Resource Delivery ---
@router.callback_query(F.data.startswith("res:"))
async def cb_deliver_resource(callback: CallbackQuery):
    _, res_id_str, _ = callback.data.split(":", 2)
    res_id = int(res_id_str)

    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
        resource = (
            await db.execute(select(LearningResource).where(LearningResource.id == res_id))
        ).scalar_one_or_none()
        if not resource or not resource.is_published:
            await callback.answer("Resource not found.", show_alert=True)
            return

        allowed, feature = await check_feature_entitlement(db, student, resource.feature_key)
        if not allowed and not getattr(resource, "is_free_override", False):
            await callback.answer("🔒 Active Premium subscription required.", show_alert=True)
            return

        is_protected = bool(
            (feature is not None and not getattr(feature, "is_free", False))
            and not getattr(resource, "is_free_override", False)
        )

        await callback.answer("Sending file...")
        await _send_learning_resource(callback, resource, feature, is_protected)


# --- 7. Quiz Engine ---
@router.callback_query(F.data.startswith("qlvl:"))
async def cb_start_timed_quiz(callback: CallbackQuery, state: FSMContext):
    _, chapter_id_str, level = callback.data.split(":", 2)
    chapter_id = int(chapter_id_str)

    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
        allowed, _ = await check_feature_entitlement(db, student, "quiz")
        if not allowed:
            await callback.answer("🔒 Premium subscription required.", show_alert=True)
            return

        chap = (await db.execute(select(Chapter).where(Chapter.id == chapter_id))).scalar_one_or_none()
        course_id = chap.course_id if chap else 0

        questions = (await db.execute(
            select(QuizQuestion).where(
                QuizQuestion.chapter_id == chapter_id,
                QuizQuestion.level == level,
                QuizQuestion.is_published == True,
            )
        )).scalars().all()

        if not questions:
            await callback.answer()
            back_target = f"chp:quiz:{course_id}:{chapter_id}" if course_id else "nav:main"
            chap_label = f"Chapter {chap.chapter_number}" if chap else "this chapter"
            await safe_edit_message(
                callback,
                build_coming_soon_message(f"Timed Quiz ({level})", chap_label),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_and_home_row(back_target)]),
            )
            return

        q_list = [
            {
                "id": q.id,
                "text": q.question_text,
                "options": q.options,
                "correct": q.correct_option,
                "explanation": q.explanation,
                "time_limit": q.time_limit_sec,
            }
            for q in questions
        ]

    await callback.answer()
    await state.set_state(QuizSession.in_progress)
    await state.update_data(
        chapter_id=chapter_id,
        course_id=course_id,
        level=level,
        questions=q_list,
        current_index=0,
        score=0,
        quiz_start_ts=time.time(),
        q_start_ts=time.time(),
    )
    await send_quiz_question(callback.message, state, edit=True)


async def send_quiz_question(message: Message, state: FSMContext, edit: bool = False):
    data = await state.get_data()
    idx = data["current_index"]
    q = data["questions"][idx]
    await state.update_data(q_start_ts=time.time())

    buttons = [
        [InlineKeyboardButton(text=opt, callback_data=f"qans:{idx}:{opt_i}")]
        for opt_i, opt in enumerate(q["options"])
    ]
    buttons.append([InlineKeyboardButton(text="🛑 Quit Quiz", callback_data="nav:main")])

    safe_level = html.escape(str(data["level"]))
    text = (
        f"⏱ <b>Quiz — {safe_level}</b>\n"
        f"Question <b>{idx + 1} of {len(data['questions'])}</b>\n\n"
        f"{q['text']}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit:
        try:
            await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await message.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(QuizSession.in_progress, F.data.startswith("qans:"))
async def cb_handle_quiz_answer(callback: CallbackQuery, state: FSMContext):
    _, q_idx_str, chosen_str = callback.data.split(":", 2)
    q_idx, chosen = int(q_idx_str), int(chosen_str)
    data = await state.get_data()
    if q_idx != data["current_index"]:
        await callback.answer("Already answered.", show_alert=True)
        return

    await callback.answer()
    q = data["questions"][q_idx]
    is_correct = (chosen == q["correct"])
    new_score = data["score"] + (1 if is_correct else 0)
    await state.update_data(score=new_score)

    verdict = "✅ <b>Correct!</b>" if is_correct else "❌ <b>Incorrect</b>"
    explanation_text = f"{verdict}\n\n<b>Explanation:</b>\n{q['explanation']}"

    next_idx = q_idx + 1
    if next_idx < len(data["questions"]):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➡️ Next Question", callback_data=f"qnext:{next_idx}")]])
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏁 View Final Score", callback_data="qfinish")]])

    await safe_edit_message(callback, explanation_text, reply_markup=kb)


@router.callback_query(QuizSession.in_progress, F.data.startswith("qnext:"))
async def cb_next_quiz_question(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(current_index=int(callback.data.split(":", 1)[1]))
    await send_quiz_question(callback.message, state, edit=True)


def _format_duration_mm_ss(seconds: int) -> str:
    mins, secs = divmod(max(0, int(seconds)), 60)
    return f"{mins:02d}:{secs:02d}"


def _build_accuracy_bar(accuracy_pct: float, width: int = 10) -> str:
    filled = max(0, min(width, round((accuracy_pct / 100.0) * width)))
    return "🟩" * filled + "⬜" * (width - filled)


@router.callback_query(QuizSession.in_progress, F.data == "qfinish")
async def cb_finish_quiz(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    total_time = int(time.time() - data.get("quiz_start_ts", time.time()))
    score = int(data.get("score", 0))
    questions = data.get("questions") or []
    total_q = len(questions)
    chapter_id = int(data.get("chapter_id", 0))
    level = str(data.get("level", "Medium"))

    accuracy_pct = (score / total_q * 100.0) if total_q > 0 else 0.0
    xp_earned = (score * 15) + 5
    pace_str = _format_duration_mm_ss(total_time)
    bar_str = _build_accuracy_bar(accuracy_pct)

    course_id = int(data.get("course_id", 0))
    async with AsyncSessionLocal() as db:
        db.add(QuizAttempt(
            student_id=callback.from_user.id,
            chapter_id=chapter_id,
            level=level,
            score=score,
            total_questions=total_q,
            duration_seconds=total_time,
        ))
        if not course_id and chapter_id:
            chap_obj = (await db.execute(select(Chapter).where(Chapter.id == chapter_id))).scalar_one_or_none()
            if chap_obj:
                course_id = chap_obj.course_id
        await db.commit()

    if accuracy_pct >= 85.0:
        tier_title = "🌟 Exam-Ready Mastery!"
        advice = "Strong command of this chapter. Try a higher difficulty or test another chapter to lock it in."
    elif accuracy_pct >= 65.0:
        tier_title = "💪 Solid Progress!"
        advice = "Good foundation—review the explanations you missed and run one more round to hit 80%+."
    elif accuracy_pct >= 40.0:
        tier_title = "📘 Building Momentum"
        advice = "Check your Study Notes or Flashcards for this chapter, then retry the quiz to boost your score."
    else:
        tier_title = "🎯 Warm-Up Round Complete"
        advice = "Every attempt builds recall. Revisit the chapter notes first and take another shot at your own pace."

    kb_rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="🔁 Retry Level", callback_data=f"qlvl:{chapter_id}:{level}"),
            InlineKeyboardButton(text="🏆 Leaderboard", callback_data="nav:leaderboard:weekly"),
        ]
    ]
    if course_id:
        kb_rows.append([InlineKeyboardButton(text="⬅️ Back to Chapters", callback_data=f"crs:quiz:{course_id}")])
    kb_rows.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="nav:main")])

    safe_level = html.escape(level)
    text = (
        f"🏁 <b>Quiz Completed — {tier_title}</b>\n\n"
        f"📌 <b>Level:</b> {safe_level} · <b>Score:</b> <b>{score}/{total_q}</b> (<b>{accuracy_pct:.0f}%</b>)\n"
        f"📊 <b>Accuracy:</b> {bar_str}\n"
        f"⏱ <b>Time Taken:</b> <code>{pace_str}</code> · ✨ <b>XP Earned:</b> <b>+{xp_earned} XP</b>\n\n"
        f"💡 <b>Coach Note:</b> <i>{html.escape(advice)}</i>\n"
        f"📢 <b>Channel:</b> <a href=\"{CHANNEL_URL}\">{CHANNEL_HANDLE}</a>"
    )

    await state.clear()
    await safe_edit_message(callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


# --- 8. Flashcards ---
async def render_flashcard(callback: CallbackQuery, cards, index: int, course_id: int, chapter_id: int, show_back: bool):
    card = cards[index]
    flip_cb = f"fc:{course_id}:{chapter_id}:{index}:{'0' if show_back else '1'}"
    nav_row = []
    if index > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"fc:{course_id}:{chapter_id}:{index-1}:0"))
    nav_row.append(InlineKeyboardButton(text="🔄 Flip", callback_data=flip_cb))
    if index < len(cards) - 1:
        nav_row.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"fc:{course_id}:{chapter_id}:{index+1}:0"))

    kb = InlineKeyboardMarkup(inline_keyboard=[nav_row, back_and_home_row(f"crs:flashcards:{course_id}")])
    body = card.back_text if show_back else card.front_text
    side_label = "Back (Answer)" if show_back else "Front (Prompt)"
    await safe_edit_message(
        callback,
        f"🃏 <b>Flashcard {index + 1} of {len(cards)}</b> · <i>{side_label}</i>\n\n{body}",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("fc:"))
async def cb_flashcard_step(callback: CallbackQuery):
    await callback.answer()
    _, c_id, ch_id, idx_s, b_s = callback.data.split(":", 4)
    async with AsyncSessionLocal() as db:
        cards = (await db.execute(
            select(Flashcard).where(Flashcard.chapter_id == int(ch_id), Flashcard.is_published == True)
        )).scalars().all()
    if not cards:
        await callback.answer("No flashcards available.", show_alert=True)
        return
    await render_flashcard(callback, cards, int(idx_s), int(c_id), int(ch_id), (b_s == "1"))


# --- 9. Membership & Payments ---
@router.callback_query(F.data == "nav:membership")
async def cb_membership(callback: CallbackQuery):
    await callback.answer()
    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
        plan_badge = format_plan_badge(student)
        active_premium = has_active_premium(student)
        current_code = (student.plan_code or "FREE").upper()
        plans = (await db.execute(
            select(SubscriptionPlan)
            .where(SubscriptionPlan.is_active == True, SubscriptionPlan.price_etb > 0)
            .order_by(SubscriptionPlan.price_etb)
        )).scalars().all()

    plus_plan = next((p for p in plans if "PLUS" in (p.code or "").upper()), plans[0] if plans else None)
    pro_plan = next((p for p in plans if "PRO" in (p.code or "").upper()), plans[1] if len(plans) > 1 else None)

    def plan_button_label(plan_obj: SubscriptionPlan | None, default_title: str, default_price: int) -> str:
        if not plan_obj:
            return f"{default_title} ({default_price} ETB)"
        price_val = int(plan_obj.price_etb)
        is_current = active_premium and current_code == (plan_obj.code or "").upper()
        if is_current:
            return f"✓ Active: {plan_obj.name}"
        if default_price >= 500:
            return f"💎 Get {plan_obj.name} ({price_val} ETB)"
        return f"👑 Upgrade to {plan_obj.name} ({price_val} ETB)"

    row1: list[InlineKeyboardButton] = []
    if plus_plan:
        row1.append(InlineKeyboardButton(
            text=plan_button_label(plus_plan, "Qine Plus", 300),
            callback_data=f"mplan:{plus_plan.code}",
        ))
    if pro_plan:
        row1.append(InlineKeyboardButton(
            text=plan_button_label(pro_plan, "Qine Pro", 500),
            callback_data=f"mplan:{pro_plan.code}",
        ))

    if not row1:
        for p in plans[:2]:
            row1.append(InlineKeyboardButton(
                text=f"👑 {p.name} ({int(p.price_etb)} ETB)",
                callback_data=f"mplan:{p.code}",
            ))

    comparison_lines = [
        "💎 <b>QineSystems Premium Membership</b>",
        "Your learning experience, upgraded.",
        f"Current Plan: <b>{plan_badge}</b>",
        "",
        "<b>👑 Qine Plus — 300 ETB</b>",
        "• ⏱ Timed Quizzes with Explanations",
        "• 🎓 Final Exams &amp; Flashcards",
        "• 🎯 Exam Trainer &amp; Study Tools",
        "",
        "<b>💎 Qine Pro — 500 ETB</b>",
        "• ✨ Everything in Qine Plus",
        "• 🤖 Full AI Study Assistant Suite",
        "• 🚀 Higher AI Usage Limits",
        "• 📄 AI-Powered Document Q&amp;A",
        "",
        "<i>Select a plan below to view payment options and activate your benefits:</i>",
    ]

    kb = InlineKeyboardMarkup(inline_keyboard=[
        row1,
        [InlineKeyboardButton(text="🎁 Referral Hub", callback_data="nav:referral")],
        back_and_home_row("nav:main"),
    ])
    await safe_edit_message(callback, "\n".join(comparison_lines), reply_markup=kb)


def format_payment_provider_label(pm: PaymentMethodConfig) -> str:
    raw_name = (pm.provider_name or pm.code or "").strip()
    upper_combo = f"{(pm.code or '').upper()} {raw_name.upper()}"
    if "CBE" in upper_combo or "COMMERCIAL BANK OF ETHIOPIA" in upper_combo:
        return "CBE"
    return raw_name


@router.callback_query(F.data.startswith("mplan:"))
async def cb_select_membership_plan(callback: CallbackQuery):
    plan_code = callback.data.split(":", 1)[1]
    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
        plan_badge = format_plan_badge(student)
        plan = (await db.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.code == plan_code)
        )).scalar_one_or_none()
        methods = (await db.execute(
            select(PaymentMethodConfig)
            .where(PaymentMethodConfig.is_verified_active == True)
            .order_by(PaymentMethodConfig.code)
        )).scalars().all()

    if not plan:
        await callback.answer("Selected plan is unavailable.", show_alert=True)
        return

    await callback.answer()
    preferred_order = ["TELEBIRR", "BOA", "ABYSSINIA", "CBE"]

    def method_sort_key(pm: PaymentMethodConfig) -> tuple[int, int | str]:
        code_u = (pm.code or "").upper()
        name_u = (pm.provider_name or "").upper()
        for idx, token in enumerate(preferred_order):
            if token in code_u or token in name_u:
                return (0, idx)
        return (1, code_u)

    ordered_methods = sorted(methods, key=method_sort_key)[:3]

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text=f"💳 Pay via {format_payment_provider_label(pm)}",
            callback_data=f"pay:{plan.code}:{pm.code}",
        )]
        for pm in ordered_methods
    ]

    if not rows:
        rows.append([InlineKeyboardButton(text="🆘 Contact Support for Payment", callback_data="nav:help")])

    rows.append([InlineKeyboardButton(text="⬅️ Change Plan", callback_data="nav:membership")])
    rows.append(back_and_home_row("nav:main"))

    text = (
        f"💎 <b>{html.escape(plan.name)} — {int(plan.price_etb)} ETB</b>\n"
        f"Current Status: <b>{plan_badge}</b>\n\n"
        f"{plan.benefits_text}\n\n"
        f"Select a payment method below:"
    )
    await safe_edit_message(callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("pay:"))
async def cb_initiate_payment(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":", 2)
    if len(parts) < 3:
        await callback.answer("Invalid payment selection.", show_alert=True)
        return

    _, plan_code, pm_code = parts
    async with AsyncSessionLocal() as db:
        plan = (await db.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.code == plan_code)
        )).scalar_one_or_none()
        pm = (await db.execute(
            select(PaymentMethodConfig).where(PaymentMethodConfig.code == pm_code)
        )).scalar_one_or_none()
        if not pm:
            all_methods = (await db.execute(select(PaymentMethodConfig))).scalars().all()
            pm = next(
                (m for m in all_methods if (m.code or "").strip().upper() == pm_code.strip().upper()),
                None,
            )

    if not plan or not pm:
        await callback.answer("Payment option is not available right now.", show_alert=True)
        return

    await callback.answer()
    await state.set_state(PaymentFlow.waiting_for_receipt)
    await state.update_data(plan_code=plan.code, pm_code=pm.code)

    short_provider = html.escape(str(format_payment_provider_label(pm)))
    note_text = (pm.instructions or "").strip()
    note_block = f"\n\n📌 <b>Payment Notes:</b>\n{html.escape(note_text)}" if note_text else ""

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Change Payment Method", callback_data=f"mplan:{plan.code}")],
        back_and_home_row("nav:membership"),
    ])

    await safe_edit_message(
        callback,
        f"🏦 <b>Payment Instructions — {short_provider}</b>\n\n"
        f"• <b>Plan:</b> {html.escape(str(plan.name))} ({int(plan.price_etb)} ETB)\n"
        f"• <b>Account Holder:</b> <code>{html.escape(str(pm.account_holder))}</code>\n"
        f"• <b>Account Number:</b> <code>{html.escape(str(pm.account_number))}</code>"
        f"{note_block}\n\n"
        f"📤 Send a photo or document of your payment receipt here once you complete the transfer.",
        reply_markup=kb,
    )


@router.message(PaymentFlow.waiting_for_receipt, F.photo | F.document)
async def receive_payment_receipt(message: Message, state: FSMContext):
    data = await state.get_data()
    plan_code = data.get("plan_code")
    pm_code = data.get("pm_code")

    if not plan_code or not pm_code:
        await state.clear()
        await message.answer(
            "⚠️ <b>Session Expired</b>\nPlease select your plan and payment method again from Membership.",
            reply_markup=build_main_menu(message.from_user.id),
            parse_mode="HTML",
        )
        return

    is_photo = bool(message.photo)
    file_id = message.photo[-1].file_id if is_photo else message.document.file_id
    stored_file_id = file_id if is_photo else f"DOC:{file_id}"
    media_label = "Photo Receipt" if is_photo else (
        f"Document ({html.escape(message.document.file_name)})"
        if message.document and message.document.file_name
        else "Document Receipt"
    )

    async with AsyncSessionLocal() as db:
        plan = (
            await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.code == plan_code))
        ).scalar_one_or_none()
        pm = (
            await db.execute(select(PaymentMethodConfig).where(PaymentMethodConfig.code == pm_code))
        ).scalar_one_or_none()

        receipt = PaymentReceipt(
            student_id=message.from_user.id,
            plan_code=plan_code,
            payment_method=pm_code,
            receipt_file_id=stored_file_id,
            status="PENDING",
        )
        db.add(receipt)
        await db.commit()
        await db.refresh(receipt)

    await state.clear()
    await message.answer(
        f"✅ <b>Receipt #{receipt.id} Submitted!</b>\n"
        f"Your payment for <b>{html.escape(plan.name if plan else plan_code)}</b> via "
        f"<b>{html.escape(pm.provider_name if pm else pm_code)}</b> is now in the admin verification queue.",
        reply_markup=build_main_menu(message.from_user.id),
        parse_mode="HTML",
    )

    safe_name = html.escape(message.from_user.full_name or "Student")
    username_line = (
        f"@{html.escape(message.from_user.username)}"
        if message.from_user.username
        else "<i>No username</i>"
    )
    plan_display = (
        f"{html.escape(plan.name)} (<code>{html.escape(plan_code)}</code> · <b>{int(plan.price_etb)} ETB</b>)"
        if plan
        else f"<code>{html.escape(plan_code)}</code>"
    )
    method_display = (
        f"{html.escape(pm.provider_name)} (<code>{html.escape(pm_code)}</code>)"
        if pm
        else f"<code>{html.escape(pm_code)}</code>"
    )
    submitted_at = (
        receipt.created_at.strftime("%Y-%m-%d %H:%M UTC")
        if getattr(receipt, "created_at", None)
        else datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    )

    admin_caption = (
        f"🔔 <b>New Payment Receipt · #{receipt.id}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Student:</b> {safe_name} ({username_line})\n"
        f"🆔 <b>Telegram ID:</b> <code>{message.from_user.id}</code>\n"
        f"💎 <b>Plan:</b> {plan_display}\n"
        f"💳 <b>Method:</b> {method_display}\n"
        f"📎 <b>Attachment:</b> {media_label}\n"
        f"🕒 <b>Submitted:</b> <code>{submitted_at}</code>\n"
        f"📌 <b>Status:</b> <b>PENDING</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Verify the transfer details on the attached receipt before approving.</i>"
    )

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Approve Plan", callback_data=f"adm_pay:approve:{receipt.id}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"adm_pay:reject:{receipt.id}"),
        ],
        [
            InlineKeyboardButton(text="💰 Payment Hub", callback_data="pro:payments"),
        ],
    ])

    for admin_id in get_live_admin_ids():
        try:
            if is_photo:
                await message.bot.send_photo(
                    admin_id,
                    file_id,
                    caption=admin_caption[:1024],
                    reply_markup=admin_kb,
                    parse_mode="HTML",
                )
            else:
                await message.bot.send_document(
                    admin_id,
                    file_id,
                    caption=admin_caption[:1024],
                    reply_markup=admin_kb,
                    parse_mode="HTML",
                )
        except Exception:
            pass


@router.callback_query(F.data.startswith("adm_pay:"))
async def cb_admin_review_payment(callback: CallbackQuery):
    if callback.from_user.id not in get_live_admin_ids():
        await callback.answer("Unauthorized.", show_alert=True)
        return
    _, action, r_id_s = callback.data.split(":", 2)
    r_id = int(r_id_s)
    async with AsyncSessionLocal() as db:
        receipt = (await db.execute(select(PaymentReceipt).where(PaymentReceipt.id == r_id))).scalar_one_or_none()
        if not receipt or receipt.status != "PENDING":
            await callback.answer("Already processed.", show_alert=True)
            return
        student = (await db.execute(select(Student).where(Student.telegram_id == receipt.student_id))).scalar_one()
        plan = (await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.code == receipt.plan_code))).scalar_one()

        if action == "approve":
            receipt.status = "APPROVED"
            student.plan_code = plan.code
            student.subscription_expires_at = datetime.utcnow() + timedelta(days=plan.duration_days)
            await db.commit()
            await callback.answer("Approved!")
            await callback.bot.send_message(
                student.telegram_id,
                f"🎉 <b>Payment Approved!</b> Plan {html.escape(plan.name)} active.\n📢 Channel: <a href=\"{CHANNEL_URL}\">{CHANNEL_HANDLE}</a>",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        else:
            receipt.status = "REJECTED"
            await db.commit()
            await callback.answer("Rejected.")
            await callback.bot.send_message(student.telegram_id, "⚠️ Payment receipt rejected.", parse_mode="HTML")
    try:
        safe_admin_name = html.escape(callback.from_user.first_name or "Admin")
        await callback.message.edit_caption(
            caption=f"{callback.message.caption}\n\nProcessed by {safe_admin_name}",
            parse_mode="HTML",
        )
    except Exception:
        pass


# --- 10. Student Utilities ---
@router.callback_query(F.data == "nav:my_courses")
async def cb_my_courses(callback: CallbackQuery, state: FSMContext):
    await cb_open_year_hub(callback, state)


@router.callback_query(F.data == "nav:my_progress")
async def cb_my_progress(callback: CallbackQuery):
    await callback.answer()
    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
        plan_badge = format_plan_badge(student)
        attempts = (await db.execute(
            select(QuizAttempt)
            .where(QuizAttempt.student_id == callback.from_user.id)
            .order_by(QuizAttempt.id.desc())
        )).scalars().all()
        streak_days = await get_student_challenge_streak_days(db, callback.from_user.id)

    total_quizzes = len(attempts)
    total_correct = sum(max(0, int(a.score or 0)) for a in attempts)
    total_questions = sum(max(0, int(a.total_questions or 0)) for a in attempts)
    total_seconds = sum(max(0, int(a.duration_seconds or 0)) for a in attempts)

    accuracy_pct = (total_correct / total_questions * 100.0) if total_questions > 0 else 0.0
    accuracy_bar = _build_accuracy_bar(accuracy_pct) if total_questions > 0 else "⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜"
    study_time_str = _format_duration_mm_ss(total_seconds)
    estimated_xp = (total_correct * 15) + (total_quizzes * 5)

    by_level: dict[str, list[float]] = {}
    for a in attempts:
        if (a.total_questions or 0) > 0:
            lvl_name = (a.level or "Medium").strip()
            by_level.setdefault(lvl_name, []).append((a.score or 0) / a.total_questions * 100.0)

    level_breakdown_lines: list[str] = []
    weakest_level: str | None = None
    weakest_avg = 101.0
    for lvl_name, pcts in by_level.items():
        avg_lvl = sum(pcts) / len(pcts)
        level_breakdown_lines.append(f"  • <b>{html.escape(lvl_name)}:</b> {avg_lvl:.0f}% ({len(pcts)} attempt(s))")
        if avg_lvl < weakest_avg:
            weakest_avg = avg_lvl
            weakest_level = lvl_name

    if total_quizzes == 0:
        readiness_tag = "🌱 Getting Started"
        recommendation = (
            "Pick your academic year (1st, 2nd, or 3rd Year) to take a chapter quiz or try today's "
            "Daily Challenge to earn your first XP and set a baseline."
        )
    elif accuracy_pct < 55.0:
        readiness_tag = "📘 Concept Building"
        focus_hint = f" around {weakest_level} questions" if weakest_level else ""
        recommendation = (
            f"Review Study Notes and Study Resources first{focus_hint}, read every quiz explanation carefully, "
            "and aim for consistent accuracy before jumping to harder sets."
        )
    elif accuracy_pct < 80.0:
        readiness_tag = "💪 Steady Momentum"
        recommendation = (
            "You're building strong recall. Re-attempt missed quiz levels to push accuracy past 80% "
            "and unlock stronger exam readiness."
        )
    else:
        readiness_tag = "🌟 Exam-Ready Pace"
        recommendation = (
            "High accuracy across your practice rounds! Keep your streak alive with Daily Challenges "
            "and test yourself against timed exam/quiz sets to stay sharp."
        )

    level_section = ""
    if level_breakdown_lines:
        level_section = "\n<b>📈 By Difficulty Level</b>\n" + "\n".join(level_breakdown_lines) + "\n"

    streak_str = f"<b>{streak_days} day(s)</b>" if streak_days > 0 else "<i>0 days (start today!)</i>"
    safe_student_name = html.escape(student.full_name or "Student")

    text = (
        f"📊 <b>QineSystems Learning Progress</b>\n"
        f"👤 <b>{safe_student_name}</b> · <b>{plan_badge}</b> · <code>{html.escape(readiness_tag)}</code>\n\n"
        f"<b>🎯 Mastery &amp; Activity</b>\n"
        f"• Completed Quizzes: <b>{total_quizzes}</b>\n"
        f"• Answered Correctly: <b>{total_correct}/{total_questions}</b> (<b>{accuracy_pct:.0f}%</b>)\n"
        f"• Mastery Bar: {accuracy_bar}\n"
        f"• Practice Time: <code>{study_time_str}</code> · Quiz XP: <b>+{estimated_xp} XP</b>\n"
        f"• Daily Challenge Streak: {streak_str}"
        f"{level_section}\n"
        f"<b>💡 Recommended Next Step</b>\n"
        f"<i>{html.escape(recommendation)}</i>\n\n"
        f"📢 <a href=\"{CHANNEL_URL}\">{CHANNEL_HANDLE}</a>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚡ Daily Challenge", callback_data="dc:hub"),
            InlineKeyboardButton(text="🏆 Leaderboard", callback_data="nav:leaderboard:weekly"),
        ],
        [
            InlineKeyboardButton(text="🎓 1st Year", callback_data="yr:1"),
            InlineKeyboardButton(text="📘 2nd Year", callback_data="yr:2"),
            InlineKeyboardButton(text="📙 3rd Year", callback_data="yr:3"),
        ],
        back_and_home_row("nav:main"),
    ])
    await safe_edit_message(callback, text, reply_markup=kb)


@router.callback_query(F.data == "nav:ai_help")
async def cb_nav_ai_help(callback: CallbackQuery, state: FSMContext):
    await show_ai_suite(callback, state)


# --- 11. Daily Challenge ---
def build_dc_hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💻 Coding", callback_data="dc:open:coding"),
            InlineKeyboardButton(text="🔬 Science", callback_data="dc:open:science"),
        ],
        [
            InlineKeyboardButton(text="📖 English Vocab", callback_data="dc:open:vocab"),
            InlineKeyboardButton(text="🏆 Leaderboard", callback_data="nav:leaderboard:weekly"),
        ],
        back_and_home_row("nav:main"),
    ])


def build_dc_question_kb(track: str, hint_shown: bool, solution_shown: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if not solution_shown:
        if not hint_shown:
            rows.append([InlineKeyboardButton(text="💡 Need a Hint? (-5 XP)", callback_data=f"dc:hint:{track}")])
        rows.append([InlineKeyboardButton(text="✅ Reveal Solution", callback_data=f"dc:solve:{track}")])
    else:
        xp_reward = 10 if hint_shown else 15
        rows.append([InlineKeyboardButton(text=f"🌟 Claim +{xp_reward} XP", callback_data=f"dc:claim:{track}")])
        rows.append([InlineKeyboardButton(text="🔄 Try Another Track", callback_data="dc:hub")])

    rows.append(back_and_home_row("dc:hub"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "dc:hub")
async def cb_dc_hub(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    today_utc = datetime.utcnow().strftime("%Y-%m-%d")

    streak = 0
    done_tracks = []
    try:
        async with AsyncSessionLocal() as db:
            streak = await get_student_challenge_streak_days(db, callback.from_user.id)
            for t_key, (t_label, _) in TRACK_META.items():
                rec = await get_daily_challenge_status(db, callback.from_user.id, t_key, today_utc)
                if rec:
                    done_tracks.append(f"{t_label} (+{rec.xp_earned} XP)")
    except Exception as exc:
        print(f"[DailyChallenge Hub DB note]: {exc}")

    streak_line = f"🔥 <b>Active Streak:</b> <b>{streak} day(s)</b>\n" if streak > 0 else ""
    done_line = (
        f"✅ <b>Completed Today:</b> {', '.join(done_tracks)}\n\n"
        if done_tracks
        else "\n"
    )

    text = (
        f"⚡ <b>QineSystems Daily Challenge</b>\n"
        f"📅 <b>Date:</b> <code>{today_utc}</code> (UTC)\n"
        f"{streak_line}"
        f"{done_line}"
        f"Pick a track below:\n"
        f"• 💻 <b>Coding</b> — Python logic, output &amp; best practices\n"
        f"• 🔬 <b>Science</b> — core concepts &amp; reasoning\n"
        f"• 📖 <b>English Vocab</b> — high-value academic words\n\n"
        f"<i>Try solving it first, use a hint only if needed, then reveal the solution!</i>"
    )
    await safe_edit_message(callback, text, reply_markup=build_dc_hub_kb())


async def render_dc_view(
    callback: CallbackQuery,
    state: FSMContext,
    track: str,
    hint_shown: bool,
    solution_shown: bool,
    extra_banner: str = "",
):
    challenge = get_daily_challenge(track)
    await state.set_state(DailyChallengeFlow.active)
    await state.update_data(
        dc_track=track,
        dc_date=challenge["date_str"],
        dc_hint_shown=hint_shown,
        dc_solution_shown=solution_shown,
    )

    existing = None
    try:
        async with AsyncSessionLocal() as db:
            existing = await get_daily_challenge_status(db, callback.from_user.id, track, challenge["date_str"])
    except Exception as exc:
        print(f"[DailyChallenge Status DB note]: {exc}")

    already_claimed = existing is not None
    effective_hint = hint_shown or (existing.hint_used if existing else False)
    effective_solution = solution_shown or already_claimed

    parts = [
        f"⚡ <b>Daily Challenge · {challenge['track_label']}</b>",
        f"📌 <b>Topic:</b> {html.escape(challenge['title'])} · <code>{challenge['date_str']}</code>",
        "",
        f"<b>Challenge:</b>\n{challenge['prompt']}",
    ]

    if effective_hint:
        parts.extend(["", f"💡 <b>Hint:</b>\n{challenge['hint']}"])

    if effective_solution:
        parts.extend(["", f"✅ <b>Solution &amp; Explanation:</b>\n{challenge['solution']}"])

    if already_claimed and not extra_banner:
        parts.extend(["", f"🏅 <b>Already Completed Today</b> (+{existing.xp_earned} XP saved to Leaderboard)"])

    if extra_banner:
        parts.extend(["", extra_banner])

    if already_claimed:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏆 View Leaderboard", callback_data="nav:leaderboard:weekly")],
            [InlineKeyboardButton(text="🔄 Try Another Track", callback_data="dc:hub")],
            back_and_home_row("dc:hub"),
        ])
    else:
        kb = build_dc_question_kb(track, effective_hint, effective_solution)

    await safe_edit_message(callback, "\n".join(parts), reply_markup=kb)


@router.callback_query(F.data.startswith("dc:open:"))
async def cb_dc_open(callback: CallbackQuery, state: FSMContext):
    track = callback.data.split(":", 2)[2]
    await callback.answer()
    data = await state.get_data()
    today_utc = datetime.utcnow().strftime("%Y-%m-%d")
    same_session = (data.get("dc_track") == track and data.get("dc_date") == today_utc)
    await render_dc_view(
        callback,
        state,
        track,
        hint_shown=bool(same_session and data.get("dc_hint_shown")),
        solution_shown=bool(same_session and data.get("dc_solution_shown")),
    )


@router.callback_query(F.data.startswith("dc:hint:"))
async def cb_dc_hint(callback: CallbackQuery, state: FSMContext):
    track = callback.data.split(":", 2)[2]
    await callback.answer("Hint unlocked!")
    data = await state.get_data()
    await render_dc_view(
        callback,
        state,
        track,
        hint_shown=True,
        solution_shown=bool(data.get("dc_solution_shown")),
    )


@router.callback_query(F.data.startswith("dc:solve:"))
async def cb_dc_solve(callback: CallbackQuery, state: FSMContext):
    track = callback.data.split(":", 2)[2]
    await callback.answer("Solution revealed!")
    data = await state.get_data()
    await render_dc_view(
        callback,
        state,
        track,
        hint_shown=bool(data.get("dc_hint_shown")),
        solution_shown=True,
    )


@router.callback_query(F.data.startswith("dc:claim:"))
async def cb_dc_claim(callback: CallbackQuery, state: FSMContext):
    track = callback.data.split(":", 2)[2]
    data = await state.get_data()
    today_utc = datetime.utcnow().strftime("%Y-%m-%d")
    hint_used = bool(data.get("dc_hint_shown"))

    async with AsyncSessionLocal() as db:
        awarded, xp_reward = await record_daily_challenge_completion(
            db,
            student_id=callback.from_user.id,
            track=track,
            hint_used=hint_used,
            date_str=today_utc,
        )

    if not awarded:
        await callback.answer("You already claimed today's XP for this track!", show_alert=True)
        await render_dc_view(callback, state, track, hint_shown=hint_used, solution_shown=True)
        return

    await callback.answer(f"+{xp_reward} XP saved to your Leaderboard score!")
    banner = f"🎉 <b>+{xp_reward} XP</b> saved for today's <b>{TRACK_META.get(track, ('Challenge', ''))[0]}</b> challenge!"
    await render_dc_view(
        callback,
        state,
        track,
        hint_shown=hint_used,
        solution_shown=True,
        extra_banner=banner,
    )


# --- 12. Scholar Leaderboard ---
def build_leaderboard_keyboard(active_period: str) -> InlineKeyboardMarkup:
    def tab(title: str, key: str) -> str:
        return f"• {title} •" if active_period == key else title

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=tab("📅 Weekly", "weekly"), callback_data="nav:leaderboard:weekly"),
            InlineKeyboardButton(text=tab("🗓 Monthly", "monthly"), callback_data="nav:leaderboard:monthly"),
            InlineKeyboardButton(text=tab("♾ All-Time", "alltime"), callback_data="nav:leaderboard:alltime"),
        ],
        [
            InlineKeyboardButton(text="⚡ Daily Challenge", callback_data="dc:hub"),
            InlineKeyboardButton(text="🔄 Refresh", callback_data=f"nav:leaderboard:{active_period}"),
        ],
        back_and_home_row("nav:main"),
    ])


@router.callback_query(F.data.startswith("nav:leaderboard:"))
async def cb_leaderboard(callback: CallbackQuery):
    period = callback.data.split(":", 2)[2]
    if period not in {"weekly", "monthly", "alltime"}:
        period = "weekly"

    await callback.answer()
    async with AsyncSessionLocal() as db:
        data = await get_leaderboard_data(db, callback.from_user.id, period=period, limit=3)

    lines = [
        "🏆 <b>QineSystems Scholar Leaderboard</b>",
        f"🕒 <b>Window:</b> {data['label']} · <b>Active Competitors:</b> {data['total_ranked']}",
        "<i>Ranked by quiz mastery (15 XP/correct + 1.25× accuracy bonus at 80%+) &amp; daily/referral participation.</i>",
        "",
    ]

    if not data["top"]:
        lines.append("📭 <i>No quiz or study activity recorded for this period yet. Complete a quiz or daily challenge to claim #1!</i>")
    else:
        for entry in data["top"]:
            medal = MEDALS.get(entry["rank"], f"#{entry['rank']}")
            boost_tag = " ⚡<i>Mastery Boost</i>" if entry["boosted"] else ""
            safe_entry_name = html.escape(entry["name"])
            lines.append(
                f"{medal} <b>{safe_entry_name}</b> — <b>{entry['xp']} XP</b>{boost_tag}\n"
                f"    🎯 Strength: <b>{entry['accuracy']:.0f}%</b> ({entry['correct']}/{entry['questions']} ans) · "
                f"📝 Quizzes: <b>{entry['quizzes']}</b>"
            )

    lines.append("")
    viewer = data["viewer"]
    if viewer:
        lines.append(
            f"📌 <b>Your Standing:</b> Rank <b>#{viewer['rank']}</b> · "
            f"<b>{viewer['xp']} XP</b> · <b>{viewer['accuracy']:.0f}%</b> accuracy"
        )
        if viewer["rank"] > 3 and data["points_to_top3"] > 0:
            lines.append(f"🔥 <i>Earn <b>+{data['points_to_top3']} XP</b> to break into the Top 3!</i>")
    else:
        lines.append("📌 <b>Your Standing:</b> <i>Unranked this period — finish a quiz or daily challenge to get on the board!</i>")

    await safe_edit_message(
        callback,
        "\n".join(lines),
        reply_markup=build_leaderboard_keyboard(period),
    )


# --- 13. Support Tickets ---
async def notify_admins(bot_instance: Bot, html_text: str, reply_markup: InlineKeyboardMarkup | None = None):
    for admin_id in get_live_admin_ids():
        try:
            await bot_instance.send_message(
                chat_id=admin_id,
                text=html_text,
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as exc:
            print(f"[Admin Alert Error for {admin_id}]: {exc}")


@router.callback_query(F.data == "nav:help")
async def cb_help(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"sup:cat:{key}")]
        for key, (label, _) in SUPPORT_TOPICS.items()
    ]
    rows.append([
        InlineKeyboardButton(text="🤖 AI Help", callback_data="nav:ai_help"),
        InlineKeyboardButton(text="👥 Community & Channel", callback_data="nav:community"),
    ])
    rows.append(back_and_home_row("nav:main"))

    text = (
        "🆘 <b>QineSystems Student Desk &amp; Community</b>\n\n"
        "Stuck on a module, receipt, quiz, or referral? Pick a topic below to open a ticket, "
        "or use AI Help or visit the official QineSystems community links:"
    )
    await safe_edit_message(callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("sup:cat:"))
async def cb_support_category(callback: CallbackQuery, state: FSMContext):
    cat_key = callback.data.split(":", 2)[2]
    _, topic_name = SUPPORT_TOPICS.get(cat_key, ("🆘 Student Desk", "General Assistance"))
    await callback.answer()
    await state.set_state(SupportFlow.waiting_for_issue)
    await state.update_data(support_category=topic_name)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Change Topic", callback_data="nav:help")],
        back_and_home_row("nav:main"),
    ])
    text = (
        f"✍️ <b>New Ticket · {html.escape(topic_name)}</b>\n\n"
        f"Send your message now so an administrator can follow up.\n"
        f"<i>Student Reference:</i> <code>{callback.from_user.id}</code>"
    )
    await safe_edit_message(callback, text, reply_markup=kb)


@router.message(SupportFlow.waiting_for_issue)
async def receive_support_ticket(message: Message, state: FSMContext):
    data = await state.get_data()
    topic_name = data.get("support_category", "General Assistance")
    raw_body = message.text or message.caption or ("Photo attached" if message.photo else "Document attached")
    formatted_msg = f"[{topic_name}] {raw_body}"

    async with AsyncSessionLocal() as db:
        ticket = SupportTicket(
            student_id=message.from_user.id,
            message=formatted_msg,
            status="OPEN",
        )
        db.add(ticket)
        await db.commit()
        await db.refresh(ticket)

    await state.clear()
    await message.answer(
        f"📩 <b>Ticket #{ticket.id} Logged</b>\n"
        f"<b>Topic:</b> {html.escape(topic_name)}\n"
        f"Your note is in the QineSystems admin queue.",
        reply_markup=build_main_menu(message.from_user.id),
        parse_mode="HTML",
    )

    user_handle = f"@{html.escape(message.from_user.username)}" if message.from_user.username else "no username"
    safe_sender = html.escape(message.from_user.full_name or "Student")
    admin_alert = (
        f"🆕 <b>New Support Ticket #{ticket.id}</b>\n"
        f"• <b>Topic:</b> {html.escape(topic_name)}\n"
        f"• <b>Student:</b> {safe_sender} ({user_handle}) · <code>{message.from_user.id}</code>\n\n"
        f"<b>Message:</b>\n{html.escape(raw_body)}"
    )

    if message.photo:
        photo_id = message.photo[-1].file_id
        for admin_id in get_live_admin_ids():
            try:
                await message.bot.send_photo(admin_id, photo_id, caption=admin_alert[:1024], parse_mode="HTML")
            except Exception as exc:
                print(f"[Admin Ticket Photo Error for {admin_id}]: {exc}")
    elif message.document:
        doc_id = message.document.file_id
        for admin_id in get_live_admin_ids():
            try:
                await message.bot.send_document(admin_id, doc_id, caption=admin_alert[:1024], parse_mode="HTML")
            except Exception as exc:
                print(f"[Admin Ticket Doc Error for {admin_id}]: {exc}")
    else:
        await notify_admins(message.bot, admin_alert)


# --- 14. Referrals ---
def build_referral_kb(ref_code: str, ref_link: str) -> InlineKeyboardMarkup:
    share_payload = (
        f"🎓 Join QineSystems — Ethiopian University Digital Learning Platform!\n"
        f"Use my invite link to start learning:\n{ref_link}\n"
        f"Invite Code: {ref_code}"
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Share Link", switch_inline_query=share_payload)],
        [
            InlineKeyboardButton(text="📋 Copy Code", callback_data="ref:copy_code"),
            InlineKeyboardButton(text="🔗 Copy Link", callback_data="ref:copy_link"),
        ],
        [
            InlineKeyboardButton(text="🏅 Referral Board", callback_data="ref:board"),
            InlineKeyboardButton(text="🔄 Refresh", callback_data="nav:referral"),
        ],
        back_and_home_row("nav:main"),
    ])


async def _get_referral_summary(db, student: Student, bot_username: str) -> dict:
    referred_students = (await db.execute(
        select(Student).where(Student.referred_by_id == student.telegram_id)
    )).scalars().all()

    total_sent = len(referred_students)
    approved_count = sum(1 for s in referred_students if has_active_premium(s))
    pending_count = max(0, total_sent - approved_count)
    success_rate = (approved_count / total_sent * 100.0) if total_sent > 0 else 0.0
    total_earned_etb = approved_count * REFERRAL_REWARD_ETB
    ref_link = f"https://t.me/{bot_username}?start={student.referral_code}"

    return {
        "code": student.referral_code,
        "link": ref_link,
        "sent": total_sent,
        "approved": approved_count,
        "pending": pending_count,
        "success_rate": success_rate,
        "earned_etb": total_earned_etb,
    }


@router.callback_query(F.data == "nav:referral")
async def cb_referral(callback: CallbackQuery):
    await callback.answer()
    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
        bot_info = await callback.bot.get_me()
        info = await _get_referral_summary(db, student, bot_info.username)

    text = (
        f"🎁 <b>Referral Hub</b>\n\n"
        f"Earn <b>{REFERRAL_REWARD_ETB} ETB</b> for every successful student referral.\n"
        f"<i>Referrals count once your invited friend activates a paid plan.</i>\n\n"
        f"🎫 <b>Your Invite Code:</b> <code>{html.escape(info['code'])}</code>\n"
        f"🔗 <b>Your Invite Link:</b> <code>{html.escape(info['link'])}</code>\n\n"
        f"📊 <b>Your Stats</b>\n"
        f"• Sent: <b>{info['sent']}</b>\n"
        f"• ✅ Approved: <b>{info['approved']}</b>\n"
        f"• ⏳ Pending: <b>{info['pending']}</b>\n"
        f"• 📈 Success Rate: <b>{info['success_rate']:.0f}%</b>\n"
        f"• 💰 Total Earned: <b>{info['earned_etb']} ETB</b>\n\n"
        f"📢 <b>Channel:</b> <a href=\"{CHANNEL_URL}\">{CHANNEL_HANDLE}</a>"
    )
    await safe_edit_message(callback, text, reply_markup=build_referral_kb(info["code"], info["link"]))


@router.callback_query(F.data == "ref:copy_code")
async def cb_ref_copy_code(callback: CallbackQuery):
    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
    await callback.answer(f"Invite Code: {student.referral_code}", show_alert=True)


@router.callback_query(F.data == "ref:copy_link")
async def cb_ref_copy_link(callback: CallbackQuery):
    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
    bot_info = await callback.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={student.referral_code}"
    await callback.answer(f"Invite Link:\n{ref_link}", show_alert=True)


@router.callback_query(F.data == "ref:board")
async def cb_ref_board(callback: CallbackQuery):
    await callback.answer()
    async with AsyncSessionLocal() as db:
        all_students = (await db.execute(select(Student))).scalars().all()

    approved_by_referrer: dict[int, int] = {}
    total_by_referrer: dict[int, int] = {}
    for st in all_students:
        if st.referred_by_id:
            rid = st.referred_by_id
            total_by_referrer[rid] = total_by_referrer.get(rid, 0) + 1
            if has_active_premium(st):
                approved_by_referrer[rid] = approved_by_referrer.get(rid, 0) + 1

    student_map = {s.telegram_id: s for s in all_students}
    ranked = []
    for rid, total_cnt in total_by_referrer.items():
        app_cnt = approved_by_referrer.get(rid, 0)
        earned = app_cnt * REFERRAL_REWARD_ETB
        owner = student_map.get(rid)
        label = owner.full_name.strip() if owner and owner.full_name else f"User #{str(rid)[-4:]}"
        ranked.append((app_cnt, total_cnt, earned, label))

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)

    medals = ["🥇", "🥈", "🥉"]
    lines = [
        "🏅 <b>Referral Board</b>",
        f"Reward: <b>{REFERRAL_REWARD_ETB} ETB</b> per approved referral",
        "",
    ]

    if not ranked:
        lines.append("📭 <i>No referrals recorded yet. Share your link to be the first on the board!</i>")
    else:
        for idx, (app_cnt, total_cnt, earned, label) in enumerate(ranked[:10]):
            badge = medals[idx] if idx < 3 else f"#{idx + 1}"
            lines.append(
                f"{badge} <b>{html.escape(label)}</b> — <b>{app_cnt} approved</b> ({total_cnt} invited) · <b>{earned} ETB</b>"
            )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Back to Referral Hub", callback_data="nav:referral")],
        back_and_home_row("nav:main"),
    ])
    await safe_edit_message(callback, "\n".join(lines), reply_markup=kb)


# --- 15. Community ---
@router.callback_query(F.data == "nav:community")
async def cb_open_community(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()

    text = (
        "👥 <b>QineSystems Community Hub</b>\n\n"
        "Connect with fellow university students, get announcements, watch study tutorials, "
        "and visit official resources:\n\n"
        "• <b>Telegram Channel &amp; Group:</b> Updates, tips &amp; peer study discussions\n"
        "• <b>YouTube:</b> Video walkthroughs &amp; study guides\n"
        "• <b>Website:</b> Official platform &amp; resources\n\n"
        "<i>Tip: Only join official QineSystems links shared here—never share your account or payment details in public chats.</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📣 Telegram Channel", url="https://t.me/QineSystems"),
            InlineKeyboardButton(text="💬 Student Group", url="https://t.me/QineSystems_fresh"),
        ],
        [
            InlineKeyboardButton(text="▶️ YouTube Channel", url="https://www.youtube.com/@qinesystems"),
            InlineKeyboardButton(text="🌐 Official Website", url="https://qinesystems.vercel.app"),
        ],
        back_and_home_row("nav:help"),
    ])
    await safe_edit_message(callback, text, reply_markup=kb)