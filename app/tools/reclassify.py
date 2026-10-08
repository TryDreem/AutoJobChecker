"""Полный пересчёт уже сохранённых постов под текущие правила.

Нужен после правок в keywords.py / extractors.py / scoring.py: новый код
применяется только к тому, что придёт дальше, а лежащее в базе остаётся с
прежней разметкой — дедуп по external_id не даст источнику отдать тот же
пост повторно.

Пересчитывает тип поста, направление заказа, географию и итоговую оценку.
Ничего не удаляет: посты, не прошедшие фильтры, получают score = 0 и просто
перестают попадать в ленту.

Запуск:
    python -m app.tools.reclassify
"""

from __future__ import annotations

import asyncio
from collections import Counter

from sqlalchemy import select

from app.core.db import session_scope
from app.core.enums import GigCategory, PostKind
from app.core.models import Vacancy
from app.pipeline.extractors import extract_all
from app.pipeline.scoring import score_post
from app.pipeline.text import normalize_for_match


async def main() -> int:
    print()
    print("═" * 66)
    print("  Пересчёт базы под текущие правила")
    print("═" * 66)
    print()

    async with session_scope() as session:
        rows = (await session.execute(select(Vacancy))).scalars().all()
        print(f"Всего постов в базе: {len(rows)}")
        print()

        kind_changes = 0
        newly_hidden = 0
        restored = 0
        reject_reasons: Counter[str] = Counter()

        for vacancy in rows:
            raw_text = vacancy.raw_text or vacancy.clean_text or ""
            # Тот же состав текста, что и при первичной обработке в processor.py.
            searchable = "\n".join(
                p for p in (vacancy.title, vacancy.company, raw_text) if p
            )
            normalized = normalize_for_match(searchable)

            data = extract_all(
                raw_text,
                normalized,
                title=vacancy.title,
                author=vacancy.author,
                known_locations=list(vacancy.locations or []),
            )
            result = score_post(
                data,
                posted_at=vacancy.posted_at,
                text_length=len(raw_text),
                normalized_text=normalized,
            )

            try:
                old_kind = PostKind(vacancy.kind)
            except ValueError:
                old_kind = PostKind.OTHER
            was_visible = vacancy.score > 0

            if data.kind is not old_kind:
                kind_changes += 1
            vacancy.kind = data.kind
            vacancy.gig_category = str(data.gig_category)
            vacancy.required_years = data.required_years
            vacancy.locations = data.locations
            vacancy.score = result.score
            vacancy.score_reasons = result.reasons

            if result.rejected:
                reject_reasons[result.reject_reason or "не прошёл фильтры"] += 1
                if was_visible:
                    newly_hidden += 1
                    print(f"  🔻 #{vacancy.id:<5} {result.reject_reason:<32} {vacancy.title[:44]}")
            elif not was_visible and result.score > 0:
                restored += 1

        # --- Итоги -------------------------------------------------------
        visible = [v for v in rows if v.score > 0]
        by_kind = Counter(str(v.kind) for v in visible)
        by_category = Counter(
            str(v.gig_category) for v in visible if str(v.kind) == str(PostKind.GIG)
        )

        print()
        print("─" * 66)
        print(f"Сменили тип: {kind_changes} · скрыто новыми правилами: {newly_hidden}"
              f" · вернулось в ленту: {restored}")
        print()
        print("Причины отсева:")
        for reason, count in reject_reasons.most_common(10):
            print(f"  {count:>4}  {reason}")
        print()
        print("Осталось видимым по типам:")
        for kind, count in by_kind.most_common():
            print(f"  {count:>4}  {PostKind(kind).label}")
        if by_category:
            print()
            print("Заказы по направлениям:")
            for cat, count in by_category.most_common():
                print(f"  {count:>4}  {GigCategory(cat).label}")
        print("─" * 66)

    print()
    print("✅ Готово.")
    print()
    return 0


if __name__ == "__main__":
    asyncio.run(main())
