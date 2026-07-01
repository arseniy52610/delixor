import asyncio
import logging
import threading
import os
import hmac
import hashlib
import json
from datetime import datetime, timedelta
from urllib.parse import urlencode
from uuid import uuid4

from aiogram import Bot, Dispatcher, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BusinessConnection,
    BusinessMessagesDeleted,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message as MessageType,
    WebAppInfo,
)
from babel.dates import format_date
from sqlmodel import SQLModel, Field, Session as SQLSession, select
from flask import Flask, jsonify, request
from flask_cors import CORS

import db
from platega import PlategaPayment

TOKEN = "8016703176:AAHeEpjl5UJp_Meg0H6OkZ44HEx3-WU4SGI"
BOT_USERNAME = "DelixorBot"

# Конфигурация Platega
PLATEGA_MERCHANT_ID = "555856bc-3f51-4859-b8be-83bcae5093e7"  # Замените на ваш Merchant ID
PLATEGA_SECRET_KEY = "giOFXHhoXcILMALHJTiV0DBAbC3uVDv9dmSglC61IdYmRaB7PO5tuKlXpTnubUUATq9nDiBRz01EVMkkZ0hebS4x9G3x9G6SDiu9"    # Замените на ваш Secret Key
PLATEGA_CALLBACK_URL = "https://bot-1782782304-4136-bynexadmin.bothost.tech/api/platega/callback"

platega = PlategaPayment(PLATEGA_MERCHANT_ID, PLATEGA_SECRET_KEY)

# Тарифы подписки
SUBSCRIPTION_PLANS = {
    "month": {"amount": 199, "days": 30, "title": "Месяц"},
    "quarter": {"amount": 399, "days": 90, "title": "3 месяца"},
    "year": {"amount": 599, "days": 365, "title": "Год"}
}

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

ADMINS = [1947766225]

# ============ Flask API ============
api_app = Flask(__name__)
CORS(api_app, resources={r"/api/*": {"origins": "*"}})


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


@api_app.route('/api/health', methods=['GET'])
def api_health():
    return jsonify({'status': 'ok', 'bot': BOT_USERNAME})


@api_app.route('/api/chats', methods=['GET'])
def api_get_chats():
    init_data = get_init_data_from_request()
    if not validate_telegram_data(init_data):
        return jsonify({'error': 'Unauthorized'}), 401
    
    user = get_user_from_init(init_data)
    if not user:
        return jsonify({'error': 'No user'}), 400
    
    user_id = user['id']
    session = SQLSession(db.engine)
    
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


@api_app.route('/api/chat/<chat_id>', methods=['GET'])
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
    
    session = SQLSession(db.engine)
    messages = session.exec(
        select(ChatMessage)
        .where(ChatMessage.unique_chat_id == chat_id)
        .order_by(ChatMessage.created_at.asc())
    ).all()
    
    result = []
    for msg in messages:
        content = msg.content or ''
        is_deleted = msg.is_deleted or '️' in content
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


@api_app.route('/api/user', methods=['GET'])
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


@api_app.route('/api/subscription', methods=['GET'])
def api_get_subscription():
    init_data = get_init_data_from_request()
    if not validate_telegram_data(init_data):
        return jsonify({'error': 'Unauthorized'}), 401
    
    user = get_user_from_init(init_data)
    if not user:
        return jsonify({'error': 'No user'}), 400
    
    session = SQLSession(db.engine)
    sub = session.get(Subscription, user['id'])
    session.close()
    
    if sub and sub.active_until and sub.active_until > datetime.now():
        days_left = (sub.active_until - datetime.now()).days
        return jsonify({
            'is_active': True,
            'days_left': days_left,
            'active_until': sub.active_until.isoformat()
        })
    
    return jsonify({'is_active': False, 'days_left': 0})


@api_app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    init_data = get_init_data_from_request()
    if not validate_telegram_data(init_data):
        return jsonify({'error': 'Unauthorized'}), 401
    
    if request.method == 'POST':
        data = request.json or {}
        return jsonify({'status': 'ok', 'settings': data})
    
    return jsonify({'theme': 'dark', 'notifications': True, 'language': 'ru'})


