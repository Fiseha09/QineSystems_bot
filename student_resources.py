import html
import inspect
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from database import AsyncSessionLocal
from models import Course
from services import get_or_create_student, check_feature_entitlement
from resource_catalog import (
    RESOURCE_CATEGORIES,
    COC_TRACKS,
    fetch_filtered_resources,
    get_serialized_resource,
    list_active_courses,
)

student_res_router = Router()

STUDENT_COURSES_PER_PAGE = 6
_ATIRERA_CHAPTER_CACHE: dict[tuple[int, int], list[str]] = {}

YEAR_LABELS: dict[int, str] = {
    1: "1st Year",
    2: "2nd Year",
    3: "3rd Year",
}

YEAR_HEADERS: dict[int, str] = {
    1: "🎓 1st Year (Freshman)",
    2: "📘 2nd Year",
    3: "📙 3rd Year",
}


def _parse_year(val: str | int | None) -> int | None:
    if val is None or val == "" or val == "0":
        return None
    try:
        num = int(val)
        return num if num in YEAR_LABELS else None
    except (TypeError, ValueError):
        return None


async def _courses_for_year(year_num: int | None) -> list:
    if not year_num:
        return await list_active_courses()

    year_label = YEAR_LABELS[year_num]
    sig = inspect.signature(list_active_courses)
    if "year_level" in sig.parameters:
        courses = await list_active_courses(year_level=year_label)
    else:
        courses = await list_active_courses()

    filtered = [
        c for c in courses
        if getattr(c, "year_level", None) in (None, "", year_label)
    ]
    return filtered if filtered else list(courses)


def _nav_back_home(back_cb: str) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text="⬅️ Back", callback_data=back_cb),
        InlineKeyboardButton(text="🏠 Main Menu", callback_data="nav:main"),
    ]


def _premium_gate_kb(back_cb: str = "nav:resources_hub") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👑 View Premium Membership & Upgrade", callback_data="nav:membership")],
        _nav_back_home(back_cb),
    ])


async def _edit_or_send(callback: CallbackQuery, text: str, kb: InlineKeyboardMarkup):
    try:
        await callback.message.edit_text(
            text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True
        )
    except Exception:
        await callback.message.answer(
            text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True
        )


async def _check_or_block_study_resources(
    callback: CallbackQuery,
    selection_label: str,
    back_cb: str,
) -> bool:
    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
        allowed, feature = await check_feature_entitlement(db, student, "more_resources")
        if not feature or not feature.is_enabled:
            await callback.answer("Study Resources is currently disabled.", show_alert=True)
            return False
        if not allowed:
            await callback.answer()
            await _edit_or_send(
                callback,
                f"🔒 <b>Premium Membership Required</b>\n\n"
                f"📌 <b>Selection:</b> <b>{html.escape(selection_label)}</b>\n\n"
                "An active <b>Qine Plus</b> or <b>Qine Pro</b> subscription is required to view "
                "and access files in <b>Study Resources</b>.",
                _premium_gate_kb(back_cb),
            )
            return False
    return True


def _build_year_resources_kb(year_num: int | None) -> InlineKeyboardMarkup:
    y_tag = f":{year_num}" if year_num else ""
    back_cb = f"yr:{year_num}" if year_num else "nav:main"

    rows: list[list[InlineKeyboardButton]] = []
    if year_num in (None, 1):
        rows.append([
            InlineKeyboardButton(text="🎓 COC Past Exams", callback_data=f"res_cat:coc{y_tag}"),
            InlineKeyboardButton(text="📝 Worksheets", callback_data=f"res_cat:worksheet{y_tag}"),
        ])
    else:
        rows.append([
            InlineKeyboardButton(text="📝 Worksheets", callback_data=f"res_cat:worksheet{y_tag}"),
        ])

    rows.append([
        InlineKeyboardButton(text="📚 አጤሬራ", callback_data=f"res_cat:atirera{y_tag}"),
        InlineKeyboardButton(text="📖 Reference Books", callback_data=f"res_cat:refbook{y_tag}"),
    ])
    rows.append(_nav_back_home(back_cb))
    return InlineKeyboardMarkup(inline_keyboard=rows)


