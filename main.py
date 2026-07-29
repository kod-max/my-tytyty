import asyncio
import json
import logging
import re
import random
import string
import os
import csv
from io import StringIO
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import httpx
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    MessageEntity,
    CallbackQuery,
    Message,
    FSInputFile,
    BufferedInputFile
)

# ======================== КОНФИГУРАЦИЯ ========================
BOT_TOKEN = "8622998587:AAFglkCHi1lOcn9hjhH7ImSOWoTH74Ltyds"
ADMIN_IDS = [8488094637]

# ======================== PIARFLOW ========================
PIARFLOW_API_KEY = "Jq1xivpQPHvRxKQE11EpbZaQ9t6BWIWE"
PIARFLOW_API_URL = "https://piarflow.com/v1"
PIARFLOW_MAX_SPONSORS = 10

CURRENCY_RUB = "₽"
CURRENCY_MANAT = "ТМТ"
RATE_MANAT = 0.25

DEFAULT_REFERRAL_REWARD_RUB = 3.0
DEFAULT_REFERRAL_REWARD_MANAT = 0.75 

TASK_REWARD_RUB = 0.5
TASK_REWARD_MANAT = TASK_REWARD_RUB * RATE_MANAT
MAX_TASKS_PER_DAY = 5

MIN_WITHDRAW_RUB = 150.0
MIN_WITHDRAW_MANAT = 37.5 

PAYMENTS_CHANNEL = "@RublTMT_Payments"

# ПРЕМИУМ ЭМОДЗИ
EMOJI_COINS = "5251435583443587818"  # Основной премиум эмодзи для спонсоров
DEFAULT_EMOJI_CHECK = "5397916757333654639"
DEFAULT_EMOJI_STAR = "5393512611968995988"
DEFAULT_EMOJI_ROCKET = "5397916757333654638"
DEFAULT_EMOJI_FIRE = "5397916757333654637"

# НОВЫЙ ЭМОДЗИ ДЛЯ КНОПКИ "НАЗАД"
EMOJI_BACK = "5413444438498223062"

EMOJI_TASK_HOURGLASS = DEFAULT_EMOJI_CHECK    
EMOJI_TASK_DONE_ALL = DEFAULT_EMOJI_CHECK     
EMOJI_TASK_LIST = DEFAULT_EMOJI_ROCKET        
EMOJI_TASK_MONEY = EMOJI_COINS  # Используем основной эмодзи
EMOJI_TASK_SUCCESS = DEFAULT_EMOJI_CHECK      
EMOJI_LOCK = "5251408958941322169"            
EMOJI_ANON = "6105006251295377689"            

INACTIVE_DAYS = 7

# ======================== ИНИЦИАЛИЗАЦИЯ ========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ======================== ПАРСЕР РАЗМЕТКИ ========================
def parse_premium_emoji(text: str) -> Tuple[str, List[MessageEntity]]:
    """
    Парсер разметки с поддержкой:
    - <emoji id="123">текст</emoji> - премиум эмодзи
    - <b>текст</b> или **текст** - жирный
    - <i>текст</i> - курсив
    - <code>текст</code> - код
    - <pole>текст</pole> - цитата (вместо ##текст##)
    - <zhirnyy>текст</zhirnyy> - жирный в цитате
    """
    if not text:
        return text, []

    entities = []
    clean_text = ""
    current_pos = 0
    
    def add_text_with_entities(content: str, entity_type: str = None, custom_emoji_id: str = None):
        nonlocal clean_text, current_pos
        
        if entity_type == "custom_emoji":
            visible = content or "🙂"
            entity_length = len(visible.encode('utf-16-le')) // 2
            entities.append(
                MessageEntity(
                    type="custom_emoji",
                    offset=current_pos,
                    length=entity_length,
                    custom_emoji_id=custom_emoji_id
                )
            )
            clean_text += visible
            current_pos += entity_length
        elif entity_type == "bold":
            entity_length = len(content.encode('utf-16-le')) // 2
            entities.append(
                MessageEntity(type="bold", offset=current_pos, length=entity_length)
            )
            clean_text += content
            current_pos += entity_length
        elif entity_type == "italic":
            entity_length = len(content.encode('utf-16-le')) // 2
            entities.append(
                MessageEntity(type="italic", offset=current_pos, length=entity_length)
            )
            clean_text += content
            current_pos += entity_length
        elif entity_type == "code":
            entity_length = len(content.encode('utf-16-le')) // 2
            entities.append(
                MessageEntity(type="code", offset=current_pos, length=entity_length)
            )
            clean_text += content
            current_pos += entity_length
        elif entity_type == "blockquote":
            entity_length = len(content.encode('utf-16-le')) // 2
            entities.append(
                MessageEntity(type="blockquote", offset=current_pos, length=entity_length)
            )
            clean_text += content
            current_pos += entity_length
        else:
            clean_text += content
            current_pos += len(content.encode('utf-16-le')) // 2
    
    # Сначала обрабатываем <pole>...</pole>
    pole_pattern = re.compile(r'<pole>(.*?)</pole>', re.DOTALL)
    
    # Разбиваем текст на части
    parts = []
    last_end = 0
    
    for match in pole_pattern.finditer(text):
        start, end = match.span()
        if start > last_end:
            parts.append(('text', text[last_end:start]))
        parts.append(('pole', match.group(1)))
        last_end = end
    
    if last_end < len(text):
        parts.append(('text', text[last_end:]))
    
    # Обрабатываем каждую часть
    for part_type, part_content in parts:
        if part_type == 'pole':
            pole_text, pole_entities = process_pole_content(part_content)
            add_text_with_entities(pole_text, 'blockquote')
            for e in pole_entities:
                e.offset += current_pos - len(pole_text.encode('utf-16-le')) // 2
                entities.append(e)
        else:
            regular_text, regular_entities = process_regular_text(part_content)
            clean_text += regular_text
            for e in regular_entities:
                e.offset += current_pos
                entities.append(e)
            current_pos += len(regular_text.encode('utf-16-le')) // 2
    
    return clean_text, entities


def process_pole_content(content: str) -> Tuple[str, List[MessageEntity]]:
    """Обрабатывает содержимое pole с поддержкой вложенных тегов"""
    inner_entities = []
    inner_text = ""
    pos = 0
    
    def add_inner_entity(text_part: str, entity_type: str = None, custom_emoji_id: str = None):
        nonlocal inner_text, pos
        if entity_type == "custom_emoji":
            visible = text_part or "🙂"
            entity_length = len(visible.encode('utf-16-le')) // 2
            inner_entities.append(
                MessageEntity(
                    type="custom_emoji",
                    offset=pos,
                    length=entity_length,
                    custom_emoji_id=custom_emoji_id
                )
            )
            inner_text += visible
            pos += entity_length
        elif entity_type == "bold":
            entity_length = len(text_part.encode('utf-16-le')) // 2
            inner_entities.append(
                MessageEntity(type="bold", offset=pos, length=entity_length)
            )
            inner_text += text_part
            pos += entity_length
        else:
            inner_text += text_part
            pos += len(text_part.encode('utf-16-le')) // 2
    
    # Обрабатываем содержимое pole
    temp = content
    
    # 1. <zhirnyy>текст</zhirnyy>
    zhirnyy_markers = []
    
    def replace_zhirnyy(match):
        zhirnyy_markers.append(match.group(1))
        return f"__Z_{len(zhirnyy_markers)-1}__"
    
    temp = re.sub(r'<zhirnyy>(.*?)</zhirnyy>', replace_zhirnyy, temp, flags=re.DOTALL)
    
    # 2. **текст**
    bold_markers = []
    
    def replace_bold(match):
        bold_markers.append(match.group(1))
        return f"__B_{len(bold_markers)-1}__"
    
    temp = re.sub(r'\*\*(.*?)\*\*', replace_bold, temp)
    
    # 3. <b>текст</b>
    b_markers = []
    
    def replace_b(match):
        b_markers.append(match.group(1))
        return f"__BB_{len(b_markers)-1}__"
    
    temp = re.sub(r'<b>(.*?)</b>', replace_b, temp, flags=re.DOTALL)
    
    # 4. <emoji id="...">текст</emoji>
    emoji_markers = []
    
    def replace_emoji(match):
        emoji_markers.append((match.group(1), match.group(2)))
        return f"__E_{len(emoji_markers)-1}__"
    
    temp = re.sub(r'<emoji\s+id=["\'](\d+)["\']>(.*?)</emoji>', replace_emoji, temp, flags=re.DOTALL)
    
    # Разбираем temp
    i = 0
    while i < len(temp):
        marker_found = False
        
        if temp[i:].startswith("__Z_"):
            end_idx = temp.find("__", i + 4)
            if end_idx != -1:
                try:
                    idx = int(temp[i+4:end_idx])
                    if idx < len(zhirnyy_markers):
                        add_inner_entity(zhirnyy_markers[idx], 'bold')
                except (ValueError, IndexError):
                    pass
                i = end_idx + 2
                marker_found = True
                continue
        
        if temp[i:].startswith("__B_"):
            end_idx = temp.find("__", i + 4)
            if end_idx != -1:
                try:
                    idx = int(temp[i+4:end_idx])
                    if idx < len(bold_markers):
                        add_inner_entity(bold_markers[idx], 'bold')
                except (ValueError, IndexError):
                    pass
                i = end_idx + 2
                marker_found = True
                continue
        
        if temp[i:].startswith("__BB_"):
            end_idx = temp.find("__", i + 5)
            if end_idx != -1:
                try:
                    idx = int(temp[i+5:end_idx])
                    if idx < len(b_markers):
                        add_inner_entity(b_markers[idx], 'bold')
                except (ValueError, IndexError):
                    pass
                i = end_idx + 2
                marker_found = True
                continue
        
        if temp[i:].startswith("__E_"):
            end_idx = temp.find("__", i + 4)
            if end_idx != -1:
                try:
                    idx = int(temp[i+4:end_idx])
                    if idx < len(emoji_markers):
                        emoji_id, visible = emoji_markers[idx]
                        add_inner_entity(visible, 'custom_emoji', emoji_id)
                except (ValueError, IndexError):
                    pass
                i = end_idx + 2
                marker_found = True
                continue
        
        if not marker_found:
            next_marker = len(temp)
            for marker in ["__Z_", "__B_", "__BB_", "__E_"]:
                pos_marker = temp.find(marker, i)
                if pos_marker != -1 and pos_marker < next_marker:
                    next_marker = pos_marker
            
            if next_marker > i:
                text_part = temp[i:next_marker]
                add_inner_entity(text_part)
                i = next_marker
            else:
                i += 1
    
    return inner_text, inner_entities


