# Wallet Monitor

Flask web service for monitoring public EVM wallet addresses.

## Features

- Add public wallet addresses
- Check ETH balance
- Store wallet reports
- Store Report date/time
- Store monitoring history
- Search wallets
- Telegram notification
- Render Web Service compatible

## Security

This application does not collect,
store or process Seed Phrases or Private Keys.

## Render

Build Command:

pip install -r requirements.txt

Start Command:

gunicorn app:app

## Environment Variables

RPC_URL

TELEGRAM_BOT_TOKEN

TELEGRAM_CHAT_ID

POLL_SECONDS=60

ENABLE_MONITOR=1
