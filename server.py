import os
import sys
from flask import Flask, jsonify, request
from flask_cors import CORS
import hmac
import hashlib
import json
from sqlmodel import Session, select

# Импортируем модели из main.py
sys.path.append(os.path.dirname(__file__))
from main import ChatMessage, Subscription, TOKEN, BOT_USERNAME

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})


def validate_telegram_data(init_data: str) -> bool:
    if not init_data:
        return False
    try:
        params = {}
        for part in init_data.split('&'):
            if '=' in part:
                k, v = part.split('=', 1)
                params[k] = v
        if 'hash' not in params:
            return False
        hash_check = params.pop('hash')
        data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(params.items()))
        secret_key = hmac.new(b'WebAppData', TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(calculated_hash, hash_check)
    except Exception:
        return False


def get_user_from_init(init_data: str) -> dict | None:
    if not init_data:
        return None
    try:
        params = {}
        for part in init_data.split('&'):
            if '=' in part:
                k, v = part.split('=', 1)
                params[k] = v
        user_json = params.get('user')
        if not user_json:
            return None
        return json.loads(user_json)
    except Exception:
        return None


def get_init_data_from_request() -> str:
    auth = request.headers.get('Authorization', '')
    if auth.startswith('tma '):
        return auth[4:]
    return request.args.get('initData', '')


@app.route('/api/health', methods=['GET'])
def api_health():
    return jsonify({'status': 'ok', 'bot': BOT_USERNAME})


@app.route('/api/chats', methods=['GET'])
def api_get_chats():
    init_data = get_init_data_from_request()
    if not validate_telegram_data(init_data):
        return jsonify({'error': 'Unauthorized'}), 401
    
    user = get_user_from_init(init_data)
    if not user:
        return jsonify({'error': 'No user'}), 400
    
    user_id = user['id']
    
    from main import db
    session = Session(db.engine)
    
    chats = session.exec(
        select(ChatMessage.unique_chat_id)
        .where(ChatMessage.unique_chat_id.like(f"{user_id}_%"))
        .distinct()
    ).all()
    
    result = []
    for unique_chat_id in chats:
        last_msg = session.exec(
            select(ChatMessage)
            .where(ChatMessage.unique_chat_id == unique_chat_id)
            .order_by(ChatMessage.created_at.desc())
        ).first()
        
        other_user_id = int(unique_chat_id.split('_', 1)[1]) if '_' in unique_chat_id else 0
        peer_name = "Неизвестный"
        
        if other_user_id and other_user_id != user_id:
            peer_msg = session.exec(
                select(ChatMessage)
                .where(ChatMessage.unique_chat_id == unique_chat_id)
                .where(ChatMessage.from_user_id == other_user_id)
                .order_by(ChatMessage.created_at.desc())
            ).first()
            if peer_msg:
                peer_name = peer_msg.from_name or peer_msg.from_username or f"ID {other_user_id}"
        
        result.append({
            'unique_chat_id': unique_chat_id,
            'peer_name': peer_name,
            'peer_user_id': other_user_id,
            'last_message': (last_msg.content or '')[:100] if last_msg else '',
            'last_message_at': last_msg.created_at.isoformat() if last_msg else None,
            'messages_count': 0,
            'unread_count': 0
        })
    
    result.sort(key=lambda x: x['last_message_at'] or '', reverse=True)
    session.close()
    return jsonify(result)


@app.route('/api/chat/<chat_id>', methods=['GET'])
def api_get_chat_history(chat_id):
    init_data = get_init_data_from_request()
    if not validate_telegram_data(init_data):
        return jsonify({'error': 'Unauthorized'}), 401
    
    user = get_user_from_init(init_data)
    if not user:
        return jsonify({'error': 'No user'}), 400
    
    user_id = user['id']
    
    if not chat_id.startswith(f"{user_id}_"):
        return jsonify({'error': 'Access denied'}), 403
    
    from main import db
    session = Session(db.engine)
    messages = session.exec(
        select(ChatMessage)
        .where(ChatMessage.unique_chat_id == chat_id)
        .order_by(ChatMessage.created_at.asc())
    ).all()
    
    result = []
    for msg in messages:
        content = msg.content or ''
        is_deleted = msg.is_deleted or '🗑️' in content
        if is_deleted:
            content = content.replace('🗑️', '').strip()
        
        result.append({
            'message_id': msg.message_id,
            'from_user_id': msg.from_user_id,
            'from_username': msg.from_username,
            'from_name': msg.from_name,
            'content': content,
            'content_type': msg.content_type or 'text',
            'file_id': msg.file_id,
            'media_uid': msg.media_uid,
            'is_deleted': is_deleted,
            'edited_at': msg.edited_at.isoformat() if msg.edited_at else None,
            'created_at': msg.created_at.isoformat() if msg.created_at else None
        })
    
    session.close()
    return jsonify(result)


@app.route('/api/user', methods=['GET'])
def api_get_user():
    init_data = get_init_data_from_request()
    if not validate_telegram_data(init_data):
        return jsonify({'error': 'Unauthorized'}), 401
    
    user = get_user_from_init(init_data)
    if not user:
        return jsonify({'error': 'No user'}), 400
    
    return jsonify({
        'id': user['id'],
        'username': user.get('username', ''),
        'first_name': user.get('first_name', ''),
        'last_name': user.get('last_name', ''),
    })


@app.route('/api/subscription', methods=['GET'])
def api_get_subscription():
    init_data = get_init_data_from_request()
    if not validate_telegram_data(init_data):
        return jsonify({'error': 'Unauthorized'}), 401
    
    user = get_user_from_init(init_data)
    if not user:
        return jsonify({'error': 'No user'}), 400
    
    from main import db
    session = Session(db.engine)
    sub = session.get(Subscription, user['id'])
    session.close()
    
    if sub and sub.active_until and sub.active_until > __import__('datetime').datetime.now():
        days_left = (sub.active_until - __import__('datetime').datetime.now()).days
        return jsonify({
            'is_active': True,
            'days_left': days_left,
            'active_until': sub.active_until.isoformat()
        })
    
    return jsonify({'is_active': False, 'days_left': 0})


@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    init_data = get_init_data_from_request()
    if not validate_telegram_data(init_data):
        return jsonify({'error': 'Unauthorized'}), 401
    
    if request.method == 'POST':
        data = request.json or {}
        return jsonify({'status': 'ok', 'settings': data})
    
    return jsonify({'theme': 'dark', 'notifications': True, 'language': 'ru'})


@app.route('/api/delpn', methods=['GET'])
def api_delpn():
    return jsonify({
        'description': 'Защищённый VPN для безопасного интернета',
        'is_connected': False,
        'status': 'Не подключено',
        'tariff': '299 руб/мес',
        'features': ['Шифрование трафика', 'Анонимность в сети', 'Обход блокировок', 'Высокая скорость'],
        'connect_url': f'https://t.me/{BOT_USERNAME}'
    })


@app.route('/api/giveaway', methods=['GET'])
def api_giveaway():
    return jsonify({
        'title': 'Розыгрыш подписки',
        'participants': 142,
        'end_date': '2026-05-25T14:00:00'
    })


if __name__ == '__main__':
    port = int(os.getenv('PORT', 3000))
    print(f" API Server running on port {port}")
    app.run(host='0.0.0.0', port=port)
