mkdir -p Trading-Analyzer/templates Trading-Analyzer/static && cd Trading-Analyzer

cat << 'EOF' > requirements.txt
Flask==3.0.0
pandas==2.1.4
numpy==1.26.2
gunicorn==21.2.0
EOF

cat << 'EOF' > Procfile
web: gunicorn app:app
EOF

cat << 'EOF' > indicators.py
import pandas as pd
import numpy as np

def ema(data, period):
    return data["close"].ewm(span=period, adjust=False).mean()

def rsi(data, period=14):
    delta = data["close"].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / (avg_loss.replace(0, np.nan))
    rsi_val = 100 - (100 / (1 + rs))
    return rsi_val.fillna(50)

def macd(data, fast=12, slow=26, signal=9):
    ema_fast = ema(data, fast)
    ema_slow = ema(data, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def bollinger_bands(data, period=20, std_dev=2):
    middle = data["close"].rolling(period).mean()
    std = data["close"].rolling(period).std()
    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)
    return upper, middle, lower

def atr(data, period=14):
    high_low = data["high"] - data["low"]
    high_close = (data["high"] - data["close"].shift()).abs()
    low_close = (data["low"] - data["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def adx(data, period=14):
    df = data.copy()
    df['up'] = df['high'] - df['high'].shift(1)
    df['down'] = df['low'].shift(1) - df['low']
    df['+dm'] = np.where((df['up'] > df['down']) & (df['up'] > 0), df['up'], 0)
    df['-dm'] = np.where((df['down'] > df['up']) & (df['down'] > 0), df['down'], 0)
    
    tr_series = atr(df, period=1)
    atr_series = tr_series.rolling(period).mean()
    
    plus_di = 100 * (df['+dm'].rolling(period).mean() / atr_series)
    minus_di = 100 * (df['-dm'].rolling(period).mean() / atr_series)
    
    di_sum = plus_di + minus_di
    dx = 100 * (np.abs(plus_di - minus_di) / di_sum.replace(0, np.nan))
    return dx.rolling(period).mean().fillna(0)

def detect_candlestick_patterns(data):
    patterns = []
    if len(data) < 2:
        return patterns
    
    curr = data.iloc[-1]
    prev = data.iloc[-2]
    
    if prev['close'] < prev['open'] and curr['close'] > curr['open']:
        if curr['close'] >= prev['open'] and curr['open'] <= prev['close']:
            patterns.append("Bullish Engulfing")
            
    if prev['close'] > prev['open'] and curr['close'] < curr['open']:
        if curr['close'] <= prev['open'] and curr['open'] >= prev['close']:
            patterns.append("Bearish Engulfing")
            
    body = abs(curr['close'] - curr['open'])
    lower_wick = curr['open'] - curr['low'] if curr['close'] >= curr['open'] else curr['close'] - curr['low']
    upper_wick = curr['high'] - curr['close'] if curr['close'] >= curr['open'] else curr['high'] - curr['open']
    
    if lower_wick >= (2 * body) and upper_wick <= body:
        patterns.append("Hammer")
        
    return patterns

def support_resistance(data, window=20):
    support = data['low'].tail(window).min()
    resistance = data['high'].tail(window).max()
    return support, resistance
EOF

cat << 'EOF' > data.py
import pandas as pd

def load_data(file_name_or_stream):
    df = pd.read_csv(file_name_or_stream)
    df.columns = [c.lower() for c in df.columns]
    
    required_cols = ["time", "open", "high", "low", "close", "volume"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Missing column: {col}")
            
    return df
EOF

cat << 'EOF' > strategy.py
from indicators import (
    ema, rsi, macd, bollinger_bands, atr, adx, 
    detect_candlestick_patterns, support_resistance
)

def analyze(df):
    df["EMA9"] = ema(df, 9)
    df["EMA21"] = ema(df, 21)
    df["RSI"] = rsi(df)
    df["MACD"], df["MACD_SIGNAL"], df["MACD_HIST"] = macd(df)
    df["BB_UPPER"], df["BB_MIDDLE"], df["BB_LOWER"] = bollinger_bands(df)
    df["ATR"] = atr(df)
    df["ADX"] = adx(df)

    last = df.iloc[-1]
    patterns = detect_candlestick_patterns(df)
    support, resistance = support_resistance(df)

    bullish_score = 0
    bearish_score = 0

    if last["EMA9"] > last["EMA21"]: bullish_score += 1
    if last["EMA9"] < last["EMA21"]: bearish_score += 1

    if last["RSI"] < 30: bullish_score += 1.5
    elif last["RSI"] > 70: bearish_score += 1.5

    if last["MACD"] > last["MACD_SIGNAL"]: bullish_score += 1
    if last["MACD"] < last["MACD_SIGNAL"]: bearish_score += 1

    if "Bullish Engulfing" in patterns or "Hammer" in patterns: bullish_score += 1
    if "Bearish Engulfing" in patterns: bearish_score += 1

    signal = "NO_TRADE"
    if bullish_score >= 2.5 and bearish_score < 1.5:
        signal = "UP"
    elif bearish_score >= 2.5 and bullish_score < 1.5:
        signal = "DOWN"

    analysis_data = {
        "ema9": float(last["EMA9"]),
        "ema21": float(last["EMA21"]),
        "rsi": float(last["RSI"]),
        "macd": float(last["MACD"]),
        "macd_signal": float(last["MACD_SIGNAL"]),
        "bb_upper": float(last["BB_UPPER"]),
        "bb_middle": float(last["BB_MIDDLE"]),
        "bb_lower": float(last["BB_LOWER"]),
        "atr": float(last["ATR"]),
        "adx": float(last["ADX"]),
        "patterns": patterns,
        "support": float(support),
        "resistance": float(resistance)
    }

    chart_data = {
        "times": df["time"].tail(50).astype(str).tolist(),
        "close": df["close"].tail(50).tolist(),
        "ema9": df["EMA9"].tail(50).tolist(),
        "ema21": df["EMA21"].tail(50).tolist(),
        "bb_upper": df["BB_UPPER"].tail(50).tolist(),
        "bb_lower": df["BB_LOWER"].tail(50).tolist()
    }

    return signal, analysis_data, chart_data
EOF

cat << 'EOF' > app.py
from flask import Flask, render_template, request, jsonify
from data import load_data
from strategy import analyze

app = Flask(__name__)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/analyze", methods=["POST"])
def analyze_data():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    try:
        df = load_data(file)
        signal, analysis, chart_data = analyze(df)
        return jsonify({
            "signal": signal,
            "analysis": analysis,
            "chart_data": chart_data
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
EOF

cat << 'EOF' > templates/index.html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trading Analyzer Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <div class="container-fluid py-4">
        <header class="pb-3 mb-4 border-bottom d-flex justify-content-between align-items-center">
            <h1 class="h3 text-primary font-weight-bold">📈 Trading Analyzer Pro</h1>
            <span class="badge bg-success fs-6">Live Server</span>
        </header>

        <div class="row g-4">
            <div class="col-lg-4">
                <div class="card shadow-sm mb-4">
                    <div class="card-header bg-dark text-white"><h5 class="card-title mb-0">Upload CSV Data</h5></div>
                    <div class="card-body">
                        <form id="uploadForm">
                            <div class="mb-3">
                                <input class="form-control" type="file" id="csvFile" accept=".csv" required>
                            </div>
                            <button type="submit" class="btn btn-primary w-100">Analyze Market</button>
                        </form>
                    </div>
                </div>

                <div class="card shadow-sm text-center">
                    <div class="card-header bg-secondary text-white"><h5 class="card-title mb-0">Trading Signal</h5></div>
                    <div class="card-body py-4">
                        <h2 id="signalResult" class="display-4 font-weight-bold text-muted">WAITING</h2>
                        <p id="signalDetails" class="text-muted mb-0">Upload CSV to see technical pattern confirmation.</p>
                    </div>
                </div>
            </div>

            <div class="col-lg-8">
                <div class="card shadow-sm mb-4">
                    <div class="card-header bg-dark text-white"><h5 class="card-title mb-0">Market Price & Indicators Chart</h5></div>
                    <div class="card-body">
                        <canvas id="marketChart" style="max-height: 400px;"></canvas>
                    </div>
                </div>

                <div class="card shadow-sm">
                    <div class="card-header bg-dark text-white"><h5 class="card-title mb-0">Technical Indicators Breakdown</h5></div>
                    <div class="card-body">
                        <div class="table-responsive">
                            <table class="table table-hover table-bordered mb-0">
                                <thead class="table-light">
                                    <tr>
                                        <th>Indicator</th>
                                        <th>Value</th>
                                        <th>Interpretation</th>
                                    </tr>
                                </thead>
                                <tbody id="indicatorTable">
                                    <tr><td colspan="3" class="text-center text-muted">No analysis performed yet.</td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <script src="/static/app.js"></script>
</body>
</html>
EOF

cat << 'EOF' > static/style.css
body { background-color: #f8f9fa; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
.card { border-radius: 10px; border: none; }
.card-header { border-top-left-radius: 10px !important; border-top-right-radius: 10px !important; }
#signalResult.UP { color: #198754 !important; }
#signalResult.DOWN { color: #dc3545 !important; }
#signalResult.NO_TRADE { color: #ffc107 !important; }
EOF

cat << 'EOF' > static/app.js
let chartInstance = null;

document.getElementById('uploadForm').addEventListener('submit', async function (e) {
    e.preventDefault();
    const fileInput = document.getElementById('csvFile');
    if (!fileInput.files.length) return;

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);

    try {
        const response = await fetch('/analyze', { method: 'POST', body: formData });
        const data = await response.json();
        if (data.error) { alert(data.error); return; }

        const signalBox = document.getElementById('signalResult');
        signalBox.innerText = data.signal.replace('_', ' ');
        signalBox.className = `display-4 font-weight-bold ${data.signal}`;
        document.getElementById('signalDetails').innerText = `Detected Patterns: ${data.analysis.patterns.join(', ') || 'None'}`;

        const tableBody = document.getElementById('indicatorTable');
        tableBody.innerHTML = `
            <tr><td>RSI (14)</td><td>${data.analysis.rsi.toFixed(2)}</td><td>${data.analysis.rsi > 70 ? 'Overbought' : data.analysis.rsi < 30 ? 'Oversold' : 'Neutral'}</td></tr>
            <tr><td>EMA (9 / 21)</td><td>${data.analysis.ema9.toFixed(2)} / ${data.analysis.ema21.toFixed(2)}</td><td>${data.analysis.ema9 > data.analysis.ema21 ? 'Bullish' : 'Bearish'}</td></tr>
            <tr><td>MACD</td><td>${data.analysis.macd.toFixed(2)} (Signal: ${data.analysis.macd_signal.toFixed(2)})</td><td>${data.analysis.macd > data.analysis.macd_signal ? 'Bullish Crossover' : 'Bearish Crossover'}</td></tr>
            <tr><td>ADX (14)</td><td>${data.analysis.adx.toFixed(2)}</td><td>${data.analysis.adx > 25 ? 'Strong Trend' : 'Ranging Market'}</td></tr>
            <tr><td>ATR (14)</td><td>${data.analysis.atr.toFixed(2)}</td><td>Market Volatility</td></tr>
            <tr><td>Bollinger Bands</td><td>Upper: ${data.analysis.bb_upper.toFixed(2)} | Lower: ${data.analysis.bb_lower.toFixed(2)}</td><td>Middle: ${data.analysis.bb_middle.toFixed(2)}</td></tr>
            <tr><td>Support & Resistance</td><td>Support: ${data.analysis.support.toFixed(2)} | Resistance: ${data.analysis.resistance.toFixed(2)}</td><td>Key Price Range</td></tr>
        `;

        renderChart(data.chart_data);
    } catch (err) {
        console.error(err);
        alert('An error occurred during market analysis.');
    }
});

function renderChart(chartData) {
    const ctx = document.getElementById('marketChart').getContext('2d');
    if (chartInstance) chartInstance.destroy();

    chartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: chartData.times,
            datasets: [
                { label: 'Close Price', data: chartData.close, borderColor: '#0d6efd', borderWidth: 2, fill: false },
                { label: 'EMA 9', data: chartData.ema9, borderColor: '#20c997', borderWidth: 1.5, fill: false },
                { label: 'EMA 21', data: chartData.ema21, borderColor: '#fd7e14', borderWidth: 1.5, fill: false },
                { label: 'BB Upper', data: chartData.bb_upper, borderColor: '#6c757d', borderDash: [5, 5], fill: false },
                { label: 'BB Lower', data: chartData.bb_lower, borderColor: '#6c757d', borderDash: [5, 5], fill: false }
            ]
        },
        options: { responsive: true, plugins: { legend: { position: 'top' } } }
    });
}
EOF
