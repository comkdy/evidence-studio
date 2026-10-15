import { ArrowUp, Square } from 'lucide-react';

type Props={value:string;onChange:(value:string)=>void;onSend:()=>void;onStop:()=>void;busy:boolean;canSend:boolean;hasTurns:boolean};

export function ResearchComposer({value,onChange,onSend,onStop,busy,canSend,hasTurns}:Props){
 function send(){if(!busy&&canSend&&value.trim())onSend();}
 return <form className="research-message-form" aria-label="대화 메시지 작성" onSubmit={event=>{event.preventDefault();send();}}>
  <label htmlFor="research-question">{hasTurns?'이어서 대화하기':'메시지 작성'}</label>
  <textarea id="research-question" aria-label="연구 질문" rows={3} maxLength={1000} value={value} onChange={event=>onChange(event.target.value)}
   placeholder={hasTurns?'앞선 답변에서 더 궁금한 점을 물어보세요.':'궁금한 연구 주제를 적어 주세요. 답변을 받은 뒤 대화를 이어갈 수 있습니다.'}
   onKeyDown={event=>{
    if(event.key==='Enter'&&!event.shiftKey&&!event.nativeEvent.isComposing&&event.nativeEvent.keyCode!==229){event.preventDefault();send();}
   }}/>
  <div className="research-message-footer"><span>Enter 전송 · Shift+Enter 줄바꿈</span>
   {busy?<button type="button" className="secondary research-stop" onClick={onStop}><Square size={13}/>답변 중지</button>
    :<button className="primary" type="submit" disabled={!canSend||!value.trim()}><ArrowUp size={16}/>{hasTurns?'이어서 질문':'대화 시작'}</button>}
  </div>
 </form>;
}
