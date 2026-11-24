from datetime import datetime, timedelta
import html
import re
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, delete, func
from sqlalchemy.exc import SQLAlchemyError

from database import AsyncSessionLocal
from models import (
    Course,
    Chapter,
    FeatureConfig,
    LearningResource,
    QuizQuestion,
    Flashcard,
    Student,
)
from admin_handlers import is_admin

admin_crud_router = Router()

PAGE_SIZE = 8
REFERRAL_REWARD_ETB = 30
_TAG_RE = re.compile(r"^\[([a-zA-Z_]+)(?::([^\]]+))?\]\s*(.*)$", re.DOTALL)

YEAR_CYCLE_ORDER: list[str] = ["1st Year", "2nd Year", "3rd Year"]
YEAR_SHORT_TAG: dict[str, str] = {
    "1st Year": "1st Yr",
    "2nd Year": "2nd Yr",
    "3rd Year": "3rd Yr",
}

BASE_RESOURCE_TYPES: list[tuple[str, str]] = [
    ("notes", "📝 Study Notes"),
    ("mid_exams", "📋 Mid-Exams"),
    ("final_exams", "🎓 Final Exams"),
    ("modules", "📘 Modules"),
    ("exam_trainer", "🎯 Exam Trainer"),
    ("practice_questions", "✍️ Practice Questions"),
    ("more_resources", "📂 Study Resources"),
]


def _course_year_label(course: Course) -> str:
    return (getattr(course, "year_level", None) or "1st Year").strip()


def _course_year_short(course: Course) -> str:
    yr = _course_year_label(course)
    return YEAR_SHORT_TAG.get(yr, yr)


def _format_resource_badge(res: LearningResource) -> str:
    raw = (getattr(res, "text_content", None) or "").strip()
    m = _TAG_RE.match(raw)
    if m:
        cat = (m.group(1) or "").lower().removeprefix("res_")
        sub = (m.group(2) or "").strip()
        return f"[{cat}:{sub}]" if sub else f"[{cat}]"
    return ""


class CrudEditStates(StatesGroup):
    renaming_chapter = State()
    renaming_resource = State()
    searching_student = State()


def _paginate(items: list, page: int, page_size: int = PAGE_SIZE) -> tuple[list, int, int]:
    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    current = max(0, min(page, total_pages - 1))
    start = current * page_size
    return items[start : start + page_size], current, total_pages


def _pagination_row(prefix: str, current_page: int, total_pages: int, extra_args: str = "") -> list[InlineKeyboardButton]:
    if total_pages <= 1:
        return []
    suffix = f":{extra_args}" if extra_args else ""
    row: list[InlineKeyboardButton] = []
    if current_page > 0:
        row.append(InlineKeyboardButton(
            text="⬅️ Prev",
            callback_data=f"{prefix}:{current_page - 1}{suffix}",
        ))
    row.append(InlineKeyboardButton(
        text=f"📄 {current_page + 1}/{total_pages}",
        callback_data="no_op",
    ))
    if current_page < total_pages - 1:
        row.append(InlineKeyboardButton(
            text="Next ➡️",
            callback_data=f"{prefix}:{current_page + 1}{suffix}",
        ))
    return row


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup):
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


def crud_hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📚 Manage Courses & Years", callback_data="crud:courses:0"),
            InlineKeyboardButton(text="📖 Manage Chapters", callback_data="crud:chapters:0"),
        ],
        [
            InlineKeyboardButton(text="📄 Manage Resources/PDFs", callback_data="crud:resources:0"),
            InlineKeyboardButton(text="⏱ Manage Quizzes", callback_data="crud:quizzes:0"),
        ],
        [
            InlineKeyboardButton(text="🃏 Manage Flashcards", callback_data="crud:flashcards:0"),
            InlineKeyboardButton(text="👥 Manage Students", callback_data="crud:students:0"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Back to Control Center", callback_data="adm:home_pro"),
        ],
    ])


