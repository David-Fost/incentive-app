# app/calculations.py
from decimal import Decimal, ROUND_HALF_UP
from typing import List

# === КОНСТАНТЫ ИЗ ПРИКАЗА И EXCEL ===
NIR_SCORE_MIN = Decimal("1.0")   # Минимум за НИР, если участвует
NIR_SCORE_MAX = Decimal("3.0")   # Cap Блока 1
MAIN_SCORE_CAP = Decimal("5.0")  # Cap основной части (lib)
ADDITIONAL_SCORE_CAP = Decimal("5.0")  # Cap доп. баллов (Сводная!H)

def _q(val: Decimal) -> Decimal:
    """Округление до 2 знаков (как в Excel ROUND(..., 2))"""
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def calculate_nir_score_raw(
    publications_count: int = 0,
    rid_count: int = 0,
    nmd_count: int = 0,
    coauthors_count: int = 3
) -> Decimal:
    """
    Блок 1: Расчёт сырого балла за НИР.
    Формула: Б = 0.5 * (N_публ/m + N_РИД/m + N_НМД/m)
    m = 1, если соавторов ≤ 3, иначе m = n_соавторов - 2
    """
    m = Decimal("1") if coauthors_count <= 3 else Decimal(str(coauthors_count - 2))
    
    raw = Decimal("0.5") * (
        Decimal(str(publications_count)) / m +
        Decimal(str(rid_count)) / m +
        Decimal(str(nmd_count)) / m
    )
    
    # Логика из Сводная!E: IF(sum=0, 0, IF(sum>3, 3, IF(sum<1, 1, sum)))
    total_items = publications_count + rid_count + nmd_count
    if total_items == 0:
        return Decimal("0")
    elif raw > NIR_SCORE_MAX:
        return NIR_SCORE_MAX
    elif raw < NIR_SCORE_MIN:
        return NIR_SCORE_MIN
    return _q(raw)

def normalize_by_working_days(score: Decimal, worked_days: int, month_total_days: int) -> Decimal:
    """
    Нормировка на рабочие дни: score * (дни_сотрудника / дней_в_месяце)
    Аналог Excel: G * H / $H$2
    """
    if month_total_days == 0:
        return Decimal("0")
    return _q(score * Decimal(str(worked_days)) / Decimal(str(month_total_days)))

def apply_additional_cap(scores: List[Decimal]) -> Decimal:
    """
    Блоки 2+3: Суммирует дополнительные баллы и применяет cap 5.
    Аналог Excel: IF(SUM(I:AG) > 5, 5, SUM(I:AG))
    """
    total = sum(scores, Decimal("0"))
    return _q(min(total, ADDITIONAL_SCORE_CAP))

def calculate_final_total(nir_normalized: Decimal, additional_total: Decimal) -> Decimal:
    """
    Итоговый балл (лист lib): min(НИР, 5) + доп_баллы
    """
    nir_capped = min(nir_normalized, MAIN_SCORE_CAP)
    return _q(nir_capped + additional_total)