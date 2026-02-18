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
# Binance USDT-Margined Perpetual for Gold
SYMBOL = 'XAU/USDT:USDT' 
LOOKBACK = 50       # Rolling liquidity lookback (50 candles on 15M = 12.5 hours)
RR_MINIMUM = 1.5    # Minimum Risk:Reward ratio

# Environment Variables (Set these in Render)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID", "YOUR_CHAT_ID")

last_signal = None 

# Initialize Exchange (Public Mode ONLY)
exchange = ccxt.binanceusdm({'enableRateLimit': True})

# ==========================================
# 2. RENDER KEEP-ALIVE SERVER
# ==========================================
app = Flask('')
@app.route('/')
def home():
    return "24/7 Gold Signal Bot is running!"

def run_http():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# ==========================================
# 3. STRATEGY ENGINE (24/7 ROLLING LIQUIDITY)
# ==========================================
def analyze_market(df_15, df_5):
    try:
        if len(df_15) < 205: return None # Need enough data for EMA 200

        # --- STEP 1: DEFINE ROLLING LIQUIDITY (15M) ---
        # Current completed 15M candle (index -2)
        curr_15 = df_15.iloc[-2]
        
        # The 50 candles before the current one
        range_data = df_15.iloc[-LOOKBACK-2 : -2]
        
        swing_high = range_data['high'].max()
        swing_low = range_data['low'].min()
        ema200 = df_15['ema200'].iloc[-2]

        # --- STEP 2: DETECT THE SWEEP & TREND CONFLUENCE ---
        sweep_type = None
        sweep_wick_extreme = None
        
        # Bearish Sweep: Wick above High, close below High + DOWN TREND
        if curr_15['high'] > swing_high and curr_15['close'] < swing_high:
            if curr_15['close'] < ema200: # Trend Filter
                sweep_type = "SHORT"
                sweep_wick_extreme = curr_15['high']
            
        # Bullish Sweep: Wick below Low, close above Low + UP TREND
        elif curr_15['low'] < swing_low and curr_15['close'] > swing_low:
            if curr_15['close'] > ema200: # Trend Filter
                sweep_type = "LONG"
                sweep_wick_extreme = curr_15['low']

        if not sweep_type: return None

        # --- STEP 3: DETECT MSS & FVG (5M TIMEFRAME) ---
        # Look at the last 3 completed 5M candles: c1 (Oldest), c2 (Middle), c3 (Newest)
        c1 = df_5.iloc[-4]
        c2 = df_5.iloc[-3]
        c3 = df_5.iloc[-2]
        
        candle_time = c3['time'] 

        # We will use a $0.50 buffer for Gold to avoid getting stopped out by noise
        GOLD_BUFFER = 0.50 

        if sweep_type == "SHORT":
            # Bearish FVG Check: c1 Low > c3 High (Gap down) AND bearish close
            if c1['low'] > c3['high'] and c3['close'] < c3['open']:
                entry = c3['high']  # Limit entry at the bottom of the gap
                sl = sweep_wick_extreme + GOLD_BUFFER 
                tp = swing_low      # Target the opposing liquidity
                
                risk = abs(entry - sl)
                reward = abs(entry - tp)
                rr = round(reward / risk, 2) if risk > 0 else 0
                
                if rr >= RR_MINIMUM:
                    return ("SHORT", entry, sl, tp, candle_time, rr)

        if sweep_type == "LONG":
            # Bullish FVG Check: c1 High < c3 Low (Gap up) AND bullish close
            if c1['high'] < c3['low'] and c3['close'] > c3['open']:
                entry = c3['low']   # Limit entry at the top of the gap
                sl = sweep_wick_extreme - GOLD_BUFFER 
                tp = swing_high     # Target the opposing liquidity
                
                risk = abs(entry - sl)
                reward = abs(tp - entry)
                rr = round(reward / risk, 2) if risk > 0 else 0
                
                if rr >= RR_MINIMUM:
                    return ("LONG", entry, sl, tp, candle_time, rr)

    except Exception as e:
        pass
        
    return None

# ==========================================
# 4. SCANNER LOOP
# ==========================================
async def scan_market(app):
    global last_signal
    bot = Bot(token=BOT_TOKEN)
    print(f"24/7 Scanner Started for {SYMBOL}...")
    
    while True:
        try:
            # Fetch Data (Need 250 candles for 15M to calculate EMA 200 properly)
            bars_15m = await exchange.fetch_ohlcv(SYMBOL, timeframe='15m', limit=250)
            bars_5m = await exchange.fetch_ohlcv(SYMBOL, timeframe='5m', limit=15)
            
            if bars_15m and bars_5m:
                df_15 = pd.DataFrame(bars_15m, columns=['timestamp', 'open', 'high', 'low', 'close', 'vol'])
                df_5 = pd.DataFrame(bars_5m, columns=['timestamp', 'open', 'high', 'low', 'close', 'vol'])
                
                # Calculate Indicators
                df_15['ema200'] = df_15['close'].ewm(span=200, adjust=False).mean()
                
                # Convert timestamps
                for df in [df_15, df_5]:
                    df['time'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)

                # Analyze
                signal_data = analyze_market(df_15, df_5)
                
                if signal_data:
                    direction, entry, sl, tp, candle_time, rr = signal_data
                    sig_id = f"{direction}_{candle_time}"
                    
                    if last_signal != sig_id:
                        emoji = "🔴" if direction == "SHORT" else "🟢"
                        
                        msg = (
                            f"{emoji} **GOLD SWEEP (24/7)** {emoji}\n\n"
                            f"🪙 **Asset:** {SYMBOL}\n"
                            f"⚡ **Action:** **{direction}** Limit\n"
                            f"-------------------------\n"
                            f"📥 **Entry Limit:** `${entry:.2f}`\n"
                            f"🛑 **Stop Loss:** `${sl:.2f}`\n"
                            f"🎯 **Take Profit:** `${tp:.2f}`\n"
                            f"⚖️ **R:R Ratio:** {rr}R\n"
                            f"-------------------------\n"
                            f"📝 *Logic: 15M Liquidity Swept with Trend. 5M FVG Formed.*"
                        )
                        
                        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                        last_signal = sig_id
                        print(f"Alert Sent: {direction} @ {entry} (RR: {rr})")

        except Exception as e:
            pass
            
        await asyncio.sleep(60) # Scan every 60 seconds

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🦅 **24/7 Gold Signal Bot Online.**\nScanning for High Probability (>1.5R) Sweeps...")

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