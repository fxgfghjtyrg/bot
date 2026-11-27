import asyncio
import logging
import aiosqlite
import os
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, LabeledPrice, PreCheckoutQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)

from crypto_pay_api import CryptoPayAsync, CryptoPayConfig
from aiorocket2 import xRocketClient

# ================== НАСТРОЙКИ ==================
# Рекомендуется задавать их через переменные окружения на Railway, 
# но для локального теста можно вписать напрямую в кавычки.
BOT_TOKEN = os.getenv("BOT_TOKEN", "9000976358:AAFm1FFWsOlh77Raw6ecIKLZz9hJb45-WHk")
ADMIN_ID = int(os.getenv("ADMIN_ID", 8121754585)) # Ваш ID
DB = os.getenv("DB_PATH", "debrw.db") # На Railway лучше указать /app/data/debrw.db

CRYPTO_PAY_TOKEN = os.getenv("CRYPTO_PAY_TOKEN", "687062:AABWEFe6eltgKvhCZWup1rMIMZ8Ip5GygIr")
XROCKET_API_KEY = os.getenv("XROCKET_API_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhcHBJZCI6IjMxMTI3MyIsImp0aSI6ImFwcDozMTEyNzM6YmIyNTZjZGYtYTFlNi00ZGY0LWI3MTgtMTNlMGU3Y2ZkZDkyIiwiaWF0IjoxNzk1NzE3NzYyfQ.HhjIx0NF3mKq_r0FIdezvlyNWqSfaEIA7ijMRQJewEA")

STARS_RATE = 10       # 1 dw = 10 ⭐
CRYPTO_RATE = 0.2     # 1 dw = 0.2 USDT
# ================================================

router = Router()

# ================== БД ==================
async def init_db():
    # Создаем папку для БД, если её нет (актуально для Railway Volume)
    db_dir = os.path.dirname(DB)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    async with aiosqlite.connect(DB) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance INTEGER DEFAULT 0,
            orders INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            target TEXT,
            speed TEXT,
            complaints INTEGER,
            price INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS promos(
            code TEXT PRIMARY KEY,
            amount INTEGER,
            uses_left INTEGER
        );
        CREATE TABLE IF NOT EXISTS promo_uses(
            code TEXT, user_id INTEGER, PRIMARY KEY(code, user_id)
        );
        CREATE TABLE IF NOT EXISTS tickets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        await db.commit()

async def ensure_user(uid, username):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR IGNORE INTO users(user_id, username) VALUES(?,?)", (uid, username or ""))
        await db.execute("UPDATE users SET username=? WHERE user_id=?", (username or "", uid))
        await db.commit()

async def get_user(uid):
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id=?", (uid,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None

async def add_balance(uid, amount):
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, uid))
        await db.commit()

# ================== СОСТОЯНИЯ ==================
class Order(StatesGroup):
    target = State()
    speed = State()
    complaints = State()

class TopUp(StatesGroup):
    amount = State()
    crypto_amount = State()
    xrocket_amount = State()

class Support(StatesGroup):
    text = State()

class Promo(StatesGroup):
    code = State()

class Admin(StatesGroup):
    give_id = State()
    give_amount = State()
    promo_code = State()
    promo_amount = State()
    promo_uses = State()
    broadcast = State()

# ================== КЛАВИАТУРЫ ==================
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="topup_menu")],
        [InlineKeyboardButton(text="sn0s", callback_data="order")],
        [InlineKeyboardButton(text="🆘 Поддержка", callback_data="support")],
    ])

def back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")]
    ])

def profile_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎟 Промокод", callback_data="promo")],
        [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="topup_menu")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
    ])

def topup_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Купить dw за звёзды", callback_data="topup_stars")],
        [InlineKeyboardButton(text="💎 CryptoBot", callback_data="topup_crypto")],
        [InlineKeyboardButton(text="🚀 xRocket", callback_data="topup_xrocket")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
    ])

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Выдать dw", callback_data="admin_give")],
        [InlineKeyboardButton(text="🎟 Создать промокод", callback_data="admin_promo")],
        [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_users")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🎫 Заявки", callback_data="admin_tickets")],
    ])

WELCOME = (
    "Добро пожаловать в debrw\n"
    "Мы даем полную гарантию на свои услуги, и если они вам не помогли вернём вам все средства.\n"
    "Выберите любое действие ниже👇"
)
THANKS = (
    "Спасибо за ваш заказ! Он будет обработан в скором времени и даст результаты.\n"
    "Мы даём полную гарантию, что выполним свою работу, если работа не была выполнена — "
    "напишите обращение в поддержку, и мы вернём вам средства."
)

