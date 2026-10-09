"""Pure b11521 observation checks; no process, HTTP, polling or proof fabrication.

Pinned source: common/log.h and tools/server/{server-queue,server-context,
server-http}.cpp at b11521/b42b7e6d3. Caller owns capture, identity and deadlines.
"""
import math
import re

FINGERPRINT='b11521-b42b7e6d3'
LOG_LIMIT=4*1024*1024

def _require(ok,code):
    if not ok:raise ValueError(code)

def validate_preflight(version,help_text,argv,*,port_mode='fixed'):
    """Reject the old verbosity1 argv before an owner can spawn a model."""
    _require(type(version) is str and re.match(r'^version: 0\.6\.0-dev \(build 11521, commit b42b7e6d3\)(?:\r?\n|$)',version),'runtime_version')
    _require(type(help_text) is str and len(help_text.encode())<=1024**2,'runtime_help')
    for level,name in enumerate(['generic output','error','warning','info','trace (more info)','debug']):
        _require(len(re.findall(r'^\s*- '+str(level)+': '+re.escape(name)+r'\s*$',help_text,re.M))==1,'log_level_semantics')
    _require(type(argv) is list and all(type(s) is str for s in argv),'argv_invalid')
    _require(port_mode in ('fixed','kernel'),'port_mode')
    pairs={'--log-verbosity':'5','--log-colors':'off','--host':'127.0.0.1','--port':'0' if port_mode=='kernel' else '18080','--device':'none','--n-gpu-layers':'0',
        '--cache-ram':'0','--threads':'2','--threads-batch':'2','--threads-http':'2','--parallel':'1','--ctx-size':'2048','--predict':'384','--chat-template':'chatml',
        '--batch-size':'128','--ubatch-size':'128','--temp':'0','--top-p':'1','--top-k':'0','--min-p':'0','--seed':'0'}
    flags=['--no-log-prefix','--no-log-timestamps','--no-log-jsonl','--offline','--no-cache-prompt','--no-context-shift','--no-webui','--no-agent','--no-kv-offload','--no-cont-batching','--no-warmup']
    value_flags=set(pairs)|{'--model','--alias'};seen=set();i=1
    while i<len(argv):
        flag=argv[i];_require(flag not in seen and flag in value_flags.union(flags),'argv_unknown_or_duplicate');seen.add(flag);i+=1
        if flag in value_flags:
            _require(i<len(argv) and argv[i] and not argv[i].startswith('--') and not any(c in argv[i] for c in '\x00\r\n'),'argv_value')
            i+=1
    _require({'--model','--alias'}<=seen,'argv_model_identity')
    for flag,value in pairs.items():
        _require(argv.count(flag)==1 and argv.index(flag)+1<len(argv) and argv[argv.index(flag)+1]==value,'argv_'+flag[2:].replace('-','_'))
    _require(all(argv.count(flag)==1 for flag in flags),'argv_required_flags')
    return {'version':FINGERPRINT,'required_level':5,'format':'plain_no_prefix_no_timestamp_no_colors','native_http_logger_required':False,'port_mode':port_mode}

def listener_port(log,*,pid,returncode,output):
    """Bind the native address to a single LISTEN socket observed for our PID.

    The caller uses bounded `lsof -nP -a -p PID -i4TCP -sTCP:LISTEN -Fpn`,
    checks child identity before/after, and makes no API call on None/error.
    Port0 is bound by the server itself; there is no reserve/release handoff.
    """
    _require(type(log) is bytes and len(log)<=LOG_LIMIT and type(pid) is int and 0<pid<2**31,'listener_capture')
    complete=log if log.endswith(b'\n') else log.rpartition(b'\n')[0]
    try:lines=complete.decode('utf-8','strict').splitlines()
    except UnicodeError:raise ValueError('listener_log_invalid') from None
    ports=[]
    for line in lines:
        if 'listening on' not in line:continue
        match=re.fullmatch(r'srv  llama_server: listening on http://127\.0\.0\.1:([1-9][0-9]{0,4})',line)
        _require(match is not None,'listener_log_unknown')
        ports.append(int(match[1]));_require(ports[-1]<=65535,'listener_port_invalid')
    _require(len(ports)<=1,'listener_log_ambiguous')
    _require(type(returncode) is int and returncode in (0,1) and type(output) is str and len(output.encode())<=1024**2,'listener_observation')
    if returncode==1:
        _require(output=='','listener_observation_unknown');return None
    fields=output.splitlines()
    if len(fields)==3:
        _require(re.fullmatch(r'f[0-9]{1,7}',fields[1]) is not None,'listener_descriptor')
        fields=[fields[0],fields[2]]
    _require(len(fields)==2 and fields[0]=='p'+str(pid),'listener_identity_or_count')
    match=re.fullmatch(r'n127\.0\.0\.1:([1-9][0-9]{0,4})',fields[1])
    _require(match is not None and int(match[1])<=65535,'listener_address')
    if not ports:return None
    _require(int(match[1])==ports[0],'listener_port_drift')
    return ports[0]

