import hashlib
import secrets
from datetime import datetime, timedelta
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from models import (
    Course, Chapter, FeatureConfig, LearningResource,
    QuizQuestion, Flashcard, Student, SubscriptionPlan,
    PaymentMethodConfig, PaymentReceipt, QuizAttempt,
    DailyChallengeCompletion,
)

CATALOGUE_26_COURSES = [
    "Anthropology", "Biology",
    "Chemistry", "Civics", "Economics", "Emerging Technology",
    "English I", "English II", "Entrepreneurship", "Geography",
    "Global Affairs", "History", "Inclusiveness", "Logic",
    "Math (Natural)", "Math (Social)", "Physics", "Psychology", "Networking",
    "Database", "Electric city"
]

DEFAULT_COURSE_YEARS: dict[str, str] = {
    "Networking": "2nd Year",
    "Database": "2nd Year",
    "Electric city": "3rd Year",
}

DEFAULT_FEATURES = [
    # Free Learning Features
    ("modules", "📚 Modules", True, False, 1),
    ("mid_exams", "📝 Mid-Exams", True, False, 2),
    ("notes", "🗒 Study Notes", True, False, 3),
    # Premium / Hub Features
    ("quiz", "⏱ Timed Quiz", False, False, 4),
    ("final_exams", "🎓 Final Exams", False, False, 5),
    ("exam_trainer", "🎯 Exam Trainer", False, False, 6),
    ("flashcards", "🃏 Flashcards", False, False, 7),
    ("practice_questions", "✍️ Practice Questions", False, False, 8),
    ("search", "🔍 Search Resources", False, False, 9),
    ("study_tools", "🛠 Study Tools", False, False, 10),
    ("more_resources", "📂 Study Resources", False, False, 11),
    ("ai_assistant", "🤖 AI Study Assistant", False, False, 12),
]

