const chatForm = document.querySelector("#chatForm");
const goalInput = document.querySelector("#goalInput");
const sendButton = document.querySelector("#sendButton");
const attachmentInput = document.querySelector("#attachmentInput");
const attachmentList = document.querySelector("#attachmentList");
const chatMessages = document.querySelector("#chatMessages");
const recommendedProducts = document.querySelector("#recommendedProducts");
const productMarketContent = document.querySelector("#productMarketContent");
const cartContent = document.querySelector("#cartContent");
const cartMiniText = document.querySelector("#cartMiniText");
const cartCountBadge = document.querySelector("#cartCountBadge");
const pcBuildPlanContent = document.querySelector("#pcBuildPlanContent");
const statusDot = document.querySelector("#statusDot");
const statusText = document.querySelector("#statusText");
const catalogSearchInput = document.querySelector("#catalogSearchInput");
const screens = Array.from(document.querySelectorAll(".screen"));
const stageButtons = Array.from(document.querySelectorAll("[data-nav-screen]"));

let SESSION_ID = `web-${Date.now()}`;
const CATEGORY_LABELS = {
  beauty: "美妆护肤",
  digital: "数码电子",
  clothing: "服饰运动",
  clothes: "服饰运动",
  food: "食品生活",
  pc_cpu: "CPU",
  pc_gpu: "显卡",
  pc_motherboard: "主板",
  pc_memory: "内存",
  pc_storage: "硬盘",
  pc_psu: "电源",
  pc_case: "机箱",
  pc_cooler: "散热",
};
const PC_CATEGORY_VALUES = ["pc_cpu", "pc_gpu", "pc_motherboard", "pc_memory", "pc_storage", "pc_psu", "pc_case", "pc_cooler"];
const PRODUCT_PAGE_SIZE = 120;

let selectedAttachments = [];
let cartState = { items: [], total_price: 0, count: 0, currency: "CNY" };
let productCatalog = [];
let productFacets = { categories: [], brands: [] };
let productFilters = { category: "", brand: "", q: "" };
let productMarketVisibleCount = PRODUCT_PAGE_SIZE;
let lastProductCards = [];
let lastComparisonRows = [];
let currentPcBuildPlan = null;
let selectedProductDetail = null;
let isBusy = false;

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  sendDemand();
});

attachmentInput.addEventListener("change", (event) => {
  selectedAttachments = Array.from(event.target.files || []).slice(0, 4).map((file) => ({
    file,
    name: file.name,
    type: file.type,
    size: file.size,
    analysis_status: "pending",
  }));
  renderAttachments();
});

catalogSearchInput.addEventListener("input", debounce((event) => {
  productFilters.q = event.target.value.trim();
  if (document.querySelector("#screen-products").classList.contains("active")) {
    loadProducts(true);
  }
}, 220));

document.querySelectorAll("[data-nav-screen]").forEach((button) => {
  button.addEventListener("click", () => showScreen(button.dataset.navScreen));
});

document.querySelectorAll("[data-go-screen]").forEach((button) => {
  button.addEventListener("click", () => showScreen(button.dataset.goScreen));
});

document.querySelectorAll("[data-example]").forEach((button) => {
  button.addEventListener("click", () => {
    goalInput.value = button.dataset.example;
    goalInput.focus();
  });
});

document.querySelectorAll("[data-focus-composer]").forEach((button) => {
  button.addEventListener("click", () => goalInput.focus());
});

document.querySelectorAll("[data-category]").forEach((button) => {
  button.addEventListener("click", () => {
    productFilters.category = button.dataset.category;
    showScreen("products");
    loadProducts(true);
  });
});

// ── 新建对话 ──
document.querySelector("#newChatButton")?.addEventListener("click", () => {
  if (isBusy) return;
  SESSION_ID = `web-${Date.now()}`;
  cartState = { items: [], total_price: 0, count: 0, currency: "CNY" };
  lastProductCards = [];
  lastComparisonRows = [];
  currentPcBuildPlan = null;
  selectedProductDetail = null;
  selectedAttachments = [];
  goalInput.value = "";
  attachmentInput.value = "";
  renderAttachments();
  renderInitialChat();
  resetRecommendationPanel();
  recommendedProducts.className = "recommendation-strip empty-state";
  recommendedProducts.textContent = "推荐商品和对比表会显示在这里。";
  renderCart();
  setStatus("等待需求", "");
  showScreen("demand");
});

// ── Enter 发送，Shift+Enter 换行 ──
goalInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    if (!isBusy && goalInput.value.trim()) sendDemand();
  }
});

renderInitialChat();
renderCart();
renderPcBuildPlan();

function renderInitialChat() {
  chatMessages.innerHTML = "";
  appendMessage(
    "assistant",
    "你好，我是 MallMind。你可以直接告诉我预算、品类、用途和偏好，我会只从本地商品库里推荐真实商品，也可以继续追问调整。"
  );
}

// 🟣 v4: 直接发送一条消息（供追问按钮等场景调用）
function sendGoalMessage(message) {
  if (!message || isBusy) return;
  goalInput.value = message;
  sendDemand();
}

async function sendDemand() {
  const message = goalInput.value.trim();
  if (!message || isBusy) return;

  setBusy(true, "正在生成导购回复");
  appendMessage("user", message);
  goalInput.value = "";
  lastComparisonRows = [];
  resetRecommendationPanel();
  const assistantNode = appendMessage("assistant", "");

  try {
    const analyzedAttachments = await analyzeSelectedAttachments();
    await streamChat(
      {
        session_id: SESSION_ID,
        message,
        attachments: analyzedAttachments,
        images: [],
      },
      (event, data) => handleChatEvent(event, data, assistantNode)
    );
    selectedAttachments = [];
    attachmentInput.value = "";
    renderAttachments();
    setStatus("推荐完成", "active");
  } catch (error) {
    assistantNode.textContent += `\n${error.message || "推荐失败，请稍后再试。"}`;
    setStatus(error.message || "推荐失败", "error");
  } finally {
    setBusy(false);
  }
}

