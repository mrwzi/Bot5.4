import os
import logging
import asyncio
from telegram import Bot
from telegram.error import TelegramError
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Get environment variables for Telegram
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Validate bot credentials
if not TOKEN or not CHAT_ID:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in environment variables.")

# Initialize the bot
bot = Bot(token=TOKEN)


async def send_file(file_path):
    """Sends a file to the Telegram chat."""
    try:
        # Check if the file exists and is not empty
        if not os.path.exists(file_path):
            logger.warning(f"File {file_path} does not exist.")
            return
        if os.path.getsize(file_path) == 0:
            logger.warning(f"File {file_path} is empty.")
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
    data_dir = os.getenv("DATA_DIR", "./data")

    if not os.path.exists(data_dir):
        logger.warning(f"Data directory '{data_dir}' does not exist.")
        return

    # Path to the trading summary report
    summary_file_path = os.path.join(data_dir, "trading_summary_report.txt")

    if os.path.exists(summary_file_path):
        try:
            # Send the file
            await send_file(summary_file_path)
        except Exception as e:
            logger.error(f"Error sending the file: {e}")
    else:
        logger.warning(f"Summary file {summary_file_path} does not exist.")


# Run the function asynchronously
if __name__ == "__main__":
    asyncio.run(send_data_to_telegram())
