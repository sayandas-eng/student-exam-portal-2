import os, requests
from mcp.server.fastmcp import FastMCP
mcp = FastMCP('Abhishek Santra English Guidance Exam Portal')
BASE = os.environ.get('PORTAL_BASE_URL', '').rstrip('/')
TOKEN = os.environ.get('MCP_API_TOKEN', '')
def call(method, path, **kwargs):
    if not BASE or not TOKEN:
        raise RuntimeError('Set PORTAL_BASE_URL and MCP_API_TOKEN in the MCP client environment.')
    response = requests.request(method, BASE + path, headers={'Authorization': 'Bearer ' + TOKEN}, timeout=90, **kwargs)
    response.raise_for_status()
    return response.json()
@mcp.tool()
def create_and_publish_exam(title: str, class_level: str, prompt: str, duration: int, review: int, shared_password: str, password_exam_limit: int = 1) -> dict:
    '''Ask Gemini to generate an exam and answer key, then publish it automatically.'''
    return call('POST', '/api/mcp/exams', json={'title': title, 'class_level': class_level, 'prompt': prompt, 'duration': duration, 'review': review, 'shared_password': shared_password, 'password_exam_limit': password_exam_limit})
@mcp.tool()
def list_examinations() -> dict:
    '''List examination titles, statuses and exam codes.'''
    return call('GET', '/api/mcp/exams')
@mcp.tool()
def get_examination_results() -> dict:
    '''Retrieve completed examination results, sorted by marks descending.'''
    return call('GET', '/api/mcp/results')
if __name__ == '__main__':
    mcp.run(transport='stdio')
