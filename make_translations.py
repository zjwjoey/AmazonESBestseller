# -*- coding: utf-8 -*-
"""
翻译生成 —— 用模型能力，不调用任何第三方翻译服务。
输入：
  _titles_part1..4.json    —— 商品名称西语→中文（逐条手译，4 批共 200 条）
  _selected_data.json      —— 含 spec_es / summary_es / details_json
输出：
  _translations.json       —— {asin: {title_zh, spec_zh, summary_zh}}
规则：
  - 规格(zh)：标签已是中文（尺寸/容量/重量/件数/材质/功率），确定性转换值（单位/材质词典）
  - 摘要(zh)：结构化事实标签西→中 + 值转换；末尾卖点句用下方逐条手译表
  - 摘要/规格的数值与单位、材质、颜色均用确定性词典转换，保证可复现
"""
import json
import re
import os

OUTDIR = r"E:\amazon_es\.worktrees\reconnaissance\AmazonESBestseller\outputs\amazon_es_catalog_20260825"
SEL = os.path.join(OUTDIR, "_selected_data.json")
OUT = os.path.join(OUTDIR, "_translations.json")


# ---------- 确定性词典（单位/材质/颜色/常用词） ----------
TERMS = [
    # 材质（长词在前，避免被短词部分替换）
    ("Acero inoxidable", "不锈钢"),
    ("acero inoxidable", "不锈钢"),
    ("Vidrio de borosilicato", "硼硅玻璃"),
    ("Vidrio templado", "钢化玻璃"),
    ("Tereftalato de polietileno", "PET塑料"),
    ("Poliéster", "涤纶"),
    ("Poliestireno", "聚苯乙烯"),
    ("Polipropileno", "聚丙烯"),
    ("Polietileno", "聚乙烯"),
    ("Policarbonato", "聚碳酸酯"),
    ("Poliuretano", "聚氨酯"),
    ("Fibra de vidrio", "玻璃纤维"),
    ("Inoxidable", "不锈钢"),
    ("Silicona", "硅胶"),
    ("Cerámica", "陶瓷"),
    ("Aluminio", "铝"),
    ("Algodón", "棉"),
    ("Bambú", "竹"),
    ("Madera", "木材"),
    ("Resina", "树脂"),
    ("Nylon", "尼龙"),
    ("Nailon", "尼龙"),
    ("Caucho", "橡胶"),
    ("Goma", "橡胶"),
    ("Plástico", "塑料"),
    ("Metal", "金属"),
    ("Cuero", "皮革"),
    ("Tela", "织物"),
    ("Esparto", "茅草"),
    ("Oxford", "牛津布"),
    ("Acero", "钢"),
    ("Vidrio", "玻璃"),
    ("ABS", "ABS"),
    # 单位
    ("centímetros", "厘米"),
    ("centimetros", "厘米"),
    ("centímetro", "厘米"),
    ("milímetros", "毫米"),
    ("milimetros", "毫米"),
    ("milímetro", "毫米"),
    ("kilómetros", "千米"),
    ("kilometros", "千米"),
    ("metros", "米"),
    ("metro", "米"),
    ("kilogramos", "千克"),
    ("kilogramo", "千克"),
    ("Kilogramos", "千克"),
    ("Kilogramo", "千克"),
    ("Libras", "磅"),
    ("libras", "磅"),
    ("gramos", "克"),
    ("gramo", "克"),
    ("Gramos", "克"),
    ("g", "克"),
    ("litros", "升"),
    ("litro", "升"),
    ("Litros", "升"),
    ("mililitros", "毫升"),
    ("mililitro", "毫升"),
    ("vatios", "瓦"),
    ("watios", "瓦"),
    ("pulgadas", "英寸"),
    ("pies", "英尺"),
    ("cm", "厘米"),
    ("mm", "毫米"),
    ("ml", "毫升"),
    ("kg", "千克"),
    ("km", "千米"),
    # 颜色
    ("Negro", "黑色"),
    ("Blanco", "白色"),
    ("Gris", "灰色"),
    ("Rojo", "红色"),
    ("Azul", "蓝色"),
    ("Verde", "绿色"),
    ("Amarillo", "黄色"),
    ("Naranja", "橙色"),
    ("Rosa", "粉色"),
    ("Morado", "紫色"),
    ("Turquesa", "青绿色"),
    ("Marrón", "棕色"),
    ("Plateado", "银色"),
    ("Plata", "银色"),
    ("Dorado", "金色"),
    ("Multicolor", "多色"),
    ("Transparente", "透明"),
    ("Abnehmbar", "可拆卸"),
    ("Comida para llevar", "外卖食物"),
    ("Sin BPA", "不含BPA"),
    # 产地
    ("España", "西班牙"),
    ("Alemania", "德国"),
    ("Francia", "法国"),
    ("Italia", "意大利"),
    ("Portugal", "葡萄牙"),
    ("Reino Unido", "英国"),
    ("China", "中国"),
    # 特性 / 适用
    ("Apto para congelador", "可用于冷冻室"),
    ("Apto para microondas", "可用于微波炉"),
    ("Apto para lavavajillas", "可用于洗碗机"),
    ("Apto para el lavavajillas", "可用于洗碗机"),
    ("congelador", "冷冻室"),
    ("microondas", "微波炉"),
    ("lavavajillas", "洗碗机"),
    ("Apilable", "可堆叠"),
    ("Extraíble", "可拆卸"),
    ("Extraible", "可拆卸"),
    ("Desmontable", "可拆卸"),
    ("Reciclable", "可回收"),
    ("Reutilizable", "可重复使用"),
    ("uds", "件"),
    ("unidades", "件"),
    ("unidad", "件"),
    ("Sí", "是"),
]

