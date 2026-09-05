import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template, request


app = Flask(__name__)

# =========================================================
# SETTINGS
# =========================================================

DB_PATH = os.getenv(
    "DB_PATH",
    "wallet_monitor.db"
)

POLL_SECONDS = int(
    os.getenv("POLL_SECONDS", "60")
)

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)

TELEGRAM_POLL_SECONDS = int(
    os.getenv("TELEGRAM_POLL_SECONDS", "5")
)

# BSC Mainnet
BSC_RPC_URL = os.getenv(
    "BSC_RPC_URL",
    "https://bsc-dataseed.binance.org"
)

# Ethereum Mainnet
ETH_RPC_URL = os.getenv(
    "ETH_RPC_URL",
    "https://cloudflare-eth.com"
)

db_lock = threading.Lock()


# =========================================================
# DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    return conn


def now_utc():
    return datetime.now(
        timezone.utc
    ).isoformat()


def init_db():

    with db_lock:

        conn = get_db()

        conn.execute("""
            CREATE TABLE IF NOT EXISTS wallets (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                address TEXT NOT NULL,

                network TEXT NOT NULL,

                label TEXT DEFAULT '',

                source_report TEXT DEFAULT '',

                report_received_at TEXT DEFAULT '',

                telegram_message_id TEXT DEFAULT '',

                created_at TEXT NOT NULL,

                last_checked_at TEXT DEFAULT '',

                balance_raw TEXT DEFAULT '0',

                balance REAL DEFAULT 0,

                has_balance INTEGER DEFAULT 0,

                UNIQUE(address, network)

            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS checks (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                wallet_id INTEGER NOT NULL,

                network TEXT NOT NULL,

                checked_at TEXT NOT NULL,

                balance_raw TEXT NOT NULL,

                balance REAL NOT NULL,

                has_balance INTEGER NOT NULL,

                FOREIGN KEY(wallet_id)
                REFERENCES wallets(id)

            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS reports (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                telegram_message_id TEXT,

                file_name TEXT NOT NULL,

                telegram_date TEXT NOT NULL,

                received_at TEXT NOT NULL,

                wallet_count INTEGER DEFAULT 0

            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS telegram_state (

                id INTEGER PRIMARY KEY CHECK(id = 1),

                last_update_id INTEGER DEFAULT 0

            )
        """)

        conn.execute("""
            INSERT OR IGNORE INTO telegram_state
            (id, last_update_id)
            VALUES (1, 0)
        """)

        conn.commit()

        conn.close()


# =========================================================
# ADDRESS VALIDATION
# =========================================================

def is_evm_address(value):

    if not value:
        return False

    return bool(
        re.fullmatch(
            r"0x[a-fA-F0-9]{40}",
            value.strip()
        )
    )


def extract_addresses(text):

    if not text:
        return []

    addresses = re.findall(
        r"0x[a-fA-F0-9]{40}",
        text
    )

    # Remove duplicates while preserving order
    result = []

    seen = set()

    for address in addresses:

        normalized = address.lower()

        if normalized not in seen:

            seen.add(normalized)

            result.append(address)

    return result


# =========================================================
# RPC
# =========================================================

def rpc_balance(
    address,
    rpc_url
):

    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getBalance",
        "params": [
            address,
            "latest"
        ],
        "id": 1
    }

    response = requests.post(
        rpc_url,
        json=payload,
        timeout=25
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

    return int(
        data["result"],
        16
    )


def get_wallet_balance(
    address,
    network
):

    network = network.upper()

    if network == "BSC":

        return rpc_balance(
            address,
            BSC_RPC_URL
        )

    if network == "ETH":

        return rpc_balance(
            address,
            ETH_RPC_URL
        )

    raise ValueError(
        f"Unsupported network: {network}"
    )


def raw_to_coin(
    balance_raw
):

    return balance_raw / 10**18


# =========================================================
# TELEGRAM
# =========================================================

def telegram_api(
    method,
    payload=None
):

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not configured"
        )

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/"
        + method
    )

    response = requests.post(
        url,
        json=payload or {},
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):

        raise RuntimeError(
            str(data)
        )

    return data