DIRECT_ACCESS_FEATURES = {"modules", "mid_exams", "final_exams"}
DAILY_CHALLENGES: dict[str, list[dict]] = {
    "coding": [
        {
            "id": "cod-1",
            "title": "List Slicing & Step",
            "prompt": "What does <code>nums =; print(nums[1:4:2])</code> output in Python?",
            "hint": "Start at index <code>1</code> (<code>20</code>), stop before index <code>4</code>, and step by <code>2</code>.",
            "solution": "<b>[20, 40]</b>\nIndex 1 is <code>20</code>. Stepping by 2 jumps to index 3 (<code>40</code>), stopping before index 4.",
        },
        {
            "id": "cod-2",
            "title": "Truthiness & Conditionals",
            "prompt": "If <code>items = []</code>, what is printed by:\n<pre>if items:\n    print('Has items')\nelse:\n    print('Empty')</pre>",
            "hint": "In Python, empty collections evaluate as falsy in boolean contexts.",
            "solution": "<b>Empty</b>\nAn empty list <code>[]</code> has a boolean value of <code>False</code>, so the <code>else</code> branch runs.",
        },
        {
            "id": "cod-3",
            "title": "Dictionary Lookup Safety",
            "prompt": "Why is <code>scores.get('bio', 0)</code> often preferred over <code>scores['bio']</code> when reading a key that might be missing?",
            "hint": "Think about what happens when a key does not exist in a dictionary.",
            "solution": "<b>It returns the default value (<code>0</code>) instead of raising a <code>KeyError</code>.</b>\n<code>.get(key, default)</code> safely falls back when the key is absent.",
        },
        {
            "id": "cod-4",
            "title": "Mutable Default Arguments",
            "prompt": "What happens if you call <code>add_item('pen')</code> and then <code>add_item('book')</code> on:\n<pre>def add_item(x, box=[]):\n    box.append(x)\n    return box</pre>",
            "hint": "Default argument objects in Python are created once when the function is defined, not on every call.",
            "solution": "<b>The second call returns <code>['pen', 'book']</code>.</b>\nThe default list <code>box</code> is shared across calls unless you use <code>box=None</code> and initialize inside the function.",
        },
        {
            "id": "cod-5",
            "title": "String Immutability",
            "prompt": "What happens when you run:\n<pre>word = 'Qine'\nword[0] = 'q'</pre>",
            "hint": "Can individual characters inside a Python <code>str</code> be modified in place?",
            "solution": "<b>It raises a <code>TypeError</code>.</b>\nPython strings are immutable; you must create a new string like <code>'q' + word[1:]</code> instead.",
        },
        {
            "id": "cod-6",
            "title": "Integer Division vs. True Division",
            "prompt": "What is the output of <code>print(7 // 2, 7 / 2)</code> in Python 3?",
            "hint": "<code>//</code> performs floor division, while <code>/</code> performs floating-point division.",
            "solution": "<b><code>3 3.5</code></b>\n<code>7 // 2</code> discards the fractional remainder to give an integer (<code>3</code>), whereas <code>7 / 2</code> returns a float (<code>3.5</code>).",
        },
        {
            "id": "cod-7",
            "title": "Set Deduplication",
            "prompt": "What is <code>len(set([1, 2, 2, 3, 3, 3]))</code>?",
            "hint": "A Python <code>set</code> only stores unique elements.",
            "solution": "<b><code>3</code></b>\nConverting the list to a set removes duplicate values, leaving <code>{1, 2, 3}</code>.",
        },
        {
            "id": "cod-8",
            "title": "Looping with enumerate()",
            "prompt": "What does this snippet print?\n<pre>for i, ch in enumerate(['A', 'B'], start=1):\n    print(f'{i}:{ch}', end=' ')</pre>",
            "hint": "<code>enumerate(..., start=1)</code> begins counting indices at <code>1</code> instead of <code>0</code>.",
            "solution": "<b><code>1:A 2:B</code></b>\n<code>enumerate()</code> pairs each item with a counter starting at <code>1</code>.",
        },
        {
            "id": "cod-9",
            "title": "List Comprehension Filtering",
            "prompt": "What list is produced by <code>[x * 2 for x in range(5) if x % 2 == 1]</code>?",
            "hint": "First filter <code>0, 1, 2, 3, 4</code> for odd numbers, then double them.",
            "solution": "<b><code>[2, 6]</code></b>\nThe odd numbers in <code>range(5)</code> are <code>1</code> and <code>3</code>; doubling each gives <code>2</code> and <code>6</code>.",
        },
        {
            "id": "cod-10",
            "title": "Identity (is) vs. Equality (==)",
            "prompt": "If <code>a = [1, 2]</code> and <code>b = [1, 2]</code>, what do <code>a == b</code> and <code>a is b</code> evaluate to?",
            "hint": "<code>==</code> compares values; <code>is</code> checks whether both variables point to the exact same object in memory.",
            "solution": "<b><code>a == b</code> is <code>True</code>, and <code>a is b</code> is <code>False</code>.</b>\nThey hold identical elements, but are two separate list objects in memory.",
        },
        {
            "id": "cod-11",
            "title": "Tuple Unpacking",
            "prompt": "What are the values of <code>first</code> and <code>rest</code> after running:\n<pre>first, *rest = [10, 20, 30, 40]</pre>",
            "hint": "The starred target <code>*rest</code> captures all remaining items as a list.",
            "solution": "<b><code>first = 10</code> and <code>rest = [20, 30, 40]</code>.</b>\nExtended unpacking assigns the first element to <code>first</code> and packs the remaining elements into <code>rest</code>.",
        },
        {
            "id": "cod-12",
            "title": "Time Complexity of Lookups",
            "prompt": "Checking <code>x in collection</code> is on average <code>O(1)</code> for a Python <code>set</code> or <code>dict</code>. What is its average time complexity for a <code>list</code> of size <code>n</code>?",
            "hint": "Without hashing, Python must scan items one by one from start to finish.",
            "solution": "<b><code>O(n)</code> (linear time).</b>\nA list checks elements sequentially, whereas sets and dictionaries use hash tables for near-instant lookups.",
        },
    ],
    "science": [
        {
            "id": "sci-1",
            "title": "Newton's Second Law Ratio",
            "prompt": "If net force <code>F</code> on an object doubles while its mass <code>m</code> stays constant, what happens to its acceleration <code>a</code>?",
            "hint": "Recall <code>a = F / m</code>.",
            "solution": "<b>Acceleration doubles (<code>2a</code>).</b>\nBecause acceleration is directly proportional to net force at constant mass, doubling <code>F</code> doubles <code>a</code>.",
        },
        {
            "id": "sci-2",
            "title": "Chemical Bonds & Valence Electrons",
            "prompt": "In a covalent bond between two atoms, how are electrons interacting compared to an ionic bond?",
            "hint": "Ionic involves transfer; covalent involves sharing.",
            "solution": "<b>Atoms share one or more pairs of valence electrons.</b>\nUnlike ionic bonding (which relies on electrostatic attraction between oppositely charged ions), covalent bonds share electron pairs.",
        },
        {
            "id": "sci-3",
            "title": "Cellular Energy Hub",
            "prompt": "Which organelle is primarily responsible for generating ATP through cellular respiration in eukaryotic cells?",
            "hint": "Often called the powerhouse of the cell.",
            "solution": "<b>Mitochondrion.</b>\nMitochondria carry out oxidative phosphorylation to produce most of the cell's ATP.",
        },
        {
            "id": "sci-4",
            "title": "Kinetic Energy & Speed",
            "prompt": "If a car's speed triples while its mass stays constant, by what factor does its kinetic energy <code>KE = ½mv²</code> increase?",
            "hint": "Notice that velocity <code>v</code> is squared in the formula.",
            "solution": "<b>It increases by a factor of <code>9</code> (<code>3²</code>).</b>\nKinetic energy scales with the square of velocity, so tripling speed multiplies <code>KE</code> by 9.",
        },
        {
            "id": "sci-5",
            "title": "pH Scale & Hydrogen Ions",
            "prompt": "How many times more acidic (higher <code>[H⁺]</code> concentration) is a solution with <code>pH 3</code> compared to one with <code>pH 5</code>?",
            "hint": "Each step of 1 on the pH scale represents a 10-fold change in <code>[H⁺]</code>.",
            "solution": "<b><code>100</code> times more acidic (<code>10²</code>).</b>\nBecause the pH scale is logarithmic (base 10), a difference of 2 pH units equals a <code>10 × 10 = 100</code>-fold difference.",
        },
        {
            "id": "sci-6",
            "title": "Ohm's Law in Series vs. Parallel",
            "prompt": "When two identical resistors are connected in <b>parallel</b> across a battery, is the total equivalent resistance larger or smaller than a single resistor?",
            "hint": "Adding a parallel branch gives electric current an extra path to flow.",
            "solution": "<b>Smaller (specifically, half of one resistor: <code>R / 2</code>).</b>\nParallel paths reduce overall resistance because <code>1/R_eq = 1/R + 1/R</code>.",
        },
        {
            "id": "sci-7",
            "title": "Osmosis Across Cell Membranes",
            "prompt": "What happens to an animal cell (such as a red blood cell) placed in a strongly <b>hypertonic</b> salt solution?",
            "hint": "Water moves from areas of lower solute concentration to higher solute concentration.",
            "solution": "It shrinks (crenates) as water leaves the cell.\nBecause the outside solution has a higher solute concentration, water diffuses out through the semipermeable membrane.",
        },
        {
            "id": "sci-8",
            "title": "Projectile Motion at Peak Height",
            "prompt": "A ball is thrown at a 45° angle above level ground (ignoring air resistance). At the very top of its trajectory, what are its vertical velocity and vertical acceleration?",
            "hint": "Gravity never turns off, even when the ball stops rising for an instant.",
            "solution": "Vertical velocity is 0 m/s, and vertical acceleration is 9.8 m/s² downward (-g).\nOnly the vertical velocity momentarily hits zero at the apex; gravitational acceleration remains constant.",
        },
        {
            "id": "sci-9",
            "title": "Exothermic vs. Endothermic Reactions",
            "prompt": "If a chemical reaction causes the temperature of the surrounding beaker to drop, is the reaction exothermic or endothermic, and what is the sign of ΔH?",
            "hint": "Heat is flowing from the surroundings into the reacting system.",
            "solution": "Endothermic (ΔH > 0).\nThe system absorbs thermal energy from the surroundings, cooling the beaker down.",
        },
        {
            "id": "sci-10",
            "title": "DNA Base Pairing",
            "prompt": "In double-stranded DNA, if a sample contains 22% Adenine (A), what percentage of Cytosine (C) does it contain?",
            "hint": "A pairs with T, and G pairs with C (Chargaff's rules).",
            "solution": "28% Cytosine.\nA = T = 22% (total 44%). The remaining 56% is split equally between G and C (56% ÷ 2 = 28%).",
        },
        {
            "id": "sci-11",
            "title": "Ideal Gas Law & Temperature",
            "prompt": "At constant pressure, if the absolute temperature (in Kelvin) of an ideal gas is doubled, what happens to its volume?",
            "hint": "Recall Charles's Law: V₁ / T₁ = V₂ / T₂.",
            "solution": "Its volume doubles.\nAt constant pressure, the volume of a fixed amount of gas is directly proportional to its Kelvin temperature.",
        },
        {
            "id": "sci-12",
            "title": "Work Done by Centripetal Force",
            "prompt": "A satellite orbits Earth in a uniform circular path. How much work does Earth's gravitational force do on the satellite during one full orbit?",
            "hint": "Compare the direction of the centripetal force with the direction of instantaneous displacement.",
            "solution": "Zero work (0 J).\nBecause centripetal force is always perpendicular (90°) to the satellite's velocity, W = F·d·cos(90°) = 0.",
        },
    ],
    "vocab": [
        {
            "id": "voc-1",
            "title": "Academic Word: Mitigate",
            "prompt": "What does mitigate mean in: 'Early backups help mitigate the impact of data loss.'?",
            "hint": "It means to make something less severe, harmful, or painful—not to eliminate it completely.",
            "solution": "To make less severe or serious.\nExample: 'Study schedules mitigate last-minute exam stress.'",
        },
        {
            "id": "voc-2",
            "title": "Academic Word: Ambiguous",
            "prompt": "If instructions are described as ambiguous, what is the issue with them?",
            "hint": "Think of multiple possible interpretations.",
            "solution": "Open to more than one interpretation; unclear or vague.\nExample: 'An ambiguous prompt can confuse students.'",
        },
        {
            "id": "voc-3",
            "title": "Academic Word: Corroborate",
            "prompt": "In research writing, what does it mean when a second study corroborates an earlier finding?",
            "hint": "It supports or confirms evidence with additional proof.",
            "solution": "To confirm or give support to a statement, theory, or finding.\nExample: 'Lab results corroborated the initial hypothesis.'",
        },
        {
            "id": "voc-4",
            "title": "Academic Word: Prudent",
            "prompt": "What does prudent mean in: 'It is prudent to review past exam papers before finals week.'?",
            "hint": "Think of wise, careful judgment that anticipates future outcomes.",
            "solution": "Acting with or showing care and thought for the future; wise and sensible.\nExample: 'Saving emergency notes offline is a prudent habit.'",
        },
        {
            "id": "voc-5",
            "title": "Academic Word: Substantiate",
            "prompt": "If a professor asks you to substantiate your argument in an essay, what must you do?",
            "hint": "An opinion alone isn't enough—you need solid backing.",
            "solution": "Provide evidence, facts, or data to prove or support a claim.\nExample: 'She substantiated her thesis with three peer-reviewed studies.'",
        },
        {
            "id": "voc-6",
            "title": "Academic Word: Ubiquitous",
            "prompt": "What does ubiquitous mean in: 'Smartphones have become ubiquitous on university campuses.'?",
            "hint": "Derived from the Latin word meaning 'everywhere'.",
            "solution": "Present, appearing, or found everywhere.\nExample: 'Wi-Fi access is now ubiquitous in modern libraries.'",
        },
        {
            "id": "voc-7",
            "title": "Academic Word: Discrepancy",
            "prompt": "What does a discrepancy between lab measurements and theoretical values refer to?",
            "hint": "Think of an unexpected mismatch or inconsistency between two things that should agree.",
            "solution": "A lack of compatibility or similarity between two or more facts or figures.\nExample: 'Human rounding errors explained the discrepancy in the data.'",
        },
        {
            "id": "voc-8",
            "title": "Academic Word: Concise",
            "prompt": "If an exam rubric asks for a concise summary, how should you write it?",
            "hint": "Focus on expressing maximum meaning without unnecessary filler.",
            "solution": "Giving a lot of information clearly and in a few words; brief but comprehensive.\nExample: 'A concise abstract summarizes the entire paper in one paragraph.'",
        },
        {
            "id": "voc-9",
            "title": "Academic Word: Caveat",
            "prompt": "What does caveat mean in: 'The model works well, with one important caveat regarding small sample sizes.'?",
            "hint": "It introduces a warning, condition, or limitation to keep in mind.",
            "solution": "A warning or proviso of specific stipulations, conditions, or limitations.\nExample: 'Accept the shortcut with the caveat that it fails on negative inputs.'",
        },
        {
            "id": "voc-10",
            "title": "Academic Word: Plausible",
            "prompt": "When a hypothesis is described as plausible, how is it being judged?",
            "hint": "It sounds reasonable and believable, even if not yet proven 100%.",
            "solution": "Seeming reasonable, credible, or probable.\nExample: 'Power outages are a plausible explanation for the delayed submission.'",
        },
        {
            "id": "voc-11",
            "title": "Academic Word: Synthesize",
            "prompt": "In literature reviews, what does it mean to synthesize multiple sources?",
            "hint": "More than just summarizing each source separately—you combine them into a unified whole.",
            "solution": "To combine separate ideas, findings, or elements into a coherent whole.\nExample: 'Strong essays synthesize viewpoints from several authors rather than listing them.'",
        },
        {
            "id": "voc-12",
            "title": "Academic Word: Arbitrary",
            "prompt": "What does arbitrary mean in: 'Choosing a random starting index without a rule is an arbitrary decision.'?",
            "hint": "Based on personal whim or random choice rather than a logical system.",
            "solution": "Based on random choice or personal whim, rather than any reason or system.\nExample: 'Grading rubrics prevent arbitrary point deductions.'",
        },
    ],
}

