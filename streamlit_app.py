import io
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title='ATLAS · Técnico + Fundamental', layout='wide')
st.title('ATLAS · Análisis técnico y fundamental')
st.caption('Precios diarios: Stooq · EE. UU. y mercados europeos · Fundamentales regulatorios: SEC EDGAR cuando aplica · Yahoo Finance no utilizado')

EUROPEAN_MARKETS = ['es', 'ch', 'de', 'fr', 'it', 'nl', 'be', 'pt', 'uk', 'ie', 'se', 'no', 'dk', 'fi', 'at', 'pl', 'cz', 'hu', 'gr', 'ro', 'hr', 'si', 'sk', 'ee', 'lt', 'lv', 'bg', 'cy', 'mt', 'is', 'lu', 'rs', 'tr']

@st.cache_data(ttl=900)
def stooq_history(symbol):
    original = symbol.strip().upper()
    # Sufijos de mercado usados por Stooq. Se prueba primero EE. UU. y después Europa.
    candidates = [original.lower()] if '.' in original else [f'{original.lower()}.us'] + [f'{original.lower()}.{market}' for market in EUROPEAN_MARKETS]
    end = date.today()
    start = end - timedelta(days=365 * 5)
    url = 'https://stooq.com/q/d/l/'
    def fetch_candidate(symbol):
        try:
            response = requests.get(url, params={'s': symbol, 'i': 'd', 'd1': start.strftime('%Y%m%d'), 'd2': end.strftime('%Y%m%d')}, timeout=5)
            response.raise_for_status()
            candidate_frame = pd.read_csv(io.StringIO(response.text))
            if not candidate_frame.empty and 'Close' in candidate_frame:
                return candidate_frame
        except (requests.RequestException, ValueError, pd.errors.ParserError):
            return pd.DataFrame()
        return pd.DataFrame()

    # Consultas paralelas: un mercado caído no bloquea todos los demás.
    frame = pd.DataFrame()
    with ThreadPoolExecutor(max_workers=min(20, len(candidates))) as pool:
        pending = [pool.submit(fetch_candidate, candidate) for candidate in candidates]
        for task in as_completed(pending):
            result = task.result()
            if not result.empty:
                frame = result
                break
    if frame.empty or 'Close' not in frame:
        # Fallback europeo: consulta todas las bolsas cuando el usuario no escribe prefijo.
        investing_markets = [original.lower().replace('.', ':')] if '.' in original else [f'{original.lower()}:{market}' for market in EUROPEAN_MARKETS]
        def fetch_investing(market):
            try:
                endpoint = f'https://api.investing.com/api/financialdata/historical/stock/{market}'
                response = requests.get(endpoint, params={'start-date': start.isoformat(), 'end-date': end.isoformat(), 'interval': 'P1D', 'time-frame': 'Daily'}, headers={'User-Agent': 'Mozilla/5.0', 'domain-id': 'www', 'Origin': 'https://www.investing.com', 'Accept': 'application/json'}, timeout=8)
                payload = response.json() or {}
                rows = payload.get('data') or []
                rows = payload.get('data') if isinstance(payload, dict) else []
                rows = rows if isinstance(rows, list) else []
                if not rows:
                    return pd.DataFrame()
                result = pd.DataFrame(rows).rename(columns={'rowDate': 'Date', 'last_open': 'Open', 'last_max': 'High', 'last_min': 'Low', 'last_close': 'Close', 'volume': 'Volume'})
                for column in ['Open', 'High', 'Low', 'Close', 'Volume']:
                    if column in result:
                        result[column] = pd.to_numeric(result[column], errors='coerce')
                return result
            except (requests.RequestException, ValueError, TypeError):
                return pd.DataFrame()
        with ThreadPoolExecutor(max_workers=min(20, len(investing_markets))) as pool:
            pending = [pool.submit(fetch_investing, market) for market in investing_markets]
            for task in as_completed(pending):
                result = task.result()
                if not result.empty and 'Close' in result:
                    frame = result
                    break

    if frame.empty or 'Close' not in frame:
        # Fallback gratuito de Nasdaq para acciones y ETF estadounidenses.
        nasdaq_url = f'https://api.nasdaq.com/api/quote/{original}/historical'
        response = requests.get(nasdaq_url, params={'assetclass': 'stocks', 'fromdate': start.isoformat(), 'todate': end.isoformat(), 'limit': 5000}, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json, text/plain, */*'}, timeout=20)
        response.raise_for_status()
        nasdaq_payload = response.json() or {}
        nasdaq_data = nasdaq_payload.get('data') or {}
        nasdaq_data = nasdaq_payload.get('data') if isinstance(nasdaq_payload, dict) else {}
        nasdaq_data = nasdaq_data if isinstance(nasdaq_data, dict) else {}
        trades_table = nasdaq_data.get('tradesTable') or {}
        trades_table = trades_table if isinstance(trades_table, dict) else {}
        rows = trades_table.get('rows') or []
        rows = rows if isinstance(rows, list) else []
        if not rows:
            raise ValueError(f'No se encontró histórico para {original} en las fuentes europeas o estadounidenses disponibles.')
        frame = pd.DataFrame(rows).rename(columns={'date': 'Date', 'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'})
        for column in ['Open', 'High', 'Low', 'Close', 'Volume']:
            if column in frame:
                frame[column] = frame[column].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False).astype(float)
    frame['Date'] = pd.to_datetime(frame['Date'])
    return frame.dropna(subset=['Open', 'High', 'Low', 'Close']).reset_index(drop=True)

@st.cache_data(ttl=21600)
def sec_facts(ticker):
    headers = {'User-Agent': 'ATLAS Fundamental Trading research@example.invalid'}
    tickers = requests.get('https://www.sec.gov/files/company_tickers.json', headers=headers, timeout=20).json() or {}
    match = next((item for item in tickers.values() if item['ticker'].upper() == ticker.upper()), None)
    if not match:
        return None, 'SEC no tiene un CIK coincidente para este ticker.'
    cik = str(match['cik_str']).zfill(10)
    payload = requests.get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json', headers=headers, timeout=20).json() or {}
    payload = payload if isinstance(payload, dict) else {}
    return (payload.get('facts') or {}).get('us-gaap', {}), payload.get('entityName', ticker)

def latest(facts, names):
    for name in names:
        units = facts.get(name, {}).get('units', {})
        values = next(iter(units.values()), [])
        valid = [x for x in values if x.get('form') in {'10-K', '10-Q'}]
        if valid:
            return valid[-1].get('val'), valid[-1].get('end')
    return None, None

def fundamental(ticker):
    facts, label = sec_facts(ticker)
    if not facts:
        return {'available': False, 'message': label}
    fields = {
        'Ingresos': ['RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues'],
        'Beneficio neto': ['NetIncomeLoss', 'ProfitLoss'],
        'Flujo operativo': ['NetCashProvidedByUsedInOperatingActivities'],
        'Capex': ['PaymentsToAcquirePropertyPlantAndEquipment'],
        'Caja': ['CashAndCashEquivalentsAtCarryingValue'],
        'Deuda': ['LongTermDebtNoncurrent', 'LongTermDebtAndFinanceLeaseObligationsCurrent'],
        'Activos': ['Assets'],
        'Pasivos': ['Liabilities'],
    }
    values, periods = {}, {}
    for label_name, names in fields.items():
        values[label_name], periods[label_name] = latest(facts, names)
    fcf = None if values['Flujo operativo'] is None or values['Capex'] is None else values['Flujo operativo'] - abs(values['Capex'])
    values['FCF calculado'] = fcf
    values['Margen FCF'] = None if not values['Ingresos'] else fcf / values['Ingresos'] if fcf is not None else None
    strengths, alerts = [], []
    if fcf is not None and fcf > 0: strengths.append('El FCF calculado es positivo.')
    if values['Caja'] is not None and values['Deuda'] is not None and values['Caja'] > values['Deuda']: strengths.append('La caja supera la deuda reportada.')
    if fcf is not None and fcf < 0: alerts.append('FCF negativo en el dato más reciente.')
    if values['Beneficio neto'] is not None and values['Beneficio neto'] < 0: alerts.append('Beneficio neto negativo.')
    if values['Caja'] is not None and values['Deuda'] is not None and values['Caja'] < values['Deuda']: alerts.append('La deuda supera la caja reportada.')
    return {'available': True, 'entity': label, 'values': values, 'periods': periods, 'strengths': strengths, 'alerts': alerts}

def technical(frame):
    close = frame['Close']
    frame = frame.copy()
    frame['EMA20'] = close.ewm(span=20, adjust=False).mean()
    frame['EMA50'] = close.ewm(span=50, adjust=False).mean()
    frame['EMA200'] = close.ewm(span=200, adjust=False).mean()
    delta = close.diff()
    gain, loss = delta.clip(lower=0).rolling(14).mean(), (-delta.clip(upper=0)).rolling(14).mean()
    frame['RSI14'] = 100 - (100 / (1 + gain / loss.replace(0, math.nan)))
    last = frame.iloc[-1]
    if last.Close > last.EMA50 > last.EMA200: trend = 'Alcista estructural'
    elif last.Close < last.EMA50 < last.EMA200: trend = 'Bajista estructural'
    else: trend = 'Transición / rango'
    score = 0
    score += 30 if trend == 'Alcista estructural' else -30 if trend == 'Bajista estructural' else 0
    score += 10 if last.EMA20 > last.EMA50 else -10
    score += 10 if last.RSI14 > 52 else -10 if last.RSI14 < 48 else 0
    return frame, {'trend': trend, 'score': score, 'price': last.Close, 'rsi': last.RSI14, 'date': last.Date}

symbol = st.text_input('Ticker', 'AAPL').strip().upper()
if st.button('Analizar', type='primary') and symbol:
    try:
        prices = stooq_history(symbol)
        chart, tech = technical(prices)
        fund = fundamental(symbol)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric('Precio', f"{tech['price']:.2f}")
        c2.metric('Tendencia', tech['trend'])
        c3.metric('Score técnico', tech['score'])
        c4.metric('RSI 14', f"{tech['rsi']:.1f}" if pd.notna(tech['rsi']) else '—')
        st.subheader('Gráfico técnico')
        st.line_chart(chart.set_index('Date')[['Close', 'EMA20', 'EMA50', 'EMA200']].tail(400))
        left, right = st.columns(2)
        with left:
            st.subheader('Fundamental')
            if not fund['available']:
                st.warning(f"Fundamental no disponible: {fund['message']}")
            else:
                st.caption(f"{fund['entity']} · SEC EDGAR · último dato por campo")
                display = {k: (f'{v:,.0f}' if k != 'Margen FCF' and v is not None else f'{v:.1%}' if v is not None else '—') for k, v in fund['values'].items()}
                st.dataframe(pd.DataFrame(display.items(), columns=['Métrica', 'Dato']), hide_index=True, use_container_width=True)
        with right:
            st.subheader('Fortalezas y alertas')
            for item in fund.get('strengths', []): st.success(item)
            for item in fund.get('alerts', []): st.warning(item)
            st.info('Las capas técnica y fundamental son independientes. No se genera una orden automática.')
    except Exception as error:
        st.error(f'No se pudo analizar {symbol}: {error}')

st.caption('Los datos pueden tener retraso. Resultado educativo; validar informes oficiales, valoración, liquidez y riesgo antes de tomar decisiones.')