# ================== СТАРТ / НАЗАД ==================
@router.message(CommandStart())
async def start(m: Message):
    await ensure_user(m.from_user.id, m.from_user.username)
    await m.answer(WELCOME, reply_markup=main_menu())

@router.callback_query(F.data == "back_main")
async def back_main(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text(WELCOME, reply_markup=main_menu())

# ================== ПРОФИЛЬ ==================
@router.callback_query(F.data == "profile")
async def profile(c: CallbackQuery):
    await ensure_user(c.from_user.id, c.from_user.username)
    u = await get_user(c.from_user.id)
    text = (
        f"👤 Имя пользователя: @{u['username'] or '—'}\n"
        f"💰 Баланс: {u['balance']} dw\n"
        f"🆔 ID: {u['user_id']}\n"
        f"Количество sn0s: {u['orders']}"
    )
    await c.message.edit_text(text, reply_markup=profile_kb())

# ================== ПРОМОКОД ==================
@router.callback_query(F.data == "promo")
async def promo(c: CallbackQuery, state: FSMContext):
    await state.set_state(Promo.code)
    await c.message.edit_text("Введите промокод:", reply_markup=back_kb())

@router.message(Promo.code)
async def promo_code(m: Message, state: FSMContext):
    code = m.text.strip()
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM promos WHERE code=?", (code,)) as cur:
            row = await cur.fetchone()
        if not row:
            await m.answer("❌ Промокод не найден."); return
        if row["uses_left"] <= 0:
            await m.answer("❌ Промокод больше не действителен."); await state.clear(); return
        async with db.execute("SELECT 1 FROM promo_uses WHERE code=? AND user_id=?", (code, m.from_user.id)) as cur2:
            if await cur2.fetchone():
                await m.answer("❌ Вы уже использовали этот промокод."); await state.clear(); return
        await db.execute("UPDATE promos SET uses_left=uses_left-1 WHERE code=?", (code,))
        await db.execute("INSERT INTO promo_uses(code,user_id) VALUES(?,?)", (code, m.from_user.id))
        await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (row["amount"], m.from_user.id))
        await db.commit()
    await state.clear()
    await m.answer(
        f"✅ Промокод активирован! +{row['amount']} dw",
        reply_markup=main_menu()
    )

# ================== ПОПОЛНЕНИЕ (ОБЩЕЕ) ==================
@router.callback_query(F.data == "topup_menu")
async def topup_menu(c: CallbackQuery, state: FSMContext):
    await c.message.edit_text(
        "💳 Пополнение баланса\n\n"
        "⭐ 1 dw = 10 звёзд\n"
        "💎 1 dw = 0.2 USDT (CryptoBot)\n"
        "🚀 1 dw = 0.2 USDT (xRocket)\n\n"
        "Выберите способ оплаты:",
        reply_markup=topup_kb()
    )

# ---------- TELEGRAM STARS ----------
@router.callback_query(F.data == "topup_stars")
async def topup_stars(c: CallbackQuery, state: FSMContext):
    await state.set_state(TopUp.amount)
    await c.message.edit_text(
        "💳 Введите сумму в dw, которую хотите получить:\n\n1 dw = 10 ⭐",
        reply_markup=back_kb()
    )

@router.message(TopUp.amount)
async def topup_amount(m: Message, state: FSMContext):
    try:
        dw = int(m.text.strip())
        if dw <= 0: raise ValueError
    except ValueError:
        await m.answer("Введите целое положительное число.")
        return
    stars = dw * STARS_RATE
    await m.answer_invoice(
        title=f"Пополнение на {dw} dw",
        description=f"Покупка {dw} dw за {stars} ⭐",
        payload=f"topup:{dw}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="XTR", amount=stars)],
    )

@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)

@router.message(F.successful_payment)
async def payment_ok(m: Message, state: FSMContext):
    payload = m.successful_payment.invoice_payload
    dw = int(payload.split(":")[1])
    await add_balance(m.from_user.id, dw)

    data = await state.get_data()
    pending = data.get("pending_order")
    await state.clear()

    if pending:
        user = await get_user(m.from_user.id)
        if user["balance"] >= pending["total"]:
            await process_order(m.bot, m.from_user, pending)
            await m.answer(THANKS, reply_markup=main_menu())
        else:
            await m.answer(
                f"❗ Всё ещё не хватает {pending['total'] - user['balance']} dw",
                reply_markup=main_menu()
            )
        return

    await m.answer(
        f"✅ Баланс пополнен на {dw} dw",
        reply_markup=main_menu()
    )

