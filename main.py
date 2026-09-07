import os
import re
import logging
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
    'OP': 'https://mainnet.optimism.io'
}

app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)

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

def process_report(doc):
    try:
        logging.info(f"Processing file: {doc.file_name}")

        # دانلود فایل
        file = bot.get_file(doc.file_id)
        content = file.download_as_bytearray()
        text = content.decode('utf-8', errors='ignore')

        addresses = list(set(re.findall(r"0x[a-fA-F0-9]{40}", text)))

        if not addresses:
            bot.send_message(chat_id=REPORT_CHANNEL, text="❌ آدرس معتبری در فایل یافت نشد.")
            return

        file_totals = {net: 0.0 for net in NETWORKS}

        with ThreadPoolExecutor(max_workers=15) as executor:
            results = list(executor.map(get_wallet_total, addresses))

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

        bot.send_message(chat_id=REPORT_CHANNEL, text=report_msg, parse_mode='Markdown')
        logging.info("Report sent successfully")

    except Exception as e:
        logging.error(f"Error: {e}")
        bot.send_message(chat_id=REPORT_CHANNEL, text=f"❌ خطا: {e}")

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, bot)

    if update.channel_post and update.channel_post.document:
        if update.channel_post.chat.id == SOURCE_CHANNEL:
            # پردازش در ترد جدا (تا وب‌هوک سریع جواب بده)
            import threading
            threading.Thread(target=process_report, args=(update.channel_post.document,)).start()

    return "OK"

@app.route('/')
def health_check():
    return "Bot is running"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
