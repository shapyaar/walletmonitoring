import os
import re
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from aiohttp import web
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from web3 import Web3

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SOURCE_CHANNEL = int(os.environ.get("SOURCE_CHANNEL", 0))
REPORT_CHANNEL = int(os.environ.get("REPORT_CHANNEL", 0))
PORT = int(os.environ.get("PORT", 10000))

NETWORKS = {
    'ETH': 'https://eth.llamarpc.com',
    'BSC': 'https://bsc-dataseed.binance.org/',
    'POLYGON': 'https://polygon-rpc.com',
    'ARB': 'https://arb1.arbitrum.io/rpc',
    'OP': 'https://mainnet.optimism.io'
}

def get_wallet_total(address):
    local_totals = {net: 0.0 for net in NETWORKS}
    for net, rpc in NETWORKS.items():
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 3}))
            checksum = Web3.to_checksum_address(address.strip())
            balance = w3.eth.get_balance(checksum)
            if balance > 0:
                local_totals[net] = float(w3.from_wei(balance, 'ether'))
        except: 
            continue
    return local_totals

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.channel_post or not update.channel_post.document:
            return
        
        chat = update.channel_post.chat
        if chat.id != SOURCE_CHANNEL:
            return

        doc = update.channel_post.document
        logging.info(f"Processing target file: {doc.file_name}")
        
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        text = content.decode('utf-8', errors='ignore')
        
        addresses = list(set(re.findall(r"0x[a-fA-F0-9]{40}", text)))
        if not addresses:
            logging.info(f"No valid addresses found in {doc.file_name}")
            return

        file_totals = {net: 0.0 for net in NETWORKS}
        with ThreadPoolExecutor(max_workers=4) as executor:
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
        logging.error(f"Error in handle_document: {e}")

# یک سرور ساده برای پاسخ به پورت رندر و جلوگیری از ارور No open ports
async def handle_web(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_web)
    app.router.add_post("/webhook", handle_web) # برای پاسخ به درخواست‌های رندر
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Web server started on port {PORT}")

async def main():
    # راه‌اندازی سرور وب برای باز نگه داشتن پورت روی رندر
    await start_web_server()

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # پاک کردن وب‌هوک تلگرام تا تداخلی با پولینگ ایجاد نکند
    await application.bot.delete_webhook(drop_pending_updates=True)
    
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.Document.ALL, handle_document))
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    
    logging.info("Telegram Bot started polling successfully.")
    
    # زنده نگه داشتن لوپ اصلی
    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(main())
