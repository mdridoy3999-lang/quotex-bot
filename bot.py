import logging
import asyncio
import requests
from datetime import datetime
from telegram import Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update

BOT_TOKEN = "8409479235:AAGBBODhZBQyKf76-zKevURrxKeVM4nINOA"
CHAT_ID   = "8583376205"

PAIRS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "Gold":    "GC=F",
    "BTC/USD": "BTC-USD",
}

TRADE_MIN = 1
CHECK_SEC = 300
MIN_SCORE = 7

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)

pending_trades = []
stats = {"win": 0, "loss": 0, "total": 0}
last_signals = {}


def fetch_ohlcv(symbol, interval="1m", range_="5d"):
    try:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
               f"?interval={interval}&range={range_}&includePrePost=false")
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=15)
        data = r.json()
        chart = data["chart"]["result"][0]
        closes = [c for c in chart["indicators"]["quote"][0]["close"] if c]
        highs  = [h for h in chart["indicators"]["quote"][0]["high"]  if h]
        lows   = [l for l in chart["indicators"]["quote"][0]["low"]   if l]
        opens  = [o for o in chart["indicators"]["quote"][0]["open"]  if o]
        vols   = [v if v else 0 for v in chart["indicators"]["quote"][0]["volume"]]
        return closes, highs, lows, opens, vols
    except Exception as e:
        logging.error(f"Fetch error {symbol}: {e}")
        return None, None, None, None, None


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100
    return 100 - (100 / (1 + ag/al))

def calc_ema(closes, span):
    if not closes: return 0
    k = 2 / (span + 1)
    ema = closes[0]
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
    return ema

def calc_ema_list(closes, span):
    if not closes: return []
    k = 2 / (span + 1)
    emas = [closes[0]]
    for c in closes[1:]:
        emas.append(c * k + emas[-1] * (1 - k))
    return emas

def calc_macd(closes):
    if len(closes) < 26: return 0, 0, 0, 0
    e12 = calc_ema_list(closes, 12)
    e26 = calc_ema_list(closes, 26)
    ml  = [a - b for a, b in zip(e12, e26)]
    sl  = calc_ema_list(ml, 9)
    return ml[-1], sl[-1], ml[-1]-sl[-1], ml[-2]-sl[-2] if len(ml)>1 else 0

def calc_bb(closes, period=20):
    if len(closes) < period: return 0, 0, 0
    r = closes[-period:]
    m = sum(r) / period
    s = (sum((x-m)**2 for x in r)/period)**0.5
    return m+2*s, m, m-2*s

def calc_stoch(highs, lows, closes, k=14):
    if len(closes) < k: return 50, 50
    h = max(highs[-k:]); l = min(lows[-k:])
    if h == l: return 50, 50
    k1 = 100*(closes[-1]-l)/(h-l)
    h2 = max(highs[-k-1:-1]) if len(highs)>k else h
    l2 = min(lows[-k-1:-1])  if len(lows)>k  else l
    k2 = 100*(closes[-2]-l2)/(h2-l2+1e-10) if len(closes)>k else k1
    return k1, k2

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < period+1: return 0
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]),
               abs(lows[i]-closes[i-1])) for i in range(1, len(closes))]
    return sum(trs[-period:]) / period

def detect_pattern(opens, closes, highs, lows):
    if len(closes) < 3: return False, False, False, False
    body = abs(closes[-1]-opens[-1])
    uw   = highs[-1] - max(closes[-1], opens[-1])
    lw   = min(closes[-1], opens[-1]) - lows[-1]
    bull = (closes[-2]<opens[-2] and closes[-1]>opens[-1] and
            closes[-1]>opens[-2] and opens[-1]<closes[-2])
    bear = (closes[-2]>opens[-2] and closes[-1]<opens[-1] and
            closes[-1]<opens[-2] and opens[-1]>closes[-2])
    hamm = lw > 2*body and uw < body and body > 0
    shot = uw > 2*body and lw < body and body > 0
    return bull, bear, hamm, shot

