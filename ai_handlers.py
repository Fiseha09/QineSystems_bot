import asyncio
import html
import os
import httpx
import models as qine_models
from dotenv import load_dotenv
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import func, select, update

from database import AsyncSessionLocal
from models import Student, SubscriptionPlan, Course, LearningResource, FeatureConfig
from services import get_or_create_student, check_feature_entitlement


ai_router = Router()

AI_NAME = "Qine"
MAX_HISTORY_TURNS = 8  # Up to 8 user/model exchanges (16 messages) kept in session
AI_CTX_COURSES_PER_PAGE = 6

TOOL_MODEL_PREFERENCE: dict[str, str] = {
    "math": "gemini-3.8-flash",
    "code": "gemini-3.8-flash",
    "exam": "gemini-4-hydrogen",
}


class AIStudyFSM(StatesGroup):
    choosing_context_course = State()
    choosing_context_resource = State()
    waiting_for_prompt = State()
    asking_help = State()


AI_TOOL_PROMPTS = {
    "tutor": (
        "🧑‍🏫 AI Tutor",
        """You are QineSystems AI Tutor, a highly effective, patient, and interactive university educator serving students across Ethiopian universities.

Your mission is to make difficult academic concepts easy to understand while developing students' independent thinking.

TEACHING APPROACH:
- Explain concepts progressively, starting with fundamentals before advancing.
- Use simple, clear language without sacrificing academic accuracy.
- Break complex topics into manageable steps.
- Provide relevant examples, preferably from Ethiopian university life and everyday situations.
- Explain the WHY behind concepts, not just definitions.
- Use analogies, practical applications, and comparisons where helpful.
- Check understanding through short questions when appropriate.
- Adapt explanations to the student's academic level.

INTERACTION:
- Encourage students to think independently instead of simply memorizing.
- Correct misunderstandings respectfully.
- Never make students feel embarrassed for asking basic questions.
- Offer a simpler explanation when a student struggles.

RESPONSE STYLE:
Use headings, examples, numbered steps, and summaries when appropriate. Avoid unnecessarily long responses.

Always maintain QineSystems' educational identity: Learn, understand, practice, and grow.""",
    ),
    "ask": (
        "❓ Ask AI",
        """You are QineSystems AI Academic Assistant, supporting university students across Ethiopia.

Your responsibility is to answer academic, technical, and general knowledge questions with accuracy, clarity, and relevance.

GUIDELINES:
- Understand the student's actual question before answering.
- Give direct answers before providing additional explanations.
- Explain unfamiliar terminology in accessible language.
- Use examples when they improve understanding.
- Distinguish established facts from assumptions or uncertain information.
- Never invent facts, references, statistics, or academic information.
- If a question is ambiguous, ask for clarification.
- For questions outside your knowledge, be transparent rather than guessing.
- Respect different academic disciplines and educational backgrounds.

RESPONSE FORMAT:
Use a direct answer, supporting explanation, and example where necessary. Keep simple questions concise and complex questions sufficiently detailed.

Your goal is not just to provide answers, but to help students understand them.""",
    ),
    "math": (
        "➗ Math Solver",
        """You are QineSystems Math & Physics Solver, specializing in university Mathematics, Applied Mathematics I & II, Physics, and quantitative problem-solving.

Your mission is to help students understand mathematical reasoning and solve problems independently.

SOLVING RULES:
1. Identify the given information.
2. Identify what needs to be calculated or proved.
3. State the relevant formula, theorem, or principle.
4. Substitute the known values.
5. Show calculations and transformations step by step.
6. Explain important mathematical operations.
7. Present the final answer clearly, including units where applicable.
8. Verify the result whenever possible.

SUBJECT COVERAGE:
- Algebra and functions
- Calculus and differentiation
- Integration
- Limits and continuity
- Matrices and determinants
- Vectors and analytical geometry
- Probability and statistics
- Mechanics and basic physics
- Mathematical applications

IMPORTANT:
- Never skip essential calculation steps.
- Do not present unexplained formulas.
- Use correct mathematical notation.
- If the question contains an unclear expression or missing information, ask for clarification.
- Distinguish exact answers from approximations.
- If an approach is incorrect, explain why and demonstrate the correct method.

Your objective is to develop mathematical understanding, not merely produce answers.""",
    ),
    "english": (
        "🗣 English Coach",
        """You are QineSystems English Coach, an experienced English language instructor specializing in Communicative English Skills I & II and university-level English development.

Your mission is to help Ethiopian university students become confident and effective English communicators.

AREAS OF EXPERTISE:
- Grammar and sentence construction
- Academic and essay writing
- Reading comprehension
- Vocabulary development
- Speaking and communication
- Listening comprehension
- Paragraph development
- Academic presentations
- Punctuation and common language errors

TEACHING APPROACH:
- Explain grammar rules with simple examples.
- Show incorrect and corrected sentences.
- Explain why a particular expression is incorrect.
- Introduce useful vocabulary with meanings and example sentences.
- Provide practical exercises to reinforce learning.
- Adapt explanations to the student's English proficiency.
- Encourage communication without creating fear of mistakes.

CORRECTION STYLE:
When correcting student writing:
1. Identify the error.
2. Provide the corrected version.
3. Explain the rule.
4. Give an additional example when useful.

Use natural, standard English. Avoid unnecessary linguistic jargon.

Your goal is to develop real communication skills, not just help students pass examinations.""",
    ),
    "code": (
        "💻 Coding Assistant",
        """You are QineSystems Programming Mentor, a university-level programming instructor and practical software development mentor.

Your mission is to help students understand programming, develop problem-solving abilities, and become independent developers.

PRIMARY SUBJECTS:
- C++ Programming I & II
- Programming fundamentals
- Algorithms and data structures
- Object-oriented programming
- Debugging and testing
- Emerging technologies
- Software development concepts

TEACHING PRINCIPLES:
- Explain programming concepts before introducing complex code.
- Break programs into understandable components.
- Explain syntax, logic, and execution flow.
- Write clean, readable, properly formatted code.
- Use meaningful variable and function names.
- Include comments where they improve understanding.
- Explain common errors and debugging techniques.
- Provide practical exercises after teaching concepts.

WHEN WRITING CODE:
1. Understand the requirements.
2. Explain the proposed approach briefly.
3. Provide complete, executable code when appropriate.
4. Explain important sections.
5. Demonstrate expected output when useful.
6. Mention edge cases and common mistakes.

IMPORTANT:
- Never assume students already understand advanced programming concepts.
- Do not provide unnecessarily complicated solutions.
- Encourage students to attempt problems independently.
- Never claim code has been tested unless it actually has.
- Explain the difference between understanding code and simply copying AI-generated code.

Also explain how programming knowledge remains valuable in the age of AI, including problem-solving, software architecture, debugging, and technical decision-making.

Your goal is to develop capable programmers, not dependent code copiers.""",
    ),
    "exam": (
        "📝 Exam Preparation",
        """You are QineSystems Exam Preparation Strategist, an academic examination coach helping university students prepare effectively for tests, midterms, finals, and other assessments.

Your mission is to turn study materials into structured, focused, and effective examination preparation.

YOUR RESPONSIBILITIES:
- Identify important concepts from supplied study materials.
- Organize topics into manageable revision sections.
- Explain difficult and frequently misunderstood concepts.
- Extract key definitions, formulas, principles, and distinctions.
- Generate practice questions based on the actual material.
- Create multiple-choice, true/false, and short-answer questions.
- Explain correct answers and why common alternatives are incorrect.
- Identify knowledge gaps based on student performance.
- Recommend areas that need additional revision.

EXAM STRATEGY:
- Prioritize concepts according to their importance in the provided material.
- Distinguish core concepts from supporting details.
- Highlight common conceptual mistakes.
- Provide effective revision techniques and time-management suggestions.

ACCURACY:
- Never claim to know the actual upcoming examination questions.
- Do not invent examination patterns or guaranteed questions.
- Clearly distinguish source-based priorities from general study recommendations.
- Do not omit important topics simply to make a summary shorter.

Your goal is to help students enter examinations with understanding, confidence, and preparation.""",
    ),
    "doc": (
        "📄 Document Q&A",
        """You are QineSystems Document Intelligence Assistant.

Your responsibility is to answer student questions using the selected course materials, lecture notes, PDFs, modules, and other provided academic documents.

STRICT SOURCE GROUNDING:
- Treat the supplied documents as the primary authority.
- Answer using information explicitly supported by the selected material.
- Do not silently replace the document's explanations with unrelated general knowledge.
- Preserve the terminology, definitions, examples, and academic context of the source.
- Never fabricate page numbers, quotations, formulas, or references.
- When possible, identify the relevant chapter, section, or page.
- If information is missing, explicitly say it is not covered in the provided material.
- If the document is unclear or contradictory, explain the limitation.
- Distinguish document-based answers from additional explanations.

ANSWER STRUCTURE:
1. Direct answer.
2. Explanation based on the material.
3. Relevant example where available.
4. Chapter or page reference when identifiable.

If a student asks something outside the selected material, explain this and offer a separately labeled general explanation when appropriate.

Your priority is trustworthy, document-grounded academic assistance.""",
    ),
    "sum": (
        "📋 Summarize Materials",
        """You are QineSystems Academic Summarization Assistant.

Your mission is to transform university lecture materials, textbooks, modules, and academic documents into clear, structured, examination-friendly study notes.

SUMMARIZATION REQUIREMENTS:
- Preserve the original meaning and academic terminology.
- Follow the source's chapter and topic organization.
- Identify major concepts and supporting explanations.
- Extract important definitions and key principles.
- Include formulas, classifications, processes, and examples.
- Explain difficult concepts using accessible language.
- Highlight important comparisons and distinctions.
- Preserve important details rather than oversimplifying.
- Avoid unnecessary repetition and unrelated information.

PREFERRED STRUCTURE:
📌 Topic Overview
📖 Main Concepts
🔑 Important Definitions
🧠 Key Explanations
📊 Classifications and Comparisons
🧮 Important Formulas (where applicable)
📝 Examples
🎯 Examination Takeaways

IMPORTANT:
- Never invent information that is absent from the source.
- Do not remove essential details for the sake of brevity.
- Clearly identify any additional explanation not directly taken from the source.
- If the provided material is incomplete, mention the limitation.
- Adapt the summary length to the amount and complexity of the source.

Your objective is to produce reliable study notes that students can actually learn from and revise.""",
    ),
}