def process_regular_text(text: str) -> Tuple[str, List[MessageEntity]]:
    """Обрабатывает обычный текст с тегами (вне pole)"""
    entities = []
    clean_text = ""
    pos = 0
    
    def add_entity(text_part: str, entity_type: str = None, custom_emoji_id: str = None):
        nonlocal clean_text, pos
        if entity_type == "custom_emoji":
            visible = text_part or "🙂"
            entity_length = len(visible.encode('utf-16-le')) // 2
            entities.append(
                MessageEntity(
                    type="custom_emoji",
                    offset=pos,
                    length=entity_length,
                    custom_emoji_id=custom_emoji_id
                )
            )
            clean_text += visible
            pos += entity_length
        elif entity_type == "bold":
            entity_length = len(text_part.encode('utf-16-le')) // 2
            entities.append(
                MessageEntity(type="bold", offset=pos, length=entity_length)
            )
            clean_text += text_part
            pos += entity_length
        elif entity_type == "italic":
            entity_length = len(text_part.encode('utf-16-le')) // 2
            entities.append(
                MessageEntity(type="italic", offset=pos, length=entity_length)
            )
            clean_text += text_part
            pos += entity_length
        elif entity_type == "code":
            entity_length = len(text_part.encode('utf-16-le')) // 2
            entities.append(
                MessageEntity(type="code", offset=pos, length=entity_length)
            )
            clean_text += text_part
            pos += entity_length
        else:
            clean_text += text_part
            pos += len(text_part.encode('utf-16-le')) // 2
    
    temp = text
    
    # ДОБАВЛЯЕМ ОБРАБОТКУ <zhirnyy> ДЛЯ ОБЫЧНОГО ТЕКСТА
    zhirnyy_markers = []
    
    def replace_zhirnyy(match):
        zhirnyy_markers.append(match.group(1))
        return f"__Z_{len(zhirnyy_markers)-1}__"
    
    temp = re.sub(r'<zhirnyy>(.*?)</zhirnyy>', replace_zhirnyy, temp, flags=re.DOTALL)
    
    # 1. <emoji id="...">текст</emoji>
    emoji_markers = []
    
    def replace_emoji(match):
        emoji_markers.append((match.group(1), match.group(2)))
        return f"__E_{len(emoji_markers)-1}__"
    
    temp = re.sub(r'<emoji\s+id=["\'](\d+)["\']>(.*?)</emoji>', replace_emoji, temp, flags=re.DOTALL)
    
    # 2. **текст**
    bold_markers = []
    
    def replace_bold(match):
        bold_markers.append(match.group(1))
        return f"__B_{len(bold_markers)-1}__"
    
    temp = re.sub(r'\*\*(.*?)\*\*', replace_bold, temp)
    
    # 3. <b>текст</b>
    b_markers = []
    
    def replace_b(match):
        b_markers.append(match.group(1))
        return f"__BB_{len(b_markers)-1}__"
    
    temp = re.sub(r'<b>(.*?)</b>', replace_b, temp, flags=re.DOTALL)
    
    # 4. <i>текст</i>
    i_markers = []
    
    def replace_i(match):
        i_markers.append(match.group(1))
        return f"__I_{len(i_markers)-1}__"
    
    temp = re.sub(r'<i>(.*?)</i>', replace_i, temp, flags=re.DOTALL)
    
    # 5. <code>текст</code>
    code_markers = []
    
    def replace_code(match):
        code_markers.append(match.group(1))
        return f"__C_{len(code_markers)-1}__"
    
    temp = re.sub(r'<code>(.*?)</code>', replace_code, temp, flags=re.DOTALL)
    
    # Разбираем temp
    i = 0
    while i < len(temp):
        marker_found = False
        
        # ДОБАВЛЯЕМ ОБРАБОТКУ __Z_ ДЛЯ ОБЫЧНОГО ТЕКСТА
        if temp[i:].startswith("__Z_"):
            end_idx = temp.find("__", i + 4)
            if end_idx != -1:
                try:
                    idx = int(temp[i+4:end_idx])
                    if idx < len(zhirnyy_markers):
                        add_entity(zhirnyy_markers[idx], 'bold')
                except (ValueError, IndexError):
                    pass
                i = end_idx + 2
                marker_found = True
                continue
        
        if temp[i:].startswith("__E_"):
            end_idx = temp.find("__", i + 4)
            if end_idx != -1:
                try:
                    idx = int(temp[i+4:end_idx])
                    if idx < len(emoji_markers):
                        emoji_id, visible = emoji_markers[idx]
                        add_entity(visible, 'custom_emoji', emoji_id)
                except (ValueError, IndexError):
                    pass
                i = end_idx + 2
                marker_found = True
                continue
        
        if temp[i:].startswith("__B_"):
            end_idx = temp.find("__", i + 4)
            if end_idx != -1:
                try:
                    idx = int(temp[i+4:end_idx])
                    if idx < len(bold_markers):
                        add_entity(bold_markers[idx], 'bold')
                except (ValueError, IndexError):
                    pass
                i = end_idx + 2
                marker_found = True
                continue
        
        if temp[i:].startswith("__BB_"):
            end_idx = temp.find("__", i + 5)
            if end_idx != -1:
                try:
                    idx = int(temp[i+5:end_idx])
                    if idx < len(b_markers):
                        add_entity(b_markers[idx], 'bold')
                except (ValueError, IndexError):
                    pass
                i = end_idx + 2
                marker_found = True
                continue
        
        if temp[i:].startswith("__I_"):
            end_idx = temp.find("__", i + 4)
            if end_idx != -1:
                try:
                    idx = int(temp[i+4:end_idx])
                    if idx < len(i_markers):
                        add_entity(i_markers[idx], 'italic')
                except (ValueError, IndexError):
                    pass
                i = end_idx + 2
                marker_found = True
                continue
        
        if temp[i:].startswith("__C_"):
            end_idx = temp.find("__", i + 4)
            if end_idx != -1:
                try:
                    idx = int(temp[i+4:end_idx])
                    if idx < len(code_markers):
                        add_entity(code_markers[idx], 'code')
                except (ValueError, IndexError):
                    pass
                i = end_idx + 2
                marker_found = True
                continue
        
        if not marker_found:
            next_marker = len(temp)
            for marker in ["__Z_", "__E_", "__B_", "__BB_", "__I_", "__C_"]:
                pos_marker = temp.find(marker, i)
                if pos_marker != -1 and pos_marker < next_marker:
                    next_marker = pos_marker
            
            if next_marker > i:
                text_part = temp[i:next_marker]
                add_entity(text_part)
                i = next_marker
            else:
                i += 1
    
    return clean_text, entities


def format_with_emoji(text: str, **kwargs) -> Tuple[str, List[MessageEntity]]:
    """Применяет форматирование с подстановкой переменных и парсит разметку"""
    try:
        formatted = text.format(**kwargs)
    except (KeyError, IndexError, ValueError) as e:
        logger.error(f"Ошибка форматирования: {e}")
        return text, []
    return parse_premium_emoji(formatted)


def strip_custom_emoji_entities(entities: Optional[List[MessageEntity]]) -> Optional[List[MessageEntity]]:
    if not entities:
        return None
    filtered = [e for e in entities if e.type != "custom_emoji"]
    return filtered if filtered else None

