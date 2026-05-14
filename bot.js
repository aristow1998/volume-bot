const { Telegraf } = require('telegraf');
const axios = require('axios');
const { createCanvas } = require('canvas');

// ======================================================
//  НАСТРОЙКИ — берём из переменных окружения
// ======================================================
const TELEGRAM_TOKEN = process.env.TELEGRAM_TOKEN;
const CHAT_ID        = process.env.CHAT_ID;
const RATIO_THRESHOLD = 10; // порог: объём > среднего в 10 раз -> алерт
const RUNTIME_MINUTES = 9;  // работаем 9 минут (GitHub перезапустит через 10)
// ======================================================

const bot = new Telegraf(TELEGRAM_TOKEN);

// Получаем топ-50 Spot + топ-50 Futures USDT-пар по объёму за 24ч
async function getTopPairs() {
    try {
        const [spot, futures] = await Promise.all([
            axios.get('https://api.binance.com/api/v3/ticker/24hr'),
            axios.get('https://fapi.binance.com/fapi/v1/ticker/24hr'),
        ]);

        const topSpot = spot.data
            .filter(t => t.symbol.endsWith('USDT'))
            .sort((a, b) => parseFloat(b.quoteVolume) - parseFloat(a.quoteVolume))
            .slice(0, 50)
            .map(t => ({ symbol: t.symbol, type: 'SPOT', change24h: t.priceChangePercent }));

        const topFut = futures.data
            .filter(t => t.symbol.endsWith('USDT'))
            .sort((a, b) => parseFloat(b.quoteVolume) - parseFloat(a.quoteVolume))
            .slice(0, 50)
            .map(t => ({ symbol: t.symbol, type: 'FUTURES', change24h: t.priceChangePercent }));

        return [...topSpot, ...topFut];
    } catch (err) {
        console.error('Ошибка getTopPairs:', err.message);
        return [];
    }
}

// Строим PNG свечного графика с объёмами
function buildChart(symbol, candles) {
    const W = 720, H = 420;
    const canvas = createCanvas(W, H);
    const ctx    = canvas.getContext('2d');

    ctx.fillStyle = '#131722';
    ctx.fillRect(0, 0, W, H);

    const PAD    = { top: 40, right: 20, bottom: 80, left: 60 };
    const cW     = W - PAD.left - PAD.right;
    const cH     = H - PAD.top  - PAD.bottom;
    const volH   = cH * 0.25;
    const priceH = cH - volH - 8;

    const opens   = candles.map(c => parseFloat(c[1]));
    const highs   = candles.map(c => parseFloat(c[2]));
    const lows    = candles.map(c => parseFloat(c[3]));
    const closes  = candles.map(c => parseFloat(c[4]));
    const volumes = candles.map(c => parseFloat(c[5]));

    const minP   = Math.min(...lows);
    const maxP   = Math.max(...highs);
    const maxV   = Math.max(...volumes);
    const pRange = maxP - minP || 1;
    const barW   = Math.max(2, (cW / candles.length) - 1);

    function priceY(p) {
        return PAD.top + priceH - ((p - minP) / pRange) * priceH;
    }

    candles.forEach((c, i) => {
        const x    = PAD.left + i * (cW / candles.length);
        const bull = closes[i] >= opens[i];
        const col  = bull ? '#26a69a' : '#ef5350';

        // объём
        const vh = (volumes[i] / maxV) * volH;
        ctx.fillStyle   = col;
        ctx.globalAlpha = 0.5;
        ctx.fillRect(x, H - PAD.bottom - vh, barW, vh);
        ctx.globalAlpha = 1;

        // фитиль
        ctx.strokeStyle = col;
        ctx.lineWidth   = 1;
        ctx.beginPath();
        ctx.moveTo(x + barW / 2, priceY(highs[i]));
        ctx.lineTo(x + barW / 2, priceY(lows[i]));
        ctx.stroke();

        // тело свечи
        const yO = priceY(opens[i]);
        const yC = priceY(closes[i]);
        ctx.fillStyle = col;
        ctx.fillRect(x, Math.min(yO, yC), barW, Math.max(1, Math.abs(yO - yC)));
    });

    // заголовок
    ctx.fillStyle = '#d1d4dc';
    ctx.font      = 'bold 16px sans-serif';
    ctx.fillText(symbol + '  1m  Volume Spike!', PAD.left, 26);

    // ось цены
    ctx.fillStyle = '#787b86';
    ctx.font      = '11px sans-serif';
    [0, 0.5, 1].forEach(t => {
        const p = minP + pRange * t;
        const y = priceY(p);
        ctx.fillText(p.toFixed(4), 2, y + 4);
        ctx.strokeStyle = '#2a2e39';
        ctx.lineWidth   = 0.5;
        ctx.beginPath();
        ctx.moveTo(PAD.left, y);
        ctx.lineTo(W - PAD.right, y);
        ctx.stroke();
    });

    return canvas.toBuffer('image/png');
}

