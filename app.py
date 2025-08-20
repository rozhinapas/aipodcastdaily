import os
import logging
from pathlib import Path
from datetime import datetime
from uuid import uuid4
from threading import Thread
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import feedparser
import requests
from bs4 import BeautifulSoup
from flask import Flask, url_for, Response, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from pytz import timezone
from openai import OpenAI


# ==============================
# تنظیمات لاگر
# ==============================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==============================
# تنظیمات ثابت
# ==============================
OPENAI_API_KEY = "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"
OUTPUT_DIR = Path("static")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

client = OpenAI(api_key=OPENAI_API_KEY)
app = Flask(__name__, static_folder=str(OUTPUT_DIR), static_url_path="/static")

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "aipoddaily@gmail.com"
SMTP_PASS = "rzxubtbasgwgbakv"

USERS = []


# ==============================
# ابزارهای پایه
# ==============================
# ارسال ایمیل به کاربر
def send_email(to_email: str, subject: str, body: str):
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_USER
        msg["To"] = to_email
        msg["Subject"] = subject

        # نسخه HTML
        html_body_content = body.replace("\n", "<br>")
        html_body = f"""
        <html>
          <body dir="rtl" style="font-family:Tahoma, sans-serif; text-align:right;">
            {html_body_content}
          </body>
        </html>
        """

        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)

        logger.info(f"📧 ایمیل با موفقیت به {to_email} ارسال شد.")
    except Exception as e:
        logger.error(f"❌ خطا در ارسال ایمیل به {to_email}: {e}")


# ==============================
# توابع مربوط به ChatGPT / OpenAI
# ==============================
def ask_to_chatgpt(prompt: str, sys_setting: str = "") -> str:
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": sys_setting},
                {"role": "user", "content": prompt},
            ],
            timeout=30
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error(f"خطا در ChatGPT API: {e}")
        return "خطا در پردازش متن"


# تولید فایل صوتی در پس‌زمینه
def generate_podcast_audio_background(text: str, voice: str, out_path: str):
    tmp_path = out_path + ".part"
    try:
        with client.audio.speech.with_streaming_response.create(
            model="tts-1-hd",
            voice=voice,
            input=text,
            response_format="mp3",
        ) as resp:
            resp.stream_to_file(tmp_path)

        os.replace(tmp_path, out_path)
        logger.info(f"✅ فایل صوتی ساخته شد: {out_path}")
    except Exception as e:
        logger.error(f"❌ خطا در ساخت صدا در پس‌زمینه: {e}")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


# ==============================
# پردازش و خلاصه‌سازی خبر
# ==============================

# دریافت محتوای مقاله
def get_article_content(url: str) -> str:
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        text_p = [s.get_text().strip() for s in soup.find_all("p") if s.get_text().strip()]
        if not text_p:
            text_p = [s.get_text().strip() for s in soup.find_all("div") if s.get_text().strip()]

        content = " ".join(text_p[:10])
        return content if content else "محتوای مقاله در دسترس نیست"

    except requests.exceptions.Timeout:
        logger.warning(f"Timeout برای URL: {url}")
        return "خطا در دریافت محتوا - timeout"
    except requests.exceptions.RequestException as e:
        logger.warning(f"خطا در دریافت URL {url}: {e}")
        return "خطا در دریافت محتوا"
    except Exception as e:
        logger.error(f"خطای غیرمنتظره برای URL {url}: {e}")
        return "خطا در پردازش محتوا"


# خلاصه‌سازی اخبار RSS
def summarize_feed(rss_url: str, num_of_articles: int) -> list[dict]:
    generated_content: list[dict] = []
    try:
        feed = feedparser.parse(rss_url)
        if not feed.entries:
            logger.error(f"هیچ خبری در RSS feed پیدا نشد: {rss_url}")
            return []
    except Exception as e:
        logger.error(f"خطا در دریافت RSS feed ({rss_url}): {e}")
        return []

    successful_articles = 0
    for idx, entry in enumerate(feed.entries[0:num_of_articles], start=1):
        if successful_articles >= num_of_articles:
            break
        try:
            title = entry.get("title", "").strip()
            summary_hint = entry.get("summary", "").strip()

            if len(summary_hint) < 40 or not any(word in summary_hint for word in title.split()[:3]):
                content = get_article_content(entry.link)
            else:
                content = summary_hint

            if not content or content.startswith("خطا"):
                logger.warning(f"خبر {idx}: محتوای نامعتبر")
                continue

            prompt = (
                "Summarize the following article IN PERSIAN in about 150 characters (~150). "
                "FOCUS ON SPECIFIC CLAIMS/OUTCOMES from the content. "
                "Avoid source mentions, meta phrases, or ceremony fluff. "
                f"عنوان خبر: {title}\n\n"
                f"متن خبر: {content}"
            )

            answer = ask_to_chatgpt(prompt)
            if answer and not answer.startswith("خطا"):
                generated_content.append({
                    "index": successful_articles + 1,
                    "title": title,
                    "summary": answer,
                    "link": entry.link
                })
                successful_articles += 1
        except Exception as e:
            logger.error(f"خطا در پردازش خبر {idx}: {e}")
            continue

    logger.info(f"تعداد خبرها پردازش شد: {len(generated_content)}")
    return generated_content


