# Abhishek Santra English Guidance — Examination Portal

Flask portal with Neon PostgreSQL support, student name + class + exam-code access, Excel-based MCP exam intake, PDF results and a companion MCP server.

## Render environment
Set `DATABASE_URL` to the staging or production Neon PostgreSQL connection string; `SECRET_KEY`; `ADMIN_USERNAME`; `ADMIN_PASSWORD`; and a long random `MCP_API_TOKEN`. `GEMINI_API_KEY` is only needed if using the portal's legacy server-side Gemini question-generation endpoint. Never commit secrets.

## Gemini + Excel + MCP workflow
Ask Gemini to create an `.xlsx` workbook and use the MCP tool `upload_exam_excel` to send its base64-encoded file bytes to the portal. The first worksheet must contain these required headers: `question`, `type`, `marks`, `answer`. Optional MCQ columns: `option_a`, `option_b`, `option_c`, `option_d`. Supported question types: `mcq`, `fill`, `text`. The upload creates a DRAFT and never publishes automatically. The teacher signs into the portal, checks the questions, answers and marks, then selects **Approve & publish**. Only then do students receive access.

## MCP configuration
Run `python mcp_server.py` in a Gemini-compatible MCP host with `PORTAL_BASE_URL` and `MCP_API_TOKEN` configured privately in that host. The token must match the Render environment variable. The host must be able to provide the generated workbook as base64-encoded bytes to the `upload_exam_excel` tool. Never expose the bearer token in browser code or source control.

## Student workflow
Students enter their name, Class 5–12, exam code and shared password. One attempt per student profile and exam is enforced by the existing attempt flow.

## Results
Results are retained unless individually deleted. Archiving closes and hides an exam while retaining its data. PDF exports contain student name, marks obtained/total and submission timestamp in Asia/Kolkata time, sorted highest first.

## Migration and caution
Upgrade migrations are additive and do not intentionally delete existing records. Test on the separate Neon staging branch before production deployment. This is not a certified high-stakes exam system. Render Free may sleep; browser monitoring cannot block OS screenshots or external devices. Review subjective grading and Gemini answer keys before use.
