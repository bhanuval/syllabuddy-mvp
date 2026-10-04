import base64
import json
import os
import re
import time
from datetime import datetime
from io import BytesIO

from dotenv import load_dotenv
from flask import Flask, g, jsonify, request, send_from_directory
from openai import OpenAI

# Load secrets from .env in this folder. The key never goes to the browser.
load_dotenv()

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_FILES = 12
MAX_TEXT_CHARS = 120000
MAX_REQUEST_BYTES = 25 * 1024 * 1024   # whole upload, so a huge request cannot fill memory
RATE_MAX = int(os.getenv("RATE_MAX", "20"))   # requests per IP per 10 minutes
RATE_WINDOW_SECONDS = 600
PLACEHOLDER_KEYS = {
    "",
    "paste_your_openai_api_key_here",
    "syllabuddy",
    "your_api_key_here",
}

ALLOWED_TYPES = {
    "Class",
    "Assignment",
    "Reading",
    "Quiz",
    "Exam",
    "Project",
}

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_24 = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
TIME_AMPM = re.compile(
    r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*$",
    re.I,
)

SYSTEM_INSTRUCTIONS = """
You build one student schedule from whatever they pasted or uploaded, including messy notes like
"algebra 100 at 8 am to 5 pm".

Include:
- class meetings (type Class), such as "Algebra 100 at 8 am to 5 pm"
- assignments, homework, labs, papers, projects
- quizzes, tests, midterms, finals
- readings with dates

Rules:
- type must be one of: Class, Assignment, Reading, Quiz, Exam, Project.
- A line that is only a course name plus a time range (8am to 5pm, 8:00-17:00) is a Class meeting, not an assignment.
- For Class rows, time is the start time as HH:MM. Put the end time in details (example: "Until 17:00").
- Midterm, final, and "test" are Exam. "Quiz" is Quiz. Do not label an exam or quiz as Assignment.
- item is a short title. Do not invent titles.
- due must be YYYY-MM-DD when the text gives a real calendar date.
- A class meeting takes the date shown with its session (for example "Saturday, 12-Sep." becomes that date). Leave a class meeting's due empty only if the text gives no date.
- A reading listed under a class session takes that session's class date as due. A reading never takes a date from its citation.
- An assignment listed under a class session does NOT take the session's class date. Give an assignment a due date only when the text states one for that assignment, for example in a table of deliverables. Otherwise leave its due empty.
- If month and day are given but year is not, use the year from Today's date. If that date is invalid (for example February 30), set due to "".
- For assignments, quizzes, exams, and projects, time must be a due time, not a lecture meeting time.
- If the student provided a default due time, you may copy it onto dated Assignment/Quiz/Exam/Project rows with no time. Do not apply it to Class or Reading.
- If date or time cannot be known, leave that field "".
- Dates that appear inside a reading's citation are publication dates, not due dates. Examples: "(2023, May-June)", "5 Apr. 2024", "Harvard Business Review, 16 March", "(2025, Sept. 26)". Never use a publication date as any item's date.
- A reading listed under a class session takes that session's class date, but only when the text gives that session's date. If the session has no date, leave the reading's due empty.
- source must be a short quote copied from the pasted text. It must include the item's name or its date. Never use a row number alone as the source.
- If the same item is listed more than once (for example once in a table of deliverables with a due date, and again under a class session with no date), return it once. Use the date and time from the entry that has them.
- details is extra scope from the text only (end time, word limit, pages). Otherwise "".
- link is a URL copied from the text only. Otherwise "".
- flag is a short warning if something is unclear. Otherwise "".
- Do not invent items, dates, times, links, or quotes.
- If nothing can be extracted, return {"items": []}.
""".strip()

ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "item", "due", "time", "source", "details", "link", "flag"],
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "Class",
                            "Assignment",
                            "Reading",
                            "Quiz",
                            "Exam",
                            "Project",
                        ],
                    },
                    "item": {"type": "string"},
                    "due": {"type": "string"},
                    "time": {"type": "string"},
                    "source": {"type": "string"},
                    "details": {"type": "string"},
                    "link": {"type": "string"},
                    "flag": {"type": "string"},
                },
            },
        }
    },
}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
_hits = {}
ROOT = os.path.dirname(os.path.abspath(__file__))
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
OLD_OFFICE_EXTS = {".doc", ".xls", ".ppt"}
TEXT_EXTS = {
    ".txt",
    ".md",
    ".csv",
    ".tsv",
    ".json",
    ".html",
    ".htm",
    ".rtf",
    ".log",
}


