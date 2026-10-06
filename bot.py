"""Однократный сбор RSS и отправка личного дайджеста. Python 3.12, без зависимостей."""
import hashlib
import html
import json
import os
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

QUERIES = [
    ('Ручной массаж', '"massage therapy" OR "manual therapy" OR "lymphatic drainage" when:2d', 'en-US', 'US'),
    ('Технологии', '"massage device" OR "massage equipment" OR "pressotherapy" OR "endermologie" when:2d', 'en-US', 'US'),
    ('На русском', 'массаж исследование OR массаж технологии OR массаж выставка when:2d', 'ru', 'RU'),
]
STATE = Path('state/sent.json')

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'MassageNewsBot/1.0'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()

def parse_feed(data, category, now):
    rows = []
    for item in ET.fromstring(data).findall('./channel/item'):
        title = html.unescape(item.findtext('title', '')).strip()
        link = item.findtext('link', '').strip()
        try:
            date = parsedate_to_datetime(item.findtext('pubDate', ''))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError, OverflowError):
            continue
        if not title or not link.startswith('https://') or not now - timedelta(hours=48) <= date <= now:
            continue
        key = hashlib.sha256(title.casefold().encode()).hexdigest()
        rows.append({'id': key, 'title': title[:700], 'link': link, 'date': date,
                     'source': item.findtext('source', 'Источник не указан'), 'category': category})
    return rows

def send(text):
    token = os.environ['TELEGRAM_BOT_TOKEN'].strip()
    chat_id = os.environ['TELEGRAM_CHAT_ID'].strip()
    data = json.dumps({'chat_id': chat_id, 'text': text,
                       'link_preview_options': {'is_disabled': True}}).encode()
    req = urllib.request.Request('https://api.telegram.org/bot' + token + '/sendMessage',
                                 data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.load(response)
        if not result.get('ok'):
            raise ValueError('Telegram rejected message')
    except Exception:
        # Не печатать URL: он содержит токен. Без автоповтора после неопределённого результата.
        raise RuntimeError('Ошибка отправки Telegram. Проверьте Secrets и /start у бота.') from None

def main():
    dry = '--dry-run' in sys.argv
    if not dry and not all(os.getenv(k, '').strip() for k in ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID')):
        raise RuntimeError('Нужны Secrets TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID')
    now = datetime.now(timezone.utc)
    sent = json.loads(STATE.read_text()) if STATE.exists() else {}
    sent = {k: v for k, v in sent.items() if v > (now - timedelta(days=14)).timestamp()}
    rows, failures = [], 0
    for category, query, language, country in QUERIES:
        params = urllib.parse.urlencode({'q': query, 'hl': language, 'gl': country,
                                         'ceid': country + ':' + language.split('-')[0]})
        try:
            rows.extend(parse_feed(fetch('https://news.google.com/rss/search?' + params), category, now))
        except Exception:
            failures += 1
            print('Не удалось прочитать ленту:', category)
    if failures == len(QUERIES):
        raise RuntimeError('Все источники недоступны. Это ошибка сбора, а не отсутствие новостей.')
    unique = {}
    for row in sorted(rows, key=lambda r: r['date'], reverse=True):
        if row['id'] not in sent:
            unique.setdefault(row['id'], row)
    items = list(unique.values())[:10]
    header = '🌍 Новости массажа — ' + now.astimezone(timezone(timedelta(hours=3))).strftime('%d.%m.%Y')
    header += '\nПубликации за последние 48 часов. Автоматическая подборка, без проверки утверждений и ИИ-анализа.'
    if failures:
        header += '\nЧасть лент временно недоступна.'
    if not items:
        header += '\nНовых публикаций для отправки не найдено.'
    if dry:
        print(header)
    else:
        send(header)
    for row in items:
        text = f"{row['category']}\n{row['title']}\n{row['source']} · {row['date']:%d.%m.%Y}\n{row['link']}"
        if dry:
            print(text)
        else:
            send(text)
            sent[row['id']] = now.timestamp()
            STATE.parent.mkdir(exist_ok=True)
            tmp = STATE.with_suffix('.tmp')
            tmp.write_text(json.dumps(sent))
            tmp.replace(STATE)
    print('Подобрано публикаций:', len(items))

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
