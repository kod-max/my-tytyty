import asyncio
import json
import logging
import re
import random
import string
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import httpx
from aiohttp import web
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
    FSInputFile
)

# ======================== КОНФИГУРАЦИЯ ========================
BOT_TOKEN = "8622998587:AAFglkCHi1lOcn9hjhH7ImSOWoTH74Ltyds"
ADMIN_IDS = [8488094637]

TGRAS_API_URL = "https://tgrass.space/offers"
TGRAS_API_KEY = "07bfe44618674796ae8a2edc404a0cea"

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

DEFAULT_EMOJI_COINS = "5424818078833715060"
DEFAULT_EMOJI_CHECK = "5397916757333654639"
DEFAULT_EMOJI_STAR = "5393512611968995988"
DEFAULT_EMOJI_ROCKET = "5397916757333654638"
DEFAULT_EMOJI_FIRE = "5397916757333654637"

EMOJI_TASK_HOURGLASS = DEFAULT_EMOJI_CHECK    
EMOJI_TASK_DONE_ALL = DEFAULT_EMOJI_CHECK     
EMOJI_TASK_LIST = DEFAULT_EMOJI_ROCKET        
EMOJI_TASK_MONEY = DEFAULT_EMOJI_COINS        
EMOJI_TASK_SUCCESS = DEFAULT_EMOJI_CHECK      
EMOJI_LOCK = "5251408958941322169"            
EMOJI_ANON = "6105006251295377689"            

# ======================== ИНИЦИАЛИЗАЦИЯ ========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# ======================== KEEP-ALIVE СЕРВЕР ========================
async def handle_ping(request):
    return web.Response(text="OK", status=200)

async def start_keep_alive_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/ping", handle_ping)
    app.router.add_head("/", handle_ping)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Keep-Alive сервер запущен на порту {port}")


# ======================== ПАРСЕР РАЗМЕТКИ ========================
TAG_RE = re.compile(
    r'<emoji\s+id=["\'](\d+)["\']>(.*?)</emoji>'
    r'|<b>(.*?)</b>'
    r'|\*\*(.*?)\*\*'
    r'|<i>(.*?)</i>'
    r'|<code>(.*?)</code>'
    r'|<quote>(.*?)</quote>'
    r'|##(.*?)##',
    re.DOTALL
)


def parse_premium_emoji(text: str) -> Tuple[str, List[MessageEntity]]:
    if not text:
        return text, []

    entities = []
    clean_text = ""
    last_end = 0

    def utf16_len(s: str) -> int:
        return len(s.encode('utf-16-le')) // 2

    utf16_offset = 0

    for match in TAG_RE.finditer(text):
        start, end = match.span()
        (emoji_id, emoji_visible, bold_text, bold_text2,
         italic_text, code_text, quote_text, quote_text2) = match.groups()

        before = text[last_end:start]
        clean_text += before
        utf16_offset += utf16_len(before)

        if emoji_id is not None:
            visible = emoji_visible or "🙂"
            entity_length = utf16_len(visible)
            entities.append(
                MessageEntity(
                    type="custom_emoji",
                    offset=utf16_offset,
                    length=entity_length,
                    custom_emoji_id=emoji_id
                )
            )
            clean_text += visible
            utf16_offset += entity_length
        elif bold_text is not None or bold_text2 is not None:
            content = bold_text if bold_text is not None else bold_text2
            entity_length = utf16_len(content)
            entities.append(MessageEntity(type="bold", offset=utf16_offset, length=entity_length))
            clean_text += content
            utf16_offset += entity_length
        elif italic_text is not None:
            entity_length = utf16_len(italic_text)
            entities.append(MessageEntity(type="italic", offset=utf16_offset, length=entity_length))
            clean_text += italic_text
            utf16_offset += entity_length
        elif code_text is not None:
            entity_length = utf16_len(code_text)
            entities.append(MessageEntity(type="code", offset=utf16_offset, length=entity_length))
            clean_text += code_text
            utf16_offset += entity_length
        elif quote_text is not None or quote_text2 is not None:
            content = quote_text if quote_text is not None else quote_text2
            entity_length = utf16_len(content)
            entities.append(MessageEntity(type="blockquote", offset=utf16_offset, length=entity_length))
            clean_text += content
            utf16_offset += entity_length

        last_end = end

    clean_text += text[last_end:]
    return clean_text, entities