@api_app.route('/api/delpn', methods=['GET'])
def api_delpn():
    return jsonify({
        'description': 'Защищённый VPN для безопасного интернета',
        'is_connected': False,
        'status': 'Не подключено',
        'tariff': '299 руб/мес',
        'features': ['Шифрование трафика', 'Анонимность в сети', 'Обход блокировок', 'Высокая скорость'],
        'connect_url': f'https://t.me/{BOT_USERNAME}'
    })


@api_app.route('/api/giveaway', methods=['GET'])
def api_giveaway():
    return jsonify({
        'title': 'Розыгрыш подписки',
        'participants': 142,
        'end_date': '2026-05-25T14:00:00'
    })


# ============ НОВЫЕ API ENDPOINTS ДЛЯ ПЛАТЕЖЕЙ ============

@api_app.route('/api/payment/create', methods=['POST'])
def api_create_payment():
    """Создание платежа через Platega.io"""
    init_data = get_init_data_from_request()
    if not validate_telegram_data(init_data):
        return jsonify({'error': 'Unauthorized'}), 401
    
    user = get_user_from_init(init_data)
    if not user:
        return jsonify({'error': 'No user'}), 400
    
    data = request.json or {}
    plan = data.get('plan')
    
    if plan not in SUBSCRIPTION_PLANS:
        return jsonify({'error': 'Invalid plan'}), 400
    
    user_id = user['id']
    session = SQLSession(db.engine)
    
    # Проверяем активную подписку
    if is_user_active(session, user_id):
        sub = session.get(Subscription, user_id)
        session.close()
        return jsonify({
            'error': 'Already has active subscription',
            'active_until': sub.active_until.isoformat()
        }), 400
    
    session.close()
    
    # Создаем платеж
    plan_info = SUBSCRIPTION_PLANS[plan]
    order_id = f"delixor_{user_id}_{plan}_{int(datetime.now().timestamp())}"
    
    result = platega.create_payment(
        user_id=user_id,
        amount=plan_info["amount"],
        plan=plan_info["title"],
        order_id=order_id,
        callback_url=PLATEGA_CALLBACK_URL
    )
    
    if result and result["success"]:
        return jsonify({
            'success': True,
            'payment_url': result["payment_url"],
            'order_id': result["order_id"]
        })
    else:
        return jsonify({'error': 'Failed to create payment'}), 500


@api_app.route('/api/platega/callback', methods=['POST'])
def platega_callback():
    """Webhook для обработки callback от Platega.io"""
    data = request.json or {}
    
    logging.info(f"Platega callback received: {data}")
    
    # Проверяем подлинность
    if not platega.verify_callback(data):
        return jsonify({'error': 'Invalid callback'}), 400
    
    order_id = data.get('order_id')
    status = data.get('status')
    
    if not order_id or not status:
        return jsonify({'error': 'Missing fields'}), 400
    
    # Парсим order_id: delixor_{user_id}_{plan}_{timestamp}
    parts = order_id.split('_')
    if len(parts) < 3:
        return jsonify({'error': 'Invalid order_id'}), 400
    
    try:
        user_id = int(parts[1])
        plan = parts[2]
    except (ValueError, IndexError):
        return jsonify({'error': 'Invalid order_id format'}), 400
    
    # Обрабатываем успешную оплату
    if status in ['paid', 'success', 'completed']:
        session = SQLSession(db.engine)
        plan_info = SUBSCRIPTION_PLANS.get(plan)
        
        if plan_info:
            sub = session.get(Subscription, user_id)
            if not sub:
                sub = Subscription(user_id=user_id)
            
            # Устанавливаем дату окончания подписки
            if sub.active_until and sub.active_until > datetime.now():
                # Если уже есть активная подписка, продлеваем
                sub.active_until += timedelta(days=plan_info["days"])
            else:
                # Новая подписка
                sub.active_until = datetime.now() + timedelta(days=plan_info["days"])
            
            sub.last_charge_id = order_id
            session.add(sub)
            session.commit()
            
            # Отправляем уведомление пользователю
            try:
                asyncio.run_coroutine_threadsafe(
                    bot.send_message(
                        chat_id=user_id,
                        text=f"✅ <b>Подписка Delixor Plus активирована!</b>\n\n"
                             f"📅 Действует до: {format_date(sub.active_until, 'd MMMM yyyy', locale='ru')}\n"
                             f"💎 Теперь вам доступны все функции бота!",
                        parse_mode="HTML"
                    ),
                    loop=asyncio.get_event_loop()
                )
            except Exception as e:
                logging.error(f"Failed to send notification: {e}")
        
        session.close()
    
    return jsonify({'status': 'ok'})


