import html
import io
import json
from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton, BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from database import AsyncSessionLocal
from models import Course, Chapter, LearningResource, QuizQuestion, Flashcard
from admin_handlers import is_admin
from resource_catalog import (
    create_catalog_file_entry,
    list_catalog_crud_items,
    get_serialized_resource,
    toggle_catalog_free,
    rename_catalog_item,
    delete_catalog_item,
)

bulk_router = Router()

YEAR_SHORT_TAG: dict[str, str] = {
    "1st Year": "1st Yr",
    "2nd Year": "2nd Yr",
    "3rd Year": "3rd Yr",
}

MORE_RES_TYPES = [
    ("🎓 COC Past Exams (1st Yr)", "res_coc"),
    ("📝 Worksheets", "res_worksheet"),
    ("📚 አጤሬራ", "res_atirera"),
    ("📖 Reference Books", "res_refbook"),
]

COC_TRACK_CHOICES = [
    ("🏥 Health", "Health"),
    ("⚖️ Law", "Law"),
    ("🏛 Architecture", "Architecture"),
]


def _course_btn_label(course: Course, max_name_len: int = 18) -> str:
    yr = (getattr(course, "year_level", None) or "1st Year").strip()
    tag = YEAR_SHORT_TAG.get(yr, yr)
    name = (course.name or "Course").strip()[:max_name_len]
    return f"[{tag}] {name}"


