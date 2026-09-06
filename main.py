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

# ساخت اپلیکیشن تلگرام
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
        except:
            continue
    return local_totals

async def process_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post or not update.channel_post.document:
        return

    if update.channel_post.chat.id != SOURCE_CHANNEL:
        return

    doc = update.channel_post.document
    logging.info(f"Processing target file: {doc.file_name}")

    try:
        await context.bot.send_message(
            chat_id=REPORT_CHANNEL,
            text=f"📥 فایل `{doc.file_name}` دریافت شد.\n⏳ در حال جمع‌آوری موجودی کل..."
        )

        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        text = content.decode('utf-8', errors='ignore')

        addresses = list(set(re.findall(r"0x[a-fA-F0-9]{40}", text)))

        if not addresses:
            await context.bot.send_message(chat_id=REPORT_CHANNEL, text="❌ آدرس معتبری در فایل یافت نشد.")
            return

        file_totals = {net: 0.0 for net in NETWORKS}

        with ThreadPoolExecutor(max_workers=20) as executor:
            loop = asyncio.get_running_loop()
            tasks = [loop.run_in_executor(executor, get_wallet_total, addr) for addr in addresses]
            results = await asyncio.gather(*tasks)

        for res in results:
            for net in NETWORKS:
                file_totals[net] += res[net]

        report_msg = (
            f"📊 **گزارش مجموع موجودی فایل**\n"
            f"📄 فایل: `{doc.file_name}`\n"
            f"🔢 ولت‌های اسکن شده: `{len(addresses)}`\n"
            f"──────────────────\n"
        )
        for net, amount in file_totals.items():
            report_msg += f"🔹 {net}: `{amount:.6f}`\n"

        await context.bot.send_message(
            chat_id=REPORT_CHANNEL,
            text=report_msg,
            parse_mode='Markdown'
        )

    except Exception as e:
        logging.error(f"Error: {e}")
        await context.bot.send_message(chat_id=REPORT_CHANNEL, text=f"❌ خطا در پردازش: {str(e)}")

# اضافه کردن هندلر
application.add_handler(
    MessageHandler(filters.ChatType.CHANNEL & filters.Document.ALL, process_report)
)

@app.route('/webhook', methods=['POST'])
def webhook():
    """روت وب‌هوک - کاملاً سینک"""
    update = Update.de_json(request.get_json(force=True), application.bot)
    
    # اجرای پردازش async داخل یک event loop جدید
    asyncio.run(application.process_update(update))
    
    return "OK"

@app.route('/')
def health():
    return "Bot is running!"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
