"use strict";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const state = { contacts: [], contact: null, result: null, selected: new Map(), upload: null, config: null, previous: null, llamaOwnerToken: null, trash: [], purgeTarget: null, importConflictResolved: true, importConflictChoice: null, view: "topics", searchVersion: 0, previewVersion: 0, contextVersion: 0, busyAI: false };
let toastTimer;
function toast(message) {
  $("toast").textContent = message; $("toast").hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { $("toast").hidden = true; }, 6500);
}
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-Memory-Client": "1" }, body: JSON.stringify(data)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "请求失败，请重试。");
  return result;
}
function messagePreview(message) {
  return '<div class="preview-row"><small>' + esc(message.timestamp) + " · " + esc(message.speaker) + '</small><p>' + esc(message.text) + "</p></div>";
}
function openDialog(id) { if (!$(id).open) $(id).showModal(); }
function closeDialog(id) { $(id).close(); if (id === "import-dialog") state.previewVersion++; }
function renderContacts() {
  $("contact-count").textContent = state.contacts.length;
  $("contacts").innerHTML = state.contacts.length ? state.contacts.map(contact =>
    '<button class="contact-item ' + (contact.id === state.contact?.id ? "active" : "") + '" data-contact="' + contact.id + '">' +
    '<span class="avatar">' + esc([...contact.name][0]) + '</span><span><strong>' + esc(contact.name) +
    '</strong><small>' + contact.message_count.toLocaleString() + " 条记录</small></span></button>"
  ).join("") : '<p class="side-empty">你的联系人会出现在这里。<br>先导入一份聊天记录。</p>';
}
async function loadContacts(selectId) {
  const [result, trash] = await Promise.all([api("/api/contacts"), api("/api/trash")]);
  state.contacts = result.contacts; state.trash = trash.contacts; renderContacts();
  $("trash-count").textContent = state.trash.length ? String(state.trash.length) : "";
  if (selectId) await selectContact(selectId);
}
function resetContext() {
  state.contextVersion++; $("context-panel").classList.remove("open");
  $("context-content").innerHTML = '<div class="context-empty"><div class="quote-mark" aria-hidden="true">“</div><h3>每个回答，都有来处。</h3><p>点击一条记录的“查看原文”，<br>核对它出现时的前后对话。</p><div class="source-key"><span>说话人</span><span>时间</span><span>原始行号</span></div></div>';
}
async function selectContact(id) {
  const version = ++state.searchVersion;
  try {
    const contact = await api("/api/contact?id=" + id);
    if (version !== state.searchVersion) return;
    state.contact = contact; state.result = null; state.previous = null; state.selected.clear();
    $("query").value = ""; $("date-from").value = ""; $("date-to").value = "";
    $("speaker").innerHTML = '<option value="">所有人</option>' + contact.speakers.map(s => '<option value="' + esc(s) + '">' + esc(s) + "</option>").join("");
    $("page-title").textContent = "与 " + contact.name + " 的聊天记忆";
    $("breadcrumb-name").textContent = contact.name;
    $("page-subtitle").textContent = contact.message_count.toLocaleString() + " 条记录 · " + contact.file_count + " 份文件 · " +
      (contact.message_count ? (contact.first_at || "").slice(0, 10) + " 至 " + (contact.last_at || "").slice(0, 10) : "尚未导入聊天记录");
    $("export-button").disabled = false; $("trash-contact").disabled = false; $("search-button").disabled = false;
    $("topics-view").disabled = false; $("search-view").disabled = false;
    renderContacts(); resetContext();
    if (contact.message_count) await showTopics(); else { setView("topics"); renderEmpty(); }
  } catch (error) { toast(error.message); }
}
function renderEmpty() {
  if (state.contact && !state.contact.message_count) {
    $("main-content").innerHTML = '<div class="empty-main"><p class="eyebrow">独立的空白档案</p><h2>联系人已创建，等待导入。</h2><p>点击导入 TXT，在预览的“保存到”中手动选择此联系人。不会自动关联虚构示例或当前档案。</p><button class="primary import-trigger">导入聊天记录</button></div>';
    $("main-content").querySelector(".import-trigger").addEventListener("click", showImport);
    return;
  }
  $("main-content").innerHTML = state.contact ?
    '<div class="empty-main"><p class="eyebrow">资料已就绪</p><h2>想起一件事，从这里找起。</h2><p>输入关键词或一句问题。先找到相关原话，再展开上下文核对。你也可以选择日期和说话人，缩小范围。</p></div><p class="hint-label">可以这样查</p>' + suggestions() :
    '<div class="empty-main"><p class="eyebrow">第一步 · 导入</p><h2>给记忆，留一份可以查找的底稿。</h2><p>上传已有的 TXT 聊天记录。说话人、时间和原话一起保存，随时回来查证。</p><div class="empty-actions"><button class="primary import-trigger">导入第一份 TXT</button><button class="text-button" id="demo-trigger">试用虚构示例</button></div></div><p class="hint-label">导入后，你可以查找</p>' + suggestions();
  document.querySelectorAll(".suggestion").forEach(button => button.addEventListener("click", () => {
    if (!state.contact) { toast("先导入 TXT，或试用虚构示例。"); return; }
    $("query").value = button.dataset.query; runSearch();
  }));
  $("demo-trigger")?.addEventListener("click", loadDemo);
  document.querySelectorAll("#main-content .import-trigger").forEach(button => button.addEventListener("click", showImport));
}
function setView(view) {
  state.view = view;
  $("topics-view").classList.toggle("active", view === "topics");
  $("search-view").classList.toggle("active", view === "search");
  $("search-form").hidden = view !== "search";
  document.querySelector(".filters").hidden = view !== "search";
}
async function showTopics() {
  if (!state.contact) return;
  setView("topics"); const version = ++state.searchVersion;
  $("main-content").innerHTML = '<div class="loading"><span class="spinner"></span>正在按固定规则整理主题…</div>';
  try {
    const overview = await api("/api/topics?id=" + state.contact.id);
    if (version !== state.searchVersion || state.view !== "topics") return;
    if (!state.contact.message_count) { renderEmpty(); return; }
    if (!overview.topics.length) {
      $("main-content").innerHTML = '<div class="empty-main"><p class="eyebrow">规则提取 · v' + esc(overview.rule_version) + '</p><h2>暂时没有匹配到主题</h2><p>这些记录仍可在“原文检索”中查找。没有规则匹配不代表聊天中没有其他内容。</p></div>';
      return;
    }
    $("main-content").innerHTML = '<div class="answer-box"><div class="answer-label"><b>主题概览</b><span>本机规则提取 · v' + esc(overview.rule_version) + '</span></div><p>以下仅表示记录中提到过这些主题，不推断喜好、关系、性格或健康结论。</p><p class="footnote">命中 ' + overview.classified_messages.toLocaleString() + ' 条消息；一条消息可能出现在多个主题中。</p></div><div class="topic-grid">' + overview.topics.map(topic => '<article class="topic-card"><div class="topic-card-head"><h2>' + esc(topic.name) + '</h2><span>' + topic.message_count.toLocaleString() + ' 条</span></div><p>' + topic.day_count + ' 个日期 · ' + esc(topic.first_at.slice(0, 10)) + ' 至 ' + esc(topic.last_at.slice(0, 10)) + '</p><div class="topic-evidence">' + topic.messages.map(message => '<button class="topic-message" data-id="' + message.id + '"><b>M' + message.id + '</b><span>' + esc(message.timestamp.slice(0, 10)) + ' · ' + esc(message.speaker) + '</span><em>' + esc(message.text.length > 70 ? message.text.slice(0, 70) + '…' : message.text) + '</em></button>').join("") + '</div></article>').join("") + '</div>';
    document.querySelectorAll(".topic-message").forEach(button => button.addEventListener("click", () => showContext(Number(button.dataset.id))));
  } catch (error) {
    if (version === state.searchVersion) $("main-content").innerHTML = '<div class="error-box">主题概览加载失败：' + esc(error.message) + '</div>';
  }
}
function showSearchView() {
  setView("search"); state.searchVersion++;
  if (state.result) renderResults(); else renderEmpty();
  $("query").focus();
}
function suggestions() {
  return '<div class="suggestions">' + ["我们第一次对话是哪一天？", "以前和现在的说法有什么变化？", "最近主要聊了什么？"].map(text =>
    '<button class="suggestion" data-query="' + esc(text) + '">' + esc(text) + "</button>").join("") + "</div>";
}
function searchParams() {
  return { contact_id: state.contact?.id, query: $("query").value.trim(), date_from: $("date-from").value,
    date_to: $("date-to").value, speaker: $("speaker").value };
}
async function runSearch() {
  if (!state.contact) { toast("请先导入一份聊天记录。"); return; }
  setView("search");
  const params = searchParams();
  if (!params.query) { $("query").focus(); return; }
  const version = ++state.searchVersion; state.result = null; state.selected.clear();
  $("search-button").disabled = true;
  $("main-content").innerHTML = '<div class="loading"><span class="spinner"></span>正在分析问题并查找证据…</div>';
  resetContext();
  try {
    const result = await api("/api/qa", {
      contact_id: params.contact_id,
      question: params.query,
      filters: { date_from: params.date_from, date_to: params.date_to, speaker: params.speaker },
      previous: state.previous,
      use_model: Boolean(state.config),
      config: state.config
    });
    if (version !== state.searchVersion) return;
    state.previous = result.next_context;
    state.result = { ...result, params, query: params.query, results: result.citations || [] };
    (result.citations || []).slice(0, 6).forEach(message => state.selected.set(message.id, message));
    renderResults();
  } catch (error) {
    if (version === state.searchVersion) $("main-content").innerHTML = '<div class="error-box">' + esc(error.message) + "</div>";
  } finally { if (version === state.searchVersion) $("search-button").disabled = false; }
}
function renderResults() {
  const result = state.result, scope = result.scope || {}, messages = result.results || [];
  const kind = { deterministic: "程序精确计算", evidence: "本机证据回答", model: "本地模型归纳", combined: "精确计算 + 证据回答" }[result.answer_kind] || result.answer_kind;
  const scopeLabel = "联系人总量 " + Number(scope.contact_total_messages || 0).toLocaleString() +
    " · 过滤后 " + Number(scope.filtered_messages || 0).toLocaleString() +
    " · 候选 " + Number(scope.candidate_messages || 0).toLocaleString() +
    " · 送入整理 " + Number(scope.evidence_messages || 0).toLocaleString() +
    " · 最终引用 " + Number(scope.final_evidence_messages || 0).toLocaleString();
  const units = (result.answer_units || []).map((unit, index) =>
    '<section class="answer-unit"><div><b>结论 ' + (index + 1) + '</b><span>' + esc(unit.kind || unit.unit_type || "fact") + '</span></div><p>' +
    esc(unit.text) + '</p>' +
    (unit.calculation ? '<small>口径：' + esc(unit.calculation) + '</small>' : '') +
    ((unit.citations || unit.citation_ids || []).length ? '<div class="unit-citations">' +
      (unit.citations || unit.citation_ids).map(id => {
        const label = String(id).startsWith("M") ? String(id) : "M" + id;
        return '<button class="suggestion answer-citation" data-id="' + Number(label.slice(1)) + '">' + esc(label) + ' 查看原文</button>';
      }).join("") + '</div>' : '') + '</section>'
  ).join("");
  const uncertainty = (result.uncertainty || []).length ?
    '<div class="uncertainty"><b>不确定性</b><ul>' + result.uncertainty.map(item => '<li>' + esc(item) + '</li>').join("") + '</ul></div>' : "";
  const inherited = result.route?.inherited_fields?.length ? " · 已继承：" + result.route.inherited_fields.join("、") : "";
  $("main-content").innerHTML = '<div class="answer-box" id="answer-box"><div class="answer-label"><b>' + esc(kind) +
    '</b><span>' + esc((result.method || []).join(" + ")) + '</span></div>' + units + uncertainty +
    '<p class="footnote">' + esc(scopeLabel + inherited) + '</p></div>' +
    (messages.length ? '<div class="section-head"><h2>引用证据 <span>' + messages.length + '</span></h2><span>每条都可回到来源行号</span></div>' +
    messages.map(message => '<article class="result-card" id="message-' + message.id + '"><div class="result-top"><div class="result-identity">' +
      '<span class="avatar">' + esc([...message.speaker][0]) + '</span><b>' + esc(message.speaker) + '</b></div><span class="result-time">' + esc(message.timestamp) +
      '</span></div><p class="result-text">' + esc(message.text.length > 550 ? message.text.slice(0, 550) + "…（展开查看全文）" : message.text) +
      '</p><div class="result-bottom"><span class="source-ref" title="' + esc(message.source_name) + '">M' + message.id + " · " + esc(message.source_name) +
      " · 第 " + message.line_start + "–" + message.line_end + ' 行</span><button class="view-source" data-source="' + message.id + '">查看原文</button></div></article>').join("") :
      '<div class="empty-main"><h3>当前范围未找到证据</h3><p>可以换一种说法或清空日期、说话人限制。当前未找到不代表记录范围之外不存在。</p><button id="reset-search-range" class="secondary">清空限制</button></div>');
  $("reset-search-range")?.addEventListener("click", () => { clearFilters(); runSearch(); });
  document.querySelectorAll(".view-source").forEach(button => button.addEventListener("click", () => showContext(Number(button.dataset.source))));
  document.querySelectorAll(".answer-citation").forEach(button => button.addEventListener("click", () => showContext(Number(button.dataset.id))));
}
function updateSelection() {
  if ($("selection-count")) { $("selection-count").textContent = "已选 " + state.selected.size + " / 12 条"; $("selection-count").className = "subtitle"; }
  if ($("ai-answer")) $("ai-answer").disabled = !state.selected.size || state.busyAI;
}
async function showContext(id) {
  const version = ++state.contextVersion; $("context-panel").classList.add("open");
  $("context-content").innerHTML = '<div class="loading"><span class="spinner"></span>正在定位原文…</div>';
  document.querySelectorAll(".result-card").forEach(card => card.classList.toggle("active", card.id === "message-" + id));
  try {
    const context = await api("/api/context?id=" + id);
    if (version !== state.contextVersion) return;
    $("context-content").innerHTML = '<div class="context-meta"><strong>' + esc(context.source_name) + "</strong><br>消息 M" + id + " · 第 " + context.line_start + "–" + context.line_end +
      ' 行<a href="/api/source?id=' + context.source_id + '">下载原始 TXT</a></div>' +
      context.messages.map(message => '<div class="thread-message ' + (message.id === id ? "focus" : "") + '"><div class="who"><span>' + esc(message.speaker) +
        '</span><span>' + esc(message.timestamp.slice(5)) + '</span></div><p>' + esc(message.text) + "</p></div>").join("") +
      '<div class="context-tools"><button class="text-button" id="include-context">把这段上下文加入 AI 选择</button></div>' +
      '<details class="raw-detail"><summary>查看原始行号</summary><pre>' + esc(context.raw_lines.map(line => line.number + "  " + line.text).join("\n")) + "</pre></details>";
    $("include-context").addEventListener("click", () => {
      const newOnes = context.messages.filter(m => !state.selected.has(m.id));
      if (state.selected.size + newOnes.length > 12) { toast("加入后会超过 12 条，请先取消部分选择。"); return; }
      context.messages.forEach(message => state.selected.set(message.id, message));
      document.querySelectorAll(".evidence-check").forEach(box => { box.checked = state.selected.has(Number(box.dataset.message)); });
      updateSelection(); toast("已加入这段上下文。发送前还可以核对。");
    });
  } catch (error) {
    if (version === state.contextVersion) $("context-content").innerHTML = '<div class="error-box">' + esc(error.message) + "</div>";
  }
}
function clearFilters() { $("speaker").value = ""; $("date-from").value = ""; $("date-to").value = ""; }
function showImport() {
  state.upload = null; state.importConflictResolved = true; state.importConflictChoice = null; state.previewVersion++;
  $("import-preview").hidden = true; $("import-preview").innerHTML = ""; $("confirm-import").disabled = true;
  $("import-hint").textContent = "解析后可以先核对，再保存。"; $("drop-zone").hidden = false; openDialog("import-dialog");
}
async function loadDemo() {
  showImport();
  try { await previewUpload(await api("/api/demo")); } catch (error) { toast(error.message); }
}
async function readFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".txt")) { toast("请选择 .txt 文件。"); return; }
  if (file.size > 5 * 1024 * 1024) { toast("文件超过 5 MB，请分段导出后导入。"); return; }
  try {
    const bytes = new Uint8Array(await file.arrayBuffer()); let binary = "";
    for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    await previewUpload({ filename: file.name, base64: btoa(binary) });
  } catch (error) { toast(error.message); }
}
async function previewUpload(upload) {
  const version = ++state.previewVersion; state.upload = null; $("confirm-import").disabled = true;
  $("import-preview").hidden = false; $("import-preview").innerHTML = '<div class="loading"><span class="spinner"></span>正在解析文件…</div>';
  try {
    const preview = await api("/api/preview", upload);
    if (version !== state.previewVersion) return;
    state.upload = upload; $("drop-zone").hidden = true;
    $("import-preview").innerHTML = '<p class="dialog-intro">' + esc(upload.filename) + ' <button class="text-button" id="replace-file">更换文件</button></p>' +
      '<div class="preview-stats"><span>' + preview.count.toLocaleString() + ' 条消息</span><span>' + preview.speakers.length + ' 位说话人</span><span>' + esc(preview.encoding) + '</span></div>' +
      (preview.count ? '<p class="subtitle">' + esc(preview.first_at) + " 至 " + esc(preview.last_at) + '</p>' : "") +
      '<label class="field">保存到<select id="import-contact"><option value="">新建联系人档案</option>' + state.contacts.map(contact =>
        '<option value="' + contact.id + '">' + esc(contact.name) + " · #" + contact.id + "</option>").join("") + '</select></label>' +
      '<label class="field" id="new-name-field">联系人名称<input id="import-name" maxlength="100" value="' + esc(upload.name || preview.contact_name || upload.filename.replace(/\.txt$/i, "")) + '"></label>' +
      (preview.trash_matches.length ? '<section class="conflict-box" aria-labelledby="conflict-title"><h3 id="conflict-title">回收站中发现可能相同的档案</h3><p>请选择恢复其中一个，或明确仍作为新联系人导入。系统不会自动恢复或合并。</p>' + preview.trash_matches.map(contact =>
        '<div class="trash-row"><div><strong>' + esc(contact.name) + ' <small>#' + contact.id + '</small></strong><p>' + contact.message_count + ' 条记录 · ' + contact.file_count + ' 份文件 · ' + (contact.reasons.includes("file") ? "相同文件" : "同名") + '</p></div><button class="secondary restore-import" type="button" data-id="' + contact.id + '">恢复并选用</button></div>').join("") +
        '<div class="conflict-actions"><button id="import-as-new" class="secondary" type="button">仍作为新联系人导入</button><button id="cancel-conflict" class="text-button" type="button">取消导入</button></div></section>' : "") +
      preview.warnings.map(w => '<div class="error-box">' + esc(w) + "</div>").join("") +
      (preview.unparsed_count ? '<details class="raw-detail"><summary>' + preview.unparsed_count + ' 行文件说明或未识别内容（保留于原文件）</summary><pre>' + esc(preview.unparsed.map(line => line.line + "  " + line.text).join("\n")) + "</pre></details>" : "") +
      '<div class="preview-rows">' + preview.messages.map(messagePreview).join("") + '</div><p class="subtitle">预览前 15 条。多行正文会保留在同一条消息中。</p>';
    $("replace-file").addEventListener("click", () => $("file-input").click());
    $("import-contact").addEventListener("change", updateImportDestination);
    $("import-name").addEventListener("input", updateImportDestination);
    state.importConflictResolved = !preview.trash_matches.length; state.importConflictChoice = null;
    $("new-name-field").hidden = !!$("import-contact").value; $("confirm-import").disabled = !preview.count || !state.importConflictResolved;
    document.querySelectorAll(".restore-import").forEach(button => button.addEventListener("click", () => restoreForImport(Number(button.dataset.id), preview.count)));
    $("import-as-new")?.addEventListener("click", () => { state.importConflictResolved = true; state.importConflictChoice = "new"; $("confirm-import").disabled = !preview.count; $("import-as-new").textContent = "已选择：新建联系人"; updateImportDestination(); });
    $("cancel-conflict")?.addEventListener("click", () => closeDialog("import-dialog"));
    updateImportDestination();
  } catch (error) {
    if (version === state.previewVersion) { $("import-preview").innerHTML = '<div class="error-box">' + esc(error.message) + "</div>"; $("drop-zone").hidden = false; }
  }
}
function updateImportDestination() {
  const contact = state.contacts.find(c => String(c.id) === $("import-contact").value);
  $("new-name-field").hidden = !!contact;
  $("import-hint").textContent = contact ? "将追加到：" + contact.name + "（#" + contact.id + "）。请确认不是其他人的档案。" : "将新建独立联系人：" + ($("import-name").value.trim() || "请填写姓名") + "。不会合并到已有档案。";
}
async function restoreForImport(contactId, messageCount) {
  const button = document.querySelector('.restore-import[data-id="' + contactId + '"]');
  if (button) button.disabled = true;
  try {
    await api("/api/contact/restore", { contact_id: contactId });
    const result = await api("/api/contacts"); state.contacts = result.contacts; renderContacts();
    const contact = state.contacts.find(item => item.id === contactId);
    const option = document.createElement("option"); option.value = String(contactId); option.textContent = contact.name + " · #" + contactId;
    $("import-contact").append(option); $("import-contact").value = String(contactId);
    state.importConflictResolved = true; state.importConflictChoice = "restored"; $("confirm-import").disabled = !messageCount;
    updateImportDestination(); toast("已从回收站恢复并选中：" + contact.name);
    const trash = await api("/api/trash"); state.trash = trash.contacts; $("trash-count").textContent = state.trash.length || "";
  } catch (error) { toast(error.message); if (button) button.disabled = false; }
}