# ======================== БАЗА ДАННЫХ ========================
class Database:
    def __init__(self, filename="referral_bot_db.json"):
        self.filename = filename
        self.data = self._load()
        self._ensure_defaults()
        self._migrate_referral_reward_paid()
        self._validate_banner()

    def _migrate_referral_reward_paid(self):
        changed = False
        for uid, user in self.data.get("users", {}).items():
            if user.get("referred_by") is not None and "referral_reward_paid" not in user:
                user["referral_reward_paid"] = True
                changed = True
        if changed:
            self.save()

    def _validate_banner(self):
        banner_path = self.data.get("banner_path")
        if banner_path and os.path.exists(banner_path):
            try:
                if os.path.getsize(banner_path) < 100:
                    self.remove_banner()
                    logger.warning("Баннер удалён: слишком маленький файл")
                    return
                with open(banner_path, 'rb') as f:
                    header = f.read(10)
                    if header[:2] != b'\xff\xd8':
                        self.remove_banner()
                        logger.warning("Баннер удалён: неверный формат файла")
                        return
            except Exception as e:
                logger.error(f"Ошибка проверки баннера: {e}")
                self.remove_banner()
        elif banner_path:
            self.remove_banner()

    def _load(self):
        try:
            with open(self.filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return self._default_data()

    def _default_data(self):
        return {
            "users": {},
            "referrals": {},
            "sponsors": [],
            "admins": ADMIN_IDS,
            "start_text": (
                '<emoji id="5224607267797606837">👋</emoji> Добро пожаловать в бот заработка!\n\n'
                'Здесь всё просто: выполняй задания от спонсоров, приглашай друзей и выводи заработанное на свой счёт.\n\n'
                '<pole>\n'
                '<emoji id="5251435583443587818">💰</emoji> <zhirnyy>За каждого приглашённого друга вы получаете {REFERRAL_REWARD_RUB}₽ / {REFERRAL_REWARD_MANAT}ТМТ</zhirnyy>\n'
                '<emoji id="5413879192267805083">🎯</emoji> До 5 заданий в день — 0.50₽ за каждое\n'
                '<emoji id="5456140674028019486">⚡️</emoji> Выплаты обрабатываются в течение 1-12 часов\n'
                '</pole>\n\n'
                'Жми «Заработок», чтобы начать зарабатывать прямо сейчас!<emoji id="5440660757194744323">⭐</emoji>'
            ),
            "button_texts": {
                "earn": "Заработок",
                "referrals": "Рефералы",
                "top": "Топ",
                "profile": "Профиль",
                "withdraw": "Вывод",
            },
            "button_emojis": {
                "earn": EMOJI_COINS,
                "referrals": DEFAULT_EMOJI_FIRE,
                "top": DEFAULT_EMOJI_ROCKET,
                "profile": DEFAULT_EMOJI_STAR,
                "withdraw": DEFAULT_EMOJI_CHECK,
            },
            "banner_path": None,
            "referral_reward_rub": DEFAULT_REFERRAL_REWARD_RUB,
            "referral_reward_manat": DEFAULT_REFERRAL_REWARD_MANAT,
            "withdrawals": [],
            "statistics": {
                "total_users": 0,
                "total_referrals": 0,
                "total_withdrawn": 0,
                "total_earned": 0.0,
                "total_spent": 0.0,
                "active_users": 0
            },
            "bot_stopped": False,
            "promocodes": {},
            "transactions": {},
            "user_activity": {}
        }

    def _ensure_defaults(self):
        changed = False
        defaults = self._default_data()
        for key in defaults:
            if key not in self.data:
                self.data[key] = defaults[key]
                changed = True
        if changed:
            self.save()

    def save(self):
        with open(self.filename, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def get_user(self, user_id: int) -> Dict:
        uid = str(user_id)
        if uid not in self.data["users"]:
            self.data["users"][uid] = {
                "id": user_id,
                "balance_rub": 0.0,
                "balance_manat": 0.0,
                "referral_count": 0,
                "tasks_completed": 0,
                "tasks_today": 0,
                "tasks_today_date": datetime.now().date().isoformat(),
                "referral_code": self._generate_code(user_id),
                "referred_by": None,
                "created_at": datetime.now().isoformat(),
                "is_banned": False,
                "verified_sponsors": False,
                "last_activity": datetime.now().isoformat()
            }
            self.save()
        return self.data["users"][uid]

    def update_activity(self, user_id: int):
        user = self.get_user(user_id)
        user["last_activity"] = datetime.now().isoformat()
        self.save()

    def _generate_code(self, user_id: int) -> str:
        chars = string.ascii_uppercase + string.digits
        code = ''.join(random.choices(chars, k=8))
        for uid, user in self.data["users"].items():
            if user.get("referral_code") == code:
                return self._generate_code(user_id)
        return code

    def link_referral(self, referrer_id: int, new_user_id: int) -> bool:
        new_user = self.get_user(new_user_id)
        if new_user.get("referred_by") is not None:
            return False
        new_user["referred_by"] = referrer_id
        new_user["referral_reward_paid"] = False
        uid = str(referrer_id)
        if uid not in self.data["referrals"]:
            self.data["referrals"][uid] = []
        self.data["referrals"][uid].append(new_user_id)
        self.save()
        return True

    def confirm_referral_reward(self, new_user_id: int) -> Optional[int]:
        new_user = self.get_user(new_user_id)
        referrer_id = new_user.get("referred_by")
        if referrer_id is None:
            return None
        if new_user.get("referral_reward_paid"):
            return None
        referrer = self.get_user(referrer_id)
        reward_rub = self.get_referral_reward_rub()
        reward_manat = self.get_referral_reward_manat()
        referrer["balance_rub"] += reward_rub
        referrer["balance_manat"] += reward_manat
        referrer["referral_count"] += 1
        new_user["referral_reward_paid"] = True
        self.data["statistics"]["total_referrals"] += 1
        self.add_transaction(referrer_id, "earn", reward_rub, f"Реферал {new_user_id}")
        self.save()
        return referrer_id

    def get_referrals(self, user_id: int) -> List[int]:
        uid = str(user_id)
        return self.data["referrals"].get(uid, [])

    def add_balance(self, user_id: int, amount_rub: float, amount_manat: float):
        user = self.get_user(user_id)
        user["balance_rub"] += amount_rub
        user["balance_manat"] += amount_manat
        self.data["statistics"]["total_earned"] += amount_rub
        self.save()

    def deduct_balance(self, user_id: int, amount_rub: float) -> bool:
        user = self.get_user(user_id)
        if user["balance_rub"] < amount_rub:
            return False
        user["balance_rub"] -= amount_rub
        user["balance_manat"] -= amount_rub * RATE_MANAT
        self.data["statistics"]["total_spent"] += amount_rub
        self.save()
        return True

    def create_withdrawal(self, user_id: int, amount_display: float, currency: str, amount_rub_deducted: float, details: str) -> int:
        withdrawal_id = len(self.data["withdrawals"]) + 1
        self.data["withdrawals"].append({
            "id": withdrawal_id,
            "user_id": user_id,
            "amount_display": amount_display,
            "currency": currency,
            "amount_rub_deducted": amount_rub_deducted,
            "details": details,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "channel_message_id": None
        })
        self.save()
        return withdrawal_id

    def get_withdrawal(self, withdrawal_id: int) -> Optional[Dict]:
        for w in self.data["withdrawals"]:
            if w["id"] == withdrawal_id:
                return w
        return None

    def set_withdrawal_channel_message(self, withdrawal_id: int, message_id: int):
        w = self.get_withdrawal(withdrawal_id)
        if w:
            w["channel_message_id"] = message_id
            self.save()

    def set_withdrawal_status(self, withdrawal_id: int, status: str) -> bool:
        w = self.get_withdrawal(withdrawal_id)
        if not w or w["status"] != "pending":
            return False
        w["status"] = status
        if status == "paid":
            self.data["statistics"]["total_withdrawn"] += w["amount_rub_deducted"]
        elif status == "rejected":
            user = self.get_user(w["user_id"])
            user["balance_rub"] += w["amount_rub_deducted"]
            user["balance_manat"] += w["amount_rub_deducted"] * RATE_MANAT
        self.save()
        return True

    def _reset_daily_tasks_if_needed(self, user: Dict):
        today = datetime.now().date().isoformat()
        if user.get("tasks_today_date") != today:
            user["tasks_today_date"] = today
            user["tasks_today"] = 0

    def get_tasks_left_today(self, user_id: int) -> int:
        user = self.get_user(user_id)
        self._reset_daily_tasks_if_needed(user)
        self.save()
        return max(MAX_TASKS_PER_DAY - user.get("tasks_today", 0), 0)

    def register_task_completed(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        self._reset_daily_tasks_if_needed(user)
        if user.get("tasks_today", 0) >= MAX_TASKS_PER_DAY:
            return False
        user["tasks_today"] += 1
        user["tasks_completed"] += 1
        user["balance_rub"] += TASK_REWARD_RUB
        user["balance_manat"] += TASK_REWARD_MANAT
        self.add_transaction(user_id, "earn", TASK_REWARD_RUB, "Выполнение задания")
        self.save()
        return True

    def get_top_referrals(self, limit: int = 10) -> List[Tuple[int, int]]:
        users = self.data["users"]
        top = []
        for uid, user in users.items():
            if user["referral_count"] > 0:
                top.append((int(uid), user["referral_count"]))
        top.sort(key=lambda x: x[1], reverse=True)
        return top[:limit]

    def admin_add_referrals(self, user_id: int, count: int):
        user = self.get_user(user_id)
        user["referral_count"] = max(0, user.get("referral_count", 0) + count)
        self.save()

    def admin_reset_referrals(self, user_id: int):
        user = self.get_user(user_id)
        user["referral_count"] = 0
        self.save()

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.data["admins"]

    def is_banned(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        return user.get("is_banned", False)

    def add_sponsor(self, button_text: str, link: str, channel_id: str = None, order: int = 0, sponsor_type: str = "channel", channel_ids: List[str] = None):
        entry = {
            "button_text": button_text,
            "link": link,
            "order": order,
            "type": sponsor_type
        }
        if sponsor_type == "addlist" and channel_ids:
            entry["channel_ids"] = channel_ids
        else:
            entry["channel_id"] = channel_id
        self.data["sponsors"].append(entry)
        self.save()

    def remove_sponsor(self, index: int):
        if 0 <= index < len(self.data["sponsors"]):
            self.data["sponsors"].pop(index)
            self.save()

    def get_sponsors(self) -> List[Dict]:
        return sorted(self.data["sponsors"], key=lambda x: x.get("order", 0))

    def set_start_text(self, text: str):
        self.data["start_text"] = text
        self.save()

    def get_start_text(self) -> str:
        return self.data.get("start_text", "")

    def set_bot_stopped(self, stopped: bool):
        self.data["bot_stopped"] = stopped
        self.save()

    def is_bot_stopped(self) -> bool:
        return self.data.get("bot_stopped", False)

    def set_verified(self, user_id: int, value: bool = True):
        user = self.get_user(user_id)
        user["verified_sponsors"] = value
        self.save()

    def is_verified(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        return user.get("verified_sponsors", False)

    def get_button_text(self, key: str) -> str:
        return self.data.get("button_texts", {}).get(key, key.capitalize())

    def set_button_text(self, key: str, text: str):
        self.data["button_texts"][key] = text
        self.save()

    def get_button_emoji(self, key: str) -> str:
        return self.data.get("button_emojis", {}).get(key, EMOJI_COINS)

    def set_button_emoji(self, key: str, emoji_id: str):
        self.data["button_emojis"][key] = emoji_id
        self.save()

    def set_banner(self, path: str):
        self.data["banner_path"] = path
        self.save()

    def get_banner(self) -> Optional[str]:
        return self.data.get("banner_path")

    def remove_banner(self):
        banner_path = self.data.get("banner_path")
        if banner_path and os.path.exists(banner_path):
            try:
                os.remove(banner_path)
                logger.info(f"Файл баннера удалён: {banner_path}")
            except Exception as e:
                logger.error(f"Ошибка удаления баннера: {e}")
        self.data["banner_path"] = None
        self.save()

    def get_referral_reward_rub(self) -> float:
        return self.data.get("referral_reward_rub", DEFAULT_REFERRAL_REWARD_RUB)

    def get_referral_reward_manat(self) -> float:
        return self.data.get("referral_reward_manat", DEFAULT_REFERRAL_REWARD_MANAT)

    def set_referral_reward_rub(self, rub: float):
        self.data["referral_reward_rub"] = rub
        self.data["referral_reward_manat"] = rub * RATE_MANAT
        self.save()

    def set_referral_reward_manat(self, manat: float):
        self.data["referral_reward_manat"] = manat
        self.data["referral_reward_rub"] = manat / RATE_MANAT
        self.save()

    # ===== НОВЫЕ МЕТОДЫ =====
    
    def create_promocode(self, code: str, reward: float, uses: int):
        self.data["promocodes"][code] = {
            "reward": reward,
            "uses": uses,
            "used_by": []
        }
        self.save()

    def use_promocode(self, user_id: int, code: str) -> Optional[float]:
        promo = self.data["promocodes"].get(code)
        if not promo:
            return None
        if len(promo["used_by"]) >= promo["uses"]:
            return None
        if user_id in promo["used_by"]:
            return None
        
        promo["used_by"].append(user_id)
        reward = promo["reward"]
        self.add_balance(user_id, reward, reward * RATE_MANAT)
        self.add_transaction(user_id, "promo", reward, f"Промокод: {code}")
        self.save()
        return reward

    def add_transaction(self, user_id: int, trans_type: str, amount: float, description: str = ""):
        uid = str(user_id)
        if uid not in self.data["transactions"]:
            self.data["transactions"][uid] = []
        self.data["transactions"][uid].append({
            "type": trans_type,
            "amount": amount,
            "date": datetime.now().isoformat(),
            "description": description
        })
        self.save()

    def get_transactions(self, user_id: int, limit: int = 20) -> List[Dict]:
        uid = str(user_id)
        return self.data["transactions"].get(uid, [])[-limit:]

    def get_referral_stats(self, user_id: int) -> Dict:
        referrals = self.get_referrals(user_id)
        active_count = 0
        for ref_id in referrals:
            user = self.get_user(ref_id)
            if user.get("tasks_completed", 0) > 0:
                active_count += 1
        return {
            "total": len(referrals),
            "active": active_count,
            "inactive": len(referrals) - active_count
        }

    def export_stats_csv(self) -> str:
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(["User ID", "Balance RUB", "Balance TMT", "Referrals", "Tasks Done", "Banned", "Joined At"])
        for uid, user in self.data["users"].items():
            writer.writerow([
                uid,
                user.get("balance_rub", 0),
                user.get("balance_manat", 0),
                user.get("referral_count", 0),
                user.get("tasks_completed", 0),
                user.get("is_banned", False),
                user.get("created_at", "")
            ])
        return output.getvalue()

    def get_inactive_users(self, days: int = INACTIVE_DAYS) -> List[int]:
        threshold = datetime.now() - timedelta(days=days)
        inactive = []
        for uid, user in self.data["users"].items():
            if user.get("is_banned", False):
                continue
            last_activity = user.get("last_activity")
            if last_activity:
                last_date = datetime.fromisoformat(last_activity)
                if last_date < threshold:
                    inactive.append(int(uid))
        return inactive

db = Database()

# ======================== PIARFLOW API ========================
_piar_cache = {}
PIAR_CACHE_TTL = 60

_shown_piar_links: Dict[int, List[str]] = {}

async def fetch_piar_sponsors(user_id: int, chat_id: int) -> List[Dict]:
    if not PIARFLOW_API_KEY or PIARFLOW_API_KEY == "BURAYA_API_KEY_YAZ":
        return []
    
    cache_key = f"{user_id}_{chat_id}"
    now = asyncio.get_event_loop().time()
    if cache_key in _piar_cache:
        cached_time, data = _piar_cache[cache_key]
        if now - cached_time < PIAR_CACHE_TTL:
            return data
    
    url = f"{PIARFLOW_API_URL}/sponsors"
    payload = {"user_id": user_id, "chat_id": chat_id, "max_sponsors": PIARFLOW_MAX_SPONSORS}
    headers = {"Authorization": f"Bearer {PIARFLOW_API_KEY}", "Content-Type": "application/json"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    sponsors = data.get("sponsors", [])
                    _piar_cache[cache_key] = (now, sponsors)
                    logger.info(f"PiarFlow: получено {len(sponsors)} спонсоров для user_id={user_id}")
                    return sponsors
                elif resp.status == 404:
                    _piar_cache[cache_key] = (now, [])
                    logger.info(f"PiarFlow: заданий нет (404) для user_id={user_id}")
                    return []
                else:
                    logger.error(f"PiarFlow ошибка: HTTP {resp.status}")
                    return []
    except Exception as e:
        logger.error(f"PiarFlow ошибка: {e}")
        return []

async def check_piar_sponsors(user_id: int, links: List[str]) -> bool:
    if not links:
        return True
    
    url = f"{PIARFLOW_API_URL}/sponsors/check"
    payload = {"user_id": user_id, "links": links}
    headers = {"Authorization": f"Bearer {PIARFLOW_API_KEY}", "Content-Type": "application/json"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    sponsors = data.get("sponsors", [])
                    return all(s.get("status") in ["subscribed", "not_counted"] for s in sponsors)
                elif resp.status == 404:
                    logger.info(f"PiarFlow check: 404 (заданий нет) для user_id={user_id}")
                    return True
                logger.error(f"PiarFlow check ошибка: HTTP {resp.status}")
                return False
    except Exception as e:
        logger.error(f"PiarFlow check ошибка: {e}")
        return False

async def check_manual_sponsors(user_id: int) -> bool:
    for sponsor in db.get_sponsors():
        sponsor_type = sponsor.get("type", "channel")
        if sponsor_type == "channel":
            if "channel_id" in sponsor and sponsor["channel_id"]:
                if not await check_channel_subscription(user_id, sponsor["channel_id"]):
                    return False
        elif sponsor_type == "addlist":
            for cid in sponsor.get("channel_ids", []):
                if not await check_channel_subscription(user_id, cid):
                    return False
    return True

async def check_channel_subscription(user_id: int, channel_id: str) -> bool:
    try:
        member = await bot.get_chat_member(channel_id, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False

async def check_all_subscriptions(user_id: int, chat_id: int, use_shown_links: bool = False) -> bool:
    manual_task = asyncio.create_task(check_manual_sponsors(user_id))

    if use_shown_links and user_id in _shown_piar_links:
        links = _shown_piar_links[user_id]
        manual_ok = await manual_task
        if not manual_ok:
            return False
        if links:
            return await check_piar_sponsors(user_id, links)
        return True

    piar_task = asyncio.create_task(fetch_piar_sponsors(user_id, chat_id))

    manual_ok = await manual_task
    if not manual_ok:
        return False

    piar_sponsors = await piar_task
    links = [s.get("link") for s in piar_sponsors if s.get("link")]
    if links:
        return await check_piar_sponsors(user_id, links)

    return True

def invalidate_piar_cache(user_id: int):
    for key in list(_piar_cache.keys()):
        if key.startswith(str(user_id)):
            del _piar_cache[key]

# ======================== КЛАВИАТУРЫ ========================
def create_button(
    text: str,
    emoji_id: str = None,
    prefix: str = "",
    style: str = None,
    **kwargs
) -> InlineKeyboardButton:
    display = f"{prefix} {text}".strip() if prefix else text
    button_kwargs = dict(text=display, **kwargs)
    if emoji_id:
        button_kwargs["icon_custom_emoji_id"] = emoji_id
    if style:
        button_kwargs["style"] = style
    return InlineKeyboardButton(**button_kwargs)

def create_sponsor_keyboard(piar_sponsors: List[Dict] = None) -> Optional[InlineKeyboardMarkup]:
    sponsors = db.get_sponsors()
    piar_sponsors = piar_sponsors or []
    
    if not sponsors and not piar_sponsors:
        return None
    
    keyboard = []
    
    # Ручные спонсоры
    row = []
    for sponsor in sponsors:
        row.append(create_button(
            sponsor["button_text"],
            EMOJI_COINS,
            style="primary",
            url=sponsor["link"]
        ))
        if len(row) >= 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    # PiarFlow спонсоры
    row = []
    for s in piar_sponsors:
        link = s.get("link")
        if not link:
            continue
        row.append(create_button(
            "📢 Канал",
            EMOJI_COINS,
            style="primary",
            url=link
        ))
        if len(row) >= 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    keyboard.append([
        create_button(
            "Я подписался, проверить",
            DEFAULT_EMOJI_CHECK,
            style="success",
            callback_data="verify_sponsors"
        )
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def main_menu_keyboard() -> InlineKeyboardMarkup:
    btn_earn = db.get_button_text("earn")
    btn_referrals = db.get_button_text("referrals")
    btn_top = db.get_button_text("top")
    btn_profile = db.get_button_text("profile")
    btn_withdraw = db.get_button_text("withdraw")
    
    emoji_earn = db.get_button_emoji("earn")
    emoji_referrals = db.get_button_emoji("referrals")
    emoji_top = db.get_button_emoji("top")
    emoji_profile = db.get_button_emoji("profile")
    emoji_withdraw = db.get_button_emoji("withdraw")
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            create_button(btn_earn, emoji_earn, style="primary", callback_data="menu_earn")
        ],
        [
            create_button(btn_referrals, emoji_referrals, style="primary", callback_data="menu_referrals"),
            create_button(btn_top, emoji_top, style="primary", callback_data="menu_top"),
        ],
        [
            create_button(btn_profile, emoji_profile, style="primary", callback_data="menu_profile"),
        ],
        [
            create_button(btn_withdraw, emoji_withdraw, style="success", callback_data="menu_withdraw"),
        ],
        [
            create_button("Промокод", emoji_earn, style="primary", callback_data="menu_promo"),
            create_button("История", emoji_profile, style="primary", callback_data="menu_history"),
        ],
    ])

def earn_keyboard(has_offer: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_offer:
        rows.append([
            create_button("Проверить выполнение", DEFAULT_EMOJI_CHECK, style="success", callback_data="check_task")
        ])
    rows.append([
        create_button("Рефералы", db.get_button_emoji("referrals"), style="primary", callback_data="menu_referrals"),
        create_button("Назад", EMOJI_BACK, callback_data="menu_main"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def referral_keyboard(user_id: int) -> InlineKeyboardMarkup:
    bot_username = bot.username if hasattr(bot, 'username') and bot.username else "Earn_TMTRublBot"
    link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button(
            "Пригласить друга",
            db.get_button_emoji("top"),
            style="primary",
            url=f"tg://msg?text=Присоединяйся к боту для заработка!\n{link}"
        )],
        [create_button("Мои рефералы", db.get_button_emoji("profile"), style="primary", callback_data="referrals_list")],
        [create_button("Статистика рефералов", db.get_button_emoji("top"), style="primary", callback_data="referrals_stats")],
        [create_button("Назад", EMOJI_BACK, callback_data="menu_main")]
    ])

def profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button("Вывести средства", db.get_button_emoji("withdraw"), style="success", callback_data="menu_withdraw")],
        [create_button("История транзакций", db.get_button_emoji("profile"), style="primary", callback_data="menu_history")],
        [create_button("Назад", EMOJI_BACK, callback_data="menu_main")]
    ])

def withdraw_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button("Вывести в ₽", db.get_button_emoji("earn"), style="primary", callback_data="withdraw_rub")],
        [create_button("Вывести в ТМТ", db.get_button_emoji("earn"), style="primary", callback_data="withdraw_manat")],
        [create_button("Назад", EMOJI_BACK, callback_data="menu_main")]
    ])

def back_keyboard(callback_data: str = "menu_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button("Назад", EMOJI_BACK, callback_data=callback_data)]
    ])

def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            create_button("Статистика", db.get_button_emoji("profile"), style="primary", callback_data="admin_stats"),
            create_button("Пользователи", db.get_button_emoji("referrals"), style="primary", callback_data="admin_users")
        ],
        [
            create_button("Текст старта", DEFAULT_EMOJI_CHECK, style="primary", callback_data="admin_start_text"),
            create_button("Спонсоры/Задания", db.get_button_emoji("earn"), style="primary", callback_data="admin_sponsors")
        ],
        [
            create_button("Текст кнопок", db.get_button_emoji("profile"), style="primary", callback_data="admin_button_texts"),
            create_button("Эмодзи кнопок", db.get_button_emoji("star"), style="primary", callback_data="admin_button_emojis")
        ],
        [
            create_button("Баннер", db.get_button_emoji("top"), style="primary", callback_data="admin_banner"),
            create_button("Награда реферала", db.get_button_emoji("earn"), style="primary", callback_data="admin_referral_reward")
        ],
        [
            create_button("Промокоды", db.get_button_emoji("earn"), style="primary", callback_data="admin_promocode"),
            create_button("Экспорт CSV", db.get_button_emoji("top"), style="primary", callback_data="admin_export_csv")
        ],
        [
            create_button("Рассылка", db.get_button_emoji("fire"), style="primary", callback_data="admin_broadcast"),
            create_button("Бан/Разбан", DEFAULT_EMOJI_CHECK, style="danger", callback_data="admin_ban")
        ],
        [
            create_button("Статус бота", db.get_button_emoji("profile"), style="primary", callback_data="admin_status"),
            create_button("PiarFlow debug", db.get_button_emoji("top"), style="primary", callback_data="admin_piarflow_debug")
        ],
        [create_button("Назад", EMOJI_BACK, callback_data="menu_main")]
    ])