def format_with_emoji(text: str, **kwargs) -> Tuple[str, List[MessageEntity]]:
    formatted = text.format(**kwargs)
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
                "👋 Добро пожаловать в бот заработка!\n\n"
                "<emoji id=\"{EMOJI_STAR}\">⭐</emoji> Приглашайте друзей и зарабатывайте!\n"
                "<emoji id=\"{EMOJI_COINS}\">💰</emoji> За каждого реферала вы получаете "
                "{REFERRAL_REWARD_RUB}₽ / {REFERRAL_REWARD_MANAT}ТМТ"
            ),
            "button_texts": {
                "earn": "Заработок",
                "referrals": "Рефералы",
                "top": "Топ",
                "profile": "Профиль",
                "withdraw": "Вывод",
            },
            "button_emojis": {
                "earn": DEFAULT_EMOJI_COINS,
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
                "total_withdrawn": 0
            },
            "bot_stopped": False
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
                "verified_sponsors": False
            }
            self.save()
        return self.data["users"][uid]

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

        self.save()
        return referrer_id

    def get_referrals(self, user_id: int) -> List[int]:
        uid = str(user_id)
        return self.data["referrals"].get(uid, [])

    def add_balance(self, user_id: int, amount_rub: float, amount_manat: float):
        user = self.get_user(user_id)
        user["balance_rub"] += amount_rub
        user["balance_manat"] += amount_manat
        self.save()

    def deduct_balance(self, user_id: int, amount_rub: float) -> bool:
        user = self.get_user(user_id)
        if user["balance_rub"] < amount_rub:
            return False
        user["balance_rub"] -= amount_rub
        user["balance_manat"] -= amount_rub * RATE_MANAT
        self.save()
        return True

    def create_withdrawal(self, user_id: int, amount_display: float, currency: str, amount_rub_deducted: float) -> int:
        withdrawal_id = len(self.data["withdrawals"]) + 1
        self.data["withdrawals"].append({
            "id": withdrawal_id,
            "user_id": user_id,
            "amount_display": amount_display,
            "currency": currency,
            "amount_rub_deducted": amount_rub_deducted,
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

    def add_sponsor(self, button_text: str, link: str, channel_username: str = None, order: int = 0):
        self.data["sponsors"].append({
            "button_text": button_text,
            "link": link,
            "channel_username": channel_username,
            "order": order
        })
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
        return self.data.get("button_emojis", {}).get(key, DEFAULT_EMOJI_STAR)

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


db = Database()


# ======================== TGRAS ========================
async def fetch_tgrass_offers(user_id: int) -> Tuple[int, Dict]:
    payload = {
        "tg_user_id": user_id,
        "tg_login": None,
        "lang": "ru",
        "is_premium": False
    }

    try:
        async with httpx.AsyncClient(verify=False, timeout=8) as client:
            response = await client.post(
                TGRAS_API_URL,
                json=payload,
                headers={
                    "accept": "application/json",
                    "Content-Type": "application/json",
                    "Auth": TGRAS_API_KEY
                }
            )
        status_code = response.status_code
        data = response.json()
        return status_code, data
    except Exception as e:
        logger.error(f"TGRAS error: {e}")
        return 500, {"status": "error", "message": str(e)}


TGRAS_CACHE_TTL = 60
_tgrass_cache: Dict[int, Tuple[float, int, Dict]] = {}


async def fetch_tgrass_offers_cached(user_id: int) -> Tuple[int, Dict]:
    now = asyncio.get_event_loop().time()
    cached = _tgrass_cache.get(user_id)
    if cached is not None:
        cached_time, cached_status, cached_data = cached
        if now - cached_time < TGRAS_CACHE_TTL:
            return cached_status, cached_data

    status_code, data = await fetch_tgrass_offers(user_id)
    _tgrass_cache[user_id] = (now, status_code, data)
    return status_code, data


async def check_tgrass_subscriptions(user_id: int) -> bool:
    status_code, response = await fetch_tgrass_offers_cached(user_id)
    if status_code == 200 and response.get("status") == "ok":
        return True
    return False


def invalidate_tgrass_cache(user_id: int):
    _tgrass_cache.pop(user_id, None)


async def get_tgrass_sponsors(user_id: int) -> List[Dict]:
    status_code, response = await fetch_tgrass_offers_cached(user_id)
    if status_code == 200:
        offers = response.get("offers", [])
        return offers
    return []


# ======================== ПРОВЕРКА ПОДПИСОК ========================
async def check_channel_subscription(user_id: int, channel_username: str) -> bool:
    if not channel_username:
        return True

    try:
        if channel_username.startswith("@"):
            channel_username = channel_username[1:]

        chat = await bot.get_chat(f"@{channel_username}")
        member = await bot.get_chat_member(chat.id, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"Check subscription error for @{channel_username}: {e}")
        return False


async def check_all_sponsor_subscriptions(user_id: int) -> Tuple[bool, List[str]]:
    sponsors = db.get_sponsors()
    active_sponsors = [s for s in sponsors if s.get("channel_username")]

    if not active_sponsors:
        return True, []

    tasks = [
        check_channel_subscription(user_id, sponsor["channel_username"])
        for sponsor in active_sponsors
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    failed = []
    for sponsor, is_subbed in zip(active_sponsors, results):
        if is_subbed is not True:
            failed.append(sponsor.get("button_text", f"@{sponsor['channel_username']}"))

    return len(failed) == 0, failed


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


def sponsors_gate_keyboard(tgrass_offers: List[Dict] = None) -> InlineKeyboardMarkup:
    sponsor_buttons = []

    for sponsor in db.get_sponsors():
        sponsor_buttons.append(
            create_button(
                sponsor["button_text"],
                db.get_button_emoji("earn"),
                "📢",
                style="primary",
                url=sponsor["link"]
            )
        )

    for offer in (tgrass_offers or []):
        link = offer.get("link")
        if not link:
            continue
        title = offer.get("name") or offer.get("title") or "Канал"
        sponsor_buttons.append(
            create_button(title, db.get_button_emoji("earn"), "📢", style="primary", url=link)
        )

    keyboard = []
    total = len(sponsor_buttons)
    i = 0
    while i < total:
        remaining = total - i
        if remaining == 1:
            keyboard.append([sponsor_buttons[i]])
            i += 1
        else:
            keyboard.append([sponsor_buttons[i], sponsor_buttons[i + 1]])
            i += 2

    keyboard.append([
        create_button(
            "Я подписался, проверить",
            db.get_button_emoji("withdraw"),
            "✅",
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
            create_button(btn_earn, emoji_earn, "💰", style="primary", callback_data="menu_earn")
        ],
        [
            create_button(btn_referrals, emoji_referrals, "👥", style="primary", callback_data="menu_referrals"),
            create_button(btn_top, emoji_top, "🏆", style="primary", callback_data="menu_top"),
        ],
        [
            create_button(btn_profile, emoji_profile, "👤", style="primary", callback_data="menu_profile"),
        ],
        [
            create_button(btn_withdraw, emoji_withdraw, "💳", style="success", callback_data="menu_withdraw"),
        ],
    ])


def earn_keyboard(has_offer: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_offer:
        rows.append([
            create_button("Проверить выполнение", db.get_button_emoji("withdraw"), "✅", style="success", callback_data="check_task")
        ])
    rows.append([
        create_button("Рефералы", db.get_button_emoji("referrals"), "👥", style="primary", callback_data="menu_referrals"),
        create_button("Назад", db.get_button_emoji("withdraw"), "🔙", callback_data="menu_main"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def referral_keyboard(user_id: int) -> InlineKeyboardMarkup:
    bot_username = bot.username if hasattr(bot, 'username') and bot.username else "Earn_TMTRublBot"
    link = f"https://t.me/{bot_username}?start=ref_{user_id}"

    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button(
            "Пригласить друга",
            db.get_button_emoji("top"),
            "🚀",
            style="primary",
            url=f"tg://msg?text=Присоединяйся к боту для заработка!\n{link}"
        )],
        [create_button("Мои рефералы", db.get_button_emoji("profile"), "📋", callback_data="referrals_list")],
        [create_button("Назад", db.get_button_emoji("withdraw"), "🔙", callback_data="menu_main")]
    ])


def profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button("Вывести средства", db.get_button_emoji("withdraw"), "💳", style="success", callback_data="menu_withdraw")],
        [create_button("Назад", db.get_button_emoji("withdraw"), "🔙", callback_data="menu_main")]
    ])


def withdraw_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button("Вывести в ₽", db.get_button_emoji("earn"), "💰", style="primary", callback_data="withdraw_rub")],
        [create_button("Вывести в ТМТ", db.get_button_emoji("earn"), "💰", style="primary", callback_data="withdraw_manat")],
        [create_button("Назад", db.get_button_emoji("withdraw"), "🔙", callback_data="menu_main")]
    ])


