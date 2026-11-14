# Abhishek Santra English Guidance — Examination Portal

Flask portal with Neon PostgreSQL support, student name + class + exam-code access, Gemini-generated exams, PDF results and a companion MCP server.

## Render environment
Set `DATABASE_URL` to the existing Neon PostgreSQL connection string; `SECRET_KEY`; `ADMIN_USERNAME`; `ADMIN_PASSWORD`; `GEMINI_API_KEY`; optional `GEMINI_MODEL` (defaults to `gemini-2.5-flash`); and a long random `MCP_API_TOKEN`. Never commit secrets.

## Student workflow
Students enter name, Class 5–12, exam code and shared password. No student Excel upload is needed. Teacher chooses the number of exams for which a shared password can be used. Each published exam gets its own code.

## Gemini
Teacher submits instructions in the dashboard. Gemini returns structured questions and answer keys; the server validates the result and publishes automatically. The API key stays server-side.

## MCP
Run `python mcp_server.py` with `PORTAL_BASE_URL` and `MCP_API_TOKEN` in the MCP client's environment. Tools include exam creation/publishing, listing and result retrieval. Do not expose the bearer token in browser code or source control.

## Results
Results are retained unless individually deleted. Archiving closes and hides an exam while retaining its data. PDF exports contain student name, marks obtained/total and submission timestamp in Asia/Kolkata time, sorted highest first.

## Migration and caution
Upgrade migrations add nullable columns and a password quota table; they do not intentionally delete existing records. Back up Neon and test on staging before deployment. This is not a certified high-stakes exam system. Render Free may sleep; browser monitoring cannot block OS screenshots or external devices. Review subjective grading and Gemini answer keys before use.