# 摘要结构化标签：西语 → 中文
SUMMARY_LABELS = {
    "Tipo": "类型",
    "Material": "材质",
    "Dimensiones": "尺寸",
    "Capacidad": "容量",
    "Contenido": "含量",
    "Color": "颜色",
    "Caract.": "特点",
    "Uso": "用途",
    "Origen": "产地",
    "Certif.": "认证",
    "Apto": "适用",
}
SP_LABEL_STARTS = tuple(k + ":" for k in SUMMARY_LABELS) + tuple(k + "：" for k in SUMMARY_LABELS)

# 末尾卖点句逐条手译（西语 → 中文；不含第三方）
FEATURE_ZH = {
    "B0CL169YC8": "可装下全部食物的午餐盒：我们的迷你分格便当盒尺寸足够…",
    "B081RXYR2Q": "使用 lässig adventure 系列的儿童不锈钢便当盒",
    "B0DHGR3WSS": "❄️保持理想温度：采用高质量隔热设计，可…",
    "B0B56CHMSC": "保温：这款午餐袋采用四层保温，让食物保持低温…",
    "B0BJV3WC3W": "[ 1",
    "B071HSRTJN": "高效保温：内衬采用优质 PEVA 材质，填充 EPE 泡沫…",
    "B0946PG1LX": "份量分装收纳套装",
    "B0CVLDSJSL": "便当盒：这款汪汪队立大功 Pups 便当盒让多变的餐食像游戏一样简单",
    "B084Q5MXHV": "18件套装：含9个不同尺寸的容器和9个盖子",
    "B0DNJV9KTP": "耐用的圣诞树收纳袋",
    "B0CDQ6YBV3": "耐用材质：这款收纳袋采用耐用的210D牛津布制成",
    "B0FP1FYWNY": "学习教育主题布艺收纳箱：蓝白底色，彩虹元素…",
    "B0FQJQH52B": "[日月光精美设计]：这款木质展示架采用天体图案，融合哥特…",
    "B095HS2STF": "[我把树放哪了？] 如果这正是你每年装饰家里那天都会问自己的问题…",
    "B077GF7N23": "适用于人造圣诞树的绿色/红色收纳袋",
    "B0G5Y7WZY6": "粉色布艺收纳箱：棕熊在云朵上欢乐嬉戏、休息摇摆…",
    "B01M4J490L": "相当于3升非浓缩配方",
    "B0DHXVSF49": "SPARES2GO 适用于 Bissell 地毯清洗机的软管和手柄",
    "B0F89TKFVX": "【注意】本产品仅为吸尘器支架",
    "B079994TFQ": "适用于所有 BISSELL 地毯和污渍清洗机的理想配件，可完成…",
    "B0H36B12CJ": "完美兼容：适用于小米 X20 Max / X20 Pro 扫地机器人的配件套装",
    "B01M29LLYG": "Kobosan Active - 地毯护理粉，Vorwerk Kobold 原装（1件，500克）",
    "B0GL8CG63F": "【恢复地板自然光泽】这款地板修复剂同时可作为…",
    "B0FLKFZQL1": "【兼容 GREEN 1425 系列】：这款死角清洁工具专为…",
    "B0DHLM8FHM": "✔️ 兼容性：我们的吸尘器支架兼容 Dyson 无线型号…",
    "B0GQ4C28RF": "SPARES2GO 适用于 Bissell 地毯清洗机的替换软管",
    "B079967QGC": "BISSELL 地毯多用配件：适合对各种地毯进行彻底清洁…",
    "B0H6DVKF35": "【兼容型号】：适用于 DREAME D10s、D10S Pro、D9 Max Gen 2、D… 的清洁拖布配件",
    "B0F23CYQX3": "兼容型号：适用于 Dreame R20 T20 T30 R30 V12 V12 Pro T20 Pro V11SE R10… 的滚刷",
    "B0DVC8FBXS": "由96%天然来源成分制成",
    "B0G21MHKZ3": "传统设计：灵感源自50年代的经典清洁工具，非常适合…",
    "B0DKJTV733": "由95%天然来源成分制成，家居芳香，清新木质香",
    "B01E6ZFV3U": "防水浴帘，可单独使用或作内衬。",
    "B0CH85YRTN": "高品质材料：这款 Ibergrif 卷纸架采用不锈钢…",
    "B00ZPZA3TI": "可拆卸马桶座铰链套装",
    "B08C5NT656": "两种使用方式：马桶刷套装可稳定立于地面；附赠贴纸可…",
    "B0GXJQMB4S": "快速干燥超强吸水：SVET 硅藻土地垫（60×40cm）可迅速吸水…",
    "B09FLCGXHB": "【耐用】地漏盖采用优质硅胶材料制成，柔软轻便…",
    "B0C58PNZBK": "【更纯净健康的水：3层过滤】我们的花洒呵护您的皮肤和头发…",
    "B08Y51NJ5F": "抗菌马桶刷：带柔软硅胶刷头的马桶刷…",
    "B0B3R9N1ZP": "高品质材料：Ibergrif 马桶盖采用耐用的 PP 材质，抗…",
    "B0CG9N8R8L": "卓越防氧化：淋浴置物架采用 SUS304 不锈钢制成…",
    "B0CH85Q6WK": "高品质材料：这款 Ibergrif 卷纸架采用不锈钢…",
    "B09YY6J1VR": "超强吸附防滑垫：我们的防滑浴缸垫采用…",
    "B0B4P8T84Z": "告别水渍：轻松避免难看的痕迹和钙垢积聚…",
    "B08HPRXGX9": "您的浴缸、淋浴区、楼梯或船只都应是安全之所，不应存在滑倒的可能…",
    "B0D8W598TN": "坚固耐用：毛巾架采用304不锈钢制成，防水…",
    "B0CPH8B2DN": "强力除垢：理想除垢液，可去除各类设备中的水垢…",
    "B000CELRGU": "必要清洁：为保证咖啡机正常运作，需定期进行清洁循环…",
    "B0741JVML1": "除垢剂：本产品可去除全自动和半自动咖啡机内的水垢…",
    "B010RLCH2U": "咖啡机收纳抽屉，最多可容纳50颗 Nespresso 胶囊。",
    "B0CX9BVT8Z": "🌍 100%环保：仅采用食品级柠檬酸配方，无香精…",
    "B077XZ621G": "✅ 适用于所有品牌和型号：通用除垢剂可轻松…",
    "B00CVTVAAM": "咖啡机除垢套装。",
    "B00DJVJ37S": "使用 Bosch 清洁除垢片，让您的 TASSIMO 咖啡机保持最佳状态…",
    "B074KQFRNV": "最佳保养：有效去除咖啡机所有水路中的水垢…",
    "B00HWCC3J0": "Tefal MS622718 - 原装压板",
    "B000KNHFJQ": "清洁片：这些咖啡机清洁片确保咖啡机系统达到最高卫生标准…",
    "B08N5DVKDT": "除垢",
    "B00H7ZELTC": "适用于咖啡机的原装替换除垢剂。",
    "B0CMQXVSPF": "[食品级安全不锈钢] - 压粉器采用高纯度不锈钢制成…",
    "B00B824KZ0": "4×100ml，适用于全自动意式咖啡机（ESAM - ECAM）的天然除垢剂",
    "B0060KKKFY": "兼容型号：XP 9000、XP 7180、XP 7200、XP 7210、XP 7220、XP 7240、XP 7250、EA 8000…",
    "B09T3L346B": "适合小型作业：Kärcher K3 高压清洗机适合轻度污渍，可清洁…",
    "B002X3IDBK": "有机生物链锯油，提供出色的防磨损保护",
    "B005ZUD9AI": "由耐用的塑料制成",
    "B0BM5L4DKK": "SEESII 明星产品：这款无线电锯通过了无数用户测试…",
    "B0DHRXMB55": "【高品质】我们的炉灶密封绳采用玻璃纤维材料制成…",
    "B0CNJV26X5": "高效刷头：配备2个10厘米高品质刷头…",
    "B009E7JGBY": "适用于炉灶、内嵌式壁炉、灶具和烤箱的玻璃清洁剂",
    "B0DRFVVJ45": "【2件装浴室收纳架】这款淋浴收纳架含2个浴室置物架",
    "B00FAMEG4E": "400W 高效电机，实现最大流量和最低能耗。",
    "B00B18KAEG": "提手",
    "B001CV02U4": "最大流量每分钟8升",
    "B008HRFFJE": "直径：4、5、6、6、7、8、10毫米",
    "B00K7Y5HT8": "出色的耐压性能",
    "B0DBSZQG44": "焊丝材料为 Sn99.3%、Cu0.7%、松香2.0%，是最经济…",
}


