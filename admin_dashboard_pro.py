import csv
import html
import io
import os
from datetime import datetime, timedelta
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv
from sqlalchemy import func, select

from database import AsyncSessionLocal
from models import (
    Chapter,
    Course,
    DailyChallengeCompletion,
    FeatureConfig,
    Flashcard,
    LearningResource,
    PaymentMethodConfig,
    PaymentReceipt,
    QuizAttempt,
    QuizQuestion,
    Student,
    SupportTicket,
)

admin_pro_router = Router()

PLAN_DURATIONS_DAYS: dict[str, int] = {
    "QINE_PLUS": 30,
    "QINE_PRO": 90,
}

REFERRAL_REWARD_ETB = 30

YEAR_OPTIONS: list[tuple[str, str]] = [
    ("1st Year", "🎓 1st Year"),
    ("2nd Year", "📘 2nd Year"),
    ("3rd Year", "📙 3rd Year"),
]

YEAR_SHORT_BADGE: dict[str, str] = {
    "1st Year": "1st Yr",
    "2nd Year": "2nd Yr",
    "3rd Year": "3rd Yr",
}


def is_pro_admin(user_id: int) -> bool:
    load_dotenv(override=True)
    admin_ids = [
        int(x.strip())
        for x in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
        if x.strip().isdigit()
    ]
    return user_id in admin_ids


class ProAdminFSM(StatesGroup):
    broadcasting = State()
    confirming_broadcast = State()
    validating_csv = State()
    editing_pm_field = State()
    adding_course = State()


def pro_admin_home_kb(pending_count: int, open_tickets: int = 0) -> InlineKeyboardMarkup:
    approval_text = (
        f"✅ Approve {pending_count} Waiting Receipt(s)"
        if pending_count > 0
        else "✅ Approve 0 Waiting Receipts"
    )
    ticket_btn_text = (
        f"🎫 Tickets ({open_tickets} Open)"
        if open_tickets > 0
        else "🎫 Support Tickets"
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=approval_text, callback_data="pro:queue")],
        [
            InlineKeyboardButton(text="💰 Payments Hub", callback_data="pro:payments"),
            InlineKeyboardButton(text=ticket_btn_text, callback_data="pro:tickets"),
        ],
        [
            InlineKeyboardButton(text="📚 Subjects & Years", callback_data="pro:subjects"),
            InlineKeyboardButton(text="❓ Question Bank", callback_data="pro:questions"),
        ],
        [
            InlineKeyboardButton(text="📦 Feature Tiers", callback_data="pro:categories"),
            InlineKeyboardButton(text="🩺 Content Health", callback_data="pro:health"),
        ],
        [
            InlineKeyboardButton(text="👥 Students", callback_data="pro:users"),
            InlineKeyboardButton(text="🎁 Referrals & Rewards", callback_data="pro:referrals"),
        ],
        [
            InlineKeyboardButton(text="📊 Dashboard", callback_data="pro:dashboard"),
            InlineKeyboardButton(text="📈 Analytics & Activity", callback_data="pro:analytics"),
        ],
        [
            InlineKeyboardButton(text="🚀 Bulk Import", callback_data="adm:bulk_hub"),
            InlineKeyboardButton(text="🛠 Full CRUD", callback_data="adm:crud_hub"),
        ],
        [
            InlineKeyboardButton(text="📋 CSV Validator", callback_data="pro:csv_validate"),
            InlineKeyboardButton(text="📢 Broadcast", callback_data="pro:broadcast"),
        ],
        [
            InlineKeyboardButton(text="🔄 Refresh", callback_data="adm:home_pro"),
            InlineKeyboardButton(text="🏠 Student Menu", callback_data="nav:main"),
        ],
    ])


def payment_hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏳ Pending Receipts", callback_data="pay:pending:0"),
            InlineKeyboardButton(text="📋 All Receipts", callback_data="pay:all:0"),
        ],
        [
            InlineKeyboardButton(text="💳 Payment Accounts", callback_data="pay:methods"),
            InlineKeyboardButton(text="📈 Ledger Stats", callback_data="pay:stats"),
        ],
        [
            InlineKeyboardButton(text="✅ Approve All Pending", callback_data="pay:approve_all_confirm"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Back to Control Center", callback_data="adm:home_pro"),
        ],
    ])