def json_error(message, status_code):
    return jsonify({"error": message}), status_code


def rate_limited():
    """Approximate per-IP limit. It resets when the server restarts; the provider spending cap is the real safeguard."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = (forwarded.split(",")[0].strip() if forwarded else request.remote_addr) or "unknown"
    now = time.time()
    recent = [t for t in _hits.get(ip, []) if now - t < RATE_WINDOW_SECONDS]
    recent.append(now)
    _hits[ip] = recent
    return len(recent) > RATE_MAX


def valid_due_date(value):
    if not value:
        return ""
    text = str(value).strip()
    if not DATE_PATTERN.fullmatch(text):
        return ""
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return ""
    return text


def valid_time(value):
    if not value:
        return ""
    text = str(value).strip()
    match = TIME_24.fullmatch(text)
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}"
    match = TIME_AMPM.fullmatch(text)
    if not match:
        return ""
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    period = match.group(3).lower()
    if hour < 1 or hour > 12 or minute > 59:
        return ""
    if period == "p" and hour != 12:
        hour += 12
    if period == "a" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def weekday_mismatch(due, text):
    if not due:
        return ""
    match = re.search(
        r"\b(monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat|sunday|sun)\b",
        text,
        re.I,
    )
    if not match:
        return ""
    names = {
        "mon": 0,
        "tue": 1,
        "wed": 2,
        "thu": 3,
        "fri": 4,
        "sat": 5,
        "sun": 6,
    }
    stated = names[match.group(1)[:3].lower()]
    actual = datetime.strptime(due, "%Y-%m-%d").weekday()
    if stated != actual:
        return "weekday does not match date"
    return ""


STALE_READING_DAYS = 180


def stale_reading(item_type, due):
    """A reading dated long before today is almost always a citation (publication) date, not a due date."""
    if item_type != "Reading" or not due:
        return False
    try:
        age = (datetime.now() - datetime.strptime(due, "%Y-%m-%d")).days
    except ValueError:
        return False
    return age > STALE_READING_DAYS


TITLE_STOPWORDS = {"a", "an", "the", "of", "and", "with", "for", "to", "in", "on", "by"}


def title_tokens(text):
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [w for w in words if not w.isdigit() and w not in TITLE_STOPWORDS]


def same_token(a, b):
    """Equal, or one is an abbreviation (prefix) of the other, such as bus and business."""
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 3 and long.startswith(short)


def token_overlap(ta, tb):
    used = set()
    common = 0
    for x in ta:
        for j, y in enumerate(tb):
            if j not in used and same_token(x, y):
                used.add(j)
                common += 1
                break
    return common


DATE_HINT = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\d{1,2}/\d{1,2}", re.I
)


def find_source_line(name, syllabus):
    """Used when the AI quotes only a row number: find the pasted line that best matches the item's name."""
    tokens = title_tokens(name)
    lines = (syllabus or "").splitlines()
    best, best_common = None, 1
    for idx, line in enumerate(lines):
        common = token_overlap(tokens, title_tokens(line))
        if common > best_common:
            best, best_common = idx, common
    if best is None:
        return ""
    text = lines[best].strip()
    if best + 1 < len(lines) and DATE_HINT.search(lines[best + 1]):
        text += " | " + lines[best + 1].strip()
    return text[:180]


def drop_session_dates(items):
    """Readings only keep a date that matches a class meeting date. An assignment dated with a class date is dropped
    when a similar assignment has a different date, because the outline's session date is not a due date."""
    class_dates = {i["due"] for i in items if i["type"] == "Class" and i["due"]}
    out = [dict(i) for i in items]
    for i in out:
        if i["type"] == "Reading" and i["due"] and class_dates and i["due"] not in class_dates:
            i["due"] = ""
            i["flag"] = "; ".join(x for x in (i["flag"], "date removed: not a class date") if x)
    plain = [k for k, i in enumerate(out) if i["type"] not in ("Reading", "Class") and i["due"]]
    drop = set()
    for k in plain:
        if out[k]["due"] not in class_dates:
            continue
        tk = title_tokens(out[k]["item"])
        for j in plain:
            if j == k or out[j]["due"] in class_dates or numbers_conflict(out[k]["item"], out[j]["item"]):
                continue
            tj = title_tokens(out[j]["item"])
            common = token_overlap(tk, tj)
            union = len(tk) + len(tj) - common
            if common >= 2 and union and common / union >= 0.5:
                drop.add(k)
                break
    return [i for k, i in enumerate(out) if k not in drop]


