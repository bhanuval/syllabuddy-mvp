(function (global) {
const MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split(" ");
const MON = "(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)";
const RE_DAY_MON = new RegExp("\\b(\\d{1,2})-" + MON + "\\b\\.?", "i");                                  // 12-Sep.
const RE_D_MON = new RegExp("\\b(\\d{1,2})(?:st|nd|rd|th)?\\s+(?:of\\s+)?" + MON + "\\b\\.?(?:,?\\s+(\\d{4}))?", "i"); // 15 August
const RE_MON_DAY = new RegExp("\\b" + MON + "\\.?\\s+(\\d{1,2})(?!\\d|:\\d)(?:st|nd|rd|th)?(?:,?\\s+(\\d{4}))?", "i"); // Sept. 20
const RE_SLASH = /\b(\d{1,2})\/(\d{1,2})(?:\/(\d{2,4}))?\b/;
const KEYWORDS = /(assignment|homework|quiz|exam|midterm|final|project|paper|essay|presentation|report|reading|case|lab|due|deliverable|submission)/i;
const UNCLEAR = /\b(tbd|tba|see canvas|see lms|to be announced)\b/i;
const WEEKDAY = /\b(Mon|Tue|Tues|Wed|Thu|Thur|Thurs|Fri|Sat|Sun)[a-z]*\.?,?\s*/gi;
const WD_NAME = /\b(Monday|Mon|Tuesday|Tues|Tue|Wednesday|Wed|Thursday|Thurs|Thur|Thu|Friday|Fri|Saturday|Sat|Sunday|Sun)\b/i;
const WD_NUM = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 };

// Time rules. Class meeting times are ignored; a time counts as a due time only with a due cue.
const TIME_AMPM = /\b(\d{1,2})(?:[:.](\d{2}))?\s*([ap])\.?m\.?(?![a-z])/i;
const TIME_24 = /\b([01]?\d|2[0-3]):([0-5]\d)\b(?!\s*[ap]\.?m)/;
const TIME_NOON = /\bnoon\b/i;
const RANGE = /(\d{1,2}[:.]\d{2}|\d{1,2}\s*[ap]\.?m\.?)\s*[\u2013\u2014\-]\s*\d{1,2}(?:[:.]\d{2})?\s*[ap]\.?m/i;
const CLASSWORDS = /\b(class|classroom|lecture|meets?|meeting|office hours|zoom|webinar)\b/i;
const DUECUE = /\b(due|deadline|closes?|closing|submit|submission|by|before|no later than|until|expires?)\b/i;

function monthNum(name) { return MONTHS.indexOf(name.slice(0, 3).toLowerCase()) + 1; }
function findDate(line) {
  let m = line.match(RE_DAY_MON);
  if (m) return { mon: monthNum(m[2]), day: +m[1], year: null, text: m[0] };
  m = line.match(RE_D_MON);
  if (m) return { mon: monthNum(m[2]), day: +m[1], year: m[3] ? +m[3] : null, text: m[0] };
  m = line.match(RE_MON_DAY);
  if (m) return { mon: monthNum(m[1]), day: +m[2], year: m[3] ? +m[3] : null, text: m[0] };
  m = line.match(RE_SLASH);
  if (m && +m[1] >= 1 && +m[1] <= 12 && +m[2] >= 1 && +m[2] <= 31) {
    let y = m[3] ? +m[3] : null; if (y !== null && y < 100) y += 2000;
    return { mon: +m[1], day: +m[2], year: y, text: m[0] };
  }
  return null;
}
function validDate(d, defaultYear) {
  const year = d.year || defaultYear;
  const date = new Date(0);
  date.setUTCFullYear(year, d.mon - 1, d.day);
  date.setUTCHours(0, 0, 0, 0);
  return date.getUTCFullYear() === year && date.getUTCMonth() === d.mon - 1 && date.getUTCDate() === d.day;
}
function iso(d, defaultYear) {
  if (!validDate(d, defaultYear)) return "";
  return (d.year || defaultYear) + "-" + String(d.mon).padStart(2, "0") + "-" + String(d.day).padStart(2, "0");
}
// Checks a stated weekday against the date, which catches wrong-year and typo problems.
function weekdayCheck(line, d, defaultYear) {
  if (!validDate(d, defaultYear)) return "invalid calendar date";
  const m = line.match(WD_NAME);
  if (!m) return "";
  const dow = new Date(d.year || defaultYear, d.mon - 1, d.day).getDay();
  return dow === WD_NUM[m[1].slice(0, 3).toLowerCase()] ? "" : "weekday does not match date (check the year)";
}
const pad = n => String(n).padStart(2, "0");
function parseTime(line) {
  let m = line.match(TIME_AMPM);
  if (m) {
    const h = +m[1], min = m[2] ? +m[2] : 0;
    if (h < 1 || h > 12 || min > 59) return null;
    return { hhmm: pad((h % 12) + (m[3].toLowerCase() === "p" ? 12 : 0)) + ":" + pad(min), text: m[0] };
  }
  m = line.match(TIME_24);
  if (m) return { hhmm: pad(+m[1]) + ":" + m[2], text: m[0] };
  m = line.match(TIME_NOON);
  if (m) return { hhmm: "12:00", text: m[0] };
  return null;
}
// paired = the time sits on the date line of a table row, so it is the due column
function getTime(line, paired) {
  if (RANGE.test(line) || CLASSWORDS.test(line)) return { time: "", flag: "", text: "" };
  const t = parseTime(line);
  if (!t) return { time: "", flag: "", text: "" };
  if (paired || DUECUE.test(line)) return { time: t.hhmm, flag: "", text: t.text };
  return { time: "", flag: "unclear time", text: "" };
}
function cleanItem(t) {
  let s = t.replace(WEEKDAY, "").replace(/^[\s\-:|,\u2022*]+|[\s\-:|,\u2022*(]+$/g, "").trim();
  let prev;
  do { prev = s; s = s.replace(/[\s,]+(by|at|due|before|@)\s*$/i, "").trim(); } while (s !== prev);
  return s;
}
const BULLET = /^[\u2022\u25AA\u25CF\u2023*\-]\s*/;
const ROMAN = /^(?:i{1,3}|iv|v|vi{1,3}|ix|x)[.)]\s+/;
const TYPEWORDS = /\b(quiz(?:zes)?|exam|midterm|final|paper|essay|project|case|lab|presentation|report|reading)s?\b/gi;
const FOOTER = /Page \d+ of \d+,[^\n]*/g;     // repeated page footers from PDFs

// Item type from its name, so exams and quizzes are not all called "assignments".
function classify(text) {
  if (/\bquiz(?:zes)?\b/i.test(text)) return "Quiz";
  if (/\b(?:exam|midterm)s?\b/i.test(text)) return "Exam";
  if (/\b(?:project|presentation|capstone)\b/i.test(text)) return "Project";
  return "Assignment";
}     // repeated page footers from PDFs

// Joins lines that a PDF wrapped mid-sentence, then splits into sentences.
function sentences(text) {
  const lines = text.replace(FOOTER, "").split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  const joined = [];
  let cur = "";
  lines.forEach(l => {
    if (cur && !/[.:?!]$/.test(cur) && /^[a-z0-9]/.test(l)) cur += " " + l;
    else { if (cur) joined.push(cur); cur = l; }
  });
  if (cur) joined.push(cur);
  const out = [];
  joined.forEach(j => j.split(/(?<=[.!?])\s+(?=[A-Z\u201C"])/).forEach(x => out.push(x.trim())));
  return out;
}

// General rules: sentences with a due cue and a time but no date, such as
// "All assignments are due Sundays at 11:59 PM" or "Quizzes close Thursday by 6 PM".
function findRules(text) {
  const rules = [];
  sentences(text).forEach(l => {
    const line = l.replace(BULLET, "");
    if (!line || findDate(line) || RANGE.test(line) || CLASSWORDS.test(line) || !DUECUE.test(line)) return;
    const t = parseTime(line);
    if (!t) return;
    const types = (line.match(TYPEWORDS) || []).map(w => w.toLowerCase().slice(0, 4));
    rules.push({ time: t.hhmm, line, types });
  });
  return rules;
}

// Layout A: an outline with "Session N" blocks. Handles "Readings:/Assignments:" bullet lists
// and the "Assignments before this session / Study: i. ii. iii." style.
function extractOutline(text, defaultYear) {
  const out = [];
  const lines = text.replace(FOOTER, "").split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  let sess = null, section = null, expectTitle = false, dueInfo = null, last = null, curReport = null, qs = [];
  const items = [];
  function flush() {
    if (!sess) return;
    items.forEach(it => {
      if (it.type === "Reading") { it.due = sess.date || ""; it.dflag = sess.dflag || ""; }
      else { it.due = it.due || ""; it.dflag = it.dflag || ""; }
      it.time = it.time || ""; it.flag = ""; it.details = it.details || "";
      it.source = "Session " + sess.n + (sess.title ? ": " + sess.title : "");
      out.push(it);
    });
    items.length = 0;
  }
  lines.forEach(rawLine => {
    const sm = rawLine.match(/^Session\s+(\d+)/i);
    if (sm) { flush(); sess = { n: sm[1], title: "", date: "", dflag: "" }; section = null; expectTitle = true; dueInfo = null; last = null; curReport = null; qs = []; return; }
    if (!sess) return;
    const isBullet = BULLET.test(rawLine);
    const body = rawLine.replace(BULLET, "").trim();

    if (/^Assignment due\b/i.test(body)) {                       // "Assignment due at 6pm Sunday 25 October"
      const d = findDate(body);
      if (d) { const t = parseTime(body); dueInfo = { due: iso(d, defaultYear), time: t ? t.hhmm : "", dflag: weekdayCheck(body, d, defaultYear) }; }
      section = null; last = null; return;
    }
    if (/^(?:\d+\.\s*)?Activities during class/i.test(body)) { section = null; last = null; return; }
    if (/^(?:\d+\.\s*)?Assignments?\s+(?:before|for)\s+this\s+session/i.test(body)) { section = "block"; last = null; return; }
    if (/^(?:[a-z]\.\s*)?Study:?\s*$/i.test(body)) { section = "Reading"; last = null; return; }
    const sub = body.replace(/^[a-z]\.\s+/, "").match(/^(?:Write and submit|Prepare and submit|Submit)\s+your\s+(.+?)(?:\s*\(via|\s+before\b|\.|$)/i);
    if (sub) {
      const name = sub[1].trim();
      const isSession = /session report/i.test(name);
      const item = isSession ? "Session " + sess.n + " report" + (sess.title ? ": " + sess.title : "") : name.charAt(0).toUpperCase() + name.slice(1);
      curReport = { type: classify(item), item, due: dueInfo ? dueInfo.due : "", time: dueInfo ? dueInfo.time : "", dflag: dueInfo ? dueInfo.dflag : "" };
      items.push(curReport); qs = [];
      section = "questions"; last = null; return;
    }
    if (/^readings?:?$/i.test(body)) { section = "Reading"; expectTitle = false; last = null; return; }
    if (/^assignments?:?$/i.test(body)) { section = "Assignment"; expectTitle = false; last = null; return; }

    if (section === "questions") {                               // the report's questions become its details
      if (ROMAN.test(body)) { qs.push(body.replace(ROMAN, "")); }
      else if (qs.length && !isBullet && !/^[a-z]\.\s|^\d+\.\s/.test(body)) { qs[qs.length - 1] += " " + body; }
      else return;
      curReport.details = "Questions:\n" + qs.map((q, i) => (i + 1) + ". " + q).join("\n");
      return;
    }
    if (!section) {                                              // session header area: class date and title
      if (/^asynchronous/i.test(body)) { return; }
      const d = findDate(rawLine);
      if (d && !sess.date) { sess.date = iso(d, defaultYear); sess.dflag = weekdayCheck(rawLine, d, defaultYear); return; }
      if (expectTitle && !isBullet) { sess.title = body; expectTitle = false; }
      return;
    }
    if (section === "Reading" && ROMAN.test(body)) { last = { type: "Reading", item: body.replace(ROMAN, "") }; items.push(last); return; }
    if (section === "Reading" && last && !isBullet && !/^[a-z]\.\s|^\d+\.\s/.test(body)) { last.item += " " + body; return; }   // wrapped title
    if ((section === "Reading" || section === "Assignment") && isBullet && body) { items.push({ type: section === "Assignment" ? classify(body) : section, item: body }); }
  });
  flush();
  return out;
}

// Layout B: one deadline per line, or a table pasted as item line then date line.
function extractTable(text, defaultYear) {
  const out = [];
  // Tables copied from Word, Canvas, or web pages often use tabs between cells, so split on tabs too.
  const lines = text.split(/\r?\n|\t+/).map(l => l.trim()).filter(l => l && !/^\d{1,2}[.)]?$/.test(l));
  const HEADER = /^(deliverable|due date|assignment|item|date|due)s?$/i;
  let pending = null, last = null;
  lines.forEach(line => {
    if (HEADER.test(line)) { pending = null; last = null; return; }
    const d = findDate(line);
    if (d) {
      last = null; // Only an immediately following time cell may attach to this row.
      const afterDate = line.replace(d.text, "");
      const restProbe = cleanItem(afterDate.replace(TIME_AMPM, "").replace(TIME_24, ""));
      const paired = restProbe.length < 3 && !!pending;
      const tm = getTime(line, paired);
      const rest = cleanItem(afterDate.replace(tm.text || "\u0000", ""));
      const dflag = weekdayCheck(line, d, defaultYear);
      if (rest.length >= 3 && KEYWORDS.test(line)) {
        last = { type: classify(rest), item: rest, due: iso(d, defaultYear), time: tm.time, flag: tm.flag, dflag, source: line };
        out.push(last);
      } else if (paired) {
        last = { type: classify(pending), item: pending, due: iso(d, defaultYear), time: tm.time, flag: tm.flag, dflag, source: pending + " | " + line };
        out.push(last);
      }
      pending = null;
    } else if (last && !last.time && !last.flag && !cleanItem(line.replace(TIME_AMPM, "").replace(TIME_24, "")) && parseTime(line) && !RANGE.test(line)) {
      last.time = parseTime(line).hhmm;   // a time in its own table cell, right after a date cell
      last = null;
    } else if (UNCLEAR.test(line) && KEYWORDS.test(line)) {
      out.push({ type: classify(line), item: line, due: "", time: "", flag: "", dflag: "", source: line });
      pending = null; last = null;
    } else if (parseTime(line) && DUECUE.test(line)) {
      last = null;
      /* a general rule line, not an item */
    } else { pending = line; last = null; }
  });
  return out;
}

// A link counts only if it appears word for word in the pasted text.
function firstUrl(t) {
  const m = String(t).match(/https?:\/\/[^\s<>"')\]]+/i);
  return m ? m[0].replace(/[.,;:!?]+$/, "") : "";
}

function extractItems(text, defaultYear, defaultTime) {
  const items = /^\s*Session\s+\d+/im.test(text) ? extractOutline(text, defaultYear) : extractTable(text, defaultYear);
  const rules = findRules(text);
  items.forEach(it => {
    it.fromDefault = false; it.fromRule = "";
    if (it.time || it.flag) return;
    const hay = (it.item + " " + it.source).toLowerCase();
    let r = null;
    if (it.type === "Reading") r = rules.find(x => x.types.includes("read"));
    else r = rules.find(x => x.types.length && !x.types.includes("read") && x.types.some(k => hay.includes(k))) || rules.find(x => !x.types.length);
    if (r) { it.time = r.time; it.fromRule = r.line; }
    else if (it.type !== "Reading" && defaultTime) { it.time = defaultTime; it.fromDefault = true; }
  });
  items.forEach(it => { it.details = it.details || ""; it.link = it.link || firstUrl(it.item + " " + it.source); });
  return items;
}

// Builds an .ics calendar file. Timed items become a 30-minute event that ends at the due time;
// items with no time become all-day events. Times carry no time zone, so they show in the
// calendar's own time zone. Rows with no valid date are skipped and counted.
const p2 = n => String(n).padStart(2, "0");
function icsEscape(t) { return String(t).replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\r?\n/g, "\\n"); }
function fold(line) { const out = []; let t = line; while (t.length > 73) { out.push(t.slice(0, 73)); t = " " + t.slice(73); } out.push(t); return out.join("\r\n"); }
const fmtLocal = d => d.getFullYear() + p2(d.getMonth() + 1) + p2(d.getDate()) + "T" + p2(d.getHours()) + p2(d.getMinutes()) + "00";
const fmtDate = d => d.getFullYear() + p2(d.getMonth() + 1) + p2(d.getDate());
function buildICS(rows, reminder, now) {
  now = now || new Date();
  const stamp = now.getUTCFullYear() + p2(now.getUTCMonth() + 1) + p2(now.getUTCDate()) + "T" + p2(now.getUTCHours()) + p2(now.getUTCMinutes()) + p2(now.getUTCSeconds()) + "Z";
  const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SyllaBuddy//Syllabus deadlines//EN", "CALSCALE:GREGORIAN"];
  const offsets = reminder === "day" ? ["-P1D"] : reminder === "hour" ? ["-PT1H"] : reminder === "both" ? ["-P1D", "-PT1H"] : [];
  let count = 0, skipped = 0;
  rows.forEach((r, i) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(r.date || "");
    if (!m || !r.item) { skipped++; return; }
    const y = +m[1], mo = +m[2] - 1, d = +m[3], day = new Date(y, mo, d);
    if (day.getMonth() !== mo) { skipped++; return; }
    const tm = /^(\d{2}):(\d{2})$/.exec(r.time || "");
    lines.push("BEGIN:VEVENT", "UID:syllabuddy-" + stamp + "-" + i + "@local", "DTSTAMP:" + stamp);
    if (tm) {
      const due = new Date(y, mo, d, +tm[1], +tm[2]), start = new Date(due.getTime() - 30 * 60000);
      lines.push("DTSTART:" + fmtLocal(start), "DTEND:" + fmtLocal(due));
    } else {
      lines.push("DTSTART;VALUE=DATE:" + fmtDate(day), "DTEND;VALUE=DATE:" + fmtDate(new Date(y, mo, d + 1)));
    }
    lines.push(fold("SUMMARY:" + icsEscape((r.course ? r.course + ": " : "") + r.item)));
    const link = /^https?:\/\/\S+$/i.test(r.link || "") ? r.link : "";
    const desc = "Type: " + (r.type || "") + (tm ? ". Due at " + r.time : "") + (r.source ? ". Source: " + r.source : "") +
      (r.details ? "\n\n" + String(r.details).slice(0, 2000) : "") + (link ? "\n\nLink: " + link : "");
    lines.push(fold("DESCRIPTION:" + icsEscape(desc)));
    if (link) lines.push(fold("URL:" + link));
    offsets.forEach(o => {
      if (!tm && o !== "-P1D") return;     // an hour-before reminder only makes sense for timed items
      lines.push("BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:" + icsEscape(r.item), (tm ? "TRIGGER;RELATED=END:" : "TRIGGER:") + o, "END:VALARM");
    });
    lines.push("END:VEVENT"); count++;
  });
  lines.push("END:VCALENDAR");
  return { text: lines.join("\r\n") + "\r\n", count, skipped };
}

if (typeof module !== "undefined") module.exports = { extractItems, findDate, parseTime, getTime, findRules, classify, buildICS };

global.extractItems = extractItems;
})(window);
