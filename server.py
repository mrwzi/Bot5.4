import logging
import os
import time
import threading
from datetime import datetime
import json

import ccxt
from dotenv import load_dotenv
from flask import Flask, request, jsonify, abort, send_from_directory
from flask_cors import CORS
from flask_httpauth import HTTPTokenAuth

auth = HTTPTokenAuth(scheme="Bearer")

# Global variables
last_update_time = time.time()
bot_status_lock = threading.Lock()

# Load environment variables
load_dotenv(".env")

# Define DATA_DIR here, then create the directory if needed
DATA_DIR = os.getenv("DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]  # Only print logs to console
)

# Initialize Flask app
app = Flask(__name__, static_folder='static')
CORS(app)

# Load API credentials
KUCOIN_API_KEY = os.getenv("KUCOIN_API_KEY")
KUCOIN_API_SECRET = os.getenv("KUCOIN_API_SECRET")
KUCOIN_API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE")

if not all([KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE]):
    logging.error("KuCoin API credentials are missing. Set them in the .env file.")
    exit(1)

# Initialize live data
live_data = {
    "price_data": {"bot_start_price": "N/A", "current_price": "N/A", "price_change": "N/A"},
    "balances": {"btc_balance": "N/A", "usdt_balance": "N/A", "total_balance": "N/A"},
    "transactions": [],
    "bot_status": "inactive"
}

def update_last_update_time():
    global last_update_time
    last_update_time = time.time()
    logging.info(f"Last update time set to: {time.ctime(last_update_time)}")

def authenticate():
    api_key = request.headers.get("KC-API-KEY")
    if api_key != KUCOIN_API_KEY:
        logging.error("Unauthorized: API key mismatch")
        abort(401, description="Unauthorized access. Invalid API Key.")

@auth.verify_token
def verify_token(token):
    return token == KUCOIN_API_KEY

@app.route("/update_bot_status", methods=["POST"])
def update_bot_status():
    authenticate()
    update_last_update_time()
    with bot_status_lock:
        live_data["bot_status"] = "active"
    return jsonify({"status": "success"}), 200

@app.route("/set_bot_status", methods=["POST"])
def set_bot_status():
    authenticate()
    data = request.json
    if "status" not in data or data["status"] not in ["active", "inactive"]:
        return jsonify({"error": "Missing or invalid status parameter"}), 400
    with bot_status_lock:
        live_data["bot_status"] = data["status"]
        if data["status"] == "active":
            update_last_update_time()
    logging.info(f"Bot status explicitly set to: {data['status']}")
    return jsonify({"status": "success", "message": f"Bot status set to {data['status']}"}), 200

@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api"):
        return jsonify({"error": "Endpoint not found"}), 404
    return app.send_static_file('index.html')

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/')
def home():
    return app.send_static_file('index.html')

# Add the missing check_connection_status function
def check_connection_status(exchange) -> str:
    if exchange is None:
        return "Disconnected"
    try:
        exchange.fetch_balance()
        return "Connected"
    except Exception as e:
        logging.error(f"API connection error: {e}")
        return "Disconnected"

def get_transactions_from_file():
    try:
        with open(os.path.join(DATA_DIR, "transaction_history.txt"), "r", encoding="utf-8") as file:
            lines = file.readlines()
        transactions = []
        for line in lines:
            parts = line.strip().split(" | ")
            if len(parts) == 6:  # Ensure the line has all expected parts
                timestamp, type_, amount_str, price_str, total_str, order_id = parts
                amount = amount_str.split(": ")[1].split()[0]  # e.g., "0.000015"
                price = price_str.split(": ")[1].split()[0]  # e.g., "88185.00"
                total_value = total_str.split(": ")[1].split()[0]  # e.g., "1.32"
                transactions.append({
                    "timestamp": timestamp,
                    "type": type_,
                    "amount": amount,
                    "price": price,
                    "total_value": total_value,
                    "order_id": order_id.split(": ")[1]  # e.g., "12345"
                })
        return transactions
    except FileNotFoundError:
        logging.info("transaction_history.txt not found, starting with empty transactions.")
        return []
    except Exception as e:
        logging.error(f"Error reading transaction file: {e}")
        return []

def initialize_exchange():
    try:
        exchange_instance = ccxt.kucoin({
            'apiKey': KUCOIN_API_KEY,
            'secret': KUCOIN_API_SECRET,
            'password': KUCOIN_API_PASSPHRASE,
        })
        logging.info("Connected to KuCoin exchange.")
        return exchange_instance
    except Exception as e:
        logging.error(f"Error connecting to KuCoin: {e}")
        return None

exchange_instance = None
connection_status = "Disconnected"

