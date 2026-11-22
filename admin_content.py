import html
import json
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from resource_catalog import (
    RESOURCE_CATEGORIES,
    COC_TRACKS,
    create_resource_entry,
    list_active_courses,
)

admin_content_router = Router()

COURSES_PER_PAGE = 6


class AdminContentFSM(StatesGroup):
    picking_course = State()
    entering_chapter = State()
    collecting_files = State()

    exam_picking_course = State()
    exam_picking_type = State()
    exam_entering_payload = State()


def _is_admin(user_id: int | None) -> bool:
    return bool(user_id)


def _nav_row(back_cb: str = "adm:feed_resources") -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text="⬅️ Back", callback_data=back_cb),
        InlineKeyboardButton(text="✖️ Cancel", callback_data="adm_feed:cancel"),
    ]


async def _set_card(
    bot,
    chat_id: int,
    state: FSMContext,
    text: str,
    kb: InlineKeyboardMarkup,
    prefer_msg: Message | None = None,
):
    data = await state.get_data()
    card_id = data.get("card_msg_id")

    if prefer_msg is not None:
        try:
            await prefer_msg.edit_text(
                text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True
            )
            await state.update_data(card_msg_id=prefer_msg.message_id)
            return
        except Exception:
            pass

    if card_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(card_id),
                text=text,
                reply_markup=kb,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        except Exception:
            pass

    sent = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=kb,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await state.update_data(card_msg_id=sent.message_id)


def _course_page_kb(courses: list, page: int, prefix: str, back_cb: str) -> InlineKeyboardMarkup:
    total_pages = max(1, (len(courses) + COURSES_PER_PAGE - 1) // COURSES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    chunk = courses[page * COURSES_PER_PAGE : (page + 1) * COURSES_PER_PAGE]

    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(chunk), 2):
        c1 = chunk[i]
        row = [InlineKeyboardButton(text=c1.name[:22], callback_data=f"{prefix}:{c1.id}")]
        if i + 1 < len(chunk):
            c2 = chunk[i + 1]
            row.append(InlineKeyboardButton(text=c2.name[:22], callback_data=f"{prefix}:{c2.id}"))
        rows.append(row)

    if total_pages > 1:
        pager: list[InlineKeyboardButton] = []
        if page > 0:
            pager.append(InlineKeyboardButton(text="◀️", callback_data=f"adm_crs_pg:{prefix}:{page - 1}"))
        pager.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            pager.append(InlineKeyboardButton(text="▶️", callback_data=f"adm_crs_pg:{prefix}:{page + 1}"))
        rows.append(pager)

    rows.append(_nav_row(back_cb))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _bulk_queue_kb(count: int, back_cb: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if count > 0:
        rows.append([
            InlineKeyboardButton(text=f"🔓 Save All Free ({count})", callback_data="adm_bulk_save:1"),
            InlineKeyboardButton(text=f"🔒 Save All Premium ({count})", callback_data="adm_bulk_save:0"),
        ])
        rows.append([
            InlineKeyboardButton(text="🗑 Clear Queue", callback_data="adm_bulk_clear"),
        ])
    rows.append(_nav_row(back_cb))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _file_title_from_doc(doc) -> str:
    raw = (getattr(doc, "file_name", None) or "").strip()
    if not raw:
        return f"Document_{getattr(doc, 'file_unique_id', 'file')}"
    return raw.rsplit(".", 1)[0].replace("_", " ").strip() or raw


@admin_content_router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


@admin_content_router.callback_query(F.data == "adm_feed:cancel")
async def adm_feed_cancel(callback: CallbackQuery, state: FSMContext):
    msg_id = (await state.get_data()).get("card_msg_id")
    await state.clear()
    if msg_id:
        await state.update_data(card_msg_id=msg_id)
    await callback.answer("Cancelled")
    await adm_choose_resource_category(callback, state)


@admin_content_router.callback_query(F.data == "adm:feed_resources")
async def adm_choose_resource_category(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return
    prev_card = (await state.get_data()).get("card_msg_id")
    await state.clear()
    if prev_card:
        await state.update_data(card_msg_id=prev_card)
    await callback.answer()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🎓 COC", callback_data="adm_res_cat:coc"),
            InlineKeyboardButton(text="📝 Worksheet", callback_data="adm_res_cat:worksheet"),
        ],
        [
            InlineKeyboardButton(text="📚 አጤሬራ", callback_data="adm_res_cat:atirera"),
            InlineKeyboardButton(text="📖 Ref Book", callback_data="adm_res_cat:refbook"),
        ],
        [
            InlineKeyboardButton(text="🧠 Exam Trainer", callback_data="adm_exam:start"),
            InlineKeyboardButton(text="🛠 Admin", callback_data="adm:home"),
        ],
    ])
    await _set_card(
        callback.bot,
        callback.message.chat.id,
        state,
        "📥 <b>Bulk Content Feeder</b> — Choose category:",
        kb,
        prefer_msg=callback.message,
    )