def back_pro_kb(
    target: str = "adm:home_pro",
    label: str = "⬅️ Back to Control Center",
    extra_rows: list[list[InlineKeyboardButton]] | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = list(extra_rows or [])
    rows.append([InlineKeyboardButton(text=label, callback_data=target)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def safe_edit_or_send(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
):
    try:
        await callback.message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        await callback.message.answer(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


@admin_pro_router.callback_query(F.data.in_(["adm:home", "adm:home_pro"]))
async def cb_pro_admin_home(callback: CallbackQuery, state: FSMContext):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("⛔ Unauthorized.", show_alert=True)
        return

    await state.clear()
    await callback.answer()

    try:
        async with AsyncSessionLocal() as db:
            total_users = (await db.execute(select(func.count(Student.telegram_id)))).scalar() or 0
            paid_users = (await db.execute(
                select(func.count(Student.telegram_id)).where(Student.plan_code != "FREE")
            )).scalar() or 0
            pending_count = (await db.execute(
                select(func.count(PaymentReceipt.id)).where(PaymentReceipt.status == "PENDING")
            )).scalar() or 0
            open_tickets = (await db.execute(
                select(func.count(SupportTicket.id)).where(SupportTicket.status == "OPEN")
            )).scalar() or 0
            total_questions = (await db.execute(select(func.count(QuizQuestion.id)))).scalar() or 0
            total_subjects = (await db.execute(select(func.count(Course.id)))).scalar() or 0
            total_categories = (await db.execute(select(func.count(FeatureConfig.key)))).scalar() or 12
            courses = (await db.execute(select(Course))).scalars().all()

        yr_counts: dict[str, int] = {}
        for c in courses:
            yr = getattr(c, "year_level", None) or "1st Year"
            yr_counts[yr] = yr_counts.get(yr, 0) + 1

        now_utc = datetime.utcnow().strftime("%d %b %Y %H:%M UTC")
        queue_line = (
            f"⚠️ <b>{pending_count} payment receipt(s) waiting for approval.</b>\n"
            if pending_count > 0
            else "✅ <b>0 payment receipts waiting in queue.</b>\n"
        )
        ticket_line = f"🎫 <b>{open_tickets} open support ticket(s).</b>\n\n" if open_tickets > 0 else "\n"
        safe_admin = html.escape(callback.from_user.full_name or "Admin")
        text = (
            f"👑 <b>Admin Control Center · QineSystems</b>\n"
            f"<i>Live DB snapshot · {now_utc}</i>\n\n"
            f"{queue_line}{ticket_line}"
            f"Users     <code>{total_users} total · {paid_users} paid</code>\n"
            f"Content   <code>{total_questions} questions · {total_subjects} subjects · {total_categories} features</code>\n"
            f"Years     <code>1st: {yr_counts.get('1st Year', 0)} · 2nd: {yr_counts.get('2nd Year', 0)} · 3rd: {yr_counts.get('3rd Year', 0)}</code>\n"
            f"Admin     <code>{safe_admin} ({callback.from_user.id})</code>"
        )
        await safe_edit_or_send(callback, text, pro_admin_home_kb(pending_count, open_tickets))
    except Exception as exc:
        await callback.message.answer(f"❌ Control Center error: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")


@admin_pro_router.callback_query(F.data == "pro:queue")
async def cb_pro_queue(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return

    try:
        async with AsyncSessionLocal() as db:
            receipts = (await db.execute(
                select(PaymentReceipt).where(PaymentReceipt.status == "PENDING").limit(10)
            )).scalars().all()

        if not receipts:
            await callback.answer("No pending payment receipts in queue.", show_alert=True)
            return

        await callback.answer(f"Loading {len(receipts)} receipt(s)...")
        for r in receipts:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Approve", callback_data=f"adm_pay:approve:{r.id}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"adm_pay:reject:{r.id}"),
            ]])
            caption = (
                f"💳 <b>Receipt #{r.id}</b>\n"
                f"Student ID: <code>{r.student_id}</code>\n"
                f"Plan: <code>{html.escape(r.plan_code)}</code>"
            )
            raw_id = (r.receipt_file_id or "").strip()
            try:
                if raw_id.startswith("DOC:"):
                    await callback.bot.send_document(
                        callback.from_user.id, raw_id[4:], caption=caption, reply_markup=kb, parse_mode="HTML"
                    )
                else:
                    try:
                        await callback.bot.send_photo(
                            callback.from_user.id, raw_id, caption=caption, reply_markup=kb, parse_mode="HTML"
                        )
                    except Exception:
                        await callback.bot.send_document(
                            callback.from_user.id, raw_id, caption=caption, reply_markup=kb, parse_mode="HTML"
                        )
            except Exception as e:
                await callback.bot.send_message(
                    callback.from_user.id, f"{caption}\n\n⚠️ <i>Error: {html.escape(str(e))}</i>", reply_markup=kb, parse_mode="HTML"
                )
    except Exception as exc:
        await callback.answer()
        await callback.message.answer(f"❌ Queue error: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")


@admin_pro_router.callback_query(F.data == "pro:users")
async def cb_pro_users(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer()
    try:
        async with AsyncSessionLocal() as db:
            students = (await db.execute(
                select(Student).order_by(Student.telegram_id.desc()).limit(15)
            )).scalars().all()

        buttons = [
            [InlineKeyboardButton(
                text=f"👤 {s.full_name or s.telegram_id} [{s.plan_code}]",
                callback_data=f"crud_stu_view:{s.telegram_id}",
            )]
            for s in students
        ]
        buttons.append([InlineKeyboardButton(text="👥 All Students (CRUD)", callback_data="crud:students:0")])
        buttons.append([InlineKeyboardButton(text="⬅️ Back to Control Center", callback_data="adm:home_pro")])
        await safe_edit_or_send(
            callback,
            f"👥 <b>Registered Users (Latest {len(students)})</b>\nTap any user to inspect or update their plan:",
            InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    except Exception as exc:
        await callback.message.answer(f"❌ Users error: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")


@admin_pro_router.callback_query(F.data == "pro:referrals")
async def cb_pro_referrals(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer()
    try:
        async with AsyncSessionLocal() as db:
            all_students = (await db.execute(select(Student))).scalars().all()

        student_map = {s.telegram_id: s for s in all_students}
        invited_by_ref: dict[int, int] = {}
        approved_by_ref: dict[int, int] = {}

        for st in all_students:
            if st.referred_by_id:
                rid = st.referred_by_id
                invited_by_ref[rid] = invited_by_ref.get(rid, 0) + 1
                if st.plan_code in ("QINE_PLUS", "QINE_PRO") and (
                    not st.subscription_expires_at or st.subscription_expires_at > datetime.utcnow()
                ):
                    approved_by_ref[rid] = approved_by_ref.get(rid, 0) + 1

        total_users = len(all_students)
        total_invited = sum(invited_by_ref.values())
        total_approved = sum(approved_by_ref.values())
        total_rewards_etb = total_approved * REFERRAL_REWARD_ETB

        ranked = sorted(
            invited_by_ref.items(),
            key=lambda item: (approved_by_ref.get(item[0], 0), item[1]),
            reverse=True,
        )

        lines = [
            "🎁 <b>Referral Program &amp; Rewards Overview</b>\n",
            f"• Total Registered Users: <b>{total_users}</b>",
            f"• Total Invited via Link: <b>{total_invited}</b>",
            f"• Active / Rewarded Referrals: <b>{total_approved}</b>",
            f"• Reward Ledger ({REFERRAL_REWARD_ETB} ETB/active): <b>{total_rewards_etb} ETB</b>",
            "",
            "<b>🏆 Top Inviters</b>",
        ]

        if not ranked:
            lines.append("<i>No referrals recorded yet.</i>")
        else:
            for rid, inv_cnt in ranked[:10]:
                app_cnt = approved_by_ref.get(rid, 0)
                owner = student_map.get(rid)
                owner_name = html.escape((owner.full_name if owner and owner.full_name else f"ID {rid}")[:24])
                lines.append(
                    f"• <b>{owner_name}</b> (<code>{rid}</code>): "
                    f"<b>{inv_cnt}</b> invited · <b>{app_cnt}</b> active (<b>{app_cnt * REFERRAL_REWARD_ETB} ETB</b>)"
                )

        await safe_edit_or_send(
            callback,
            "\n".join(lines),
            back_pro_kb(extra_rows=[[InlineKeyboardButton(text="👥 View Users", callback_data="pro:users")]]),
        )
    except Exception as exc:
        await callback.message.answer(f"❌ Referrals error: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")


@admin_pro_router.callback_query(F.data == "pro:questions")
async def cb_pro_questions(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ Bulk Import Questions", callback_data="bulk:quizzes")],
        [InlineKeyboardButton(text="🗑 View / Delete Questions", callback_data="crud:quizzes:0")],
        [InlineKeyboardButton(text="📋 CSV Validator", callback_data="pro:csv_validate")],
        [InlineKeyboardButton(text="⬅️ Back to Control Center", callback_data="adm:home_pro")],
    ])
    await safe_edit_or_send(callback, "❓ <b>Question Bank Management</b>", kb)


@admin_pro_router.callback_query(F.data == "pro:subjects")
async def cb_pro_subjects(callback: CallbackQuery, state: FSMContext):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await state.clear()
    await callback.answer()

    async with AsyncSessionLocal() as db:
        courses = (await db.execute(select(Course).order_by(Course.year_level, Course.name))).scalars().all()

    yr_counts: dict[str, int] = {}
    for c in courses:
        yr = getattr(c, "year_level", None) or "1st Year"
        yr_counts[yr] = yr_counts.get(yr, 0) + 1

    summary_line = (
        f"1st Yr: <b>{yr_counts.get('1st Year', 0)}</b> · "
        f"2nd Yr: <b>{yr_counts.get('2nd Year', 0)}</b> · "
        f"3rd Yr: <b>{yr_counts.get('3rd Year', 0)}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add New Course (with Year)", callback_data="pro:add_course")],
        [
            InlineKeyboardButton(text="🔄 Assign / Cycle Course Year", callback_data="pro:year_assign:0"),
            InlineKeyboardButton(text="📖 Bulk Add Chapters", callback_data="bulk:chapters"),
        ],
        [
            InlineKeyboardButton(text="📚 Batch Drop PDFs", callback_data="bulk:pdfs"),
            InlineKeyboardButton(text="🔄 Toggle / Delete Subjects", callback_data="crud:courses:0"),
        ],
        [InlineKeyboardButton(text="⬅️ Back to Control Center", callback_data="adm:home_pro")],
    ])
    await safe_edit_or_send(
        callback,
        f"📚 <b>Subjects &amp; Year Management</b>\n"
        f"<i>{summary_line}</i>\n"
        f"<i>Note: COC Past Exams apply to 1st Year only.</i>",
        kb,
    )


