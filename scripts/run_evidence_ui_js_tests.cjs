// Offline own-product logic fixtures. No browser, socket, worker or target code.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {randomUUID} = require('node:crypto');
const source = fs.readFileSync(path.join(__dirname,'../src/macr/api/evidence_ui/workbench.js'),'utf8');
function fixture(session=false,seed={}) {
  const elements = new Map(), storage = new Map(Object.entries(seed)), calls = [], downloads = [];
  function element() {
    const attributes = new Map(), events = new Map();
    const node={hidden:false, disabled:false, textContent:'', value:'', children:[], events,
      classList:{toggle(){}}, setAttribute(k,v){attributes.set(k,v);}, removeAttribute(k){attributes.delete(k);},
      addEventListener(k,v){events.set(k,v);}, replaceChildren(...items){this.children=items;},
      append(...items){this.children.push(...items);}, focus(){}, remove(){}, click(){downloads.push(this.download);},
      get firstElementChild(){return this.children[0];}};
    Object.defineProperty(node,'innerHTML',{set(){throw new Error('HTML insertion is forbidden for fixture data');}});
    return node;
  }
  const document = {hidden:false,getElementById(id){if(!elements.has(id)) elements.set(id,element());return elements.get(id);},createElement:element};
  const env={fetch:async()=>{throw new Error('fixture_fetch_unconfigured');}};
  const factory=new Function('document','location','sessionStorage','history','fetch','setTimeout','clearTimeout','setInterval','crypto','URL',
    source+'\nreturn {api,poll,loadReport,showState,download,loadHistory,loadRepairHistory,loadRepairCapabilities,showRepairState:typeof showRepairState==="function"?showRepairState:null,pollRepair:typeof pollRepair==="function"?pollRepair:null,get:()=>({current,saved,intent,repairCurrent:typeof repairCurrent!=="undefined"?repairCurrent:null})};');
  const api=factory(document,{hash:session?'#'+'A'.repeat(43):'',pathname:'/'},{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    {replaceState(){}},(...args)=>{calls.push(args);return env.fetch(...args);},()=>1,()=>{},()=>{}, {randomUUID},{createObjectURL:()=>'blob:fixture',revokeObjectURL(){}});
  return {api,env,calls,downloads,el:id=>document.getElementById(id),storage};
}
const run=id=>({id,state:'ready',analysis_status:'completed',publication:'committed',verification_status:'passed',question:id,project:'/fixture',worker_started:true,worker_exit_confirmed:true});
const report=id=>({state:run(id),report:{status:'completed',evidence:[],gaps:[],errors:[],limits:[],selection:{reported_frames:[]},coverage:{unread:{},unparsed:{},excluded:{},unsupported:{},unreadable:{}},observations:{symbols:[]},usage:{},capabilities:{marker:id}},receipt:{worker:{exit_confirmed:true}},read_usage:{},final:null});
const response=value=>({ok:true,json:async()=>value});
const logs=[];const log=value=>{logs.push(String(value));console.log(value);};
(async()=>{
  const tests = [
    ['runtime display reports fixed budget and backend registration without asserting repair',async()=>{
      const f=fixture();f.env.fetch=async()=>response({schema:'macr.workbench.repair-capabilities.v1',
        model:{remote_enabled:true,cell_id:'approved-one',max_requests:2,max_usd:'1.000000'},
        verification:{execution_enabled:true,backend_registered:true}});
      await f.api.loadRepairCapabilities();assert.match(f.el('repair-runtime').textContent,/approved-one/);
      assert.match(f.el('repair-runtime').textContent,/不是账单硬限/);assert.match(f.el('repair-runtime').textContent,/独立的环境资格/);
      f.api.showRepairState({id:'R',state:'completed',repair_status:'public_tests_reported_passed',summary:{simulation:false,verification_origin:'backend_reported_unattested'}});
      assert.match(f.el('repair-detail').textContent,/不证明隐藏测试通过/);assert.doesNotMatch(f.el('repair-status').textContent,/修复成功/);
    }],
    ['unavailable runtime metadata does not invent enabled model access',async()=>{
      const f=fixture();await f.api.loadRepairCapabilities();
      assert.match(f.el('repair-runtime').textContent,/无法核对/);assert.equal(f.calls.filter(([,o])=>o?.method==='POST').length,0);
    }],
    ['acknowledged shutdown gates both flows even after a late state reply',async()=>{
      const f=fixture(true);f.env.fetch=async()=>response({status:'stopping'});
      await f.el('shutdown').events.get('click')();
      assert.equal(f.el('start').disabled,true);assert.equal(f.el('repair-start').disabled,true);
      f.api.showRepairState({id:'R',state:'blocked',repair_status:'verification_unavailable',summary:{provider_requests:0,repaired:false}});
      assert.equal(f.el('repair-start').disabled,true);
    }],
    ['unknown repair history gates new tasks without a saved intent',async()=>{
      const f=fixture(true);assert.equal(Boolean(f.el('repair-start').disabled),false);
      f.env.fetch=async()=>response({items:[{id:'R',state:'unknown',repair_status:'provider_outcome_unknown',project:'/fixture'}],next_cursor:null});
      await f.api.loadRepairHistory();
      assert.equal(f.el('repair-start').disabled,true);assert.equal(f.el('start').disabled,true);
    }],
    ['restored unresolved repair intent blocks a new UUID after lookup failure',async()=>{
      const f=fixture(true,{'repair-intent':'retained-intent'});
      assert.equal(f.el('repair-start').disabled,true);
      f.env.fetch=async()=>({ok:false,json:async()=>({error:{code:'run_not_found'}})});
      await f.el('repair-reconnect').events.get('click')();
      f.el('repair-repo').value='/authored/source';f.el('repair-task').value='/authored/task.json';f.el('repair-config').value='/authored/model.json';
      await f.el('repair-form').events.get('submit')({preventDefault(){}});
      assert.equal(f.storage.get('repair-intent'),'retained-intent');assert.equal(f.calls.filter(([,options])=>options?.method==='POST').length,0);
    }],
    ['new repair submission invalidates an older in-flight poll',async()=>{
      const f=fixture(true);let releaseOld,releasePost;
      const old={id:'OLD',state:'completed',repair_status:'candidate_unverified',result_available:false,summary:{repaired:false},project:'/fixture'};
      f.api.showRepairState(old);
      f.env.fetch=async(url,options)=>options?.method==='POST'?new Promise(resolve=>{releasePost=()=>resolve(response({...old,id:'NEW',state:'blocked',repair_status:'provider_disabled'}));}):url==='/api/repairs/OLD'?new Promise(resolve=>{releaseOld=()=>resolve(response(old));}):response({items:[],next_cursor:null});
      const pending=f.api.pollRepair('OLD');
      f.el('repair-repo').value='/authored/source';f.el('repair-task').value='/authored/task.json';f.el('repair-config').value='/authored/model.json';f.el('repair-strategy').value='direct_api';
      const submitting=f.el('repair-form').events.get('submit')({preventDefault(){}});releaseOld();await pending;
      assert.match(f.el('repair-status').textContent,/提交/);releasePost();await submitting;
    }],
    ['selected earlier candidate is distinguished from abandoned edit history',async()=>{
      const f=fixture(),state={id:'R',state:'completed',repair_status:'candidate_selected_unverified',result_available:true,summary:{repaired:false,final_verification_status:'not_rerun'},project:'/fixture'};
      const p=(round,id)=>({round,candidate_source_id:id,status:'candidate',summary:'authored',edits:[{file:'a.py',old:'x=1',new:'x='+round}]});
      const result={state,summary:state.summary,report:{status:'candidate_selected_unverified',final_source_id:'A',final_selection:{accepted_round:1,selected_source_id:'A'}},proposals:[p(1,'A'),p(2,'B'),{round:3,status:'select_candidate',edits:[]}]};
      f.env.fetch=async url=>response(url.includes('history')?{items:[],next_cursor:null}:url.endsWith('/result')?result:state);await f.api.pollRepair('R');
      assert.match(f.el('repair-final').textContent,/第 1 轮/);assert.match(f.el('repair-detail').textContent,/未重新运行验证/);
      assert.match(f.el('repair-edits').children[0].children[0].textContent,/当前保留/);assert.match(f.el('repair-edits').children[1].children[0].textContent,/历史/);
    }],
    ['failed repair record verification removes stale candidate edits',async()=>{
      const f=fixture(),state={id:'R',state:'completed',repair_status:'candidate_unverified',result_available:true,summary:{repaired:false},project:'/fixture'};
      const result={state,summary:state.summary,report:{},proposals:[{round:1,status:'candidate',summary:'authored',edits:[{file:'a.py',old:'x=1',new:'x=2'}]}]};
      f.env.fetch=async url=>response(url.includes('history')?{items:[],next_cursor:null}:url.endsWith('/result')?result:state);await f.api.pollRepair('R');
      assert.equal(f.el('repair-edits').children.length,1);
      f.env.fetch=async url=>url.endsWith('/result')?{ok:false,json:async()=>({error:{code:'history_corrupt'}})}:response(state);
      await f.api.pollRepair('R');assert.equal(f.el('repair-edits').children.length,0);assert.match(f.el('repair-status').textContent,/无法核验/);
    }],
    ['repair session-storage failure prevents creation and explains recovery',async()=>{
      const f=fixture(true);f.el('repair-repo').value='/authored/source';f.el('repair-task').value='/authored/task.json';f.el('repair-config').value='/authored/model.json';f.el('repair-strategy').value='api_debugger';
      f.storage.set=()=>{throw new Error('authored storage unavailable');};
      await f.el('repair-form').events.get('submit')({preventDefault(){}});
      assert.equal(f.calls.filter(([url,options])=>url==='/api/repairs'&&options?.method==='POST').length,0);assert.match(f.el('repair-error').textContent,/尚未创建/);
    }],
    ['withdrawn candidates remain labelled history and render model text safely',async()=>{
      const f=fixture(),state={id:'R',state:'completed',repair_status:'no_candidate',result_available:true,summary:{repaired:false},project:'/fixture'};
      const result={state,summary:state.summary,report:{},proposals:[{round:1,status:'candidate',summary:'<img src=x onerror=bad()>',edits:[{file:'a.py',old:'<script>bad()</script>',new:'fixed'}]},{round:2,status:'no_candidate',summary:'withdraw',edits:[]}]};
      f.env.fetch=async url=>response(url.includes('history')?{items:[],next_cursor:null}:url.endsWith('/result')?result:state);await f.api.pollRepair('R');
      assert.match(f.el('repair-detail').textContent,/未交付候选/);assert.equal(f.el('repair-edits').children[0].children[1].textContent,'<img src=x onerror=bad()>');
    }],
    ['repair controls exist in the actual packaged page',async()=>{
      const html=fs.readFileSync(path.join(__dirname,'../src/macr/api/evidence_ui/index.html'),'utf8');
      for(const id of ['new-repair','repair-panel','repair-form','repair-repo','repair-task','repair-config','repair-strategy','repair-start','repair-result','repair-status','repair-detail','repair-edits','repair-history']) assert.ok(html.includes('id="'+id+'"'),id);
    }],
    ['repair submits one bounded request without runtime credentials or output paths',async()=>{
      const f=fixture(true);f.el('repair-repo').value='/authored/source';f.el('repair-task').value='/authored/task.json';f.el('repair-config').value='/authored/model.json';f.el('repair-strategy').value='api_debugger';
      const blocked={id:'R',state:'blocked',repair_status:'verification_unavailable',result_available:false,summary:{provider_requests:0,repaired:false},project:'/authored/source'};
      f.env.fetch=async url=>response(url.includes('history')?{items:[],next_cursor:null}:blocked);
      await f.el('repair-form').events.get('submit')({preventDefault(){}});
      const posts=f.calls.filter(([url,options])=>url==='/api/repairs'&&options?.method==='POST');assert.equal(posts.length,1);
      const payload=JSON.parse(posts[0][1].body);assert.deepEqual(Object.keys(payload).sort(),['intent_id','model_config','proposal_contract','repo','strategy','task']);assert.equal(payload.proposal_contract,'v6');
      assert.equal(f.api.get().repairCurrent.id,'R');assert.doesNotMatch(f.el('repair-status').textContent,/修复成功/);
    }],
    ['missing verifier and simulation are not shown as successful repairs',async()=>{
      const f=fixture();f.api.showRepairState({id:'R',state:'blocked',repair_status:'verification_unavailable',summary:{provider_requests:0,repaired:false},project:'/fixture'});
      assert.match(f.el('repair-detail').textContent,/验证/);assert.doesNotMatch(f.el('repair-status').textContent,/成功|通过/);
      f.api.showRepairState({id:'R',state:'completed',repair_status:'simulation_passed',summary:{simulation:true,provider_requests:1,repaired:false},project:'/fixture'});
      assert.match(f.el('repair-detail').textContent,/模拟/);assert.doesNotMatch(f.el('repair-status').textContent,/修复成功|修复通过/);
    }],
    ['superseded repair result cannot replace a newer repair',async()=>{
      const f=fixture();let release;
      const state=id=>({id,state:'completed',repair_status:'candidate_unverified',result_available:true,summary:{provider_requests:1,repaired:false},project:'/fixture'});
      f.env.fetch=async url=>url==='/api/repairs/A'?new Promise(resolve=>{release=()=>resolve(response(state('A')));}):response(url.includes('history')?{items:[],next_cursor:null}:url.endsWith('/result')?{state:state('B'),summary:state('B').summary,report:{},proposals:[]}:state('B'));
      const pending=f.api.pollRepair('A');await f.api.pollRepair('B');release();await pending;
      assert.equal(f.api.get().repairCurrent.id,'B');
    }],
    ['repair creation response loss is resolved by intent without posting again',async()=>{
      const f=fixture(true);f.el('repair-repo').value='/authored/source';f.el('repair-task').value='/authored/task.json';f.el('repair-config').value='/authored/model.json';f.el('repair-strategy').value='api_debugger';
      const state={id:'R',state:'blocked',repair_status:'verification_unavailable',result_available:false,summary:{provider_requests:0,repaired:false},project:'/fixture'};
      f.env.fetch=async (url,options)=>{if(options?.method==='POST')return {ok:true,json:async()=>{throw new SyntaxError('authored lost body');}};return response(url.includes('history')?{items:[],next_cursor:null}:state);};
      await f.el('repair-form').events.get('submit')({preventDefault(){}});
      assert.equal(f.calls.filter(([url,options])=>url==='/api/repairs'&&options?.method==='POST').length,1);
      assert.ok(f.calls.some(([url])=>url.startsWith('/api/repairs?intent_id=')));
      assert.equal(f.api.get().repairCurrent.id,'R');
    }],
    ['response-body loss retains uncertain-creation classification',async()=>{
      const f=fixture();f.env.fetch=async()=>({ok:true,json:async()=>{throw new SyntaxError('truncated body');}});
      await assert.rejects(f.api.api('/api/runs','POST',{}),error=>error.code==='connection_lost');
    }],
    ['changing run and failed report clears all old panels',async()=>{
      const f=fixture();f.api.showState(run('A'));
      for(const id of ['coverage-data','gaps-data','run-data']) f.el(id).textContent='old-A';
      f.el('frames').append({textContent:'old-A'});f.el('coverage-counts').append({textContent:'old-A'});
      f.api.showState(run('B'));f.env.fetch=async()=>({ok:false,json:async()=>({error:{code:'history_corrupt'}})});
      await f.api.loadReport('B');
      for(const id of ['coverage-data','gaps-data','run-data']) assert.notEqual(f.el(id).textContent,'old-A');
      assert.equal(f.el('frames').children.length,0);assert.equal(f.el('coverage-counts').children.length,0);
      assert.notEqual(f.el('status').textContent,'读取完成');assert.equal(f.api.get().saved,null);
    }],
    ['superseded report cannot replace current evidence or export identity',async()=>{
      const f=fixture();let release;f.env.fetch=async url=>url.includes('/A/')?new Promise(resolve=>{release=()=>resolve(response(report('A')));}):response(report('B'));
      f.api.showState(run('A'));const pending=f.api.loadReport('A');
      f.api.showState(run('B'));await f.api.loadReport('B');release();await pending;
      assert.equal(f.api.get().current.id,'B');assert.equal(f.api.get().saved.id,'B');
      assert.match(f.el('run-data').textContent,/B/);assert.doesNotMatch(f.el('run-data').textContent,/"marker": "A"/);
    }],
    ['superseded state poll cannot change current run',async()=>{
      const f=fixture();let release;f.env.fetch=async url=>url==='/api/runs/A'?new Promise(resolve=>{release=()=>resolve(response(run('A')));}):response(url==='/api/runs/B'?run('B'):url.includes('/report')?report('B'):{items:[],next_cursor:null});
      const pending=f.api.poll('A');await f.api.poll('B');release();await pending;
      assert.equal(f.api.get().current.id,'B');assert.equal(f.api.get().saved.id,'B');
    }],
    ['late cancellation response cannot replace a newer view',async()=>{
      const f=fixture();let release;f.api.showState(run('A'));
      f.env.fetch=async()=>new Promise(resolve=>{release=()=>resolve(response({...run('A'),state:'cancelling'}));});
      const pending=f.el('cancel').events.get('click')();f.api.showState(run('B'));release();await pending;
      assert.equal(f.api.get().current.id,'B');
    }],
    ['inflight original export keeps its starting run filename',async()=>{
      const f=fixture();let release;f.api.showState(run('A'));f.env.fetch=async()=>response(report('A'));await f.api.loadReport('A');
      f.env.fetch=async url=>url.includes('/export')?new Promise(resolve=>{release=()=>resolve({ok:true,blob:async()=>({})});}):response(report('B'));
      const pending=f.api.download('json');f.api.showState(run('B'));await f.api.loadReport('B');release();await pending;
      assert.equal(f.downloads[0],'evidence-A.json');
    }],
    ['active reading can display distinctly labelled already-loaded history',async()=>{
      const f=fixture();f.api.showState(run('A'));const old=report('A');old.report.status='partial';
      f.env.fetch=async()=>response(old);await f.api.loadReport('A');f.api.showState({...run('B'),state:'running',analysis_status:null,publication:'uncommitted'});
      f.env.fetch=async()=>response({items:[run('A')],next_cursor:null});await f.api.loadHistory();
      await f.el('history').children[0].events.get('click')();
      assert.equal(f.api.get().current.id,'B');assert.equal(f.api.get().saved.id,'A');
      assert.match(f.el('report-context').textContent,/历史/);assert.match(f.el('report-context').textContent,/A/);
      assert.equal(f.el('status').textContent,'正在读取并核对证据');assert.equal(f.el('exports').hidden,true);
    }],
    ['failed reading shows code stage and actual limit without source or export',async()=>{
      const f=fixture();const failed={...run('F'),state:'failed',analysis_status:'failed',publication:'uncommitted',verification_status:'not_started',error:'scope_limit',analysis_failure:{code:'scope_limit',field:'max_metadata_entries',stage:'supervisor_storage',effective:20000,observed:20001}};
      f.env.fetch=async url=>response(url.includes('/history')?{items:[failed],next_cursor:null}:failed);
      await f.api.poll('F');
      for(const value of ['scope_limit','max_metadata_entries','supervisor_storage','20000','20001']) assert.ok(f.el('outcome-detail').textContent.includes(value),value);
      assert.equal(f.el('exports').hidden,true);assert.equal(f.el('source-panel').hidden,true);
      assert.equal(f.el('status').textContent,'本次读取未完成');assert.equal(f.api.get().saved,null);
    }],
    ['manual capacity selection is submitted once without changing the choice',async()=>{
      const f=fixture(true);f.el('repo').value='/full-project';f.el('question').value='Read the full project';f.el('entries').value='a.py:entry';f.el('symbols').value='';f.el('traceback').value='';f.el('profile').value='standard-capacity';
      const done={...run('C'),state:'failed',analysis_status:'failed',publication:'uncommitted',error:'scope_limit',profile_name:'standard-capacity'};
      f.env.fetch=async url=>response(url.includes('/history')?{items:[],next_cursor:null}:done);
      await f.el('reading-form').events.get('submit')({preventDefault(){}});
      const posted=f.calls.filter(call=>call[1]?.method==='POST');assert.equal(posted.length,1);
      assert.equal(JSON.parse(posted[0][1].body).profile_name,'standard-capacity');
      assert.equal(f.api.get().current.profile_name,'standard-capacity');
    }],
    ['successful current verification replaces previous failed heading',async()=>{
      const f=fixture();f.api.showState({...run('A'),state:'verification_failed',verification_status:'budget_exceeded'});f.env.fetch=async()=>response(report('A'));
      await f.api.loadReport('A');assert.equal(f.api.get().current.state,'ready');assert.equal(f.el('status').textContent,'读取完成');
    }]
  ];
  let failures=0;
  for(const [name,test] of tests){try{await test();log('PASS '+name);}catch(error){failures++;log('FAIL '+name+' '+error.message);}}
  const receipt={status:failures?'failed':'passed',tests:tests.length,failures,browser:false,native_analysis:false};
  const out=path.join(__dirname,'../artifacts/test-runs/evidence-ui-js-'+randomUUID());fs.mkdirSync(out,{recursive:true});
  fs.writeFileSync(path.join(out,'diagnostic.txt'),logs.join('\n')+'\n');fs.writeFileSync(path.join(out,'receipt.json'),JSON.stringify(receipt,null,2)+'\n');
  log(JSON.stringify({...receipt,run_dir:out}));process.exitCode=failures?1:0;
})();
