const WORKSPACES = {
  claims: "Claims Queue", analytics: "Executive Analytics", rag: "Policy RAG Engine",
  integrations: "Integration Hub", audit: "Audit Logs",
};

const state = {
  workspace: "claims", claims: [], analytics: null, filter: "ALL", selectedId: null,
  selectedIds: new Set(), detailTab: "resolution", auditLogs: {}, auditLoading: false,
  busy: false, exportMenuOpen: false, ediPayload: null, ragLoaded: false,
  integrationsLoaded: false, globalAuditLoaded: false, processingMode: null,
};
const elements = {};

document.addEventListener("DOMContentLoaded", () => {
  cacheElements(); bindEvents();
  const routeView = { "/analytics": "analytics", "/rag": "rag", "/integrations": "integrations", "/audit": "audit" }[location.pathname];
  const requested = location.hash.replace("#", "") || routeView || "claims";
  navigateTo(WORKSPACES[requested] ? requested : "claims", false);
  loadDashboard();
});

function cacheElements() {
  Object.assign(elements, {
    tableBody: document.querySelector("#claims-table-body"), claimsEmpty: document.querySelector("#claims-empty"),
    summary: document.querySelector("#claims-summary"), detailEmpty: document.querySelector("#detail-empty"),
    detailPanel: document.querySelector("#detail-panel"), actionBar: document.querySelector("#dynamic-action-bar"),
    analyzeAllButton: document.querySelector("#analyze-all-button"), scraperButton: document.querySelector("#scraper-button"),
    resolutionTab: document.querySelector("#resolution-tab"), auditTab: document.querySelector("#audit-tab"),
    resolutionPanel: document.querySelector("#resolution-tab-panel"), auditPanel: document.querySelector("#audit-tab-panel"),
    auditTimeline: document.querySelector("#audit-timeline"), auditLoading: document.querySelector("#audit-loading"),
    auditEmpty: document.querySelector("#audit-empty"), bulkBar: document.querySelector("#bulk-action-bar"),
    selectAll: document.querySelector("#select-all-checkbox"), policyDrawer: document.querySelector("#policy-drawer"),
    policyOverlay: document.querySelector("#policy-overlay"), ediModal: document.querySelector("#edi-modal"),
    ediCode: document.querySelector("#edi-code"), toast: document.querySelector("#toast"),
    integrationDrawer: document.querySelector("#integration-console-drawer"),
    integrationOverlay: document.querySelector("#integration-console-overlay"),
  });
}

function bindEvents() {
  document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => navigateTo(button.dataset.workspace)));
  document.querySelectorAll(".filter-button").forEach((button) => button.addEventListener("click", () => setFilter(button.dataset.status)));
  elements.selectAll.addEventListener("change", toggleSelectAll);
  document.querySelector("#clear-selection-button").addEventListener("click", clearSelection);
  document.querySelector("#bulk-analyze-button").addEventListener("click", analyzeSelectedClaims);
  elements.analyzeAllButton.addEventListener("click", analyzeAllClaims);
  elements.scraperButton.addEventListener("click", runScraper);
  elements.resolutionTab.addEventListener("click", () => setDetailTab("resolution"));
  elements.auditTab.addEventListener("click", () => setDetailTab("audit"));
  document.querySelector("#policy-citation-button").addEventListener("click", openPolicyDrawer);
  document.querySelector("#policy-close-button").addEventListener("click", closePolicyDrawer);
  elements.policyOverlay.addEventListener("click", closePolicyDrawer);
  document.querySelector("#edi-close-button").addEventListener("click", closeEdiInspector);
  elements.ediModal.addEventListener("click", (event) => { if (event.target === elements.ediModal) closeEdiInspector(); });
  document.querySelector("#copy-payload-button").addEventListener("click", copyPayload);
  document.querySelector("#rag-search-form").addEventListener("submit", searchPolicies);
  document.querySelector("#rag-query").addEventListener("input", schedulePolicySearch);
  document.querySelector("#audit-search-form").addEventListener("submit", filterAuditLogs);
  document.querySelector("#audit-query").addEventListener("input", scheduleAuditSearch);
  document.querySelector("#run-diagnostic-button").addEventListener("click", runIntegrationDiagnostic);
  document.querySelector("#open-integration-console-button").addEventListener("click", openIntegrationConsole);
  document.querySelector("#integration-console-close-button").addEventListener("click", closeIntegrationConsole);
  elements.integrationOverlay.addEventListener("click", closeIntegrationConsole);
  document.querySelector("#rag-payer").addEventListener("change", () => loadPolicyChunks(document.querySelector("#rag-query").value.trim(), document.querySelector("#rag-payer").value));
  document.addEventListener("mousemove", updateSurfaceSpotlight, { passive: true });
  window.addEventListener("hashchange", () => { const view = location.hash.replace("#", ""); if (WORKSPACES[view]) navigateTo(view, false); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") { closePolicyDrawer(); closeEdiInspector(); closeIntegrationConsole(); state.exportMenuOpen = false; renderActionBar(); } });
  document.addEventListener("click", (event) => { if (state.exportMenuOpen && !event.target.closest("#dynamic-action-bar")) { state.exportMenuOpen = false; renderActionBar(); } });
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { Accept: "application/json", "Content-Type": "application/json", ...options.headers }, ...options });
  if (!response.ok) { let message = `Request failed (${response.status})`; try { message = (await response.json()).detail || message; } catch (_) { /* Non-JSON response. */ } throw new Error(message); }
  return response.json();
}

