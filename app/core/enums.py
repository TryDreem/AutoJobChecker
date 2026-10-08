"""Справочники значений. Русские подписи живут здесь же, чтобы UI и БД не разъезжались."""

from __future__ import annotations

from enum import StrEnum


class SourceKind(StrEnum):
    """Тип источника. Новая биржа = новое значение + класс в app/sources/."""

    TELEGRAM = "telegram"
    JOBBOARD = "jobboard"
    RSS = "rss"
    UPWORK = "upwork"


class PostKind(StrEnum):
    """Что это за пост. Главный фильтр: резюме и шум нам не нужны."""

    JOB = "job"          # вакансия в штат
    GIG = "gig"          # разовый заказ / проект / подработка
    INTERNSHIP = "internship"
    RESUME = "resume"    # «ищу работу» — отбрасываем
    OTHER = "other"      # реклама, новости, флуд — отбрасываем

    @property
    def label(self) -> str:
        return {
            "job": "Вакансия",
            "gig": "Заказ",
            "internship": "Стажировка",
            "resume": "Резюме",
            "other": "Прочее",
        }[self.value]

    @property
    def emoji(self) -> str:
        return {
            "job": "💼",
            "gig": "🧩",
            "internship": "🎓",
            "resume": "📄",
            "other": "📌",
        }[self.value]


class Seniority(StrEnum):
    INTERN = "intern"
    JUNIOR = "junior"
    MIDDLE = "middle"
    SENIOR = "senior"
    LEAD = "lead"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            "intern": "Стажёр",
            "junior": "Junior",
            "middle": "Middle",
            "senior": "Senior",
            "lead": "Lead",
            "unknown": "Не указан",
        }[self.value]


class WorkMode(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            "remote": "Удалённо",
            "hybrid": "Гибрид",
            "onsite": "Офис",
            "unknown": "Не указано",
        }[self.value]

    @property
    def emoji(self) -> str:
        return {"remote": "🌍", "hybrid": "🏠", "onsite": "🏢", "unknown": "❓"}[self.value]


class Employment(StrEnum):
    FULLTIME = "fulltime"
    PARTTIME = "parttime"
    CONTRACT = "contract"
    FREELANCE = "freelance"
    INTERNSHIP = "internship"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            "fulltime": "Полная занятость",
            "parttime": "Частичная занятость",
            "contract": "Контракт / B2B",
            "freelance": "Фриланс",
            "internship": "Стажировка",
            "unknown": "Не указана",
        }[self.value]


class SalaryPeriod(StrEnum):
    HOUR = "hour"
    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    PROJECT = "project"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            "hour": "в час",
            "day": "в день",
            "month": "в месяц",
            "year": "в год",
            "project": "за проект",
            "unknown": "",
        }[self.value]


class UserStatus(StrEnum):
    """Реакция пользователя на вакансию. Используется и для обучения фильтра."""

    NEW = "new"
    SEEN = "seen"
    FAVORITE = "favorite"
    HIDDEN = "hidden"
    APPLIED = "applied"

    @property
    def label(self) -> str:
        return {
            "new": "Новое",
            "seen": "Просмотрено",
            "favorite": "В избранном",
            "hidden": "Скрыто",
            "applied": "Откликнулся",
        }[self.value]


# Роли, по которым бот умеет отличать «моё» от «не моё».
class Role(StrEnum):
    PYTHON_BACKEND = "python_backend"
    BACKEND = "backend"
    FRONTEND = "frontend"
    FULLSTACK = "fullstack"
    JS_NODE = "js_node"
    DATA_ML = "data_ml"
    DEVOPS = "devops"
    MOBILE = "mobile"
    QA = "qa"
    OTHER_ROLE = "other"

    @property
    def label(self) -> str:
        return {
            "python_backend": "Python Backend",
            "backend": "Backend",
            "frontend": "Frontend",
            "fullstack": "Fullstack",
            "js_node": "JS / Node.js",
            "data_ml": "Data / ML",
            "devops": "DevOps",
            "mobile": "Mobile",
            "qa": "QA",
            "other": "Другое",
        }[self.value]

    @property
    def emoji(self) -> str:
        return {
            "python_backend": "🐍",
            "backend": "⚙️",
            "frontend": "🎨",
            "fullstack": "🧬",
            "js_node": "🟨",
            "data_ml": "📊",
            "devops": "🛠",
            "mobile": "📱",
            "qa": "🔍",
            "other": "💻",
        }[self.value]


class GigCategory(StrEnum):
    """Направление разового заказа.

    Во фриланс-каналах вперемешку идут заказы на код, монтаж, дизайн и тексты.
    Роли (Role) для них не годятся: они описывают позицию разработчика, а тут
    нужно грубое деление «чем вообще предлагают заняться», чтобы можно было
    отключить всё нерелевантное одной кнопкой.
    """

    BACKEND = "backend"
    WEB = "web"
    BOTS = "bots"
    SCRAPING = "scraping"
    MOBILE = "mobile"
    DATA_ML = "data_ml"
    GAMEDEV = "gamedev"
    DEVOPS = "devops"
    DESIGN = "design"
    VIDEO = "video"
    TEXT = "text"
    SMM = "smm"
    AUDIO = "audio"
    OTHER = "other"

    @property
    def label(self) -> str:
        return {
            "backend": "Бэкенд / API",
            "web": "Сайты и вёрстка",
            "bots": "Боты",
            "scraping": "Парсинг / автоматизация",
            "mobile": "Мобильные приложения",
            "data_ml": "Данные / ML",
            "gamedev": "Геймдев",
            "devops": "Серверы / DevOps",
            "design": "Дизайн",
            "video": "Видеомонтаж",
            "text": "Тексты и переводы",
            "smm": "SMM и реклама",
            "audio": "Звук и озвучка",
            "other": "Прочее",
        }[self.value]

    @property
    def emoji(self) -> str:
        return {
            "backend": "⚙️",
            "web": "🌐",
            "bots": "🤖",
            "scraping": "🕸",
            "mobile": "📱",
            "data_ml": "📊",
            "gamedev": "🎮",
            "devops": "🛠",
            "design": "🎨",
            "video": "🎬",
            "text": "✍️",
            "smm": "📣",
            "audio": "🎧",
            "other": "📦",
        }[self.value]

    @classmethod
    def coding(cls) -> list["GigCategory"]:
        """Категории «про код» — то, что подходит Python/JS-разработчику."""
        return [cls.BACKEND, cls.WEB, cls.BOTS, cls.SCRAPING, cls.MOBILE,
                cls.DATA_ML, cls.GAMEDEV, cls.DEVOPS]