def get_htf(symbol):
    try:
        c, _, _, _, _ = fetch_ohlcv(symbol, "5m", "5d")
        if not c or len(c)<20: return 0
        e10=calc_ema(c,10); e20=calc_ema(c,20); r=calc_rsi(c)
        if e10>e20 and r>50: return 1
        if e10<e20 and r<50: return -1
        return 0
    except: return 0


def get_signal(symbol):
    try:
        closes,highs,lows,opens,vols = fetch_ohlcv(symbol,"1m","5d")
        if not closes or len(closes)<60: return None

        rsi  = calc_rsi(closes)
        rsip = calc_rsi(closes[:-1])
        macd,sig,h,h1 = calc_macd(closes)
        e9   = calc_ema(closes,9)
        e21  = calc_ema(closes,21)
        e50  = calc_ema(closes,50)
        bbu,bbm,bbl = calc_bb(closes)
        sk,sk1 = calc_stoch(highs,lows,closes)
        atr  = calc_atr(highs,lows,closes)
        price= closes[-1]
        vola = sum(vols[-20:])/20 if len(vols)>=20 else 1
        volok= vols[-1] > vola*1.2
        be,br,hm,sh = detect_pattern(opens,closes,highs,lows)
        htf  = get_htf(symbol)

        bs=0; br_=[]
        if rsi<35:              bs+=1; br_.append("RSI oversold")
        if rsi>rsip and rsi<45: bs+=1; br_.append("RSI turning up ↑")
        if macd>sig and h>h1:   bs+=1; br_.append("MACD bullish cross")
        if e9>e21>e50:          bs+=1; br_.append("EMA stack bullish")
        if price>bbm:           bs+=1; br_.append("Price above BB mid")
        if price<=bbl*1.002:    bs+=1; br_.append("BB lower bounce")
        if sk<25 and sk>sk1:    bs+=1; br_.append("Stoch oversold ↑")
        if volok:               bs+=1; br_.append("Volume spike ✓")
        if be or hm:            bs+=1; br_.append("Bullish candle pattern")
        if htf==1:              bs+=1; br_.append("5m trend bullish ✓")

        ss=0; sr=[]
        if rsi>65:              ss+=1; sr.append("RSI overbought")
        if rsi<rsip and rsi>55: ss+=1; sr.append("RSI turning down ↓")
        if macd<sig and h<h1:   ss+=1; sr.append("MACD bearish cross")
        if e9<e21<e50:          ss+=1; sr.append("EMA stack bearish")
        if price<bbm:           ss+=1; sr.append("Price below BB mid")
        if price>=bbu*0.998:    ss+=1; sr.append("BB upper reject")
        if sk>75 and sk<sk1:    ss+=1; sr.append("Stoch overbought ↓")
        if volok:               ss+=1; sr.append("Volume spike ✓")
        if br or sh:            ss+=1; sr.append("Bearish candle pattern")
        if htf==-1:             ss+=1; sr.append("5m trend bearish ✓")

        if bs>=MIN_SCORE:
            return {"direction":"BUY ⬆️","price":price,"rsi":rsi,
                    "score":bs,"reasons":br_,"atr":atr}
        if ss>=MIN_SCORE:
            return {"direction":"SELL ⬇️","price":price,"rsi":rsi,
                    "score":ss,"reasons":sr,"atr":atr}
        return None
    except Exception as e:
        logging.error(f"Signal error {symbol}: {e}")
        return None


