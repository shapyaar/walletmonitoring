import os
import re
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from web3 import Web3

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SOURCE_CHANNEL = -1003533610913
REPORT_CHANNEL = -1004337084974

NETWORKS = {
    'ETH': 'https://eth.llamarpc.com',
    'BSC': 'https://bsc-dataseed.binance.org/',
    'POLYGON': 'https://polygon-rpc.com',
    'ARB': 'https://arb1.arbitrum.io/rpc',
    'OP': 'https://mainnet.optimism.io'
}

app = Flask(__name__)

# ساخت اپلیکیشن
application = Application.builder().token(BOT_TOKEN).build()

def get_wallet_total(address):
    local_totals = {net: 0.0 for net in NETWORKS}
    for net, rpc in NETWORKS.items():
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 5}))
            checksum = Web3.to_checksum_address(address.strip())
            balance = w3.eth.get_balance(checksum)
            if balance > 0:
                local_totals[net] = float(w3.from_wei(balance, 'ether'))
        except Exception:
            continue
    return local_totals

async def process_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post or not update.channel_post.document:
        return

    if update.channel_post.chat.id != SOURCE_CHANNEL:
        return

    doc = update.channel_post.document
    logging.info(f"Processing: {doc.file_name}")

    try:
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        text = content.decode('utf-8', errors='ignore')

        addresses = list(set(re.findall(r"0x[a-fA-F0-9]{40}", text)))

        if not addresses:
            await context.bot.send_message(
                chat_id=REPORT_CHANNEL,
                text=f"❌ فایل `{doc.file_name}` آدرس معتبری نداشت."
            )
            return

        file_totals = {net: 0.0 for net in NETWORKS}

        with ThreadPoolExecutor(max_workers=10) as executor:
            loop = asyncio.get_running_loop()
            tasks = [loop.run_in_executor(executor, get_wallet_total, addr) for addr in addresses]
            results = await asyncio.gather(*tasks)

        for res in results:
            for net in NETWORKS:
                file_totals[net] += res[net]

        report = (
            f"📊 **گزارش نهایی**\n"
            f"📄 فایل: `{doc.file_name}`\n"
            f"🔢 تعداد ولت: `{len(addresses)}`\n"
            f"──────────────────\n"
        )
        for net, amount in file_totals.items():
            report += f"🔹 {net}: `{amount:.6f}`\n"

        await context.bot.send_message(
            chat_id=REPORT_CHANNEL,
            text=report,
            parse_mode="Markdown"
        )
        logging.info("Report sent successfully")

    except Exception as e:
        logging.exception("Error while processing")
        await context.bot.send_message(
            chat_id=REPORT_CHANNEL,
            text=f"❌ خطا در پردازش `{doc.file_name}`:\n`{e}`"
        )

application.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.Document.ALL, process_report))

@app.route("/webhook", methods=["POST"])
async def webhook():
    """این روت async هست"""
    update = Update.de_json(request.get_json(force=True), application.bot)
    await application.process_update(update)
    return "OK"

@app.route("/")
def health():
    return "Bot is running"

async def main():
    await application.initialize()
    logging.info("Application initialized")

    # اجرای Flask با پشتیبانی از async
    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    config = Config()
    config.bind = [f"0.0.0.0:{os.environ.get('PORT', 10000)}"]
    await serve(app, config)

if __name__ == "__main__":
    asyncio.run(main())