@app.route('/api/data', methods=['GET'])
def get_data():
    global exchange_instance, connection_status
    try:
        if not exchange_instance or check_connection_status(exchange_instance) != "Connected":
            exchange_instance = initialize_exchange()
        if not exchange_instance:
            connection_status = "Disconnected"
            live_data["connection_status"] = connection_status
        else:
            exchange_connection_status = "Connected" if check_connection_status(
                exchange_instance) == "Connected" else "Disconnected"
            bot_status = live_data.get("bot_status", "inactive")
            connection_status = "Connected" if exchange_connection_status == "Connected" and bot_status == "active" else "Disconnected"
            live_data["connection_status"] = connection_status

        # Convert datetime fields in live_data to strings
        if isinstance(live_data.get("timestamp"), datetime):
            live_data["timestamp"] = live_data["timestamp"].strftime('%Y-%m-%d %H:%M:%S')

        # Load transactions from file into live_data
        live_data["transactions"] = get_transactions_from_file()

        logging.info(f"Sending live data with connection status: {connection_status}")
        return jsonify({"status": "success", "data": live_data}), 200
    except Exception as e:
        logging.error(f"Error in /api/data endpoint: {str(e)}")
        connection_status = "Disconnected"
        live_data["connection_status"] = connection_status
        return jsonify({"status": "success", "data": live_data}), 200

@app.route("/update_data", methods=["POST"])
def update_data():
    authenticate()
    update_last_update_time()
    try:
        data = request.json
        if not data:
            return jsonify({"error": "Invalid JSON format"}), 400

        logging.info(f"Received data: {data}")

        with bot_status_lock:
            for key in ('price_data', 'balances', 'transactions'):
                if key in data:
                    if key == "transactions" and data[key]:
                        # Avoid duplicating transactions
                        existing_transactions = {tx['order_id'] for tx in live_data[key]}
                        new_transactions = [tx for tx in data[key] if tx['order_id'] not in existing_transactions]
                        live_data[key].extend(new_transactions)  # Append new transactions
                    elif key != "transactions":
                        live_data[key] = data[key]

            if not data.get("transactions"):
                logging.warning("Received empty or missing transactions data")

        logging.info(f"Updated live data: {live_data}")
        return jsonify({"status": "success", "updated_data": live_data}), 200
    except Exception as e:
        logging.error(f"Error updating data: {str(e)}")
        return jsonify({"error": "Failed to update data"}), 500

@app.route("/execute_trade", methods=["POST"])
def execute_trade():
    authenticate()
    try:
        data = request.json
        if not data:
            return jsonify({"error": "Invalid JSON format"}), 400
        action = data.get("action")
        amount = data.get("amount")
        price = data.get("price", "N/A")
        total_value = data.get("total_value", "N/A")
        timestamp = data.get("timestamp", "N/A")
        if action not in ["buy", "sell"] or not isinstance(amount, (int, float)) or amount <= 0:
            return jsonify({"error": "Invalid action or amount"}), 400

        # Check for sufficient funds before executing trade
        if action == "buy":
            usdt_balance = float(live_data["balances"]["usdt_balance"].replace(" USDT", ""))
            if usdt_balance < total_value:
                return jsonify({"error": "Insufficient funds for buy transaction"}), 400
        elif action == "sell":
            btc_balance = float(live_data["balances"]["btc_balance"].replace(" BTC", ""))
            if btc_balance < amount:
                return jsonify({"error": "Insufficient funds for sell transaction"}), 400

        transaction = {
            "timestamp": timestamp,
            "type": action,
            "amount": amount,
            "price": price,
            "total_value": total_value
        }
        # Write to a transaction history file
        file_path = os.path.join(DATA_DIR, "transaction_history.txt")
        log_entry = f"{timestamp} | {action} | Amount: {amount} BTC | Price: {price} USDT | Total: {total_value} USDT | Order ID: N/A\n"
        with open(file_path, "a", encoding="utf-8") as file:
            file.write(log_entry)

        with bot_status_lock:
            live_data["transactions"].append(transaction)
        logging.info(f"Transaction added: {transaction}")
        return jsonify({"status": "success", "message": f"Executed {action} of {amount} USDT"}), 200
    except Exception as e:
        logging.error(f"Error executing trade: {str(e)}")
        return jsonify({"error": "Failed to execute trade"}), 500

def check_bot_status():
    global last_update_time, live_data
    while True:
        current_time = time.time()
        if current_time - last_update_time > 10:
            with bot_status_lock:
                if live_data["bot_status"] != "inactive":
                    live_data["bot_status"] = "inactive"
                    logging.info(
                        f"Bot status set to 'inactive' due to inactivity (last update: {time.ctime(last_update_time)})")
        time.sleep(10)

threading.Thread(target=check_bot_status, daemon=True).start()

if __name__ == "__main__":
    PORT = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=PORT, debug=False)
