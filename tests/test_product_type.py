# -*- coding: utf-8 -*-
"""translation/product_type.py 测试：已知回归（QA_RULES §11 商品类型、§16 中文品名）。"""
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


# ---------- 真实标题 fixtures（_feat_scan.txt / product_details.json） ----------

def test_real_thermal_bag_titles():
    assert detect_product_type(
        "Coronel Tapioca - Bolsa Termica Porta Alimentos - Nevera por"
    ) == "保温包"
    assert detect_product_type(
        "Lifewit Bolsa Termica Porta Alimentos 9L(12 Latas), Bolso Co"
    ) == "保温包"


def test_real_lunchbox_titles():
    # 含英文 Lunch Box + Fiambrera（真实标题，英文/西语同义均须命中）
    assert detect_product_type(
        "b.box Mini Lunch Box para Niños | Fiambrera Infantil Bento c"
    ) == "便当盒"
    assert detect_product_type(
        "LÄSSIG Fiambrera para niños de acero inoxidable Fiambrera pa"
    ) == "便当盒"


def test_real_cleaning_tablets_title():
    assert detect_product_type(
        "KRUPS Pack de 10 Pastillas de Limpieza para Cafeteras Automá"
    ) == "除垢片"


def test_real_chain_oil_title_para_lubricar_cadenas():
    # 真实回归：标题不含 "aceite de cadena"，只有 "Aceite ... para Lubricar Cadenas"
    assert detect_product_type(
        "Oregon Aceite Orgánico y Biodegradable para Lubricar Cadenas"
    ) == "链条润滑油"


def test_real_mini_chainsaw_title():
    assert detect_product_type(
        "SEESII 6'' Mini Motosierra Bateria 8000mAh, Motosierra Eléct"
    ) == "迷你电锯"


def test_real_disposable_air_fryer_paper_title():
    # 真实回归：Desechable → 一次性（旧规则漏检返回 None）
    assert detect_product_type(
        "edihome, Papel Freidora Aire, Air Fryer, 100 Unidades, 20-24 cm, "
        "BPA Free, Desechable, para Horno, Accesorios para Freidora sin "
        "Aceite de 5 a 8 litros (20-24 cm, Cuadrado)"
    ) == "一次性"


def test_cleaning_tablets_never_portafiltro():
    # 互斥：含除垢片证据时绝不判滤杯手柄（desincrustantes 必须先于 portafiltro）
    assert detect_product_type("Desincrustantes para portafiltro") == "除垢片"
    assert detect_product_type("Pastillas de limpieza para portafiltro") == "除垢片"


def test_lunch_box_english_only():
    assert detect_product_type("Bento Lunch Box con compartimentos") == "便当盒"


def test_rules_ordered_by_specificity():
    # 更具体的词必须排在宽泛词之前（保证上面 ≠ 回归成立）
    es_list = [es for es, _ in PRODUCT_TYPE_RULES]
    assert es_list.index("bolsa térmica") < es_list.index("fiambrera")
    assert es_list.index("hilo de desbrozadora") < es_list.index("desbrozadora")
    assert es_list.index("aceite de cadena") < es_list.index("motosierra")
    assert es_list.index("portafiltro") < es_list.index("tamper")
