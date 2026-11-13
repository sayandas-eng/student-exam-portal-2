# Student Examination Portal (starter build)

A private Flask + SQLite examination portal. Students cannot register. Admin imports student credentials and question sheets through the dashboard.

## Run locally

1. Install Python 3.10+.
2. Open a terminal in this folder and create a virtual environment:
   - Windows: `py -m venv .venv` then `.venv\\Scripts\\activate`
   - macOS/Linux: `python3 -m venv .venv` then `source .venv/bin/activate`
3. Install dependencies: `pip install -r requirements.txt`
4. The project includes a local `.env` file with the preset admin login (`teacher` / `Sayan2007`) and a generated Flask secret. The app loads it automatically; no terminal prompts are required. Change the admin password before using this on a public server. Keep `.env` and `portal.db` private.
5. Start: `python app.py`
6. Open `http://127.0.0.1:5000`.

The admin account is initialized from `ADMIN_USERNAME` and `ADMIN_PASSWORD` the first time the database is created. Keep `portal.db` private and back it up.

## Excel templates

### Students workbook
First worksheet, first row headers exactly:

| username | password |
|---|---|
| student01 | temporary-password |

Passwords are converted to secure hashes during import. Do not email or publish the source workbook.

### Questions workbook
First worksheet, first row headers:

`question`, `type`, `option_a`, `option_b`, `option_c`, `option_d`, `answer`, `marks`

Supported types: `mcq`, `fill`, `text`. For MCQs, `answer` must exactly match the correct option text. For fill questions, it must match the expected answer. `text` questions are intended for manual grading. Example rows:

| question | type | option_a | option_b | option_c | option_d | answer | marks |
|---|---|---|---|---|---|---|---|
| 2 + 2 = ? | mcq | 3 | 4 | 5 | 6 | 4 | 1 |
| Capital of France? | fill | | | | | Paris | 2 |
| Explain photosynthesis. | text | | | | | | 5 |

Importing creates a draft. Publish it from the admin dashboard when ready.

## Important limitations before real exams

- This is a starter implementation, not a certified high-stakes exam system. Test thoroughly with dummy accounts before using it with students.
- A normal browser page cannot block OS-level screenshots/screen recordings, another phone, or tools outside the browser. This build submits and locks an attempt when the exam tab becomes hidden or fullscreen is exited after fullscreen has been enabled. Browser/device behavior varies; use a managed Android kiosk or dedicated secure exam browser for stronger lockdown.
- The timer is checked by the server on answer saves and page loads. The client countdown is a display. For production, add a server-side scheduled expiry worker and robust reconnection handling.
- This starter currently supports one published exam at a time in the student view. Add per-student exam assignment and scheduling before using multiple simultaneous exam groups.
- The review period is included in the timer display, but answer edits are currently blocked once the answering deadline passes. Decide whether review should permit editing and adjust the API accordingly.
- Add HTTPS, secure production cookies, CSRF protection, rate limiting, database backups, audit retention, and a production WSGI server before public deployment.
- Written questions with a non-empty answer key are now auto-checked using normalized phrase matching; written questions with no answer key remain available for manual grading. Confirm grading with test cases.


## Opening the site on Android while Flask runs on your computer

- Connect the Android phone and computer to the same Wi-Fi network.
- Start Flask on the computer with `python app.py` (it listens on `0.0.0.0:5000`).
- Find the computer's private IPv4 address (`ipconfig` on Windows).
- On Android Chrome, open `http://COMPUTER-IP:5000` (replace with the computer's IPv4 address).
- Allow Python/port 5000 through the computer's firewall on a private network if prompted. Do not expose the development server directly to the public internet.

## Security notes

The bundled `.env` contains the requested initial admin credentials. It is excluded from Git by `.gitignore`, but anyone who receives this project archive can read it. Change `ADMIN_PASSWORD` and `SECRET_KEY` before deployment and keep `.env` private. The requested password is a starter credential, not a strong production password.

The browser can detect many tab/app switches, but cannot reliably prevent OS screenshots, screen recordings, external cameras, or use of a second device. For Android device-level restrictions, deploy through Android Enterprise kiosk/lock-task mode or a dedicated secure exam client.


## Deploy on Render (free web service) + Neon PostgreSQL (free database)

This version supports PostgreSQL through `DATABASE_URL` and keeps SQLite for local development. Do not deploy with SQLite on Render Free: its filesystem is ephemeral and the database can be lost on restarts, spin-downs, and redeploys.

1. Create a Neon project at https://neon.tech/ and copy its PostgreSQL connection string (use the pooled connection string if offered). Keep it private.
2. Create a private GitHub repository and upload the contents of this project folder. Confirm `.env` and any `portal.db` are NOT uploaded.
3. In Render, choose **New → Blueprint**, connect the GitHub repository, and deploy the included `render.yaml`.
4. In the Render service's **Environment** page, set `DATABASE_URL` to the Neon connection string and set `ADMIN_PASSWORD` to a strong, unique password. Keep `ADMIN_USERNAME=teacher` or change it. Render generates `SECRET_KEY` automatically.
5. Trigger a deploy if Render does not deploy automatically. Open the generated `https://...onrender.com` URL.
6. Log in as admin and import your student list and question workbook. Test with dummy student accounts before a real exam.

### Free-tier warnings

- Render Free web services sleep after 15 minutes without inbound traffic and may take about a minute to wake. They are not guaranteed 24/7.
- Render Free local files are ephemeral, which is why this deployment uses Neon rather than SQLite.
- Neon Free currently has usage/storage limits and can suspend compute when monthly compute or network quotas are exhausted. Check its current plan limits.
- Free hosting is suitable for testing or a small pilot, not a high-stakes examination. Do not run a live exam until you have tested concurrent logins, answer saving, submission, database persistence, backups/export, and recovery.
- The app still needs production security hardening (CSRF protection, rate limiting, stronger audit controls, server-side expiry worker, and security review) before a real high-stakes exam.
- The admin account is seeded only when it does not already exist. Changing `ADMIN_PASSWORD` in Render later does not reset an existing admin password.
