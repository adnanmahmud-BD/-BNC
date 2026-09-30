import os
import sys
import time
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# স্ক্রিপ্টের নিজের ফোল্ডার থেকে .env লোড করা হয়
load_dotenv(Path(__file__).resolve().parent / ".env")

# ================= কনফিগারেশন প্যারামিটার =================
INTERVAL = '15m'         # চার্ট টাইমফ্রেম ১৫ মিনিট
MIN_PERCENT_GAIN = 1.0   # পরপর ২টি সবুজ ক্যান্ডেলে নূন্যতম সামগ্রিক বৃদ্ধি (%)
QUOTE_ASSET = 'USDT'     # বেস পেয়ার
MAX_PAIRS_TO_SCAN = 120  # ভলিউম অনুযায়ী শীর্ষ কয়টি পেয়ার স্ক্যান করবেন

# কোনো কয়েন খুঁজে না পেলেও কি টেলিগ্রামে মেসেজ পাঠাবে?
SEND_MSG_IF_NO_COIN = False  # মেসেজ না পাঠাতে চাইলে False, পাঠাতে চাইলে True

# ================= Telegram কনফিগারেশন (.env থেকে) =================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
# ========================================================

BASE_URL = "https://data-api.binance.vision"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}


def send_telegram_message(token, chat_id, message):
    """Telegram Bot API ব্যবহার করে মেসেজ পাঠায়।"""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        res_data = response.json()
        if res_data.get("ok"):
            print("Telegram-এ মেসেজ সফলভাবে পাঠানো হয়েছে।")
        else:
            print(f"Telegram API ত্রুটি: {res_data.get('description')}")
    except Exception as e:
        print(f"Telegram মেসেজ পাঠানোর সময় ত্রুটি: {e}")


def get_top_trading_pairs(quote_asset='USDT', limit=100):
    """২৪ ঘণ্টার ভলিউম অনুযায়ী শীর্ষ পেয়ারগুলো সংগ্রহ করে এবং লিভারেজড টোকেন বাদ দেয়।"""
    url = f"{BASE_URL}/api/v3/ticker/24hr"
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            print(f"API এরর: স্ট্যাটাস কোড {response.status_code}")
            return []

        data = response.json()
        if not isinstance(data, list):
            return []

        excluded_tokens = ["UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"]

        pairs = [
            item for item in data
            if isinstance(item, dict)
            and item.get('symbol', '').endswith(quote_asset)
            and not any(token in item.get('symbol', '') for token in excluded_tokens)
            and float(item.get('quoteVolume', 0)) > 0
        ]
        pairs.sort(key=lambda x: float(x['quoteVolume']), reverse=True)
        return [item['symbol'] for item in pairs[:limit]]
    except Exception as e:
        print(f"পেয়ার সংগ্রহে ত্রুটি: {e}")
        return []


def check_pattern_and_growth(symbol, interval, min_growth):
    """
    চলমান ক্যান্ডেলসহ একদম শেষ ৩টি ক্যান্ডেল চেক করে:
    - ক্যান্ডেল ৩ (klines[-3]): লাল ক্যান্ডেল (Close < Open)
    - ক্যান্ডেল ২ (klines[-2]): ১ম সবুজ ক্যান্ডেল (Close > Open)
    - ক্যান্ডেল ১ (klines[-1]): ২য় সবুজ ক্যান্ডেল (বর্তমান চলমান, Current Price > Open)
    - বৃদ্ধি: ১ম সবুজের Open থেকে বর্তমান রানিং প্রাইসের বৃদ্ধি >= min_growth
    """
    url = f"{BASE_URL}/api/v3/klines"
    params = {
        'symbol': symbol,
        'interval': interval,
        'limit': 3  # একদম শেষ ৩টি ক্যান্ডেল (চলমানটি সহ)
    }
    try:
        res = requests.get(url, headers=HEADERS, params=params, timeout=5)
        if res.status_code != 200:
            return None

        klines = res.json()
        if not isinstance(klines, list) or len(klines) < 3:
            return None

        # শেষ ৩টি ক্যান্ডেল (klines[-1] হলো বর্তমান চলমান ক্যান্ডেল)
        c_red = klines[-3]       # ২ ক্যান্ডেল আগের সমাপ্ত ক্যান্ডেল (লাল)
        c_green1 = klines[-2]    # পূর্ববর্তী সমাপ্ত ক্যান্ডেল (সবুজ)
        c_green2 = klines[-1]    # বর্তমান রানিং ক্যান্ডেল (সবুজ)

        open_red, close_red = float(c_red[1]), float(c_red[4])
        open_g1, close_g1 = float(c_green1[1]), float(c_green1[4])
        open_g2, current_price = float(c_green2[1]), float(c_green2[4])

        # শর্ত ১: প্রথম ক্যান্ডেলটি লাল
        is_red = close_red < open_red

        # শর্ত ২: পরের দুইটি সবুজ (চলমানটির বর্তমান দাম ওপেনের চেয়ে বেশি হতে হবে)
        is_two_green = (close_g1 > open_g1) and (current_price > open_g2)

        if not (is_red and is_two_green):
            return None

        if open_g1 == 0:
            return None

        # শর্ত ৩: ১ম সবুজের Open থেকে বর্তমান প্রাইসের মোট বৃদ্ধি >= min_growth
        pct_gain = ((current_price - open_g1) / open_g1) * 100

        if pct_gain >= min_growth:
            return {
                'symbol': symbol,
                'open_g1': open_g1,
                'current_price': current_price,
                'growth': round(pct_gain, 2)
            }
    except Exception:
        return None
    return None


