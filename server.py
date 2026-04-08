"""
APEX Alert Tracker - Webhook Server
Receives TradingView alerts and stores them in SQLite database
Deploy to Railway/Render for free hosting
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import json
from datetime import datetime
import os

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

@app.route('/')
def index():
    return app.send_static_file('dashboard.html')

@app.route('/dashboard')
def dashboard():
    return app.send_static_file('dashboard.html')

@app.route('/dial')
def dial():
    return app.send_static_file('dial.html')

DB_PATH = os.environ.get('DB_PATH', 'apex_trades.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL,
            ticker TEXT,
            action TEXT,
            price REAL,
            strategy TEXT,
            timeframe TEXT,
            trend TEXT,
            rsi REAL,
            atr REAL,
            position_size REAL,
            raw_message TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_alert_id INTEGER,
            exit_alert_id INTEGER,
            ticker TEXT,
            direction TEXT,
            entry_price REAL,
            exit_price REAL,
            stop_price REAL,
            target_price REAL,
            contracts INTEGER DEFAULT 1,
            entry_time TEXT,
            exit_time TEXT,
            pnl REAL,
            result TEXT,
            strategy TEXT,
            timeframe TEXT,
            entry_trend TEXT,
            entry_rsi REAL,
            entry_atr REAL,
            hold_minutes REAL,
            notes TEXT,
            FOREIGN KEY (entry_alert_id) REFERENCES alerts(id)
        )
    ''')
    conn.commit()
    conn.close()

# Point values per instrument
POINT_VALUES = {
    'MNQ': 2, 'MNQ1!': 2, 'MNQH6': 2, 'MNQH2026': 2,
    'MES': 5, 'MES1!': 5, 'MESH6': 5, 'MESH2026': 5,
    'MGC': 10, 'MGC1!': 10, 'MGCJ6': 10,
    'MCL': 100, 'MCL1!': 100,
    'M2K': 5, 'MBT': 25
}

def get_point_value(ticker):
    for key, val in POINT_VALUES.items():
        if key in ticker.upper():
            return val
    return 2

def parse_tradingview_alert(body):
    """Parse TradingView webhook payload"""
    import re
    try:
        # Try JSON first
        if isinstance(body, dict):
            data = body
        else:
            text = body if isinstance(body, str) else body.decode('utf-8')
            # Strip any wrapping whitespace or BOM
            text = text.strip().lstrip('\ufeff')
            data = json.loads(text)

        # Fix corrupted ticker — extract real symbol from settlement-as-close format
        # e.g. "={"settlement-as-close":true,"symbol":"CME_MINI:MNQ1!"}" -> "MNQ1"
        import re as _re
        ticker = data.get('ticker', '')
        if 'settlement-as-close' in str(ticker) or 'symbol' in str(ticker):
            sym_match = _re.search(r'"symbol"\s*:\s*"([^"]+)"', str(ticker))
            if sym_match:
                full_sym = sym_match.group(1)
                # Extract just the instrument name e.g. MNQ1! from CME_MINI:MNQ1!
                inst_match = _re.search(r'(MNQ|MES|MGC|MCL|M2K|MBT)\w*', full_sym)
                if inst_match:
                    data['ticker'] = inst_match.group(0)

        # Ensure all numeric fields are properly parsed
        for price_key in ['price', 'close']:
            if price_key in data:
                try:
                    val = float(str(data[price_key]).replace(',', ''))
                    if val > 0:
                        data['price'] = val
                        break
                except:
                    pass

        for key in ['stop', 'target', 'atr', 'adx', 'rsi', 'vol_ratio']:
            if key in data:
                try:
                    data[key] = float(str(data[key]).replace(',', ''))
                except:
                    pass

        return data

    except:
        # Fallback: parse plain text format
        result = {}
        text = body if isinstance(body, str) else body.decode('utf-8')

        if 'BUY' in text.upper():
            result['action'] = 'BUY'
        elif 'SELL' in text.upper():
            result['action'] = 'SELL'
        elif 'EXIT' in text.upper():
            result['action'] = 'EXIT'
        elif 'CLOSED' in text.upper():
            result['action'] = 'CLOSED'

        ticker_match = re.search(r'\b(MNQ|MES|MGC|MCL|M2K|MBT)\w*', text, re.IGNORECASE)
        if ticker_match:
            result['ticker'] = ticker_match.group(0).upper()

        # Try multiple price patterns
        for pattern in [r'"price"\s*:\s*([0-9.]+)', r'@\s*([0-9.]+)', r'price[:\s]+([0-9,.]+)']:
            price_match = re.search(pattern, text, re.IGNORECASE)
            if price_match:
                try:
                    result['price'] = float(price_match.group(1).replace(',', ''))
                    break
                except:
                    pass

        return result

@app.route('/webhook', methods=['POST'])
def receive_webhook():
    """Main webhook endpoint for TradingView alerts"""
    import re as _re
    try:
        raw = request.get_data(as_text=True)

        # Parse raw body first — most reliable
        parsed = parse_tradingview_alert(raw)

        # Flask JSON parser as backup
        try:
            flask_data = request.get_json(force=True) or {}
        except:
            flask_data = {}

        # Merge — parsed raw takes priority
        data = {**flask_data, **parsed}

        # Last resort price extraction directly from raw string
        raw_price = data.get('price', 0)
        try:
            raw_price = float(raw_price)
        except:
            raw_price = 0
        if raw_price <= 1:
            pm = _re.search(r'"price"\s*:\s*([0-9]+\.?[0-9]*)', raw)
            if pm:
                extracted = float(pm.group(1))
                if extracted > 1:
                    data['price'] = extracted

        # Store raw alert
        conn = get_db()
        cursor = conn.execute('''
            INSERT INTO alerts 
            (received_at, ticker, action, price, strategy, timeframe, trend, rsi, atr, position_size, raw_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.utcnow().isoformat(),
            data.get('ticker', data.get('symbol', 'UNKNOWN')),
            data.get('action', data.get('order_action', '')),
            data.get('price', data.get('close', 0)),
            data.get('strategy', ''),
            data.get('timeframe', data.get('interval', '')),
            data.get('trend', ''),
            data.get('rsi', 0),
            data.get('atr', 0),
            data.get('position_size', 0),
            raw
        ))
        alert_id = cursor.lastrowid

        action = data.get('action', '').upper()
        ticker = data.get('ticker', data.get('symbol', ''))
        price  = float(data.get('price', 0) or 0)
        atr    = float(data.get('atr',   0) or 0)
        rsi    = float(data.get('rsi',   0) or 0)
        stop   = data.get('stop',   None)
        target = data.get('target', None)
        trend  = data.get('trend',  '')
        tf     = data.get('timeframe', '')

        # Handle BUY/SELL — create new open trade
        if action in ['BUY', 'SELL']:
            conn.execute('''
                INSERT INTO trades 
                (entry_alert_id, ticker, direction, entry_price, stop_price, target_price, entry_time, 
                 result, strategy, timeframe, entry_trend, entry_rsi, entry_atr, contracts)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, 1)
            ''', (
                alert_id,
                ticker,
                action,
                price,
                stop,
                target,
                datetime.utcnow().isoformat(),
                data.get('strategy', ''),
                tf,
                trend,
                rsi,
                atr
            ))

        # Handle EXIT/CLOSED — close the open trade
        elif action in ['EXIT', 'CLOSED', 'EXIT LONG', 'EXIT SHORT']:
            open_trade = conn.execute('''
                SELECT * FROM trades 
                WHERE ticker = ? AND result = 'OPEN'
                ORDER BY entry_time DESC LIMIT 1
            ''', (ticker,)).fetchone()

            if open_trade:
                entry_time = datetime.fromisoformat(open_trade['entry_time'])
                exit_time = datetime.utcnow()
                hold_minutes = (exit_time - entry_time).total_seconds() / 60

                point_value = get_point_value(ticker)
                contracts = open_trade['contracts'] or 1

                if open_trade['direction'] == 'BUY':
                    pnl = (price - open_trade['entry_price']) * point_value * contracts
                else:
                    pnl = (open_trade['entry_price'] - price) * point_value * contracts

                result = 'WIN' if pnl > 0 else 'LOSS' if pnl < 0 else 'SCRATCH'

                conn.execute('''
                    UPDATE trades SET
                        exit_alert_id = ?,
                        exit_price = ?,
                        exit_time = ?,
                        pnl = ?,
                        result = ?,
                        hold_minutes = ?
                    WHERE id = ?
                ''', (alert_id, price, exit_time.isoformat(), pnl, result, hold_minutes, open_trade['id']))

        conn.commit()
        conn.close()

        return jsonify({'status': 'ok', 'alert_id': alert_id, 'action': action}), 200

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/trades', methods=['GET'])
def get_trades():
    """Get all trades with filters"""
    conn = get_db()
    ticker = request.args.get('ticker')
    result = request.args.get('result')
    days = int(request.args.get('days', 30))

    query = '''SELECT * FROM trades WHERE entry_time >= datetime('now', ?) '''
    params = [f'-{days} days']

    if ticker:
        query += ' AND ticker = ?'
        params.append(ticker)
    if result:
        query += ' AND result = ?'
        params.append(result)

    query += ' ORDER BY entry_time DESC'
    trades = [dict(row) for row in conn.execute(query, params).fetchall()]
    conn.close()
    return jsonify(trades)

@app.route('/analytics', methods=['GET'])
def get_analytics():
    """Deep analytics — win rates by time, strategy, trend, R:R"""
    conn = get_db()
    days = int(request.args.get('days', 30))

    closed = conn.execute('''
        SELECT * FROM trades 
        WHERE result != 'OPEN' 
        AND entry_time >= datetime('now', ?)
    ''', (f'-{days} days',)).fetchall()
    closed = [dict(r) for r in closed]

    def win_rate(trades):
        if not trades: return 0
        wins = sum(1 for t in trades if t['result'] == 'WIN')
        return round(wins / len(trades) * 100, 1)

    def avg_pnl(trades):
        if not trades: return 0
        return round(sum(t['pnl'] or 0 for t in trades) / len(trades), 2)

    # By instrument
    tickers = list(set(t['ticker'] for t in closed))
    by_ticker = {}
    for tk in tickers:
        group = [t for t in closed if t['ticker'] == tk]
        by_ticker[tk] = {
            'trades': len(group),
            'win_rate': win_rate(group),
            'avg_pnl': avg_pnl(group),
            'total_pnl': round(sum(t['pnl'] or 0 for t in group), 2)
        }

    # By strategy
    strategies = list(set(t['strategy'] for t in closed if t['strategy']))
    by_strategy = {}
    for s in strategies:
        group = [t for t in closed if t['strategy'] == s]
        by_strategy[s] = {
            'trades': len(group),
            'win_rate': win_rate(group),
            'avg_pnl': avg_pnl(group)
        }

    # By time of day (hour buckets)
    by_hour = {}
    for t in closed:
        try:
            hour = datetime.fromisoformat(t['entry_time']).hour
            bucket = f"{hour:02d}:00"
            if bucket not in by_hour:
                by_hour[bucket] = []
            by_hour[bucket].append(t)
        except: pass
    by_hour_stats = {h: {'trades': len(v), 'win_rate': win_rate(v), 'avg_pnl': avg_pnl(v)} for h, v in by_hour.items()}

    # By trend
    by_trend = {}
    for t in closed:
        trend = t.get('entry_trend') or 'UNKNOWN'
        if trend not in by_trend:
            by_trend[trend] = []
        by_trend[trend].append(t)
    by_trend_stats = {tr: {'trades': len(v), 'win_rate': win_rate(v), 'avg_pnl': avg_pnl(v)} for tr, v in by_trend.items()}

    # Overall
    total_pnl = sum(t['pnl'] or 0 for t in closed)
    avg_hold = sum(t['hold_minutes'] or 0 for t in closed) / len(closed) if closed else 0

    analytics = {
        'summary': {
            'total_trades': len(closed),
            'open_trades': conn.execute("SELECT COUNT(*) FROM trades WHERE result='OPEN'").fetchone()[0],
            'win_rate': win_rate(closed),
            'total_pnl': round(total_pnl, 2),
            'avg_pnl': avg_pnl(closed),
            'avg_hold_minutes': round(avg_hold, 1),
            'wins': sum(1 for t in closed if t['result'] == 'WIN'),
            'losses': sum(1 for t in closed if t['result'] == 'LOSS')
        },
        'by_ticker': by_ticker,
        'by_strategy': by_strategy,
        'by_hour': by_hour_stats,
        'by_trend': by_trend_stats
    }

    conn.close()
    return jsonify(analytics)

@app.route('/update_trade/<int:trade_id>', methods=['POST'])
def update_trade(trade_id):
    """Manually update trade details — stop, target, contracts, notes"""
    data = request.get_json()
    conn = get_db()
    fields = []
    values = []
    for field in ['stop_price', 'target_price', 'contracts', 'notes', 'result', 'exit_price', 'pnl']:
        if field in data:
            fields.append(f'{field} = ?')
            values.append(data[field])

    if fields:
        values.append(trade_id)
        conn.execute(f'UPDATE trades SET {", ".join(fields)} WHERE id = ?', values)
        conn.commit()

    conn.close()
    return jsonify({'status': 'ok'})

@app.route('/alerts', methods=['GET'])
def get_alerts():
    conn = get_db()
    alerts = [dict(r) for r in conn.execute('SELECT * FROM alerts ORDER BY received_at DESC LIMIT 100').fetchall()]
    conn.close()
    return jsonify(alerts)

@app.route('/delete_trade/<int:trade_id>', methods=['DELETE', 'POST', 'GET'])
def delete_trade(trade_id):
    conn = get_db()
    conn.execute('DELETE FROM trades WHERE id = ?', (trade_id,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok', 'deleted': trade_id})

@app.route('/delete_by_ticker/<ticker>', methods=['GET', 'POST'])
def delete_by_ticker(ticker):
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM trades WHERE ticker LIKE ?", (f'%{ticker}%',)).fetchone()[0]
    conn.execute("DELETE FROM trades WHERE ticker LIKE ?", (f'%{ticker}%',))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok', 'ticker': ticker, 'deleted': count})

@app.route('/clear_open_trades', methods=['DELETE', 'POST'])
def clear_open_trades():
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM trades WHERE result = 'OPEN'").fetchone()[0]
    conn.execute("DELETE FROM trades WHERE result = 'OPEN'")
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok', 'cleared': count})

@app.route('/clear_all_trades', methods=['DELETE', 'POST'])
def clear_all_trades():
    conn = get_db()
    conn.execute('DELETE FROM trades')
    conn.execute('DELETE FROM alerts')
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok', 'message': 'All trades and alerts cleared'})

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'running', 'time': datetime.utcnow().isoformat()})

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

# This ensures DB is initialized when gunicorn imports the module
with app.app_context():
    init_db()

@app.route('/test_trade', methods=['GET'])
def test_trade():
    """Send a fake BUY then WIN to verify P&L calculation"""
    action = request.args.get('action', 'BUY')
    ticker = request.args.get('ticker', 'MNQ1!')
    price  = float(request.args.get('price', 21500))
    result = request.args.get('result', '')

    conn = get_db()
    cursor = conn.execute('''
        INSERT INTO alerts (received_at, ticker, action, price, raw_message)
        VALUES (?, ?, ?, ?, ?)
    ''', (datetime.utcnow().isoformat(), ticker, action, price, str({'action':action,'ticker':ticker,'price':price})))
    alert_id = cursor.lastrowid

    if action in ['BUY', 'SELL']:
        conn.execute('''
            INSERT INTO trades (entry_alert_id, ticker, direction, entry_price, entry_time, result, contracts)
            VALUES (?, ?, ?, ?, ?, 'OPEN', 1)
        ''', (alert_id, ticker, action, price, datetime.utcnow().isoformat()))

    elif action == 'CLOSED':
        open_trade = conn.execute('''
            SELECT * FROM trades WHERE ticker = ? AND result = 'OPEN'
            ORDER BY entry_time DESC LIMIT 1
        ''', (ticker,)).fetchone()
        if open_trade:
            point_value = get_point_value(ticker)
            pnl = (price - open_trade['entry_price']) * point_value if open_trade['direction'] == 'BUY' else (open_trade['entry_price'] - price) * point_value
            res = result if result else ('WIN' if pnl > 0 else 'LOSS')
            conn.execute('''
                UPDATE trades SET exit_price=?, exit_time=?, pnl=?, result=?, hold_minutes=1
                WHERE id=?
            ''', (price, datetime.utcnow().isoformat(), pnl, res, open_trade['id']))

    conn.commit()
    conn.close()
    return jsonify({'status': 'ok', 'action': action, 'ticker': ticker, 'price': price})