@admin_content_router.callback_query(F.data.startswith("adm_crs_pg:"))
async def adm_course_page_turn(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, prefix, page_s = callback.data.split(":", 2)
    page = int(page_s) if page_s.isdigit() else 0
    courses = await list_active_courses()
    back_cb = "adm_exam:start" if prefix == "adm_ex_crs" else "adm:feed_resources"
    await _set_card(
        callback.bot,
        callback.message.chat.id,
        state,
        "📘 <b>Select Course</b>:",
        _course_page_kb(courses, page, prefix, back_cb),
        prefer_msg=callback.message,
    )


@admin_content_router.callback_query(F.data.startswith("adm_res_cat:"))
async def adm_res_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    cat = callback.data.split(":", 1)[1]
    card_id = (await state.get_data()).get("card_msg_id")
    await state.update_data(
        adm_cat=cat,
        adm_course_id=0,
        adm_sub="",
        adm_back_cb="adm:feed_resources",
        bulk_files=[],
        card_msg_id=card_id,
    )

    if cat == "coc":
        rows = [[InlineKeyboardButton(text=lbl, callback_data=f"adm_coc_trk:{k}")] for k, lbl in COC_TRACKS]
        rows.append(_nav_row("adm:feed_resources"))
        await _set_card(
            callback.bot,
            callback.message.chat.id,
            state,
            "🎓 <b>COC Track</b>:",
            InlineKeyboardMarkup(inline_keyboard=rows),
            prefer_msg=callback.message,
        )
        return

    courses = await list_active_courses()
    await state.set_state(AdminContentFSM.picking_course)
    label = RESOURCE_CATEGORIES.get(cat, "Resource")
    await _set_card(
        callback.bot,
        callback.message.chat.id,
        state,
        f"<b>{html.escape(label)}</b> — Course:",
        _course_page_kb(courses, 0, "adm_res_crs", "adm:feed_resources"),
        prefer_msg=callback.message,
    )


async def _enter_bulk_collector(
    bot,
    chat_id: int,
    state: FSMContext,
    header_label: str,
    back_cb: str,
    prefer_msg: Message | None = None,
):
    data = await state.get_data()
    files: list[dict] = data.get("bulk_files") or []
    await state.update_data(adm_header=header_label, adm_back_cb=back_cb)
    await state.set_state(AdminContentFSM.collecting_files)

    if not files:
        body = (
            f"📥 <b>{html.escape(header_label)}</b>\n"
            "Send <b>1 or more PDF/files</b> now — each file automatically uses its filename as title.\n"
            "Tap <b>Save</b> once your files are sent."
        )
    else:
        names_preview = "\n".join(f"• <code>{html.escape(f['title'][:36])}</code>" for f in files[-8:])
        extra = f"\n<i>(+{len(files) - 8} earlier)</i>" if len(files) > 8 else ""
        body = (
            f"📥 <b>{html.escape(header_label)}</b> — <b>{len(files)}</b> file(s) queued:\n"
            f"{names_preview}{extra}\n\n"
            "Send more files or tap <b>Save</b> below:"
        )

    await _set_card(bot, chat_id, state, body, _bulk_queue_kb(len(files), back_cb), prefer_msg=prefer_msg)


@admin_content_router.callback_query(F.data.startswith("adm_coc_trk:"))
async def adm_coc_trk_picked(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    trk_key = callback.data.split(":", 1)[1]
    trk_label = dict(COC_TRACKS).get(trk_key, trk_key.title())
    await state.update_data(adm_sub=trk_label)
    await _enter_bulk_collector(
        callback.bot,
        callback.message.chat.id,
        state,
        f"COC · {trk_label}",
        "adm_res_cat:coc",
        prefer_msg=callback.message,
    )


@admin_content_router.callback_query(F.data.startswith("adm_res_crs:"))
async def adm_res_crs_picked(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    cid_s = callback.data.split(":", 1)[1]
    cid = int(cid_s) if cid_s.isdigit() else 0
    await state.update_data(adm_course_id=cid)
    data = await state.get_data()
    cat = data.get("adm_cat", "worksheet")
    cat_label = RESOURCE_CATEGORIES.get(cat, "Resource")

    if cat == "atirera":
        await state.set_state(AdminContentFSM.entering_chapter)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📑 Use 'General'", callback_data="adm_ch_general")],
            _nav_row(f"adm_res_cat:{cat}"),
        ])
        await _set_card(
            callback.bot,
            callback.message.chat.id,
            state,
            "📑 <b>አጤሬራ</b> — Type chapter name or tap <b>General</b>:",
            kb,
            prefer_msg=callback.message,
        )
        return

    await _enter_bulk_collector(
        callback.bot,
        callback.message.chat.id,
        state,
        cat_label,
        f"adm_res_cat:{cat}",
        prefer_msg=callback.message,
    )


@admin_content_router.callback_query(F.data == "adm_ch_general")
async def adm_chapter_general(callback: CallbackQuery, state: FSMContext):
    await callback.answer("General")
    await state.update_data(adm_sub="General")
    await _enter_bulk_collector(
        callback.bot,
        callback.message.chat.id,
        state,
        "አጤሬራ · General",
        "adm_res_cat:atirera",
        prefer_msg=callback.message,
    )


@admin_content_router.message(AdminContentFSM.entering_chapter)
async def adm_chapter_entered(message: Message, state: FSMContext):
    ch = (message.text or "").strip() or "General"
    await state.update_data(adm_sub=ch)
    await _enter_bulk_collector(
        message.bot,
        message.chat.id,
        state,
        f"አጤሬራ · {ch}",
        "adm_res_cat:atirera",
    )


@admin_content_router.message(AdminContentFSM.collecting_files)
async def adm_collect_bulk_file(message: Message, state: FSMContext):
    if not message.document:
        await message.answer("⚠️ Send a <b>PDF/file</b>, or tap <b>Save</b> on the menu above.", parse_mode="HTML")
        return

    title = _file_title_from_doc(message.document)
    data = await state.get_data()
    files: list[dict] = list(data.get("bulk_files") or [])
    files.append({"file_id": message.document.file_id, "title": title})
    await state.update_data(bulk_files=files)

    header = data.get("adm_header") or "Bulk Upload"
    back_cb = data.get("adm_back_cb") or "adm:feed_resources"
    names_preview = "\n".join(f"• <code>{html.escape(f['title'][:36])}</code>" for f in files[-8:])
    extra = f"\n<i>(+{len(files) - 8} earlier)</i>" if len(files) > 8 else ""

    await _set_card(
        message.bot,
        message.chat.id,
        state,
        f"📥 <b>{html.escape(header)}</b> — <b>{len(files)}</b> file(s) queued:\n"
        f"{names_preview}{extra}\n\n"
        "Drop more files or tap <b>Save</b>:",
        _bulk_queue_kb(len(files), back_cb),
    )


@admin_content_router.callback_query(F.data == "adm_bulk_clear")
async def adm_bulk_clear(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Cleared")
    data = await state.get_data()
    await state.update_data(bulk_files=[])
    await _enter_bulk_collector(
        callback.bot,
        callback.message.chat.id,
        state,
        data.get("adm_header") or "Bulk Upload",
        data.get("adm_back_cb") or "adm:feed_resources",
        prefer_msg=callback.message,
    )


@admin_content_router.callback_query(F.data.startswith("adm_bulk_save:"))
async def adm_bulk_save_all(callback: CallbackQuery, state: FSMContext):
    is_free = callback.data.split(":", 1)[1] == "1"
    data = await state.get_data()
    files: list[dict] = data.get("bulk_files") or []
    if not files:
        await callback.answer("No files queued yet.", show_alert=True)
        return

    await callback.answer(f"Saving {len(files)}...")
    cat = data.get("adm_cat", "worksheet")
    cid = int(data.get("adm_course_id") or 0)
    sub = data.get("adm_sub") or ""
    card_id = data.get("card_msg_id")

    saved_count = 0
    for item in files:
        await create_resource_entry(
            category=cat,
            title=item["title"],
            course_id=cid,
            track_or_chapter=sub,
            file_id=item["file_id"],
            text_content="",
            is_free=is_free,
        )
        saved_count += 1

    await state.clear()
    if card_id:
        await state.update_data(card_msg_id=card_id)

    tier_lbl = "Free" if is_free else "Premium"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Bulk More", callback_data="adm:feed_resources"),
            InlineKeyboardButton(text="🛠 Admin", callback_data="adm:home"),
        ]
    ])
    await _set_card(
        callback.bot,
        callback.message.chat.id,
        state,
        f"✅ Uploaded <b>{saved_count}</b> file(s) as <code>{tier_lbl}</code> (titles taken from filenames).",
        kb,
        prefer_msg=callback.message,
    )