async function analyzeSelectedAttachments() {
  if (!selectedAttachments.length) return [];

  setBusy(true, "正在解析图片");
  selectedAttachments = selectedAttachments.map((item) => ({
    ...item,
    analysis_status: "analyzing",
  }));
  renderAttachments();

  try {
    const attachments = await Promise.all(
      selectedAttachments.map(async (item) => ({
        name: item.name,
        type: item.type,
        size: item.size,
        data_url: await readFileAsDataURL(item.file),
      }))
    );
    const data = await postJson("/api/analyze-attachments", { attachments });
    const analyzed = data.attachments || [];
    selectedAttachments = selectedAttachments.map((item, index) => ({
      ...item,
      ...(analyzed[index] || {}),
      file: item.file,
    }));
    renderAttachments();
    return analyzed.map(sanitizeAttachmentForChat);
  } catch (error) {
    selectedAttachments = selectedAttachments.map((item) => ({
      ...item,
      kind: "image",
      analysis_status: "fallback",
      analysis_source: "frontend_analysis_error",
      summary: `图片解析接口调用失败：${error.message || "未知错误"}。系统将仅根据图片元信息继续导购。`,
      extracted_text: "",
      signals: ["image_input", "analysis_failed"],
      shopping_hints: ["请结合文字需求继续推荐，必要时追问图片中的关键商品信息"],
      input_modalities: ["image"],
    }));
    renderAttachments();
    return selectedAttachments.map(sanitizeAttachmentForChat);
  }
}

function readFileAsDataURL(file) {
  return new Promise((resolve, reject) => {
    if (!file) {
      reject(new Error("图片文件不可用"));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("图片读取失败"));
    reader.readAsDataURL(file);
  });
}

function sanitizeAttachmentForChat(item) {
  const {
    file,
    data_url: _dataUrlSnake,
    dataUrl: _dataUrlCamel,
    ...safeAttachment
  } = item || {};
  return safeAttachment;
}

async function streamChat(payload, onEvent) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `${response.status} ${response.statusText}`);
  }
  if (!response.body) throw new Error("当前浏览器不支持流式响应。");

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    blocks.forEach((block) => {
      const parsed = parseSseBlock(block);
      if (parsed) onEvent(parsed.event, parsed.data);
    });
  }
  if (buffer.trim()) {
    const parsed = parseSseBlock(buffer);
    if (parsed) onEvent(parsed.event, parsed.data);
  }
}

