const state = { products: [], locations: [], scanner: { stream: null, active: false, target: null, detector: null, timer: null } };
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (response.redirected) window.location.href = response.url;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Nao foi possivel concluir a operacao.");
  return data;
}

function formatNumber(value) { return new Intl.NumberFormat("pt-BR").format(value || 0); }
function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(new Date(value.replace(" ", "T") + "Z"));
}
function movementLabel(type) { return ({ ENTRY: "Entrada", EXIT: "Saida", ADJUSTMENT: "Ajuste" })[type] || type; }
function toast(message, error = false) {
  const el = $("#toast"); el.textContent = message; el.className = `toast show${error ? " error" : ""}`;
  clearTimeout(window.toastTimer); window.toastTimer = setTimeout(() => el.className = "toast", 2800);
}

async function loadDashboard() {
  const data = await api("/api/dashboard");
  $("#metricUnits").textContent = formatNumber(data.metrics.units);
  $("#metricSkus").textContent = formatNumber(data.metrics.skus);
  $("#metricLow").textContent = formatNumber(data.metrics.low_stock);
  $("#metricDiff").textContent = formatNumber(data.metrics.divergences);
  $("#metricExpiry").textContent = formatNumber(data.metrics.expiring);
  $("#recentMovements").innerHTML = data.recent.length ? data.recent.map(m => `
    <div class="activity-item"><span class="activity-icon">${m.type === "EXIT" ? "↑" : "↓"}</span>
    <div><strong>${m.sku} · ${m.name}</strong><small>${movementLabel(m.type)} em ${m.location} · ${formatDate(m.created_at)}</small></div>
    <span class="qty ${m.quantity < 0 ? "negative" : "positive"}">${m.quantity > 0 ? "+" : ""}${formatNumber(m.quantity)} un.</span></div>`).join("") : '<div class="empty">Nenhuma movimentacao registrada.</div>';
}

async function loadProducts() {
  state.products = await api("/api/products");
  $("#productsCount").textContent = `${state.products.length} produtos`;
  $("#productsTable tbody").innerHTML = state.products.map(p => `
    <tr><td><strong>${p.sku}</strong></td><td><strong>${p.name}</strong><small>${p.unit}</small></td><td>${p.locations || "—"}</td>
    <td><strong>${formatNumber(p.stock)} ${p.unit.toLowerCase()}.</strong></td><td>${formatNumber(p.min_stock)}</td>
    <td><span class="badge ${p.stock <= p.min_stock ? "low" : "ok"}">${p.stock <= p.min_stock ? "Estoque baixo" : "Normal"}</span></td></tr>`).join("");
  $$(".product-options").forEach(select => select.innerHTML = '<option value="">Selecione...</option>' + state.products.map(p => `<option value="${p.id}">${p.sku} · ${p.name}</option>`).join(""));
}

async function loadLocations() {
  state.locations = await api("/api/locations");
  $("#locationsTable tbody").innerHTML = state.locations.map(l => `
    <tr><td><strong>${l.code}</strong></td><td>${l.description || "—"}</td><td>${formatNumber(l.skus)}</td><td><strong>${formatNumber(l.units)}</strong></td><td><span class="badge ok">Ativo</span></td></tr>`).join("");
  $$(".location-options").forEach(select => select.innerHTML = '<option value="">Selecione...</option>' + state.locations.map(l => `<option value="${l.id}">${l.code} · ${l.description}</option>`).join(""));
}

async function loadMovements() {
  const rows = await api("/api/movements");
  $("#movementsTable tbody").innerHTML = rows.map(m => `<tr><td>${formatDate(m.created_at)}</td><td><span class="badge ${m.type.toLowerCase()}">${movementLabel(m.type)}</span></td><td><strong>${m.sku}</strong><small>${m.name}</small></td><td>${m.location}</td><td><span class="qty ${m.quantity < 0 ? "negative" : "positive"}">${m.quantity > 0 ? "+" : ""}${formatNumber(m.quantity)}</span></td><td>${m.note || "—"}</td></tr>`).join("");
}