# ============ Модели БД ============

class Subscription(SQLModel, table=True):
    user_id: int = Field(primary_key=True)
    active_until: datetime | None = None
    last_charge_id: str | None = None


class BusinessStatus(SQLModel, table=True):
    user_id: int = Field(primary_key=True)
    is_connected: bool = False
    updated_at: datetime = Field(default_factory=datetime.now)


class MenuState(SQLModel, table=True):
    user_id: int = Field(primary_key=True)
    chat_id: int
    message_id: int
    updated_at: datetime = Field(default_factory=datetime.now)


class ChatMessage(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    unique_chat_id: str
    message_id: int
    from_user_id: int
    from_username: str
    from_name: str
    content: str
    content_type: str | None = None
    file_id: str | None = None
    caption: str | None = None
    media_uid: str | None = Field(default=None, index=True, unique=True)
    is_deleted: bool = False
    edited_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.now)


# ============ Утилиты бота ============

def is_user_active(session: SQLSession, user_id: int) -> bool:
    sub = session.get(Subscription, user_id)
    return bool(sub and sub.active_until and sub.active_until > datetime.now())


def build_webapp_url(session: SQLSession, user) -> str:
    base_url = "https://arseniy52610.github.io/DelixorMiniApp/"
    user_id = user.id
    status = session.get(BusinessStatus, user_id)

    user_messages = session.exec(
        select(ChatMessage).where(ChatMessage.unique_chat_id.like(f"{user_id}_%"))
    ).all()

    params = {
        "id": user_id,
        "username": user.username or "",
        "name": user.full_name or "",
        "avatar": getattr(user, "photo_url", "") or "",
        "bot_username": BOT_USERNAME,
        "close_on_pay": "1",
        "connected": "1" if status and status.is_connected else "0",
        "deleted": sum(1 for m in user_messages if m.is_deleted),
        "edited": sum(1 for m in user_messages if m.edited_at is not None),
        "incoming": sum(1 for m in user_messages if m.from_user_id != user_id),
        "outgoing": sum(1 for m in user_messages if m.from_user_id == user_id),
    }
    return f"{base_url}?{urlencode(params)}"


def start_keyboard(webapp_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=" DelixorMod", icon_custom_emoji_id="5334821222444212525", style="primary",
                    web_app=WebAppInfo(url=webapp_url),
                ),
                InlineKeyboardButton(text=" Ваши чаты", icon_custom_emoji_id="5264976953902900857", callback_data="all_chats")],
            [
                InlineKeyboardButton(text=" Наш канал", url="https://t.me/delixornews", icon_custom_emoji_id="5452002597592382164"),
            ],
            [InlineKeyboardButton(text="ℹ️ Информация", callback_data="info")],
        ]
    )


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="back")]]
    )


def store_menu_state(session: SQLSession, user_id: int, chat_id: int, message_id: int) -> None:
    state = session.get(MenuState, user_id) or MenuState(
        user_id=user_id,
        chat_id=chat_id,
        message_id=message_id,
    )
    state.chat_id = chat_id
    state.message_id = message_id
    state.updated_at = datetime.now()
    session.add(state)
    session.commit()


async def refresh_menu_link(bot: Bot, session: SQLSession, user_id: int) -> None:
    state = session.get(MenuState, user_id)
    if not state:
        return

    try:
        user = await bot.get_chat(user_id)
    except Exception:
        return

    webapp_url = build_webapp_url(session, user)
    try:
        await bot.edit_message_reply_markup(
            chat_id=state.chat_id,
            message_id=state.message_id,
            reply_markup=start_keyboard(webapp_url),
        )
    except Exception:
        return


async def periodic_refresh_menu_links(interval_seconds: int = 60) -> None:
    while True:
        session = SQLSession(db.engine)
        states = session.exec(select(MenuState)).all()
        for state in states:
            await refresh_menu_link(bot, session, state.user_id)
        await asyncio.sleep(interval_seconds)


def build_media_caption(msg: ChatMessage) -> str:
    sender = f"@{msg.from_username}" if msg.from_username else msg.from_name
    if msg.caption:
        return f"Отправил: {sender}\n{msg.caption}"
    return f"Отправил: {sender}"