# ---------- CRYPTOBOT ----------
@router.callback_query(F.data == "topup_crypto")
async def topup_crypto(c: CallbackQuery, state: FSMContext):
    await state.set_state(TopUp.crypto_amount)
    await c.message.edit_text(
        "💎 Введите сумму в dw для пополнения через CryptoBot:\n\n"
        "1 dw = 0.2 USDT",
        reply_markup=back_kb()
    )

@router.message(TopUp.crypto_amount)
async def topup_crypto_amount(m: Message, state: FSMContext):
    try:
        dw = int(m.text.strip())
        if dw <= 0: raise ValueError
    except ValueError:
        await m.answer("Введите целое положительное число.")
        return

    usdt = round(dw * CRYPTO_RATE, 2)
    cfg = CryptoPayConfig(token=CRYPTO_PAY_TOKEN)
    async with CryptoPayAsync(cfg) as cp:
        try:
            invoice = await cp.create_invoice(
                asset="USDT",
                amount=usdt,
                description=f"Пополнение на {dw} dw ({usdt} USDT)",
                payload=f"cryptobot:{m.from_user.id}:{dw}"
            )
            pay_url = invoice["pay_url"]
            await m.answer(
                f"💎 Счёт на {dw} dw создан.\n"
                f"Сумма к оплате: {usdt} USDT\n"
                f"Оплатите по ссылке: {pay_url}\n\n"
                f"После оплаты баланс пополнится автоматически.",
                reply_markup=main_menu()
            )
            asyncio.create_task(poll_crypto_invoice(cp, invoice["invoice_id"], m.from_user.id, dw, m.bot))
        except Exception as e:
            await m.answer(f"Ошибка при создании счёта: {e}")

async def poll_crypto_invoice(cp, invoice_id, user_id, dw, bot):
    await asyncio.sleep(5)
    while True:
        try:
            invoices = await cp.get_invoices(invoice_ids=[invoice_id])
            if invoices and invoices[0]["status"] == "paid":
                await add_balance(user_id, dw)
                try:
                    await bot.send_message(
                        user_id,
                        f"✅ Оплата через CryptoBot получена! Баланс пополнен на {dw} dw.",
                        reply_markup=main_menu()
                    )
                except Exception:
                    pass
                break
        except Exception:
            pass
        await asyncio.sleep(10)

# ---------- XROCKET ----------
@router.callback_query(F.data == "topup_xrocket")
async def topup_xrocket(c: CallbackQuery, state: FSMContext):
    await state.set_state(TopUp.xrocket_amount)
    await c.message.edit_text(
        "🚀 Введите сумму в dw для пополнения через xRocket:\n\n"
        "1 dw = 0.2 USDT",
        reply_markup=back_kb()
    )

@router.message(TopUp.xrocket_amount)
async def topup_xrocket_amount(m: Message, state: FSMContext):
    try:
        dw = int(m.text.strip())
        if dw <= 0: raise ValueError
    except ValueError:
        await m.answer("Введите целое положительное число.")
        return

    usdt = round(dw * CRYPTO_RATE, 2)
    client = xRocketClient(api_key=XROCKET_API_KEY)
    
    try:
        invoice = await client.create_invoice(
            amount=usdt,
            currency="USDT",
            description=f"Пополнение на {dw} dw ({usdt} USDT)",
            payload=f"xrocket:{m.from_user.id}:{dw}",
            num_payments=1,
            min_payment=0
        )
        pay_url = invoice.link
        await m.answer(
            f"🚀 Счёт на {dw} dw создан.\n"
            f"Сумма к оплате: {usdt} USDT\n"
            f"Оплатите по ссылке: {pay_url}\n\n"
            f"После оплаты баланс пополнится автоматически.",
            reply_markup=main_menu()
        )
        asyncio.create_task(poll_xrocket_invoice(client, invoice.id, m.from_user.id, dw, m.bot))
    except Exception as e:
        await m.answer(f"Ошибка при создании счёта: {e}")

async def poll_xrocket_invoice(client, invoice_id, user_id, dw, bot):
    await asyncio.sleep(5)
    while True:
        try:
            # Используем get_invoices вместо get_invoice во избежание ошибок
            invoices = await client.get_invoices(invoice_ids=[invoice_id])
            if invoices and invoices[0].status == "paid":
                await add_balance(user_id, dw)
                try:
                    await bot.send_message(
                        user_id,
                        f"✅ Оплата через xRocket получена! Баланс пополнен на {dw} dw.",
                        reply_markup=main_menu()
                    )
                except Exception:
                    pass
                break
        except Exception:
            pass
        await asyncio.sleep(10)