async function loadInventory() {
  const rows = await api("/api/inventory");
  $("#inventoryTable tbody").innerHTML = rows.map(i => `<tr><td>${formatDate(i.created_at)}</td><td><strong>${i.location}</strong></td><td><strong>${i.sku}</strong><small>${i.name}</small></td><td>${formatNumber(i.system_quantity)}</td><td>${formatNumber(i.counted_quantity)}</td><td><span class="diff ${i.difference === 0 ? "zero" : "not-zero"}">${i.difference > 0 ? "+" : ""}${i.difference}</span></td><td>${i.operator}</td></tr>`).join("");
}

async function refreshAll() { await Promise.all([loadProducts(), loadLocations(), loadDashboard(), loadMovements(), loadInventory()]); }

function goTo(page) {
  $$(".page").forEach(el => el.classList.toggle("active", el.id === `${page}-page`));
  $$(".nav-item").forEach(el => el.classList.toggle("active", el.dataset.page === page));
  $("#sidebar").classList.remove("open"); window.scrollTo(0, 0);
}
function openModal(id) { $(`#${id}`).classList.add("open"); }
function closeModal(modal) { stopScanner(); modal.closest(".modal").classList.remove("open"); }
function formData(form) { return Object.fromEntries(new FormData(form).entries()); }

function stopScanner() {
  const scanner = state.scanner;
  scanner.active = false;
  clearTimeout(scanner.timer);
  if (scanner.stream) scanner.stream.getTracks().forEach(track => track.stop());
  scanner.stream = null;
  scanner.detector = null;
  scanner.target = null;
  const video = $("#scannerVideo");
  if (video) video.srcObject = null;
  $("#scannerPanel")?.classList.add("hidden");
}

function selectScannedCode(rawValue) {
  const code = String(rawValue || "").trim().toUpperCase();
  const scanner = state.scanner;
  const form = $("#inventoryForm");
  const isLocation = scanner.target === "location";
  const item = isLocation
    ? state.locations.find(location => location.code.trim().toUpperCase() === code)
    : state.products.find(product => product.sku.trim().toUpperCase() === code);

  if (!item) {
    $("#scannerStatus").textContent = `${isLocation ? "Posição" : "SKU"} ${code} não cadastrado. Tente novamente.`;
    return false;
  }

  const select = form.elements[isLocation ? "location_id" : "product_id"];
  select.value = String(item.id);
  select.dispatchEvent(new Event("change", { bubbles: true }));
  stopScanner();
  toast(`${isLocation ? "Posição" : "SKU"} ${code} identificado.`);
  if (isLocation) form.querySelector('[data-scan="product"]').focus();
  else form.elements.counted_quantity.focus();
  return true;
}

async function detectBarcode() {
  const scanner = state.scanner;
  if (!scanner.active || !scanner.detector) return;
  try {
    const video = $("#scannerVideo");
    if (video.readyState >= 2) {
      const codes = await scanner.detector.detect(video);
      if (codes.length && selectScannedCode(codes[0].rawValue)) return;
    }
  } catch (error) {
    // Alguns aparelhos falham em quadros isolados enquanto a câmera ajusta o foco.
  }
  scanner.timer = setTimeout(detectBarcode, 180);
}