def run_scanner():
    """সম্পূর্ণ স্ক্যান পরিচালনা করে এবং টেলিগ্রামে ফলাফল পাঠায়।"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("\n==========================================")
    print(f"[{now_str}] স্ক্যানিং শুরু হচ্ছে...")
    print(f"টাইমফ্রেম: {INTERVAL} | লজিক: ১টি লাল + ২টি সবুজ (চলমানসহ) | নূন্যতম বৃদ্ধি: >= +{MIN_PERCENT_GAIN}%")

    symbols = get_top_trading_pairs(QUOTE_ASSET, limit=MAX_PAIRS_TO_SCAN)
    if not symbols:
        print("কোনো পেয়ার লোড করা সম্ভব হয়নি। সংযোগ পরীক্ষা করুন।")
        return

    matched_coins = []

    for symbol in symbols:
        result = check_pattern_and_growth(symbol, INTERVAL, MIN_PERCENT_GAIN)
        if result:
            matched_coins.append(result)
            print(f"[মিল পাওয়া গেছে] {result['symbol']} -> বৃদ্ধি: +{result['growth']}%")
        time.sleep(0.04)  # Rate limit এড়ানোর জন্য সাময়িক বিরতি

    if not matched_coins:
        print("শর্ত পূরণ করে এমন কোনো কয়েন পাওয়া যায়নি।")
        if SEND_MSG_IF_NO_COIN:
            msg = (
                f"🔍 <b>Binance Scanner ({INTERVAL})</b>\n"
                f"১টি লাল ও ২টি সবুজ ক্যান্ডেলে (চলমানসহ) +{MIN_PERCENT_GAIN}% বৃদ্ধি পাওয়া কোনো কয়েন পাওয়া যায়নি।\n"
                f"সময়: <code>{now_str}</code>"
            )
            send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, msg)
    else:
        matched_coins.sort(key=lambda x: x['growth'], reverse=True)

        msg_lines = [
            f"🚀 <b>Binance Live Alert ({INTERVAL})</b>",
            f"প্যাটার্ন: <code>১ লাল + ২ সবুজ (চলমান ক্যান্ডেলসহ)</code>",
            f"শর্ত: <code>মোট বৃদ্ধি >= +{MIN_PERCENT_GAIN}%</code>",
            f"সময়: <code>{now_str}</code>\n"
        ]

        for coin in matched_coins:
            msg_lines.append(
                f"• <b>{coin['symbol']}</b>: <code>+{coin['growth']}%</code>\n"
                f"  Base Open: <code>{coin['open_g1']}</code> | Live Price: <code>{coin['current_price']}</code>"
            )

        full_message = "\n".join(msg_lines)
        send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, full_message)


def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("ত্রুটি: TELEGRAM_BOT_TOKEN অথবা TELEGRAM_CHAT_ID সেট করা নেই।")
        print("GitHub Secrets অথবা .env ফাইলে ভ্যারিয়েবলগুলো যোগ করুন।")
        sys.exit(1)

    try:
        run_scanner()
    except Exception as err:
        print(f"একটি সমস্যা হয়েছে: {err}")
        sys.exit(1)


if __name__ == '__main__':
    main()
