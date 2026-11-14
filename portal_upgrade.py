"""Feature layer for Abhishek Santra English Guidance portal.

Loaded by wsgi.py after the legacy schema has been initialized. Keeps existing
attempts/results intact and adds the new name/class + exam-code workflow.
"""
import os, json, secrets, re, io
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import request, session, redirect, url_for, flash, render_template, jsonify, send_file, abort
from werkzeug.security import generate_password_hash, check_password_hash

IST = ZoneInfo("Asia/Kolkata")

def register(app):
    from app import db, now, iso, admin_required, student_required

    def migrate():
        # Additive-only migrations: never drop or rewrite existing result rows.
        with db() as c:
            statements = [
                "ALTER TABLE students ADD COLUMN IF NOT EXISTS display_name TEXT",
                "ALTER TABLE students ADD COLUMN IF NOT EXISTS class_level TEXT",
                "ALTER TABLE exams ADD COLUMN IF NOT EXISTS exam_code TEXT",
                "ALTER TABLE exams ADD COLUMN IF NOT EXISTS class_level TEXT",
                "ALTER TABLE exams ADD COLUMN IF NOT EXISTS access_password_hash TEXT",
                "ALTER TABLE exams ADD COLUMN IF NOT EXISTS archived_at TEXT",
                "CREATE TABLE IF NOT EXISTS portal_access_groups (id BIGSERIAL PRIMARY KEY, password_hash TEXT NOT NULL, max_exams INTEGER NOT NULL, exams_created INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)",
            ]
            for sql in statements:
                if not os.environ.get("DATABASE_URL") and sql.startswith("CREATE TABLE IF NOT EXISTS portal_access_groups"):
                    sql = "CREATE TABLE IF NOT EXISTS portal_access_groups (id INTEGER PRIMARY KEY AUTOINCREMENT, password_hash TEXT NOT NULL, max_exams INTEGER NOT NULL, exams_created INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)"
                try: c.execute(sql)
                except Exception:
                    # SQLite does not support ADD COLUMN IF NOT EXISTS.
                    if "ADD COLUMN IF NOT EXISTS" not in sql: raise
                    col = "display_name" if "display_name" in sql else "class_level" if "class_level" in sql else "exam_code" if "exam_code" in sql else "access_password_hash" if "access_password_hash" in sql else "archived_at"
                    table = "students" if "students" in sql else "exams"
                    cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
                    if col not in cols: c.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
            try: c.execute("ALTER TABLE exams ADD COLUMN access_group_id BIGINT")
            except Exception:
                try:
                    cols=[r[1] for r in c.execute("PRAGMA table_info(exams)").fetchall()]
                    if "access_group_id" not in cols: c.execute("ALTER TABLE exams ADD COLUMN access_group_id INTEGER")
                except Exception: pass

    migrate()

    def create_student(name, class_level):
        # Legacy username remains a private unique key for FK compatibility.
        key = secrets.token_hex(12)
        with db() as c:
            cur = c.execute(
                "INSERT INTO students(username,password_hash,active,display_name,class_level) VALUES(?,?,1,?,?) RETURNING id",
                ("student_"+key, generate_password_hash(secrets.token_urlsafe(32)), name, class_level))
            return cur.fetchone()["id"]

    def student_login_v2():
        if request.method == "POST":
            name = request.form.get("name","").strip()[:100]
            grade = request.form.get("class_level","").strip()
            code = request.form.get("exam_code","").strip().upper()
            password = request.form.get("password","")
            if not name or grade not in [f"Class {i}" for i in range(5,13)] or not code or not password:
                flash("Enter your name, class, exam code and password.")
                return render_template("login.html", kind="Student")
            with db() as c:
                exam = c.execute("SELECT * FROM exams WHERE exam_code=? AND status='published' AND archived_at IS NULL",(code,)).fetchone()
                if not exam or exam["class_level"] != grade or not exam["access_password_hash"] or not check_password_hash(exam["access_password_hash"], password):
                    flash("Invalid exam code or password.")
                    return render_template("login.html", kind="Student")
                s = c.execute("SELECT * FROM students WHERE display_name=? AND class_level=? ORDER BY id LIMIT 1",(name,grade)).fetchone()
                sid = s["id"] if s else create_student(name,grade)
                session.clear(); session["role"]="student"; session["student_id"]=sid
                session["username"]=name; session["student_name"]=name; session["class_level"]=grade
                session["exam_code"]=code
                return redirect(url_for("student_home"))
        return render_template("login.html", kind="Student")
    app.view_functions["student_login"] = student_login_v2

    # Restrict a student session to the exam code they authenticated against.
    old_student_home = app.view_functions["student_home"]
    def student_home_v2():
        if not session.get("exam_code"): return redirect(url_for("student_login"))
        with db() as c:
            exam=c.execute("SELECT * FROM exams WHERE exam_code=? AND status='published' AND archived_at IS NULL",(session["exam_code"],)).fetchone()
            if not exam: return render_template("student.html", exams=[])
            attempt=c.execute("SELECT status FROM attempts WHERE exam_id=? AND student_id=?",(exam["id"],session["student_id"])).fetchone()
        return render_template("student.html", exams=[{"exam":exam,"attempt":attempt}])
    app.view_functions["student_home"] = student_home_v2

    def generate_exam():
        payload=request.get_json(silent=True) or request.form
        title=str(payload.get("title","")).strip()[:180]
        grade=str(payload.get("class_level","Class 5"))
        prompt=str(payload.get("prompt","")).strip()[:6000]
        duration=max(1,min(int(payload.get("duration",45)),240))
        review=max(0,min(int(payload.get("review",0)),60))
        password=str(payload.get("shared_password","")).strip()
        exam_limit=max(1,min(int(payload.get("password_exam_limit",1)),100))
        if not title or not prompt or not password:
            return jsonify(ok=False,error="Title, question instructions and shared password are required."),400
        api_key=os.environ.get("GEMINI_API_KEY")
        if not api_key: return jsonify(ok=False,error="GEMINI_API_KEY is not configured in Render."),503
        try:
            from google import genai
            client=genai.Client(api_key=api_key)
            instruction = (
              "Create an English examination. Return ONLY valid JSON with a questions array. "
              "Each item: question, type (mcq/fill/text), options (array, only for mcq), "
              "answer (string; for subjective questions use a concise marking key), marks (number). "
              "Ensure total marks are sensible. Do not include markdown. "
              f"Class: {grade}. Exam title: {title}. Teacher instructions: {prompt}"
            )
            response=client.models.generate_content(model=os.environ.get("GEMINI_MODEL","gemini-2.5-flash"),contents=instruction)
            raw=response.text.strip()
            raw=re.sub(r"^\x60{3}(?:json)?|\x60{3}$","",raw,flags=re.I).strip()
            data=json.loads(raw)
            questions=data.get("questions",[])
            if not questions or len(questions)>100: raise ValueError("Gemini returned no questions or too many questions.")
            for q in questions:
                if not q.get("question") or q.get("type") not in ("mcq","fill","text") or float(q.get("marks",0))<=0:
                    raise ValueError("Gemini returned an invalid question.")
            with db() as c:
                # A shared password group is reused only while its teacher-defined exam quota remains.
                groups=c.execute("SELECT * FROM portal_access_groups ORDER BY id DESC").fetchall()
                matching=next((g for g in groups if check_password_hash(g["password_hash"],password)),None)
                if matching and matching["exams_created"] >= matching["max_exams"]:
                    raise ValueError("This shared password has reached its exam limit. Choose a new password.")
                if matching:
                    gid=matching["id"]
                else:
                    cur=c.execute("INSERT INTO portal_access_groups(password_hash,max_exams,exams_created,created_at) VALUES(?,?,0,?) RETURNING id",
                                  (generate_password_hash(password),exam_limit,iso(now())))
                    gid=cur.fetchone()["id"]
                code=secrets.token_urlsafe(6).replace("-","").replace("_","").upper()[:10]
                cur=c.execute("INSERT INTO exams(title,duration,review_minutes,status,created_at,exam_code,access_password_hash,access_group_id,class_level) VALUES(?,?,?,'published',?,?,?,?,?) RETURNING id",
                              (title,duration,review,iso(now()),code,generate_password_hash(password),gid,grade))
                eid=cur.fetchone()["id"]
                for n,q in enumerate(questions,1):
                    opts="|".join(map(str,q.get("options",[]))) if q.get("options") else ""
                    c.execute("INSERT INTO questions(exam_id,q_no,prompt,qtype,options,answer,marks) VALUES(?,?,?,?,?,?,?)",
                              (eid,n,str(q["question"]),q["type"],opts,str(q.get("answer") or ""),float(q["marks"])))
                c.execute("UPDATE portal_access_groups SET exams_created=exams_created+1 WHERE id=?",(gid,))
            return jsonify(ok=True,exam_id=eid,exam_code=code,title=title,question_count=len(questions),status="published")
        except Exception as ex:
            app.logger.exception("Gemini exam creation failed")
            return jsonify(ok=False,error=str(ex)[:500]),500
    app.add_url_rule("/admin/gemini/create-exam","gemini_create_exam",admin_required(generate_exam),methods=["POST"])

    def results_v2():
        with db() as c:
            rows=c.execute("""SELECT a.id attempt_id,e.id exam_id,e.title,s.display_name student_name,s.class_level,a.status,a.submitted_at,
                COALESCE((SELECT SUM(r.marks_awarded) FROM responses r WHERE r.attempt_id=a.id),0) score,
                COALESCE((SELECT SUM(q.marks) FROM questions q WHERE q.exam_id=a.exam_id),0) total
                FROM attempts a JOIN exams e ON e.id=a.exam_id JOIN students s ON s.id=a.student_id
                WHERE e.archived_at IS NULL ORDER BY score DESC,a.submitted_at ASC""").fetchall()
            exams=c.execute("SELECT id,title FROM exams WHERE archived_at IS NULL ORDER BY id DESC").fetchall()
        return render_template("results.html",rows=rows,exams=exams)
    app.view_functions["results"] = admin_required(results_v2)

    def export_pdf():
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import mm
        exam_id=request.args.get("exam_id","").strip()
        buf=io.BytesIO()
        with db() as c:
            sql="""SELECT s.display_name student_name,
                COALESCE((SELECT SUM(r.marks_awarded) FROM responses r WHERE r.attempt_id=a.id),0) score,
                COALESCE((SELECT SUM(q.marks) FROM questions q WHERE q.exam_id=a.exam_id),0) total,
                a.submitted_at
                FROM attempts a JOIN students s ON s.id=a.student_id
                JOIN exams e ON e.id=a.exam_id WHERE e.archived_at IS NULL AND a.status<>'in_progress'"""
            params=()
            if exam_id: sql+=" AND e.id=?"; params=(int(exam_id),)
            sql+=" ORDER BY score DESC, s.display_name ASC"
            rows=c.execute(sql,params).fetchall()
            title="Examination Results"
            if exam_id:
                e=c.execute("SELECT title FROM exams WHERE id=?",(int(exam_id),)).fetchone()
                if e: title=e["title"]
        pdf=canvas.Canvas(buf,pagesize=A4); width,height=A4
        pdf.setTitle(title)
        y=height-22*mm
        pdf.setFont("Helvetica-Bold",16); pdf.drawString(18*mm,y,"Abhishek Santra English Guidance")
        y-=9*mm; pdf.setFont("Helvetica-Bold",12); pdf.drawString(18*mm,y,title)
        y-=12*mm; pdf.setFont("Helvetica-Bold",9)
        pdf.drawString(18*mm,y,"Student name"); pdf.drawString(95*mm,y,"Marks"); pdf.drawString(125*mm,y,"Date & time (IST)")
        y-=7*mm; pdf.setFont("Helvetica",9)
        for r in rows:
            if y<18*mm: pdf.showPage(); y=height-20*mm; pdf.setFont("Helvetica",9)
            dt=""
            if r["submitted_at"]:
                try: dt=datetime.fromisoformat(r["submitted_at"]).astimezone(IST).strftime("%d-%m-%Y %I:%M %p")
                except Exception: dt=str(r["submitted_at"])
            pdf.drawString(18*mm,y,str(r["student_name"] or "Student")[:38])
            pdf.drawString(95*mm,y,f'{r["score"]} / {r["total"]}')
            pdf.drawString(125*mm,y,dt)
            y-=6*mm
        pdf.save(); buf.seek(0)
        return send_file(buf,as_attachment=True,download_name="examination_results.pdf",mimetype="application/pdf")
    app.add_url_rule("/admin/results.pdf","export_results_pdf",admin_required(export_pdf),methods=["GET"])

    def archive_exam(exam_id):
        with db() as c: c.execute("UPDATE exams SET archived_at=?,status='closed' WHERE id=?",(iso(now()),exam_id))
        flash("Examination archived. Existing results have been retained.")
        return redirect(url_for("dashboard"))
    app.add_url_rule("/admin/exam/<int:exam_id>/archive","archive_exam",admin_required(archive_exam),methods=["POST"])

    def delete_result(attempt_id):
        with db() as c:
            c.execute("DELETE FROM attempts WHERE id=?",(attempt_id,))
        flash("The selected result and its answer/violation records were deleted.")
        return redirect(url_for("results"))
    app.add_url_rule("/admin/result/<int:attempt_id>/delete","delete_result",admin_required(delete_result),methods=["POST"])

    # Authenticated JSON surface for the companion MCP server.
    def mcp_auth(fn):
        def wrapped(*args,**kwargs):
            expected=os.environ.get("MCP_API_TOKEN","")
            supplied=request.headers.get("Authorization","").removeprefix("Bearer ")
            if not expected or not secrets.compare_digest(expected,supplied): return jsonify(error="Unauthorized"),401
            return fn(*args,**kwargs)
        wrapped.__name__=fn.__name__
        return wrapped
    @app.get("/api/mcp/exams")
    @mcp_auth
    def mcp_list_exams():
        with db() as c:
            rows=c.execute("SELECT id,title,status,exam_code,duration,created_at FROM exams ORDER BY id DESC").fetchall()
        return jsonify(exams=[dict(r) for r in rows])
    @app.post("/api/mcp/exams")
    @mcp_auth
    def mcp_create_exam():
        data=request.get_json(force=True)
        with app.test_request_context("/admin/gemini/create-exam",method="POST",json=data):
            # MCP calls the same validated Gemini creation workflow.
            return generate_exam()
    @app.get("/api/mcp/results")
    @mcp_auth
    def mcp_results():
        with db() as c:
            rows=c.execute("""SELECT e.title,s.display_name student_name,s.class_level,
                COALESCE((SELECT SUM(r.marks_awarded) FROM responses r WHERE r.attempt_id=a.id),0) score,
                COALESCE((SELECT SUM(q.marks) FROM questions q WHERE q.exam_id=a.exam_id),0) total,a.submitted_at
                FROM attempts a JOIN exams e ON e.id=a.exam_id JOIN students s ON s.id=a.student_id
                WHERE a.status<>'in_progress' ORDER BY score DESC""").fetchall()
        return jsonify(results=[dict(r) for r in rows])