TRACK_META = {
    "coding": ("💻 Coding", "dc:coding"),
    "science": ("🔬 Science", "dc:science"),
    "vocab": ("📖 English Vocab", "dc:vocab"),
}


async def get_leaderboard_data(
    db: AsyncSession,
    viewer_telegram_id: int,
    period: str = "weekly",
    limit: int = 3,
) -> dict:
    """Computes ranked students combining quiz accuracy/strength and participation for a period."""
    now = datetime.utcnow()
    if period == "weekly":
        cutoff = now - timedelta(days=7)
        label = "This Week (7 Days)"
    elif period == "monthly":
        cutoff = now - timedelta(days=30)
        label = "This Month (30 Days)"
    else:
        cutoff = None
        label = "All-Time"

    attempt_stmt = select(QuizAttempt)
    if cutoff is not None and hasattr(QuizAttempt, "created_at"):
        attempt_stmt = attempt_stmt.where(QuizAttempt.created_at >= cutoff)
    attempts = (await db.execute(attempt_stmt)).scalars().all()

    dc_stmt = select(DailyChallengeCompletion)
    if cutoff is not None:
        cutoff_date_str = cutoff.strftime("%Y-%m-%d")
        dc_stmt = dc_stmt.where(DailyChallengeCompletion.challenge_date >= cutoff_date_str)
    dc_rows = (await db.execute(dc_stmt)).scalars().all()

    dc_stats: dict[int, dict] = {}
    for dc in dc_rows:
        d_entry = dc_stats.setdefault(dc.student_id, {"dc_xp": 0, "dc_count": 0})
        d_entry["dc_xp"] += max(0, int(dc.xp_earned or 0))
        d_entry["dc_count"] += 1

    students = (await db.execute(select(Student))).scalars().all()
    student_map = {s.telegram_id: s for s in students}

    referral_counts: dict[int, int] = {}
    for s in students:
        if s.referred_by_id:
            if cutoff is None or not hasattr(s, "created_at") or not s.created_at or s.created_at >= cutoff:
                referral_counts[s.referred_by_id] = referral_counts.get(s.referred_by_id, 0) + 1

    stats: dict[int, dict] = {}
    for att in attempts:
        uid = att.student_id
        entry = stats.setdefault(uid, {"correct": 0, "questions": 0, "quizzes": 0})
        entry["correct"] += max(0, int(att.score or 0))
        entry["questions"] += max(0, int(att.total_questions or 0))
        entry["quizzes"] += 1

    all_uids = set(stats.keys()) | set(referral_counts.keys()) | set(dc_stats.keys())

    rankings = []
    for uid in all_uids:
        st = student_map.get(uid)
        display_name = (st.full_name.strip() if st and st.full_name else f"Scholar #{str(uid)[-4:]}")
        s_data = stats.get(uid, {"correct": 0, "questions": 0, "quizzes": 0})
        refs = referral_counts.get(uid, 0)

        accuracy = (s_data["correct"] / s_data["questions"] * 100.0) if s_data["questions"] > 0 else 0.0
        base_quiz_xp = (s_data["correct"] * 15) + (s_data["quizzes"] * 5)
        strength_multiplier = 1.25 if (s_data["questions"] >= 5 and accuracy >= 80.0) else 1.0
        d_data = dc_stats.get(uid, {"dc_xp": 0, "dc_count": 0})
        participation_xp = (refs * 10) + d_data["dc_xp"]
        total_xp = round((base_quiz_xp * strength_multiplier) + participation_xp)

        rankings.append({
            "telegram_id": uid,
            "name": display_name,
            "xp": total_xp,
            "correct": s_data["correct"],
            "questions": s_data["questions"],
            "quizzes": s_data["quizzes"],
            "accuracy": accuracy,
            "boosted": strength_multiplier > 1.0,
        })

    rankings.sort(key=lambda r: (r["xp"], r["accuracy"], r["quizzes"]), reverse=True)
    for idx, row in enumerate(rankings, start=1):
        row["rank"] = idx

    top_entries = rankings[:limit]
    viewer_row = next((r for r in rankings if r["telegram_id"] == viewer_telegram_id), None)

    third_place_xp = rankings[2]["xp"] if len(rankings) >= 3 else 0
    points_to_top3 = max(0, (third_place_xp - viewer_row["xp"]) + 1) if viewer_row and viewer_row["rank"] > 3 else 0

    return {
        "period": period,
        "label": label,
        "top": top_entries,
        "viewer": viewer_row,
        "points_to_top3": points_to_top3,
        "total_ranked": len(rankings),
    }