async def check_results():
    now  = datetime.now()
    done = [t for t in pending_trades
            if (now-t["time"]).total_seconds()/60 >= TRADE_MIN]
    for trade in done:
        pending_trades.remove(trade)
        try:
            closes,_,_,_,_ = fetch_ohlcv(trade["symbol"],"1m","1d")
            if not closes: continue
            ex   = closes[-1]; en = trade["entry_price"]
            win  = (ex>en) if "BUY" in trade["direction"] else (ex<en)
            stats["total"] += 1
            stats["win" if win else "loss"] += 1
            wr   = stats["win"]/stats["total"]*100
            pips = abs(ex-en)*10000
            msg  = (
                f"{'✅' if win else '❌'} *ট্রেড রেজাল্ট*\n"
                f"━━━━━━━━━━━━━━━\n"
                f"💱 Pair   : `{trade['name']}`\n"
                f"📌 Dir    : {trade['direction']}\n"
                f"💵 Entry  : `{en:.5f}`\n"
                f"💵 Exit   : `{ex:.5f}`\n"
                f"📐 Pips   : `{pips:.1f}`\n"
                f"🎯 Result : *{'✅ WIN 🎉' if win else '❌ LOSS'}*\n"
                f"━━━━━━━━━━━━━━━\n"
                f"✅ {stats['win']}W  ❌ {stats['loss']}L  🏆 {wr:.1f}%"
            )
            await bot.send_message(chat_id=CHAT_ID,text=msg,parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Result error: {e}")


def build_msg(name, sig):
    rt = "\n".join(f"  ✔️ {r}" for r in sig["reasons"])
    sc = sig["score"]
    badge = "🔥🔥 PERFECT" if sc==10 else "🔥 VERY STRONG" if sc>=8 else "💪 STRONG"
    return (
        f"📊 *Quotex Signal*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💱 Pair     : `{name}`\n"
        f"📌 Direction: *{sig['direction']}*\n"
        f"⚡ Strength : *{badge}*\n"
        f"💵 Price    : `{sig['price']:.5f}`\n"
        f"🔢 RSI      : `{sig['rsi']:.2f}`\n"
        f"🏆 Score    : *{sc}/10*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📋 *এনালাইসিস:*\n{rt}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⏱ TF: 1m  |  Expiry: {TRADE_MIN}m\n"
        f"⚠️ _Trade at your own risk!_"
    )


async def auto_scan(app):
    await asyncio.sleep(5)
    while True:
        logging.info("Scanning...")
        await check_results()
        for name, symbol in PAIRS.items():
            last = last_signals.get(name)
            if last and (datetime.now()-last).total_seconds()<290:
                continue
            result = get_signal(symbol)
            if result:
                last_signals[name] = datetime.now()
                await bot.send_message(
                    chat_id=CHAT_ID,
                    text=build_msg(name,result),
                    parse_mode="Markdown"
                )
                pending_trades.append({
                    "name":name,"symbol":symbol,
                    "direction":result["direction"],
                    "entry_price":result["price"],
                    "time":datetime.now()
                })
                await asyncio.sleep(2)
        await asyncio.sleep(CHECK_SEC)


async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ *Quotex Signal Bot চালু!*\n\n"
        "⏱ Timeframe : 1 মিনিট\n"
        "🔄 স্ক্যান   : প্রতি ৫ মিনিট\n"
        "🏁 রেজাল্ট  : ট্রেড শেষে অটো\n"
        "🏆 Min Score : ৭/১০\n\n"
        "/scan — এখনই স্ক্যান\n"
        "/stats — Win/Loss দেখো",
        parse_mode="Markdown"
    )

async def scan_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 এনালাইসিস চলছে...")
    found = False
    for name, symbol in PAIRS.items():
        result = get_signal(symbol)
        if result:
            found = True
            await update.message.reply_text(build_msg(name,result),parse_mode="Markdown")
            pending_trades.append({
                "name":name,"symbol":symbol,
                "direction":result["direction"],
                "entry_price":result["price"],
                "time":datetime.now()
            })
    if not found:
        await update.message.reply_text("⏳ এখন কোনো STRONG সিগন্যাল নেই।")

async def stats_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    total = stats["total"]
    wr = (stats["win"]/total*100) if total>0 else 0
    await update.message.reply_text(
        f"📊 *স্ট্যাটস*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"✅ Win     : {stats['win']}\n"
        f"❌ Loss    : {stats['loss']}\n"
        f"📋 Total   : {total}\n"
        f"🏆 Win Rate: `{wr:.1f}%`\n"
        f"⏳ Pending : {len(pending_trades)} ট্রেড",
        parse_mode="Markdown"
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("scan",  scan_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    loop = asyncio.get_event_loop()
    loop.create_task(auto_scan(app))
    print("🤖 Bot চালু!")
    app.run_polling()

if __name__ == "__main__":
    main()
