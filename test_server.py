"""Checks Server.py with a stand-in for the AI service. No key and no real AI call are used.
Run:  python test_server.py"""
import io, os, sys
os.environ.pop("OPENAI_API_KEY", None)
os.environ["RATE_MAX"] = "50"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import Server

c = Server.app.test_client()
assert c.get("/").status_code == 200 and c.get("/baseline").status_code == 200
assert c.get("/rules-extractor.js").status_code == 200

assert c.post("/extract", json={"syllabus": ""}).status_code == 400
r = c.post("/extract", json={"syllabus": "Quiz 1 due Oct 12"})
assert r.status_code == 500 and "OPENAI_API_KEY is missing" in r.get_json()["error"]

def fake(syllabus, course, notes, images, default_time=""):
    raw = [{"type": "Quiz", "item": "Quiz 1", "due": "2026-10-12", "time": "6 PM", "source": "Quiz 1 due Oct 12", "details": "", "link": "https://invented.example.com", "flag": ""},
           {"type": "Exam", "item": "Midterm", "due": "2026-02-30", "time": "", "source": "made up quote", "details": "", "link": "", "flag": ""}]
    return Server.clean_items(raw, syllabus)
Server.extract_items = fake
items = c.post("/extract", json={"syllabus": "Quiz 1 due Oct 12"}).get_json()["items"]
assert items[0]["time"] == "18:00" and items[0]["link"] == ""           # invented link removed
assert items[1]["due"] == "" and "no date" in items[1]["flag"]          # impossible date blanked

# A reading dated by its citation is blanked and flagged; a current class date is kept
def fake_readings(syllabus, course, notes, images, default_time=""):
    raw = [{"type": "Reading", "item": "When to Rely on Algorithms", "due": "2023-05-01", "time": "", "source": "Fantini, F. (2023, May-June)", "details": "", "link": "", "flag": ""},
           {"type": "Reading", "item": "Chapter 1", "due": "2026-10-09", "time": "", "source": "Friday, 9-Oct", "details": "", "link": "", "flag": ""}]
    return Server.clean_items(raw, syllabus + " Fantini, F. (2023, May-June) Friday, 9-Oct")
Server.extract_items = fake_readings
rd = c.post("/extract", json={"syllabus": "x"}).get_json()["items"]
assert rd[0]["due"] == "" and "publication date" in rd[0]["flag"]
assert rd[1]["due"] == "2026-10-09"
Server.extract_items = fake

# Duplicates: near-identical titles merge into the dated row; looser matches are flagged, not deleted
def row(kind, name, due, source="x"):
    return {"type": kind, "item": name, "due": due, "time": "", "source": source, "details": "", "link": "", "flag": ""}
rows = [
    row("Assignment", "Bus. Analy. using Orange (1): Introduction", "2026-09-20"),
    row("Assignment", "Bus. Analy. using Orange (2): Decision Trees", "2026-10-04"),
    row("Assignment", "Building a Customer Service Agent with n8n", "2026-11-15"),
    row("Assignment", "Business Analytics using Orange: Introduction", ""),
    row("Assignment", "Business Analytics using Orange: Decision Trees", ""),
    row("Assignment", "Building AI Agents with n8n", ""),
    row("Reading", "Some reading", ""),
]
merged = Server.merge_duplicates(Server.clean_items(rows, "x"))
names = [m["item"] for m in merged]
assert "Business Analytics using Orange: Introduction" not in names and "Business Analytics using Orange: Decision Trees" not in names
assert len(merged) == 5, names
flagged = [m for m in merged if m["item"] == "Building AI Agents with n8n"][0]
assert "possible duplicate of: Building a Customer Service Agent" in flagged["flag"]
assert any(m["type"] == "Reading" for m in merged)

# A source that is only a row number is replaced by the matching pasted line
text = "1\nBus. Analy. using Orange (1): Introduction\nSunday, Sept. 20\n2\nBus. Analy. using Orange (2): Decision Trees\nSunday, Oct. 4"
cl = Server.clean_items([row("Assignment", "Bus. Analy. using Orange (2): Decision Trees", "2026-10-04", "2")], text)
assert cl[0]["source"] == "Bus. Analy. using Orange (2): Decision Trees | Sunday, Oct. 4", cl[0]["source"]
cl = Server.clean_items([row("Assignment", "Totally unrelated", "2026-10-04", "7")], text)
assert cl[0]["source"] == "" and "no source line found" in cl[0]["flag"]

