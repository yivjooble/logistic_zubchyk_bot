"""Telegram bot for PUESC RMPD-406 location monitoring."""
import os
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

import database as db
from scraper import PUESCScraper, LocationResult

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Parse allowed user IDs (comma-separated)
def parse_allowed_users() -> set[int]:
    """Parse ALLOWED_USER_IDS from environment variable."""
    ids_str = os.getenv('ALLOWED_USER_IDS', '')
    if not ids_str:
        # Fallback to old single-user variable for backwards compatibility
        single_id = os.getenv('ALLOWED_USER_ID', '')
        if single_id:
            return {int(single_id)}
        return set()
    
    try:
        return {int(uid.strip()) for uid in ids_str.split(',') if uid.strip()}
    except ValueError:
        return set()

ALLOWED_USER_IDS = parse_allowed_users()

# Conversation states
ADD_NAME, ADD_REFERENCE, ADD_REGISTRATION, ADD_LOCATOR, EDIT_RMPD = range(5)

# Kyiv timezone
KYIV_TZ = ZoneInfo("Europe/Kyiv")

# Overdue threshold in minutes
OVERDUE_THRESHOLD_MINUTES = 45


def format_interval(enabled: bool, interval: int) -> str:
    """Format monitoring interval for display."""
    if not enabled:
        return "вимкнено"
    if interval == 720:
        return "2 рази на добу"
    if interval == 360:
        return "кожні 6 год"
    return f"{interval} хв"

# Global scraper instance
scraper: PUESCScraper = None


def auth_required(func):
    """Decorator to check if user is authorized."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
            await update.message.reply_text("⛔ Доступ заборонено.")
            return
        return await func(update, context)
    return wrapper


# === Command Handlers ===

@auth_required
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - show main menu."""
    keyboard = [
        [InlineKeyboardButton("📋 Список авто", callback_data="list")],
        [InlineKeyboardButton("➕ Додати авто", callback_data="add")],
        [InlineKeyboardButton("🗑 Видалити авто", callback_data="delete")],
        [InlineKeyboardButton("⏰ Налаштувати моніторинг", callback_data="schedule")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🚛 *PUESC RMPD-406 Монітор*\n\n"
        "Команди:\n"
        "/list - Список авто\n"
        "/add - Додати авто\n"
        "/delete - Видалити авто\n"
        "/schedule - Налаштувати моніторинг",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )


@auth_required
async def list_vehicles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all vehicles with check buttons."""
    vehicles = await db.get_all_vehicles()

    if not vehicles:
        await update.message.reply_text(
            "📭 Список авто порожній.\n\nВикористайте /add щоб додати авто."
        )
        return

    text = "🚛 *Ваші авто:*\n\n"
    keyboard = []
    
    # Add "Check all" button at the top if there are multiple vehicles
    if len(vehicles) > 1:
        keyboard.append([
            InlineKeyboardButton("🔄 Перевірити всіх", callback_data="check_all")
        ])

    for v in vehicles:
        text += (
            f"*{v.id}. {v.name}*\n"
            f"   📝 `{v.reference_number}`\n"
            f"   🚗 `{v.registration_number}`\n"
            f"   📡 `{v.locator_id}`\n\n"
        )
        keyboard.append([
            InlineKeyboardButton(f"📍 {v.name}", callback_data=f"check_{v.id}"),
            InlineKeyboardButton("✏️", callback_data=f"edit_menu_{v.id}")
        ])

    await update.message.reply_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


@auth_required
async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of vehicles to delete."""
    vehicles = await db.get_all_vehicles()

    if not vehicles:
        await update.message.reply_text("📭 Немає авто для видалення.")
        return

    keyboard = [
        [InlineKeyboardButton(f"🗑 {v.name} ({v.registration_number})", callback_data=f"del_{v.id}")]
        for v in vehicles
    ]
    keyboard.append([InlineKeyboardButton("❌ Скасувати", callback_data="cancel_delete")])

    await update.message.reply_text(
        "🗑 *Виберіть авто для видалення:*",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


@auth_required
async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of vehicles to configure monitoring."""
    vehicles = await db.get_all_vehicles()

    if not vehicles:
        await update.message.reply_text("📭 Спочатку додайте авто через /add")
        return

    text = "⏰ *Налаштування моніторингу*\n\n"
    keyboard = []

    for v in vehicles:
        status = "✅" if v.monitoring_enabled else "❌"
        interval = format_interval(v.monitoring_enabled, v.monitoring_interval)
        text += f"*{v.name}* ({v.registration_number})\n   Моніторинг: {status} {interval}\n\n"
        keyboard.append([
            InlineKeyboardButton(f"⚙️ {v.name}", callback_data=f"sched_{v.id}")
        ])

    keyboard.append([InlineKeyboardButton("❌ Закрити", callback_data="cancel_schedule")])

    await update.message.reply_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# === Add Vehicle Conversation ===

@auth_required
async def add_vehicle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start adding a new vehicle."""
    await update.message.reply_text(
        "➕ *Додавання нового авто*\n\n"
        "Введіть *назву* для цього авто (напр. 'MAN 1', 'Volvo синій'):",
        parse_mode='Markdown'
    )
    return ADD_NAME


async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save name and ask for reference number."""
    context.user_data['name'] = update.message.text
    await update.message.reply_text(
        "Введіть *номер RMPD* (numer referencyjny):",
        parse_mode='Markdown'
    )
    return ADD_REFERENCE


async def add_reference(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save reference and ask for registration number."""
    context.user_data['reference'] = update.message.text
    await update.message.reply_text(
        "Введіть *номер авто* (numer rejestracyjny):",
        parse_mode='Markdown'
    )
    return ADD_REGISTRATION


async def add_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save registration and ask for locator ID."""
    context.user_data['registration'] = update.message.text
    await update.message.reply_text(
        "Введіть *ID GPS локатора* (numer lokalizatora):",
        parse_mode='Markdown'
    )
    return ADD_LOCATOR


async def add_locator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save locator and create vehicle."""
    locator = update.message.text
    data = context.user_data

    vehicle_id = await db.add_vehicle(
        data['name'],
        data['reference'],
        data['registration'],
        locator
    )

    keyboard = [[
        InlineKeyboardButton("📍 Перевірити зараз", callback_data=f"check_{vehicle_id}")
    ]]

    await update.message.reply_text(
        f"✅ *Авто додано!*\n\n"
        f"🚛 *Назва:* {data['name']}\n"
        f"📝 *RMPD:* `{data['reference']}`\n"
        f"🚗 *Номер:* `{data['registration']}`\n"
        f"📡 *GPS:* `{locator}`",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel conversation."""
    context.user_data.clear()
    await update.message.reply_text("❌ Скасовано.")
    return ConversationHandler.END


# === Edit RMPD Conversation ===

async def edit_rmpd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start editing RMPD for a vehicle (called from callback)."""
    query = update.callback_query
    await query.answer()
    
    vehicle_id = int(query.data.split("_")[2])
    vehicle = await db.get_vehicle(vehicle_id)
    
    if not vehicle:
        await query.edit_message_text("❌ Авто не знайдено.")
        return ConversationHandler.END
    
    context.user_data['edit_vehicle_id'] = vehicle_id
    context.user_data['edit_vehicle_name'] = vehicle.name
    
    await query.edit_message_text(
        f"✏️ *Редагування RMPD для {vehicle.name}*\n\n"
        f"Поточний номер: `{vehicle.reference_number}`\n\n"
        f"Введіть новий номер RMPD (або /cancel для скасування):",
        parse_mode='Markdown'
    )
    return EDIT_RMPD


async def edit_rmpd_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save the new RMPD number."""
    new_rmpd = update.message.text
    vehicle_id = context.user_data.get('edit_vehicle_id')
    vehicle_name = context.user_data.get('edit_vehicle_name', 'Авто')
    
    if not vehicle_id:
        await update.message.reply_text("❌ Помилка: ID авто не знайдено.")
        return ConversationHandler.END
    
    await db.update_vehicle_reference(vehicle_id, new_rmpd)
    
    keyboard = [[
        InlineKeyboardButton("📍 Перевірити зараз", callback_data=f"check_{vehicle_id}")
    ]]
    
    await update.message.reply_text(
        f"✅ *RMPD оновлено!*\n\n"
        f"🚛 *{vehicle_name}*\n"
        f"📝 Новий RMPD: `{new_rmpd}`",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    context.user_data.clear()
    return ConversationHandler.END


# === Location Check ===

async def perform_check(message, vehicle_id: int, edit: bool = False):
    """Perform location check for a vehicle."""
    vehicle = await db.get_vehicle(vehicle_id)

    if not vehicle:
        text = "❌ Авто не знайдено."
        if edit:
            await message.edit_text(text)
        else:
            await message.reply_text(text)
        return

    if edit:
        await message.edit_text(f"🔍 Перевіряю *{vehicle.name}*...", parse_mode='Markdown')
    else:
        message = await message.reply_text(f"🔍 Перевіряю *{vehicle.name}*...", parse_mode='Markdown')

    result = await scraper.check_location(
        vehicle.reference_number,
        vehicle.registration_number,
        vehicle.locator_id
    )

    if result.success:
        await db.update_vehicle_location(
            vehicle_id,
            f"{result.latitude}, {result.longitude}",
            result.location_time or datetime.now().isoformat()
        )

    text = format_location_result(result, vehicle.name, vehicle.registration_number)

    try:
        await message.edit_text(text, parse_mode='Markdown', disable_web_page_preview=False)
    except Exception as e:
        # Fallback without markdown if parsing fails
        logger.error(f"Markdown error: {e}")
        await message.edit_text(text.replace('*', '').replace('_', ''), disable_web_page_preview=False)

    # Send separate alert if data is overdue
    if result.success and check_overdue(result.location_time):
        maps_link = f"https://maps.google.com/?q={result.latitude},{result.longitude}"
        alert_text = (
            f"🚨🚨🚨 *УВАГА: ПРОТЕРМІНУВАННЯ!* 🚨🚨🚨\n\n"
            f"🚛 *{vehicle.name}*\n"
            f"🚗 *Номер:* `{vehicle.registration_number}`\n\n"
            f"⏰ Останнє оновлення: {result.location_time}\n"
            f"⚠️ *Дані застаріли більше 45 хвилин!*\n\n"
            f"📍 Остання позиція:\n"
            f"Широта: {result.latitude}\n"
            f"Довгота: {result.longitude}\n\n"
            f"[🗺 Відкрити в Google Maps]({maps_link})"
        )
        try:
            # Send as a new separate message to ensure user sees it
            await message.chat.send_message(
                text=alert_text,
                parse_mode='Markdown',
                disable_web_page_preview=False
            )
        except Exception as e:
            logger.error(f"Failed to send overdue alert: {e}")


async def perform_check_all(message):
    """Perform location check for all vehicles."""
    vehicles = await db.get_all_vehicles()
    
    if not vehicles:
        await message.edit_text("📭 Немає авто для перевірки.")
        return
    
    await message.edit_text(f"🔄 Перевіряю *{len(vehicles)} авто*...", parse_mode='Markdown')
    
    results = []
    for vehicle in vehicles:
        result = await scraper.check_location(
            vehicle.reference_number,
            vehicle.registration_number,
            vehicle.locator_id
        )
        
        if result.success:
            await db.update_vehicle_location(
                vehicle.id,
                f"{result.latitude}, {result.longitude}",
                result.location_time or datetime.now().isoformat()
            )
            
            # Check if overdue
            overdue = check_overdue(result.location_time)
            status = "🚨" if overdue else "✅"
            maps_link = f"https://maps.google.com/?q={result.latitude},{result.longitude}"
            
            results.append(
                f"{status} *{vehicle.name}* ({vehicle.registration_number})\n"
                f"   🕐 {result.location_time or 'Невідомо'}\n"
                f"   [📍 Карта]({maps_link})"
            )
        else:
            results.append(
                f"❌ *{vehicle.name}* ({vehicle.registration_number})\n"
                f"   Помилка: {result.error or 'Невідома'}"
            )
    
    text = "🚛 *Результати перевірки всіх авто:*\n\n" + "\n\n".join(results)
    
    # Add legend
    text += "\n\n_Легенда: ✅ OK | 🚨 Дані застаріли >45хв_"
    
    try:
        await message.edit_text(text, parse_mode='Markdown', disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Markdown error in check_all: {e}")
        await message.edit_text(text.replace('*', '').replace('_', ''), disable_web_page_preview=True)


def parse_location_time(time_str: str) -> datetime | None:
    """Parse location time string to datetime object."""
    if not time_str:
        return None

    # Format: "28.12.2025, год.18:42:19"
    match = re.search(r'(\d{2})\.(\d{2})\.(\d{4}),?\s*год\.(\d{2}):(\d{2}):(\d{2})', time_str)
    if match:
        day, month, year, hour, minute, second = map(int, match.groups())
        return datetime(year, month, day, hour, minute, second, tzinfo=KYIV_TZ)

    return None


def check_overdue(time_str: str) -> bool:
    """Check if location time is more than 45 minutes old."""
    location_time = parse_location_time(time_str)
    if not location_time:
        return False

    now_kyiv = datetime.now(KYIV_TZ)
    diff = now_kyiv - location_time
    return diff > timedelta(minutes=OVERDUE_THRESHOLD_MINUTES)


def format_location_result(result: LocationResult, name: str, registration: str) -> str:
    """Format location result for display in Ukrainian."""
    if result.success:
        maps_link = f"https://maps.google.com/?q={result.latitude},{result.longitude}"

        # Check if location is overdue
        overdue_warning = ""
        if check_overdue(result.location_time):
            overdue_warning = "\n\n⚠️ *УВАГА: Дані застаріли більше 45 хв!*"

        return (
            f"🚛 *{name}*\n"
            f"🚗 *Номер:* `{registration}`\n\n"
            f"📍 *Широта:* {result.latitude}\n"
            f"📍 *Довгота:* {result.longitude}\n"
            f"🕐 *Час оновлення:* {result.location_time or 'Невідомо'}"
            f"{overdue_warning}\n\n"
            f"[🗺 Відкрити в Google Maps]({maps_link})"
        )
    else:
        return f"🚛 *{name}*\n🚗 *Номер:* `{registration}`\n\n❌ *Помилка:* {result.error or 'Невідома помилка'}"


# === Callback Query Handler ===

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button presses."""
    query = update.callback_query
    await query.answer()

    # Check authorization
    if ALLOWED_USER_IDS and query.from_user.id not in ALLOWED_USER_IDS:
        await query.edit_message_text("⛔ Доступ заборонено.")
        return

    data = query.data

    if data == "list":
        vehicles = await db.get_all_vehicles()
        if not vehicles:
            await query.edit_message_text("📭 Список авто порожній.\n\nВикористайте /add щоб додати авто.")
            return

        text = "🚛 *Ваші авто:*\n\n"
        keyboard = []
        for v in vehicles:
            text += f"*{v.id}. {v.name}* - `{v.registration_number}`\n"
            keyboard.append([
                InlineKeyboardButton(f"📍 Перевірити {v.name}", callback_data=f"check_{v.id}")
            ])

        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "add":
        await query.edit_message_text("Використайте команду /add щоб додати нове авто.")

    elif data == "delete":
        vehicles = await db.get_all_vehicles()
        if not vehicles:
            await query.edit_message_text("📭 Немає авто для видалення.")
            return

        keyboard = [
            [InlineKeyboardButton(f"🗑 {v.name} ({v.registration_number})", callback_data=f"del_{v.id}")]
            for v in vehicles
        ]
        keyboard.append([InlineKeyboardButton("❌ Скасувати", callback_data="cancel_delete")])

        await query.edit_message_text(
            "🗑 *Виберіть авто для видалення:*",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "check_all":
        await perform_check_all(query.message)

    elif data.startswith("edit_menu_"):
        vehicle_id = int(data.split("_")[2])
        vehicle = await db.get_vehicle(vehicle_id)
        
        if not vehicle:
            await query.edit_message_text("❌ Авто не знайдено.")
            return
        
        keyboard = [
            [InlineKeyboardButton("✏️ Змінити RMPD", callback_data=f"edit_rmpd_{vehicle_id}")],
            [InlineKeyboardButton("⬅️ Назад до списку", callback_data="list")],
        ]
        
        await query.edit_message_text(
            f"✏️ *Редагування: {vehicle.name}*\n\n"
            f"📝 RMPD: `{vehicle.reference_number}`\n"
            f"🚗 Номер: `{vehicle.registration_number}`\n"
            f"📡 GPS: `{vehicle.locator_id}`\n\n"
            "Що бажаєте змінити?",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("check_"):
        vehicle_id = int(data.split("_")[1])
        await perform_check(query.message, vehicle_id, edit=True)

    elif data.startswith("del_"):
        vehicle_id = int(data.split("_")[1])
        vehicle = await db.get_vehicle(vehicle_id)

        keyboard = [
            [
                InlineKeyboardButton("✅ Так, видалити", callback_data=f"confirm_del_{vehicle_id}"),
                InlineKeyboardButton("❌ Ні", callback_data="cancel_delete"),
            ]
        ]

        await query.edit_message_text(
            f"❓ Видалити *{vehicle.name}* ({vehicle.registration_number})?",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("confirm_del_"):
        vehicle_id = int(data.split("_")[2])
        vehicle = await db.get_vehicle(vehicle_id)
        name = vehicle.name if vehicle else "Авто"

        await db.delete_vehicle(vehicle_id)
        await query.edit_message_text(f"✅ *{name}* видалено.")

    elif data == "cancel_delete":
        await query.edit_message_text("❌ Скасовано.")

    elif data == "schedule":
        vehicles = await db.get_all_vehicles()
        if not vehicles:
            await query.edit_message_text("📭 Спочатку додайте авто через /add")
            return

        text = "⏰ *Налаштування моніторингу*\n\n"
        keyboard = []

        for v in vehicles:
            status = "✅" if v.monitoring_enabled else "❌"
            interval = format_interval(v.monitoring_enabled, v.monitoring_interval)
            text += f"*{v.name}* ({v.registration_number})\n   Моніторинг: {status} {interval}\n\n"
            keyboard.append([
                InlineKeyboardButton(f"⚙️ {v.name}", callback_data=f"sched_{v.id}")
            ])

        keyboard.append([InlineKeyboardButton("❌ Закрити", callback_data="cancel_schedule")])

        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("sched_"):
        vehicle_id = int(data.split("_")[1])
        vehicle = await db.get_vehicle(vehicle_id)

        if not vehicle:
            await query.edit_message_text("❌ Авто не знайдено.")
            return

        status = "✅ Увімкнено" if vehicle.monitoring_enabled else "❌ Вимкнено"
        current_interval = format_interval(True, vehicle.monitoring_interval)

        keyboard = [
            [InlineKeyboardButton("⏱ 15 хв", callback_data=f"setint_{vehicle_id}_15")],
            [InlineKeyboardButton("⏱ 30 хв", callback_data=f"setint_{vehicle_id}_30")],
            [InlineKeyboardButton("⏱ 60 хв", callback_data=f"setint_{vehicle_id}_60")],
            [InlineKeyboardButton("🕕 Кожні 6 год", callback_data=f"setint_{vehicle_id}_360")],
            [InlineKeyboardButton("🌅 2 рази на добу", callback_data=f"setint_{vehicle_id}_720")],
            [InlineKeyboardButton("🔴 Вимкнути", callback_data=f"setint_{vehicle_id}_0")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="schedule")],
        ]

        await query.edit_message_text(
            f"⚙️ *Моніторинг: {vehicle.name}*\n"
            f"🚗 Номер: `{vehicle.registration_number}`\n\n"
            f"Поточний статус: {status}\n"
            f"Інтервал: {current_interval}\n\n"
            "Виберіть інтервал перевірки:",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("setint_"):
        parts = data.split("_")
        vehicle_id = int(parts[1])
        interval = int(parts[2])

        vehicle = await db.get_vehicle(vehicle_id)
        if not vehicle:
            await query.edit_message_text("❌ Авто не знайдено.")
            return

        if interval == 0:
            await db.update_monitoring(vehicle_id, enabled=False)
            # Reschedule monitoring jobs
            await schedule_vehicle_jobs(context.application)
            await query.edit_message_text(
                f"🔴 Моніторинг для *{vehicle.name}* вимкнено.",
                parse_mode='Markdown'
            )
        else:
            await db.update_monitoring(vehicle_id, enabled=True, interval=interval)
            # Reschedule monitoring jobs
            await schedule_vehicle_jobs(context.application)
            interval_text = format_interval(True, interval)
            await query.edit_message_text(
                f"✅ Моніторинг для *{vehicle.name}* увімкнено.\n"
                f"Інтервал перевірки: *{interval_text}*\n\n"
                f"Якщо дані застаріють більше 45 хв - отримаєте сповіщення.",
                parse_mode='Markdown'
            )

    elif data == "cancel_schedule":
        await query.edit_message_text("❌ Закрито.")


# === Background Monitoring ===

async def monitoring_job(context: ContextTypes.DEFAULT_TYPE):
    """Background job to check monitored vehicles and send alerts."""
    vehicles = await db.get_monitored_vehicles()

    if not vehicles:
        return

    for vehicle in vehicles:
        try:
            logger.info(f"Monitoring check for {vehicle.name}")

            result = await scraper.check_location(
                vehicle.reference_number,
                vehicle.registration_number,
                vehicle.locator_id
            )

            if result.success:
                # Update location in database
                await db.update_vehicle_location(
                    vehicle.id,
                    f"{result.latitude}, {result.longitude}",
                    result.location_time or datetime.now().isoformat()
                )

                # Check if overdue
                if check_overdue(result.location_time):
                    # Send alert
                    maps_link = f"https://maps.google.com/?q={result.latitude},{result.longitude}"
                    alert_text = (
                        f"🚨 *УВАГА: Протермінування!*\n\n"
                        f"🚛 *{vehicle.name}*\n"
                        f"🚗 *Номер:* `{vehicle.registration_number}`\n\n"
                        f"⏰ Останнє оновлення: {result.location_time}\n"
                        f"⚠️ Дані застаріли більше 45 хвилин!\n\n"
                        f"📍 Остання позиція:\n"
                        f"Широта: {result.latitude}\n"
                        f"Довгота: {result.longitude}\n\n"
                        f"[🗺 Відкрити в Google Maps]({maps_link})"
                    )

                    # Send alert to all allowed users
                    for user_id in ALLOWED_USER_IDS:
                        try:
                            await context.bot.send_message(
                                chat_id=user_id,
                                text=alert_text,
                                parse_mode='Markdown',
                                disable_web_page_preview=False
                            )
                            logger.info(f"Overdue alert sent for {vehicle.name} to user {user_id}")
                        except Exception as e:
                            logger.error(f"Failed to send alert for {vehicle.name} to user {user_id}: {e}")

        except Exception as e:
            logger.error(f"Error monitoring {vehicle.name}: {e}")


async def schedule_vehicle_jobs(application: Application):
    """Schedule monitoring jobs for all enabled vehicles."""
    job_queue = application.job_queue

    # Remove existing vehicle monitoring jobs
    current_jobs = job_queue.get_jobs_by_name("vehicle_monitor")
    for job in current_jobs:
        job.schedule_removal()

    # Get monitored vehicles and schedule new jobs
    vehicles = await db.get_monitored_vehicles()

    if vehicles:
        # Use the minimum interval from all monitored vehicles
        min_interval = min(v.monitoring_interval for v in vehicles)
        job_queue.run_repeating(
            monitoring_job,
            interval=min_interval * 60,  # Convert minutes to seconds
            first=60,  # Start after 1 minute
            name="vehicle_monitor"
        )
        logger.info(f"Monitoring scheduled: {len(vehicles)} vehicles, interval: {min_interval} min")


# === Main ===

async def post_init(application: Application):
    """Initialize after bot starts."""
    global scraper

    # Initialize database
    await db.init_db()

    # Start scraper
    scraper = PUESCScraper()
    await scraper.start()

    # Schedule monitoring jobs
    await schedule_vehicle_jobs(application)

    logger.info("Bot initialized")


async def post_shutdown(application: Application):
    """Cleanup on shutdown."""
    global scraper

    if scraper:
        await scraper.stop()

    logger.info("Bot shutdown")


def main():
    """Start the bot."""
    if not TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not set")
        return

    if not ALLOWED_USER_IDS:
        print("Warning: ALLOWED_USER_IDS not set - bot is open to everyone!")
    else:
        print(f"Allowed users: {ALLOWED_USER_IDS}")

    # Create application
    app = Application.builder().token(TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()

    # Add conversation handler for adding vehicles
    add_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_vehicle_start)],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
            ADD_REFERENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_reference)],
            ADD_REGISTRATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_registration)],
            ADD_LOCATOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_locator)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    # Add conversation handler for editing RMPD
    edit_rmpd_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_rmpd_start, pattern=r"^edit_rmpd_\d+$")],
        states={
            EDIT_RMPD: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_rmpd_save)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_vehicles))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(add_handler)
    app.add_handler(edit_rmpd_handler)
    app.add_handler(CallbackQueryHandler(button_handler))

    # Run the bot
    logger.info("Starting bot...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