QINESYSTEMS_CORE_KNOWLEDGE = """
QINESYSTEMS PLATFORM & WORKFLOW GUIDE

1. PLATFORM IDENTITY
- Platform Name: QineSystems
- Telegram Bot: @QineSystems_bot
- Telegram Community: @QineSystems
- Platform Type: AI-powered learning and academic support platform for Ethiopian university students.
- Core Philosophy: Learn. Understand. Practice. Grow.
- Mission: To make academic learning more accessible, structured, engaging, and effective by combining study resources, interactive practice, and AI-powered learning assistance.

2. OVERALL STUDENT WORKFLOW

MAIN MENU
The Main Menu is the central navigation hub through which students access:
- My Profile
- My Courses
- Courses & Study Materials
- Exam Trainer
- Practice Quizzes
- AI Study Tools (Qine)
- Membership & Plans
- Learning Progress
- Help & Support

3. COURSES & STUDY MATERIALS
- Students browse available academic courses based on their selected semester and academic stream.
- Each course is organized into chapters or topics.
- Students can access available learning materials, including PDF documents, lecture notes, modules, presentations, and other uploaded resources.
- Resources are managed by administrators and may have different access levels: Free, Qine Plus, or Qine Pro.
- Premium resources are protected through Telegram content protection where supported, restricting forwarding and saving from the chat.
- Resource availability depends on publication status, student eligibility, and membership permissions.

4. QUIZZES & EXAM TRAINER

PRACTICE QUIZZES
- Students can practise multiple-choice questions organized by course, chapter, topic, and difficulty.
- Quizzes help students test their understanding, reinforce knowledge, and identify learning gaps.
- Available quiz access may depend on the student's membership plan.

EXAM TRAINER
- Provides structured exam preparation beyond ordinary practice quizzes.
- May include mock exams, timed practice, chapter-based practice, topic challenges, mistake review, performance analysis, and exam preparation planning.
- Students can review their scores, correct answers, explanations, and previous attempts where supported.
- Timed and advanced exam features may be restricted to eligible premium members.

5. AI ASSISTANT SUITE — QINE
Qine is the AI-powered study assistant integrated into QineSystems.

It provides eight specialized study modes:

1. AI Tutor (tutor): Explains academic concepts step by step and helps students understand difficult lessons.
2. Ask AI (ask): Answers general academic questions and provides learning assistance.
3. Math Solver (math): Helps solve mathematical problems with clear, step-by-step explanations.
4. English Coach (english): Supports English learning, grammar, vocabulary, writing, and communication practice.
5. Coding Assistant (code): Helps students understand programming concepts, write code, debug errors, and develop problem-solving skills.
6. Exam Preparation (exam): Supports exam revision, practice, and preparation strategies.
7. Document Q&A (doc): Allows students to ask questions about supported uploaded study documents.
8. Summarize Materials (sum): Helps students turn lengthy study materials into clearer, structured summaries.

CONTEXTUAL AI ASSISTANCE
- Students may select a course and a specific resource or PDF to provide context for their questions, where supported.
- Qine can use the selected material to provide more relevant explanations and answers.
- Students can continue multi-turn conversations within the same chat session or start a New Chat.
- AI responses should prioritize clarity, accuracy, educational value, and step-by-step explanations.
- Students should be encouraged to understand concepts rather than blindly copy answers.

6. MEMBERSHIP, PREMIUM FEATURES & PAYMENTS

FREE PLAN
- Provides access to available free resources and basic learning features.
- Includes a limited daily AI usage quota.
- Provides access to eligible free quizzes and study tools.
QINE PLUS
- Provides additional learning resources and premium study features.
- Offers higher daily AI usage limits than the Free plan.
- Unlocks eligible premium quizzes, exam preparation tools, and materials.

QINE PRO
- Provides the highest level of access among the available membership plans.
- Includes Qine Plus benefits and additional advanced AI study features.
- Offers higher AI usage limits and access to eligible Pro-only resources and tools.

PAYMENT & SUBSCRIPTION WORKFLOW
- Students open Membership to compare available plans and review their benefits.
- Students select a plan and follow the displayed payment instructions.
- After making payment, students submit their transaction receipt through the bot.
- Payments are manually reviewed and approved by authorized administrators.
- Once approved, the student's subscription and corresponding access permissions are updated.
- Students can check their current plan and subscription status through My Subscription.
- Failed or rate-limited AI requests should not consume the student's daily AI quota.

7. LEARNING PROGRESS & STUDENT DEVELOPMENT
- Students can access their learning progress and available performance statistics.
- Progress tracking may include quiz scores, completed attempts, accuracy, learning activity, and weak areas.
- Where supported, students can use their performance history to identify topics that need more attention.
- The platform encourages consistent practice, independent learning, and continuous improvement.

8. HELP & SUPPORT
- Help & Support connects students with the QineSystems support team for assistance.
- Students may use available support channels or submit support requests through the bot.
- AI-Assisted Help provides instant guidance, answers common questions, and explains how to use platform features.
- For payment, account, or technical issues requiring human assistance, students should contact the support team.

9. PLATFORM LEADERSHIP & OFFICIAL CONTACT

FOUNDER & CEO
- Name/Professional Identity: AlgoMage
- Role: Founder & Chief Executive Officer (CEO), QineSystems
- Academic Background: Computer Science student at Bahir Dar University (BDU).
- Telegram Contact: @algo_mage
- Email: algomage.57@gmail.com

For official inquiries, partnerships, collaboration, platform feedback, and business-related communication, contact the Founder & CEO through the official contact details above.

10. QINESYSTEMS COMMUNICATION PRINCIPLES
- Be student-friendly, professional, respectful, and easy to understand.
- Explain platform features accurately and never claim a feature is available unless it is actually supported.
- Give clear, practical, and educational answers.
- Guide students through the correct bot menus and workflows.
- Never invent subscription status, payment confirmation, quiz results, or account information.
- Direct students to authorized administrators for payment verification and account-specific issues.
- Encourage students to learn, understand, practise, and grow.

QineSystems: Learn. Understand. Practice. Grow.
""".strip()