@admin_crud_router.callback_query(F.data == "adm:crud_hub")
async def cb_crud_hub(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await state.clear()
    await callback.answer()

    async with AsyncSessionLocal() as db:
        c_cnt = (await db.execute(select(func.count(Course.id)))).scalar() or 0
        ch_cnt = (await db.execute(select(func.count(Chapter.id)))).scalar() or 0
        r_cnt = (await db.execute(select(func.count(LearningResource.id)))).scalar() or 0
        q_cnt = (await db.execute(select(func.count(QuizQuestion.id)))).scalar() or 0
        f_cnt = (await db.execute(select(func.count(Flashcard.id)))).scalar() or 0
        s_cnt = (await db.execute(select(func.count(Student.telegram_id)))).scalar() or 0

    text = (
        "🛠 <b>QineSystems Full CRUD Console</b>\n\n"
        f"📊 <b>Live Database Summary:</b>\n"
        f"• Courses: <b>{c_cnt}</b> · Chapters: <b>{ch_cnt}</b>\n"
        f"• Resources: <b>{r_cnt}</b> · Quizzes: <b>{q_cnt}</b>\n"
        f"• Flashcards: <b>{f_cnt}</b> · Students: <b>{s_cnt}</b>\n\n"
        "Select a domain below to inspect, rename, update, or delete records:"
    )
    await _safe_edit(callback, text, crud_hub_kb())


# =====================================================================
# 1. COURSES CRUD (WITH YEAR LEVEL)
# =====================================================================
@admin_crud_router.callback_query(F.data == "crud:courses")
@admin_crud_router.callback_query(F.data.startswith("crud:courses:"))
async def cb_crud_courses(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return

    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    async with AsyncSessionLocal() as db:
        courses = (await db.execute(select(Course).order_by(Course.year_level, Course.name))).scalars().all()

    page_items, cur_page, total_pages = _paginate(courses, page)
    await callback.answer()

    buttons: list[list[InlineKeyboardButton]] = []
    for c in page_items:
        status = "🟢" if c.is_active else "🔴"
        yr_tag = _course_year_short(c)
        buttons.append([
            InlineKeyboardButton(text=f"{status} [{yr_tag}] {c.name[:18]}", callback_data="no_op"),
            InlineKeyboardButton(text="📅 Year", callback_data=f"crud_c_yr:{c.id}:{cur_page}"),
            InlineKeyboardButton(text="🔄", callback_data=f"crud_c_tog:{c.id}:{cur_page}"),
            InlineKeyboardButton(text="🗑", callback_data=f"crud_c_del:{c.id}:{cur_page}"),
        ])

    nav = _pagination_row("crud:courses", cur_page, total_pages)
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="⬅️ Back to CRUD Hub", callback_data="adm:crud_hub")])

    await _safe_edit(
        callback,
        f"📚 <b>Course Catalogue &amp; Year Management</b> (Page {cur_page + 1}/{total_pages})\n"
        "Tap <b>📅 Year</b> to cycle <code>1st → 2nd → 3rd Year</code>, <b>🔄</b> to toggle visibility, or <b>🗑</b> to delete:",
        InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@admin_crud_router.callback_query(F.data.startswith("crud_c_yr:"))
async def cb_crud_cycle_course_year(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    parts = callback.data.split(":")
    c_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    async with AsyncSessionLocal() as db:
        c = (await db.execute(select(Course).where(Course.id == c_id))).scalar_one_or_none()
        if not c:
            await callback.answer("Course not found.", show_alert=True)
            return
        cur_yr = _course_year_label(c)
        next_yr = (
            YEAR_CYCLE_ORDER[(YEAR_CYCLE_ORDER.index(cur_yr) + 1) % len(YEAR_CYCLE_ORDER)]
            if cur_yr in YEAR_CYCLE_ORDER
            else "1st Year"
        )
        c.year_level = next_yr
        await db.commit()
        cname = c.name

    await callback.answer(f"{cname} → {next_yr}", show_alert=False)
    callback.data = f"crud:courses:{page}"
    await cb_crud_courses(callback)


@admin_crud_router.callback_query(F.data.startswith("crud_c_tog:"))
async def cb_crud_toggle_course(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    parts = callback.data.split(":")
    c_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    async with AsyncSessionLocal() as db:
        c = (await db.execute(select(Course).where(Course.id == c_id))).scalar_one_or_none()
        if not c:
            await callback.answer("Course not found.", show_alert=True)
            return
        c.is_active = not c.is_active
        new_state = "Active" if c.is_active else "Hidden"
        await db.commit()

    await callback.answer(f"Course is now {new_state}.", show_alert=False)
    callback.data = f"crud:courses:{page}"
    await cb_crud_courses(callback)


@admin_crud_router.callback_query(F.data.startswith("crud_c_del:"))
async def cb_crud_course_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    parts = callback.data.split(":")
    c_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    async with AsyncSessionLocal() as db:
        c = (await db.execute(select(Course).where(Course.id == c_id))).scalar_one_or_none()
        if not c:
            await callback.answer("Course already removed.", show_alert=True)
            return
        try:
            chapter_ids = (await db.execute(select(Chapter.id).where(Chapter.course_id == c_id))).scalars().all()
            if chapter_ids:
                await db.execute(delete(QuizQuestion).where(QuizQuestion.chapter_id.in_(chapter_ids)))
                await db.execute(delete(Flashcard).where(Flashcard.chapter_id.in_(chapter_ids)))
                await db.execute(delete(LearningResource).where(LearningResource.chapter_id.in_(chapter_ids)))
            if hasattr(LearningResource, "course_id"):
                await db.execute(delete(LearningResource).where(LearningResource.course_id == c_id))
            await db.execute(delete(Chapter).where(Chapter.course_id == c_id))
            await db.delete(c)
            await db.commit()
        except SQLAlchemyError as exc:
            await db.rollback()
            await callback.answer(f"Delete failed: {exc.__class__.__name__}", show_alert=True)
            return

    await callback.answer("Course and linked items deleted.", show_alert=True)
    callback.data = f"crud:courses:{page}"
    await cb_crud_courses(callback)


# =====================================================================
# 2. CHAPTERS CRUD
# =====================================================================
@admin_crud_router.callback_query(F.data == "crud:chapters")
@admin_crud_router.callback_query(F.data.startswith("crud:chapters:"))
async def cb_crud_chapters(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return

    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    async with AsyncSessionLocal() as db:
        courses = (await db.execute(select(Course).order_by(Course.year_level, Course.name))).scalars().all()

    page_items, cur_page, total_pages = _paginate(courses, page, page_size=10)
    await callback.answer()

    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for c in page_items:
        row.append(
            InlineKeyboardButton(
                text=f"[{_course_year_short(c)}] {c.name[:16]}",
                callback_data=f"crud_ch_crs:{c.id}:0",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    nav = _pagination_row("crud:chapters", cur_page, total_pages)
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Back to CRUD Hub", callback_data="adm:crud_hub")])

    await _safe_edit(
        callback,
        f"📖 <b>Chapters Management</b> → Select a course (Page {cur_page + 1}/{total_pages}):",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@admin_crud_router.callback_query(F.data.startswith("crud_ch_crs:"))
async def cb_crud_chapters_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return

    parts = callback.data.split(":")
    course_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    async with AsyncSessionLocal() as db:
        course = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none()
        if not course:
            await callback.answer("Course not found.", show_alert=True)
            return
        chapters = (await db.execute(
            select(Chapter).where(Chapter.course_id == course_id).order_by(Chapter.chapter_number, Chapter.id)
        )).scalars().all()

        ch_summary: list[tuple[Chapter, int, int]] = []
        for ch in chapters:
            q_n = (await db.execute(
                select(func.count(QuizQuestion.id)).where(QuizQuestion.chapter_id == ch.id)
            )).scalar() or 0
            r_n = (await db.execute(
                select(func.count(LearningResource.id)).where(LearningResource.chapter_id == ch.id)
            )).scalar() or 0
            ch_summary.append((ch, q_n, r_n))

    yr_badge = html.escape(_course_year_label(course))
    page_items, cur_page, total_pages = _paginate(ch_summary, page, page_size=6)
    await callback.answer()

    if not ch_summary:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Back to Courses", callback_data="crud:chapters:0")],
            [InlineKeyboardButton(text="⬅️ Back to CRUD Hub", callback_data="adm:crud_hub")],
        ])
        await _safe_edit(
            callback,
            f"📖 <b>Chapters for {html.escape(course.name)}</b> (<i>{yr_badge}</i>)\n\nNo chapters found for this course.",
            kb,
        )
        return

    buttons: list[list[InlineKeyboardButton]] = []
    lines: list[str] = [
        f"📖 <b>Chapters for {html.escape(course.name)}</b> · <i>{yr_badge}</i> (Page {cur_page + 1}/{total_pages})\n"
    ]

    for ch, q_n, r_n in page_items:
        title_part = f" — {html.escape(ch.title)}" if getattr(ch, "title", None) else ""
        lines.append(f"• <b>Ch {ch.chapter_number}</b>{title_part} <i>({q_n}Q, {r_n}R)</i>")
        buttons.append([
            InlineKeyboardButton(text=f"✏️ Rename Ch {ch.chapter_number}", callback_data=f"crud_ch_ren:{ch.id}:{course_id}:{cur_page}"),
            InlineKeyboardButton(text=f"🗑 Delete Ch {ch.chapter_number}", callback_data=f"crud_ch_del:{ch.id}:{course_id}:{cur_page}"),
        ])

    nav = _pagination_row("crud_ch_crs", cur_page, total_pages, extra_args=str(course_id))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Courses", callback_data="crud:chapters:0")])
    buttons.append([InlineKeyboardButton(text="⬅️ Back to CRUD Hub", callback_data="adm:crud_hub")])

    await _safe_edit(callback, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


@admin_crud_router.callback_query(F.data.startswith("crud_ch_ren:"))
async def cb_crud_chapter_rename_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    parts = callback.data.split(":")
    ch_id = int(parts[1])
    course_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0

    await callback.answer()
    await state.set_state(CrudEditStates.renaming_chapter)
    await state.update_data(ren_ch_id=ch_id, ren_course_id=course_id, ren_page=page)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Cancel", callback_data=f"crud_ch_crs:{course_id}:{page}")]
    ])
    await callback.message.edit_text(
        "✏️ <b>Rename Chapter</b>\n\n"
        "Send the new chapter title below:",
        reply_markup=kb,
        parse_mode="HTML",
    )


@admin_crud_router.message(CrudEditStates.renaming_chapter)
async def msg_crud_chapter_rename_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    new_title = (message.text or "").strip()
    if not new_title:
        await message.answer("Please send a non-empty chapter title.", parse_mode="HTML")
        return

    data = await state.get_data()
    ch_id = int(data.get("ren_ch_id", 0))
    course_id = int(data.get("ren_course_id", 0))
    page = int(data.get("ren_page", 0))

    async with AsyncSessionLocal() as db:
        ch = (await db.execute(select(Chapter).where(Chapter.id == ch_id))).scalar_one_or_none()
        if not ch:
            await state.clear()
            await message.answer("Chapter not found.", parse_mode="HTML")
            return
        ch.title = new_title[:120]
        await db.commit()

    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Back to Chapters", callback_data=f"crud_ch_crs:{course_id}:{page}")],
        [InlineKeyboardButton(text="🛠 Back to CRUD Hub", callback_data="adm:crud_hub")],
    ])
    await message.answer(
        f"✅ Chapter updated to: <b>{html.escape(new_title[:120])}</b>",
        reply_markup=kb,
        parse_mode="HTML",
    )


@admin_crud_router.callback_query(F.data.startswith("crud_ch_del:"))
async def cb_crud_chapter_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    parts = callback.data.split(":")
    ch_id = int(parts[1])
    course_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0

    async with AsyncSessionLocal() as db:
        ch = (await db.execute(select(Chapter).where(Chapter.id == ch_id))).scalar_one_or_none()
        if not ch:
            await callback.answer("Chapter already deleted.", show_alert=True)
            callback.data = f"crud_ch_crs:{course_id}:{page}"
            await cb_crud_chapters_list(callback)
            return

        try:
            await db.execute(delete(QuizQuestion).where(QuizQuestion.chapter_id == ch_id))
            await db.execute(delete(Flashcard).where(Flashcard.chapter_id == ch_id))
            await db.execute(delete(LearningResource).where(LearningResource.chapter_id == ch_id))
            await db.delete(ch)
            await db.commit()
        except SQLAlchemyError as exc:
            await db.rollback()
            await callback.answer(f"Failed to delete chapter: {exc.__class__.__name__}", show_alert=True)
            return

    await callback.answer("Chapter and linked items deleted!", show_alert=True)
    callback.data = f"crud_ch_crs:{course_id}:{page}"
    await cb_crud_chapters_list(callback)


# =====================================================================
# 3. ALL RESOURCES & PDFs CRUD (Every Resource Type + Course / Direct)
# =====================================================================
async def _build_resource_types_list(db) -> list[tuple[str, str]]:
    types_dict: dict[str, str] = dict(BASE_RESOURCE_TYPES)
    feat_rows = (await db.execute(select(FeatureConfig))).scalars().all()
    skip_keys = {"quiz", "flashcards", "search", "ai_assistant", "study_tools"}
    for f in feat_rows:
        k = (f.key or "").strip().lower()
        if k and k not in skip_keys and k not in types_dict:
            types_dict[k] = f.title or k

    res_keys = (await db.execute(select(LearningResource.feature_key).distinct())).scalars().all()
    for rk in res_keys:
        k = (rk or "").strip().lower()
        if k and k not in types_dict:
            types_dict[k] = f"📄 {k.replace('_', ' ').title()}"

    ordered = list(types_dict.items())
    ordered.append(("all", "📦 All Resource Types"))
    return ordered


async def _collect_resources_for_type_and_course(
    db,
    type_key: str,
    course_id: int | None = None,
    chapter_to_course: dict[int, int] | None = None,
) -> list[LearningResource]:
    stmt = select(LearningResource).order_by(LearningResource.id.desc())
    if type_key != "all":
        stmt = stmt.where(LearningResource.feature_key == type_key)

    all_res = (await db.execute(stmt)).scalars().all()
    if course_id is None:
        return list(all_res)

    if chapter_to_course is None:
        chaps = (await db.execute(select(Chapter))).scalars().all()
        chapter_to_course = {ch.id: ch.course_id for ch in chaps}

    matched: list[LearningResource] = []
    for r in all_res:
        direct_cid = getattr(r, "course_id", None)
        mapped_cid = chapter_to_course.get(r.chapter_id) if r.chapter_id is not None else None
        effective_cid = direct_cid if direct_cid is not None else mapped_cid
        if effective_cid == course_id:
            matched.append(r)
    return matched


@admin_crud_router.callback_query(F.data.in_(["crud:resources", "crud:resources:0"]))
async def cb_crud_resources_type_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Admin access required.", show_alert=True)
        return

    await callback.answer()

    async with AsyncSessionLocal() as db:
        all_res = (await db.execute(select(LearningResource))).scalars().all()
        resource_types = await _build_resource_types_list(db)

    counts_by_type: dict[str, int] = {}
    for r in all_res:
        k = (r.feature_key or "").strip().lower()
        counts_by_type[k] = counts_by_type.get(k, 0) + 1

    buttons: list[list[InlineKeyboardButton]] = []
    for t_key, t_label in resource_types:
        cnt = len(all_res) if t_key == "all" else counts_by_type.get(t_key, 0)
        buttons.append([
            InlineKeyboardButton(
                text=f"{t_label} ({cnt})",
                callback_data=f"crud_rt:{t_key}:0",
            )
        ])

    buttons.append([InlineKeyboardButton(text="⬅️ Back to CRUD Hub", callback_data="adm:crud_hub")])

    await _safe_edit(
        callback,
        "📄 <b>Manage Resources / PDFs</b>\n"
        "Step 1 → Choose a <b>Resource Type</b> (COC applies to 1st Year only):",
        InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@admin_crud_router.callback_query(F.data.startswith("crud_rt:"))
async def cb_crud_resource_courses(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return

    parts = callback.data.split(":")
    type_key = parts[1] if len(parts) > 1 else "all"
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    async with AsyncSessionLocal() as db:
        resource_types = await _build_resource_types_list(db)
        type_label = dict(resource_types).get(type_key, type_key)
        courses = (await db.execute(select(Course).order_by(Course.year_level, Course.name))).scalars().all()
        type_resources = await _collect_resources_for_type_and_course(db, type_key, course_id=None)

        chapters = (await db.execute(select(Chapter))).scalars().all()
        chapter_to_course = {ch.id: ch.course_id for ch in chapters}

    count_per_course: dict[int, int] = {}
    unassigned_cnt = 0
    for r in type_resources:
        cid = getattr(r, "course_id", None)
        if cid is None and r.chapter_id is not None:
            cid = chapter_to_course.get(r.chapter_id)
        if cid is not None:
            count_per_course[cid] = count_per_course.get(cid, 0) + 1
        else:
            unassigned_cnt += 1

    page_courses, cur_page, total_pages = _paginate(courses, page, page_size=10)
    await callback.answer()

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text=f"📋 All {type_label} ({len(type_resources)})",
            callback_data=f"crud_rc:{type_key}:-1:0",
        )]
    ]
    if unassigned_cnt > 0:
        rows.append([InlineKeyboardButton(
            text=f"📁 General / Hub Items ({unassigned_cnt})",
            callback_data=f"crud_rc:{type_key}:0:0",
        )])

    row: list[InlineKeyboardButton] = []
    for c in page_courses:
        c_cnt = count_per_course.get(c.id, 0)
        yr_tag = _course_year_short(c)
        row.append(
            InlineKeyboardButton(
                text=f"[{yr_tag}] {c.name[:13]} ({c_cnt})",
                callback_data=f"crud_rc:{type_key}:{c.id}:0",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    if total_pages > 1:
        nav_row: list[InlineKeyboardButton] = []
        if cur_page > 0:
            nav_row.append(InlineKeyboardButton(
                text="⬅️ Prev",
                callback_data=f"crud_rt:{type_key}:{cur_page - 1}",
            ))
        nav_row.append(InlineKeyboardButton(text=f"📄 {cur_page + 1}/{total_pages}", callback_data="no_op"))
        if cur_page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(
                text="Next ➡️",
                callback_data=f"crud_rt:{type_key}:{cur_page + 1}",
            ))
        rows.append(nav_row)

    rows.append([InlineKeyboardButton(text="⬅️ Change Resource Type", callback_data="crud:resources")])
    rows.append([InlineKeyboardButton(text="⬅️ Back to CRUD Hub", callback_data="adm:crud_hub")])

    coc_note = "\n<i>⚠️ COC Past Exams apply to 1st Year only.</i>" if type_key == "coc" else ""
    await _safe_edit(
        callback,
        f"📄 <b>{type_label}</b> → <b>Select Scope / Course</b> (Page {cur_page + 1}/{total_pages}){coc_note}\n"
        "View all files in this type directly or pick a course:",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@admin_crud_router.callback_query(F.data.startswith("crud_rc:"))
async def cb_crud_course_resources_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return

    parts = callback.data.split(":")
    type_key = parts[1] if len(parts) > 1 else "all"
    course_id = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else -1
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0

    async with AsyncSessionLocal() as db:
        resource_types = await _build_resource_types_list(db)
        type_label = dict(resource_types).get(type_key, type_key)
        chapters = (await db.execute(select(Chapter))).scalars().all()
        chapter_to_course = {ch.id: ch.course_id for ch in chapters}

        if course_id == -1:
            course_name = "All Courses"
            resources = await _collect_resources_for_type_and_course(db, type_key, course_id=None)
        elif course_id == 0:
            course_name = "General / Hub"
            all_t = await _collect_resources_for_type_and_course(db, type_key, course_id=None)
            resources = [
                r for r in all_t
                if (getattr(r, "course_id", None) is None and chapter_to_course.get(r.chapter_id) is None)
            ]
        else:
            course = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none()
            course_name = f"{course.name} ({_course_year_label(course)})" if course else f"Course #{course_id}"
            resources = await _collect_resources_for_type_and_course(
                db, type_key, course_id=course_id, chapter_to_course=chapter_to_course
            )

    page_items, cur_page, total_pages = _paginate(resources, page, page_size=6)
    await callback.answer()

    buttons: list[list[InlineKeyboardButton]] = []
    lines: list[str] = [
        f"📄 <b>{type_label}</b> → <b>{html.escape(course_name)}</b>",
        f"Files: <b>{len(resources)}</b> · Page <b>{cur_page + 1}/{total_pages}</b>\n",
    ]

    if not page_items:
        lines.append("<i>No files found under this resource type for this selection.</i>")
    else:
        for idx, res in enumerate(page_items, start=cur_page * 6 + 1):
            pub_icon = "🟢" if getattr(res, "is_published", True) else "⚪"
            badge = _format_resource_badge(res)
            badge_str = f" <code>{html.escape(badge)}</code>" if badge else ""
            lines.append(f"{idx}. {pub_icon} <b>{html.escape(res.title)}</b>{badge_str}")
            buttons.append([
                InlineKeyboardButton(
                    text=f"✏️ Rename #{idx}",
                    callback_data=f"crud_rren:{res.id}:{type_key}:{course_id}:{cur_page}",
                ),
                InlineKeyboardButton(
                    text=("🙈 Hide" if getattr(res, "is_published", True) else "👁 Publish"),
                    callback_data=f"crud_rtog:{res.id}:{type_key}:{course_id}:{cur_page}",
                ),
                InlineKeyboardButton(
                    text="🗑 Delete",
                    callback_data=f"crud_rdel:{res.id}:{type_key}:{course_id}:{cur_page}",
                ),
            ])

    if total_pages > 1:
        nav_row: list[InlineKeyboardButton] = []
        if cur_page > 0:
            nav_row.append(InlineKeyboardButton(
                text="⬅️ Prev",
                callback_data=f"crud_rc:{type_key}:{course_id}:{cur_page - 1}",
            ))
        nav_row.append(InlineKeyboardButton(text=f"📄 {cur_page + 1}/{total_pages}", callback_data="no_op"))
        if cur_page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(
                text="Next ➡️",
                callback_data=f"crud_rc:{type_key}:{course_id}:{cur_page + 1}",
            ))
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="⬅️ Back to Courses", callback_data=f"crud_rt:{type_key}:0")])
    buttons.append([InlineKeyboardButton(text="⬅️ Back to CRUD Hub", callback_data="adm:crud_hub")])

    await _safe_edit(callback, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


@admin_crud_router.callback_query(F.data.startswith("crud_rtog:"))
async def cb_crud_resource_toggle_publish(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return

    _, res_id_s, type_key, course_id_s, page_s = callback.data.split(":")
    res_id, course_id, page = int(res_id_s), int(course_id_s), int(page_s)

    async with AsyncSessionLocal() as db:
        res = (await db.execute(select(LearningResource).where(LearningResource.id == res_id))).scalar_one_or_none()
        if not res:
            await callback.answer("Resource not found.", show_alert=True)
            return
        res.is_published = not bool(getattr(res, "is_published", True))
        new_status = "Published" if res.is_published else "Hidden"
        await db.commit()

    await callback.answer(f"File is now {new_status}.", show_alert=False)
    callback.data = f"crud_rc:{type_key}:{course_id}:{page}"
    await cb_crud_course_resources_list(callback)


@admin_crud_router.callback_query(F.data.startswith("crud_rren:"))
async def cb_crud_resource_rename_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return

    _, res_id_s, type_key, course_id_s, page_s = callback.data.split(":")
    res_id, course_id, page = int(res_id_s), int(course_id_s), int(page_s)

    await callback.answer()
    await state.set_state(CrudEditStates.renaming_resource)
    await state.update_data(
        ren_res_id=res_id,
        ren_res_type=type_key,
        ren_res_course_id=course_id,
        ren_res_page=page,
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Cancel", callback_data=f"crud_rc:{type_key}:{course_id}:{page}")]
    ])
    await callback.message.edit_text(
        "✏️ <b>Rename Resource File</b>\n\n"
        "Send the new title for this resource below:",
        reply_markup=kb,
        parse_mode="HTML",
    )


@admin_crud_router.message(CrudEditStates.renaming_resource)
async def msg_crud_resource_rename_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    new_title = (message.text or "").strip()
    if not new_title:
        await message.answer("Please enter a valid non-empty title.", parse_mode="HTML")
        return

    data = await state.get_data()
    res_id = int(data.get("ren_res_id", 0))
    type_key = str(data.get("ren_res_type", "all"))
    course_id = int(data.get("ren_res_course_id", -1))
    page = int(data.get("ren_res_page", 0))

    async with AsyncSessionLocal() as db:
        res = (await db.execute(select(LearningResource).where(LearningResource.id == res_id))).scalar_one_or_none()
        if not res:
            await state.clear()
            await message.answer("Resource not found.", parse_mode="HTML")
            return
        res.title = new_title[:160]
        await db.commit()

    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Back to Files", callback_data=f"crud_rc:{type_key}:{course_id}:{page}")],
        [InlineKeyboardButton(text="🛠 Back to CRUD Hub", callback_data="adm:crud_hub")],
    ])
    await message.answer(
        f"✅ Updated file title to: <b>{html.escape(new_title[:160])}</b>",
        reply_markup=kb,
        parse_mode="HTML",
    )


