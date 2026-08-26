# -*- coding: utf-8 -*-
"""normalization/dates.py 测试。"""
import datetime

from amazon_es_bestseller.normalization.dates import (
    MONTHS_ES,
    excel_serial_to_dt,
    parse_es_date,
    norm_dt,
)


def test_months_es_full_set():
    assert len(MONTHS_ES) == 12
    assert MONTHS_ES["enero"] == 1
    assert MONTHS_ES["diciembre"] == 12


def test_excel_serial_known_anchor():
    # 已知锚点：Excel 序列 44927 = 2023-01-01
    assert excel_serial_to_dt(44927).date() == datetime.date(2023, 1, 1)


def test_excel_serial_accepts_string_and_comma():
    assert excel_serial_to_dt("44927").date() == datetime.date(2023, 1, 1)
    assert excel_serial_to_dt("44927,5") == datetime.datetime(2023, 1, 1, 12, 0)


def test_excel_serial_none_and_garbage():
    assert excel_serial_to_dt(None) is None
    assert excel_serial_to_dt("abc") is None


def test_excel_serial_overflow_guarded():
    assert excel_serial_to_dt(10**30) is None


def test_parse_es_date_basic():
    assert parse_es_date("28 octubre 2023") == datetime.date(2023, 10, 28)
    assert parse_es_date("1 enero 2024") == datetime.date(2024, 1, 1)


def test_parse_es_date_case_insensitive_month():
    assert parse_es_date("28 Octubre 2023") == datetime.date(2023, 10, 28)


def test_parse_es_date_extra_whitespace():
    assert parse_es_date("30  noviembre  2023") == datetime.date(2023, 11, 30)
    # 历法校验：11 月只有 30 天
    assert parse_es_date("31  noviembre  2023") is None


def test_parse_es_date_calendar_validity():
    # 历法校验：2 月无 31 日 → None
    assert parse_es_date("31 febrero 2023") is None


def test_parse_es_date_unknown_month_and_garbage():
    assert parse_es_date("28 pebrero 2023") is None
    assert parse_es_date("2023-10-28") is None
    assert parse_es_date("") is None
    assert parse_es_date(None) is None


def test_norm_dt_numeric_becomes_iso():
    assert norm_dt(44927) == "2023-01-01 00:00:00"
    assert norm_dt("44927") == "2023-01-01 00:00:00"


def test_norm_dt_passthrough_and_empty():
    assert norm_dt("28 octubre 2023") == "28 octubre 2023"
    assert norm_dt(None) == ""
    assert norm_dt("") == ""
