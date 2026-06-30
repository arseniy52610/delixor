from flask import Flask, jsonify, request
from flask_cors import CORS
import sqlite3
import os
import hmac
import hashlib
import json

app = Flask(__name__)
CORS(app, origins=["https://*.github.io"])  # Разрешить GitHub Pages

BOT_TOKEN = os.getenv('BOT_TOKEN')
DB_PATH = 'database.db'  # путь к твоей БД

def validate_telegram_data(init_data):
    """Проверка подписи Telegram initData"""
    try:
        params = dict(x.split('=') for x in init_data.split('&'))
        hash_check = params.pop('hash')
        data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(params.items()))
        secret_key = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        return calculated_hash == hash_check
    except:
        return False

def get_user_from_init(init_data):
    """Получаем user_id из initData"""
    try:
        params = dict(x.split('=') for x in init_data.split('&'))
        user = json.loads(params.get('user', '{}'))
        return user.get('id')
    except:
        return None

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ============ API ENDPOINTS ============

@app.route('/api/chats', methods=['GET'])
def get_chats():
    """Получить чаты пользователя"""
    init_data = request.headers.get('Authorization', '').replace('tma ', '')
    
    if not validate_telegram_data(init_data):
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = get_user_from_init(init_data)
    if not user_id:
        return jsonify({'error': 'No user'}), 400
    
    db = get_db()
    # ИЗОЛЯЦИЯ: только чаты этого пользователя!
    chats = db.execute(
        'SELECT * FROM chats WHERE user_id = ? ORDER BY last_message_at DESC', 
        (user_id,)
    ).fetchall()
    db.close()
    
    return jsonify([dict(row) for row in chats])

@app.route('/api/chat/<chat_id>', methods=['GET'])
def get_chat_history(chat_id):
    """Получить историю чата"""
    init_data = request.headers.get('Authorization', '').replace('tma ', '')
    
    if not validate_telegram_data(init_data):
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = get_user_from_init(init_data)
    
    db = get_db()
    # Проверяем что чат принадлежит пользователю
    chat = db.execute(
        'SELECT * FROM chats WHERE unique_chat_id = ? AND user_id = ?', 
        (chat_id, user_id)
    ).fetchone()
    
    if not chat:
        db.close()
        return jsonify({'error': 'Chat not found'}), 404
    
    messages = db.execute(
        'SELECT * FROM messages WHERE chat_id = ? ORDER BY created_at ASC',
        (chat_id,)
    ).fetchall()
    db.close()
    
    return jsonify([dict(row) for row in messages])

@app.route('/api/user', methods=['GET'])
def get_user():
    init_data = request.headers.get('Authorization', '').replace('tma ', '')
    user_id = get_user_from_init(init_data)
    return jsonify({'id': user_id, 'username': None})

@app.route('/api/settings', methods=['GET', 'POST'])
def settings():
    init_data = request.headers.get('Authorization', '').replace('tma ', '')
    user_id = get_user_from_init(init_data)
    
    if request.method == 'POST':
        data = request.json
        # Сохрани настройки в БД
        return jsonify({'status': 'ok'})
    
    return jsonify({'theme': 'dark', 'notifications': True})

# ВАЖНО: слушать на 0.0.0.0 и порту из переменной окружения!
if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