def admin_sponsors_keyboard() -> InlineKeyboardMarkup:
    sponsors = db.get_sponsors()
    keyboard = []
    
    for i, sponsor in enumerate(sponsors):
        s_type = sponsor.get("type", "channel")
        type_emoji = "📺" if s_type == "channel" else "📋" if s_type == "addlist" else "🔘"
        display = f"{i+1}. {type_emoji} {sponsor['button_text']}"
        keyboard.append([
            create_button(display[:50], style="primary", callback_data=f"admin_sponsor_{i}"),
            create_button("🗑", style="danger", callback_data=f"admin_sponsor_del_{i}")
        ])
    
    # ОДНА КНОПКА ДЛЯ ДОБАВЛЕНИЯ
    keyboard.append([
        create_button("➕ Добавить спонсора", db.get_button_emoji("earn"), style="success", callback_data="admin_sponsor_add")
    ])
    keyboard.append([create_button("Назад", EMOJI_BACK, callback_data="admin_panel")])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def admin_button_texts_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button(f"💰 {db.get_button_text('earn')}", db.get_button_emoji("earn"), style="primary", callback_data="admin_btn_earn")],
        [create_button(f"👥 {db.get_button_text('referrals')}", db.get_button_emoji("referrals"), style="primary", callback_data="admin_btn_referrals")],
        [create_button(f"🏆 {db.get_button_text('top')}", db.get_button_emoji("top"), style="primary", callback_data="admin_btn_top")],
        [create_button(f"👤 {db.get_button_text('profile')}", db.get_button_emoji("profile"), style="primary", callback_data="admin_btn_profile")],
        [create_button(f"💳 {db.get_button_text('withdraw')}", db.get_button_emoji("withdraw"), style="primary", callback_data="admin_btn_withdraw")],
        [create_button("Назад", EMOJI_BACK, callback_data="admin_panel")]
    ])

def admin_button_emojis_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button(f"💰 {db.get_button_text('earn')}", db.get_button_emoji("earn"), style="primary", callback_data="admin_emoji_earn")],
        [create_button(f"👥 {db.get_button_text('referrals')}", db.get_button_emoji("referrals"), style="primary", callback_data="admin_emoji_referrals")],
        [create_button(f"🏆 {db.get_button_text('top')}", db.get_button_emoji("top"), style="primary", callback_data="admin_emoji_top")],
        [create_button(f"👤 {db.get_button_text('profile')}", db.get_button_emoji("profile"), style="primary", callback_data="admin_emoji_profile")],
        [create_button(f"💳 {db.get_button_text('withdraw')}", db.get_button_emoji("withdraw"), style="primary", callback_data="admin_emoji_withdraw")],
        [create_button("Назад", EMOJI_BACK, callback_data="admin_panel")]
    ])

def admin_banner_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button("📤 Загрузить баннер", db.get_button_emoji("earn"), style="success", callback_data="banner_upload")],
        [create_button("🗑 Удалить баннер", DEFAULT_EMOJI_CHECK, style="danger", callback_data="banner_delete")],
        [create_button("Назад", EMOJI_BACK, callback_data="admin_panel")]
    ])

def admin_referral_reward_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button("Изменить награду (₽)", db.get_button_emoji("earn"), style="primary", callback_data="admin_reward_rub")],
        [create_button("Изменить награду (ТМТ)", db.get_button_emoji("earn"), style="primary", callback_data="admin_reward_manat")],
        [create_button("Назад", EMOJI_BACK, callback_data="admin_panel")]
    ])