function resetSelectedContact() {
  state.contact = null; state.result = null; state.selected.clear();
  $("breadcrumb-name").textContent = "聊天记忆"; $("page-title").textContent = "那些聊过的事，都有迹可循。";
  $("page-subtitle").textContent = "导入一份 TXT，找回某个人提过的喜好、约定和日常。";
  $("export-button").disabled = true; $("trash-contact").disabled = true; $("search-button").disabled = true;
  $("topics-view").disabled = true; $("search-view").disabled = true; setView("topics");
  renderContacts(); resetContext(); renderEmpty();
}

function prepareTrashContact() {
  if (!state.contact) return;
  $("trash-contact-summary").textContent = "“" + state.contact.name + "”共有 " + state.contact.message_count + " 条记录、" + state.contact.file_count + " 份文件。";
  openDialog("trash-contact-dialog");
}

async function trashSelectedContact() {
  if (!state.contact) return;
  const button = $("confirm-trash-contact"); button.disabled = true;
  try {
    const result = await api("/api/contact/trash", { contact_id: state.contact.id });
    closeDialog("trash-contact-dialog"); resetSelectedContact(); await loadContacts();
    toast("已将“" + result.name + "”移入回收站，可随时恢复。"); $("trash-trigger").focus();
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
}

async function showTrash() {
  openDialog("trash-dialog"); $("trash-list").innerHTML = '<div class="loading"><span class="spinner"></span>正在读取回收站…</div>';
  try {
    const result = await api("/api/trash"); state.trash = result.contacts; $("trash-count").textContent = state.trash.length || "";
    $("trash-list").innerHTML = state.trash.length ? state.trash.map(contact => '<div class="trash-row"><div><strong>' + esc(contact.name) + ' <small>#' + contact.id + '</small></strong><p>' + contact.message_count + ' 条记录 · ' + contact.file_count + ' 份文件<br>移入时间 ' + esc(contact.deleted_at.replace("T", " ")) + '</p></div><div class="trash-actions"><button class="secondary restore-contact" data-id="' + contact.id + '">恢复</button><button class="text-button danger-text purge-contact" data-id="' + contact.id + '">永久删除</button></div></div>').join("") : '<div class="empty-main"><h3>回收站是空的</h3><p>移入的联系人会保留在这里，且不会自动清理。</p></div>';
    document.querySelectorAll(".restore-contact").forEach(button => button.addEventListener("click", () => restoreContact(Number(button.dataset.id), button)));
    document.querySelectorAll(".purge-contact").forEach(button => button.addEventListener("click", () => preparePurge(Number(button.dataset.id))));
  } catch (error) { $("trash-list").innerHTML = '<div class="error-box">' + esc(error.message) + '</div>'; }
}

async function restoreContact(contactId, button) {
  button.disabled = true;
  try {
    const result = await api("/api/contact/restore", { contact_id: contactId });
    closeDialog("trash-dialog"); await loadContacts(result.contact_id); toast("已恢复“" + result.name + "”。");
  } catch (error) { toast(error.message); button.disabled = false; }
}

function preparePurge(contactId) {
  const contact = state.trash.find(item => item.id === contactId); if (!contact) return;
  state.purgeTarget = contact; $("purge-name").value = ""; $("purge-error").textContent = "";
  $("purge-summary").textContent = "永久删除“" + contact.name + "”的 " + contact.message_count + " 条记录和 " + contact.file_count + " 份文件。此操作无法恢复。";
  closeDialog("trash-dialog"); openDialog("purge-dialog"); $("purge-name").focus();
}

async function purgeContact(event) {
  event.preventDefault(); if (!state.purgeTarget) return;
  const button = $("confirm-purge"); button.disabled = true; $("purge-error").textContent = "";
  try {
    const result = await api("/api/contact/purge", { contact_id: state.purgeTarget.id, exact_name: $("purge-name").value });
    state.purgeTarget = null; closeDialog("purge-dialog"); await loadContacts(); toast("已永久删除“" + result.name + "”。"); $("trash-trigger").focus();
  } catch (error) { $("purge-error").textContent = error.message; }
  finally { button.disabled = false; }
}
async function createContact(event) {
  event.preventDefault();
  const name = $("new-contact-name").value.trim();
  if (!name) { $("new-contact-error").textContent = "请输入联系人姓名。"; return; }
  $("create-contact").disabled = true;
  try {
    const result = await api("/api/contacts", { name });
    closeDialog("new-contact-dialog"); await loadContacts(result.contact_id);
    toast("已创建独立联系人：" + name);
  } catch (error) { $("new-contact-error").textContent = error.message; }
  finally { $("create-contact").disabled = false; }
}
async function confirmImport() {
  if (!state.upload) return;
  const retryUpload = { ...state.upload, name: $("import-name").value };
  $("confirm-import").disabled = true; $("confirm-import").textContent = "正在保存…";
  try {
    const result = await api("/api/import", { ...state.upload, contact_id: $("import-contact").value || null, name: $("import-name").value,
      trash_conflict_action: state.importConflictChoice === "new" ? "new" : null });
    closeDialog("import-dialog"); await loadContacts(result.contact_id);
    toast("新增 " + result.added + " 条，已存在 " + result.existing + " 条。" + (result.uncertain ? "其中 " + result.uncertain + " 条疑似重复，已保留待核对。" : ""));
  } catch (error) {
    if (error.message.includes("回收站")) await previewUpload(retryUpload);
    toast(error.message);
  }
  finally { $("confirm-import").disabled = !state.upload || !state.importConflictResolved; $("confirm-import").textContent = "保存到资料库"; }
}
function prepareAnswer() {
  if (!state.config) { openDialog("settings-dialog"); return; }
  if (!state.selected.size || !state.result) return;
  const location = new URL(state.config.endpoint);
  const destination = ["127.0.0.1", "localhost", "[::1]"].includes(location.hostname) ? "发送到本机 " + location.hostname : "发送到远程服务 " + state.config.endpoint;
  $("send-destination").textContent = destination + "；共 " + state.selected.size + " 条原文。问题：" + state.result.query;
  $("send-preview").innerHTML = [...state.selected.values()].map(messagePreview).join(""); openDialog("send-dialog");
}
async function sendAnswer() {
  if (!state.result || !state.config || state.busyAI) return;
  const version = state.searchVersion;
  const params = { ...state.result.params, evidence_ids: [...state.selected.keys()], config: { ...state.config }, allow_send: true };
  closeDialog("send-dialog"); state.busyAI = true; updateSelection();
  $("answer-box").innerHTML = '<div class="answer-label"><b>AI 正在整理</b><span class="spinner"></span></div><p>正在依据所选原文生成回答…</p>';
  try {
    const result = await api("/api/answer", params);
    if (version !== state.searchVersion) return;
    $("answer-box").innerHTML = '<div class="answer-label"><b>AI 整理</b><span>' + esc(result.destination) + '</span></div><p>' + esc(result.answer) +
      '</p><p class="footnote">' + esc(result.notice) + '</p><div class="suggestions">' +
      result.citations.map(id => '<button class="suggestion ai-citation" data-id="' + Number(id.slice(1)) + '">' + esc(id) + " 查看原文</button>").join("") + "</div>";
    document.querySelectorAll(".ai-citation").forEach(button => button.addEventListener("click", () => showContext(Number(button.dataset.id))));
  } catch (error) {
    if (version === state.searchVersion) $("answer-box").innerHTML = '<div class="answer-label"><b>AI 暂未完成</b></div><p>' + esc(error.message) + '</p><p class="footnote">下方原文和本地检索仍可使用。</p>';
  } finally { state.busyAI = false; updateSelection(); }
}
document.querySelectorAll(".import-trigger").forEach(button => button.addEventListener("click", showImport));
$("new-contact-trigger").addEventListener("click", () => {
  $("new-contact-name").value = ""; $("new-contact-error").textContent = "";
  openDialog("new-contact-dialog"); $("new-contact-name").focus();
});
$("new-contact-form").addEventListener("submit", createContact);
$("trash-contact").addEventListener("click", prepareTrashContact);
$("confirm-trash-contact").addEventListener("click", trashSelectedContact);
$("trash-trigger").addEventListener("click", showTrash);
$("purge-form").addEventListener("submit", purgeContact);
document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => closeDialog(button.dataset.close)));
$("contacts").addEventListener("click", event => { const button = event.target.closest("[data-contact]"); if (button) selectContact(Number(button.dataset.contact)); });
$("search-form").addEventListener("submit", event => { event.preventDefault(); runSearch(); });
$("topics-view").addEventListener("click", showTopics);
$("search-view").addEventListener("click", showSearchView);
$("clear-filters").addEventListener("click", clearFilters);
$("close-context").addEventListener("click", () => $("context-panel").classList.remove("open"));
$("pick-file").addEventListener("click", () => $("file-input").click());
$("file-input").addEventListener("change", event => { const file = event.target.files[0]; event.target.value = ""; readFile(file); });
$("drop-zone").addEventListener("dragover", event => { event.preventDefault(); $("drop-zone").classList.add("dragover"); });
$("drop-zone").addEventListener("dragleave", () => $("drop-zone").classList.remove("dragover"));
$("drop-zone").addEventListener("drop", event => { event.preventDefault(); $("drop-zone").classList.remove("dragover"); readFile(event.dataTransfer.files[0]); });
$("confirm-import").addEventListener("click", confirmImport);
function setAIStatus(text, status = "idle") {
  $("ai-status").textContent = text; $("ai-status").dataset.status = status;
}
function draftLlamaConfig() {
  return {
    exe_path: $("llama-exe").value.trim(),
    model_path: $("llama-model-file").value.trim(),
    port: Number($("llama-port").value),
    context_size: Number($("llama-context").value),
    gpu_layers: Number($("llama-gpu").value),
    threads: Number($("llama-threads").value)
  };
}
function fillLlamaConfig(config) {
  if (!config) return;
  $("llama-exe").value = config.exe_path || "";
  $("llama-model-file").value = config.model_path || "";
  $("llama-port").value = config.port || 8080;
  $("llama-context").value = config.context_size || 8192;
  $("llama-gpu").value = config.gpu_layers ?? 0;
  $("llama-threads").value = config.threads || 4;
}
function updateLlamaStatus(status) {
  const labels = { unconfigured: "未配置", loading: "正在加载模型", available: "本地模型可用", external_service: "检测到兼容的外部服务", port_conflict: "端口被非兼容服务占用", abnormal_exit: "进程异常退出", start_failed: "进程启动失败", stopped: "已停止" };
  setAIStatus(labels[status.state] || status.state || "状态未知", ["available", "external_service"].includes(status.state) ? "local-connected" : (["port_conflict", "abnormal_exit"].includes(status.state) ? "failed" : "testing"));
  $("stop-llama").disabled = !status.can_stop || !state.llamaOwnerToken;
}
const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
async function waitForLlamaReady(config, attempts = 90) {
  for (let attempt = 0; attempt < attempts; attempt++) {
    if (!state.llamaOwnerToken && attempt > 0) return;
    const status = await api("/api/llama/status");
    updateLlamaStatus(status);
    if (["available", "external_service"].includes(status.state)) {
      const probe = await api("/api/model/test", { config });
      state.config = { ...config, model: probe.model };
      setAIStatus("本地连接成功：" + probe.destination + " · " + probe.model, "local-connected");
      return;
    }
    if (["port_conflict", "abnormal_exit", "start_failed", "stopped"].includes(status.state)) {
      state.config = null;
      throw new Error("llama.cpp 未能进入可用状态：" + status.state);
    }
    await wait(1000);
  }
  state.config = null;
  throw new Error("模型加载等待超时；可稍后点击“探测本地服务”。");
}
async function loadLlamaSettings() {
  try {
    const [saved, status] = await Promise.all([api("/api/llama/config"), api("/api/llama/status")]);
    fillLlamaConfig(saved.config); updateLlamaStatus(status);
    if (saved.config && ["available", "external_service"].includes(status.state)) {
      const config = { endpoint: "http://127.0.0.1:" + saved.config.port + "/v1", model: "local-model", key: "" };
      const probe = await api("/api/model/test", { config });
      state.config = { ...config, model: probe.model };
      setAIStatus("本地连接成功：" + probe.destination + " · " + probe.model, "local-connected");
    } else {
      state.config = null;
    }
  } catch (error) { setAIStatus(error.message, "failed"); }
}
$("settings-trigger").addEventListener("click", () => { openDialog("settings-dialog"); loadLlamaSettings(); });
$("save-ai").addEventListener("click", async () => {
  try {
    const saved = await api("/api/llama/config", draftLlamaConfig());
    fillLlamaConfig(saved.config); state.config = null; setAIStatus("配置已保存，尚未启动。", "idle");
  } catch (error) { setAIStatus(error.message, "failed"); }
});
$("start-llama").addEventListener("click", async () => {
  $("start-llama").disabled = true; setAIStatus("正在保存配置并启动…", "testing");
  try {
    const saved = await api("/api/llama/config", draftLlamaConfig());
    const result = await api("/api/llama/start", {});
    state.llamaOwnerToken = result.owner_token || null;
    const config = { endpoint: "http://127.0.0.1:" + saved.config.port + "/v1", model: "local-model", key: "" };
    state.config = null;
    updateLlamaStatus(result);
    await waitForLlamaReady(config);
  } catch (error) { state.config = null; setAIStatus(error.message, "failed"); }
  finally { $("start-llama").disabled = false; }
});
$("test-ai").addEventListener("click", async () => {
  const port = Number($("llama-port").value);
  const config = { endpoint: "http://127.0.0.1:" + port + "/v1", model: "local-model", key: "" };
  $("test-ai").disabled = true; setAIStatus("正在发送不含聊天记录的固定探针…", "testing");
  try {
    const result = await api("/api/model/test", { config });
    state.config = { ...config, model: result.model };
    setAIStatus("本地连接成功：" + result.destination + " · " + result.model, "local-connected");
  } catch (error) { state.config = null; setAIStatus("连接失败：" + error.message, "failed"); }
  finally { $("test-ai").disabled = false; }
});
$("stop-llama").addEventListener("click", async () => {
  if (!state.llamaOwnerToken) return;
  try {
    const result = await api("/api/llama/stop", { owner_token: state.llamaOwnerToken });
    if (result.stopped) { state.llamaOwnerToken = null; state.config = null; }
    updateLlamaStatus(result);
  } catch (error) { setAIStatus(error.message, "failed"); }
});
$("confirm-send").addEventListener("click", sendAnswer);
$("export-button").addEventListener("click", () => { if (state.contact) window.location.assign("/api/export?id=" + state.contact.id); });
$("import-dialog").addEventListener("cancel", () => { state.previewVersion++; });
setView("topics"); renderEmpty();
loadContacts().then(() => { if (state.contacts.length) selectContact(state.contacts[0].id); }).catch(error => toast("无法连接本地服务：" + error.message));