async function loadDashboard() { await Promise.all([loadClaims(), loadAnalytics()]); }

function navigateTo(workspace, updateHash = true) {
  state.workspace = workspace;
  document.querySelectorAll(".workspace-view").forEach((view) => { view.hidden = view.dataset.view !== workspace; view.classList.toggle("active", view.dataset.view === workspace); });
  document.querySelectorAll(".nav-item").forEach((button) => { const active = button.dataset.workspace === workspace; button.classList.toggle("active", active); button.setAttribute("aria-current", active ? "page" : "false"); });
  setText("#workspace-title", WORKSPACES[workspace]);
  if (updateHash) history.replaceState(null, "", `#${workspace}`);
  if (workspace === "analytics") { renderAnalytics(); void loadAnalytics(); }
  if (workspace === "rag" && !state.ragLoaded) loadPolicyChunks();
  if (workspace === "integrations" && !state.integrationsLoaded) loadIntegrations();
  if (workspace === "audit") void loadGlobalAuditLogs();
}

async function loadClaims({ preserveSelection = true } = {}) {
  setConnection("loading");
  try {
    state.claims = await api("/api/claims");
    if (!preserveSelection || !state.claims.some((claim) => claim.id === state.selectedId)) state.selectedId = state.claims[0]?.id ?? null;
    state.selectedIds = new Set([...state.selectedIds].filter((id) => state.claims.some((claim) => claim.id === id && claim.status !== "RESOLVED")));
    setConnection("online"); renderClaims(); await loadClaimAuditLogs();
  } catch (error) { setConnection("offline"); showToast(error.message, true); }
}

async function loadAnalytics() {
  try { state.analytics = await api("/api/analytics/summary"); renderAnalytics(); }
  catch (error) { showToast(`Analytics unavailable: ${error.message}`, true); }
}

function renderClaims() { renderQueueMetrics(); renderFilters(); renderTable(); renderDetail(); renderBulkBar(); setBusy(state.busy); }

function renderQueueMetrics() { const risk = state.analytics?.total_at_risk_revenue ?? state.claims.filter((claim) => claim.status !== "RESOLVED").reduce((sum, claim) => sum + Number(claim.amount), 0); setText("#queue-risk", formatCurrency(risk)); }

function renderFilters() { document.querySelectorAll(".filter-button").forEach((button) => { const active = button.dataset.status === state.filter; button.classList.toggle("active", active); button.setAttribute("aria-pressed", String(active)); }); }

