import os
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func

from database import AsyncSessionLocal
from models import (
    Course, Chapter, FeatureConfig, LearningResource,
    QuizQuestion, Flashcard, Student, PaymentMethodConfig,
    PaymentReceipt, SupportTicket
)

admin_router = Router()


def get_admin_ids() -> list[int]:
    return [int(x) for x in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",") if x.strip().isdigit()]


def is_admin(user_id: int) -> bool:
    return user_id in get_admin_ids()


# --- Admin FSM States ---
class AdminChapterFSM(StatesGroup):
    choosing_course = State()
    entering_number_and_title = State()


class AdminResourceFSM(StatesGroup):
    choosing_feature = State()
    choosing_course = State()
    choosing_chapter = State()
    entering_title = State()
    uploading_content = State()


class AdminQuizFSM(StatesGroup):
    choosing_course = State()
    choosing_chapter = State()
    entering_question_data = State()


class AdminFlashcardFSM(StatesGroup):
    choosing_course = State()
    choosing_chapter = State()
    entering_card_data = State()


class AdminPaymentMethodFSM(StatesGroup):
    entering_details = State()


class AdminTicketReplyFSM(StatesGroup):
    entering_reply = State()


def admin_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Approve Receipts", callback_data="adm:pending_receipts"),
            InlineKeyboardButton(text="👥 Users", callback_data="crud:students"),
        ],
        [
            InlineKeyboardButton(text="🎁 Referrals", callback_data="nav:referral"),
            InlineKeyboardButton(text="❓ Questions", callback_data="crud:quizzes"),
        ],
        [
            InlineKeyboardButton(text="📚 Subjects", callback_data="crud:courses"),
            InlineKeyboardButton(text="📦 Feature Access", callback_data="adm:features"),
        ],
        [
            InlineKeyboardButton(text="📊 Dashboard", callback_data="adm:dashboard"),
            InlineKeyboardButton(text="📈 Analytics", callback_data="adm:analytics"),
        ],
        [
            InlineKeyboardButton(text="📢 Broadcast", callback_data="adm:broadcast"),
            InlineKeyboardButton(text="📋 CSV Validator", callback_data="adm:csv_validate"),
        ],
        [
            InlineKeyboardButton(text="🛠 Full CRUD Manager", callback_data="adm:crud_hub"),
            InlineKeyboardButton(text="🚀 Bulk Import", callback_data="adm:bulk_hub"),
        ],
        [
            InlineKeyboardButton(text="🏠 Student Main Menu", callback_data="nav:main"),
        ],
    ])


# --- 1. Main /admin Dashboard ---
@admin_router.message(Command("admin"))
async def cmd_admin_panel(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer(
            f"⛔ <b>Access Denied.</b>\nYour Telegram ID is <code>{message.from_user.id}</code>.\n"
            f"Add this ID to <code>ADMIN_TELEGRAM_IDS</code> in your <code>.env</code> file and restart <code>python main.py</code>.",
            parse_mode="HTML",
        )
        return

    await state.clear()
    async with AsyncSessionLocal() as db:
        pending_count = (await db.execute(select(func.count(PaymentReceipt.id)).where(PaymentReceipt.status == "PENDING"))).scalar() or 0
        open_tickets = (await db.execute(select(func.count(SupportTicket.id)).where(SupportTicket.status == "OPEN"))).scalar() or 0
        total_students = (await db.execute(select(func.count(Student.telegram_id)))).scalar() or 0
        total_chapters = (await db.execute(select(func.count(Chapter.id)))).scalar() or 0
        total_resources = (await db.execute(select(func.count(LearningResource.id)))).scalar() or 0

    await message.answer(
        f"🛠 <b>QineSystems In-Telegram Admin Panel</b>\n\n"
        f"👥 <b>Students:</b> {total_students} · 📖 <b>Chapters:</b> {total_chapters} · 📄 <b>Resources:</b> {total_resources}\n"
        f"💳 <b>Pending Receipts:</b> {pending_count} · 🆘 <b>Open Tickets:</b> {open_tickets}\n\n"
        f"Select an administrative action below:",
        reply_markup=admin_home_kb(),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data == "adm:home")
async def cb_admin_home(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    try:
        from admin_dashboard_pro import cb_pro_admin_home
        await cb_pro_admin_home(callback, state)
    except Exception:
        await state.clear()
        await callback.answer()
        await callback.message.edit_text(
            "🛠 <b>QineSystems Admin Panel</b>\nSelect an action below:",
            reply_markup=admin_home_kb(),
            parse_mode="HTML",
        )


# --- 2. Add Chapter to a Course ---
@admin_router.callback_query(F.data == "adm:add_chapter")
async def cb_adm_add_chapter(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    async with AsyncSessionLocal() as db:
        courses = (await db.execute(select(Course).order_by(Course.name))).scalars().all()

    rows = []
    for i in range(0, len(courses), 2):
        row = [InlineKeyboardButton(text=courses[i].name, callback_data=f"adm_ch_crs:{courses[i].id}")]
        if i + 1 < len(courses):
            row.append(InlineKeyboardButton(text=courses[i + 1].name, callback_data=f"adm_ch_crs:{courses[i + 1].id}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Back to Admin", callback_data="adm:home")])

    await state.set_state(AdminChapterFSM.choosing_course)
    await callback.message.edit_text(
        "➕ <b>Add Chapter</b> → Select a Course:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


@admin_router.callback_query(AdminChapterFSM.choosing_course, F.data.startswith("adm_ch_crs:"))
async def cb_adm_chapter_course_chosen(callback: CallbackQuery, state: FSMContext):
    course_id = int(callback.data.split(":")[1])
    await state.update_data(course_id=course_id)
    await state.set_state(AdminChapterFSM.entering_number_and_title)
    await callback.message.edit_text(
        "✍️ <b>Send the Chapter Number</b> (e.g., <code>1</code>, <code>2</code>, <code>3</code>):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Cancel", callback_data="adm:home")]]),
        parse_mode="HTML",
    )