def validate_model(props,models,*,weight_path,model_id):
    _require(type(props) is dict and type(models) is dict and type(props.get('total_slots')) is int and props['total_slots']==1
        and props.get('model_path')==weight_path and type(props.get('default_generation_settings')) is dict
        and type(props['default_generation_settings'].get('n_ctx')) is int and props['default_generation_settings']['n_ctx']==2048,'native_context_identity')
    data=models.get('data');_require(type(data) is list and len(data)==1 and type(data[0]) is dict and data[0].get('id')==model_id,'native_alias')

def validate_prompt(messages,template,tokenized):
    _require(type(messages) is list and len(messages)==2 and all(type(m) is dict and set(m)=={'role','content'} and type(m['content']) is str for m in messages)
        and [m['role'] for m in messages]==['system','user'],'native_prompt_roles')
    expected=''.join('<|im_start|>'+m['role']+'\n'+m['content']+'<|im_end|>\n' for m in messages)+'<|im_start|>assistant\n'
    _require(type(template) is dict and template.get('prompt')==expected,'native_template_mismatch')
    ids=tokenized.get('tokens') if type(tokenized) is dict else None
    _require(type(ids) is list and 0<len(ids)<=1536 and all(type(t) is int and t>=0 for t in ids) and ids.count(151644)==3 and ids.count(151645)==2,'native_tokens_or_context')
    return len(ids)

def validate_response(raw,*,http_status,model_id,input_tokens):
    """Outer native contract/usage only; content JSON is graded after idle proof."""
    _require(type(http_status) is int and http_status==200,'native_http_status')
    _require(type(raw) is dict and raw.get('system_fingerprint')==FINGERPRINT and raw.get('model')==model_id,'native_response_identity')
    _require(type(input_tokens) is int and 0<input_tokens<=1536,'input_token_count')
    u=raw.get('usage');_require(type(u) is dict and all(type(u.get(k)) is int for k in ['prompt_tokens','completion_tokens','total_tokens'])
        and u['prompt_tokens']==input_tokens and 0<u['completion_tokens']<=384 and u['total_tokens']==input_tokens+u['completion_tokens'],'native_usage_mismatch')
    details=u.get('prompt_tokens_details');_require(type(details) is dict and type(details.get('cached_tokens')) is int and details['cached_tokens']==0,'native_cache_reuse')
    choices=raw.get('choices');_require(type(choices) is list and len(choices)==1 and type(choices[0]) is dict,'native_choices')
    c=choices[0];m=c.get('message');_require(type(c.get('index')) is int and c['index']==0 and c.get('finish_reason') in ['stop','length'] and type(m) is dict
        and m.get('role')=='assistant' and type(m.get('content')) is str and not m.get('tool_calls') and not m.get('reasoning_content'),'native_response_contract')
    return {'input_tokens':input_tokens,'output_tokens':u['completion_tokens'],'finish_reason':c['finish_reason']}

def idle_proof(log,*,elapsed_seconds):
    """Parse one bounded request slice. Never attest a late/partial capture.

    Result-reader removal and slot release run in separate threads. Both must
    follow the one add; all-idle must follow release. HTTP200 is checked by the
    owner from its independent full response, not an unregistered HTTP logger.
    """
    result={'qualified':False,'task_id':None,'add_line':None,'release_line':None,'remove_waiting_line':None,'idle_line':None,'error':'native_idle_missing'}
    if type(elapsed_seconds) not in (int,float) or not math.isfinite(elapsed_seconds) or not 0<=elapsed_seconds<10:
        return result|{'error':'native_idle_timeout'}
    if type(log) is not bytes or len(log)>LOG_LIMIT or log and not log.endswith(b'\n'):
        return result|{'error':'native_log_incomplete'}
    try:lines=log.decode('utf-8','strict').splitlines()
    except UnicodeError:return result|{'error':'native_log_unknown'}
    patterns=[r'res  add_waiting_: add task (\d+) to waiting list\. current waiting = 0 \(before add\)',
        r'slot +release: id +0 \| task (\d+) \| stop processing: n_tokens = [1-9]\d*, truncated = 0',
        r'res  remove_waiti: remove task (\d+) from waiting list\. current waiting = 1 \(before remove\)',
        r'srv  update_slots: all slots are idle']
    hits=[[],[],[],[]]
    for number,line in enumerate(lines,1):
        for kind,pattern in enumerate(patterns):
            m=re.fullmatch(pattern,line)
            if m:hits[kind].append((int(m[1]) if kind<3 else None,number));break
        else:
            if any(marker in line for marker in ['waiting list','stop processing:','all slots are idle']):
                return result|{'error':'native_log_unknown'}
    if not all(len(h)==1 for h in hits[:3]):return result
    (task,a), (released,r), (removed,d)=[h[0] for h in hits[:3]]
    after=[i for _,i in hits[3] if i>r]
    if task!=released or task!=removed or not (a<r and a<d) or not after:return result
    return result|{'qualified':True,'task_id':task,'add_line':a,'release_line':r,'remove_waiting_line':d,'idle_line':after[0],'error':None}