def _build_course_grid(
    courses: list[Course],
    callback_prefix: str,
    back_cb: str = "adm:bulk_hub",
    max_name_len: int = 18,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for c in courses:
        row.append(
            InlineKeyboardButton(
                text=_course_btn_label(c, max_name_len=max_name_len),
                callback_data=f"{callback_prefix}:{c.id}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


class BulkAdminFSM(StatesGroup):
    ch_course = State()
    ch_payload = State()
    fc_course = State()
    fc_chapter = State()
    fc_payload = State()
    qz_course = State()
    qz_chapter = State()
    qz_payload = State()
    pdf_feature = State()
    pdf_course = State()
    pdf_chapter = State()
    pdf_batch_listening = State()
    crud_renaming = State()


def bulk_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📖 Bulk Add Chapters", callback_data="bulk:chapters"),
            InlineKeyboardButton(text="🃏 Bulk Add Flashcards", callback_data="bulk:flashcards"),
        ],
        [
            InlineKeyboardButton(text="⏱ Bulk Import Quizzes (JSON/CSV/Text)", callback_data="bulk:quizzes"),
        ],
        [
            InlineKeyboardButton(text="📚 Batch Drop Multiple PDFs/Files", callback_data="bulk:pdfs"),
        ],
        [
            InlineKeyboardButton(text="📂 Batch COC / Worksheet / አጤሬራ / Books", callback_data="bulk:more_res"),
        ],
        [
            InlineKeyboardButton(text="📥 Sample Files", callback_data="bulk:templates"),
            InlineKeyboardButton(text="🗂 Manage Items", callback_data="bulk:crud"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Back to Admin Panel", callback_data="adm:home_pro"),
        ],
    ])


async def extract_text_from_msg(message: Message) -> str:
    if message.document:
        buf = io.BytesIO()
        await message.bot.download(message.document, destination=buf)
        return buf.getvalue().decode("utf-8-sig", errors="replace")
    return (message.text or "").strip()


@bulk_router.callback_query(F.data == "adm:bulk_hub")
async def cb_bulk_hub(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await state.clear()
    await callback.answer()
    await callback.message.edit_text(
        "🚀 <b>QineSystems Bulk Import &amp; Batch Upload Engine</b>\n\n"
        "Populate courses, chapters, quiz banks, flashcards, and PDF modules across <b>1st / 2nd / 3rd Year</b>:",
        reply_markup=bulk_menu_kb(),
        parse_mode="HTML",
    )


@bulk_router.callback_query(F.data == "bulk:templates")
async def cb_send_templates(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer("Generating templates...")

    sample_chapters_txt = "1\n2\n3\n4\n5\n6\n".encode("utf-8")
    sample_flashcards_txt = (
        "Deductive Argument | An argument claiming necessity.\n"
        "Inductive Argument | An argument claiming probability.\n"
    ).encode("utf-8")

    await callback.message.answer_document(
        document=BufferedInputFile(sample_chapters_txt, filename="sample_chapters.txt"),
        caption="📥 <b>Sample Chapters File</b> (One chapter number per line: 1, 2, 3...)",
        parse_mode="HTML",
    )
    await callback.message.answer_document(
        document=BufferedInputFile(sample_flashcards_txt, filename="sample_flashcards.txt"),
        caption="📥 <b>Sample Flashcards File</b> (Format: <code>Front | Back</code>)",
        parse_mode="HTML",
    )


# --- 1. BULK ADD CHAPTERS ---
@bulk_router.callback_query(F.data == "bulk:chapters")
async def cb_bulk_chapters_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    async with AsyncSessionLocal() as db:
        courses = (await db.execute(select(Course).order_by(Course.year_level, Course.name))).scalars().all()

    await state.set_state(BulkAdminFSM.ch_course)
    await callback.message.edit_text(
        "📖 <b>Bulk Add Chapters</b> → Select a Course:",
        reply_markup=_build_course_grid(courses, "blk_ch_crs", "adm:bulk_hub"),
        parse_mode="HTML",
    )


@bulk_router.callback_query(BulkAdminFSM.ch_course, F.data.startswith("blk_ch_crs:"))
async def cb_bulk_chapters_course(callback: CallbackQuery, state: FSMContext):
    course_id = int(callback.data.split(":")[1])
    await state.update_data(course_id=course_id)
    await state.set_state(BulkAdminFSM.ch_payload)
    await callback.answer()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Download Sample Chapters File", callback_data="bulk:templates")],
        [InlineKeyboardButton(text="⬅️ Cancel", callback_data="adm:bulk_hub")],
    ])
    await callback.message.edit_text(
        "✍️ <b>Send Chapter Numbers (one per line)</b> or upload a <code>.txt</code> file:\n"
        "<i>No titles needed! The system automatically creates 'Chapter 1', 'Chapter 2', etc.</i>\n\n"
        "Example:\n"
        "<pre>1\n2\n3\n4\n5</pre>",
        reply_markup=kb,
        parse_mode="HTML",
    )


@bulk_router.message(BulkAdminFSM.ch_payload)
async def msg_bulk_chapters_save(message: Message, state: FSMContext):
    raw_text = await extract_text_from_msg(message)
    data = await state.get_data()
    course_id = data.get("course_id")
    if not course_id:
        await state.clear()
        await message.answer("⚠️ Session expired. Start Bulk Add Chapters again.", reply_markup=bulk_menu_kb(), parse_mode="HTML")
        return

    added = 0
    skipped = 0
    async with AsyncSessionLocal() as db:
        existing_nums = set((await db.execute(
            select(Chapter.chapter_number).where(Chapter.course_id == int(course_id))
        )).scalars().all())

        for line in raw_text.splitlines():
            num_s = line.replace("Chapter", "").strip()
            if not num_s.isdigit():
                skipped += 1
                continue
            ch_num = int(num_s)
            if ch_num in existing_nums:
                skipped += 1
                continue
            auto_title = f"Chapter {ch_num}"
            db.add(Chapter(course_id=int(course_id), chapter_number=ch_num, title=auto_title, is_published=True))
            existing_nums.add(ch_num)
            added += 1
        await db.commit()

    await state.clear()
    await message.answer(
        f"✅ <b>Bulk Chapters Import Complete!</b>\n"
        f"• <b>Created:</b> {added} chapters (Chapter 1, Chapter 2...)\n"
        f"• <b>Skipped (duplicates):</b> {skipped}",
        reply_markup=bulk_menu_kb(),
        parse_mode="HTML",
    )


# --- 2. BULK FLASHCARDS ---
@bulk_router.callback_query(F.data == "bulk:flashcards")
async def cb_bulk_fc_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    async with AsyncSessionLocal() as db:
        courses = (await db.execute(select(Course).order_by(Course.year_level, Course.name))).scalars().all()

    await state.set_state(BulkAdminFSM.fc_course)
    await callback.message.edit_text(
        "🃏 <b>Bulk Add Flashcards</b> → Select Course:",
        reply_markup=_build_course_grid(courses, "blk_fc_crs", "adm:bulk_hub"),
        parse_mode="HTML",
    )


@bulk_router.callback_query(BulkAdminFSM.fc_course, F.data.startswith("blk_fc_crs:"))
async def cb_bulk_fc_course(callback: CallbackQuery, state: FSMContext):
    course_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as db:
        chapters = (await db.execute(
            select(Chapter).where(Chapter.course_id == course_id).order_by(Chapter.chapter_number)
        )).scalars().all()
    buttons = [[InlineKeyboardButton(text=f"Chapter {ch.chapter_number}", callback_data=f"blk_fc_chp:{ch.id}")] for ch in chapters]
    buttons.append([InlineKeyboardButton(text="⬅️ Cancel", callback_data="adm:bulk_hub")])
    await state.set_state(BulkAdminFSM.fc_chapter)
    await callback.message.edit_text(
        "📖 Select Chapter for Flashcards:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@bulk_router.callback_query(BulkAdminFSM.fc_chapter, F.data.startswith("blk_fc_chp:"))
async def cb_bulk_fc_chapter(callback: CallbackQuery, state: FSMContext):
    chapter_id = int(callback.data.split(":")[1])
    await state.update_data(chapter_id=chapter_id)
    await state.set_state(BulkAdminFSM.fc_payload)
    await callback.answer()
    await callback.message.edit_text(
        "✍️ <b>Paste Multiple Flashcards</b> (<code>Front | Back</code> per line):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Cancel", callback_data="adm:bulk_hub")]]),
        parse_mode="HTML",
    )