@admin_router.message(AdminChapterFSM.entering_number_and_title)
async def msg_adm_save_chapter(message: Message, state: FSMContext):
    num_str = (message.text or "").strip()
    if not num_str.isdigit():
        await message.answer("⚠️ Please send a valid chapter number (e.g., <code>1</code>, <code>2</code>, <code>3</code>).", parse_mode="HTML")
        return

    ch_num = int(num_str)
    auto_title = f"Chapter {ch_num}"
    data = await state.get_data()

    async with AsyncSessionLocal() as db:
        ch = Chapter(course_id=data["course_id"], chapter_number=ch_num, title=auto_title, is_published=True)
        db.add(ch)
        await db.commit()
        await db.refresh(ch)

    await state.clear()
    await message.answer(
        f"✅ <b>{auto_title}</b> created and published successfully!",
        reply_markup=admin_home_kb(),
        parse_mode="HTML",
    )


# --- 3. Upload Resource ---
@admin_router.callback_query(F.data == "adm:add_resource")
async def cb_adm_add_resource(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    resource_features = [
        ("📚 Modules (Free)", "modules"),
        ("📝 Mid-Exams (Free)", "mid_exams"),
        ("🗒 Study Notes (Free)", "notes"),
        ("🎓 Final Exams (Premium)", "final_exams"),
        ("🎯 Exam Trainer (Premium)", "exam_trainer"),
        ("✍️ Practice Questions (Premium)", "practice_questions"),
        ("📂 Study Resources", "more_resources"),
    ]
    buttons = [[InlineKeyboardButton(text=label, callback_data=f"adm_res_feat:{key}")] for label, key in resource_features]
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Admin", callback_data="adm:home")])

    await state.set_state(AdminResourceFSM.choosing_feature)
    await callback.message.edit_text(
        "📤 <b>Upload Learning Resource</b> → Select target feature:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@admin_router.callback_query(AdminResourceFSM.choosing_feature, F.data.startswith("adm_res_feat:"))
async def cb_adm_res_feature(callback: CallbackQuery, state: FSMContext):
    feat_key = callback.data.split(":")[1]
    await state.update_data(feature_key=feat_key)

    async with AsyncSessionLocal() as db:
        stmt = select(Course).join(Chapter, Chapter.course_id == Course.id).distinct().order_by(Course.name)
        courses = (await db.execute(stmt)).scalars().all()

    if not courses:
        await callback.message.edit_text(
            "⚠️ No courses have chapters yet! First add a chapter via <b>Bulk Import</b> or <b>Add Chapter</b>.",
            reply_markup=admin_home_kb(),
            parse_mode="HTML",
        )
        await state.clear()
        return

    buttons = [[InlineKeyboardButton(text=c.name, callback_data=f"adm_res_crs:{c.id}")] for c in courses]
    buttons.append([InlineKeyboardButton(text="⬅️ Cancel", callback_data="adm:home")])
    await state.set_state(AdminResourceFSM.choosing_course)
    await callback.message.edit_text(
        "📘 Select the <b>Course</b>:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@admin_router.callback_query(AdminResourceFSM.choosing_course, F.data.startswith("adm_res_crs:"))
async def cb_adm_res_course(callback: CallbackQuery, state: FSMContext):
    course_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as db:
        chapters = (await db.execute(
            select(Chapter).where(Chapter.course_id == course_id).order_by(Chapter.chapter_number)
        )).scalars().all()

    buttons = [
        [InlineKeyboardButton(text=f"Ch {ch.chapter_number}: {ch.title}", callback_data=f"adm_res_chp:{ch.id}")]
        for ch in chapters
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Cancel", callback_data="adm:home")])
    await state.set_state(AdminResourceFSM.choosing_chapter)
    await callback.message.edit_text(
        "📖 Select the <b>Chapter</b>:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@admin_router.callback_query(AdminResourceFSM.choosing_chapter, F.data.startswith("adm_res_chp:"))
async def cb_adm_res_chapter(callback: CallbackQuery, state: FSMContext):
    chapter_id = int(callback.data.split(":")[1])
    await state.update_data(chapter_id=chapter_id)
    await state.set_state(AdminResourceFSM.entering_title)
    await callback.message.edit_text(
        "✍️ Send the <b>Resource Title</b>:",
        parse_mode="HTML",
    )


@admin_router.message(AdminResourceFSM.entering_title)
async def msg_adm_res_title(message: Message, state: FSMContext):
    await state.update_data(title=(message.text or "").strip() or "Resource")
    await state.set_state(AdminResourceFSM.uploading_content)
    await message.answer(
        "📎 Now <b>upload the PDF/Document file</b> OR <b>send the text study note</b> directly:",
        parse_mode="HTML",
    )


@admin_router.message(AdminResourceFSM.uploading_content)
async def msg_adm_res_content(message: Message, state: FSMContext):
    data = await state.get_data()
    if message.document:
        content_type = "telegram_file"
        file_id = message.document.file_id
        text_content = None
    else:
        content_type = "text"
        file_id = None
        text_content = message.text

    async with AsyncSessionLocal() as db:
        res = LearningResource(
            chapter_id=data["chapter_id"],
            feature_key=data["feature_key"],
            title=data["title"],
            content_type=content_type,
            telegram_file_id=file_id,
            text_content=text_content,
            is_published=True,
        )
        db.add(res)
        await db.commit()

    await state.clear()
    await message.answer(
        f"✅ <b>Resource Published!</b>\n"
        f"• <b>Title:</b> {data['title']}\n"
        f"• <b>Feature:</b> <code>{data['feature_key']}</code>",
        reply_markup=admin_home_kb(),
        parse_mode="HTML",
    )


# --- 4. Add Timed Quiz Question ---
@admin_router.callback_query(F.data == "adm:add_quiz")
async def cb_adm_add_quiz(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    async with AsyncSessionLocal() as db:
        stmt = select(Course).join(Chapter, Chapter.course_id == Course.id).distinct().order_by(Course.name)
        courses = (await db.execute(stmt)).scalars().all()

    if not courses:
        await callback.message.edit_text("⚠️ Create a chapter first.", reply_markup=admin_home_kb(), parse_mode="HTML")
        return

    buttons = [[InlineKeyboardButton(text=c.name, callback_data=f"adm_qz_crs:{c.id}")] for c in courses]
    buttons.append([InlineKeyboardButton(text="⬅️ Cancel", callback_data="adm:home")])
    await state.set_state(AdminQuizFSM.choosing_course)
    await callback.message.edit_text("⏱ <b>Add Quiz Question</b> → Select Course:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


@admin_router.callback_query(AdminQuizFSM.choosing_course, F.data.startswith("adm_qz_crs:"))
async def cb_adm_quiz_course(callback: CallbackQuery, state: FSMContext):
    course_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as db:
        chapters = (await db.execute(select(Chapter).where(Chapter.course_id == course_id).order_by(Chapter.chapter_number))).scalars().all()
    buttons = [[InlineKeyboardButton(text=f"Ch {ch.chapter_number}: {ch.title}", callback_data=f"adm_qz_chp:{ch.id}")] for ch in chapters]
    await state.set_state(AdminQuizFSM.choosing_chapter)
    await callback.message.edit_text("📖 Select Chapter for Quiz:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


@admin_router.callback_query(AdminQuizFSM.choosing_chapter, F.data.startswith("adm_qz_chp:"))
async def cb_adm_quiz_chapter(callback: CallbackQuery, state: FSMContext):
    chapter_id = int(callback.data.split(":")[1])
    await state.update_data(chapter_id=chapter_id)
    await state.set_state(AdminQuizFSM.entering_question_data)
    await callback.message.edit_text(
        "✍️ <b>Send the Quiz Question in 8 lines:</b>\n\n"
        "Line 1: Level Name\n"
        "Line 2: Time limit in seconds\n"
        "Line 3: Question text\n"
        "Line 4-7: Options A, B, C, D\n"
        "Line 8: <code>CorrectIndex | Explanation</code>",
        parse_mode="HTML",
    )


@admin_router.message(AdminQuizFSM.entering_question_data)
async def msg_adm_save_quiz(message: Message, state: FSMContext):
    lines = [line.strip() for line in (message.text or "").strip().split("\n") if line.strip()]
    if len(lines) < 8 or "|" not in lines[7]:
        await message.answer("⚠️ Please provide all 8 lines with <code>CorrectIndex | Explanation</code> on line 8.", parse_mode="HTML")
        return

    level = lines[0]
    time_limit = int(lines[1]) if lines[1].isdigit() else 45
    q_text = lines[2]
    options = [lines[3], lines[4], lines[5], lines[6]]
    idx_str, explanation = [x.strip() for x in lines[7].split("|", 1)]
    correct_idx = int(idx_str)

    data = await state.get_data()
    async with AsyncSessionLocal() as db:
        db.add(QuizQuestion(
            chapter_id=data["chapter_id"],
            level=level,
            question_text=q_text,
            options=options,
            correct_option=correct_idx,
            explanation=explanation,
            time_limit_sec=time_limit,
            is_published=True,
        ))
        await db.commit()

    await state.clear()
    await message.answer(
        f"✅ <b>Timed Quiz Question Added!</b>\n• <b>Level:</b> {level}\n• <b>Timer:</b> {time_limit}s",
        reply_markup=admin_home_kb(),
        parse_mode="HTML",
    )


# --- 5. Add Flashcard ---
@admin_router.callback_query(F.data == "adm:add_flashcard")
async def cb_adm_add_fc(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    async with AsyncSessionLocal() as db:
        stmt = select(Course).join(Chapter, Chapter.course_id == Course.id).distinct().order_by(Course.name)
        courses = (await db.execute(stmt)).scalars().all()
    if not courses:
        await callback.message.edit_text("⚠️ Create a chapter first.", reply_markup=admin_home_kb(), parse_mode="HTML")
        return
    buttons = [[InlineKeyboardButton(text=c.name, callback_data=f"adm_fc_crs:{c.id}")] for c in courses]
    await state.set_state(AdminFlashcardFSM.choosing_course)
    await callback.message.edit_text("🃏 <b>Add Flashcard</b> → Select Course:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


@admin_router.callback_query(AdminFlashcardFSM.choosing_course, F.data.startswith("adm_fc_crs:"))
async def cb_adm_fc_course(callback: CallbackQuery, state: FSMContext):
    course_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as db:
        chapters = (await db.execute(select(Chapter).where(Chapter.course_id == course_id).order_by(Chapter.chapter_number))).scalars().all()
    buttons = [[InlineKeyboardButton(text=f"Ch {ch.chapter_number}: {ch.title}", callback_data=f"adm_fc_chp:{ch.id}")] for ch in chapters]
    await state.set_state(AdminFlashcardFSM.choosing_chapter)
    await callback.message.edit_text("📖 Select Chapter for Flashcard:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


@admin_router.callback_query(AdminFlashcardFSM.choosing_chapter, F.data.startswith("adm_fc_chp:"))
async def cb_adm_fc_chapter(callback: CallbackQuery, state: FSMContext):
    chapter_id = int(callback.data.split(":")[1])
    await state.update_data(chapter_id=chapter_id)
    await state.set_state(AdminFlashcardFSM.entering_card_data)
    await callback.message.edit_text(
        "✍️ Send <b>Front Prompt | Back Answer</b> separated by <code>|</code>:",
        parse_mode="HTML",
    )


@admin_router.message(AdminFlashcardFSM.entering_card_data)
async def msg_adm_save_fc(message: Message, state: FSMContext):
    text = message.text or ""
    if "|" not in text:
        await message.answer("⚠️ Separate Front and Back with <code>|</code>.", parse_mode="HTML")
        return
    front, back = [x.strip() for x in text.split("|", 1)]
    data = await state.get_data()
    async with AsyncSessionLocal() as db:
        db.add(Flashcard(chapter_id=data["chapter_id"], front_text=front, back_text=back, is_published=True))
        await db.commit()
    await state.clear()
    await message.answer("✅ <b>Flashcard published!</b>", reply_markup=admin_home_kb(), parse_mode="HTML")


# --- 6. Toggle Feature Free vs. Premium Status ---
@admin_router.callback_query(F.data == "adm:features")
async def cb_adm_features(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    async with AsyncSessionLocal() as db:
        features = (await db.execute(select(FeatureConfig).order_by(FeatureConfig.sort_order))).scalars().all()

    buttons = []
    for f in features:
        badge = "🟢 FREE" if f.is_free else "💎 PREMIUM"
        buttons.append([InlineKeyboardButton(text=f"{f.title}: {badge}", callback_data=f"adm_tog_feat:{f.key}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Admin", callback_data="adm:home")])

    await callback.message.edit_text(
        "🔄 <b>Configure Feature Access (Free vs. Premium)</b>\n"
        "Tap any feature to toggle its status immediately:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data.startswith("adm_tog_feat:"))
async def cb_adm_toggle_feature(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    key = callback.data.split(":")[1]
    async with AsyncSessionLocal() as db:
        feat = (await db.execute(select(FeatureConfig).where(FeatureConfig.key == key))).scalar_one_or_none()
        if feat:
            feat.is_free = not feat.is_free
            await db.commit()
    await cb_adm_features(callback)


# --- 7. Configure & Verify Payment Methods ---
@admin_router.callback_query(F.data == "adm:pay_methods")
async def cb_adm_pay_methods(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    async with AsyncSessionLocal() as db:
        methods = (await db.execute(select(PaymentMethodConfig))).scalars().all()

    buttons = []
    for pm in methods:
        status = "✅ Active" if pm.is_verified_active else "⚠️ Unverified/Hidden"
        buttons.append([InlineKeyboardButton(
            text=f"{pm.provider_name} ({status})",
            callback_data=f"adm_pm_edit:{pm.code}",
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Admin", callback_data="adm:home")])

    await callback.message.edit_text(
        "🏦 <b>Payment Methods Configuration</b>\n"
        "Tap a method to set its account holder &amp; number:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data.startswith("adm_pm_edit:"))
async def cb_adm_pm_edit(callback: CallbackQuery, state: FSMContext):
    code = callback.data.split(":")[1]
    await state.update_data(pm_code=code)
    await state.set_state(AdminPaymentMethodFSM.entering_details)
    await callback.message.edit_text(
        f"🏦 <b>Configure {code}</b>\n\n"
        f"Send <code>Account Holder Name | Account/Phone Number</code>:",
        parse_mode="HTML",
    )


@admin_router.message(AdminPaymentMethodFSM.entering_details)
async def msg_adm_pm_save(message: Message, state: FSMContext):
    text = message.text or ""
    if "|" not in text:
        await message.answer("⚠️ Use format: <code>Account Holder | Account Number</code>", parse_mode="HTML")
        return
    holder, number = [x.strip() for x in text.split("|", 1)]
    data = await state.get_data()
    async with AsyncSessionLocal() as db:
        pm = (await db.execute(select(PaymentMethodConfig).where(PaymentMethodConfig.code == data["pm_code"]))).scalar_one()
        pm.account_holder = holder
        pm.account_number = number
        pm.is_verified_active = True
        await db.commit()
    await state.clear()
    await message.answer(
        f"✅ <b>{data['pm_code']} Verified and Activated!</b>",
        reply_markup=admin_home_kb(),
        parse_mode="HTML",
    )


# --- 8. Pending Receipts & Open Support Tickets ---
@admin_router.callback_query(F.data == "adm:pending_receipts")
async def cb_adm_pending_receipts(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return

    async with AsyncSessionLocal() as db:
        receipts = (await db.execute(
            select(PaymentReceipt)
            .where(PaymentReceipt.status == "PENDING")
            .order_by(PaymentReceipt.created_at.desc())
            .limit(10)
        )).scalars().all()

    if not receipts:
        await callback.answer("🎉 No pending payment receipts!", show_alert=True)
        return

    await callback.answer(f"Sending {len(receipts)} pending receipt(s)...")

    for r in receipts:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Approve Payment", callback_data=f"adm_pay:approve:{r.id}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"adm_pay:reject:{r.id}"),
        ]])
        created_str = r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "N/A"
        caption = (
            f"💳 <b>Pending Receipt #{r.id}</b>\n"
            f"👤 <b>Student ID:</b> <code>{r.student_id}</code>\n"
            f"👑 <b>Plan:</b> <code>{r.plan_code}</code>\n"
            f"🏦 <b>Method:</b> <code>{r.payment_method}</code>\n"
            f"🕒 <b>Submitted:</b> {created_str}"
        )
        raw_id = (r.receipt_file_id or "").strip()

        try:
            if raw_id.startswith("DOC:"):
                await callback.bot.send_document(
                    chat_id=callback.from_user.id,
                    document=raw_id.replace("DOC:", "", 1),
                    caption=caption,
                    reply_markup=kb,
                    parse_mode="HTML",
                )
            else:
                try:
                    await callback.bot.send_photo(
                        chat_id=callback.from_user.id,
                        photo=raw_id,
                        caption=caption,
                        reply_markup=kb,
                        parse_mode="HTML",
                    )
                except Exception:
                    await callback.bot.send_document(
                        chat_id=callback.from_user.id,
                        document=raw_id,
                        caption=caption,
                        reply_markup=kb,
                        parse_mode="HTML",
                    )
        except Exception as e:
            await callback.bot.send_message(
                chat_id=callback.from_user.id,
                text=f"{caption}\n\n⚠️ <i>Media preview error: {str(e)[:120]}</i>",
                reply_markup=kb,
                parse_mode="HTML",
            )


@admin_router.callback_query(F.data == "adm:tickets")
async def cb_adm_tickets(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    async with AsyncSessionLocal() as db:
        tickets = (await db.execute(select(SupportTicket).where(SupportTicket.status == "OPEN").limit(10))).scalars().all()

    if not tickets:
        await callback.answer("🎉 No open support tickets!", show_alert=True)
        return

    buttons = [
        [InlineKeyboardButton(text=f"Ticket #{t.id}: {t.message[:30]}...", callback_data=f"adm_tkt:{t.id}")]
        for t in tickets
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Admin", callback_data="adm:home")])
    await callback.message.edit_text("🆘 <b>Open Support Tickets</b> — Tap to reply:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


@admin_router.callback_query(F.data.startswith("adm_tkt:"))
async def cb_adm_ticket_detail(callback: CallbackQuery, state: FSMContext):
    t_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as db:
        t = (await db.execute(select(SupportTicket).where(SupportTicket.id == t_id))).scalar_one()
    await state.update_data(ticket_id=t.id, student_id=t.student_id)
    await state.set_state(AdminTicketReplyFSM.entering_reply)
    await callback.message.edit_text(
        f"🆘 <b>Ticket #{t.id}</b> from <code>{t.student_id}</code>:\n\n"
        f"\"{t.message}\"\n\n"
        f"✍️ <b>Send your reply below:</b>",
        parse_mode="HTML",
    )


@admin_router.message(AdminTicketReplyFSM.entering_reply)
async def msg_adm_ticket_reply(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    async with AsyncSessionLocal() as db:
        t = (await db.execute(select(SupportTicket).where(SupportTicket.id == data["ticket_id"]))).scalar_one()
        t.admin_reply = message.text
        t.status = "RESOLVED"
        await db.commit()

    try:
        await bot.send_message(
            chat_id=data["student_id"],
            text=f"✉️ <b>Support Ticket #{data['ticket_id']} Reply:</b>\n\n{message.text}",
            parse_mode="HTML",
        )
    except Exception:
        pass

    await state.clear()
    await message.answer(f"✅ Reply sent and Ticket #{data['ticket_id']} marked RESOLVED!", reply_markup=admin_home_kb(), parse_mode="HTML")