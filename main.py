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

# --- خواندن اطلاعات از متغیرهای محیطی (Environment Variables) ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SOURCE_CHANNEL = int(os.environ.get("SOURCE_CHANNEL", 0))
REPORT_CHANNEL = int(os.environ.get("REPORT_CHANNEL", 0))
RENDER_URL = os.environ.get("RENDER_URL", "https://walletmonitoring.onrender.com")

# بررسی اولیه برای اطمینان از تنظیم بودن مقادیر حیاتی
if not BOT_TOKEN or not SOURCE_CHANNEL or not REPORT_CHANNEL:
    logging.error("CRITICAL: BOT_TOKEN, SOURCE_CHANNEL or REPORT_CHANNEL is missing from environment variables!")

NETWORKS = {
    'ETH': 'https://eth.llamarpc.com',
    'BSC': 'https://bsc-dataseed.binance.org/',
    'POLYGON': 'https://polygon-rpc.com',
    'ARB': 'https://arb1.arbitrum.io/rpc',
    'OP': 'https://mainnet.optimism.io'
}

app = Quart(__name__)
tg_app = ApplicationBuilder().token(BOT_TOKEN).build()

# صف و قفل برای پردازش ترتیبی و جلوگیری از تداخل و Flood
task_queue = asyncio.Queue()
processing_lock = asyncio.Lock()

def get_wallet_total(address):
    local_totals = {net: 0.0 for net in NETWORKS}
    for net, rpc in NETWORKS.items():
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 4}))
            checksum = Web3.to_checksum_address(address.strip())
            balance = w3.eth.get_balance(checksum)
            if balance > 0:
                local_totals[net] = float(w3.from_wei(balance, 'ether'))
        except: continue
    return local_totals

async def worker():
    """این تابع صف را مدیریت می‌کند تا فایل‌ها دقیقاً یکی پس از دیگری پردازش شوند"""
    while True:
        update, context = await task_queue.get()
        async with processing_lock:
            try:
                await actual_process_report(update, context)
            except Exception as e:
                logging.error(f"Worker Error: {e}")
            finally:
                task_queue.task_done()
                # وقفه کوتاه بین پردازش‌ها برای جلوگیری از Flood تلگرام
                await asyncio.sleep(3)

async def actual_process_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post or not update.channel_post.document:
        return
    
    chat = update.channel_post.chat
    if chat.id != SOURCE_CHANNEL:
        return

    doc = update.channel_post.document
    logging.info(f"Processing target file: {doc.file_name}")

    try:
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        text = content.decode('utf-8', errors='ignore')

        addresses = list(set(re.findall(r"0x[a-fA-F0-9]{40}", text)))
        
        if not addresses:
            return

        file_totals = {net: 0.0 for net in NETWORKS}
        
        with ThreadPoolExecutor(max_workers=10) as executor:
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
        for net, amount in file_totals.items():
            report_msg += f"🔹 {net}: `{amount:.6f}`\n"
        
        await context.bot.send_message(chat_id=REPORT_CHANNEL, text=report_msg, parse_mode='Markdown')
        logging.info(f"Report for {doc.file_name} sent successfully.")

    except Exception as e:
        logging.error(f"Error in processing file: {e}")

async def process_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await task_queue.put((update, context))

@app.route('/webhook', methods=['POST'])
async def webhook():
    data = await request.get_json(force=True)
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return "OK"

@app.route('/')
async def health_check():
    return "Bot is running on Webhook mode with Quart!"

async def initialize_bot():
    tg_app.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.Document.ALL, process_report))
    
    await tg_app.initialize()
    await tg_app.start()
    
    asyncio.create_task(worker())
    
    webhook_url = f"{RENDER_URL}/webhook"
    await tg_app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
    logging.info(f"Webhook set to {webhook_url}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    loop = asyncio.get_event_loop()
    loop.run_until_complete(initialize_bot())
    app.run(host='0.0.0.0', port=port)
