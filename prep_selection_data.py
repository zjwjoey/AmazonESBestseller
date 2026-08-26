# -*- coding: utf-8 -*-
"""
Amazon.es 选品数据库重构 —— 数据预处理
读取源工作簿「AmazonES_产品清单与提取信息.xlsx」的「提取信息」sheet（200 行原始爬虫数据），
解析 details_json，生成：
  - 规格（西语，来自真实字段：尺寸/容量/重量/件数/材质/功率）
  - 商品详情摘要（西语，150~250 字，格式 属性1；属性2；…）
  - BSR 主/细分类目（来自 Amazon 详情页 "clasificacion_en_los_mas_vendidos_de_amazon"）
  - 上架时间标准化（西语日期 → YYYY-MM-DD）
  - 品牌清洗（去掉 "Visita la tienda de" 前缀）
  - first_seen/last_seen Excel 序列号 → 真实日期时间
输出：
  _selected_data.json       —— 选品清单/排行榜记录/后台数据所需的全部计算字段
  _translation_input.jsonl  —— 待翻译文本（西语），供后续模型翻译为中文

不重新抓取 Amazon；不猜测缺失字段；原始字段一律保留在后台数据。
"""
import openpyxl
import json
import re
import os
import datetime

SRC = r"E:\amazon_es\.worktrees\reconnaissance\AmazonESBestseller\outputs\amazon_es_catalog_20260825\AmazonES_产品清单与提取信息.xlsx"
OUTDIR = os.path.dirname(SRC)
OUT_DATA = os.path.join(OUTDIR, "_selected_data.json")
OUT_TRANSL = os.path.join(OUTDIR, "_translation_input.jsonl")

MONTHS_ES = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6,
    'julio': 7, 'agosto': 8, 'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12,
}


def excel_serial_to_dt(v):
    """Excel 序列号 → datetime（序列号为字符串/数字均可）"""
    if v is None:
        return None
    try:
        f = float(str(v).replace(',', '.').strip())
    except (ValueError, TypeError):
        return None
    try:
        return datetime.datetime(1899, 12, 30) + datetime.timedelta(days=f)
    except (OverflowError, ValueError):
        return None


def parse_es_date(s):
    """西语日期 '28 octubre 2023' → date，无法解析返回 None"""
    if not s:
        return None
    s = str(s).strip()
    m = re.match(r'^(\d{1,2})\s+([A-Za-záéíóúñÁÉÍÓÚÑ]+)\s+(\d{4})\s*$', s)
    if not m:
        return None
    d, mo, y = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    if mo not in MONTHS_ES:
        return None
    try:
        return datetime.date(y, MONTHS_ES[mo], d)
    except ValueError:
        return None


def extract_bsr_segments(s):
    """从 'nº52 en Hogar y cocina ( Ver el Top 100 … ) nº1 en Juegos de recipientes'
    提取 [(排名, 类目), …]，只取最具体片段。返回 (主类目, 主排名, 细分类目, 细分排名)。"""
    if not s:
        return None, None, None, None
    segs = []
    for m in re.finditer(r'n\.?º?\s*([\d.,]+)\s*en\s*([^()\n]+)', s):
        rank = m.group(1).replace('.', '').replace(',', '')
        cat = re.sub(r'\s*(Ver el|Ver los|Ver).*$', '', m.group(2)).strip()
        if cat:
            segs.append((rank, cat))
    if not segs:
        return None, None, None, None
    main_rank, main_cat = segs[0]
    # 最后一段往往是最具体的子类目（如 "Juegos de recipientes"）
    leaf_rank, leaf_cat = segs[-1] if len(segs) > 1 else (None, None)
    return main_cat, main_rank, leaf_cat, leaf_rank


def clean_brand(b):
    """去掉 'Visita la tienda de ' 前缀，仅做确定性的前缀清洗"""
    if not b:
        return ''
    s = str(b).strip()
    m = re.match(r'^Visita\s+la\s+tienda\s+de\s+(.+)$', s, re.IGNORECASE)
    return m.group(1).strip() if m else s


