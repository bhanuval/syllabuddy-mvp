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

from docx import Document
d = Document(); d.add_paragraph("Quiz 1 due Oct 12 at 6 PM"); b = io.BytesIO(); d.save(b); b.seek(0)
r = c.post("/extract", data={"files": (b, "syl.docx")}, content_type="multipart/form-data")
assert r.status_code == 200 and r.get_json()["files"] == ["syl.docx"]

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