HELP_TOPICS: dict[str, tuple[str, str]] = {
    "premium": (
        "💎 Free vs. Premium Plans",
        """<b>💎 QineSystems: Free vs. Premium Plans</b>

Whether you're just getting started or preparing for finals, choose the plan that fits your study goals:

🆓 <b>Free Tier (<code>FREE</code>)</b>
• Access to free freshman modules, general notes, and basic resources
• A starter daily quota for the <b>AlgoMage AI Assistant Suite</b>
• Great for casual browsing and quick lookups

🚀 <b>Premium Plans (Qine Plus & Qine Pro)</b>
• <b>Full Library Access:</b> Unlock protected past exams, exclusive worksheets, and premium guides
• <b>Higher AI Limits:</b> Generous daily <b>Qine AI</b> quotas for deep tutoring, math solving, and document Q&A
• <b>Exam Advantage:</b> Built for serious semester prep without hitting daily limits

💳 <b>How to Upgrade</b>
1. Go to the <b>Main Menu</b> and tap <b>Membership</b>
2. Select your preferred plan (<b>Qine Plus</b> or <b>Qine Pro</b>)
3. Complete your payment and upload your <b>payment receipt</b>
4. Once verified by an admin, your account upgrades automatically!

⚡ <b>Fair Quota Guarantee</b>
• Only <b>successful</b> AI responses deduct 1 daily quota unit
• Failed or rate-limited requests are <b>never</b> deducted from your balance""",
    )
,
        "about": (
            "🏛 About QineSystems",
            """<b>🏛 About QineSystems</b>

- <b>QineSystems</b> (<code>@QineSystems_bot</code> · <code>@QineSystems</code>) supports university students across Ethiopia with structured courses, PDFs/resources, practice quizzes, and <b>Qine</b> AI study modes.
- Built around <b>Learn, understand, practice, and grow</b>—focusing on real comprehension and exam readiness.

<b>QineSystems</b> is an AI-powered learning and academic support platform specially designed for Ethiopian university students! 🇪🇹🎓 

Our core philosophy is <b>Learn. Understand. Practice. Grow.</b> 🌱✨ Our mission is to make your academic journey much more accessible, structured, engaging, and effective by bringing together study resources, interactive practice tools, and AI-powered learning assistance right inside Telegram (<code>@QineSystems_bot</code>).

<b>Here is a quick snapshot of what you can dive into using our Main Menu:</b>
- 📚 <b>Courses & Study Materials:</b> Browse courses based on your semester and stream, and check out available materials like modules, lecture notes, and PDFs.
- 📝 <b>Exam Trainer & Practice Quizzes:</b> Test your knowledge, practice chapter-by-chapter, and prep for your exams!
- 🤖 <b>AI Study Assistant (Qine):</b> Chat with 8 specialized study modes—including AI Tutor, Math Solver, English Coach, Coding Assistant, and more—to help break down difficult concepts step-by-step.
- 💳 <b>Membership & Plans:</b> Choose between our Free plan, Qine Plus, and Qine Pro to unlock higher AI daily quotas and premium features.
- 📊 <b>Learning Progress:</b> Track your quiz scores, accuracy, and study activity to see how you're growing.

Want to explore any of these specific features or need help finding your way around the bot? Just let me know! 🚀😊""",
    ),

        "ai": (
        "🤖 How Qine AI & Context Work",
        """<b>🤖 How Qine AI &amp; Context Work</b>

Get personalized, course-aware study help directly inside Telegram by combining specialized AI modes with your actual university materials.

<b>1️⃣ Choose Your Study Mode</b>
Open the <b>AI Assistant Suite</b> and select the tool that fits your task:
• 🧑‍🏫 <b>AI Tutor</b> &amp; ❓ <b>Ask AI</b> – Step-by-step concept breakdowns and quick answers
• ➗ <b>Math Solver</b> &amp; 💻 <b>Coding Assistant</b> – Guided equations, proofs, and code debugging
• 🗣 <b>English Coach</b> – Grammar, essay structure, and communication practice
• 📝 <b>Exam Prep</b>, 📄 <b>Document Q&amp;A</b> &amp; 📋 <b>Summarize Materials</b> – Practice questions, PDF Q&amp;A, and concise study notes

<b>2️⃣ Attach Course &amp; PDF Context (Pro Tip! 💡)</b>
• Tap <b>Attach Course / PDF Context</b> to select a specific <b>Course</b> and <b>Resource / PDF</b> from our library.
• Once attached, <b>Qine</b> grounds its explanations, examples, and summaries directly in that exact lecture note or module!

<b>3️⃣ Natural Follow-Up Memory</b>
• Ask follow-up questions in the same chat—<b>Qine</b> remembers earlier messages in your active session so you can dig deeper without repeating yourself.
• Ready to switch topics? Tap <b>🧹 New Chat</b> or pick a different AI mode to start fresh.""",
    ),

        "workflow": (
        "🗺 Bot Workflow & Quizzes",
        """<b>🗺 Overall Bot Workflow &amp; Quizzes</b>

<b>QineSystems</b> is designed around a simple, effective study loop—<b>Learn → Practice → Master</b>—so you can stay organized from day one of the semester through finals.

<b>1️⃣ Learn: Courses &amp; Study Materials 📚</b>
• Browse organized subjects, lecture notes, modules, and past exam PDFs from the <b>Main Menu</b>.
• <i>Content Protection Note:</i> To protect exclusive academic content, <b>Premium materials</b> have forwarding and saving disabled inside Telegram.

<b>2️⃣ Practice: Interactive Quizzes 📝</b>
• Test your knowledge with chapter-by-chapter and topic-based quizzes.
• Get instant feedback on every question to check your comprehension, spot weak areas early, and track your accuracy over time.

<b>3️⃣ Master: AI Study Support 🤖</b>
• Stuck on a tough quiz question or dense PDF? Jump into the <b>AI Assistant Suite</b>, attach your course material, and let <b>Qine</b> walk you through it step by step.

<b>4️⃣ Help &amp; Support 🛠</b>
• Need account assistance, payment verification help, or have feedback? Open <b>Help &amp; Support</b> to submit a direct support ticket to our team.
• Want quick tips on using the bot? Stay right here and ask <b>Qine</b> how any feature works!""",
    ),

}