# ---------- 转换工具 ----------
def _dec_comma(s):
    """西语小数点逗号 → 点（仅在数字之间）"""
    return re.sub(r'(?<=\d),(?=\d)', '.', s)


_DIM_RE1 = re.compile(
    r'^([\d.]+)\s*l\.\s*x\s*([\d.]+)\s*an\.\s*x\s*([\d.]+)\s*al\.\s*(centímetros|milímetros|metros)?',
    re.IGNORECASE)
_DIM_RE2 = re.compile(
    r'^([\d.]+)\s*l\.\s*x\s*([\d.]+)\s*an\.\s*(centímetros|milímetros|metros)?',
    re.IGNORECASE)
_DIM_RE3 = re.compile(
    r'^([\d.]+)\s*l\.\s*x\s*([\d.]+)\s*al\.\s*(centímetros|milímetros|metros)?',
    re.IGNORECASE)
_DIM_RE4 = re.compile(
    r'^([\d.]+)\s*x\s*([\d.]+)\s*x\s*([\d.]+)\s*(cm|mm|m|centímetros)?',
    re.IGNORECASE)
_UNIT_CM = {'centímetros': '厘米', 'milímetros': '毫米', 'metros': '米', 'cm': '厘米', 'mm': '毫米', 'm': '米', '': ''}


