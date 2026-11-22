import re
from sqlalchemy import select
from database import AsyncSessionLocal
from models import Course, Chapter, LearningResource

RESOURCE_CATEGORIES: dict[str, str] = {
    "coc": "🎓 COC Past Exams",
    "worksheet": "📝 Worksheets",
    "atirera": "📚 አጤሬራ",
    "refbook": "📖 Reference Books",
}

COC_TRACKS: list[tuple[str, str]] = [
    ("health", "🏥 Health"),
    ("law", "⚖️ Law"),
    ("architecture", "🏛 Architecture"),
]

_TAG_RE = re.compile(r"^\[([a-zA-Z_]+)(?::([^\]]+))?\]\s*(.*)$", re.DOTALL)


def _normalize_cat(raw: str) -> str:
    val = (raw or "").strip().lower().removeprefix("res_")
    if val in ("coc", "certificate_of_competency"):
        return "coc"
    if val in ("worksheet", "worksheets"):
        return "worksheet"
    if val in ("atirera", "አጤሬራ"):
        return "atirera"
    if val in ("refbook", "reference_book", "reference_books", "reference"):
        return "refbook"
    return ""


def _parse_resource_row(res: LearningResource, chapter_obj: Chapter | None) -> dict:
    cols = LearningResource.__table__.columns
    title = (getattr(res, "title", None) or f"Resource #{res.id}").strip()

    file_id = None
    for fname in ("telegram_file_id", "file_id", "document_id"):
        if fname in cols:
            file_id = getattr(res, fname, None)
            if file_id:
                break

    raw_text = (getattr(res, "text_content", None) or "").strip()
    cat_key = ""
    sub_val = ""
    note_val = raw_text

    m = _TAG_RE.match(raw_text)
    if m:
        cat_key = _normalize_cat(m.group(1) or "")
        sub_val = (m.group(2) or "").strip()
        note_val = (m.group(3) or "").strip()

    if not cat_key and "feature_key" in cols:
        fk = (getattr(res, "feature_key", None) or "").strip().lower()
        if fk.startswith("res_"):
            cat_key = _normalize_cat(fk)

    if not sub_val and chapter_obj is not None:
        sub_val = (getattr(chapter_obj, "title", None) or "").strip()

    course_id = int(getattr(chapter_obj, "course_id", 0) or 0) if chapter_obj is not None else 0

    if "is_free" in cols:
        is_free = bool(getattr(res, "is_free", False))
    elif "is_free_override" in cols:
        is_free = bool(getattr(res, "is_free_override", False))
    else:
        is_free = False

    is_published = bool(getattr(res, "is_published", True)) if "is_published" in cols else True

    return {
        "id": int(res.id),
        "title": title,
        "category": cat_key,
        "sub": sub_val,
        "note": note_val,
        "file_id": file_id,
        "course_id": course_id,
        "chapter_id": getattr(res, "chapter_id", None),
        "is_free": is_free,
        "is_published": is_published,
    }


async def _load_catalog_items(include_unpublished: bool = False, limit: int = 250) -> list[dict]:
    async with AsyncSessionLocal() as db:
        stmt = select(LearningResource).order_by(LearningResource.id.desc())
        if not include_unpublished and hasattr(LearningResource, "is_published"):
            stmt = stmt.where(
                LearningResource.is_published.is_(True) | LearningResource.is_published.is_(None)
            )
        rows = (await db.execute(stmt.limit(limit))).scalars().all()
        chap_ids = {int(r.chapter_id) for r in rows if getattr(r, "chapter_id", None) is not None}
        chap_map: dict[int, Chapter] = {}
        if chap_ids:
            chaps = (await db.execute(select(Chapter).where(Chapter.id.in_(chap_ids)))).scalars().all()
            chap_map = {int(c.id): c for c in chaps}

        return [
            item
            for r in rows
            if (item := _parse_resource_row(r, chap_map.get(int(getattr(r, "chapter_id", 0) or 0))))["category"]
        ]


async def fetch_filtered_resources(
    category: str,
    course_id: int = 0,
    track_or_chapter: str = "",
) -> list[dict]:
    cat_norm = _normalize_cat(category)
    items = await _load_catalog_items(include_unpublished=False)
    return [
        it
        for it in items
        if (not cat_norm or it["category"] == cat_norm)
        and (course_id <= 0 or it["course_id"] == course_id)
        and (not track_or_chapter or it["sub"].lower() == track_or_chapter.lower())
    ]


async def list_catalog_crud_items(category: str = "", limit: int = 30) -> list[dict]:
    cat_norm = _normalize_cat(category) if category else ""
    items = await _load_catalog_items(include_unpublished=True)
    if cat_norm:
        items = [it for it in items if it["category"] == cat_norm]
    return items[:limit]


