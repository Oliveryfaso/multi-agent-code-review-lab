"""Local durable repair-call admission; never credentials, HTTP or authorization.

The exclusive writer/append/fsync and known-prefix recovery patterns come from
the reviewed literal-controlled runner. Local files prevent accidental replay;
their hashes do not authenticate an operator, provider or machine owner.
"""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import fcntl
import os
from pathlib import Path
import re
import stat
import time

from macr.investigation.repair_io import encode_bounded, load_json
from macr.investigation.scope import open_directory, safe_parent

SCHEMA = 'macr.repair.spend-plan.v1'
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z')
_USD = re.compile(r'(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,6})?\Z')
_FINISH = {'completed','failed','stopped','budget_exhausted','verification_unavailable'}
_CALL_STATUS = {'dispatch_intent','response_recorded','known_provider_failure',
                'outcome_unknown','identity_invalid','usage_limit_exceeded','processing_failed'}


class RepairSpendError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(ok, code='repair_spend_invalid'):
    if not ok: raise RepairSpendError(code)


def _hash(value):
    return sha256(encode_bounded(value, 65536)).hexdigest()


def _money(value):
    _need(type(value) is str and _USD.fullmatch(value) is not None)
    amount = Decimal(value) * 1000000
    _need(amount <= 100000000000 and amount == int(amount))
    return int(amount)


def _usd(value):
    return f'{value//1000000}.{value%1000000:06d}'


def _integer(value, minimum=0, maximum=1000000000):
    return type(value) is int and minimum <= value <= maximum


def _text(value):
    try:
        return (type(value) is str and 0 < len(value) <= 256 and value == value.strip()
                and not any(c in value for c in '\x00\n\r') and len(value.encode()) <= 1024)
    except UnicodeError: return False


def validate_spend_plan(plan):
    fields = {'schema','run_id','ledger_root','configuration_sha256','output_contract_sha256',
              'max_requests','max_usd','max_total_input_tokens','max_total_output_tokens',
              'expires_at','cells'}
    try:
        _need(type(plan) is dict and set(plan) == fields and plan['schema'] == SCHEMA)
        _need(type(plan['run_id']) is str and _ID.fullmatch(plan['run_id']) is not None)
        path = plan['ledger_root']
        _need(type(path) is str and len(path.encode()) <= 4096 and Path(path).is_absolute()
              and str(Path(path)) == path and '..' not in Path(path).parts
              and Path(path).resolve() == Path(path))
        _need(all(type(plan[k]) is str and _HEX.fullmatch(plan[k]) for k in
                  ('configuration_sha256','output_contract_sha256')))
        _need(_integer(plan['max_requests'],1,384) and 0 < _money(plan['max_usd'])
              and _integer(plan['max_total_input_tokens'],1)
              and _integer(plan['max_total_output_tokens'],1)
              and _integer(plan['expires_at'],1,4102444800))
        cells = plan['cells']
        _need(type(cells) is list and 1 <= len(cells) <= 128)
        for cell in cells:
            _need(type(cell) is dict and set(cell) == {'cell_id','binding_sha256'}
                  and type(cell['cell_id']) is str and _ID.fullmatch(cell['cell_id'])
                  and type(cell['binding_sha256']) is str and _HEX.fullmatch(cell['binding_sha256']))
        _need(len({c['cell_id'] for c in cells}) == len(cells))
        encode_bounded(plan,65536)
        return deepcopy(plan)
    except (ValueError,TypeError,KeyError,OSError,UnicodeError,RecursionError) as error:
        if isinstance(error,RepairSpendError): raise
        raise RepairSpendError('repair_spend_plan_invalid') from None


