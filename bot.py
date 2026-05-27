import logging
import asyncio
import yfinance as yf
import pandas as pd
import numpy as np
from telegram import Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update

# ===================== CONFIG =====================
BOT_TOKEN = "8409479235:AAGBBODhZBQyKf76-zKevURrxHzYM4nINOA"   # @BotFather থেকে নিন
CHAT_ID   = "8583376205"              # আপনার Chat ID

# ট্রেড করার পেয়ার লিস্ট (Yahoo Finance symbol)
PAIRS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "Gold":    "GC=F",
    "BTC/USD": "BTC-USD",
}

INTERVAL  = "5m"    # 1m, 5m, 15m, 1h
PERIOD    = "1d"    # ডেটা কতদিনের
CHECK_SEC = 300     # কতক্ষণ পর পর চেক করবে (সেকেন্ড)
# ==================================================

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)


# ── ইন্ডিকেটর ক্যালকুলেশন ──────────────────────────

def calc_rsi(close: pd.Series, period=14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))

def calc_macd(close: pd.Series):
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    signal= macd.ewm(span=9, adjust=False).mean()
    hist  = macd - signal
    return macd, signal, hist

def calc_ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean()


# ── সিগন্যাল লজিক ────────────────────────────────

def get_signal(symbol: str) -> dict | None:
    try:
        df = yf.download(symbol, interval=INTERVAL, period=PERIOD,
                         progress=False, auto_adjust=True)
        if df is None or len(df) < 50:
            return None

        close = df["Close"].squeeze()

        rsi        = calc_rsi(close)
        macd, sig, hist = calc_macd(close)
        ema20      = calc_ema(close, 20)
        ema50      = calc_ema(close, 50)

        # সর্বশেষ মান
        r   = rsi.iloc[-1]
        m   = macd.iloc[-1]
        s   = sig.iloc[-1]
        h   = hist.iloc[-1]
        h_1 = hist.iloc[-2]      # আগের হিস্টোগ্রাম
        e20 = ema20.iloc[-1]
        e50 = ema50.iloc[-1]
        price = close.iloc[-1]

        # ── BUY শর্ত ──
        buy_score = 0
        if r < 40:            buy_score += 1   # RSI oversold zone
        if m > s:             buy_score += 1   # MACD bullish crossover
        if h > h_1:           buy_score += 1   # Histogram বাড়ছে
        if e20 > e50:         buy_score += 1   # EMA bullish alignment
        if price > e20:       buy_score += 1   # Price EMA-এর উপরে

        # ── SELL শর্ত ──
        sell_score = 0
        if r > 60:            sell_score += 1
        if m < s:             sell_score += 1
        if h < h_1:           sell_score += 1
        if e20 < e50:         sell_score += 1
        if price < e20:       sell_score += 1

        # স্ট্রেন্থ নির্ধারণ
        def strength(score):
            if score == 5: return "🔥 VERY STRONG"
            if score == 4: return "💪 STRONG"
            if score == 3: return "✅ MEDIUM"
            return None

        if buy_score >= 3:
            return {"direction": "BUY ⬆️", "strength": strength(buy_score),
                    "rsi": r, "price": price, "score": buy_score}
        if sell_score >= 3:
            return {"direction": "SELL ⬇️", "strength": strength(sell_score),
                    "rsi": r, "price": price, "score": sell_score}

        return None

    except Exception as e:
        logging.error(f"Error for {symbol}: {e}")
        return None


# ── টেলিগ্রাম মেসেজ ─────────────────────────────

def build_message(name: str, sig: dict) -> str:
    return (
        f"📊 *Quotex Signal*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💱 Pair     : `{name}`\n"
        f"📌 Direction: *{sig['direction']}*\n"
        f"⚡ Strength : {sig['strength']}\n"
        f"📈 Price    : `{sig['price']:.5f}`\n"
        f"🔢 RSI      : `{sig['rsi']:.2f}`\n"
        f"🏆 Score    : {sig['score']}/5\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⏱ Timeframe : {INTERVAL}\n"
        f"⚠️ _Trade at your own risk!_"
    )


# ── অটো স্ক্যান লুপ ─────────────────────────────

async def auto_scan(app: Application):
    await asyncio.sleep(5)
    while True:
        logging.info("Scanning markets...")
        for name, symbol in PAIRS.items():
            result = get_signal(symbol)
            if result:
                msg = build_message(name, result)
                await bot.send_message(chat_id=CHAT_ID, text=msg,
                                       parse_mode="Markdown")
                await asyncio.sleep(1)
        await asyncio.sleep(CHECK_SEC)


# ── /start কমান্ড ───────────────────────────────

async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Quotex Signal Bot চালু!\n"
        f"প্রতি {CHECK_SEC//60} মিনিটে সিগন্যাল আসবে।\n\n"
        "ইন্ডিকেটর: RSI + MACD + EMA20/50"
    )


# ── /scan কমান্ড (ম্যানুয়াল) ────────────────────

async def scan_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 স্ক্যান করছি...")
    found = False
    for name, symbol in PAIRS.items():
        result = get_signal(symbol)
        if result:
            found = True
            await update.message.reply_text(
                build_message(name, result), parse_mode="Markdown"
            )
    if not found:
        await update.message.reply_text("⏳ এখন কোনো স্ট্রং সিগন্যাল নেই।")


# ── মেইন ────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("scan",  scan_cmd))

    loop = asyncio.get_event_loop()
    loop.create_task(auto_scan(app))

    print("🤖 Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
