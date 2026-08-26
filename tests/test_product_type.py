# -*- coding: utf-8 -*-
"""translation/product_type.py 测试：已知回归（QA_RULES §27-§28）。"""
from amazon_es_bestseller.translation.product_type import PRODUCT_TYPE_RULES, detect_product_type


def test_thermal_lunch_bag_not_lunchbox():
    assert detect_product_type("Bolsa térmica para comer") == "保温包"
    assert detect_product_type("Fiambrera de cristal con 4 piezas") == "便当盒"


def test_portafilter_not_tamper():
    assert detect_product_type("Portafiltro de acero inoxidable") == "滤杯手柄"
    assert detect_product_type("Tamper de 51 mm") == "压粉器"


def test_cleaning_tablets_not_portafilter():
    assert detect_product_type("Pastillas de limpieza para cafeteras") == "除垢片"
    assert detect_product_type("Desincrustantes para cafeteras") == "除垢片"


def test_mini_chainsaw_not_chain_oil():
    assert detect_product_type("Aceite de cadena para motosierra") == "链条润滑油"
    assert detect_product_type("Motosierra eléctrica 300W") == "迷你电锯"


def test_trimmer_line_not_trimmer_machine():
    assert detect_product_type("Hilo de desbrozadora 2 mm") == "打草线"


def test_reutilizable_not_disposable():
    assert detect_product_type("Juego de recipientes reutilizables") == "可重复使用"


def test_detect_unknown_is_none():
    assert detect_product_type("Caja de cartón decorativa") is None
    assert detect_product_type("") is None
    assert detect_product_type(None) is None


def test_rules_ordered_by_specificity():
    # 更具体的词必须排在宽泛词之前（保证上面 ≠ 回归成立）
    es_list = [es for es, _ in PRODUCT_TYPE_RULES]
    assert es_list.index("bolsa térmica") < es_list.index("fiambrera")
    assert es_list.index("hilo de desbrozadora") < es_list.index("desbrozadora")
    assert es_list.index("aceite de cadena") < es_list.index("motosierra")
    assert es_list.index("portafiltro") < es_list.index("tamper")