def merge_duplicates(items):
    """The same assignment often appears twice: in a deliverables table (with a date) and in a session outline (without).
    Near-identical titles are merged into the dated row. Looser matches are flagged, not deleted, so the student decides."""
    keep = [dict(i) for i in items]
    removed = set()
    used_dated = set()
    plain = lambda k: keep[k]["type"] not in ("Reading", "Class")
    dated = [k for k in range(len(keep)) if plain(k) and keep[k]["due"]]
    undated = [k for k in range(len(keep)) if plain(k) and not keep[k]["due"]]
    for u in undated:
        tu = title_tokens(keep[u]["item"])
        best = None
        for d in dated:
            if d in used_dated:
                continue
            td = title_tokens(keep[d]["item"])
            if numbers_conflict(keep[u]["item"], keep[d]["item"]):
                continue
            common = token_overlap(tu, td)
            diff = (len(tu) - common) + (len(td) - common)
            if common >= 2 and diff <= 1 and (best is None or (diff, -common) < best[0]):
                best = ((diff, -common), d)
        if best:
            d = best[1]
            used_dated.add(d)
            removed.add(u)
            if not keep[d]["details"] and keep[u]["details"]:
                keep[d]["details"] = keep[u]["details"]
    for u in undated:
        if u in removed:
            continue
        tu = title_tokens(keep[u]["item"])
        best = None
        for d in dated:
            if d in used_dated:
                continue
            td = title_tokens(keep[d]["item"])
            if numbers_conflict(keep[u]["item"], keep[d]["item"]):
                continue
            common = token_overlap(tu, td)
            union = len(tu) + len(td) - common
            score = common / union if union else 0
            if common >= 2 and score >= 0.5 and (best is None or score > best[0]):
                best = (score, d)
        if best:
            note = "possible duplicate of: " + keep[best[1]]["item"][:60]
            keep[u]["flag"] = "; ".join(x for x in (keep[u]["flag"], note) if x)
    return [i for k, i in enumerate(keep) if k not in removed]


MONTH_NUMBER = {m: n for n, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
MONTH_NAMES = "(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"


def date_from_text(text, year):
    """Finds a day and month such as '12-Sep.' or 'October 9' in text. Returns YYYY-MM-DD, or '' if absent or impossible."""
    t = text or ""
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?[-\s]+" + MONTH_NAMES + r"\b", t, re.I)
    if m:
        day, mon = int(m.group(1)), MONTH_NUMBER[m.group(2).lower()]
    else:
        m = re.search(r"\b" + MONTH_NAMES + r"\.?\s+(\d{1,2})\b(?!:)", t, re.I)
        if not m:
            return ""
        mon, day = MONTH_NUMBER[m.group(1).lower()], int(m.group(2))
    try:
        return datetime(year, mon, day).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def numbers_conflict(a, b):
    """'Case Study 1' and 'Case Study 2' are different items. A missing number is not a conflict."""
    na, nb = set(re.findall(r"\d+", a or "")), set(re.findall(r"\d+", b or ""))
    return bool(na) and bool(nb) and na != nb


def appears_in(snippet, syllabus):
    hay = re.sub(r"\s+", " ", (syllabus or "").lower())
    needle = re.sub(r"\s+", " ", (snippet or "").strip().lower())
    return bool(needle) and needle in hay


def clean_items(raw_items, syllabus=""):
    cleaned = []
    for raw in raw_items or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("item") or "").strip()
        if not name:
            continue
        item_type = str(raw.get("type") or "Assignment").strip()
        if item_type not in ALLOWED_TYPES:
            item_type = "Assignment"
        due = valid_due_date(raw.get("due"))
        source = str(raw.get("source") or "").strip()
        if not due and item_type == "Class":
            due = date_from_text(source, datetime.now().year)
        details = str(raw.get("details") or "").strip()
        link = str(raw.get("link") or "").strip()
        flags = []
        extra = str(raw.get("flag") or "").strip()
        if extra:
            flags.append(extra)
        if syllabus and (len(source) < 4 or source.isdigit()):
            repaired = find_source_line(name, syllabus)
            if repaired:
                source = repaired
            else:
                source = ""
                flags.append("no source line found")
        if source and syllabus and not appears_in(source, syllabus):
            source = source[:180]
            flags.append("source not copied from paste")
        if details and syllabus and item_type != "Class" and not appears_in(details, syllabus):
            details = ""
        if link:
            if not re.match(r"^https?://", link, re.I) or (
                syllabus and link not in syllabus
            ):
                link = ""
        if stale_reading(item_type, due):
            due = ""
            flags.append("date removed: it looks like a publication date")
        mismatch = weekday_mismatch(due, name + " " + source)
        if mismatch:
            flags.append(mismatch)
        if not due and item_type != "Class":
            flags.append("no date")
        cleaned.append(
            {
                "type": item_type,
                "item": name,
                "due": due,
                "time": valid_time(raw.get("time")),
                "source": source,
                "details": details,
                "link": link,
                "flag": "; ".join(dict.fromkeys(flags)),
            }
        )
    return cleaned