@admin_crud_router.callback_query(F.data.startswith("crud_rdel:"))
async def cb_crud_resource_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return

    _, res_id_s, type_key, course_id_s, page_s = callback.data.split(":")
    res_id, course_id, page = int(res_id_s), int(course_id_s), int(page_s)

    async with AsyncSessionLocal() as db:
        res = (await db.execute(select(LearningResource).where(LearningResource.id == res_id))).scalar_one_or_none()
        if res:
            await db.delete(res)
            await db.commit()

    await callback.answer("Resource deleted!", show_alert=True)
    callback.data = f"crud_rc:{type_key}:{course_id}:{page}"
    await cb_crud_course_resources_list(callback)


# =====================================================================
# 4. QUIZZES CRUD
# =====================================================================
@admin_crud_router.callback_query(F.data == "crud:quizzes")
@admin_crud_router.callback_query(F.data.startswith("crud:quizzes:"))
async def cb_crud_quizzes(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    async with AsyncSessionLocal() as db:
        quizzes = (await db.execute(
            select(QuizQuestion, Chapter, Course)
            .join(Chapter, QuizQuestion.chapter_id == Chapter.id)
            .join(Course, Chapter.course_id == Course.id)
            .order_by(QuizQuestion.id.desc())
        )).all()

    page_items, cur_page, total_pages = _paginate(quizzes, page)
    await callback.answer()

    if not quizzes:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Back to CRUD Hub", callback_data="adm:crud_hub")]
        ])
        await _safe_edit(callback, "⏱ <b>Manage Quiz Questions</b>\n\nNo quiz questions found.", kb)
        return

    buttons = [
        [InlineKeyboardButton(
            text=f"[{crs.name[:12]}] {q.question_text[:20]}... 🗑",
            callback_data=f"crud_qz_del:{q.id}:{cur_page}",
        )]
        for q, ch, crs in page_items
    ]
    nav = _pagination_row("crud:quizzes", cur_page, total_pages)
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="⬅️ Back to CRUD Hub", callback_data="adm:crud_hub")])

    await _safe_edit(
        callback,
        f"⏱ <b>Manage Quiz Questions</b> (Page {cur_page + 1}/{total_pages})",
        InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@admin_crud_router.callback_query(F.data.startswith("crud_qz_del:"))
async def cb_crud_qz_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    parts = callback.data.split(":")
    qz_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    async with AsyncSessionLocal() as db:
        q = (await db.execute(select(QuizQuestion).where(QuizQuestion.id == qz_id))).scalar_one_or_none()
        if q:
            await db.delete(q)
            await db.commit()

    await callback.answer("Quiz question deleted!", show_alert=False)
    callback.data = f"crud:quizzes:{page}"
    await cb_crud_quizzes(callback)


# =====================================================================
# 5. FLASHCARDS CRUD
# =====================================================================
@admin_crud_router.callback_query(F.data == "crud:flashcards")
@admin_crud_router.callback_query(F.data.startswith("crud:flashcards:"))
async def cb_crud_flashcards(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    async with AsyncSessionLocal() as db:
        cards = (await db.execute(
            select(Flashcard, Chapter, Course)
            .join(Chapter, Flashcard.chapter_id == Chapter.id)
            .join(Course, Chapter.course_id == Course.id)
            .order_by(Flashcard.id.desc())
        )).all()

    page_items, cur_page, total_pages = _paginate(cards, page)
    await callback.answer()

    if not cards:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Back to CRUD Hub", callback_data="adm:crud_hub")]
        ])
        await _safe_edit(callback, "🃏 <b>Manage Flashcards</b>\n\nNo flashcards found in the database.", kb)
        return

    buttons = [
        [InlineKeyboardButton(
            text=f"[{crs.name[:12]}] {fc.front_text[:20]}... 🗑",
            callback_data=f"crud_fc_del:{fc.id}:{cur_page}",
        )]
        for fc, ch, crs in page_items
    ]
    nav = _pagination_row("crud:flashcards", cur_page, total_pages)
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="⬅️ Back to CRUD Hub", callback_data="adm:crud_hub")])

    await _safe_edit(
        callback,
        f"🃏 <b>Manage Flashcards</b> (Page {cur_page + 1}/{total_pages})",
        InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@admin_crud_router.callback_query(F.data.startswith("crud_fc_del:"))
async def cb_crud_fc_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    parts = callback.data.split(":")
    fc_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    async with AsyncSessionLocal() as db:
        fc = (await db.execute(select(Flashcard).where(Flashcard.id == fc_id))).scalar_one_or_none()
        if fc:
            await db.delete(fc)
            await db.commit()

    await callback.answer("Flashcard deleted!", show_alert=False)
    callback.data = f"crud:flashcards:{page}"
    await cb_crud_flashcards(callback)


# =====================================================================
# 6. STUDENTS CRUD
# =====================================================================
@admin_crud_router.callback_query(F.data == "crud:students")
@admin_crud_router.callback_query(F.data.startswith("crud:students:"))
async def cb_crud_students(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    async with AsyncSessionLocal() as db:
        students = (await db.execute(select(Student).order_by(Student.created_at.desc()))).scalars().all()

    page_items, cur_page, total_pages = _paginate(students, page)
    await callback.answer()

    buttons: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🔎 Search Student by ID / Name", callback_data="crud_stu_search")]
    ]
    for s in page_items:
        display_name = s.full_name or f"User {s.telegram_id}"
        buttons.append([
            InlineKeyboardButton(
                text=f"👤 {display_name[:22]} [{s.plan_code}]",
                callback_data=f"crud_stu_view:{s.telegram_id}",
            )
        ])

    nav = _pagination_row("crud:students", cur_page, total_pages)
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="⬅️ Back to CRUD Hub", callback_data="adm:crud_hub")])

    await _safe_edit(
        callback,
        f"👥 <b>Manage Students</b> (Page {cur_page + 1}/{total_pages})",
        InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@admin_crud_router.callback_query(F.data == "crud_stu_search")
async def cb_crud_student_search_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    await callback.answer()
    await state.set_state(CrudEditStates.searching_student)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Back to Students", callback_data="crud:students:0")]
    ])
    await callback.message.edit_text(
        "🔎 <b>Search Student</b>\n\nSend a student's Telegram ID or full name:",
        reply_markup=kb,
        parse_mode="HTML",
    )


