"""Однократный сбор RSS и отправка личного дайджеста. Python 3.12, без зависимостей."""
import hashlib
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

# Строгая редакционная подборка для студии женского массажа.
# Источник в списке не означает научного подтверждения всех его утверждений.
SOURCE_GROUPS = {
    'Профессиональная практика': ('massagemag.com', 'abmp.com', 'amtamassage.org'),
    'Индустрия SPA': ('professionalbeauty.co.uk', 'spabusiness.com', 'europeanspamagazine.com'),
    'Исследования и разборы': ('pubmed.ncbi.nlm.nih.gov', 'pmc.ncbi.nlm.nih.gov',
        'uclahealth.org', 'nccih.nih.gov', 'cochrane.org', 'journals.sagepub.com'),
}
TOPICS = ('"massage" OR "lymphatic drainage" OR "myofascial" OR "endermologie" '
          'OR "pressotherapy" OR "body contouring" OR "body treatment"')
QUERIES = [
    (category, '(' + ' OR '.join('site:' + d for d in domains) + ') (' + TOPICS + ') when:7d', 'en-US', 'US')
    for category, domains in SOURCE_GROUPS.items()
]
BLOCKED = re.compile(
    r"\b(porn\w*|erotic\w*|sexual\w*|sex|nude\w*|naked|brothel\w*|prostitut\w*|"
    r"escort\w*|trafficking|assault\w*|arrest\w*|police|lawsuit\w*|crime|criminal\w*|"
    r"raid\w*|homicide|murder\w*|rape|rapist|molest\w*|misconduct|"
    r"vacanc\w*|hiring|recruit\w*|salary|salaries|job|jobs|coupon\w*|"
    r"discount\w*|prime day|black friday|shopping|amazon|chair|chairs|"
    r"massage gun|massage guns|foot massager|vibrat\w*|pet|pets|equine|horse|horses|"
    r"dog|dogs|reiki|astrology|crystal healing|franchise\w*|grand opening)\b|"
    r"порн|эрот|секс|проститу|бордел|интим|полиц|арест|убий|изнасил|вакан|купить|скидк",
    re.I,
)
SUBJECTS = [
    ('Лимфодренаж', r'lymphatic|lymphoedema|lymphedema|лимфодрен'),
    ('Аппаратные процедуры', r'endermolog|pressotherap|вакуум|LPG|эндосфер|endosph|'
        r'icoone|radiofrequen|body contour|body sculpt|body treatment|'
        r'robot.{0,20}massage|massage.{0,20}robot|ultrasound|cavitation'),
    ('Ручные техники', r'myofascial|fascia|deep tissue|swedish massage|hot stone|'
        r'cupping|trigger point|manual therapy|massage technique|массаж'),
    ('Эффекты и безопасность массажа', r'massage|bodywork'),
]

def classify(title, source_url):
    host = (urllib.parse.urlparse(source_url).hostname or '').lower()
    kind = next((name for name, domains in SOURCE_GROUPS.items()
                 if any(host == d or host.endswith('.' + d) for d in domains)), None)
    if not kind or BLOCKED.search(title):
        return None
    topic = next((name for name, pattern in SUBJECTS if re.search(pattern, title, re.I)), None)
    if not topic:
        return None
    # Общие новости бизнеса со словом massage не нужны. Требуется содержательная тема.
    if topic == 'Эффекты и безопасность массажа' and not re.search(
        r'research|study|trial|review|evidence|effect|benefit|risk|safe|pain|stress|sleep|'
        r'technique|treatment|therap|innovation|technology|recovery|relax', title, re.I
    ):
        return None
    return topic, kind

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
        source = item.find('source')
        selected = classify(title, source.get('url', '') if source is not None else '')
        if selected is None:
            continue
        try:
            date = parsedate_to_datetime(item.findtext('pubDate', ''))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError, OverflowError):
            continue
        if not title or not link.startswith('https://') or not now - timedelta(days=7) <= date <= now:
            continue
        key = hashlib.sha256(title.casefold().encode()).hexdigest()
        rows.append({'id': key, 'title': title[:700], 'link': link, 'date': date,
                     'source': item.findtext('source', 'Источник не указан'),
                     'category': selected[0], 'kind': selected[1]})
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
    # Не заполнять всю подборку одним изданием или одной темой.
    items, source_counts, topic_counts = [], {}, {}
    for row in unique.values():
        if source_counts.get(row['source'], 0) >= 2 or topic_counts.get(row['category'], 0) >= 3:
            continue
        items.append(row)
        source_counts[row['source']] = source_counts.get(row['source'], 0) + 1
        topic_counts[row['category']] = topic_counts.get(row['category'], 0) + 1
        if len(items) >= 6:
            break
    header = '🌿 Профессиональный обзор для студии массажа — ' + now.astimezone(timezone(timedelta(hours=3))).strftime('%d.%m.%Y')
    header += '\nМатериалы за последние 7 дней из выбранных профессиональных и научных источников. Заголовки на языке оригинала. Автоматический отбор, без ИИ-пересказа и проверки полного текста.'
    if failures:
        header += '\nЧасть лент временно недоступна.'
    if not items:
        header += '\nНовых материалов, прошедших фильтр для студии, сегодня не найдено. Случайные новости не добавляю.'
    if dry:
        print(header)
    else:
        send(header)
    for row in items:
        text = f"{row['category']} · {row['kind']}\n{row['title']}\n{row['source']} · {row['date']:%d.%m.%Y}\n{row['link']}"
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
