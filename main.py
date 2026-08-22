import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import time

# --- تنظیمات تلگرام ---
TELEGRAM_BOT_TOKEN = '8764438353:AAF0pOccZmYBHTtPNH8RvdOsJ1tDFvIf29w'
TELEGRAM_CHAT_ID = '694199592'

# لیست ارزها برای اسکن سریع و یافتن آنی سیگنال
SYMBOLS = [
    'BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT', 
    'DOGE/USDT:USDT', 'XRP/USDT:USDT', 'NEAR/USDT:USDT', 
    'SUI/USDT:USDT', 'PEPE/USDT:USDT', 'AVAX/USDT:USDT', 'LINK/USDT:USDT'
]

TIMEFRAME = '3m'  # تایم‌فریم ۳ دقیقه برای معاملات ۱۵ تا ۳۰ دقیقه‌ای
exchange = ccxt.bybit({'enableRateLimit': True})

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'}, timeout=5)
    except Exception as e:
        print(f"Telegram Error: {e}")

def check_signal(symbol):
    try:
        # دریافت ۱۰۰ کندل اخیر
        bars = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # محاسبه RSI و MACD
        df['RSI'] = ta.rsi(df['close'], length=14)
        macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
        df = pd.concat([df, macd], axis=1)
        
        macd_col = [c for c in df.columns if c.startswith('MACD_')][0]
        macds_col = [c for c in df.columns if c.startswith('MACDs_')][0]
        macdh_col = [c for c in df.columns if c.startswith('MACDh_')][0]
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        close = curr['close']
        rsi = curr['RSI']
        
        # --- استراتژی ساده و روان ---
        # سیگنال خرید (LONG): خط مک‌دی بالای سیگنال باشد و RSI در محدوده ۴۰ تا ۶۵ (روند صعودی تازه)
        long_cond = (curr[macd_col] > curr[macds_col]) and (curr[macdh_col] > prev[macdh_col]) and (40 <= rsi <= 65)
        
        # سیگنال فروش (SHORT): خط مک‌دی زیر سیگنال باشد و RSI در محدوده ۳۵ تا ۶۰ (روند نزولی تازه)
        short_cond = (curr[macd_col] < curr[macds_col]) and (curr[macdh_col] < prev[macdh_col]) and (35 <= rsi <= 60)
        
        if long_cond:
            sl = close * 0.995  # ۰.۵ درصد حد ضرر (مناسب اسکالپ سریع)
            tp = close * 1.010  # ۱.۰ درصد تارگت سود (نسبت R/R معادل ۱ به ۲)
            return 'LONG', close, sl, tp, rsi
            
        elif short_cond:
            sl = close * 1.005  # ۰.۵ درصد حد ضرر
            tp = close * 0.990  # ۱.۰ درصد تارگت سود
            return 'SHORT', close, sl, tp, rsi
            
    except Exception as e:
        print(f"خطا در اسکن {symbol}: {e}")
        
    return None, None, None, None, None

def scan_market_now():
    print("🔍 در حال اسکن آنی بازار برای یافتن سریع‌ترین سیگنال...")
    signals_found = 0
    
    for symbol in SYMBOLS:
        side, entry, sl, tp, rsi = check_signal(symbol)
        
        if side:
            signals_found += 1
            direction_icon = "🟢" if side == 'LONG' else "🔴"
            msg = (f"{direction_icon} <b>سیگنال اسکالپ سریع ({side})</b>\n\n"
                   f"نماد: <code>{symbol.split(':')[0]}</code>\n"
                   f"قیمت ورود: {entry}\n"
                   f"🎯 حد سود (TP): {tp:.4f} (حدود ۱۵-۳۰ دقیقه)\n"
                   f"🛑 حد ضرر (SL): {sl:.4f}\n"
                   f"📊 شاخص RSI: {rsi:.1f}")
            
            print(f"✅ سیگنال پیدا شد: {symbol} - {side}")
            send_telegram(msg)
            
    if signals_found == 0:
        print("هیچ سیگنال آنی در این لحظه یافت نشد. اسکن بعدی ۳ دقیقه دیگر...")

# اجرای بلافاصله در همان ثانیه اول پس از RUN کردن
scan_market_now()

# ادامه اسکن هر ۳ دقیقه یک‌بار
while True:
    time.sleep(180)
    scan_market_now()