async function startScanner(target) {
  stopScanner();
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    toast("A câmera exige HTTPS ou acesso pelo endereço localhost.", true);
    return;
  }
  if (!("BarcodeDetector" in window)) {
    toast("Este navegador não possui leitor de códigos. Use Chrome no Android ou selecione manualmente.", true);
    return;
  }

  const scanner = state.scanner;
  scanner.target = target;
  $("#scannerTitle").textContent = target === "location" ? "Escaneando posição" : "Escaneando SKU";
  $("#scannerStatus").textContent = "Aponte a câmera para o código";
  $("#scannerPanel").classList.remove("hidden");

  try {
    const wanted = ["qr_code", "code_128", "code_39", "ean_13", "ean_8", "upc_a", "upc_e", "itf", "codabar"];
    const supported = BarcodeDetector.getSupportedFormats ? await BarcodeDetector.getSupportedFormats() : [];
    const formats = wanted.filter(format => supported.includes(format));
    scanner.detector = formats.length ? new BarcodeDetector({ formats }) : new BarcodeDetector();
    scanner.stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
    const video = $("#scannerVideo");
    video.srcObject = scanner.stream;
    await video.play();
    scanner.active = true;
    detectBarcode();
  } catch (error) {
    stopScanner();
    const denied = error?.name === "NotAllowedError";
    toast(denied ? "Permita o acesso à câmera para escanear." : "Não foi possível abrir a câmera deste aparelho.", true);
  }
}

$$(".nav-item").forEach(button => button.addEventListener("click", () => goTo(button.dataset.page)));
$$(`[data-goto]`).forEach(button => button.addEventListener("click", () => goTo(button.dataset.goto)));
$$(`[data-open]`).forEach(button => button.addEventListener("click", () => openModal(button.dataset.open)));
$$(`.modal-close`).forEach(button => button.addEventListener("click", () => closeModal(button)));
$$(`.modal`).forEach(modal => modal.addEventListener("click", e => { if (e.target === modal) { stopScanner(); modal.classList.remove("open"); } }));
$("#menuButton").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
$$(`[data-scan]`).forEach(button => button.addEventListener("click", () => startScanner(button.dataset.scan)));
$("#stopScanner").addEventListener("click", stopScanner);

$("#productForm").addEventListener("submit", async e => {
  e.preventDefault(); try { const data = await api("/api/products", { method: "POST", body: JSON.stringify(formData(e.target)) }); toast(data.message); e.target.reset(); closeModal(e.target); await refreshAll(); } catch (err) { toast(err.message, true); }
});
$("#locationForm").addEventListener("submit", async e => {
  e.preventDefault(); try { const data = await api("/api/locations", { method: "POST", body: JSON.stringify(formData(e.target)) }); toast(data.message); e.target.reset(); closeModal(e.target); await refreshAll(); } catch (err) { toast(err.message, true); }
});
$("#movementForm").addEventListener("submit", async e => {
  e.preventDefault(); try { const data = await api("/api/movements", { method: "POST", body: JSON.stringify(formData(e.target)) }); toast(data.message); e.target.reset(); closeModal(e.target); await refreshAll(); } catch (err) { toast(err.message, true); }
});
$("#inventoryForm").addEventListener("submit", async e => {
  e.preventDefault(); try {
    const data = await api("/api/inventory", { method: "POST", body: JSON.stringify(formData(e.target)) });
    const result = $("#countResult"); result.classList.remove("hidden"); result.innerHTML = `<div><span>SISTEMA</span><strong>${data.system_quantity}</strong></div><div><span>CONTADO</span><strong>${data.counted_quantity}</strong></div><div><span>DIFERENCA</span><strong class="${data.difference ? "diff not-zero" : "diff zero"}">${data.difference > 0 ? "+" : ""}${data.difference}</strong></div>`;
    toast(data.message); await refreshAll(); setTimeout(() => { e.target.reset(); result.classList.add("hidden"); closeModal(e.target); }, 1800);
  } catch (err) { toast(err.message, true); }
});

$$(`.table-search`).forEach(input => input.addEventListener("input", () => {
  const query = input.value.toLowerCase(); $$(`#${input.dataset.table} tbody tr`).forEach(row => row.hidden = !row.textContent.toLowerCase().includes(query));
}));
$("#globalSearch").addEventListener("keydown", e => { if (e.key === "Enter") { goTo("products"); const input = $(".table-search[data-table='productsTable']"); input.value = e.target.value; input.dispatchEvent(new Event("input")); } });

refreshAll().catch(err => toast(err.message, true));