# ساخت متن پادکست
def build_podcast_text(news_list: list[dict]) -> str:
    if not news_list:
        return ""
    try:
        news_text = "\n\n".join(f"{n['index']}. {n['summary']}" for n in news_list)
        system_prompt = (
            "You are a professional Persian news podcast scriptwriter. "
            "Your tone should be neutral, fluent, short, and suitable for TTS."
        )
        user_prompt = (
            "Write a complete Persian news podcast script:\n"
            "- Start with greeting.\n"
            "- Read each news item.\n"
            "- End with goodbye.\n"
            "- Convert all numbers to Persian words.\n\n"
            f"News list:\n{news_text}"
        )
        return ask_to_chatgpt(user_prompt, sys_setting=system_prompt)
    except Exception as e:
        logger.error(f"خطا در ساخت اسکریپت پادکست: {e}")
        return ""


# ==============================
# پایپ‌لاین اصلی
# ==============================

# تولید محتوا برای یک کاربر

def generate_for_user_background(user: dict, n: int, audio_path: str) -> str:
    try:
        rss_url = user.get("rss_url", "").strip()
        summaries = summarize_feed(rss_url, n)

        if summaries:
            summaries_text = "\n".join(
                f"تیتر {i}: {it.get('title','')}\nتوضیحات: {it.get('summary','')}"
                for i, it in enumerate(summaries, start=1)
            )
            script = build_podcast_text(summaries) or ""
            tts_input = script.strip()[:1400]
            if tts_input:
                generate_podcast_audio_background(tts_input, "shimmer", audio_path)
        else:
            summaries_text = "خطا: خبری پیدا نشد یا پردازش نشد."

        return summaries_text

    except Exception as e:
        logger.error(f"❌ خطای پس‌زمینه برای کاربر {user.get('email')}: {e}")
        return "خطای داخلی در تولید محتوا."


# اجرای خودکار روزانه
def daily_job():
    logger.info("⏰ شروع اجرای خودکار...")

    for user in USERS:
        if user.get("days_left", 0) > 0 and user.get("rss_url"):

            # ساخت اسم فایل خروجی برای پادکست
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            short_id = uuid4().hex[:5]
            base = f"podcast_{stamp}_{short_id}"

            audio_path = str(OUTPUT_DIR / f"{base}.mp3")

            # گرفتن خلاصه خبرها + ساخت فایل صوتی
            summary_text = generate_for_user_background(user, 5, audio_path)

            # ساخت متن ایمیل (خلاصه خبرها + لینک پادکست)
            audio_url = f"https://flask-aipodcast.chbk.app/static/{os.path.basename(audio_path)}"
            email_body = (
                f"سلام!\n\n"
                f"این خلاصه خبرهای امروز شماست:\n\n"
                f"{summary_text}\n\n"
                f"🎧 لینک پادکست:\n{audio_url}\n\n"
                f"روز خوبی داشته باشید."
            )

            # ارسال ایمیل
            send_email(user["email"], "خلاصه خبرهای امروز", email_body)

            # کم کردن یک روز از اعتبار
            if user["days_left"] > 0:
                user["days_left"] -= 1
                logger.info(f"⏳ یک روز از اعتبار {user['email']} کم شد. باقی‌مانده: {user['days_left']} روز")

    logger.info("✅ اجرای خودکار به پایان رسید.")


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        email = data.get("customer_email")
        days_left = int(data.get("day", 0))
        rss_url = data.get("rss_value", "").strip()

        if not email or not rss_url:
            return jsonify({"error": "email و rss_url اجباری هستن"}), 400

        USERS.append({
            "email": email,
            "days_left": days_left,
            "rss_url": rss_url
        })

        logger.info(f"✅ کاربر جدید اضافه شد: {email}")
        return jsonify({"status": "ok", "user_count": len(USERS)}), 200

    except Exception as e:
        logger.error(f"❌ خطا در وب‌هوک: {e}")
        return jsonify({"error": "server error"}), 500


# ==============================
# زمان‌بندی روزانه
# ==============================
scheduler = BackgroundScheduler(timezone=timezone("Asia/Tehran"))
scheduler.add_job(daily_job, "cron", hour=9, minute=0)
scheduler.start()
logger.info("📅 زمان‌بندی روزانه ساعت ۹ صبح فعال شد.")


# ==============================
# اجرای برنامه اصلی
# ==============================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