def send_telegram(
    message
):

    if not TELEGRAM_BOT_TOKEN:
        return

    if not TELEGRAM_CHAT_ID:
        return

    try:

        telegram_api(
            "sendMessage",
            {
                "chat_id":
                    TELEGRAM_CHAT_ID,

                "text":
                    message,

                "disable_web_page_preview":
                    True
            }
        )

    except Exception as exc:

        print(
            "Telegram send error:",
            exc,
            flush=True
        )


def telegram_file_download(
    file_id
):

    result = telegram_api(
        "getFile",
        {
            "file_id": file_id
        }
    )

    file_path = result[
        "result"
    ][
        "file_path"
    ]

    url = (
        "https://api.telegram.org/file/bot"
        + TELEGRAM_BOT_TOKEN
        + "/"
        + file_path
    )

    response = requests.get(
        url,
        timeout=60
    )

    response.raise_for_status()

    return response.content


def get_file_name(document):

    if document.get("file_name"):

        return document[
            "file_name"
        ]

    return (
        "telegram_report_"
        + str(
            document.get(
                "file_id",
                "unknown"
            )
        )
        + ".txt"
    )


# =========================================================
# REPORT PROCESSING
# =========================================================

def process_report(
    file_name,
    file_content,
    telegram_message_id,
    telegram_date
):

    # Only decode as text.
    # Binary/unreadable files are ignored.
    try:

        text = file_content.decode(
            "utf-8",
            errors="ignore"
        )

    except Exception:

        return 0

    addresses = extract_addresses(
        text
    )

    received_at = now_utc()

    with db_lock:

        conn = get_db()

        cursor = conn.execute(
            """
            INSERT INTO reports
            (
                telegram_message_id,
                file_name,
                telegram_date,
                received_at,
                wallet_count
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(
                    telegram_message_id
                ),

                file_name,

                telegram_date,

                received_at,

                len(addresses)
            )
        )

        report_id = cursor.lastrowid

        for address in addresses:

            # Report ID is stored in source_report.
            # This lets you find exactly which report
            # generated the wallet record.

            source_report = (
                f"{file_name} | "
                f"Report ID: {report_id}"
            )

            conn.execute(
                """
                INSERT INTO wallets
                (
                    address,
                    network,
                    source_report,
                    report_received_at,
                    telegram_message_id,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)

                ON CONFLICT(address, network)
                DO UPDATE SET

                    source_report =
                        excluded.source_report,

                    report_received_at =
                        excluded.report_received_at,

                    telegram_message_id =
                        excluded.telegram_message_id
                """,
                (
                    address.lower(),

                    "BSC",

                    source_report,

                    telegram_date,

                    str(
                        telegram_message_id
                    ),

                    received_at
                )
            )

        conn.commit()

        conn.close()

    return len(addresses)


def process_telegram_update(
    update
):

    message = (
        update.get("message")
        or
        update.get("channel_post")
    )

    if not message:
        return

    document = message.get(
        "document"
    )

    if not document:
        return

    file_name = get_file_name(
        document
    )

    file_id = document.get(
        "file_id"
    )

    if not file_id:
        return

    telegram_message_id = message.get(
        "message_id",
        ""
    )

    telegram_timestamp = message.get(
        "date"
    )

    if telegram_timestamp:

        telegram_date = (
            datetime.fromtimestamp(
                telegram_timestamp,
                timezone.utc
            ).isoformat()
        )

    else:

        telegram_date = now_utc()

    # Prevent processing same Telegram message twice
    with db_lock:

        conn = get_db()

        exists = conn.execute(
            """
            SELECT id
            FROM reports
            WHERE telegram_message_id=?
            LIMIT 1
            """,
            (
                str(
                    telegram_message_id
                ),
            )
        ).fetchone()

        conn.close()

    if exists:

        return

    try:

        content = telegram_file_download(
            file_id
        )

        count = process_report(
            file_name,
            content,
            telegram_message_id,
            telegram_date
        )

        print(
            f"Report received: "
            f"{file_name} | "
            f"wallets={count} | "
            f"time={telegram_date}",
            flush=True
        )

    except Exception as exc:

        print(
            "Report processing error:",
            exc,
            flush=True
        )


def telegram_polling_loop():

    if not TELEGRAM_BOT_TOKEN:

        print(
            "Telegram polling disabled: "
            "TELEGRAM_BOT_TOKEN missing.",
            flush=True
        )

        return

    print(
        "Telegram report listener started.",
        flush=True
    )

    # First read current state
    try:

        with db_lock:

            conn = get_db()

            row = conn.execute(
                """
                SELECT last_update_id
                FROM telegram_state
                WHERE id=1
                """
            ).fetchone()

            conn.close()

        offset = (
            int(
                row["last_update_id"]
            )
            + 1
            if row
            else 0
        )

    except Exception:

        offset = 0

    while True:

        try:

            result = telegram_api(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 25,

                    "allowed_updates": [
                        "message",
                        "channel_post"
                    ]
                }
            )

            updates = result.get(
                "result",
                []
            )

            for update in updates:

                update_id = update.get(
                    "update_id"
                )

                if update_id is not None:

                    offset = (
                        update_id + 1
                    )

                    with db_lock:

                        conn = get_db()

                        conn.execute(
                            """
                            UPDATE telegram_state
                            SET last_update_id=?
                            WHERE id=1
                            """,
                            (
                                update_id,
                            )
                        )

                        conn.commit()

                        conn.close()

                process_telegram_update(
                    update
                )

        except Exception as exc:

            print(
                "Telegram polling error:",
                exc,
                flush=True
            )

            time.sleep(
                TELEGRAM_POLL_SECONDS
            )


# =========================================================
# WALLET MONITOR
# =========================================================

def save_check(
    wallet_id,
    address,
    network,
    balance_raw
):

    balance = raw_to_coin(
        balance_raw
    )

    checked_at = now_utc()

    has_balance = (
        1
        if balance_raw > 0
        else 0
    )

    with db_lock:

        conn = get_db()

        previous = conn.execute(
            """
            SELECT has_balance
            FROM wallets
            WHERE id=?
            """,
            (
                wallet_id,
            )
        ).fetchone()

        conn.execute(
            """
            UPDATE wallets

            SET
                last_checked_at=?,
                balance_raw=?,
                balance=?,
                has_balance=?

            WHERE id=?
            """,
            (
                checked_at,

                str(
                    balance_raw
                ),

                balance,

                has_balance,

                wallet_id
            )
        )

        conn.execute(
            """
            INSERT INTO checks
            (
                wallet_id,
                network,
                checked_at,
                balance_raw,
                balance,
                has_balance
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                wallet_id,

                network,

                checked_at,

                str(
                    balance_raw
                ),

                balance,

                has_balance
            )
        )

        wallet = conn.execute(
            """
            SELECT
                address,
                network,
                source_report,
                report_received_at
            FROM wallets
            WHERE id=?
            """,
            (
                wallet_id,
            )
        ).fetchone()

        conn.commit()

        conn.close()

    # Notify when changing from zero to positive
    if (
        has_balance
        and
        (
            previous is None
            or
            previous["has_balance"] == 0
        )
    ):

        symbol = (
            "BNB"
            if network.upper() == "BSC"
            else "ETH"
        )

        message = (
            "💰 موجودی جدید پیدا شد\n\n"

            f"شبکه: {network}\n"

            f"آدرس:\n"
            f"{address}\n\n"

            f"موجودی:\n"
            f"{balance:.18f} {symbol}\n\n"

            f"Report:\n"
            f"{wallet['source_report'] or '-'}\n\n"

            f"زمان دریافت Report:\n"
            f"{wallet['report_received_at'] or '-'}\n\n"

            f"زمان بررسی:\n"
            f"{checked_at}"
        )

        send_telegram(
            message
        )


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
                    SELECT
                        id,
                        address,
                        network
                    FROM wallets
                    """
                ).fetchall()

                conn.close()

            for wallet in wallets:

                try:

                    balance = get_wallet_balance(
                        wallet["address"],
                        wallet["network"]
                    )

                    save_check(
                        wallet["id"],
                        wallet["address"],
                        wallet["network"],
                        balance
                    )

                except Exception as exc:

                    print(
                        f"Check failed "
                        f"{wallet['address']} "
                        f"{wallet['network']}: "
                        f"{exc}",
                        flush=True
                    )

        except Exception as exc:

            print(
                "Monitor loop error:",
                exc,
                flush=True
            )

        time.sleep(
            POLL_SECONDS
        )


# =========================================================
# WEB
# =========================================================

@app.route("/")
def index():

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
            """
            SELECT COUNT(*) AS c
            FROM wallets
            """
        ).fetchone()["c"]

        funded = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM wallets
            WHERE has_balance=1
            """
        ).fetchone()["c"]

        checks = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM checks
            """
        ).fetchone()["c"]

        reports = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM reports
            """
        ).fetchone()["c"]

        conn.close()

    return jsonify({

        "wallets": wallets,

        "funded": funded,

        "checks": checks,

        "reports": reports

    })


