'use strict';
const $ = id => document.getElementById(id);
const clean = value => String(value ?? '').replace(/[\p{Cc}\p{Cf}\u2028\u2029]/gu, char => ['\n','\t'].includes(char) ? char : '\\u' + char.codePointAt(0).toString(16).padStart(4,'0'));
const text = (id, value) => { $(id).textContent = clean(value); };
const pretty = value => JSON.stringify(value, null, 2);
const activeStates = new Set(['running','cancelling','verifying']);
const labels = {running:'正在读取并核对证据',cancelling:'已请求取消，正在等待进程退出',verifying:'正在核验已保存报告',ready:'读取完成',partial:'部分证据',verification_failed:'报告暂时无法核验',cancelled:'已取消',rejected:'无法开始读取',failed:'本次读取未完成',closure_unknown:'进程关闭尚未确认'};
const errors = {input_invalid:'输入不满足读取合同，请检查对应字段。',contract_invalid:'输入或报告不满足合同。',forbidden_path:'所选路径超出读取范围，请选择允许的具体项目目录。',unsupported_environment:'当前环境无法提供所需监控或安全读取能力。',scope_limit:'达到读取或资源预算，已停止本次操作。',resource_pressure:'内存压力不满足读取条件。',workbench_busy:'已有读取或核验正在进行，请等待它完成。',intent_conflict:'请求标识已用于另一项输入，请新建读取。',closure_unknown:'进程关闭证据不足；不能开始新的分析。',history_corrupt:'保存文件或证据关联无法核验。文件已保留。',history_budget_exceeded:'本次核验超出工作台读取预算，不能据此判断报告损坏。',history_version_unsupported:'暂不支持此报告版本，原文件已保留。',workbench_storage_limit:'工作台保存额度不足，请检查并手动处理拥有的旧输出。',session_required:'本地会话已失效，请从启动时的本地地址重新打开。',report_unavailable:'本次没有已提交的证据报告。',workbench_stopping:'工作台正在退出。',workbench_unavailable:'本地工作台当前无法完成操作。',output_limit_unrepresentable:'报告超过输出限制，无法显示。'};
let token = '', current = null, saved = null, loadedReport = null, timer = null, intent = null, nextCursor = null, submitting = false, connected = true, viewGeneration = 0, transportGeneration = 0;
try {
  const fragment = location.hash.slice(1);
  if (/^[A-Za-z0-9_-]{43}$/.test(fragment)) { sessionStorage.setItem('evidence-access', fragment); history.replaceState(null,'',location.pathname); }
  token = sessionStorage.getItem('evidence-access') || '';
  intent = sessionStorage.getItem('evidence-intent');
} catch { text('connection','浏览器会话存储不可用，请重新打开初始本地地址。'); $('connection').hidden=false; }
function connection(message='') { text('connection',message); $('connection').hidden=!message; }
function explain(error) { return errors[error.code] || ('本次操作未完成。代码：' + clean(error.code)); }
async function api(path, method='GET', body) {
  const options = {method, headers:{Authorization:'Bearer '+token}, cache:'no-store'};
  if (method==='POST') { options.headers['Content-Type']='application/json'; options.body=JSON.stringify(body); }
  const ticket=++transportGeneration;
  let response,value;
  try { response=await fetch(path,options); value=await response.json(); }
  catch { if(ticket===transportGeneration) connected=false; throw {code:'connection_lost'}; }
  if(ticket===transportGeneration) connected=true;
  if (!response.ok) { if(value.error?.code==='session_required'){token='';sessionStorage.removeItem('evidence-access');} throw value.error || {code:'transport_error'}; }
  return value;
}
function formError(message, field=null) {
  for(const id of ['repo','question','entries','traceback']) { $(id).removeAttribute('aria-invalid'); $(id+'-error').hidden=true; }
  $('form-error').hidden=true;
  if(field && $(field+'-error')) { $(field).setAttribute('aria-invalid','true'); text(field+'-error',message); $(field+'-error').hidden=false; $(field).focus(); }
  else { text('form-error',message); $('form-error').hidden=!message; }
}
function listValue(id) { return $(id).value.split(/\r\n|\r|\n/).map(line=>line.trim()).filter(Boolean); }
function updateControls() {
  const busy=submitting || (current && activeStates.has(current.state));
  $('start').disabled=busy || !token || !connected || (current && current.state==='closure_unknown');
  $('cancel').hidden=!current || !activeStates.has(current.state);
  $('cancel').disabled=!current || current.cancel_requested || current.state==='verifying';
}
function clearReport(message='尚未加载本次报告。') {
  saved=null; $('exports').hidden=true; $('source-panel').hidden=true; $('required-gaps').hidden=true; $('export-error').hidden=true; $('report-context').hidden=true;
  for(const id of ['evidence-list','source-code','coverage-counts','frames']) $(id).replaceChildren();
  for(const id of ['source-location','source-symbol','range','reference','required-gaps','report-context','coverage-data','gaps-data','run-data']) text(id,'');
  text('evidence-empty',message); $('evidence-empty').hidden=!message;
}
function showState(run) {
  if(current?.id!==run.id) clearReport();
  current=run; $('result').hidden=false;
  const status=run.state==='ready' && run.analysis_status==='partial' ? 'partial' : run.state;
  const message=labels[status] || clean(status);
  if($('status').textContent!==message) text('status',message);
  text('task-summary',run.question+' · '+run.project);
  let details=run.state==='ready' ? '仅完成本次静态读取；不代表全仓已验证或根因已确定。' : run.error ? explain({code:run.error}) : '不执行目标项目，不启动模型。';
  if(run.error) details+=' 代码：'+run.error+'。';
  const failure=run.analysis_failure;
  if(failure) {
    details+=' 原分析代码：'+failure.code+'；阶段：'+failure.stage+'；字段：'+failure.field+'。';
    if(failure.effective!==null && failure.observed!==null) details+=' 上限：'+failure.effective+'；实测：'+failure.observed+'。';
    if(failure.field==='max_metadata_entries' || failure.field==='max_metadata_bytes') details+=' 这是累计元数据访问预算，包含监控重复遍历，不能当作目录项总数。保留本次失败，检查容量配置后再决定下一次读取。';
  } else if(run.error) details+=' 阶段详情未记录，本次记录已保留。';
  text('outcome-detail',details);
  if(run.worker_started && run.worker_exit_confirmed!==true && !activeStates.has(run.state)) text('outcome-detail','进程退出尚未得到确认；新的读取已禁用。 '+details);
  updateControls();
}
function elapsed() {
  if(current && activeStates.has(current.state)) text('elapsed','已等待 '+Math.max(0,Math.floor(Date.now()/1000-current.started_at))+' 秒');
  else text('elapsed','');
}
async function poll(id, generation=++viewGeneration) {
  if(generation!==viewGeneration) return;
  clearTimeout(timer);
  try {
    const run=await api('/api/runs/'+id); if(generation!==viewGeneration) return; showState(run); connection();
    if(activeStates.has(run.state)) timer=setTimeout(()=>poll(id,generation),document.hidden?2000:500);
    else {
      if(run.publication==='committed') await loadReport(id,generation);
      else { clearReport(run.state==='cancelled'?'本次已取消，没有已提交的源码报告。':explain({code:run.error || 'report_unavailable'})); text('run-data',pretty(run)); }
      if(generation===viewGeneration) await loadHistory();
    }
  } catch(error) {
    if(generation!==viewGeneration) return;
    if(error.code==='connection_lost') { connection('与本地工作台连接中断，当前显示是最后已知状态。重新连接只查询这次读取，不重新启动。'); updateControls(); timer=setTimeout(()=>poll(id,generation),2000); }
    else { connection(explain(error)); updateControls(); }
  }
}
async function resolveIntent(value) {
  try { const run=await api('/api/runs?intent_id='+encodeURIComponent(value)); $('input-panel').open=false; await poll(run.id); }
  catch(error) { if(error.code==='run_not_found') { sessionStorage.removeItem('evidence-intent'); intent=null; connection('未找到已创建的读取。检查输入后，可手动开始新的读取。'); } else { connection(error.code==='connection_lost'?'连接中断，尚不能确认本次请求是否已创建。':explain(error)); } }
  submitting=false; updateControls();
}
$('reading-form').addEventListener('submit',async event=>{
  event.preventDefault(); if(submitting || $('start').disabled) return;
  formError(''); const entries=listValue('entries'),symbols=listValue('symbols'),question=$('question').value,traceback=$('traceback').value,repo=$('repo').value.trim(),bytes=value=>new TextEncoder().encode(value).length;
  if(!repo.startsWith('/')) return formError('填写项目的绝对目录。','repo');
  if(!question.trim() || bytes(question)>8192) return formError('填写非空问题，UTF8 大小不超过 8 KiB。','question');
  if(bytes(traceback)>65536) return formError('已有报错的 UTF8 大小不能超过 64 KiB。','traceback');
  if(entries.length+symbols.length>6) return formError('入口与符号合计最多 6 项。','entries');
  if(!entries.length&&!symbols.length&&!traceback.trim()) return formError('添加源码入口、限定符号或已有报错中的至少一项。','entries');
  intent=crypto.randomUUID(); sessionStorage.setItem('evidence-intent',intent); submitting=true; viewGeneration++; clearTimeout(timer); current=null; clearReport('正在提交这次读取。'); updateControls();
  const payload={intent_id:intent,repo,question,entries,symbols,traceback,profile_name:$('profile').value};
  try { const run=await api('/api/runs','POST',payload); saved=null; $('exports').hidden=true; $('required-gaps').hidden=true; $('input-panel').open=false; submitting=false; await poll(run.id); }
  catch(error) { submitting=false; if(error.code==='connection_lost') await resolveIntent(intent); else { sessionStorage.removeItem('evidence-intent'); intent=null; formError(explain(error),['repo','question','entries','traceback'].includes(error.field)?error.field:null); } updateControls(); }
});
$('cancel').addEventListener('click',async()=>{ if(!current) return; const id=current.id,generation=viewGeneration; $('cancel').disabled=true; try { const run=await api('/api/runs/'+id+'/cancel','POST',{}); if(generation===viewGeneration && current?.id===id) showState(run); } catch(error) { if(generation===viewGeneration && current?.id===id) { connection(explain(error)); updateControls(); } } });
$('new-reading').addEventListener('click',()=>{ $('input-panel').open=true; $('question').focus(); });
$('history-toggle').addEventListener('click',()=>{ const hidden=!$('history-panel').hidden; $('history-panel').hidden=hidden; $('history-toggle').setAttribute('aria-expanded',String(!hidden)); });
$('shutdown').addEventListener('click',async()=>{ $('shutdown').disabled=true; try { await api('/api/shutdown','POST',{}); connection('已请求退出工作台，正在等待分析和连接关闭。'); $('start').disabled=true; } catch(error) { connection(explain(error)); $('shutdown').disabled=false; } });
function activateTab(name) {
  for(const item of ['evidence','coverage','details']) { const on=item===name; $('tab-'+item).setAttribute('aria-selected',String(on)); $('tab-'+item).tabIndex=on?0:-1; $('panel-'+item).hidden=!on; }
}
for(const name of ['evidence','coverage','details']) {
  $('tab-'+name).addEventListener('click',()=>activateTab(name));
  $('tab-'+name).addEventListener('keydown',event=>{ const names=['evidence','coverage','details']; if(['ArrowRight','ArrowLeft','Home','End'].includes(event.key)) { event.preventDefault(); const index=event.key==='Home'?0:event.key==='End'?2:(names.indexOf(name)+(event.key==='ArrowRight'?1:2))%3; activateTab(names[index]); $('tab-'+names[index]).focus(); } });
}
async function loadHistory(cursor=0) {
  const page=await api('/api/history?cursor='+cursor); if(cursor===0) $('history').replaceChildren();
  $('history-empty').hidden=page.items.length>0 || cursor>0;
  for(const run of page.items) { const button=document.createElement('button'); button.textContent=clean((run.analysis_status==='partial'?'部分证据':labels[run.state]||run.state)+' · '+run.question); button.addEventListener('click',async()=>{ try { if(current&&activeStates.has(current.state)&&run.id!==current.id) { if(loadedReport && loadedReport.id===run.id) { const cached=loadedReport; clearReport(); saved=cached; renderReport(cached.value,cached.id); $('exports').hidden=true; return; } connection('正在分析；新的历史核验需等本次读取退出。'); return; } await poll(run.id); } catch(error) { connection(explain(error)); } }); $('history').append(button); }
  nextCursor=page.next_cursor; $('more-history').hidden=nextCursor===null;
}
$('more-history').addEventListener('click',()=>loadHistory(nextCursor).catch(error=>connection(explain(error))));
async function loadReport(id, generation=viewGeneration) {
  try {
    const value=await api('/api/runs/'+id+'/report');
    if(generation!==viewGeneration || current?.id!==id) return;
    if(value.state && value.state.id!==id) throw {code:'history_corrupt'};
    if(value.state) showState(value.state);
    saved={id,value}; loadedReport=saved; renderReport(value,id); $('exports').hidden=false; text('export-error',''); $('export-error').hidden=true;
  } catch(error) {
    if(generation!==viewGeneration || current?.id!==id) return;
    clearReport(explain(error));
    text('status',error.code?.startsWith('history_')?labels.verification_failed:'报告未加载');
    text('outcome-detail',explain(error));
    if(error.code==='connection_lost') connection('连接中断，尚未加载本次报告。');
  }
}
function renderReport(value, identifier) {
  const report=value.report, isCurrent=current?.id===identifier;
  text('report-context',(isCurrent?'本次报告':'已加载历史报告')+' · '+(report.request?.question||identifier)+' · '+(report.request?.repo||'')+' · '+(report.status==='partial'?'部分证据':'读取完成')+(isCurrent?'':'。当前分析仍在进行；导出需等它退出。')); $('report-context').hidden=false; $('evidence-list').replaceChildren(); $('source-panel').hidden=true; $('evidence-empty').hidden=report.evidence.length>0;
  if(!report.evidence.length) text('evidence-empty','本次没有可展示的源码；请查看覆盖与缺口。合法的部分报告仍可导出。');
  const required=report.gaps.filter(gap=>gap.required===true); $('required-gaps').hidden=!required.length; text('required-gaps',required.length+' 项主证据缺口：'+required.map(gap=>gap.code).join('、'));
  if(isCurrent && report.status==='partial') text('status','部分证据');
  let displayed=0;
  function appendEvidence() {
    const end=Math.min(displayed+50,report.evidence.length);
    for(let index=displayed;index<end;index++) { const item=report.evidence[index],button=document.createElement('button'); button.textContent=clean(item.file+':'+item.actual_span.start+'–'+item.actual_span.end+' · '+(item.complete_symbol?'完整函数/声明':item.complete_range?'完整选中范围':'部分源码')); button.addEventListener('click',()=>selectEvidence(report,item,button)); $('evidence-list').append(button); }
    displayed=end;
    if(displayed<report.evidence.length) { const more=document.createElement('button'); more.textContent='更多证据'; more.addEventListener('click',()=>{more.remove();appendEvidence();}); $('evidence-list').append(more); }
  }
  appendEvidence(); if(report.evidence.length) selectEvidence(report,report.evidence[0],$('evidence-list').firstElementChild);
  $('coverage-counts').replaceChildren();
  const counts={discovered_count:'发现条目',eligible_text_count:'捕获文本文件',indexed_text_count:'已索引文本',python_file_count:'Python 文件',parsed_python_count:'已解析 Python',selected_entry_count:'唯一选中证据单位',full_entry_count:'完整选中单位',partial_entry_count:'部分选中单位',excluded_count:'排除',unsupported_count:'不支持',unreadable_count:'不可读'};
  for(const [field,label] of Object.entries(counts)) { const row=document.createElement('div'),term=document.createElement('dt'),number=document.createElement('dd'); term.textContent=label; number.textContent=String(report.coverage[field]??'未提供'); row.append(term,number); $('coverage-counts').append(row); }
  $('frames').replaceChildren(); const frames=report.selection.reported_frames||[];
  for(const frame of frames) { const row=document.createElement('p'); row.className='frame-row'; row.textContent=clean(frame.file+':'+frame.line+' '+frame.name+' · '+frame.status); $('frames').append(row); }
  const omitted=report.selection.reported_frames_omitted_count||0; if(omitted) { const row=document.createElement('p'); row.textContent='另有 '+omitted+' 帧未列出'; $('frames').append(row); }
  if(!frames.length) { const row=document.createElement('p'); row.textContent='未提供可解析的报错帧。'; $('frames').append(row); }
  text('coverage-data',pretty({未展示源码:report.coverage.unread,无法解析或仅文本:report.coverage.unparsed,排除:report.coverage.excluded,不支持:report.coverage.unsupported,不可读:report.coverage.unreadable,选择:report.selection}));
  text('gaps-data',pretty({gaps:report.gaps,errors:report.errors,limits:report.limits}));
  text('run-data',pretty({保存报告状态:report.status,保存报告用量:report.usage,最终生命周期与累计用量:value.final||'未保存或不可用',历史字节核验用量:value.read_usage,回执关闭记录:value.receipt.worker,静态能力边界:report.capabilities,本次核验状态:value.state,核验状态保存:value.state_persistence||'未提供',语法观察:'当时 core 已提交的观察，历史阅读不独立重证'}));
  if(isCurrent && value.final?.run.committed_before_cancel) text('outcome-detail','取消到达时报告已保存；有效提交已保留。');
  if(isCurrent && value.state_persistence==='unavailable') text('outcome-detail','报告字节已核验；这次核验状态暂未保存，已保留原始报告。');
}
function selectEvidence(report,item,button) {
  for(const row of $('evidence-list').children) row.removeAttribute('aria-current'); button.setAttribute('aria-current','true');
  $('source-panel').hidden=false; text('source-location',item.file+':'+item.actual_span.start+'–'+item.actual_span.end); const symbol=(report.observations.symbols||[]).find(value=>value.symbol_id===item.symbol_id); text('source-symbol',symbol?.qualified_name||item.symbol_id||'选中源码范围');
  text('range','请求 '+item.requested_span.start+'–'+item.requested_span.end+' / 捕获 '+item.actual_span.start+'–'+item.actual_span.end+' · '+(item.complete_symbol?'声明完整':item.complete_range?'选中范围完整':'范围被截断'));
  $('range').classList.toggle('partial',!item.complete_range || Boolean(item.symbol_id && !item.complete_symbol)); $('source-code').replaceChildren();
  const lines=item.text.match(/[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$/g)||[];
  lines.forEach((line,index)=>{const row=document.createElement('div'),number=document.createElement('span'),code=document.createElement('span');row.className='code-line';number.className='line-number';code.className='line-text';number.textContent=String(item.actual_span.start+index);code.textContent=clean(line.replace(/(?:\r\n|\r|\n)$/,''));row.append(number,code);$('source-code').append(row);});
  text('reference','证据 '+item.evidence_id+' · 编码 '+(item.encoding||'未提供')+' · complete_symbol='+item.complete_symbol);
}
$('copy-location').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('source-location').textContent);$('copy-location').textContent='已复制位置';setTimeout(()=>{$('copy-location').textContent='复制位置';},1500);}catch{text('export-error','无法访问剪贴板，可选中位置文字手动复制。');$('export-error').hidden=false;}});
async function download(format) {
  if(!saved) return; const id=saved.id; text('export-error',''); $('export-error').hidden=true;
  try { const response=await fetch('/api/runs/'+id+'/export?format='+format,{headers:{Authorization:'Bearer '+token},cache:'no-store'}); if(!response.ok) {const raw=await response.json();throw raw.error;} const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='evidence-'+id+(format==='json'?'.json':'.md');link.click();setTimeout(()=>URL.revokeObjectURL(url),1000); }
  catch(error){if(saved?.id===id){text('export-error','未能导出报告。'+explain(error));$('export-error').hidden=false;}}
}
$('export-json').addEventListener('click',()=>download('json'));$('export-markdown').addEventListener('click',()=>download('markdown'));
setInterval(elapsed,1000); updateControls();
if(!token) connection('缺少本地会话凭据，请从启动时打印的完整本地地址打开。');
else loadHistory().then(()=>intent?resolveIntent(intent):null).catch(error=>connection(error.code==='connection_lost'?'与本地工作台连接中断。':explain(error)));