def back_keyboard(callback_data: str = "menu_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button("Назад", db.get_button_emoji("withdraw"), "🔙", callback_data=callback_data)]
    ])


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            create_button("Статистика", db.get_button_emoji("profile"), "📊", callback_data="admin_stats"),
            create_button("Пользователи", db.get_button_emoji("referrals"), "👥", callback_data="admin_users")
        ],
        [
            create_button("Текст старта", db.get_button_emoji("withdraw"), "📝", callback_data="admin_start_text"),
            create_button("Спонсоры/Задания", db.get_button_emoji("earn"), "📺", callback_data="admin_sponsors")
        ],
        [
            create_button("Текст кнопок", db.get_button_emoji("profile"), "🧩", callback_data="admin_button_texts"),
            create_button("Эмодзи кнопок", db.get_button_emoji("star"), "🎨", callback_data="admin_button_emojis")
        ],
        [
            create_button("Баннер", db.get_button_emoji("top"), "🖼", callback_data="admin_banner"),
            create_button("Награда реферала", db.get_button_emoji("earn"), "🎁", callback_data="admin_referral_reward")
        ],
        [
            create_button("Рассылка", db.get_button_emoji("fire"), "📢", callback_data="admin_broadcast"),
            create_button("Бан/Разбан", db.get_button_emoji("withdraw"), "🚫", callback_data="admin_ban")
        ],
        [
            create_button("Статус бота", db.get_button_emoji("profile"), "⚡", callback_data="admin_status"),
            create_button("TGRAS debug", db.get_button_emoji("top"), "🔍", callback_data="admin_tgras_debug")
        ],
        [create_button("Назад", db.get_button_emoji("withdraw"), "🔙", callback_data="menu_main")]
    ])


