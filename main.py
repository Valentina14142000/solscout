import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome to Solscout! Use /help to see available commands.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🔍 *Solscout Bot Help*\n\n"
        "Here are the available commands:\n"
        "/start - Start the bot and get an overview\n"
        "/help - Display this help message\n"
        "/scout <token> - Analyze a Solana token address"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

def main():
    # Replace with your actual bot token or load it via environment variables
    token = "YOUR_TELEGRAM_BOT_TOKEN"
    
    app = ApplicationBuilder().token(token).build()
    
    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    
    print("Solscout bot is polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