def decode_bytes(data):
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_pdf(data):
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages).strip()


def extract_docx(data):
    from docx import Document

    document = Document(BytesIO(data))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(part for part in parts if part).strip()


def extract_xlsx(data):
    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(data), data_only=True, read_only=True)
    lines = []
    for sheet in workbook.worksheets:
        lines.append("Sheet: " + sheet.title)
        for row in sheet.iter_rows(values_only=True):
            values = ["" if cell is None else str(cell) for cell in row]
            if any(value.strip() for value in values):
                lines.append("\t".join(values))
    return "\n".join(lines).strip()


def extract_pptx(data):
    from pptx import Presentation

    presentation = Presentation(BytesIO(data))
    parts = []
    for slide in presentation.slides:
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                parts.append(shape.text)
    return "\n".join(part for part in parts if part).strip()


def mime_for_image(ext):
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(ext, "image/png")


def read_uploaded_files(files):
    texts = []
    images = []
    names = []
    if len(files) > MAX_FILES:
        raise RuntimeError("Upload at most " + str(MAX_FILES) + " files at a time.")
    for stored in files:
        filename = (stored.filename or "").strip()
        if not filename:
            continue
        data = stored.read()
        if not data:
            continue
        if len(data) > MAX_FILE_BYTES:
            raise RuntimeError(filename + " is larger than 12 MB.")
        ext = os.path.splitext(filename)[1].lower()
        if ext in OLD_OFFICE_EXTS:
            raise RuntimeError(
                os.path.basename(filename)
                + " is an older Office format. Save it as .docx, .xlsx, .pptx, or PDF and upload it again."
            )
        supported = IMAGE_EXTS | TEXT_EXTS | {".pdf", ".docx", ".xlsx", ".xlsm", ".pptx", ""}
        if ext not in supported or os.path.basename(filename).startswith("."):
            g.skipped = getattr(g, "skipped", []) + [os.path.basename(filename)]   # for example .DS_Store or .zip inside a folder
            continue
        names.append(os.path.basename(filename))
        label = "File: " + os.path.basename(filename)
        try:
            if ext in IMAGE_EXTS:
                images.append(
                    {
                        "name": filename,
                        "mime": mime_for_image(ext),
                        "data": data,
                    }
                )
                texts.append(label + "\n[image uploaded]")
            elif ext == ".pdf":
                text = extract_pdf(data)
                texts.append(label + "\n" + (text or "[PDF had no readable text]"))
            elif ext == ".docx":
                texts.append(label + "\n" + extract_docx(data))
            elif ext in {".xlsx", ".xlsm"}:
                texts.append(label + "\n" + extract_xlsx(data))
            elif ext == ".pptx":
                texts.append(label + "\n" + extract_pptx(data))
            else:
                texts.append(label + "\n" + decode_bytes(data))
        except Exception as error:
            raise RuntimeError("Could not read " + filename + ": " + str(error)) from error
    return texts, images, names


def openai_client():
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key.lower() in PLACEHOLDER_KEYS:
        raise RuntimeError(
            "OPENAI_API_KEY is missing. Put a real OpenAI key in the .env file."
        )
    return OpenAI(api_key=api_key)