@admin_crud_router.message(CrudEditStates.searching_student)
async def msg_crud_student_search_result(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    query_str = (message.text or "").strip()
    await state.clear()

    async with AsyncSessionLocal() as db:
        matched: list[Student] = []
        if query_str.isdigit():
            by_id = (await db.execute(select(Student).where(Student.telegram_id == int(query_str)))).scalar_one_or_none()
            if by_id:
                matched = [by_id]
        if not matched:
            all_st = (await db.execute(select(Student))).scalars().all()
            matched = [s for s in all_st if query_str.lower() in (s.full_name or "").lower()][:10]

    if not matched:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Back to Students", callback_data="crud:students:0")]
        ])
        await message.answer(f"No student matched <code>{html.escape(query_str)}</code>.", reply_markup=kb, parse_mode="HTML")
        return

    buttons = [
        [InlineKeyboardButton(text=f"👤 {s.full_name or s.telegram_id} [{s.plan_code}]", callback_data=f"crud_stu_view:{s.telegram_id}")]
        for s in matched
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Students", callback_data="crud:students:0")])
    await message.answer("🔎 <b>Matching Students:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


@admin_crud_router.callback_query(F.data.startswith("crud_stu_view:"))
async def cb_crud_student_detail(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    st_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as db:
        s = (await db.execute(select(Student).where(Student.telegram_id == st_id))).scalar_one_or_none()
        referred = (await db.execute(
            select(Student).where(Student.referred_by_id == st_id)
        )).scalars().all()

    if not s:
        await callback.answer("Student not found.", show_alert=True)
        return

    await callback.answer()
    invited_cnt = len(referred)
    active_cnt = sum(
        1 for r in referred
        if r.plan_code in ("QINE_PLUS", "QINE_PRO")
        and (not r.subscription_expires_at or r.subscription_expires_at > datetime.utcnow())
    )
    reward_etb = active_cnt * REFERRAL_REWARD_ETB

    exp_str = s.subscription_expires_at.strftime("%Y-%m-%d") if s.subscription_expires_at else "N/A"
    uname_str = f"@{s.username}" if getattr(s, "username", None) else "—"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟢 Free", callback_data=f"crud_stu_plan:{s.telegram_id}:FREE"),
            InlineKeyboardButton(text="💎 Plus", callback_data=f"crud_stu_plan:{s.telegram_id}:QINE_PLUS"),
            InlineKeyboardButton(text="👑 Pro", callback_data=f"crud_stu_plan:{s.telegram_id}:QINE_PRO"),
        ],
        [InlineKeyboardButton(text="⬅️ Back to Students", callback_data="crud:students:0")],
    ])

    await _safe_edit(
        callback,
        f"👤 <b>Student Profile</b>\n\n"
        f"• <b>Name:</b> {html.escape(s.full_name or '—')}\n"
        f"• <b>Username:</b> {html.escape(uname_str)}\n"
        f"• <b>Telegram ID:</b> <code>{s.telegram_id}</code>\n"
        f"• <b>Plan:</b> <code>{s.plan_code}</code>\n"
        f"• <b>Expires:</b> <code>{exp_str}</code>\n"
        f"• <b>Referrals:</b> <b>{invited_cnt}</b> invited · <b>{active_cnt}</b> active (<b>{reward_etb} ETB</b>)",
        kb,
    )


@admin_crud_router.callback_query(F.data.startswith("crud_stu_plan:"))
async def cb_crud_student_set_plan(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Unauthorized.", show_alert=True)
        return
    _, st_id_s, new_plan = callback.data.split(":")
    st_id = int(st_id_s)

    async with AsyncSessionLocal() as db:
        s = (await db.execute(select(Student).where(Student.telegram_id == st_id))).scalar_one_or_none()
        if not s:
            await callback.answer("Student not found.", show_alert=True)
            return
        s.plan_code = new_plan
        s.subscription_expires_at = datetime.utcnow() + timedelta(days=365) if new_plan != "FREE" else None
        await db.commit()

    await callback.answer(f"Plan updated to {new_plan}!", show_alert=True)
    callback.data = f"crud_stu_view:{st_id}"
    await cb_crud_student_detail(callback)