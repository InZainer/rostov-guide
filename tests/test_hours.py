from datetime import datetime

from rostov_guide.places.hours import ROSTOV_TZ, parse, status

WED_1200 = datetime(2026, 10, 7, 12, 0, tzinfo=ROSTOV_TZ)       # среда
WED_0700 = datetime(2026, 10, 7, 7, 0, tzinfo=ROSTOV_TZ)
SAT_1200 = datetime(2026, 10, 10, 12, 0, tzinfo=ROSTOV_TZ)
SUN_1200 = datetime(2026, 10, 11, 12, 0, tzinfo=ROSTOV_TZ)
THU_0100 = datetime(2026, 10, 8, 1, 0, tzinfo=ROSTOV_TZ)


def test_round_the_clock():
    assert status("24/7", WED_1200).open is True
    assert status("24/7", WED_1200).label == "круглосуточно"


def test_everyday_interval_without_days():
    st = status("08:00-22:00", WED_1200)
    assert st.open is True and st.label == "открыто до 22:00"


def test_closed_before_opening_says_when():
    st = status("Mo-Su 09:00-21:00", WED_0700)
    assert st.open is False and st.label == "закрыто, откроется в 09:00"


def test_weekday_rules_and_day_off():
    text = "Mo-Fr 08:00-13:00,14:00-20:00; Sa 09:00-13:00,14:00-18:00; Su off"
    assert status(text, WED_1200).open is True
    assert status(text, SAT_1200).label == "открыто до 13:00"
    sunday = status(text, SUN_1200)
    assert sunday.open is False and sunday.label == "закрыто, откроется завтра в 08:00"


def test_lunch_break_is_closed():
    st = status("Mo-Fr 08:00-13:00,14:00-20:00", datetime(2026, 10, 7, 13, 30, tzinfo=ROSTOV_TZ))
    assert st.open is False and st.label == "закрыто, откроется в 14:00"


def test_overnight_interval_spills_into_next_day():
    assert status("Mo-Su 18:00-02:00", THU_0100).open is True
    assert status("Mo-Su 18:00-02:00", THU_0100).label == "открыто до 02:00"


def test_midnight_is_written_as_24():
    assert status("11:00-24:00", WED_1200).label == "открыто до 24:00"


def test_unknown_format_is_unknown_not_closed():
    assert parse("PH off; sunrise-sunset") is None
    assert status("по договорённости", WED_1200).open is None
    assert status(None, WED_1200).label == "часы не указаны"


def test_later_rule_overrides_earlier():
    schedule = parse("Mo-Su 09:00-21:00; Su off")
    assert schedule[6] == [] and schedule[0] == [(540, 1260)]