def admin_sponsors_keyboard() -> InlineKeyboardMarkup:
    sponsors = db.get_sponsors()
    keyboard = []

    for i, sponsor in enumerate(sponsors):
        channel = sponsor.get("channel_username", "")
        display = f"{i+1}. {sponsor['button_text']}"
        if channel:
            display += f" (@{channel})"
        keyboard.append([
            InlineKeyboardButton(text=display[:50], callback_data=f"admin_sponsor_{i}"),
            InlineKeyboardButton(text="🗑", callback_data=f"admin_sponsor_del_{i}")
        ])

    keyboard.append([create_button("Добавить спонсора/задание", db.get_button_emoji("earn"), "➕", style="success", callback_data="admin_sponsor_add")])
    keyboard.append([create_button("Назад", db.get_button_emoji("withdraw"), "🔙", callback_data="admin_panel")])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def admin_button_texts_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button(f"💰 {db.get_button_text('earn')}", db.get_button_emoji("earn"), callback_data="admin_btn_earn")],
        [create_button(f"👥 {db.get_button_text('referrals')}", db.get_button_emoji("referrals"), callback_data="admin_btn_referrals")],
        [create_button(f"🏆 {db.get_button_text('top')}", db.get_button_emoji("top"), callback_data="admin_btn_top")],
        [create_button(f"👤 {db.get_button_text('profile')}", db.get_button_emoji("profile"), callback_data="admin_btn_profile")],
        [create_button(f"💳 {db.get_button_text('withdraw')}", db.get_button_emoji("withdraw"), callback_data="admin_btn_withdraw")],
        [create_button("Назад", db.get_button_emoji("withdraw"), "🔙", callback_data="admin_panel")]
    ])