@app.route("/api/wallets")
def api_wallets():

    query = request.args.get(
        "q",
        ""
    ).strip()

    network = request.args.get(
        "network",
        ""
    ).strip().upper()

    with db_lock:

        conn = get_db()

        if query and network:

            rows = conn.execute(
                """
                SELECT *
                FROM wallets

                WHERE
                    (
                        address LIKE ?
                        OR label LIKE ?
                        OR source_report LIKE ?
                    )

                    AND network=?

                ORDER BY id DESC
                """,
                (
                    f"%{query}%",
                    f"%{query}%",
                    f"%{query}%",
                    network
                )
            ).fetchall()

        elif query:

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

        elif network:

            rows = conn.execute(
                """
                SELECT *
                FROM wallets
                WHERE network=?
                ORDER BY id DESC
                """,
                (
                    network,
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


@app.route("/api/reports")
def api_reports():

    with db_lock:

        conn = get_db()

        rows = conn.execute(
            """
            SELECT *
            FROM reports
            ORDER BY id DESC
            LIMIT 200
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
        data.get(
            "address",
            ""
        )
    ).strip().lower()

    network = str(
        data.get(
            "network",
            "BSC"
        )
    ).strip().upper()

    label = str(
        data.get(
            "label",
            ""
        )
    ).strip()

    source_report = str(
        data.get(
            "source_report",
            ""
        )
    ).strip()

    report_received_at = str(
        data.get(
            "report_received_at",
            ""
        )
    ).strip()

    if not is_evm_address(
        address
    ):

        return jsonify({
            "error":
                "آدرس EVM معتبر نیست"
        }), 400

    if network not in [
        "BSC",
        "ETH"
    ]:

        return jsonify({
            "error":
                "شبکه باید BSC یا ETH باشد"
        }), 400

    with db_lock:

        conn = get_db()

        try:

            conn.execute(
                """
                INSERT INTO wallets
                (
                    address,
                    network,
                    label,
                    source_report,
                    report_received_at,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    address,
                    network,
                    label,
                    source_report,
                    report_received_at,
                    now_utc()
                )
            )

            conn.commit()

        except sqlite3.IntegrityError:

            conn.close()

            return jsonify({
                "error":
                    "این کیف پول در این شبکه قبلاً ثبت شده است"
            }), 409

        conn.close()

    return jsonify({
        "ok": True
    })


# =========================================================
# START
# =========================================================

init_db()


def start_background_services():

    if os.getenv(
        "ENABLE_MONITOR",
        "1"
    ) == "1":

        threading.Thread(
            target=monitor_loop,
            daemon=True
        ).start()

    if os.getenv(
        "ENABLE_TELEGRAM",
        "1"
    ) == "1":

        threading.Thread(
            target=telegram_polling_loop,
            daemon=True
        ).start()


start_background_services()


if __name__ == "__main__":

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
