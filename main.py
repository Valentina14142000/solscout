import os
import logging
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Load variables from .env file
load_dotenv()

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
        "/scout <token_address> - Analyze a Solana token via DexScreener"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def scout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if a token address was provided
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please provide a token address.\nUsage: `/scout <token_address>`",
            parse_mode="Markdown"
        )
        return

    token_address = context.args[0]
    await update.message.reply_text(f"🔍 Scouting Solana token: `{token_address}`...", parse_mode="Markdown")

    try:
        # Query DexScreener API for token pairs
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        response = requests.get(url, timeout=10)
        data = response.json()

        pairs = data.get("pairs")
        if not pairs:
            await update.message.reply_text("❌ No active trading pairs found for this token address.")
            return

        # Grab the first available pair (usually highest liquidity)
        pair = pairs[0]
        base_token = pair.get("baseToken", {})
        name = base_token.get("name", "Unknown")
        symbol = base_token.get("symbol", "UNKNOWN")
        price_usd = pair.get("priceUsd", "N/A")
        liquidity = pair.get("liquidity", {}).get("usd", 0)
        volume_24h = pair.get("volume", {}).get("h24", 0)
        dex = pair.get("dexId", "N/A")
        url_pair = pair.get("url", "")

        msg = (
            f"🚀 *Token Report: {name} ({symbol})*\n\n"
            f"💰 *Price (USD):* `${price_usd}`\n"
            f"💧 *Liquidity:* `${liquidity:,.2f}`\n"
            f"📊 *24h Volume:* `${volume_24h:,.2f}`\n"
            f"exchange: *DEX:* `{dex.upper()}`\n\n"
            f"[View on DexScreener]({url_pair})"
        )
        await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

    except Exception as e:
        logging.error(f"Error fetching token data: {e}")
        await update.message.reply_text("⚠️ An error occurred while fetching token metrics.")

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("No TELEGRAM_BOT_TOKEN found in environment variables.")
    
    app = ApplicationBuilder().token(token).build()
    
    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("scout", scout_command))
    
    print("Solscout bot is polling with /scout enabled...")
    app.run_polling()

if __name__ == "__main__":
    main()