class RepairSpendLedger:
    """Own the lock across a complete cell, including remote I/O in the caller.

    New roots must not exist. A previous incomplete claim/reservation is never
    reclaimed. Expiry denies new intent, but cannot erase late real charges.
    """
    def __init__(self, root, plan, config):
        from macr.providers.repair_binding import validate_config
        self._plan = validate_spend_plan(plan)
        self.root = Path(root)
        self._config = validate_config(config)
        _need(self.root == Path(self._plan['ledger_root']) and self.root.resolve() == self.root,
              'repair_spend_root_mismatch')
        _need(_hash(self._config) == self._plan['configuration_sha256'], 'repair_spend_config_mismatch')
        self.plan_sha256 = _hash(self._plan)
        self._fd = self._lock_fd = None
        self._identity = None
        self.cell_id = None
        self._pending_seq = None
        self._poisoned = False

    def ref(self):
        return {'plan_sha256':self.plan_sha256,'cell_id':self.cell_id}

    def __enter__(self):
        _need(self._fd is None,'repair_spend_already_open')
        try:
            new = False
            with open_directory(self.root.parent) as parent:
                try:
                    os.stat(self.root.name,dir_fd=parent,follow_symlinks=False)
                except FileNotFoundError:
                    _need(time.time() < self._plan['expires_at'],'repair_spend_expired')
                    try: os.mkdir(self.root.name,0o700,dir_fd=parent)
                    except FileExistsError: pass
                    else: os.fsync(parent);new = True
            with open_directory(self.root) as directory:
                self._fd = os.dup(directory)
            info = os.fstat(self._fd)
            _need(stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.geteuid(),
                  'repair_spend_root_not_private')
            self._identity = [info.st_dev,info.st_ino]
            self._lock_fd = os.open('writer.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=self._fd)
            lock = os.fstat(self._lock_fd)
            _need(stat.S_ISREG(lock.st_mode) and lock.st_nlink == 1
                  and stat.S_IMODE(lock.st_mode) == 0o600,'repair_spend_lock_invalid')
            try: fcntl.flock(self._lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError: raise RepairSpendError('repair_spend_busy') from None
            if new:
                self._append('plan.json',{'schema':'macr.repair.spend-root.v1',
                    'plan':self._plan,'plan_sha256':self.plan_sha256,'root_identity':self._identity},65536)
            self._load()
            return self
        except Exception as error:
            self.__exit__(None,None,None)
            if isinstance(error,RepairSpendError): raise
            raise RepairSpendError('repair_spend_storage_invalid') from None

    def __exit__(self,*_):
        for name in ('_lock_fd','_fd'):
            fd = getattr(self,name)
            if fd is not None: os.close(fd);setattr(self,name,None)
        self.cell_id = None;self._pending_seq = None

    def _anchor(self):
        _need(self._fd is not None,'repair_spend_closed')
        with open_directory(self.root) as current:
            info = os.fstat(current)
            _need([info.st_dev,info.st_ino] == self._identity,'repair_spend_root_changed')
            if self._lock_fd is not None:
                locked=os.fstat(self._lock_fd)
                named=os.stat('writer.lock',dir_fd=current,follow_symlinks=False)
                _need(stat.S_ISREG(named.st_mode) and named.st_nlink==1
                    and (locked.st_dev,locked.st_ino)==(named.st_dev,named.st_ino),
                    'repair_spend_lock_changed')

    def _append(self,name,value,limit=4096):
        try:
            self._anchor()
            payload = encode_bounded(value,limit)
            with safe_parent(self.root,name) as (directory,leaf):
                _need([os.fstat(directory).st_dev,os.fstat(directory).st_ino] == self._identity,
                      'repair_spend_root_changed')
                fd = os.open(leaf,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
                with os.fdopen(fd,'wb') as stream:
                    stream.write(payload);stream.flush();os.fsync(stream.fileno())
                os.fsync(directory)
            self._anchor()
        except Exception as error:
            self._poisoned = True
            if isinstance(error,RepairSpendError): raise
            raise RepairSpendError('repair_spend_write_failed') from None

    def _read(self,name,limit=4096):
        self._anchor()
        fd = os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=self._fd)
        with os.fdopen(fd,'rb') as stream:
            before = os.fstat(stream.fileno())
            _need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                  and before.st_uid == os.geteuid() and stat.S_IMODE(before.st_mode) == 0o600
                  and before.st_size <= limit,'repair_spend_record_invalid')
            raw = stream.read(limit+1);after = os.fstat(stream.fileno())
            _need((before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns),
                  'repair_spend_record_changed')
        return load_json(raw.decode(),max_bytes=limit,max_depth=16,utf8_bytes=True)

    def _reserve_amount(self):
        limits=self._config['limits']
        return self._charge(limits['reservation_input_tokens'],limits['max_output_per_request'])

    def _charge(self,inputs,outputs):
        price=self._config['pricing']
        numerator=inputs*_money(price['input_usd_per_million'])+outputs*_money(price['output_usd_per_million'])
        return (numerator+999999)//1000000

    def _outcome(self,reservation,usage,identity,status,previous_fingerprint):
        valid_usage=(status not in ('dispatch_intent','outcome_unknown')
            and type(usage) is dict and set(usage)=={'input_tokens','output_tokens'}
            and all(_integer(usage[k]) for k in usage) and sum(usage.values())>0)
        usage=deepcopy(usage) if valid_usage else None
        valid_identity=(type(identity) is dict and set(identity)=={'model','fingerprint'}
            and _text(identity['model']) and _text(identity['fingerprint']))
        identity=deepcopy(identity) if valid_identity else None
        reason=None
        fingerprint=previous_fingerprint
        if usage is None:reason='usage_unknown'
        if identity is None or identity['model']!=self._config['model']:
            reason=reason or 'identity_invalid'
        elif fingerprint is not None and identity['fingerprint']!=fingerprint:
            reason=reason or 'identity_drift'
        else:fingerprint=identity['fingerprint']
        if status in {'processing_failed','identity_invalid','usage_limit_exceeded'}:
            reason=reason or status
        return {'schema':'macr.repair.spend-outcome.v1','reservation_sha256':_hash(reservation),
            'usage':usage,'identity':identity,'call_status':status,
            'known_micro_usd':self._charge(**{'inputs':usage['input_tokens'],'outputs':usage['output_tokens']}) if usage else 0,
            'reservation_retained':usage is None,'stop_reason':reason},fingerprint

    def _budget(self,state,cell):
        limits=self._config['limits'];plan=self._plan
        _need(not state['stopped'] and not state['pending'],'repair_spend_stopped')
        _need(state['requests'] < plan['max_requests']
            and state['known']+state['held']+self._reserve_amount() <= _money(plan['max_usd'])
            and state['inputs']+limits['max_input_per_request'] <= plan['max_total_input_tokens']
            and state['outputs']+limits['max_output_per_request'] <= plan['max_total_output_tokens']
            and cell['requests'] < limits['max_requests']
            and cell['known']+self._reserve_amount() <= _money(limits['max_usd'])
            and cell['inputs']+limits['max_input_per_request'] <= limits['max_total_input_tokens']
            and cell['outputs']+limits['max_output_per_request'] <= limits['max_total_output_tokens'],
            'repair_spend_budget_exhausted')

    def _load(self):
        try:
            self._anchor()
            expected={'schema':'macr.repair.spend-root.v1','plan':self._plan,
                      'plan_sha256':self.plan_sha256,'root_identity':self._identity}
            _need(self._read('plan.json',65536)==expected,'repair_spend_plan_mismatch')
            names=set(os.listdir(self._fd))
            _need(len(names)<=3+2*len(self._plan['cells'])+2*self._plan['max_requests'],
                  'repair_spend_record_limit')
            allowed={'plan.json','writer.lock'}
            claims=[];finished={};cells={}
            for index,cell in enumerate(self._plan['cells'],1):
                claim=f'cell-{index:03d}.claim.json';finish=f'cell-{index:03d}.finish.json'
                if claim not in names:
                    _need(finish not in names);continue
                _need(index==len(claims)+1 and (index==1 or index-1 in finished),
                      'repair_spend_claim_prefix')
                claimed=self._read(claim)
                _need(type(claimed.get('index')) is int and claimed=={'schema':'macr.repair.spend-claim.v1',
                    'plan_sha256':self.plan_sha256,'index':index,**cell},'repair_spend_claim_invalid')
                claims.append(cell['cell_id']);allowed.add(claim)
                cells[cell['cell_id']]={'requests':0,'inputs':0,'outputs':0,'known':0}
                if finish in names:
                    row=self._read(finish)
                    _need(type(row) is dict and set(row)=={'schema','cell_id','status'}
                          and row['schema']=='macr.repair.spend-finish.v1'
                          and row['cell_id']==cell['cell_id'] and row['status'] in _FINISH)
                    finished[index]=row;allowed.add(finish)
            state={'requests':0,'inputs':0,'outputs':0,'known':0,'held':0,
                'fingerprint':self._config['expected_fingerprint'],'stopped':False,
                'reason':None,'pending':None,'claims':claims,'finished':finished,'cells':cells}
            previous_cell=-1
            for number in range(1,self._plan['max_requests']+1):
                name=f'request-{number:04d}.reserve.json';out=f'request-{number:04d}.outcome.json'
                if name not in names:
                    _need(out not in names);continue
                row=self._read(name)
                _need(type(row) is dict and set(row)=={'schema','plan_sha256','sequence','cell_id',
                    'number','request_sha256','wire_sha256','reserved_micro_usd'}
                    and row['schema']=='macr.repair.spend-reserve.v1'
                    and row['plan_sha256']==self.plan_sha256 and type(row['sequence']) is int
                    and type(row['number']) is int and row['sequence']==number
                    and number==state['requests']+1 and row['cell_id'] in cells,
                    'repair_spend_reservation_invalid')
                cell=cells[row['cell_id']];cell_index=claims.index(row['cell_id'])
                _need(cell_index>=previous_cell and row['number']==cell['requests']+1
                    and row['reserved_micro_usd']==self._reserve_amount()
                    and all(type(row[k]) is str and _HEX.fullmatch(row[k]) for k in
                            ('request_sha256','wire_sha256')),'repair_spend_reservation_invalid')
                self._budget(state,cell);previous_cell=cell_index
                cell['requests']+=1;state['requests']+=1;allowed.add(name)
                if out not in names:
                    state['pending']=row;state['held']+=row['reserved_micro_usd'];continue
                outcome=self._read(out)
                _need(type(outcome) is dict and outcome.get('call_status') in _CALL_STATUS
                    and type(outcome.get('known_micro_usd')) is int
                    and type(outcome.get('reservation_retained')) is bool,
                      'repair_spend_outcome_invalid')
                computed,fingerprint=self._outcome(row,outcome.get('usage'),outcome.get('identity'),
                    outcome['call_status'],state['fingerprint'])
                _need(computed==outcome,'repair_spend_outcome_invalid');allowed.add(out)
                state['fingerprint']=fingerprint
                if outcome['reservation_retained']:state['held']+=row['reserved_micro_usd']
                else:
                    i,o=outcome['usage']['input_tokens'],outcome['usage']['output_tokens']
                    state['inputs']+=i;state['outputs']+=o;state['known']+=outcome['known_micro_usd']
                    cell['inputs']+=i;cell['outputs']+=o;cell['known']+=outcome['known_micro_usd']
                    limits=self._config['limits']
                    if (i>limits['max_input_per_request'] or o>limits['max_output_per_request']
                        or state['inputs']>self._plan['max_total_input_tokens']
                        or state['outputs']>self._plan['max_total_output_tokens']
                        or state['known']+state['held']>_money(self._plan['max_usd'])
                        or cell['inputs']>limits['max_total_input_tokens']
                        or cell['outputs']>limits['max_total_output_tokens']
                        or cell['known']>_money(limits['max_usd'])
                        or outcome['known_micro_usd']>row['reserved_micro_usd']):
                        state['stopped']=True;state['reason']='usage_limit_exceeded'
                if outcome['stop_reason']:
                    state['stopped']=True;state['reason']=outcome['stop_reason']
            _need(names==allowed,'repair_spend_unexpected_files')
            incomplete=bool(claims and len(finished)<len(claims))
            if (state['pending'] and self._pending_seq!=state['pending']['sequence']) or (
                incomplete and self.cell_id!=claims[-1]):
                state['stopped']=True;state['reason']=state['reason'] or 'incomplete_claim'
            return state
        except Exception as error:
            if isinstance(error,RepairSpendError):raise
            raise RepairSpendError('repair_spend_storage_invalid') from None

    def summary(self):
        state=self._load()
        return {'schema':'macr.repair.spend-summary.v1',**self.ref(),
            'configuration_sha256':self._plan['configuration_sha256'],
            'requests':state['requests'],'input_tokens':state['inputs'],'output_tokens':state['outputs'],
            'known_usd':_usd(state['known']),'unknown_reserved_usd':_usd(state['held']),
            'expected_fingerprint':state['fingerprint'],'stopped':state['stopped'] or self._poisoned,
            'stop_reason':state['reason'] or ('storage_failure' if self._poisoned else None),
            'state':'halted' if state['stopped'] or self._poisoned else
                    'awaiting_outcome' if state['pending'] else 'open',
            'claimed_cells':list(state['claims']),'finished_cells':len(state['finished']),
            'local_budget_only':True,'identity_authenticated':False,'automatic_retry_allowed':False}

    def claim_cell(self,cell_id,binding_sha256):
        _need(not self._poisoned and self.cell_id is None,'repair_spend_cell_active')
        _need(time.time()<self._plan['expires_at'],'repair_spend_expired')
        state=self._load();_need(not state['stopped'],'repair_spend_stopped')
        index=len(state['claims'])
        _need(index<len(self._plan['cells']) and self._plan['cells'][index]=={
            'cell_id':cell_id,'binding_sha256':binding_sha256},'repair_spend_cell_mismatch')
        self._append(f'cell-{index+1:03d}.claim.json',{'schema':'macr.repair.spend-claim.v1',
            'plan_sha256':self.plan_sha256,'index':index+1,'cell_id':cell_id,'binding_sha256':binding_sha256})
        self.cell_id=cell_id
        return self.summary()

    def _bind_call(self,call):
        _need(type(call) is dict and _integer(call.get('number'),1,3)
            and all(type(call.get(k)) is str and _HEX.fullmatch(call[k]) for k in
                    ('request_sha256','wire_sha256'))
            and type(call.get('reserved_micro_usd')) is int
            and call['reserved_micro_usd']==self._reserve_amount(),'repair_spend_call_invalid')

    def reserve(self,call):
        _need(not self._poisoned and self.cell_id is not None,'repair_spend_cell_missing')
        _need(time.time()<self._plan['expires_at'],'repair_spend_expired')
        self._bind_call(call);state=self._load();cell=state['cells'][self.cell_id]
        self._budget(state,cell)
        _need(call.get('status')=='dispatch_intent' and call.get('reservation_retained') is True
              and call['number']==cell['requests']+1,'repair_spend_call_order')
        sequence=state['requests']+1
        row={'schema':'macr.repair.spend-reserve.v1','plan_sha256':self.plan_sha256,
             'sequence':sequence,'cell_id':self.cell_id,**{k:call[k]for k in
             ('number','request_sha256','wire_sha256','reserved_micro_usd')}}
        self._append(f'request-{sequence:04d}.reserve.json',row)
        self._pending_seq=sequence
        return self.summary()

    def settle(self,call):
        _need(not self._poisoned and self.cell_id is not None and self._pending_seq is not None,
              'repair_spend_pending_missing')
        self._bind_call(call);state=self._load();row=state['pending']
        _need(row is not None and row['sequence']==self._pending_seq
            and all(row[k]==call[k]for k in ('number','request_sha256','wire_sha256','reserved_micro_usd')),
            'repair_spend_receipt_mismatch')
        status=call.get('status')
        _need(status in _CALL_STATUS,'repair_spend_call_invalid')
        usage=call.get('usage') if call.get('reservation_retained') is False else None
        if type(usage) is dict:
            usage={k:usage.get(k)for k in ('input_tokens','output_tokens')}
        identity=call.get('identity')
        if (type(identity) is dict and identity.get('model_state')=='validated'
            and identity.get('fingerprint_state')=='validated'):
            identity={'model':identity.get('model'),'fingerprint':identity.get('system_fingerprint')}
        else:identity=None
        outcome,_=self._outcome(row,usage,identity,status,state['fingerprint'])
        self._append(f'request-{self._pending_seq:04d}.outcome.json',outcome)
        self._pending_seq=None
        return self.summary()

    def finish_cell(self,status):
        _need(self.cell_id is not None and type(status) is str and status in _FINISH,
              'repair_spend_finish_invalid')
        state=self._load()
        _need(status!='completed' or (not state['stopped'] and not state['pending'] and not self._poisoned),
              'repair_spend_false_completion')
        index=state['claims'].index(self.cell_id)+1
        self._append(f'cell-{index:03d}.finish.json',{'schema':'macr.repair.spend-finish.v1',
            'cell_id':self.cell_id,'status':status})
        self.cell_id=None;self._pending_seq=None
        return self.summary()