async def send_saved_media_by_uid(message: MessageType, media_uid: str) -> None:
    session = SQLSession(db.engine)
    msg = session.exec(select(ChatMessage).where(ChatMessage.media_uid == media_uid)).first()

    if not msg or not msg.file_id or not msg.content_type:
        await message.answer("⚠️ Медиа не найдено или уже удалено.")
        return

    media_caption = build_media_caption(msg)
    if msg.content_type == "photo":
        await message.answer_photo(photo=msg.file_id, caption=media_caption)
    elif msg.content_type == "video":
        await message.answer_video(video=msg.file_id, caption=media_caption)
    elif msg.content_type == "video_note":
        await message.answer_video_note(video_note=msg.file_id)
        await message.answer(media_caption)
    elif msg.content_type == "document":
        await message.answer_document(document=msg.file_id, caption=media_caption)
    elif msg.content_type == "audio":
        await message.answer_audio(audio=msg.file_id, caption=media_caption)
    elif msg.content_type == "voice":
        await message.answer_voice(voice=msg.file_id, caption=media_caption)
    elif msg.content_type == "animation":
        await message.answer_animation(animation=msg.file_id, caption=media_caption)
    else:
        await message.answer("⚠️ Этот тип медиа пока не поддерживается.")


@dp.message(CommandStart())
async def cmd_start(message: MessageType):
    args = (message.text or "").split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("media_"):
        media_uid = args[1].replace("media_", "", 1).strip()
        if media_uid:
            await send_saved_media_by_uid(message, media_uid)
            try:
                await message.delete()
            except Exception:
                pass
            return

    session = SQLSession(db.engine)
    webapp_url = build_webapp_url(session, message.from_user)

    sent = await message.answer(
    f'<tg-emoji emoji-id="5469986291380657759">✌️</tg-emoji> '
    f'Привет, {html.bold(message.from_user.full_name)}!\n\n'
    'Delixor сохраняет удалённые и изменённые сообщения в чатах. '
    'Ничего лишнего — только контроль и прозрачность',
    parse_mode="HTML",
    reply_markup=start_keyboard(webapp_url),
)
    store_menu_state(session, message.from_user.id, sent.chat.id, sent.message_id)


def get_interlocutor_name(session: SQLSession, unique_chat_id: str, owner_id: int) -> str:
    try:
        other_user_id = int(unique_chat_id.split("_", 1)[1])
    except (IndexError, ValueError):
        return "Неизвестный"

    if other_user_id == owner_id:
        return "Неизвестный"

    stored_message = session.exec(
        select(ChatMessage)
        .where(ChatMessage.unique_chat_id == unique_chat_id)
        .where(ChatMessage.from_user_id == other_user_id)
        .order_by(ChatMessage.created_at.desc())
    ).first()

    if stored_message:
        return stored_message.from_name

    return f"ID {other_user_id}"


async def render_all_chats(callback: CallbackQuery, session: SQLSession) -> None:
    user_id = callback.from_user.id
    
    # Проверяем подписку
    if not is_user_active(session, user_id):
        await callback.message.edit_text(
            "🔒 <b>Доступ ограничен</b>\n\n"
            "Этот раздел доступен только пользователям с активной подпиской Delixor Plus.\n\n"
            "💎 Оформите подписку через Mini App для доступа ко всем функциям.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Оформить подписку", web_app=WebAppInfo(url=build_webapp_url(session, callback.from_user)))],
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")]
                ]
            )
        )
        return
    
    chats = session.exec(
        select(ChatMessage.unique_chat_id)
        .where(ChatMessage.unique_chat_id.like(f"{user_id}_%"))
        .distinct()
    ).all()

    if not chats:
        await callback.message.edit_text("💬 Нет сохраненных чатов.", reply_markup=back_keyboard())
        return

    owner_name = callback.from_user.full_name
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{owner_name} ↔ {get_interlocutor_name(session, chat, user_id)}",
                    callback_data=f"open_chat_{chat}",
                )
            ]
            for chat in chats
        ]
        + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="back")]]
    )
    await callback.message.edit_text("💬 Ваши чаты:", reply_markup=keyboard)


@dp.callback_query(lambda c: c.data=="info")
async def cb_info(callback: CallbackQuery):
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Политика конфиденциальности", url="https://telegra.ph/Politika-konfidencialnosti-06-28-42")],
        [InlineKeyboardButton(text="Пользовательское соглашение", url="https://telegra.ph/Polzovatelskoe-soglashenie-06-28-25")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")]
    ])
    await callback.message.edit_text("ℹ️ <b>Информация</b>\n\nПоддержка: @DelixorSupport\n\nВыберите нужный документ:", reply_markup=kb)

