(() => {
const PAGE = JSON.parse(document.getElementById("page").textContent), CSRF = JSON.parse(document.getElementById("csrfval").textContent);
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const LABEL = {submitted:"submitted",evaluation_pending:"waiting for evaluation",evaluating:"evaluating…",evaluated_passed:"passed",evaluated_failed:"failed",evaluation_error:"evaluation error",passed:"passed",failed:"failed",error:"error"};
const badge = s => `<span class="b ${esc(s)}">${esc(LABEL[s]||s)}</span>`;
let editor = null; const ta = $("ed"); ta.value = PAGE.starter;
const getCode = () => editor ? editor.getValue() : ta.value;
function notice(m){const n=$("notice");n.textContent=m;n.hidden=false;clearTimeout(notice.t);notice.t=setTimeout(()=>n.hidden=true,3500);}
if (PAGE.paste) {  // optional paste deterrent (low priority; NOT copy-proof)
  const block = e => {e.preventDefault();notice("Paste is disabled in the editor (a deterrent only). Please type your own code.");};
  ta.addEventListener("paste", block); ta.addEventListener("drop", block);
}
let tabFree = false;  // Tab inserts spaces; press Escape then Tab to move focus away (keyboard accessibility)
ta.addEventListener("keydown", e => {
  if (e.key === "Escape") { tabFree = true; return; }
  if (e.key === "Tab" && !tabFree && !e.shiftKey) { e.preventDefault(); ta.setRangeText("    ", ta.selectionStart, ta.selectionEnd, "end"); }
  if (e.key !== "Tab") tabFree = false;
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run(); }
});
(function loadMonaco(){  // the label says Monaco only if it really loaded; otherwise the textarea keeps working
  const base = "https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs";
  const s = document.createElement("script"); s.src = base + "/loader.min.js";
  s.onload = () => {
    try {
      require.config({paths:{vs:base}});
      window.MonacoEnvironment = {getWorkerUrl: () => "data:text/javascript;charset=utf-8," + encodeURIComponent(`self.MonacoEnvironment={baseUrl:"${base}/../"};importScripts("${base}/base/worker/workerMain.js");`)};
      require(["vs/editor/editor.main"], () => {
        editor = monaco.editor.create($("monaco"), {value: ta.value, language: "cpp", theme: "vs-dark", fontSize: 15, automaticLayout: true, minimap: {enabled:false}});
        $("monaco").hidden = false; ta.hidden = true; $("engine").textContent = "Editor: Monaco";
        if (PAGE.paste) {
          const msg = () => notice("Paste is disabled in the editor (a deterrent only).");
          editor.onKeyDown(e => {if (((e.ctrlKey||e.metaKey) && e.keyCode === monaco.KeyCode.KeyV) || (e.shiftKey && e.keyCode === monaco.KeyCode.Insert)) {e.preventDefault();e.stopPropagation();msg();}});
          $("monaco").addEventListener("paste", e => {e.preventDefault();e.stopPropagation();msg();}, true);
        }
        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, run);
      });
    } catch (e) {}
  };
  document.head.appendChild(s);
})();
async function api(url, body) {
  const opt = body === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json","X-CSRF-Token":CSRF}, body:JSON.stringify(body)};
  let r; try { r = await fetch(url, opt); } catch (e) { return {ok:false, status:0, data:{error:"Network error: could not reach CodeLab."}}; }
  let data = {}; try { data = await r.json(); } catch (e) { data = {error:"Unexpected server response (" + r.status + ")."}; }
  return {ok:r.ok, status:r.status, data};
}
const busy = b => { $("run").disabled = $("submit").disabled = b; };
async function run() {
  busy(true); $("runout").innerHTML = '<p><span class="spin"></span> Running on the compiler service…</p>';
  const r = await api(`/api/assignments/${PAGE.assignment_id}/run`, {code:getCode(), stdin:$("stdin").value}), d = r.data;
  if (!r.ok) { $("runout").innerHTML = `<div class="errbox"><b>${r.status===503?"Execution service problem":"Could not run"}:</b> ${esc(d.error)}</div>` + (r.status===503?"<small>Your code was not judged. Nothing was marked wrong.</small>":""); busy(false); return; }
  let h = `<h4>Run result ${badge(d.kind==="ok"?"passed":"failed")} <small>${esc(d.status)}${d.time!=null?" · "+d.time+"s":""}${d.memory!=null?" · "+d.memory+" KB":""}</small></h4>`;
  if (d.compile_output) h += `<b>Compiler diagnostics</b><pre>${esc(d.compile_output)}</pre>`;
  if (d.stderr) h += `<b>Runtime error output</b><pre>${esc(d.stderr)}</pre>`;
  $("runout").innerHTML = h + `<b>Program output</b><pre>${esc(d.stdout)}</pre>`; busy(false);
}
function renderSubmission(s) {
  let h = `<h4>Attempt ${s.attempt}: ${badge(s.status)}</h4>`;
  if (s.status === "evaluation_error") h += `<div class="errbox">${esc(s.error_message)}<br><small>Your code is saved. This is a service problem, not a wrong answer.</small></div>`;
  if (s.compile_output) h += `<b>Compilation failed</b><pre>${esc(s.compile_output)}</pre>`;
  if (s.status.startsWith("evaluated") || s.status === "evaluation_error") h += `<p>${s.passed_count}/${s.total} tests passed${s.status==="evaluation_error"?" before the service failed":""}.</p>`;
  h += (s.results||[]).map(t => t.hidden ? `<div>Hidden test ${t.position}: ${badge(t.status)}</div>` :
    `<div>Sample test ${t.position}: ${badge(t.status)}${t.status==="passed"?"":`<pre>Input:\n${esc(t.input)}\nExpected:\n${esc(t.expected)}\nGot:\n${esc(t.actual)}${t.stderr?"\n"+esc(t.stderr):""}${t.exec_status&&t.exec_status!=="Completed"?"\n["+esc(t.exec_status)+"]":""}</pre>`}</div>`).join("");
  $("subout").innerHTML = h;
}
async function loadHistory() {
  const rows = (await api(`/api/assignments/${PAGE.assignment_id}/submissions`)).data.submissions || [];
  $("history").innerHTML = rows.length ? rows.map(x => `<tr><td>${x.attempt}</td><td>${esc(x.created_at)}</td><td>${badge(x.status)}</td><td>${x.status.startsWith("evaluated")?x.passed_count+"/"+x.total:"-"}</td></tr>`).join("") : '<tr><td colspan="4">No submissions yet.</td></tr>';
}
async function submit() {
  busy(true); const token = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now())+Math.random().toString(36).slice(2)).replace(/[^A-Za-z0-9_-]/g,"");
  $("subout").innerHTML = '<p><span class="spin"></span> Saving your submission…</p>';
  const r = await api(`/api/assignments/${PAGE.assignment_id}/submit`, {code:getCode(), client_token:token});
  if (!r.ok) { $("subout").innerHTML = `<div class="errbox">${esc(r.data.error)}</div>`; busy(false); return; }
  const id = r.data.submission_id, t0 = Date.now(); loadHistory();
  for (;;) {
    const p = await api(`/api/submissions/${id}`);
    if (!p.ok) { $("subout").innerHTML = `<div class="errbox">${esc(p.data.error)}</div>`; break; }
    renderSubmission(p.data);
    if (["evaluated_passed","evaluated_failed","evaluation_error"].includes(p.data.status)) break;
    if (Date.now() - t0 > 240000) { $("subout").innerHTML += "<p>Still evaluating. Your submission is saved; check 'My attempts' later.</p>"; break; }
    await new Promise(r => setTimeout(r, 1200));
  }
  loadHistory(); busy(false);
}
$("run").addEventListener("click", run); $("submit").addEventListener("click", submit); loadHistory();
})();
