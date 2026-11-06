"""Explicit process-owned model session; browser inputs never enable transport."""
from pathlib import Path
from types import SimpleNamespace
import time

from ..investigation import repair_entry as entry, repair_loop as loop
from ..investigation.validation import ContractError
from ..providers.base import ProviderError
from ..providers.repair_binding import validate_config
from ..providers.repair_spend import validate_spend_plan


class RepairRuntime:
    def __init__(self, plan_path, cell_id, *, credential_reader=None):
        self.plan_path=Path(plan_path).absolute()
        try:
            self.plan=validate_spend_plan(entry._json(self.plan_path))
            if time.time()>=self.plan['expires_at'] or cell_id not in [c['cell_id'] for c in self.plan['cells']]:raise ValueError()
        except (ValueError,OSError):
            raise ContractError('input_invalid','repair_spend_plan') from None
        self.plan_sha256=loop.digest(self.plan)
        self.cell_id=cell_id
        self._reader=credential_reader or entry._read_runtime_credential
        self._credential=None;self._closed=False

    def prepare(self):
        if self._closed:raise ProviderError('repair_credential_input_unavailable')
        if self._credential is None:
            value=self._reader()
            if not (type(value) is str and 0<len(value)<=4096 and all(33<=ord(c)<=126 for c in value)):
                raise ProviderError('repair_credential_input_unavailable')
            self._credential=value

    def credential(self):
        if self._closed or self._credential is None:raise ProviderError('repair_credential_input_unavailable')
        return self._credential

    def close(self):
        self._credential=None;self._reader=None;self._closed=True

    def arguments(self, request, out):
        try:
            current=validate_spend_plan(entry._json(self.plan_path))
            if loop.digest(current)!=self.plan_sha256:raise ValueError()
            task=entry.load_task(request['repo'],request['task'])
            config=validate_config(entry._json(request['model_config']))
            binding=entry.spend_binding_sha256(task,request['strategy'],config,
                proposal_contract=request['proposal_contract'])
            cells=[c for c in self.plan['cells'] if c['binding_sha256']==binding and c['cell_id']==self.cell_id]
            if len(cells)!=1:raise ValueError()
            args=SimpleNamespace(**request,out=out,
                enable_remote=True,spend_plan=self.plan_path,spend_cell=cells[0]['cell_id'],
                expected_spend_plan_sha256=self.plan_sha256)
            entry._runtime_plan(args,task,config)
            return args
        except (ValueError,OSError,ProviderError,ContractError):
            raise ContractError('input_invalid','repair_runtime_scope') from None

    def public(self):
        return {'remote_enabled':True,'credential_ready':self._credential is not None and not self._closed,
            'endpoint':'https://api.deepseek.com','plan_sha256':self.plan_sha256,
            'expires_at':self.plan['expires_at'],'max_requests':self.plan['max_requests'],
            'max_usd':self.plan['max_usd'],'cell_id':self.cell_id}