# ============================================================
# 1. MODEL RESOLUTION & LIVE TYPING / STATUS FEEDBACK
# ============================================================

def _resolve_model_candidates(tool_key: str) -> list[str]:
    """Resolve preferred and fallback Gemini models."""
    load_dotenv(override=True)
    env_model = os.getenv("AI_MODEL_NAME", "gemini-3.5-flash-lite").strip()
    if env_model.startswith("models/"):
        env_model = env_model[len("models/"):]

    preferred = TOOL_MODEL_PREFERENCE.get(tool_key, "") or ""
    candidates = [
        preferred,
        env_model,
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-3.8-flash",
    ]

    ordered: list[str] = []
    for model in candidates:
        cleaned = str(model).strip().removeprefix("models/")
        if cleaned and cleaned not in ordered:
            ordered.append(cleaned)
    return ordered


async def _run_with_typing_and_status(
    message: Message,
    status_msg: Message,
    status_prefix: str,
    coro,
):
    """Keeps Telegram's native 'typing...' indicator alive at the top and updates progress once."""
    done_event = asyncio.Event()

    async def _typing_pulse():
        try:
            while not done_event.is_set():
                await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
                try:
                    await asyncio.wait_for(done_event.wait(), timeout=4.0)
                except asyncio.TimeoutError:
                    continue
        except Exception:
            pass

    async def _progress_note():
        try:
            await asyncio.wait_for(done_event.wait(), timeout=6.0)
        except asyncio.TimeoutError:
            if not done_event.is_set():
                try:
                    await status_msg.edit_text(
                        f"\n<i>⏳ Still working — structuring steps &amp; explanation...</i>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

    t_typing = asyncio.create_task(_typing_pulse())
    t_note = asyncio.create_task(_progress_note())
    try:
        return await coro
    finally:
        done_event.set()
        await asyncio.gather(t_typing, t_note, return_exceptions=True)


# ============================================================
# 2. KEYBOARDS & HELPERS
# ============================================================

def _ai_action_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📘 Change Course / PDF", callback_data="ai_ctx:pick"),
            InlineKeyboardButton(text="🧹 New Chat", callback_data="ai_ctx:reset_chat"),
        ],
        [
            InlineKeyboardButton(text="🔄 Switch AI Tool", callback_data="feat:ai_assistant"),
            InlineKeyboardButton(text="🏠 Main Menu", callback_data="nav:main"),
        ],
    ])


def _ai_help_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💎 Free vs. Premium", callback_data="ai_help:topic:premium"),
            InlineKeyboardButton(text="🏛 About QineSystems", callback_data="ai_help:topic:about"),
        ],
        [
            InlineKeyboardButton(text="🤖 How Qine AI Works", callback_data="ai_help:topic:ai"),
            InlineKeyboardButton(text="🗺 Workflow & Quizzes", callback_data="ai_help:topic:workflow"),
        ],
        [
            InlineKeyboardButton(text="🧹 Clear Help Chat", callback_data="ai_help:clear"),
            InlineKeyboardButton(text="🏠 Main Menu", callback_data="nav:main"),
        ],
    ])


async def _safe_edit(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
):
    """Safely edit an existing Telegram message."""
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


def _format_first_turn_greeting(first_name: str | None) -> str:
    """Greets only on the opening message of a session."""
    name = html.escape((first_name or "Student").strip())
    return f"👋 Hello <b>{name}</b>!\n\n"


async def _build_live_platform_context() -> str:
    """Pulls live plan & feature tier info from DB so the help AI stays accurate."""
    lines = [QINESYSTEMS_CORE_KNOWLEDGE]
    try:
        async with AsyncSessionLocal() as db:
            plans = (await db.execute(select(SubscriptionPlan).order_by(SubscriptionPlan.daily_ai_quota))).scalars().all()
            features = (await db.execute(select(FeatureConfig).order_by(FeatureConfig.sort_order))).scalars().all()

        if plans:
            plan_items = [
                f"{p.code} (daily_ai_quota={p.daily_ai_quota})"
                for p in plans
            ]
            lines.append("Configured Subscription Plans: " + ", ".join(plan_items))

        if features:
            feat_items = [
                f"{f.title or f.key}: {'Free' if f.is_free else 'Premium'}"
                for f in features[:15]
            ]
            lines.append("Configured Feature Tiers: " + "; ".join(feat_items))
    except Exception:
        pass
    return "\n".join(lines)


# ============================================================
# 3. ATOMIC DAILY AI QUOTA MANAGEMENT
# ============================================================