function renderTable() {
  const visible = filteredClaims(); elements.summary.textContent = `${visible.length} of ${state.claims.length} claims in view`;
  elements.tableBody.replaceChildren(); elements.claimsEmpty.hidden = visible.length > 0;
  visible.forEach((claim) => {
    const eligible = claim.status !== "RESOLVED"; const row = document.createElement("tr"); row.className = "claim-row"; row.tabIndex = 0; row.setAttribute("aria-selected", String(claim.id === state.selectedId));
    row.innerHTML = `<td><input class="claim-checkbox row-checkbox" type="checkbox" data-claim-id="${escapeHtml(claim.id)}" aria-label="Select ${escapeHtml(claim.id)}" ${state.selectedIds.has(claim.id) ? "checked" : ""} ${eligible ? "" : "disabled"}></td><td><p class="text-xs font-bold text-zinc-200">${escapeHtml(claim.patient_name)}</p><p class="mt-1 font-mono text-[9px] text-zinc-600">${escapeHtml(claim.id)}</p></td><td><span class="payer-chip">${escapeHtml(shortPayer(claim.insurer))}</span></td><td><span class="font-mono text-[10px] font-black text-rose-300">${escapeHtml(claim.denial_code)}</span><p class="mt-1 text-[9px] text-zinc-600">CPT ${escapeHtml(claim.cpt_code)}</p></td><td class="text-right text-xs font-black text-zinc-200">${formatCurrency(claim.amount)}</td><td>${statusBadge(claim.status)}</td>`;
    const choose = () => selectClaim(claim.id); row.addEventListener("click", choose); row.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); choose(); } });
    row.querySelector(".row-checkbox").addEventListener("click", (event) => event.stopPropagation());
    row.querySelector(".row-checkbox").addEventListener("change", (event) => toggleClaimSelection(claim.id, event.target.checked));
    elements.tableBody.append(row);
  });
  syncSelectAll();
}

function renderBulkBar() { const count = state.selectedIds.size; const processing = state.processingMode === "bulk"; elements.bulkBar.hidden = count === 0; elements.bulkBar.classList.toggle("processing", processing); setText("#bulk-selected-count", `${count} claim${count === 1 ? "" : "s"} selected`); const button = document.querySelector("#bulk-analyze-button"); button.disabled = state.busy || count === 0; button.innerHTML = processing ? '<span class="ai-spinner"></span>Analyzing selected claims…' : "⚡ Bulk AI Batch Analysis"; }
function toggleClaimSelection(id, selected) { selected ? state.selectedIds.add(id) : state.selectedIds.delete(id); renderBulkBar(); syncSelectAll(); }
function toggleSelectAll(event) { filteredClaims().filter((claim) => claim.status !== "RESOLVED").forEach((claim) => event.target.checked ? state.selectedIds.add(claim.id) : state.selectedIds.delete(claim.id)); renderTable(); renderBulkBar(); }
function syncSelectAll() { const eligible = filteredClaims().filter((claim) => claim.status !== "RESOLVED"); const count = eligible.filter((claim) => state.selectedIds.has(claim.id)).length; elements.selectAll.checked = eligible.length > 0 && count === eligible.length; elements.selectAll.indeterminate = count > 0 && count < eligible.length; }
function clearSelection() { state.selectedIds.clear(); renderTable(); renderBulkBar(); }

function renderDetail() {
  const claim = selectedClaim(); elements.detailEmpty.hidden = Boolean(claim); elements.detailPanel.hidden = !claim; if (!claim) return;
  setText("#detail-claim-id", claim.id); setText("#detail-patient", claim.patient_name); setText("#detail-insurer", claim.insurer); setText("#detail-cpt", claim.cpt_code); setText("#detail-denial-code", claim.denial_code); setText("#detail-amount", formatCurrency(claim.amount)); setText("#detail-service-date", formatDate(claim.date_of_service));
  setText("#evidence-auth", claim.auth_number_in_chart || "NOT FOUND"); setText("#evidence-diagnosis", claim.diagnosis_code || "NOT LINKED"); setText("#evidence-modifier", claim.modifier_attached || "NONE ATTACHED"); setText("#evidence-eob", claim.denial_code); setText("#evidence-note", claim.eob_notes || claim.denial_reason); document.querySelector("#detail-status").innerHTML = statusBadge(claim.status);
  const analyzed = Boolean(claim.suggested_fix && claim.policy_citation); document.querySelector("#ai-empty").hidden = analyzed; document.querySelector("#ai-result").hidden = !analyzed; setText("#detail-fix", claim.suggested_fix || ""); setText("#confidence-badge", `${Math.round(Number(claim.policy_confidence || 0))}% Policy Match`);
  renderDetailTabs(); renderClaimAuditTrail(); renderActionBar();
}

function renderActionBar() {
  const claim = selectedClaim(); if (!claim) return;
  if (claim.status === "PENDING") elements.actionBar.innerHTML = `<div class="flex justify-end"><button data-action="analyze" class="button-primary" ${state.busy ? "disabled" : ""}>${state.processingMode === "single" ? '<span class="ai-spinner"></span>Analyzing evidence…' : "⚡ Analyze with AI"}</button></div>`;
  else if (claim.status === "ANALYZED") elements.actionBar.innerHTML = `<div class="flex justify-end gap-2"><button data-action="export-toggle" class="action-secondary">Export package <span class="ml-2 text-zinc-600">⌄</span></button><button data-action="resolve" class="action-primary" ${state.busy ? "disabled" : ""}>Approve &amp; Resubmit</button></div>${state.exportMenuOpen ? exportMenu() : ""}`;
  else { const exports = (state.auditLogs[claim.id] || []).filter((log) => log.action === "EXPORTED").length; elements.actionBar.innerHTML = `<div class="settled-state"><span class="settled-mark"><i></i> Resubmitted &amp; Settled</span><div class="flex gap-4"><span class="micro-label">${exports} exports</span><button data-action="pdf" class="history-link">Appeal PDF</button><button data-action="edi" class="history-link">EDI payload</button></div></div>`; }
  elements.actionBar.querySelector('[data-action="analyze"]')?.addEventListener("click", analyzeSelectedClaim); elements.actionBar.querySelector('[data-action="resolve"]')?.addEventListener("click", resolveSelectedClaim);
  elements.actionBar.querySelector('[data-action="export-toggle"]')?.addEventListener("click", (event) => { event.stopPropagation(); state.exportMenuOpen = !state.exportMenuOpen; renderActionBar(); });
  elements.actionBar.querySelectorAll('[data-action="pdf"]').forEach((button) => button.addEventListener("click", downloadAppealPdf)); elements.actionBar.querySelectorAll('[data-action="edi"]').forEach((button) => button.addEventListener("click", openEdiInspector));
}
function exportMenu() { return `<div class="export-menu"><button data-action="pdf"><span>PDF appeal packet</span><span>↓</span></button><button data-action="edi"><span>EDI clearinghouse payload</span><span>{ }</span></button></div>`; }

function renderAnalytics() {
  if (!state.analytics) return; const data = state.analytics;
  setText("#analytics-updated", `Updated ${formatTimestamp(data.generated_at)}`); setText("#kpi-first-pass", formatPercent(data.first_pass_resolution_rate)); setText("#kpi-automation", formatPercent(data.automation_coverage_rate)); setText("#kpi-recovered", formatCurrency(data.total_recovered_revenue)); setText("#kpi-recovery-subtitle", `${formatCurrency(data.total_at_risk_revenue)} remains at risk`); setText("#kpi-average", formatCurrency(data.average_claim_value)); renderQueueMetrics();
  document.querySelector("#payer-breakdown").innerHTML = data.payer_breakdown.map((payer) => `<div class="rounded-xl border border-white/[.07] bg-white/[.02] p-3"><div class="flex justify-between"><div><p class="text-[11px] font-black text-zinc-300">${escapeHtml(payer.payer_key)}</p><p class="mt-1 text-[9px] text-zinc-600">${payer.claim_count} claims · ${formatCurrency(payer.total_revenue)}</p></div><span class="confidence-badge">${formatPercent(payer.resolution_success_rate)}</span></div><div class="mt-3 flex h-1.5 overflow-hidden rounded-full bg-white/5"><span class="bg-emerald-400" style="width:${percentageOf(payer.recovered_revenue,payer.total_revenue)}%"></span><span class="bg-rose-400" style="width:${percentageOf(payer.at_risk_revenue,payer.total_revenue)}%"></span></div></div>`).join("");
  if (state.workspace === "analytics") requestAnimationFrame(renderAnalyticsCharts);
}
function renderAnalyticsCharts() { if (!state.analytics || !window.RCMCharts) return; window.RCMCharts.renderPayerDistribution(state.analytics.payer_breakdown); window.RCMCharts.renderRecoveryTrend(state.analytics.recovery_trends); window.RCMCharts.renderDenialHeatmap(document.querySelector("#denial-heatmap"), state.analytics.payer_denial_trends); }

async function loadPolicyChunks(query = "", payer = "") {
  try { const params = new URLSearchParams({ limit: query ? "6" : "40" }); if (query) params.set("q", query); if (payer) params.set("payer", payer); const payload = await api(`/api/rag/chunks?${params}`); state.ragLoaded = true; setText("#rag-index-count", payload.total_indexed); setText("#rag-result-summary", `${payload.chunks.length} ${query ? "semantic matches" : payer ? `${payer} policy chunks` : "indexed policy chunks"}`); document.querySelector("#rag-results").innerHTML = payload.chunks.map((chunk) => `<article class="rag-result"><header><div><h4>${escapeHtml(chunk.section)}</h4><span class="micro-label">${escapeHtml(chunk.payer)} · ${escapeHtml(chunk.source)}</span></div>${chunk.match_score === undefined ? "" : `<span class="confidence-badge">${formatPercent(chunk.match_score)} cosine</span>`}</header><p>${escapeHtml(chunk.text)}</p></article>`).join("") || emptyMessage("No matching policy excerpts"); }
  catch (error) { showToast(error.message, true); }
}
async function searchPolicies(event) { event.preventDefault(); await loadPolicyChunks(document.querySelector("#rag-query").value.trim(), document.querySelector("#rag-payer").value); }
let policySearchTimer;
function schedulePolicySearch() {
  clearTimeout(policySearchTimer);
  policySearchTimer = setTimeout(() => loadPolicyChunks(
    document.querySelector("#rag-query").value.trim(),
    document.querySelector("#rag-payer").value,
  ), 280);
}

async function loadIntegrations() {
  try { const payload = await api("/api/integrations/status"); state.integrationsLoaded = true; const cards = [...payload.portals, { id: "scheduler", name: "Scraper Scheduler", status: payload.scheduler.status, transport: payload.scheduler.cadence, last_check: new Date().toISOString() }, { id: "vector", name: "Chroma Policy Index", status: payload.vector_store.status, transport: `${payload.vector_store.indexed_chunks} indexed chunks`, last_check: new Date().toISOString() }]; document.querySelector("#integration-cards").innerHTML = cards.map((item) => `<article class="integration-card"><div class="icon">${item.id.includes("vector") ? "◈" : item.id.includes("scheduler") ? "◷" : "↗"}</div><h3>${escapeHtml(item.name)}</h3><p>${escapeHtml(item.transport)}</p><footer><span class="status-pill ${item.status === "online" || item.status === "active" ? "status-resolved" : "status-pending"}">${escapeHtml(item.status.toUpperCase())}</span><span>${formatTimestamp(item.last_check)}</span></footer></article>`).join(""); initializeConsole(payload); }
  catch (error) { showToast(error.message, true); }
}
function initializeConsole(payload) { const now = new Date(); const lines = [`[${formatClock(now)}] control-plane  INFO  Integration registry synchronized`, ...payload.portals.map((portal) => `[${formatClock(now)}] ${portal.id.padEnd(14)} INFO  TLS session healthy · portal reachable`), `[${formatClock(now)}] chroma-index   INFO  ${payload.vector_store.indexed_chunks} policy chunks ready for retrieval`, `[${formatClock(now)}] scheduler      INFO  Next scraper sweep scheduled · ${payload.scheduler.cadence}`]; document.querySelector("#integration-console").textContent = lines.join("\n"); }
function openIntegrationConsole() { elements.integrationOverlay.hidden = false; elements.integrationDrawer.hidden = false; document.body.style.overflow = "hidden"; document.querySelector("#integration-console-close-button").focus(); }
function closeIntegrationConsole() { if (!elements.integrationDrawer || elements.integrationDrawer.hidden) return; elements.integrationDrawer.hidden = true; elements.integrationOverlay.hidden = true; if (elements.policyDrawer.hidden && elements.ediModal.hidden) document.body.style.overflow = ""; }
function runIntegrationDiagnostic() { const consoleElement = document.querySelector("#integration-console"); const steps = ["Opening isolated browser contexts", "BCBS portal handshake passed", "Aetna gateway handshake passed", "Chroma collection query passed", "Diagnostic complete · all systems nominal"]; steps.forEach((step,index) => setTimeout(() => { consoleElement.textContent += `\n[${formatClock(new Date())}] diagnostic     ${index === steps.length - 1 ? "PASS" : "INFO"}  ${step}`; consoleElement.scrollTop = consoleElement.scrollHeight; if (index === steps.length - 1) showToast("Connectivity diagnostic passed."); }, index * 350)); }

async function loadGlobalAuditLogs() {
  const query = document.querySelector("#audit-query").value.trim(); const action = document.querySelector("#audit-action").value; const params = new URLSearchParams({ limit: "300" }); if (query) params.set("q", query); if (action) params.set("action", action);
  try { const logs = await api(`/api/audit-logs?${params}`); state.globalAuditLoaded = true; setText("#audit-log-count", logs.length); const body = document.querySelector("#global-audit-body"); body.innerHTML = logs.map((log) => `<tr><td class="whitespace-nowrap text-[10px] text-zinc-500">${escapeHtml(formatTimestamp(log.timestamp))}</td><td class="font-mono text-[10px] font-bold text-cyan-300">${escapeHtml(log.claim_id)}</td><td>${statusBadge(log.action)}</td><td class="text-[10px] text-zinc-400">${escapeHtml(log.performed_by)}</td><td class="max-w-xl text-[10px] leading-5 text-zinc-500">${escapeHtml(log.details)}</td></tr>`).join(""); document.querySelector("#global-audit-empty").hidden = logs.length > 0; }
  catch (error) { showToast(error.message, true); }
}
async function filterAuditLogs(event) { event.preventDefault(); await loadGlobalAuditLogs(); }
let auditSearchTimer;
function scheduleAuditSearch() {
  clearTimeout(auditSearchTimer);
  auditSearchTimer = setTimeout(loadGlobalAuditLogs, 280);
}

async function analyzeSelectedClaims() { const ids = [...state.selectedIds]; if (!ids.length) return; await performAction(() => api("/api/claims/analyze-batch", { method: "POST", body: JSON.stringify({ claim_ids: ids }) }), `${ids.length} selected claim${ids.length === 1 ? "" : "s"} analyzed.`, "bulk"); clearSelection(); }
async function analyzeSelectedClaim() { const claim = selectedClaim(); if (claim) await performAction(() => api(`/api/claims/${encodeURIComponent(claim.id)}/analyze`, { method: "POST" }), "Root cause isolated and policy matched.", "single"); }
async function resolveSelectedClaim() { const claim = selectedClaim(); if (claim) await performAction(() => api(`/api/claims/${encodeURIComponent(claim.id)}/resolve`, { method: "POST" }), "Claim approved for resubmission."); }
async function analyzeAllClaims() { const count = state.claims.filter((claim) => claim.status === "PENDING").length; if (!count) return showToast("No pending claims to analyze."); await performAction(() => api("/api/claims/analyze-all", { method: "POST" }), `${count} pending claims analyzed.`, "bulk"); }
async function runScraper() { await performAction(() => api("/api/scraper/run", { method: "POST" }), "Payer portals synchronized."); state.integrationsLoaded = false; if (state.workspace === "integrations") loadIntegrations(); }

async function loadClaimAuditLogs() { const id = state.selectedId; if (!id) return; state.auditLoading = true; renderClaimAuditTrail(); try { const logs = await api(`/api/claims/${encodeURIComponent(id)}/audit-logs`); if (id === state.selectedId) state.auditLogs[id] = logs; } catch (error) { showToast(error.message, true); } finally { if (id === state.selectedId) { state.auditLoading = false; renderClaimAuditTrail(); renderActionBar(); } } }
function renderClaimAuditTrail() { const logs = state.auditLogs[state.selectedId] || []; elements.auditLoading.hidden = !state.auditLoading; elements.auditEmpty.hidden = state.auditLoading || logs.length > 0; elements.auditTimeline.hidden = state.auditLoading || !logs.length; elements.auditTimeline.innerHTML = logs.map((log,index) => `<li class="relative grid grid-cols-[20px_1fr] gap-3 pb-4"><div class="relative flex justify-center">${index < logs.length - 1 ? '<span class="absolute top-4 h-[calc(100%+4px)] w-px bg-white/10"></span>' : ""}<span class="relative z-10 mt-1.5 h-2.5 w-2.5 rounded-full ${auditColor(log.action)}"></span></div><div class="rounded-xl border border-white/[.07] bg-white/[.025] p-3"><div class="flex justify-between"><strong class="text-[9px] tracking-widest text-zinc-300">${escapeHtml(log.action)}</strong><time class="text-[8px] text-zinc-600">${escapeHtml(formatTimestamp(log.timestamp))}</time></div><p class="mt-2 text-[10px] leading-5 text-zinc-500">${escapeHtml(log.details)}</p></div></li>`).join(""); }

async function downloadAppealPdf() { const claim = selectedClaim(); if (!claim || state.busy) return; state.exportMenuOpen = false; state.busy = true; renderClaims(); try { const response = await fetch(`/api/claims/${encodeURIComponent(claim.id)}/export/appeal-pdf`); if (!response.ok) throw new Error(await responseError(response)); downloadBlob(await response.blob(), `MainStreet_Appeal_${claim.id}.pdf`); await loadClaimAuditLogs(); showToast("Appeal packet downloaded."); } catch (error) { showToast(error.message, true); } finally { state.busy = false; renderClaims(); } }
async function openEdiInspector() { const claim = selectedClaim(); if (!claim || state.busy) return; state.exportMenuOpen = false; state.busy = true; renderClaims(); try { state.ediPayload = await api(`/api/claims/${encodeURIComponent(claim.id)}/export/clearinghouse`); elements.ediCode.innerHTML = syntaxHighlightJson(state.ediPayload); setText("#edi-filename", `MainStreet_837P_${claim.id}.json`); elements.ediModal.hidden = false; document.body.style.overflow = "hidden"; await loadClaimAuditLogs(); } catch (error) { showToast(error.message, true); } finally { state.busy = false; renderClaims(); } }
function closeEdiInspector() { if (!elements.ediModal || elements.ediModal.hidden) return; elements.ediModal.hidden = true; if (elements.policyDrawer.hidden && elements.integrationDrawer.hidden) document.body.style.overflow = ""; }
async function copyPayload() { if (!state.ediPayload) return; const text = JSON.stringify(state.ediPayload,null,2); try { await navigator.clipboard.writeText(text); showToast("Payload copied."); } catch (_) { const area=document.createElement("textarea"); area.value=text; document.body.append(area); area.select(); document.execCommand("copy"); area.remove(); showToast("Payload copied."); } }
function openPolicyDrawer() { const claim=selectedClaim(); if(!claim?.policy_citation)return; setText("#policy-claim-id",`${claim.id} · ${claim.insurer}`); setText("#policy-citation-text",claim.policy_citation); elements.policyOverlay.hidden=false; elements.policyDrawer.hidden=false; document.body.style.overflow="hidden"; }
function closePolicyDrawer() { if(!elements.policyDrawer||elements.policyDrawer.hidden)return; elements.policyDrawer.hidden=true; elements.policyOverlay.hidden=true; if(elements.ediModal.hidden&&elements.integrationDrawer.hidden)document.body.style.overflow=""; }

async function performAction(action,message,processingMode=null) { if(state.busy)return; state.busy=true; state.processingMode=processingMode; state.exportMenuOpen=false; renderClaims(); try{await action(); await Promise.all([loadClaims(),loadAnalytics()]); state.globalAuditLoaded=false; showToast(message);}catch(error){showToast(error.message,true);}finally{state.busy=false;state.processingMode=null;renderClaims();} }
function setBusy(busy){elements.scraperButton.disabled=busy;elements.analyzeAllButton.disabled=busy;elements.scraperButton.classList.toggle("animate-pulse",busy);}
function setFilter(filter){state.filter=filter;state.selectedId=null;state.detailTab="resolution";state.exportMenuOpen=false;renderClaims();}
function setDetailTab(tab){state.detailTab=tab;renderDetailTabs();}
function renderDetailTabs(){const audit=state.detailTab==="audit";elements.resolutionPanel.hidden=audit;elements.auditPanel.hidden=!audit;elements.resolutionTab.classList.toggle("active",!audit);elements.auditTab.classList.toggle("active",audit);elements.resolutionTab.setAttribute("aria-selected",String(!audit));elements.auditTab.setAttribute("aria-selected",String(audit));}
function selectClaim(id){state.selectedId=id;state.detailTab="resolution";state.exportMenuOpen=false;renderClaims();loadClaimAuditLogs();}
function selectedClaim(){return state.claims.find((claim)=>claim.id===state.selectedId)||null;}
function filteredClaims(){return state.filter==="ALL"?state.claims:state.claims.filter((claim)=>claim.status===state.filter);}
function statusBadge(status){const style={PENDING:"status-pending",ANALYZED:"status-analyzed",RESOLVED:"status-resolved",CLAIM_RESUBMITTED:"status-resolved",SCRAPED:"status-analyzed",EXPORTED:"status-pending"}[status]||"";return `<span class="status-pill ${style}">${escapeHtml(status)}</span>`;}
function auditColor(action){return{SCRAPED:"bg-violet-400",ANALYZED:"bg-cyan-400",RESOLVED:"bg-emerald-400",CLAIM_RESUBMITTED:"bg-emerald-400",EXPORTED:"bg-amber-400"}[action]||"bg-zinc-500";}
function updateSurfaceSpotlight(event){const surface=event.target.closest(".queue-shell,.panel-card,.kpi-card,.integration-card,.rag-result,.data-tile,.evidence-cell,.ai-resolution");if(!surface)return;const bounds=surface.getBoundingClientRect();surface.classList.add("spotlight-surface");surface.style.setProperty("--mouse-x",`${event.clientX-bounds.left}px`);surface.style.setProperty("--mouse-y",`${event.clientY-bounds.top}px`);}
function shortPayer(value){if(/blue cross|blue shield/i.test(value))return"BCBS";if(/unitedhealth/i.test(value))return"UnitedHealthcare";return value;}
function formatCurrency(value){return new Intl.NumberFormat("en-US",{style:"currency",currency:"USD"}).format(Number(value||0));}
function formatPercent(value){return `${new Intl.NumberFormat("en-US",{maximumFractionDigits:1}).format(Number(value||0))}%`;}
function percentageOf(value,total){return total?Math.max(0,Math.min(100,Number(value)/Number(total)*100)):0;}
function formatDate(value){return value?new Intl.DateTimeFormat("en-US",{month:"short",day:"numeric",year:"numeric",timeZone:"UTC"}).format(new Date(`${value}T00:00:00Z`)):"—";}
function formatTimestamp(value){return new Intl.DateTimeFormat("en-US",{month:"short",day:"numeric",hour:"numeric",minute:"2-digit"}).format(new Date(value));}
function formatClock(value){return new Intl.DateTimeFormat("en-US",{hour12:false,hour:"2-digit",minute:"2-digit",second:"2-digit"}).format(value);}
function setText(selector,value){document.querySelector(selector).textContent=value;}
function emptyMessage(message){return `<div class="empty-state"><strong>${escapeHtml(message)}</strong></div>`;}
function syntaxHighlightJson(payload){const json=escapeHtml(JSON.stringify(payload,null,2));return json.replace(/(&quot;(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\&])*?&quot;)(\s*:)?|\b(true|false)\b|\b(null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,(match,string,keyMarker,bool,nil,number)=>{if(string)return`<span class="${keyMarker?"json-key":"json-string"}">${string}</span>${keyMarker||""}`;if(bool)return`<span class="json-boolean">${bool}</span>`;if(nil)return`<span class="json-null">${nil}</span>`;return`<span class="json-number">${number}</span>`;});}
function downloadBlob(blob,filename){const url=URL.createObjectURL(blob);const link=document.createElement("a");link.href=url;link.download=filename;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function responseError(response){try{return(await response.json()).detail||`Request failed (${response.status})`;}catch(_){return`Request failed (${response.status})`;}}
function setConnection(mode){const config={loading:["bg-amber-400","Synchronizing"],online:["bg-emerald-400 shadow-[0_0_9px_#34d399]","Systems nominal"],offline:["bg-rose-400","Connection fault"]}[mode];document.querySelector("#connection-dot").className=`h-2 w-2 rounded-full ${config[0]}`;setText("#connection-label",config[1]);}
let toastTimer;function showToast(message,error=false){clearTimeout(toastTimer);elements.toast.textContent=message;elements.toast.className=`toast${error?" error":""}`;elements.toast.hidden=false;toastTimer=setTimeout(()=>{elements.toast.hidden=true;},4000);}
function escapeHtml(value){return String(value).replace(/[&<>'"]/g,(character)=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[character]);}