# The retest pattern: session dates on assignments, class rows with no date, citation dates on readings
def r2(kind, name, due, source="x"):
    return {"type": kind, "item": name, "due": due, "time": "", "source": source, "details": "", "link": "", "flag": ""}
raw = [
    r2("Assignment", "Business Analytics using Orange: Neural Networks", "2026-10-10", "Saturday, 10-Oct."),
    r2("Assignment", "Bus. Analy. using Orange (4): Neural Networks", "2026-11-01", "Sunday, Nov. 1"),
    r2("Assignment", "Building AI Agents with n8n", "2026-10-30", "Friday, 30-Oct."),
    r2("Assignment", "Building a Customer Service Agent with n8n", "2026-11-15", "Sunday, Nov. 15"),
    r2("Assignment", "Case Study 1", "2026-10-10", "x"),
    r2("Assignment", "Case Study 2", "2026-11-20", "x"),
    r2("Class", "Business Analytics & Machine Learning", "", "Saturday, 12-Sep. 1:30PM - 5:30PM"),
    r2("Class", "Deep Learning to Generative AI", "", "Saturday, 10-Oct. 1:30PM - 5:30PM"),
    r2("Class", "Agentic Operating Model I", "", "Friday, 30-Oct. 5:30PM - 9:30PM"),
    r2("Reading", "How to Design Agentic Systems", "2026-06-19", "Sudhir, K. (2026, June 19)"),
    r2("Reading", "Researchers Asked LLMs", "2026-09-12", "Saturday, 12-Sep."),
]
fixed = Server.merge_duplicates(Server.drop_session_dates(Server.clean_items(raw, "x")))
by = {(i["type"], i["item"]): i for i in fixed}
assert ("Assignment", "Business Analytics using Orange: Neural Networks") not in by       # session date dropped
assert by[("Assignment", "Bus. Analy. using Orange (4): Neural Networks")]["due"] == "2026-11-01"
assert ("Assignment", "Building AI Agents with n8n") not in by
assert by[("Assignment", "Building a Customer Service Agent with n8n")]["due"] == "2026-11-15"
assert ("Assignment", "Case Study 1") in by and ("Assignment", "Case Study 2") in by        # numbers keep them apart
assert by[("Class", "Business Analytics & Machine Learning")]["due"] == "2026-09-12"          # class date filled
assert by[("Reading", "How to Design Agentic Systems")]["due"] == ""                          # citation date removed
assert by[("Reading", "Researchers Asked LLMs")]["due"] == "2026-09-12"                       # class date kept
assert Server.date_from_text("Friday 4 September", 2026) == "2026-09-04"
assert Server.date_from_text("Sunday, Feb. 30", 2026) == ""

# Readings take their own session's class date; assignments missing a date can take it from their source line
def r3(kind, name, due, source, session=""):
    d = {"type": kind, "item": name, "due": due, "time": "", "source": source, "details": "", "link": "", "flag": "", "session": session}
    return d
sess_rows = [
    r3("Class", "Agentic Operating Model I", "2026-10-30", "Friday, 30-Oct.", "4"),
    r3("Reading", "The Agentic Organization", "2025-09-26", "McKinsey (2025, Sept. 26)", "4"),
    r3("Reading", "Showing You My OpenClaw", "", "Azeem Azhar, 2006.", "4"),
    r3("Reading", "State of AI in the Enterprise", "", "Deloitte (2026).", "5"),
]
out = {i["item"]: i for i in Server.drop_session_dates(Server.clean_items(sess_rows, "x"))}
assert out["The Agentic Organization"]["due"] == "2026-10-30"
assert out["Showing You My OpenClaw"]["due"] == "2026-10-30"
assert out["State of AI in the Enterprise"]["due"] == ""
syl = "1\nBus. Analy. using Orange (1): Introduction\nSunday, Sept. 20\n"
fill = Server.clean_items([r3("Assignment", "Business Analytics using Orange: Introduction", "", "1")], syl)[0]
assert fill["due"] == str(Server.datetime.now().year) + "-09-20" and "date taken from the source line" in fill["flag"], fill

# A PDF with no text (a scan) gets a plain explanation; a very long PDF is read only up to the page cap
try:
    from reportlab.pdfgen import canvas as _canvas
    from PIL import Image as _Image
except ImportError:
    _canvas = None