@dp.callback_query(lambda c: c.data == "profile")
async def cb_profile(callback: CallbackQuery):
    session = SQLSession(db.engine)
    user_id = callback.from_user.id
    user = callback.from_user
    sub = session.get(Subscription, user_id)

    text = f"<b>👤 Профиль</b>\n\n<b>🧑‍💻Имя:</b> {user.full_name}\n<b>🆔ID:</b> {user.id}\n"

    if sub and sub.active_until and sub.active_until > datetime.now():
        until = format_date(sub.active_until, "d MMMM yyyy", locale="ru")
        text += f"<b>✅Подписка активна до:</b> {until}"
    else:
        text += "<b>Подписка:</b> ❌ не активна"

    await callback.message.edit_text(text, reply_markup=back_keyboard())


@dp.message(Command("gift"))
async def cmd_gift(message: MessageType):
    if message.from_user.id not in ADMINS:
        return await message.answer("⚠️ Эта команда доступна только админам!")

    args = message.text.split()
    if len(args) != 2:
        return await message.answer("Использование: /gift <user_id>")

    try:
        user_id = int(args[1])
    except ValueError:
        return await message.answer("⚠️ Некорректный ID пользователя!")

    session = SQLSession(db.engine)
    active_until = datetime.now() + timedelta(days=30)

    sub = session.get(Subscription, user_id)
    if not sub:
        sub = Subscription(user_id=user_id)
    sub.active_until = active_until
    session.add(sub)
    session.commit()

    try:
        await message.bot.send_message(
            chat_id=user_id,
            text=f"🎁 Вам подарили подписку на DelixorBOT!\n✅ Подписка активна до {format_date(active_until, 'd MMMM yyyy', locale='ru')}",
        )
    except Exception:
        pass

    await message.answer(
        f"✅ Подписка успешно подарена пользователю {user_id} до {format_date(active_until, 'd MMMM yyyy', locale='ru')}"
    )


@dp.message(Command("dump_db"))
async def cmd_dump_db(message: MessageType):
    if message.from_user.id not in ADMINS:
        return await message.answer("⚠️ Эта команда доступна только админам!")

    db_path = getattr(db.engine.url, "database", None)
    if not db_path:
        return await message.answer("️ Для удалённой БД выгрузка файлом недоступна.")

    if not db_path.endswith(".db"):
        return await message.answer("⚠️ Поддерживается только SQLite база данных.")

    try:
        await message.bot.send_document(
            chat_id=message.chat.id,
            document=FSInputFile(db_path),
            caption="📦 Текущая база данных",
        )
    except Exception:
        await message.answer("⚠️ Не удалось отправить файл базы данных.")


@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection):
    user_chat_id = connection.user_chat_id
    session = SQLSession(db.engine)
    status = session.get(BusinessStatus, user_chat_id) or BusinessStatus(user_id=user_chat_id)
    status.is_connected = bool(connection.is_enabled)
    status.updated_at = datetime.now()
    session.add(status)
    session.commit()

    if connection.is_enabled:
        await connection.bot.send_message(
            chat_id=user_chat_id,
            text="✅ <b>Бот успешно подключен!</b>\n\nТеперь я буду сохранять и отслеживать сообщения ✨",
        )
    else:
        await connection.bot.send_message(chat_id=user_chat_id, text="Будем вас ждать снова 💖")

