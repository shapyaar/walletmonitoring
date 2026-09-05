import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

DB_PATH = os.getenv("DB_PATH", "wallet_monitor.db")
RPC_URL = os.getenv("RPC_URL", "https://cloudflare-eth.com")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))

db_lock = threading.Lock()


def get_db():
    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    with db_lock:
        conn = get_db()

        conn.execute("""
            CREATE TABLE IF NOT EXISTS wallets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT UNIQUE NOT NULL,
                label TEXT DEFAULT '',
                source_report TEXT DEFAULT '',
                report_received_at TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                last_checked_at TEXT DEFAULT '',
                balance_wei TEXT DEFAULT '0',
                balance_eth REAL DEFAULT 0,
                has_balance INTEGER DEFAULT 0
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_id INTEGER NOT NULL,
                checked_at TEXT NOT NULL,
                balance_wei TEXT NOT NULL,
                balance_eth REAL NOT NULL,
                has_balance INTEGER NOT NULL,
                FOREIGN KEY(wallet_id)
                REFERENCES wallets(id)
            )
        """)

        conn.commit()
        conn.close()


def valid_address(address):
    return bool(
        re.fullmatch(
            r"0x[a-fA-F0-9]{40}",
            address.strip()
        )
    )


def get_balance(address):
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getBalance",
        "params": [address, "latest"],
        "id": 1
    }

    response = requests.post(
        RPC_URL,
        json=payload,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    if "error" in data:
        raise RuntimeError(
            data["error"].get(
                "message",
                "RPC error"
            )
        )

    return int(data["result"], 16)


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN:
        return

    if not TELEGRAM_CHAT_ID:
        return

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:
        requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            },
            timeout=20
        )
    except Exception as exc:
        print(
            f"Telegram error: {exc}",
            flush=True
        )


def save_check(wallet_id, balance_wei):
    balance_eth = balance_wei / 10**18
    checked_at = now()
    has_balance = 1 if balance_wei > 0 else 0

    with db_lock:
        conn = get_db()

        previous = conn.execute(
            """
            SELECT has_balance
            FROM wallets
            WHERE id=?
            """,
            (wallet_id,)
        ).fetchone()

        conn.execute(
            """
            UPDATE wallets
            SET
                last_checked_at=?,
                balance_wei=?,
                balance_eth=?,
                has_balance=?
            WHERE id=?
            """,
            (
                checked_at,
                str(balance_wei),
                balance_eth,
                has_balance,
                wallet_id
            )
        )

        conn.execute(
            """
            INSERT INTO checks
            (
                wallet_id,
                checked_at,
                balance_wei,
                balance_eth,
                has_balance
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                wallet_id,
                checked_at,
                str(balance_wei),
                balance_eth,
                has_balance
            )
        )

        wallet = conn.execute(
            """
            SELECT
                address,
                label,
                source_report,
                report_received_at
            FROM wallets
            WHERE id=?
            """,
            (wallet_id,)
        ).fetchone()

        conn.commit()
        conn.close()

    # فقط هنگام تغییر از بدون موجودی به دارای موجودی هشدار می‌دهیم
    if (
        has_balance == 1
        and (
            previous is None
            or previous["has_balance"] == 0
        )
    ):
        message = (
            "💰 موجودی کیف پول پیدا شد\n\n"
            f"Address:\n{wallet['address']}\n\n"
            f"Balance:\n{balance_eth:.18f} ETH\n\n"
            f"Report:\n"
            f"{wallet['source_report'] or '-'}\n\n"
            f"Report Time:\n"
            f"{wallet['report_received_at'] or '-'}\n\n"
            f"Checked:\n{checked_at}"
        )

        send_telegram(message)


def monitor_loop():
    print(
        "Wallet monitor started.",
        flush=True
    )

    while True:

        try:
            with db_lock:
                conn = get_db()

                wallets = conn.execute(
                    """
                    SELECT id, address
                    FROM wallets
                    """
                ).fetchall()

                conn.close()

            for wallet in wallets:

                try:
                    balance = get_balance(
                        wallet["address"]
                    )

                    save_check(
                        wallet["id"],
                        balance
                    )

                except Exception as exc:
                    print(
                        f"Check failed "
                        f"{wallet['address']}: {exc}",
                        flush=True
                    )

        except Exception as exc:
            print(
                f"Monitor error: {exc}",
                flush=True
            )

        time.sleep(POLL_SECONDS)


@app.route("/")
def home():
    return render_template(
        "index.html"
    )


@app.route("/health")
def health():
    return "OK", 200


@app.route("/api/stats")
def stats():

    with db_lock:
        conn = get_db()

        wallets = conn.execute(
            "SELECT COUNT(*) AS c FROM wallets"
        ).fetchone()["c"]

        funded = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM wallets
            WHERE has_balance=1
            """
        ).fetchone()["c"]

        checks = conn.execute(
            "SELECT COUNT(*) AS c FROM checks"
        ).fetchone()["c"]

        conn.close()

    return jsonify({
        "wallets": wallets,
        "funded": funded,
        "checks": checks
    })


@app.route("/api/wallets")
def list_wallets():

    query = request.args.get(
        "q",
        ""
    ).strip()

    with db_lock:
        conn = get_db()

        if query:

            rows = conn.execute(
                """
                SELECT *
                FROM wallets
                WHERE
                    address LIKE ?
                    OR label LIKE ?
                    OR source_report LIKE ?
                ORDER BY id DESC
                """,
                (
                    f"%{query}%",
                    f"%{query}%",
                    f"%{query}%"
                )
            ).fetchall()

        else:

            rows = conn.execute(
                """
                SELECT *
                FROM wallets
                ORDER BY id DESC
                """
            ).fetchall()

        conn.close()

    return jsonify(
        [dict(row) for row in rows]
    )


@app.route(
    "/api/wallets",
    methods=["POST"]
)
def add_wallet():

    data = request.get_json(
        silent=True
    ) or {}

    address = str(
        data.get("address", "")
    ).strip()

    label = str(
        data.get("label", "")
    ).strip()

    source_report = str(
        data.get("source_report", "")
    ).strip()

    report_received_at = str(
        data.get("report_received_at", "")
    ).strip()

    if not valid_address(address):
        return jsonify({
            "error": "آدرس کیف پول معتبر نیست"
        }), 400

    with db_lock:

        conn = get_db()

        try:

            conn.execute(
                """
                INSERT INTO wallets
                (
                    address,
                    label,
                    source_report,
                    report_received_at,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    address,
                    label,
                    source_report,
                    report_received_at,
                    now()
                )
            )

            conn.commit()

        except sqlite3.IntegrityError:

            conn.close()

            return jsonify({
                "error": "این کیف پول قبلاً ثبت شده است"
            }), 409

        conn.close()

    return jsonify({
        "ok": True
    })


if __name__ == "__main__":

    init_db()

    if os.getenv(
        "ENABLE_MONITOR",
        "1"
    ) == "1":

        threading.Thread(
            target=monitor_loop,
            daemon=True
        ).start()

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )

else:

    init_db()

    if os.getenv(
        "ENABLE_MONITOR",
        "1"
    ) == "1":

        threading.Thread(
            target=monitor_loop,
            daemon=True
        ).start()
