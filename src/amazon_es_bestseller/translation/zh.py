# -*- coding: utf-8 -*-
"""确定性西→中词典与翻译函数（D5：合并去重排序，剔德语词，first-wins）。

数据源：
  - _TERMS_BASE   ← make_translations.py 的 TERMS（剔德语 "Abnehmbar"，QA_RULES §36）
  - _TERMS_MAT    ← prep_v2_selection.py 的 _ZH_MAT
  - _TERMS_TR     ← prep_v2_selection.py 的 _ZH_TR
  - _TERMS_USO    ← prep_v2_selection.py 的 _ZH_USO

尺寸一律走 normalization.specification.dim_zh（简式，D2/QA_RULES §40）。
"""
from __future__ import annotations

import re

from .exceptions import feature_zh
from ..normalization.specification import dim_zh
from ..normalization.text import dec_comma

# ---------- 词典来源 ----------
_TERMS_BASE = [
    # 材质
    ("Acero inoxidable", "不锈钢"), ("acero inoxidable", "不锈钢"),
    ("Vidrio de borosilicato", "硼硅玻璃"), ("Vidrio templado", "钢化玻璃"),
    ("Tereftalato de polietileno", "PET塑料"), ("Poliéster", "涤纶"),
    ("Poliestireno", "聚苯乙烯"), ("Polipropileno", "聚丙烯"),
    ("Polietileno", "聚乙烯"), ("Policarbonato", "聚碳酸酯"),
    ("Poliuretano", "聚氨酯"), ("Fibra de vidrio", "玻璃纤维"),
    ("Inoxidable", "不锈钢"), ("Silicona", "硅胶"), ("Cerámica", "陶瓷"),
    ("Aluminio", "铝"), ("Algodón", "棉"), ("Bambú", "竹"),
    ("Madera", "木材"), ("Resina", "树脂"), ("Nylon", "尼龙"),
    ("Nailon", "尼龙"), ("Caucho", "橡胶"), ("Goma", "橡胶"),
    ("Plástico", "塑料"), ("Metal", "金属"), ("Cuero", "皮革"),
    ("Tela", "织物"), ("Esparto", "茅草"), ("Oxford", "牛津布"),
    ("Acero", "钢"), ("Vidrio", "玻璃"), ("ABS", "ABS"),
    # 单位
    ("centímetros", "厘米"), ("centimetros", "厘米"), ("centímetro", "厘米"),
    ("milímetros", "毫米"), ("milimetros", "毫米"), ("milímetro", "毫米"),
    ("kilómetros", "千米"), ("kilometros", "千米"), ("metros", "米"),
    ("metro", "米"), ("kilogramos", "千克"), ("kilogramo", "千克"),
    ("Kilogramos", "千克"), ("Kilogramo", "千克"), ("Libras", "磅"),
    ("libras", "磅"), ("gramos", "克"), ("gramo", "克"), ("Gramos", "克"),
    ("g", "克"), ("litros", "升"), ("litro", "升"), ("Litros", "升"),
    ("mililitros", "毫升"), ("mililitro", "毫升"), ("vatios", "瓦"),
    ("watios", "瓦"), ("pulgadas", "英寸"), ("pies", "英尺"),
    ("cm", "厘米"), ("mm", "毫米"), ("ml", "毫升"), ("kg", "千克"), ("km", "千米"),
    # 颜色
    ("Negro", "黑色"), ("Blanco", "白色"), ("Gris", "灰色"),
    ("Rojo", "红色"), ("Azul", "蓝色"), ("Verde", "绿色"),
    ("Amarillo", "黄色"), ("Naranja", "橙色"), ("Rosa", "粉色"),
    ("Morado", "紫色"), ("Turquesa", "青绿色"), ("Marrón", "棕色"),
    ("Plateado", "银色"), ("Plata", "银色"), ("Dorado", "金色"),
    ("Multicolor", "多色"), ("Transparente", "透明"),
    # （D5：德语 "Abnehmbar" 已剔除，QA_RULES §36）
    ("Comida para llevar", "外卖食物"), ("Sin BPA", "不含BPA"),
    # 产地
    ("España", "西班牙"), ("Alemania", "德国"), ("Francia", "法国"),
    ("Italia", "意大利"), ("Portugal", "葡萄牙"), ("Reino Unido", "英国"),
    ("China", "中国"),
    # 特性 / 适用
    ("Apto para congelador", "可用于冷冻室"), ("Apto para microondas", "可用于微波炉"),
    ("Apto para lavavajillas", "可用于洗碗机"), ("Apto para el lavavajillas", "可用于洗碗机"),
    ("congelador", "冷冻室"), ("microondas", "微波炉"), ("lavavajillas", "洗碗机"),
    ("Apilable", "可堆叠"), ("Extraíble", "可拆卸"), ("Extraible", "可拆卸"),
    ("Desmontable", "可拆卸"), ("Reciclable", "可回收"),
    ("Reutilizable", "可重复使用"), ("uds", "件"), ("unidades", "件"),
    ("unidad", "件"), ("Sí", "是"),
]

