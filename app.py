import os
import uuid
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

DATABASE_PATH = os.getenv('DATABASE_PATH', './pomodoro.db')

def get_db():
    """获取数据库连接"""
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(error):
    """关闭数据库连接"""
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    """初始化数据库表"""
    db = sqlite3.connect(DATABASE_PATH)
    cursor = db.cursor()
    
    # 创建任务表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            parent_id TEXT,
            estimated_pomodoros INTEGER DEFAULT 1,
            completed_pomodoros INTEGER DEFAULT 0,
            status TEXT CHECK(status IN ('active', 'done', 'cancelled')) DEFAULT 'active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            completed_at DATETIME
        )
    ''')
    
    # 创建专注记录表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS focus_sessions (
            id TEXT PRIMARY KEY,
            task_id TEXT REFERENCES tasks(id),
            duration_minutes INTEGER NOT NULL,
            completed_at DATETIME NOT NULL,
            audio_preset TEXT
        )
    ''')
    
    # 创建使用统计表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usage_stats (
            id TEXT PRIMARY KEY,
            session_id TEXT UNIQUE,
            total_sessions INTEGER DEFAULT 0,
            total_minutes INTEGER DEFAULT 0,
            first_seen DATETIME,
            last_seen DATETIME
        )
    ''')
    
    db.commit()
    db.close()

# 初始化数据库
init_db()

@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    """获取所有任务"""
    db = get_db()
    cursor = db.execute('''
        SELECT * FROM tasks 
        WHERE status != 'cancelled' 
        ORDER BY created_at DESC
    ''')
    tasks = [dict(row) for row in cursor.fetchall()]
    return jsonify(tasks)

@app.route('/api/tasks', methods=['POST'])
def create_task():
    """创建新任务"""
    data = request.json
    if not data or not data.get('title'):
        return jsonify({'error': '任务标题不能为空'}), 400
    
    task_id = str(uuid.uuid4())
    db = get_db()
    
    cursor = db.execute('''
        INSERT INTO tasks (id, title, parent_id, estimated_pomodoros)
        VALUES (?, ?, ?, ?)
    ''', (task_id, data['title'], data.get('parent_id'), data.get('estimated_pomodoros', 1)))
    
    db.commit()
    
    # 返回新创建的任务
    cursor = db.execute('SELECT * FROM tasks WHERE id = ?', (task_id,))
    task = dict(cursor.fetchone())
    
    return jsonify(task), 201

@app.route('/api/tasks/<task_id>', methods=['PUT'])
def update_task(task_id):
    """更新任务"""
    data = request.json
    db = get_db()
    
    # 检查任务是否存在
    cursor = db.execute('SELECT * FROM tasks WHERE id = ?', (task_id,))
    task = cursor.fetchone()
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    
    # 更新任务字段
    update_fields = []
    values = []
    
    if 'title' in data:
        update_fields.append('title = ?')
        values.append(data['title'])
    
    if 'status' in data:
        update_fields.append('status = ?')
        values.append(data['status'])
        if data['status'] == 'done':
            update_fields.append('completed_at = ?')
            values.append(datetime.now().isoformat())
    
    if 'completed_pomodoros' in data:
        update_fields.append('completed_pomodoros = ?')
        values.append(data['completed_pomodoros'])
    
    if update_fields:
        values.append(task_id)
        db.execute(f'UPDATE tasks SET {", ".join(update_fields)} WHERE id = ?', values)
        db.commit()
    
    # 返回更新后的任务
    cursor = db.execute('SELECT * FROM tasks WHERE id = ?', (task_id,))
    updated_task = dict(cursor.fetchone())
    
    return jsonify(updated_task)

@app.route('/api/focus-sessions', methods=['POST'])
def create_focus_session():
    """记录专注会话"""
    data = request.json
    if not data or not data.get('duration_minutes'):
        return jsonify({'error': '缺少必要的会话数据'}), 400
    
    session_id = str(uuid.uuid4())
    db = get_db()
    
    db.execute('''
        INSERT INTO focus_sessions (id, task_id, duration_minutes, audio_preset)
        VALUES (?, ?, ?, ?)
    ''', (session_id, data.get('task_id'), data['duration_minutes'], data.get('audio_preset')))
    
    # 更新任务完成番茄钟数
    if data.get('task_id'):
        db.execute('''
            UPDATE tasks 
            SET completed_pomodoros = completed_pomodoros + 1
            WHERE id = ?
        ''', (data['task_id'],))
    
    # 更新使用统计
    session = request.headers.get('X-Session-ID', 'default')
    cursor = db.execute('SELECT * FROM usage_stats WHERE session_id = ?', (session,))
    stats = cursor.fetchone()
    
    if stats:
        db.execute('''
            UPDATE usage_stats 
            SET total_sessions = total_sessions + 1,
                total_minutes = total_minutes + ?,
                last_seen = ?
            WHERE session_id = ?
        ''', (data['duration_minutes'], datetime.now().isoformat(), session))
    else:
        db.execute('''
            INSERT INTO usage_stats (id, session_id, total_sessions, total_minutes, first_seen, last_seen)
            VALUES (?, ?, 1, ?, ?, ?)
        ''', (str(uuid.uuid4()), session, data['duration_minutes'], datetime.now().isoformat(), datetime.now().isoformat()))
    
    db.commit()
    
    return jsonify({'id': session_id, 'message': '专注会话已记录'}), 201

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """获取统计数据"""
    db = get_db()
    
    # 获取总专注时间
    cursor = db.execute('SELECT COALESCE(SUM(duration_minutes), 0) as total_minutes FROM focus_sessions')
    total_minutes = cursor.fetchone()['total_minutes']
    
    # 获取总会话数
    cursor = db.execute('SELECT COUNT(*) as count FROM focus_sessions')
    total_sessions = cursor.fetchone()['count']
    
    # 获取完成任务数
    cursor = db.execute('SELECT COUNT(*) as count FROM tasks WHERE status = "done"')
    completed_tasks = cursor.fetchone()['count']
    
    # 获取今日专注时间
    today = datetime.now().strftime('%Y-%m-%d')
    cursor = db.execute('''
        SELECT COALESCE(SUM(duration_minutes), 0) as today_minutes 
        FROM focus_sessions 
        WHERE date(completed_at) = ?
    ''', (today,))
    today_minutes = cursor.fetchone()['today_minutes']
    
    # 获取最近7天数据
    cursor = db.execute('''
        SELECT date(completed_at) as date, SUM(duration_minutes) as minutes
        FROM focus_sessions
        WHERE completed_at >= datetime('now', '-7 days')
        GROUP BY date(completed_at)
        ORDER BY date ASC
    ''')
    weekly_data = [dict(row) for row in cursor.fetchall()]
    
    return jsonify({
        'total_minutes': total_minutes,
        'total_sessions': total_sessions,
        'completed_tasks': completed_tasks,
        'today_minutes': today_minutes,
        'weekly_data': weekly_data
    })

@app.route('/api/ai/decompose', methods=['POST'])
def decompose_task():
    """AI任务拆解"""
    data = request.json
    if not data or not data.get('task'):
        return jsonify({'error': '请提供要拆解的任务'}), 400
    
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        # 返回示例拆解结果（无API密钥时）
        sample_tasks = [
            {'title': '任务规划与准备', 'estimated_pomodoros': 1},
            {'title': '核心工作阶段', 'estimated_pomodoros': 2},
            {'title': '检查与完善', 'estimated_pomodoros': 1}
        ]
        return jsonify({'tasks': sample_tasks})
    
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "你是一个任务管理助手。请将用户的任务拆解为可执行的子任务，每个子任务需要估计需要的番茄钟数量（每个番茄钟25分钟）。请以JSON格式返回，格式为：[{'title': '子任务名称', 'estimated_pomodoros': 数字}]"},
                {"role": "user", "content": data['task']}
            ],
            temperature=0.7,
            max_tokens=500
        )
        
        content = response.choices[0].message.content
        # 解析JSON
        import json
        tasks = json.loads(content)
        return jsonify({'tasks': tasks})
        
    except Exception as e:
        return jsonify({'error': f'AI服务暂时不可用: {str(e)}'}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
