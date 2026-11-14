from app import app, init_db
init_db()
from portal_upgrade import register
register(app)