def is_direct_access_feature(feature_key: str) -> bool:
    return feature_key in DIRECT_ACCESS_FEATURES


async def seed_initial_data(db: AsyncSession):
    """Seeds default courses, feature flags, plans, and payment methods."""
    existing_courses = set((await db.execute(select(Course.name))).scalars().all())
    for name in CATALOGUE_26_COURSES:
        if name not in existing_courses:
            db.add(Course(
                name=name,
                year_level=DEFAULT_COURSE_YEARS.get(name, "1st Year"),
                is_active=True,
            ))

    existing_features = set((await db.execute(select(FeatureConfig.key))).scalars().all())
    for key, title, is_free, has_levels, order in DEFAULT_FEATURES:
        if key not in existing_features:
            db.add(FeatureConfig(
                key=key,
                title=title,
                is_free=is_free,
                has_levels=has_levels,
                sort_order=order,
            ))

    existing_plans = (await db.execute(select(SubscriptionPlan.code))).scalars().all()
    if not existing_plans:
        db.add_all([
            SubscriptionPlan(
                code="FREE", name="Free Student Plan", price_etb=0, duration_days=3650,
                benefits_text="• Access to Modules, Mid-Exams, and Study Notes", daily_ai_quota=3,
            ),
            SubscriptionPlan(
                code="QINE_PLUS", name="Qine Plus", price_etb=300, duration_days=365,
                benefits_text="• Timed Quizzes with Explanations\n• Final Exams & Flashcards\n• Exam Trainer & Study Tools",
                daily_ai_quota=25,
            ),
            SubscriptionPlan(
                code="QINE_PRO", name="Qine Pro", price_etb=500, duration_days=365,
                benefits_text="• All Qine Plus Features\n• Full AI Study Assistant Suite\n• Extended AI Quotas & Document Q&A",
                daily_ai_quota=100,
            ),
        ])

    existing_pm = (await db.execute(select(PaymentMethodConfig.code))).scalars().all()
    if not existing_pm:
        db.add_all([
            PaymentMethodConfig(code="TELEBIRR", provider_name="Telebirr", account_holder="UNCONFIGURED", account_number="0900000000", is_verified_active=False),
            PaymentMethodConfig(code="CBE", provider_name="CBE", account_holder="UNCONFIGURED", account_number="1000000000000", is_verified_active=False),
            PaymentMethodConfig(code="BOA", provider_name="Abyssinia", account_holder="UNCONFIGURED", account_number="00000000", is_verified_active=False),
        ])
    await db.commit()


