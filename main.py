import os
import re
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request
from telegram import Bot, Update
from web3 import Web3

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SOURCE_CHANNEL = -1003533610913
REPORT_CHANNEL = -1004337084974

NETWORKS = {
    'ETH': 'https://eth.llamarpc.com',
    'BSC': 'https://bsc-dataseed.binance.org/',
}

app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)

def get_wallet_total(address: str) -> dict:
    totals = {net: 0.0 for net in NETWORKS}
    for net, rpc in NETWORKS.items():
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 5}))
            checksum = Web3.to_checksum_address(address.strip())
            balance = w3.eth.get_balance(checksum)
            totals[net] = float(w3.from_wei(balance, 'ether'))
        except:
            continue
    return totals

async def process_report(doc):
    try:
        logging.info(f"Start processing: {doc.file_name}")

        file = await bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        text = content.decode('utf-8', errors='ignore')

        addresses = list(set(re.findall(r"0x[a-fA-F0-9]{40}", text)))
        logging.info(f"Found {len(addresses)} addresses")

        if not addresses:
            await bot.send_message(chat_id=REPORT_CHANNEL, text=f"❌ آدرس معتبری در `{doc.file_name}` پیدا نشد.")
            return

        # محدود کردن برای جلوگیری از Timeout
        if len(addresses) > 400:
            addresses = addresses[:400]
            logging.info("Limited to first 400 addresses")

        file_totals = {net: 0.0 for net in NETWORKS}
        rich_wallets = []

        with ThreadPoolExecutor(max_workers=10) as executor:
            loop = asyncio.get_running_loop()
            tasks = [loop.run_in_executor(executor, get_wallet_total, addr) for addr in addresses]
            results = await asyncio.gather(*tasks)

        for addr, res in zip(addresses, results):
            total = sum(res.values())
            if total > 0:
                rich_wallets.append((addr, res))
            for net in NETWORKS:
                file_totals[net] += res[net]

        # ساخت گزارش
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
            for addr, balances in rich_wallets:
                report += f"\n`{addr}`\n"
                if balances['ETH'] > 0:
                    report += f"   • ETH: `{balances['ETH']:.6f}`\n"
                if balances['BSC'] > 0:
                    report += f"   • BSC: `{balances['BSC']:.6f}`\n"

        await bot.send_message(chat_id=REPORT_CHANNEL, text=report, parse_mode='Markdown')
        logging.info("Report sent successfully")

    except Exception as e:
        logging.error(f"Error: {e}", exc_info=True)
        try:
            await bot.send_message(chat_id=REPORT_CHANNEL, text=f"❌ خطا: {str(e)}")
        except:
            pass

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, bot)

    if update.channel_post and update.channel_post.document:
        if update.channel_post.chat.id == SOURCE_CHANNEL:
            def run():
                asyncio.run(process_report(update.channel_post.document))
            import threading
            threading.Thread(target=run).start()

    return "OK"

@app.route('/')
def health():
    return "Bot is running"