# ================== SN0S ==================
@router.callback_query(F.data == "order")
async def order_start(c: CallbackQuery, state: FSMContext):
    await state.set_state(Order.target)
    await c.message.edit_text(
        "sn0s\n\nУкажите user/ID пользователя, которому он хочет отписать для работы:",
        reply_markup=back_kb()
    )

@router.message(Order.target)
async def order_target(m: Message, state: FSMContext):
    await state.update_data(target=m.text.strip())
    await state.set_state(Order.speed)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Медленно — 1 dw (48 часов)", callback_data="speed_slow")],
        [InlineKeyboardButton(text="Быстро — 3 dw (12 часов)", callback_data="speed_fast")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
    ])
    await m.answer("Выберите скорость:", reply_markup=kb)

@router.callback_query(F.data.startswith("speed_"), Order.speed)
async def order_speed(c: CallbackQuery, state: FSMContext):
    speed = "медленно" if c.data == "speed_slow" else "быстро"
    price = 1 if c.data == "speed_slow" else 3
    await state.update_data(speed=speed, speed_price=price)
    await state.set_state(Order.complaints)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="50 (+0 dw)",  callback_data="cmp_50_0")],
        [InlineKeyboardButton(text="100 (+1 dw)", callback_data="cmp_100_1")],
        [InlineKeyboardButton(text="150 (+2 dw)", callback_data="cmp_150_2")],
        [InlineKeyboardButton(text="300 (+3 dw)", callback_data="cmp_300_3")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
    ])
    await c.message.edit_text(
        f"Сколько жалоб?\n\nСкорость: {speed} ({price} dw)",
        reply_markup=kb
    )

async def process_order(bot, user, data):
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (data["total"], user.id))
        await db.execute(
            "INSERT INTO orders(user_id,target,speed,complaints,price) VALUES(?,?,?,?,?)",
            (user.id, data["target"], data["speed"], data["complaints"], data["total"])
        )
        await db.execute("UPDATE users SET orders=orders+1 WHERE user_id=?", (user.id,))
        await db.commit()
    try:
        await bot.send_message(
            ADMIN_ID,
            f"📦 Новый sn0s\n"
            f"От: @{user.username} ({user.id})\n"
            f"Цель: {data['target']}\n"
            f"Скорость: {data['speed']}\n"
            f"Жалоб: {data['complaints']}\n"
            f"Оплачено: {data['total']} dw"
        )
    except Exception:
        pass

@router.callback_query(F.data.startswith("cmp_"), Order.complaints)
async def order_complaints(c: CallbackQuery, state: FSMContext):
    _, count, extra = c.data.split("_")
    count, extra = int(count), int(extra)
    data = await state.get_data()
    total = data["speed_price"] + extra
    data["complaints"] = count
    data["total"] = total

    user = await get_user(c.from_user.id)
    if user["balance"] < total:
        missing = total - user["balance"]
        await state.update_data(pending_order=data)
        await state.set_state(TopUp.amount)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="topup_menu")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
        ])
        await c.message.edit_text(
            f"❗ Не хватает {missing} dw\n\n"
            f"💳 Введите сумму в dw, которую хотите получить:\n1 dw = 10 ⭐",
            reply_markup=kb
        )
        return

    await process_order(c.bot, c.from_user, data)
    await state.clear()
    await c.message.edit_text(THANKS, reply_markup=main_menu())

# ================== ПОДДЕРЖКА ==================
@router.callback_query(F.data == "support")
async def support(c: CallbackQuery, state: FSMContext):
    await state.set_state(Support.text)
    await c.message.edit_text(
        "Здравствуйте, напишите ваше обращение, и оно будет передано администратору.",
        reply_markup=back_kb()
    )

@router.message(Support.text)
async def support_text(m: Message, state: FSMContext):
    await state.clear()
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("INSERT INTO tickets(user_id,text) VALUES(?,?)", (m.from_user.id, m.text))
        await db.commit()
        tid = cur.lastrowid
    await m.answer(
        "Спасибо за ваше обращение. Ожидайте ответа прямо в бота, обычно заявки обрабатываются в течение 24 часов.",
        reply_markup=main_menu()
    )
    try:
        await m.bot.send_message(
            ADMIN_ID,
            f"🎫 Обращение #{tid}\nОт @{m.from_user.username} ({m.from_user.id}):\n\n{m.text}"
        )
    except Exception:
        pass