async def get_serialized_resource(res_id: int) -> dict | None:
    async with AsyncSessionLocal() as db:
        res = (
            await db.execute(select(LearningResource).where(LearningResource.id == res_id))
        ).scalar_one_or_none()
        if not res:
            return None
        chap = None
        if getattr(res, "chapter_id", None) is not None:
            chap = (
                await db.execute(select(Chapter).where(Chapter.id == res.chapter_id))
            ).scalar_one_or_none()
        return _parse_resource_row(res, chap)


async def resolve_catalog_chapter_id(db, course_id: int | None, sub_label: str, category: str) -> int:
    if course_id:
        chap = (
            await db.execute(
                select(Chapter).where(Chapter.course_id == course_id).order_by(Chapter.chapter_number.asc())
            )
        ).scalars().first()
        if chap:
            return int(chap.id)

    hub_course = (
        await db.execute(select(Course).where(Course.name == "More Resources"))
    ).scalar_one_or_none()
    if not hub_course:
        hub_course = Course(name="More Resources")
        db.add(hub_course)
        await db.flush()

    clean_sub = (sub_label or RESOURCE_CATEGORIES.get(category, category)).strip()[:120]
    chap = (
        await db.execute(
            select(Chapter).where(Chapter.course_id == hub_course.id, Chapter.title == clean_sub)
        )
    ).scalar_one_or_none()
    if not chap:
        existing_count = len((await db.execute(select(Chapter.id).where(Chapter.course_id == hub_course.id))).all())
        chap = Chapter(
            course_id=hub_course.id,
            chapter_number=existing_count + 1,
            title=clean_sub,
            is_published=True,
        )
        db.add(chap)
        await db.flush()
    return int(chap.id)


async def create_catalog_file_entry(
    category: str,
    title: str,
    telegram_file_id: str,
    course_id: int | None = None,
    sub_label: str = "",
    is_free: bool = False,
) -> dict:
    cat_norm = _normalize_cat(category) or "worksheet"
    tag = f"[{cat_norm}:{sub_label}]" if sub_label else f"[{cat_norm}]"
    cols = LearningResource.__table__.columns

    async with AsyncSessionLocal() as db:
        chap_id = await resolve_catalog_chapter_id(db, course_id=course_id, sub_label=sub_label, category=cat_norm)
        payload: dict[str, object] = {
            "chapter_id": chap_id,
            "feature_key": "more_resources",
            "title": title.strip()[:200],
            "content_type": "telegram_file",
            "telegram_file_id": telegram_file_id,
            "text_content": tag,
            "is_published": True,
        }
        if "is_free" in cols:
            payload["is_free"] = bool(is_free)
        elif "is_free_override" in cols:
            payload["is_free_override"] = bool(is_free)

        obj = LearningResource(**payload)
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        return {"id": int(obj.id), "title": obj.title}


async def toggle_catalog_free(res_id: int) -> dict | None:
    cols = LearningResource.__table__.columns
    free_col = "is_free" if "is_free" in cols else ("is_free_override" if "is_free_override" in cols else None)
    if not free_col:
        return await get_serialized_resource(res_id)

    async with AsyncSessionLocal() as db:
        res = (
            await db.execute(select(LearningResource).where(LearningResource.id == res_id))
        ).scalar_one_or_none()
        if not res:
            return None
        setattr(res, free_col, not bool(getattr(res, free_col, False)))
        await db.commit()
    return await get_serialized_resource(res_id)


async def rename_catalog_item(res_id: int, new_title: str) -> dict | None:
    clean = new_title.strip()[:200]
    if not clean:
        return await get_serialized_resource(res_id)
    async with AsyncSessionLocal() as db:
        res = (
            await db.execute(select(LearningResource).where(LearningResource.id == res_id))
        ).scalar_one_or_none()
        if not res:
            return None
        res.title = clean
        await db.commit()
    return await get_serialized_resource(res_id)


async def delete_catalog_item(res_id: int) -> bool:
    async with AsyncSessionLocal() as db:
        res = (
            await db.execute(select(LearningResource).where(LearningResource.id == res_id))
        ).scalar_one_or_none()
        if not res:
            return False
        await db.delete(res)
        await db.commit()
        return True


async def list_active_courses() -> list[Course]:
    async with AsyncSessionLocal() as db:
        stmt = select(Course).order_by(Course.name)
        if hasattr(Course, "is_active"):
            stmt = stmt.where(Course.is_active.is_(True) | Course.is_active.is_(None))
        return (await db.execute(stmt)).scalars().all()