async def get_or_create_student(db: AsyncSession, tg_user, referred_by_code: str = None) -> Student:
    stmt = select(Student).where(Student.telegram_id == tg_user.id)
    student = (await db.execute(stmt)).scalar_one_or_none()
    if student:
        return student

    ref_code = f"QN{secrets.token_hex(4).upper()}"
    referred_by_id = None
    if referred_by_code:
        referrer = (await db.execute(select(Student).where(Student.referral_code == referred_by_code))).scalar_one_or_none()
        if referrer and referrer.telegram_id != tg_user.id:
            referred_by_id = referrer.telegram_id

    student = Student(
        telegram_id=tg_user.id,
        username=tg_user.username,
        full_name=tg_user.full_name or "Student",
        referral_code=ref_code,
        referred_by_id=referred_by_id,
    )
    db.add(student)
    await db.commit()
    await db.refresh(student)
    return student


def has_active_premium(student: Student) -> bool:
    if student.plan_code in ("QINE_PLUS", "QINE_PRO"):
        if student.subscription_expires_at and student.subscription_expires_at > datetime.utcnow():
            return True
    return False


async def check_feature_entitlement(db: AsyncSession, student: Student, feature_key: str) -> tuple[bool, FeatureConfig | None]:
    """Backend entitlement check required before opening feature OR delivering resource."""
    feature = (await db.execute(select(FeatureConfig).where(FeatureConfig.key == feature_key))).scalar_one_or_none()
    if not feature or not feature.is_enabled:
        return False, feature
    if feature.is_free:
        return True, feature
    return has_active_premium(student), feature


