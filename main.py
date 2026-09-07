import os
import re
import logging
import asyncio
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

# تنظیمات اتصال بهتر
request_config = HTTPXRequest(
    connection_pool_size=30,
    pool_timeout=30.0,
    connect_timeout=20.0,
    read_timeout=30.0
)
bot = Bot(token=BOT_TOKEN, request=request_config)

app = Flask(__name__)

# برای جلوگیری از پردازش همزمان چندباره یک فایل
processing_files = set()

def get_wallet_total(address: str) -> dict:
    totals = {'ETH': 0.0, 'BSC': 0.0}
    for net, rpc in NETWORKS.items():
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 4}))
            checksum = Web3.to_checksum_address(address.strip())
            balance = w3.eth.get_balance(checksum)
            totals[net] = float(w3.from_wei(balance, 'ether'))
        except:
            continue
    return totals

async def process_report(doc):
    file_key = f"{doc.file_id}_{doc.file_name}"
    
    if file_key in processing_files:
        logging.info(f"File {doc.file_name} is already being processed, skipping")
        return
    
    processing_files.add(file_key)
    
    try:
        logging.info(f"=== Start processing: {doc.file_name} ===")

        file = await bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        text = content.decode('utf-8', errors='ignore')

        addresses = list(set(re.findall(r"0x[a-fA-F0-9]{40}", text)))
        logging.info(f"Found {len(addresses)} addresses")

        if not addresses:
            await bot.send_message(chat_id=REPORT_CHANNEL, text=f"❌ آدرس معتبری در `{doc.file_name}` پیدا نشد.")
            return

        # محدود کردن تعداد
        addresses = addresses[:200]
        logging.info(f"Scanning {len(addresses)} addresses...")

        file_totals = {'ETH': 0.0, 'BSC': 0.0}
        rich_wallets = []

        with ThreadPoolExecutor(max_workers=5) as executor:
            loop = asyncio.get_running_loop()
            tasks = [loop.run_in_executor(executor, get_wallet_total, addr) for addr in addresses]
            results = await asyncio.gather(*tasks)

        for addr, res in zip(addresses, results):
            total = res['ETH'] + res['BSC']
            if total > 0.0001:  # فقط موجودی‌های معنی‌دار
                rich_wallets.append((addr, res))
            file_totals['ETH'] += res['ETH']
            file_totals['BSC'] += res['BSC']

        report = (
            f"📊 **گزارش فایل**\n"
            f"📄 فایل: `{doc.file_name}`\n"
            f"🔢 تعداد ولت اسکن‌شده: `{len(addresses)}`\n"
            f"──────────────────\n"
            f"**مجموع موجودی‌ها:**\n"
            f"🔹 ETH: `{file_totals['ETH']:.6f}`\n"
            f"🔹 BSC: `{file_totals['BSC']:.6f}`\n"
        )

        if rich_wallets:
            report += f"\n**ولت‌های دارای موجودی ({len(rich_wallets)} عدد):**\n"
            for addr, bal in rich_wallets[:15]:  # حداکثر ۱۵ تا نشون بده
                report += f"\n`{addr}`\n"
                if bal['ETH'] > 0:
                    report += f"   • ETH: `{bal['ETH']:.6f}`\n"
                if bal['BSC'] > 0:
                    report += f"   • BSC: `{bal['BSC']:.6f}`\n"
        else:
            report += "\nهیچ ولتی با موجودی قابل توجه پیدا نشد."

        await bot.send_message(chat_id=REPORT_CHANNEL, text=report, parse_mode='Markdown')
        logging.info("=== Report sent successfully ===")

    except Exception as e:
        logging.error(f"Error: {e}", exc_info=True)
        try:
            await bot.send_message(chat_id=REPORT_CHANNEL, text=f"❌ خطا در پردازش `{doc.file_name}`:\n`{str(e)[:150]}`")
        except:
            pass
    finally:
        processing_files.discard(file_key)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, bot)

    if update.channel_post and update.channel_post.document:
        if update.channel_post.chat.id == SOURCE_CHANNEL:
            def run():
                try:
                    asyncio.run(process_report(update.channel_post.document))
                except Exception as e:
                    logging.error(f"Thread error: {e}")
            import threading
            threading.Thread(target=run, daemon=True).start()

    return "OK"

@app.route('/')
def health():
    return "Bot is running"