# ================== АДМИН-ПАНЕЛЬ ==================
@router.message(Command("admin"))
async def admin_cmd(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    await m.answer("🛠 Админ-панель", reply_markup=admin_kb())

@router.callback_query(F.data == "admin_give")
async def admin_give(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID: return
    await state.set_state(Admin.give_id)
    await c.message.edit_text("Введите ID пользователя:", reply_markup=back_kb())

@router.message(Admin.give_id)
async def admin_give_id(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    try:
        uid = int(m.text.strip())
    except ValueError:
        await m.answer("Некорректный ID.")
        return
    await state.update_data(give_id=uid)
    await state.set_state(Admin.give_amount)
    await m.answer("Введите сумму в dw:")

@router.message(Admin.give_amount)
async def admin_give_amount(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    try:
        amount = int(m.text.strip())
    except ValueError:
        await m.answer("Некорректная сумма.")
        return
    data = await state.get_data()
    await ensure_user(data["give_id"], "")
    await add_balance(data["give_id"], amount)
    await state.clear()
    await m.answer(
        f"✅ Выдано {amount} dw пользователю {data['give_id']}",
        reply_markup=admin_kb()
    )

@router.callback_query(F.data == "admin_promo")
async def admin_promo(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID: return
    await state.set_state(Admin.promo_code)
    await c.message.edit_text("Введите код промокода:", reply_markup=back_kb())

@router.message(Admin.promo_code)
async def admin_promo_code(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    await state.update_data(promo_code=m.text.strip())
    await state.set_state(Admin.promo_amount)
    await m.answer("Введите сумму в dw:")

@router.message(Admin.promo_amount)
async def admin_promo_amount(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    try:
        amount = int(m.text.strip())
    except ValueError:
        await m.answer("Некорректная сумма.")
        return
    await state.update_data(promo_amount=amount)
    await state.set_state(Admin.promo_uses)
    await m.answer("Введите количество использований:")

@router.message(Admin.promo_uses)
async def admin_promo_uses(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    try:
        uses = int(m.text.strip())
    except ValueError:
        await m.answer("Некорректное число.")
        return
    data = await state.get_data()
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO promos(code,amount,uses_left) VALUES(?,?,?)",
            (data["promo_code"], data["promo_amount"], uses)
        )
        await db.commit()
    await state.clear()
    await m.answer(
        f"✅ Промокод создан: {data['promo_code']} | {data['promo_amount']} dw | {uses} исп.",
        reply_markup=admin_kb()
    )

@router.callback_query(F.data == "admin_users")
async def admin_users(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users ORDER BY balance DESC LIMIT 50") as cur:
            rows = await cur.fetchall()
    if not rows:
        await c.message.edit_text("Пользователей нет.", reply_markup=admin_kb())
        return
    text = "👥 Пользователи (топ-50 по балансу):\n\n"
    for r in rows:
        text += f"@{r['username'] or '—'} | ID: {r['user_id']} | {r['balance']} dw | sn0s: {r['orders']}\n"
    if len(text) > 4000:
        text = text[:4000] + "\n…"
    await c.message.edit_text(text, reply_markup=admin_kb())

@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID: return
    await state.set_state(Admin.broadcast)
    await c.message.edit_text(
        "📢 Введите текст рассылки (отправится всем пользователям):",
        reply_markup=back_kb()
    )

@router.message(Admin.broadcast)
async def admin_broadcast_send(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    await state.clear()
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT user_id FROM users") as cur:
            uids = [r[0] for r in await cur.fetchall()]
    ok, fail = 0, 0
    for uid in uids:
        try:
            await m.bot.send_message(uid, m.text)
            ok += 1
        except Exception:
            fail += 1
    await m.answer(
        f"✅ Рассылка завершена\nУспешно: {ok}\nОшибок: {fail}",
        reply_markup=admin_kb()
    )

@router.callback_query(F.data == "admin_tickets")
async def admin_tickets(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tickets ORDER BY id DESC LIMIT 20") as cur:
            rows = await cur.fetchall()
    if not rows:
        await c.message.edit_text("Заявок нет.", reply_markup=admin_kb())
        return
    text = "🎫 Последние заявки:\n\n"
    for r in rows:
        text += f"#{r['id']} от {r['user_id']} [{r['created_at']}]:\n{r['text']}\n\n"
    if len(text) > 4000:
        text = text[:4000] + "\n…"
    await c.message.edit_text(text, reply_markup=admin_kb())

# ================== ЗАПУСК ==================
async def main():
    await init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    print("Бот запущен и работает!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
