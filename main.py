import os
import ccxt.async_support as ccxt
import pandas as pd
import asyncio
from datetime import datetime, timezone
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from flask import Flask
from threading import Thread

# ==========================================
# 1. CONFIGURATION
# ==========================================
SYMBOL = 'XAU/USDT:USDT'  # Binance Gold Perpetual

# Environment Variables
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID", "YOUR_CHAT_ID")

last_signal = None 
exchange = ccxt.binanceusdm({'enableRateLimit': True})

# ==========================================
# 2. RENDER KEEP-ALIVE SERVER
# ==========================================
app = Flask('')
@app.route('/')
def home():
    return "Gold 5M Scalp Bot is running!"

def run_http():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# ==========================================
# 3. STRATEGY ENGINE (5M MEAN REVERSION)
# ==========================================
def calculate_indicators(df):
    """Calculates Bollinger Bands and RSI natively without pandas-ta"""
    # 20-Period Simple Moving Average (Middle Band)
    df['sma20'] = df['close'].rolling(window=20).mean()
    
    # Standard Deviation for Bollinger Bands
    df['stddev'] = df['close'].rolling(window=20).std()
    
    # Upper and Lower Bands (2 Standard Deviations)
    df['upper_band'] = df['sma20'] + (2 * df['stddev'])
    df['lower_band'] = df['sma20'] - (2 * df['stddev'])
    
    # Native RSI Calculation (14 period)
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    return df

def analyze_scalp(df):
    try:
        if len(df) < 25: return None

        df = calculate_indicators(df)

        # prev = The candle that spiked outside the band
        # curr = The just-completed candle that confirms the reversal
        prev = df.iloc[-3]
        curr = df.iloc[-2]
        
        candle_time = curr['time']
        
        # Stop Loss Buffer for Gold
        BUFFER = 1.00 

        # 🔴 SHORT SCALP (Price pumped too high, snapping back down)
        # 1. Previous candle went ABOVE upper band
        # 2. Current candle is RED (Close < Open) and closed BACK INSIDE the band
        # 3. RSI shows it was overbought (> 60)
        if prev['high'] > prev['upper_band'] and curr['close'] < curr['open'] and curr['close'] < curr['upper_band']:
            if curr['rsi'] > 60:
                
                entry = curr['close']
                tp = curr['sma20'] # Target the middle moving average
                sl = max(prev['high'], curr['high']) + BUFFER
                
                # Check if TP is worth it (At least $0.80 move on gold)
                if abs(entry - tp) > 0.80:
                    return ("SHORT", entry, sl, tp, candle_time)

        # 🟢 LONG SCALP (Price dumped too low, snapping back up)
        # 1. Previous candle went BELOW lower band
        # 2. Current candle is GREEN (Close > Open) and closed BACK INSIDE the band
        # 3. RSI shows it was oversold (< 40)
        if prev['low'] < prev['lower_band'] and curr['close'] > curr['open'] and curr['close'] > prev['lower_band']:
            if curr['rsi'] < 40:
                
                entry = curr['close']
                tp = curr['sma20'] # Target the middle moving average
                sl = min(prev['low'], curr['low']) - BUFFER
                
                # Check if TP is worth it (At least $0.80 move on gold)
                if abs(tp - entry) > 0.80:
                    return ("LONG", entry, sl, tp, candle_time)

    except Exception as e:
        print(f"Analysis Error: {e}")
        
    return None

# ==========================================
# 4. SCANNER LOOP
# ==========================================
async def scan_market(app):
    global last_signal
    bot = Bot(token=BOT_TOKEN)
    print(f"5M Scalp Scanner Started for {SYMBOL}...")
    
    while True:
        try:
            # We ONLY need the 5-minute chart now. Faster and highly responsive.
            bars_5m = await exchange.fetch_ohlcv(SYMBOL, timeframe='5m', limit=50)
            
            if bars_5m:
                df_5 = pd.DataFrame(bars_5m, columns=['timestamp', 'open', 'high', 'low', 'close', 'vol'])
                df_5['time'] = pd.to_datetime(df_5['timestamp'], unit='ms', utc=True)

                signal_data = analyze_scalp(df_5)
                
                if signal_data:
                    direction, entry, sl, tp, candle_time = signal_data
                    sig_id = f"{direction}_{candle_time}"
                    
                    if last_signal != sig_id:
                        
                        risk = abs(entry - sl)
                        reward = abs(tp - entry)
                        rr = round(reward / risk, 2) if risk > 0 else 0
                        
                        emoji = "🔴" if direction == "SHORT" else "🟢"
                        
                        msg = (
                            f"{emoji} **GOLD 5M SCALP** {emoji}\n\n"
                            f"⚡ **Action:** **{direction}** Market\n"
                            f"-------------------------\n"
                            f"📥 **Entry:** `${entry:.2f}`\n"
                            f"🎯 **Target (SMA):** `${tp:.2f}`\n"
                            f"🛑 **Stop Loss:** `${sl:.2f}`\n"
                            f"⚖️ **R:R Ratio:** {rr}R\n"
                            f"-------------------------\n"
                            f"📝 *Logic: Bollinger Band Rejection + RSI*"
                        )
                        
                        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                        last_signal = sig_id
                        print(f"Alert Sent: {direction} @ {entry}")

        except Exception as e:
            pass
            
        await asyncio.sleep(20) # Check every 20 seconds for fast scalping

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🦅 **Gold 5M Scalper Bot Online.**\nHunting for quick mean-reversion pullbacks...")

if __name__ == '__main__':
    keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    
    loop = asyncio.get_event_loop()
    loop.create_task(scan_market(application))
    
    try:
        application.run_polling()
    except KeyboardInterrupt:
        print("Stopping Bot...")
    finally:
        loop.run_until_complete(exchange.close())