# --- Exam Trainer Feeder ---

@admin_content_router.callback_query(F.data == "adm_exam:start")
async def adm_exam_pick_course(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return
    card_id = (await state.get_data()).get("card_msg_id")
    await state.clear()
    if card_id:
        await state.update_data(card_msg_id=card_id)
    await callback.answer()
    courses = await list_active_courses()
    await state.set_state(AdminContentFSM.exam_picking_course)
    await _set_card(
        callback.bot,
        callback.message.chat.id,
        state,
        "🧠 <b>Exam Trainer</b> — Select course:",
        _course_page_kb(courses, 0, "adm_ex_crs", "adm:feed_resources"),
        prefer_msg=callback.message,
    )


@admin_content_router.callback_query(F.data.startswith("adm_ex_crs:"))
async def adm_exam_pick_type(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    cid_s = callback.data.split(":", 1)[1]
    cid = int(cid_s) if cid_s.isdigit() else 0
    await state.update_data(ex_course_id=cid)
    await state.set_state(AdminContentFSM.exam_picking_type)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔘 MCQ", callback_data="adm_ex_typ:mcq"),
            InlineKeyboardButton(text="✅ True/False", callback_data="adm_ex_typ:tf"),
        ],
        [
            InlineKeyboardButton(text="✍️ Short", callback_data="adm_ex_typ:short"),
            InlineKeyboardButton(text="📦 JSON", callback_data="adm_ex_typ:json"),
        ],
        _nav_row("adm_exam:start"),
    ])
    await _set_card(callback.bot, callback.message.chat.id, state, "🧠 <b>Select format</b>:", kb, prefer_msg=callback.message)