@dp.callback_query()
async def cb_handler(callback: CallbackQuery):
    session = SQLSession(db.engine)
    if callback.data == "help":
        await callback.message.edit_text(
            "<b>💫 Для подключения Delixor выполните следующие шаги:</b>\n\n"
            "▶ Откройте настройки Telegram\n"
            "▶ Перейдите в раздел «Telegram для Бизнеса»\n"
            f"▶ Выберите «Чат-боты» и найдите {BOT_USERNAME}\n\n"
            "<blockquote>💻 В разрешениях для бота выберите все пункты раздела Сообщения (5/5)</blockquote>\n"
            "<blockquote>⚠️ Для подключения нашего мода требуется Telegram Premium</blockquote>",
            reply_markup=back_keyboard(),
        )
    elif callback.data == "noop":
        await callback.answer()
    elif callback.data == "back_to_chats":
        await render_all_chats(callback, session)
    elif callback.data == "back":
        webapp_url = build_webapp_url(session, callback.from_user)
        await callback.message.edit_text(
            f"👋 Привет, {html.bold(callback.from_user.full_name)}!\n\n"
            "Delixor сохраняет удалённые и изменённые сообщения в чатах. Ничего лишнего — только контроль и прозрачность",
            reply_markup=start_keyboard(webapp_url),
        )
        store_menu_state(session, callback.from_user.id, callback.message.chat.id, callback.message.message_id)
    elif callback.data == "all_chats":
        await render_all_chats(callback, session)

    elif callback.data.startswith("open_chat_"):
        payload = callback.data[len("open_chat_") :]
        if "_page_" in payload:
            unique_chat_id, page_str = payload.rsplit("_page_", 1)
            try:
                page = max(int(page_str), 1)
            except ValueError:
                page = 1
        else:
            unique_chat_id = payload
            page = 1
        messages = session.exec(
            select(ChatMessage)
            .where(ChatMessage.unique_chat_id == unique_chat_id)
            .order_by(ChatMessage.created_at)
        ).all()

        if not messages:
            await callback.message.edit_text(
                "💬 Сообщения в этом чате отсутствуют.", reply_markup=back_keyboard()
            )
            return

        owner_name = callback.from_user.full_name
        interlocutor_name = get_interlocutor_name(session, unique_chat_id, callback.from_user.id)
        per_page = 20
        start = (page - 1) * per_page
        end = start + per_page
        page_messages = messages[start:end]
        total_pages = max((len(messages) + per_page - 1) // per_page, 1)
        text = f"<b>💬 Чат: {owner_name} ↔ {interlocutor_name}</b>\n\n"

        media_type_labels = {
            "photo": "[Фото]",
            "video": "[Видео]",
            "video_note": "[Кружок]",
            "document": "[Файл]",
            "audio": "[Аудио]",
            "voice": "[Голосовое]",
            "animation": "[GIF]",
        }
        for msg in page_messages:
            deleted_flag = msg.is_deleted or "🗑️" in msg.content or msg.content.startswith("Само сообщение")
            content = msg.content.replace("🗑️", "").strip()
            display_name = (msg.from_username or msg.from_name).strip()

            if msg.file_id and msg.content_type and msg.media_uid:
                media_label = media_type_labels.get(msg.content_type, "[Медиа]")
                if deleted_flag:
                    media_label = f" {media_label}"
                text += f"<b>@{display_name}:</b> "
                text += (
                    f"<a href=\"https://t.me/{BOT_USERNAME}?start=media_{msg.media_uid}\">"
                    f"{media_label}</a>\n\n"
                )
                continue

            if deleted_flag:
                content = f"❌{content}"
            text += f"<b>@{display_name}:</b> {content}\n\n"

        nav_buttons = []
        if page > 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="⬅️ Предыдущая",
                    callback_data=f"open_chat_{unique_chat_id}_page_{page - 1}",
                )
            )
        nav_buttons.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="➡️ Следующая",
                    callback_data=f"open_chat_{unique_chat_id}_page_{page + 1}",
                )
            )

        keyboard_rows = []
        if nav_buttons:
            keyboard_rows.append(nav_buttons)
        keyboard_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_chats")])
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
        )

    elif callback.data.startswith("media_"):
        msg_id_str = callback.data[len("media_") :]
        if not msg_id_str.isdigit():
            await callback.answer("Некорректный идентификатор медиа", show_alert=True)
            return

        msg = session.get(ChatMessage, int(msg_id_str))
        if not msg or not msg.file_id or not msg.content_type:
            await callback.answer("Медиа не найдено", show_alert=True)
            return

        await callback.answer("Отправляю медиа…")
        media_caption = build_media_caption(msg)
        if msg.content_type == "photo":
            await callback.message.answer_photo(photo=msg.file_id, caption=media_caption)
        elif msg.content_type == "video":
            await callback.message.answer_video(video=msg.file_id, caption=media_caption)
        elif msg.content_type == "video_note":
            await callback.message.answer_video_note(video_note=msg.file_id)
            await callback.message.answer(media_caption)
        elif msg.content_type == "document":
            await callback.message.answer_document(document=msg.file_id, caption=media_caption)
        elif msg.content_type == "audio":
            await callback.message.answer_audio(audio=msg.file_id, caption=media_caption)
        elif msg.content_type == "voice":
            await callback.message.answer_voice(voice=msg.file_id, caption=media_caption)
        elif msg.content_type == "animation":
            await callback.message.answer_animation(animation=msg.file_id, caption=media_caption)
        else:
            await callback.answer("Тип медиа пока не поддерживается", show_alert=True)


