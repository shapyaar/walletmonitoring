# Wallet Monitor

Wallet monitoring web service built with Flask.

## Features

- BSC monitoring
- Ethereum monitoring
- BNB balance monitoring
- ETH balance monitoring
- Telegram Report receiver
- Report filename storage
- Telegram message ID storage
- Telegram message date/time storage
- Report received date/time storage
- Public wallet address extraction
- Wallet monitoring history
- Telegram balance alerts
- Web dashboard
- Render Web Service support

## Security

The application only processes public EVM wallet addresses.

Seed Phrases and Private Keys are not collected,
stored or processed.

## Render

Build Command:

pip install -r requirements.txt

Start Command:

gunicorn app:app

## Environment Variables

BSC_RPC_URL

ETH_RPC_URL

TELEGRAM_BOT_TOKEN

TELEGRAM_CHAT_ID

POLL_SECONDS=60

TELEGRAM_POLL_SECONDS=5

ENABLE_MONITOR=1

ENABLE_TELEGRAM=1