def extract_items(syllabus, course, notes, images, default_time=""):
    client = openai_client()
    user_text = (
        "Today's date: "
        + datetime.now().strftime("%Y-%m-%d")
        + "\nCourse name: "
        + (course or "(not provided)")
        + "\nDefault due time for this course: "
        + (default_time or "(none)")
        + "\n\nStudent request: "
        + (notes or "(none — build one schedule from this input with type, date, time, and source for each item)")
        + "\n\nPasted text:\n"
        + syllabus
    )
    content = [{"type": "input_text", "text": user_text}]
    for image in images:
        encoded = base64.b64encode(image["data"]).decode("ascii")
        content.append(
            {
                "type": "input_image",
                "image_url": "data:" + image["mime"] + ";base64," + encoded,
            }
        )
    response = client.responses.create(
        model=OPENAI_MODEL,
        instructions=SYSTEM_INSTRUCTIONS,
        input=[{"role": "user", "content": content}],
        text={
            "format": {
                "type": "json_schema",
                "name": "syllabus_items",
                "schema": ITEM_SCHEMA,
                "strict": True,
            }
        },
    )
    raw_text = (response.output_text or "").strip()
    if not raw_text:
        raise RuntimeError("The AI returned an empty response.")
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as error:
        raise RuntimeError("The AI did not return valid JSON.") from error
    return merge_duplicates(drop_session_dates(clean_items(parsed.get("items"), syllabus)))


def request_payload():
    if request.files or request.form:
        course = str(request.form.get("course") or "").strip()
        notes = str(request.form.get("notes") or "").strip()
        origin = str(request.form.get("origin") or "Syllabus").strip() or "Syllabus"
        pasted = str(request.form.get("syllabus") or "").strip()
        default_time = str(request.form.get("defaultTime") or "").strip()
        uploaded = request.files.getlist("files") if request.files else []
        texts, images, names = read_uploaded_files(uploaded)
        chunks = []
        if pasted:
            chunks.append("Pasted text:\n" + pasted)
        chunks.extend(texts)
        syllabus = "\n\n".join(chunk for chunk in chunks if chunk).strip()
        return syllabus, course, notes, origin, images, names, default_time
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("Send JSON or upload files.")
    return (
        str(data.get("syllabus") or "").strip(),
        str(data.get("course") or "").strip(),
        str(data.get("notes") or "").strip(),
        str(data.get("origin") or "Syllabus").strip() or "Syllabus",
        [],
        [],
        str(data.get("defaultTime") or "").strip(),
    )


@app.route("/")
def home():
    return send_from_directory(ROOT, "index.html")


@app.route("/baseline")
def baseline():
    return send_from_directory(ROOT, "baseline.html")


@app.route("/rules-extractor.js")
def rules_extractor():
    return send_from_directory(ROOT, "rules-extractor.js")


@app.route("/extract", methods=["POST"])
def extract():
    if rate_limited():
        return json_error("Too many requests. Please try again in a few minutes.", 429)
    try:
        syllabus, course, notes, origin, images, names, default_time = request_payload()
    except ValueError as error:
        return json_error(str(error), 400)
    except RuntimeError as error:
        return json_error(str(error), 400)
    if not syllabus and not images:
        return json_error("Paste a syllabus first.", 400)
    if len(syllabus) > MAX_TEXT_CHARS:
        syllabus = syllabus[:MAX_TEXT_CHARS]
    try:
        items = extract_items(syllabus, course, notes, images, default_time)
    except RuntimeError as error:
        return json_error(str(error), 500)          # messages written in this file, safe to show
    except Exception as error:
        app.logger.error("AI call failed: %s", type(error).__name__)   # never log pasted text or keys
        return json_error("The AI service could not finish the request. Please try again, or use the rules-only extractor.", 502)
    return jsonify(
        {
            "items": items,
            "course": course,
            "origin": origin,
            "files": names,
            "skipped": getattr(g, "skipped", []),
        }
    )


@app.errorhandler(404)
def not_found(_error):
    return json_error("That page or API route was not found.", 404)


@app.errorhandler(413)
def too_large(_error):
    return json_error("That upload is too large. Keep the total under 25 MB.", 413)


@app.errorhandler(500)
def server_error(_error):
    return json_error("Something went wrong on the server. Please try again.", 500)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)