function parseSseBlock(block) {
  const lines = block.split("\n");
  let event = "message";
  const dataLines = [];
  lines.forEach((line) => {
    if (line.startsWith("event: ")) event = line.slice(7);
    if (line.startsWith("data: ")) dataLines.push(line.slice(6));
  });
  if (!dataLines.length) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

function handleChatEvent(event, data, assistantNode) {
  if (event === "tool_call") {
    const name = data.name || "";
    const src = data.source || "";
    const TOOL_LABELS = {
      recommend_shopping_products: "商品推荐",
      compare_products: "商品对比",
      generate_pc_build_plan: "PC 装机",
      apply_cart_instruction: "购物车操作",
      general_chat: "闲聊",
      parameter_query: "参数查询",
      sku_detail: "SKU 详情",
      price_comparison: "比价",
    };
    const label = TOOL_LABELS[name] || name;
    const badge = document.createElement("div");
    badge.className = "tool-badge";
    badge.style.cssText = "display:inline-block;font-size:11px;padding:2px 8px;border-radius:6px;background:#e8f0fe;color:#1a73e8;margin:4px 0;font-family:monospace;";
    badge.textContent = `⚙ ${label}${src ? ` (${src})` : ""}`;
    assistantNode.parentElement?.insertBefore(badge, assistantNode);
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }
  if (event === "delta") {
    assistantNode.textContent += `${data.text || ""}\n`;
  }
  if (event === "progress") {
    appendProgress(data);
  }
  if (event === "attachment_analysis") {
    appendProgress({
      label: "图片解析完成",
      detail: data.summary || "图片附件已进入导购上下文。",
    });
  }
  if (event === "validation_error") {
    assistantNode.textContent += `${data.detail || "需求无法识别。"}\n`;
    setStatus(data.label || "需求无法识别", "error");
  }
  if (event === "product_cards") {
    lastProductCards = data.cards || data.products || [];
    renderRecommendedProducts(lastProductCards, lastComparisonRows);
  }
  if (event === "comparison_table") {
    lastComparisonRows = data.rows || [];
    renderRecommendedProducts(lastProductCards, lastComparisonRows);
  }
  if (event === "follow_up_questions") {
    const questions = data.questions || [];
    if (questions.length) assistantNode.textContent += `\n可以继续补充：${questions.join(" / ")}\n`;
  }
  if (event === "cart") {
    cartState = data.cart || cartState;
    renderCart();
  }
  // 🟣 v4: 购物车确认事件——展示操作计划，等待用户确认
  if (event === "cart_confirmation") {
    renderCartConfirmation(data, assistantNode);
  }
  // 🟣 v4: 购物车追问事件——操作不明确时展示选项
  if (event === "cart_clarification") {
    renderCartClarification(data, assistantNode);
  }
  if (event === "pc_build_plan") {
    currentPcBuildPlan = data;
    renderPcBuildPlan();
    renderPcBuildPlanInline(data);
  }
  if (event === "done") {
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }
}

function resetRecommendationPanel() {
  recommendedProducts.className = "recommendation-progress";
  recommendedProducts.innerHTML = `
    <div class="progress-head">
      <strong>正在检索商品库</strong>
      <span>实时进度</span>
    </div>
    <ol class="progress-list" id="progressList"></ol>
  `;
}

function appendProgress(item) {
  const list = recommendedProducts.querySelector("#progressList");
  if (!list) return;
  const row = document.createElement("li");
  row.className = "progress-item active";
  row.innerHTML = `
    <span class="progress-pulse"></span>
    <div>
      <strong>${escapeHtml(item.label || "处理中")}</strong>
      <p>${escapeHtml(item.detail || "")}</p>
    </div>
  `;
  Array.from(list.children).forEach((child) => child.classList.replace("active", "done"));
  list.appendChild(row);
  while (list.children.length > 8) list.removeChild(list.firstElementChild);
}

function renderRecommendedProducts(products, rows) {
  const cards = products || [];
  if (!cards.length && !(rows || []).length) {
    recommendedProducts.className = "recommendation-strip empty-state";
    recommendedProducts.textContent = "暂时没有候选商品。";
    return;
  }
  recommendedProducts.className = "recommendation-strip";
  recommendedProducts.innerHTML = `
    ${cards.length ? `<div class="product-card-grid">${cards.map(renderProductCard).join("")}</div>` : ""}
    ${(rows || []).length ? renderComparisonTable(rows) : ""}
    ${selectedProductDetail ? renderProductDetailPanel(selectedProductDetail) : ""}
  `;
  bindProductButtons(recommendedProducts);
}

function renderPcBuildPlanInline(plan) {
  recommendedProducts.className = "recommendation-strip";
  recommendedProducts.innerHTML = renderPcBuildPlanMarkup(plan);
  recommendedProducts.querySelector("[data-add-pc-build]")?.addEventListener("click", addPcBuildToCart);
}

function renderProductCard(product) {
  const id = product.product_id || "";
  const title = product.title || product.name || id || "未命名商品";
  const price = product.price ?? product.min_price ?? product.base_price;
  const tags = normalizeTags(product.tags || product.best_for || product.supported_scenarios).slice(0, 3);
  const brand = product.brand || "未知品牌";
  const stock = product.stock_status || (product.stock_quantity > 0 ? "available" : "demo");
  const skuCount = product.sku_count ?? (Array.isArray(product.skus) ? product.skus.length : 0);
  const rating = product.rating_avg ? `${Number(product.rating_avg).toFixed(1)} 分` : "暂无评分";
  const selected = selectedProductDetail?.product_id === id ? " selected" : "";
  return `
    <article class="product-card market-product-card${selected}" data-product-detail="${escapeHtml(id)}" tabindex="0" role="button" aria-label="查看 ${escapeHtml(title)} 详情">
      ${renderProductVisual(product, title)}
      <div class="product-card-body">
        <div class="product-card-head">
          <span>${formatPrice(price, product.currency || "CNY")}</span>
          <small>${escapeHtml(CATEGORY_LABELS[product.category] || product.category_name || product.category || "商品")}</small>
        </div>
        <h3>${escapeHtml(title)}</h3>
        <dl class="product-basic-info">
          <div><dt>品牌</dt><dd>${escapeHtml(brand)}</dd></div>
          <div><dt>库存</dt><dd>${escapeHtml(stockStatusLabel(stock))}</dd></div>
          <div><dt>SKU</dt><dd>${skuCount || 1}</dd></div>
          <div><dt>评分</dt><dd>${escapeHtml(rating)}</dd></div>
        </dl>
        <div class="product-tags">
          ${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}
        </div>
        <div class="product-actions">
          <button class="secondary-button" type="button" data-compare="${escapeHtml(id)}">对比</button>
          <button class="secondary-button" type="button" data-open-detail="${escapeHtml(id)}">详情</button>
          <button class="primary-button" type="button" data-add-cart="${escapeHtml(id)}">加入购物车</button>
        </div>
      </div>
    </article>
  `;
}

function renderProductDetailPanel(product) {
  if (!product) return "";
  const id = product.product_id || "";
  const title = product.title || product.name || id || "商品详情";
  const price = product.price ?? product.min_price ?? product.base_price;
  const tags = normalizeTags(product.tags || product.best_for || product.supported_scenarios).slice(0, 8);
  const skus = Array.isArray(product.skus) ? product.skus.slice(0, 4) : [];
  const reviews = Array.isArray(product.reviews) ? product.reviews.slice(0, 2) : [];
  const faqs = Array.isArray(product.faqs) ? product.faqs.slice(0, 2) : [];
  const specs = product.metadata?.specs && typeof product.metadata.specs === "object"
    ? Object.entries(product.metadata.specs).filter(([, value]) => value !== "" && value !== null && value !== undefined).slice(0, 10)
    : [];
  return `
    <aside class="product-detail-panel" aria-label="商品详情">
      <div class="product-detail-head">
        <div>
          <span class="section-eyebrow">Product Detail</span>
          <h3>${escapeHtml(title)}</h3>
        </div>
        <button class="detail-close-button" type="button" data-close-product-detail aria-label="关闭详情">×</button>
      </div>
      ${renderProductVisual(product, title)}
      <div class="product-detail-price">${formatPrice(price, product.currency || "CNY")}</div>
      <dl class="product-detail-facts">
        <div><dt>品牌</dt><dd>${escapeHtml(product.brand || "-")}</dd></div>
        <div><dt>分类</dt><dd>${escapeHtml(CATEGORY_LABELS[product.category] || product.category_name || product.category || "-")}</dd></div>
        <div><dt>库存</dt><dd>${escapeHtml(product.stock_status || "-")}${product.stock_quantity != null ? ` · ${product.stock_quantity}` : ""}</dd></div>
        <div><dt>评分</dt><dd>${escapeHtml(String(product.rating_avg ?? "-"))}${product.review_count ? ` · ${product.review_count} 条评价` : ""}</dd></div>
      </dl>
      ${product.description ? `<p class="product-detail-description">${escapeHtml(product.description)}</p>` : ""}
      ${tags.length ? `<div class="product-tags">${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>` : ""}
      ${skus.length ? `
        <section class="product-detail-section">
          <strong>SKU</strong>
          ${skus.map((sku) => `<p>${escapeHtml(sku.sku_id || "SKU")} · ${formatPrice(sku.price ?? price, product.currency || "CNY")} ${sku.properties ? `· ${escapeHtml(Object.values(sku.properties).join(" / "))}` : ""}</p>`).join("")}
        </section>
      ` : ""}
      ${specs.length ? `
        <section class="product-detail-section">
          <strong>关键参数</strong>
          ${specs.map(([key, value]) => `<p>${escapeHtml(key)}: ${escapeHtml(String(value))}</p>`).join("")}
        </section>
      ` : ""}
      ${reviews.length ? `
        <section class="product-detail-section">
          <strong>评价摘要</strong>
          ${reviews.map((review) => `<p>${escapeHtml(review.nickname || "用户")}：${escapeHtml(review.content || "")}</p>`).join("")}
        </section>
      ` : ""}
      ${faqs.length ? `
        <section class="product-detail-section">
          <strong>常见问题</strong>
          ${faqs.map((faq) => `<p>${escapeHtml(faq.question || "")} ${escapeHtml(faq.answer || "")}</p>`).join("")}
        </section>
      ` : ""}
      <div class="product-detail-actions">
        <button class="secondary-button" type="button" data-compare="${escapeHtml(id)}">对比</button>
        <button class="primary-button" type="button" data-add-cart="${escapeHtml(id)}">加入购物车</button>
      </div>
    </aside>
  `;
}

function renderComparisonTable(rows) {
  return `
    <section class="comparison-panel">
      <div class="comparison-head">
        <h3>候选对比</h3>
        <span>${rows.length} 个商品</span>
      </div>
      <div class="comparison-table-wrap">
        <table class="comparison-table">
          <thead><tr><th>商品</th><th>价格</th><th>评分</th><th>取舍</th></tr></thead>
          <tbody>
            ${rows.map((row) => `
              <tr>
                <td>${escapeHtml(row.title || row.product_id || "")}</td>
                <td>${formatPrice(row.price, row.currency || "CNY")}</td>
                <td>${escapeHtml(String(row.rating_avg ?? row.score ?? "-"))}</td>
                <td>${escapeHtml(row.tradeoff || row.reason || "")}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function renderPcBuildPlanMarkup(plan) {
  const checks = plan.compatibility?.checks || [];
  const reasons = plan.recommendation_reasons || [];
  const comparison = plan.comparison || null;
  return `
    <article class="pc-plan-summary">
      <div class="pc-plan-head">
        <div>
          <span class="section-eyebrow">Build Summary</span>
          <h3>${escapeHtml(plan.summary || "电脑整机方案")}</h3>
        </div>
        <span class="${plan.compatibility?.valid === false ? "compat-failed" : "compat-pass"}">
          ${plan.compatibility?.valid === false ? "需要复核" : "兼容通过"}
        </span>
      </div>
      <p>预算 ${formatPrice(plan.budget, plan.currency)}，总价 ${formatPrice(plan.total_price, plan.currency)}。</p>
      ${reasons.length ? `
        <section class="product-detail-section">
          <strong>推荐理由</strong>
          ${reasons.map((reason) => `<p>${escapeHtml(reason)}</p>`).join("")}
        </section>
      ` : ""}
      ${comparison ? renderPcPlanComparison(comparison) : ""}
      <div class="cart-actions">
        <button class="primary-button" type="button" data-add-pc-build>整机加入购物车</button>
      </div>
    </article>
    <article>
      <span class="section-eyebrow">Compatibility</span>
      <h3>兼容性校验</h3>
      <ul>
        ${checks.map((check) => `<li>${escapeHtml(check.name)}：${escapeHtml(check.detail || check.status)}</li>`).join("") || "<li>暂无兼容性校验信息。</li>"}
      </ul>
    </article>
    <article class="pc-parts-card">
      <span class="section-eyebrow">Parts</span>
      <h3>配件清单</h3>
      <div class="pc-parts-list">
        ${(plan.parts || plan.items || []).map(renderPcPartRow).join("")}
      </div>
    </article>
  `;
}

function renderPcPlanComparison(comparison) {
  const highlights = comparison.highlights || [];
  const changes = comparison.changes || [];
  return `
    <section class="product-detail-section">
      <strong>方案对比</strong>
      ${highlights.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}
      ${changes.length ? `
        <div class="comparison-table-wrap">
          <table class="comparison-table">
            <thead><tr><th>配件</th><th>之前</th><th>当前</th><th>依据</th></tr></thead>
            <tbody>
              ${changes.map((item) => `
                <tr>
                  <td>${escapeHtml(item.role_name || item.role || "")}</td>
                  <td>${escapeHtml(item.from || "-")}</td>
                  <td>${escapeHtml(item.to || "-")}</td>
                  <td>${escapeHtml(item.reason || "")}</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      ` : ""}
    </section>
  `;
}

function renderPcBuildPlan() {
  if (!currentPcBuildPlan) {
    pcBuildPlanContent.className = "placeholder-board";
    pcBuildPlanContent.innerHTML = `
      <article>
        <h3>还没有整机方案</h3>
        <p>在智能导购里输入“帮我配一台 7000 元以内的游戏电脑”之类的需求，生成后会自动显示在这里。</p>
      </article>
      <article>
        <h3>支持多轮调整</h3>
        <p>生成方案后可以继续说“显卡强一点”“再便宜 500”“换白色机箱”，系统会基于上一轮方案继续修改。</p>
      </article>
    `;
    return;
  }

  const plan = currentPcBuildPlan;
  const checks = plan.compatibility?.checks || [];
  pcBuildPlanContent.className = "placeholder-board";
  pcBuildPlanContent.innerHTML = renderPcBuildPlanMarkup(plan);
  pcBuildPlanContent.querySelector("[data-add-pc-build]")?.addEventListener("click", addPcBuildToCart);
  return;
  pcBuildPlanContent.innerHTML = `
    <article class="pc-plan-summary">
      <div class="pc-plan-head">
        <div>
          <span class="section-eyebrow">Build Summary</span>
          <h3>${escapeHtml(plan.summary || "电脑整机方案")}</h3>
        </div>
        <span class="${plan.compatibility?.valid === false ? "compat-failed" : "compat-pass"}">
          ${plan.compatibility?.valid === false ? "需复核" : "兼容通过"}
        </span>
      </div>
      <p>预算 ${formatPrice(plan.budget, plan.currency)}，总价 ${formatPrice(plan.total_price, plan.currency)}。</p>
      <div class="cart-actions">
        <button class="primary-button" type="button" data-add-pc-build>整机加入购物车</button>
      </div>
    </article>
    <article>
      <span class="section-eyebrow">Compatibility</span>
      <h3>兼容性校验</h3>
      <ul>
        ${checks.map((check) => `<li>${escapeHtml(check.name)}：${escapeHtml(check.detail || check.status)}</li>`).join("") || "<li>暂无兼容性校验信息。</li>"}
      </ul>
    </article>
    <article class="pc-parts-card">
      <span class="section-eyebrow">Parts</span>
      <h3>配件清单</h3>
      <div class="pc-parts-list">
        ${(plan.parts || plan.items || []).map(renderPcPartRow).join("")}
      </div>
    </article>
  `;
  pcBuildPlanContent.querySelector("[data-add-pc-build]")?.addEventListener("click", addPcBuildToCart);
}

function renderPcPartRow(item) {
  return `
    <div class="pc-part-row">
      ${renderProductVisual(item, item.title)}
      <div>
        <span>${escapeHtml(item.role_name || item.role || "配件")}</span>
        <strong>${escapeHtml(item.title || item.product_id || "")}</strong>
        <p>${escapeHtml(item.reason || item.brand || "")}</p>
      </div>
      <em>${formatPrice(item.price, item.currency || currentPcBuildPlan?.currency || "CNY")}</em>
    </div>
  `;
}

async function addPcBuildToCart() {
  const ids = (currentPcBuildPlan?.parts || currentPcBuildPlan?.items || []).map((item) => item.product_id).filter(Boolean);
  if (!ids.length) return;
  await updateCart("加入整机方案", ids);
  showScreen("cart");
}

async function loadProducts(force = false) {
  if (!productMarketContent) return;
  if (productMarketContent.dataset.loaded === "true" && !force) return;
  productMarketContent.innerHTML = `<div class="empty-state">正在加载商品库...</div>`;
  try {
    const params = new URLSearchParams();
    if (productFilters.category) params.set("category", productFilters.category);
    if (productFilters.brand) params.set("brand", productFilters.brand);
    if (productFilters.q) params.set("q", productFilters.q);
    const data = await getJson(`/api/products${params.toString() ? `?${params}` : ""}`);
    productCatalog = data.products || [];
    productFacets = { categories: data.categories || [], brands: data.brands || [] };
    if (selectedProductDetail && !productCatalog.some((item) => item.product_id === selectedProductDetail.product_id)) {
      selectedProductDetail = null;
    }
    productMarketVisibleCount = PRODUCT_PAGE_SIZE;
    productMarketContent.dataset.loaded = "true";
    renderProductMarket();
  } catch (error) {
    productMarketContent.innerHTML = `<div class="empty-state">${escapeHtml(error.message || "商品库加载失败")}</div>`;
  }
}

function renderProductMarket() {
  const visibleProducts = productCatalog.slice(0, productMarketVisibleCount);
  const hasMoreProducts = productMarketVisibleCount < productCatalog.length;
  productMarketContent.innerHTML = `
    <div class="product-market-layout">
      <aside class="product-filter-panel">
        <div class="filter-head">
          <strong>筛选</strong>
          <button type="button" data-clear-filters>清空</button>
        </div>
        <input class="product-search" type="search" placeholder="搜索商品或品牌" value="${escapeHtml(productFilters.q)}" data-market-search />
        <section class="filter-section">
          <strong>PC 配件</strong>
          <div class="filter-chips">
            ${renderPcCategoryButtons()}
          </div>
        </section>
        <section class="filter-section">
          <strong>商品分类</strong>
          <div class="filter-chips">
            ${renderFacetButtons("category", productFacets.categories)}
          </div>
        </section>
        <section class="filter-section">
          <strong>品牌</strong>
          <div class="filter-chips">
            ${renderFacetButtons("brand", productFacets.brands.slice(0, 14))}
          </div>
        </section>
      </aside>
      <section class="product-board${selectedProductDetail ? " with-detail" : ""}">
        <div class="product-board-head">
          <div>
            <span class="section-eyebrow">Catalog</span>
            <h3>${productCatalog.length} 件商品</h3>
          </div>
          <span class="subtle">${activeFilterText()}</span>
        </div>
        <div class="product-grid">
          ${visibleProducts.map(renderProductCard).join("") || `<div class="empty-state">没有找到匹配商品。</div>`}
        </div>
        ${selectedProductDetail ? renderProductDetailPanel(selectedProductDetail) : ""}
        ${hasMoreProducts ? `
          <div class="cart-actions">
            <button class="secondary-button" type="button" data-load-more-products>
              加载更多 ${Math.min(PRODUCT_PAGE_SIZE, productCatalog.length - productMarketVisibleCount)} 件
            </button>
          </div>
        ` : ""}
      </section>
    </div>
  `;
  bindProductMarketControls();
  bindProductButtons(productMarketContent);
}

function renderFacetButtons(type, facets) {
  return (facets || []).map((facet) => {
    const value = facet.value || "";
    const active = productFilters[type] === value ? "active" : "";
    const label = type === "category" ? (CATEGORY_LABELS[value] || value) : value;
    return `<button class="${active}" type="button" data-filter-type="${type}" data-filter-value="${escapeHtml(value)}">${escapeHtml(label)} <span>${facet.count}</span></button>`;
  }).join("");
}

function renderPcCategoryButtons() {
  const counts = new Map((productFacets.categories || []).map((facet) => [facet.value, facet.count]));
  return PC_CATEGORY_VALUES.map((value) => {
    const active = productFilters.category === value ? "active" : "";
    const count = counts.get(value) || 0;
    return `<button class="${active}" type="button" data-filter-type="category" data-filter-value="${escapeHtml(value)}">${escapeHtml(CATEGORY_LABELS[value] || value)} <span>${count}</span></button>`;
  }).join("");
}

function bindProductMarketControls() {
  productMarketContent.querySelector("[data-market-search]")?.addEventListener("input", debounce((event) => {
    productFilters.q = event.target.value.trim();
    catalogSearchInput.value = productFilters.q;
    loadProducts(true);
  }, 220));
  productMarketContent.querySelectorAll("[data-filter-type]").forEach((button) => {
    button.addEventListener("click", () => {
      const type = button.dataset.filterType;
      const value = button.dataset.filterValue;
      productFilters[type] = productFilters[type] === value ? "" : value;
      loadProducts(true);
    });
  });
  productMarketContent.querySelector("[data-clear-filters]")?.addEventListener("click", () => {
    productFilters = { category: "", brand: "", q: "" };
    catalogSearchInput.value = "";
    loadProducts(true);
  });
  productMarketContent.querySelector("[data-load-more-products]")?.addEventListener("click", () => {
    productMarketVisibleCount = Math.min(productMarketVisibleCount + PRODUCT_PAGE_SIZE, productCatalog.length);
    renderProductMarket();
  });
}

function bindProductButtons(root) {
  root.querySelectorAll("[data-product-detail]").forEach((card) => {
    const open = () => openProductDetail(card.dataset.productDetail);
    card.addEventListener("click", open);
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
  });
  root.querySelectorAll("[data-open-detail]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      openProductDetail(button.dataset.openDetail);
    });
  });
  root.querySelector("[data-close-product-detail]")?.addEventListener("click", (event) => {
    event.stopPropagation();
    selectedProductDetail = null;
    renderActiveProductSurface();
  });
  root.querySelectorAll("[data-add-cart]").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      await updateCart("加入购物车", [button.dataset.addCart]);
      button.textContent = "已加入";
      setTimeout(() => {
        button.textContent = "加入购物车";
      }, 1200);
    });
  });
  root.querySelectorAll("[data-compare]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      compareProduct(button.dataset.compare);
    });
  });
}

async function openProductDetail(productId) {
  const sourceCards = lastProductCards.length ? lastProductCards : productCatalog;
  selectedProductDetail = sourceCards.find((item) => item.product_id === productId) || productCatalog.find((item) => item.product_id === productId) || null;
  renderActiveProductSurface();

  if (selectedProductDetail && needsFullProductDetail(selectedProductDetail)) {
    try {
      const detail = await getJson(`/api/products/${encodeURIComponent(productId)}`);
      selectedProductDetail = { ...selectedProductDetail, ...detail };
    } catch (error) {
      console.warn("Product detail loading failed", error);
    }
  }
  renderActiveProductSurface();
}

function needsFullProductDetail(product) {
  return !Array.isArray(product?.skus) || !Object.prototype.hasOwnProperty.call(product || {}, "description");
}

function renderActiveProductSurface() {
  if (document.querySelector("#screen-products").classList.contains("active")) {
    renderProductMarket();
  } else {
    renderRecommendedProducts(lastProductCards, lastComparisonRows);
  }
}

async function compareProduct(productId) {
  const sourceCards = lastProductCards.length ? lastProductCards : productCatalog;
  const ids = Array.from(new Set([productId, ...sourceCards.map((item) => item.product_id).filter(Boolean)])).slice(0, 4);
  if (ids.length < 2) return;
  const data = await postJson("/api/products/compare", { product_ids: ids });
  lastComparisonRows = data.rows || [];
  if (document.querySelector("#screen-products").classList.contains("active")) {
    productMarketContent.insertAdjacentHTML("afterbegin", renderComparisonTable(lastComparisonRows));
  } else {
    renderRecommendedProducts(lastProductCards, lastComparisonRows);
  }
}

async function updateCart(instruction, productIds = []) {
  const data = await postJson("/api/cart/actions", {
    session_id: SESSION_ID,
    instruction,
    product_ids: Array.isArray(productIds) ? productIds : [productIds],
  });
  cartState = data.cart || cartState;
  renderCart();
}

function renderCart() {
  const items = cartState.items || [];
  const count = cartState.count ?? items.reduce((total, item) => total + Number(item.quantity || 1), 0);
  cartCountBadge.textContent = count;
  cartMiniText.textContent = items.length
    ? `${items.length} 种商品，合计 ${formatPrice(cartState.total_price, cartState.currency)}`
    : "暂未添加商品。";

  if (!cartContent) return;
  if (!items.length) {
    cartContent.className = "cart-panel empty-state";
    cartContent.innerHTML = "购物车还是空的。可以在智能导购里让系统推荐商品，或在商品库里加入购物车。";
    return;
  }

  cartContent.className = "cart-panel";
  cartContent.innerHTML = `
    <div class="cart-list">
      ${items.map((item) => `
        <article class="cart-item">
          ${renderProductVisual(item, item.title)}
          <div>
            <strong>${escapeHtml(item.title || item.product_id)}</strong>
            <span>${escapeHtml(item.brand || "")}</span>
            <span>${formatPrice(item.price, item.currency)} x ${item.quantity}</span>
          </div>
          <div class="cart-actions">
            <button type="button" data-cart-dec="${escapeHtml(item.product_id)}">-</button>
            <button type="button" data-cart-inc="${escapeHtml(item.product_id)}">+</button>
            <button type="button" data-cart-remove="${escapeHtml(item.product_id)}">删除</button>
          </div>
        </article>
      `).join("")}
    </div>
    <div class="cart-total">
      <span>合计</span>
      <strong>${formatPrice(cartState.total_price, cartState.currency)}</strong>
      <button class="secondary-button" type="button" data-cart-clear>清空购物车</button>
    </div>
  `;
  cartContent.querySelectorAll("[data-cart-remove]").forEach((button) => button.addEventListener("click", () => updateCart("删除", [button.dataset.cartRemove])));
  cartContent.querySelectorAll("[data-cart-inc]").forEach((button) => {
    const item = items.find((entry) => entry.product_id === button.dataset.cartInc);
    button.addEventListener("click", () => updateCart(`数量改成 ${(item?.quantity || 1) + 1}`, [button.dataset.cartInc]));
  });
  cartContent.querySelectorAll("[data-cart-dec]").forEach((button) => {
    const item = items.find((entry) => entry.product_id === button.dataset.cartDec);
    const next = Math.max((item?.quantity || 1) - 1, 1);
    button.addEventListener("click", () => updateCart(`数量改成 ${next}`, [button.dataset.cartDec]));
  });
  cartContent.querySelector("[data-cart-clear]")?.addEventListener("click", () => updateCart("清空购物车"));
}

// ── 🟣 v4: 购物车确认 UI ──

function renderCartConfirmation(data, assistantNode) {
  const plan = data.plan || {};
  const message = data.message || "确认操作？";
  const operation = plan.operation || "add";

  const container = document.createElement("div");
  container.className = "cart-confirm-box";
  const borderColor = operation === "remove" ? "#e74c3c" : "#1a73e8";
  const actionLabel = operation === "remove" ? "移除" : operation === "set_quantity" ? "修改数量" : "加入购物车";
  const confirmColor = operation === "remove" ? "#e74c3c" : "#1a73e8";

  container.style.cssText = `border:1px solid ${borderColor};border-radius:10px;padding:12px 16px;margin:8px 0;background:#fafbfc;`;
  container.innerHTML = `
    <div style="font-size:13px;color:#555;margin-bottom:6px;">${escapeHtml(message)}</div>
    <div style="display:flex;gap:8px;">
      <button class="primary-button cart-confirm-yes" type="button"
        style="font-size:12px;padding:5px 16px;border-radius:6px;background:${confirmColor};color:#fff;border:none;cursor:pointer;">
        确认${actionLabel}
      </button>
      <button class="secondary-button cart-confirm-no" type="button"
        style="font-size:12px;padding:5px 16px;border-radius:6px;border:1px solid #ccc;background:#fff;cursor:pointer;">
        取消
      </button>
    </div>
  `;
  assistantNode.parentElement?.insertBefore(container, assistantNode);
  chatMessages.scrollTop = chatMessages.scrollHeight;

  container.querySelector(".cart-confirm-yes").addEventListener("click", async () => {
    try {
      const result = await postJson("/api/cart/confirm", { session_id: SESSION_ID, confirmed: true });
      cartState = result.cart || cartState;
      renderCart();
      container.innerHTML = `<div style="font-size:13px;color:#2e7d32;">✓ ${escapeHtml((result.messages || []).join(" ") || "操作已执行。")}</div>`;
    } catch (e) {
      container.innerHTML = `<div style="font-size:13px;color:#c62828;">✗ 操作失败：${escapeHtml(String(e.message || e))}</div>`;
    }
  });
  container.querySelector(".cart-confirm-no").addEventListener("click", async () => {
    try {
      await postJson("/api/cart/confirm", { session_id: SESSION_ID, confirmed: false });
    } catch (_) { /* ignore */ }
    container.innerHTML = `<div style="font-size:13px;color:#888;">已取消。</div>`;
  });
}

// ── 🟣 v4: 购物车追问 UI ──

function renderCartClarification(data, assistantNode) {
  const text = data.text || "请指定要操作的商品。";
  const items = data.cart_items || [];

  const container = document.createElement("div");
  container.className = "cart-clarify-box";
  container.style.cssText = "border:1px solid #f0ad4e;border-radius:10px;padding:12px 16px;margin:8px 0;background:#fffbf0;";

  let itemsHtml = "";
  if (items.length) {
    itemsHtml = `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;">` +
      items.map((item) =>
        `<button class="cart-clarify-btn" type="button" data-clarify-pid="${escapeHtml(item.product_id)}"
          style="font-size:12px;padding:4px 12px;border-radius:6px;border:1px solid #f0ad4e;background:#fff;cursor:pointer;">
          ${escapeHtml(item.title)}（x${item.quantity}）
        </button>`
      ).join("") +
      `</div>`;
  }
  container.innerHTML = `
    <div style="font-size:13px;color:#856404;">${escapeHtml(text)}</div>
    ${itemsHtml}
  `;
  assistantNode.parentElement?.insertBefore(container, assistantNode);
  chatMessages.scrollTop = chatMessages.scrollHeight;

  container.querySelectorAll(".cart-clarify-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const pid = btn.dataset.clarifyPid;
      const action = data.action || "remove";
      const actionWord = action === "remove" ? "删除" : "数量改成";
      // 发送一条带 product_id 的精确指令
      sendGoalMessage(`${actionWord} ${pid}`);
      container.innerHTML = `<div style="font-size:13px;color:#555;">已选择：${escapeHtml(btn.textContent.trim())}</div>`;
    });
  });
}

function showScreen(screen) {
  screens.forEach((node) => node.classList.toggle("active", node.dataset.screen === screen));
  stageButtons.forEach((button) => button.classList.toggle("active", button.dataset.navScreen === screen));
  if (screen === "products") loadProducts();
  if (screen === "cart") renderCart();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function appendMessage(role, text) {
  const node = document.createElement("div");
  node.className = `chat-message ${role}`;
  node.textContent = text;
  chatMessages.appendChild(node);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return node;
}

function renderAttachments() {
  attachmentList.innerHTML = selectedAttachments.map((file, index) => `
    <div class="attachment-item">
      <div>
        <span class="attachment-name">${escapeHtml(file.name)}</span>
        <span class="attachment-meta">${escapeHtml(file.type || "image")} · ${Math.ceil((file.size || 0) / 1024)} KB · ${escapeHtml(attachmentStatusLabel(file))}</span>
        ${file.summary ? `<span class="attachment-meta">${escapeHtml(file.summary)}</span>` : ""}
      </div>
      <button class="attachment-remove" type="button" data-remove-attachment="${index}">移除</button>
    </div>
  `).join("");
  attachmentList.querySelectorAll("[data-remove-attachment]").forEach((button) => {
    button.addEventListener("click", () => {
      selectedAttachments.splice(Number(button.dataset.removeAttachment), 1);
      renderAttachments();
    });
  });
}

function attachmentStatusLabel(file) {
  const status = file.analysis_status || "pending";
  if (status === "pending") return "等待解析";
  if (status === "analyzing") return "解析中";
  if (status === "success") return "已解析";
  if (status === "skipped") return "未配置视觉模型";
  if (status === "fallback") return "已降级";
  if (status === "rejected") return "不支持";
  return status;
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

function setBusy(value, label = "等待需求") {
  isBusy = value;
  sendButton.disabled = value;
  setStatus(value ? label : "等待需求", value ? "active" : "");
}

function setStatus(text, mode = "") {
  statusText.textContent = text;
  statusDot.className = `status-dot ${mode}`.trim();
}

function renderProductVisual(product, alt) {
  if (isPcProduct(product)) return renderPcProductVisual(product, alt);
  return renderImage(product?.image_url || product?.image_path, alt);
}

function isPcProduct(product) {
  const category = String(product?.category || "");
  const sourceType = String(product?.metadata?.source_type || "");
  const componentType = String(product?.metadata?.component_type || product?.role || "");
  return category.startsWith("pc_") || sourceType === "jd_pc_product" || Boolean(componentType);
}

function renderPcProductVisual(product, alt) {
  const image = product?.image_url || product?.image_path;
  if (image) return renderImage(image, alt);
  const type = normalizePcComponentType(product);
  const label = pcComponentLabel(type);
  const brand = product?.brand || product?.metadata?.specs?.brand || label;
  const model = product?.metadata?.specs?.model || product?.model || product?.sub_category || product?.title || alt || label;
  const specs = pcVisualSpecs(product, type);
  return `
    <div class="pc-product-visual pc-visual-${escapeHtml(type)}" role="img" aria-label="${escapeHtml(alt || product?.title || label)}">
      <div class="pc-visual-head">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(brand)}</strong>
      </div>
      <div class="pc-visual-device" aria-hidden="true">
        ${pcVisualDeviceMarkup(type)}
      </div>
      <div class="pc-visual-model">${escapeHtml(model)}</div>
      <div class="pc-visual-specs">
        ${specs.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
      </div>
    </div>
  `;
}

function normalizePcComponentType(product) {
  const raw = String(product?.metadata?.component_type || product?.role || product?.category || "").replace(/^pc_/, "");
  if (raw === "storage") return "ssd";
  if (raw === "cooler") return "cpu_cooler";
  return raw || "part";
}

function pcComponentLabel(type) {
  return {
    cpu: "CPU",
    gpu: "GPU",
    motherboard: "MB",
    memory: "RAM",
    ssd: "SSD",
    hdd: "HDD",
    psu: "PSU",
    case: "CASE",
    cpu_cooler: "COOLER",
  }[type] || "PC";
}

function pcVisualSpecs(product, type) {
  const specs = product?.metadata?.specs || product?.specs || {};
  const pick = {
    cpu: ["socket", "cores", "threads", "tdp_w"],
    gpu: ["chipset", "vram_gb", "memory_type", "power_w"],
    motherboard: ["socket", "chipset", "memory_type", "form_factor"],
    memory: ["capacity_gb", "memory_type", "speed_mhz", "modules"],
    ssd: ["capacity_gb", "interface", "form_factor", "read_mb_s"],
    hdd: ["capacity_gb", "interface", "rpm"],
    psu: ["wattage_w", "efficiency_rating", "modular", "atx_version"],
    case: ["motherboard_support", "gpu_clearance_mm", "cooler_clearance_mm"],
    cpu_cooler: ["tdp_w", "height_mm", "radiator_size_mm", "socket_support"],
  }[type] || [];
  const values = pick.map((key) => formatPcSpec(key, specs[key])).filter(Boolean).slice(0, 4);
  return values.length ? values : [product?.category_name || pcComponentLabel(type)];
}

function formatPcSpec(key, value) {
  if (value === undefined || value === null || value === "" || value === false) return "";
  const joined = Array.isArray(value) ? value.join("/") : String(value);
  const label = {
    socket: "Socket",
    cores: "Core",
    threads: "Thread",
    tdp_w: "TDP",
    chipset: "Chip",
    vram_gb: "VRAM",
    memory_type: "Mem",
    power_w: "Power",
    form_factor: "Size",
    capacity_gb: "Cap",
    speed_mhz: "Speed",
    modules: "Kit",
    interface: "IF",
    read_mb_s: "Read",
    rpm: "RPM",
    wattage_w: "W",
    efficiency_rating: "Eff",
    modular: "Modular",
    atx_version: "ATX",
    motherboard_support: "Board",
    gpu_clearance_mm: "GPU Len",
    cooler_clearance_mm: "Air H",
    height_mm: "Height",
    radiator_size_mm: "RAD",
    socket_support: "Socket",
  }[key] || key;
  return `${label}: ${joined}`;
}

function pcVisualDeviceMarkup(type) {
  const slots = {
    cpu: '<span class="pc-device-chip"></span>',
    gpu: '<span class="pc-device-board"></span><span class="pc-device-fan"></span><span class="pc-device-fan"></span>',
    motherboard: '<span class="pc-device-board"></span><span class="pc-device-chip"></span><span class="pc-device-lines"></span>',
    memory: '<span class="pc-device-stick"></span><span class="pc-device-stick"></span>',
    ssd: '<span class="pc-device-drive"></span>',
    hdd: '<span class="pc-device-drive"></span><span class="pc-device-disc"></span>',
    psu: '<span class="pc-device-box"></span><span class="pc-device-fan"></span>',
    case: '<span class="pc-device-tower"></span><span class="pc-device-window"></span>',
    cpu_cooler: '<span class="pc-device-tower"></span><span class="pc-device-fan"></span>',
  };
  return slots[type] || '<span class="pc-device-box"></span>';
}

function renderImage(src, alt) {
  if (!src) return `<div class="image-placeholder" role="img" aria-label="${escapeHtml(alt || "商品图片")}"></div>`;
  return `<img src="${escapeHtml(src)}" alt="${escapeHtml(alt || "商品图片")}" loading="lazy" />`;
}

function formatPrice(value, currency = "CNY") {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return "-";
  const amount = Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 0 });
  return `${currency === "CNY" ? "¥" : ""}${amount}${currency && currency !== "CNY" ? ` ${currency}` : ""}`;
}

function stockStatusLabel(value) {
  const labels = {
    available: "有货",
    in_stock: "有货",
    limited: "库存有限",
    low_stock: "库存有限",
    sold_out: "已售罄",
    out_of_stock: "无货",
    demo: "库存待确认",
  };
  return labels[String(value || "").toLowerCase()] || "库存待确认";
}

function normalizeTags(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value.filter(Boolean).map(String);
  return String(value).split(/[、,，\s]+/).filter(Boolean);
}

function activeFilterText() {
  const parts = [];
  if (productFilters.category) parts.push(CATEGORY_LABELS[productFilters.category] || productFilters.category);
  if (productFilters.brand) parts.push(productFilters.brand);
  if (productFilters.q) parts.push(`搜索“${productFilters.q}”`);
  return parts.length ? parts.join(" · ") : "全部商品";
}

function debounce(fn, wait) {
  let timer = 0;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), wait);
  };
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
