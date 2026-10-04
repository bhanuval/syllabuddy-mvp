# SyllaBuddy (Flask version): deploy guide

Hamedah's app, plus a few small safety changes (see "What was changed"). It serves the AI page at `/`, the rules-only page at `/baseline`, and the AI endpoint at `/extract`. The OpenAI key stays on the server.

## Files
- `Server.py` the Flask app (Hamedah's code, lightly hardened)
- `index.html` the AI page, `baseline.html` the rules-only page, `rules-extractor.js` shared rules
- `requirements.txt` the packages (the file name must be exactly this)
- `.gitignore` keeps `.env` out of GitHub. Do not remove the `.env` line.
- `test_server.py` checks with a stand-in AI (no key needed): `python test_server.py`

## Deploy on Render (free plan)
1. Put these files at the top level of a GitHub repo.
2. In Render: New, Web Service, connect the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn Server:app --timeout 120`
5. Environment variables: `OPENAI_API_KEY` (your key; never in the repo). Optional: `OPENAI_MODEL`, `RATE_MAX`.
6. Create the service, wait for "Live", and open the address Render gives you.

The free plan sleeps after about 15 minutes without traffic, and the first request afterward can take up to about 30 seconds. Open the link yourself a few minutes before sending it to testers.

## Before you share the link
- Set a low monthly spending limit at OpenAI.
- Tell testers that pasted text and uploaded files go to an outside AI service, and to remove names, emails, and student IDs first.
- The rate limit is approximate. The spending limit is the real safeguard.

## What was changed from Hamedah's version
- The model name can be set with `OPENAI_MODEL` (default unchanged).
- Whole uploads are capped at 25 MB, with a clear message.
- Basic per-IP rate limit.
- Provider errors are logged without details and the browser gets a plain message, so no internal text or key fragments reach it.
- `requirements.txt` renamed from `requirement.txt`, duplicate removed, `gunicorn` added.
- `.gitignore` now actually lists `.env`.
The extraction logic and prompt are unchanged.
