import os, requests
from mcp.server.fastmcp import FastMCP

mcp = FastMCP('Abhishek Santra English Guidance Exam Portal')
BASE = os.environ.get('PORTAL_BASE_URL', '').rstrip('/')
TOKEN = os.environ.get('MCP_API_TOKEN', '')

def call(method, path, **kwargs):
    if not BASE or not TOKEN:
        raise RuntimeError('Set PORTAL_BASE_URL and MCP_API_TOKEN in the MCP client environment.')
    response = requests.request(method, BASE + path, headers={'Authorization': 'Bearer ' + TOKEN}, timeout=120, **kwargs)
    response.raise_for_status()
    return response.json()

@mcp.tool()
def upload_exam_excel(title: str, class_level: str, workbook_base64: str, shared_password: str,
                      duration: int = 45, review: int = 0, password_exam_limit: int = 1,
                      file_name: str = "exam.xlsx") -> dict:
    """Upload a Gemini-created .xlsx examination workbook to the portal as a DRAFT.
    workbook_base64 must be the complete base64-encoded bytes of the .xlsx file.
    Required Excel headers: question, type, marks, answer.
    Optional MCQ headers: option_a, option_b, option_c, option_d.
    Supported types: mcq, fill, text. The teacher must review and publish it in the portal."""
    return call('POST', '/api/mcp/exams/upload', json={
        'title': title, 'class_level': class_level, 'workbook_base64': workbook_base64,
        'shared_password': shared_password, 'duration': duration, 'review': review,
        'password_exam_limit': password_exam_limit, 'file_name': file_name
    })

@mcp.tool()
def create_exam_draft(title: str, class_level: str, prompt: str, duration: int, review: int,
                      shared_password: str, password_exam_limit: int = 1) -> dict:
    """Legacy JSON question generation route. Creates a DRAFT for teacher approval, never auto-publishes."""
    return call('POST', '/api/mcp/exams', json={
        'title': title, 'class_level': class_level, 'prompt': prompt, 'duration': duration,
        'review': review, 'shared_password': shared_password, 'password_exam_limit': password_exam_limit
    })

@mcp.tool()
def list_examinations() -> dict:
    """List examination titles, statuses and exam codes."""
    return call('GET', '/api/mcp/exams')

@mcp.tool()
def get_examination_results() -> dict:
    """Retrieve completed examination results, sorted by marks descending."""
    return call('GET', '/api/mcp/results')

if __name__ == '__main__':
    mcp.run(transport='stdio')