async def get_all_courses_for_feature(
    db: AsyncSession,
    feature_key: str,
    year_level: str | None = None,
) -> list[Course]:
    """Returns active courses, optionally filtered by year_level (e.g., '1st Year', '2nd Year', '3rd Year')."""
    stmt = select(Course).where(Course.is_active == True)
    if year_level:
        stmt = stmt.where(Course.year_level == year_level)
    stmt = stmt.order_by(Course.name)
    return list((await db.execute(stmt)).scalars().all())


async def get_chapters_with_content_status(db: AsyncSession, course_id: int, feature_key: str) -> list[tuple[Chapter, bool]]:
    """Returns ALL chapters for a course, along with a boolean flag indicating whether content exists."""
    chapters = (await db.execute(
        select(Chapter)
        .where(Chapter.course_id == course_id, Chapter.is_published == True)
        .order_by(Chapter.chapter_number)
    )).scalars().all()

    result = []
    for ch in chapters:
        if feature_key == "quiz":
            count = (await db.execute(
                select(func.count(QuizQuestion.id))
                .where(QuizQuestion.chapter_id == ch.id, QuizQuestion.is_published == True)
            )).scalar()
        elif feature_key == "flashcards":
            count = (await db.execute(
                select(func.count(Flashcard.id))
                .where(Flashcard.chapter_id == ch.id, Flashcard.is_published == True)
            )).scalar()
        else:
            count = (await db.execute(
                select(func.count(LearningResource.id))
                .where(
                    LearningResource.chapter_id == ch.id,
                    LearningResource.feature_key == feature_key,
                    LearningResource.is_published == True,
                )
            )).scalar()

        has_content = bool(count and count > 0)
        result.append((ch, has_content))
    return result


