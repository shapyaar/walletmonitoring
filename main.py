import os
import re
import logging
import asyncio
from quart import Quart, request
from concurrent.futures import ThreadPoolExecutor
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from web3 import Web3

# تنظیمات لاگ
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- تنظیمات اصلی ---
BOT_TOKEN = '8794980895:AAG7PSNwSZiWVyxj58POCVTV9ZgPMG-LJ_U'
SOURCE_CHANNEL = -1003533610913
REPORT_CHANNEL = -1004337084974
RENDER_URL = "https://regroupmywallet.onrender.com" 

NETWORKS = {
    'ETH': 'https://eth.llamarpc.com',
    'BSC': 'https://bsc-dataseed.binance.org/',
    'POLYGON': 'https://polygon-rpc.com',
    'ARB': 'https://arb1.arbitrum.io/rpc',
    'OP': 'https://mainnet.optimism.io'
}

# استفاده از Quart به جای Flask
app = Quart(__name__)
tg_app = ApplicationBuilder().token(BOT_TOKEN).build()

def get_wallet_total(address):
    local_totals = {net: 0.0 for net in NETWORKS}
    for net, rpc in NETWORKS.items():
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 5}))
            checksum = Web3.to_checksum_address(address.strip())
            balance = w3.eth.get_balance(checksum)
            if balance > 0:
                local_totals[net] = float(w3.from_wei(balance, 'ether'))
        except: continue
    return local_totals

async def process_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post or not update.channel_post.document:
        return
    
    chat = update.channel_post.chat
    if chat.id != SOURCE_CHANNEL:
        return

    doc = update.channel_post.document
    logging.info(f"Processing target file: {doc.file_name}")

    try:
        await context.bot.send_message(chat_id=REPORT_CHANNEL, text=f"📥 فایل `{doc.file_name}` دریافت شد.\n⏳ در حال جمع‌آوری موجودی کل...")

        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        text = content.decode('utf-8', errors='ignore')

        addresses = list(set(re.findall(r"0x[a-fA-F0-9]{40}", text)))
        
        if not addresses:
            await context.bot.send_message(chat_id=REPORT_CHANNEL, text="❌ آدرس معتبری در فایل یافت نشد.")
            return

        file_totals = {net: 0.0 for net in NETWORKS}
        
        with ThreadPoolExecutor(max_workers=30) as executor:
            loop = asyncio.get_running_loop()
            tasks = [loop.run_in_executor(executor, get_wallet_total, addr) for addr in addresses]
            results = await asyncio.gather(*tasks)

        for res in results:
            for net in NETWORKS:
                file_totals[net] += res[net]

        report_msg = f"📊 **گزارش مجموع موجودی فایل**\n"
        report_msg += f"📄 فایل: `{doc.file_name}`\n"
        report_msg += f"🔢 ولت‌های اسکن شده: `{len(addresses)}`\n"
        report_msg += "──────────────────\n"
        report_msg += f"🔹 ETH: `{file_totals['ETH']:.6f}`\n"
        report_msg += f"🔹 BSC: `{file_totals['BSC']:.6f}`\n"
        report_msg += f"🔹 POLYGON: `{file_totals['POLYGON']:.6f}`\n"
        report_msg += f"🔹 ARB: `{file_totals['ARB']:.6f}`\n"
        report_msg += f"🔹 OP: `{file_totals['OP']:.6f}`\n"
        
        await context.bot.send_message(chat_id=REPORT_CHANNEL, text=report_msg, parse_mode='Markdown')

    except Exception as e:
        logging.error(f"Error: {e}")

@app.route('/webhook', methods=['POST'])
async def webhook():
    data = await request.get_json(force=True)
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return "OK"

@app.route('/')
async def health_check():
    return "Bot is running on Webhook mode with Quart!"

async def main():
    tg_app.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.Document.ALL, process_report))
    
    webhook_url = f"{RENDER_URL}/webhook"
    await tg_app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
    logging.info(f"Webhook set to {webhook_url}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    app.run(host='0.0.0.0', port=port)
