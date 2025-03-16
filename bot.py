import logging
import os
import re
import sys
import time
import json
from datetime import datetime
import ccxt
import requests
from dotenv import load_dotenv
from pytz import timezone as pytz_timezone
import asyncio

from telegram import Bot
from telegram.error import TelegramError  # Importing TelegramError from the telegram.error module

from telegram_bot import send_data_to_telegram

# --- Environment Configuration ---
MIN_BTC_AMOUNT = 0.00001      # Minimum BTC amount for orders
TRADE_AMOUNT_USD = 1.3        # Trade amount in USD
TRADE_PAIR = 'BTC/USDT'

# Initialize global variables
last_price = None
last_trade_time = time.time()
last_data_sent_time = 0     # Throttles data updates to the server

# Load environment variables and ensure DATA_DIR exists
load_dotenv()

DATA_DIR = os.getenv("DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Check if environment variables are loaded correctly
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Debugging: Print to confirm the values are loaded
print(f"Telegram Bot Token: {TOKEN}")
print(f"Telegram Chat ID: {CHAT_ID}")

# Telegram Bot Setup
if not TOKEN or not CHAT_ID:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in environment variables.")

# Initialize the bot
bot = Bot(token=TOKEN)


###########################################################################
# --- Async Telegram Functions ---
async def send_file(file_path):
    """Sends a file to the Telegram chat."""
    try:
        # Check if the file exists and is not empty
        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            logger.warning(f"File {file_path} does not exist or is empty.")
            return

        # Send the file asynchronously
        with open(file_path, 'rb') as file:
            await bot.send_document(chat_id=CHAT_ID, document=file)

        logger.info(f"File {file_path} sent successfully.")
    except TelegramError as e:
        logger.error(f"Failed to send file: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")

async def send_data_to_telegram():
    """Sends the trading summary report to Telegram."""
    summary_file_path = os.path.join(DATA_DIR, "trading_summary_report.txt")
    if os.path.exists(summary_file_path) and os.path.getsize(summary_file_path) > 0:
        try:
            await send_file(summary_file_path)
        except Exception as e:
            logger.error(f"Error reading or sending the file: {e}")
    else:
        logger.warning(f"Summary file {summary_file_path} is empty or does not exist.")

async def loop_send_summary():
    """Loops to send trading summary at regular intervals (4 hours)."""
    while True:
        try:
            await send_data_to_telegram()  # Send the trading summary report
            await asyncio.sleep(14400)  # Sleep for 4 hours (14400 seconds) before the next iteration
        except Exception as e:
            logger.error(f"Error in loop: {e}")
            await asyncio.sleep(30)  # Retry every 30 seconds in case of error
###########################################################################


# --- Load Environment Configuration ---
def load_environment():
    env = os.environ.get('ENVIRONMENT', 'LOCAL').upper()
    env_file = '.env.server' if env == 'SERVER' else '.env.local'
    default_data_dir = '/home/ubuntu/bot5/data' if env == 'SERVER' else 'data'
    try:
        load_dotenv(env_file)
        logging.info(f"Loaded {env_file}")
    except Exception as e:
        logging.warning(f"Could not load {env_file}: {e}")
    global DATA_DIR, KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE, SERVER_PORT
    DATA_DIR = os.getenv('DATA_DIR', default_data_dir)
    os.makedirs(DATA_DIR, exist_ok=True)
    KUCOIN_API_KEY = os.getenv("KUCOIN_API_KEY")
    KUCOIN_API_SECRET = os.getenv("KUCOIN_API_SECRET")
    KUCOIN_API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE")
    SERVER_PORT = os.getenv("SERVER_PORT", "5000")
    if not all([KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE]):
        logging.error("Missing KuCoin API credentials. Exiting.")
        sys.exit(1)
    logging.info(f"Env: {env}, DATA_DIR={DATA_DIR}, PORT={SERVER_PORT}")

load_environment()


# --- Example BTC Balance Extraction ---
if (match := re.search(r"BTC Balance:\s*([\d]+(?:\.\d+)?)\s*BTC", "BTC Balance: 0.12345678 BTC\n")):
    logging.info(f"Extracted BTC Balance: {match.group(1)}")
else:
    logging.warning("BTC Balance not found")


def check_connection_status(exchange) -> str:
    if exchange is None:
        return "Disconnected"
    try:
        exchange.fetch_balance()  # Example API call
        return "Connected"
    except Exception as e:
        logging.error(f"API connection error: {e}")
        return "Disconnected"



# --- Utility Functions ---
def get_public_ip():
    try:
        response = requests.get('http://127.0.0.1:4040/api/tunnels', timeout=2)  # Add timeout to avoid long waiting
        response.raise_for_status()
        tunnels = response.json().get('tunnels', [])
        for tunnel in tunnels:
            if tunnel.get('public_url'):
                return tunnel['public_url']
    except requests.RequestException:
        logging.debug("Ngrok is not running, using local IP instead.")  # Changed from ERROR to DEBUG

    # Fallback to local IP if Ngrok is not running
    env = os.environ.get('ENVIRONMENT', 'LOCAL').upper()
    if env == 'SERVER':
        ip = os.getenv('SERVER_IP')
        if ip:
            logging.info(f"Using SERVER_IP: {ip}")
            return ip
        try:
            r = requests.get('https://api.ipify.org', timeout=5)
            r.raise_for_status()
            ip = r.text.strip()
            logging.info(f"Fetched public IP: {ip}")
            return ip
        except Exception as e:
            logging.error(f"Public IP fetch failed: {e}")
            return None
    logging.info("Running locally, using 127.0.0.1")
    return "127.0.0.1"



def serialize_datetime(v):
    return v.isoformat() if isinstance(v, datetime) else v



def send_data_to_server(price_data, balances, transactions, retries=3, timeout=10):
    ip = get_public_ip()  # Will return Ngrok URL if running Ngrok
    if not ip:
        logging.error("No public IP available.")
        return False, None, "No public IP"
    url = f"http://{ip}:{SERVER_PORT}/update_data"  # Use Ngrok URL if available
    data = {
        "price_data": {k: serialize_datetime(v) for k, v in price_data.items()},
        "balances": {k: serialize_datetime(v) for k, v in balances.items()},
        "transactions": [{k: serialize_datetime(v) for k, v in tx.items()} for tx in transactions]
    }
    headers = {'KC-API-KEY': KUCOIN_API_KEY, 'Content-Type': 'application/json'}
    for attempt in range(retries):
        try:
            resp = requests.post(url, json=data, headers=headers, timeout=timeout)
            resp.raise_for_status()
            logging.info(f"Data sent: {resp.status_code} {resp.text}")
            return True, resp.status_code, resp.text
        except requests.exceptions.Timeout:
            logging.error("Request timed out while sending data to server.")
        except requests.exceptions.ConnectionError:
            logging.error("Network issue! Could not connect to server.")
        except requests.exceptions.HTTPError as http_err:
            logging.error(f"HTTP error occurred: {http_err}")
        except Exception as e:
            logging.error(f"Unexpected error: {e}")
        time.sleep(min(5 * (attempt + 1), 30))  # Exponential backoff for retries
    return False, None, "Max retries reached"



def get_transactions_from_file():
    file_path = os.path.join(DATA_DIR, "transaction_history.txt")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # Only keep the last 20 transactions
        txs = []
        for line in lines[-20:]:
            parts = line.strip().split(" | ")
            if len(parts) == 6:
                ts, t_type, amt_str, price_str, total_str, order_str = parts
                amt = amt_str.split(": ")[1].split()[0]
                price = price_str.split(": ")[1].split()[0]
                total = total_str.split(": ")[1].split()[0]
                order_id = order_str.split(": ")[1]
                txs.append({"timestamp": ts, "type": t_type, "amount": amt,
                            "price": price, "total_value": total, "order_id": order_id})
        return txs
    except FileNotFoundError:
        logging.info("transaction_history.txt not found, starting empty.")
        return []
    except Exception as e:
        logging.error(f"Error reading transactions: {e}")
        return []

# --- Exchange Connection ---
def connect_to_exchange():
    """Connect to KuCoin Exchange"""
    try:
        exchange = ccxt.kucoin({
            'apiKey': os.getenv("KUCOIN_API_KEY"),
            'secret': os.getenv("KUCOIN_API_SECRET"),
            'password': os.getenv("KUCOIN_API_PASSPHRASE"),
        })
        exchange.load_markets()
        return exchange
    except Exception as e:
        logger.error(f"KuCoin connection error: {e}")
        return None

def check_api_connection(exchange):
    try:
        exchange.fetch_balance()
        logging.debug("API Connection Successful")  # Changed from INFO to DEBUG to reduce log spam
        return "Connected"
    except Exception as e:
        logging.error(f"API connection error: {e}")
        return "Disconnected"


# --- Margin Balance ---
def get_margin_balance(exchange):
    if exchange is None:
        logging.error("Exchange is not connected. Cannot fetch margin balance.")
        return None, None
    try:
        balance = exchange.fetch_balance({'type': 'margin'})
        btc_balance = float(balance.get('BTC', {}).get('free', 0))
        usdt_balance = float(balance.get('USDT', {}).get('free', 0))
        logging.info(f"[BALANCE] BTC: {btc_balance:.8f} BTC | USDT: {usdt_balance:.2f} USDT")
        return btc_balance, usdt_balance
    except Exception as e:
        logging.error(f"Error getting margin balance: {e}")
        return None, None


# --- Trading Functions ---
def get_current_price(exchange):
    try:
        return exchange.fetch_ticker(TRADE_PAIR)['last']
    except Exception as e:
        logging.error(f"Current price error: {e}")
        return None

def rotate_logs(max_lines=500):
    file_path = os.path.join(DATA_DIR, "transaction_history.txt")
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > max_lines:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.writelines(lines[-max_lines:])  # Only keep the most recent 500 lines
        except Exception as e:
            logging.error(f"Rotate logs error: {e}")

def log_message(message, level="info"):
    rotate_logs()
    ts = datetime.now(pytz_timezone('UTC')).strftime('%Y-%m-%d %H:%M:%S')
    entry = f"{ts} | {message}"
    getattr(logging, level)(entry)
    print(entry)



def log_transaction(t_type, amount, price, total, order_id="N/A"):
    try:
        ts = datetime.now(pytz_timezone('UTC')).strftime('%Y-%m-%d %H:%M:%S')
        amount_str = f"{float(amount):.8f}"
        price_str = f"{float(price):.2f}"
        total_str = f"{float(total)::.2f}"
        entry = f"{ts} | {t_type} | Amount: {amount_str} BTC | Price: {price_str} USDT | Total: {total_str} USDT | Order ID: {order_id}\n"
        with open(os.path.join(DATA_DIR, "transaction_history.txt"), 'a', encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        logging.error(f"Logging transaction error: {e}")

def can_trade():
    global last_trade_time
    return (time.time() - last_trade_time) >= 5

def reset_last_trade_time():
    global last_trade_time
    last_trade_time = time.time()




def create_market_order(exchange, order_type, amount_usd=None, amount_btc=None):
    """Create a market order on KuCoin"""
    global last_price
    price = fetch_with_retry(lambda: get_current_price(exchange))

    if not price or price == 0:
        logger.warning("Failed to get valid price, aborting trade.")
        return None

    try:
        if order_type == "buy":
            # Calculate BTC amount based on USD
            btc_amt = amount_usd / price
            # Round to 8 decimal places (KuCoin precision for BTC)
            btc_amt = round(btc_amt, 8)

            if btc_amt < MIN_BTC_AMOUNT:
                logger.warning(f"Buy amount {btc_amt:.8f} BTC is below the minimum {MIN_BTC_AMOUNT} BTC")
                return None

            # Create the buy order
            order = exchange.create_market_buy_order(TRADE_PAIR, btc_amt, params={'marginMode': 'cross'})
        elif order_type == "sell":
            if amount_btc < MIN_BTC_AMOUNT:
                logger.warning(f"Sell amount {amount_btc} BTC is below the minimum {MIN_BTC_AMOUNT} BTC")
                return None

            # Round to 8 decimal places (KuCoin precision for BTC)
            amount_btc = round(amount_btc, 8)

            # Create the sell order
            order = exchange.create_market_sell_order(TRADE_PAIR, amount_btc, params={'marginMode': 'cross'})
        else:
            logger.warning(f"Invalid order type: {order_type}")
            return None

        time.sleep(1)  # Allow exchange to process the order

        # Fetch order details to confirm execution
        order_details = exchange.fetch_order(order['id'], TRADE_PAIR)
        filled = float(order_details.get('filled', 0))
        actual_price = float(order_details.get('price', price))
        total_value = filled * actual_price

        # Log with actual execution price and filled amount
        if filled > 0:
            logger.info(f"{order_type.upper()} executed: {filled:.8f} BTC at {actual_price:.2f} USDT")
            log_transaction(order_type.upper(), filled, actual_price, total_value, order.get('id', "N/A"))
        else:
            logger.warning(f"{order_type.upper()} order might be pending or partially filled.")
            log_transaction(f"PARTIAL {order_type.upper()}", filled, actual_price, total_value, order.get('id', "N/A"))

        reset_last_trade_time()  # Reset the trade cooldown
        return order

    except Exception as e:
        logger.error(f"{order_type.capitalize()} order error: {e}")
        log_transaction(f"FAILED {order_type.upper()}", 0, price, 0, f"API error: {e}")
        reset_last_trade_time()  # Reset the trade cooldown after failure
        return None





def reset_price_change_logic():
    global last_price
    last_price = None  # Reset the base price so the bot can re-evaluate the market
    log_message("Price change logic reset due to insufficient balance or failed trade.")




def check_price_change(exchange):
    global last_price, last_data_sent_time

    # Check if the exchange is connected
    if not exchange or check_api_connection(exchange) == "Disconnected":
        send_data_to_server({}, {}, [])  # Sending empty data as API is disconnected
        log_message("API disconnected.", "error")
        return

    # Retrieve the balance of BTC and USDT
    btc_balance, usdt_balance = get_margin_balance(exchange)
    if btc_balance is None or usdt_balance is None:
        log_message("Balance retrieval error.", "error")
        return

    # Fetch the current price
    current_price = get_current_price(exchange)
    if not current_price or current_price == 0:
        log_message("Current price retrieval failed. Check API connection.", "error")
        return

    # Initialize base price if it hasn't been set already
    if last_price is None:
        last_price = current_price
        log_message(f"Base price set to {current_price:.2f}")
        return

    # Set price slippage tolerance (can be adjusted to handle larger/smaller fluctuations)
    price_tolerance = 0.5  # Tolerance set to 0.5% for price fluctuations

    # Calculate price difference in percentage
    price_diff = abs(current_price - last_price) / last_price * 100
    if price_diff > price_tolerance:
        log_transaction("FAILED PRICE CHANGE", 0, current_price, 0, f"Price change exceeded tolerance: {price_diff:.2f}%")
        log_message(f"Price slippage exceeded tolerance: {price_diff:.2f}%", "warning")
        time.sleep(5)  # Retry after a brief delay
        return

    # Calculate price change in percentage
    change = ((current_price - last_price) / last_price) * 100

    # Throttle server updates to once every 5 seconds
    if time.time() - last_data_sent_time >= 5:
        total_balance = btc_balance * current_price + usdt_balance
        data = {
            "bot_start_price": f"{last_price:.2f}",
            "current_price": f"{current_price:.2f}",
            "price_change": f"{change:.2f}%"
        }
        balances = {
            "btc_balance": f"{btc_balance * current_price:.2f} USDT",
            "usdt_balance": f"{usdt_balance:.2f} USDT",
            "total_balance": f"{total_balance:.2f} USDT"
        }
        txs = get_transactions_from_file()  # Fetch transaction history
        success, _, _ = send_data_to_server(data, balances, txs)
        if success:
            last_sent_data = f"{balances['btc_balance']} | {balances['usdt_balance']} | {current_price:.2f}"
            if last_sent_data != getattr(check_price_change, "last_sent_data", None):
                log_message(f"Data updated: BTC {balances['btc_balance']}, USDT {balances['usdt_balance']}, Price {current_price:.2f}")
                check_price_change.last_sent_data = last_sent_data
                rotate_logs() 
        last_data_sent_time = time.time()  # Update last data sent time

    # Only proceed if the cooldown has passed
    if not can_trade():
        log_message(f"Cooldown active. Last trade at {time.strftime('%H:%M:%S', time.localtime(last_trade_time))}.", "info")
        return

    # Log the current price change for debugging purposes
    if abs(change) >= 0.01:
        log_message(f"Price change: {change:.2f}% (Current: {current_price:.2f}, Base: {last_price:.2f})", "info")

    # Trading conditions:
    # SELL when the price increases by at least 0.1%,
    # BUY when it drops by at least 0.05%
    if change >= 0.1:
        if btc_balance >= (TRADE_AMOUNT_USD / current_price):
            log_message(f"SELL triggered for {TRADE_AMOUNT_USD} USDT")
            if create_market_order(exchange, "sell", amount_btc=TRADE_AMOUNT_USD / current_price):
                last_price = current_price  # Update base price only after successful trade
        else:
            log_transaction("FAILED SELL", 0, current_price, 0,
                            f"Insufficient BTC. Required: {TRADE_AMOUNT_USD / current_price:.8f} BTC, Available: {btc_balance:.8f} BTC")
            last_price = current_price  # Reset base price after failed trade
            time.sleep(10)  # Add cooldown after failed trade
    elif change <= -0.05:
        if usdt_balance >= TRADE_AMOUNT_USD:
            log_message(f"BUY triggered for {TRADE_AMOUNT_USD} USDT")
            if create_market_order(exchange, "buy", amount_usd=TRADE_AMOUNT_USD):
                last_price = current_price  # Update base price only after successful trade
        else:
            log_transaction("FAILED BUY", 0, current_price, 0,
                            f"Insufficient USDT. Required: {TRADE_AMOUNT_USD} USDT, Available: {usdt_balance:.2f} USDT")
            last_price = current_price  # Reset base price after failed trade
            time.sleep(10)  # Add cooldown after failed trade

    # Log the base price update only after a successful trade
    log_message(f"Base price remains at {last_price:.2f} after trade attempt.", "info")




def fetch_with_retry(func, retries=3, delay=2):
    """Retry fetching exchange data with exponential backoff"""
    for attempt in range(retries):
        try:
            result = func()
            if result:
                return result
        except Exception as e:
            logger.warning(f"Retry {attempt + 1}/{retries}: {e}")
            time.sleep(delay * (2 ** attempt))  # Exponential backoff
    return None


# In the run function
def run():
    global last_price

    ip = get_public_ip()
    exchange = connect_to_exchange()
    if not exchange:
        logger.error("Failed to connect to exchange. Exiting.")
        return

    # Initialize base price
    last_price = fetch_with_retry(lambda: get_current_price(exchange))
    if not last_price:
        print("Failed to get initial price. Exiting.")
        sys.exit(1)

    last_heartbeat = time.time()
    try:
        while True:
            if check_api_connection(exchange) == "Connected":
                logging.info("Monitoring price change...")
                check_price_change(exchange)
            else:
                log_message("No connection, retrying...", "warning")
                time.sleep(1)

            if time.time() - last_heartbeat >= 10:
                try:
                    r = requests.post(f"http://{ip or '127.0.0.1'}:{SERVER_PORT}/update_bot_status",
                                      headers={'KC-API-KEY': KUCOIN_API_KEY})
                    if r and hasattr(r, 'status_code') and r.status_code == 200:
                        logging.info("Bot status updated.")
                    else:
                        logging.error(f"Status update failed: {r.text if r else 'No response'}")
                    last_heartbeat = time.time()
                except Exception as e:
                    logging.error(f"Status update error: {e}")

            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Stopping bot and setting status to inactive...")
        try:
            r = requests.post(f"http://{ip or '127.0.0.1'}:{SERVER_PORT}/update_bot_status",
                              json={"status": "inactive"}, headers={'KC-API-KEY': KUCOIN_API_KEY})
            if r and hasattr(r, 'status_code') and r.status_code == 200:
                logging.info("Bot status set to inactive.")
            else:
                logging.error(f"Status inactive update failed: {r.text if r else 'No response'}")
        except Exception as e:
            logging.error(f"Shutdown error: {e}")

        final_btc, final_usdt = get_margin_balance(exchange)
        final_btc = final_btc if final_btc is not None else 0
        final_usdt = final_usdt if final_usdt is not None else 0
        current_price = fetch_with_retry(lambda: get_current_price(exchange))  # Fetch the current price
        update_information_file(final_btc, final_usdt, current_price)  # Include current_price
        generate_final_trading_summary()
        sys.exit(0)
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        time.sleep(1)



# --- Trading Summary Report Functions ---
def generate_final_trading_summary():
    """Reads information.txt and generates a trading summary report."""
    info_file = os.path.join(DATA_DIR, "information.txt")
    report_file = os.path.join(DATA_DIR, "trading_summary_report.txt")

    # Check if the information file exists and is not empty
    if not os.path.exists(info_file) or os.path.getsize(info_file) == 0:
        logging.error(f"{info_file} is empty or does not exist! Cannot generate report.")
        return

    try:
        # Open and read the information file
        with open(info_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Ensure necessary fields are present in the data
        start_time = data.get("bot_start_time", "Unknown")
        end_time = data.get("bot_end_time", "Unknown")
        if start_time == "Unknown" or end_time == "Unknown":
            logging.error("Missing start or end time in information.txt, cannot generate report.")
            return

        # Parse start_time and end_time into datetime objects
        try:
            start_dt = datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')
            end_dt = datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S')
            duration = end_dt - start_dt
        except ValueError as e:
            logging.error(f"Error parsing time: {e}")
            return

        # Get Prices from the data
        start_price = float(data.get("bot_start_price", 0))
        end_price = float(data.get("bot_end_price", start_price))  # Default to start_price if end_price is missing

        # Get Balances
        initial_btc = float(data.get("initial_btc", 0))
        initial_usdt = float(data.get("initial_usdt", 0))
        final_btc = float(data.get("final_btc", 0))
        final_usdt = float(data.get("final_usdt", 0))

        # Calculate Total Initial and Final Balances
        total_initial = (initial_btc * start_price) + initial_usdt
        total_final = (final_btc * end_price) + final_usdt

        # Calculate Profit & Loss
        profit_total = total_final - total_initial
        profit_btc = final_btc - initial_btc

        # USDT movement (change)
        usdt_change = final_usdt - initial_usdt

        # Create the report as a formatted string
        report = f"""
-------------------------------------------------------------
                  TRADING BOT SUMMARY REPORT
-------------------------------------------------------------
📅 **Bot Start Time:**    {start_time}
📅 **Bot End Time:**      {end_time}
⏳ **Total Duration:**    {duration}
-------------------------------------------------------------
                      BALANCE SUMMARY
-------------------------------------------------------------
🔹 **Starting Balances:**
   - 🟢 BTC Balance (Initial):  {initial_btc:.8f} BTC
   - 💵 USDT Balance (Initial): {initial_usdt:.2f} USDT
   - 💰 **Total Initial Value:** {total_initial:.2f} USDT

🔹 **Ending Balances:**
   - 🟢 BTC Balance (Final):  {final_btc:.8f} BTC
   - 💵 USDT Balance (Final): {final_usdt:.2f} USDT
   - 💰 **Total Final Value:** {total_final:.2f} USDT

🔹 **Profit & Loss:**
   - 📉 **Total Profit/Loss:** {profit_total:.2f} USDT
   - 📉 **BTC Profit/Loss:** {profit_btc:.8f} BTC

🔹 **USDT Movement:**
   - 🔄 **USDT Change:** {usdt_change:.2f} USDT
-------------------------------------------------------------
"""
        # Save the generated report to a file
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report)
        logging.info("✅ Trading summary report saved to trading_summary_report.txt")

    except json.JSONDecodeError as e:
        logging.error(f"Error reading {info_file}: {e}. The file may be corrupted.")
    except Exception as e:
        logging.error(f"Unexpected error generating report: {e}")




def initialize_information_file():
    """Ensure information.txt exists and initialize required fields with initial balances."""
    info_file = os.path.join(DATA_DIR, "information.txt")

    if not os.path.exists(info_file) or os.path.getsize(info_file) == 0:
        logging.info(f"Initializing {info_file} with initial balances.")

        exchange = connect_to_exchange()
        if exchange is None:
            logging.error("Failed to connect to exchange during initialization.")
            sys.exit(1)

        btc_balance, usdt_balance = get_margin_balance(exchange)
        btc_balance = btc_balance if btc_balance is not None else 0
        usdt_balance = usdt_balance if usdt_balance is not None else 0

        # Ensure we get a valid BTC price
        current_price = fetch_with_retry(lambda: get_current_price(exchange))
        if current_price is None or current_price == 0:
            logging.error("Could not fetch a valid BTC price. Exiting.")
            sys.exit(1)

        # Initialize the file with these balances
        data = {
            "bot_start_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "bot_start_price": current_price,
            "initial_btc": btc_balance,
            "initial_usdt": usdt_balance,
            "final_btc": None,
            "final_usdt": None,
            "bot_end_price": None,
            "profit_btc": 0,
            "profit_total": 0,
            "usdt_change": 0,
            "total_initial": (btc_balance * current_price) + usdt_balance,
            "total_final": 0,
            "total_trades": 0
        }

        with open(info_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

        logging.info(f"information.txt initialized with BTC: {btc_balance}, USDT: {usdt_balance}, Start Price: {current_price}")

    else:
        logging.info(f"{info_file} exists and is not empty.")



def update_information_file(final_btc, final_usdt, current_price):
    """Update final balance details when the bot stops."""
    info_file = os.path.join(DATA_DIR, "information.txt")

    if not os.path.exists(info_file) or os.path.getsize(info_file) == 0:
        logging.error(f"{info_file} is empty or does not exist! Cannot update information.")
        return

    try:
        with open(info_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Ensure we have a start price
        start_price = float(data.get("bot_start_price", 0))
        if start_price == 0:
            logging.warning("Start price is missing, using latest price instead.")
            start_price = current_price

        # Store end price
        data["bot_end_price"] = current_price

        # Ensure final balances are updated
        initial_btc = float(data.get("initial_btc", 0))
        initial_usdt = float(data.get("initial_usdt", 0))

        # Calculate total balances (BTC converted to USDT)
        total_initial = (initial_btc * start_price) + initial_usdt
        total_final = (final_btc * current_price) + final_usdt

        # Store new balances
        data["final_btc"] = final_btc
        data["final_usdt"] = final_usdt
        data["bot_end_time"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Calculate profit in BTC and total USD equivalent
        data["profit_btc"] = final_btc - initial_btc
        data["profit_total"] = total_final - total_initial
        data["usdt_change"] = final_usdt - initial_usdt
        data["total_initial"] = total_initial
        data["total_final"] = total_final

        # Save back to information.txt
        with open(info_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

        logging.info(f"information.txt updated with final balances: BTC: {final_btc}, USDT: {final_usdt}, Total: {total_final} USDT")

    except json.JSONDecodeError as e:
        logging.error(f"Error reading {info_file}: {e}. The file may be corrupted.")
    except Exception as e:
        logging.error(f"Unexpected error updating {info_file}: {e}")



# In the stop_bot function
def stop_bot():
    """Stops the bot, updates final balances, and generates a report."""
    global last_price, exchange  # Add global exchange

    if exchange is None:
        logging.error("Exchange is not connected. Cannot fetch balances.")
        return

    # Get final balances
    final_btc, final_usdt = get_margin_balance(exchange)
    final_btc = final_btc if final_btc is not None else 0
    final_usdt = final_usdt if final_usdt is not None else 0

    # Fetch the latest BTC price with a retry loop
    current_price = fetch_with_retry(lambda: get_current_price(exchange))

    # If still no valid price, fallback to stored start price
    if current_price is None or current_price <= 0:
        logging.error("Could not fetch a valid BTC price. Using last known price.")
        info_file = os.path.join(DATA_DIR, "information.txt")
        if os.path.exists(info_file):
            with open(info_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            current_price = float(data.get("bot_start_price", 0))  # Fallback to start price
        else:
            logging.warning("information.txt not found. Using default price: 0")
            current_price = 0  # Safe fallback

    # Now update the information.txt with final values
    update_information_file(final_btc, final_usdt, current_price)  # Include current_price

    # Generate a summary report
    generate_final_trading_summary()

    # Log bot shutdown
    logging.info("Bot has stopped successfully.")

    # Exit the bot
    sys.exit(0)

def clear_files():
    """Clear the contents of information.txt, trading_summary_report.txt, and transaction_history.txt."""
    files_to_clear = ["information.txt", "trading_summary_report.txt", "transaction_history.txt"]
    for filename in files_to_clear:
        file_path = os.path.join(DATA_DIR, filename)
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("")
            logging.info(f"Cleared contents of {filename}")
        except Exception as e:
            logging.error(f"Error clearing {filename}: {e}")

####################################################################
async def generate_and_send_summary():
    """Generate the trading summary and send it to Telegram"""
    try:
        await asyncio.sleep(30)
        await send_data_to_telegram()
    except Exception as e:
        logger.error(f"Error generating summary: {e}")


async def generate_and_send_summary_with_delay():
    """Generate and send the trading summary every 4 hours."""
    while True:
        logging.info("Trading will continue for 4 hours before generating summary...")
        await asyncio.sleep(14400)  # Wait 4 hours before generating the summary

        logging.info("Generating trading summary report...")
        generate_final_trading_summary()  # ✅ Generate the report

        logging.info("Sending trading summary to Telegram...")
        await send_data_to_telegram()  # ✅ Send the report

        logging.info("Summary sent. Continuing trading...")


# --- Bot Execution ---
async def main():
    exchange = connect_to_exchange()
    if not exchange:
        logger.error("Failed to connect to exchange. Exiting.")
        return  # ✅ Clean exit without breaking async loop

    logging.info("Starting trading bot...")

    # Run trading & summary functions in parallel
    await asyncio.gather(
        asyncio.to_thread(run),  # Run the trading bot
        generate_and_send_summary_with_delay()  # Repeatedly generate and send summary every 5 min
    )
####################################################################

# --- Main Execution ---
if __name__ == '__main__':
    logging.info("Starting bot...")

    # Initialize exchange connection and file clearing
    exchange = connect_to_exchange()

    if exchange:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Run both the trading bot and the summary generator (5-min delay) concurrently
        try:
            loop.run_until_complete(asyncio.gather(
                asyncio.to_thread(run),  # Run the trading bot in a separate thread
                generate_and_send_summary_with_delay()  # 5 min delay summary generation
            ))

        except Exception as e:
            logging.error(f"Error running the bot: {e}")
    else:
        logging.error("Failed to connect to exchange.")
        sys.exit(1)