_TERMS_MAT = [
    ('Acero inoxidable', '不锈钢'), ('acero inoxidable', '不锈钢'),
    ('Vidrio de borosilicato', '硼硅玻璃'), ('Vidrio templado', '钢化玻璃'),
    ('Tereftalato de polietileno', 'PET塑料'), ('Poliéster', '涤纶'),
    ('Polipropileno', '聚丙烯'), ('Polietileno', '聚乙烯'),
    ('Policarbonato', '聚碳酸酯'), ('Poliuretano', '聚氨酯'),
    ('Fibra de vidrio', '玻璃纤维'), ('Silicona', '硅胶'), ('Cerámica', '陶瓷'),
    ('Aluminio', '铝'), ('Algodón', '棉'), ('Bambú', '竹'), ('Madera', '木材'),
    ('Resina', '树脂'), ('Nylon', '尼龙'), ('Nailon', '尼龙'),
    ('Caucho', '橡胶'), ('Goma', '橡胶'), ('Plástico', '塑料'),
    ('Plastico', '塑料'), ('Metal', '金属'), ('Cuero', '皮革'), ('Tela', '织物'),
    ('Esparto', '茅草'), ('Oxford', '牛津布'), ('Inoxidable', '不锈钢'),
    ('Acero', '钢'), ('Vidrio', '玻璃'), ('Ratán', '藤'), ('ABS', 'ABS'),
    ('Porcelana', '陶瓷'), ('Piedra', '石材'), ('Teflón', '特氟龙'),
    ('Teflon', '特氟龙'), ('Mármol', '大理石'), ('Cuarzo', '石英'),
    ('Grafito', '石墨'), ('Cristal', '水晶'), ('Elástico', '弹力'),
    ('Espuma', '海绵'), ('Vellón', '绒'), ('Lino', '亚麻'), ('Seda', '丝绸'),
    ('Piel', '皮革'), ('Corcho', '软木'), ('Carbono', '碳纤维'),
    ('Vidrio templado', '钢化玻璃'), ('Poliamida', '聚酰胺'),
]

_TERMS_TR = [
    ('Apilable', '可堆叠'), ('Extraíble', '可拆卸'), ('Extraible', '可拆卸'),
    ('Desmontable', '可拆卸'), ('Reciclable', '可回收'),
    ('Reutilizable', '可重复使用'), ('Doble sello', '双重密封'),
    ('Airtight', '密封防漏'), ('A prueba de agua', '防水'),
    ('Impermeable', '防水'), ('Sin BPA', '不含BPA'), ('Sí', '是'), ('No', '否'),
    ('Diseño plegable', '可折叠'), ('Plegable', '可折叠'), ('Portátil', '便携'),
    ('Ligero', '轻便'), ('Robusto', '耐用'), ('Robusta', '耐用'),
    ('Flexible', '柔韧'), ('Flexibles', '柔韧'),
    ('resistente a desgarros', '耐撕'), ('Resistente a desgarros', '耐撕'),
    ('Sin enjuague', '免冲洗'), ('Sin residuos', '无残留'),
    ('Sin residuo', '无残留'), ('Concentrado', '浓缩'),
    ('Concentrada', '浓缩'), ('Sin aluminio', '不含铝'),
    ('Sin cobre', '不含铜'), ('Sin metales pesados', '不含重金属'),
    ('Metales pesados', '重金属'), ('alta presión', '高压'),
    ('Alta presión', '高压'), ('ajustable', '可调节'), ('Ajustable', '可调节'),
    ('fácil instalación', '易安装'), ('Fácil instalación', '易安装'),
    ('Descalcificador', '除垢'), ('elimina depósitos de cal', '去除水垢'),
    ('Elimina depósitos de cal', '去除水垢'), ('Filtración avanzada', '深度过滤'),
    ('sin costuras', '无缝'), ('Sin costuras', '无缝'), ('hermético', '密封'),
    ('Hermético', '密封'), ('resistente al calor', '耐热'),
    ('Resistente al calor', '耐热'), ('A prueba de fugas', '防漏'),
    ('antideslizante', '防滑'), ('Antideslizante', '防滑'),
    ('transpirable', '透气'), ('Transpirable', '透气'),
    ('a prueba de polvo', '防尘'),
]