def _dim_cn(m):
    parts = [p for p in m.groups() if p and p.lower() not in ('cm', 'mm', 'm', 'centímetros', 'milímetros', 'metros')]
    unit = ''
    for p in m.groups():
        if p and p.lower() in _UNIT_CM:
            unit = _UNIT_CM[p.lower()]
    labels = ['长', '宽', '高'][:len(parts)]
    return '×'.join('%s%s' % (lab, p) for lab, p in zip(labels, parts)) + unit


def translate_value(v):
    """把西语值字符串确定性转换为中文（单位/材质/颜色/尺寸格式）"""
    if not v:
        return ''
    s = _dec_comma(str(v).strip())
    m = _DIM_RE1.match(s) or _DIM_RE2.match(s) or _DIM_RE3.match(s) or _DIM_RE4.match(s)
    if m:
        # 只替换匹配到的尺寸段，保留其余部分（如 "；250 g" 的重量）
        s = s[:m.start()] + _dim_cn(m) + s[m.end():]
    else:
        s = re.sub(r'\s+x\s+', '×', s)
    # 单位/材质/颜色词典（按长度降序，避免部分重叠）
    for es, zh in sorted(TERMS, key=lambda t: -len(t[0])):
        s = re.sub(r'\b' + re.escape(es) + r'\b', zh, s)
    # 件数 "1.0件"/"1.0 件" → "1件"
    s = re.sub(r'(?<=\d)\s*\.0\s*(?=件)', '', s)
    s = re.sub(r'(?<=\d)\s+(?=件)', '', s)
    # 数字与中文单位之间的空格去掉："350 克" → "350克"
    s = re.sub(r'(?<=\d)\s+(?=[克升毫升瓦件磅千米])', '', s)
    return s.strip()