@bulk_router.message(BulkAdminFSM.fc_payload)
async def msg_bulk_fc_save(message: Message, state: FSMContext):
    raw_text = await extract_text_from_msg(message)
    data = await state.get_data()
    chapter_id = data.get("chapter_id")
    if not chapter_id:
        await state.clear()
        await message.answer("⚠️ Session expired. Start Bulk Add Flashcards again.", reply_markup=bulk_menu_kb(), parse_mode="HTML")
        return

    cards = []
    for line in raw_text.splitlines():
        if "|" in line:
            f, b = [x.strip() for x in line.split("|", 1)]
            if f and b:
                cards.append(Flashcard(chapter_id=int(chapter_id), front_text=f, back_text=b, is_published=True))
    async with AsyncSessionLocal() as db:
        db.add_all(cards)
        await db.commit()
    await state.clear()
    await message.answer(f"✅ <b>{len(cards)} Flashcards Published!</b>", reply_markup=bulk_menu_kb(), parse_mode="HTML")


# --- 3. BULK QUIZZES ---
@bulk_router.callback_query(F.data == "bulk:quizzes")
async def cb_bulk_qz_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    async with AsyncSessionLocal() as db:
        courses = (await db.execute(select(Course).order_by(Course.year_level, Course.name))).scalars().all()

    await state.set_state(BulkAdminFSM.qz_course)
    await callback.message.edit_text(
        "⏱ <b>Bulk Import Quizzes</b> → Select Course:",
        reply_markup=_build_course_grid(courses, "blk_qz_crs", "adm:bulk_hub"),
        parse_mode="HTML",
    )


@bulk_router.callback_query(BulkAdminFSM.qz_course, F.data.startswith("blk_qz_crs:"))
async def cb_bulk_qz_course(callback: CallbackQuery, state: FSMContext):
    course_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as db:
        chapters = (await db.execute(
            select(Chapter).where(Chapter.course_id == course_id).order_by(Chapter.chapter_number)
        )).scalars().all()
    buttons = [[InlineKeyboardButton(text=f"Chapter {ch.chapter_number}", callback_data=f"blk_qz_chp:{ch.id}")] for ch in chapters]
    buttons.append([InlineKeyboardButton(text="⬅️ Cancel", callback_data="adm:bulk_hub")])
    await state.set_state(BulkAdminFSM.qz_chapter)
    await callback.message.edit_text(
        "📖 Select Chapter for Quizzes:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@bulk_router.callback_query(BulkAdminFSM.qz_chapter, F.data.startswith("blk_qz_chp:"))
async def cb_bulk_qz_chapter(callback: CallbackQuery, state: FSMContext):
    chapter_id = int(callback.data.split(":")[1])
    await state.update_data(chapter_id=chapter_id)
    await state.set_state(BulkAdminFSM.qz_payload)
    await callback.answer()
    await callback.message.edit_text(
        "⏱ Send your Quiz Bank JSON file or paste JSON text:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Cancel", callback_data="adm:bulk_hub")]]),
        parse_mode="HTML",
    )


