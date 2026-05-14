import os
import time
import ccxt
import logging
from datetime import datetime
from telegram import Bot
from telegram.error import TelegramError
import matplotlib
matplotlib.use('Agg')  # Для работы без графического интерфейса
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO

# ═══════════════════════════════════════════════════════════════
# НАСТРОЙКИ
# ═══════════════════════════════════════════════════════════════

# Получаем данные из переменных окружения (секреты GitHub)
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

# Коэффициент увеличения объёма для отправки сигнала
VOLUME_SPIKE_MULTIPLIER = 50

# ═══════════════════════════════════════════════════════════════
# НАСТРОЙКА ЛОГИРОВАНИЯ
# ═══════════════════════════════════════════════════════════════

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# ПОДКЛЮЧЕНИЕ К БИРЖАМ
# ═══════════════════════════════════════════════════════════════

def init_exchanges():
    """Создаём подключения к биржам Binance и Bybit"""
    
    binance = ccxt.binance({
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })
    
    bybit = ccxt.bybit({
        'enableRateLimit': True,
        'options': {'defaultType': 'linear'}
    })
    
    return {'Binance': binance, 'Bybit': bybit}

# ═══════════════════════════════════════════════════════════════
# ПОЛУЧЕНИЕ СПИСКА USDT ПАР
# ═══════════════════════════════════════════════════════════════

def get_usdt_pairs(exchange, exchange_name):
    """Получаем все торговые пары с USDT"""
    
    try:
        markets = exchange.load_markets()
        usdt_pairs = [
            symbol for symbol in markets 
            if symbol.endswith('/USDT') and markets[symbol]['active']
        ]
        logger.info(f"{exchange_name}: найдено {len(usdt_pairs)} USDT пар")
        return usdt_pairs
    except Exception as e:
        logger.error(f"Ошибка получения пар с {exchange_name}: {e}")
        return []

# ═══════════════════════════════════════════════════════════════
# РАСЧЁТ СРЕДНЕГО ОБЪЁМА ЗА 24 ЧАСА
# ═══════════════════════════════════════════════════════════════

def calculate_avg_volume_24h(exchange, symbol):
    """Считаем средний объём за 24 часа"""
    
    try:
        since = exchange.milliseconds() - 24 * 60 * 60 * 1000
        ohlcv = exchange.fetch_ohlcv(symbol, '1m', since=since, limit=1440)
        
        if not ohlcv or len(ohlcv) < 100:
            return None
        
        volumes = [candle[5] for candle in ohlcv]
        avg_volume = sum(volumes) / len(volumes)
        
        return avg_volume
    
    except Exception as e:
        logger.debug(f"Ошибка расчёта объёма для {symbol}: {e}")
        return None

# ═══════════════════════════════════════════════════════════════
# ПОЛУЧЕНИЕ ДАННЫХ ЗА ПОСЛЕДНИЙ ЧАС
# ═══════════════════════════════════════════════════════════════

def get_last_hour_data(exchange, symbol):
    """Получаем данные за последний час"""
    
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, '1m', limit=60)
        
        if not ohlcv or len(ohlcv) < 2:
            return None
        
        last_candle = ohlcv[-1]
        last_volume = last_candle[5]
        
        price_hour_ago = ohlcv[0][4]
        current_price = last_candle[4]
        
        price_change = ((current_price - price_hour_ago) / price_hour_ago) * 100
        
        return {
            'last_volume': last_volume,
            'price_change': price_change,
            'current_price': current_price,
            'ohlcv': ohlcv
        }
    
    except Exception as e:
        logger.debug(f"Ошибка получения данных за час для {symbol}: {e}")
        return None

# ═══════════════════════════════════════════════════════════════
# СОЗДАНИЕ ГРАФИКА ЦЕНЫ
# ═══════════════════════════════════════════════════════════════

def create_price_chart(ohlcv_data, symbol):
    """Создаём график цены за последний час"""
    
    try:
        times = [datetime.fromtimestamp(candle[0] / 1000) for candle in ohlcv_data]
        closes = [candle[4] for candle in ohlcv_data]
        
        plt.figure(figsize=(10, 5))
        plt.plot(times, closes, linewidth=2, color='#2962FF')
        
        plt.title(f'{symbol} - Цена за последний час', fontsize=14, fontweight='bold')
        plt.xlabel('Время', fontsize=10)
        plt.ylabel('Цена (USDT)', fontsize=10)
        plt.grid(True, alpha=0.3)
        
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        buf.seek(0)
        plt.close()
        
        return buf
    
    except Exception as e:
        logger.error(f"Ошибка создания графика: {e}")
        return None

# ═══════════════════════════════════════════════════════════════
# ОТПРАВКА СИГНАЛА В TELEGRAM
# ═══════════════════════════════════════════════════════════════