@dp.business_message()
async def save_business(message: MessageType):
    session = SQLSession(db.engine)
    bc = await message.bot.get_business_connection(message.business_connection_id)

    if message.from_user.id == bc.user_chat_id:
        other_user_id = message.chat.id
    else:
        other_user_id = message.from_user.id

    unique_chat_id = f"{bc.user_chat_id}_{other_user_id}"

    if not is_user_active(session, bc.user_chat_id):
        await message.bot.send_message(
            chat_id=bc.user_chat_id,
            text="⚠️ У вас нет активной подписки! Оформите Delixor Plus через Mini App 💎",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="💳 Оформить подписку", web_app=WebAppInfo(url=build_webapp_url(session, message.from_user)))]]
            ),
        )
        return

    content_type = None
    file_id = None
    caption = message.caption or ""
    content = message.text or ""

    if message.photo:
        content_type = "photo"
        file_id = message.photo[-1].file_id
        content = caption or "[Фото]"
    elif message.video:
        content_type = "video"
        file_id = message.video.file_id
        content = caption or "[Видео]"
    elif message.video_note:
        content_type = "video_note"
        file_id = message.video_note.file_id
        content = "[Кружок]"
    elif message.document:
        content_type = "document"
        file_id = message.document.file_id
        content = caption or f"[Файл] {message.document.file_name or ''}".strip()
    elif message.audio:
        content_type = "audio"
        file_id = message.audio.file_id
        content = caption or f"[Аудио] {message.audio.title or ''}".strip()
    elif message.voice:
        content_type = "voice"
        file_id = message.voice.file_id
        content = caption or "[Голосовое]"
    elif message.animation:
        content_type = "animation"
        file_id = message.animation.file_id
        content = caption or "[GIF]"

    if message.text or file_id:
        media_uid = uuid4().hex if file_id else None
        session.add(
            ChatMessage(
                unique_chat_id=unique_chat_id,
                message_id=message.message_id,
                from_user_id=message.from_user.id,
                from_username=message.from_user.username or "",
                from_name=message.from_user.full_name,
                content=content,
                content_type=content_type or "text",
                file_id=file_id,
                caption=caption or None,
                media_uid=media_uid,
            )
        )
        session.commit()


@dp.edited_business_message()
async def handle_edited_business_message(message: MessageType):
    session = SQLSession(db.engine)
    bc = await message.bot.get_business_connection(message.business_connection_id)

    if message.from_user.id == bc.user_chat_id:
        other_user_id = message.chat.id
    else:
        other_user_id = message.from_user.id

    unique_chat_id = f"{bc.user_chat_id}_{other_user_id}"
    stored_message = session.exec(
        select(ChatMessage)
        .where(ChatMessage.unique_chat_id == unique_chat_id)
        .where(ChatMessage.message_id == message.message_id)
    ).first()

    if stored_message and message.text:
        old_content = stored_message.content
        stored_message.content = message.text
        stored_message.edited_at = datetime.now()
        session.add(stored_message)
        session.commit()

        username = message.from_user.username or message.from_user.full_name
        await message.bot.send_message(
            chat_id=bc.user_chat_id,
            text=(
                f'<tg-emoji emoji-id="5334952532479351827"></tg-emoji> '
                f" <b>@{username} изменил(а) сообщение</b>\n"
                f"<blockquote>💬{old_content} ➜ {message.text}</blockquote>"
            ),
        )