@admin_pro_router.callback_query(F.data.startswith("pro:year_assign:"))
async def cb_pro_year_assign_list(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    page_s = callback.data.split(":")[2]
    page = int(page_s) if page_s.isdigit() else 0
    page_size = 8

    async with AsyncSessionLocal() as db:
        courses = (await db.execute(select(Course).order_by(Course.name))).scalars().all()

    await callback.answer()
    total_pages = max(1, (len(courses) + page_size - 1) // page_size)
    cur = max(0, min(page, total_pages - 1))
    chunk = courses[cur * page_size : (cur + 1) * page_size]

    buttons: list[list[InlineKeyboardButton]] = []
    for c in chunk:
        cur_yr = getattr(c, "year_level", None) or "1st Year"
        badge = YEAR_SHORT_BADGE.get(cur_yr, cur_yr)
        buttons.append([
            InlineKeyboardButton(
                text=f"[{badge}] {c.name[:24]} → Tap to Cycle",
                callback_data=f"pro:cycle_yr:{c.id}:{cur}",
            )
        ])

    nav: list[InlineKeyboardButton] = []
    if cur > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"pro:year_assign:{cur - 1}"))
    if cur < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"pro:year_assign:{cur + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Subjects", callback_data="pro:subjects")])

    await safe_edit_or_send(
        callback,
        f"🔄 <b>Assign / Cycle Course Year</b> (Page {cur + 1}/{total_pages})\n"
        f"Tap a course to cycle <code>1st Year → 2nd Year → 3rd Year</code>:",
        InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@admin_pro_router.callback_query(F.data.startswith("pro:cycle_yr:"))
async def cb_pro_cycle_course_year(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    parts = callback.data.split(":")
    course_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0

    order = ["1st Year", "2nd Year", "3rd Year"]
    async with AsyncSessionLocal() as db:
        course = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none()
        if not course:
            await callback.answer("Course not found.", show_alert=True)
            return
        cur_yr = getattr(course, "year_level", None) or "1st Year"
        next_yr = order[(order.index(cur_yr) + 1) % len(order)] if cur_yr in order else "1st Year"
        course.year_level = next_yr
        await db.commit()
        cname = course.name

    await callback.answer(f"{cname} → {next_yr}")
    callback.data = f"pro:year_assign:{page}"
    await cb_pro_year_assign_list(callback)


@admin_pro_router.callback_query(F.data == "pro:add_course")
async def cb_pro_add_course_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer()
    await state.set_state(ProAdminFSM.adding_course)
    await safe_edit_or_send(
        callback,
        "➕ <b>Add New Course</b>\n\n"
        "Step 1: Send the course name (for example: <code>Applied Mathematics I</code>).\n"
        "Step 2: Pick its academic year (<b>1st Year</b>, <b>2nd Year</b>, or <b>3rd Year</b>):",
        back_pro_kb("pro:subjects", "⬅️ Cancel"),
    )


@admin_pro_router.message(ProAdminFSM.adding_course, F.text & ~F.text.startswith("/"))
async def msg_pro_add_course_pick_year(message: Message, state: FSMContext):
    if not is_pro_admin(message.from_user.id):
        return
    course_name = " ".join((message.text or "").split()).strip()
    if not course_name:
        await message.answer("⚠️ Course name cannot be empty. Send a valid course name.")
        return
    if len(course_name) > 150:
        await message.answer("⚠️ Course name must be 150 characters or fewer.")
        return

    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(
                select(Course).where(func.lower(Course.name) == course_name.lower())
            )
        ).scalar_one_or_none()
        if existing:
            await state.clear()
            await message.answer(
                f"⚠️ Course <b>{html.escape(existing.name)}</b> already exists (ID: <code>{existing.id}</code>).",
                reply_markup=back_pro_kb(
                    "pro:subjects",
                    "⬅️ Back to Subjects",
                    extra_rows=[[InlineKeyboardButton(text="➕ Add Another Course", callback_data="pro:add_course")]],
                ),
                parse_mode="HTML",
            )
            return

    await state.update_data(pending_course_name=course_name)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🎓 1st Year", callback_data="pro:save_crs_yr:1"),
            InlineKeyboardButton(text="📘 2nd Year", callback_data="pro:save_crs_yr:2"),
            InlineKeyboardButton(text="📙 3rd Year", callback_data="pro:save_crs_yr:3"),
        ],
        [InlineKeyboardButton(text="⬅️ Cancel", callback_data="pro:subjects")],
    ])
    await message.answer(
        f"📚 <b>Course Name:</b> <b>{html.escape(course_name)}</b>\n\n"
        f"Select the academic year for this course:",
        reply_markup=kb,
        parse_mode="HTML",
    )