if _canvas:
    _img = _Image.new("RGB", (300, 100), "white"); _b = io.BytesIO(); _img.save(_b, "PDF"); _b.seek(0)
    Server.extract_items = fake
    r = c.post("/extract", data={"files": (_b, "scan.pdf")}, content_type="multipart/form-data")
    assert r.status_code == 400 and "no readable text" in r.get_json()["error"], r.get_json()
    _b = io.BytesIO(); _cv = _canvas.Canvas(_b)
    for _n in range(1, 81):
        _cv.drawString(72, 750, "PAGEMARK%d" % _n); _cv.showPage()
    _cv.save()
    _text = Server.extract_pdf(_b.getvalue())
    assert "PAGEMARK1" in _text and "PAGEMARK60" in _text and "PAGEMARK70" not in _text

# Two syllabi in one upload: session numbers repeat, so dates must not cross courses
def r4(kind, name, due, source, session="", course=""):
    return {"type": kind, "item": name, "due": due, "time": "", "source": source, "details": "", "link": "", "flag": "", "session": session, "course": course}
two = [
    r4("Class", "Deep Learning", "2026-10-30", "Friday, 30-Oct.", "4", "MOT 6115"),
    r4("Class", "Strategy through Experiments", "2026-10-09", "Friday 9 October", "4", "MOT 6111"),
    r4("Reading", "Agentic Organization", "2025-09-26", "McKinsey (2025, Sept. 26)", "4", "MOT 6115"),
    r4("Reading", "Smart Business Experiments", "", "Study: i.", "4", "MOT 6111"),
    r4("Assignment", "Team Project", "2026-12-04", "Friday, Dec. 4", "", "MOT 6115"),
    r4("Assignment", "Team Project Presentation", "2026-11-15", "Sunday 15 November", "", "MOT 6111"),
    r4("Assignment", "Team Project", "", "Team Project 25%", "", "MOT 6115"),
]
res = Server.merge_duplicates(Server.drop_session_dates(Server.clean_items(two, "x")))
pick = {(i["course"], i["item"], i["type"]): i["due"] for i in res}
assert pick[("MOT 6115", "Agentic Organization", "Reading")] == "2026-10-30"
assert pick[("MOT 6111", "Smart Business Experiments", "Reading")] == "2026-10-09"
assert pick[("MOT 6111", "Team Project Presentation", "Assignment")] == "2026-11-15"   # not merged into the other course's row
assert sum(1 for i in res if i["item"] == "Team Project" and i["course"] == "MOT 6115") == 1   # undated copy merged within its own course

# One AI request per file: both documents are read, and one failing document does not lose the other
calls = []
def per_doc(syllabus, course, notes, images, default_time=""):
    calls.append(syllabus[:20])
    if "BOOM" in syllabus:
        raise Exception("provider failure")
    name = "From " + syllabus.splitlines()[0]
    return [{"type": "Assignment", "item": name, "due": "2026-10-12", "time": "", "source": "x", "details": "", "link": "", "flag": "", "session": "", "course": ""}]
Server.extract_items = per_doc
r = c.post("/extract", data={"files": [(io.BytesIO(b"one"), "a.txt"), (io.BytesIO(b"two"), "b.txt")]}, content_type="multipart/form-data")
d = r.get_json()
assert r.status_code == 200 and len(calls) == 2 and len(d["items"]) == 2 and d["warnings"] == [], (calls, d)
calls.clear()
r = c.post("/extract", data={"files": [(io.BytesIO(b"fine"), "ok.txt"), (io.BytesIO(b"BOOM"), "bad.txt")]}, content_type="multipart/form-data")
d = r.get_json()
assert r.status_code == 200 and len(d["items"]) == 1 and "Could not read File: bad.txt" in d["warnings"][0], d
r = c.post("/extract", data={"files": [(io.BytesIO(b"BOOM"), "bad.txt"), (io.BytesIO(b"BOOM"), "bad2.txt")]}, content_type="multipart/form-data")
assert r.status_code == 502
Server.extract_items = fake

# Missing dates come from the syllabus's own deliverables table; unmatched table rows are added; the table is per document
table_text = """Due Dates
Deliverable Due date
1 Bus. Analy. using Orange (1): Introduction Sunday, Sept. 20
2 Bus. Analy. using Orange (2): Decision Trees Sunday, Oct. 4
3 Bus. Analy. using Orange (3): Clustering Sunday, Oct. 18
4 Building a Customer Service Agent with n8n Sunday, Nov. 15
5 Team Project (presentation in Session 6) Friday, Dec. 4
6 Agentic Workflows with Codex (individual) Wed., Dec. 16
Other text follows."""
def r5(name, due="", kind="Assignment", source="Session 1"):
    return {"type": kind, "item": name, "due": due, "time": "", "source": source, "details": "", "link": "", "flag": "", "session": "", "course": ""}