def admin_button_emojis_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button(f"💰 {db.get_button_text('earn')}", db.get_button_emoji("earn"), callback_data="admin_emoji_earn")],
        [create_button(f"👥 {db.get_button_text('referrals')}", db.get_button_emoji("referrals"), callback_data="admin_emoji_referrals")],
        [create_button(f"🏆 {db.get_button_text('top')}", db.get_button_emoji("top"), callback_data="admin_emoji_top")],
        [create_button(f"👤 {db.get_button_text('profile')}", db.get_button_emoji("profile"), callback_data="admin_emoji_profile")],
        [create_button(f"💳 {db.get_button_text('withdraw')}", db.get_button_emoji("withdraw"), callback_data="admin_emoji_withdraw")],
        [create_button("Назад", db.get_button_emoji("withdraw"), "🔙", callback_data="admin_panel")]
    ])


def admin_banner_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button("📤 Загрузить баннер", db.get_button_emoji("earn"), style="success", callback_data="banner_upload")],
        [create_button("🗑 Удалить баннер", db.get_button_emoji("withdraw"), style="danger", callback_data="banner_delete")],
        [create_button("🔙 Назад", db.get_button_emoji("withdraw"), callback_data="admin_panel")]
    ])


def admin_referral_reward_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button("Изменить награду (₽)", db.get_button_emoji("earn"), callback_data="admin_reward_rub")],
        [create_button("Изменить награду (ТМТ)", db.get_button_emoji("earn"), callback_data="admin_reward_manat")],
        [create_button("🔙 Назад", db.get_button_emoji("withdraw"), callback_data="admin_panel")]
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
            InlineKeyboardButton(text=f"{status} ID {user['id']}", callback_data=f"admin_user_{user['id']}")
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin_users_page_{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="admin_users_page_info"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"admin_users_page_{page+1}"))
    if nav:
        keyboard.append(nav)

    keyboard.append([create_button("Назад", db.get_button_emoji("withdraw"), "🔙", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def user_actions_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            create_button("+Баланс", db.get_button_emoji("earn"), "💰", style="success", callback_data=f"admin_user_add_balance_{user_id}"),
            create_button("-Баланс", db.get_button_emoji("earn"), "💰", style="danger", callback_data=f"admin_user_sub_balance_{user_id}")
        ],
        [
            create_button("+Рефералы", db.get_button_emoji("referrals"), "👥", style="success", callback_data=f"admin_user_add_ref_{user_id}"),
            create_button("Обнулить рефералы", db.get_button_emoji("referrals"), "🔄", style="danger", callback_data=f"admin_user_reset_ref_{user_id}")
        ],
        [
            create_button("Бан", db.get_button_emoji("fire"), "🚫", style="danger", callback_data=f"admin_user_ban_{user_id}"),
            create_button("Разбан", db.get_button_emoji("withdraw"), "🔓", style="success", callback_data=f"admin_user_unban_{user_id}")
        ],
        [create_button("Назад", db.get_button_emoji("withdraw"), "🔙", callback_data="admin_users")]
    ])


# ======================== СОСТОЯНИЯ FSM ========================
class AdminStates(StatesGroup):
    waiting_for_start_text = State()
    waiting_for_sponsor_name = State()
    waiting_for_sponsor_link = State()
    waiting_for_sponsor_channel = State()
    waiting_for_broadcast_text = State()
    waiting_for_balance_amount = State()
    waiting_for_referral_count = State()
    waiting_for_ban_user = State()
    waiting_for_button_text = State()
    waiting_for_button_emoji = State()
    waiting_for_banner = State()
    waiting_for_reward_rub = State()
    waiting_for_reward_manat = State()


