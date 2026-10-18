from odoo import api, fields, models

MONTHS = ['Meskerem', 'Tikimt', 'Hidar', 'Tahsas', 'Tir', 'Yekatit', 'Megabit', 'Miazia', 'Ginbot', 'Sene', 'Hamle', 'Nehase', 'Pagume']
MONTHS_AM = ['መስከረም', 'ጥቅምት', 'ኅዳር', 'ታኅሣሥ', 'ጥር', 'የካቲት', 'መጋቢት', 'ሚያዝያ', 'ግንቦት', 'ሰኔ', 'ሐምሌ', 'ነሐሴ', 'ጳጉሜን']
JDN_OFFSET = 1721425  # date.toordinal() + this = Julian day number
ETHIOPIC_EPOCH = 1723856


def to_ethiopian(day):
    """(year, month, day) in the Ethiopian calendar for a Gregorian date."""
    jdn = day.toordinal() + JDN_OFFSET
    r = (jdn - ETHIOPIC_EPOCH) % 1461
    n = r % 365 + 365 * (r // 1460)
    year = 4 * ((jdn - ETHIOPIC_EPOCH) // 1461) + r // 365 - r // 1460
    return year, n // 30 + 1, n % 30 + 1


def from_ethiopian(year, month, day):
    """The Gregorian date for an Ethiopian (year, month, day)."""
    jdn = ETHIOPIC_EPOCH + 365 * year + year // 4 + 30 * month + day - 31
    return fields.Date.to_date('0001-01-01').fromordinal(jdn - JDN_OFFSET)


class MosEthiopianDate(models.AbstractModel):
    """Dates in the Ethiopian calendar for documents and reports (FR-PLT-052)."""

    _name = 'mos.ethiopian.date'
    _description = 'MOS Ethiopian calendar'

    @api.model
    def convert(self, day, amharic=False):
        """Text such as '8 Tikimt 2019' for a Gregorian date."""
        day = fields.Date.to_date(day)
        year, month, dom = to_ethiopian(day)
        names = MONTHS_AM if amharic else MONTHS
        return f'{dom} {names[month - 1]} {year}'