@student_res_router.callback_query(F.data.startswith("yr_res:"))
async def cb_year_resources_hub(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    year_num = _parse_year(callback.data.split(":", 1)[1]) or 1
    year_title = YEAR_HEADERS[year_num]
    year_label = YEAR_LABELS[year_num]

    await callback.answer()
    coc_line = (
        "• <b>COC Past Exams:</b> Health, Law &amp; Architecture (1st Year)\n"
        if year_num == 1
        else ""
    )
    await _edit_or_send(
        callback,
        f"📂 <b>{year_title} · Study Resources</b>\n\n"
        f"Choose a resource library for <b>{year_label}</b>:\n"
        f"{coc_line}"
        f"• <b>Worksheets:</b> {year_label} course practice sheets &amp; files\n"
        f"• <b>አጤሬራ:</b> {year_label} course chapters &amp; study guides\n"
        f"• <b>Reference Books:</b> {year_label} course textbooks &amp; references",
        _build_year_resources_kb(year_num),
    )


@student_res_router.callback_query(F.data == "nav:resources_hub")
async def cb_resources_hub(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await _edit_or_send(
        callback,
        "📂 <b>QineSystems Study Resources</b>\n\n"
        "Choose a resource library below:\n"
        "• <b>COC Past Exams:</b> Health, Law &amp; Architecture (1st Year)\n"
        "• <b>Worksheets:</b> Course practice sheets &amp; files\n"
        "• <b>አጤሬራ:</b> Course chapters &amp; study files\n"
        "• <b>Reference Books:</b> Course reference books",
        _build_year_resources_kb(None),
    )


def _student_course_kb(
    courses: list,
    cat: str,
    page: int,
    year_num: int | None = None,
) -> InlineKeyboardMarkup:
    total_pages = max(1, (len(courses) + STUDENT_COURSES_PER_PAGE - 1) // STUDENT_COURSES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    chunk = courses[page * STUDENT_COURSES_PER_PAGE : (page + 1) * STUDENT_COURSES_PER_PAGE]
    y_suffix = f":{year_num}" if year_num else ""
    back_cb = f"yr_res:{year_num}" if year_num else "nav:resources_hub"

    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(chunk), 2):
        c1 = chunk[i]
        row = [
            InlineKeyboardButton(
                text=c1.name[:22],
                callback_data=f"res_crs:{cat}:{c1.id}{y_suffix}",
            )
        ]
        if i + 1 < len(chunk):
            c2 = chunk[i + 1]
            row.append(
                InlineKeyboardButton(
                    text=c2.name[:22],
                    callback_data=f"res_crs:{cat}:{c2.id}{y_suffix}",
                )
            )
        rows.append(row)

    if total_pages > 1:
        pager: list[InlineKeyboardButton] = []
        if page > 0:
            pager.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=f"stu_crs_pg:{cat}:{page - 1}{y_suffix}",
                )
            )
        pager.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            pager.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=f"stu_crs_pg:{cat}:{page + 1}{y_suffix}",
                )
            )
        rows.append(pager)

    rows.append(_nav_back_home(back_cb))
    return InlineKeyboardMarkup(inline_keyboard=rows)