@admin_content_router.callback_query(F.data.startswith("adm_ex_typ:"))
async def adm_exam_prompt_format(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    qtype = callback.data.split(":", 1)[1]
    await state.update_data(ex_type=qtype)
    await state.set_state(AdminContentFSM.exam_entering_payload)

    guides = {
        "mcq": "<code>Question | A | B | C | D | B | Explanation</code>",
        "tf": "<code>Statement | True | Explanation</code>",
        "short": "<code>Question | Answer</code>",
        "json": '<code>[{"question":"...","options":["A","B","C","D"],"answer":"A"}]</code>',
    }
    kb = InlineKeyboardMarkup(inline_keyboard=[_nav_row("adm_exam:start")])
    await _set_card(
        callback.bot,
        callback.message.chat.id,
        state,
        f"✍️ Send <b>{html.escape(qtype.upper())}</b>:\n{guides.get(qtype, '')}",
        kb,
        prefer_msg=callback.message,
    )


@admin_content_router.message(AdminContentFSM.exam_entering_payload)
async def adm_exam_save_payload(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    data = await state.get_data()
    qtype = data.get("ex_type", "mcq")
    cid = int(data.get("ex_course_id") or 0)

    try:
        if qtype == "json":
            arr = json.loads(raw)
            if not isinstance(arr, list) or not arr:
                raise ValueError("JSON must be a non-empty list.")
            count = len(arr)
        elif qtype == "mcq":
            if len([p.strip() for p in raw.split("|")]) < 6:
                raise ValueError("Need: Question | A | B | C | D | Answer")
            count = 1
        elif qtype == "tf":
            if len([p.strip() for p in raw.split("|")]) < 2:
                raise ValueError("Need: Statement | True/False")
            count = 1
        else:
            if len([p.strip() for p in raw.split("|", 1)]) < 2:
                raise ValueError("Need: Question | Answer")
            count = 1
    except Exception as exc:
        await message.answer(f"❌ <code>{html.escape(str(exc))}</code>", parse_mode="HTML")
        return

    await create_resource_entry(
        category="worksheet",
        title=f"Exam[{qtype.upper()}]: {raw[:45]}",
        course_id=cid,
        track_or_chapter=f"exam_{qtype}",
        text_content=raw,
        is_free=False,
    )
    card_id = data.get("card_msg_id")
    await state.clear()
    if card_id:
        await state.update_data(card_msg_id=card_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Add More", callback_data="adm:feed_resources"),
            InlineKeyboardButton(text="🛠 Admin", callback_data="adm:home"),
        ]
    ])
    await _set_card(
        message.bot,
        message.chat.id,
        state,
        f"✅ Saved <b>{count}</b> exam item(s).",
        kb,
    )