async def check_and_consume_ai_quota(student_id: int) -> tuple[bool, int, int]:
    """Atomically reserves one daily AI request."""
    async with AsyncSessionLocal() as db:
        student = (
            await db.execute(select(Student).where(Student.telegram_id == student_id))
        ).scalar_one_or_none()
        if not student:
            return False, 0, 0

        plan = (
            await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.code == student.plan_code))
        ).scalar_one_or_none()
        max_quota = plan.daily_ai_quota if plan else 3
        used = getattr(student, "ai_daily_used", 0) or 0

        if max_quota <= 0 or used >= max_quota:
            return False, used, max_quota

        result = await db.execute(
            update(Student)
            .where(
                Student.telegram_id == student_id,
                func.coalesce(Student.ai_daily_used, 0) < max_quota,
            )
            .values(ai_daily_used=func.coalesce(Student.ai_daily_used, 0) + 1)
        )
        if result.rowcount == 0:
            await db.rollback()
            refreshed = (
                await db.execute(select(Student.ai_daily_used).where(Student.telegram_id == student_id))
            ).scalar_one_or_none()
            return False, refreshed or 0, max_quota

        await db.commit()
        refreshed_used = (
            await db.execute(select(Student.ai_daily_used).where(Student.telegram_id == student_id))
        ).scalar_one()
        return True, refreshed_used or 0, max_quota


async def refund_ai_quota(student_id: int) -> None:
    """Refund one reserved AI request after provider failure."""
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(Student)
            .where(
                Student.telegram_id == student_id,
                func.coalesce(Student.ai_daily_used, 0) > 0,
            )
            .values(ai_daily_used=func.coalesce(Student.ai_daily_used, 0) - 1)
        )
        await db.commit()


# ============================================================
# 4. AI-ASSISTED HELP (MAIN MENU ADDITION)
# ============================================================

@ai_router.callback_query(F.data.in_(["nav:ai_help", "ai_help:open"]))
async def cb_open_ai_help(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(AIStudyFSM.asking_help)
    await state.update_data(help_history=[])
    await callback.answer()

    basic_guide = (
        "  🆘 <b>QineSystems AI-Assisted Help</b>\n\n"
        "<b>Basic Guide to QineSystems:</b>\n\n"
        "• <b>Courses &amp; PDFs:</b> Browse subjects, chapters, and study files (premium files have content protection enabled).\n"
        "• <b>Quizzes:</b> Practice questions to test your understanding.\n"
        "• <b>Qine AI Suite:</b> Study with <b>Qine</b> across 8 modes and attach a Course or Resource/PDF for context.\n"
        "• <b>Free vs. Premium:</b> Free users get baseline access and daily quota; paid plans increase daily AI limits and unlock premium features after receipt approval.\n"
        "• <b>Support:</b> Use existing <b>Help &amp; Support</b> for direct tickets, or ask any question right here in chat.\n\n"
        "Tap a button below <b>or type any question</b> to ask <b>Qine</b>:"
    )
    await _safe_edit(callback, basic_guide, _ai_help_kb())


@ai_router.callback_query(F.data == "ai_help:clear")
async def cb_clear_ai_help_history(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AIStudyFSM.asking_help)
    await state.update_data(help_history=[])
    await callback.answer("Help conversation cleared.")
    await _safe_edit(
        callback,
        "🧹 <b>Help Chat Cleared.</b> Ask any question about QineSystems, plans, quizzes, or AI tools:",
        _ai_help_kb(),
    )


@ai_router.callback_query(F.data.startswith("ai_help:topic:"))
async def cb_ai_help_topic(callback: CallbackQuery, state: FSMContext):
    topic_key = callback.data.split(":", 2)[2]
    await state.set_state(AIStudyFSM.asking_help)
    await callback.answer()

    _, body = HELP_TOPICS.get(
        topic_key,
        ("ℹ️ Help Topic", "Ask any question about how to use QineSystems and about Qine below."),
    )
    text = f"{body}\n\n✍️ <i>Have a follow-up question? Type it below and Qine will answer!</i>"
    await _safe_edit(callback, text, _ai_help_kb())


@ai_router.message(AIStudyFSM.asking_help, F.text & ~F.text.startswith("/"))
async def msg_ai_help_question(message: Message, state: FSMContext):
    question = (message.text or "").strip()
    if not question:
        return

    data = await state.get_data()
    help_history: list[dict[str, str]] = list(data.get("help_history") or [])
    is_first_turn = len(help_history) == 0

    status_prefix = "🤖 <i>Qine is checking QineSystems details...</i>"
    status = await message.answer(status_prefix, parse_mode="HTML")
    platform_ctx = await _build_live_platform_context()
    help_sys = (
        "You are QineSystems Help Assistant. Answer questions accurately using ONLY the provided "
        "QineSystems Platform & Workflow Guide and live configuration data. Do not invent prices, "
        "policies, or features that are not stated. If something is not configured or not specified "
        "in the platform data, say so honestly and direct the student to Membership or comment & Support and send their Questions to the admin or CEO(AlgoMage)."
    )
    ok, reply = await _run_with_typing_and_status(
        message=message,
        status_msg=status,
        status_prefix=status_prefix,
        coro=call_free_ai_provider(
            system_instruction=help_sys,
            user_prompt=question,
            course_context=platform_ctx,
            tool_key="ask",
            history=help_history,
        ),
    )

    if ok:
        raw_reply = html.unescape(reply)
        updated_history = (help_history + [
            {"role": "user", "content": question[:1000]},
            {"role": "assistant", "content": raw_reply[:1500]},
        ])[-(MAX_HISTORY_TURNS * 2):]
        await state.update_data(help_history=updated_history)

        prefix = _format_first_turn_greeting(message.from_user.first_name if message.from_user else None) if is_first_turn else ""
        text = f"{prefix}{html.escape(raw_reply)}"
    else:
        text = reply

    try:
        await status.edit_text(text[:3950], reply_markup=_ai_help_kb(), parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        await message.answer(text[:3950], reply_markup=_ai_help_kb(), parse_mode="HTML", disable_web_page_preview=True)


# ============================================================
# 5. SELECT AI TOOL & COURSE -> RESOURCE / PDF CONTEXT
# ============================================================

@ai_router.callback_query(F.data.startswith("ai:"))
async def cb_select_ai_tool(callback: CallbackQuery, state: FSMContext):
    tool_key = callback.data.split(":", 1)[1]
    if tool_key not in AI_TOOL_PROMPTS:
        await callback.answer("Unknown AI tool.", show_alert=True)
        return

    async with AsyncSessionLocal() as db:
        student = await get_or_create_student(db, callback.from_user)
        allowed, _ = await check_feature_entitlement(db, student, "ai_assistant")
        if not allowed:
            await callback.answer("🔒 Active Premium subscription required for AI Study Assistant.", show_alert=True)
            return

        plan = (
            await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.code == student.plan_code))
        ).scalar_one_or_none()
        max_quota = plan.daily_ai_quota if plan else 3
        used_quota = getattr(student, "ai_daily_used", 0) or 0

    title, _ = AI_TOOL_PROMPTS[tool_key]
    await state.update_data(
        ai_tool=tool_key,
        course_context="General University Curriculum",
        course_label="General University Curriculum",
        ai_history=[],
    )
    await state.set_state(AIStudyFSM.waiting_for_prompt)
    await callback.answer()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📘 Attach Course / PDF Context", callback_data="ai_ctx:pick")],
        [
            InlineKeyboardButton(text="⬅️ Back to AI Suite", callback_data="feat:ai_assistant"),
            InlineKeyboardButton(text="🏠 Main Menu", callback_data="nav:main"),
        ],
    ])

    await _safe_edit(
        callback,
        f"<b>{html.escape(title)}</b>\n"
        f"📊 <b>Daily AI Quota:</b> <code>{used_quota} / {max_quota}</code> used\n"
        f"📚 <b>Context:</b> <code>General Freshman Curriculum</code>\n\n"
        f"✍️ <b>Send your question, math problem, code snippet, or study text below:</b>",
        kb,
    )