@student_res_router.callback_query(F.data.startswith("stu_crs_pg:"))
async def cb_stu_crs_page(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Invalid page.", show_alert=True)
        return

    _, cat, page_s = parts[:3]
    year_num = _parse_year(parts[3]) if len(parts) >= 4 else None
    page = int(page_s) if page_s.isdigit() else 0

    await callback.answer()
    courses = await _courses_for_year(year_num)
    label = RESOURCE_CATEGORIES.get(cat, "Resources")
    yr_tag = f" · {YEAR_LABELS[year_num]}" if year_num else ""
    await _edit_or_send(
        callback,
        f"<b>{html.escape(label)}{html.escape(yr_tag)} — Select Course</b>",
        _student_course_kb(courses, cat, page, year_num=year_num),
    )


@student_res_router.callback_query(F.data.startswith("res_cat:"))
async def cb_res_category(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    parts = callback.data.split(":")
    cat = parts[1] if len(parts) >= 2 else ""
    year_num = _parse_year(parts[2]) if len(parts) >= 3 else None

    if cat == "coc" and year_num not in (None, 1):
        await callback.answer("COC Past Exams are available for 1st Year only.", show_alert=True)
        return

    await callback.answer()
    y_suffix = f":{year_num}" if year_num else ""
    back_cb = f"yr_res:{year_num}" if year_num else "nav:resources_hub"

    if cat == "coc":
        rows = [
            [InlineKeyboardButton(text=label, callback_data=f"res_trk:{key}{y_suffix}")]
            for key, label in COC_TRACKS
        ]
        rows.append(_nav_back_home(back_cb))
        await _edit_or_send(
            callback,
            "🎓 <b>COC Past Exams (1st Year) — Select Track</b>\nChoose your COC field:",
            InlineKeyboardMarkup(inline_keyboard=rows),
        )
        return

    courses = await _courses_for_year(year_num)
    label = RESOURCE_CATEGORIES.get(cat, "Resources")
    yr_tag = f" · {YEAR_LABELS[year_num]}" if year_num else ""
    await _edit_or_send(
        callback,
        f"<b>{html.escape(label)}{html.escape(yr_tag)} — Select Course</b>",
        _student_course_kb(courses, cat, 0, year_num=year_num),
    )


@student_res_router.callback_query(F.data.startswith("res_trk:"))
async def cb_coc_track_files(callback: CallbackQuery):
    parts = callback.data.split(":")
    track_key = parts[1] if len(parts) >= 2 else ""
    year_num = _parse_year(parts[2]) if len(parts) >= 3 else None

    if year_num not in (None, 1):
        await callback.answer("COC Past Exams are available for 1st Year only.", show_alert=True)
        return

    y_suffix = f":{year_num}" if year_num else ""
    back_cb = f"res_cat:coc{y_suffix}"
    raw_label = dict(COC_TRACKS).get(track_key, track_key.title())
    clean_track = raw_label.split(" ", 1)[1] if " " in raw_label else raw_label

    if not await _check_or_block_study_resources(
        callback,
        selection_label=f"COC Past Exams · {raw_label}",
        back_cb=back_cb,
    ):
        return

    await callback.answer()
    items = await fetch_filtered_resources("coc", course_id=0, track_or_chapter=clean_track)

    rows = [
        [InlineKeyboardButton(text=f"📄 {r['title'][:34]}", callback_data=f"res_open:{r['id']}")]
        for r in items[:20]
    ]
    rows.append(_nav_back_home(back_cb))
    await _edit_or_send(
        callback,
        f"🎓 <b>COC Past Exams · {html.escape(raw_label)}</b>\n"
        + ("Select a past exam PDF/file below:" if items else "<i>No past exams uploaded for this track yet.</i>"),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@student_res_router.callback_query(F.data.startswith("res_crs:"))
async def cb_course_branch(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Invalid selection.", show_alert=True)
        return

    _, cat, cid_s = parts[:3]
    year_num = _parse_year(parts[3]) if len(parts) >= 4 else None
    cid = int(cid_s) if cid_s.isdigit() else 0
    y_suffix = f":{year_num}" if year_num else ""
    cat_back_cb = f"res_cat:{cat}{y_suffix}"

    async with AsyncSessionLocal() as db:
        course = (
            await db.execute(select(Course).where(Course.id == cid))
        ).scalar_one_or_none()
        course_name = course.name if course else f"Course #{cid}"

    cat_label = RESOURCE_CATEGORIES.get(cat, "Resources")
    if not await _check_or_block_study_resources(
        callback,
        selection_label=f"{cat_label} · {course_name}",
        back_cb=cat_back_cb,
    ):
        return

    await callback.answer()
    if cat == "atirera":
        items = await fetch_filtered_resources("atirera", course_id=cid)
        chapters: list[str] = []
        for r in items:
            ch_clean = (r["sub"] or "General").strip()
            if ch_clean not in chapters:
                chapters.append(ch_clean)

        rows = [
            [
                InlineKeyboardButton(
                    text=f"📑 {ch[:34]}",
                    callback_data=f"res_chp:{cid}:{idx}{y_suffix}",
                )
            ]
            for idx, ch in enumerate(chapters[:25])
        ]
        rows.append(_nav_back_home(cat_back_cb))
        _ATIRERA_CHAPTER_CACHE[(callback.from_user.id, cid)] = chapters

        await _edit_or_send(
            callback,
            f"📚 <b>አጤሬራ · {html.escape(course_name)}</b>\n"
            + (
                "Select a chapter:"
                if chapters
                else "<i>No chapters/files added for this course yet.</i>"
            ),
            InlineKeyboardMarkup(inline_keyboard=rows),
        )
        return

    items = await fetch_filtered_resources(cat, course_id=cid)
    rows = [
        [
            InlineKeyboardButton(
                text=f"📄 {r['title'][:34]}",
                callback_data=f"res_open:{r['id']}",
            )
        ]
        for r in items[:20]
    ]
    rows.append(_nav_back_home(cat_back_cb))

    await _edit_or_send(
        callback,
        f"<b>{html.escape(cat_label)} · {html.escape(course_name)}</b>\n"
        + (
            "Select a file/book below:"
            if items
            else "<i>No items uploaded for this course yet.</i>"
        ),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@student_res_router.callback_query(F.data.startswith("res_chp:"))
async def cb_atirera_chapter_files(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Invalid chapter.", show_alert=True)
        return

    _, cid_s, ch_idx_s = parts[:3]
    year_num = _parse_year(parts[3]) if len(parts) >= 4 else None
    y_suffix = f":{year_num}" if year_num else ""
    back_cb = f"res_crs:atirera:{cid_s}{y_suffix}"

    cid = int(cid_s) if cid_s.isdigit() else 0
    ch_idx = int(ch_idx_s) if ch_idx_s.isdigit() else 0
    chapters = _ATIRERA_CHAPTER_CACHE.get((callback.from_user.id, cid), [])
    chapter_name = chapters[ch_idx] if 0 <= ch_idx < len(chapters) else ""

    if not await _check_or_block_study_resources(
        callback,
        selection_label=f"አጤሬራ · {chapter_name or 'Chapter'}",
        back_cb=back_cb,
    ):
        return

    await callback.answer()
    items = await fetch_filtered_resources("atirera", course_id=cid, track_or_chapter=chapter_name)
    rows = [
        [InlineKeyboardButton(text=f"📄 {r['title'][:34]}", callback_data=f"res_open:{r['id']}")]
        for r in items[:20]
    ]
    rows.append(_nav_back_home(back_cb))
    await _edit_or_send(
        callback,
        f"📚 <b>አጤሬራ · {html.escape(chapter_name or 'Chapter')}</b>\n"
        + ("Select a study file below:" if items else "<i>No files in this chapter yet.</i>"),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@student_res_router.callback_query(F.data.startswith("res_open:"))
async def cb_open_resource(callback: CallbackQuery):
    res_id_s = callback.data.split(":", 1)[1]
    res_id = int(res_id_s) if res_id_s.isdigit() else 0

    item = await get_serialized_resource(res_id)
    if not item:
        await callback.answer("Resource not found.", show_alert=True)
        return

    is_free = bool(item["is_free"])
    if not is_free and not await _check_or_block_study_resources(
        callback,
        selection_label=item["title"],
        back_cb="nav:resources_hub",
    ):
        return

    await callback.answer("Sending file...")
    caption = f"📄 <b>{html.escape(item['title'])}</b>"
    if item["sub"]:
        caption += f"\n🏷 <code>{html.escape(item['sub'])}</code>"
    if item["note"]:
        caption += f"\n\n{html.escape(item['note'][:900])}"

    if item["file_id"]:
        raw_fid = item["file_id"]
        clean_fid = raw_fid[4:] if raw_fid.startswith("DOC:") else raw_fid
        try:
            await callback.message.answer_document(
                document=clean_fid,
                caption=caption[:1020],
                parse_mode="HTML",
                protect_content=not is_free,
            )
        except Exception:
            await callback.message.answer_photo(
                photo=clean_fid,
                caption=caption[:1020],
                parse_mode="HTML",
                protect_content=not is_free,
            )
    else:
        await callback.message.answer(caption[:3950], parse_mode="HTML", disable_web_page_preview=True)