def spec_zh_from(spec_es):
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


def summary_zh_from(summary_es, asin):
    if not summary_es:
        return ''
    segs = [x.strip() for x in summary_es.split('；') if x.strip()]
    out = []
    for i, seg in enumerate(segs):
        is_last = (i == len(segs) - 1)
        if is_last and not seg.startswith(SP_LABEL_STARTS):
            # 末尾卖点句：优先用逐条手译
            zh = FEATURE_ZH.get(asin)
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


# ---------- 主流程 ----------
# 1. 合并 4 批商品名手译
titles = {}
for i in range(1, 5):
    p = os.path.join(OUTDIR, '_titles_part%d.json' % i)
    with open(p, encoding='utf-8') as f:
        titles.update(json.load(f))

# 2. 读取选品数据
with open(SEL, encoding='utf-8') as f:
    records = json.load(f)

missing_t = [r['asin'] for r in records if r['asin'] not in titles]
transl = {}
for r in records:
    a = r['asin']
    # 按 row_idx 作 key：7 个重复 ASIN 的两行内容可能不同（有的行 details 为空），
    # 每行翻译严格对应自己行的西语内容，避免 ASIN key 被覆盖。
    key = str(r['row_idx'])
    transl[key] = {
        'asin': a,
        'title_zh': titles.get(a, ''),
        'spec_zh': spec_zh_from(r['spec_es']),
        'summary_zh': summary_zh_from(r['summary_es'], a),
    }

with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(transl, f, ensure_ascii=False, indent=1)

# ---------- 统计 ----------
n_title = sum(1 for v in transl.values() if v['title_zh'])
n_spec = sum(1 for v in transl.values() if v['spec_zh'])
n_summary = sum(1 for v in transl.values() if v['summary_zh'])
n_spec_src = sum(1 for r in records if r['spec_es'])
n_sum_src = sum(1 for r in records if r['summary_es'])
print('商品名中文: %d/200 | 规格中文: %d/%d | 摘要中文: %d/%d' % (n_title, n_spec, n_spec_src, n_summary, n_sum_src))
print('缺商品名翻译 ASIN:', missing_t if missing_t else '无')
print('已写出:', OUT)