def package_count(d):
    """件数：优先取 总数，其次 每包数量"""
    for k in ('total_del_paquete_segun_la_medida_elegida_para_referenciar_precio',
              'cantidad_de_articulos_en_el_paquete', 'numero_de_articulos'):
        v = d.get(k)
        if v:
            m = re.match(r'([\d.,]+)', str(v).replace(' ', ''))
            if m:
                return m.group(1).replace(',', '.')
    return None


def build_spec(d):
    """规格（西语，真实字段），不确定时留空"""
    if not d:
        return ''
    parts = []
    dim = d.get('dimensiones_del_articulo_largo_x_ancho_x_alto') or d.get('dimensiones_del_producto')
    if dim:
        parts.append('尺寸: ' + str(dim).strip())
    cap = d.get('capacidad')
    if cap:
        parts.append('容量: ' + str(cap).strip())
    peso = d.get('peso_del_producto')
    if peso:
        parts.append('重量: ' + str(peso).strip())
    cnt = package_count(d)
    if cnt:
        parts.append('件数: %s件' % cnt)
    mat = d.get('tipo_de_material') or d.get('material_de_la_tapa')
    if mat:
        parts.append('材质: ' + str(mat).strip())
    pot = d.get('potencia')
    if pot:
        parts.append('功率: ' + str(pot).strip())
    return '；'.join(parts)


def build_summary(d):
    """商品详情摘要（西语），150~250 字，格式 属性1；属性2；…"""
    if not d:
        return ''
    facts = []
    tipo = d.get('nombre_del_tipo_de_articulo')
    if tipo:
        facts.append('Tipo: ' + str(tipo).strip())
    mat = d.get('tipo_de_material') or d.get('material_de_la_tapa')
    if mat:
        facts.append('Material: ' + str(mat).strip())
    dim = d.get('dimensiones_del_articulo_largo_x_ancho_x_alto') or d.get('dimensiones_del_producto')
    if dim:
        facts.append('Dimensiones: ' + str(dim).strip())
    cap = d.get('capacidad')
    if cap:
        facts.append('Capacidad: ' + str(cap).strip())
    cnt = package_count(d)
    if cnt:
        facts.append('Contenido: %s uds' % cnt)
    col = d.get('color')
    if col:
        facts.append('Color: ' + str(col).strip())
    espec = d.get('carecteristicas_especiales')
    if espec:
        facts.append('Caract.: ' + str(espec).strip())
    usos = d.get('usos_recomendados_para_producto')
    if usos:
        facts.append('Uso: ' + str(usos).strip())
    origen = d.get('country_of_origin')
    if origen:
        facts.append('Origen: ' + str(origen).strip())
    safety = d.get('sin_tipo_de_material')
    if safety:
        facts.append('Certif.: ' + str(safety).strip())
    mw = d.get('el_articulo_es_apto_para_el_microondas')
    dw = d.get('el_articulo_es_apto_para_lavavajillas')
    apto = []
    if mw and 'Sí' in str(mw):
        apto.append('microondas')
    if dw and 'Sí' in str(dw):
        apto.append('lavavajillas')
    if apto:
        facts.append('Apto: ' + '/'.join(apto))

    txt = '；'.join(facts)

    # 不足 150 字时补第一条卖点（截取第一个短句）
    if len(txt) < 150 and d.get('features'):
        f0 = str(d['features'][0])
        seg = re.split(r'[–—]|\s*\.\s', f0, maxsplit=1)[0].strip()
        if len(seg) > 100:
            seg = seg[:100].rstrip() + '…'
        if seg:
            txt = (txt + '；' + seg) if txt else seg

    # 超长则按 250 字截断（尽量在 '；' 处断）
    if len(txt) > 250:
        cut = txt[:250].rfind('；')
        txt = txt[:cut] if cut > 120 else txt[:250]
        txt = txt.rstrip('；') + '…'
    return txt


# ---------- 读取源数据 ----------
wb = openpyxl.load_workbook(SRC, read_only=True, data_only=True)
ws = wb['提取信息']
rows = list(ws.iter_rows(min_row=4, values_only=True))
header = [str(h).strip() if h else '' for h in rows[0]]
data = [r for r in rows[1:] if r and any(c is not None and str(c).strip() != '' for c in r)]
idx = {h: i for i, h in enumerate(header)}