class WithdrawStates(StatesGroup):
    waiting_for_amount = State()


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
    try:
        clean_text, entities = format_with_emoji(
            start_text,
            EMOJI_STAR=db.get_button_emoji("profile"),
            EMOJI_COINS=db.get_button_emoji("earn"),
            REFERRAL_REWARD_RUB=reward_rub,
            REFERRAL_REWARD_MANAT=reward_manat
        )
    except (KeyError, ValueError, IndexError) as e:
        logger.error(f"Невалидный start_text, использую заглушку: {e}")
        clean_text, entities = (
            "👋 Добро пожаловать в бот заработка!",
            []
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


async def send_sponsors_gate(target_message: Message, user_id: int, edit: bool = False):
    tgrass_offers = await get_tgrass_sponsors(user_id)
    manual_sponsors = db.get_sponsors()
    logger.info(
        f"[sponsors_gate] user_id={user_id} "
        f"manual_sponsors={len(manual_sponsors)} "
        f"tgrass_offers={len(tgrass_offers)} "
        f"tgrass_links={[o.get('link') for o in tgrass_offers]}"
    )
    raw_text = (
        f'<emoji id="{EMOJI_LOCK}">🔒</emoji> Для доступа к боту подпишитесь на '
        f'все каналы ниже, затем нажмите «Проверить».'
    )
    clean_text, entities = parse_premium_emoji(raw_text)
    kb = sponsors_gate_keyboard(tgrass_offers)
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


async def user_needs_gate(user_id: int) -> bool:
    if db.is_verified(user_id):
        return False
    if db.get_sponsors():
        return True
    tgrass_offers = await get_tgrass_sponsors(user_id)
    return bool(tgrass_offers)


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

    if len(args) > 1 and args[1].startswith("ref_"):
        ref_part = args[1][4:]
        if ref_part.isdigit():
            referrer_id = int(ref_part)
            if referrer_id != user_id and str(referrer_id) in db.data["users"]:
                db.link_referral(referrer_id, user_id)

    needs_gate = await user_needs_gate(user_id)
    if needs_gate:
        await send_sponsors_gate(message, user_id)
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

    invalidate_tgrass_cache(user_id)

    ok_manual, failed_manual = await check_all_sponsor_subscriptions(user_id)
    ok_tgrass = await check_tgrass_subscriptions(user_id)

    failed = list(failed_manual)
    if not ok_tgrass:
        failed.append("Спонсорские каналы (TGRAS)")

    if ok_manual and ok_tgrass:
        db.set_verified(user_id, True)
        await try_confirm_referral(user_id)
        await callback.answer("✅ Подписка подтверждена!")
        await send_main_menu(callback.message, edit=True)
    else:
        failed_text = "\n".join(f"• {f}" for f in failed)
        await callback.answer(
            f"❌ Вы подписаны не на все каналы:\n{failed_text}",
            show_alert=True
        )


# ======================== ГЛАВНОЕ МЕНЮ ========================
@dp.callback_query(F.data == "menu_main")
async def menu_main(callback: CallbackQuery):
    user_id = callback.from_user.id

    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return

    if await user_needs_gate(user_id):
        await send_sponsors_gate(callback.message, user_id, edit=True)
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

    if await user_needs_gate(user_id):
        await send_sponsors_gate(callback.message, user_id, edit=True)
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
        channel = sponsor.get("channel_username")
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
        [create_button("Подписаться", db.get_button_emoji("withdraw"), "✅", style="primary", url=link)],
        [create_button("Проверить выполнение", db.get_button_emoji("profile"), "🔄", style="success", callback_data="check_task")],
        [create_button("Назад", db.get_button_emoji("withdraw"), "🔙", callback_data="menu_main")]
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
        channel = sponsor.get("channel_username")
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

    if await user_needs_gate(user_id):
        await send_sponsors_gate(callback.message, user_id, edit=True)
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


# ======================== ТОП ========================
@dp.callback_query(F.data == "menu_top")
async def menu_top(callback: CallbackQuery):
    user_id = callback.from_user.id
    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return
    if await user_needs_gate(user_id):
        await send_sponsors_gate(callback.message, user_id, edit=True)
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
    if await user_needs_gate(user_id):
        await send_sponsors_gate(callback.message, user_id, edit=True)
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


# ======================== ВЫВОД СРЕДСТВ ========================
@dp.callback_query(F.data == "menu_withdraw")
async def menu_withdraw(callback: CallbackQuery):
    user_id = callback.from_user.id
    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return
    if await user_needs_gate(user_id):
        await send_sponsors_gate(callback.message, user_id, edit=True)
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
            f"💳 Вывод в ₽\n\nВаш баланс: {user['balance_rub']:.2f}₽\nМинимум: {MIN_WITHDRAW_RUB}₽\n\nВведите сумму вывода:",
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
            f"💳 Вывод в ТМТ\n\nВаш баланс: {user['balance_manat']:.2f}ТМТ\nМинимум: {MIN_WITHDRAW_MANAT:.1f}ТМТ\n\nВведите сумму вывода:",
            back_keyboard("menu_withdraw")
        )

    await callback.answer()


