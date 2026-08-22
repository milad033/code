import ccxt
import pandas as pd
import pandas_ta as ta
import sqlite3
import time
import requests
from datetime import datetime

# ==================== تنظیمات اصلی ====================
SYMBOLS = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT']
TIMEFRAME = '15m'
TELEGRAM_BOT_TOKEN = '8764438353:AAF0pOccZmYBHTtPNH8RvdOsJ1tDFvIf29w'  # توکن ربات تلگرام
TELEGRAM_CHAT_ID = '694199592'      # آیدی چت یا کانال
TELEGRAM_CHANNEL_ID = '-1004326073088'
# ضریب ATR برای حد ضرر و حد سود (نسبت ریسک به ریوارد ۱ به ۲)
SL_ATR_MULT = 1.5
TP_ATR_MULT = 3.0
MIN_SCORE = 70  # حداقل امتیاز کیفیت سیگنال برای ورود (از ۱۰۰)

# اتصال به صرافی بای‌بیت (بدون نیاز به API Key برای دیتای عمومی)
exchange = ccxt.bybit({'enableRateLimit': True})

# ==================== مدیریت پایگاه داده ====================
DB_NAME = 'paper_trades.db'

def init_db():
    """ایجاد جدول معاملات کاغذی در صورت عدم وجود"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            side TEXT,
            entry_price REAL,
            sl_price REAL,
            tp_price REAL,
            atr REAL,
            score INTEGER,
            opened_at TEXT,
            closed_at TEXT,
            close_price REAL,
            status TEXT,
            pnl_r REAL
        )
    ''')
    conn.commit()
    conn.close()

def has_active_position(symbol):
    """بررسی اینکه آیا برای این نماد پوزیشن باز کاغذی وجود دارد یا خیر"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM paper_trades WHERE symbol = ? AND status = 'OPEN'", (symbol,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def open_paper_trade(symbol, side, entry, sl, tp, atr, score):
    """ثبت معامله کاغذی جدید در پایگاه داده"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    opened_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO paper_trades (symbol, side, entry_price, sl_price, tp_price, atr, score, opened_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
    ''', (symbol, side, entry, sl, tp, atr, score, opened_at))
    conn.commit()
    conn.close()

def close_paper_trade(trade_id, close_price, status, pnl_r):
    """بستن معامله کاغذی و ثبت نتیجه"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    closed_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        UPDATE paper_trades
        SET closed_at = ?, close_price = ?, status = ?, pnl_r = ?
        WHERE id = ?
    ''', (closed_at, close_price, status, pnl_r, trade_id))
    conn.commit()
    conn.close()

def get_stats():
    """محاسبه آمار کلی معاملات کاغذی ثبت شده"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*), SUM(pnl_r) FROM paper_trades WHERE status IN ('WIN_TP', 'LOSS_SL')")
    total, total_r = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) FROM paper_trades WHERE status = 'WIN_TP'")
    wins = cursor.fetchone()[0]
    conn.close()
    
    total = total or 0
    total_r = total_r or 0.0
    win_rate = (wins / total * 100) if total > 0 else 0.0
    return total, wins, win_rate, total_r

# ==================== اطلاع‌رسانی تلگرام ====================
def send_telegram(message):
    """ارسال پیام به تلگرام"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'HTML'}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"⚠️ خطای ارسال به تلگرام: {e}")

# ==================== تحلیل تکنیکال و امتیازدهی ====================
def analyze_market(symbol):
    """دریافت کندل‌ها و بررسی شرایط استراتژی Smart Pullback"""
    bars = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=250)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    # محاسبه اندیکاتورها
    df['EMA_20'] = ta.ema(df['close'], length=20)
    df['EMA_50'] = ta.ema(df['close'], length=50)
    df['EMA_200'] = ta.ema(df['close'], length=200)
    df['RSI_14'] = ta.rsi(df['close'], length=14)
    df['ATR_14'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    
    macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
    df = pd.concat([df, macd], axis=1)
    
    bb = ta.bbands(df['close'], length=20, std=2)
    df = pd.concat([df, bb], axis=1)
    
    # برای جلوگیری از Repaint، کندل بسته شده قبلی بررسی می‌شود
    curr = df.iloc[-2]
    prev = df.iloc[-3]
    
    macd_hist_col = [c for c in df.columns if c.startswith('MACDh')][0]
    bb_width_col = [c for c in df.columns if c.startswith('BBB')][0]
    
    # شرط بازار رنج
    if curr[bb_width_col] < 1.8:
        return None
    
    side = None
    # شرایط ورود LONG
    if (curr['close'] > curr['EMA_200'] and 
        curr['EMA_20'] > curr['EMA_50'] and 
        35 <= curr['RSI_14'] <= 52 and 
        curr['close'] > prev['close'] and 
        curr[macd_hist_col] > prev[macd_hist_col]):
        side = 'LONG'
        
    # شرایط ورود SHORT
    elif (curr['close'] < curr['EMA_200'] and 
          curr['EMA_20'] < curr['EMA_50'] and 
          48 <= curr['RSI_14'] <= 65 and 
          curr['close'] < prev['close'] and 
          curr[macd_hist_col] < prev[macd_hist_col]):
        side = 'SHORT'
        
    if not side:
        return None

    # سیستم امتیازدهی کیفیت سیگنال (0-100)
    score = 30  # امتیاز پایه ورود
    
    # ۱. همسویی کامل روند EMA
    if side == 'LONG' and curr['EMA_20'] > curr['EMA_50'] > curr['EMA_200']: score += 25
    elif side == 'SHORT' and curr['EMA_20'] < curr['EMA_50'] < curr['EMA_200']: score += 25
    
    # ۲. عمق مناسب پولبک RSI
    if side == 'LONG' and curr['RSI_14'] <= 42: score += 25
    elif side == 'SHORT' and curr['RSI_14'] >= 58: score += 25
    
    # ۳. عرض باند بولینگر و نوسان
    if curr[bb_width_col] > 3.0: score += 20
    
    if score < MIN_SCORE:
        return None
        
    entry = float(curr['close'])
    atr = float(curr['ATR_14'])
    
    if side == 'LONG':
        sl = entry - (SL_ATR_MULT * atr)
        tp = entry + (TP_ATR_MULT * atr)
    else:
        sl = entry + (SL_ATR_MULT * atr)
        tp = entry - (TP_ATR_MULT * atr)
        
    return {
        'symbol': symbol,
        'side': side,
        'entry': entry,
        'sl': sl,
        'tp': tp,
        'atr': atr,
        'score': score,
        'rsi': curr['RSI_14']
    }

# ==================== ردیاب زنده پوزیشن‌ها ====================
def track_open_positions():
    """رصد قیمت لحظه‌ای برای پوزیشن‌های باز جهت ثبت TP/SL"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, symbol, side, entry_price, sl_price, tp_price FROM paper_trades WHERE status = 'OPEN'")
    open_trades = cursor.fetchall()
    conn.close()
    
    for trade in open_trades:
        trade_id, symbol, side, entry, sl, tp = trade
        try:
            ticker = exchange.fetch_ticker(symbol)
            last_price = float(ticker['last'])
            
            status = None
            pnl_r = 0.0
            
            if side == 'LONG':
                if last_price >= tp:
                    status = 'WIN_TP'
                    pnl_r = +2.0
                elif last_price <= sl:
                    status = 'LOSS_SL'
                    pnl_r = -1.0
            elif side == 'SHORT':
                if last_price <= tp:
                    status = 'WIN_TP'
                    pnl_r = +2.0
                elif last_price >= sl:
                    status = 'LOSS_SL'
                    pnl_r = -1.0
                    
            if status:
                close_paper_trade(trade_id, last_price, status, pnl_r)
                total, wins, win_rate, total_r = get_stats()
                
                icon = "✅" if status == 'WIN_TP' else "❌"
                msg = (f"{icon} <b>نتیجه معامله کاغذی ({symbol})</b>\n\n"
                       f"وضعیت: {status}\n"
                       f"قیمت خروج: {last_price}\n"
                       f"سود/زیان بر مبنای R: {pnl_r:+.1f}R\n"
                       f"----------------------\n"
                       f"📊 <b>آمار کل:</b>\n"
                       f"تعداد معاملات: {total}\n"
                       f"نرخ برد (Win Rate): {win_rate:.1f}%\n"
                       f"مجموع سود/زیان: {total_r:+.1f}R")
                send_telegram(msg)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Trade Closed: {symbol} -> {status}")
                
        except Exception as e:
            print(f"⚠️ خطای ردیابی پوزیشن برای {symbol}: {e}")

# ==================== حلقه اصلی برنامه ====================
if __name__ == '__main__':
    init_db()
    print("🚀 Paper Trading Bot is Running on VS Code...")
    send_telegram("🚀 <b>ربات معامله کاغذی (Paper Trading) فعال شد.</b>")
    
    last_scan_time = 0
    
    while True:
        try:
            # ۱. ردیابی لحظه‌ای قیمت پوزیشن‌های باز (هر ۶۰ ثانیه)
            track_open_positions()
            
            # ۲. اسکن سیگنال جدید در انتهای هر کندل ۱۵ دقیقه
            now = time.time()
            if now - last_scan_time >= 900:  # هر ۱۵ دقیقه (۹۰۰ ثانیه)
                last_scan_time = now
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Scanning symbols for signals...")
                
                for symbol in SYMBOLS:
                    if has_active_position(symbol):
                        continue  # اگر پوزیشن باز کاغذی دارد، سیگنال جدید صادر نکن
                        
                    sig = analyze_market(symbol)
                    if sig:
                        open_paper_trade(sig['symbol'], sig['side'], sig['entry'], sig['sl'], sig['tp'], sig['atr'], sig['score'])
                        
                        msg = (f"📝 <b>سیگنال جدید Paper Trade ({sig['side']})</b>\n\n"
                               f"نماد: {sig['symbol']}\n"
                               f"نقطه ورود: {sig['entry']:.2f}\n"
                               f"حد ضرر (SL): {sig['sl']:.2f}\n"
                               f"تارگت (TP): {sig['tp']:.2f}\n"
                               f"امتیاز سیگنال: {sig['score']}/100\n"
                               f"RSI: {sig['rsi']:.1f}")
                        send_telegram(msg)
                        print(f"-> Signal Found for {symbol} ({sig['side']})")
                        
            time.sleep(60)  # چک کردن قیمت و زمان هر یک دقیقه
            
        except KeyboardInterrupt:
            print("ربات متوقف شد.")
            break
        except Exception as e:
            print(f"⚠️ خطای غیرمنتظره در حلقه اصلی: {e}")
            time.sleep(10)