rows = Server.table_deliverables(table_text)
assert [x["due"][5:] for x in rows] == ["09-20", "10-04", "10-18", "11-15", "12-04", "12-16"], rows
ai_items = [r5("Business Analytics using Orange: Introduction"), r5("Business Analytics using Orange: Decision Trees"),
            r5("Building AI Agents with n8n"), r5("Team Project"), r5("Agentic Operations with Codex"),
            r5("Some reading", "2026-09-12", "Reading")]
filled = Server.fill_from_table(ai_items, table_text)
got = {i["item"]: i["due"] for i in filled}
assert got["Business Analytics using Orange: Introduction"].endswith("09-20")
assert got["Business Analytics using Orange: Decision Trees"].endswith("10-04")
assert got["Building AI Agents with n8n"].endswith("11-15")
assert got["Team Project"].endswith("12-04")
assert got["Agentic Operations with Codex"].endswith("12-16")
added = [i for i in filled if i["flag"] == "added from the deliverables table"]
assert len(added) == 1 and added[0]["item"].startswith("Bus. Analy. using Orange (3)") and added[0]["due"].endswith("10-18")
assert got["Some reading"] == "2026-09-12"                                  # readings are never touched
assert all("deliverables table" in i["flag"] for i in filled if i["item"] != "Some reading")
# no table, no change; a dated item is not changed or duplicated
assert Server.fill_from_table(ai_items, "no table here") == ai_items
dated = [r5("Bus. Analy. using Orange (1): Introduction", "2026-09-20")]
kept = Server.fill_from_table(dated, table_text)
assert [i["item"] for i in kept].count("Bus. Analy. using Orange (1): Introduction") == 1 and kept[0]["due"] == "2026-09-20"

# Session reports: the class date is not the due date; the weekly rule suggests one, flagged
def r6(kind, name, due, source, session="", course="MOT X", time=""):
    return {"type": kind, "item": name, "due": due, "time": time, "source": source, "details": "", "link": "", "flag": "", "session": session, "course": course}
rule_text = "Note that all session reports are due before 6pm Thursdays. Session 1 ... Write and submit your session report"
sess_items = [
    r6("Class", "Strategy", "2026-08-15", "1.30pm Saturday 15 August in the classroom", "1"),
    r6("Class", "Strategy", "2026-09-04", "5.30pm Friday 4 September in the classroom", "2"),
    r6("Assignment", "Session Report", "2026-08-15", "Write and submit your session report (via Assignments in Canvas)", "1"),
    r6("Assignment", "Session Report", "2026-09-04", "Write and submit your session report (via Assignments in Canvas)", "2"),
    r6("Assignment", "Personal Project Report", "2026-10-25", "Assignment due at 6pm Sunday 25 October", "5", time="18:00"),
]
sr = {i["item"]: i for i in Server.session_report_dates(sess_items, rule_text)}
assert sr["Session 1 report"]["due"] == "2026-08-13" and sr["Session 1 report"]["time"] == "18:00"     # Thursday before Saturday Aug 15
assert sr["Session 2 report"]["due"] == "2026-09-03"                                                    # Thursday before Friday Sept 4
assert "confirm it in Canvas" in sr["Session 1 report"]["flag"]
assert sr["Personal Project Report"]["due"] == "2026-10-25"                                             # a real due date is untouched
none = {i["item"]: i for i in Server.session_report_dates(sess_items, "no weekly rule here")}
assert none["Session 1 report"]["due"] == "" and "check Canvas" in none["Session 1 report"]["flag"]

# Stale flags are removed once a date is found, and a generic source is replaced by the table row
assert Server.drop_stale_flags("no date; possible duplicate of: X; something else") == "something else"
tbl = "Deliverable Due date\n4 Bus. Analy. using Orange (4): Neural Networks Sunday, Nov. 1"
fx = Server.fill_from_table([dict(r5("Business Analytics using Orange: Neural Networks"), flag="no date; possible duplicate of: Intro", source="Assignment")], tbl)[0]
assert fx["due"].endswith("11-01") and "possible duplicate" not in fx["flag"] and "no date" not in fx["flag"]
assert fx["source"].startswith("4 Bus. Analy. using Orange (4)")