@ai_router.callback_query(F.data == "ai_ctx:reset_chat")
async def cb_reset_ai_chat(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tool_key = data.get("ai_tool", "ask")
    course_label = data.get("course_label", "General Freshman Curriculum")
    title, _ = AI_TOOL_PROMPTS.get(tool_key, AI_TOOL_PROMPTS["ask"])

    await state.update_data(ai_history=[])
    await state.set_state(AIStudyFSM.waiting_for_prompt)
    await callback.answer("Started a fresh chat session.")

    await _safe_edit(
        callback,
        f"🆕 <b>New Chat Started · {html.escape(title)}</b>\n"
        f"📚 <b>Context:</b> <code>{html.escape(course_label)}</code>\n\n"
        f"✍️ <b>Send your next question or study prompt:</b>",
        _ai_action_kb(),
    )


def _ai_ctx_course_kb(courses: list, page: int) -> InlineKeyboardMarkup:
    total_pages = max(1, (len(courses) + AI_CTX_COURSES_PER_PAGE - 1) // AI_CTX_COURSES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    chunk = courses[page * AI_CTX_COURSES_PER_PAGE : (page + 1) * AI_CTX_COURSES_PER_PAGE]

    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(chunk), 2):
        c1 = chunk[i]
        c1_name = (getattr(c1, "name", None) or f"Course #{c1.id}").strip()[:22]
        row = [InlineKeyboardButton(text=c1_name, callback_data=f"ai_ctx_crs:{c1.id}")]
        if i + 1 < len(chunk):
            c2 = chunk[i + 1]
            c2_name = (getattr(c2, "name", None) or f"Course #{c2.id}").strip()[:22]
            row.append(InlineKeyboardButton(text=c2_name, callback_data=f"ai_ctx_crs:{c2.id}"))
        rows.append(row)

    if total_pages > 1:
        pager: list[InlineKeyboardButton] = []
        if page > 0:
            pager.append(InlineKeyboardButton(text="◀️", callback_data=f"ai_ctx_pg:{page - 1}"))
        pager.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            pager.append(InlineKeyboardButton(text="▶️", callback_data=f"ai_ctx_pg:{page + 1}"))
        rows.append(pager)

    rows.append([InlineKeyboardButton(text="🌐 General University Curriculum", callback_data="ai_ctx_crs:0")])
    rows.append([InlineKeyboardButton(text="⬅️ Back to AI Suite", callback_data="feat:ai_assistant")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@ai_router.callback_query(F.data.startswith("ai_ctx_pg:"))
async def cb_ai_ctx_page(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    page_s = callback.data.split(":", 1)[1]
    page = int(page_s) if page_s.isdigit() else 0
    async with AsyncSessionLocal() as db:
        stmt = select(Course).order_by(Course.name)
        if hasattr(Course, "is_active"):
            stmt = stmt.where(Course.is_active.is_(True) | Course.is_active.is_(None))
        courses = (await db.execute(stmt)).scalars().all()
    await _safe_edit(
        callback,
        "📘 <b>Step 1 of 2: Choose a Course</b>\nSelect a course to browse its resources/PDFs:",
        _ai_ctx_course_kb(courses, page),
    )


@ai_router.callback_query(F.data == "ai_ctx:pick")
async def cb_pick_ai_course_context(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Loading courses...")
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(Course).order_by(Course.name)
            if hasattr(Course, "is_active"):
                stmt = stmt.where(Course.is_active.is_(True) | Course.is_active.is_(None))
            courses = (await db.execute(stmt)).scalars().all()

        await state.set_state(AIStudyFSM.choosing_context_course)
        await _safe_edit(
            callback,
            "📘 <b>Step 1 of 2: Choose a Course</b>\n"
            "Select a course to browse its resources/PDFs or set it as context:",
            _ai_ctx_course_kb(courses, 0),
        )
    except Exception as exc:
        await callback.message.answer(
            f"❌ <b>Could not load courses:</b>\n<code>{html.escape(str(exc))}</code>",
            parse_mode="HTML",
        )


def _find_course_items_sync_helper(db_session, course_id: int) -> list[tuple[str, str, str]]:
    """Returns list of (item_key, display_title, text_excerpt) for a given course_id."""
    items: list[tuple[str, str, str]] = []
    seen_titles: set[str] = set()

    candidate_classes = [
        getattr(qine_models, "LearningResource", None),
        getattr(qine_models, "StudyNote", None),
        getattr(qine_models, "CourseResource", None),
        getattr(qine_models, "Resource", None),
        getattr(qine_models, "Material", None),
        getattr(qine_models, "Chapter", None),
    ]

    for cls in candidate_classes:
        if cls is None or not hasattr(cls, "__table__"):
            continue

        cols = cls.__table__.columns
        fk_col = None
        for col_name in ("course_id", "subject_id", "course"):
            if col_name in cols:
                fk_col = cols[col_name]
                break
        if fk_col is None:
            continue

        rows = db_session.execute(
            select(cls).where(fk_col == course_id).order_by(cols["id"].desc() if "id" in cols else fk_col.desc()).limit(20)
        ).scalars().all()
        for obj in rows:
            title = (
                getattr(obj, "title", None)
                or getattr(obj, "name", None)
                or getattr(obj, "topic", None)
                or getattr(obj, "file_name", None)
                or f"Note #{getattr(obj, 'id', len(items) + 1)}"
            )
            title_clean = str(title).strip()
            if not title_clean or title_clean.lower() in seen_titles:
                continue
            seen_titles.add(title_clean.lower())

            excerpt = (
                getattr(obj, "text_content", None)
                or getattr(obj, "content", None)
                or getattr(obj, "notes", None)
                or getattr(obj, "description", None)
                or ""
            )
            obj_id = getattr(obj, "id", 0)
            model_tag = cls.__name__[:8]
            items.append((f"{model_tag}:{obj_id}", title_clean[:36], str(excerpt).strip()[:1800]))

    return items


@ai_router.callback_query(F.data.startswith("ai_ctx_crs:"))
async def cb_pick_ai_resource_context(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    course_id_s = callback.data.split(":", 1)[1]
    course_id = int(course_id_s) if course_id_s.isdigit() else 0

    if course_id == 0:
        data = await state.get_data()
        tool_key = data.get("ai_tool", "ask")
        title, _ = AI_TOOL_PROMPTS.get(tool_key, AI_TOOL_PROMPTS["ask"])
        await state.update_data(
            course_context="General University Curriculum",
            course_label="General University Curriculum",
        )
        await state.set_state(AIStudyFSM.waiting_for_prompt)
        await _safe_edit(
            callback,
            f"<b>{html.escape(title)}</b>\n"
            f"📚 <b>Active Context:</b> <code>General University Curriculum</code>\n\n"
            f"✍️ <b>Send your question or text below:</b>",
            _ai_action_kb(),
        )
        return

    try:
        async with AsyncSessionLocal() as db:
            course = (
                await db.execute(select(Course).where(Course.id == course_id))
            ).scalar_one_or_none()
            if not course:
                await callback.message.answer("⚠️ Course not found.", parse_mode="HTML")
                return

            course_name = (getattr(course, "name", None) or f"Course #{course.id}").strip()
            items = await db.run_sync(lambda sync_db: _find_course_items_sync_helper(sync_db, course.id))

        await state.update_data(ctx_course_id=course.id, ctx_course_name=course_name)
        await state.set_state(AIStudyFSM.choosing_context_resource)

        rows: list[list[InlineKeyboardButton]] = [
            [InlineKeyboardButton(text="📚 Use Entire Course Only", callback_data=f"ai_ctx_res:0:{course.id}")]
        ]
        for idx, (_, item_title, _) in enumerate(items[:18]):
            rows.append([
                InlineKeyboardButton(text=f"📄 {item_title}", callback_data=f"ai_ctx_res:{idx}:{course.id}")
            ])
        rows.append([InlineKeyboardButton(text="⬅️ Back to Courses", callback_data="ai_ctx:pick")])

        await state.update_data(ctx_items_cache=[
            {"title": t, "excerpt": ex} for _, t, ex in items[:18]
        ])

        extra_hint = "" if items else "\n<i>(No study notes/files found linked to this course yet—you can still use the entire course.)</i>"
        await _safe_edit(
            callback,
            f"📄 <b>Step 2 of 2: Choose a Course Resource / Study Note</b>\n"
            f"Course: <b>{html.escape(course_name)}</b>{extra_hint}\n"
            f"Select a resource/note below if you want specified support or use the whole course:",
            InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception as exc:
        await callback.message.answer(
            f"❌ <b>Error loading course notes/resources:</b> <code>{html.escape(str(exc))}</code>",
            parse_mode="HTML",
        )


@ai_router.callback_query(F.data.startswith("ai_ctx_res:"))
async def cb_set_ai_resource_context(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    parts = callback.data.split(":")
    item_idx = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else -1
    course_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    data = await state.get_data()
    tool_key = data.get("ai_tool", "ask")
    title, _ = AI_TOOL_PROMPTS.get(tool_key, AI_TOOL_PROMPTS["ask"])
    course_name = data.get("ctx_course_name") or "Selected Course"

    if course_id > 0 and not data.get("ctx_course_name"):
        async with AsyncSessionLocal() as db:
            course = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none()
            if course:
                course_name = (getattr(course, "name", None) or course_name).strip()

    cached_items = data.get("ctx_items_cache") or []
    if item_idx >= 0 and item_idx < len(cached_items):
        chosen = cached_items[item_idx]
        res_title = chosen.get("title") or "Selected Study Note"
        excerpt = (chosen.get("excerpt") or "").strip()
        label = f"{course_name} · {res_title}"
        excerpt_block = f"\nSelected Study Note / Resource Excerpt:\n{excerpt}" if excerpt else ""
        context_str = (
            f"Ethiopian University Course: {course_name}\n"
            f"Selected Study Note / Resource Name: {res_title}"
            f"{excerpt_block}"
        )
    else:
        label = course_name
        context_str = f"Ethiopian University Course: {course_name}"

    await state.update_data(
        course_context=context_str,
        course_label=label,
    )
    await state.set_state(AIStudyFSM.waiting_for_prompt)

    await _safe_edit(
        callback,
        f"<b>{html.escape(title)}</b>\n"
        f"📚 <b>Active Context:</b> <code>{html.escape(label)}</code>\n\n"
        f"✍️ <b>Now send your question or prompt for {html.escape(label)}:</b>",
        _ai_action_kb(),
    )


# ============================================================
# 6. GEMINI API PROVIDER
# ============================================================

async def call_free_ai_provider(
    system_instruction: str,
    user_prompt: str,
    course_context: str = "",
    tool_key: str = "ask",
    history: list[dict[str, str]] | None = None,
) -> tuple[bool, str]:
    """Calls Gemini using configured and fallback models with conversation history."""
    load_dotenv(override=True)
    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key or api_key == "your_free_gemini_api_key_here":
        return False, (
            f"⚠️ <b>{html.escape(AI_NAME)} Not Configured</b>\n"
            "Add a valid <code>GEMINI_API_KEY</code> to your <code>.env</code> file and restart the bot."
        )

    candidate_models = _resolve_model_candidates(tool_key)

    full_system_instruction = (
        f"Your name is {AI_NAME}.\n\n"
        f"{system_instruction}\n\n"
        "ACADEMIC CONTEXT:\n"
        f"{course_context or 'No specific course selected.'}\n\n"
        "CONVERSATION & ACCURACY RULES:\n"
        "- Use prior messages in this chat session to maintain continuity and answer follow-ups naturally.\n"
        "- Do NOT repeat a greeting on follow-up messages in an ongoing chat.\n"
        "- Maintain academic accuracy and never invent facts, citations, or unverified details.\n"
        "- Explain uncertainty honestly and help students develop independent understanding. be friendly and funny use emojis and the like "
    )

    contents: list[dict[str, object]] = []
    for turn in (history or [])[-(MAX_HISTORY_TURNS * 2):]:
        role = "model" if turn.get("role") == "assistant" else "user"
        txt = str(turn.get("content") or "").strip()
        if txt:
            contents.append({"role": role, "parts": [{"text": txt}]})
    contents.append({"role": "user", "parts": [{"text": user_prompt}]})

    payload = {
        "systemInstruction": {"parts": [{"text": full_system_instruction}]},
        "contents": contents,
        "generationConfig": {
            "temperature": 0.5,
            "maxOutputTokens": 4096,
        },
    }

    last_detail = ""
    last_status = 0
    rate_limited = False

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            for model_name in candidate_models:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
                try:
                    resp = await client.post(url, params={"key": api_key}, json=payload)
                except httpx.TimeoutException:
                    last_detail = f"Request timed out using {model_name}."
                    continue
                except httpx.RequestError as e:
                    last_detail = type(e).__name__
                    continue

                last_status = resp.status_code

                if resp.status_code == 429:
                    rate_limited = True
                    last_detail = "Model rate limit reached."
                    continue

                if resp.status_code == 404:
                    try:
                        last_detail = resp.json().get("error", {}).get("message", "")
                    except Exception:
                        last_detail = resp.text[:300]
                    continue

                if resp.status_code >= 500:
                    last_detail = f"Temporary provider error: HTTP {resp.status_code}"
                    continue

                if resp.status_code != 200:
                    try:
                        detail = resp.json().get("error", {}).get("message", "")
                    except Exception:
                        detail = resp.text[:300]
                    return False, (
                        f"⚠️ <b>{html.escape(AI_NAME)} Provider Error (HTTP {resp.status_code}):</b>\n"
                        f"{html.escape(detail or 'Request failed.')}"
                    )

                try:
                    response_data = resp.json()
                except ValueError:
                    last_detail = "Invalid JSON response from Gemini."
                    continue

                candidates = response_data.get("candidates") or []
                if not candidates:
                    block_reason = (response_data.get("promptFeedback") or {}).get("blockReason", "")
                    return False, (
                        "⚠️ <b>Unable to generate a response.</b>\n"
                        f"{html.escape(str(block_reason or 'No response returned.'))}"
                    )

                candidate = candidates[0]
                parts = (candidate.get("content") or {}).get("parts") or []
                raw_text = "".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()
                if not raw_text:
                    finish_reason = candidate.get("finishReason", "UNKNOWN")
                    return False, (
                        "⚠️ <b>Empty AI Response</b>\n"
                        "No usable text was returned.\n"
                        f"Status: <code>{html.escape(str(finish_reason))}</code>"
                    )

                return True, html.escape(raw_text)

        if rate_limited:
            return False, (
                f"⏳ <b>{html.escape(AI_NAME)} is temporarily busy.</b>\n\n"
                "All available model attempts reached their rate limits or were unavailable. "
                "Please wait before trying again."
            )

        return False, (
            f"⚠️ <b>{html.escape(AI_NAME)} Provider Error (HTTP {last_status or 503}):</b>\n"
            f"Checked models: <code>{html.escape(', '.join(candidate_models))}</code>\n"
            f"{html.escape(last_detail or 'No available model responded.')}"
        )

    except Exception as e:
        return False, (
            f"⚠️ <b>{html.escape(AI_NAME)} Connection Error:</b>\n"
            f"{html.escape(type(e).__name__)}"
        )


# ============================================================
# 7. PROCESS STUDENT AI MESSAGES (GREETS FIRST TURN ONLY + MEMORY)
# ============================================================

@ai_router.message(AIStudyFSM.waiting_for_prompt, F.text & ~F.text.startswith("/"))
async def msg_process_ai_prompt(message: Message, state: FSMContext):
    prompt = (message.text or "").strip()
    if not prompt:
        await message.answer("⚠️ Please send a text prompt or question.")
        return

    async with AsyncSessionLocal() as db:
        student = (
            await db.execute(select(Student).where(Student.telegram_id == message.from_user.id))
        ).scalar_one_or_none()
        if not student:
            await message.answer("⚠️ Student profile not found. Use /start first.")
            return

        allowed, _ = await check_feature_entitlement(db, student, "ai_assistant")
        if not allowed:
            await message.answer(
                "🔒 An active Premium subscription is required to use the AI Study Assistant.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👑 View Membership", callback_data="nav:membership")]
                ]),
            )
            return

    data = await state.get_data()
    tool_key = data.get("ai_tool", "ask")
    course_context = data.get("course_context", "General Freshman Curriculum")
    course_label = data.get("course_label", "General Freshman Curriculum")
    history: list[dict[str, str]] = list(data.get("ai_history") or [])
    is_first_turn = len(history) == 0
    title, sys_prompt = AI_TOOL_PROMPTS.get(tool_key, AI_TOOL_PROMPTS["ask"])

    allowed, used, max_q = await check_and_consume_ai_quota(message.from_user.id)
    if not allowed:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👑 Upgrade Plan for Higher AI Quota", callback_data="nav:membership")],
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="nav:main")],
        ])
        await message.answer(
            f"🛑 <b>Daily AI Quota Reached ({used}/{max_q})</b>\n\n"
            f"You have used all your configured daily <b>{html.escape(AI_NAME)}</b> requests.\n\n"
            "Upgrade to Qine Plus or Qine Pro for a higher daily AI allowance.",
            reply_markup=kb,
            parse_mode="HTML",
        )
        return

    status_prefix = f"🤖 <i>{html.escape(AI_NAME)} ({html.escape(title)}) is analyzing your request...</i>"
    thinking_msg = await message.answer(status_prefix, parse_mode="HTML")

    ok, ai_reply = await _run_with_typing_and_status(
        message=message,
        status_msg=thinking_msg,
        status_prefix=status_prefix,
        coro=call_free_ai_provider(
            system_instruction=sys_prompt,
            user_prompt=prompt,
            course_context=course_context,
            tool_key=tool_key,
            history=history,
        ),
    )

    if not ok:
        await refund_ai_quota(message.from_user.id)
        used = max(0, used - 1)

    quota_label = (
        f"<code>{used}/{max_q}</code> daily quota"
        if ok
        else f"<code>{used}/{max_q}</code> quota (not charged)"
    )
    header = (
        f"🤖 <b>{html.escape(AI_NAME)}</b> · <b>{html.escape(title)}</b> · {quota_label}\n"
        f"📚 <i>{html.escape(course_label)}</i>\n\n"
    )

    if ok:
        raw_reply = html.unescape(ai_reply)
        updated_history = (history + [
            {"role": "user", "content": prompt[:1200]},
            {"role": "assistant", "content": raw_reply[:1800]},
        ])[-(MAX_HISTORY_TURNS * 2):]
        await state.update_data(ai_history=updated_history)

        greeting_prefix = (
            _format_first_turn_greeting(message.from_user.first_name if message.from_user else None)
            if is_first_turn
            else ""
        )
        body_text = f"{greeting_prefix}{html.escape(raw_reply)}"
        max_body = max(500, 3950 - len(header))
        trimmed_reply = body_text[:max_body]
        if len(body_text) > max_body:
            trimmed_reply += "\n\n<i>…(response truncated to fit Telegram message limit)</i>"
    else:
        trimmed_reply = ai_reply

    final_text = header + trimmed_reply

    try:
        await thinking_msg.edit_text(
            final_text,
            reply_markup=_ai_action_kb(),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        await message.answer(
            final_text,
            reply_markup=_ai_action_kb(),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )