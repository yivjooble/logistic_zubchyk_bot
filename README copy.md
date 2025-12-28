# PUESC SENT-406 Telegram Bot

Telegram бот для моніторингу геолокації транспортних засобів через польську систему PUESC SENT-406.

## Можливості

- Додавання до 4 транспортних засобів
- Перевірка локації за запитом
- Автоматичний моніторинг з налаштовуваним інтервалом (15 хв - 2 год)
- Сповіщення в Telegram при кожній перевірці
- Історія перевірок в базі даних

## Команди бота

| Команда | Опис |
|---------|------|
| `/start` | Головне меню |
| `/list` | Список всіх авто |
| `/add` | Додати нове авто |
| `/check <id>` | Перевірити локацію |
| `/check_all` | Перевірити всі авто |
| `/monitor <id>` | Налаштувати моніторинг |
| `/delete <id>` | Видалити авто |
| `/status` | Статус моніторингу |
| `/q REF REG LOC` | Швидка одноразова перевірка |

## Встановлення

### 1. Створіть Telegram бота

1. Напишіть [@BotFather](https://t.me/BotFather) в Telegram
2. Надішліть `/newbot`
3. Виберіть ім'я та username для бота
4. Збережіть токен бота

### 2. Отримайте свій User ID

1. Напишіть [@userinfobot](https://t.me/userinfobot) в Telegram
2. Збережіть ваш ID

### 3. Налаштуйте змінні середовища

```bash
cp .env.example .env
```

Відредагуйте `.env`:
```
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USER_ID=your_user_id_here
```

## Запуск

### Docker (рекомендовано)

```bash
# Збірка та запуск
docker-compose up -d

# Перегляд логів
docker-compose logs -f

# Зупинка
docker-compose down
```

### Локально (для тестування)

```bash
# Створіть віртуальне середовище
python -m venv venv
source venv/bin/activate  # Linux/Mac
# або
venv\Scripts\activate  # Windows

# Встановіть залежності
pip install -r requirements.txt

# Встановіть браузер Playwright
playwright install chromium

# Запустіть бота
python bot.py
```

## Деплой на сервер

### Варіант 1: VPS (Hetzner, DigitalOcean, etc.)

```bash
# На сервері
git clone <your-repo>
cd puesc-telegram-bot
cp .env.example .env
nano .env  # додайте токени

# Запуск
docker-compose up -d
```

### Варіант 2: Railway.app

1. Форкніть репозиторій на GitHub
2. Зайдіть на [railway.app](https://railway.app)
3. New Project → Deploy from GitHub repo
4. Додайте змінні середовища в Settings → Variables
5. Deploy

### Варіант 3: Fly.io

```bash
# Встановіть flyctl
curl -L https://fly.io/install.sh | sh

# Логін
fly auth login

# Створіть app
fly launch

# Додайте секрети
fly secrets set TELEGRAM_BOT_TOKEN=xxx ALLOWED_USER_ID=xxx

# Деплой
fly deploy
```

## Структура проекту

```
puesc-telegram-bot/
├── bot.py           # Головний файл бота
├── scraper.py       # Playwright scraper для PUESC
├── database.py      # SQLite база даних
├── requirements.txt # Python залежності
├── Dockerfile       # Docker конфігурація
├── docker-compose.yml
├── .env.example     # Приклад змінних середовища
└── data/            # Дані (база даних)
```

## Важливо

- Бот використовує web scraping для отримання даних з PUESC
- Використовуйте помірну частоту перевірок (не частіше 15 хв)
- Для особистого використання ризики мінімальні
- Дані зберігаються локально в SQLite

## Troubleshooting

### Бот не відповідає
- Перевірте токен бота в `.env`
- Перевірте логи: `docker-compose logs -f`

### Помилка "Could not find form fields"
- Структура сторінки PUESC могла змінитися
- Потрібно оновити селектори в `scraper.py`

### Timeout errors
- PUESC може бути повільним
- Спробуйте пізніше або збільшіть timeout в `scraper.py`

## Ліцензія

MIT - використовуйте на свій розсуд.