def send_signal(bot, exchange_name, symbol, avg_volume, last_volume, price_change, current_price, chart):
    """Отправляем сигнал в Telegram"""
    
    try:
        coin = symbol.replace('/USDT', '')
        
        message = f"""
🚀 <b>СИГНАЛ: РЕЗКИЙ РОСТ ОБЪЁМА!</b>

💰 <b>Монета:</b> {coin}
📊 <b>Биржа:</b> {exchange_name}

📈 <b>Изменение цены за час:</b> {price_change:+.2f}%
💵 <b>Текущая цена:</b> ${current_price:.8f}

📊 <b>Объёмы:</b>
   • Средний за 24ч: {avg_volume:,.0f}
   • Последняя минута: {last_volume:,.0f}
   • <b>Увеличение в {last_volume/avg_volume:.1f} раз!</b>

⏰ <b>Время:</b> {datetime.now().strftime('%H:%M:%S')}
"""
        
        bot.send_message(
            chat_id=CHAT_ID,
            text=message,
            parse_mode='HTML'
        )
        
        if chart:
            bot.send_photo(
                chat_id=CHAT_ID,
                photo=chart,
                caption=f"График {coin} за последний час"
            )
        
        logger.info(f"✅ Сигнал отправлен: {exchange_name} - {symbol}")
        
    except TelegramError as e:
        logger.error(f"Ошибка отправки в Telegram: {e}")

# ═══════════════════════════════════════════════════════════════
# ПРОВЕРКА ОДНОЙ ПАРЫ
# ═══════════════════════════════════════════════════════════════

def check_pair(exchange, exchange_name, symbol, bot):
    """Проверяем одну торговую пару"""
    
    try:
        avg_volume = calculate_avg_volume_24h(exchange, symbol)
        if not avg_volume:
            return
        
        hour_data = get_last_hour_data(exchange, symbol)
        if not hour_data:
            return
        
        last_volume = hour_data['last_volume']
        
        if last_volume >= avg_volume * VOLUME_SPIKE_MULTIPLIER:
            logger.info(f"🔥 НАЙДЕН СПАЙК: {exchange_name} - {symbol}")
            
            chart = create_price_chart(hour_data['ohlcv'], symbol)
            
            send_signal(
                bot=bot,
                exchange_name=exchange_name,
                symbol=symbol,
                avg_volume=avg_volume,
                last_volume=last_volume,
                price_change=hour_data['price_change'],
                current_price=hour_data['current_price'],
                chart=chart
            )
    
    except Exception as e:
        logger.debug(f"Ошибка проверки {symbol} на {exchange_name}: {e}")

# ═══════════════════════════════════════════════════════════════
# ОСНОВНОЙ ЦИКЛ РАБОТЫ БОТА
# ═══════════════════════════════════════════════════════════════

def main():
    """Главная функция"""
    
    logger.info("🤖 Запуск бота...")
    
    if not TELEGRAM_TOKEN or not CHAT_ID:
        logger.error("❌ ОШИБКА: Не указаны TELEGRAM_TOKEN или CHAT_ID в секретах!")
        return
    
    bot = Bot(token=TELEGRAM_TOKEN)
    
    try:
        bot.send_message(
            chat_id=CHAT_ID,
            text="✅ Бот запущен и готов к работе!\n\n"
                 "Отслеживаю объёмы на Binance и Bybit...",
            parse_mode='HTML'
        )
    except TelegramError as e:
        logger.error(f"❌ Ошибка отправки сообщения: {e}")
        return
    
    exchanges = init_exchanges()
    
    pairs_data = {}
    for name, exchange in exchanges.items():
        pairs_data[name] = {
            'exchange': exchange,
            'pairs': get_usdt_pairs(exchange, name)
        }
    
    logger.info("🔄 Начинаю мониторинг...")
    
    start_time = time.time()
    runtime = 9 * 60  # 9 минут
    
    while (time.time() - start_time) < runtime:
        iteration_start = time.time()
        
        for exchange_name, data in pairs_data.items():
            exchange = data['exchange']
            pairs = data['pairs']
            
            for symbol in pairs:
                check_pair(exchange, exchange_name, symbol, bot)
                time.sleep(0.5)
        
        iteration_time = time.time() - iteration_start
        logger.info(f"✅ Итерация завершена за {iteration_time:.1f} сек")
        
        remaining_time = runtime - (time.time() - start_time)
        if remaining_time > 60:
            logger.info("⏳ Ожидание 60 секунд...")
            time.sleep(60)
        else:
            break
    
    logger.info("🏁 Завершение работы (перезапуск через 1 минуту)")

if __name__ == '__main__':
    main()
