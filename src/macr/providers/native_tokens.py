"""Exact b5083 ChatML/native-token accounting. No service startup or estimate."""
from dataclasses import asdict
import hashlib
import json

from .base import ProviderError, validate_request
from .profiles import validate_profile


def _require(ok):
    if not ok:
        raise ProviderError('model_token_accounting_unavailable')


class ChatMLCounter:
    """Caller supplies a bounded, owned-service API; no default network access.

    Cache keys include the complete request and profile. Records are native API
    observations, not deployment attestation. A fake API remains a fake count.
    """
    def __init__(self, api):
        _require(callable(api))
        self.api=api; self.cache={}; self.records=[]

    @staticmethod
    def render(messages):
        _require(type(messages) is list and messages and messages[0].get('role')=='system')
        _require(all(set(m)=={'role','content'} and m['role'] in ('system','user','assistant')
            and type(m['content']) is str and '<|im_start|>' not in m['content']
            and '<|im_end|>' not in m['content'] for m in messages))
        return ''.join('<|im_start|>'+m['role']+'\n'+m['content']+'<|im_end|>\n' for m in messages)+'<|im_start|>assistant\n'

    def __call__(self, request, profile):
        validate_request(request);validate_profile(profile)
        _require(profile.runtime=='llama.cpp'
            and profile.runtime_version=='b5083@7538246e7ce0606694c38055cc2fc9f60535be6c'
            and profile.chat_template_version=='llama.cpp-b5083-chatml-explicit-system-v1'
            and profile.model_revision=='b052da96e84324589544585e439f410ccae19904'
            and profile.tokenizer_revision=='GGUF@'+profile.model_revision
            and not request.tools and request.response_schema is not None)
        canonical=lambda v:json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        key=hashlib.sha256(canonical({'request':asdict(request),'profile':asdict(profile)})).hexdigest()
        if key in self.cache:return self.cache[key]
        _require(len(self.cache)<2)
        expected=self.render(request.messages)
        body={'model':profile.model_id,'messages':request.messages,'stream':False,
            'max_tokens':request.max_output_tokens,**profile.sampling,'response_format':{'type':'json_object'},
            'metadata':{k:getattr(profile,k) for k in ('profile_id','model_revision','tokenizer_revision','chat_template_version')}}
        status,rendered=self.api('/apply-template',body)
        _require(status==200 and type(rendered) is dict and rendered.get('prompt')==expected)
        status,tokenized=self.api('/tokenize',{'content':expected,'add_special':True,'with_pieces':False})
        ids=tokenized.get('tokens') if type(tokenized) is dict else None
        _require(status==200 and type(ids) is list and ids and all(type(x) is int and x>=0 for x in ids)
            and ids.count(151644)==len(request.messages)+1 and ids.count(151645)==len(request.messages))
        if len(ids)>request.max_input_tokens or len(ids)+request.max_output_tokens>profile.context_limit:
            raise ProviderError('model_context_overflow')
        self.cache[key]=len(ids)
        self.records.append({'request_sha256':hashlib.sha256(canonical(asdict(request))).hexdigest(),
            'profile_sha256':hashlib.sha256(canonical(asdict(profile))).hexdigest(),
            'rendered_prompt':expected,'token_ids':ids,'token_count':len(ids),
            'add_special':True,'template_exact_match':True,'measurement_source':'injected_native_api',
            'model_revision':profile.model_revision,'deployment_attested':False})
        return len(ids)