@bulk_router.message(BulkAdminFSM.qz_payload)
async def msg_bulk_qz_save(message: Message, state: FSMContext):
    raw_text = await extract_text_from_msg(message)
    data = await state.get_data()
    chapter_id = data.get("chapter_id")
    if not chapter_id:
        await state.clear()
        await message.answer("⚠️ Session expired. Start Bulk Import Quizzes again.", reply_markup=bulk_menu_kb(), parse_mode="HTML")
        return

    questions = []
    try:
        items = json.loads(raw_text)
        for q in items:
            questions.append(QuizQuestion(
                chapter_id=int(chapter_id),
                level=q.get("level", "Medium"),
                question_text=q["question_text"],
                options=q["options"],
                correct_option=int(q["correct_option"]),
                explanation=q.get("explanation", ""),
                time_limit_sec=int(q.get("time_limit_sec", 45)),
                is_published=True,
            ))
        async with AsyncSessionLocal() as db:
            db.add_all(questions)
            await db.commit()
        await state.clear()
        await message.answer(f"✅ <b>{len(questions)} Questions Imported!</b>", reply_markup=bulk_menu_kb(), parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Error: <code>{html.escape(str(e))}</code>", parse_mode="HTML")


# --- 4. BATCH PDF UPLOADER ---
@bulk_router.callback_query(F.data == "bulk:pdfs")
async def cb_batch_pdf_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    features = [
        ("📚 Modules", "modules"), ("📝 Mid-Exams", "mid_exams"), ("🗒 Study Notes", "notes"),
        ("🎓 Final Exams", "final_exams"), ("🎯 Exam Trainer", "exam_trainer"),
        ("✍️ Practice Questions", "practice_questions"), ("📂 More Resources", "more_resources"),
    ]
    buttons = [[InlineKeyboardButton(text=lbl, callback_data=f"blk_pdf_ft:{k}")] for lbl, k in features]
    buttons.append([InlineKeyboardButton(text="⬅️ Back", callback_data="adm:bulk_hub")])
    await state.set_state(BulkAdminFSM.pdf_feature)
    await callback.message.edit_text(
        "📚 <b>Batch Drop PDFs</b> → Select Feature:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@bulk_router.callback_query(BulkAdminFSM.pdf_feature, F.data.startswith("blk_pdf_ft:"))
async def cb_batch_pdf_feature(callback: CallbackQuery, state: FSMContext):
    await state.update_data(feature_key=callback.data.split(":")[1])
    async with AsyncSessionLocal() as db:
        courses = (await db.execute(select(Course).order_by(Course.year_level, Course.name))).scalars().all()

    await state.set_state(BulkAdminFSM.pdf_course)
    await callback.message.edit_text(
        "📘 Select Course:",
        reply_markup=_build_course_grid(courses, "blk_pdf_crs", "adm:bulk_hub"),
        parse_mode="HTML",
    )


@bulk_router.callback_query(BulkAdminFSM.pdf_course, F.data.startswith("blk_pdf_crs:"))
async def cb_batch_pdf_course(callback: CallbackQuery, state: FSMContext):
    course_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as db:
        chapters = (await db.execute(
            select(Chapter).where(Chapter.course_id == course_id).order_by(Chapter.chapter_number)
        )).scalars().all()
    buttons = [[InlineKeyboardButton(text=f"Chapter {ch.chapter_number}", callback_data=f"blk_pdf_chp:{ch.id}")] for ch in chapters]
    buttons.append([InlineKeyboardButton(text="⬅️ Cancel", callback_data="adm:bulk_hub")])
    await state.set_state(BulkAdminFSM.pdf_chapter)
    await callback.message.edit_text(
        "📖 Select Chapter:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@bulk_router.callback_query(BulkAdminFSM.pdf_chapter, F.data.startswith("blk_pdf_chp:"))
async def cb_batch_pdf_ready(callback: CallbackQuery, state: FSMContext):
    await state.update_data(chapter_id=int(callback.data.split(":")[1]), mr_cat="", mr_sub="", uploaded_count=0)
    await state.set_state(BulkAdminFSM.pdf_batch_listening)
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Finish Batch Upload", callback_data="adm:bulk_hub")]])
    await callback.message.edit_text("🟢 <b>Batch Upload Mode Active!</b> Drop multiple PDFs now.", reply_markup=kb, parse_mode="HTML")


# --- 5. BATCH MORE RESOURCES (COC / Worksheet / አጤሬራ / Reference Books) ---
async def _get_or_create_helper_chapter(db, course_id: int | None, label: str) -> int:
    if course_id:
        existing = (
            await db.execute(
                select(Chapter).where(Chapter.course_id == course_id).order_by(Chapter.chapter_number.asc())
            )
        ).scalars().first()
        if existing:
            return existing.id

    hub_course = (
        await db.execute(select(Course).where(Course.name == "More Resources"))
    ).scalar_one_or_none()
    if not hub_course:
        hub_course = Course(name="More Resources", year_level="1st Year")
        db.add(hub_course)
        await db.flush()

    chap = (
        await db.execute(
            select(Chapter).where(Chapter.course_id == hub_course.id, Chapter.title == label)
        )
    ).scalar_one_or_none()
    if not chap:
        existing_count = len((await db.execute(select(Chapter.id).where(Chapter.course_id == hub_course.id))).all())
        chap = Chapter(
            course_id=hub_course.id,
            chapter_number=existing_count + 1,
            title=label[:120],
            is_published=True,
        )
        db.add(chap)
        await db.flush()
    return chap.id


@bulk_router.callback_query(F.data == "bulk:more_res")
async def cb_bulk_more_res_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    await state.clear()
    buttons = [[InlineKeyboardButton(text=lbl, callback_data=f"blk_mr_cat:{k}")] for lbl, k in MORE_RES_TYPES]
    buttons.append([InlineKeyboardButton(text="⬅️ Back", callback_data="adm:bulk_hub")])
    await callback.message.edit_text(
        "📂 <b>Batch Upload More Resources</b> → Choose Category:\n"
        "<i>Note: COC Past Exams apply to 1st Year only.</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@bulk_router.callback_query(F.data.startswith("blk_mr_cat:"))
async def cb_bulk_more_res_cat(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    mr_cat = callback.data.split(":", 1)[1]
    await state.update_data(mr_cat=mr_cat, mr_sub="")

    if mr_cat == "res_coc":
        buttons = [[InlineKeyboardButton(text=lbl, callback_data=f"blk_mr_trk:{val}")] for lbl, val in COC_TRACK_CHOICES]
        buttons.append([InlineKeyboardButton(text="⬅️ Back", callback_data="bulk:more_res")])
        await callback.message.edit_text(
            "🎓 <b>COC Past Exams (1st Year Only)</b> → Select Track:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )
        return

    async with AsyncSessionLocal() as db:
        courses = (await db.execute(select(Course).order_by(Course.year_level, Course.name))).scalars().all()

    await callback.message.edit_text(
        "📘 <b>Select Course</b>:",
        reply_markup=_build_course_grid(courses, "blk_mr_crs", "bulk:more_res"),
        parse_mode="HTML",
    )


@bulk_router.callback_query(F.data.startswith("blk_mr_trk:"))
async def cb_bulk_more_res_coc_trk(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    track_val = callback.data.split(":", 1)[1]
    async with AsyncSessionLocal() as db:
        chap_id = await _get_or_create_helper_chapter(db, None, f"COC · {track_val}")
        await db.commit()

    await state.update_data(
        chapter_id=chap_id,
        feature_key="more_resources",
        mr_sub=track_val,
        uploaded_count=0,
    )
    await state.set_state(BulkAdminFSM.pdf_batch_listening)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Finish Batch Upload", callback_data="adm:bulk_hub")]])
    await callback.message.edit_text(
        f"🟢 <b>Batch Upload Active (1st Year COC · {html.escape(track_val)})</b>\n"
        "Drop multiple PDFs/files now — titles come automatically from each filename.",
        reply_markup=kb,
        parse_mode="HTML",
    )


@bulk_router.callback_query(F.data.startswith("blk_mr_crs:"))
async def cb_bulk_more_res_course(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    course_id = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    mr_cat = data.get("mr_cat", "res_worksheet")

    async with AsyncSessionLocal() as db:
        chapters = (
            await db.execute(
                select(Chapter).where(Chapter.course_id == course_id).order_by(Chapter.chapter_number)
            )
        ).scalars().all()
        chap_id = chapters[0].id if chapters else await _get_or_create_helper_chapter(db, course_id, "General")
        await db.commit()

    if mr_cat == "res_atirera" and chapters:
        await state.update_data(course_id=course_id)
        buttons = [
            [InlineKeyboardButton(text=f"Chapter {ch.chapter_number}", callback_data=f"blk_mr_achp:{ch.id}:{ch.chapter_number}")]
            for ch in chapters
        ]
        buttons.append([InlineKeyboardButton(text="⬅️ Back", callback_data=f"blk_mr_cat:{mr_cat}")])
        await callback.message.edit_text(
            "📚 <b>አጤሬራ</b> → Select Chapter:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )
        return

    sub_label = f"Chapter {chapters[0].chapter_number}" if (mr_cat == "res_atirera" and chapters) else ""
    await state.update_data(
        course_id=course_id,
        chapter_id=chap_id,
        feature_key="more_resources",
        mr_sub=sub_label,
        uploaded_count=0,
    )
    await state.set_state(BulkAdminFSM.pdf_batch_listening)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Finish Batch Upload", callback_data="adm:bulk_hub")]])
    await callback.message.edit_text(
        "🟢 <b>Batch Upload Mode Active!</b> Drop multiple PDFs/files now.",
        reply_markup=kb,
        parse_mode="HTML",
    )


@bulk_router.callback_query(F.data.startswith("blk_mr_achp:"))
async def cb_bulk_more_res_atirera_chapter(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, ch_id_s, ch_num_s = callback.data.split(":", 2)
    await state.update_data(
        chapter_id=int(ch_id_s),
        feature_key="more_resources",
        mr_sub=f"Chapter {ch_num_s}",
        uploaded_count=0,
    )
    await state.set_state(BulkAdminFSM.pdf_batch_listening)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Finish Batch Upload", callback_data="adm:bulk_hub")]])
    await callback.message.edit_text(
        f"🟢 <b>Batch Upload Active (አጤሬራ · Chapter {html.escape(ch_num_s)})</b>\n"
        "Drop multiple PDFs/files now — titles come automatically from each filename.",
        reply_markup=kb,
        parse_mode="HTML",
    )


@bulk_router.message(BulkAdminFSM.pdf_batch_listening, F.document)
async def msg_batch_pdf_receive(message: Message, state: FSMContext):
    data = await state.get_data()
    doc_name = message.document.file_name if message.document else "Resource"
    title = (message.caption or doc_name or "Resource").rsplit(".", 1)[0].replace("_", " ").strip()
    mr_cat = data.get("mr_cat") or ""
    mr_sub = data.get("mr_sub") or ""

    if mr_cat:
        await create_catalog_file_entry(
            category=mr_cat,
            title=title,
            telegram_file_id=message.document.file_id,
            course_id=data.get("course_id"),
            sub_label=mr_sub,
            is_free=bool(data.get("mr_free", False)),
        )
    else:
        async with AsyncSessionLocal() as db:
            db.add(
                LearningResource(
                    chapter_id=int(data["chapter_id"]),
                    feature_key=str(data["feature_key"]),
                    title=title,
                    content_type="telegram_file",
                    telegram_file_id=message.document.file_id,
                    is_published=True,
                )
            )
            await db.commit()

    count = int(data.get("uploaded_count", 0)) + 1
    await state.update_data(uploaded_count=count)
    await message.reply(f"✅ Indexed #{count}: <b>{html.escape(title)}</b>", parse_mode="HTML")


# --- 6. NESTED MANAGE CATALOG ITEMS (CRUD) ---
@bulk_router.callback_query(F.data == "bulk:crud")
async def cb_bulk_crud_categories(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await state.clear()
    await callback.answer()
    rows = [
        [
            InlineKeyboardButton(text="🎓 COC Past Exams (1st Yr)", callback_data="blk_mc_cat:coc"),
            InlineKeyboardButton(text="📝 Worksheets", callback_data="blk_mc_cat:worksheet"),
        ],
        [
            InlineKeyboardButton(text="📚 አጤሬራ", callback_data="blk_mc_cat:atirera"),
            InlineKeyboardButton(text="📖 Reference Books", callback_data="blk_mc_cat:refbook"),
        ],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="adm:bulk_hub")],
    ]
    await callback.message.edit_text(
        "🗂 <b>Manage Catalog Items</b> → Choose Category:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


@bulk_router.callback_query(F.data.startswith("blk_mc_cat:"))
async def cb_bulk_crud_pick_sub_or_course(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer()
    cat = callback.data.split(":", 1)[1]
    await state.update_data(mc_cat=cat, mc_track="", mc_course_id=0, mc_chapter="")

    if cat == "coc":
        rows = [
            [InlineKeyboardButton(text=lbl, callback_data=f"blk_mc_trk:{val}")]
            for lbl, val in COC_TRACK_CHOICES
        ]
        rows.append([InlineKeyboardButton(text="⬅️ Back to Categories", callback_data="bulk:crud")])
        await callback.message.edit_text(
            "🗂 <b>COC Past Exams (1st Year Only)</b> → Choose Track:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
        return

    async with AsyncSessionLocal() as db:
        courses = (await db.execute(select(Course).order_by(Course.year_level, Course.name))).scalars().all()

    await callback.message.edit_text(
        "🗂 <b>Select Course</b>:",
        reply_markup=_build_course_grid(courses, f"blk_mc_crs:{cat}", "bulk:crud"),
        parse_mode="HTML",
    )


async def _show_crud_files_view(
    callback_or_msg,
    state: FSMContext,
    cat: str,
    track: str = "",
    course_id: int = 0,
    chapter_label: str = "",
    back_cb: str = "bulk:crud",
):
    all_items = await list_catalog_crud_items(category=cat, limit=100)
    filtered = []
    for it in all_items:
        if course_id > 0 and it.get("course_id") != course_id:
            continue
        if track and (it.get("sub") or "").lower() != track.lower():
            continue
        if chapter_label and (it.get("sub") or "").lower() != chapter_label.lower():
            continue
        filtered.append(it)

    await state.update_data(
        mc_cat=cat,
        mc_track=track,
        mc_course_id=course_id,
        mc_chapter=chapter_label,
        mc_back_cb=back_cb,
    )

    rows = [
        [
            InlineKeyboardButton(
                text=f"{'🆓' if it['is_free'] else '🔒'} {it['title'][:28]}",
                callback_data=f"blk_crud_item:{it['id']}",
            )
        ]
        for it in filtered[:20]
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data=back_cb)])

    scope_parts = [cat.upper()]
    if track:
        scope_parts.append(track)
    if course_id > 0:
        scope_parts.append(f"Course #{course_id}")
    if chapter_label:
        scope_parts.append(chapter_label)

    safe_scope = html.escape(" · ".join(scope_parts))
    text = (
        f"🗂 <b>Manage Files ({safe_scope})</b>\n"
        + ("Tap a file to toggle access, rename, or delete:" if filtered else "<i>No files found in this selection.</i>")
    )
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    if isinstance(callback_or_msg, CallbackQuery):
        try:
            await callback_or_msg.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await callback_or_msg.message.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        await callback_or_msg.answer(text, reply_markup=kb, parse_mode="HTML")


@bulk_router.callback_query(F.data.startswith("blk_mc_trk:"))
async def cb_bulk_crud_coc_track(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer()
    track_val = callback.data.split(":", 1)[1]
    await _show_crud_files_view(
        callback,
        state,
        cat="coc",
        track=track_val,
        back_cb="blk_mc_cat:coc",
    )


@bulk_router.callback_query(F.data.startswith("blk_mc_crs:"))
async def cb_bulk_crud_course(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer()
    _, cat, cid_s = callback.data.split(":", 2)
    cid = int(cid_s) if cid_s.isdigit() else 0

    if cat == "atirera":
        items = await list_catalog_crud_items(category="atirera", limit=100)
        chapters: list[str] = []
        for it in items:
            if it.get("course_id") == cid:
                ch_val = (it.get("sub") or "General").strip()
                if ch_val not in chapters:
                    chapters.append(ch_val)

        if chapters:
            await state.update_data(mc_cat=cat, mc_course_id=cid, mc_atirera_chapters=chapters)
            rows = [
                [InlineKeyboardButton(text=f"📑 {ch[:30]}", callback_data=f"blk_mc_chp:{cid}:{idx}")]
                for idx, ch in enumerate(chapters[:20])
            ]
            rows.append([InlineKeyboardButton(text="⬅️ Back to Courses", callback_data="blk_mc_cat:atirera")])
            await callback.message.edit_text(
                "🗂 <b>አጤሬራ</b> → Select Chapter:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
                parse_mode="HTML",
            )
            return

    await _show_crud_files_view(
        callback,
        state,
        cat=cat,
        course_id=cid,
        back_cb=f"blk_mc_cat:{cat}",
    )


@bulk_router.callback_query(F.data.startswith("blk_mc_chp:"))
async def cb_bulk_crud_atirera_chapter(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer()
    _, cid_s, idx_s = callback.data.split(":", 2)
    cid = int(cid_s) if cid_s.isdigit() else 0
    idx = int(idx_s) if idx_s.isdigit() else 0
    chapters = (await state.get_data()).get("mc_atirera_chapters") or []
    ch_label = chapters[idx] if 0 <= idx < len(chapters) else ""

    await _show_crud_files_view(
        callback,
        state,
        cat="atirera",
        course_id=cid,
        chapter_label=ch_label,
        back_cb=f"blk_mc_crs:atirera:{cid}",
    )


@bulk_router.callback_query(F.data.startswith("blk_crud_item:"))
async def cb_bulk_crud_item(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    res_id = int(callback.data.split(":", 1)[1])
    it = await get_serialized_resource(res_id)
    await callback.answer()
    if not it:
        await callback.message.edit_text("⚠️ Item not found.", reply_markup=bulk_menu_kb(), parse_mode="HTML")
        return

    cat = it.get("category") or "worksheet"
    sub = it.get("sub") or ""
    cid = int(it.get("course_id") or 0)
    if cat == "coc" and sub:
        return_cb = f"blk_mc_trk:{sub}"
    elif cat == "atirera" and cid > 0:
        return_cb = f"blk_mc_crs:atirera:{cid}"
    elif cid > 0:
        return_cb = f"blk_mc_crs:{cat}:{cid}"
    else:
        return_cb = "bulk:crud"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Set Premium 🔒" if it["is_free"] else "Set Free 🆓",
                callback_data=f"blk_crud_free:{res_id}",
            ),
            InlineKeyboardButton(text="✏️ Rename", callback_data=f"blk_crud_ren:{res_id}"),
        ],
        [InlineKeyboardButton(text="🗑 Delete", callback_data=f"blk_crud_del:{res_id}")],
        [InlineKeyboardButton(text="⬅️ Back to Files", callback_data=return_cb)],
    ])
    await callback.message.edit_text(
        f"📄 <b>{html.escape(str(it['title']))}</b>\n"
        f"• Category: <code>{html.escape(str(cat))}</code>\n"
        f"• Track/Ch: <code>{html.escape(str(sub or '—'))}</code>\n"
        f"• Access: <code>{'Free' if it['is_free'] else 'Premium'}</code>",
        reply_markup=kb,
        parse_mode="HTML",
    )


@bulk_router.callback_query(F.data.startswith("blk_crud_free:"))
async def cb_bulk_crud_toggle_free(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    res_id = int(callback.data.split(":", 1)[1])
    await toggle_catalog_free(res_id)
    await callback.answer("Updated access")
    callback.data = f"blk_crud_item:{res_id}"
    await cb_bulk_crud_item(callback, state)


@bulk_router.callback_query(F.data.startswith("blk_crud_ren:"))
async def cb_bulk_crud_rename_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    res_id = int(callback.data.split(":", 1)[1])
    await state.update_data(crud_rename_id=res_id)
    await state.set_state(BulkAdminFSM.crud_renaming)
    await callback.answer()
    await callback.message.answer("✍️ Send the new title:")


@bulk_router.message(BulkAdminFSM.crud_renaming)
async def msg_bulk_crud_rename_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    res_id = int(data.get("crud_rename_id") or 0)
    await rename_catalog_item(res_id, message.text or "")
    await state.clear()
    await message.answer("✅ Title updated.", reply_markup=bulk_menu_kb(), parse_mode="HTML")


@bulk_router.callback_query(F.data.startswith("blk_crud_del:"))
async def cb_bulk_crud_delete(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    res_id = int(callback.data.split(":", 1)[1])
    it = await get_serialized_resource(res_id)
    await delete_catalog_item(res_id)
    await callback.answer("Deleted")

    cat = (it or {}).get("category") or ""
    sub = (it or {}).get("sub") or ""
    cid = int((it or {}).get("course_id") or 0)
    back_cb = f"blk_mc_trk:{sub}" if (cat == "coc" and sub) else (f"blk_mc_crs:{cat}:{cid}" if cid > 0 else "bulk:crud")

    await _show_crud_files_view(
        callback,
        state,
        cat=cat,
        track=sub if cat == "coc" else "",
        course_id=cid,
        chapter_label=sub if cat == "atirera" else "",
        back_cb=back_cb,
    )