_TERMS_USO = [
    ('Comida para llevar', '外卖'), ('Transportador de alimentos', '食物携带'),
    ('Alfombras', '地毯'), ('Interior del automóvil', '汽车内饰'),
    ('Tapetes', '门垫'), ('Tapicería', '织物'), ('Ropa', '衣物'),
    ('Baño', '浴室'), ('Cocina', '厨房'), ('Jardin', '花园'),
    ('Jardín', '花园'), ('Exterior', '户外'), ('Interior', '室内'),
    ('Almacenamiento de comidas', '食物储存'), ('Máquinas de cápsulas', '胶囊咖啡机'),
    ('Desincrustantes para cafeteras', '咖啡机除垢'), ('Mascotas', '宠物'),
    ('Limpieza', '清洁'), ('Organización', '收纳'), ('Viaje', '旅行'),
    ('Escuela', '学校'), ('Picnic', '野餐'), ('Congelador', '冷冻'),
    ('Nevera', '冰箱'), ('Horno', '烤箱'), ('Microondas', '微波炉'),
]


def _merge_terms(*groups) -> list[tuple[str, str]]:
    """多词典合并去重：同一西语词 first-wins（按传入顺序）。"""
    seen = set()
    out = []
    for group in groups:
        for es, zh in group:
            if es not in seen:
                seen.add(es)
                out.append((es, zh))
    return out


#: 合并去重后的确定性词典（调用方按 -len(es) 排序使用）
TERMS = _merge_terms(_TERMS_BASE, _TERMS_MAT, _TERMS_TR, _TERMS_USO)

#: 摘要结构化标签：西语 → 中文
SUMMARY_LABELS = {
    "Tipo": "类型", "Material": "材质", "Dimensiones": "尺寸",
    "Capacidad": "容量", "Contenido": "含量", "Color": "颜色",
    "Caract.": "特点", "Uso": "用途", "Origen": "产地",
    "Certif.": "认证", "Apto": "适用",
}
SP_LABEL_STARTS = tuple(k + ":" for k in SUMMARY_LABELS) + tuple(k + "：" for k in SUMMARY_LABELS)


def apply_terms(s, terms=TERMS) -> str:
    """按词典替换（长词在前，避免部分重叠）。"""
    if not s:
        return ''
    s = str(s)
    for es, zh in sorted(terms, key=lambda t: -len(t[0])):
        s = re.sub(r'\b' + re.escape(es) + r'\b', zh, s)
    return s.strip()


def translate_value(v) -> str:
    """西语值 → 中文（尺寸走简式 dim_zh；单位/材质/颜色用确定性词典）。"""
    if not v:
        return ''
    s = dec_comma(str(v).strip())
    dz = dim_zh(s)
    if dz is not None:
        return dz
    s = re.sub(r'\s+x\s+', '×', s)
    s = apply_terms(s)
    # 件数 "1.0件"/"1.0 件" → "1件"
    s = re.sub(r'(?<=\d)\s*\.0\s*(?=件)', '', s)
    s = re.sub(r'(?<=\d)\s+(?=件)', '', s)
    # 数字与中文单位之间的空格去掉："350 克" → "350克"
    s = re.sub(r'(?<=\d)\s+(?=[克升毫升瓦件磅千米])', '', s)
    return s.strip()


def spec_zh_from(spec_es) -> str:
    """西语规格（标签为中文）→ 中文规格。"""
    if not spec_es:
        return ''
    out = []
    for seg in [x.strip() for x in spec_es.split('；') if x.strip()]:
        m = re.match(r'^(尺寸|容量|重量|件数|材质|功率)\s*[:：]\s*(.*)$', seg)
        if m:
            out.append(m.group(1) + '：' + translate_value(m.group(2)))
        else:
            out.append(translate_value(seg))
    return '；'.join(out)


def summary_zh_from(summary_es, asin=None) -> str:
    """西语摘要 → 中文摘要；末尾卖点句优先用逐 ASIN 手译表。"""
    if not summary_es:
        return ''
    segs = [x.strip() for x in summary_es.split('；') if x.strip()]
    out = []
    for i, seg in enumerate(segs):
        is_last = (i == len(segs) - 1)
        if is_last and not seg.startswith(SP_LABEL_STARTS):
            zh = feature_zh(asin)
            out.append(zh if zh else translate_value(seg))
            continue
        m = re.match(
            r'^(Tipo|Material|Dimensiones|Capacidad|Contenido|Color|Caract\.|Uso|Origen|Certif\.|Apto)\s*[:：]\s*(.*)$',
            seg)
        if m:
            out.append(SUMMARY_LABELS[m.group(1)] + '：' + translate_value(m.group(2)))
        else:
            out.append(translate_value(seg))
    return '；'.join(out)