async def get_quiz_levels_for_chapter(db: AsyncSession, chapter_id: int) -> list[str]:
    stmt = (
        select(QuizQuestion.level)
        .where(QuizQuestion.chapter_id == chapter_id, QuizQuestion.is_published == True)
        .distinct()
        .order_by(QuizQuestion.level)
    )
    return list((await db.execute(stmt)).scalars().all())


def get_daily_challenge(track: str, date_str: str | None = None) -> dict:
    """Returns a deterministic daily challenge for a given track and UTC date (YYYY-MM-DD)."""
    track_key = track if track in DAILY_CHALLENGES else "coding"
    day_key = date_str or datetime.utcnow().strftime("%Y-%m-%d")
    pool = DAILY_CHALLENGES[track_key]
    digest = hashlib.sha256(f"{day_key}:{track_key}".encode("utf-8")).hexdigest()
    idx = int(digest, 16) % len(pool)
    challenge = dict(pool[idx])
    challenge["track"] = track_key
    challenge["track_label"] = TRACK_META[track_key][0]
    challenge["date_str"] = day_key
    return challenge


async def get_daily_challenge_status(
    db: AsyncSession,
    student_id: int,
    track: str,
    date_str: str | None = None,
) -> DailyChallengeCompletion | None:
    day_key = date_str or datetime.utcnow().strftime("%Y-%m-%d")
    stmt = select(DailyChallengeCompletion).where(
        DailyChallengeCompletion.student_id == student_id,
        DailyChallengeCompletion.challenge_date == day_key,
        DailyChallengeCompletion.track == track,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def record_daily_challenge_completion(
    db: AsyncSession,
    student_id: int,
    track: str,
    hint_used: bool,
    date_str: str | None = None,
) -> tuple[bool, int]:
    """Records a daily challenge completion once per track per UTC day. Returns (newly_awarded, xp_amount)."""
    day_key = date_str or datetime.utcnow().strftime("%Y-%m-%d")
    existing = await get_daily_challenge_status(db, student_id, track, day_key)
    if existing:
        return False, existing.xp_earned

    xp_earned = 10 if hint_used else 15
    row = DailyChallengeCompletion(
        student_id=student_id,
        challenge_date=day_key,
        track=track,
        hint_used=bool(hint_used),
        xp_earned=xp_earned,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return True, xp_earned


async def get_student_challenge_streak_days(db: AsyncSession, student_id: int) -> int:
    """Counts consecutive UTC days ending today or yesterday where the student completed at least one challenge."""
    stmt = (
        select(DailyChallengeCompletion.challenge_date)
        .where(DailyChallengeCompletion.student_id == student_id)
        .distinct()
    )
    dates_set = set((await db.execute(stmt)).scalars().all())
    if not dates_set:
        return 0

    today = datetime.utcnow().date()
    cursor = today if today.strftime("%Y-%m-%d") in dates_set else (today - timedelta(days=1))
    if cursor.strftime("%Y-%m-%d") not in dates_set:
        return 0

    streak = 0
    while cursor.strftime("%Y-%m-%d") in dates_set:
        streak += 1
        cursor -= timedelta(days=1)
    return streak