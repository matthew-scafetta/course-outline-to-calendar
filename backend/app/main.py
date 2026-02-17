from fastapi import FastAPI, File, UploadFile, HTTPException, Body, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional, Tuple

import openai
import json
import re
import os
from datetime import datetime, timedelta
from dateutil import parser as dt_parser
from icalendar import Calendar, Event, vRecur
from dotenv import load_dotenv
import base64
import hashlib
import fitz

# ============================================================
# CONFIG
# ============================================================
load_dotenv()
TARGET_YEAR = 2026  # default year when missing
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-5-mini")

# OpenAI client
client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ============================================================
# FASTAPI
# ============================================================
app = FastAPI(title="Course Outline Parser", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# MODELS
# ============================================================
class CourseEvent(BaseModel):
    date: Optional[str] = None  # allow null for unresolved Week X
    title: str
    description: Optional[str] = None
    event_type: str = "other"
    time: Optional[str] = None
    recurrence: Optional[str] = None  # "WEEKLY"
    byday: Optional[List[str]] = None  # ["MO","WE"]
    until: Optional[str] = None  # YYYY-MM-DD


class ParseResponse(BaseModel):
    events: List[CourseEvent]
    success: bool
    message: str


# ============================================================
# PROMPT
# ============================================================
def create_ai_prompt() -> str:
    return f"""
You extract academic events from a course syllabus image or PDF page to be converted into a json/ics for google calendar uploading.

WHAT TO EXTRACT:
- Only include items that belong on a calendar:
- Must have a specific date OR a recurrence rule (weekly quizzes/meetings/discussion posts) OR an explicitly dated deadline (e.g., assignment due date, presentation date, exam date, midterm date, test date, quiz date etc).
- Do NOT output general policies or statements, even if important.
- Any event, task, assignment, project, report, demo, presentation, lab, seminar, lecture, quiz, exam, or graded deliverable that has a date (or Week N reference).
- Check paragraphs, bullet lists, and tables. Do NOT skip table entries.
- Include weight/percent in description if present.
- If a weekly schedule row contains a graded deliverable (assignment, quiz, report, exam), create a separate event for the deliverable. Do NOT use the weekly topic as the event title.

DATE RULES (very important):
- If a date includes a year (e.g., "Oct 12 2023" or "2023-10-12"), keep that year.
- If a date does NOT include a year (e.g., "Oct 12"), assume the year is {TARGET_YEAR}.
- Do NOT change the month or day under any circumstances.
- Output dates strictly as "YYYY-MM-DD".
- If you truly cannot determine a date, set "date" to null (do not guess).

TIME RULES:
- Only include a time if the syllabus explicitly shows a time.
- Use 24-hour format "HH:MM".
- If a time range is shown (e.g., "2:30–3:20 PM"), use ONLY the start time ("14:30").
- If no time is explicitly stated, set "time" to null.
- Never invent or guess times.

WEEK-BASED DATES:
- If the syllabus has "Classes start <date>" or "Week 1 begins <date>", treat that as Week 1 start.
- Convert "Week N" to a calendar date:
  - Week N start = Week 1 start + (N-1)*7 days.
  - If it says "Friday Week N", use that weekday within the week.
- If there is no Week 1 anchor, do not guess. Set date to null and mention "Week N" in description.

EVENT CLASSIFICATION:
Use exactly one of:
assignment, project, demo, report, quiz, exam, lab, presentation, other

RECURRENCE RULES:
- Only include recurrence if the syllabus clearly describes a repeating event.
- Use recurrence: "WEEKLY" only.
- byday must be a subset of ["MO","TU","WE","TH","FR","SA","SU"].
- If recurrence is present but days are unclear, omit recurrence entirely.

OUTPUT FORMAT (STRICT):
- Output ONLY valid JSON (no markdown, no backticks, no commentary).
- Always include all keys.
- Use null for fields that do not apply.

JSON SCHEMA:
[
  {{
    "date": "YYYY-MM-DD",
    "title": "Short title",
    "description": "Optional context",
    "event_type": "assignment|project|demo|report|quiz|exam|lab|presentation|other|discussion",
    "time": null,
    "recurrence": null,
    "byday": null,
    "until": null
  }}
]
""".strip()


# ============================================================
# PARSING + NORMALIZATION HELPERS
# ============================================================
ALLOWED_TYPES = {
    "assignment",
    "project",
    "demo",
    "report",
    "quiz",
    "exam",
    "lab",
    "presentation",
    "other",
}
ALLOWED_BYDAY = {"MO", "TU", "WE", "TH", "FR", "SA", "SU"}

WEEKDAY_MAP = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
WEEKDAY_NAME_TO_CODE = {
    "monday": "MO",
    "mon": "MO",
    "tuesday": "TU",
    "tue": "TU",
    "tues": "TU",
    "wednesday": "WE",
    "wed": "WE",
    "thursday": "TH",
    "thu": "TH",
    "thur": "TH",
    "thurs": "TH",
    "friday": "FR",
    "fri": "FR",
    "saturday": "SA",
    "sat": "SA",
    "sunday": "SU",
    "sun": "SU",
}

POLICY_KEYWORDS = [
    "academic integrity",
    "integrity",
    "misconduct",
    "plagiarism",
    "use of generative ai",
    "generative ai",
    "ai policy",
    "llm",
    "policy",
    "policies",
    "guidelines",
    "participation",
    "expectations",
    "firing group",
    "fire group",
    "group members",
    "experiential learning",
    "office hours",
    "contact",
    "email",
    "instructor",
    "learning outcomes",
    "outcomes",
    "resources",
    "textbook",
    "reading list",
]

IMPORTANT_OTHER_KEYWORDS = [
    "drop",
    "withdraw",
    "add",
    "last day",
    "reading week",
    "no class",
    "holiday",
    "course evaluation",
    "course eval",
    "exam period",
]


def filter_events_min(events: List[CourseEvent]) -> List[CourseEvent]:
    kept = []
    for ev in events:
        title = (ev.title or "").strip()
        desc = (ev.description or "").strip()
        blob = f"{title} {desc}".lower()

        has_date = bool(ev.date and str(ev.date).strip())
        has_recur = bool(ev.recurrence and ev.byday)

        # 1) If it's not schedulable, drop it.
        if not has_date and not has_recur:
            continue
        if ev.event_type == "other" and is_week_topic(ev.title):
            continue
        # 2) If it looks like policy/admin text, drop it unless it's an important institutional deadline.
        if any(k in blob for k in POLICY_KEYWORDS):
            if not any(k in blob for k in IMPORTANT_OTHER_KEYWORDS):
                continue

        kept.append(ev)

    return kept


def has_explicit_year(s: str) -> bool:
    return bool(re.search(r"\b(19|20)\d{2}\b", s or ""))


def is_week_topic(title: str) -> bool:
    return bool(re.match(r"week\s*\d+", title.lower()))


def normalize_time(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = re.split(r"\s*[-–—]\s*", s)[0].strip()  # take start of range
    try:
        t = dt_parser.parse(s).time()
        return f"{t.hour:02d}:{t.minute:02d}"
    except Exception:
        return None


def parse_date_keep_year_or_default(
    date_str: str, default_year: int
) -> Optional[datetime]:
    if not date_str:
        return None
    s = str(date_str).strip()
    if not s:
        return None
    try:
        dt = dt_parser.parse(s, default=datetime(default_year, 1, 1))
    except Exception:
        return None
    if not has_explicit_year(s):
        try:
            dt = dt.replace(year=default_year)
        except ValueError:
            dt = dt + timedelta(days=365)
    return dt


def extract_week_ref(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"\bweek\s*(\d{1,2})\b", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def extract_weekday_code(text: str) -> Optional[str]:
    if not text:
        return None
    low = text.lower()
    for name, code in WEEKDAY_NAME_TO_CODE.items():
        if re.search(rf"\b{name}\b", low):
            return code
    return None


def compute_week_date(
    week1_start: datetime, week_num: int, weekday_code: Optional[str]
) -> datetime:
    week_start = week1_start.date() + timedelta(days=(week_num - 1) * 7)
    base = datetime.combine(week_start, datetime.min.time())
    if not weekday_code:
        return base
    target = WEEKDAY_MAP[weekday_code]
    delta = target - base.weekday()
    return base + timedelta(days=delta)


def find_week1_anchor(
    events: List[CourseEvent], default_year: int
) -> Optional[datetime]:
    patterns = [
        r"\bclasses start\b",
        r"\bclass(es)? begin\b",
        r"\bfirst day of classes\b",
        r"\bterm begins\b",
        r"\bweek\s*1\b.*\bbegins\b",
        r"\bweek\s*1\b.*\bstarts\b",
    ]
    best = None
    for ev in events:
        hay = f"{ev.title} {ev.description or ''}".lower()
        if any(re.search(p, hay, re.IGNORECASE) for p in patterns):
            if ev.date:
                dt = parse_date_keep_year_or_default(ev.date, default_year)
                if dt and (best is None or dt < best):
                    best = dt
    return best


def normalize_events(
    events: List[CourseEvent], default_year: int = TARGET_YEAR
) -> List[CourseEvent]:
    week1_anchor = find_week1_anchor(events, default_year)

    out: List[CourseEvent] = []
    for ev in events:
        # normalize event_type
        et = (ev.event_type or "other").strip().lower()
        ev.event_type = et if et in ALLOWED_TYPES else "other"

        # normalize time
        ev.time = normalize_time(ev.time)

        # normalize date if present
        if ev.date:
            dt = parse_date_keep_year_or_default(ev.date, default_year)
            ev.date = dt.date().isoformat() if dt else None

        # resolve Week N if no date
        if not ev.date:
            text = f"{ev.title} {ev.description or ''}"
            week_num = extract_week_ref(text)
            if week_num and week1_anchor:
                weekday_code = extract_weekday_code(text)
                computed = compute_week_date(week1_anchor, week_num, weekday_code)
                ev.date = computed.date().isoformat()

        # normalize recurrence
        if ev.recurrence:
            r = str(ev.recurrence).strip().upper()
            ev.recurrence = "WEEKLY" if r == "WEEKLY" else None

        # normalize byday
        if ev.byday and isinstance(ev.byday, list):
            bd = [
                str(d).strip().upper()
                for d in ev.byday
                if str(d).strip().upper() in ALLOWED_BYDAY
            ]
            ev.byday = bd or None
        else:
            ev.byday = None

        # normalize until
        if ev.until:
            udt = parse_date_keep_year_or_default(ev.until, default_year)
            ev.until = udt.date().isoformat() if udt else None

        # if recurrence without byday, drop recurrence
        if ev.recurrence == "WEEKLY" and not ev.byday:
            ev.recurrence = None
            ev.until = None

        out.append(ev)

    return out


# ============================================================
# PARSE MODEL JSON
# ============================================================
def parse_model_json(response_content: str) -> List[CourseEvent]:
    try:
        text = (response_content or "").strip()

        # strip markdown fences
        if text.startswith("```json"):
            text = text[7:].strip()
        if text.startswith("```"):
            text = text[3:].strip()
        if text.endswith("```"):
            text = text[:-3].strip()

        # extract JSON array
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            raise ValueError("No JSON array found in model output.")
        data = json.loads(m.group(0))
        if not isinstance(data, list):
            raise ValueError("Top-level JSON must be an array.")

        events: List[CourseEvent] = []
        for item in data:
            if not isinstance(item, dict):
                continue

            title = item.get("title", None)
            title = str(title).strip() if title is not None else ""
            if not title:
                continue

            ev = CourseEvent(
                date=item.get("date", None),
                title=title,
                description=item.get("description", None),
                event_type=item.get("event_type", "other"),
                time=item.get("time", None),
                recurrence=item.get("recurrence", None),
                byday=item.get("byday", None),
                until=item.get("until", None),
            )
            events.append(ev)

        return events

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error parsing model JSON: {str(e)}"
        )


# ============================================================
# UID + DEDUPE (merge-based, midterm-safe)
# ============================================================

TYPE_PRIORITY = {
    "exam": 100,
    "quiz": 90,
    "test": 85,
    "report": 80,
    "project": 70,
    "assignment": 60,
    "lab": 50,
    "presentation": 40,
    "demo": 30,
    "other": 0,
}


def _clean_text(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)  # drop parentheticals
    s = re.sub(r"no\.?\s*", "", s)  # "No. 1" -> "1"
    s = re.sub(r"#\s*(\d+)", r" \1", s)  # "#1" -> " 1"
    s = re.sub(r"\b1st\b", " 1", s)
    s = re.sub(r"\b2nd\b", " 2", s)
    s = re.sub(r"\b3rd\b", " 3", s)
    s = re.sub(r"\b(\d+)th\b", r" \1", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def canonical_assessment_key(ev: CourseEvent) -> str:
    """
    Canonical key used to stabilize UIDs for exams/quizzes/tests so that:
      - 'Midterm #1' == 'Midterm 1'
      - variants like '(in-person)' don't matter
    Keeps different numbered assessments distinct (midterm-1 vs midterm-2).
    """
    blob = _clean_text(f"{ev.title or ''} {ev.description or ''}")

    # quick family detection
    is_midterm = "midterm" in blob
    is_final_exam = (
        ("final" in blob)
        and ("final report" not in blob)
        and (ev.event_type == "exam" or "exam" in blob)
    )
    is_quiz = "quiz" in blob
    is_test = ("test" in blob) and not is_midterm and not is_quiz
    is_exam = (ev.event_type == "exam") or ("exam" in blob)

    # number extraction
    num = None
    m = re.search(r"\b(midterm|quiz|test|exam)\s*(\d{1,2})\b", blob)
    if m:
        num = m.group(2)

    # word numbers (first/second) for midterms often appear without a digit
    if is_midterm and not num:
        if re.search(r"\b(first|one)\b", blob):
            num = "1"
        elif re.search(r"\b(second|two)\b", blob):
            num = "2"
        elif re.search(r"\b(third|three)\b", blob):
            num = "3"

    if is_midterm:
        return f"midterm-{num or 'x'}"
    if is_final_exam:
        return "final-x"
    if is_quiz:
        return f"quiz-{num or 'x'}"
    if is_test:
        return f"test-{num or 'x'}"
    if is_exam:
        return f"exam-{num or 'x'}"
    return ""


def normalize_midterm_numbers(events: List[CourseEvent]) -> List[CourseEvent]:
    """
    If a date has a numbered midterm (e.g., Midterm #1), force any other unnumbered
    midterm on that same date to become Midterm 1 as well. This eliminates the
    'MIDTERM #1' + 'Midterm (in class)' duplicates.
    """
    date_to_midterm_num: dict[str, str] = {}

    for ev in events:
        if ev.event_type != "exam" or not ev.date:
            continue
        blob = _clean_text(f"{ev.title or ''} {ev.description or ''}")
        if "midterm" not in blob:
            continue
        m = re.search(r"\bmidterm\s*(\d{1,2})\b", blob)
        if m:
            date_to_midterm_num[ev.date] = m.group(1)

    for ev in events:
        if ev.event_type != "exam" or not ev.date:
            continue
        blob = _clean_text(f"{ev.title or ''} {ev.description or ''}")
        if "midterm" not in blob:
            continue
        has_num = re.search(r"\bmidterm\s*(\d{1,2})\b", blob) is not None
        if not has_num and ev.date in date_to_midterm_num:
            n = date_to_midterm_num[ev.date]
            # Preserve original phrasing in description, but stabilize title
            old_title = (ev.title or "").strip()
            if old_title and old_title.lower() != f"midterm {n}":
                ev.description = (f"{old_title}. " + (ev.description or "")).strip()
            ev.title = f"Midterm {n}"
    return events


def generate_event_uid(ev: CourseEvent) -> str:
    # Exams/quizzes/tests get a canonical title-part so variants hash the same
    canon = (
        canonical_assessment_key(ev)
        if (ev.event_type in {"exam", "quiz", "test"})
        else ""
    )
    title_part = canon if canon else (ev.title or "").lower().strip()

    base = "|".join(
        [
            title_part,
            (ev.date or "").strip(),
            (ev.time or "").strip(),
            (ev.event_type or "other").strip().lower(),
            (ev.recurrence or "").strip().upper(),
            ",".join(ev.byday or []),
            (ev.until or "").strip(),
        ]
    )
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()
    return f"{digest}@course-outline-parser"


def _normalize_title_for_merge(title: str) -> str:
    t = _clean_text(title or "")
    # remove "due" to merge "Group Contract due" vs "Group Contract"
    t = re.sub(r"\bdue\b", " ", t)

    # normalize common equivalences so table-row vs paragraph titles merge
    # e.g., "Final Group Project" vs "Group Final Report"
    t = t.replace("final group project", "group final report")
    t = t.replace("final project", "final report")
    t = t.replace("group project", "project")

    t = re.sub(r"\s+", " ", t).strip()
    return t


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return [p.strip() for p in parts if p and p.strip()]


def _merge_descriptions(a_desc: str, b_desc: str) -> str:
    a_sents = _split_sentences(a_desc)
    b_sents = _split_sentences(b_desc)

    seen: set[str] = set()
    out: List[str] = []

    def norm(s: str) -> str:
        s = (s or "").lower().strip()
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r"[^a-z0-9%: ]", "", s)
        return s

    for s in a_sents + b_sents:
        key = norm(s)
        if key and key not in seen:
            seen.add(key)
            out.append(s)

    return " ".join(out).strip()


def _merge_two(a: CourseEvent, b: CourseEvent) -> CourseEvent:
    # time: prefer non-null
    if (not a.time) and b.time:
        a.time = b.time

    # event_type: prefer higher priority
    if TYPE_PRIORITY.get(b.event_type, 0) > TYPE_PRIORITY.get(a.event_type, 0):
        a.event_type = b.event_type

    # title: prefer the more specific/longer one (usually includes more context)
    if (b.title or "") and len(b.title or "") > len(a.title or ""):
        a.title = b.title

    # description: sentence-level unique merge (prevents repeated 'Group Contract due...' lines)
    a.description = _merge_descriptions(a.description or "", b.description or "")

    return a


def deduplicate_events(events: List[CourseEvent]) -> List[CourseEvent]:
    """
    Merge duplicates instead of dropping them.
    - For non-recurring dated items: merge by (date + normalized title)
      (this fixes 'Group Contract due' vs 'Group Contract' + 23:59)
    - For recurring items (lectures, etc): keep strict UID semantics.
    """
    merged: dict[str, CourseEvent] = {}

    for ev in events:
        is_recurring = bool(ev.recurrence)
        if ev.date and (not is_recurring):
            key = f"{ev.date}|{_normalize_title_for_merge(ev.title)}"
        else:
            key = generate_event_uid(ev)

        if key in merged:
            merged[key] = _merge_two(merged[key], ev)
        else:
            merged[key] = ev

    return list(merged.values())


# ============================================================
# ICS GENERATION
# ============================================================
def events_to_ics(events: List[CourseEvent]) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Course Outline Parser//example.com//")
    cal.add("version", "2.0")

    for ev in events:
        if not ev.date:
            continue

        e = Event()
        e.add("uid", generate_event_uid(ev))
        e.add("summary", ev.title)
        e.add("description", ev.description or "")

        dt = parse_date_keep_year_or_default(ev.date, TARGET_YEAR)
        if not dt:
            continue

        if ev.time:
            try:
                t = dt_parser.parse(ev.time).time()
                start_dt = datetime.combine(dt.date(), t)
                end_dt = start_dt + timedelta(hours=1)
                e.add("dtstart", start_dt)
                e.add("dtend", end_dt)
            except Exception:
                e.add("dtstart", dt.date())
                e.add("dtend", dt.date() + timedelta(days=1))
        else:
            e.add("dtstart", dt.date())
            e.add("dtend", dt.date() + timedelta(days=1))

        if ev.recurrence == "WEEKLY" and ev.byday:
            rule = vRecur({"FREQ": "WEEKLY", "BYDAY": ev.byday})

            if ev.until:
                udt = parse_date_keep_year_or_default(ev.until, TARGET_YEAR)
                if udt:
                    until_dt = datetime.combine(
                        udt.date(), datetime.max.time().replace(microsecond=0)
                    )
                    rule["UNTIL"] = until_dt

            e.add("rrule", rule)

        cal.add_component(e)

    return cal.to_ical()


# ============================================================
# AI PROCESSING
# ============================================================
async def process_file_with_ai(uploaded_file: UploadFile) -> List[CourseEvent]:
    try:
        file_bytes = await uploaded_file.read()
        mime_type = uploaded_file.content_type or ""

        all_events: List[CourseEvent] = []

        def call_model_on_image_bytes(image_bytes: bytes, mime: str) -> str:
            b64 = base64.b64encode(image_bytes).decode("utf-8")
            data_url = f"data:{mime};base64,{b64}"

            resp = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {
                        "role": "system",
                        "content": "You extract course events from syllabi.",
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": create_ai_prompt()},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    },
                ],
            )
            return (resp.choices[0].message.content or "").strip()

        if mime_type == "application/pdf":
            doc = fitz.open(stream=file_bytes, filetype="pdf")

            for page in doc:
                pix = page.get_pixmap(dpi=200)  # 200 DPI is good balance
                img_bytes = pix.tobytes("png")

                model_text = call_model_on_image_bytes(img_bytes, "image/png")
                all_events.extend(parse_model_json(model_text))

            doc.close()

        elif mime_type in ("image/png", "image/jpeg", "image/jpg"):
            img_mime = "image/jpeg" if "jp" in mime_type else "image/png"
            model_text = call_model_on_image_bytes(file_bytes, img_mime)
            all_events.extend(parse_model_json(model_text))

        else:
            raise HTTPException(
                status_code=400, detail=f"Unsupported file type: {mime_type}"
            )

        # normalize -> dedupe
        all_events = normalize_events(all_events, default_year=TARGET_YEAR)
        all_events = filter_events_min(all_events)
        all_events = normalize_midterm_numbers(all_events)
        all_events = deduplicate_events(all_events)

        return all_events

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error processing file with AI: {str(e)}"
        )


# ============================================================
# ENDPOINTS
# ============================================================
@app.post("/upload-json", response_model=ParseResponse)
async def upload_file_json(file: UploadFile = File(...)):
    events = await process_file_with_ai(file)
    print("OPENAI KEY LOADED:", bool(os.getenv("OPENAI_API_KEY")))
    return ParseResponse(
        events=events, success=True, message=f"Extracted {len(events)} events"
    )


@app.post("/calendar-from-events")
async def calendar_from_events(events: List[CourseEvent] = Body(...)):
    # Optional: re-apply cleanup for safety/consistency
    events = normalize_events(events, default_year=TARGET_YEAR)
    events = filter_events_min(events)
    events = normalize_midterm_numbers(events)
    events = deduplicate_events(events)

    ics_bytes = events_to_ics(events)

    return Response(
        content=ics_bytes,
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="course_schedule.ics"'},
    )


@app.get("/")
async def root():
    return {
        "message": "Course Outline Parser API",
        "version": "2.0.0",
        "endpoints": {
            "upload_json": "/upload-json (POST)",
            "upload_calendar": "/upload-calendar (POST)",
            "health": "/health (GET)",
            "docs": "/docs (GET)",
        },
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "message": "Course Parser API is running",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001, reload=False)
