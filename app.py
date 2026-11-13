import os, io, csv, sqlite3, secrets, re, unicodedata
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from openpyxl import load_workbook, Workbook
from dotenv import load_dotenv
from psycopg.rows import dict_row

# Load private local settings from .env; real deployment may use platform env vars.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get('DATABASE_PATH', os.path.join(BASE, 'portal.db'))
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_urlsafe(48)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', '0') == '1' or bool(DATABASE_URL), MAX_CONTENT_LENGTH=8*1024*1024)

class PostgresConnection:
    # Compatibility wrapper for the existing parameterized SQL.
    def __init__(self, connection): self.connection = connection
    def execute(self, sql, params=()): return self.connection.execute(sql.replace('?', '%s'), params)
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type: self.connection.rollback()
            else: self.connection.commit()
        finally: self.connection.close()
        return False

def db():
    if DATABASE_URL:
        import psycopg
        url = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
        if 'sslmode=' not in url: url += ('&' if '?' in url else '?') + 'sslmode=require'
        return PostgresConnection(psycopg.connect(url, row_factory=dict_row))
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con


def init_db():
    if DATABASE_URL:
        schema = '''
        CREATE TABLE IF NOT EXISTS students(id BIGSERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS exams(id BIGSERIAL PRIMARY KEY, title TEXT NOT NULL, duration INTEGER NOT NULL, review_minutes INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS questions(id BIGSERIAL PRIMARY KEY, exam_id BIGINT NOT NULL REFERENCES exams(id) ON DELETE CASCADE, q_no INTEGER NOT NULL, prompt TEXT NOT NULL, qtype TEXT NOT NULL, options TEXT, answer TEXT, marks DOUBLE PRECISION NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS attempts(id BIGSERIAL PRIMARY KEY, exam_id BIGINT NOT NULL REFERENCES exams(id), student_id BIGINT NOT NULL REFERENCES students(id), started_at TEXT NOT NULL, deadline TEXT NOT NULL, review_deadline TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'in_progress', submitted_at TEXT, UNIQUE(exam_id, student_id));
        CREATE TABLE IF NOT EXISTS responses(id BIGSERIAL PRIMARY KEY, attempt_id BIGINT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE, question_id BIGINT NOT NULL REFERENCES questions(id), answer TEXT, marks_awarded DOUBLE PRECISION, UNIQUE(attempt_id, question_id));
        CREATE TABLE IF NOT EXISTS violations(id BIGSERIAL PRIMARY KEY, attempt_id BIGINT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE, event TEXT NOT NULL, happened_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS admins(id BIGSERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL);
        '''
        with db() as c:
            for statement in schema.split(';'):
                if statement.strip(): c.execute(statement)
            admin_user, admin_pass = os.environ.get('ADMIN_USERNAME'), os.environ.get('ADMIN_PASSWORD')
            if admin_user and admin_pass and not c.execute('SELECT 1 FROM admins WHERE username=?', (admin_user,)).fetchone():
                c.execute('INSERT INTO admins(username,password_hash) VALUES(?,?)', (admin_user, generate_password_hash(admin_pass)))
    else:
        with db() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS students(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, active INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS exams(id INTEGER PRIMARY KEY, title TEXT NOT NULL, duration INTEGER NOT NULL, review_minutes INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS questions(id INTEGER PRIMARY KEY, exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE, q_no INTEGER NOT NULL, prompt TEXT NOT NULL, qtype TEXT NOT NULL, options TEXT, answer TEXT, marks REAL NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, exam_id INTEGER NOT NULL REFERENCES exams(id), student_id INTEGER NOT NULL REFERENCES students(id), started_at TEXT NOT NULL, deadline TEXT NOT NULL, review_deadline TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'in_progress', submitted_at TEXT, UNIQUE(exam_id, student_id));
            CREATE TABLE IF NOT EXISTS responses(id INTEGER PRIMARY KEY, attempt_id INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE, question_id INTEGER NOT NULL REFERENCES questions(id), answer TEXT, marks_awarded REAL, UNIQUE(attempt_id, question_id));
            CREATE TABLE IF NOT EXISTS violations(id INTEGER PRIMARY KEY, attempt_id INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE, event TEXT NOT NULL, happened_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL);
            ''')
            admin_user = os.environ.get('ADMIN_USERNAME')
            admin_pass = os.environ.get('ADMIN_PASSWORD')
            if admin_user and admin_pass and not c.execute('SELECT 1 FROM admins WHERE username=?', (admin_user,)).fetchone():
                c.execute('INSERT INTO admins(username,password_hash) VALUES(?,?)', (admin_user, generate_password_hash(admin_pass)))


def now(): return datetime.now(timezone.utc)
def iso(dt): return dt.isoformat()
def parse_dt(s): return datetime.fromisoformat(s)
def current_user(): return session.get('username')

def admin_required(fn):
    @wraps(fn)
    def wrap(*a, **kw):
        if session.get('role') != 'admin': return redirect(url_for('admin_login'))
        return fn(*a, **kw)
    return wrap

def student_required(fn):
    @wraps(fn)
    def wrap(*a, **kw):
        if session.get('role') != 'student': return redirect(url_for('student_login'))
        return fn(*a, **kw)
    return wrap

def normalize_answer(value):
    """Normalize text for forgiving, case/punctuation-insensitive answer matching."""
    value = unicodedata.normalize('NFKC', str(value or '')).casefold()
    value = re.sub(r'[^\w\s]', ' ', value, flags=re.UNICODE)
    return re.sub(r'\s+', ' ', value).strip()


def answer_matches(given, answer_key):
    """Match an expected phrase whether the student enters a phrase or a sentence."""
    response = normalize_answer(given)
    if not response:
        return False
    aliases = [normalize_answer(x) for x in str(answer_key or '').split('|')]
    for alias in filter(None, aliases):
        # Whole-phrase matching in either direction avoids rejecting a concise
        # answer when the answer key contains a longer explanatory sentence.
        if (response == alias or
                re.search(r'(?<!\\w)' + re.escape(alias) + r'(?!\\w)', response) or
                re.search(r'(?<!\\w)' + re.escape(response) + r'(?!\\w)', alias)):
            return True
    return False


def finalize(c, attempt_id, status='submitted'):
    a = c.execute('SELECT * FROM attempts WHERE id=?', (attempt_id,)).fetchone()
    if not a or a['status'] != 'in_progress': return
    qs = c.execute('SELECT * FROM questions WHERE exam_id=?', (a['exam_id'],)).fetchall()
    for q in qs:
        r = c.execute('SELECT * FROM responses WHERE attempt_id=? AND question_id=?', (attempt_id, q['id'])).fetchone()
        if not r:
            c.execute('INSERT INTO responses(attempt_id,question_id,answer,marks_awarded) VALUES(?,?,?,?)', (attempt_id,q['id'],'',0 if q['qtype'] in ('mcq','fill') else None))
        elif q['qtype'] in ('mcq','fill','text') and q['answer'] is not None and str(q['answer']).strip():
            awarded = float(q['marks']) if answer_matches(r['answer'], q['answer']) else 0
            c.execute('UPDATE responses SET marks_awarded=? WHERE id=?', (awarded,r['id']))
    c.execute('UPDATE attempts SET status=?, submitted_at=? WHERE id=?', (status, iso(now()), attempt_id))

@app.route('/')
def home():
    if session.get('role') == 'admin': return redirect(url_for('dashboard'))
    if session.get('role') == 'student': return redirect(url_for('student_home'))
    return render_template('home.html')

@app.route('/student/login', methods=['GET','POST'])
def student_login():
    if request.method == 'POST':
        u, p = request.form.get('username','').strip(), request.form.get('password','')
        with db() as c:
            s = c.execute('SELECT * FROM students WHERE username=? AND active=1', (u,)).fetchone()
            if s and check_password_hash(s['password_hash'], p):
                session.clear(); session['role']='student'; session['student_id']=s['id']; session['username']=s['username']
                return redirect(url_for('student_home'))
        flash('Incorrect username or password.')
    return render_template('login.html', kind='Student')

@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if request.method == 'POST':
        u, p = request.form.get('username','').strip(), request.form.get('password','')
        with db() as c:
            a = c.execute('SELECT * FROM admins WHERE username=?', (u,)).fetchone()
            if a and check_password_hash(a['password_hash'], p):
                session.clear(); session['role']='admin'; session['username']=u
                return redirect(url_for('dashboard'))
        flash('Incorrect admin credentials.')
    return render_template('login.html', kind='Admin')

@app.route('/logout', methods=['POST'])
def logout():
    if session.get('role') == 'student':
        with db() as c:
            a = c.execute("SELECT id FROM attempts WHERE student_id=? AND status='in_progress' ORDER BY id DESC LIMIT 1", (session['student_id'],)).fetchone()
            if a: finalize(c, a['id'], 'submitted')
    session.clear()
    return redirect(url_for('home'))

@app.route('/student')
@student_required
def student_home():
    with db() as c:
        exams = c.execute("SELECT * FROM exams WHERE status='published' ORDER BY id DESC").fetchall()
        rows=[]
        for e in exams:
            attempt=c.execute('SELECT status FROM attempts WHERE exam_id=? AND student_id=?',(e['id'],session['student_id'])).fetchone()
            rows.append({'exam':e,'attempt':attempt})
    return render_template('student.html', exams=rows)

@app.route('/exam/<int:exam_id>/start', methods=['POST'])
@student_required
def start_exam(exam_id):
    with db() as c:
        e=c.execute("SELECT * FROM exams WHERE id=? AND status='published'",(exam_id,)).fetchone()
        if not e: abort(404)
        existing=c.execute('SELECT * FROM attempts WHERE exam_id=? AND student_id=?',(exam_id,session['student_id'])).fetchone()
        if existing:
            if existing['status']=='in_progress': return redirect(url_for('take_exam',attempt_id=existing['id']))
            flash('You have already used your attempt for this examination.')
            return redirect(url_for('student_home'))
        start=now(); deadline=start+timedelta(minutes=e['duration']); review=deadline+timedelta(minutes=e['review_minutes'])
        cur=c.execute('INSERT INTO attempts(exam_id,student_id,started_at,deadline,review_deadline) VALUES(?,?,?,?,?) RETURNING id',(exam_id,session['student_id'],iso(start),iso(deadline),iso(review)))
        return redirect(url_for('take_exam',attempt_id=cur.fetchone()['id']))

@app.route('/exam/attempt/<int:attempt_id>')
@student_required
def take_exam(attempt_id):
    with db() as c:
        a=c.execute('SELECT * FROM attempts WHERE id=? AND student_id=?',(attempt_id,session['student_id'])).fetchone()
        if not a: abort(404)
        if a['status']!='in_progress': return render_template('finished.html')
        if now() >= parse_dt(a['review_deadline']):
            finalize(c,attempt_id,'auto_submitted'); return render_template('finished.html')
        qs=c.execute('SELECT id,q_no,prompt,qtype,options,marks FROM questions WHERE exam_id=? ORDER BY q_no',(a['exam_id'],)).fetchall()
        answers={r['question_id']:r['answer'] for r in c.execute('SELECT question_id,answer FROM responses WHERE attempt_id=?',(attempt_id,)).fetchall()}
        e=c.execute('SELECT * FROM exams WHERE id=?',(a['exam_id'],)).fetchone()
    return render_template('exam.html', attempt=a, questions=qs, answers=answers, exam=e)

@app.route('/api/attempt/<int:attempt_id>/answer',methods=['POST'])
@student_required
def save_answer(attempt_id):
    data=request.get_json(force=True); qid=data.get('question_id'); answer=str(data.get('answer',''))
    with db() as c:
        a=c.execute('SELECT * FROM attempts WHERE id=? AND student_id=?',(attempt_id,session['student_id'])).fetchone()
        if not a or a['status']!='in_progress': return jsonify(ok=False,error='Attempt is not active'),403
        if now() >= parse_dt(a['review_deadline']): return jsonify(ok=False,error='Examination time has ended'),403
        q=c.execute('SELECT id FROM questions WHERE id=? AND exam_id=?',(qid,a['exam_id'])).fetchone()
        if not q: return jsonify(ok=False),400
        c.execute('INSERT INTO responses(attempt_id,question_id,answer) VALUES(?,?,?) ON CONFLICT(attempt_id,question_id) DO UPDATE SET answer=excluded.answer',(attempt_id,qid,answer))
    return jsonify(ok=True)

@app.route('/api/attempt/<int:attempt_id>/event',methods=['POST'])
@student_required
def event(attempt_id):
    data=request.get_json(force=True); name=str(data.get('event','focus_lost'))[:80]
    with db() as c:
        a=c.execute('SELECT * FROM attempts WHERE id=? AND student_id=?',(attempt_id,session['student_id'])).fetchone()
        if not a or a['status']!='in_progress': return jsonify(ok=False),403
        c.execute('INSERT INTO violations(attempt_id,event,happened_at) VALUES(?,?,?)',(attempt_id,name,iso(now())))
        # A backgrounded exam tab is treated as a disqualifying violation.
        # The server, not the browser, locks and submits the attempt.
        if name in ('tab_hidden', 'fullscreen_exited'):
            finalize(c, attempt_id, 'terminated_violation')
            return jsonify(ok=True, terminated=True)
    return jsonify(ok=True, terminated=False)

@app.route('/api/attempt/<int:attempt_id>/leave', methods=['POST'])
@student_required
def leave_exam(attempt_id):
    # Called with sendBeacon during pagehide. Best effort only: browsers/network
    # are not required to deliver requests when a tab or device is terminated.
    with db() as c:
        a = c.execute('SELECT * FROM attempts WHERE id=? AND student_id=?', (attempt_id, session['student_id'])).fetchone()
        if not a: return ('', 404)
        finalize(c, attempt_id, 'submitted')
    return ('', 204)


@app.route('/exam/attempt/<int:attempt_id>/submit',methods=['POST'])
@student_required
def submit_exam(attempt_id):
    with db() as c:
        a=c.execute('SELECT * FROM attempts WHERE id=? AND student_id=?',(attempt_id,session['student_id'])).fetchone()
        if not a: abort(404)
        finalize(c,attempt_id,'submitted')
    return render_template('finished.html')

@app.route('/admin')
@admin_required
def dashboard():
    with db() as c:
        students=c.execute('SELECT COUNT(*) n FROM students').fetchone()['n']
        exams=c.execute('SELECT * FROM exams ORDER BY id DESC').fetchall()
        attempts=c.execute('SELECT COUNT(*) n FROM attempts').fetchone()['n']
    return render_template('admin.html', students=students, exams=exams, attempts=attempts)

@app.route('/admin/import-students',methods=['POST'])
@admin_required
def import_students():
    f=request.files.get('file')
    if not f: flash('Choose an Excel file.'); return redirect(url_for('dashboard'))
    try:
        ws=load_workbook(f,read_only=True,data_only=True).active
        rows=list(ws.iter_rows(values_only=True)); headers=[str(x).strip().lower() if x else '' for x in rows[0]]
        ui=headers.index('username'); pi=headers.index('password'); count=0
        with db() as c:
            for row in rows[1:]:
                if len(row)<=max(ui,pi) or not row[ui] or not row[pi]: continue
                u=str(row[ui]).strip(); p=str(row[pi]).strip()
                c.execute('INSERT INTO students(username,password_hash) VALUES(?,?) ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash,active=1',(u,generate_password_hash(p))); count+=1
        flash(f'Imported/updated {count} student accounts.')
    except Exception as ex: flash(f'Could not import file: {ex}')
    return redirect(url_for('dashboard'))

@app.route('/admin/create-exam',methods=['POST'])
@admin_required
def create_exam():
    title=request.form.get('title','').strip(); duration=int(request.form.get('duration',60)); review=int(request.form.get('review',10))
    f=request.files.get('questions')
    if not title or not f: flash('Enter an exam title and choose a question Excel file.'); return redirect(url_for('dashboard'))
    try:
        ws=load_workbook(f,read_only=True,data_only=True).active
        rows=list(ws.iter_rows(values_only=True)); headers=[str(x).strip().lower() if x else '' for x in rows[0]]
        idx={h:i for i,h in enumerate(headers)}
        required=['question','type','marks']
        if any(x not in idx for x in required): raise ValueError('Required columns: question, type, marks')
        with db() as c:
            cur=c.execute('INSERT INTO exams(title,duration,review_minutes,status,created_at) VALUES(?,?,?,\'draft\',?) RETURNING id',(title,duration,review,iso(now())))
            eid=cur.fetchone()['id']
            for n,row in enumerate(rows[1:],1):
                if not row or not row[idx['question']]: continue
                def val(k,default=''):
                    return str(row[idx[k]]).strip() if k in idx and idx[k]<len(row) and row[idx[k]] is not None else default
                typ=val('type').lower().replace(' ','_')
                if typ in ('multiple choice','multiple_choice','mcq'): typ='mcq'
                elif typ in ('fill in the blank','fill_in_the_blank','fill_blank'): typ='fill'
                elif typ in ('short answer','long answer','writing'): typ='text'
                opts='|'.join([val(k) for k in ('option_a','option_b','option_c','option_d') if val(k)])
                c.execute('INSERT INTO questions(exam_id,q_no,prompt,qtype,options,answer,marks) VALUES(?,?,?,?,?,?,?)',(eid,n,val('question'),typ,opts,val('answer') or None,float(val('marks','1'))))
        flash('Exam imported as draft. Review it, then publish it.')
    except Exception as ex: flash(f'Question import failed: {ex}')
    return redirect(url_for('dashboard'))

@app.route('/admin/exam/<int:exam_id>/publish',methods=['POST'])
@admin_required
def publish_exam(exam_id):
    with db() as c:
        n=c.execute('SELECT COUNT(*) n FROM questions WHERE exam_id=?',(exam_id,)).fetchone()['n']
        if not n: flash('Cannot publish an exam without questions.')
        else: c.execute("UPDATE exams SET status='published' WHERE id=?",(exam_id,)); flash('Examination published.')
    return redirect(url_for('dashboard'))

@app.route('/admin/exam/<int:exam_id>/close',methods=['POST'])
@admin_required
def close_exam(exam_id):
    with db() as c: c.execute("UPDATE exams SET status='closed' WHERE id=?",(exam_id,))
    flash('Examination closed.'); return redirect(url_for('dashboard'))
    
@app.route('/admin/results')
@admin_required
def results():
    with db() as c:
        rows = c.execute('''
            SELECT
                a.id AS attempt_id,
                e.title,
                s.username,
                a.status,
                a.submitted_at,
                COALESCE(
                    (SELECT SUM(r.marks_awarded)
                     FROM responses r
                     WHERE r.attempt_id = a.id), 0
                ) AS score,
                COALESCE(
                    (SELECT SUM(q.marks)
                     FROM questions q
                     WHERE q.exam_id = a.exam_id), 0
                ) AS total
            FROM attempts a
            JOIN exams e ON e.id = a.exam_id
            JOIN students s ON s.id = a.student_id
            ORDER BY a.id DESC
        ''').fetchall()

    return render_template('results.html', rows=rows)


@app.route('/admin/export.xlsx')
@admin_required
def export_results():
    
@app.route('/admin/export.xlsx')
@admin_required
def export_results():
    wb = Workbook()
    ws = wb.active
    ws.title = 'Results'

    ws.append([
        'Username', 'Examination', 'Status',
        'Obtained Marks', 'Total Marks',
        'Percentage', 'Submitted At'
    ])

    with db() as c:
        rows = c.execute('''
            SELECT
                s.username,
                e.title,
                a.status,
                a.submitted_at,
                COALESCE(
                    (SELECT SUM(r.marks_awarded)
                     FROM responses r
                     WHERE r.attempt_id = a.id), 0
                ) AS score,
                COALESCE(
                    (SELECT SUM(q.marks)
                     FROM questions q
                     WHERE q.exam_id = a.exam_id), 0
                ) AS total
            FROM attempts a
            JOIN exams e ON e.id = a.exam_id
            JOIN students s ON s.id = a.student_id
            ORDER BY s.username
        ''').fetchall()

        for r in rows:
            pct = (r['score'] / r['total'] * 100) if r['total'] else 0

            ws.append([
                r['username'],
                r['title'],
                r['status'],
                r['score'],
                r['total'],
                round(pct, 2),
                r['submitted_at']
            ])

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)

    return send_file(
        out,
        as_attachment=True,
        download_name='exam_results.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/admin/attempt/<int:attempt_id>')
@admin_required
def attempt_detail(attempt_id):
    with db() as c:
        a=c.execute('SELECT a.*,s.username,e.title FROM attempts a JOIN students s ON s.id=a.student_id JOIN exams e ON e.id=a.exam_id WHERE a.id=?',(attempt_id,)).fetchone()
        if not a: abort(404)
        qs=c.execute('SELECT q.*,q.answer AS correct_answer,r.answer AS student_answer,r.marks_awarded FROM questions q LEFT JOIN responses r ON r.question_id=q.id AND r.attempt_id=? WHERE q.exam_id=? ORDER BY q.q_no',(attempt_id,a['exam_id'])).fetchall()
        logs=c.execute('SELECT * FROM violations WHERE attempt_id=? ORDER BY id',(attempt_id,)).fetchall()
    return render_template('attempt.html', attempt=a, questions=qs, logs=logs)

@app.route('/admin/attempt/<int:attempt_id>/grade',methods=['POST'])
@admin_required
def grade_attempt(attempt_id):
    with db() as c:
        a=c.execute('SELECT * FROM attempts WHERE id=?',(attempt_id,)).fetchone()
        if not a: abort(404)
        for key,value in request.form.items():
            if key.startswith('marks_'):
                qid=int(key[6:]); q=c.execute('SELECT marks FROM questions WHERE id=? AND exam_id=?',(qid,a['exam_id'])).fetchone()
                if q:
                    try: score=max(0,min(float(value),float(q['marks'])))
                    except ValueError: continue
                    c.execute('UPDATE responses SET marks_awarded=? WHERE attempt_id=? AND question_id=?',(score,attempt_id,qid))
    flash('Marks saved.'); return redirect(url_for('attempt_detail',attempt_id=attempt_id))

@app.errorhandler(413)
def too_large(_): return 'File too large. Maximum upload size is 8 MB.',413

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
