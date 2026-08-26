/**
 * Amazon.es 商品详情批量提取脚本（v2，已修正价格/库存/BSR 选择器）
 * 运行环境：Playwright (browser_run_code_unsafe, async (page) => {...})
 * 输入：ASIN 列表（本文件顶部）
 * 输出：由调用方落盘为 E:/amazon_es/product_details.json 与 product_details.csv
 * 字段：标题、现价、划线价、评分、评论数、库存、BSR、卖家、品牌、已选规格、是否亚马逊自营
 *
 * 说明：
 * - 每个商品页等待价格/库存元素出现后再提取，避免抢跑导致字段为空
 * - 页间等待 2 秒，降低触发反爬验证的概率
 * - BSR 只取主排名（"n.º N en 类目"），在 "(" 处截断
 */
async (page) => {
  // —— 待提取的 ASIN 列表（来自畅销榜 TOP 30）——
  const ASINS = [
    'B078C6QR1C','B075JJRFVV','B07RN64P2R','B0H1H86BF3','B008YETL18','B0BZVH1KZD',
    'B01KOAJ5M4','B0D3VCV459','B00Y0OYIFU','B084H8X4SW','B0BSXDVQG7','B0GGN7Z8VX',
    'B08PKWD87W','B01M66MBWZ','B0BY93HZHR','B0C7SBTGYZ','B07PDHNRND','B09X5GL8SL',
    'B0C9JJZ5RD','B01N9XBDTI','B0D1KCLVPX','B0812BKN39','B0BWNC18MM','B00GKBQKP2',
    'B0B57J6FFY','B07ZHF4FVK','B0DH566SV6','B07YG63BQJ','B07P1328L3','B0C7CVYDYN'
  ];

  const results = [];

  for (const asin of ASINS) {
    const row = { asin };
    try {
      await page.goto('https://www.amazon.es/dp/' + asin, {
        waitUntil: 'domcontentloaded',
        timeout: 45000,
      });
      // 等待关键区域出现：价格 或 库存 或 缺货标识（任一生效即继续）
      await page
        .waitForSelector('#corePrice_feature_div, #corePriceDisplay_desktop_feature_div, #availability, #outOfStock', { timeout: 20000 })
        .catch(() => {});
      // 额外等待价格真正渲染出数字
      try {
        await page.waitForFunction(() => {
          const t = document.querySelector('#corePrice_feature_div .a-price .a-offscreen, .apex-pricetopay-value .a-offscreen, .priceToPay .a-offscreen');
          return t && t.textContent.trim().length > 0;
        }, { timeout: 10000 });
      } catch (e) {}
      await page.waitForTimeout(1500);

      const data = await page.evaluate(() => {
        const q = (s) => document.querySelector(s);
        const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
        const isCaptcha = /Captcha|Type the characters|resolver el captcha/i.test(document.body ? document.body.innerText.slice(0, 300) : '');

        const title = clean(q('#productTitle')?.textContent);

        // 现价（主 BuyBox 价格）
        const priceEl =
          q('#corePrice_feature_div .a-price .a-offscreen') ||
          q('#corePriceDisplay_desktop_feature_div .a-price .a-offscreen') ||
          q('.apex-pricetopay-value .a-offscreen') ||
          q('.priceToPay .a-offscreen');
        const price = priceEl ? clean(priceEl.textContent) : '';

        // 划线价（原价）——仅取 corePrice 区域的 .a-text-price，排除 per-unit 价格
        const listEl = q('#corePrice_feature_div .a-text-price .a-offscreen, #corePriceDisplay_desktop_feature_div .a-text-price .a-offscreen');
        const listPrice = listEl ? clean(listEl.textContent) : '';

        // 评分
        const ratingEl = q('#acrPopover .a-icon-alt, #averageCustomerReviews_feature_div .a-icon-alt');
        const rating = ratingEl ? clean(ratingEl.textContent) : '';

        // 评论数
        const revEl = q('#acrCustomerReviewText');
        const reviews = revEl ? clean(revEl.textContent) : '';

        // 库存状态
        const availEl =
          q('#availability .a-color-success') ||
          q('#availability .a-color-price') ||
          q('#availability .a-declarative span') ||
          q('#availability span') ||
          q('#outOfStock .a-size-medium') ||
          q('#outOfStock .a-color-error');
        const availability = availEl ? clean(availEl.textContent) : '';

        // BSR 主排名：仅匹配 "n.º N en 类目"，在 "(" 处截断
        let bsr = '';
        const detailEl = q('#detailBulletsWrapper_feature_div, #prodDetails, #SalesRank');
        if (detailEl) {
          const m = detailEl.textContent.match(/(?:n\.º)\s*([\d.,]+)\s*en\s*([^(|\n]{0,50})/);
          if (m) bsr = 'n.º ' + m[1] + ' en ' + clean(m[2]);
        }

        // 卖家（BuyBox "Vendido por X"）
        let seller = '';
        const merchantEl = q('#merchantInfoFeature_feature_div a, #sellerProfileTriggerId');
        if (merchantEl) seller = clean(merchantEl.textContent);
        if (!seller) {
          const bb = q('#tabular-buybox .tabular-buybox-text[role="text"] span');
          seller = bb ? clean(bb.textContent) : '';
        }

        // 是否亚马逊自营 / 亚马逊配送
        const buyboxText = clean(q('#buybox')?.textContent) || '';
        const soldByAmazon = seller === 'Amazon' || /Vendido y enviado por Amazon|Vendido por Amazon/i.test(buyboxText);
        const fulfilledByAmazon = /Enviado por Amazon/i.test(buyboxText) || /Vendido y enviado por Amazon/i.test(buyboxText);

        // 品牌（Byline）
        let brand = '';
        const byline = q('#bylineInfo');
        if (byline) brand = clean(byline.textContent.replace(/^Visita la tienda de\s*/i, '').replace(/^Marca:\s*/i, ''));

        // 已选规格（变体）
        let variant = '';
        const varEl = q('#variation_name .selection, #twister-plus-name-feature .selection, .twister-plus-buying-options-price-data .selection');
        if (varEl) variant = clean(varEl.textContent);

        return { isCaptcha, title, price, listPrice, rating, reviews, availability, bsr, seller, brand, variant, soldByAmazon, fulfilledByAmazon };
      });

      Object.assign(row, data);
    } catch (e) {
      row.error = String(e).slice(0, 300);
    }
    results.push(row);
  }

  return JSON.stringify(results, null, 0);
}