records = []
for r in data:
    def g(name):
        v = r[idx[name]] if name in idx and idx[name] < len(r) else None
        return '' if v is None else str(v).strip()

    asin = g('asin')
    brand_raw = g('brand')
    brand = clean_brand(brand_raw)
    details_json_raw = g('details_json')
    d = None
    if details_json_raw:
        try:
            d = json.loads(details_json_raw)
        except Exception:
            d = None

    first_dt = excel_serial_to_dt(g('first_seen'))
    last_dt = excel_serial_to_dt(g('last_seen'))
    date_raw = g('date_first_available_raw')
    date_obj = parse_es_date(date_raw)

    main_cat, main_rank, leaf_cat, leaf_rank = extract_bsr_segments(
        (d or {}).get('clasificacion_en_los_mas_vendidos_de_amazon', ''))

    spec = build_spec(d)
    summary = build_summary(d)

    detail_status = '已提取' if g('详情已提取') == '是' else '未提取'
    img_status = '已嵌入' if g('图片已下载') == '是' else '未下载'

    records.append({
        'row_idx': len(records),
        '采集类目中文': g('采集类目中文'),
        '采集类目西语': g('采集类目西语'),
        '详情已提取': g('详情已提取'),
        '图片已下载': g('图片已下载'),
        'asin': asin,
        'parent_asin': g('parent_asin'),
        'parent_asin_status': g('parent_asin_status'),
        'title_es': g('title_es'),
        'brand_raw': brand_raw,
        'brand': brand,
        'price': g('price'),
        'original_price': g('original_price'),
        'current_price': g('current_price'),
        'currency': g('currency'),
        'discount_rate': g('discount_rate'),
        'rating': g('rating'),
        'review_count': g('review_count'),
        'monthly_bought_text': g('monthly_bought_text'),
        'image_url': g('image_url'),
        'product_url': g('product_url'),
        'details_json': details_json_raw,
        'details': g('details'),
        'specification': g('specification'),
        'date_first_available': g('date_first_available'),
        'date_first_available_raw': date_raw,
        'date_first_available_norm': date_obj.strftime('%Y-%m-%d') if date_obj else '',
        'first_seen_dt': first_dt.isoformat(sep=' ') if first_dt else '',
        'last_seen_dt': last_dt.isoformat(sep=' ') if last_dt else '',
        'ranking_count': g('ranking_count'),
        'best_rank': g('best_rank'),
        'image_path': g('image_path'),
        'image_download_status': g('image_download_status'),
        'image_download_error': g('image_download_error'),
        '详情状态': detail_status,
        '图片状态': img_status,
        # 派生字段
        'spec_es': spec,
        'summary_es': summary,
        'bsr_main_cat': main_cat or '',
        'bsr_main_rank': main_rank or '',
        'bsr_leaf_cat': leaf_cat or '',
        'bsr_leaf_rank': leaf_rank or '',
    })

# ---------- 写出 ----------
with open(OUT_DATA, 'w', encoding='utf-8') as f:
    json.dump(records, f, ensure_ascii=False, indent=1)

# 翻译输入：只含需翻译的西语文本
with open(OUT_TRANSL, 'w', encoding='utf-8') as f:
    for rec in records:
        f.write(json.dumps({
            'asin': rec['asin'],
            'title_es': rec['title_es'],
            'spec_es': rec['spec_es'],
            'summary_es': rec['summary_es'],
        }, ensure_ascii=False) + '\n')

# ---------- 统计 ----------
n_price = sum(1 for x in records if x['current_price'])
n_json = sum(1 for x in records if x['details_json'])
n_spec = sum(1 for x in records if x['spec_es'])
n_summary = sum(1 for x in records if x['summary_es'])
n_date = sum(1 for x in records if x['date_first_available_norm'])
n_leaf = sum(1 for x in records if x['bsr_leaf_cat'])
print('读取商品数:', len(records))
print('有当前价格: %d | 有details_json: %d | 有规格: %d | 有摘要: %d | 有上架时间: %d | 有BSR细分类目: %d' % (
    n_price, n_json, n_spec, n_summary, n_date, n_leaf))
print('已写出:')
print('  ', OUT_DATA)
print('  ', OUT_TRANSL)