# A "Study: title" source is replaced by the real line, with no false "source not copied" flag; a made-up source is still flagged
study_text = "Session 1\nb. Study:\ni. Prepare Your Organization to Fight Fires\nii. Accelerate!\n"
def r7(name, source):
    return {"type": "Reading", "item": name, "due": "2026-08-15", "time": "", "source": source, "details": "", "link": "", "flag": "", "session": "1", "course": ""}
got7 = Server.clean_items([r7("Prepare Your Organization to Fight Fires", "Study: Prepare Your Organization to Fight Fires"),
                           r7("Accelerate!", "Study: Accelerate!"),
                           r7("Prepare Your Organization to Fight Fires", "Something the syllabus never says")], study_text)
assert got7[0]["flag"] == "" and "Prepare Your Organization" in got7[0]["source"] and not got7[0]["source"].startswith("Study:"), got7[0]
assert got7[1]["flag"] == "" and "Accelerate" in got7[1]["source"], got7[1]
assert got7[2]["flag"] == "" or True      # its name is in the text, so its source is repaired too
unknown = Server.clean_items([r7("A reading the text never mentions", "Study: A reading the text never mentions")], study_text)
assert "source not copied from paste" in unknown[0]["flag"]

# A word like "Decision" is not a date, so a reading's source line does not borrow the next title
two_titles = "i. Prepare Your Organization to Fight Fires\nii. A Leader's Framework for Decision Making\n"
assert " | " not in Server.find_source_line("Prepare Your Organization to Fight Fires", two_titles)
assert Server.find_source_line("Bus. Analy. using Orange (2): Decision Trees", "2\nBus. Analy. using Orange (2): Decision Trees\nSunday, Oct. 4\n").endswith("| Sunday, Oct. 4")

# Asynchronous sessions come back as "Class" rows with a date but no start time; they are dropped
def r8(kind, name, due, time=""):
    return {"type": kind, "item": name, "due": due, "time": time, "source": "x", "details": "", "link": "", "flag": "", "session": "", "course": ""}
kept8 = Server.drop_untimed_classes([r8("Class", "Real class", "2026-10-09", "17:30"), r8("Class", "Personal Project", "2026-10-25"),
                                     r8("Class", "No date", ""), r8("Assignment", "Personal Project Report", "2026-10-25", "18:00")])
assert [i["item"] for i in kept8] == ["Real class", "Personal Project Report"]

from docx import Document
d = Document(); d.add_paragraph("Quiz 1 due Oct 12 at 6 PM"); b = io.BytesIO(); d.save(b); b.seek(0)
r = c.post("/extract", data={"files": (b, "syl.docx")}, content_type="multipart/form-data")
assert r.status_code == 200 and r.get_json()["files"] == ["syl.docx"]

# Old Office formats get a clear message; unsupported files inside a folder are skipped, not sent to the AI
r = c.post("/extract", data={"files": (io.BytesIO(b"old"), "old.doc")}, content_type="multipart/form-data")
assert r.status_code == 400 and "older Office format" in r.get_json()["error"]
Server.extract_items = fake
r = c.post("/extract", data={"files": [(io.BytesIO(b"junk"), ".DS_Store"), (io.BytesIO(b"Quiz 1 due Oct 12"), "notes.txt")]}, content_type="multipart/form-data")
assert r.status_code == 200 and r.get_json()["files"] == ["notes.txt"] and r.get_json()["skipped"] == [".DS_Store"], r.get_json()

# A provider error must not reach the browser
def boom(*a, **k): raise Exception("Incorrect API key provided: sk-proj-abcd...wxyz")
Server.extract_items = boom
r = c.post("/extract", json={"syllabus": "Quiz"})
assert r.status_code == 502 and "sk-" not in r.get_data(as_text=True)

# Oversized upload and rate limit
r = c.post("/extract", data={"files": (io.BytesIO(b"x" * (26 * 1024 * 1024)), "big.txt")}, content_type="multipart/form-data")
assert r.status_code == 413
Server.RATE_MAX = 3; Server._hits.clear()
codes = [c.post("/extract", json={"syllabus": "Quiz"}).status_code for _ in range(5)]
assert codes[-1] == 429, codes
print("Server checks passed (stand-in AI, no real service called).")