@dp.deleted_business_messages()
async def handle_deleted_business_messages(deleted: BusinessMessagesDeleted):
    session = SQLSession(db.engine)
    bc = await deleted.bot.get_business_connection(deleted.business_connection_id)

    unique_chat_id = f"{bc.user_chat_id}_{deleted.chat.id}"
    stored_messages = session.exec(
        select(ChatMessage)
        .where(ChatMessage.unique_chat_id == unique_chat_id)
        .where(ChatMessage.message_id.in_(deleted.message_ids))
    ).all()

    if not stored_messages:
        stored_messages = session.exec(
            select(ChatMessage)
            .where(ChatMessage.unique_chat_id.like(f"{bc.user_chat_id}_%"))
            .where(ChatMessage.message_id.in_(deleted.message_ids))
        ).all()
        if not stored_messages:
            return

    for stored_message in stored_messages:
        if stored_message.is_deleted:
            continue
        original_content = stored_message.content
        stored_message.content = f"{original_content} 🗑️"
        stored_message.is_deleted = True
        session.add(stored_message)
        username = stored_message.from_username or stored_message.from_name
        media_caption = f"🗑️ @{username} удалил(а) медиа"
        await deleted.bot.send_message(
            chat_id=bc.user_chat_id,
            text=(
                f'<tg-emoji emoji-id="5332372777552881448">🗑️</tg-emoji> '
                f" <b>@{username} удалил(а) сообщение</b>\n"
                f"<blockquote>💬{original_content}</blockquote>"
            ),
        )

        if stored_message.file_id and stored_message.content_type:
            if stored_message.content_type == "photo":
                await deleted.bot.send_photo(
                    chat_id=bc.user_chat_id,
                    photo=stored_message.file_id,
                    caption=f"{media_caption}\n{stored_message.caption}".strip()
                    if stored_message.caption
                    else media_caption,
                )
            elif stored_message.content_type == "video":
                await deleted.bot.send_video(
                    chat_id=bc.user_chat_id,
                    video=stored_message.file_id,
                    caption=f"{media_caption}\n{stored_message.caption}".strip()
                    if stored_message.caption
                    else media_caption,
                )
            elif stored_message.content_type == "video_note":
                await deleted.bot.send_video_note(
                    chat_id=bc.user_chat_id,
                    video_note=stored_message.file_id,
                )
                if stored_message.caption:
                    await deleted.bot.send_message(
                        chat_id=bc.user_chat_id,
                        text=f"{media_caption}\n{stored_message.caption}",
                    )
                else:
                    await deleted.bot.send_message(chat_id=bc.user_chat_id, text=media_caption)
            elif stored_message.content_type == "document":
                await deleted.bot.send_document(
                    chat_id=bc.user_chat_id,
                    document=stored_message.file_id,
                    caption=f"{media_caption}\n{stored_message.caption}".strip()
                    if stored_message.caption
                    else media_caption,
                )
            elif stored_message.content_type == "audio":
                await deleted.bot.send_audio(
                    chat_id=bc.user_chat_id,
                    audio=stored_message.file_id,
                    caption=f"{media_caption}\n{stored_message.caption}".strip()
                    if stored_message.caption
                    else media_caption,
                )
            elif stored_message.content_type == "voice":
                await deleted.bot.send_voice(
                    chat_id=bc.user_chat_id,
                    voice=stored_message.file_id,
                    caption=f"{media_caption}\n{stored_message.caption}".strip()
                    if stored_message.caption
                    else media_caption,
                )
            elif stored_message.content_type == "animation":
                await deleted.bot.send_animation(
                    chat_id=bc.user_chat_id,
                    animation=stored_message.file_id,
                    caption=f"{media_caption}\n{stored_message.caption}".strip()
                    if stored_message.caption
                    else media_caption,
                )

    session.commit()
    await refresh_menu_link(deleted.bot, session, bc.user_chat_id)


async def cleanup_old_messages():
    while True:
        session = SQLSession(db.engine)
        threshold = datetime.now() - timedelta(days=3)
        old_msgs = session.exec(select(ChatMessage).where(ChatMessage.created_at < threshold)).all()
        for msg in old_msgs:
            session.delete(msg)
        session.commit()
        await asyncio.sleep(3600)


async def main():
    db.init()
    SQLModel.metadata.create_all(db.engine)
    print(f"✅ База данных: {db.DB_PATH}")
    
    await asyncio.gather(
        dp.start_polling(bot),
        cleanup_old_messages(),
        periodic_refresh_menu_links(),
        return_exceptions=True
    )


def flask_thread():
    """Flask в отдельном потоке"""
    port = int(os.getenv('PORT', 3000))
    print(f"✅ Flask API на порту {port}")
    api_app.run(host='0.0.0.0', port=port, use_reloader=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Flask в фоновом потоке
    flask_t = threading.Thread(target=flask_thread, daemon=True)
    flask_t.start()
    
    # Бот в главном потоке
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 Бот остановлен")