// Основная проверка (запускается каждую минуту)
async function checkVolumes() {
    console.log('[' + new Date().toLocaleTimeString() + '] Проверяем объёмы...');
    const pairs = await getTopPairs();

    for (const pair of pairs) {
        try {
            const base = pair.type === 'SPOT'
                ? 'https://api.binance.com/api/v3/klines'
                : 'https://fapi.binance.com/fapi/v1/klines';

            const { data: candles } = await axios.get(base, {
                params: { symbol: pair.symbol, interval: '1m', limit: 61 },
            });

            if (candles.length < 7) continue;

            // последняя ЗАКРЫТАЯ свеча - предпоследняя
            const last5  = candles.slice(-7, -2);
            const cur    = candles[candles.length - 2];
            const curVol = parseFloat(cur[5]);
            const avgVol = last5.reduce((s, c) => s + parseFloat(c[5]), 0) / 5;
            const ratio  = curVol / (avgVol || 1);

            if (ratio >= RATIO_THRESHOLD) {
                console.log('🔥 ВСПЛЕСК: ' + pair.symbol + ' x' + ratio.toFixed(1));

                const png     = buildChart(pair.symbol, candles.slice(-61, -1));
                const nl      = String.fromCharCode(10);
                const caption =
                    '🚀 Всплеск объёма! ' + pair.symbol + ' (' + pair.type + ')' + nl +
                    '📊 Коэффициент: ' + ratio.toFixed(1) + 'x' + nl +
                    '📈 Изм. за 24ч: ' + parseFloat(pair.change24h).toFixed(2) + '%' + nl +
                    '💹 Тек. объём: ' + curVol.toFixed(2) + nl +
                    '📉 Сред. объём (5м): ' + avgVol.toFixed(2);

                await bot.telegram.sendPhoto(
                    CHAT_ID,
                    { source: png },
                    { caption }
                );
            }
        } catch (err) {
            // лимит / пара недоступна - пропускаем
            if (err.response && err.response.status === 451) {
                console.log('⚠️  Binance заблокирован, пропускаем...');
                break; // Выходим из цикла если Binance недоступен
            }
        }

        // пауза чтобы не превысить rate-limit Binance
        await new Promise(r => setTimeout(r, 120));
    }
}

// Главная функция с таймером
async function main() {
    console.log('🤖 Бот запущен!');
    
    // Проверяем наличие токенов
    if (!TELEGRAM_TOKEN || !CHAT_ID) {
        console.error('❌ Ошибка: не указаны TELEGRAM_TOKEN или CHAT_ID');
        process.exit(1);
    }

    // Отправляем стартовое сообщение
    try {
        await bot.telegram.sendMessage(
            CHAT_ID,
            '✅ Бот запущен!\n\nОтслеживаю всплески объёма на Binance Spot и Futures...'
        );
    } catch (err) {
        console.error('❌ Ошибка отправки сообщения:', err.message);
        process.exit(1);
    }

    const startTime = Date.now();
    const endTime = startTime + (RUNTIME_MINUTES * 60 * 1000);

    // Первая проверка сразу
    await checkVolumes();

    // Запускаем проверку каждую минуту
    const interval = setInterval(async () => {
        if (Date.now() >= endTime) {
            console.log('⏱️  Время работы истекло, завершаем...');
            clearInterval(interval);
            
            await bot.telegram.sendMessage(
                CHAT_ID,
                '⏹️ Бот завершил цикл работы (перезапуск через 1 минуту)'
            );
            
            process.exit(0);
        }
        
        await checkVolumes();
    }, 60000); // каждую минуту
}

// Обработка ошибок
process.on('unhandledRejection', (err) => {
    console.error('Необработанная ошибка:', err);
});

process.on('SIGINT', () => {
    console.log('Получен сигнал остановки, завершаем...');
    process.exit(0);
});

// Запуск
main().catch(err => {
    console.error('Критическая ошибка:', err);
    process.exit(1);
});
