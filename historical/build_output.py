# -*- coding: utf-8 -*-
"""
Amazon.es 家居厨房畅销品数据合并脚本
读取 amazon_es_home_kitchen_bestsellers.csv（榜单数据），
合并三轮详情页提取的商品详情（价格/卖家/品牌/库存/自营/划线价），
输出 product_details.csv 与 product_details.json。

编码：UTF-8 with BOM（utf-8-sig），保证 Excel 直接打开不乱码。
"""
import csv
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
LIST_CSV = os.path.join(BASE, "amazon_es_home_kitchen_bestsellers.csv")
OUT_CSV = os.path.join(BASE, "product_details.csv")
OUT_JSON = os.path.join(BASE, "product_details.json")

# —— 详情数据（key = ASIN）——
# price/listPrice 为字符串（保留原始格式 "12,62"），seller/brand 为卖家/品牌，
# soldByAmazon 是否亚马逊自营，availability 库存状态。
DETAIL = {
    "B078C6QR1C": {"price": "12,62", "listPrice": "13,29", "seller": "Utopia Brands", "brand": "Utopia Bedding", "soldByAmazon": False, "availability": "En stock"},
    "B075JJRFVV": {"price": "16,98", "listPrice": "", "seller": "Utopia Brands", "brand": "Utopia Bedding", "soldByAmazon": False, "availability": "En stock"},
    "B07RN64P2R": {"price": "13,52", "listPrice": "", "seller": "Amazon", "brand": "Amazon Basics", "soldByAmazon": True, "availability": "En stock"},
    "B0H1H86BF3": {"price": "29,99", "listPrice": "", "seller": "Amazon", "brand": "Todocama", "soldByAmazon": True, "availability": "En stock"},
    "B008YETL18": {"price": "31,99", "listPrice": "", "seller": "多卖家（多购买选项）", "brand": "De'Longhi", "soldByAmazon": False, "availability": "En stock"},
    "B0BZVH1KZD": {"price": "16,70", "listPrice": "", "seller": "—", "brand": "Super Sparrow", "soldByAmazon": False, "availability": "En stock"},
    "B01KOAJ5M4": {"price": "9,99", "listPrice": "", "seller": "Haberdashery Online", "brand": "Haberdashery Online", "soldByAmazon": False, "availability": "En stock"},
    "B0D3VCV459": {"price": "7,50", "listPrice": "", "seller": "Amazon", "brand": "edihome", "soldByAmazon": True, "availability": "En stock"},
    "B00Y0OYIFU": {"price": "38,99", "listPrice": "", "seller": "Amazon", "brand": "PIKOLIN", "soldByAmazon": True, "availability": "En stock"},
    "B084H8X4SW": {"price": "17,50", "listPrice": "", "seller": "Amazon", "brand": "Amazon Basics", "soldByAmazon": True, "availability": "En stock"},
    "B0BSXDVQG7": {"price": "28,52", "listPrice": "", "seller": "Amazon", "brand": "BRITA", "soldByAmazon": True, "availability": "En stock"},
    "B0GGN7Z8VX": {"price": "9,49", "listPrice": "9,99", "seller": "TrendPlain", "brand": "TrendPlain", "soldByAmazon": False, "availability": "En stock"},
    "B08PKWD87W": {"price": "44,99", "listPrice": "", "seller": "Amazon", "brand": "Cecotec", "soldByAmazon": True, "availability": "En stock"},
    "B01M66MBWZ": {"price": "17,99", "listPrice": "", "seller": "Amazon", "brand": "Amazon Basics", "soldByAmazon": True, "availability": "En stock"},
    "B0BY93HZHR": {"price": "31,99", "listPrice": "34,99", "seller": "Amazon", "brand": "Rowenta", "soldByAmazon": True, "availability": "En stock"},
    "B0C7SBTGYZ": {"price": "6,79", "listPrice": "", "seller": "Amazon", "brand": "PORTENTUM", "soldByAmazon": True, "availability": "En stock"},
    "B07PDHNRND": {"price": "15,99", "listPrice": "", "seller": "Utopia Brands", "brand": "Utopia Bedding", "soldByAmazon": False, "availability": "En stock"},
    "B09X5GL8SL": {"price": "10,99", "listPrice": "", "seller": "Amazon", "brand": "Todocama", "soldByAmazon": True, "availability": "En stock"},
    "B0C9JJZ5RD": {"price": "17,93", "listPrice": "", "seller": "Amazon", "brand": "Degrees home", "soldByAmazon": True, "availability": "En stock"},
    "B01N9XBDTI": {"price": "21,99", "listPrice": "31,99", "seller": "Amazon", "brand": "PHILIPS", "soldByAmazon": True, "availability": "En stock"},
    "B0D1KCLVPX": {"price": "94,99", "listPrice": "159,00", "seller": "HUIZHENG TECO", "brand": "VACTechPro", "soldByAmazon": False, "availability": "En stock"},
    "B0812BKN39": {"price": "8,95", "listPrice": "", "seller": "—", "brand": "Arcos", "soldByAmazon": False, "availability": "En stock"},
    "B0BWNC18MM": {"price": "8,49", "listPrice": "", "seller": "BMS España", "brand": "Dreamzie", "soldByAmazon": False, "availability": "En stock"},
    "B00GKBQKP2": {"price": "0,88", "listPrice": "", "seller": "Amazon", "brand": "APLI", "soldByAmazon": True, "availability": "En stock"},
    "B0B57J6FFY": {"price": "", "listPrice": "", "seller": "—", "brand": "Cecotec", "soldByAmazon": False, "availability": "Agotado temporalmente"},
    "B07ZHF4FVK": {"price": "17,99", "listPrice": "", "seller": "Utopia Brands", "brand": "Utopia Bedding", "soldByAmazon": False, "availability": "En stock"},
    "B0DH566SV6": {"price": "9,49", "listPrice": "", "seller": "Flowen ES", "brand": "Flowen", "soldByAmazon": False, "availability": "En stock"},
    "B07YG63BQJ": {"price": "18,17", "listPrice": "26,95", "seller": "Amazon", "brand": "BRITA", "soldByAmazon": True, "availability": "En stock"},
    "B07P1328L3": {"price": "42,90", "listPrice": "", "seller": "Amazon", "brand": "Todocama", "soldByAmazon": True, "availability": "En stock"},
    "B0C7CVYDYN": {"price": "10,84", "listPrice": "15,95", "seller": "Amazon", "brand": "BRITA", "soldByAmazon": True, "availability": "En stock"},
}