async def send_withdrawal_check(withdrawal_id: int, user_id: int, amount_display: float, currency: str):
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
        f'Сумма: {amount_display:.2f} {currency_label}</quote>\n\n'
        f'Статус: ⏳ Ожидает обработки'
    )
    clean_text, entities = parse_premium_emoji(raw_text)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            create_button("Отклонить", db.get_button_emoji("withdraw"), "❌", style="danger",
                          callback_data=f"wd_reject_{withdrawal_id}"),
            create_button("Выплачено", db.get_button_emoji("withdraw"), "✅", style="success",
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

    data = await state.get_data()
    currency = data.get("withdraw_currency", "rub")
    user_id = message.from_user.id
    user = db.get_user(user_id)

    if currency == "rub":
        if amount < MIN_WITHDRAW_RUB:
            await message.answer(f"❌ Минимальная сумма вывода {MIN_WITHDRAW_RUB}₽")
            return
        if amount > user["balance_rub"]:
            await message.answer(f"❌ Недостаточно средств. Ваш баланс: {user['balance_rub']:.2f}₽")
            return
        if db.deduct_balance(user_id, amount):
            withdrawal_id = db.create_withdrawal(user_id, amount, "rub", amount)
            await send_withdrawal_check(withdrawal_id, user_id, amount, "rub")
            await message.answer(
                f"✅ Заявка на вывод {amount:.2f}₽ принята!\n\nОжидайте обработки в течение 24-48 часов.",
                reply_markup=main_menu_keyboard()
            )
        else:
            await message.answer("❌ Ошибка при обработке заявки.")

    elif currency == "manat":
        if amount < MIN_WITHDRAW_MANAT:
            await message.answer(f"❌ Минимальная сумма вывода {MIN_WITHDRAW_MANAT:.1f}ТМТ")
            return
        if amount > user["balance_manat"]:
            await message.answer(f"❌ Недостаточно средств. Ваш баланс: {user['balance_manat']:.2f}ТМТ")
            return
        amount_rub = amount / RATE_MANAT
        if db.deduct_balance(user_id, amount_rub):
            withdrawal_id = db.create_withdrawal(user_id, amount, "manat", amount_rub)
            await send_withdrawal_check(withdrawal_id, user_id, amount, "manat")
            await message.answer(
                f"✅ Заявка на вывод {amount:.2f}ТМТ принята!\n\nОжидайте обработки в течение 24-48 часов.",
                reply_markup=main_menu_keyboard()
            )
        else:
            await message.answer("❌ Ошибка при обработке заявки.")

    await state.clear()


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
        f'Сумма: {amount_display:.2f} {currency_label}</quote>\n\n'
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

    text = (
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"🚫 Заблокировано: <b>{banned_users}</b>\n"
        f"👥 Всего рефералов: <b>{stats.get('total_referrals', 0)}</b>\n\n"
        f"💰 Суммарный баланс пользователей:\n"
        f"• <b>{total_rub:.2f} ₽</b>\n"
        f"• <b>{total_manat:.2f} ТМТ</b>\n\n"
        f"💳 Всего выплачено: <b>{stats.get('total_withdrawn', 0.0):.2f} ₽</b>"
    )

    clean_text, entities = parse_premium_emoji(text)
    await safe_edit_or_send(callback.message, clean_text, back_keyboard("admin_panel"), entities)
    await callback.answer()


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


@dp.callback_query(F.data == "admin_start_text")
async def admin_start_text(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    current_text = db.get_start_text()
    text = (
        f"📝 <b>Текущий текст приветствия (/start):</b>\n\n"
        f"<code>{current_text}</code>\n\n"
        f"Доступные переменные:\n"
        f"• <code>{{EMOJI_STAR}}</code>\n"
        f"• <code>{{EMOJI_COINS}}</code>\n"
        f"• <code>{{REFERRAL_REWARD_RUB}}</code>\n"
        f"• <code>{{REFERRAL_REWARD_MANAT}}</code>\n\n"
        f"Поддерживается форматирование тегами (<b>bold</b>, <i>italic</i>, <code>code</code>, <quote>quote</quote>, **bold**, ##quote##).\n\n"
        f"Введите новый текст:"
    )
    clean_text, entities = parse_premium_emoji(text)
    await state.set_state(AdminStates.waiting_for_start_text)
    await safe_edit_or_send(callback.message, clean_text, back_keyboard("admin_panel"), entities)
    await callback.answer()


@dp.message(AdminStates.waiting_for_start_text)
async def process_start_text(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return

    new_text = message.text
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

    db.set_start_text(new_text)
    await state.clear()
    await message.answer("✅ Текст старта успешно обновлён!", reply_markup=back_keyboard("admin_panel"))


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
        "➕ <b>Добавление спонсора</b>\n\nШаг 1/3: Введите название кнопки (например: <code>Подписаться на Канал</code>):",
        back_keyboard("admin_sponsors")
    )
    await callback.answer()


@dp.message(AdminStates.waiting_for_sponsor_name)
async def process_sponsor_name(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    await state.update_data(sponsor_name=message.text)
    await state.set_state(AdminStates.waiting_for_sponsor_link)
    await message.answer("Шаг 2/3: Введите ссылку на канал/ресурс (например: <code>https://t.me/mychannel</code>):")


@dp.message(AdminStates.waiting_for_sponsor_link)
async def process_sponsor_link(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    await state.update_data(sponsor_link=message.text)
    await state.set_state(AdminStates.waiting_for_sponsor_channel)
    await message.answer(
        "Шаг 3/3: Введите username канала с @ для автоматической проверки подписки (например <code>@mychannel</code>), "
        "или отправьте <code>-</code> если автоматическая проверка не требуется:"
    )


@dp.message(AdminStates.waiting_for_sponsor_channel)
async def process_sponsor_channel(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return

    data = await state.get_data()
    channel = message.text.strip()
    if channel == "-":
        channel = None
    elif channel.startswith("@"):
        channel = channel[1:]

    db.add_sponsor(
        button_text=data["sponsor_name"],
        link=data["sponsor_link"],
        channel_username=channel
    )

    await state.clear()
    await message.answer("✅ Спонсор успешно добавлен!", reply_markup=back_keyboard("admin_sponsors"))


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


@dp.callback_query(F.data == "admin_tgras_debug")
async def admin_tgras_debug(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    user_id = callback.from_user.id
    status_code, data = await fetch_tgrass_offers(user_id)

    debug_info = (
        f"🔍 <b>TGRAS API Debug</b>\n\n"
        f"Status Code: <code>{status_code}</code>\n"
        f"Response:\n<code>{json.dumps(data, ensure_ascii=False, indent=2)[:3000]}</code>"
    )

    clean_text, entities = parse_premium_emoji(debug_info)
    await safe_edit_or_send(callback.message, clean_text, back_keyboard("admin_panel"), entities)
    await callback.answer()


# ======================== ЗАПУСК БОТА ========================
async def main():
    logger.info("Запуск бота...")
    await start_keep_alive_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
