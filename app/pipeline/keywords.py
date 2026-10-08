"""Словари предметной области: технологии, грейды, локации, маркеры мусора.

Это «мозг» фильтрации на правилах. Файл сознательно сделан данными, а не кодом:
править словарь можно, не трогая логику, а бот умеет дописывать сюда
пользовательские ключевые и стоп-слова.

Все паттерны рассчитаны на текст, прогнанный через normalize_for_match():
нижний регистр, «ё» → «е», без ссылок и эмодзи.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Технологии. Ключ — каноническое имя для показа, значения — как это пишут люди.
# ---------------------------------------------------------------------------
STACK: dict[str, list[str]] = {
    # --- Python и его экосистема ---
    "Python": [r"\bpython\b", r"\bpython3\b", r"\bпитон\w*", r"\bпайтон\w*", r"\bpy\b"],
    "Django": [r"\bdjango\b", r"\bджанго\b", r"\bdrf\b"],
    "FastAPI": [r"\bfastapi\b", r"\bфастапи\b", r"fast api"],
    "Flask": [r"\bflask\b", r"\bфласк\b"],
    "aiohttp": [r"\baiohttp\b"],
    "asyncio": [r"\basyncio\b", r"\basync\b.{0,10}\bawait\b"],
    "Celery": [r"\bcelery\b", r"\bсelery\b", r"\bселери\b"],
    "SQLAlchemy": [r"\bsqlalchemy\b", r"\balembic\b"],
    "Pydantic": [r"\bpydantic\b"],
    "aiogram": [r"\baiogram\b", r"\bpyrogram\b", r"\btelethon\b", r"\bpytelegrambotapi\b"],
    "Scrapy": [r"\bscrapy\b", r"\bselenium\b", r"\bplaywright\b", r"\bbeautifulsoup\b", r"\bbs4\b"],
    # --- JS / TS ---
    "JavaScript": [r"\bjavascript\b", r"\bjs\b", r"\bяваскрипт\w*", r"\bджаваскрипт\w*"],
    "TypeScript": [r"\btypescript\b", r"\bts\b", r"\bтайпскрипт\w*"],
    "Node.js": [r"\bnode\.?js\b", r"\bnodejs\b", r"\bнода\b", r"\bноде\b"],
    "React": [r"\breact\b", r"\breact\.?js\b", r"\bреакт\w*"],
    "Next.js": [r"\bnext\.?js\b", r"\bнекст\b"],
    "Vue": [r"\bvue\b", r"\bvue\.?js\b", r"\bnuxt\b", r"\bвью\b"],
    "Angular": [r"\bangular\b", r"\bангуляр\w*"],
    "NestJS": [r"\bnest\.?js\b"],
    "Express": [r"\bexpress\.?js\b", r"\bexpress\b"],
    # --- Прочие языки (нужны, чтобы отличать «не моё») ---
    "Go": [r"\bgolang\b", r"\bgo\b(?!\s*(?:to|live|market))"],
    "Java": [r"\bjava\b(?!script)", r"\bspring boot\b", r"\bkotlin\b"],
    "PHP": [r"\bphp\b", r"\blaravel\b", r"\bsymfony\b", r"\bbitrix\b", r"\bбитрикс\w*"],
    "C#": [r"\bc#\b", r"\b\.net\b", r"\bdotnet\b", r"\basp\.net\b"],
    "C++": [r"\bc\+\+\b", r"\bqt\b"],
    "Ruby": [r"\bruby\b", r"\brails\b"],
    "Rust": [r"\brust\b"],
    "1C": [r"\b1с\b", r"\b1c\b"],
    # --- Базы и хранилища ---
    "PostgreSQL": [r"\bpostgres\w*", r"\bpsql\b", r"\bпостгрес\w*"],
    "MySQL": [r"\bmysql\b", r"\bmariadb\b"],
    "MongoDB": [r"\bmongo\w*"],
    "Redis": [r"\bredis\b", r"\bредис\b"],
    "ClickHouse": [r"\bclickhouse\b", r"\bкликхаус\b"],
    "Elasticsearch": [r"\belastic\w*", r"\bopensearch\b"],
    "Kafka": [r"\bkafka\b", r"\brabbitmq\b", r"\bкафка\b", r"\brabbit\b"],
    # --- Инфраструктура ---
    "Docker": [r"\bdocker\b", r"\bдокер\b", r"\bdocker-compose\b"],
    "Kubernetes": [r"\bkubernetes\b", r"\bk8s\b", r"\bкубер\w*"],
    "CI/CD": [r"\bci/cd\b", r"\bcicd\b", r"\bgithub actions\b", r"\bgitlab ci\b", r"\bjenkins\b"],
    "Linux": [r"\blinux\b", r"\bubuntu\b", r"\bлинукс\b", r"\bnginx\b"],
    "AWS": [r"\baws\b", r"\bamazon web services\b", r"\bs3\b", r"\blambda\b"],
    "GCP": [r"\bgcp\b", r"\bgoogle cloud\b"],
    "Azure": [r"\bazure\b"],
    "Git": [r"\bgit\b", r"\bgithub\b", r"\bgitlab\b"],
    # --- API и протоколы ---
    "REST": [r"\brest\b", r"\brest api\b", r"\brestful\b"],
    "GraphQL": [r"\bgraphql\b"],
    "gRPC": [r"\bgrpc\b"],
    "WebSocket": [r"\bwebsocket\w*", r"\bвебсокет\w*"],
    # --- Data / ML ---
    "Pandas": [r"\bpandas\b", r"\bnumpy\b", r"\bpolars\b"],
    "ML": [r"\bmachine learning\b", r"\bмашинн\w+ обучени\w+", r"\bml\b", r"\bnlp\b"],
    "LLM": [r"\bllm\b", r"\bopenai\b", r"\blangchain\b", r"\bgpt\b", r"\bclaude\b", r"\bai агент\w*"],
    "PyTorch": [r"\bpytorch\b", r"\btensorflow\b", r"\bsklearn\b", r"\bscikit-learn\b"],
    # --- Frontend-обвязка ---
    "HTML/CSS": [r"\bhtml\b", r"\bcss\b", r"\bsass\b", r"\btailwind\b", r"\bbootstrap\b"],
}

# Технологии, по которым определяется «моя» специализация.
PYTHON_STACK = {"Python", "Django", "FastAPI", "Flask", "aiohttp", "Celery", "SQLAlchemy", "aiogram"}
JS_STACK = {"JavaScript", "TypeScript", "Node.js", "React", "Next.js", "Vue", "Angular", "NestJS", "Express"}
# Языки, для которых Python/JS-разработчик заведомо не подходит.
FOREIGN_LANGS = {"Java", "PHP", "C#", "C++", "Ruby", "Rust", "1C", "Go"}

# ---------------------------------------------------------------------------
# Роли
# ---------------------------------------------------------------------------
BACKEND_MARKERS = [
    r"\bbackend\b", r"\bback-end\b", r"\bбэкенд\w*", r"\bбекенд\w*", r"\bсерверн\w+ (?:часть|разработ)",
    r"\bapi\b", r"\bмикросервис\w*", r"\bmicroservice\w*",
]
FRONTEND_MARKERS = [
    r"\bfrontend\b", r"\bfront-end\b", r"\bфронтенд\w*", r"\bфронт\b", r"\bверстальщик\w*",
    r"\bui разработ\w*",
]
FULLSTACK_MARKERS = [r"\bfull ?-?stack\b", r"\bфулл?стек\w*", r"\bфул стек\b"]
DATA_MARKERS = [r"\bdata (?:scientist|engineer|analyst)\b", r"\bдата.?(?:сайентист|инженер|аналитик)"]
DEVOPS_MARKERS = [r"\bdevops\b", r"\bдевопс\w*", r"\bsre\b", r"\bсистемн\w+ администратор"]
MOBILE_MARKERS = [r"\bandroid\b", r"\bios\b", r"\bflutter\b", r"\breact native\b", r"\bмобильн\w+ разработ"]
QA_MARKERS = [r"\bqa\b", r"\bтестировщик\w*", r"\bавтотест\w*", r"\btest automation\b"]

# ---------------------------------------------------------------------------
# Грейды. Порядок важен: ищем от старших к младшим, первое совпадение выигрывает.
# ---------------------------------------------------------------------------
SENIORITY_PATTERNS: list[tuple[str, list[str]]] = [
    ("lead", [r"\bteam ?lead\b", r"\btech ?lead\b", r"\bтим ?лид\w*", r"\bтех ?лид\w*",
              r"\bhead of\b", r"\bруководител\w+ (?:группы|отдела|команды)", r"\bcto\b",
              r"\barchitect\b", r"\bархитектор\b"]),
    ("senior", [r"\bsenior\b", r"\bсеньор\w*", r"\bсиньор\w*", r"\bстарший разработ\w*",
                r"\bведущий разработ\w*", r"\bsr\.?\b", r"\bопыт(?:а|ом)? от (?:4|5|6|7|8|9|10)\b"]),
    ("middle", [r"\bmiddle\b", r"\bмидл\w*", r"\bmid-?level\b", r"\bсредний уровень\b",
                r"\bопыт(?:а|ом)? от (?:2|3)\b"]),
    ("junior", [r"\bjunior\b", r"\bджуниор\w*", r"\bджун\w*", r"\bjr\.?\b", r"\bмладший разработ\w*",
                r"\bначинающ\w+ (?:разработ\w+|программист\w*)", r"\bentry.?level\b",
                r"\bбез опыта\b", r"\bопыт не (?:обязателен|требуется)\b", r"\bначальный уровень\b",
                r"\bmłodszy\b"]),
    ("intern", [r"\bинтерн\w*", r"\bintern\b", r"\binternship\b", r"\btrainee\b", r"\bстажер\w*",
                r"\bстажёр\w*", r"\bстажировк\w*", r"\bпрактикант\w*", r"\bstaż\w*", r"\bpraktyk\w*"]),
]

# ---------------------------------------------------------------------------
# Формат работы
# ---------------------------------------------------------------------------
REMOTE_MARKERS = [
    r"\bудал[её]нн?\w*", r"\bудал[её]нк\w*", r"\bremote\b", r"\bдистанционн\w*", r"\bонлайн\b",
    r"\bиз дома\b", r"\bwork from home\b", r"\bwfh\b", r"\bfully remote\b", r"\banywhere\b",
    r"\bworldwide\b", r"\bzdalnie\b", r"\bzdaln\w*", r"\bremote-?first\b", r"\bиз любой точки\b",
]
HYBRID_MARKERS = [r"\bгибрид\w*", r"\bhybrid\w*", r"\bhybrydow\w*", r"\bчастично удал[её]нн\w*",
                  r"\d\s*(?:дня|дней) в офисе"]
ONSITE_MARKERS = [
    r"\bв офис\w*", r"\bофисн\w+ формат", r"\bon-?site\b", r"\bstacjonarn\w*", r"\bрелокац\w*",
    r"\brelocation\b", r"\bпереезд\b", r"\bполный день в офисе\b",
]

# ---------------------------------------------------------------------------
# География. Познань и Польша — приоритетные, остальное нужно, чтобы понять,
# что вакансия привязана к месту, куда я не поеду.
# ---------------------------------------------------------------------------
LOCATIONS: dict[str, list[str]] = {
    "Познань": [r"\bпознан\w*", r"\bpozna[nń]\w*"],
    "Польша": [r"\bпольш\w*", r"\bpoland\b", r"\bpolsk\w*", r"\bпольский рынок\b"],
    "Варшава": [r"\bваршав\w*", r"\bwarsaw\b", r"\bwarszaw\w*"],
    "Краков": [r"\bкраков\w*", r"\bkrak[oó]w\b", r"\bcracow\b"],
    "Вроцлав": [r"\bвроцлав\w*", r"\bwroc[lł]aw\w*"],
    "Гданьск": [r"\bгданьск\w*", r"\bgda[nń]sk\w*"],
    "ЕС": [r"\beurope\b", r"\beu\b", r"\bевроп\w*", r"\beea\b"],
    "США": [r"\busa\b", r"\bunited states\b", r"\bсша\b", r"\bus only\b", r"\bамерик\w*"],
    "Москва": [r"\bмоскв\w*", r"\bmoscow\b", r"\bмск\b"],
    "Санкт-Петербург": [r"\bпитер\w*", r"\bспб\b", r"\bсанкт-?петербург\w*"],
    "Россия": [
        r"\bросси\w*", r"\brussia\b", r"\bрф\b",
        # Города-миллионники: в вакансиях указывают их, а не страну.
        r"\bновосибирск\w*", r"\bекатеринбург\w*", r"\bказан[ьи]\b", r"\bнижн\w+ новгород\w*",
        r"\bчелябинск\w*", r"\bсамар[аеы]\b", r"\bомск\w*", r"\bростов\w*",
        r"\bуф[аеы]\b", r"\bкрасноярск\w*", r"\bворонеж\w*", r"\bперм[ьи]\b",
        r"\bволгоград\w*", r"\bкраснодар\w*", r"\bсочи\b", r"\bтюмен[ьи]\w*",
        r"\bиркутск\w*", r"\bхабаровск\w*", r"\bвладивосток\w*", r"\bкалининград\w*",
        r"\bярославл[ьия]\w*", r"\bтул[аеы]\b", r"\bиннополис\w*", r"\bсаратов\w*",
        r"\bтольятти\b", r"\bижевск\w*", r"\bбарнаул\w*", r"\bульяновск\w*",
    ],
    "Казахстан": [r"\bказахстан\w*", r"\bалмат\w*", r"\bастан\w*"],
    "Грузия": [r"\bгрузи\w*", r"\bтбилиси\b", r"\bgeorgia\b"],
    "Германия": [r"\bгермани\w*", r"\bgermany\b", r"\bберлин\w*", r"\bberlin\b", r"\bmunich\b"],
    "Кипр": [r"\bкипр\w*", r"\bcyprus\b", r"\bлимассол\w*"],
    "Дубай": [r"\bдубай\w*", r"\bdubai\b", r"\buae\b", r"\bоаэ\b"],
    "Украина": [r"\bукраин\w*", r"\bukraine\b", r"\bкиев\w*", r"\bkyiv\b"],
    "Беларусь": [r"\bбеларус\w*", r"\bбелорус\w*", r"\bминск\w*", r"\bbelarus\b"],
    "Индия": [r"\bindia\b", r"\bинди[йя]\w*"],
    "LATAM": [r"\blatam\b", r"\blatin america\b", r"\bbrazil\b", r"\bargentina\b"],
}

# Требования, из-за которых устроиться физически невозможно: нужно быть в
# России или иметь российское резидентство/гражданство/налоговую прописку.
# Единственная страна с жёстким отказом, а не штрафом баллов — см. scoring.py.
RUSSIA_RESIDENCY_MARKERS = [
    r"\bгражданств\w+ рф\b", r"\bтолько (?:мск|москв\w+|спб|россия|рф)\b",
    r"\bналогов\w+ резидент\w+ рф\b", r"\bрезидент\w+ рф\b",
    r"\bоформление (?:по )?(?:договор\w* )?гпх\b", r"\bсамозанят\w+ рф\b",
    r"\bип на территории рф\b", r"\bтолько для граждан рф\b", r"\bрегистрац\w+ ип в рф\b",
    # Оформление по российскому трудовому праву — работать оттуда невозможно.
    r"\bтк рф\b", r"\bпо тк\b", r"\bтрудов\w+ кодекс\w*\b", r"\bоформление в штат по тк\b",
    r"\bбел\w+ (?:зарплат\w+|з/?п)\b", r"\bдмс\b", r"\bсоцпакет\w*\b",
    r"\bрайонн\w+ коэффициент\w*\b", r"\bподмосковь\w*\b",
]

# Российские работодатели: в тексте может не быть ни города, ни валюты,
# но по названию компании и так ясно, что оформление будет российским.
RUSSIA_COMPANY_MARKERS = [
    # Только однозначные названия. Слова, которые в русском языке живут своей
    # жизнью («лента», «магнит», «самокат», «купер», «барс», «вб»), сюда не
    # годятся: они дают ложные срабатывания на обычном тексте и молча съедают
    # нормальные вакансии.
    r"\bяндекс\w*\b", r"\byandex\b", r"\bсбер\w*\b", r"\bsber\w*\b",
    r"\bозон\b", r"\bozon\b", r"\bвконтакте\b", r"\bvk (?:team|tech|group|cloud)\b",
    r"\bmail\.?ru\b", r"\bwildberries\b", r"\bвайлдберриз\b",
    r"\bтиньк\w+\b", r"\btinkoff\b", r"\bт-?банк\b", r"\bавито\b", r"\bavito\b",
    r"\bмтс\b", r"\bmts\b", r"\bмегафон\w*\b", r"\bmegafon\b", r"\bбилайн\w*\b",
    r"\bростелеком\w*\b", r"\bальфа-?банк\w*\b", r"\bвтб\b", r"\bгазпром\w*\b",
    r"\bделимобиль\w*\b", r"\bциан\b", r"\bhh\.?ru\b", r"\bheadhunter\b",
    r"\bskyeng\b", r"\bскайэнг\b", r"\bskillbox\b", r"\bскилбокс\b",
    r"\bнетолог\w+\b", r"\bяндекс[- ]?практикум\w*\b", r"\bgeekbrains\b",
    r"\bсбермаркет\w*\b", r"\bбитрикс\w*\b",
    r"\bкасперск\w+\b", r"\bkaspersky\b", r"\bpositive technologies\b",
    r"\bселектел\b", r"\bselectel\b", r"\breg\.ru\b",
    r"\bржд\b", r"\bпочта россии\b", r"\bаэрофлот\w*\b", r"\bросатом\w*\b",
    r"\bиннополис\w*\b", r"\bдиасофт\b",
]

# Зарплата в рублях — сама по себе достаточный признак российского оформления.
RUBLE_SALARY_MARKERS = [
    # «т.р.» намеренно требует числа перед собой: голое «тр» встречается в
    # обычном тексте и раньше срабатывало впустую.
    r"₽", r"\bруб\w*\b", r"\brub\b", r"\d\s?т\.?\s?р\.?\b",
    r"\d\s?\d{0,3}\s?(?:000|к|k)\s*(?:руб|₽)", r"\bна руки\b",
]

# Локации, из которых удалённая работа обычно недоступна для меня, если
# в тексте стоит жёсткое «only». Помимо RUSSIA_RESIDENCY_MARKERS, тут — все
# остальные страны, для которых пока сделан только штраф баллов, не отказ.
GEO_RESTRICTION = [
    *RUSSIA_RESIDENCY_MARKERS,
    r"\bus only\b", r"\busa only\b", r"\bus-based\b", r"\bмust be (?:located|based) in\b",
    r"\bonly (?:in|from) (?:the )?(?:us|usa|uk|india|canada|latam)\b", r"\btolko\b",
    r"\bwork authorization\b",
    r"\bmust be authorized to work\b", r"\btimezone\b.{0,20}\b(?:pst|est|cst)\b",
    # «Philippine-based applicants», «US-based candidates only» и подобное.
    r"\b\w+-based (?:applicants|candidates|only)\b", r"\bapplicants? must (?:reside|be located)\b",
    r"\bresidents? of \w+ only\b", r"\bmust hold .{0,20}\bwork permit\b",
]

# ---------------------------------------------------------------------------
# Тип поста
# ---------------------------------------------------------------------------
# «Ищу работу» — самый частый мусор в фриланс-чатах. Ловим точные обороты,
# чтобы не задеть «ищем разработчика».
RESUME_MARKERS = [
    r"\bищу (?:работу|подработк\w+|вакансию|проект\w*|заказ\w*|команду|стажировк\w+)",
    r"\bи[щш]у удал[её]нн\w+ работ\w+", r"\bрассмотрю (?:предложения|вакансии|варианты|офферы)",
    r"\bоткрыт\w* (?:к предложениям|для предложений|to work)", r"\bopen to work\b",
    r"\blooking for (?:a )?(?:job|work|new opportunit|position)", r"\bseeking (?:a )?(?:job|position)\b",
    r"\bмо[её] резюме\b", r"\bмой стек\b", r"\bобо мне\b", r"\bмой опыт\b", r"\bmy cv\b",
    r"\bя (?:python|js|frontend|backend|fullstack)?\s*-?\s*разработчик\b", r"\bя разработчик\b",
    # Хэштеги: \b перед '#' никогда не сработает — '#' не входит в \w, и слева
    # от него в реальном тексте почти всегда пробел/начало строки, то есть
    # тоже не-\w. Такая пара «не-\w | не-\w» — это не граница слова для regex,
    # поэтому \b перед '#' молча не match'ился ни разу. Якорим только справа.
    r"#резюме\b", r"#ищу_?работу\b", r"#cv\b",
    r"\bавтор ищет\b", r"\bготов приступить\b",
    r"\bбуду рад(?:а)? (?:предложениям|офферам)\b", r"\bсвободен для (?:проектов|заказов)\b",
]

VACANCY_MARKERS = [
    r"\bваканси\w*", r"\bищем\b", r"\bи[щш]ем\b", r"\bтребуется\b", r"\bтребуются\b", r"\bнужен\b",
    r"\bнужна\b", r"\bнужны\b", r"\bприглашаем\b", r"\bв команду\b", r"\bнабираем\b",
    r"\bwe(?:'re| are)? (?:looking for|hiring|seeking)\b", r"\bhiring\b", r"\bjoin (?:our|the) team\b",
    r"\bwe need\b", r"\bposzukujemy\b", r"\bszukamy\b", r"\bzatrudni\w*", r"\bоткрыта позиц\w*",
    r"\bна проект нужен\b", r"\bдолжност\w*", r"\bобязанност\w*", r"\bусловия работы\b",
    r"\bчто мы предлагаем\b", r"\bwhat we offer\b", r"\bresponsibilities\b", r"\brequirements\b",
    r"\bмы предлагаем\b", r"\bо компании\b", r"\bapply\b", r"\bотклик\w*",
]

# Слабый маркер: одно название должности встречается и в вакансиях, и в
# самоописании резюме («Node.js разработчик с 4 годами опыта»). Держим
# отдельно от VACANCY_MARKERS, чтобы такое не перевешивало явные признаки
# резюме в classify_kind — но при этом всё ещё засчитывалось как вакансия,
# если резюме-маркеров не было вовсе.
VACANCY_TITLE_MARKERS = [
    r"\bdeveloper\b", r"\bengineer\b", r"\bразработчик\w*", r"\bпрограммист\w*", r"\bprogramist\w*",
]

# Обороты, которые встречаются почти исключительно в разовых заказах.
# Считаются с утроенным весом: одного такого достаточно, чтобы перевесить
# пару общих слов вроде «нужен разработчик», которые есть и в вакансиях.
GIG_STRONG_MARKERS = [
    r"\bбюджет\b", r"\bнужно (?:сделать|разработать|написать|доработать|создать|починить)\b",
    r"\bнадо (?:сделать|написать|доработать)\b", r"\bразов\w+ (?:задач|работ|проект|заказ)",
    r"\bпо тз\b", r"\bтехническое задание\b", r"\bищу (?:фрилансер|исполнител|подрядчик)\w*",
    r"\bнужен (?:фрилансер|исполнитель|подрядчик)\w*", r"\bоплата (?:за результат|сдельн\w+|по факту)\b",
    r"\bза проект\b", r"\bсрок(?:и)? (?:выполнения|сдачи)\b", r"\bдедлайн\w*",
    r"\bстоимость работ\w*", r"\bсколько будет стоить\b", r"\bпроектн\w+ работ\w*",
    r"\bна аутсорс\w*", r"\bподработк\w*", r"\bfixed price\b", r"\bза час работы\b",
]

# Обороты, характерные для постоянной работы в штате.
VACANCY_STRONG_MARKERS = [
    r"\bваканси\w*", r"\bв команду\b", r"\bк нам в команду\b", r"\bполн\w+ занятост\w*",
    r"\bграфик работы\b", r"\bоформлени\w+\b", r"\bтрудоустройств\w*",
    r"\bиспытательн\w+ срок\w*", r"\bсоцпакет\w*", r"\bотпуск\w*", r"\bоклад\w*",
    r"\bзаработн\w+ плат\w*", r"\bмы предлагаем\b", r"\bо компании\b",
    r"\bчто мы предлагаем\b", r"\bwhat we offer\b", r"\bbenefits\b",
    r"\bfull[- ]?time\b", r"\bwe are hiring\b", r"\bjoin (?:our|the) team\b",
]

GIG_MARKERS = [
    r"\bзаказ\b", r"\bнужно сделать\b", r"\bнадо сделать\b", r"\bтребуется доработ\w*",
    r"\bразов\w+ (?:задач|работ|проект)", r"\bбюджет\b", r"\bпо тз\b", r"\bтехническое задание\b",
    r"\bфрилансер\w*", r"\bfreelanc\w*", r"\bна аутсорс\w*", r"\bподработк\w*", r"\bза проект\b",
    r"\bоплата (?:по факту|за результат|сдельн\w+)", r"\bнужен специалист для\b", r"\bпочасов\w*",
]

# Реклама курсов, крипта, инфобизнес и прочий шум фриланс-чатов.
SPAM_MARKERS = [
    r"\bкурс\w* по\b", r"\bобучени\w+ с нуля\b", r"\bнаучу\b", r"\bнаставник\w*", r"\bментор\w*",
    r"\bгарантия трудоустройства\b", r"\bнабор на курс\b", r"\bбесплатный вебинар\b",
    r"\bинтенсив\b", r"\bмарафон\b", r"\bпройди тест\b", r"\bскидк\w+ \d+%",
    r"\bкриптовалют\w*", r"\bкрипт\w+ проект\b", r"\bбиткоин\w*", r"\btrading\b", r"\bтрейдинг\w*",
    r"\bставк\w+ на спорт\b", r"\bказино\b", r"\bинвестиц\w+ от\b", r"\bпассивный доход\b",
    r"\bзаработок от \d+", r"\bдоход от \d+ ?\$", r"\bсхема заработка\b", r"\bлегк\w+ деньги\b",
    r"\bреклам\w+ в канале\b", r"\bкупить подписчик\w*", r"\bнакрутк\w*", r"\bпродам аккаунт\w*",
    r"\bдропшиппинг\b", r"\bреферальн\w+ ссылк\w*", r"\bамбассадор\w*", r"\bразмещение рекламы\b",
    r"\bприглашаем в чат\b", r"\bподпишись\b", r"\bподписывайтесь\b",
]

# Вакансии не по профилю: не разработка вообще.
NON_DEV_MARKERS = [
    # Русскоязычные
    r"\bменеджер по продажам\b", r"\bsmm\w*", r"\bтаргетолог\w*",
    r"\bкопирайтер\w*", r"\bдизайнер\w*(?! ?/ ?(?:frontend|разработ))", r"\bмаркетолог\w*",
    r"\bрекрутер\w*", r"\bhr[- ]?менеджер\b", r"\bбухгалтер\w*", r"\bюрист\w*",
    r"\bмодератор\w*", r"\bоператор\w+ (?:call|чата)", r"\bкурьер\w*", r"\bводител\w*",
    r"\bпроджект[- ]?менеджер\w*", r"\bбизнес[- ]?аналитик\w*", r"\bтехподдержк\w*",
    r"\bмонтажер\w*", r"\bпереводчик\w*", r"\bмодел\w+ ?онлифанс\w*", r"\bассистент\w*",
    # Англоязычные: биржи забиты вакансиями саппорта и продаж
    r"\bcustomer (?:support|success|service)\b", r"\bsupport (?:consultant|agent|specialist)\b",
    r"\bsales (?:manager|representative|executive|development)\b", r"\baccount manager\b",
    r"\bproject manage\w*", r"\bproduct manage\w*", r"\bbusiness analyst\b",
    r"\boffice manage\w*", r"\bcommunity manage\w*", r"\bpeople partner\b",
    r"\brecruiter\b", r"\btalent acquisition\b", r"\bcontent writer\b", r"\bcopywriter\b",
    r"\bvirtual assistant\b", r"\bvideo editor\b", r"\bgraphic designer\b",
    r"\bsocial media manager\b", r"\bbookkeep\w*", r"\baccountant\b", r"\bparalegal\b",
    r"\bnurse\b", r"\bteacher\b", r"\btutor\b", r"\bdata entry\b", r"\bmoderator\b",
]

# ---------------------------------------------------------------------------
# Контакты
# ---------------------------------------------------------------------------
TG_USERNAME_RE = re.compile(r"(?<![\w/])@([a-zA-Z][a-zA-Z0-9_]{4,31})\b")
TG_LINK_RE = re.compile(r"(?:https?://)?t\.me/(?!c/|joinchat|\+)([a-zA-Z][a-zA-Z0-9_]{4,31})")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")
LINK_RE = re.compile(r"https?://[^\s<>\"')]+")

# Ники, которые не являются контактом работодателя.
CONTACT_BLACKLIST = {
    "channel", "telegram", "durov", "username", "example", "admin", "support",
    "everyone", "here", "all",
}


def compile_all(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE | re.UNICODE) for p in patterns]


def any_match(text: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(p.search(text) for p in patterns)


def count_matches(text: str, patterns: list[re.Pattern[str]]) -> int:
    return sum(1 for p in patterns if p.search(text))


# Предкомпиляция: регексов много, а вызывается это на каждом посте.
STACK_RE: dict[str, list[re.Pattern[str]]] = {k: compile_all(v) for k, v in STACK.items()}
LOCATIONS_RE: dict[str, list[re.Pattern[str]]] = {k: compile_all(v) for k, v in LOCATIONS.items()}
SENIORITY_RE: list[tuple[str, list[re.Pattern[str]]]] = [
    (name, compile_all(pats)) for name, pats in SENIORITY_PATTERNS
]

BACKEND_RE = compile_all(BACKEND_MARKERS)
FRONTEND_RE = compile_all(FRONTEND_MARKERS)
FULLSTACK_RE = compile_all(FULLSTACK_MARKERS)
DATA_RE = compile_all(DATA_MARKERS)
DEVOPS_RE = compile_all(DEVOPS_MARKERS)
MOBILE_RE = compile_all(MOBILE_MARKERS)
QA_RE = compile_all(QA_MARKERS)

REMOTE_RE = compile_all(REMOTE_MARKERS)
HYBRID_RE = compile_all(HYBRID_MARKERS)
ONSITE_RE = compile_all(ONSITE_MARKERS)
GEO_RESTRICTION_RE = compile_all(GEO_RESTRICTION)
RUSSIA_RESIDENCY_RE = compile_all(RUSSIA_RESIDENCY_MARKERS)
RUSSIA_COMPANY_RE = compile_all(RUSSIA_COMPANY_MARKERS)
RUBLE_SALARY_RE = compile_all(RUBLE_SALARY_MARKERS)

RESUME_RE = compile_all(RESUME_MARKERS)
VACANCY_RE = compile_all(VACANCY_MARKERS)
VACANCY_TITLE_RE = compile_all(VACANCY_TITLE_MARKERS)
GIG_RE = compile_all(GIG_MARKERS)
GIG_STRONG_RE = compile_all(GIG_STRONG_MARKERS)
VACANCY_STRONG_RE = compile_all(VACANCY_STRONG_MARKERS)
SPAM_RE = compile_all(SPAM_MARKERS)
NON_DEV_RE = compile_all(NON_DEV_MARKERS)


# ---------------------------------------------------------------------------
# Категории фриланс-заказов
# ---------------------------------------------------------------------------
# Порядок важен: категории проверяются сверху вниз, первое совпадение выигрывает.
# Поэтому узкие направления (боты, парсинг) стоят раньше широких (бэкенд, сайты) —
# «нужен бот на Python» должен стать ботом, а не бэкендом.
GIG_CATEGORIES: list[tuple[str, list[str]]] = [
    ("bots", [
        r"\bтелеграм[- ]?бот\w*", r"\btelegram bot\w*", r"\btg[- ]?бот\w*", r"\bчат[- ]?бот\w*",
        r"\bchatbot\w*", r"\bбот\w* (?:для|на|под)\b", r"\bнужен бот\b", r"\baiogram\b",
        r"\bбот в телеграм\w*", r"\bdiscord bot\b", r"\bwhatsapp бот\w*",
    ]),
    ("scraping", [
        r"\bпарсинг\w*", r"\bпарсер\w*", r"\bscrap(?:er|ing)\b", r"\bспарсить\b",
        r"\bавтоматизац\w+", r"\bautomation\b", r"\bсобрать данные\b", r"\bselenium\b",
        r"\bplaywright\b", r"\bbeautifulsoup\b", r"\bскрипт для сбора\b", r"\bмакрос\w*",
    ]),
    ("mobile", [
        r"\bмобильн\w+ приложени\w*", r"\bmobile app\b", r"\bios\b", r"\bandroid\b",
        r"\bflutter\b", r"\breact native\b", r"\bswift\b", r"\bkotlin\b", r"\bприложени\w+ под\b",
    ]),
    ("gamedev", [
        r"\bгеймдев\w*", r"\bgamedev\b", r"\bunity\b", r"\bunreal\b", r"\bгодот\b", r"\bgodot\b",
        r"\bигр\w+ (?:на|под|для)\b", r"\bразработка игр\w*", r"\bgame develop\w*",
    ]),
    ("data_ml", [
        r"\bмашинн\w+ обучени\w*", r"\bmachine learning\b", r"\bнейросет\w*", r"\bнейронн\w+ сет\w*",
        r"\bdata scien\w*", r"\bаналитик\w+ данн\w*", r"\bdata analys\w*", r"\bpandas\b",
        r"\btensorflow\b", r"\bpytorch\b", r"\bcomputer vision\b", r"\bllm\b", r"\bgpt\b",
        r"\bдашборд\w*", r"\bdashboard\b", r"\bpower bi\b", r"\btableau\b",
    ]),
    ("devops", [
        r"\bdevops\b", r"\bнастро\w+ сервер\w*", r"\bадминистрирован\w*", r"\bkubernetes\b",
        r"\bdocker\b", r"\bci/?cd\b", r"\bnginx\b", r"\bперенос\w* сайт\w* на сервер\b",
        r"\bvps\b", r"\bдеплой\w*", r"\bdeploy\w*", r"\bсисадмин\w*",
    ]),
    ("web", [
        r"\bсайт\w*", r"\bлендинг\w*", r"\blanding\b", r"\bвёрстк\w*", r"\bверстк\w*",
        r"\bwordpress\b", r"\bвордпресс\w*", r"\btilda\b", r"\bтильд\w*", r"\bbitrix\b",
        r"\bинтернет[- ]?магазин\w*", r"\becommerce\b", r"\bweb[- ]?разработ\w*",
        r"\bдоработ\w+ сайт\w*", r"\bадаптивн\w+ вёрстк\w*", r"\bhtml\b", r"\bcss\b",
    ]),
    ("backend", [
        r"\bбэкенд\w*", r"\bбекенд\w*", r"\bbackend\b", r"\bapi\b", r"\brest\b",
        r"\bмикросервис\w*", r"\bбаз[аыу] данных\b", r"\bdjango\b", r"\bfastapi\b",
        r"\bflask\b", r"\bnode\.?js\b", r"\bинтеграц\w+ (?:с|api)\b", r"\bcrm\b", r"\berp\b",
        r"\bсерверн\w+ част\w*", r"\bpython\b",
    ]),
    ("video", [
        r"\bмонтаж\w*", r"\bвидеомонтаж\w*", r"\bvideo edit\w*", r"\bпремьер\w*",
        r"\bpremiere pro\b", r"\bafter effects\b", r"\bдавинчи\b", r"\bdavinci\b",
        r"\bмоушн[- ]?дизайн\w*", r"\bmotion design\w*", r"\bанимац\w+ ролик\w*",
        r"\bсмонтировать\b", r"\bрилс\w*", r"\breels\b", r"\bshorts\b", r"\bвидеоролик\w*",
    ]),
    ("design", [
        r"\bдизайн\w*(?! ?/ ?(?:backend|бэкенд))", r"\bui/?ux\b", r"\bфигм\w*", r"\bfigma\b",
        r"\bлоготип\w*", r"\blogo\b", r"\bбрендбук\w*", r"\bфотошоп\w*", r"\bphotoshop\b",
        r"\bиллюстрац\w*", r"\bбаннер\w*", r"\bинфографик\w*", r"\bпрезентац\w+ дизайн\w*",
        r"\b3d[- ]?модел\w*", r"\bblender\b",
    ]),
    ("text", [
        r"\bкопирайт\w*", r"\bcopywrit\w*", r"\bрерайт\w*", r"\bнаписать (?:текст|статью)\b",
        r"\bстать[юи]\b", r"\bперевод\w*(?! денег)", r"\btranslat\w*", r"\bредактур\w*",
        r"\bконтент[- ]?менеджер\w*", r"\bнаполнени\w+ сайт\w*", r"\bтранскриб\w*",
        r"\bсценари\w+ для\b",
    ]),
    ("smm", [
        r"\bsmm\b", r"\bсмм\b", r"\bтаргет\w*", r"\bконтекстн\w+ реклам\w*", r"\bdirect\b",
        r"\bпродвижени\w*", r"\bseo\b", r"\bсео\b", r"\bведени\w+ (?:соцсет|инстаграм)\w*",
        r"\bинстаграм\w*", r"\binstagram\b", r"\bяндекс[- ]?директ\w*", r"\bреклам\w+ кампани\w*",
    ]),
    ("audio", [
        r"\bозвучк\w*", r"\bозвучива\w*", r"\bдиктор\w*", r"\bсведени\w+ (?:звук|трек)\w*",
        r"\bмастеринг\w*", r"\bаудиомонтаж\w*", r"\bподкаст\w*", r"\bvoice[- ]?over\b",
        r"\bмузык\w+ для\b", r"\bбит\w* для\b",
    ]),
]

GIG_CATEGORIES_RE: list[tuple[str, list[re.Pattern[str]]]] = [
    (name, compile_all(pats)) for name, pats in GIG_CATEGORIES
]


# ---------------------------------------------------------------------------
# Требуемый опыт в годах
# ---------------------------------------------------------------------------
# «3+ года коммерческого опыта» — самый честный признак того, что вакансия не
# для джуна, и он не всегда сопровождается словом Senior. Ловим число, чтобы
# сравнивать с порогом, а не угадывать по грейду.
EXPERIENCE_YEARS_PATTERNS = [
    # Русский: «от 3 лет», «3+ года», «не менее 2 лет», «минимум 5 лет»
    r"(?:опыт\w*|стаж\w*)[^.\n]{0,40}?(?:от|более|свыше|минимум|не менее)\s*(\d{1,2})\s*(?:\+\s*)?(?:год|лет|года)",
    r"(?:от|более|свыше|минимум|не менее)\s*(\d{1,2})\s*(?:\+\s*)?(?:год|лет|года)[^.\n]{0,30}?(?:опыт|стаж|коммерч|разработ)",
    r"(\d{1,2})\s*\+\s*(?:год|лет|года)[^.\n]{0,30}?(?:опыт|стаж|коммерч|разработ|программир)",
    r"(?:опыт\w*|стаж\w*)[^.\n]{0,25}?(\d{1,2})\s*\+?\s*(?:год|лет|года)",
    # Английский: «3+ years», «at least 5 years», «minimum of 2 years»
    r"(\d{1,2})\s*\+?\s*years?[^.\n]{0,30}?(?:experience|exp\b|background|working)",
    r"(?:experience|exp\b)[^.\n]{0,30}?(?:of\s*)?(\d{1,2})\s*\+?\s*years?",
    r"(?:at least|minimum(?:\s+of)?|no less than|over)\s*(\d{1,2})\s*\+?\s*years?",
    # «7+ years» — плюс сам по себе означает требование, слово experience рядом
    # стоит не всегда («5+ years building backend systems»).
    r"(\d{1,2})\s*\+\s*years?\b",
    # Польский
    r"(?:minimum|min\.?|co najmniej)\s*(\d{1,2})\s*(?:lat|lata|roku)",
    r"(\d{1,2})\s*\+?\s*(?:lat|lata)[^.\n]{0,25}?doświadcz",
]

# Диапазоны вида «2-4 years» / «2–4 года»: берём нижнюю границу, это порог входа.
EXPERIENCE_RANGE_PATTERNS = [
    r"(\d{1,2})\s*[-–—]\s*\d{1,2}\s*(?:год|лет|года|years?|lat|lata)",
]

EXPERIENCE_YEARS_RE = compile_all(EXPERIENCE_YEARS_PATTERNS)
EXPERIENCE_RANGE_RE = compile_all(EXPERIENCE_RANGE_PATTERNS)