# —— 读取榜单 CSV ——
rows = []
with open(LIST_CSV, "r", encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)
    for r in reader:
        rows.append(r)

# —— 合并详情并输出 ——
details = []
for r in rows:
    asin = r["ASIN"].strip()
    d = DETAIL.get(asin, {})
    bsr = "n.º %s en Hogar y cocina" % r["Rank"].strip()
    merged = {
        "Rank": r["Rank"].strip(),
        "ASIN": asin,
        "Title": r["Title"].strip(),
        "Price_EUR": d.get("price", ""),
        "ListPrice_EUR": d.get("listPrice", ""),
        "Rating": r["Rating"].strip(),
        "Reviews": r["Reviews"].strip(),
        "Availability": d.get("availability", ""),
        "BSR": bsr,
        "Seller": d.get("seller", ""),
        "Brand": d.get("brand", ""),
        "SoldByAmazon": "Sí" if d.get("soldByAmazon") else "No",
        "URL": r["URL"].strip(),
    }
    details.append(merged)

# 写 JSON
with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(details, f, ensure_ascii=False, indent=2)

# 写 CSV（utf-8-sig，Excel 友好）
fields = ["Rank", "ASIN", "Title", "Price_EUR", "ListPrice_EUR", "Rating", "Reviews",
          "Availability", "BSR", "Seller", "Brand", "SoldByAmazon", "URL"]
with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(details)

# —— 同时回填榜单 CSV 中缺失的价格（#5/#22/#25）——
# 若原文件被 Excel/编辑器占用，则改写为 _updated 版本
list_fields = ["Rank", "Title", "ASIN", "Price_EUR", "Rating", "Reviews", "URL"]
for r in rows:
    asin = r["ASIN"].strip()
    if not r["Price_EUR"].strip():
        r["Price_EUR"] = DETAIL.get(asin, {}).get("price", "")
list_target = LIST_CSV
try:
    with open(LIST_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list_fields)
        writer.writeheader()
        writer.writerows(rows)
except PermissionError:
    list_target = os.path.join(BASE, "amazon_es_home_kitchen_bestsellers_updated.csv")
    with open(list_target, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list_fields)
        writer.writeheader()
        writer.writerows(rows)
    print("警告：原榜单 CSV 正被其他程序占用，已改写为 %s" % list_target)

# 统计
n_price = sum(1 for d in details if d["Price_EUR"])
n_amazon = sum(1 for d in details if d["SoldByAmazon"] == "Sí")
print("完成。共 %d 条商品。" % len(details))
print("有价格: %d / %d；亚马逊自营: %d。" % (n_price, len(details), n_amazon))
print("已写入: %s" % OUT_CSV)
print("已写入: %s" % OUT_JSON)
