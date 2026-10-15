// Offline own-product logic fixtures. No browser, socket, worker or target code.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {randomUUID} = require('node:crypto');
const source = fs.readFileSync(path.join(__dirname,'../src/macr/api/evidence_ui/workbench.js'),'utf8');
function fixture(session=false) {
  const elements = new Map(), storage = new Map(), calls = [], downloads = [];
  function element() {
    const attributes = new Map(), events = new Map();
    return {hidden:false, disabled:false, textContent:'', value:'', children:[], events,
      classList:{toggle(){}}, setAttribute(k,v){attributes.set(k,v);}, removeAttribute(k){attributes.delete(k);},
      addEventListener(k,v){events.set(k,v);}, replaceChildren(...items){this.children=items;},
      append(...items){this.children.push(...items);}, focus(){}, remove(){}, click(){downloads.push(this.download);},
      get firstElementChild(){return this.children[0];}};
  }
  const document = {hidden:false,getElementById(id){if(!elements.has(id)) elements.set(id,element());return elements.get(id);},createElement:element};
  const env={fetch:async()=>{throw new Error('fixture_fetch_unconfigured');}};
  const factory=new Function('document','location','sessionStorage','history','fetch','setTimeout','clearTimeout','setInterval','crypto','URL',
    source+'\nreturn {api,poll,loadReport,showState,download,loadHistory,get:()=>({current,saved,intent})};');
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
