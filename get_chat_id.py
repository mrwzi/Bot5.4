import logging
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, CallbackContext
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Your Telegram bot token
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "7800778708:AAE7HzgfrFsr7IfEJTANAB5YmUujwPZqaxc")

# Configure logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

async def start(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id
    await update.message.reply_text(f"Your chat ID is: {chat_id}")
    logger.info(f"Chat ID: {chat_id}")

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    logger.info("Bot started. Send /start command to get your chat ID.")
    application.run_polling()

if __name__ == "__main__":
    main()
