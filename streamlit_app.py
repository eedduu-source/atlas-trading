from datetime import date, timedelta
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title='ATLAS · Técnico + Fundamental', layout='wide')
st.title('ATLAS · Análisis técnico y fundamental')
st.caption('Precios diarios: Alpha Vantage · Fundamentales: SEC EDGAR cuando aplica · Yahoo Finance no utilizado')

EUROPE = ['MC', 'SW', 'DE', 'PA', 'MI', 'AS', 'BR', 'LS', 'L', 'ST', 'OL', 'CO', 'HE', 'VI', 'WA', 'PL', 'PR', 'BU', 'AT', 'IR', 'RO', 'ZL', 'IST']

def api_key():
    try:
        return st.secrets.get('ALPHAVANTAGE_API_KEY', '')
    except Exception:
        return ''

@st.cache_data(ttl=900)
def prices(ticker):
    key = api_key()
    if not key:
        raise RuntimeError('Falta configurar ALPHAVANTAGE_API_KEY en Streamlit → Settings → Secrets.')
    ticker = ticker.strip().upper()
    symbols = [ticker] if '.' in ticker else [ticker] + [f'{ticker}.{market}' for market in EUROPE]
    errors = []
    for symbol in symbols:
        try:
            response = requests.get('https://www.alphavantage.co/query', params={'function': 'TIME_SERIES_DAILY', 'symbol': symbol, 'outputsize': 'full', 'apikey': key}, timeout=20)
            payload = response.json() if response.content else {}
            series = payload.get('Time Series (Daily)') if isinstance(payload, dict) else None
            if isinstance(series, dict) and len(series) >= 60:
                rows = [{'Date': d, 'Open': v.get('1. open'), 'High': v.get('2. high'), 'Low': v.get('3. low'), 'Close': v.get('4. close'), 'Volume': v.get('5. volume')} for d, v in series.items()]
                frame = pd.DataFrame(rows)
                for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                    frame[col] = pd.to_numeric(frame[col], errors='coerce')
                frame['Date'] = pd.to_datetime(frame['Date'])
                return frame.sort_values('Date').dropna(subset=['Open', 'High', 'Low', 'Close']).reset_index(drop=True), symbol
            errors.append(f'{symbol}: respuesta sin serie diaria')
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f'{symbol}: {exc}')
    raise RuntimeError('No se obtuvo una serie válida de Alpha Vantage. ' + '; '.join(errors[:3]))

@st.cache_data(ttl=21600)
def sec_data(ticker):
    try:
        headers = {'User-Agent': 'ATLAS research contact=research@example.invalid'}
        catalog = requests.get('https://www.sec.gov/files/company_tickers.json', headers=headers, timeout=20).json() or {}
        match = next((x for x in catalog.values() if x.get('ticker', '').upper() == ticker.upper()), None)
        if not match:
            return None, 'SEC no identifica este ticker.'
        cik = str(match['cik_str']).zfill(10)
        payload = requests.get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json', headers=headers, timeout=20).json() or {}
        return (payload.get('facts') or {}).get('us-gaap') or {}, payload.get('entityName', ticker)
    except Exception as exc:
        return None, f'SEC no disponible: {exc}'

def fact(facts, names):
    for name in names:
        units = (facts.get(name) or {}).get('units') or {}
        values = next(iter(units.values()), [])
        valid = [x for x in values if x.get('form') in {'10-K', '10-Q'}]
        if valid:
            return valid[-1].get('val')
    return None

def analyze(frame):
    close = frame['Close']
    frame = frame.copy()
    frame['EMA20'] = close.ewm(span=20, adjust=False).mean()
    frame['EMA50'] = close.ewm(span=50, adjust=False).mean()
    frame['EMA200'] = close.ewm(span=200, adjust=False).mean()
    change = close.diff()
    gain = change.clip(lower=0).rolling(14).mean()
    loss = (-change.clip(upper=0)).rolling(14).mean()
    frame['RSI14'] = 100 - 100 / (1 + gain / loss.replace(0, pd.NA))
    last = frame.iloc[-1]
    if last.Close > last.EMA50 > last.EMA200: trend = 'Alcista estructural'
    elif last.Close < last.EMA50 < last.EMA200: trend = 'Bajista estructural'
    else: trend = 'Transición / rango'
    return frame, trend, last

ticker = st.text_input('Ticker', 'AAPL').strip().upper()
if st.button('Analizar', type='primary') and ticker:
    try:
        frame, resolved = prices(ticker)
        chart, trend, last = analyze(frame)
        a, b, c, d = st.columns(4)
        a.metric('Símbolo resuelto', resolved)
        b.metric('Precio', f'{last.Close:.2f}')
        c.metric('Tendencia', trend)
        d.metric('RSI 14', f'{last.RSI14:.1f}' if pd.notna(last.RSI14) else '—')
        st.subheader('Análisis técnico')
        st.line_chart(chart.set_index('Date')[['Close', 'EMA20', 'EMA50', 'EMA200']].tail(400))
        st.subheader('Análisis fundamental')
        facts, entity = sec_data(ticker)
        if not facts:
            st.info(entity)
        else:
            values = {'Ingresos': fact(facts, ['RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues']), 'Beneficio neto': fact(facts, ['NetIncomeLoss', 'ProfitLoss']), 'Flujo operativo': fact(facts, ['NetCashProvidedByUsedInOperatingActivities']), 'Caja': fact(facts, ['CashAndCashEquivalentsAtCarryingValue']), 'Deuda': fact(facts, ['LongTermDebtNoncurrent'])}
            st.caption(entity + ' · SEC EDGAR')
            st.dataframe(pd.DataFrame(values.items(), columns=['Métrica', 'Dato']), hide_index=True, use_container_width=True)
    except Exception as exc:
        st.error(f'No se pudo analizar {ticker}: {exc}')

st.caption('Datos con posible retraso. Resultado educativo; comprobar informes oficiales y riesgo antes de operar.')
