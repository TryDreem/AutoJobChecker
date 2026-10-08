"""Извлечение структуры из свободного текста поста.

Никакой магии: регулярки плюс словари из keywords.py. Задача — вытащить то,
по чему потом считается релевантность и что показывается в карточке.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.core.enums import (
    Employment,
    GigCategory,
    PostKind,
    Role,
    SalaryPeriod,
    Seniority,
    WorkMode,
)
from app.pipeline import keywords as kw
from app.pipeline.text import normalize_for_match

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Деньги
# ---------------------------------------------------------------------------
CURRENCIES = {
    "$": "USD", "usd": "USD", "долл": "USD", "доллар": "USD", "бакс": "USD", "us$": "USD",
    "€": "EUR", "eur": "EUR", "евро": "EUR",
    "zł": "PLN", "zl": "PLN", "pln": "PLN", "злот": "PLN", "злотых": "PLN",
    "₽": "RUB", "rub": "RUB", "руб": "RUB", "рублей": "RUB",
    "₴": "UAH", "uah": "UAH", "грн": "UAH", "гривен": "UAH",
    "£": "GBP", "gbp": "GBP", "фунт": "GBP",
    "₸": "KZT", "kzt": "KZT", "тенге": "KZT",
}

# Приблизительные курсы к доллару. Точность здесь не нужна — цифра служит
# только для сортировки и порога «не меньше N», а не для бухгалтерии.
USD_RATES = {
    "USD": 1.0, "EUR": 1.08, "GBP": 1.27, "PLN": 0.25,
    "RUB": 0.011, "UAH": 0.024, "KZT": 0.002,
    # Валюты, в которых иногда публикуют вилки зарубежные борды.
    "CAD": 0.73, "AUD": 0.66, "INR": 0.012, "BRL": 0.18,
    "PHP": 0.017, "ZAR": 0.055, "CZK": 0.043, "TRY": 0.029,
}

# Множители перевода в «примерно за месяц».
PERIOD_TO_MONTH = {
    SalaryPeriod.HOUR: 160.0,   # ~40 часов в неделю
    SalaryPeriod.DAY: 21.0,
    SalaryPeriod.MONTH: 1.0,
    SalaryPeriod.YEAR: 1 / 12,
}

_NUM = r"\d{1,3}(?:[  .,]\d{3})+|\d+(?:[.,]\d+)?"
_CUR_CHARS = r"[$€£₽₴₸]"
_CUR_WORDS = r"usd|eur|pln|zł|zl|rub|uah|kzt|gbp|евро|доллар\w*|долл\w*|руб\w*|злот\w*|грн\w*|тенге|бакс\w*"
_KILO = r"\s*(?:k\b|к\b|тыс\.?\w*|тысяч\w*)"

MONEY_RE = re.compile(
    rf"(?P<cur_pre>{_CUR_CHARS}|\b(?:{_CUR_WORDS})\b)?\s*"
    rf"(?P<lo>{_NUM})(?P<k_lo>{_KILO})?"
    # Во второй части вилки валюта часто повторяется: «$120,000 - $150,000».
    rf"(?:\s*(?:-|–|—|\.\.|\bдо\b|\bto\b|\bи\b)\s*(?:{_CUR_CHARS}|\b(?:{_CUR_WORDS})\b)?\s*"
    rf"(?P<hi>{_NUM})(?P<k_hi>{_KILO})?)?"
    rf"\s*(?P<cur_post>{_CUR_CHARS}|\b(?:{_CUR_WORDS})\b)?",
    re.IGNORECASE | re.UNICODE,
)

PERIOD_PATTERNS: list[tuple[SalaryPeriod, re.Pattern[str]]] = [
    (SalaryPeriod.HOUR, re.compile(r"/\s*(?:ч|час|h|hr|hour)\b|\bв час\b|\bза час\b|\bper hour\b"
                                   r"|\bhourly\b|\bчасов\w+ ставк\w+|\bgodzin\w*", re.I)),
    (SalaryPeriod.DAY, re.compile(r"/\s*(?:д|день|day)\b|\bв день\b|\bза день\b|\bper day\b"
                                  r"|\bdaily rate\b", re.I)),
    (SalaryPeriod.MONTH, re.compile(r"/\s*(?:мес\w*|month|mo)\b|\bв месяц\b|\bза месяц\b"
                                    r"|\bper month\b|\bmonthly\b|\bmiesi[ęe]cznie\b|\bна руки\b", re.I)),
    (SalaryPeriod.YEAR, re.compile(r"/\s*(?:год|year|yr|annum)\b|\bв год\b|\bper year\b"
                                   r"|\bannual\w*\b|\bгодов\w+ доход\b", re.I)),
    (SalaryPeriod.PROJECT, re.compile(r"\bза проект\b|\bper project\b|\bза работу\b|\bfixed price\b"
                                      r"|\bза весь проект\b|\bединоразов\w*", re.I)),
]

# Слова, рядом с которыми число почти наверняка означает деньги.
PAY_CONTEXT_RE = re.compile(
    r"\bз/?п\b|\bзарплат\w*|\bоплат\w*|\bставк\w*|\bбюджет\w*|\bгонорар\w*|\bвилк\w*|\bдоход\w*"
    r"|\bsalary\b|\bbudget\b|\bcompensation\b|\brate\b|\bpay\b|\bwynagrodzeni\w*|\bплатим\b"
    r"|\bплачу\b|\bот\s|\bearn\b|\bpayment\b",
    re.IGNORECASE | re.UNICODE,
)

# Числа, которые ловятся регуляркой, но деньгами не являются.
NOT_MONEY_RE = re.compile(
    r"\bлет\b|\bгод(?:а|ов)?\b|\byears?\b|\bопыт\w*|\bexperience\b|\bчасов в неделю\b"
    r"|\bhours? per week\b|\bсотрудник\w*|\bчеловек\b|\bпроцент\w*|\b%|\bверси\w*|\bv?\d\.\d+\b",
    re.IGNORECASE | re.UNICODE,
)


@dataclass(slots=True)
class Salary:
    min: float | None = None
    max: float | None = None
    currency: str = ""
    period: SalaryPeriod = SalaryPeriod.UNKNOWN
    raw: str = ""

    @property
    def usd_month(self) -> float | None:
        """Грубая нормализация «сколько это в долларах за месяц»."""
        value = self.max or self.min
        if not value or not self.currency:
            return None
        rate = USD_RATES.get(self.currency)
        multiplier = PERIOD_TO_MONTH.get(self.period)
        if rate is None or multiplier is None:
            return None
        return round(value * rate * multiplier, 2)


def _to_number(raw: str, kilo: str | None) -> float | None:
    """«12 500», «12,5k», «1.500» -> число."""
    if not raw:
        return None
    cleaned = raw.replace(" ", "").replace(" ", "")
    # Разделитель тысяч vs десятичная точка: «1.500» — это 1500, «1.5» — 1.5.
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif cleaned.count(",") == 1 and len(cleaned.split(",")[-1]) <= 2:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    if cleaned.count(".") == 1 and len(cleaned.split(".")[-1]) == 3:
        cleaned = cleaned.replace(".", "")

    try:
        value = float(cleaned)
    except ValueError:
        return None
    if kilo:
        value *= 1000
    return value


def _currency_of(token: str | None) -> str:
    if not token:
        return ""
    key = token.strip().lower()
    if key in CURRENCIES:
        return CURRENCIES[key]
    for prefix, code in CURRENCIES.items():
        if key.startswith(prefix):
            return code
    return ""


def _period_near(text: str, start: int, end: int) -> SalaryPeriod:
    """Ищет указание периода в окне вокруг найденной суммы."""
    window = text[max(0, start - 40) : min(len(text), end + 40)]
    for period, pattern in PERIOD_PATTERNS:
        if pattern.search(window):
            return period
    return SalaryPeriod.UNKNOWN


def extract_salary(text: str) -> Salary:
    """Достаёт вилку из текста. Выбирает самого «уверенного» кандидата."""
    if not text:
        return Salary()

    best: Salary | None = None
    best_weight = -1.0

    for match in MONEY_RE.finditer(text):
        lo = _to_number(match.group("lo"), match.group("k_lo"))
        if lo is None or lo <= 0:
            continue
        hi = _to_number(match.group("hi"), match.group("k_hi"))
        currency = _currency_of(match.group("cur_pre")) or _currency_of(match.group("cur_post"))

        around = text[max(0, match.start() - 45) : match.end() + 45]
        if NOT_MONEY_RE.search(around) and not currency:
            continue

        has_context = bool(PAY_CONTEXT_RE.search(around))
        # Без валюты верим числу, только если рядом явно говорят про оплату
        # и сумма правдоподобная.
        if not currency and not (has_context and lo >= 100):
            continue

        period = _period_near(text, match.start(), match.end())

        # Чем больше признаков, тем выше доверие: валюта > период > диапазон.
        weight = 0.0
        weight += 3.0 if currency else 0.0
        weight += 2.0 if period is not SalaryPeriod.UNKNOWN else 0.0
        weight += 1.5 if hi else 0.0
        weight += 1.0 if has_context else 0.0

        if weight > best_weight:
            best_weight = weight
            best = Salary(
                min=lo,
                max=hi,
                currency=currency,
                period=period,
                raw=match.group(0).strip(),
            )

    if best is None:
        return Salary()

    # Часовая ставка в долларах редко бывает четырёхзначной — скорее это месяц.
    if best.period is SalaryPeriod.UNKNOWN and best.currency:
        amount = best.max or best.min or 0
        if best.currency in {"USD", "EUR", "GBP"}:
            best.period = SalaryPeriod.MONTH if amount >= 400 else SalaryPeriod.HOUR
        else:
            best.period = SalaryPeriod.MONTH

    if best.min and best.max and best.min > best.max:
        best.min, best.max = best.max, best.min
    return best


# ---------------------------------------------------------------------------
# Технологии и роли
# ---------------------------------------------------------------------------
def extract_stack(text: str) -> list[str]:
    return [name for name, patterns in kw.STACK_RE.items() if kw.any_match(text, patterns)]


def extract_roles(text: str, stack: list[str], title: str = "") -> list[str]:
    """Определяет специализацию.

    Заголовок весит больше остального текста: «Site Reliability Engineer» —
    это SRE, даже если дальше в требованиях перечислен Python. Поэтому узкие
    роли (DevOps, QA, Mobile, Data) сначала проверяются по заголовку и, если
    найдены, вытесняют backend — иначе любая вакансия с Python в требованиях
    выглядела бы как Python backend.
    """
    stack_set = set(stack)
    has_python = bool(stack_set & kw.PYTHON_STACK)
    has_js = bool(stack_set & kw.JS_STACK)

    headline = normalize_for_match(title) if title else ""
    scope = headline or text

    is_devops = kw.any_match(scope, kw.DEVOPS_RE)
    is_qa = kw.any_match(scope, kw.QA_RE)
    is_mobile = kw.any_match(scope, kw.MOBILE_RE)
    is_data = kw.any_match(scope, kw.DATA_RE)

    is_backend = kw.any_match(text, kw.BACKEND_RE)
    is_frontend = kw.any_match(text, kw.FRONTEND_RE)
    is_fullstack = kw.any_match(text, kw.FULLSTACK_RE)

    roles: list[str] = []

    # Узкая роль в заголовке — решающая: разработчиком её уже не считаем.
    narrow = is_devops or is_qa or is_mobile
    if is_devops:
        roles.append(Role.DEVOPS)
    if is_qa:
        roles.append(Role.QA)
    if is_mobile:
        roles.append(Role.MOBILE)
    if is_data or {"ML", "PyTorch"} & stack_set:
        roles.append(Role.DATA_ML)

    if not narrow:
        if is_fullstack:
            roles.append(Role.FULLSTACK)
        if has_python and (is_backend or not (is_frontend or is_fullstack)):
            roles.append(Role.PYTHON_BACKEND)
        if has_js and not is_fullstack:
            roles.append(Role.JS_NODE)
        if is_backend and Role.PYTHON_BACKEND not in roles:
            roles.append(Role.BACKEND)
        if is_frontend and not is_fullstack:
            roles.append(Role.FRONTEND)

    if not roles:
        roles.append(Role.OTHER_ROLE)
    # dict.fromkeys — уникальные значения с сохранением порядка.
    return list(dict.fromkeys(str(r) for r in roles))


def extract_required_years(text: str) -> int | None:
    """Сколько лет опыта требует вакансия. None — требование не названо.

    Это честнее грейда: «Senior» пишут не всегда, а «3+ years of experience»
    в описании стоит почти везде, и для джуна такая вакансия закрыта
    независимо от того, как названа позиция.

    Из нескольких упоминаний берём максимальное: если просят «2+ года Python
    и 5+ лет разработки», планка — пять.
    """
    years: list[int] = []

    # Диапазоны разбираем первыми и запоминаем их границы: внутри «2-4 years»
    # обычные шаблоны увидели бы верхнее число и завысили требование, хотя
    # порог входа здесь — нижняя граница.
    range_spans: list[tuple[int, int]] = []
    for pattern in kw.EXPERIENCE_RANGE_RE:
        for match in pattern.finditer(text):
            try:
                value = int(match.group(1))
            except (ValueError, IndexError):
                continue
            range_spans.append(match.span())
            if 1 <= value <= 15:
                years.append(value)

    def inside_range(position: int) -> bool:
        """Попадает ли найденное число внутрь уже разобранного диапазона.

        Сверяем именно позицию числа, а не всего совпадения: шаблон вида
        «4 years of experience» стартует внутри «2-4 years of experience»,
        но заканчивается за его пределами.
        """
        return any(start <= position < end for start, end in range_spans)

    for pattern in kw.EXPERIENCE_YEARS_RE:
        for match in pattern.finditer(text):
            if inside_range(match.start(1)):
                continue
            try:
                value = int(match.group(1))
            except (ValueError, IndexError):
                continue
            # Отсекаем мусор: «20 years» в рассказе о компании и опечатки.
            if 1 <= value <= 15:
                years.append(value)

    return max(years) if years else None


# Насколько близко должны стоять два грейда, чтобы считать их диапазоном
# («Junior/Middle», «Middle+ / Senior»), а не отдельными упоминаниями.
_RANGE_DISTANCE = 30


def extract_seniority(text: str, hint: str = "") -> Seniority:
    """Грейд. Подсказка от биржи имеет приоритет — она структурная.

    Когда в вакансии стоит диапазон («Junior/Middle»), берём нижнюю границу:
    это порог входа, и для джуна такая вакансия подходит. Раньше побеждал
    старший грейд просто потому, что стоял раньше в списке шаблонов, и
    вакансии «Junior/Middle» отсеивались фильтром по уровню.

    Если же грейды разбросаны по тексту далеко друг от друга (например,
    «Senior-разработчик... будете менторить junior-ов»), диапазоном это не
    считаем и оставляем старший — требование там именно к сеньору.
    """
    haystack = f"{normalize_for_match(hint)} {text}" if hint else text

    # Ранг совпадает с порядком в SENIORITY_RE: 0 — самый старший.
    found: list[tuple[int, str, int]] = []  # (ранг, грейд, позиция)
    for rank, (name, patterns) in enumerate(kw.SENIORITY_RE):
        position = min(
            (m.start() for p in patterns if (m := p.search(haystack))),
            default=None,
        )
        if position is not None:
            found.append((rank, name, position))

    if not found:
        return Seniority.UNKNOWN

    senior_most = min(found, key=lambda item: item[0])
    junior_most = max(found, key=lambda item: item[0])

    if senior_most is not junior_most:
        if abs(senior_most[2] - junior_most[2]) <= _RANGE_DISTANCE:
            return Seniority(junior_most[1])

    return Seniority(senior_most[1])


def extract_work_mode(text: str, is_remote_hint: bool | None = None) -> WorkMode:
    """Формат работы. Гибрид проверяем первым: он содержит слово «удалённо»."""
    if kw.any_match(text, kw.HYBRID_RE):
        return WorkMode.HYBRID
    if kw.any_match(text, kw.REMOTE_RE):
        return WorkMode.REMOTE
    if kw.any_match(text, kw.ONSITE_RE):
        return WorkMode.ONSITE
    if is_remote_hint:
        return WorkMode.REMOTE
    return WorkMode.UNKNOWN


def extract_gig_category(text: str) -> GigCategory:
    """Направление заказа: код, монтаж, дизайн, тексты и т.д.

    Первое совпадение по порядку из GIG_CATEGORIES выигрывает — узкие
    направления там стоят раньше широких, чтобы «бот на Python» не
    засчитывался как обычный бэкенд.
    """
    for name, patterns in kw.GIG_CATEGORIES_RE:
        if kw.any_match(text, patterns):
            return GigCategory(name)
    return GigCategory.OTHER


def extract_locations(text: str, known: list[str] | None = None) -> list[str]:
    found = [name for name, patterns in kw.LOCATIONS_RE.items() if kw.any_match(text, patterns)]
    for item in known or []:
        cleaned = item.strip()
        if cleaned and cleaned not in found and len(cleaned) < 64:
            found.append(cleaned)
    return found


def extract_employment(text: str, hint: str = "") -> Employment:
    haystack = f"{normalize_for_match(hint)} {text}" if hint else text
    # Без жёсткой границы «intern» ловит «international» и «internal» —
    # так вакансия техподдержки превращалась в стажировку.
    if re.search(r"\bintern(?:ship)?s?\b|\bстажировк\w*|\bстажер\w*|\bstaż\w*", haystack):
        return Employment.INTERNSHIP
    if re.search(r"\bfreelance\b|\bфриланс\w*|\bразов\w+|\bпо задачам\b|\bgig\b", haystack):
        return Employment.FREELANCE
    if re.search(r"\bb2b\b|\bконтракт\w*|\bcontract\b|\bподряд\w*|\bиндивидуальн\w+ предприн", haystack):
        return Employment.CONTRACT
    if re.search(r"\bпарт-?тайм\b|\bpart[- ]?time\b|\bчастичн\w+ занятост\w*|\bподработк\w*", haystack):
        return Employment.PARTTIME
    if re.search(r"\bполн\w+ занятост\w*|\bfull[- ]?time\b|\bфул[- ]?тайм\b|\bштат\b", haystack):
        return Employment.FULLTIME
    return Employment.UNKNOWN


# ---------------------------------------------------------------------------
# Контакты
# ---------------------------------------------------------------------------
def extract_contacts(raw_text: str, author: str = "") -> dict[str, list[str]]:
    """Собирает, куда писать. Автор поста идёт первым — он важнее ников из текста."""
    telegram: list[str] = []
    if author.startswith("@"):
        telegram.append(author)

    for match in kw.TG_USERNAME_RE.finditer(raw_text):
        nick = match.group(1)
        if nick.lower() not in kw.CONTACT_BLACKLIST:
            telegram.append(f"@{nick}")
    for match in kw.TG_LINK_RE.finditer(raw_text):
        nick = match.group(1)
        if nick.lower() not in kw.CONTACT_BLACKLIST:
            telegram.append(f"@{nick}")

    emails = kw.EMAIL_RE.findall(raw_text)
    links = [u for u in kw.LINK_RE.findall(raw_text) if "t.me/" not in u]

    return {
        "telegram": list(dict.fromkeys(telegram))[:5],
        "email": list(dict.fromkeys(emails))[:3],
        "links": list(dict.fromkeys(links))[:5],
    }


# ---------------------------------------------------------------------------
# Тип поста
# ---------------------------------------------------------------------------
def classify_kind(
    text: str,
    employment: Employment,
    *,
    has_salary: bool = False,
    has_stack: bool = False,
) -> tuple[PostKind, list[str]]:
    """Отличает вакансию от заказа, резюме и рекламы.

    Возвращает тип и короткие пояснения — они попадают в карточку, чтобы
    было видно, почему бот решил именно так.
    """
    reasons: list[str] = []

    resume_hits = kw.count_matches(text, kw.RESUME_RE)
    vacancy_hits = kw.count_matches(text, kw.VACANCY_RE)
    # Название должности («developer», «разработчик») отдельно от остальных
    # признаков вакансии: та же фраза — обычное дело в самоописании резюме
    # («Node.js разработчик с 4 годами опыта»), поэтому в спор резюме/вакансия
    # эти совпадения не идут, а как fallback-признак вакансии — всё ещё идут.
    vacancy_title_hits = kw.count_matches(text, kw.VACANCY_TITLE_RE)
    gig_hits = kw.count_matches(text, kw.GIG_RE)
    spam_hits = kw.count_matches(text, kw.SPAM_RE)

    # Резюме почти всегда содержит «ищу работу» и почти не содержит
    # «обязанности/условия» — на этой асимметрии и строим решение.
    if resume_hits and resume_hits >= vacancy_hits:
        reasons.append("похоже на резюме («ищу работу»)")
        return PostKind.RESUME, reasons

    if spam_hits >= 2 and vacancy_hits == 0:
        reasons.append("реклама/инфобизнес")
        return PostKind.OTHER, reasons

    if kw.any_match(text, kw.NON_DEV_RE) and vacancy_hits and not gig_hits:
        # Не-разработческая вакансия: отсеиваем, только если про код ни слова.
        if not kw.any_match(text, kw.BACKEND_RE) and not kw.any_match(text, kw.FRONTEND_RE):
            reasons.append("вакансия не по разработке")
            return PostKind.OTHER, reasons

    if employment is Employment.INTERNSHIP:
        reasons.append("стажировка")
        return PostKind.INTERNSHIP, reasons

    # Заказ и вакансия делят почти весь словарь («нужен разработчик» бывает и
    # там, и там), поэтому решает не количество совпадений, а их вес: обороты
    # вроде «бюджет» или «по ТЗ» встречаются практически только в заказах,
    # а «оформление», «полная занятость» — только в вакансиях.
    gig_weight = gig_hits + 3 * kw.count_matches(text, kw.GIG_STRONG_RE)
    vacancy_weight = (
        vacancy_hits
        + 3 * kw.count_matches(text, kw.VACANCY_STRONG_RE)
        # Название должности само по себе почти ничего не говорит.
        + 0.5 * vacancy_title_hits
    )
    if employment is Employment.FREELANCE:
        gig_weight += 2

    if gig_weight and gig_weight > vacancy_weight:
        reasons.append("разовый заказ / проект")
        return PostKind.GIG, reasons

    if vacancy_weight >= 1:
        reasons.append("вакансия")
        return PostKind.JOB, reasons

    if gig_hits >= 1:
        reasons.append("похоже на заказ")
        return PostKind.GIG, reasons

    # Ни одного маркера, но есть вилка и технологии — так выглядят короткие
    # объявления вида «Зарплата 200–300к, Python, удалённо».
    if has_salary and has_stack:
        reasons.append("вакансия по косвенным признакам (вилка + стек)")
        return PostKind.JOB, reasons

    reasons.append("непонятный тип поста")
    return PostKind.OTHER, reasons


# ---------------------------------------------------------------------------
# Сборка всего вместе
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Extracted:
    kind: PostKind
    roles: list[str]
    stack: list[str]
    seniority: Seniority
    employment: Employment
    work_mode: WorkMode
    locations: list[str]
    salary: Salary
    contacts: dict[str, list[str]]
    gig_category: GigCategory = GigCategory.OTHER
    required_years: int | None = None

    # Метрики качества заказа. Из текста их не вытащить — приходят от площадки
    # (Upwork и подобные) и проставляются процессором поверх разбора.
    proposals_min: int | None = None
    proposals_max: int | None = None
    client_payment_verified: bool | None = None
    client_rating: float | None = None
    client_reviews: int | None = None
    client_spent: float | None = None
    client_hires: int | None = None
    client_country: str = ""
    experience_level: str = ""

    @property
    def proposals_label(self) -> str:
        """Человекочитаемое число откликов: «20–50», «до 5», «12»."""
        low, high = self.proposals_min, self.proposals_max
        if low is not None and high is not None:
            return f"{low}–{high}" if low != high else str(low)
        if high is not None:
            return f"до {high}"
        return str(low) if low is not None else "?"
    geo_restricted: bool = False
    # Явное требование резидентства/гражданства РФ («только для граждан РФ»,
    # «оформление по ГПХ», «только Москва/СПб»). Единственная гео-метка,
    # из-за которой пост отбрасывается жёстко — см. scoring.py.
    russia_restricted: bool = False
    notes: list[str] = field(default_factory=list)


def extract_all(
    raw_text: str,
    normalized: str,
    *,
    title: str = "",
    author: str = "",
    seniority_hint: str = "",
    employment_hint: str = "",
    known_locations: list[str] | None = None,
    is_remote_hint: bool | None = None,
) -> Extracted:
    """Единая точка входа: сырой текст -> все поля, которые бот умеет понимать."""
    stack = extract_stack(normalized)
    employment = extract_employment(normalized, employment_hint)
    salary = extract_salary(raw_text)
    kind, notes = classify_kind(
        normalized,
        employment,
        has_salary=salary.min is not None,
        has_stack=bool(stack),
    )

    return Extracted(
        kind=kind,
        roles=extract_roles(normalized, stack, title),
        stack=stack,
        seniority=extract_seniority(normalized, seniority_hint),
        employment=employment,
        work_mode=extract_work_mode(normalized, is_remote_hint),
        locations=extract_locations(normalized, known_locations),
        salary=salary,
        contacts=extract_contacts(raw_text, author),
        gig_category=extract_gig_category(normalized),
        required_years=extract_required_years(normalized),
        geo_restricted=kw.any_match(normalized, kw.GEO_RESTRICTION_RE),
        russia_restricted=(
            kw.any_match(normalized, kw.RUSSIA_RESIDENCY_RE)
            or kw.any_match(normalized, kw.RUSSIA_COMPANY_RE)
            # Рубли в вилке — российское оформление, даже если ни город,
            # ни компания в тексте не названы.
            or salary.currency == "RUB"
            or kw.any_match(normalized, kw.RUBLE_SALARY_RE)
        ),
        notes=notes,
    )