@admin_pro_router.callback_query(F.data.startswith("pro:save_crs_yr:"))
async def cb_pro_save_course_with_year(callback: CallbackQuery, state: FSMContext):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    yr_code = callback.data.split(":")[2]
    yr_map = {"1": "1st Year", "2": "2nd Year", "3": "3rd Year"}
    chosen_year = yr_map.get(yr_code, "1st Year")

    data = await state.get_data()
    course_name = (data.get("pending_course_name") or "").strip()
    await state.clear()

    if not course_name:
        await callback.answer("Session expired. Start Add Course again.", show_alert=True)
        return

    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(
                select(Course).where(func.lower(Course.name) == course_name.lower())
            )
        ).scalar_one_or_none()
        if existing:
            existing.year_level = chosen_year
            await db.commit()
            created_id = existing.id
            created_name = existing.name
        else:
            new_course = Course(name=course_name, year_level=chosen_year, is_active=True)
            db.add(new_course)
            await db.commit()
            await db.refresh(new_course)
            created_id = new_course.id
            created_name = new_course.name

    await callback.answer("Course saved!")
    await safe_edit_or_send(
        callback,
        f"✅ <b>Course Saved!</b>\n\n"
        f"• Name: <b>{html.escape(created_name)}</b>\n"
        f"• Year Level: <b>{html.escape(chosen_year)}</b>\n"
        f"• Course ID: <code>{created_id}</code>\n"
        f"• Status: 🟢 Active",
        back_pro_kb(
            "pro:subjects",
            "⬅️ Back to Subjects",
            extra_rows=[
                [
                    InlineKeyboardButton(text="➕ Add Another Course", callback_data="pro:add_course"),
                    InlineKeyboardButton(text="📖 Bulk Add Chapters", callback_data="bulk:chapters"),
                ]
            ],
        ),
    )