def admin_users_keyboard(page: int = 0) -> InlineKeyboardMarkup:
    users = list(db.data["users"].values())
    per_page = 10
    total_pages = max((len(users) + per_page - 1) // per_page, 1)
    
    start = page * per_page
    end = min(start + per_page, len(users))
    current_users = users[start:end]
    
    keyboard = []
    for user in current_users:
        status = "🚫" if user.get("is_banned", False) else "✅"
        keyboard.append([
            create_button(f"{status} ID {user['id']}", style="primary", callback_data=f"admin_user_{user['id']}")
        ])
    
    nav = []
    if page > 0:
        nav.append(create_button("⬅️", style="primary", callback_data=f"admin_users_page_{page-1}"))
    nav.append(create_button(f"{page+1}/{total_pages}", callback_data="admin_users_page_info"))
    if page < total_pages - 1:
        nav.append(create_button("➡️", style="primary", callback_data=f"admin_users_page_{page+1}"))
    if nav:
        keyboard.append(nav)
    
    keyboard.append([create_button("Назад", EMOJI_BACK, callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def user_actions_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            create_button("+Баланс", db.get_button_emoji("earn"), style="success", callback_data=f"admin_user_add_balance_{user_id}"),
            create_button("-Баланс", db.get_button_emoji("earn"), style="danger", callback_data=f"admin_user_sub_balance_{user_id}")
        ],
        [
            create_button("+Рефералы", db.get_button_emoji("referrals"), style="success", callback_data=f"admin_user_add_ref_{user_id}"),
            create_button("Обнулить рефералы", db.get_button_emoji("referrals"), style="danger", callback_data=f"admin_user_reset_ref_{user_id}")
        ],
        [
            create_button("Бан", db.get_button_emoji("fire"), style="danger", callback_data=f"admin_user_ban_{user_id}"),
            create_button("Разбан", DEFAULT_EMOJI_CHECK, style="success", callback_data=f"admin_user_unban_{user_id}")
        ],
        [create_button("Назад", EMOJI_BACK, callback_data="admin_users")]
    ])

# ======================== СОСТОЯНИЯ FSM ========================
class AdminStates(StatesGroup):
    waiting_for_start_text = State()
    waiting_for_sponsor_name = State()
    waiting_for_sponsor_link = State()
    waiting_for_sponsor_channel = State()
    waiting_for_sponsor_channel_id = State()
    waiting_for_sponsor_addlist_ids = State()
    waiting_for_broadcast_text = State()
    waiting_for_balance_amount = State()
    waiting_for_referral_count = State()
    waiting_for_ban_user = State()
    waiting_for_button_text = State()
    waiting_for_button_emoji = State()
    waiting_for_banner = State()
    waiting_for_reward_rub = State()
    waiting_for_reward_manat = State()
    waiting_for_promocode = State()
    waiting_for_piarflow_test_id = State()

class WithdrawStates(StatesGroup):
    waiting_for_amount = State()
    waiting_for_phone = State()
    waiting_for_details = State()

class UserStates(StatesGroup):
    waiting_for_promo = State()

# ======================== ХЕЛПЕРЫ ========================
async def is_banner_valid() -> bool:
    banner_path = db.get_banner()
    if not banner_path:
        return False
    if not os.path.exists(banner_path):
        db.remove_banner()
        return False
    try:
        if os.path.getsize(banner_path) < 100:
            db.remove_banner()
            return False
        with open(banner_path, 'rb') as f:
            header = f.read(10)
            if header[:2] != b'\xff\xd8':
                db.remove_banner()
                return False
        return True
    except Exception:
        db.remove_banner()
        return False

async def safe_edit_or_send(
    callback_message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup = None,
    entities: List[MessageEntity] = None
):
    safe_entities = strip_custom_emoji_entities(entities)
    
    if callback_message.photo:
        if len(text) <= 1024:
            try:
                await callback_message.edit_caption(
                    caption=text,
                    caption_entities=entities,
                    reply_markup=reply_markup
                )
                return
            except Exception as e:
                logger.error(f"Ошибка edit_caption: {e}")
                try:
                    await callback_message.edit_caption(
                        caption=text,
                        caption_entities=safe_entities,
                        reply_markup=reply_markup
                    )
                    return
                except Exception as e2:
                    logger.error(f"Ошибка edit_caption (fallback): {e2}")
        
        chat_id = callback_message.chat.id
        try:
            await callback_message.delete()
        except Exception:
            pass
        
        try:
            await bot.send_message(
                chat_id,
                text,
                entities=entities,
                reply_markup=reply_markup
            )
        except Exception:
            await bot.send_message(
                chat_id,
                text,
                entities=safe_entities,
                reply_markup=reply_markup
            )
    else:
        try:
            await callback_message.edit_text(
                text,
                entities=entities,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Ошибка edit_text: {e}")
            try:
                await callback_message.edit_text(
                    text,
                    entities=safe_entities,
                    reply_markup=reply_markup
                )
            except Exception:
                try:
                    await bot.send_message(
                        callback_message.chat.id,
                        text,
                        entities=entities,
                        reply_markup=reply_markup
                    )
                except Exception:
                    await bot.send_message(
                        callback_message.chat.id,
                        text,
                        entities=safe_entities,
                        reply_markup=reply_markup
                    )

async def send_main_menu(target_message: Message, edit: bool = False):
    start_text = db.get_start_text()
    reward_rub = db.get_referral_reward_rub()
    reward_manat = db.get_referral_reward_manat()
    
    # Используем format_with_emoji для применения парсинга
    clean_text, entities = format_with_emoji(
        start_text,
        EMOJI_STAR=db.get_button_emoji("profile"),
        EMOJI_COINS=db.get_button_emoji("earn"),
        REFERRAL_REWARD_RUB=reward_rub,
        REFERRAL_REWARD_MANAT=reward_manat
    )
    
    kb = main_menu_keyboard()
    
    if await is_banner_valid():
        try:
            photo = FSInputFile(db.get_banner())
            if edit:
                await safe_edit_or_send(target_message, clean_text, kb, entities)
            else:
                try:
                    await target_message.answer_photo(
                        photo=photo,
                        caption=clean_text,
                        caption_entities=entities if entities else None,
                        reply_markup=kb
                    )
                except Exception as e:
                    logger.error(f"Ошибка answer_photo с entities: {e}")
                    safe_entities = strip_custom_emoji_entities(entities)
                    if safe_entities is not None:
                        await target_message.answer_photo(
                            photo=photo,
                            caption=clean_text,
                            caption_entities=safe_entities,
                            reply_markup=kb
                        )
                    else:
                        raise
            return
        except Exception as e:
            logger.error(f"Ошибка отправки баннера: {e}")
            db.remove_banner()
    
    if edit:
        await safe_edit_or_send(target_message, clean_text, kb, entities)
    else:
        try:
            await target_message.answer(clean_text, entities=entities if entities else None, reply_markup=kb)
        except Exception as e:
            logger.error(f"Ошибка answer с entities: {e}")
            safe_entities = strip_custom_emoji_entities(entities)
            if safe_entities is not None:
                await target_message.answer(clean_text, entities=safe_entities, reply_markup=kb)

async def send_sponsors_gate(target_message: Message, user_id: int, chat_id: int, edit: bool = False):
    piar_sponsors = await fetch_piar_sponsors(user_id, chat_id)
    manual_sponsors = db.get_sponsors()
    logger.info(f"[sponsors_gate] user_id={user_id} manual={len(manual_sponsors)} piar={len(piar_sponsors)}")

    _shown_piar_links[user_id] = [s.get("link") for s in piar_sponsors if s.get("link")]

    raw_text = f'<emoji id="{EMOJI_LOCK}">🔒</emoji> Для доступа к боту подпишитесь на все каналы ниже, затем нажмите «Проверить».'
    clean_text, entities = parse_premium_emoji(raw_text)
    kb = create_sponsor_keyboard(piar_sponsors)
    
    if edit:
        await safe_edit_or_send(target_message, clean_text, kb, entities)
    else:
        try:
            await target_message.answer(clean_text, entities=entities if entities else None, reply_markup=kb)
        except Exception as e:
            logger.error(f"Ошибка answer с entities в send_sponsors_gate: {e}")
            safe_entities = strip_custom_emoji_entities(entities)
            if safe_entities is not None:
                await target_message.answer(clean_text, entities=safe_entities, reply_markup=kb)
            else:
                await target_message.answer(clean_text, reply_markup=kb)

async def user_needs_gate(user_id: int, chat_id: int) -> bool:
    if db.is_verified(user_id):
        return False
    if db.get_sponsors():
        return True
    piar_sponsors = await fetch_piar_sponsors(user_id, chat_id)
    return bool(piar_sponsors)

async def try_confirm_referral(user_id: int):
    referrer_id = db.confirm_referral_reward(user_id)
    if referrer_id is None:
        return
    try:
        reward_rub = db.get_referral_reward_rub()
        reward_manat = db.get_referral_reward_manat()
        clean_ref_text, ref_entities = parse_premium_emoji(
            f'🎉 Новый реферал!\n<emoji id="{db.get_button_emoji("profile")}">⭐</emoji> '
            f'Вы получили {reward_rub}₽ / {reward_manat}ТМТ'
        )
        try:
            await bot.send_message(
                referrer_id, clean_ref_text,
                entities=ref_entities if ref_entities else None
            )
        except Exception as e:
            logger.error(f"Ошибка отправки реферального уведомления с entities: {e}")
            safe_entities = strip_custom_emoji_entities(ref_entities)
            if safe_entities is not None:
                await bot.send_message(referrer_id, clean_ref_text, entities=safe_entities)
            else:
                await bot.send_message(referrer_id, clean_ref_text)
    except Exception:
        pass

# ======================== ФОНОВЫЕ ЗАДАЧИ ========================
async def check_inactive_users():
    while True:
        try:
            inactive_users = db.get_inactive_users(INACTIVE_DAYS)
            for user_id in inactive_users:
                try:
                    await bot.send_message(
                        user_id,
                        f"⏰ <b>Вы давно не заходили!</b>\n\n"
                        f"У нас появились новые задания и промокоды. "
                        f"Заходите, чтобы заработать! 🚀",
                        parse_mode="HTML"
                    )
                    db.update_activity(user_id)
                    await asyncio.sleep(0.5)
                except Exception:
                    pass
            await asyncio.sleep(3600 * 12)
        except Exception as e:
            logger.error(f"Ошибка в check_inactive_users: {e}")
            await asyncio.sleep(60)

# ======================== ОБРАБОТЧИКИ КОМАНД ========================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    args = message.text.split()
    
    if db.is_banned(user_id):
        await message.answer("🚫 Вы забанены в этом боте!")
        return
    
    if db.is_bot_stopped():
        await message.answer("⏸ Бот временно не работает. Попробуйте позже.")
        return
    
    db.get_user(user_id)
    db.update_activity(user_id)
    
    if len(args) > 1 and args[1].startswith("ref_"):
        ref_part = args[1][4:]
        if ref_part.isdigit():
            referrer_id = int(ref_part)
            if referrer_id != user_id and str(referrer_id) in db.data["users"]:
                db.link_referral(referrer_id, user_id)
    
    needs_gate = await user_needs_gate(user_id, message.chat.id)
    if needs_gate:
        await send_sponsors_gate(message, user_id, message.chat.id)
        return
    
    await try_confirm_referral(user_id)
    await send_main_menu(message)

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    user_id = message.from_user.id
    if not db.is_admin(user_id):
        await message.answer("⛔ У вас нет доступа к админ-панели.")
        return
    await message.answer("🛠 Админ панель", reply_markup=admin_panel_keyboard())

# ======================== ПРОВЕРКА ПОДПИСКИ ========================
@dp.callback_query(F.data == "verify_sponsors")
async def verify_sponsors(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return
    
    ok = await check_all_subscriptions(user_id, callback.message.chat.id, use_shown_links=True)
    
    if ok:
        db.set_verified(user_id, True)
        _shown_piar_links.pop(user_id, None)
        await try_confirm_referral(user_id)
        await callback.answer("✅ Подписка подтверждена!")
        await send_main_menu(callback.message, edit=True)
    else:
        await callback.answer(
            "❌ Вы подписаны не на все каналы!\nПроверьте подписки и нажмите снова.",
            show_alert=True
        )

@dp.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    await callback.answer()

# ======================== ГЛАВНОЕ МЕНЮ ========================
@dp.callback_query(F.data == "menu_main")
async def menu_main(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return
    
    db.update_activity(user_id)
    
    if await user_needs_gate(user_id, callback.message.chat.id):
        await send_sponsors_gate(callback.message, user_id, callback.message.chat.id, edit=True)
        await callback.answer()
        return
    
    await send_main_menu(callback.message, edit=True)
    await callback.answer()

# ======================== ЗАРАБОТОК ========================
@dp.callback_query(F.data == "menu_earn")
async def menu_earn(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return
    
    db.update_activity(user_id)
    
    if await user_needs_gate(user_id, callback.message.chat.id):
        await send_sponsors_gate(callback.message, user_id, callback.message.chat.id, edit=True)
        await callback.answer()
        return
    
    tasks_left = db.get_tasks_left_today(user_id)
    
    if tasks_left <= 0:
        raw_text = (
            f'<emoji id="{EMOJI_TASK_HOURGLASS}">⏳</emoji> <b>Заработок на сегодня</b>\n\n'
            f'Вы выполнили максимум заданий на сегодня (<b>{MAX_TASKS_PER_DAY}/{MAX_TASKS_PER_DAY}</b>).\n'
            f'Новые задания появятся после полуночи.'
        )
        clean_text, entities = parse_premium_emoji(raw_text)
        await safe_edit_or_send(callback.message, clean_text, earn_keyboard(has_offer=False), entities)
        await callback.answer()
        return
    
    sponsors = db.get_sponsors()
    
    task_sponsor = None
    for sponsor in sponsors:
        channel = sponsor.get("channel_id")
        if channel:
            is_subscribed = await check_channel_subscription(user_id, channel)
            if not is_subscribed:
                task_sponsor = sponsor
                break
    
    if not task_sponsor:
        if sponsors:
            raw_text = (
                f'<emoji id="{EMOJI_TASK_DONE_ALL}">✅</emoji> <b>Все задания выполнены!</b>\n\n'
                f'Вы подписаны на все каналы.\n'
                f'Осталось заданий сегодня: <b>{tasks_left}/{MAX_TASKS_PER_DAY}</b>'
            )
            clean_text, entities = parse_premium_emoji(raw_text)
            await safe_edit_or_send(callback.message, clean_text, earn_keyboard(has_offer=False), entities)
            await callback.answer()
            return
        
        raw_text = (
            '😔 <b>Сейчас нет доступных заданий</b>\n\n'
            'Попробуйте позже — новые задания появляются регулярно.'
        )
        clean_text, entities = parse_premium_emoji(raw_text)
        await safe_edit_or_send(callback.message, clean_text, earn_keyboard(has_offer=False), entities)
        await callback.answer()
        return
    
    link = task_sponsor.get("link", "")
    title = task_sponsor.get("button_text", "Задание")
    
    raw_text = (
        f'<emoji id="{EMOJI_TASK_MONEY}">💰</emoji> <b>Заработок</b>\n\n'
        f'<quote>📋 Задание: {title}\n'
        f'💵 Награда: {TASK_REWARD_RUB:.2f}₽ / {TASK_REWARD_MANAT:.2f}ТМТ\n'
        f'📊 Осталось заданий сегодня: {tasks_left}/{MAX_TASKS_PER_DAY}</quote>\n\n'
        f'Подпишитесь по кнопке ниже, затем нажмите «Проверить выполнение».'
    )
    clean_text, entities = parse_premium_emoji(raw_text)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [create_button("Подписаться", DEFAULT_EMOJI_CHECK, "✅", style="primary", url=link)],
        [create_button("Проверить выполнение", db.get_button_emoji("profile"), "🔄", style="success", callback_data="check_task")],
        [create_button("Назад", EMOJI_BACK, callback_data="menu_main")]
    ])
    
    await safe_edit_or_send(callback.message, clean_text, keyboard, entities)
    await callback.answer()

@dp.callback_query(F.data == "check_task")
async def check_task(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return
    
    tasks_left = db.get_tasks_left_today(user_id)
    if tasks_left <= 0:
        await callback.answer("Лимит заданий на сегодня исчерпан.", show_alert=True)
        return
    
    sponsors = db.get_sponsors()
    all_subscribed = True
    failed = []
    
    for sponsor in sponsors:
        channel = sponsor.get("channel_id")
        if channel:
            if not await check_channel_subscription(user_id, channel):
                all_subscribed = False
                failed.append(sponsor.get("button_text"))
    
    if all_subscribed:
        counted = db.register_task_completed(user_id)
        if counted:
            new_left = db.get_tasks_left_today(user_id)
            raw_text = (
                f'<emoji id="{EMOJI_TASK_SUCCESS}">✅</emoji> <b>Задание выполнено!</b>\n\n'
                f'<quote>💵 Начислено: {TASK_REWARD_RUB:.2f}₽ / {TASK_REWARD_MANAT:.2f}ТМТ\n'
                f'Осталось заданий сегодня: {new_left}/{MAX_TASKS_PER_DAY}</quote>'
            )
            clean_text, entities = parse_premium_emoji(raw_text)
            await safe_edit_or_send(callback.message, clean_text, earn_keyboard(has_offer=new_left > 0), entities)
            await callback.answer("✅ Награда начислена!")
        else:
            await callback.answer("Лимит заданий на сегодня исчерпан.", show_alert=True)
    else:
        failed_text = "\n".join(f"• {f}" for f in failed)
        await callback.answer(f"❌ Вы подписаны не на все каналы:\n{failed_text}", show_alert=True)

# ======================== РЕФЕРАЛЫ ========================
@dp.callback_query(F.data == "menu_referrals")
async def menu_referrals(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return
    
    db.update_activity(user_id)
    
    if await user_needs_gate(user_id, callback.message.chat.id):
        await send_sponsors_gate(callback.message, user_id, callback.message.chat.id, edit=True)
        await callback.answer()
        return
    
    user = db.get_user(user_id)
    count = user["referral_count"]
    reward_rub = db.get_referral_reward_rub()
    reward_manat = db.get_referral_reward_manat()
    bot_username = bot.username if hasattr(bot, 'username') and bot.username else "Earn_TMTRublBot"
    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    
    raw_text = f"""<emoji id="{db.get_button_emoji('referrals')}">🔥</emoji> <b>Реферальная система</b>

<emoji id="{db.get_button_emoji('profile')}">⭐</emoji> Ваш ID: <b>{user_id}</b>
Ваша ссылка (нажмите, чтобы скопировать):
<code>{ref_link}</code>

<quote>💰 Приглашено: {count} чел.
🚀 Заработано: {count * reward_rub:.2f}₽ / {count * reward_manat:.2f}ТМТ</quote>

Поделитесь ссылкой с друзьями и получайте бонусы!"""
    
    clean_text, entities = parse_premium_emoji(raw_text)
    await safe_edit_or_send(callback.message, clean_text, referral_keyboard(user_id), entities)
    await callback.answer()

@dp.callback_query(F.data == "referrals_list")
async def referrals_list(callback: CallbackQuery):
    user_id = callback.from_user.id
    referrals = db.get_referrals(user_id)
    
    if not referrals:
        await callback.answer("У вас пока нет рефералов", show_alert=True)
        return
    
    text = "📋 Мои рефералы:\n\n"
    for i, ref_id in enumerate(referrals, 1):
        try:
            chat = await bot.get_chat(ref_id)
            name = chat.full_name or chat.username or str(ref_id)
            text += f"{i}. {name}\n"
        except Exception:
            text += f"{i}. {ref_id}\n"
    text += f"\nВсего: {len(referrals)}"
    
    await safe_edit_or_send(callback.message, text, back_keyboard("menu_referrals"))
    await callback.answer()

@dp.callback_query(F.data == "referrals_stats")
async def referrals_stats(callback: CallbackQuery):
    user_id = callback.from_user.id
    stats = db.get_referral_stats(user_id)
    
    text = (
        f"📊 <b>Статистика рефералов</b>\n\n"
        f"Всего приглашено: <b>{stats['total']}</b>\n"
        f"Активных (выполняют задания): <b>{stats['active']}</b>\n"
        f"Неактивных: <b>{stats['inactive']}</b>\n"
    )
    
    await safe_edit_or_send(callback.message, text, back_keyboard("menu_referrals"))
    await callback.answer()

# ======================== ТОП ========================
@dp.callback_query(F.data == "menu_top")
async def menu_top(callback: CallbackQuery):
    user_id = callback.from_user.id
    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return
    
    db.update_activity(user_id)
    
    if await user_needs_gate(user_id, callback.message.chat.id):
        await send_sponsors_gate(callback.message, user_id, callback.message.chat.id, edit=True)
        await callback.answer()
        return
    
    top = db.get_top_referrals(10)
    text = "🏆 Топ рефералов:\n\n"
    
    if not top:
        text += "Пока нет лидеров. Будьте первым!"
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, (uid, count) in enumerate(top, 1):
            medal = medals[i-1] if i <= 3 else f"{i}."
            try:
                chat = await bot.get_chat(uid)
                name = chat.full_name or chat.username or str(uid)
            except Exception:
                name = str(uid)
            text += f"{medal} {name[:20]} — {count} рефералов\n"
    
    await safe_edit_or_send(callback.message, text, back_keyboard("menu_main"))
    await callback.answer()

# ======================== ПРОФИЛЬ ========================
@dp.callback_query(F.data == "menu_profile")
async def menu_profile(callback: CallbackQuery):
    user_id = callback.from_user.id
    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return
    
    db.update_activity(user_id)
    
    if await user_needs_gate(user_id, callback.message.chat.id):
        await send_sponsors_gate(callback.message, user_id, callback.message.chat.id, edit=True)
        await callback.answer()
        return
    
    user = db.get_user(user_id)
    tasks_left = db.get_tasks_left_today(user_id)
    
    raw_text = f"""<emoji id="{db.get_button_emoji('profile')}">⭐</emoji> <b>Профиль</b>

👤 ID: <b>{user_id}</b>

<quote>💰 Баланс:
• {user["balance_rub"]:.2f} ₽
• {user["balance_manat"]:.2f} ТМТ</quote>

<emoji id="{db.get_button_emoji('referrals')}">🔥</emoji> Рефералов: <b>{user["referral_count"]}</b>
<emoji id="{db.get_button_emoji('top')}">🚀</emoji> Заданий выполнено всего: {user["tasks_completed"]}
📊 Заданий сегодня: {MAX_TASKS_PER_DAY - tasks_left}/{MAX_TASKS_PER_DAY}"""
    
    clean_text, entities = parse_premium_emoji(raw_text)
    await safe_edit_or_send(callback.message, clean_text, profile_keyboard(), entities)
    await callback.answer()

# ======================== ИСТОРИЯ ТРАНЗАКЦИЙ ========================
@dp.callback_query(F.data == "menu_history")
async def menu_history(callback: CallbackQuery):
    user_id = callback.from_user.id
    transactions = db.get_transactions(user_id, 20)
    
    if not transactions:
        text = "📜 История транзакций пуста."
    else:
        text = "📜 <b>Последние транзакции</b>:\n\n"
        for t in reversed(transactions):
            emoji = "💰" if t["type"] in ["earn", "promo"] else "💸" if t["type"] == "withdraw" else "📊"
            sign = "+" if t["amount"] > 0 else ""
            text += f"{emoji} {t['date'][:10]} {sign}{t['amount']:.2f}₽ — {t.get('description', t['type'])}\n"
    
    await safe_edit_or_send(callback.message, text, back_keyboard("menu_profile"))
    await callback.answer()

# ======================== ПРОМОКОДЫ ========================
@dp.callback_query(F.data == "menu_promo")
async def menu_promo(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    
    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return
    
    db.update_activity(user_id)
    
    if await user_needs_gate(user_id, callback.message.chat.id):
        await send_sponsors_gate(callback.message, user_id, callback.message.chat.id, edit=True)
        await callback.answer()
        return
    
    await state.set_state(UserStates.waiting_for_promo)
    await safe_edit_or_send(
        callback.message,
        "🎫 Введите промокод:",
        back_keyboard("menu_main")
    )
    await callback.answer()

@dp.message(UserStates.waiting_for_promo)
async def process_promo_code(message: Message, state: FSMContext):
    user_id = message.from_user.id
    code = message.text.strip()
    reward = db.use_promocode(user_id, code)
    
    if reward is None:
        await message.answer("❌ Неверный или уже использованный промокод!")
    else:
        await message.answer(f"✅ Промокод активирован! Вы получили {reward:.2f}₽ / {reward * RATE_MANAT:.2f}ТМТ")
    
    await state.clear()

# ======================== ВЫВОД СРЕДСТВ ========================
@dp.callback_query(F.data == "menu_withdraw")
async def menu_withdraw(callback: CallbackQuery):
    user_id = callback.from_user.id
    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return
    
    db.update_activity(user_id)
    
    if await user_needs_gate(user_id, callback.message.chat.id):
        await send_sponsors_gate(callback.message, user_id, callback.message.chat.id, edit=True)
        await callback.answer()
        return
    
    user = db.get_user(user_id)
    
    raw_text = f"""<emoji id="{db.get_button_emoji('earn')}">💰</emoji> <b>Вывод средств</b>

<quote>⭐ Ваш баланс:
• {user["balance_rub"]:.2f} ₽
• {user["balance_manat"]:.2f} ТМТ</quote>

Минимальная сумма вывода:
• <b>{MIN_WITHDRAW_RUB:.0f} ₽</b>
• <b>{MIN_WITHDRAW_MANAT:.1f} ТМТ</b>

Выберите валюту вывода:"""
    
    clean_text, entities = parse_premium_emoji(raw_text)
    await safe_edit_or_send(callback.message, clean_text, withdraw_keyboard(), entities)
    await callback.answer()

@dp.callback_query(F.data.startswith("withdraw_"))
async def withdraw_request(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    currency = callback.data.split("_")[1]
    user = db.get_user(user_id)
    
    if currency == "rub":
        if user["balance_rub"] < MIN_WITHDRAW_RUB:
            await callback.answer(f"❌ Недостаточно средств. Минимум {MIN_WITHDRAW_RUB}₽", show_alert=True)
            return
        await state.update_data(withdraw_currency="rub")
        await state.set_state(WithdrawStates.waiting_for_amount)
        await safe_edit_or_send(
            callback.message,
            f"💳 Вывод в ₽\n\nВаш баланс: {user['balance_rub']:.2f}₽\nМинимум: {MIN_WITHDRAW_RUB}₽\n\n"
            f"Введите сумму вывода:\n\n<i>После ввода суммы укажите реквизиты для перевода (номер карты/кошелька)</i>",
            back_keyboard("menu_withdraw")
        )
    elif currency == "manat":
        if user["balance_manat"] < MIN_WITHDRAW_MANAT:
            await callback.answer(f"❌ Недостаточно средств. Минимум {MIN_WITHDRAW_MANAT:.1f}ТМТ", show_alert=True)
            return
        await state.update_data(withdraw_currency="manat")
        await state.set_state(WithdrawStates.waiting_for_amount)
        await safe_edit_or_send(
            callback.message,
            f"💳 Вывод в ТМТ\n\nВаш баланс: {user['balance_manat']:.2f}ТМТ\nМинимум: {MIN_WITHDRAW_MANAT:.1f}ТМТ\n\n"
            f"Введите сумму вывода:\n\n<i>После ввода суммы укажите номер телефона в формате +993XXXXXXXXX</i>",
            back_keyboard("menu_withdraw")
        )
    
    await callback.answer()

@dp.message(WithdrawStates.waiting_for_amount)
async def process_withdraw_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0:
            await message.answer("❌ Сумма должна быть положительной.")
            return
    except ValueError:
        await message.answer("❌ Введите корректное число.")
        return
    
    await state.update_data(amount=amount)
    data = await state.get_data()
    currency = data.get("withdraw_currency")
    
    if currency == "rub":
        await state.set_state(WithdrawStates.waiting_for_details)
        await message.answer("💳 Введите реквизиты для перевода (номер карты или кошелька):")
    else:
        await state.set_state(WithdrawStates.waiting_for_phone)
        await message.answer("📱 Введите номер телефона в формате +993XXXXXXXXX:")

@dp.message(WithdrawStates.waiting_for_phone)
async def process_withdraw_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    if not phone.startswith("+993") or len(phone) < 13:
        await message.answer("❌ Неверный формат! Введите номер в формате +993XXXXXXXXX")
        return
    
    await state.update_data(details=phone)
    await finalize_withdrawal(message, state)

@dp.message(WithdrawStates.waiting_for_details)
async def process_withdraw_details(message: Message, state: FSMContext):
    details = message.text.strip()
    if len(details) < 5:
        await message.answer("❌ Введите корректные реквизиты (минимум 5 символов)")
        return
    
    await state.update_data(details=details)
    await finalize_withdrawal(message, state)

async def finalize_withdrawal(message: Message, state: FSMContext):
    data = await state.get_data()
    amount = data.get("amount")
    currency = data.get("withdraw_currency")
    details = data.get("details")
    
    user_id = message.from_user.id
    user = db.get_user(user_id)
    
    if currency == "rub":
        if amount > user["balance_rub"]:
            await message.answer(f"❌ Недостаточно средств. Ваш баланс: {user['balance_rub']:.2f}₽")
            return
        if db.deduct_balance(user_id, amount):
            withdrawal_id = db.create_withdrawal(user_id, amount, "rub", amount, details)
            db.add_transaction(user_id, "withdraw", -amount, f"Вывод {amount:.2f}₽")
            await send_withdrawal_check(withdrawal_id, user_id, amount, "rub", details)
            await message.answer(
                f"✅ Заявка на вывод {amount:.2f}₽ принята!\nРеквизиты: {details}\n\nОжидайте обработки в течение 1-12 часов.",
                reply_markup=main_menu_keyboard()
            )
    else:
        if amount > user["balance_manat"]:
            await message.answer(f"❌ Недостаточно средств. Ваш баланс: {user['balance_manat']:.2f}ТМТ")
            return
        amount_rub = amount / RATE_MANAT
        if db.deduct_balance(user_id, amount_rub):
            withdrawal_id = db.create_withdrawal(user_id, amount, "manat", amount_rub, details)
            db.add_transaction(user_id, "withdraw", -amount, f"Вывод {amount:.2f}ТМТ")
            await send_withdrawal_check(withdrawal_id, user_id, amount, "manat", details)
            await message.answer(
                f"✅ Заявка на вывод {amount:.2f}ТМТ принята!\nТелефон: {details}\n\nОжидайте обработки в течение 1-12 часов.",
                reply_markup=main_menu_keyboard()
            )
    
    await state.clear()

async def send_withdrawal_check(withdrawal_id: int, user_id: int, amount_display: float, currency: str, details: str):
    currency_label = "₽" if currency == "rub" else "ТМТ"
    try:
        user_chat = await bot.get_chat(user_id)
        username_part = f" (@{user_chat.username})" if user_chat.username else ""
    except Exception:
        username_part = ""
    
    raw_text = (
        f'<emoji id="{EMOJI_TASK_MONEY}">💵</emoji> <b>Новая заявка на вывод</b>\n\n'
        f'<quote>Заявка №{withdrawal_id}\n'
        f'Пользователь: {user_id}{username_part}\n'
        f'Сумма: {amount_display:.2f} {currency_label}\n'
        f'Реквизиты: {details}</quote>\n\n'
        f'Статус: ⏳ Ожидает обработки'
    )
    clean_text, entities = parse_premium_emoji(raw_text)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            create_button("Отклонить", DEFAULT_EMOJI_CHECK, "❌", style="danger",
                          callback_data=f"wd_reject_{withdrawal_id}"),
            create_button("Выплачено", DEFAULT_EMOJI_CHECK, "✅", style="success",
                          callback_data=f"wd_paid_{withdrawal_id}")
        ]
    ])
    
    try:
        sent = await bot.send_message(
            PAYMENTS_CHANNEL, clean_text,
            entities=entities if entities else None,
            reply_markup=keyboard
        )
        db.set_withdrawal_channel_message(withdrawal_id, sent.message_id)
    except Exception as e:
        logger.error(f"Не удалось отправить чек на вывод в канал {PAYMENTS_CHANNEL}: {e}")

# ======================== ОБРАБОТЧИКИ ВЫВОДОВ (АДМИН) ========================
@dp.callback_query(F.data.startswith("wd_paid_") | F.data.startswith("wd_reject_"))
async def handle_withdrawal_action(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Только администратор может это сделать!", show_alert=True)
        return
    
    is_paid = callback.data.startswith("wd_paid_")
    withdrawal_id = int(callback.data.split("_")[2])
    
    withdrawal = db.get_withdrawal(withdrawal_id)
    if not withdrawal:
        await callback.answer("❌ Заявка не найдена!", show_alert=True)
        return
    
    new_status = "paid" if is_paid else "rejected"
    changed = db.set_withdrawal_status(withdrawal_id, new_status)
    
    if not changed:
        await callback.answer("⚠️ Эта заявка уже обработана!", show_alert=True)
        return
    
    withdrawal = db.get_withdrawal(withdrawal_id)
    currency_label = "₽" if withdrawal["currency"] == "rub" else "ТМТ"
    user_id = withdrawal["user_id"]
    amount_display = withdrawal["amount_display"]
    details = withdrawal.get("details", "Не указаны")
    
    try:
        user_chat = await bot.get_chat(user_id)
        username_part = f" (@{user_chat.username})" if user_chat.username else ""
    except Exception:
        username_part = ""
    
    if is_paid:
        status_line = "✅ Выплачено"
        user_notify_text = (
            f'<emoji id="{EMOJI_TASK_SUCCESS}">✅</emoji> <b>Ваша заявка на вывод выплачена!</b>\n\n'
            f'Сумма: {amount_display:.2f} {currency_label}\n'
            f'Реквизиты: {details}\n'
            f'Спасибо, что пользуетесь ботом!'
        )
    else:
        status_line = "❌ Отклонено"
        user_notify_text = (
            f'❌ <b>Ваша заявка на вывод отклонена</b>\n\n'
            f'Сумма {amount_display:.2f} {currency_label} возвращена на ваш баланс.\n'
            f'Если это ошибка — свяжитесь с поддержкой.'
        )
    
    updated_raw_text = (
        f'<emoji id="{EMOJI_TASK_MONEY}">💵</emoji> <b>Заявка на вывод №{withdrawal_id}</b>\n\n'
        f'<quote>Пользователь: {user_id}{username_part}\n'
        f'Сумма: {amount_display:.2f} {currency_label}\n'
        f'Реквизиты: {details}</quote>\n\n'
        f'Статус: {status_line}\n'
        f'<emoji id="{EMOJI_ANON}">🕵️</emoji> Обработал: Аноним'
    )
    updated_clean, updated_entities = parse_premium_emoji(updated_raw_text)
    
    try:
        await callback.message.edit_text(
            updated_clean,
            entities=updated_entities if updated_entities else None,
            reply_markup=None
        )
    except Exception as e:
        logger.error(f"Ошибка edit_text чека в канале: {e}")
        safe_entities = strip_custom_emoji_entities(updated_entities)
        try:
            await callback.message.edit_text(
                updated_clean,
                entities=safe_entities,
                reply_markup=None
            )
        except Exception as e2:
            logger.error(f"Ошибка edit_text чека без custom_emoji: {e2}")
            try:
                await callback.message.edit_text(updated_clean, reply_markup=None)
            except Exception as e3:
                logger.error(f"Финальная попытка edit_text тоже упала: {e3}")
    
    try:
        clean_notify, notify_entities = parse_premium_emoji(user_notify_text)
        try:
            await bot.send_message(user_id, clean_notify, entities=notify_entities if notify_entities else None)
        except Exception as e:
            logger.error(f"Ошибка уведомления пользователя о статусе выплаты: {e}")
            safe_entities = strip_custom_emoji_entities(notify_entities)
            if safe_entities is not None:
                await bot.send_message(user_id, clean_notify, entities=safe_entities)
            else:
                await bot.send_message(user_id, clean_notify)
    except Exception:
        pass
    
    await callback.answer("✅ Готово!" if is_paid else "❌ Заявка отклонена")

# ======================== АДМИН-ПАНЕЛЬ ========================
@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    await safe_edit_or_send(
        callback.message,
        "🛠 <b>Админ-панель</b>\n\nВыберите нужный раздел для управления ботом:",
        admin_panel_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    users = db.data.get("users", {})
    total_users = len(users)
    banned_users = sum(1 for u in users.values() if u.get("is_banned"))
    total_rub = sum(u.get("balance_rub", 0.0) for u in users.values())
    total_manat = sum(u.get("balance_manat", 0.0) for u in users.values())
    stats = db.data.get("statistics", {})
    
    threshold = datetime.now() - timedelta(days=7)
    active_users = 0
    for user in users.values():
        last_activity = user.get("last_activity")
        if last_activity:
            try:
                last_date = datetime.fromisoformat(last_activity)
                if last_date > threshold:
                    active_users += 1
            except:
                pass
    
    text = (
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"✅ Активных (7 дней): <b>{active_users}</b>\n"
        f"🚫 Заблокировано: <b>{banned_users}</b>\n"
        f"👥 Всего рефералов: <b>{stats.get('total_referrals', 0)}</b>\n\n"
        f"💰 Суммарный баланс пользователей:\n"
        f"• <b>{total_rub:.2f} ₽</b>\n"
        f"• <b>{total_manat:.2f} ТМТ</b>\n\n"
        f"💳 Всего выплачено: <b>{stats.get('total_withdrawn', 0.0):.2f} ₽</b>\n"
        f"💰 Всего заработано: <b>{stats.get('total_earned', 0.0):.2f} ₽</b>"
    )
    
    clean_text, entities = parse_premium_emoji(text)
    await safe_edit_or_send(callback.message, clean_text, back_keyboard("admin_panel"), entities)
    await callback.answer()

@dp.callback_query(F.data == "admin_start_text")
async def admin_start_text(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    current_text = db.get_start_text()
    # Для отображения в админ-панели показываем исходный текст без парсинга
    text = (
        f"📝 <b>Текущий текст приветствия (/start):</b>\n\n"
        f"<code>{current_text}</code>\n\n"
        f"Доступные переменные:\n"
        f"• <code>{{EMOJI_STAR}}</code>\n"
        f"• <code>{{EMOJI_COINS}}</code>\n"
        f"• <code>{{REFERRAL_REWARD_RUB}}</code>\n"
        f"• <code>{{REFERRAL_REWARD_MANAT}}</code>\n\n"
        f"Доступные теги:\n"
        f"• <code>&lt;emoji id=\"ID\"&gt;текст&lt;/emoji&gt;</code> - премиум эмодзи\n"
        f"• <code>&lt;pole&gt;текст&lt;/pole&gt;</code> - цитата\n"
        f"• <code>&lt;zhirnyy&gt;текст&lt;/zhirnyy&gt;</code> - жирный внутри цитаты\n"
        f"• <code>**текст**</code> или <code>&lt;b&gt;текст&lt;/b&gt;</code> - жирный\n"
        f"• <code>&lt;i&gt;текст&lt;/i&gt;</code> - курсив\n"
        f"• <code>&lt;code&gt;текст&lt;/code&gt;</code> - код\n\n"
        f"<b>Введите новый текст (сохраняется как есть, без парсинга):</b>"
    )
    await state.set_state(AdminStates.waiting_for_start_text)
    await safe_edit_or_send(callback.message, text, back_keyboard("admin_panel"))
    await callback.answer()

@dp.message(AdminStates.waiting_for_start_text)
async def process_start_text(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    
    new_text = message.text  # Сохраняем как есть, без парсинга
    
    # Проверяем только переменные форматирования
    try:
        new_text.format(
            EMOJI_STAR="",
            EMOJI_COINS="",
            REFERRAL_REWARD_RUB=0,
            REFERRAL_REWARD_MANAT=0
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка в переменных форматирования: {e}\nПопробуйте ещё раз.")
        return
    
    db.set_start_text(new_text)  # Сохраняем исходный текст
    await state.clear()
    await message.answer("✅ Текст старта успешно обновлён!", reply_markup=back_keyboard("admin_panel"))

# ======================== АДМИН-СПОНСОРЫ (УПРОЩЁННЫЙ) ========================
@dp.callback_query(F.data == "admin_sponsors")
async def admin_sponsors(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    await safe_edit_or_send(
        callback.message,
        "📺 <b>Управление спонсорами и заданиями</b>\n\nНиже список текущих обязательных каналов:",
        admin_sponsors_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_sponsor_del_"))
async def admin_sponsor_del(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    idx = int(callback.data.replace("admin_sponsor_del_", ""))
    db.remove_sponsor(idx)
    await callback.answer("🗑 Спонсор удалён!", show_alert=True)
    await safe_edit_or_send(
        callback.message,
        "📺 <b>Управление спонсорами и заданиями</b>\n\nНиже список текущих обязательных каналов:",
        admin_sponsors_keyboard()
    )

@dp.callback_query(F.data == "admin_sponsor_add")
async def admin_sponsor_add(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    await state.set_state(AdminStates.waiting_for_sponsor_name)
    await safe_edit_or_send(
        callback.message,
        "📝 <b>Добавление спонсора</b>\n\n"
        "Шаг 1/2: Введите название кнопки спонсора:",
        back_keyboard("admin_sponsors")
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_sponsor_name)
async def process_sponsor_name_simple(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    await state.update_data(sponsor_name=message.text.strip())
    await state.set_state(AdminStates.waiting_for_sponsor_link)
    await message.answer(
        "Шаг 2/2: Введите ссылку на канал/чат (например: <code>https://t.me/mychannel</code>):"
    )

@dp.message(AdminStates.waiting_for_sponsor_link)
async def process_sponsor_link_simple(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    link = message.text.strip()
    button_text = data.get("sponsor_name", "Спонсор")
    
    # Добавляем спонсора без ID (только кнопка)
    db.add_sponsor(button_text, link, None, len(db.get_sponsors()), "button")
    await message.answer("✅ Спонсор успешно добавлен!")
    
    await state.clear()
    await safe_edit_or_send(
        message,
        "📺 <b>Управление спонсорами и заданиями</b>\n\nНиже список текущих обязательных каналов:",
        admin_sponsors_keyboard()
    )

# ======================== АДМИН-КНОПКИ ========================
@dp.callback_query(F.data == "admin_button_texts")
async def admin_button_texts(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    await safe_edit_or_send(
        callback.message,
        "🧩 <b>Настройка названий кнопок меню</b>\nВыберите кнопку для изменения:",
        admin_button_texts_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_btn_"))
async def admin_btn_text_edit(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    btn_key = callback.data.replace("admin_btn_", "")
    await state.update_data(target_btn_key=btn_key)
    await state.set_state(AdminStates.waiting_for_button_text)
    
    current = db.get_button_text(btn_key)
    await safe_edit_or_send(
        callback.message,
        f"📝 Введите новое название для кнопки <code>{btn_key}</code> (текущее: <code>{current}</code>):",
        back_keyboard("admin_button_texts")
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_button_text)
async def process_button_text(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    data = await state.get_data()
    btn_key = data.get("target_btn_key")
    if btn_key:
        db.set_button_text(btn_key, message.text.strip())
        await message.answer(f"✅ Название кнопки <code>{btn_key}</code> обновлено!", reply_markup=back_keyboard("admin_button_texts"))
    await state.clear()

@dp.callback_query(F.data == "admin_button_emojis")
async def admin_button_emojis(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    await safe_edit_or_send(
        callback.message,
        "🎨 <b>Настройка Премиум-эмодзи для кнопок</b>\nВыберите кнопку для смены custom_emoji_id:",
        admin_button_emojis_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_emoji_"))
async def admin_emoji_edit(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    btn_key = callback.data.replace("admin_emoji_", "")
    await state.update_data(target_emoji_key=btn_key)
    await state.set_state(AdminStates.waiting_for_button_emoji)
    
    current = db.get_button_emoji(btn_key)
    await safe_edit_or_send(
        callback.message,
        f"🎨 Введите ID Премиум-эмодзи (custom_emoji_id) для кнопки <code>{btn_key}</code>\n(текущий ID: <code>{current}</code>):",
        back_keyboard("admin_button_emojis")
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_button_emoji)
async def process_button_emoji(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    data = await state.get_data()
    btn_key = data.get("target_emoji_key")
    emoji_id = message.text.strip()
    if btn_key:
        db.set_button_emoji(btn_key, emoji_id)
        await message.answer(f"✅ Эмодзи кнопки <code>{btn_key}</code> обновлено!", reply_markup=back_keyboard("admin_button_emojis"))
    await state.clear()

# ======================== АДМИН-БАННЕР ========================
@dp.callback_query(F.data == "admin_banner")
async def admin_banner(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    banner_path = db.get_banner()
    has_banner = "Да" if banner_path and os.path.exists(banner_path) else "Нет"
    
    await safe_edit_or_send(
        callback.message,
        f"🖼 <b>Управление баннером меню</b>\n\nТекущий баннер: <b>{has_banner}</b>",
        admin_banner_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "banner_upload")
async def banner_upload(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    await state.set_state(AdminStates.waiting_for_banner)
    await safe_edit_or_send(
        callback.message,
        "📤 Отправьте изображение (фото) для установки в качестве баннера меню:",
        back_keyboard("admin_banner")
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_banner, F.photo)
async def process_banner_photo(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    
    photo = message.photo[-1]
    file_path = "banner.jpg"
    await bot.download(photo, destination=file_path)
    db.set_banner(file_path)
    
    await state.clear()
    await message.answer("✅ Баннер успешно сохранён!", reply_markup=back_keyboard("admin_banner"))

@dp.callback_query(F.data == "banner_delete")
async def banner_delete(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    db.remove_banner()
    await callback.answer("🗑 Баннер удалён!", show_alert=True)
    await safe_edit_or_send(
        callback.message,
        "🖼 <b>Управление баннером меню</b>\n\nТекущий баннер: <b>Нет</b>",
        admin_banner_keyboard()
    )

# ======================== АДМИН-РЕФЕРАЛЫ ========================
@dp.callback_query(F.data == "admin_referral_reward")
async def admin_referral_reward(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    rub = db.get_referral_reward_rub()
    manat = db.get_referral_reward_manat()
    
    text = (
        f"🎁 <b>Настройка награды за реферала</b>\n\n"
        f"Текущая награда за приглашённого пользователя:\n"
        f"• <b>{rub:.2f} ₽</b>\n"
        f"• <b>{manat:.2f} ТМТ</b>"
    )
    
    await safe_edit_or_send(callback.message, text, admin_referral_reward_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "admin_reward_rub")
async def admin_reward_rub(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    await state.set_state(AdminStates.waiting_for_reward_rub)
    await safe_edit_or_send(
        callback.message,
        "🎁 Введите новое значение награды за реферала в <b>рублях (₽)</b>:",
        back_keyboard("admin_referral_reward")
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_reward_rub)
async def process_reward_rub(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    try:
        val = float(message.text.replace(",", "."))
        if val < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректное положительное число!")
        return
    
    db.set_referral_reward_rub(val)
    await state.clear()
    await message.answer("✅ Награда за реферала обновлена!", reply_markup=back_keyboard("admin_referral_reward"))

@dp.callback_query(F.data == "admin_reward_manat")
async def admin_reward_manat(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    await state.set_state(AdminStates.waiting_for_reward_manat)
    await safe_edit_or_send(
        callback.message,
        "🎁 Введите новое значение награды за реферала в <b>манатах (ТМТ)</b>:",
        back_keyboard("admin_referral_reward")
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_reward_manat)
async def process_reward_manat(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    try:
        val = float(message.text.replace(",", "."))
        if val < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректное положительное число!")
        return
    
    db.set_referral_reward_manat(val)
    await state.clear()
    await message.answer("✅ Награда за реферала обновлена!", reply_markup=back_keyboard("admin_referral_reward"))

# ======================== АДМИН-ПРОМОКОДЫ ========================
@dp.callback_query(F.data == "admin_promocode")
async def admin_promocode(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_for_promocode)
    await safe_edit_or_send(
        callback.message,
        "🎫 <b>Создание промокода</b>\n\nВведите данные в формате:\n<code>КОД|СУММА|КОЛ-ВО_АКТИВАЦИЙ</code>\n\nПример: <code>BONUS50|5|100</code>",
        back_keyboard("admin_panel")
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_promocode)
async def process_promocode(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    
    try:
        code, reward, uses = message.text.split("|")
        reward = float(reward)
        uses = int(uses)
        db.create_promocode(code.strip(), reward, uses)
        await message.answer(f"✅ Промокод <code>{code}</code> создан!\nНаграда: {reward}₽\nАктиваций: {uses}")
    except Exception as e:
        await message.answer(f"❌ Неверный формат! Используйте: КОД|СУММА|КОЛ-ВО\nОшибка: {e}")
    
    await state.clear()

# ======================== АДМИН-ЭКСПОРТ ========================
@dp.callback_query(F.data == "admin_export_csv")
async def admin_export_csv(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    csv_data = db.export_stats_csv()
    await callback.message.answer_document(
        BufferedInputFile(csv_data.encode('utf-8'), filename="statistics.csv"),
        caption="📊 Экспорт статистики пользователей"
    )
    await callback.answer()

# ======================== АДМИН-БАН ========================
@dp.callback_query(F.data == "admin_ban")
async def admin_ban(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    await state.set_state(AdminStates.waiting_for_ban_user)
    await safe_edit_or_send(
        callback.message,
        "🚫 <b>Бан / Разбан пользователя</b>\n\nВведите Telegram ID пользователя:",
        back_keyboard("admin_panel")
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_ban_user)
async def process_admin_ban_user(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    
    try:
        target_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите корректный числовой ID!")
        return
    
    user = db.get_user(target_id)
    is_banned = not user.get("is_banned", False)
    user["is_banned"] = is_banned
    db.save()
    
    status_str = "заблокирован" if is_banned else "разблокирован"
    await state.clear()
    await message.answer(f"✅ Пользователь `{target_id}` успешно {status_str}!", reply_markup=back_keyboard("admin_panel"))

# ======================== АДМИН-СТАТУС ========================
@dp.callback_query(F.data == "admin_status")
async def admin_status(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    current_status = db.is_bot_stopped()
    new_status = not current_status
    db.set_bot_stopped(new_status)
    
    status_str = "⏸ Остановлен" if new_status else "⚡ Активен"
    await callback.answer(f"Статус бота изменён: {status_str}", show_alert=True)
    await safe_edit_or_send(
        callback.message,
        f"🛠 <b>Управление статусом бота</b>\n\nТекущее состояние: <b>{status_str}</b>",
        admin_panel_keyboard()
    )

# ======================== АДМИН-PIARFLOW ========================
@dp.callback_query(F.data == "admin_piarflow_debug")
async def admin_piarflow_debug(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    user_id = callback.from_user.id
    piar_sponsors = await fetch_piar_sponsors(user_id, callback.message.chat.id)
    
    debug_info = (
        f"🔍 <b>PiarFlow Debug</b>\n\n"
        f"API URL: {PIARFLOW_API_URL}\n"
        f"API Key: {'✅ Установлен' if PIARFLOW_API_KEY and PIARFLOW_API_KEY != 'BURAYA_API_KEY_YAZ' else '❌ Не установлен'}\n"
        f"Max sponsors: {PIARFLOW_MAX_SPONSORS}\n\n"
        f"Получено спонсоров: <b>{len(piar_sponsors)}</b>\n"
    )
    
    if piar_sponsors:
        debug_info += "\nСписок спонсоров:\n"
        for i, s in enumerate(piar_sponsors[:5], 1):
            debug_info += f"{i}. {s.get('button_text', 'Без названия')}\n"
    
    clean_text, entities = parse_premium_emoji(debug_info)
    await safe_edit_or_send(callback.message, clean_text, back_keyboard("admin_panel"), entities)
    await callback.answer()

# ======================== АДМИН-ПОЛЬЗОВАТЕЛИ ========================
@dp.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    await safe_edit_or_send(
        callback.message,
        "👥 <b>Список пользователей</b> (выберите для управления):",
        admin_users_keyboard(page=0)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_users_page_"))
async def admin_users_page(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    page_str = callback.data.replace("admin_users_page_", "")
    if page_str == "info":
        await callback.answer()
        return
    page = int(page_str)
    await safe_edit_or_send(
        callback.message,
        f"👥 <b>Список пользователей</b> (Страница {page + 1}):",
        admin_users_keyboard(page=page)
    )
    await callback.answer()

async def show_user_card(message: Message, user: Dict):
    status = "🚫 Забанен" if user.get("is_banned") else "✅ Активен"
    text = (
        f"👤 <b>Карточка пользователя</b> <code>{user['id']}</code>\n\n"
        f"• Статус: <b>{status}</b>\n"
        f"• Баланс: <b>{user.get('balance_rub', 0.0):.2f} ₽</b> / <b>{user.get('balance_manat', 0.0):.2f} ТМТ</b>\n"
        f"• Рефералов: <b>{user.get('referral_count', 0)}</b>\n"
        f"• Заданий выполнено: <b>{user.get('tasks_completed', 0)}</b>\n"
        f"• Дата регистрации: <code>{user.get('created_at', 'Н/Д')[:10]}</code>"
    )
    clean_text, entities = parse_premium_emoji(text)
    await safe_edit_or_send(message, clean_text, user_actions_keyboard(user["id"]), entities)

@dp.callback_query(F.data.startswith("admin_user_") & ~F.data.startswith("admin_users_"))
async def admin_user_info(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    data = callback.data
    
    if data.startswith("admin_user_add_balance_"):
        target_id = int(data.replace("admin_user_add_balance_", ""))
        await state.update_data(target_user_id=target_id, action="add_balance")
        await state.set_state(AdminStates.waiting_for_balance_amount)
        await safe_edit_or_send(
            callback.message,
            f"💰 Введите сумму в ₽ для добавления к балансу пользователя `{target_id}`:",
            back_keyboard(f"admin_user_{target_id}")
        )
        await callback.answer()
        return
    
    if data.startswith("admin_user_sub_balance_"):
        target_id = int(data.replace("admin_user_sub_balance_", ""))
        await state.update_data(target_user_id=target_id, action="sub_balance")
        await state.set_state(AdminStates.waiting_for_balance_amount)
        await safe_edit_or_send(
            callback.message,
            f"💰 Введите сумму в ₽ для списания с баланса пользователя `{target_id}`:",
            back_keyboard(f"admin_user_{target_id}")
        )
        await callback.answer()
        return
    
    if data.startswith("admin_user_add_ref_"):
        target_id = int(data.replace("admin_user_add_ref_", ""))
        await state.update_data(target_user_id=target_id)
        await state.set_state(AdminStates.waiting_for_referral_count)
        await safe_edit_or_send(
            callback.message,
            f"👥 Введите количество рефералов для добавления пользователю `{target_id}`:",
            back_keyboard(f"admin_user_{target_id}")
        )
        await callback.answer()
        return
    
    if data.startswith("admin_user_reset_ref_"):
        target_id = int(data.replace("admin_user_reset_ref_", ""))
        db.admin_reset_referrals(target_id)
        await callback.answer("🔄 Счётчик рефералов обнулён!", show_alert=True)
        user = db.get_user(target_id)
        await show_user_card(callback.message, user)
        return
    
    if data.startswith("admin_user_ban_"):
        target_id = int(data.replace("admin_user_ban_", ""))
        user = db.get_user(target_id)
        user["is_banned"] = True
        db.save()
        await callback.answer("🚫 Пользователь забанен!", show_alert=True)
        await show_user_card(callback.message, user)
        return
    
    if data.startswith("admin_user_unban_"):
        target_id = int(data.replace("admin_user_unban_", ""))
        user = db.get_user(target_id)
        user["is_banned"] = False
        db.save()
        await callback.answer("🔓 Пользователь разбанен!", show_alert=True)
        await show_user_card(callback.message, user)
        return
    
    target_id = int(data.replace("admin_user_", ""))
    user = db.get_user(target_id)
    await show_user_card(callback.message, user)
    await callback.answer()

@dp.message(AdminStates.waiting_for_balance_amount)
async def process_admin_balance(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    try:
        amount = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("❌ Введите число!")
        return
    
    data = await state.get_data()
    target_id = data.get("target_user_id")
    action = data.get("action")
    
    if target_id:
        if action == "add_balance":
            db.add_balance(target_id, amount, amount * RATE_MANAT)
            await message.answer(f"✅ Добавлено {amount:.2f} ₽ пользователю ID `{target_id}`")
        elif action == "sub_balance":
            db.deduct_balance(target_id, amount)
            await message.answer(f"✅ Списано {amount:.2f} ₽ у пользователя ID `{target_id}`")
    
    await state.clear()

@dp.message(AdminStates.waiting_for_referral_count)
async def process_admin_referrals(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    try:
        count = int(message.text)
    except ValueError:
        await message.answer("❌ Введите целое число!")
        return
    
    data = await state.get_data()
    target_id = data.get("target_user_id")
    
    if target_id:
        db.admin_add_referrals(target_id, count)
        await message.answer(f"✅ Пользователю ID `{target_id}` добавлено {count} рефералов")
    
    await state.clear()

# ======================== АДМИН-РАССЫЛКА ========================
@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    
    await state.set_state(AdminStates.waiting_for_broadcast_text)
    await safe_edit_or_send(
        callback.message,
        "📢 <b>Рассылка сообщений</b>\n\nВведите текст сообщения для рассылки всем пользователям (поддерживаются теги форматирования):",
        back_keyboard("admin_panel")
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_broadcast_text)
async def process_broadcast_text(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    
    text = message.text
    clean_text, entities = parse_premium_emoji(text)
    users = list(db.data["users"].keys())
    
    await message.answer(f"⏳ Запуск рассылки на {len(users)} пользователей...")
    
    success = 0
    failed = 0
    
    for uid in users:
        try:
            await bot.send_message(
                int(uid),
                clean_text,
                entities=entities if entities else None
            )
            success += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    
    await state.clear()
    await message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n• Успешно: <b>{success}</b>\n• Ошибок: <b>{failed}</b>",
        reply_markup=back_keyboard("admin_panel")
    )

# ======================== ЗАПУСК БОТА ========================
async def main():
    logger.info("🚀 Запуск бота...")
    
    # Запускаем веб-сервер как отдельную задачу
    async def run_web_server():
        try:
            from aiohttp import web
            
            app = web.Application()
            
            async def handle_ping(request):
                return web.Response(text="OK", status=200)
            
            app.router.add_get("/", handle_ping)
            app.router.add_get("/ping", handle_ping)
            app.router.add_get("/health", handle_ping)
            
            runner = web.AppRunner(app)
            await runner.setup()
            
            port = int(os.environ.get("PORT", 10000))
            site = web.TCPSite(runner, "0.0.0.0", port)
            await site.start()
            
            logger.info(f"✅ Веб-сервер запущен на порту {port}")
            logger.info(f"✅ Проверьте: http://0.0.0.0:{port}/ping")
            
            # Держим веб-сервер активным - это критически важно!
            # Без этого веб-сервер не будет обрабатывать запросы
            await asyncio.Event().wait()  # Бесконечное ожидание
            
        except Exception as e:
            logger.error(f"❌ Ошибка веб-сервера: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    # Запускаем веб-сервер в фоне
    web_task = asyncio.create_task(run_web_server())
    
    # Даём время веб-серверу запуститься
    await asyncio.sleep(1)
    
    # Запускаем фоновую задачу
    asyncio.create_task(check_inactive_users())
    
    # Запускаем бота
    logger.info("🤖 Бот запущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