@admin_pro_router.callback_query(F.data == "pro:categories")
async def cb_pro_categories(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer()
    try:
        async with AsyncSessionLocal() as db:
            features = (await db.execute(select(FeatureConfig).order_by(FeatureConfig.sort_order))).scalars().all()

        buttons = [
            [InlineKeyboardButton(
                text=f"{f.title} — {'🟢 FREE' if f.is_free else '💎 PREMIUM'}",
                callback_data=f"pro_tog_cat:{f.key}",
            )]
            for f in features
        ]
        buttons.append([InlineKeyboardButton(text="⬅️ Back to Control Center", callback_data="adm:home_pro")])
        await safe_edit_or_send(
            callback,
            "📦 <b>Feature Categories (Free vs Premium)</b>\nTap any feature to toggle access tier:",
            InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    except Exception as exc:
        await callback.message.answer(f"❌ Categories error: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")


@admin_pro_router.callback_query(F.data.startswith("pro_tog_cat:"))
async def cb_pro_toggle_category(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer("Updated!")
    key = callback.data.split(":", 1)[1]
    async with AsyncSessionLocal() as db:
        feat = (await db.execute(select(FeatureConfig).where(FeatureConfig.key == key))).scalar_one_or_none()
        if feat:
            feat.is_free = not feat.is_free
            await db.commit()
    await cb_pro_categories(callback)


# =====================================================================
# SUPPORT TICKETS QUEUE
# =====================================================================
@admin_pro_router.callback_query(F.data == "pro:tickets")
async def cb_pro_open_tickets(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer()

    async with AsyncSessionLocal() as db:
        tickets = (await db.execute(
            select(SupportTicket)
            .where(SupportTicket.status == "OPEN")
            .order_by(SupportTicket.id.desc())
            .limit(8)
        )).scalars().all()

    if not tickets:
        await safe_edit_or_send(
            callback,
            "🎫 <b>Support Tickets</b>\n\n✅ No open support tickets right now.",
            back_pro_kb(),
        )
        return

    lines = [f"🎫 <b>Open Support Tickets ({len(tickets)} shown)</b>\n"]
    buttons: list[list[InlineKeyboardButton]] = []
    for t in tickets:
        preview = html.escape((t.message or "").strip()[:80])
        lines.append(f"• <b>#{t.id}</b> · Student <code>{t.student_id}</code>\n  <i>{preview}</i>")
        buttons.append([
            InlineKeyboardButton(text=f"✅ Resolve #{t.id}", callback_data=f"pro:tkt_resolve:{t.id}")
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Control Center", callback_data="adm:home_pro")])

    await safe_edit_or_send(callback, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


@admin_pro_router.callback_query(F.data.startswith("pro:tkt_resolve:"))
async def cb_pro_resolve_ticket(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    t_id = int(callback.data.split(":")[2])

    async with AsyncSessionLocal() as db:
        ticket = (await db.execute(select(SupportTicket).where(SupportTicket.id == t_id))).scalar_one_or_none()
        if not ticket:
            await callback.answer("Ticket not found.", show_alert=True)
            return
        ticket.status = "RESOLVED"
        await db.commit()
        stu_id = ticket.student_id

    try:
        await callback.bot.send_message(
            stu_id,
            f"✅ <b>Support Ticket #{t_id} Resolved</b>\nAn admin has reviewed and resolved your request.",
            parse_mode="HTML",
        )
    except Exception:
        pass

    await callback.answer(f"Ticket #{t_id} resolved.")
    await cb_pro_open_tickets(callback)


# =====================================================================
# PAYMENT MANAGEMENT HUB & PAYMENT ACCOUNTS
# =====================================================================
@admin_pro_router.callback_query(F.data == "pro:payments")
async def cb_payment_management_hub(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer()

    async with AsyncSessionLocal() as db:
        pending_n = (await db.execute(
            select(func.count(PaymentReceipt.id)).where(PaymentReceipt.status == "PENDING")
        )).scalar() or 0
        approved_n = (await db.execute(
            select(func.count(PaymentReceipt.id)).where(PaymentReceipt.status == "APPROVED")
        )).scalar() or 0
        rejected_n = (await db.execute(
            select(func.count(PaymentReceipt.id)).where(PaymentReceipt.status == "REJECTED")
        )).scalar() or 0
        active_pm_n = (await db.execute(
            select(func.count(PaymentMethodConfig.code)).where(PaymentMethodConfig.is_verified_active.is_(True))
        )).scalar() or 0

    text = (
        "💰 <b>Payment Management</b>\n\n"
        "Choose an option to manage payments:\n\n"
        f"• ⏳ Pending: <b>{pending_n}</b>\n"
        f"• ✅ Approved: <b>{approved_n}</b>\n"
        f"• ❌ Rejected: <b>{rejected_n}</b>\n"
        f"• 💳 Active Payment Methods: <b>{active_pm_n}</b>"
    )
    await safe_edit_or_send(callback, text, payment_hub_kb())


@admin_pro_router.callback_query(F.data == "pay:methods")
async def cb_pay_methods_list(callback: CallbackQuery, state: FSMContext):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await state.clear()
    await callback.answer()

    async with AsyncSessionLocal() as db:
        methods = (
            await db.execute(select(PaymentMethodConfig).order_by(PaymentMethodConfig.code))
        ).scalars().all()

    if not methods:
        await safe_edit_or_send(
            callback,
            "💳 <b>Payment Accounts</b>\n\nNo payment methods found in database.",
            back_pro_kb("pro:payments", "⬅️ Back to Payments"),
        )
        return

    lines = ["💳 <b>Payment Accounts &amp; Activation</b>\n"]
    buttons: list[list[InlineKeyboardButton]] = []
    for pm in methods:
        badge = "🟢 ACTIVE" if pm.is_verified_active else "🔴 INACTIVE"
        note_preview = (pm.instructions or "").strip()
        note_line = f"\n   Note: <i>{html.escape(note_preview[:45])}</i>" if note_preview else ""
        lines.append(
            f"{badge} <b>{html.escape(pm.provider_name)}</b> (<code>{html.escape(pm.code)}</code>)\n"
            f"   Holder: <code>{html.escape(pm.account_holder)}</code> · Acct: <code>{html.escape(pm.account_number)}</code>{note_line}"
        )
        buttons.append([
            InlineKeyboardButton(
                text=f"{'🔴 Disable' if pm.is_verified_active else '🟢 Enable'} {pm.code}",
                callback_data=f"pay:pm_tog:{pm.code}",
            ),
            InlineKeyboardButton(text="✏️ Holder", callback_data=f"pay:pm_edit:{pm.code}:holder"),
            InlineKeyboardButton(text="🔢 Acct", callback_data=f"pay:pm_edit:{pm.code}:number"),
            InlineKeyboardButton(text="📝 Note", callback_data=f"pay:pm_edit:{pm.code}:instructions"),
        ])

    buttons.append([InlineKeyboardButton(text="⬅️ Back to Payments", callback_data="pro:payments")])
    await safe_edit_or_send(callback, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


@admin_pro_router.callback_query(F.data.startswith("pay:pm_tog:"))
async def cb_pay_method_toggle(callback: CallbackQuery, state: FSMContext):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    code = callback.data.split(":", 2)[2]
    async with AsyncSessionLocal() as db:
        pm = (
            await db.execute(select(PaymentMethodConfig).where(PaymentMethodConfig.code == code))
        ).scalar_one_or_none()
        if not pm:
            await callback.answer("Payment method not found.", show_alert=True)
            return
        pm.is_verified_active = not bool(pm.is_verified_active)
        await db.commit()
        new_state = pm.is_verified_active

    await callback.answer(f"{code}: {'ACTIVE' if new_state else 'INACTIVE'}", show_alert=True)
    await cb_pay_methods_list(callback, state)


@admin_pro_router.callback_query(F.data.startswith("pay:pm_edit:"))
async def cb_pay_method_edit_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    _, _, code, field = callback.data.split(":", 3)

    async with AsyncSessionLocal() as db:
        pm = (
            await db.execute(select(PaymentMethodConfig).where(PaymentMethodConfig.code == code))
        ).scalar_one_or_none()
    if not pm:
        await callback.answer("Payment method not found.", show_alert=True)
        return

    await callback.answer()
    await state.set_state(ProAdminFSM.editing_pm_field)
    await state.update_data(pm_code=pm.code, pm_field=field)

    label_map = {
        "holder": ("Account Holder Name", pm.account_holder),
        "number": ("Account / Phone Number", pm.account_number),
        "instructions": ("Payment Instructions", pm.instructions or "—"),
    }
    label, current_val = label_map.get(field, ("Value", "—"))
    await safe_edit_or_send(
        callback,
        f"✏️ <b>Edit {label} · {html.escape(pm.code)}</b>\n"
        f"Current value: <code>{html.escape(str(current_val))}</code>\n\n"
        f"Send the new {label.lower()} below:",
        back_pro_kb("pay:methods", "⬅️ Cancel"),
    )


@admin_pro_router.message(ProAdminFSM.editing_pm_field, F.text & ~F.text.startswith("/"))
async def msg_pay_method_save_field(message: Message, state: FSMContext):
    if not is_pro_admin(message.from_user.id):
        return
    new_val = (message.text or "").strip()
    if not new_val:
        await message.answer("⚠️ Value cannot be empty. Send a valid value.")
        return

    data = await state.get_data()
    code = data.get("pm_code")
    field = data.get("pm_field")
    await state.clear()

    async with AsyncSessionLocal() as db:
        pm = (
            await db.execute(select(PaymentMethodConfig).where(PaymentMethodConfig.code == code))
        ).scalar_one_or_none()
        if not pm:
            await message.answer("❌ Payment method not found.", reply_markup=back_pro_kb("pay:methods", "⬅️ Back"))
            return
        if field == "holder":
            pm.account_holder = new_val
        elif field == "number":
            pm.account_number = new_val
        elif field == "instructions":
            pm.instructions = new_val
        await db.commit()

    await message.answer(
        f"✅ Updated <b>{html.escape(str(code))}</b> ({html.escape(str(field))}) to <code>{html.escape(new_val)}</code>.",
        reply_markup=back_pro_kb("pay:methods", "⬅️ Back to Payment Accounts"),
        parse_mode="HTML",
    )


@admin_pro_router.callback_query(F.data.startswith("pay:pending:"))
async def cb_pay_pending_list(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    page_s = callback.data.split(":")[2]
    page = int(page_s) if page_s.isdigit() else 0
    page_size = 5

    async with AsyncSessionLocal() as db:
        receipts = (await db.execute(
            select(PaymentReceipt, Student)
            .outerjoin(Student, PaymentReceipt.student_id == Student.telegram_id)
            .where(PaymentReceipt.status == "PENDING")
            .order_by(PaymentReceipt.id.desc())
        )).all()

    await callback.answer()
    if not receipts:
        await safe_edit_or_send(
            callback,
            "⏳ <b>Pending Payments</b>\n\nNo pending receipts waiting for review.",
            back_pro_kb("pro:payments", "⬅️ Back to Payments"),
        )
        return

    total_pages = max(1, (len(receipts) + page_size - 1) // page_size)
    cur = max(0, min(page, total_pages - 1))
    chunk = receipts[cur * page_size : (cur + 1) * page_size]

    lines = [f"⏳ <b>Pending Payments</b> (Page {cur + 1}/{total_pages}, Total: {len(receipts)})\n"]
    buttons: list[list[InlineKeyboardButton]] = []

    for r, st in chunk:
        st_name = html.escape((st.full_name if st and st.full_name else f"ID {r.student_id}")[:20])
        created = r.created_at.strftime("%m-%d %H:%M") if getattr(r, "created_at", None) else "—"
        lines.append(f"• <b>#{r.id}</b> · {st_name} · <code>{html.escape(r.plan_code)}</code> · <i>{created}</i>")
        buttons.append([
            InlineKeyboardButton(text=f"✅ #{r.id}", callback_data=f"pay:act:approve:{r.id}:{cur}"),
            InlineKeyboardButton(text=f"❌ #{r.id}", callback_data=f"pay:act:reject:{r.id}:{cur}"),
            InlineKeyboardButton(text=f"📎 #{r.id}", callback_data=f"pay:view:{r.id}"),
        ])

    nav: list[InlineKeyboardButton] = []
    if cur > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"pay:pending:{cur - 1}"))
    if cur < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"pay:pending:{cur + 1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="⬅️ Back to Payments", callback_data="pro:payments")])
    await safe_edit_or_send(callback, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


@admin_pro_router.callback_query(F.data.startswith("pay:view:"))
async def cb_pay_view_receipt(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    r_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as db:
        r = (await db.execute(select(PaymentReceipt).where(PaymentReceipt.id == r_id))).scalar_one_or_none()
    if not r:
        await callback.answer("Receipt not found.", show_alert=True)
        return

    await callback.answer("Sending receipt file...")
    caption = (
        f"💳 <b>Receipt #{r.id}</b>\n"
        f"• Student ID: <code>{r.student_id}</code>\n"
        f"• Plan: <code>{html.escape(r.plan_code)}</code>\n"
        f"• Status: <b>{html.escape(r.status)}</b>"
    )
    raw_id = (r.receipt_file_id or "").strip()
    try:
        if raw_id.startswith("DOC:"):
            await callback.bot.send_document(callback.from_user.id, raw_id[4:], caption=caption, parse_mode="HTML")
        else:
            try:
                await callback.bot.send_photo(callback.from_user.id, raw_id, caption=caption, parse_mode="HTML")
            except Exception:
                await callback.bot.send_document(callback.from_user.id, raw_id, caption=caption, parse_mode="HTML")
    except Exception as e:
        await callback.bot.send_message(callback.from_user.id, f"{caption}\n\n⚠️ <i>Could not load file: {html.escape(str(e))}</i>", parse_mode="HTML")


@admin_pro_router.callback_query(F.data.startswith("pay:act:"))
async def cb_pay_single_action(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    _, _, action, r_id_s, page_s = callback.data.split(":")
    r_id = int(r_id_s)
    page = int(page_s) if page_s.isdigit() else 0

    async with AsyncSessionLocal() as db:
        r = (await db.execute(select(PaymentReceipt).where(PaymentReceipt.id == r_id))).scalar_one_or_none()
        if not r:
            await callback.answer("Receipt not found.", show_alert=True)
            return

        if action == "approve":
            r.status = "APPROVED"
            st = (await db.execute(select(Student).where(Student.telegram_id == r.student_id))).scalar_one_or_none()
            if st:
                st.plan_code = r.plan_code
                days = PLAN_DURATIONS_DAYS.get(r.plan_code, 30)
                st.subscription_expires_at = datetime.utcnow() + timedelta(days=days)
            await db.commit()
            try:
                await callback.bot.send_message(
                    r.student_id,
                    f"✅ <b>Payment Approved!</b>\nYour <code>{html.escape(r.plan_code)}</code> subscription is now active.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            await callback.answer(f"Receipt #{r.id} approved.", show_alert=True)
        else:
            r.status = "REJECTED"
            await db.commit()
            try:
                await callback.bot.send_message(
                    r.student_id,
                    f"❌ <b>Payment Receipt #{r.id} Rejected</b>\nPlease contact support if you need help.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            await callback.answer(f"Receipt #{r.id} rejected.", show_alert=True)

    callback.data = f"pay:pending:{page}"
    await cb_pay_pending_list(callback)


@admin_pro_router.callback_query(F.data.startswith("pay:all:"))
async def cb_pay_all_list(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    page_s = callback.data.split(":")[2]
    page = int(page_s) if page_s.isdigit() else 0
    page_size = 8

    async with AsyncSessionLocal() as db:
        receipts = (await db.execute(
            select(PaymentReceipt).order_by(PaymentReceipt.id.desc())
        )).scalars().all()

    await callback.answer()
    if not receipts:
        await safe_edit_or_send(
            callback,
            "📋 <b>All Payments</b>\n\nNo payment receipts recorded yet.",
            back_pro_kb("pro:payments", "⬅️ Back to Payments"),
        )
        return

    total_pages = max(1, (len(receipts) + page_size - 1) // page_size)
    cur = max(0, min(page, total_pages - 1))
    chunk = receipts[cur * page_size : (cur + 1) * page_size]

    badge = {"APPROVED": "✅", "PENDING": "⏳", "REJECTED": "❌"}
    lines = [f"📋 <b>All Payments</b> (Page {cur + 1}/{total_pages}, Total: {len(receipts)})\n"]
    for r in chunk:
        b = badge.get(r.status, "•")
        lines.append(f"{b} <b>#{r.id}</b> · Student <code>{r.student_id}</code> · <code>{html.escape(r.plan_code)}</code> · <b>{html.escape(r.status)}</b>")

    rows: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if cur > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"pay:all:{cur - 1}"))
    if cur < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"pay:all:{cur + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Back to Payments", callback_data="pro:payments")])

    await safe_edit_or_send(callback, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


@admin_pro_router.callback_query(F.data == "pay:stats")
async def cb_pay_stats(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer()

    async with AsyncSessionLocal() as db:
        all_r = (await db.execute(select(PaymentReceipt))).scalars().all()

    approved = [r for r in all_r if r.status == "APPROVED"]
    pending = [r for r in all_r if r.status == "PENDING"]
    rejected = [r for r in all_r if r.status == "REJECTED"]

    plus_approved = sum(1 for r in approved if r.plan_code == "QINE_PLUS")
    pro_approved = sum(1 for r in approved if r.plan_code == "QINE_PRO")

    text = (
        "📈 <b>Payment &amp; Revenue Stats (Honest Ledger)</b>\n\n"
        f"• Total Receipts Submitted: <b>{len(all_r)}</b>\n"
        f"• Approved Receipts: <b>{len(approved)}</b> (Plus: <b>{plus_approved}</b> · Pro: <b>{pro_approved}</b>)\n"
        f"• Pending Review: <b>{len(pending)}</b>\n"
        f"• Rejected Receipts: <b>{len(rejected)}</b>\n\n"
        "<i>Only verified database receipt counts are shown; no estimated or unverified revenue figures are added.</i>"
    )
    await safe_edit_or_send(callback, text, back_pro_kb("pro:payments", "⬅️ Back to Payments"))


@admin_pro_router.callback_query(F.data == "pay:approve_all_confirm")
async def cb_pay_approve_all_confirm(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    async with AsyncSessionLocal() as db:
        pending_n = (await db.execute(
            select(func.count(PaymentReceipt.id)).where(PaymentReceipt.status == "PENDING")
        )).scalar() or 0

    await callback.answer()
    if pending_n == 0:
        await safe_edit_or_send(
            callback,
            "✅ <b>Approve All</b>\n\nThere are currently <b>0</b> pending receipts to approve.",
            back_pro_kb("pro:payments", "⬅️ Back to Payments"),
        )
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"✅ Confirm Approve ({pending_n})", callback_data="pay:approve_all_exec"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="pro:payments"),
        ]
    ])
    await safe_edit_or_send(
        callback,
        f"⚠️ <b>Confirm Bulk Approval</b>\n\n"
        f"This will mark <b>{pending_n}</b> pending receipt(s) as <b>APPROVED</b> and activate their subscriptions.",
        kb,
    )


@admin_pro_router.callback_query(F.data == "pay:approve_all_exec")
async def cb_pay_approve_all_exec(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return

    approved_count = 0
    async with AsyncSessionLocal() as db:
        pending_list = (await db.execute(
            select(PaymentReceipt).where(PaymentReceipt.status == "PENDING")
        )).scalars().all()

        for r in pending_list:
            r.status = "APPROVED"
            st = (await db.execute(select(Student).where(Student.telegram_id == r.student_id))).scalar_one_or_none()
            if st:
                st.plan_code = r.plan_code
                days = PLAN_DURATIONS_DAYS.get(r.plan_code, 30)
                st.subscription_expires_at = datetime.utcnow() + timedelta(days=days)
            approved_count += 1

        await db.commit()

    await callback.answer(f"Approved {approved_count} receipt(s).", show_alert=True)
    await safe_edit_or_send(
        callback,
        f"✅ <b>Bulk Approval Complete</b>\n\nApproved <b>{approved_count}</b> pending receipt(s).",
        back_pro_kb("pro:payments", "⬅️ Back to Payments"),
    )


# =====================================================================
# DASHBOARD, ANALYTICS & CONTENT HEALTH
# =====================================================================
@admin_pro_router.callback_query(F.data == "pro:dashboard")
async def cb_pro_dashboard(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer()
    try:
        async with AsyncSessionLocal() as db:
            u_total = (await db.execute(select(func.count(Student.telegram_id)))).scalar() or 0
            u_free = (await db.execute(select(func.count(Student.telegram_id)).where(Student.plan_code == "FREE"))).scalar() or 0
            u_paid = max(0, u_total - u_free)

            courses = (await db.execute(select(Course))).scalars().all()
            chp_cnt = (await db.execute(select(func.count(Chapter.id)))).scalar() or 0
            res_cnt = (await db.execute(select(func.count(LearningResource.id)))).scalar() or 0
            qz_cnt = (await db.execute(select(func.count(QuizQuestion.id)))).scalar() or 0
            fc_cnt = (await db.execute(select(func.count(Flashcard.id)))).scalar() or 0

            pend_pay = (await db.execute(select(func.count(PaymentReceipt.id)).where(PaymentReceipt.status == "PENDING"))).scalar() or 0
            open_tkt = (await db.execute(select(func.count(SupportTicket.id)).where(SupportTicket.status == "OPEN"))).scalar() or 0

        yr_counts: dict[str, int] = {}
        for c in courses:
            yr = getattr(c, "year_level", None) or "1st Year"
            yr_counts[yr] = yr_counts.get(yr, 0) + 1

        text = (
            "📊 <b>Operational Dashboard</b>\n\n"
            "<b>👥 Students</b>\n"
            f"• Total Registered: <b>{u_total}</b>\n"
            f"• Free Plan: <b>{u_free}</b> · Paid Plan: <b>{u_paid}</b>\n\n"
            "<b>📚 Study Catalogue</b>\n"
            f"• Courses: <b>{len(courses)}</b> (1st: <b>{yr_counts.get('1st Year', 0)}</b> · 2nd: <b>{yr_counts.get('2nd Year', 0)}</b> · 3rd: <b>{yr_counts.get('3rd Year', 0)}</b>)\n"
            f"• Chapters: <b>{chp_cnt}</b> · Resources/PDFs: <b>{res_cnt}</b>\n"
            f"• Quiz Questions: <b>{qz_cnt}</b> · Flashcards: <b>{fc_cnt}</b>\n\n"
            "<b>🔔 Action Queue</b>\n"
            f"• Pending Payment Receipts: <b>{pend_pay}</b>\n"
            f"• Open Support Tickets: <b>{open_tkt}</b>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="💰 Payments", callback_data="pro:payments"),
                InlineKeyboardButton(text="🩺 Content Health", callback_data="pro:health"),
            ],
            [InlineKeyboardButton(text="⬅️ Back to Control Center", callback_data="adm:home_pro")],
        ])
        await safe_edit_or_send(callback, text, kb)
    except Exception as exc:
        await callback.message.answer(f"❌ Dashboard error: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")


@admin_pro_router.callback_query(F.data == "pro:analytics")
async def cb_pro_analytics(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer()
    try:
        async with AsyncSessionLocal() as db:
            free_u = (await db.execute(select(func.count(Student.telegram_id)).where(Student.plan_code == "FREE"))).scalar() or 0
            plus_u = (await db.execute(select(func.count(Student.telegram_id)).where(Student.plan_code == "QINE_PLUS"))).scalar() or 0
            pro_u = (await db.execute(select(func.count(Student.telegram_id)).where(Student.plan_code == "QINE_PRO"))).scalar() or 0
            ref_u = (await db.execute(select(func.count(Student.telegram_id)).where(Student.referred_by_id.isnot(None)))).scalar() or 0
            attempts = (await db.execute(select(QuizAttempt))).scalars().all()
            dc_completions = (await db.execute(select(func.count(DailyChallengeCompletion.id)))).scalar() or 0

        total_u = free_u + plus_u + pro_u
        paid_u = plus_u + pro_u
        conv_pct = (paid_u / total_u * 100.0) if total_u > 0 else 0.0

        valid_attempts = [a for a in attempts if (a.total_questions or 0) > 0]
        if valid_attempts:
            avg_acc = sum((a.score / a.total_questions * 100.0) for a in valid_attempts) / len(valid_attempts)
            quiz_line = f"• Recorded Quiz Attempts: <b>{len(valid_attempts)}</b> · Avg Accuracy: <b>{avg_acc:.1f}%</b>"
        else:
            quiz_line = "• Recorded Quiz Attempts: <b>0</b> · Avg Accuracy: <b>N/A (no attempts yet)</b>"

        text = (
            "📈 <b>Analytics &amp; Study Activity</b>\n\n"
            "<b>💎 Plan Distribution</b>\n"
            f"• Free: <b>{free_u}</b> · Plus: <b>{plus_u}</b> · Pro: <b>{pro_u}</b>\n"
            f"• Paid Share: <b>{conv_pct:.1f}%</b> ({paid_u}/{total_u})\n\n"
            "<b>🌱 Growth &amp; Learning</b>\n"
            f"• Referred Users: <b>{ref_u}</b>\n"
            f"• Daily Challenge Completions: <b>{dc_completions}</b>\n"
            f"{quiz_line}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🎁 Referrals", callback_data="pro:referrals"),
                InlineKeyboardButton(text="💰 Revenue Stats", callback_data="pay:stats"),
            ],
            [InlineKeyboardButton(text="⬅️ Back to Control Center", callback_data="adm:home_pro")],
        ])
        await safe_edit_or_send(callback, text, kb)
    except Exception as exc:
        await callback.message.answer(f"❌ Analytics error: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")


@admin_pro_router.callback_query(F.data == "pro:health")
async def cb_pro_content_health(callback: CallbackQuery):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer()
    try:
        async with AsyncSessionLocal() as db:
            courses = (await db.execute(select(Course).order_by(Course.name))).scalars().all()
            chapters = (await db.execute(select(Chapter))).scalars().all()
            quizzes = (await db.execute(select(QuizQuestion))).scalars().all()
            resources = (await db.execute(select(LearningResource))).scalars().all()

        chapters_by_course: dict[int, list[Chapter]] = {}
        for ch in chapters:
            chapters_by_course.setdefault(ch.course_id, []).append(ch)

        quizzes_by_chapter: dict[int, int] = {}
        for q in quizzes:
            quizzes_by_chapter[q.chapter_id] = quizzes_by_chapter.get(q.chapter_id, 0) + 1

        courses_no_chapters = [c.name for c in courses if not chapters_by_course.get(c.id)]
        chapters_no_quizzes = sum(1 for ch in chapters if quizzes_by_chapter.get(ch.id, 0) == 0)
        unpublished_res = sum(1 for r in resources if getattr(r, "is_published", True) is False)

        preview_empty_courses = ", ".join(html.escape(n) for n in courses_no_chapters[:6])
        if len(courses_no_chapters) > 6:
            preview_empty_courses += f" (+{len(courses_no_chapters) - 6} more)"

        text = (
            "🩺 <b>Content Health Audit</b>\n\n"
            f"• Courses with 0 Chapters: <b>{len(courses_no_chapters)}</b>"
            + (f"\n  <i>{preview_empty_courses}</i>" if courses_no_chapters else "")
            + f"\n• Chapters with 0 Quizzes: <b>{chapters_no_quizzes}</b>\n"
            f"• Unpublished Resources: <b>{unpublished_res}</b>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📖 Manage Chapters", callback_data="crud:chapters:0"),
                InlineKeyboardButton(text="📄 Manage Resources", callback_data="crud:resources"),
            ],
            [InlineKeyboardButton(text="⬅️ Back to Control Center", callback_data="adm:home_pro")],
        ])
        await safe_edit_or_send(callback, text, kb)
    except Exception as exc:
        await callback.message.answer(f"❌ Health check error: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")


# =====================================================================
# CSV VALIDATOR & BROADCAST (WITH CONFIRMATION GUARD)
# =====================================================================
@admin_pro_router.callback_query(F.data == "pro:csv_validate")
async def cb_pro_csv_validate(callback: CallbackQuery, state: FSMContext):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer()
    await state.set_state(ProAdminFSM.validating_csv)
    await safe_edit_or_send(
        callback,
        "📋 <b>CSV Validator</b>\nUpload your <code>.csv</code> file to check columns and row integrity.",
        back_pro_kb(),
    )


@admin_pro_router.message(ProAdminFSM.validating_csv, F.document)
async def msg_pro_validate_csv(message: Message, state: FSMContext):
    buf = io.BytesIO()
    await message.bot.download(message.document, destination=buf)
    try:
        raw_text = buf.getvalue().decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(raw_text))
        headers = [h.strip() for h in (reader.fieldnames or []) if h]
        rows = list(reader)
        empty_rows = sum(
            1 for r in rows
            if not any((v or "").strip() for v in r.values() if isinstance(v, str))
        )
        valid_rows = len(rows) - empty_rows
        await message.answer(
            f"✅ <b>CSV Validated!</b>\n"
            f"• Columns ({len(headers)}): <code>{html.escape(', '.join(headers)) if headers else 'None'}</code>\n"
            f"• Valid Rows: <b>{valid_rows}</b> · Blank Rows: <b>{empty_rows}</b>",
            reply_markup=back_pro_kb(),
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(f"❌ Error: <code>{html.escape(str(e))}</code>", reply_markup=back_pro_kb(), parse_mode="HTML")
    await state.clear()


@admin_pro_router.callback_query(F.data == "pro:broadcast")
async def cb_pro_broadcast(callback: CallbackQuery, state: FSMContext):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer()
    await state.set_state(ProAdminFSM.broadcasting)
    await safe_edit_or_send(
        callback,
        "📢 <b>Broadcast</b>\nSend the announcement text to preview before delivering to students:",
        back_pro_kb(),
    )


@admin_pro_router.message(ProAdminFSM.broadcasting)
async def msg_pro_broadcast_preview(message: Message, state: FSMContext):
    if not is_pro_admin(message.from_user.id):
        return
    text = (message.text or message.caption or "").strip()
    if not text:
        await message.answer("Please send non-empty text for the announcement.", reply_markup=back_pro_kb(), parse_mode="HTML")
        return

    async with AsyncSessionLocal() as db:
        total_students = (await db.execute(select(func.count(Student.telegram_id)))).scalar() or 0

    await state.update_data(broadcast_text=text)
    await state.set_state(ProAdminFSM.confirming_broadcast)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"🚀 Send to {total_students} Users", callback_data="pro:bcast:send"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="adm:home_pro"),
        ]
    ])
    await message.answer(
        f"📢 <b>Broadcast Preview</b>\n"
        f"• Recipients: <b>{total_students}</b> registered student(s)\n\n"
        f"<blockquote>{html.escape(text)}</blockquote>",
        reply_markup=kb,
        parse_mode="HTML",
    )


@admin_pro_router.callback_query(F.data == "pro:bcast:send")
async def cb_pro_broadcast_send(callback: CallbackQuery, state: FSMContext):
    if not is_pro_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return

    data = await state.get_data()
    text = str(data.get("broadcast_text") or "").strip()
    await state.clear()
    await callback.answer("Sending broadcast...")

    if not text:
        await safe_edit_or_send(callback, "⚠️ No broadcast text found.", back_pro_kb())
        return

    async with AsyncSessionLocal() as db:
        student_ids = (await db.execute(select(Student.telegram_id))).scalars().all()

    safe_msg = html.escape(text)
    sent, failed = 0, 0
    for uid in student_ids:
        try:
            await callback.bot.send_message(uid, f"📢 <b>Announcement</b>\n\n{safe_msg}", parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1

    await safe_edit_or_send(
        callback,
        f"✅ <b>Broadcast Finished</b>\n• Delivered: <b>{sent}</b>\n• Failed: <b>{failed}</b>",
        back_pro_kb(),
    )